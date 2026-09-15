"""Invite codes: how an approved member lets somebody in directly.

The waitlist is the default door. A member an admin approved gets a code, and a
person who joins with it skips the wait: the member has vouched for them, which
is the thing the admin would otherwise be checking.

Three rules shape it, and each is a limit on how far one vouch reaches:

**Only members an admin let in can invite.** A person who came in on a code
cannot hand out codes of their own. Otherwise one approval becomes a chain —
five invite five invite five — and the waitlist stops meaning anything within a
week. Accounts that were already active before approval existed count as let in.

**Each member has `INVITES_PER_MEMBER`.** A code posted in a campus group chat
is one member's five places, not an open door. A place is taken when somebody
*registers* with the code rather than when they finish, so a code cannot be
handed to fifty people at once in the hope that five finish. A place taken by
somebody who never finished stays taken — the honest cost of that choice.

**A vouch is checked again when the profile is finished.** If the member who
invited somebody has since been suspended, their vouch is void and the new
profile waits like anyone else's.

Codes are 8 characters from an alphabet without 0/O or 1/I, so they survive
being read aloud or copied by hand. At 32^8 they are not guessable, and
registration is rate-limited besides.
"""

from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import User, UserStatus
from backend.errors import AppError

INVITES_PER_MEMBER = 5
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def normalise(raw: str | None) -> str:
    """What was typed, as it is stored: capitals, no spaces or dashes — a code
    read off a screen often arrives as `abcd-efgh`."""
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def can_invite(user: User) -> bool:
    return user.status == UserStatus.active and user.invited_by_id is None


async def places_used(db: AsyncSession, inviter_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(User.id))
            .where(User.invited_by_id == inviter_id)
            .where(User.deleted_at.is_(None))
        )
        or 0
    )


async def code_for(db: AsyncSession, user: User) -> str | None:
    """This member's code, made the first time it is asked for. None for
    somebody who cannot invite, so a code never exists that could not work."""
    if not can_invite(user):
        return None
    if user.invite_code:
        return user.invite_code
    # Looked up rather than retried on an IntegrityError: a rollback there
    # would discard whatever else the request had pending. The unique index is
    # still the real guarantee if two requests ever race to the same code.
    for _ in range(5):
        candidate = new_code()
        if not (await db.execute(select(User.id).where(User.invite_code == candidate))).first():
            user.invite_code = candidate
            await db.flush()
            return candidate
    raise AppError("Could not make an invite code just now. Try again.")


async def inviter_for(db: AsyncSession, raw: str) -> User:
    """The member whose code this is, if it can still let somebody in."""
    code = normalise(raw)
    inviter = (
        (await db.execute(select(User).where(User.invite_code == code))).scalars().first() if code else None
    )
    if inviter is None or not can_invite(inviter):
        raise AppError("That invite code isn't valid.", field="invite_code")
    if await places_used(db, inviter.id) >= INVITES_PER_MEMBER:
        raise AppError("That invite code has been used up.", field="invite_code")
    return inviter


async def vouched_for(db: AsyncSession, user: User) -> bool:
    """Whether this person's inviter can still vouch for them now."""
    if not user.invited_by_id:
        return False
    inviter = await db.get(User, user.invited_by_id)
    return inviter is not None and can_invite(inviter)
