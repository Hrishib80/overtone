"""Job handlers.

One function per job kind. Each takes a session and a payload, does the work,
and lets exceptions escape — the runner owns retry and backoff, so a handler
never has to think about it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import ml, storage
from backend.database import (
    MediaAsset,
    MediaKind,
    MediaStatus,
    ProfileEmbedding,
    PromptKind,
    PromptResponse,
    utcnow,
)
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

    # The content vector is the whole profile's words, not just this clip:
    # three written answers plus what was spoken, embedded together.
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
    parts = [a.body.strip() for a in answers if a.kind == PromptKind.written and a.body]
    if transcript.text:
        parts.append(transcript.text)

    if parts:
        row.text_vector = ml.text_encoder().encode("\n".join(parts))

    # Keep the transcript on the response it came from too, so Your Read can
    # quote the actual sentence later.
    voice_answer = next((a for a in answers if a.kind == PromptKind.voice), None)
    if voice_answer is not None:
        voice_answer.transcript = transcript.text
        voice_answer.language = transcript.language

    asset.status = MediaStatus.processed
    asset.processed_at = utcnow()
    await db.commit()

    log.info(
        "voice_processed",
        asset_id=asset.id,
        language=transcript.language,
        transcript_chars=len(transcript.text),
        text_sources=len(parts),
    )


HANDLERS = {
    "process_photo": process_photo,
    "process_voice": process_voice,
}
