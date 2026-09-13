"""Biometric consent, and erasure.

Two obligations that turn out to be the same piece of work: both need an exact
answer to "what personal data does this account have, and where". Consent has
to name it before it is collected; erasure has to reach all of it afterwards.

**Face embeddings are biometric identifiers** under India's DPDP Act. That
puts them in a category needing consent that is specific, informed and
withdrawable, so a photo cannot be processed before it is given, withdrawal
deletes the vectors immediately, and both events are recorded with the notice
version they relate to.

**Erasure is explicit, not a cascade.** Every foreign key to `users` is
already `ON DELETE CASCADE` and that would mostly work — but this is a legal
obligation, and three things make relying on it a bad idea:

* SQLite does not enforce `ondelete` unless told to, so the whole test suite
  would exercise a behaviour production does not have, in the direction that
  silently leaves data behind.
* `jobs.subject_id` has no foreign key at all. A cascade cannot reach it, and
  those rows carry the user id and the object key of files being processed.
* "It cascaded" is not an answer to "what did you delete". Naming the tables
  means the answer can be read off the code and counted in the response.

The cascades stay as a backstop. This is the thing that actually runs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import storage
from backend.database import (
    Affinity,
    BiometricConsent,
    Block,
    ChatMessage,
    Connection,
    EmailVerification,
    MediaAsset,
    Pairing,
    Profile,
    ProfileEmbedding,
    PromptResponse,
    RateLimitWindow,
    Rating,
    Report,
    User,
    UserInterestedIn,
    UserVisibleAs,
    ViewerPreference,
    utcnow,
)
from backend.jobs import Job
from backend.logging_config import get_logger

log = get_logger(__name__)

# Bump when the wording below changes in a way that alters what somebody is
# agreeing to. Consent recorded against an older version is consent to the
# older statement, which is why the version is stored rather than assumed.
NOTICE_VERSION = "2026-09-1"

BIOMETRIC_PURPOSE = (
    "We work out a numeric description of your face from your photos and use it "
    "for one thing: finding other people who look similar, so the two of you can "
    "be shown side by side. It is never shown to anyone, never used to identify "
    "you anywhere else, and never shared. It is deleted when you withdraw this "
    "permission or delete your account."
)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


async def current_consent(db: AsyncSession, user_id: str) -> BiometricConsent | None:
    """The live grant, if there is one. Withdrawn rows stay but do not count."""
    return (
        (
            await db.execute(
                select(BiometricConsent)
                .where(BiometricConsent.user_id == user_id)
                .where(BiometricConsent.withdrawn_at.is_(None))
                .order_by(BiometricConsent.granted_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def has_biometric_consent(db: AsyncSession, user_id: str) -> bool:
    return await current_consent(db, user_id) is not None


async def grant(db: AsyncSession, user_id: str) -> BiometricConsent:
    """Record agreement to the *current* statement of purpose.

    A live grant against an older notice is left alone rather than edited, and
    a second row is written. Overwriting the version on the old row would
    destroy the only evidence of which words were actually agreed to, at the
    moment that question matters most.
    """
    existing = await current_consent(db, user_id)
    if existing is not None and existing.notice_version == NOTICE_VERSION:
        return existing

    row = BiometricConsent(
        user_id=user_id,
        purpose=BIOMETRIC_PURPOSE,
        notice_version=NOTICE_VERSION,
    )
    db.add(row)
    await db.flush()
    log.info("biometric_consent_granted", user_id=user_id, notice_version=NOTICE_VERSION)
    return row


async def withdraw(db: AsyncSession, user_id: str) -> int:
    """Stop the processing and delete what it produced, in one step.

    A withdrawal that left the vectors in place would be a preference, not a
    withdrawal. Deleting them also removes the account from pairing on its own
    — `_eligible_candidates` requires a face vector — so there is no separate
    "disabled" state to keep in sync with this one.
    """
    live = (
        (
            await db.execute(
                select(BiometricConsent)
                .where(BiometricConsent.user_id == user_id)
                .where(BiometricConsent.withdrawn_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    for row in live:
        row.withdrawn_at = utcnow()

    embedding = await db.get(ProfileEmbedding, user_id)
    cleared = 0
    if embedding is not None and embedding.face_vector is not None:
        embedding.face_vector = None
        embedding.face_source_id = None
        cleared = 1

    log.info("biometric_consent_withdrawn", user_id=user_id, vectors_cleared=cleared)
    return len(live)


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------

# Every table holding rows keyed to a user, and the column that keys them.
# Ordered so that rows pointing at other rows go first; the list is the
# answer to "what is deleted", so it is written out rather than derived.
OWNED_BY_USER = (
    (ChatMessage, ChatMessage.from_user_id),
    (Affinity, Affinity.viewer_id),
    (Affinity, Affinity.subject_id),
    (Pairing, Pairing.viewer_id),
    (Pairing, Pairing.subject_a_id),
    (Pairing, Pairing.subject_b_id),
    (Rating, Rating.subject_id),
    (ViewerPreference, ViewerPreference.viewer_id),
    (Block, Block.blocker_id),
    (Block, Block.blocked_id),
    (PromptResponse, PromptResponse.user_id),
    (MediaAsset, MediaAsset.user_id),
    (ProfileEmbedding, ProfileEmbedding.user_id),
    (UserVisibleAs, UserVisibleAs.user_id),
    (UserInterestedIn, UserInterestedIn.user_id),
    (EmailVerification, EmailVerification.user_id),
    (BiometricConsent, BiometricConsent.user_id),
    (Profile, Profile.user_id),
)


async def erase(db: AsyncSession, user: User) -> dict[str, Any]:
    """Remove an account and everything personal attached to it.

    Returns a count per table, because "we deleted your data" is a claim that
    should be checkable rather than asserted.
    """
    user_id, email = user.id, user.email
    removed: dict[str, int] = {}

    # Storage first, while the rows that name the objects still exist. Losing
    # the rows first would leave files nobody can find, which is the one
    # outcome erasure cannot tolerate.
    keys = (
        (await db.execute(select(MediaAsset.object_key).where(MediaAsset.user_id == user_id))).scalars().all()
    )
    stored, failed = 0, 0
    for key in keys:
        try:
            await storage.delete(key)
            stored += 1
        except Exception:
            # Recorded rather than swallowed: the row still goes, but an
            # object left behind is a promise not fully kept and somebody has
            # to be able to find it later.
            failed += 1
            log.error("erasure_object_delete_failed", user_id=user_id, key=key)
    removed["storage_objects"] = stored
    if failed:
        removed["storage_objects_failed"] = failed

    # Conversations are keyed by a pair, not a single column — and the whole
    # conversation goes, including the other person's replies. Deleting only
    # the messages *sent* by this account would leave half a conversation
    # hanging off a connection that no longer exists: unreadable, unreachable,
    # and still personal data about two people.
    conversations = (
        (
            await db.execute(
                select(Connection.id).where(
                    or_(Connection.user_a_id == user_id, Connection.user_b_id == user_id)
                )
            )
        )
        .scalars()
        .all()
    )
    if conversations:
        result = await db.execute(delete(ChatMessage).where(ChatMessage.connection_id.in_(conversations)))
        removed["chat_messages"] = result.rowcount or 0

    result = await db.execute(delete(Connection).where(Connection.id.in_(conversations)))
    removed["connections"] = result.rowcount or 0

    # Queued work carries the user id and the object keys; no foreign key
    # reaches it, so nothing else would.
    result = await db.execute(delete(Job).where(Job.subject_id == user_id))
    removed["jobs"] = result.rowcount or 0

    for model, column in OWNED_BY_USER:
        result = await db.execute(delete(model).where(column == user_id))
        name = model.__tablename__
        removed[name] = removed.get(name, 0) + (result.rowcount or 0)

    # Rate-limit counters are keyed by a bucket string, not a column.
    result = await db.execute(delete(RateLimitWindow).where(RateLimitWindow.bucket.like(f"%:{user_id}")))
    removed["rate_limit_windows"] = result.rowcount or 0

    # Reports *about* this person go; reports *by* them stay, with the
    # reporter forgotten. One dismissed report means little and four from
    # unrelated people is the pattern — losing the count because a reporter
    # deleted their account would hide exactly the behaviour worth seeing.
    result = await db.execute(delete(Report).where(Report.subject_id == user_id))
    removed["reports_about_them"] = result.rowcount or 0

    authored = (await db.execute(select(Report).where(Report.reporter_id == user_id))).scalars().all()
    for report in authored:
        report.reporter_id = None
    removed["reports_they_made_anonymised"] = len(authored)

    # A reviewer who leaves takes their name off their decisions but not the
    # decisions themselves. The outcome is what the record is for, and it
    # still has to be readable by whoever inherits the queue.
    reviewed = (await db.execute(select(Report).where(Report.reviewer_id == user_id))).scalars().all()
    for report in reviewed:
        report.reviewer_id = None
    removed["reviews_they_made_anonymised"] = len(reviewed)

    await db.delete(user)
    await db.flush()
    removed["users"] = 1

    log.info("account_erased", user_id=user_id, email_domain=email.rsplit("@", 1)[-1], **removed)
    return removed
