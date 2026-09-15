"""The admin portal: deciding who joins.

A finished profile waits here until an admin approves it — or sends it back
with a note saying what to change, which keeps it on the waitlist but out of the
queue until the person resubmits. Approving is what makes somebody `active`,
and `active` is the only status anything in the app will put in front of anyone
else, so approval is the whole gate: there is no second list to keep in sync.

**Separate from reviewing.** Reviewers answer reports about people already on
the platform; admins decide who gets onto it. They are different questions and
different trust, so they are different flags — an admin is not automatically a
reviewer or the other way round.

**Admins are made from the command line** (`manage.py admin --username`), for
the reason reviewers are: the account that decides who joins must not be
grantable through any surface an attacker could already hold a session on. A
non-admin gets 404, not 403 — that the portal exists is itself information.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import current_user
from backend.database import User, UserStatus, get_db, utcnow
from backend.errors import AppError, NotFound
from backend.logging_config import get_logger
from backend.moderation import staff_profile

log = get_logger(__name__)

MAX_NOTE = 1000


async def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin or user.status == UserStatus.suspended:
        raise NotFound("Not found.")
    return user


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _when(value) -> str | None:
    return value.isoformat() if value else None


async def _applicant(db: AsyncSession, user: User) -> dict[str, Any]:
    view = await staff_profile(db, user)
    view["application"] = {
        "applied_at": _when(user.applied_at),
        "note": user.application_note,
        "invited_by": None,
    }
    if user.invited_by_id:
        inviter = await db.get(User, user.invited_by_id)
        if inviter is not None:
            view["application"]["invited_by"] = {
                "id": inviter.id,
                "display_name": inviter.display_name,
                "username": inviter.username,
            }
    return view


@router.get("/waitlist")
async def read_waitlist(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Waiting, oldest first; and sent back, most recently sent first."""
    waiting_rows = (
        (
            await db.execute(
                select(User)
                .where(User.status == UserStatus.waitlisted)
                .where(User.application_note.is_(None))
                # A tiebreak, because two submissions can share a clock tick on
                # Windows and an ordering without one is decided by the database.
                .order_by(User.applied_at, User.created_at, User.id)
            )
        )
        .scalars()
        .all()
    )
    sent_back_rows = (
        (
            await db.execute(
                select(User)
                .where(User.status == UserStatus.waitlisted)
                .where(User.application_note.is_not(None))
                .order_by(User.applied_at.desc(), User.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "waiting": [await _applicant(db, u) for u in waiting_rows],
        "sent_back": [await _applicant(db, u) for u in sent_back_rows],
    }


async def _waiting_or_404(db: AsyncSession, user_id: str) -> User:
    applicant = await db.get(User, user_id)
    if applicant is None or applicant.deleted_at is not None:
        raise NotFound("That account no longer exists.")
    if applicant.status != UserStatus.waitlisted:
        raise AppError("That account isn't waiting for approval.")
    return applicant


@router.post("/applicants/{user_id}/approve")
async def approve(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Let them in. Allowed from "sent back" too: a note is a request, not a
    verdict, and an admin who changes their mind should not have to wait for the
    person to resubmit something that was fine."""
    applicant = await _waiting_or_404(db, user_id)
    applicant.status = UserStatus.active
    applicant.approved_at = utcnow()
    applicant.approved_by_id = admin.id
    applicant.application_note = None
    await db.commit()
    log.info("applicant_approved", user_id=applicant.id, admin_id=admin.id)
    return {"status": applicant.status}


class SendBack(BaseModel):
    note: str = Field(min_length=1, max_length=MAX_NOTE)


@router.post("/applicants/{user_id}/send-back")
async def send_back(
    user_id: str,
    body: SendBack,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ask for a change. The person reads the note, edits their profile and
    resubmits; until then they stay out of the queue."""
    note = body.note.strip()
    if not note:
        raise AppError("Say what should change, so they know what to fix.", field="note")
    applicant = await _waiting_or_404(db, user_id)
    applicant.application_note = note
    await db.commit()
    log.info("applicant_sent_back", user_id=applicant.id, admin_id=admin.id)
    return {"status": applicant.status, "note": applicant.application_note}


@router.get("/invited")
async def read_invited(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Who came in on whose code, newest first — the other door, kept visible.
    An invite skips the queue, so this is the one place an admin sees who used
    it, and who vouched for them."""
    rows = (
        (
            await db.execute(
                select(User)
                .where(User.invited_by_id.is_not(None))
                .order_by(User.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for user in rows:
        inviter = await db.get(User, user.invited_by_id)
        out.append(
            {
                "id": user.id,
                "display_name": user.display_name,
                "username": user.username,
                "status": user.status,
                "joined_at": _when(user.created_at),
                "invited_by": (
                    {"id": inviter.id, "display_name": inviter.display_name, "username": inviter.username}
                    if inviter
                    else None
                ),
            }
        )
    return {"invited": out}
