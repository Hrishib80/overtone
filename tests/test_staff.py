"""Staff accounts: the portal, and nothing else.

A staff account decides who joins. It is deliberately not a member — no
profile, never in the pool, no invite code — so the tests here are mostly about
the doors it must not be able to walk through, and in particular the one that
would quietly turn it into an applicant: finishing a profile.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.database import User, UserStatus
from tests.conftest import onboard
from tests.test_pairs import _two_mutual_users

PASSWORD = "portal-only-password"


def _manage():
    path = Path(__file__).resolve().parent.parent / "scripts" / "manage.py"
    spec = importlib.util.spec_from_file_location("overtone_manage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_staff(monkeypatch, db_sessionmaker, username: str, password: str) -> int:
    manage = _manage()
    monkeypatch.setattr(manage, "AsyncSessionLocal", db_sessionmaker)
    monkeypatch.setattr(sys, "stdin", io.StringIO(password + "\n"))
    return await manage.cmd_staff(argparse.Namespace(username=username))


async def _sign_in(client, username: str, password: str = PASSWORD) -> dict:
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_the_command_makes_an_admin_who_is_not_a_member(client, db_sessionmaker, monkeypatch):
    assert await _run_staff(monkeypatch, db_sessionmaker, "@DeskKeeper", PASSWORD) == 0

    async with db_sessionmaker() as db:
        user = (await db.execute(select(User).where(User.username == "deskkeeper"))).scalars().one()
    assert user.status == UserStatus.staff
    assert user.is_admin
    assert user.password_hash != PASSWORD

    headers = await _sign_in(client, "deskkeeper")
    me = (await client.get("/api/auth/me", headers=headers)).json()
    assert me["status"] == "staff" and me["is_admin"]
    assert (await client.get("/api/admin/waitlist", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_a_staff_account_can_reach_nothing_a_member_can(
    client, db_sessionmaker, monkeypatch, fake_storage
):
    await _run_staff(monkeypatch, db_sessionmaker, "deskkeeper", PASSWORD)
    headers = await _sign_in(client, "deskkeeper")

    refused = [
        ("get", "/api/pairs/next"),
        ("get", "/api/connections"),
        ("get", "/api/connections/counts"),
        ("get", "/api/profile"),
        ("post", "/api/profile/submit"),
        ("post", "/api/profile/resubmit"),
        ("get", "/api/media"),
        ("post", "/api/media/upload-url"),
    ]
    for method, path in refused:
        response = await getattr(client, method)(path, headers=headers)
        assert response.status_code in (401, 403), f"{method.upper()} {path}: {response.status_code}"

    invite = (await client.get("/api/account/invite", headers=headers)).json()
    assert invite["can_invite"] is False and not invite.get("code")

    async with db_sessionmaker() as db:
        user = (await db.execute(select(User).where(User.username == "deskkeeper"))).scalars().one()
    # Above all: trying to finish a profile did not put it on the waitlist.
    assert user.status == UserStatus.staff


@pytest.mark.asyncio
async def test_a_staff_account_is_never_in_the_pool(client, db_sessionmaker, monkeypatch, fake_storage):
    await _run_staff(monkeypatch, db_sessionmaker, "deskkeeper", PASSWORD)
    async with db_sessionmaker() as db:
        staff_id = (await db.execute(select(User.id).where(User.username == "deskkeeper"))).scalar_one()

    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    seen = set()
    for _ in range(4):
        pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
        if not pair:
            break
        ids = [subject["id"] for subject in pair["subjects"]]
        seen |= set(ids)
        await client.post(
            f"/api/pairs/{pair['id']}/decide", headers=viewer["headers"], json={"chosen_id": ids[0]}
        )
    # Before/after: the pool did serve people, so the absence means something.
    assert seen
    assert staff_id not in seen


@pytest.mark.asyncio
async def test_the_command_will_not_take_over_a_member(client, db_sessionmaker, monkeypatch, fake_storage):
    await onboard(client, "member", visible_as=["man"], interested_in=["woman"], store=fake_storage)
    assert await _run_staff(monkeypatch, db_sessionmaker, "member", PASSWORD) == 1

    async with db_sessionmaker() as db:
        user = (await db.execute(select(User).where(User.username == "member"))).scalars().one()
    assert user.status != UserStatus.staff and not user.is_admin


@pytest.mark.asyncio
async def test_running_it_again_resets_the_password(client, db_sessionmaker, monkeypatch):
    await _run_staff(monkeypatch, db_sessionmaker, "deskkeeper", PASSWORD)
    assert await _run_staff(monkeypatch, db_sessionmaker, "deskkeeper", "a-different-long-one") == 0

    stale = await client.post("/api/auth/login", json={"username": "deskkeeper", "password": PASSWORD})
    assert stale.status_code == 401
    await _sign_in(client, "deskkeeper", "a-different-long-one")


@pytest.mark.asyncio
async def test_a_short_password_is_refused(db_sessionmaker, monkeypatch):
    assert await _run_staff(monkeypatch, db_sessionmaker, "deskkeeper", "short") == 1
    async with db_sessionmaker() as db:
        assert (await db.execute(select(User).where(User.username == "deskkeeper"))).first() is None
