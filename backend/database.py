"""Database engine, session factory and ORM models.

Option-set fields (dating intentions, vices, religion …) are plain strings
validated in the API layer against `backend.options`, not database enums — a
native enum needs a migration to add one value, and this vocabulary will grow.

The pairwise tables (rating, pair_queue, pair_impression …) arrive in phase 03.
Schema is owned by Alembic, never by `create_all`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.types import TypeDecorator

from backend.config import settings

Base = declarative_base()


def utcnow() -> datetime:
    """Timezone-aware UTC. `datetime.utcnow()` is deprecated and returns naive values."""
    return datetime.now(UTC)


def generate_uuid() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """A timestamp that is always timezone-aware UTC, on every backend.

    SQLite has no timezone storage, so a `UTCDateTime()` column hands
    back naive values there and aware ones on Postgres — and comparing a naive
    value against `utcnow()` raises. Normalising in both directions means the
    application only ever sees aware UTC, and the test suite exercises the same
    behaviour production gets.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


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


# --------------------------------------------------------------------------
# Enumerations that are genuinely closed — lifecycle states, not vocabulary.
# --------------------------------------------------------------------------


class UserStatus(enum.StrEnum):
    pending_verification = "pending_verification"
    onboarding = "onboarding"
    waitlisted = "waitlisted"
    active = "active"
    deleted = "deleted"


class ScopeKind(enum.StrEnum):
    campus = "campus"
    city = "city"


class ScopeStatus(enum.StrEnum):
    building = "building"  # accepting signups; matching not yet switched on
    open = "open"
    closed = "closed"


class WaitlistStatus(enum.StrEnum):
    waiting = "waiting"
    invited = "invited"
    claimed = "claimed"
    expired = "expired"


class PromptKind(enum.StrEnum):
    written = "written"
    voice = "voice"


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


# --------------------------------------------------------------------------
# Seeded reference data
# --------------------------------------------------------------------------


class GenderIdentity(Base):
    """Expressive and open. Shown on the profile; never used in a filter."""

    __tablename__ = "gender_identities"

    id = Column(String, primary_key=True)
    label = Column(String, nullable=False)
    default_visible_as = Column(JSON, nullable=False, default=list)
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class Sexuality(Base):
    """Display only. Matching uses interested_in, never this."""

    __tablename__ = "sexualities"

    id = Column(String, primary_key=True)
    label = Column(String, nullable=False)
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class PromptLibrary(Base):
    __tablename__ = "prompt_library"

    id = Column(String, primary_key=True)
    text = Column(String, nullable=False)
    category = Column(String, nullable=False, index=True)
    emphasis = Column(String, nullable=True)
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


# --------------------------------------------------------------------------
# Scope, caps and waitlist
# --------------------------------------------------------------------------


class Scope(Base):
    """A campus now, a city later. Every match query is scoped through this."""

    __tablename__ = "scopes"

    id = Column(String, primary_key=True, default=generate_uuid)
    slug = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default=ScopeKind.campus)
    email_domains = Column(JSON, nullable=False, default=list)
    status = Column(String, nullable=False, default=ScopeStatus.building)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)


class SegmentCap(Base):
    """Caps are per segment, never one headline number.

    A single global cap fills one segment in a week and starves the other, which
    is the standard way a dating app dies before it starts.
    """

    __tablename__ = "segment_caps"
    __table_args__ = (UniqueConstraint("scope_id", "segment", name="uq_segment_cap"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    scope_id = Column(String, ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False, index=True)
    segment = Column(String, nullable=False)
    cap = Column(Integer, nullable=False)


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_waitlist_user"),
        Index("ix_waitlist_queue", "scope_id", "segment", "status", "joined_at"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope_id = Column(String, ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False)
    segment = Column(String, nullable=False)
    status = Column(String, nullable=False, default=WaitlistStatus.waiting)
    joined_at = Column(UTCDateTime(), default=utcnow, nullable=False)
    invited_at = Column(UTCDateTime(), nullable=True)
    # An unclaimed invitation must expire, or a freed slot sits dead forever.
    claim_expires_at = Column(UTCDateTime(), nullable=True)


class FormerMember(Base):
    """A salted hash of the email, and nothing else.

    Exists only so that rejoining after deletion goes to the back of the queue.
    Deliberately unable to identify anyone or reconstruct an account, and it
    expires on its own so it does not become durable personal data.
    """

    __tablename__ = "former_members"
    __table_args__ = (Index("ix_former_member_lookup", "scope_id", "email_hash"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    scope_id = Column(String, ForeignKey("scopes.id", ondelete="CASCADE"), nullable=False)
    email_hash = Column(String, nullable=False)
    left_at = Column(UTCDateTime(), default=utcnow, nullable=False)
    expires_at = Column(UTCDateTime(), nullable=False)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_scope_segment_status", "scope_id", "cap_segment", "status"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    birthdate = Column(Date, nullable=True)

    scope_id = Column(String, ForeignKey("scopes.id", ondelete="RESTRICT"), nullable=True, index=True)
    status = Column(String, nullable=False, default=UserStatus.pending_verification)

    # Which segment this account counts against for cap accounting. Derived from
    # the primary `visible_as` choice; someone visible in several searches is
    # counted once, in their primary one.
    cap_segment = Column(String, nullable=True)

    email_verified_at = Column(UTCDateTime(), nullable=True)
    last_active_at = Column(UTCDateTime(), nullable=True)
    deleted_at = Column(UTCDateTime(), nullable=True)

    processing_status = Column(String, nullable=False, default=ProcessingStatus.pending)
    avatar_url = Column(String, nullable=True)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)

    @property
    def age(self) -> int | None:
        if not self.birthdate:
            return None
        today = date.today()
        return (
            today.year
            - self.birthdate.year
            - ((today.month, today.day) < (self.birthdate.month, self.birthdate.day))
        )


class EmailVerification(Base):
    """Single-use tokens, stored only as a hash — so a database read cannot be
    replayed as a working verification link."""

    __tablename__ = "email_verifications"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(UTCDateTime(), nullable=False)
    used_at = Column(UTCDateTime(), nullable=True)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)


class UserVisibleAs(Base):
    """Whose searches this person appears in."""

    __tablename__ = "user_visible_as"
    __table_args__ = (UniqueConstraint("user_id", "segment", name="uq_visible_as"),)

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    segment = Column(String, primary_key=True)
    is_primary = Column(Boolean, nullable=False, default=False)


class UserInterestedIn(Base):
    """Whose profiles this person sees. Applied in both directions at match time."""

    __tablename__ = "user_interested_in"
    __table_args__ = (UniqueConstraint("user_id", "segment", name="uq_interested_in"),)

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    segment = Column(String, primary_key=True)


class Profile(Base):
    """Hinge's grouping — vitals, virtues, vices — kept because it is
    well-tested and students will recognise it."""

    __tablename__ = "profiles"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    # Identity (expressive)
    gender_identity_id = Column(String, ForeignKey("gender_identities.id"), nullable=True)
    custom_gender = Column(String, nullable=True)
    sexuality_id = Column(String, ForeignKey("sexualities.id"), nullable=True)
    custom_sexuality = Column(String, nullable=True)
    pronouns = Column(String, nullable=True)

    # Vitals
    height_cm = Column(Integer, nullable=True)
    location = Column(String, nullable=True)
    hometown = Column(String, nullable=True)
    ethnicities = Column(JSON, nullable=False, default=list)
    children = Column(String, nullable=True)
    family_plans = Column(String, nullable=True)
    pets = Column(String, nullable=True)
    zodiac = Column(String, nullable=True)

    # Virtues
    job_title = Column(String, nullable=True)
    workplace = Column(String, nullable=True)
    school = Column(String, nullable=True)
    education_level = Column(String, nullable=True)
    religion = Column(String, nullable=True)
    politics = Column(String, nullable=True)
    languages = Column(JSON, nullable=False, default=list)
    dating_intentions = Column(String, nullable=True)
    relationship_type = Column(String, nullable=True)

    # Vices
    drinking = Column(String, nullable=True)
    smoking = Column(String, nullable=True)
    marijuana = Column(String, nullable=True)
    drugs = Column(String, nullable=True)

    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class PromptResponse(Base):
    """Three written, one voice. Updated in place rather than deleted and
    recreated — the pairwise loop attaches statistics to these ids."""

    __tablename__ = "prompt_responses"
    __table_args__ = (
        UniqueConstraint("user_id", "slot", name="uq_prompt_slot"),
        Index("ix_prompt_responses_user", "user_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    prompt_id = Column(String, ForeignKey("prompt_library.id"), nullable=False)
    kind = Column(String, nullable=False, default=PromptKind.written)
    slot = Column(Integer, nullable=False)

    body = Column(Text, nullable=True)
    audio_key = Column(String, nullable=True)
    audio_duration_ms = Column(Integer, nullable=True)
    transcript = Column(Text, nullable=True)
    language = Column(String, nullable=True)

    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class Photo(Base):
    __tablename__ = "photos"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    storage_url = Column(String, nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    display_order = Column(Integer, nullable=False, default=0)
    moderation_passed = Column(String, nullable=False, default="pending")
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)


# --------------------------------------------------------------------------
# Conversations (reworked when the message-request unlock lands in phase 04)
# --------------------------------------------------------------------------


class MatchRecord(Base):
    __tablename__ = "match_records"
    __table_args__ = (
        Index("ix_match_records_user_a", "user_a_id"),
        Index("ix_match_records_user_b", "user_b_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    user_a_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_b_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False, default=MatchStatus.pending)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_match_created", "match_id", "created_at"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    match_id = Column(String, ForeignKey("match_records.id", ondelete="CASCADE"), index=True, nullable=False)
    from_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message_text = Column(Text)
    status = Column(String, nullable=False, default=ChatStatus.sent)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)
