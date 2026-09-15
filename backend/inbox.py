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

from backend import connections, signaling
from backend.auth import require_participant
from backend.database import User, get_db
from backend.logging_config import get_logger
from backend.ratelimit import SEND_REQUEST, consume

log = get_logger(__name__)
# On the router as well as each route, so a route added later cannot forget it.
router = APIRouter(
    prefix="/api/connections", tags=["connections"], dependencies=[Depends(require_participant)]
)


class RequestBody(BaseModel):
    model_config = {"extra": "forbid"}

    subject_id: str
    text: str = Field(min_length=1, max_length=connections.MAX_MESSAGE_LENGTH)


class MessageBody(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1, max_length=connections.MAX_MESSAGE_LENGTH)


@router.get("")
async def get_inbox(
    user: User = Depends(require_participant), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await connections.inbox(db, user)


@router.get("/counts")
async def get_counts(
    user: User = Depends(require_participant), db: AsyncSession = Depends(get_db)
) -> dict[str, int]:
    """Just the three numbers on the navbar.

    Declared above the `/{connection_id}/...` routes so a literal path can
    never be read as an id.
    """
    return await connections.counts(db, user)


@router.post("/requests", status_code=201)
async def create_request(
    body: RequestBody,
    user: User = Depends(require_participant),
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
    # No socket publish here on purpose: a request has no live channel — the
    # socket only opens on a conversation that is already open, which is the
    # single-message limit doing its job. The email above is the whole of how
    # a request reaches somebody who is not looking.
    return payload


@router.get("/{connection_id}/messages")
async def get_messages(
    connection_id: str,
    limit: int = 50,
    before: str | None = None,
    user: User = Depends(require_participant),
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
    user: User = Depends(require_participant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    message = await connections.post_message(db, sender=user, connection_id=connection_id, text=body.text)
    payload = connections.serialise_message(message, user.id)
    await db.commit()
    # After the commit, never before: an event announcing a message that then
    # failed to save would put text on the other person's screen that does not
    # exist. Fan-out is best-effort and must not fail the send either way.
    await signaling.message_sent(connection_id, payload, user.id)
    return payload


@router.post("/{connection_id}/decline")
async def decline_request(
    connection_id: str,
    user: User = Depends(require_participant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    connection = await connections.decline(db, user=user, connection_id=connection_id)
    status = connection.status
    await db.commit()
    return {"status": status}


@router.post("/{connection_id}/read")
async def mark_read(
    connection_id: str,
    user: User = Depends(require_participant),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    count = await connections.mark_read(db, user=user, connection_id=connection_id)
    await db.commit()
    # Only when something actually changed: re-opening a thread you have
    # already read should not keep nudging the other person's ticks.
    if count:
        await signaling.read_up_to(connection_id, user.id)
    return {"marked_read": count}
