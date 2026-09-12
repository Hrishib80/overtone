"""Database engine, session factory and ORM models.

Scope note: this is the phase-00 model set — only what auth and chat still use.
The pairwise tables (rating, pair_queue, pair_impression, prompt_library,
scope, waitlist_entry …) arrive in phases 01–03 as Alembic migrations. The
like/swipe and call-era tables have been removed along with their code.

Schema is owned by Alembic, never by `create_all`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from backend.config import settings

Base = declarative_base()


def utcnow() -> datetime:
    """Timezone-aware UTC. `datetime.utcnow()` is deprecated and returns naive values."""
    return datetime.now(UTC)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def _make_engine():
    url = settings.database_url
    if not url:
        return None
    # SQLite (used by the test suite) rejects pool sizing arguments.
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=False, future=True)
    return create_async_engine(
        url,
        echo=False,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


engine = _make_engine()
AsyncSessionLocal = (
    async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False) if engine else None
)


async def get_db():
    if AsyncSessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured.")
    async with AsyncSessionLocal() as session:
        yield session


class ProcessingStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class MatchStatus(enum.StrEnum):
    pending = "pending"
    matched = "matched"


class ChatStatus(enum.StrEnum):
    sent = "sent"
    delivered = "delivered"
    read = "read"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    processing_status = Column(Enum(ProcessingStatus), default=ProcessingStatus.pending, nullable=False)
    avatar_url = Column(String, nullable=True)


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)


class Photo(Base):
    __tablename__ = "photos"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    storage_url = Column(String, nullable=False)
    moderation_passed = Column(String, default="pending", nullable=False)


class MatchRecord(Base):
    __tablename__ = "match_records"
    __table_args__ = (
        Index("ix_match_records_user_a", "user_a_id"),
        Index("ix_match_records_user_b", "user_b_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    user_a_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_b_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(Enum(MatchStatus), default=MatchStatus.pending, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_match_created", "match_id", "created_at"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    match_id = Column(String, ForeignKey("match_records.id", ondelete="CASCADE"), index=True, nullable=False)
    from_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message_text = Column(Text)
    status = Column(Enum(ChatStatus), default=ChatStatus.sent, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
