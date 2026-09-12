"""Registration, login, and the token dependency."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import Photo, Prompt, User, get_db, utcnow
from backend.errors import Conflict, NotAuthenticated, NotFound
from backend.logging_config import get_logger

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


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


@router.post("/register", status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    email = req.email.strip().lower()

    user = User(
        email=email,
        password_hash=hash_password(req.password),
        display_name=req.display_name.strip(),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Deliberately the same wording as a duplicate check would give — the
        # unique index is the only thing that can decide this without a race.
        raise Conflict("That email is already registered.") from None
    await db.refresh(user)

    log.info("user_registered", user_id=user.id)
    return {"access_token": create_access_token(user.id, user.email), "token_type": "bearer"}


@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    email = req.email.strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    if not user or not verify_password(req.password, user.password_hash):
        raise NotAuthenticated("Incorrect email or password.")

    log.info("user_logged_in", user_id=user.id)
    return {"access_token": create_access_token(user.id, user.email), "token_type": "bearer"}


@router.get("/me")
async def get_me(
    user_id: str = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    user = await db.get(User, user_id)
    if not user:
        raise NotFound("Account not found.")

    prompts = (await db.execute(select(Prompt).where(Prompt.user_id == user_id))).scalars().all()
    photos = (await db.execute(select(Photo).where(Photo.user_id == user_id))).scalars().all()

    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "prompts": [{"question": p.question, "answer": p.answer} for p in prompts],
        "photos": [p.storage_url for p in photos],
    }
