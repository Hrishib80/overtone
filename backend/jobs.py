"""A durable job queue built on the database we already have.

Why not Redis yet: the queue's only hard requirement in this phase is that a
job survives a restart, and Postgres gives that for free — plus jobs become
transactional with the rows they describe, so a photo row and the job that
processes it commit together or not at all. Redis arrives in phase 03, when
chat fanout needs pub/sub and a queue is no longer the only reason for it.

Claiming uses `FOR UPDATE SKIP LOCKED` on Postgres so several workers can pull
from the same table without blocking each other. SQLite has no such clause, so
the test path falls back to select-then-update, which is correct for the single
worker a test run has.
"""

from __future__ import annotations

import enum
import os
import socket
import traceback
from datetime import timedelta
from typing import Any

from sqlalchemy import Column, Index, Integer, String, Text, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import JSON

from backend.config import settings
from backend.database import Base, UTCDateTime, generate_uuid, utcnow
from backend.logging_config import get_logger

log = get_logger(__name__)


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class JobKind(enum.StrEnum):
    process_photo = "process_photo"
    process_voice = "process_voice"
    refresh_text = "refresh_text"
    # Delivery goes through the queue so a provider that is briefly down costs
    # a retry, not an account nobody can ever verify.
    send_email = "send_email"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        Index("ix_jobs_subject", "subject_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    kind = Column(String, nullable=False)
    # What the job is about — lets a subject's pending work be found or cancelled.
    subject_id = Column(String, nullable=True)
    # Plain JSON: the payload is read by id, never queried into, so JSONB
    # would buy nothing and its Alembic rendering needs hand-patching.
    payload = Column(JSON, nullable=False, default=dict)

    status = Column(String, nullable=False, default=JobStatus.queued)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)

    run_after = Column(UTCDateTime(), nullable=False, default=utcnow)
    locked_at = Column(UTCDateTime(), nullable=True)
    locked_by = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at = Column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def backoff(attempts: int) -> timedelta:
    """Exponential, capped. Attempt 1 waits 2s, attempt 5 waits ~32s."""
    return timedelta(seconds=min(2**attempts, 300))


async def enqueue(
    db: AsyncSession,
    kind: JobKind | str,
    payload: dict[str, Any],
    *,
    subject_id: str | None = None,
    delay: timedelta | None = None,
) -> Job:
    """Add a job. Caller commits — so the job lands with whatever it describes."""
    job = Job(
        kind=str(kind),
        subject_id=subject_id,
        payload=payload,
        max_attempts=settings.job_max_attempts,
        run_after=utcnow() + delay if delay else utcnow(),
    )
    db.add(job)
    return job


async def claim(db: AsyncSession, *, limit: int = 1, owner: str | None = None) -> list[Job]:
    """Take up to `limit` due jobs and mark them running."""
    owner = owner or worker_id()
    now = utcnow()

    ready = (
        select(Job.id)
        .where(Job.status == JobStatus.queued)
        .where(Job.run_after <= now)
        .order_by(Job.run_after)
        .limit(limit)
    )
    if settings.db_is_postgres:
        ready = ready.with_for_update(skip_locked=True)

    ids = list((await db.execute(ready)).scalars().all())
    if not ids:
        return []

    await db.execute(
        update(Job)
        .where(Job.id.in_(ids))
        .values(status=JobStatus.running, locked_at=now, locked_by=owner, updated_at=now)
    )
    await db.commit()

    claimed = (await db.execute(select(Job).where(Job.id.in_(ids)))).scalars().all()
    return list(claimed)


async def complete(db: AsyncSession, job: Job) -> None:
    job.status = JobStatus.done
    job.locked_at = None
    job.locked_by = None
    job.last_error = None
    await db.commit()
    log.info("job_done", job_id=job.id, kind=job.kind, attempts=job.attempts)


async def fail(db: AsyncSession, job: Job, error: BaseException) -> None:
    """Retry with backoff until max_attempts, then park the job as failed.

    A parked job is not lost — it keeps its payload and its last error, so the
    cause is inspectable and it can be requeued once the cause is fixed.
    """
    job.attempts += 1
    job.last_error = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-4000:]
    job.locked_at = None
    job.locked_by = None

    if job.attempts >= job.max_attempts:
        job.status = JobStatus.failed
        log.error("job_failed_permanently", job_id=job.id, kind=job.kind, attempts=job.attempts)
    else:
        job.status = JobStatus.queued
        job.run_after = utcnow() + backoff(job.attempts)
        log.warning("job_retry", job_id=job.id, kind=job.kind, attempts=job.attempts)

    await db.commit()


async def reap_stale(db: AsyncSession, older_than: timedelta = timedelta(minutes=15)) -> int:
    """Requeue jobs whose worker died mid-run.

    Without this, a worker killed between claiming and finishing leaves its jobs
    marked running forever and nothing ever picks them up again.
    """
    cutoff = utcnow() - older_than
    stale = (
        (await db.execute(select(Job).where(Job.status == JobStatus.running).where(Job.locked_at < cutoff)))
        .scalars()
        .all()
    )
    for job in stale:
        job.status = JobStatus.queued
        job.locked_at = None
        job.locked_by = None
        job.run_after = utcnow()
    if stale:
        await db.commit()
        log.warning("jobs_reaped", count=len(stale))
    return len(stale)
