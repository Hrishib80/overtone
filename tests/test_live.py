"""Fan-out, and the single write path behind it.

Two things are being pinned here.

**The bus delivers to everyone, not to whoever grabs it first.** A shared
queue would make two sockets in a room competing consumers — each getting
every other message — which looks like a flaky connection rather than a bug,
and is the sort of thing that only shows up with two people talking.

**Messages are written in exactly one place.** The socket used to insert a
`ChatMessage` of its own, so the single-message limit on an unanswered
request, blocking and the rate limits all applied to the HTTP path and not to
the socket one. The socket is now delivery-only, and `RELAYED_TYPES` is the
allowlist that makes it so.

There is no end-to-end WebSocket test here, and that is a real gap rather than
an oversight: `signaling._is_participant` opens `AsyncSessionLocal` directly
instead of the injected session, so it reads a different database from the one
each test builds. Worth fixing when the socket next changes shape.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from backend import bus, signaling
from backend.database import ChatMessage, ConnectionStatus
from tests.conftest import TEST_DOMAIN, onboard, run_jobs
from tests.test_privacy import _uid


@pytest.fixture(autouse=True)
def _fresh_bus():
    """A bus per test. It is module-level state, and a queue left subscribed
    by one test would receive another's events."""
    bus.reset_bus()
    yield
    bus.reset_bus()


# ---------------------------------------------------------------------------
# The bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_redis_the_bus_is_the_local_one():
    """Not a fallback in the apologetic sense: one worker is the correct
    deployment at campus scale, and this is what it runs."""
    assert isinstance(bus.get_bus(), bus.LocalBus)


@pytest.mark.asyncio
async def test_both_subscribers_get_every_event():
    async with bus.subscribe("room") as first, bus.subscribe("room") as second:
        await bus.publish("room", {"type": "chat-message", "n": 1})
        await bus.publish("room", {"type": "chat-message", "n": 2})

        assert [(await first.get())["n"], (await first.get())["n"]] == [1, 2]
        assert [(await second.get())["n"], (await second.get())["n"]] == [1, 2]


@pytest.mark.asyncio
async def test_events_stay_in_their_room():
    async with bus.subscribe("a") as a, bus.subscribe("b") as b:
        await bus.publish("a", {"type": "typing"})
        assert (await a.get())["type"] == "typing"
        assert b.empty()


@pytest.mark.asyncio
async def test_leaving_stops_delivery_and_cleans_up():
    async with bus.subscribe("room"):
        pass
    await bus.publish("room", {"type": "typing"})
    assert bus.get_bus()._rooms == {}, "a room nobody is in should not be kept"


@pytest.mark.asyncio
async def test_a_subscriber_that_stopped_reading_cannot_stall_a_publisher():
    """A socket that wedged must be its own problem. Blocking here would
    block the HTTP request that published, for everybody."""
    async with bus.subscribe("room") as queue:
        for n in range(400):  # the queue caps at 256
            await asyncio.wait_for(bus.publish("room", {"n": n}), timeout=1)
        assert queue.full()


# ---------------------------------------------------------------------------
# What the HTTP path publishes
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def talking(client, db_sessionmaker, fake_storage):
    """Two people with an open conversation between them."""
    her = await onboard(
        client, f"her@{TEST_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    him = await onboard(
        client, f"him@{TEST_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    await run_jobs(db_sessionmaker)

    from backend.database import Connection, pair_key

    async with db_sessionmaker() as db:
        connection = Connection(
            user_a_id=min(_uid(her), _uid(him)),
            user_b_id=max(_uid(her), _uid(him)),
            connection_key=pair_key(_uid(her), _uid(him)),
            initiator_id=_uid(her),
            status=ConnectionStatus.open,
        )
        db.add(connection)
        await db.commit()
        room = connection.id

    return {"her": her, "him": him, "room": room}


@pytest.mark.asyncio
async def test_sending_a_message_announces_the_saved_one(client, talking):
    async with bus.subscribe(talking["room"]) as queue:
        sent = await client.post(
            f"/api/connections/{talking['room']}/messages",
            headers=talking["her"]["headers"],
            json={"text": "are you going on saturday"},
        )
        assert sent.status_code == 201, sent.text

        event = await asyncio.wait_for(queue.get(), timeout=2)

    assert event["type"] == "chat-message"
    assert event["from"] == _uid(talking["her"])
    # The stored row, not the request body: id and timestamp come from the
    # database, so what the peer sees is what was actually saved.
    assert event["message"]["id"] == sent.json()["id"]
    assert event["message"]["text"] == "are you going on saturday"
    assert event["message"]["sent_at"]


@pytest.mark.asyncio
async def test_the_announcement_drops_mine(client, talking):
    """`mine` is true from the sender's point of view and every recipient of
    this event is the other person — leaving it in would show somebody their
    own reply as theirs."""
    async with bus.subscribe(talking["room"]) as queue:
        await client.post(
            f"/api/connections/{talking['room']}/messages",
            headers=talking["her"]["headers"],
            json={"text": "hello"},
        )
        event = await asyncio.wait_for(queue.get(), timeout=2)

    assert "mine" not in event["message"]


@pytest.mark.asyncio
async def test_a_message_that_is_refused_announces_nothing(client, db_sessionmaker, talking):
    """The publish is after the commit, so a send the rules turned down must
    not put text on the other person's screen."""
    await client.post(
        "/api/safety/blocks",
        headers=talking["him"]["headers"],
        json={"user_id": _uid(talking["her"])},
    )

    async with bus.subscribe(talking["room"]) as queue:
        refused = await client.post(
            f"/api/connections/{talking['room']}/messages",
            headers=talking["her"]["headers"],
            json={"text": "hello?"},
        )
        assert refused.status_code == 409
        await asyncio.sleep(0)
        assert queue.empty()

    async with db_sessionmaker() as db:
        from sqlalchemy import select

        rows = (
            (await db.execute(select(ChatMessage).where(ChatMessage.connection_id == talking["room"])))
            .scalars()
            .all()
        )
    assert rows == []


@pytest.mark.asyncio
async def test_reading_tells_the_sender_once(client, talking):
    await client.post(
        f"/api/connections/{talking['room']}/messages",
        headers=talking["her"]["headers"],
        json={"text": "hello"},
    )

    async with bus.subscribe(talking["room"]) as queue:
        first = await client.post(
            f"/api/connections/{talking['room']}/read", headers=talking["him"]["headers"]
        )
        assert first.json()["marked_read"] == 1
        event = await asyncio.wait_for(queue.get(), timeout=2)
        assert event == {"type": "read-receipt", "from": _uid(talking["him"])}

        # Re-opening a thread already read should not keep nudging their ticks.
        again = await client.post(
            f"/api/connections/{talking['room']}/read", headers=talking["him"]["headers"]
        )
        assert again.json()["marked_read"] == 0
        await asyncio.sleep(0)
        assert queue.empty()


# ---------------------------------------------------------------------------
# The write path that was removed
# ---------------------------------------------------------------------------


def test_the_socket_cannot_send_a_message():
    """`RELAYED_TYPES` is the allowlist the socket acts on — anything else is
    ignored — so this is the gate itself, not a proxy for it. A
    `chat-message` reappearing here would restore a second write path around
    the request protocol, the blocks and the rate limits."""
    assert "chat-message" not in signaling.RELAYED_TYPES
    assert {"typing", "read-receipt"} == signaling.RELAYED_TYPES


def test_the_socket_module_does_not_write_messages():
    """Belt and braces for the same rule: the module should have no reason to
    reference the table it used to insert into."""
    import inspect

    source = inspect.getsource(signaling)
    assert "ChatMessage(" not in source
