"""Who may join.

One rule now: you have to be 18. Everything else that used to live here — the
campus domain check, per-segment caps, the waitlist, the former-member hash —
is gone, along with the `Scope` it all hung from.

That was a real trade, and it is worth being honest about what was given up.
The campus domain was the identity anchor: it bounded the population to people
who genuinely belonged somewhere, and it made ban evasion cost something,
because getting back in meant getting another address at that university.
Joining with a personal account removes both. Email verification still proves
somebody can read mail at the address they gave, which stops the laziest
duplicate accounts, but anyone can get another free mailbox in a minute.

What now carries that weight instead: verification, the 18+ check, blocking,
reporting, and rate limits. If ban evasion turns out to be a real problem, the
answers are phone verification or invite codes, not bringing the domain back —
the domain only ever worked because a university mailbox is hard to get twice.

There is no population boundary at all any more: every active account is in one
pool. The seam where a boundary would go back is a single filter in
`pairing._eligible_candidates`, plus whatever column decides which pool
somebody is in.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import date

from backend.errors import AppError
from backend.logging_config import get_logger
from backend.options import MAX_AGE, MIN_AGE

log = get_logger(__name__)


class UnderageError(AppError):
    status_code = 403
    code = "underage"
    message = "You must be 18 or older to use Overtone."


def age_on(birthdate: date, today: date | None = None) -> int:
    today = today or date.today()
    return today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))


def check_age(birthdate: date) -> None:
    age = age_on(birthdate)
    if age < MIN_AGE:
        raise UnderageError()
    if age > MAX_AGE:
        raise AppError("That date of birth doesn't look right.")


def new_verification_token() -> tuple[str, str]:
    """A token to send, and the hash to keep. Storing only the hash means a
    database read cannot be replayed as a working verification link."""
    token = secrets.token_urlsafe(32)
    return token, hash_verification_token(token)


def hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
