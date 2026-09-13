"""Registration, login, and the token dependency.

An account is a username and a password, and nothing else. There is no email
— not for verification, not for notifications, not as a login — so the rules
for what a username may be live in `backend/usernames.py` and this module only
applies them.

Two trades worth stating where somebody will read them before putting this in
front of real people:

* **Nothing proves who anybody is.** A banned account comes back for the price
  of a new username, which is free. What holds the line is the 18+ check,
  blocking, reporting, the review queue and rate limits. If ban evasion becomes
  real the answer is phone verification or invite codes — something that costs
  the attacker something.
* **A forgotten password is a lost account.** There is no address to send a
  reset to. The join form says so; do not quietly add a recovery flow that
  asks for an email, because that brings back the thing that was removed.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, Request
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend import access, usernames
from backend.config import settings
from backend.database import (
    Profile,
    User,
    UserStatus,
    get_db,
    utcnow,
)
from backend.errors import AppError, Conflict, NotAuthenticated, NotAuthorized
from backend.logging_config import get_logger
from backend.ratelimit import (
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_ADDRESS,
    REGISTER,
    USERNAME_CHECK,
    client_key,
    consume,
)

log = get_logger(__name__)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter(prefix="/api/auth", tags=["auth"])


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": utcnow() + timedelta(hours=settings.jwt_expiry_hours),
        "iat": utcnow(),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


async def require_auth(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise NotAuthenticated()
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise NotAuthenticated("Your session has expired. Sign in again.") from None
    user_id = payload.get("sub")
    if not user_id:
        raise NotAuthenticated()
    return user_id


# How stale `last_active_at` has to be before a request is worth a write. Every
# authenticated request could update it, but that is a write per read for a
# column accurate to the second when nothing needs better than the minute.
# Nothing reads it today; it is kept as the seam for presence in chat.
ACTIVITY_RESOLUTION = timedelta(minutes=5)


async def current_user(user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotAuthenticated("Account not found.")

    # Recorded here rather than at login, because a login is not activity —
    # people stay signed in for weeks, and "last seen" taken from the last
    # sign-in would say a person reading their inbox right now was last here
    # on Tuesday.
    now = utcnow()
    if user.last_active_at is None or now - user.last_active_at > ACTIVITY_RESOLUTION:
        user.last_active_at = now
        await db.commit()

    return user


async def require_member(user: User = Depends(current_user)) -> User:
    """Everything a suspended account may not do — which is everything that
    involves another person.

    Applied as a *router* dependency rather than per route, so a route added
    later to one of those routers cannot forget it. The two routers it is
    deliberately absent from are `auth` and `account`: a suspended person can
    still sign in, see why, withdraw their consent and delete themselves.
    Suspension is a judgement about how they treated other people; it is not
    a forfeit of what is theirs.
    """
    if user.status == UserStatus.suspended:
        raise NotAuthorized(
            "Your account is suspended. You can still delete it, or take back "
            "permission to analyse your photos, from your settings."
        )
    return user


def verify_ws_token(token: str) -> str | None:
    """Token check for the WebSocket handshake, which cannot send headers."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    return payload.get("sub")


class RegisterRequest(BaseModel):
    # Loose here on purpose: the real rules are in `usernames.problem`, which
    # says *what* is wrong in words a person can act on. A pydantic pattern
    # would reject the same input with a regex in the error message.
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    birthdate: date


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def _username_or_refuse(raw: str) -> str:
    name = usernames.normalise(raw)
    reason = usernames.problem(name)
    if reason:
        raise AppError(reason, field="username")
    return name


@router.get("/username-available")
async def username_available(
    name: str, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    """Whether a username can be taken, asked while somebody is still typing.

    This does say whether an account exists under a name, and that is worth
    being honest about: registration has to refuse a taken name anyway, so the
    same fact is one form submission away regardless. Usernames are private —
    no member ever sees another's — which keeps this a login handle rather than
    a directory. Counted per address, loosely, for the campus-NAT reason in
    backend/ratelimit.py.
    """
    await consume(db, USERNAME_CHECK, client_key(request))
    normalised = usernames.normalise(name)
    reason = usernames.problem(normalised)
    if reason:
        return {"username": normalised, "available": False, "reason": reason}
    taken = (await db.execute(select(User.id).where(User.username == normalised))).first()
    return {
        "username": normalised,
        "available": taken is None,
        "reason": "That one is taken." if taken else None,
    }


@router.post("/register", status_code=201)
async def register(
    req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    # Counted per address, since there is no account to count against yet.
    await consume(db, REGISTER, client_key(request))
    username = _username_or_refuse(req.username)

    # Refuses before an account exists.
    access.check_age(req.birthdate)

    user = User(
        username=username,
        password_hash=hash_password(req.password),
        display_name=req.display_name.strip(),
        birthdate=req.birthdate,
        # Straight into onboarding. There is nothing to wait for.
        status=UserStatus.onboarding,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # The unique index is the real check; looking first would race.
        raise Conflict("That username is taken.", field="username") from None

    db.add(Profile(user_id=user.id))
    await db.commit()
    await db.refresh(user)

    log.info("user_registered", user_id=user.id)

    return {
        "access_token": create_access_token(user.id, user.username),
        "token_type": "bearer",
        "status": user.status,
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    # Normalised but not validated: a name that could never be registered
    # gets the same answer as a wrong password, rather than a hint that it
    # was the username that was wrong.
    username = usernames.normalise(req.username)
    # Per account first, because that is where a brute-force attempt is
    # aimed and it is the limit that can afford to be tight. The per-address
    # one is loose on purpose — see the note in backend/ratelimit.py about
    # what a campus NAT does to an address-keyed limit.
    await consume(db, LOGIN_PER_ACCOUNT, f"username:{username}")
    await consume(db, LOGIN_PER_ADDRESS, client_key(request))
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()

    if not user or user.deleted_at is not None or not verify_password(req.password, user.password_hash):
        raise NotAuthenticated("Incorrect username or password.")

    user.last_active_at = utcnow()
    await db.commit()

    log.info("user_logged_in", user_id=user.id)
    return {
        "access_token": create_access_token(user.id, user.username),
        "token_type": "bearer",
        "status": user.status,
    }


@router.get("/me")
async def get_me(user: User = Depends(current_user)) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "age": user.age,
        "status": user.status,
        "avatar_url": user.avatar_url,
        # So the app knows whether to offer the queue. It is a hint for the
        # interface only — every moderation route checks the column itself.
        "is_reviewer": user.is_reviewer,
    }
