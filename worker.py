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
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend import ml  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.database import AsyncSessionLocal  # noqa: E402
from backend.logging_config import configure_logging, get_logger  # noqa: E402
from backend.runner import drain, loop, maintenance  # noqa: E402

configure_logging(level=settings.log_level, json_output=settings.emit_json_logs)
log = get_logger("worker")

_stopping = asyncio.Event()


def _request_stop(*_: object) -> None:
    # Finish the job in hand rather than abandoning it half-written.
    log.info("worker_stopping")
    _stopping.set()


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

    await loop(_stopping)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
