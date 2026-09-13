"""Registration, login, and the token dependency.

There is no email verification. It was removed deliberately, and the trade is
worth stating where somebody will read it before putting this in front of real
people: nothing now proves that the person signing up can read mail at the
address they typed. Anyone can register as anyone, and a banned account can
come back for the price of a new address.

What is left holding the line: the 18+ check, blocking, reporting, the review
queue, and rate limits. If ban evasion becomes real, the answer is phone
verification or invite codes — something that costs the attacker something —
rather than putting the email round trip back, because a mailbox is free.

The seam is still here. `users.email_verified_at` exists and is never set; the
gate that used to read it is gone from `profile.submit`. Turning verification
back on means re-adding a channel to send on and restoring that check.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, Request
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend import access
from backend.config import settings
from backend.database import (
    Profile,
    User,
    UserStatus,
    get_db,
    utcnow,
)
from backend.errors import Conflict, NotAuthenticated, NotAuthorized
from backend.logging_config import get_logger
from backend.ratelimit import (
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_ADDRESS,
    REGISTER,
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


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
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
# column accurate to the second when nothing needs better than the minute —
# and what needs it at all is `notify`, deciding whether somebody is sitting
# in the app right now and should be left alone.
ACTIVITY_RESOLUTION = timedelta(minutes=5)


async def current_user(user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotAuthenticated("Account not found.")

    # Recorded here rather than at login, because a login is not activity —
    # people stay signed in for weeks, and "last seen" taken from the last
    # sign-in would have told `notify` that a person reading their inbox right
    # now was last here on Tuesday.
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
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    birthdate: date


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


@router.post("/register", status_code=201)
async def register(
    req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    # Counted per address, since there is no account to count against yet.
    await consume(db, REGISTER, client_key(request))
    email = req.email.strip().lower()

    # Refuses before an account exists.
    access.check_age(req.birthdate)

    user = User(
        email=email,
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
        raise Conflict("That email is already registered.") from None

    db.add(Profile(user_id=user.id))
    await db.commit()
    await db.refresh(user)

    log.info("user_registered", user_id=user.id)

    return {
        "access_token": create_access_token(user.id, user.email),
        "token_type": "bearer",
        "status": user.status,
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    email = req.email.strip().lower()
    # Per account first, because that is where a brute-force attempt is
    # aimed and it is the limit that can afford to be tight. The per-address
    # one is loose on purpose — see the note in backend/ratelimit.py about
    # what a campus NAT does to an address-keyed limit.
    await consume(db, LOGIN_PER_ACCOUNT, f"email:{email}")
    await consume(db, LOGIN_PER_ADDRESS, client_key(request))
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    if not user or user.deleted_at is not None or not verify_password(req.password, user.password_hash):
        raise NotAuthenticated("Incorrect email or password.")

    user.last_active_at = utcnow()
    await db.commit()

    log.info("user_logged_in", user_id=user.id)
    return {
        "access_token": create_access_token(user.id, user.email),
        "token_type": "bearer",
        "status": user.status,
    }


@router.get("/me")
async def get_me(user: User = Depends(current_user)) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "age": user.age,
        "status": user.status,
        "email_verified": user.email_verified_at is not None,
        "avatar_url": user.avatar_url,
        # So the app knows whether to offer the queue. It is a hint for the
        # interface only — every moderation route checks the column itself.
        "is_reviewer": user.is_reviewer,
    }
