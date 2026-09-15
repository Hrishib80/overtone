"""The job loop, shared by the worker process and the API.

`python worker.py` runs it as its own process, which is the shape for real
models: they are gigabytes, and the API tier should never load them. With the
stand-in models there is nothing heavy to keep apart, so a single small
instance can run the loop inside the API instead (`RUN_WORKER_IN_API=true`) —
one service rather than two, which is the difference between a free deploy and
a paid one. Jobs are claimed with row locks either way, so running both at once
is safe, merely pointless.
"""

from __future__ import annotations

import asyncio
import contextlib

from backend import jobs, ml, ratelimit
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.handlers import HANDLERS
from backend.logging_config import get_logger
from backend.pairing import expire_stale_round_two

log = get_logger("worker")


async def run_one(job: jobs.Job) -> None:
    handler = HANDLERS.get(job.kind)
    if handler is None:
        raise ValueError(f"No handler registered for job kind {job.kind!r}")

    async with AsyncSessionLocal() as db:
        await handler(db, job.payload or {})


async def drain(*, limit: int) -> int:
    """Claim and run up to `limit` jobs. Returns how many ran."""
    async with AsyncSessionLocal() as db:
        claimed = await jobs.claim(db, limit=limit)

    for job in claimed:
        log.info("job_started", job_id=job.id, kind=job.kind, attempt=job.attempts + 1)
        try:
            await run_one(job)
        except Exception as exc:  # noqa: BLE001 — the runner owns retry policy
            async with AsyncSessionLocal() as db:
                fresh = await db.get(jobs.Job, job.id)
                if fresh is not None:
                    await jobs.fail(db, fresh, exc)
        else:
            async with AsyncSessionLocal() as db:
                fresh = await db.get(jobs.Job, job.id)
                if fresh is not None:
                    await jobs.complete(db, fresh)

    return len(claimed)


async def maintenance() -> None:
    """Periodic sweeps that nothing else triggers.

    All three are recovery from something *not happening* rather than from
    something failing, which is the kind of work that has no natural caller: a
    job left `running` by a worker that died stays stranded until requeued, a
    round-2 pairing nobody returns for would surface stale whenever that
    viewer next opens the app, and spent rate-limit windows are never read
    again but are never deleted by the request that wrote them either.
    """
    try:
        async with AsyncSessionLocal() as db:
            await jobs.reap_stale(db)
            await expire_stale_round_two(db)
            await ratelimit.sweep(db)
            await db.commit()
    except Exception:
        log.exception("maintenance_failed")


async def loop(stopping: asyncio.Event) -> None:
    log.info("worker_started", models=ml.model_status(), poll=settings.worker_poll_seconds)

    ticks = 0
    while not stopping.is_set():
        try:
            ran = await drain(limit=settings.worker_batch_size)
        except Exception:
            log.exception("worker_tick_failed")
            ran = 0

        # Roughly every five minutes, run the periodic sweeps.
        ticks += 1
        if ticks % max(1, int(300 / settings.worker_poll_seconds)) == 0:
            await maintenance()

        if ran == 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=settings.worker_poll_seconds)

    log.info("worker_stopped")
