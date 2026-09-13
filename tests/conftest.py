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

from backend import jobs as jobs_module  # noqa: E402
from backend import storage  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.database import Base, get_db  # noqa: E402
from backend.handlers import HANDLERS  # noqa: E402
from backend.seeds import seed_all  # noqa: E402

# Any address works now; this is just the one the tests use.
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


async def run_jobs(db_sessionmaker) -> int:
    """Drain the queue the way `worker.py --once` does.

    Loops rather than claiming a single batch: `process_voice` enqueues a
    `refresh_text` job as a side effect of its own completion, and that row
    does not exist yet when an outer, one-shot claim runs — a single pass can
    silently leave a chained job unprocessed.
    """
    total = 0
    while True:
        async with db_sessionmaker() as db:
            claimed = await jobs_module.claim(db, limit=20)
        if not claimed:
            return total
        for job in claimed:
            async with db_sessionmaker() as db:
                await HANDLERS[job.kind](db, job.payload)
            async with db_sessionmaker() as db:
                fresh = await db.get(jobs_module.Job, job.id)
                await jobs_module.complete(db, fresh)
            total += 1


async def give_consent(client, headers):
    """Agree to face analysis. Idempotent, so callers need not track it."""
    granted = await client.post("/api/account/consent", headers=headers)
    assert granted.status_code == 201, granted.text
    return granted.json()


async def upload_media(client, headers, store, *, kind="photo", content_type="image/jpeg", body=None):
    """Walk the real three-step upload, with storage faked underneath.

    Consent is given here rather than in `register_and_verify`, so that a test
    which wants a verified account *without* it still has one to work with —
    which is the whole point of the gate.
    """
    if kind == "photo":
        await give_consent(client, headers)

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
async def seeded(db_sessionmaker):
    """Reference data loaded: genders, sexualities, prompts.

    This used to also build a campus with per-segment caps. There is no campus
    and no cap any more — anyone may join with any address, and a finished
    profile on a verified address is a member.
    """
    async with db_sessionmaker() as db:
        await seed_all(db)
        await db.commit()


@pytest_asyncio.fixture
async def client(db_sessionmaker, seeded):
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
    username: str = "aditi",
    *,
    birthdate: date = ADULT_BIRTHDATE,
    display_name: str = "Aditi",
) -> dict:
    response = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-strong-enough-password",
            "display_name": display_name,
            "birthdate": birthdate.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    body["headers"] = {"Authorization": f"Bearer {body['access_token']}"}
    return body


async def register_and_verify(client: AsyncClient, username: str = "aditi", **kw) -> dict:
    """Registering *is* the whole of it now — there is no verification step.

    The name is kept because roughly a hundred call sites use it and renaming
    them would bury the change that matters in a diff full of renames. It
    returns an account that is through the door, which is what every caller
    actually wants from it.
    """
    account = await register(client, username, **kw)
    assert account["status"] == "onboarding", account
    return account


async def complete_profile(
    client: AsyncClient,
    headers: dict,
    *,
    visible_as: list[str],
    interested_in: list[str],
    store: dict,
) -> None:
    """Fill in everything `submit` requires: identity, intentions, photo, prompts.

    The voice prompt's `audio_key` now has to name a real MediaAsset the
    caller owns — the API verifies that rather than trusting the string — so
    the recording is uploaded here for real, through the same fake-storage
    path a photo goes through, rather than a placeholder string.
    """
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

    await upload_media(client, headers, store)
    voice_asset_id = await upload_media(client, headers, store, kind="voice", content_type="audio/webm")

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
                    "audio_key": voice_asset_id,
                    "audio_duration_ms": 9000,
                },
            ]
        },
    )
    assert prompts.status_code == 200, prompts.text


async def onboard(
    client: AsyncClient,
    username: str,
    *,
    visible_as: list[str],
    interested_in: list[str],
    store: dict,
) -> dict:
    """Register, complete the profile, and submit — the full path from a
    bare username to an account in the pool."""
    account = await register_and_verify(client, username)
    await complete_profile(
        client,
        account["headers"],
        visible_as=visible_as,
        interested_in=interested_in,
        store=store,
    )
    response = await client.post("/api/profile/submit", headers=account["headers"])
    assert response.status_code == 200, response.text
    return {**account, "submit": response.json()}


@pytest_asyncio.fixture
async def registered(client):
    return await register(client)


@pytest_asyncio.fixture
async def verified(client):
    return await register_and_verify(client)
