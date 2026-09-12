"""Object storage (Supabase).

Media never transits the API. The client asks for a signed URL, uploads the
bytes straight to storage, then tells us the key. That removes the whole class
of failure where a large upload exhausts application memory, and it means the
API tier stays small enough to scale on request volume alone.

The worker is the only thing that downloads bytes back, and only once, to
embed them.
"""

from __future__ import annotations

import mimetypes
import secrets
from dataclasses import dataclass

import httpx

from backend.config import settings
from backend.errors import AppError, ServiceUnavailable
from backend.logging_config import get_logger

log = get_logger(__name__)

PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}
AUDIO_TYPES = {"audio/webm": "webm", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/wav": "wav"}


class StorageNotConfigured(AppError):
    status_code = 503
    code = "storage_not_configured"
    message = "Uploads are not available right now."


@dataclass(slots=True)
class SignedUpload:
    object_key: str
    upload_url: str
    token: str
    public_url: str


def _configured() -> None:
    if not settings.supabase_url or not settings.supabase_key:
        raise StorageNotConfigured()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.supabase_key}",
        "apiKey": settings.supabase_key,
    }


def object_key(user_id: str, kind: str, content_type: str) -> str:
    """Unguessable per-file path, namespaced by user so deletion is a prefix sweep."""
    table = PHOTO_TYPES if kind == "photo" else AUDIO_TYPES
    extension = table.get(content_type) or mimetypes.guess_extension(content_type) or "bin"
    return f"{user_id}/{kind}/{secrets.token_urlsafe(16)}.{extension.lstrip('.')}"


def public_url(key: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_bucket}/{key}"


async def create_signed_upload(user_id: str, kind: str, content_type: str) -> SignedUpload:
    """Mint a short-lived URL the browser can PUT to directly."""
    _configured()
    key = object_key(user_id, kind, content_type)
    endpoint = f"{settings.supabase_url}/storage/v1/object/upload/sign/{settings.supabase_bucket}/{key}"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                endpoint,
                headers=_headers(),
                json={"expiresIn": settings.upload_url_ttl_seconds},
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        log.exception("signed_upload_failed", key=key)
        raise ServiceUnavailable("Could not start the upload. Try again.") from exc

    # Supabase returns a path like /object/upload/sign/<bucket>/<key>?token=…
    signed_path = body.get("url", "")
    token = signed_path.partition("token=")[2]

    log.info("signed_upload_issued", key=key, kind=kind)
    return SignedUpload(
        object_key=key,
        upload_url=f"{settings.supabase_url}/storage/v1{signed_path}",
        token=token,
        public_url=public_url(key),
    )


async def head(key: str) -> tuple[bool, int | None]:
    """Confirm the bytes actually arrived, and how many.

    The client tells us it uploaded; this is how we check rather than believe
    it. A row marked uploaded without this would let anyone claim an asset
    that does not exist.
    """
    _configured()
    url = f"{settings.supabase_url}/storage/v1/object/info/{settings.supabase_bucket}/{key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=_headers())
            if response.status_code == 404:
                return False, None
            response.raise_for_status()
            info = response.json()
    except httpx.HTTPError as exc:
        log.exception("storage_head_failed", key=key)
        raise ServiceUnavailable("Could not verify the upload.") from exc

    size = info.get("size") or (info.get("metadata") or {}).get("size")
    return True, int(size) if size is not None else None


async def download(key: str) -> bytes:
    """Worker-side read. The only place bytes enter application memory."""
    _configured()
    url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_bucket}/{key}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=_headers())
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        log.exception("storage_download_failed", key=key)
        raise ServiceUnavailable("Could not read the uploaded file.") from exc


async def delete(key: str) -> None:
    _configured()
    url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_bucket}/{key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.delete(url, headers=_headers())
            if response.status_code != 404:
                response.raise_for_status()
    except httpx.HTTPError as exc:
        log.exception("storage_delete_failed", key=key)
        raise ServiceUnavailable("Could not delete that file.") from exc
