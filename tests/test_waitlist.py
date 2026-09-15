"""The waitlist, the admin portal, and invite codes.

With approval on, a finished profile waits until an admin approves it, and is
shown to nobody and able to reach nobody until then. An admin can approve, or
send the profile back with a note; the person edits and resubmits. A member an
admin approved has an invite code, and somebody who joins with it skips the
wait — within limits on how far one vouch reaches.

The rest of the suite runs with approval off (see conftest), so every test here
switches it on explicitly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend import invites
from backend.config import settings
from backend.database import User, UserStatus
from tests.conftest import all_route_paths, onboard, register, run_jobs
from tests.test_privacy import _uid


@pytest.fixture
def approval(monkeypatch):
    monkeypatch.setattr(settings, "require_approval", True)


async def _set(db_sessionmaker, user_id, **fields):
    async with db_sessionmaker() as db:
        user = await db.get(User, user_id)
        for key, value in fields.items():
            setattr(user, key, value)
        await db.commit()


async def _status(db_sessionmaker, user_id):
    async with db_sessionmaker() as db:
        return (await db.get(User, user_id)).status


async def _admin(client, db_sessionmaker, fake_storage):
    """An admin who is a member, the way `manage.py admin` insists on."""
    account = await onboard(client, "boss", visible_as=["man"], interested_in=["woman"], store=fake_storage)
    await _set(db_sessionmaker, _uid(account), status=UserStatus.active, is_admin=True)
    return account


async def _woman(client, name, fake_storage, **kw):
    return await onboard(client, name, visible_as=["woman"], interested_in=["man"], store=fake_storage, **kw)


# ---------------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_finished_profile_waits_and_is_shown_to_nobody(
    client, db_sessionmaker, fake_storage, monkeypatch
):
    """Before and after, through a real viewer: with one approved woman the
    viewer has nobody to pair; the applicant does not change that while
    waiting, and does the moment she is approved."""
    viewer = await onboard(client, "viewer", visible_as=["man"], interested_in=["woman"], store=fake_storage)
    await _woman(client, "member", fake_storage)

    monkeypatch.setattr(settings, "require_approval", True)
    applicant = await _woman(client, "applicant", fake_storage)
    await run_jobs(db_sessionmaker)

    assert applicant["submit"]["status"] == "waitlisted"
    assert (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"] is None

    admin = await _admin(client, db_sessionmaker, fake_storage)
    approved = await client.post(f"/api/admin/applicants/{_uid(applicant)}/approve", headers=admin["headers"])
    assert approved.status_code == 200, approved.text

    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    assert pair is not None
    assert _uid(applicant) in {s["id"] for s in pair["subjects"]}


@pytest.mark.asyncio
async def test_a_waiting_account_can_reach_nobody(client, fake_storage, approval):
    applicant = await _woman(client, "applicant", fake_storage)
    for path in ("/api/pairs/next", "/api/connections", "/api/connections/counts"):
        response = await client.get(path, headers=applicant["headers"])
        assert response.status_code == 403, f"{path}: {response.text}"

    me = (await client.get("/api/auth/me", headers=applicant["headers"])).json()
    assert me["status"] == "waitlisted"
    # And the steps of their own account stay open.
    assert (await client.get("/api/profile", headers=applicant["headers"])).status_code == 200


@pytest.mark.asyncio
async def test_with_approval_off_a_finished_profile_is_a_member(client, fake_storage):
    """The switch that opens signup up again later works."""
    person = await _woman(client, "open", fake_storage)
    assert person["submit"]["status"] == "active"


# ---------------------------------------------------------------------------
# The portal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_portal_is_not_found_for_anyone_but_an_admin(
    client, db_sessionmaker, fake_storage, approval
):
    member = await _woman(client, "member", fake_storage)
    await _set(db_sessionmaker, _uid(member), status=UserStatus.active, is_reviewer=True)
    applicant = await _woman(client, "applicant", fake_storage)

    for method, path in (
        ("GET", "/api/admin/waitlist"),
        ("GET", "/api/admin/invited"),
        ("POST", f"/api/admin/applicants/{_uid(applicant)}/approve"),
    ):
        response = await client.request(method, path, headers=member["headers"])
        # 404 even for a reviewer: that the portal exists is not theirs to know.
        assert response.status_code == 404, f"{method} {path}: {response.text}"
    assert await _status(db_sessionmaker, _uid(applicant)) == UserStatus.waitlisted


@pytest.mark.asyncio
async def test_signed_out_the_portal_looks_like_nothing_is_there(client):
    """Not 401: a "sign in" answer at a path where made-up paths get 404 is the
    confirmation the portal's hidden address exists to avoid."""

    def shape(response):
        body = response.json()
        body["error"].pop("request_id", None)
        return response.status_code, body

    made_up = shape(await client.get("/api/definitely-not-a-route"))
    assert made_up[0] == 404, "an unknown API path must be a 404, not the SPA's HTML"
    for headers in ({}, {"Authorization": "Bearer not-a-token"}):
        for path in ("/api/admin/waitlist", "/api/admin/invited"):
            assert shape(await client.get(path, headers=headers)) == made_up, f"{path} {headers}"


@pytest.mark.asyncio
async def test_nothing_over_http_can_make_an_admin(client, db_sessionmaker, fake_storage):
    from backend.app import create_app

    paths = all_route_paths(create_app())
    assert "/api/admin/waitlist" in paths, "the walk did not reach inside the routers"
    assert not any(path.endswith("/admin") or "make-admin" in path or "grant" in path for path in paths)

    person = await register(client, "sneaky")
    patched = await client.patch("/api/profile", headers=person["headers"], json={"is_admin": True})
    assert patched.status_code in (400, 422)
    async with db_sessionmaker() as db:
        assert (await db.get(User, _uid(person))).is_admin is False


@pytest.mark.asyncio
async def test_the_waitlist_shows_everything_needed_to_decide(
    client, db_sessionmaker, fake_storage, approval
):
    admin = await _admin(client, db_sessionmaker, fake_storage)
    await _woman(client, "first", fake_storage)
    await _woman(client, "second", fake_storage)

    body = (await client.get("/api/admin/waitlist", headers=admin["headers"])).json()

    assert [a["user"]["username"] for a in body["waiting"]] == ["first", "second"], "oldest first"
    first = body["waiting"][0]
    assert first["photos"] and first["prompts"]
    assert first["application"]["applied_at"]
    assert body["sent_back"] == []


@pytest.mark.asyncio
async def test_sending_back_then_resubmitting(client, db_sessionmaker, fake_storage, approval):
    admin = await _admin(client, db_sessionmaker, fake_storage)
    applicant = await _woman(client, "applicant", fake_storage)
    uid = _uid(applicant)

    refused = await client.post(
        f"/api/admin/applicants/{uid}/send-back", headers=admin["headers"], json={"note": "   "}
    )
    assert refused.status_code == 400

    sent = await client.post(
        f"/api/admin/applicants/{uid}/send-back",
        headers=admin["headers"],
        json={"note": "Please add a photo where your face is clearly visible."},
    )
    assert sent.status_code == 200, sent.text

    waitlist = (await client.get("/api/admin/waitlist", headers=admin["headers"])).json()
    assert waitlist["waiting"] == []
    assert [a["user"]["id"] for a in waitlist["sent_back"]] == [uid]

    me = (await client.get("/api/auth/me", headers=applicant["headers"])).json()
    assert me["status"] == "waitlisted"
    assert me["application_note"] == "Please add a photo where your face is clearly visible."

    again = await client.post("/api/profile/resubmit", headers=applicant["headers"])
    assert again.status_code == 200, again.text
    waitlist = (await client.get("/api/admin/waitlist", headers=admin["headers"])).json()
    assert [a["user"]["id"] for a in waitlist["waiting"]] == [uid]
    assert (await client.get("/api/auth/me", headers=applicant["headers"])).json()["application_note"] is None

    # A profile already in the queue has nothing to resubmit.
    assert (await client.post("/api/profile/resubmit", headers=applicant["headers"])).status_code == 400


@pytest.mark.asyncio
async def test_an_admin_can_approve_somebody_they_sent_back(client, db_sessionmaker, fake_storage, approval):
    admin = await _admin(client, db_sessionmaker, fake_storage)
    applicant = await _woman(client, "applicant", fake_storage)
    await client.post(
        f"/api/admin/applicants/{_uid(applicant)}/send-back", headers=admin["headers"], json={"note": "x"}
    )
    approved = await client.post(f"/api/admin/applicants/{_uid(applicant)}/approve", headers=admin["headers"])
    assert approved.status_code == 200
    async with db_sessionmaker() as db:
        user = await db.get(User, _uid(applicant))
    assert user.status == UserStatus.active
    assert user.application_note is None
    assert user.approved_by_id == _uid(admin)


@pytest.mark.asyncio
async def test_only_somebody_waiting_can_be_approved(client, db_sessionmaker, fake_storage):
    admin = await _admin(client, db_sessionmaker, fake_storage)
    member = await _woman(client, "member", fake_storage)
    response = await client.post(f"/api/admin/applicants/{_uid(member)}/approve", headers=admin["headers"])
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


async def _code(client, account):
    return (await client.get("/api/account/invite", headers=account["headers"])).json()


@pytest.mark.asyncio
async def test_an_invite_skips_the_waitlist(client, db_sessionmaker, fake_storage, approval):
    admin = await _admin(client, db_sessionmaker, fake_storage)
    invite = await _code(client, admin)
    assert invite["can_invite"] is True
    assert len(invite["code"]) == invites.CODE_LENGTH
    assert invite["link"].endswith(f"/join?invite={invite['code']}")
    assert (invite["used"], invite["limit"]) == (0, invites.INVITES_PER_MEMBER)

    # Typed the way people type codes read off a screen.
    messy = f"{invite['code'][:4].lower()}-{invite['code'][4:].lower()}"
    friend = await _woman(client, "friend", fake_storage, invite_code=messy)

    assert friend["submit"]["status"] == "active"
    assert (await _code(client, admin))["used"] == 1

    invited = (await client.get("/api/admin/invited", headers=admin["headers"])).json()["invited"]
    assert [(i["username"], i["invited_by"]["username"]) for i in invited] == [("friend", "boss")]


@pytest.mark.asyncio
async def test_an_invited_member_cannot_invite_in_turn(client, db_sessionmaker, fake_storage, approval):
    """One approval must not become a chain."""
    admin = await _admin(client, db_sessionmaker, fake_storage)
    friend = await _woman(client, "friend", fake_storage, invite_code=(await _code(client, admin))["code"])
    assert friend["submit"]["status"] == "active"
    assert await _code(client, friend) == {"can_invite": False}


@pytest.mark.asyncio
async def test_nobody_waiting_or_unfinished_has_a_code(client, fake_storage, approval):
    applicant = await _woman(client, "applicant", fake_storage)
    unfinished = await register(client, "unfinished")
    assert await _code(client, applicant) == {"can_invite": False}
    assert await _code(client, unfinished) == {"can_invite": False}


@pytest.mark.asyncio
async def test_a_bad_code_is_refused_before_an_account_exists(client, db_sessionmaker):
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "hopeful",
            "password": "a-strong-enough-password",
            "display_name": "Hopeful",
            "birthdate": "2000-01-01",
            "invite_code": "NOTACODE",
        },
    )
    assert response.status_code == 400
    assert response.json()["fields"][0]["field"] == "invite_code"
    async with db_sessionmaker() as db:
        assert (await db.execute(select(User).where(User.username == "hopeful"))).first() is None


@pytest.mark.asyncio
async def test_a_code_runs_out(client, db_sessionmaker, fake_storage, approval, monkeypatch):
    monkeypatch.setattr(invites, "INVITES_PER_MEMBER", 2)
    admin = await _admin(client, db_sessionmaker, fake_storage)
    code = (await _code(client, admin))["code"]
    await register(client, "one", invite_code=code)
    await register(client, "two", invite_code=code)

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "three",
            "password": "a-strong-enough-password",
            "display_name": "Three",
            "birthdate": "2000-01-01",
            "invite_code": code,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "That invite code has been used up."


@pytest.mark.asyncio
async def test_a_vouch_from_somebody_since_suspended_counts_for_nothing(
    client, db_sessionmaker, fake_storage, approval
):
    admin = await _admin(client, db_sessionmaker, fake_storage)
    member = await _woman(client, "member", fake_storage)
    await _set(db_sessionmaker, _uid(member), status=UserStatus.active)
    code = (await _code(client, member))["code"]

    friend = await register(client, "friend", invite_code=code)
    await _set(db_sessionmaker, _uid(member), status=UserStatus.suspended)

    from tests.conftest import complete_profile

    await complete_profile(
        client, friend["headers"], visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    submitted = await client.post("/api/profile/submit", headers=friend["headers"])
    assert submitted.json()["status"] == "waitlisted"
    assert admin  # the admin exists; the point is only that the vouch did not count


@pytest.mark.asyncio
async def test_deleting_an_inviter_leaves_no_row_naming_them(client, db_sessionmaker, fake_storage, approval):
    admin = await _admin(client, db_sessionmaker, fake_storage)
    friend = await _woman(client, "friend", fake_storage, invite_code=(await _code(client, admin))["code"])
    approved = await _woman(client, "approved", fake_storage)
    await client.post(f"/api/admin/applicants/{_uid(approved)}/approve", headers=admin["headers"])

    gone = await client.post(
        "/api/account/delete", headers=admin["headers"], json={"password": "a-strong-enough-password"}
    )
    assert gone.status_code == 200, gone.text

    async with db_sessionmaker() as db:
        friend_row = await db.get(User, _uid(friend))
        approved_row = await db.get(User, _uid(approved))
    assert friend_row.status == UserStatus.active, "the people they let in stay members"
    assert friend_row.invited_by_id is None
    assert approved_row.approved_by_id is None


def test_production_publishes_no_api_schema(monkeypatch):
    """The schema names every route, `/api/admin/...` included — publishing it
    would undo the 404 the portal answers to anybody it does not name."""
    from backend.app import create_app

    monkeypatch.setattr(settings, "environment", "production")
    app = create_app()
    assert app.openapi_url is None
    assert app.docs_url is None
