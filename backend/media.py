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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import jobs, storage
from backend.auth import current_user
from backend.config import settings
from backend.database import (
    MediaAsset,
    MediaKind,
    MediaStatus,
    User,
    get_db,
)
from backend.errors import AppError, NotFound
from backend.logging_config import get_logger
from backend.ratelimit import UPLOAD_TICKET, consume

log = get_logger(__name__)
router = APIRouter(prefix="/api/media", tags=["media"])

MAX_PHOTOS = 6


class UploadRequest(BaseModel):
    model_config = {"extra": "forbid"}

    kind: MediaKind
    content_type: str = Field(max_length=100)
    byte_size: int = Field(gt=0)


class ConfirmRequest(BaseModel):
    model_config = {"extra": "forbid"}

    duration_ms: int | None = Field(default=None, ge=0)


def _limit_for(kind: MediaKind) -> tuple[dict[str, str], int]:
    if kind == MediaKind.photo:
        return storage.PHOTO_TYPES, settings.max_photo_bytes
    return storage.AUDIO_TYPES, settings.max_audio_bytes


@router.post("/upload-url", status_code=201)
async def create_upload_url(
    req: UploadRequest,
    user: User = Depends(current_user),
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
        existing = (
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
        if len(existing) >= MAX_PHOTOS:
            raise AppError(f"You can have at most {MAX_PHOTOS} photos.")

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


@router.post("/{asset_id}/confirm")
async def confirm_upload(
    asset_id: str,
    req: ConfirmRequest,
    user: User = Depends(current_user),
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
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    assets = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user.id)
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
                "url": a.public_url if a.status == MediaStatus.processed else None,
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
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    asset = await db.get(MediaAsset, asset_id)
    if asset is None or asset.user_id != user.id:
        raise NotFound("Upload not found.")

    await storage.delete(asset.object_key)
    await db.delete(asset)
    await db.commit()
    log.info("media_deleted", asset_id=asset_id, user_id=user.id)


@router.get("/status")
async def processing_status(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """What the worker still owes this user. Lets onboarding show progress
    instead of a spinner with no end."""
    pending = (
        (
            await db.execute(
                select(jobs.Job)
                .where(jobs.Job.subject_id == user.id)
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
