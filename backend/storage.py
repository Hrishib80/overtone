"""Object storage.

Interim implementation: files are still proxied through the API. Phase 02
replaces this with signed direct-to-storage uploads so large media never
touches application memory.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import httpx

from backend.config import settings
from backend.errors import ServiceUnavailable
from backend.logging_config import get_logger

log = get_logger(__name__)


def _object_url(path: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/{settings.supabase_bucket}/{path}"


async def upload_file(file_path: str | Path, user_id: str, filename: str) -> str:
    """Upload and return the public URL.

    Raises rather than falling back to a local path: the old fallback returned a
    URL that works on one machine and 404s on every other instance, so an
    upload could 'succeed' and still be broken for the user.
    """
    path = f"{user_id}/{filename}"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # Read off the event loop — a blocking read here stalls every other request.
    payload = await asyncio.to_thread(Path(file_path).read_bytes)

    headers = {
        "Authorization": f"Bearer {settings.supabase_key}",
        "apiKey": settings.supabase_key,
        "Content-Type": content_type,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(_object_url(path), headers=headers, content=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.exception("storage_upload_failed", path=path, bytes=len(payload))
        raise ServiceUnavailable("Upload failed. Try again.") from exc

    log.info("storage_uploaded", path=path, bytes=len(payload))
    return f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_bucket}/{path}"


async def delete_file(storage_path: str) -> None:
    headers = {
        "Authorization": f"Bearer {settings.supabase_key}",
        "apiKey": settings.supabase_key,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(_object_url(storage_path), headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.exception("storage_delete_failed", path=storage_path)
        raise ServiceUnavailable("Could not delete that file.") from exc
