"""Database engine, session factory and ORM models.

Option-set fields (dating intentions, vices, religion …) are plain strings
validated in the API layer against `backend.options`, not database enums — a
native enum needs a migration to add one value, and this vocabulary will grow.

Schema is owned by Alembic, never by `create_all`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
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


def embedding_column(dim: int):
    """A pgvector column on Postgres, JSON everywhere else.

    Only Postgres can do the ANN search the pair loop needs; SQLite just has to
    store and return the numbers so the rest of the pipeline is testable
    without a database server.
    """
    return Vector(dim).with_variant(JSON, "sqlite")


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
        connect_args=settings.db_connect_args,
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
    active = "active"
    # Removed from everyone else's experience, but still the owner of their
    # own data: a suspended account keeps its settings, its right to withdraw
    # consent and its right to delete itself. Suspension is a decision about
    # somebody's conduct towards others, not a forfeit of what is theirs.
    suspended = "suspended"
    deleted = "deleted"


class PromptKind(enum.StrEnum):
    written = "written"
    voice = "voice"


class ProcessingStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class ConnectionStatus(enum.StrEnum):
    requested = "requested"  # one opening message sent, waiting on a reply
    open = "open"  # both sides in; the conversation is live
    declined = "declined"  # the recipient said no, and that is final


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
# Accounts
# --------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_status", "status"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    birthdate = Column(Date, nullable=True)

    status = Column(String, nullable=False, default=UserStatus.pending_verification)

    email_verified_at = Column(UTCDateTime(), nullable=True)
    last_active_at = Column(UTCDateTime(), nullable=True)
    deleted_at = Column(UTCDateTime(), nullable=True)

    processing_status = Column(String, nullable=False, default=ProcessingStatus.pending)
    avatar_url = Column(String, nullable=True)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)

    # Granted from the command line only. There is deliberately no endpoint
    # that sets this: the one account able to suspend other people must not be
    # reachable through the same surface an attacker already has a session on.
    is_reviewer = Column(Boolean, nullable=False, default=False)

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


class MediaKind(enum.StrEnum):
    photo = "photo"
    voice = "voice"


class MediaStatus(enum.StrEnum):
    pending_upload = "pending_upload"  # signed URL issued, bytes not confirmed
    uploaded = "uploaded"  # bytes present, not yet processed
    processed = "processed"  # passed the gate and embedded
    rejected = "rejected"  # failed the gate; see gate_reason


class MediaAsset(Base):
    """One uploaded file. Bytes go straight from the browser to object storage;
    this row is the only thing that ever touches the API."""

    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_user_kind", "user_id", "kind", "status"),
        UniqueConstraint("object_key", name="uq_media_object_key"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False)

    object_key = Column(String, nullable=False)
    public_url = Column(String, nullable=True)
    content_type = Column(String, nullable=False)
    byte_size = Column(Integer, nullable=True)

    status = Column(String, nullable=False, default=MediaStatus.pending_upload)
    is_primary = Column(Boolean, nullable=False, default=False)
    display_order = Column(Integer, nullable=False, default=0)

    # Quality gate, from face detection. Populated for photos only.
    gate_reason = Column(String, nullable=True)
    face_count = Column(Integer, nullable=True)
    det_score = Column(Float, nullable=True)

    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)
    processed_at = Column(UTCDateTime(), nullable=True)


class ProfileEmbedding(Base):
    """The three vectors the pair loop reads.

    Stored separately rather than as one composite: collapsing them at write
    time would mean re-embedding everyone to change the blend, and the ranking
    weights are the thing most likely to move.
    """

    __tablename__ = "profile_embeddings"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    face_vector = Column(embedding_column(512), nullable=True)
    face_source_id = Column(String, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)

    voice_vector = Column(embedding_column(192), nullable=True)
    text_vector = Column(embedding_column(1024), nullable=True)

    transcript = Column(Text, nullable=True)
    transcript_language = Column(String, nullable=True)

    updated_at = Column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------


class Connection(Base):
    """One conversation between two people, in whatever state it has reached.

    There is exactly one row per pair, ever, keyed on the unordered
    `connection_key` — a second request after a decline is not a new
    connection, it is the same answer again. `user_a_id` / `user_b_id` are
    stored in the same canonical order the key is built from, so a row and its
    key can never disagree about who it is between.

    `initiator_id` is who reached out. It is null for a connection that opened
    because both people crossed the line independently: nobody asked, so
    naming an asker would be a small lie about how it happened.
    """

    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("connection_key", name="uq_connection_key"),
        Index("ix_connections_user_a", "user_a_id", "status"),
        Index("ix_connections_user_b", "user_b_id", "status"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    user_a_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_b_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    connection_key = Column(String, nullable=False)

    initiator_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default=ConnectionStatus.requested)

    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)
    opened_at = Column(UTCDateTime(), nullable=True)
    closed_at = Column(UTCDateTime(), nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_connection_created", "connection_id", "created_at"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    connection_id = Column(
        String, ForeignKey("connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message_text = Column(Text)
    status = Column(String, nullable=False, default=ChatStatus.sent)
    created_at = Column(UTCDateTime(), default=utcnow, nullable=False)


# --------------------------------------------------------------------------
# The pair loop
# --------------------------------------------------------------------------


class RatingKind(enum.StrEnum):
    visual = "visual"  # from the photo-only round
    profile = "profile"  # from the full-profile round


class Rating(Base):
    """Glicko-2 state for one subject, within one audience.

    `audience_segment` is the `interested_in` segment of the viewers who
    produced the comparisons behind this rating — not the subject's own
    identity. A subject visible_as both "woman" and "nonbinary" carries two
    independent visual ratings, one per population that can be shown them,
    because a rating only means something within the population that judged
    it: averaging across audiences would describe neither.

    Rows are created lazily, on first use, with Glicko-2's own defaults
    (1500 / 350 / 0.06) rather than backfilled — a person who has never been
    shown to a given audience has no rating there yet, which is simply true.
    """

    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("subject_id", "audience_segment", "kind", name="uq_rating_subject_segment_kind"),
        Index("ix_ratings_subject", "subject_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    subject_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    audience_segment = Column(String, nullable=False)
    kind = Column(String, nullable=False)

    rating = Column(Float, nullable=False, default=1500.0)
    deviation = Column(Float, nullable=False, default=350.0)
    volatility = Column(Float, nullable=False, default=0.06)
    comparison_count = Column(Integer, nullable=False, default=0)

    updated_at = Column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


class PairRound(enum.StrEnum):
    round_1 = "round_1"  # photo only
    round_2 = "round_2"  # full profile, at least 48h later


class PairStatus(enum.StrEnum):
    pending = "pending"  # queued, not yet served
    shown = "shown"  # served to the viewer, awaiting their choice
    decided = "decided"  # the viewer chose
    expired = "expired"  # a round-2 window passed with the pair never shown


class Pairing(Base):
    """One pairing, for one viewer. Doubles as both the upcoming queue and the
    permanent record of what was shown and decided — a decided or expired row
    is never deleted, so this is also the impression history.

    `pair_key` is the unordered key of the two subject ids, which is what the
    uniqueness constraint below actually enforces: (A, B) and (B, A) are the
    same pair, and this viewer must never be shown it twice in the same round.
    """

    __tablename__ = "pairings"
    __table_args__ = (
        UniqueConstraint("viewer_id", "pair_key", "round", name="uq_pairing_viewer_pair_round"),
        Index("ix_pairings_serve", "viewer_id", "status", "round", "position"),
        Index("ix_pairings_due", "status", "round", "due_at"),
        Index("ix_pairings_subject_a", "subject_a_id"),
        Index("ix_pairings_subject_b", "subject_b_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    viewer_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_a_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_b_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    pair_key = Column(String, nullable=False)
    # The interested_in segment this pairing was generated under. Both
    # subjects were drawn from viewers-interested-in-this-segment at
    # generation time, and it is what the rating updates key off.
    segment = Column(String, nullable=False)

    round = Column(String, nullable=False, default=PairRound.round_1)
    status = Column(String, nullable=False, default=PairStatus.pending)
    position = Column(Integer, nullable=False, default=0)

    # Round 2 only: the earliest moment this may be served. Set when round 1
    # is decided; a scheduler (or the serve path) promotes it once due.
    due_at = Column(UTCDateTime(), nullable=True)

    chosen_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(UTCDateTime(), nullable=True)

    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)
    shown_at = Column(UTCDateTime(), nullable=True)


class ViewerPreference(Base):
    """One viewer's learned taste, as a direction in the face embedding space.

    The same 512 dimensions a face lives in, so the two can be compared
    directly — the vector is not a face, it is the direction that has been
    separating the faces this viewer picks from the ones they pass over.

    Per segment, like `Rating`, and for the same reason: someone interested in
    more than one segment is not one taste applied twice.

    Only round-1 decisions train it. See `backend/preference.py` for why.
    """

    __tablename__ = "viewer_preferences"
    __table_args__ = (UniqueConstraint("viewer_id", "segment", name="uq_viewer_preference"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    viewer_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    segment = Column(String, nullable=False)

    weights = Column(embedding_column(512), nullable=True)
    observations = Column(Integer, nullable=False, default=0)

    updated_at = Column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


def pair_key(subject_a_id: str, subject_b_id: str) -> str:
    """Canonical unordered key for two subjects, order-independent."""
    return "|".join(sorted((subject_a_id, subject_b_id)))


# --------------------------------------------------------------------------
# What one viewer has demonstrated about one other person
# --------------------------------------------------------------------------


class AffinityState(enum.StrEnum):
    learning = "learning"  # the interval still straddles the threshold
    unlocked = "unlocked"  # confidently above it — the profile is revealed
    settled = "settled"  # confidently below it, or out of patience


class Affinity(Base):
    """One viewer's running record of one subject: shown, picked, and what
    that adds up to.

    Denormalised from `pairings` deliberately. The counts are derivable — the
    pairing rows are never deleted — but the unlock test runs on every single
    decision and needs the totals for exactly two people out of a viewer's
    entire history, which is the shape a denormalised row is for.

    It is also where `unlocked_at` lives, and that is not derivable at all.
    An unlock is an *event*: it grants a person something, and later evidence
    that would no longer clear the bar must not silently take it back.

    Unlike `Rating`, this is not keyed by segment. A rating describes how a
    population sees someone, so it has to be per-audience. This describes what
    one person thinks of one other person, which does not change because the
    pair happened to be drawn under a different segment.
    """

    __tablename__ = "affinities"
    __table_args__ = (
        UniqueConstraint("viewer_id", "subject_id", name="uq_affinity_viewer_subject"),
        Index("ix_affinities_viewer_state", "viewer_id", "state"),
        Index("ix_affinities_subject", "subject_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    viewer_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Decided appearances only. A pair that was served and never answered, or
    # one that expired unshown, is not evidence about anything.
    shown = Column(Integer, nullable=False, default=0)
    picked = Column(Integer, nullable=False, default=0)

    state = Column(String, nullable=False, default=AffinityState.learning)
    # The Wilson bounds as of the last decision. Stored for ordering and for
    # answering "why did this unlock?" later — never shown to anyone, under
    # the same rule that keeps ratings private.
    confidence_low = Column(Float, nullable=False, default=0.0)
    confidence_high = Column(Float, nullable=False, default=1.0)

    unlocked_at = Column(UTCDateTime(), nullable=True)
    last_decided_at = Column(UTCDateTime(), nullable=True)
    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


class Block(Base):
    """One person's decision not to encounter another.

    Stored one-directionally — who blocked whom is a fact worth keeping — but
    read symmetrically everywhere. If either side has blocked, neither is shown
    to the other, neither can write, and any conversation between them closes.
    A block that only worked one way would let the blocked person keep seeing
    someone who has removed themselves, which is the case it exists for.
    """

    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),
        Index("ix_blocks_blocker", "blocker_id"),
        Index("ix_blocks_blocked", "blocked_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    blocker_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    blocked_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)


class ReportReason(enum.StrEnum):
    fake = "fake"  # not who they say they are
    harassment = "harassment"
    sexual = "sexual"  # unsolicited sexual content
    underage = "underage"
    hate = "hate"
    other = "other"


class ReportStatus(enum.StrEnum):
    open = "open"
    actioned = "actioned"
    dismissed = "dismissed"


class Report(Base):
    """A report, and what a human decided about it.

    Kept after review rather than deleted: a single dismissed report means
    little, and four dismissed reports about the same person from four
    unrelated people is the pattern that matters. `reporter_id` survives the
    reporter's own deletion as NULL so the count stays honest.
    """

    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_queue", "status", "created_at"),
        Index("ix_reports_subject", "subject_id"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    reporter_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Indexed by ix_reports_subject in __table_args__; `index=True` here as
    # well would create a second, identical index.
    subject_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    reason = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    # Where it happened — a connection id or a pairing id — so a reviewer can
    # see the message that prompted it rather than guessing.
    context = Column(String, nullable=True)

    # *What* was reported, when it was a particular thing rather than the
    # person in general. "This photo" and "that answer" are the two reports
    # somebody actually wants to make about a profile, and a reviewer looking
    # at six photos cannot act on a report that does not say which one.
    # SET NULL rather than CASCADE: the report outlives the thing it is about,
    # because deleting the photo is one of the outcomes.
    subject_media_id = Column(String, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    subject_prompt_id = Column(String, ForeignKey("prompt_responses.id", ondelete="SET NULL"), nullable=True)

    status = Column(String, nullable=False, default=ReportStatus.open)
    # Who decided. "A human reads it" is only a real promise if the human is
    # named — a decision nobody is attached to is a decision nobody owns.
    reviewer_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(UTCDateTime(), nullable=True)
    reviewer_note = Column(Text, nullable=True)

    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)


class RateLimitWindow(Base):
    """One counter, for one bucket, in one window.

    In the database rather than in memory because the app runs more than one
    worker: a per-process counter makes the real allowance whatever was
    configured times however many processes happen to be up, which is not a
    number anybody chose.

    Rows are swept by the worker. See `backend/ratelimit.py` for why the sweep
    keeps two windows rather than one.
    """

    __tablename__ = "rate_limit_windows"
    __table_args__ = (
        UniqueConstraint("bucket", "window_start", name="uq_rate_limit_window"),
        Index("ix_rate_limit_sweep", "window_start"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    bucket = Column(String, nullable=False)
    window_start = Column(UTCDateTime(), nullable=False)
    count = Column(Integer, nullable=False, default=0)


class BiometricConsent(Base):
    """Permission to compute a numeric representation of somebody's face.

    A face embedding is a biometric identifier under India's DPDP Act, which
    means it needs consent that is specific, informed and withdrawable — not a
    line buried in terms of service. So it is its own record with its own
    timestamps, rather than a boolean on the user.

    `purpose` and `notice_version` are stored with the grant because consent is
    to a *stated purpose* at a point in time. Changing what the vectors are
    used for, or the words used to explain it, makes previously collected
    consent no longer consent to the new thing — and that is impossible to
    argue either way without knowing which version somebody agreed to.

    Withdrawal is kept rather than deleted, for the same reason a report
    outlives its reporter: "this person withdrew on this date" is the evidence
    that the deletion which followed was justified.
    """

    __tablename__ = "biometric_consents"
    __table_args__ = (Index("ix_biometric_consent_user", "user_id"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    purpose = Column(Text, nullable=False)
    notice_version = Column(String, nullable=False)

    granted_at = Column(UTCDateTime(), nullable=False, default=utcnow)
    withdrawn_at = Column(UTCDateTime(), nullable=True)
