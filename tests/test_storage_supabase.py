"""The Supabase storage client against the answers Supabase actually gives."""

from __future__ import annotations

import httpx
import pytest

from backend import storage
from backend.config import settings

# Taken at import, before the autouse `fake_storage` fixture swaps it for the
# in-memory one — these tests are about the real client.
head = storage.head


@pytest.fixture
def supabase(monkeypatch):
    monkeypatch.setattr(settings, "storage_provider", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_key", "sb_secret_test")

    def respond_with(status: int, body: dict):
        real_client = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
            return real_client(*args, **kwargs)

        monkeypatch.setattr(storage.httpx, "AsyncClient", client)

    return respond_with


@pytest.mark.asyncio
async def test_a_missing_object_is_missing_not_an_outage(supabase):
    """Captured from a live project: a missing object is HTTP 400 with the 404
    in the body. It has to reach confirm as "not there", not as a 503."""
    supabase(
        400, {"statusCode": "404", "error": "not_found", "message": "Object not found", "code": "NoSuchKey"}
    )
    assert await head("user/photo/never-uploaded.jpg") == (False, None)


@pytest.mark.asyncio
async def test_a_real_bad_request_is_still_an_error(supabase):
    supabase(400, {"statusCode": "400", "error": "invalid_key", "message": "Invalid key"})
    with pytest.raises(storage.ServiceUnavailable):
        await head("user/photo/x.jpg")


@pytest.mark.asyncio
async def test_an_object_that_arrived_reports_its_size(supabase):
    supabase(200, {"name": "x.jpg", "size": 504})
    assert await head("user/photo/x.jpg") == (True, 504)
