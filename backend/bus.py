"""Fan-out between processes.

The chat socket keeps its connections in a dict in memory, which is correct
and complete for exactly one worker. Run two and the app quietly half-works:
a message reaches whoever happens to be connected to the same process as the
sender, and nobody else. It is the worst shape of bug — no error, no log, just
a conversation that works when you test it and drops messages in production.

So every event goes through here. A publisher does not know or care who is
connected where; it says what happened, and each process delivers to whatever
sockets it is holding.

**Two backends, one interface.** With `REDIS_URL` set, Redis pub/sub carries
events between processes. Without it, an in-process implementation does the
same job within one. That is not a stub: a single worker is the correct
deployment for a campus-sized launch, and the local fallback means the socket
behaves identically in development and in tests without anyone installing a
server. The seam is here so that scaling out is an environment variable.

**Delivery is best-effort and unordered across rooms.** Redis pub/sub does not
persist, so a process that is down misses what was published while it was
down. That is the right trade for this traffic: chat messages are already
durable in Postgres and the client refetches the thread on open, so the socket
is an accelerator, never the record. Never publish something that only exists
as an event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from backend.config import settings
from backend.logging_config import get_logger

log = get_logger(__name__)

# One channel per conversation. Namespaced because a Redis instance is a
# shared thing and "the chat one" is not a channel name.
CHANNEL = "overtone:chat:{room_id}"


class Bus:
    """What both backends implement."""

    async def publish(self, room_id: str, event: dict[str, Any]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    @contextlib.asynccontextmanager
    async def subscribe(self, room_id: str) -> AsyncIterator[asyncio.Queue]:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        return None


class LocalBus(Bus):
    """One process, no dependencies.

    Each subscriber gets its own queue rather than sharing one, because a
    shared queue is a competing consumer — two sockets in a room would get
    every other message each instead of both getting all of them.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, set[asyncio.Queue]] = {}

    async def publish(self, room_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._rooms.get(room_id, ())):
            # A subscriber that has stopped reading must not be able to stall
            # a publisher; its queue is bounded and the drop is its problem.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self, room_id: str) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._rooms.setdefault(room_id, set()).add(queue)
        try:
            yield queue
        finally:
            subscribers = self._rooms.get(room_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._rooms.pop(room_id, None)


class RedisBus(Bus):
    """Across processes, over Redis pub/sub.

    One connection per subscriber. A connection pool shared between pub/sub
    and ordinary commands is a known way to get stuck: a subscribed connection
    is in a different protocol mode and cannot serve anything else.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._publisher = None

    async def _client(self):
        if self._publisher is None:
            import redis.asyncio as redis

            self._publisher = redis.from_url(self._url, decode_responses=True)
        return self._publisher

    async def publish(self, room_id: str, event: dict[str, Any]) -> None:
        try:
            client = await self._client()
            await client.publish(CHANNEL.format(room_id=room_id), json.dumps(event))
        except Exception:
            # A socket that misses a live update still shows the message on
            # the next fetch, so losing fan-out is a degradation rather than a
            # failure — and it must never fail the HTTP request that caused it.
            log.warning("bus_publish_failed", room_id=room_id, event_type=event.get("type"))

    @contextlib.asynccontextmanager
    async def subscribe(self, room_id: str) -> AsyncIterator[asyncio.Queue]:
        import redis.asyncio as redis

        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        client = redis.from_url(self._url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(CHANNEL.format(room_id=room_id))

        async def pump() -> None:
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                try:
                    event = json.loads(raw["data"])
                except (TypeError, ValueError):
                    continue
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

        task = asyncio.create_task(pump())
        try:
            yield queue
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()
                await client.aclose()

    async def close(self) -> None:
        if self._publisher is not None:
            with contextlib.suppress(Exception):
                await self._publisher.aclose()
            self._publisher = None


_bus: Bus | None = None


def get_bus() -> Bus:
    """The one the app is configured for, built on first use."""
    global _bus
    if _bus is None:
        _bus = RedisBus(settings.redis_url) if settings.redis_url else LocalBus()
        log.info("bus_selected", backend=type(_bus).__name__)
    return _bus


def reset_bus() -> None:
    """Drop the cached bus. For tests, and for a config change at startup."""
    global _bus
    _bus = None


async def publish(room_id: str, event: dict[str, Any]) -> None:
    await get_bus().publish(room_id, event)


def subscribe(room_id: str):
    return get_bus().subscribe(room_id)
