"""How a person is rendered to somebody else.

Two views, and the gap between them is the product. `photo_only_view` is what
a round-1 pair shows: a photograph and nothing else. That is not incidental
minimalism, it is the whole point of measuring a snap judgement, and this
module is where the rule is actually enforced — `photo_only_view` must never
grow a field beyond a photo url.

`full_profile_view` is the reveal, used by round 2 and by the unlock. It
carries everything a person chose to publish and nothing they did not: no
email, no raw birthdate, and no rating of any kind.

Kept apart from the routers that call it because both the pair loop and the
connection inbox need it, and threading it through one of them would have
made the other import a router to get at a serializer.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import (
    GenderIdentity,
    MediaAsset,
    MediaKind,
    MediaStatus,
    Profile,
    PromptKind,
    PromptLibrary,
    PromptResponse,
    Sexuality,
    User,
)


async def primary_photo_url(db: AsyncSession, user_id: str) -> str | None:
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


async def photo_only_view(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Round 1. Deliberately minimal — see the module docstring."""
    return {"id": user_id, "photo_url": await primary_photo_url(db, user_id)}


async def full_profile_view(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Everything. Never anything private — no email, no raw birthdate (age
    only), no rating of any kind."""
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
            # The answer's own id, not just the question's — reporting needs
            # to name this person's answer, and `prompt_id` names a question
            # eighty other people have also answered.
            "id": response.id,
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
        # Objects rather than bare urls, so a viewer can say *which* photo
        # they are reporting. The id is opaque and the report endpoint checks
        # it belongs to the person being reported, so exposing it costs
        # nothing and "one of these is a problem" stops being the only thing
        # a reviewer can be told.
        "photos": [{"id": p.id, "url": p.public_url} for p in photos],
        "prompts": prompts,
        "gender_identity": gender_label,
        "sexuality": sexuality_label,
        **profile_fields,
    }
