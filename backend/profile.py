"""Profile: the Hinge field set, identity, and prompt responses.

Identity is two different things wearing similar names. `gender_identity` is
expressive — an open list plus free text, shown as written, never queried.
`visible_as` and `interested_in` are operational: small multi-selects over
three segments, and they are what matching actually runs against. Keeping them
apart is what lets the identity list stay open without making the filter
unwritable.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import jobs, options
from backend.auth import require_member
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
    UserInterestedIn,
    UserStatus,
    UserVisibleAs,
    get_db,
    utcnow,
)
from backend.errors import AppError, NotFound
from backend.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/profile", tags=["profile"])


def _one_of(allowed: tuple[str, ...], label: str):
    def check(value: str | None) -> str | None:
        if value is not None and value not in allowed:
            raise ValueError(f"{label} must be one of: {', '.join(allowed)}")
        return value

    return check


def _all_of(allowed: tuple[str, ...], label: str):
    def check(values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        unknown = sorted(set(values) - set(allowed))
        if unknown:
            raise ValueError(f"{label} contains unknown value(s): {', '.join(unknown)}")
        return list(dict.fromkeys(values))  # de-duplicate, keep order

    return check


class ProfilePatch(BaseModel):
    """Every field optional — this is a partial update."""

    model_config = {"extra": "forbid"}

    gender_identity_id: str | None = None
    custom_gender: str | None = Field(default=None, max_length=80)
    sexuality_id: str | None = None
    custom_sexuality: str | None = Field(default=None, max_length=80)
    pronouns: str | None = Field(default=None, max_length=40)

    visible_as: list[str] | None = None
    interested_in: list[str] | None = None

    height_cm: Annotated[int, Field(ge=options.HEIGHT_CM_MIN, le=options.HEIGHT_CM_MAX)] | None = None
    location: str | None = Field(default=None, max_length=120)
    hometown: str | None = Field(default=None, max_length=120)
    ethnicities: list[str] | None = None
    children: str | None = None
    family_plans: str | None = None
    pets: str | None = None
    zodiac: str | None = None

    job_title: str | None = Field(default=None, max_length=120)
    workplace: str | None = Field(default=None, max_length=120)
    school: str | None = Field(default=None, max_length=120)
    education_level: str | None = None
    religion: str | None = None
    politics: str | None = None
    languages: list[str] | None = None
    dating_intentions: str | None = None
    relationship_type: str | None = None

    drinking: str | None = None
    smoking: str | None = None
    marijuana: str | None = None
    drugs: str | None = None

    _v_children = field_validator("children")(_one_of(options.CHILDREN, "children"))
    _v_family = field_validator("family_plans")(_one_of(options.FAMILY_PLANS, "family_plans"))
    _v_pets = field_validator("pets")(_one_of(options.PETS, "pets"))
    _v_zodiac = field_validator("zodiac")(_one_of(options.ZODIAC, "zodiac"))
    _v_education = field_validator("education_level")(_one_of(options.EDUCATION_LEVELS, "education_level"))
    _v_religion = field_validator("religion")(_one_of(options.RELIGIONS, "religion"))
    _v_politics = field_validator("politics")(_one_of(options.POLITICS, "politics"))
    _v_intent = field_validator("dating_intentions")(_one_of(options.DATING_INTENTIONS, "dating_intentions"))
    _v_reltype = field_validator("relationship_type")(
        _one_of(options.RELATIONSHIP_TYPES, "relationship_type")
    )
    _v_vices = field_validator("drinking", "smoking", "marijuana", "drugs")(
        _one_of(options.FREQUENCY, "vice")
    )
    _v_ethnicities = field_validator("ethnicities")(_all_of(options.ETHNICITIES, "ethnicities"))
    _v_languages = field_validator("languages")(_all_of(options.LANGUAGES, "languages"))
    _v_visible = field_validator("visible_as")(_all_of(options.SEGMENTS, "visible_as"))
    _v_interested = field_validator("interested_in")(_all_of(options.SEGMENTS, "interested_in"))

    @model_validator(mode="after")
    def _non_empty_selections(self) -> ProfilePatch:
        if self.visible_as is not None and not self.visible_as:
            raise ValueError("visible_as cannot be empty — pick at least one.")
        if self.interested_in is not None and not self.interested_in:
            raise ValueError("interested_in cannot be empty — pick at least one.")
        return self


class PromptAnswer(BaseModel):
    model_config = {"extra": "forbid"}

    prompt_id: str
    slot: int = Field(ge=1, le=options.WRITTEN_PROMPT_SLOTS + options.VOICE_PROMPT_SLOTS)
    kind: PromptKind = PromptKind.written
    body: str | None = Field(default=None, max_length=500)
    audio_key: str | None = None
    audio_duration_ms: int | None = Field(default=None, ge=500)

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> PromptAnswer:
        if self.kind == PromptKind.written:
            if not (self.body or "").strip():
                raise ValueError("A written prompt needs an answer.")
        elif not self.audio_key:
            raise ValueError("A voice prompt needs a recording.")

        limit_ms = options.VOICE_PROMPT_MAX_SECONDS * 1000
        if self.audio_duration_ms and self.audio_duration_ms > limit_ms:
            raise ValueError(f"Voice answers are capped at {options.VOICE_PROMPT_MAX_SECONDS} seconds.")
        return self


class PromptAnswers(BaseModel):
    model_config = {"extra": "forbid"}

    answers: list[PromptAnswer]

    @model_validator(mode="after")
    def _slots_are_unique(self) -> PromptAnswers:
        slots = [a.slot for a in self.answers]
        if len(slots) != len(set(slots)):
            raise ValueError("Each prompt slot can only be answered once.")
        return self


async def _get_profile(db: AsyncSession, user_id: str) -> Profile:
    profile = await db.get(Profile, user_id)
    if profile is None:
        raise NotFound("Profile not found.")
    return profile


async def _segments(db: AsyncSession, user_id: str) -> tuple[list[str], list[str]]:
    visible = (
        (await db.execute(select(UserVisibleAs).where(UserVisibleAs.user_id == user_id))).scalars().all()
    )
    interested = (
        (await db.execute(select(UserInterestedIn.segment).where(UserInterestedIn.user_id == user_id)))
        .scalars()
        .all()
    )
    # Primary first — it is the one cap accounting uses.
    visible_sorted = sorted(visible, key=lambda row: (not row.is_primary, row.segment))
    return [row.segment for row in visible_sorted], sorted(interested)


def _serialise(profile: Profile) -> dict[str, Any]:
    return {
        column.name: getattr(profile, column.name)
        for column in Profile.__table__.columns
        if column.name != "user_id"
    }


@router.get("/options")
async def get_options(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Everything the onboarding UI needs to render its pickers."""
    genders = (
        (
            await db.execute(
                select(GenderIdentity)
                .where(GenderIdentity.is_active.is_(True))
                .order_by(GenderIdentity.display_order)
            )
        )
        .scalars()
        .all()
    )
    sexualities = (
        (
            await db.execute(
                select(Sexuality).where(Sexuality.is_active.is_(True)).order_by(Sexuality.display_order)
            )
        )
        .scalars()
        .all()
    )
    prompts = (
        (
            await db.execute(
                select(PromptLibrary)
                .where(PromptLibrary.is_active.is_(True))
                .order_by(PromptLibrary.display_order)
            )
        )
        .scalars()
        .all()
    )

    return {
        "gender_identities": [
            {"id": g.id, "label": g.label, "default_visible_as": g.default_visible_as} for g in genders
        ],
        "sexualities": [{"id": s.id, "label": s.label} for s in sexualities],
        "prompts": [
            {"id": p.id, "text": p.text, "category": p.category, "emphasis": p.emphasis} for p in prompts
        ],
        "slots": {
            "written": options.WRITTEN_PROMPT_SLOTS,
            "voice": options.VOICE_PROMPT_SLOTS,
            "voice_max_seconds": options.VOICE_PROMPT_MAX_SECONDS,
        },
        "fields": dict(options.OPTION_SETS),
    }


@router.get("")
async def get_profile(
    user: User = Depends(require_member), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    profile = await _get_profile(db, user.id)
    visible_as, interested_in = await _segments(db, user.id)

    answers = (
        (
            await db.execute(
                select(PromptResponse).where(PromptResponse.user_id == user.id).order_by(PromptResponse.slot)
            )
        )
        .scalars()
        .all()
    )

    return {
        "status": user.status,
        "display_name": user.display_name,
        "age": user.age,
        "visible_as": visible_as,
        "interested_in": interested_in,
        **_serialise(profile),
        "prompts": [
            {
                "slot": a.slot,
                "prompt_id": a.prompt_id,
                "kind": a.kind,
                "body": a.body,
                "audio_key": a.audio_key,
            }
            for a in answers
        ],
        "completeness": await _completeness(db, user, profile),
    }


@router.patch("")
async def patch_profile(
    patch: ProfilePatch,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    profile = await _get_profile(db, user.id)
    changes = patch.model_dump(exclude_unset=True)

    if (
        changes.get("gender_identity_id") is not None
        and (await db.get(GenderIdentity, changes["gender_identity_id"])) is None
    ):
        raise AppError("That gender option is not recognised.")
    if changes.get("sexuality_id") is not None and (await db.get(Sexuality, changes["sexuality_id"])) is None:
        raise AppError("That sexuality option is not recognised.")

    visible_as = changes.pop("visible_as", None)
    interested_in = changes.pop("interested_in", None)

    for field, value in changes.items():
        setattr(profile, field, value)

    if visible_as is not None:
        await db.execute(delete(UserVisibleAs).where(UserVisibleAs.user_id == user.id))
        for index, segment in enumerate(visible_as):
            db.add(UserVisibleAs(user_id=user.id, segment=segment, is_primary=index == 0))
        # Cap accounting follows the primary choice.

    if interested_in is not None:
        await db.execute(delete(UserInterestedIn).where(UserInterestedIn.user_id == user.id))
        for segment in interested_in:
            db.add(UserInterestedIn(user_id=user.id, segment=segment))

    profile.updated_at = utcnow()
    await db.commit()

    log.info("profile_updated", user_id=user.id, fields=sorted(changes))
    return await get_profile(user=user, db=db)


@router.put("/prompts")
async def set_prompts(
    payload: PromptAnswers,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    known = {
        row.id
        for row in (await db.execute(select(PromptLibrary).where(PromptLibrary.is_active.is_(True))))
        .scalars()
        .all()
    }
    unknown = sorted({a.prompt_id for a in payload.answers} - known)
    if unknown:
        raise AppError(f"Unknown prompt(s): {', '.join(unknown)}")

    written = sum(1 for a in payload.answers if a.kind == PromptKind.written)
    voice = sum(1 for a in payload.answers if a.kind == PromptKind.voice)
    if written > options.WRITTEN_PROMPT_SLOTS:
        raise AppError(f"You can answer at most {options.WRITTEN_PROMPT_SLOTS} written prompts.")
    if voice > options.VOICE_PROMPT_SLOTS:
        raise AppError(f"You can record at most {options.VOICE_PROMPT_SLOTS} voice prompt.")

    # audio_key names a MediaAsset id, not a raw storage key — despite the
    # field's name, which follows what the client calls it. Nothing checked
    # that it actually belonged to this caller, so a submitted id could
    # reference someone else's recording, or nothing at all; later code (the
    # round-2 profile reveal) trusts this field to resolve a playable clip, so
    # it has to be verified here rather than there.
    for answer in payload.answers:
        if answer.kind != PromptKind.voice:
            continue
        asset = await db.get(MediaAsset, answer.audio_key)
        if asset is None or asset.user_id != user.id or asset.kind != MediaKind.voice:
            raise AppError("That recording could not be found. Try recording again.")

    existing = {
        row.slot: row
        for row in (await db.execute(select(PromptResponse).where(PromptResponse.user_id == user.id)))
        .scalars()
        .all()
    }

    for answer in payload.answers:
        # Updated in place rather than deleted and recreated: the pairwise loop
        # attaches per-prompt statistics to these ids, and recreating orphans them.
        row = existing.get(answer.slot)
        if row is None:
            row = PromptResponse(user_id=user.id, slot=answer.slot)
            db.add(row)
        row.prompt_id = answer.prompt_id
        row.kind = answer.kind
        row.body = answer.body
        row.audio_key = answer.audio_key
        row.audio_duration_ms = answer.audio_duration_ms
        if answer.kind == PromptKind.written:
            row.audio_key = None
            row.transcript = None

    submitted = {a.slot for a in payload.answers}
    for slot, row in existing.items():
        if slot not in submitted:
            await db.delete(row)

    # The content vector follows the words, so it is rebuilt on every edit
    # rather than only when a voice clip happens to be uploaded.
    await jobs.enqueue(db, jobs.JobKind.refresh_text, {"user_id": user.id}, subject_id=user.id)

    await db.commit()
    log.info("prompts_updated", user_id=user.id, count=len(payload.answers))
    return await get_profile(user=user, db=db)


async def _completeness(db: AsyncSession, user: User, profile: Profile) -> dict[str, Any]:
    visible_as, interested_in = await _segments(db, user.id)
    answers = (
        (await db.execute(select(PromptResponse).where(PromptResponse.user_id == user.id))).scalars().all()
    )
    written = sum(1 for a in answers if a.kind == PromptKind.written)
    voice = sum(1 for a in answers if a.kind == PromptKind.voice)

    photos = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user.id)
                .where(MediaAsset.kind == MediaKind.photo)
                .where(MediaAsset.status != MediaStatus.rejected)
            )
        )
        .scalars()
        .all()
    )

    missing = []
    if not photos:
        missing.append("photo")
    if not visible_as:
        missing.append("visible_as")
    if not interested_in:
        missing.append("interested_in")
    if not profile.dating_intentions:
        missing.append("dating_intentions")
    if written < options.WRITTEN_PROMPT_SLOTS:
        missing.append(f"written_prompts ({written}/{options.WRITTEN_PROMPT_SLOTS})")
    if voice < options.VOICE_PROMPT_SLOTS:
        missing.append("voice_prompt")

    return {"complete": not missing, "missing": missing}


@router.post("/submit")
async def submit_profile(
    user: User = Depends(require_member), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Finish onboarding. A complete profile on a verified address is a member —
    there is no cap to clear and no queue to join."""
    if user.status == UserStatus.active:
        return {"status": user.status}
    if user.email_verified_at is None:
        raise AppError("Verify your email address first.")

    profile = await _get_profile(db, user.id)
    completeness = await _completeness(db, user, profile)
    if not completeness["complete"]:
        raise AppError(
            "Your profile isn't finished yet.",
            missing=completeness["missing"],
        )

    user.status = UserStatus.active
    await db.commit()
    log.info("user_activated", user_id=user.id)
    return {"status": user.status}
