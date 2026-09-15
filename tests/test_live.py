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

**And the socket is now driven end to end.** `Socket` below speaks ASGI to the
app directly, because the HTTP fixture is httpx's `ASGITransport`, which has no
WebSocket support, and Starlette's `TestClient` runs the app in a portal on its
own event loop — the wrong loop for an aiosqlite session bound to this test's.
Driving the three ASGI message types by hand avoids both and exercises the
route as written. `_is_participant` still opens `AsyncSessionLocal` rather than
taking an injected session, because it lives inside a socket that outlives any
request, so the fixture puts the test database there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
import pytest_asyncio

from backend import bus, signaling
from backend.database import ChatMessage, ConnectionStatus
from tests.conftest import onboard, run_jobs
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
    her = await onboard(client, "her", visible_as=["woman"], interested_in=["man"], store=fake_storage)
    him = await onboard(client, "him", visible_as=["man"], interested_in=["woman"], store=fake_storage)
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


# ---------------------------------------------------------------------------
# The socket, end to end
# ---------------------------------------------------------------------------


class Socket:
    """A WebSocket client that speaks ASGI to the app directly.

    Three message types in each direction is the whole protocol, so driving it
    by hand costs less than it looks and buys the two things the alternatives
    could not: it stays in this test's event loop, so the injected sessionmaker
    and the bus both behave, and it exercises the route exactly as written —
    token check, origin check, participation check, accept, relay, disconnect.
    """

    def __init__(self, room_id: str, token: str, *, origin: str | None = None):
        path = f"/ws/signal/{room_id}"
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": ("test", 80),
            "client": ("test", 123),
            "root_path": "",
            "path": path,
            "raw_path": path.encode(),
            "query_string": f"token={token}".encode(),
            "headers": [(b"host", b"test")] + ([(b"origin", origin.encode())] if origin else []),
            "subprotocols": [],
            "state": {},
        }
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.accepted = False
        self.close_code: int | None = None

    async def __aenter__(self):
        from backend.app import app

        self._task = asyncio.create_task(app(self.scope, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(self._from_app.get(), timeout=5)
        if first["type"] == "websocket.accept":
            self.accepted = True
        else:
            self.close_code = first.get("code")
        return self

    async def __aexit__(self, *exc):
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        if self._task:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=5)

    async def send(self, payload: dict) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(payload)})

    async def next_event(self, wait: float = 2.0) -> dict:
        frame = await asyncio.wait_for(self._from_app.get(), timeout=wait)
        assert frame["type"] == "websocket.send", frame
        return json.loads(frame["text"])

    async def nothing_arrives(self, within: float = 0.25) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(self._from_app.get(), timeout=within)


@pytest_asyncio.fixture
async def socket_room(talking, db_sessionmaker, monkeypatch):
    """`talking`, plus the one thing a socket needs that a request does not.

    `_is_participant` opens `AsyncSessionLocal` itself rather than taking an
    injected session, because it lives inside a socket that outlives any
    request. Without this it reads a different database from the one the
    fixture just built, and every connection is refused for the wrong reason.
    """
    monkeypatch.setattr(signaling, "AsyncSessionLocal", db_sessionmaker)
    return talking


@pytest.mark.asyncio
async def test_a_socket_on_an_open_conversation_connects(socket_room):
    async with Socket(socket_room["room"], socket_room["her"]["access_token"]) as socket:
        assert socket.accepted is True


@pytest.mark.asyncio
async def test_a_stranger_is_refused_the_room(socket_room, client, fake_storage):
    """The access check over the real handshake, rather than by calling
    `_is_participant` directly and trusting the route to also call it."""
    outsider = await onboard(
        client,
        "cara",
        visible_as=["woman"],
        interested_in=["man"],
        store=fake_storage,
    )
    async with Socket(socket_room["room"], outsider["access_token"]) as socket:
        assert socket.accepted is False
        assert socket.close_code == signaling.POLICY_VIOLATION


@pytest.mark.asyncio
async def test_a_junk_token_is_refused(socket_room):
    async with Socket(socket_room["room"], "not-a-jwt") as socket:
        assert socket.accepted is False
        assert socket.close_code == signaling.POLICY_VIOLATION


@pytest.mark.asyncio
async def test_a_message_posted_over_http_arrives_on_the_peers_socket(socket_room, client):
    """The property the socket exists for, and the one nothing tested.

    He holds a socket; she posts over HTTP. The event has to reach him without
    him asking, and it has to carry what the database committed rather than
    anything a client claimed.
    """
    room = socket_room["room"]
    async with Socket(room, socket_room["him"]["access_token"]) as his_socket:
        assert his_socket.accepted
        # His own peer-joined is filtered at the sender, so: nothing yet.
        await his_socket.nothing_arrives()

        posted = await client.post(
            f"/api/connections/{room}/messages",
            headers=socket_room["her"]["headers"],
            json={"text": "did the socket carry this?"},
        )
        assert posted.status_code == 201

        event = await his_socket.next_event()
        assert event["type"] == "chat-message"
        assert event["message"]["text"] == "did the socket carry this?"
        # The id the server committed, not one a client invented.
        assert event["message"]["id"] == posted.json()["id"]


@pytest.mark.asyncio
async def test_typing_is_restamped_with_the_authenticated_sender(socket_room):
    """A client that names somebody else must not be able to type as them."""
    room = socket_room["room"]
    her_id, his_id = _uid(socket_room["her"]), _uid(socket_room["him"])
    async with (
        Socket(room, socket_room["her"]["access_token"]) as her_socket,
        Socket(room, socket_room["him"]["access_token"]) as his_socket,
    ):
        assert her_socket.accepted and his_socket.accepted
        assert await her_socket.next_event() == {"type": "peer-joined", "from": his_id}

        await her_socket.send({"type": "typing", "from": his_id})

        assert await his_socket.next_event() == {"type": "typing", "from": her_id}


@pytest.mark.asyncio
async def test_the_socket_will_not_carry_a_chat_message(socket_room):
    """`RELAYED_TYPES` as a live wire rather than a set literal: a client
    sending `chat-message` gets silence, because a message has exactly one
    write path and it is HTTP."""
    room = socket_room["room"]
    async with (
        Socket(room, socket_room["her"]["access_token"]) as her_socket,
        Socket(room, socket_room["him"]["access_token"]) as his_socket,
    ):
        await her_socket.next_event()  # peer-joined

        await her_socket.send({"type": "chat-message", "text": "straight down the socket"})

        await his_socket.nothing_arrives()


@pytest.mark.asyncio
async def test_ping_is_answered_on_the_socket_that_asked(socket_room):
    async with Socket(socket_room["room"], socket_room["her"]["access_token"]) as socket:
        await socket.send({"type": "ping"})
        assert await socket.next_event() == {"type": "pong"}


def test_a_socket_token_never_reaches_the_logs():
    """uvicorn logs the full path of every socket, and the token rides in the
    query string — so it has to be scrubbed before any handler writes it."""
    import logging

    from backend.logging_config import _redact_tokens

    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:5000", "/ws/signal/abc?token=eyJhbGciOiJIUzI1NiJ9.payload.sig&x=1"),
        None,
    )
    assert _redact_tokens(record)
    line = record.getMessage()
    assert "eyJ" not in line and "payload" not in line
    assert "token=[redacted]&x=1" in line
