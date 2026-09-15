"""The chat socket.

It carries three things — new messages, typing, and read receipts — and it
**writes none of them**. That is the design decision worth understanding
before changing anything here.

**There is one write path, and it is HTTP.** `connections.post_message` is
where the rules live: who may write to whom, the single-message limit on an
unanswered request, blocking, and the rate limit behind all of it. The socket
used to have a second path that inserted a `ChatMessage` directly, which meant
those rules applied to one way of sending a message and not the other — and
worse, it rebroadcast the *client's own payload*, so the text, the id and the
timestamp the peer saw were whatever the sender's browser claimed rather than
what was stored. Now the browser POSTs, the handler publishes what it
committed, and the socket delivers it.

**The socket is an accelerator, never the record.** Everything it carries is
already in Postgres and the client refetches the thread on open, so a dropped
event costs latency and nothing else. That is what makes best-effort fan-out
(see `backend/bus.py`) an honest choice rather than a corner cut.

Typing and read receipts are the exception to "already in Postgres": they are
ephemeral by nature, so they are relayed through the bus and never stored. A
lost one means a dot that did not appear.

Security: the token is verified, the origin is checked, and `_is_participant`
confirms membership of an **open** conversation. That last one is not
cosmetic — an earlier version authenticated the token and nothing else, so
anyone holding a room id could read another pair's conversation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import or_, select

from backend import bus
from backend.auth import verify_ws_token
from backend.config import settings
from backend.database import AsyncSessionLocal, Connection, ConnectionStatus
from backend.logging_config import get_logger
from backend.safety import hidden_accounts, is_blocked

log = get_logger(__name__)
router = APIRouter()

# Ephemeral, never stored, and the only things a client may originate.
RELAYED_TYPES = {"typing", "read-receipt"}

# Close codes
POLICY_VIOLATION = 1008
INTERNAL_ERROR = 1011


async def _is_participant(connection_id: str, user_id: str) -> bool:
    """Membership *and* state: a socket is only ever opened on a conversation
    that is actually open.

    A request that has not been answered has no live channel — its one message
    went through the HTTP path, and the single-message limit would mean
    nothing if the sender could then hold a socket open to the same room.
    """
    if AsyncSessionLocal is None:
        return False
    async with AsyncSessionLocal() as db:
        connection = (
            await db.execute(
                select(Connection)
                .where(Connection.id == connection_id)
                .where(Connection.status == ConnectionStatus.open)
                .where(or_(Connection.user_a_id == user_id, Connection.user_b_id == user_id))
            )
        ).scalar_one_or_none()
        if connection is None:
            return False

        # Blocking closes the conversation, so an open one should never be
        # between blocked people — but a socket outlives the HTTP request that
        # would have noticed, and this is the check that holds while one is
        # already connected.
        other = connection.user_b_id if connection.user_a_id == user_id else connection.user_a_id
        if await is_blocked(db, user_id, other):
            return False
        # Either side suspended: a suspended account has no live channel to
        # anybody, and nobody has one to them. Checked on every connect, so a
        # socket opened before a suspension does not survive its next reconnect.
        return not await hidden_accounts(db, {user_id, other})


async def message_sent(connection_id: str, message: dict[str, Any], sender_id: str) -> None:
    """Announce a message the HTTP handler has already committed.

    `mine` is deliberately dropped: the serialised message carries it from the
    sender's point of view, and every recipient of this event is the other
    person. Leaving it in would show them their own reply as theirs.
    """
    await bus.publish(
        connection_id,
        {
            "type": "chat-message",
            "from": sender_id,
            "message": {key: value for key, value in message.items() if key != "mine"},
        },
    )


async def read_up_to(connection_id: str, reader_id: str) -> None:
    """Tell the other side their messages have been read.

    Sent from the HTTP handler that actually changed the rows, so the ticks a
    sender sees match what the database says rather than what a client claimed
    over the socket.
    """
    await bus.publish(connection_id, {"type": "read-receipt", "from": reader_id})


async def _deliver(websocket: WebSocket, queue: asyncio.Queue, user_id: str) -> None:
    """Pump bus events out to this socket until it goes away."""
    while True:
        event = await queue.get()
        # Everyone in the room hears everything; the sender filters its own
        # echo here rather than the publisher tracking who is where.
        if event.get("from") == user_id:
            continue
        await websocket.send_json(event)


@router.websocket("/ws/signal/{room_id}")
async def chat_socket(websocket: WebSocket, room_id: str, token: str = Query(...)) -> None:
    user_id = verify_ws_token(token)
    if not user_id:
        await websocket.close(code=POLICY_VIOLATION)
        return

    origin = websocket.headers.get("origin")
    if settings.allowed_origins != ["*"] and origin not in settings.allowed_origins:
        log.warning("ws_rejected_origin", origin=origin)
        await websocket.close(code=POLICY_VIOLATION)
        return

    if not await _is_participant(room_id, user_id):
        log.warning("ws_rejected_not_participant", room_id=room_id, user_id=user_id)
        await websocket.close(code=POLICY_VIOLATION)
        return

    await websocket.accept()
    log.info("ws_connected", room_id=room_id, user_id=user_id)

    async with bus.subscribe(room_id) as queue:
        await bus.publish(room_id, {"type": "peer-joined", "from": user_id})
        sender = asyncio.create_task(_deliver(websocket, queue, user_id))
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue

                message_type = message.get("type")

                if message_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif message_type in RELAYED_TYPES:
                    # Re-stamped with the authenticated sender rather than
                    # relayed as given: a client that names somebody else must
                    # not be able to type on their behalf.
                    await bus.publish(room_id, {"type": message_type, "from": user_id})

                # Anything else — a `chat-message` from an old client, say —
                # is ignored on purpose. Messages are sent over HTTP.

        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("ws_error", room_id=room_id, user_id=user_id)
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=INTERNAL_ERROR)
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
            await bus.publish(room_id, {"type": "peer-left", "from": user_id})
