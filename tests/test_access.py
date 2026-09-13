"""Who may join.

One rule left: you have to be 18. The campus domain check, the per-segment
caps and the waitlist are gone, so most of what this file used to assert went
with them — what is left is the age gate, and a set of tests asserting that
the door is genuinely open to any address, because that is the change and it
is the kind of thing a later "tightening" could quietly undo.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend import access
from backend.options import MIN_AGE
from tests.conftest import TEST_DOMAIN, register


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
            "email": f"young@{TEST_DOMAIN}",
            "password": "a-strong-enough-password",
            "display_name": "Young",
            "birthdate": _birthdate(16),
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "underage"


# ---------------------------------------------------------------------------
# The door is open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "someone@gmail.com",
        "someone@outlook.com",
        "someone@a-university.edu",
        "someone@sub.domain.co.in",
    ],
)
async def test_any_address_may_join(client, address):
    """The change: an ordinary personal account is a first-class way in, not a
    workaround. Previously every one of these was refused with a 403."""
    response = await client.post(
        "/api/auth/register",
        json={
            "email": address,
            "password": "a-strong-enough-password",
            "display_name": "Someone",
            "birthdate": _birthdate(21),
        },
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_the_same_address_still_cannot_join_twice(client):
    address = f"twice@{TEST_DOMAIN}"
    await register(client, address)

    again = await client.post(
        "/api/auth/register",
        json={
            "email": address,
            "password": "a-strong-enough-password",
            "display_name": "Again",
            "birthdate": _birthdate(21),
        },
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_registering_no_longer_reports_a_campus(client):
    """`scope` is gone from the response. A client still reading it would be
    reading something that cannot come back."""
    body = await register(client, f"nocampus@{TEST_DOMAIN}")
    assert "scope" not in body


@pytest.mark.asyncio
async def test_me_no_longer_reports_a_campus_or_a_queue(client, verified):
    body = (await client.get("/api/auth/me", headers=verified["headers"])).json()
    assert "scope_id" not in body
    assert "waitlist" not in body
