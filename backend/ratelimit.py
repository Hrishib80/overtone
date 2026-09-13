"""Rate limits, counted in the database.

Not in memory, because the app runs more than one worker and a per-process
counter means the real allowance is whatever you set times however many
processes happen to be up — which is not a number anyone chose. Not in Redis
either, for now, for the same reason the job queue is not: Redis arrives when
chat fanout needs pub/sub, and until then it is an extra thing to run.

**Sliding window, not fixed.** A fixed window lets someone spend their whole
allowance in the last second of one window and again in the first second of
the next — twice the limit at the moment it matters most, which is exactly the
shape of a login attack. This counts the current window in full and the
previous one in proportion to how much of it is still inside the trailing
window, which is the standard approximation and costs one extra row read.

It is an approximation, and worth being honest about what it does not do: it
assumes the previous window's uses were spread evenly through it, so a burst
packed into the last moment of a window is under-counted slightly, and about
one extra request can slip across a boundary. It turns "the whole allowance
again, immediately" into "one more", which is the part that mattered.

**`consume` commits.** A rate limit that is rolled back with the request it was
counting is no rate limit at all — failed logins are the entire brute-force
case, and a failed login rolls its transaction back. So the counter is written
in its own right, which means `consume` has to be the first write in a
handler: anything already pending on that session would be committed with it.

The counters are written on limited endpoints only, not on every request.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import RateLimitWindow, utcnow
from backend.errors import RateLimited
from backend.logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Limit:
    """How many of `action` one subject may do per `window`."""

    action: str
    allowance: int
    window: timedelta

    def bucket(self, subject: str) -> str:
        return f"{self.action}:{subject}"


# Deliberately generous. These exist to stop abuse, not to ration ordinary
# use — a limit a real person can reach on a good day is a bug, and the number
# to watch after launch is how often each of these actually fires.
#
# Ten opening messages a day sounds low until you remember each one costs
# seven comparisons of that specific person to unlock; the unlock is the real
# gate and this is the backstop behind it.
SEND_REQUEST = Limit("connection_request", 10, timedelta(days=1))
REPORT = Limit("report", 20, timedelta(days=1))
UPLOAD_TICKET = Limit("upload_ticket", 40, timedelta(hours=1))

# **Per-address limits have to be loose on a campus, and this is why.**
#
# A university presents a handful of public addresses for thousands of
# students on its wifi. Anything tight enough to be a real brute-force defence
# per address would lock out the entire campus during the launch rush, which
# is both the worst possible moment and the hardest failure to diagnose from
# the outside — everyone on wifi is refused, everyone on mobile data is fine.
#
# So the tight limit is keyed on the *account*, which is where a brute-force
# attempt is actually aimed, and the per-address limit is set only high enough
# to catch one machine spraying a list of addresses. Do not "tighten" these
# without a way to tell campus NAT apart from an attacker.
LOGIN_PER_ACCOUNT = Limit("login_account", 10, timedelta(minutes=15))
LOGIN_PER_ADDRESS = Limit("login_address", 120, timedelta(minutes=15))
REGISTER = Limit("register", 120, timedelta(hours=1))

# Tight per address, because each one sends a real email to somebody who did
# not ask for it, and loose per network for the campus-NAT reason above.
RESEND_VERIFICATION = Limit("resend_verification", 5, timedelta(hours=1))

# Re-entering a password inside an existing session. Separate from the login
# limiter so that confirming a deletion cannot lock somebody out of signing
# in — and so that a stolen session cannot be used to guess the password by
# spending the account's login budget from the inside.
CONFIRM_PASSWORD = Limit("confirm_password", 10, timedelta(minutes=15))

ALL_LIMITS = (
    SEND_REQUEST,
    RESEND_VERIFICATION,
    REPORT,
    UPLOAD_TICKET,
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_ADDRESS,
    REGISTER,
    CONFIRM_PASSWORD,
)


def client_key(request: Request) -> str:
    """Who to count against when there is no account yet.

    `request.client.host` is the real address only because uvicorn runs with
    `proxy_headers=True` behind a trusted proxy. Without that it would be the
    proxy's own address and every visitor would share one bucket — so if the
    deployment ever stops being behind a proxy that sets X-Forwarded-For, this
    silently becomes a global limit.
    """
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _window_start(now: datetime, window: timedelta) -> datetime:
    seconds = int(window.total_seconds())
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=now.tzinfo)


async def _count(db: AsyncSession, bucket: str, start: datetime) -> RateLimitWindow | None:
    return (
        await db.execute(
            select(RateLimitWindow)
            .where(RateLimitWindow.bucket == bucket)
            .where(RateLimitWindow.window_start == start)
        )
    ).scalar_one_or_none()


async def consume(db: AsyncSession, limit: Limit, subject: str, *, now: datetime | None = None) -> int:
    """Record one use, or raise `RateLimited`. Returns what is left.

    The check and the increment are one step on purpose: two callers checking
    before either increments is how a limit gets exceeded by exactly the
    number of people looking at once.

    **Commits.** See the module docstring — a counter that rolls back with the
    request it was counting does nothing on the path that matters. Call this
    before any other write in the handler.
    """
    now = now or utcnow()

    if db.new or db.dirty or db.deleted:
        # Not fatal, but it means this commit is carrying somebody else's
        # half-finished work — which is the kind of thing that is invisible
        # until the request after it fails and the partial write is already in.
        log.warning("rate_limit_consume_with_pending_changes", action=limit.action)
    bucket = limit.bucket(subject)
    start = _window_start(now, limit.window)
    previous_start = start - limit.window

    current = await _count(db, bucket, start)
    previous = await _count(db, bucket, previous_start)

    # How much of the previous window is still inside the trailing window.
    elapsed = (now - start).total_seconds() / limit.window.total_seconds()
    carried = (previous.count if previous else 0) * (1.0 - elapsed)
    used = (current.count if current else 0) + carried

    if used >= limit.allowance:
        retry_after = max(1, math.ceil((start + limit.window - now).total_seconds()))
        log.info("rate_limited", action=limit.action, subject=subject, retry_after=retry_after)
        raise RateLimited(retry_after=retry_after)

    if current is None:
        current = RateLimitWindow(bucket=bucket, window_start=start, count=0)
        db.add(current)
    current.count += 1
    await db.commit()

    return max(0, limit.allowance - int(used) - 1)


async def sweep(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Drop windows nothing can still be counting against.

    Two windows back is the cutoff, not one: `consume` reads the previous
    window to weight it, so deleting it early would quietly hand everyone a
    fresh allowance at each boundary.
    """
    now = now or utcnow()
    longest = max(limit.window for limit in ALL_LIMITS)
    cutoff = now - (longest * 2)

    rows = (
        (await db.execute(select(RateLimitWindow).where(RateLimitWindow.window_start < cutoff)))
        .scalars()
        .all()
    )
    for row in rows:
        await db.delete(row)
    if rows:
        log.info("rate_limit_windows_swept", count=len(rows))
    return len(rows)
