"""Who may join.

One rule left: you have to be 18. The campus domain check, the per-segment
caps and the waitlist are gone, and so is email — an account is a username
and a password, whose rules have their own file in `test_usernames.py`. What
is left here is the age gate and the things that must stay gone.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend import access
from backend.database import User
from backend.options import MIN_AGE
from tests.conftest import register


def _birthdate(age_years: int) -> str:
    today = date.today()
    try:
        born = today.replace(year=today.year - age_years)
    except ValueError:  # 29 February
        born = today.replace(year=today.year - age_years, day=28)
    return born.isoformat()


# ---------------------------------------------------------------------------
# Age
# ---------------------------------------------------------------------------


def test_age_is_counted_from_the_birthday_not_the_year():
    born = date(2007, 12, 31)
    assert access.age_on(born, today=date(2025, 12, 30)) == 17
    assert access.age_on(born, today=date(2025, 12, 31)) == 18


def test_someone_under_eighteen_is_refused():
    with pytest.raises(access.UnderageError):
        access.check_age(date.today() - timedelta(days=365 * 15))


def test_exactly_eighteen_is_allowed():
    access.check_age(date.today().replace(year=date.today().year - MIN_AGE))


def test_an_implausible_birthdate_is_refused():
    with pytest.raises(Exception, match="doesn't look right"):
        access.check_age(date(1850, 1, 1))


@pytest.mark.asyncio
async def test_registration_refuses_someone_underage(client):
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "young",
            "password": "a-strong-enough-password",
            "display_name": "Young",
            "birthdate": _birthdate(16),
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "underage"


# ---------------------------------------------------------------------------
# What must stay gone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_joining_does_not_ask_for_an_email(client, db_sessionmaker):
    """The change, pinned. A username and a password is the whole of an
    account; an address sent anyway is ignored rather than stored."""
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "nomail",
            "email": "someone@gmail.com",
            "password": "a-strong-enough-password",
            "display_name": "Someone",
            "birthdate": _birthdate(21),
        },
    )
    assert response.status_code == 201, response.text

    async with db_sessionmaker() as db:
        stored = (await db.execute(select(User).where(User.username == "nomail"))).scalar_one()
    assert stored.email is None

    me = (
        await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
        )
    ).json()
    assert "email" not in me
    assert me["username"] == "nomail"


@pytest.mark.asyncio
async def test_registering_no_longer_reports_a_campus(client):
    """`scope` is gone from the response. A client still reading it would be
    reading something that cannot come back."""
    body = await register(client, "nocampus")
    assert "scope" not in body


@pytest.mark.asyncio
async def test_me_no_longer_reports_a_campus_or_a_queue(client, verified):
    body = (await client.get("/api/auth/me", headers=verified["headers"])).json()
    assert "scope_id" not in body
    assert "waitlist" not in body
