"""Object storage.

Media never transits the API *in production*. The client asks for a signed
URL, uploads the bytes straight to Supabase, then tells us the key. That
removes the whole class of failure where a large upload exhausts application
memory, and it keeps the API tier small enough to scale on request volume
alone. The worker is the only thing that downloads bytes back, and only once,
to embed them.

There is a second provider, `local`, and it exists because the first one made
the app impossible to work on. Every upload needed a live Supabase project, so
a developer with no project — or one that has been paused, renamed or deleted,
which is what happened — could not add a photo at all, and the failure arrives
as a 503 with nothing in it to act on. `local` writes into `MEDIA_ROOT`, which
the app already serves at `/media_uploads` and which the demo seeder already
writes to.

In `local` mode the bytes *do* pass through the API, which is the one property
the signed-URL design exists to avoid. Acceptable on a developer machine,
unacceptable in production — so production refuses to start on it.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import pathlib
import secrets
import time
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


def local_mode() -> bool:
    return settings.storage_provider == "local"


def _root() -> pathlib.Path:
    root = pathlib.Path(settings.media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_path(key: str) -> pathlib.Path:
    """Resolve a storage key under MEDIA_ROOT, refusing anything that escapes.

    Keys are minted by `object_key` and are not user input, but this function
    is one `..` away from writing anywhere on the disk if that ever stops
    being true.
    """
    root = _root().resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise AppError("That upload path isn't valid.")
    return path


def check_configuration() -> None:
    """Called at startup, like the mailer. A storage backend that only fails
    when somebody tries to add a photo is one found too late."""
    if settings.storage_provider not in {"local", "supabase"}:
        raise RuntimeError(f"Unknown storage provider {settings.storage_provider!r}.")
    if settings.storage_provider == "supabase" and not (settings.supabase_url and settings.supabase_key):
        raise RuntimeError("STORAGE_PROVIDER=supabase needs SUPABASE_URL and SUPABASE_KEY.")
    if settings.is_production and local_mode():
        raise RuntimeError(
            "Production cannot use local storage: uploads would pass through the API "
            "and live on one machine's disk. Set STORAGE_PROVIDER=supabase."
        )


def _configured() -> None:
    if not settings.supabase_url or not settings.supabase_key:
        raise StorageNotConfigured()


def _explain(exc: BaseException) -> str:
    """Whatever the storage service actually said, in one line.

    `raise_for_status` throws away the body, which is where Supabase puts the
    only useful part — the bucket that does not exist, or the policy that
    refused the key.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return type(exc).__name__
    try:
        body = response.json()
    except Exception:
        return f"{response.status_code}: {response.text[:200]}"
    message = body.get("message") or body.get("error") or str(body)
    return f"{response.status_code}: {message}"


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
    if local_mode():
        return f"/media_uploads/{key}"
    return f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_bucket}/{key}"


async def create_signed_upload(user_id: str, kind: str, content_type: str) -> SignedUpload:
    """Mint a short-lived URL the browser can PUT to directly."""
    if local_mode():
        key = object_key(user_id, kind, content_type)
        # Signed with the same short TTL as the real thing, so the client
        # code path is identical and an expired ticket behaves the same way.
        token = _sign_local(key)
        log.info("signed_upload_issued", key=key, kind=kind, provider="local")
        return SignedUpload(
            object_key=key,
            upload_url=f"/api/media/local/{key}?token={token}",
            token=token,
            public_url=public_url(key),
        )

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
        # Supabase puts the actual reason in the response body — a missing
        # bucket and an anon key that RLS refuses both arrive as a bare 400,
        # and without this the only thing anyone sees is "try again", which is
        # advice that will never work.
        detail = _explain(exc)
        log.error("signed_upload_failed", key=key, bucket=settings.supabase_bucket, detail=detail)
        raise ServiceUnavailable("Could not start the upload. Try again.", detail=detail) from exc

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


def _is_missing_object(response: httpx.Response) -> bool:
    """Supabase answers a missing object with HTTP 400, not 404, and puts the
    404 in the body: `{"statusCode": "404", "error": "not_found", ...}`.

    Read as a failure, an upload that never arrived came back as "Could not
    verify the upload" — a 503 saying storage is down — instead of asking the
    person to try again.
    """
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return str(body.get("statusCode")) == "404" or body.get("error") == "not_found"


async def head(key: str) -> tuple[bool, int | None]:
    """Confirm the bytes actually arrived, and how many.

    The client tells us it uploaded; this is how we check rather than believe
    it. A row marked uploaded without this would let anyone claim an asset
    that does not exist.
    """
    if local_mode():
        path = _local_path(key)
        return (True, path.stat().st_size) if path.exists() else (False, None)

    _configured()
    url = f"{settings.supabase_url}/storage/v1/object/info/{settings.supabase_bucket}/{key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=_headers())
            if response.status_code == 404 or _is_missing_object(response):
                return False, None
            response.raise_for_status()
            info = response.json()
    except httpx.HTTPError as exc:
        log.error("storage_head_failed", key=key, bucket=settings.supabase_bucket, detail=_explain(exc))
        raise ServiceUnavailable("Could not verify the upload.") from exc

    size = info.get("size") or (info.get("metadata") or {}).get("size")
    return True, int(size) if size is not None else None


async def download(key: str) -> bytes:
    """Worker-side read. The only place bytes enter application memory."""
    if local_mode():
        path = _local_path(key)
        if not path.exists():
            raise ServiceUnavailable("Could not read the uploaded file.")
        return path.read_bytes()

    _configured()
    url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_bucket}/{key}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=_headers())
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        log.error("storage_download_failed", key=key, bucket=settings.supabase_bucket, detail=_explain(exc))
        raise ServiceUnavailable("Could not read the uploaded file.") from exc


async def delete(key: str) -> None:
    if local_mode():
        _local_path(key).unlink(missing_ok=True)
        return

    _configured()
    url = f"{settings.supabase_url}/storage/v1/object/{settings.supabase_bucket}/{key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.delete(url, headers=_headers())
            if response.status_code != 404:
                response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("storage_delete_failed", key=key, bucket=settings.supabase_bucket, detail=_explain(exc))
        raise ServiceUnavailable("Could not delete that file.") from exc


# ---------------------------------------------------------------------------
# Local provider internals
# ---------------------------------------------------------------------------


def _sign_local(key: str) -> str:
    """A ticket only this server could have issued, carrying its own expiry.

    Without it the local upload endpoint would accept any key from anybody,
    which is a stranger writing files into somebody else's media prefix.
    """
    expires = int(time.time()) + settings.upload_url_ttl_seconds
    payload = f"{key}:{expires}"
    digest = hmac.new(settings.jwt_secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{digest}"


def verify_local_token(key: str, token: str) -> bool:
    expires, _, digest = token.partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    payload = f"{key}:{expires}"
    expected = hmac.new(settings.jwt_secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)


def write_local(key: str, data: bytes) -> None:
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
