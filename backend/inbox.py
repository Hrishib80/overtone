"""HTTP surface for connections: the inbox, requests, and messages.

The domain rules live in `backend.connections` — who may write to whom, what
an unlock entitles someone to, and when a request becomes a conversation.
This module only translates them to and from HTTP, the same way `pairs.py`
sits over `pairing.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend import connections
from backend.auth import require_member
from backend.database import User, get_db
from backend.logging_config import get_logger
from backend.ratelimit import SEND_REQUEST, consume

log = get_logger(__name__)
router = APIRouter(prefix="/api/connections", tags=["connections"])


class RequestBody(BaseModel):
    model_config = {"extra": "forbid"}

    subject_id: str
    text: str = Field(min_length=1, max_length=connections.MAX_MESSAGE_LENGTH)


class MessageBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1, max_length=connections.MAX_MESSAGE_LENGTH)


@router.get("")
async def get_inbox(
    user: User = Depends(require_member), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await connections.inbox(db, user)


@router.post("/requests", status_code=201)
async def create_request(
    body: RequestBody,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Before the work, not after: a refused request should not have created
    # a connection row on its way to being refused.
    await consume(db, SEND_REQUEST, user.id)
    connection, message = await connections.send_request(
        db, sender=user, recipient_id=body.subject_id, text=body.text
    )
    payload = {
        "id": connection.id,
        "status": connection.status,
        "message": connections.serialise_message(message, user.id),
    }
    await db.commit()
    return payload


@router.get("/{connection_id}/messages")
async def get_messages(
    connection_id: str,
    limit: int = 50,
    before: str | None = None,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    connection, rows = await connections.messages(
        db, user=user, connection_id=connection_id, limit=min(limit, 100), before=before
    )
    return {
        "id": connection.id,
        "status": connection.status,
        "peer": await connections.peer_card(db, connections.peer_id(connection, user.id)),
        "messages": [connections.serialise_message(m, user.id) for m in rows],
    }


@router.post("/{connection_id}/messages", status_code=201)
async def create_message(
    connection_id: str,
    body: MessageBody,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    message = await connections.post_message(db, sender=user, connection_id=connection_id, text=body.text)
    payload = connections.serialise_message(message, user.id)
    await db.commit()
    return payload


@router.post("/{connection_id}/decline")
async def decline_request(
    connection_id: str,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    connection = await connections.decline(db, user=user, connection_id=connection_id)
    status = connection.status
    await db.commit()
    return {"status": status}


@router.post("/{connection_id}/read")
async def mark_read(
    connection_id: str,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    count = await connections.mark_read(db, user=user, connection_id=connection_id)
    await db.commit()
    return {"marked_read": count}
