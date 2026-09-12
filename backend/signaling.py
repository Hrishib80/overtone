"""Chat WebSocket.

Calls are gone, so this carries only chat, typing and read receipts. The
connection registry is still per-process — that is the known single-worker
limit, replaced by Redis pub/sub in phase 03 (see DEPLOYMENT.md).

Security note: the previous version authenticated the token but never checked
that the connecting user belonged to the room, so anyone holding a match id
could read and write another pair's conversation. `_is_participant` closes that.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import or_, select

from backend.auth import verify_ws_token
from backend.config import settings
from backend.database import AsyncSessionLocal, ChatMessage, Connection, ConnectionStatus
from backend.logging_config import get_logger
from backend.safety import is_blocked

log = get_logger(__name__)
router = APIRouter()

RELAYED_TYPES = {"typing", "read-receipt"}

# Close codes
POLICY_VIOLATION = 1008
INTERNAL_ERROR = 1011


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, ws: WebSocket, room_id: str, user_id: str) -> None:
        self.rooms.setdefault(room_id, {})[user_id] = ws

    def disconnect(self, room_id: str, user_id: str) -> None:
        room = self.rooms.get(room_id)
        if not room:
            return
        room.pop(user_id, None)
        if not room:
            self.rooms.pop(room_id, None)

    async def broadcast(self, room_id: str, message: dict[str, Any], exclude: str | None = None) -> None:
        for user_id, ws in list(self.rooms.get(room_id, {}).items()):
            if user_id == exclude:
                continue
            try:
                await ws.send_json(message)
            except (RuntimeError, WebSocketDisconnect):
                self.disconnect(room_id, user_id)


manager = ConnectionManager()


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
        return not await is_blocked(db, user_id, other)


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
    await manager.connect(websocket, room_id, user_id)
    await manager.broadcast(room_id, {"type": "peer-joined", "user_id": user_id}, exclude=user_id)
    log.info("ws_connected", room_id=room_id, user_id=user_id)

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

            elif message_type == "chat-message":
                body = (message.get("message") or {}).get("text")
                if not isinstance(body, str) or not body.strip():
                    continue
                async with AsyncSessionLocal() as db:
                    db.add(
                        ChatMessage(
                            connection_id=room_id,
                            from_user_id=user_id,
                            message_text=body.strip()[:4000],
                        )
                    )
                    await db.commit()
                await manager.broadcast(room_id, message, exclude=user_id)

            elif message_type in RELAYED_TYPES:
                await manager.broadcast(room_id, message, exclude=user_id)

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws_error", room_id=room_id, user_id=user_id)
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=INTERNAL_ERROR)
    finally:
        manager.disconnect(room_id, user_id)
        await manager.broadcast(room_id, {"type": "peer-left", "user_id": user_id}, exclude=user_id)
