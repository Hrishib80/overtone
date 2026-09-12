"""The worker process.

Run alongside the API:

    python worker.py

It owns every model. The API tier loads none of them, which is what keeps the
API image around 200 MB and its deploys fast.

    python worker.py --once     process what is queued, then exit (for CI)
    python worker.py --status   report which models this process would use
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend import jobs, ml  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.database import AsyncSessionLocal  # noqa: E402
from backend.handlers import HANDLERS  # noqa: E402
from backend.logging_config import configure_logging, get_logger  # noqa: E402
from backend.pairing import expire_stale_round_two  # noqa: E402

configure_logging(level=settings.log_level, json_output=settings.emit_json_logs)
log = get_logger("worker")

_stopping = asyncio.Event()


def _request_stop(*_: object) -> None:
    # Finish the job in hand rather than abandoning it half-written.
    log.info("worker_stopping")
    _stopping.set()


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

    Both are recovery from something not happening rather than from something
    failing, which is exactly the kind of work that has no natural caller:
    a job left `running` by a worker that died is stranded until requeued, and
    a round-2 pairing nobody returns for would otherwise surface stale
    whenever that viewer next opens the app.
    """
    try:
        async with AsyncSessionLocal() as db:
            await jobs.reap_stale(db)
            await expire_stale_round_two(db)
            await db.commit()
    except Exception:
        log.exception("maintenance_failed")


async def loop() -> None:
    log.info("worker_started", models=ml.model_status(), poll=settings.worker_poll_seconds)

    ticks = 0
    while not _stopping.is_set():
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
                await asyncio.wait_for(_stopping.wait(), timeout=settings.worker_poll_seconds)

    log.info("worker_stopped")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    parser.add_argument("--status", action="store_true", help="print model status and exit")
    args = parser.parse_args()

    if args.status:
        for role, impl in ml.model_status().items():
            print(f"  {role:<6} {impl}")
        return 0

    if AsyncSessionLocal is None:
        print("DATABASE_URL is not configured.", file=sys.stderr)
        return 2

    if args.once:
        total = 0
        while True:
            ran = await drain(limit=settings.worker_batch_size)
            total += ran
            if ran == 0:
                break
        # --once is also how a cron-style deployment runs the worker, so it
        # has to do the periodic sweeps too — otherwise nothing ever expires.
        await maintenance()
        log.info("worker_drained", jobs=total)
        return 0

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows has no add_signal_handler; fall back to the sync handler.
            signal.signal(sig, _request_stop)

    await loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
