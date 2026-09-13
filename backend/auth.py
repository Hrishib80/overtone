"""Registration, verification, login, and the token dependency."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, Request
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend import access, jobs, mail
from backend.config import settings
from backend.database import (
    EmailVerification,
    Profile,
    User,
    UserStatus,
    get_db,
    utcnow,
)
from backend.errors import AppError, Conflict, NotAuthenticated, NotAuthorized, NotFound
from backend.logging_config import get_logger
from backend.ratelimit import (
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_ADDRESS,
    REGISTER,
    RESEND_VERIFICATION,
    client_key,
    consume,
)

log = get_logger(__name__)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter(prefix="/api/auth", tags=["auth"])

VERIFICATION_TTL = timedelta(hours=24)


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


async def current_user(user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotAuthenticated("Account not found.")
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


class VerifyRequest(BaseModel):
    token: str = Field(min_length=8, max_length=256)


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
        status=UserStatus.pending_verification,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("That email is already registered.") from None

    db.add(Profile(user_id=user.id))

    token, token_hash = access.new_verification_token()
    db.add(
        EmailVerification(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=utcnow() + VERIFICATION_TTL,
        )
    )
    await _queue_verification_email(db, user, token)
    await db.commit()
    await db.refresh(user)

    log.info("user_registered", user_id=user.id)

    body: dict[str, object] = {
        "access_token": create_access_token(user.id, user.email),
        "token_type": "bearer",
        "status": user.status,
    }
    # Returned only when nothing was actually delivered. With a real provider
    # configured, handing the token back would undo the entire point of
    # sending it: the link is what proves the person reads mail at that
    # address, and a token in the response proves only that they typed it.
    if not mail.delivers():
        body["verification_token"] = token
    return body


async def _queue_verification_email(db: AsyncSession, user: User, token: str) -> None:
    """Enqueue rather than send inline. Registration must not fail because a
    mail provider is slow, and a link that failed to send once should be
    retried rather than lost."""
    message = mail.verification_message(to=user.email, token=token, display_name=user.display_name)
    await jobs.enqueue(
        db,
        jobs.JobKind.send_email,
        {"to": message.to, "subject": message.subject, "text": message.text},
        subject_id=user.id,
    )


class ResendRequest(BaseModel):
    model_config = {"extra": "forbid"}

    email: EmailStr


@router.post("/resend-verification")
async def resend_verification(
    req: ResendRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    """Send the link again.

    Always answers the same way, whether or not the address exists or is
    already verified. Anything else turns this into a way to ask "does this
    person have an Overtone account", which on a single campus is a question
    about somebody's private life.
    """
    await consume(db, RESEND_VERIFICATION, f"email:{req.email.strip().lower()}")
    await consume(db, RESEND_VERIFICATION, client_key(request))

    body: dict[str, object] = {"status": "sent"}
    user = (await db.execute(select(User).where(User.email == req.email.strip().lower()))).scalars().first()

    if user is None or user.email_verified_at is not None:
        return body

    # Old links stop working, so a forwarded one cannot be used later.
    await db.execute(
        update(EmailVerification)
        .where(EmailVerification.user_id == user.id)
        .where(EmailVerification.used_at.is_(None))
        .values(used_at=utcnow())
    )

    token, token_hash = access.new_verification_token()
    db.add(
        EmailVerification(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=utcnow() + VERIFICATION_TTL,
        )
    )
    await _queue_verification_email(db, user, token)
    await db.commit()

    if not mail.delivers():
        body["verification_token"] = token
    return body


@router.post("/verify-email")
async def verify_email(req: VerifyRequest, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.token_hash == access.hash_verification_token(req.token)
        )
    )
    verification = result.scalars().first()

    if verification is None or verification.used_at is not None:
        raise AppError("That verification link is not valid. Request a new one.")
    if verification.expires_at < utcnow():
        raise AppError("That verification link has expired. Request a new one.")

    user = await db.get(User, verification.user_id)
    if user is None:
        raise NotFound("Account not found.")

    verification.used_at = utcnow()
    user.email_verified_at = utcnow()
    if user.status == UserStatus.pending_verification:
        user.status = UserStatus.onboarding
    await db.commit()

    log.info("email_verified", user_id=user.id)
    return {"status": user.status}


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
