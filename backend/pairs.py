"""The pair view's API: serve the next pair, record a choice.

The serializers live in `backend.profile_view` — the rule that round 1 shows a
photograph and nothing else is enforced there, where the connection inbox can
share it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import current_user
from backend.database import PairRound, User, get_db
from backend.logging_config import get_logger
from backend.pairing import next_pair, record_decision
from backend.profile_view import full_profile_view, photo_only_view

log = get_logger(__name__)
router = APIRouter(prefix="/api/pairs", tags=["pairs"])


async def _serialise(db: AsyncSession, pairing) -> dict[str, Any]:
    view = photo_only_view if pairing.round == PairRound.round_1 else full_profile_view
    return {
        "id": pairing.id,
        "round": pairing.round,
        "subjects": [
            await view(db, pairing.subject_a_id),
            await view(db, pairing.subject_b_id),
        ],
    }


class DecideRequest(BaseModel):
    model_config = {"extra": "forbid"}

    chosen_id: str


@router.get("/next")
async def get_next_pair(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    pairing = await next_pair(db, user)
    await db.commit()

    if pairing is None:
        return {"pair": None}
    return {"pair": await _serialise(db, pairing)}


@router.post("/{pairing_id}/decide")
async def decide_pair(
    pairing_id: str,
    req: DecideRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    decision = await record_decision(db, user, pairing_id, req.chosen_id)

    # Built before the commit, deliberately: this is the one response that
    # will ever carry the unlock, so it is worth resolving inside the same
    # transaction that created it rather than risking an expired row.
    unlocked = (
        await full_profile_view(db, decision.unlocked.subject_id) if decision.unlocked is not None else None
    )
    connection_id = decision.connection.id if decision.connection is not None else None
    await db.commit()

    return {
        "status": "decided",
        "round": decision.pairing.round,
        "round_two_scheduled": decision.pairing.round == PairRound.round_1,
        # Present exactly once, on the choice that earned it. Never a count,
        # never a score — the viewer learns that someone opened up, not how
        # close anybody else is to opening up.
        "unlocked": unlocked,
        # Set only when the other person had already crossed the same line.
        # Nobody has to send anything: the conversation is simply open.
        "connection_id": connection_id,
    }
