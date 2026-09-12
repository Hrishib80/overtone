"""Blocking and reporting.

Two different acts that are often confused. **Blocking** is private and
immediate: it is one person removing another from their experience, needs no
justification, and nobody reviews it. **Reporting** is a message to us about
behaviour, and a human reads it.

They are offered together because someone who needs one usually wants the
other, and separating them into two flows at the moment somebody is upset is
how people end up doing neither. Reporting therefore blocks by default, and
says so.

Blocks are read symmetrically everywhere — pairing, connections, the socket.
The single most important property in this module is that a block is applied
in *both* directions: a one-way block would leave the blocked person still
seeing someone who has removed themselves, which is the exact situation it
exists to prevent.

Domain and HTTP live in one file here, unlike `pairing`/`pairs` or
`connections`/`inbox`. There is not enough of either to be worth two.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import current_user
from backend.database import (
    Affinity,
    Block,
    Connection,
    ConnectionStatus,
    Report,
    ReportReason,
    User,
    get_db,
    pair_key,
    utcnow,
)
from backend.errors import AppError, NotFound
from backend.logging_config import get_logger
from backend.ratelimit import REPORT, consume

log = get_logger(__name__)
router = APIRouter(prefix="/api/safety", tags=["safety"])

MAX_NOTE_LENGTH = 2000


async def blocked_ids(db: AsyncSession, user_id: str) -> set[str]:
    """Everyone this user cannot encounter, in either direction.

    One query rather than two, because every caller wants the union and a
    caller that fetched only one direction would be a silent hole.
    """
    rows = (
        await db.execute(
            select(Block.blocker_id, Block.blocked_id).where(
                or_(Block.blocker_id == user_id, Block.blocked_id == user_id)
            )
        )
    ).all()
    out: set[str] = set()
    for blocker, blocked in rows:
        out.add(blocked if blocker == user_id else blocker)
    return out


async def is_blocked(db: AsyncSession, a: str, b: str) -> bool:
    """Whether these two are separated, whichever of them did it."""
    row = (
        await db.execute(
            select(Block.id)
            .where(
                or_(
                    (Block.blocker_id == a) & (Block.blocked_id == b),
                    (Block.blocker_id == b) & (Block.blocked_id == a),
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def block(db: AsyncSession, *, blocker: User, subject_id: str) -> Block:
    """Remove someone from this person's experience, now.

    Closing any open conversation is part of the same act: leaving a thread
    alive but unreachable would show as a conversation that silently stopped
    working, and the other person would have no idea why their messages were
    refused.
    """
    if subject_id == blocker.id:
        raise AppError("You can't block yourself.")
    if await db.get(User, subject_id) is None:
        raise NotFound("That person doesn't exist.")

    existing = (
        await db.execute(
            select(Block).where(Block.blocker_id == blocker.id).where(Block.blocked_id == subject_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = Block(blocker_id=blocker.id, blocked_id=subject_id)
        db.add(existing)
        await db.flush()

    connection = (
        await db.execute(
            select(Connection).where(Connection.connection_key == pair_key(blocker.id, subject_id))
        )
    ).scalar_one_or_none()
    if connection is not None and connection.status != ConnectionStatus.declined:
        connection.status = ConnectionStatus.declined
        connection.closed_at = utcnow()

    log.info("user_blocked", blocker_id=blocker.id, subject_id=subject_id)
    return existing


async def unblock(db: AsyncSession, *, blocker: User, subject_id: str) -> bool:
    """Undo a block. The closed conversation is *not* reopened — that was a
    separate decision and reversing it is the other person's to make too."""
    row = (
        await db.execute(
            select(Block).where(Block.blocker_id == blocker.id).where(Block.blocked_id == subject_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    log.info("user_unblocked", blocker_id=blocker.id, subject_id=subject_id)
    return True


async def report(
    db: AsyncSession,
    *,
    reporter: User,
    subject_id: str,
    reason: str,
    note: str | None = None,
    context: str | None = None,
    also_block: bool = True,
) -> Report:
    if subject_id == reporter.id:
        raise AppError("You can't report yourself.")
    if reason not in set(ReportReason):
        raise AppError("That isn't a reason we recognise.")
    if await db.get(User, subject_id) is None:
        raise NotFound("That person doesn't exist.")

    row = Report(
        reporter_id=reporter.id,
        subject_id=subject_id,
        reason=reason,
        note=(note or "").strip()[:MAX_NOTE_LENGTH] or None,
        context=context,
    )
    db.add(row)
    await db.flush()

    if also_block:
        await block(db, blocker=reporter, subject_id=subject_id)

    log.info("user_reported", subject_id=subject_id, reason=reason, blocked=also_block)
    return row


async def forget_affinity(db: AsyncSession, *, viewer_id: str, subject_id: str) -> None:
    """Drop what the pair loop learned about this pairing, both ways.

    Without it a blocked person keeps their unlocked status and would come
    back the moment the block were lifted, which is not what "I don't want to
    see this person" meant.
    """
    rows = (
        (
            await db.execute(
                select(Affinity).where(
                    or_(
                        (Affinity.viewer_id == viewer_id) & (Affinity.subject_id == subject_id),
                        (Affinity.viewer_id == subject_id) & (Affinity.subject_id == viewer_id),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await db.delete(row)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class BlockBody(BaseModel):
    model_config = {"extra": "forbid"}

    user_id: str


class ReportBody(BaseModel):
    model_config = {"extra": "forbid"}

    user_id: str
    reason: str
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    context: str | None = None
    # Reporting blocks unless the reporter deliberately says otherwise; the
    # form says so rather than leaving it as a surprise either way.
    block: bool = True


@router.get("/blocks")
async def list_blocks(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Only the blocks this person made. Being blocked is not disclosed —
    telling someone they have been blocked hands them a reason to find another
    way to reach the person who blocked them."""
    rows = (
        (await db.execute(select(Block).where(Block.blocker_id == user.id).order_by(Block.created_at.desc())))
        .scalars()
        .all()
    )
    people = []
    for row in rows:
        subject = await db.get(User, row.blocked_id)
        people.append(
            {
                "id": row.blocked_id,
                "display_name": subject.display_name if subject else None,
                "blocked_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"blocks": people}


@router.post("/blocks", status_code=201)
async def create_block(
    body: BlockBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await block(db, blocker=user, subject_id=body.user_id)
    await forget_affinity(db, viewer_id=user.id, subject_id=body.user_id)
    await db.commit()
    return {"status": "blocked"}


@router.delete("/blocks/{subject_id}")
async def remove_block(
    subject_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    removed = await unblock(db, blocker=user, subject_id=subject_id)
    await db.commit()
    return {"status": "unblocked" if removed else "not_blocked"}


@router.post("/reports", status_code=201)
async def create_report(
    body: ReportBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await consume(db, REPORT, user.id)
    row = await report(
        db,
        reporter=user,
        subject_id=body.user_id,
        reason=body.reason,
        note=body.note,
        context=body.context,
        also_block=body.block,
    )
    if body.block:
        await forget_affinity(db, viewer_id=user.id, subject_id=body.user_id)
    await db.commit()
    # Deliberately no detail about what happens next. A reporter learning the
    # outcome would learn things about the other person they are not entitled
    # to, and a reporter learning nothing happened is discouraging.
    return {"status": "received", "id": row.id, "blocked": body.block}


@router.get("/reasons")
async def list_reasons() -> dict[str, Any]:
    return {"reasons": [r.value for r in ReportReason]}
