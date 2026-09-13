"""Job handlers.

One function per job kind. Each takes a session and a payload, does the work,
and lets exceptions escape — the runner owns retry and backoff, so a handler
never has to think about it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import mail, ml, storage
from backend.database import (
    MediaAsset,
    MediaKind,
    MediaStatus,
    ProfileEmbedding,
    PromptKind,
    PromptResponse,
    utcnow,
)
from backend.jobs import JobKind
from backend.jobs import enqueue as jobs_enqueue
from backend.logging_config import get_logger

log = get_logger(__name__)


async def _embedding_row(db: AsyncSession, user_id: str) -> ProfileEmbedding:
    row = await db.get(ProfileEmbedding, user_id)
    if row is None:
        row = ProfileEmbedding(user_id=user_id)
        db.add(row)
    return row


async def process_photo(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Gate the photo, then embed the face.

    The gate is not moderation — it only asks whether there is exactly one
    clear face to embed. Nudity and minor detection arrive with the review
    queue; until then no photo is visible to anyone but its owner.
    """
    asset = await db.get(MediaAsset, payload["asset_id"])
    if asset is None or asset.status == MediaStatus.rejected:
        return

    image = await storage.download(asset.object_key)
    result = ml.face_encoder().analyse(image)

    asset.face_count = result.face_count
    asset.det_score = result.det_score
    asset.processed_at = utcnow()

    if not result.ok:
        asset.status = MediaStatus.rejected
        asset.gate_reason = result.reason
        await db.commit()
        log.info("photo_rejected", asset_id=asset.id, reason=result.reason)
        return

    asset.status = MediaStatus.processed
    asset.gate_reason = None

    # The primary photo is the earliest *uploaded* one that passes, not
    # whichever job happened to run first — two uploads a moment apart would
    # otherwise pick arbitrarily, and the pair view needs one stable photo per
    # person or the rating measures photo choice instead of the person.
    processed = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == asset.user_id)
                .where(MediaAsset.kind == MediaKind.photo)
                .where(MediaAsset.status == MediaStatus.processed)
                .order_by(MediaAsset.created_at, MediaAsset.id)
            )
        )
        .scalars()
        .all()
    )
    primary = processed[0] if processed else None
    for photo in processed:
        photo.is_primary = photo is primary

    if primary is not None:
        row = await _embedding_row(db, asset.user_id)
        # Re-embed only when the primary actually changed; the vector belongs
        # to whichever photo is primary now.
        if row.face_source_id != primary.id:
            if primary.id == asset.id:
                row.face_vector = result.embedding
                row.face_source_id = asset.id
            else:
                row.face_source_id = primary.id

    await db.commit()
    log.info(
        "photo_processed",
        asset_id=asset.id,
        primary=asset.is_primary,
        score=result.det_score,
    )


async def process_voice(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Two independent axes from one clip.

    Timbre comes from the audio directly and needs no transcription, so it
    works the same in English, Hindi and Telugu. Content needs the transcript,
    which then goes to a multilingual embedder — no translation step, because
    translating first loses exactly the code-mixed nuance these users speak in.
    """
    asset = await db.get(MediaAsset, payload["asset_id"])
    if asset is None or asset.status == MediaStatus.rejected:
        return

    audio = await storage.download(asset.object_key)
    row = await _embedding_row(db, asset.user_id)

    row.voice_vector = ml.voice_encoder().encode(audio)

    transcript = await ml.transcriber().transcribe(audio, filename=asset.object_key.rsplit("/", 1)[-1])
    row.transcript = transcript.text
    row.transcript_language = transcript.language

    # Attach the transcript to the answer it came from, so Your Read can quote
    # the actual sentence later.
    answers = (
        (
            await db.execute(
                select(PromptResponse)
                .where(PromptResponse.user_id == asset.user_id)
                .order_by(PromptResponse.slot)
            )
        )
        .scalars()
        .all()
    )
    voice_answer = next((a for a in answers if a.kind == PromptKind.voice), None)
    if voice_answer is not None:
        voice_answer.transcript = transcript.text
        voice_answer.language = transcript.language

    asset.status = MediaStatus.processed
    asset.processed_at = utcnow()
    await db.commit()

    # The content vector is rebuilt separately: the clip and the written
    # answers arrive in either order, and prompts can be edited afterwards.
    await jobs_enqueue(db, JobKind.refresh_text, {"user_id": asset.user_id}, subject_id=asset.user_id)
    await db.commit()

    log.info(
        "voice_processed",
        asset_id=asset.id,
        language=transcript.language,
        confidence=transcript.language_confidence,
        transcript_chars=len(transcript.text),
    )


async def refresh_text(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Rebuild the content vector from the profile's current words.

    Enqueued whenever prompts change, which decouples it from the voice upload
    entirely: the clip and the written answers can land in either order, and a
    later edit is picked up without touching the audio again.
    """
    user_id = payload["user_id"]
    row = await _embedding_row(db, user_id)

    answers = (
        (
            await db.execute(
                select(PromptResponse).where(PromptResponse.user_id == user_id).order_by(PromptResponse.slot)
            )
        )
        .scalars()
        .all()
    )

    parts = [a.body.strip() for a in answers if a.kind == PromptKind.written and a.body]
    if row.transcript:
        parts.append(row.transcript)

    row.text_vector = ml.text_encoder().encode("\n".join(parts)) if parts else None
    await db.commit()
    log.info("text_refreshed", user_id=user_id, sources=len(parts))


async def send_email(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Deliver one message. Exceptions escape so the queue retries with backoff.

    The message is built at enqueue time and carried whole in the payload, not
    rebuilt here from a user id: by the time this runs the account may have
    been deleted, and a verification link that cannot be regenerated is better
    than one that quietly stops being sent.
    """
    await mail.send(mail.Message(to=payload["to"], subject=payload["subject"], text=payload["text"]))


HANDLERS = {
    "process_photo": process_photo,
    "process_voice": process_voice,
    "refresh_text": refresh_text,
    "send_email": send_email,
}
