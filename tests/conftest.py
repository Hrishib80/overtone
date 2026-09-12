"""Test fixtures.

The environment is set before any backend import, because settings are read —
and the engine built — at module import time.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-used-anywhere-real")
os.environ.setdefault("ALLOWED_ORIGINS", "*")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from datetime import date  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from backend import storage  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.database import Base, Scope, ScopeStatus, SegmentCap, get_db  # noqa: E402
from backend.seeds import seed_all  # noqa: E402

CAMPUS_DOMAIN = "campus.edu"
ADULT_BIRTHDATE = date(2003, 5, 17)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload" * 8
WEBM_BYTES = b"\x1aE\xdf\xa3" + b"fake-audio-payload" * 8


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    """In-memory object store standing in for Supabase.

    Autouse, because almost every flow now touches an upload and these tests
    are about our logic rather than Supabase's.
    """
    store: dict[str, bytes] = {}

    async def create_signed_upload(user_id, kind, content_type):
        key = storage.object_key(user_id, kind, content_type)
        return storage.SignedUpload(
            object_key=key,
            upload_url=f"https://storage.test/upload/{key}",
            token="signed-token",
            public_url=f"https://storage.test/public/{key}",
        )

    async def head(key):
        return (key in store, len(store[key]) if key in store else None)

    async def download(key):
        return store[key]

    async def delete(key):
        store.pop(key, None)

    monkeypatch.setattr(storage, "create_signed_upload", create_signed_upload)
    monkeypatch.setattr(storage, "head", head)
    monkeypatch.setattr(storage, "download", download)
    monkeypatch.setattr(storage, "delete", delete)
    return store


async def upload_media(client, headers, store, *, kind="photo", content_type="image/jpeg", body=None):
    """Walk the real three-step upload, with storage faked underneath."""
    ticket = await client.post(
        "/api/media/upload-url",
        headers=headers,
        json={"kind": kind, "content_type": content_type, "byte_size": 1000},
    )
    assert ticket.status_code == 201, ticket.text
    payload = ticket.json()

    store[payload["object_key"]] = body or (PNG_BYTES if kind == "photo" else WEBM_BYTES)

    confirmed = await client.post(f"/api/media/{payload['asset_id']}/confirm", headers=headers, json={})
    assert confirmed.status_code == 200, confirmed.text
    return payload["asset_id"]


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def db_sessionmaker():
    """A fresh in-memory database per test, so tests cannot leak state into each other."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def scope(db_sessionmaker):
    """A campus with room for two people per segment — small enough that cap
    behaviour is reachable in a test."""
    async with db_sessionmaker() as db:
        await seed_all(db)
        campus = Scope(
            slug="testcampus",
            name="Test Campus",
            email_domains=[CAMPUS_DOMAIN],
            status=ScopeStatus.building,
        )
        db.add(campus)
        await db.flush()
        db.add(SegmentCap(scope_id=campus.id, segment="man", cap=2))
        db.add(SegmentCap(scope_id=campus.id, segment="woman", cap=2))
        await db.commit()
        await db.refresh(campus)
        return campus


@pytest_asyncio.fixture
async def client(db_sessionmaker, scope):
    app = create_app()

    async def override_get_db():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def register(
    client: AsyncClient,
    email: str = f"aditi@{CAMPUS_DOMAIN}",
    *,
    birthdate: date = ADULT_BIRTHDATE,
    display_name: str = "Aditi",
) -> dict:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "a-strong-enough-password",
            "display_name": display_name,
            "birthdate": birthdate.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    body["headers"] = {"Authorization": f"Bearer {body['access_token']}"}
    return body


async def register_and_verify(client: AsyncClient, email: str = f"aditi@{CAMPUS_DOMAIN}", **kw) -> dict:
    account = await register(client, email, **kw)
    verified = await client.post("/api/auth/verify-email", json={"token": account["verification_token"]})
    assert verified.status_code == 200, verified.text
    return account


async def complete_profile(
    client: AsyncClient,
    headers: dict,
    *,
    visible_as: list[str],
    interested_in: list[str],
    store: dict | None = None,
) -> None:
    """Fill in everything `submit` requires: identity, intentions, photo, prompts."""
    patch = await client.patch(
        "/api/profile",
        headers=headers,
        json={
            "gender_identity_id": "woman" if visible_as[0] == "woman" else "man",
            "visible_as": visible_as,
            "interested_in": interested_in,
            "dating_intentions": "long_term",
        },
    )
    assert patch.status_code == 200, patch.text

    prompts = await client.put(
        "/api/profile/prompts",
        headers=headers,
        json={
            "answers": [
                {"prompt_id": "greatest_strength", "slot": 1, "kind": "written", "body": "Listening."},
                {"prompt_id": "simple_pleasures", "slot": 2, "kind": "written", "body": "Chai at 4pm."},
                {"prompt_id": "life_goal", "slot": 3, "kind": "written", "body": "Sail somewhere far."},
                {
                    "prompt_id": "how_to_pronounce_my_name",
                    "slot": 4,
                    "kind": "voice",
                    "audio_key": "voice/aditi.webm",
                    "audio_duration_ms": 9000,
                },
            ]
        },
    )
    assert prompts.status_code == 200, prompts.text

    if store is not None:
        await upload_media(client, headers, store)


@pytest_asyncio.fixture
async def registered(client):
    return await register(client)


@pytest_asyncio.fixture
async def verified(client):
    return await register_and_verify(client)
