"""Who may join, and when.

Three rules, in order:

1. The email domain has to belong to a scope. That is the identity anchor —
   it bounds the population to people who actually belong there and makes ban
   evasion cost something.
2. The account has to be 18 or older, computed from a birthdate.
3. The segment has to have room. Caps are per segment, and admission from the
   waitlist favours the under-filled side, because a balanced population is
   worth more than a first-come-first-served one.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import (
    FormerMember,
    Scope,
    ScopeStatus,
    SegmentCap,
    User,
    UserStatus,
    WaitlistEntry,
    WaitlistStatus,
    utcnow,
)
from backend.errors import AppError
from backend.logging_config import get_logger
from backend.options import MAX_AGE, MIN_AGE

log = get_logger(__name__)

# How long an invited person has to claim a freed slot before it passes on.
CLAIM_WINDOW = timedelta(hours=48)
# How long a former member's hash survives. Long enough to make rejoining fair,
# short enough not to become a permanent record.
FORMER_MEMBER_RETENTION = timedelta(days=365)


class ScopeNotRecognised(AppError):
    status_code = 403
    code = "scope_not_recognised"
    message = "Overtone isn't open at your campus yet. Use your campus email address to join."


class UnderageError(AppError):
    status_code = 403
    code = "underage"
    message = "You must be 18 or older to use Overtone."


class ScopeClosed(AppError):
    status_code = 403
    code = "scope_closed"
    message = "Signups are closed for your campus right now."


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower()


def hash_email(email: str) -> str:
    """Keyed hash, so the stored value is useless without the application secret."""
    return hmac.new(
        settings.jwt_secret_key.encode(), email.strip().lower().encode(), hashlib.sha256
    ).hexdigest()


def age_on(birthdate: date, today: date | None = None) -> int:
    today = today or date.today()
    return today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))


def check_age(birthdate: date) -> None:
    age = age_on(birthdate)
    if age < MIN_AGE:
        raise UnderageError()
    if age > MAX_AGE:
        raise AppError("Please enter a valid date of birth.")


async def resolve_scope(db: AsyncSession, email: str) -> Scope:
    """Find the campus this address belongs to, or refuse the signup."""
    domain = email_domain(email)
    scopes = (await db.execute(select(Scope))).scalars().all()

    for scope in scopes:
        domains = [d.lower() for d in (scope.email_domains or [])]
        # Accept subdomains too: a student at cse.example.edu belongs to example.edu.
        if any(domain == d or domain.endswith(f".{d}") for d in domains):
            if scope.status == ScopeStatus.closed:
                raise ScopeClosed()
            return scope

    raise ScopeNotRecognised()


async def segment_occupancy(db: AsyncSession, scope_id: str, segment: str) -> int:
    """Accounts holding a slot.

    Only active members count. An account still onboarding holds nothing — if it
    did, the person submitting their own profile would be counted against their
    own admission and the last slot in a segment could never be filled.
    """
    result = await db.execute(
        select(func.count(User.id))
        .where(User.scope_id == scope_id)
        .where(User.cap_segment == segment)
        .where(User.status == UserStatus.active)
        .where(User.deleted_at.is_(None))
    )
    return result.scalar_one()


async def segment_cap(db: AsyncSession, scope_id: str, segment: str) -> int | None:
    result = await db.execute(
        select(SegmentCap.cap).where(SegmentCap.scope_id == scope_id).where(SegmentCap.segment == segment)
    )
    return result.scalar_one_or_none()


async def has_room(db: AsyncSession, scope_id: str, segment: str) -> bool:
    cap = await segment_cap(db, scope_id, segment)
    if cap is None:
        return True  # no cap configured for this segment means uncapped
    return await segment_occupancy(db, scope_id, segment) < cap


async def was_former_member(db: AsyncSession, scope_id: str, email: str) -> bool:
    result = await db.execute(
        select(FormerMember.id)
        .where(FormerMember.scope_id == scope_id)
        .where(FormerMember.email_hash == hash_email(email))
        .where(FormerMember.expires_at > utcnow())
    )
    return result.first() is not None


async def join_waitlist(db: AsyncSession, user: User, segment: str) -> WaitlistEntry:
    entry = WaitlistEntry(
        user_id=user.id,
        scope_id=user.scope_id,
        segment=segment,
        status=WaitlistStatus.waiting,
    )
    db.add(entry)
    user.status = UserStatus.waitlisted
    log.info("waitlist_joined", user_id=user.id, scope_id=user.scope_id, segment=segment)
    return entry


async def waitlist_position(db: AsyncSession, entry: WaitlistEntry) -> int:
    """Position within the person's own segment — the only honest number to show."""
    result = await db.execute(
        select(func.count(WaitlistEntry.id))
        .where(WaitlistEntry.scope_id == entry.scope_id)
        .where(WaitlistEntry.segment == entry.segment)
        .where(WaitlistEntry.status == WaitlistStatus.waiting)
        .where(WaitlistEntry.joined_at < entry.joined_at)
    )
    return result.scalar_one() + 1


async def record_departure(db: AsyncSession, user: User) -> None:
    """Called on account deletion: free the slot and remember only that an
    address was once a member, so a rejoin queues fairly."""
    db.add(
        FormerMember(
            scope_id=user.scope_id,
            email_hash=hash_email(user.email),
            expires_at=utcnow() + FORMER_MEMBER_RETENTION,
        )
    )
    log.info("member_departed", scope_id=user.scope_id, segment=user.cap_segment)


async def expire_stale_invitations(db: AsyncSession) -> int:
    """An invitation nobody claimed must release its slot to the next person."""
    now = utcnow()
    stale = (
        (
            await db.execute(
                select(WaitlistEntry)
                .where(WaitlistEntry.status == WaitlistStatus.invited)
                .where(WaitlistEntry.claim_expires_at < now)
            )
        )
        .scalars()
        .all()
    )
    for entry in stale:
        entry.status = WaitlistStatus.expired
    if stale:
        log.info("waitlist_invitations_expired", count=len(stale))
    return len(stale)


async def invite_next(db: AsyncSession, scope_id: str, segment: str) -> WaitlistEntry | None:
    """Offer a freed slot to the person who has waited longest in that segment."""
    if not await has_room(db, scope_id, segment):
        return None

    result = await db.execute(
        select(WaitlistEntry)
        .where(WaitlistEntry.scope_id == scope_id)
        .where(WaitlistEntry.segment == segment)
        .where(WaitlistEntry.status == WaitlistStatus.waiting)
        .order_by(WaitlistEntry.joined_at.asc())
        .limit(1)
    )
    entry = result.scalars().first()
    if entry is None:
        return None

    entry.status = WaitlistStatus.invited
    entry.invited_at = utcnow()
    entry.claim_expires_at = entry.invited_at + CLAIM_WINDOW
    log.info("waitlist_invited", user_id=entry.user_id, segment=segment)
    return entry


def new_verification_token() -> tuple[str, str]:
    """Returns (token to email, hash to store). The plaintext is never persisted."""
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
