"""Queue semantics: claim once, retry with backoff, park, and recover."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from backend import jobs
from backend.database import utcnow


@pytest.mark.asyncio
async def test_enqueued_job_is_claimable(db_sessionmaker):
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, jobs.JobKind.process_photo, {"asset_id": "a1"}, subject_id="u1")
        await db.commit()

        claimed = await jobs.claim(db, limit=10)
        assert len(claimed) == 1
        assert claimed[0].status == jobs.JobStatus.running
        assert claimed[0].locked_by


@pytest.mark.asyncio
async def test_a_claimed_job_is_not_claimed_twice(db_sessionmaker):
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, jobs.JobKind.process_photo, {"asset_id": "a1"})
        await db.commit()

        assert len(await jobs.claim(db, limit=10)) == 1
        assert await jobs.claim(db, limit=10) == []


@pytest.mark.asyncio
async def test_a_job_scheduled_for_later_is_not_claimed_yet(db_sessionmaker):
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, jobs.JobKind.process_voice, {}, delay=timedelta(minutes=5))
        await db.commit()

        assert await jobs.claim(db, limit=10) == []


@pytest.mark.asyncio
async def test_failure_requeues_with_backoff(db_sessionmaker):
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, jobs.JobKind.process_photo, {})
        await db.commit()
        job = (await jobs.claim(db, limit=1))[0]

        await jobs.fail(db, job, ValueError("storage was unreachable"))

        assert job.status == jobs.JobStatus.queued
        assert job.attempts == 1
        assert job.run_after > utcnow()
        assert "storage was unreachable" in job.last_error
        # Backed off, so not immediately claimable again.
        assert await jobs.claim(db, limit=10) == []


@pytest.mark.asyncio
async def test_a_job_is_parked_after_max_attempts(db_sessionmaker):
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, jobs.JobKind.process_photo, {"asset_id": "a1"})
        await db.commit()

        job = (await db.execute(select(jobs.Job))).scalars().one()
        job.attempts = job.max_attempts - 1
        await db.commit()

        await jobs.fail(db, job, RuntimeError("still broken"))

        assert job.status == jobs.JobStatus.failed
        # Parked, not deleted — the payload and cause stay inspectable.
        assert job.payload == {"asset_id": "a1"}
        assert "still broken" in job.last_error


@pytest.mark.asyncio
async def test_completion_clears_the_lock(db_sessionmaker):
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, jobs.JobKind.process_photo, {})
        await db.commit()
        job = (await jobs.claim(db, limit=1))[0]

        await jobs.complete(db, job)

        assert job.status == jobs.JobStatus.done
        assert job.locked_by is None
        assert job.last_error is None


@pytest.mark.asyncio
async def test_stale_running_jobs_are_requeued(db_sessionmaker):
    """A worker killed mid-job must not strand its work forever."""
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, jobs.JobKind.process_photo, {})
        await db.commit()
        job = (await jobs.claim(db, limit=1))[0]

        job.locked_at = utcnow() - timedelta(hours=1)
        await db.commit()

        assert await jobs.reap_stale(db, older_than=timedelta(minutes=15)) == 1
        assert job.status == jobs.JobStatus.queued
        assert len(await jobs.claim(db, limit=10)) == 1


@pytest.mark.asyncio
async def test_backoff_grows_and_is_capped():
    assert jobs.backoff(1) < jobs.backoff(3) < jobs.backoff(5)
    assert jobs.backoff(50) == timedelta(seconds=300)
