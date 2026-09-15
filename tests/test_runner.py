"""The job loop, run the way the API runs it when `RUN_WORKER_IN_API` is set."""

from __future__ import annotations

import asyncio

import pytest

from backend import jobs, runner
from backend.handlers import HANDLERS


@pytest.mark.asyncio
async def test_the_loop_runs_queued_work_and_stops_when_asked(db_sessionmaker, monkeypatch):
    monkeypatch.setattr(runner, "AsyncSessionLocal", db_sessionmaker)
    monkeypatch.setattr(runner.settings, "worker_poll_seconds", 0.05)

    ran = asyncio.Event()

    async def handler(db, payload):
        ran.set()

    monkeypatch.setitem(HANDLERS, "test_job", handler)
    async with db_sessionmaker() as db:
        await jobs.enqueue(db, "test_job", {"n": 1})
        await db.commit()

    stopping = asyncio.Event()
    task = asyncio.create_task(runner.loop(stopping))
    await asyncio.wait_for(ran.wait(), timeout=5)

    stopping.set()
    await asyncio.wait_for(task, timeout=5)
    assert task.done() and task.exception() is None
