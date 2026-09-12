"""The pair view's API: serve the next pair, record a choice.

Round 1 shows a photo and nothing else — no name, no age, no prompts. That is
not an incidental minimalism; it is the whole point of measuring a snap
judgement, and the serializer below is where that rule is actually enforced,
so it is worth being explicit that `_photo_only_view` must never grow a field
beyond a photo url.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import current_user
from backend.database import (
    GenderIdentity,
    MediaAsset,
    MediaKind,
    MediaStatus,
    PairRound,
    Profile,
    PromptKind,
    PromptLibrary,
    PromptResponse,
    Sexuality,
    User,
    get_db,
)
from backend.logging_config import get_logger
from backend.pairing import next_pair, record_decision

log = get_logger(__name__)
router = APIRouter(prefix="/api/pairs", tags=["pairs"])


async def _primary_photo_url(db: AsyncSession, user_id: str) -> str | None:
    photo = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user_id)
                .where(MediaAsset.kind == MediaKind.photo)
                .where(MediaAsset.status == MediaStatus.processed)
                .where(MediaAsset.is_primary.is_(True))
            )
        )
        .scalars()
        .first()
    )
    return photo.public_url if photo else None


async def _photo_only_view(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Round 1. Deliberately minimal — see the module docstring."""
    return {"id": user_id, "photo_url": await _primary_photo_url(db, user_id)}


async def _full_profile_view(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Round 2: everything. Never anything private — no email, no raw
    birthdate (age only), no rating of any kind."""
    user = await db.get(User, user_id)
    profile = await db.get(Profile, user_id)

    photos = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user_id)
                .where(MediaAsset.kind == MediaKind.photo)
                .where(MediaAsset.status == MediaStatus.processed)
                .order_by(MediaAsset.is_primary.desc(), MediaAsset.display_order, MediaAsset.created_at)
            )
        )
        .scalars()
        .all()
    )

    answers = (
        await db.execute(
            select(PromptResponse, PromptLibrary)
            .join(PromptLibrary, PromptLibrary.id == PromptResponse.prompt_id)
            .where(PromptResponse.user_id == user_id)
            .order_by(PromptResponse.slot)
        )
    ).all()

    prompts = []
    for response, prompt in answers:
        entry: dict[str, Any] = {
            "prompt_id": prompt.id,
            "text": prompt.text,
            "kind": response.kind,
        }
        if response.kind == PromptKind.written:
            entry["body"] = response.body
        else:
            audio = await db.get(MediaAsset, response.audio_key) if response.audio_key else None
            # An audio_key can exist while its asset is still processing (the
            # worker hasn't finished the gate yet) — omit the url rather than
            # surface an asset that isn't playable, or one that failed.
            entry["audio_url"] = audio.public_url if audio and audio.status == MediaStatus.processed else None
        prompts.append(entry)

    profile_fields = (
        {
            column.name: getattr(profile, column.name)
            for column in Profile.__table__.columns
            if column.name not in ("user_id", "gender_identity_id", "sexuality_id")
        }
        if profile
        else {}
    )

    # gender_identity_id / sexuality_id are foreign keys into seeded reference
    # tables — resolved to their labels here rather than dropped, since a full
    # reveal that silently omits how someone identifies is not actually full.
    gender_label = sexuality_label = None
    if profile and profile.gender_identity_id:
        gender = await db.get(GenderIdentity, profile.gender_identity_id)
        gender_label = gender.label if gender else None
    if profile and profile.sexuality_id:
        sexuality = await db.get(Sexuality, profile.sexuality_id)
        sexuality_label = sexuality.label if sexuality else None

    return {
        "id": user_id,
        "display_name": user.display_name if user else None,
        "age": user.age if user else None,
        "photos": [p.public_url for p in photos],
        "prompts": prompts,
        "gender_identity": gender_label,
        "sexuality": sexuality_label,
        **profile_fields,
    }


async def _serialise(db: AsyncSession, pairing) -> dict[str, Any]:
    view = _photo_only_view if pairing.round == PairRound.round_1 else _full_profile_view
    return {
        "id": pairing.id,
        "round": pairing.round,
        "subjects": [
            await view(db, pairing.subject_a_id),
            await view(db, pairing.subject_b_id),
        ],
    }


class DecideRequest(BaseModel):
    model_config = {"extra": "forbid"}

    chosen_id: str


@router.get("/next")
async def get_next_pair(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    pairing = await next_pair(db, user)
    await db.commit()

    if pairing is None:
        return {"pair": None}
    return {"pair": await _serialise(db, pairing)}


@router.post("/{pairing_id}/decide")
async def decide_pair(
    pairing_id: str,
    req: DecideRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    pairing = await record_decision(db, user, pairing_id, req.chosen_id)
    await db.commit()

    return {
        "status": "decided",
        "round": pairing.round,
        "round_two_scheduled": pairing.round == PairRound.round_1,
    }
