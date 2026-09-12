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

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.database import Base, get_db  # noqa: E402


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
async def client(db_sessionmaker):
    app = create_app()

    async def override_get_db():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def registered(client):
    """A registered account plus its auth header."""
    payload = {
        "email": "aditi@campus.edu",
        "password": "a-strong-enough-password",
        "display_name": "Aditi",
    }
    response = await client.post("/api/auth/register", json=payload)
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {
        "payload": payload,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }
