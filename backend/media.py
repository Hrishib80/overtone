"""Media upload endpoints.

Three calls, in order:

    POST /api/media/upload-url   -> a signed URL and an asset id
    (the browser PUTs the bytes straight to storage)
    POST /api/media/{id}/confirm -> we verify the bytes landed, then queue work

The API never sees the file. Size limits are enforced when the URL is issued
and again against what storage reports, so a client cannot lie about either.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import jobs, storage
from backend.auth import require_member
from backend.config import settings
from backend.database import (
    MediaAsset,
    MediaKind,
    MediaStatus,
    User,
    get_db,
)
from backend.errors import AppError, NotAuthorized, NotFound
from backend.logging_config import get_logger
from backend.privacy import has_biometric_consent
from backend.ratelimit import UPLOAD_TICKET, consume

log = get_logger(__name__)
router = APIRouter(prefix="/api/media", tags=["media"])

# Three, and the first one is special. A profile is one person's face, not a
# gallery: more slots mean more time spent choosing and a weaker round-1
# signal, because the pair view only ever shows the primary.
MAX_PHOTOS = 3
MIN_PHOTOS = 1


class UploadRequest(BaseModel):
    model_config = {"extra": "forbid"}

    kind: MediaKind
    content_type: str = Field(max_length=100)
    byte_size: int = Field(gt=0)
    # Declared here as well as at confirm, and only so the cap lets the ticket
    # through. Without it somebody sitting on their third photo cannot get a
    # ticket to swap one out, and "replace" becomes impossible at exactly the
    # point it is the only thing left to do.
    replaces: str | None = None


class ConfirmRequest(BaseModel):
    model_config = {"extra": "forbid"}

    duration_ms: int | None = Field(default=None, ge=0)
    # Swap this photo into another one's slot and drop the old one, in one
    # step. Two steps would either dip below the one-photo minimum (delete
    # first) or leave a stranger's fourth photo wedged over the cap (upload
    # first and then fail to delete).
    replaces: str | None = None


# A ticket that was issued and never uploaded to is not a photo. Counting
# one would let an abandoned ticket eat a slot, and would let the
# last-photo guard pass while the only real photo was being deleted.
REAL_PHOTO_STATES = (MediaStatus.uploaded, MediaStatus.processed)


async def _forget_object(key: str) -> None:
    """Drop the stored bytes, and carry on if they are already unreachable.

    Removing or replacing a photo must not fail because the object behind it
    has gone. The row is what decides whether somebody has a photo; the bytes
    are downstream of it, and an object that cannot be deleted is a cleanup
    problem rather than a reason to refuse the person their own edit.

    Left behind by a failure here: an orphaned object in the bucket. That is
    the cheaper of the two bad outcomes — the other is somebody permanently
    unable to change a photo because of a file they cannot see.

    `storage.delete` still raises for callers that genuinely need to know it
    worked, which account deletion will.
    """
    try:
        await storage.delete(key)
    except Exception:
        log.warning("object_delete_failed_continuing", key=key)


async def _photo_count(db: AsyncSession, user_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(MediaAsset.id))
            .where(MediaAsset.user_id == user_id)
            .where(MediaAsset.kind == MediaKind.photo)
            .where(MediaAsset.status.in_(REAL_PHOTO_STATES))
        )
        or 0
    )


def _limit_for(kind: MediaKind) -> tuple[dict[str, str], int]:
    if kind == MediaKind.photo:
        return storage.PHOTO_TYPES, settings.max_photo_bytes
    return storage.AUDIO_TYPES, settings.max_audio_bytes


@router.post("/upload-url", status_code=201)
async def create_upload_url(
    req: UploadRequest,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Issuing a signed URL is the cheap half of an upload and the half an
    # abuser would spam; the bytes cost them nothing if they never PUT them.
    await consume(db, UPLOAD_TICKET, user.id)
    allowed, max_bytes = _limit_for(req.kind)

    if req.content_type not in allowed:
        raise AppError(
            f"That file type isn't supported. Use {', '.join(sorted(allowed))}.",
            content_type=req.content_type,
        )
    if req.byte_size > max_bytes:
        raise AppError(f"That file is too large. The limit is {max_bytes // (1024 * 1024)} MB.")

    if req.kind == MediaKind.photo:
        # Asked before the URL is issued rather than before the embedding is
        # computed. Consent covers the face vector, but the honest place to
        # stop is before somebody's face reaches our storage at all — not
        # afterwards, with an apology and a deletion.
        if not await has_biometric_consent(db, user.id):
            raise AppError(
                "We need your permission to read faces from your photos first.",
                needs="biometric_consent",
            )

        # A replacement is not an addition, so it does not have to fit under
        # the cap — the old photo goes at confirm, and the count comes out the
        # same. Verified here rather than trusted, or `replaces` would be a
        # way to ask for a fourth slot.
        held = await _photo_count(db, user.id)
        if req.replaces:
            target = await db.get(MediaAsset, req.replaces)
            if target is None or target.user_id != user.id or target.kind != MediaKind.photo:
                raise NotFound("There's no photo to replace.")
            held -= 1

        if held >= MAX_PHOTOS:
            raise AppError(
                f"You can have {MAX_PHOTOS} photos. Replace one instead.",
                limit=MAX_PHOTOS,
            )

    signed = await storage.create_signed_upload(user.id, str(req.kind), req.content_type)

    asset = MediaAsset(
        user_id=user.id,
        kind=req.kind,
        object_key=signed.object_key,
        public_url=signed.public_url,
        content_type=req.content_type,
        byte_size=req.byte_size,
        status=MediaStatus.pending_upload,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    return {
        "asset_id": asset.id,
        "upload_url": signed.upload_url,
        "token": signed.token,
        "object_key": signed.object_key,
        "expires_in": settings.upload_url_ttl_seconds,
    }


@router.put("/local/{key:path}", status_code=204)
async def receive_local_upload(
    key: str,
    request: Request,
    token: str = Query(default=""),
) -> Response:
    """Receive bytes for the local storage provider.

    Deliberately unauthenticated, and safe because the signed ticket in the
    query string is the authorisation: only this server could have minted it,
    it names one exact key, and it expires. That mirrors how a Supabase signed
    URL works, so the browser code is the same either way.

    This route is the one place bytes pass through the API, which is why
    production refuses to run on local storage at all.
    """
    if not storage.local_mode():
        raise NotFound("Not found.")
    if not storage.verify_local_token(key, token):
        raise NotAuthorized("That upload link is no longer valid.")

    body = await request.body()
    if len(body) > max(settings.max_photo_bytes, settings.max_audio_bytes):
        raise AppError("That file is too large.")

    storage.write_local(key, body)
    log.info("local_upload_received", key=key, bytes=len(body))
    return Response(status_code=204)


@router.post("/{asset_id}/confirm")
async def confirm_upload(
    asset_id: str,
    req: ConfirmRequest,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    asset = await db.get(MediaAsset, asset_id)
    if asset is None or asset.user_id != user.id:
        raise NotFound("Upload not found.")

    if asset.status != MediaStatus.pending_upload:
        # Confirming twice is harmless; say what the state already is.
        return {"asset_id": asset.id, "status": asset.status}

    exists, size = await storage.head(asset.object_key)
    if not exists:
        raise AppError("We couldn't find that upload. Try again.")

    _, max_bytes = _limit_for(MediaKind(asset.kind))
    if size is not None and size > max_bytes:
        # The signed URL was issued against a declared size; this is the check
        # against what actually arrived.
        await storage.delete(asset.object_key)
        asset.status = MediaStatus.rejected
        asset.gate_reason = "too_large"
        await db.commit()
        raise AppError("That file is larger than the limit. It has been discarded.")

    asset.status = MediaStatus.uploaded
    if size is not None:
        asset.byte_size = size

    if asset.kind == MediaKind.photo:
        replaced = None
        if req.replaces:
            replaced = await db.get(MediaAsset, req.replaces)
            if replaced is None or replaced.user_id != user.id or replaced.kind != MediaKind.photo:
                raise NotFound("There's no photo to replace.")

        if replaced is not None:
            # Inherit the slot, then remove the old one. Position is what makes
            # "replace your first photo" mean the new one *is* now first,
            # rather than landing third and leaving the old order intact.
            asset.display_order = replaced.display_order
            asset.is_primary = replaced.is_primary
            await _forget_object(replaced.object_key)
            await db.delete(replaced)
        else:
            highest = await db.scalar(
                select(func.max(MediaAsset.display_order))
                .where(MediaAsset.user_id == user.id)
                .where(MediaAsset.kind == MediaKind.photo)
                .where(MediaAsset.id != asset.id)
            )
            asset.display_order = 0 if highest is None else highest + 1

        # Claim primary if nothing holds it. The worker refines this once it
        # has actually looked at the images, but a profile whose only photo is
        # not primary shows nothing in the pair view, and that should not
        # depend on a background process having run.
        held = await db.scalar(
            select(func.count(MediaAsset.id))
            .where(MediaAsset.user_id == asset.user_id)
            .where(MediaAsset.kind == MediaKind.photo)
            .where(MediaAsset.is_primary.is_(True))
            .where(MediaAsset.id != asset.id)
        )
        if not held:
            asset.is_primary = True

    kind = jobs.JobKind.process_photo if asset.kind == MediaKind.photo else jobs.JobKind.process_voice
    # Job and asset commit together — the row can never exist without its work
    # queued, which is the property a separate queue service would not give.
    await jobs.enqueue(
        db,
        kind,
        {"asset_id": asset.id, "user_id": user.id, "duration_ms": req.duration_ms},
        subject_id=user.id,
    )
    await db.commit()

    log.info("media_confirmed", asset_id=asset.id, kind=asset.kind, bytes=asset.byte_size)
    return {"asset_id": asset.id, "status": asset.status, "processing": True}


@router.get("")
async def list_media(
    user: User = Depends(require_member), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    # Tickets that were issued and never used are intents, not media. Listing
    # them meant the grid drew a blank tile for every time somebody opened the
    # file picker and changed their mind. Rejected ones stay, because the UI
    # has to be able to say why they were rejected.
    assets = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user.id)
                .where(MediaAsset.status != MediaStatus.pending_upload)
                .order_by(MediaAsset.kind, MediaAsset.display_order, MediaAsset.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "media": [
            {
                "id": a.id,
                "kind": a.kind,
                "status": a.status,
                # Shown as soon as the bytes are there, not once the worker
                # has finished with them. Withholding it until `processed`
                # meant somebody adding their first photo watched an empty
                # square and could not tell whether the upload had worked —
                # and this is the owner looking at their own photo, so the
                # quality gate has nothing to do with it.
                "url": a.public_url if a.status in REAL_PHOTO_STATES else None,
                "is_primary": a.is_primary,
                # Surfaced so the UI can say *why* a photo was refused rather
                # than silently dropping it.
                "gate_reason": a.gate_reason,
            }
            for a in assets
        ]
    }


@router.delete("/{asset_id}", status_code=204)
async def delete_media(
    asset_id: str,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
) -> None:
    asset = await db.get(MediaAsset, asset_id)
    if asset is None or asset.user_id != user.id:
        raise NotFound("Upload not found.")

    # The first photo is the one the pair view shows, and a profile without
    # it is not a profile. It can be *replaced* — that is what the upload
    # endpoint is for — but removing it outright would leave an account with
    # nothing to be compared on, which the pair loop has no answer for.
    if asset.kind == MediaKind.photo:
        remaining = await _photo_count(db, user.id)
        if remaining <= MIN_PHOTOS:
            raise AppError(
                "Your first photo can be replaced, but not removed.",
                minimum=MIN_PHOTOS,
            )

    was_primary = asset.kind == MediaKind.photo and asset.is_primary

    await _forget_object(asset.object_key)
    await db.delete(asset)
    await db.flush()

    if was_primary:
        # Somebody has to hold it, or the account keeps photos and shows none.
        # Oldest first, which is the rule the worker uses too, so the two
        # cannot disagree about who is on top.
        successor = (
            (
                await db.execute(
                    select(MediaAsset)
                    .where(MediaAsset.user_id == user.id)
                    .where(MediaAsset.kind == MediaKind.photo)
                    .where(MediaAsset.status.in_(REAL_PHOTO_STATES))
                    .order_by(MediaAsset.display_order, MediaAsset.created_at)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if successor is not None:
            successor.is_primary = True

    await db.commit()
    log.info("media_deleted", asset_id=asset_id, user_id=user.id, was_primary=was_primary)


@router.get("/status")
async def processing_status(
    user: User = Depends(require_member), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """What the worker still owes this user *on their media*. Lets onboarding
    show progress instead of a spinner with no end.

    Filtered by kind, not just by subject: this account also has jobs queued
    against it that have nothing to do with a photo — a verification email, to
    begin with — and counting those would leave onboarding reporting that the
    pictures are still processing until an unrelated message had been sent.
    """
    media_kinds = [
        jobs.JobKind.process_photo,
        jobs.JobKind.process_voice,
        jobs.JobKind.refresh_text,
    ]
    pending = (
        (
            await db.execute(
                select(jobs.Job)
                .where(jobs.Job.subject_id == user.id)
                .where(jobs.Job.kind.in_(media_kinds))
                .where(jobs.Job.status.in_([jobs.JobStatus.queued, jobs.JobStatus.running]))
            )
        )
        .scalars()
        .all()
    )

    failed = (
        (
            await db.execute(
                select(jobs.Job)
                .where(jobs.Job.subject_id == user.id)
                .where(jobs.Job.status == jobs.JobStatus.failed)
            )
        )
        .scalars()
        .all()
    )

    return {
        "pending": len(pending),
        "failed": len(failed),
        "done": not pending and not failed,
    }
