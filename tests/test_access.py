"""Registration gates: campus, age, verification."""

from datetime import date, timedelta

import pytest

from tests.conftest import CAMPUS_DOMAIN, register, register_and_verify


@pytest.mark.asyncio
async def test_campus_email_is_accepted(client):
    account = await register(client)
    assert account["status"] == "pending_verification"
    assert account["scope"]["slug"] == "testcampus"


@pytest.mark.asyncio
async def test_subdomain_of_the_campus_is_accepted(client):
    """A student at cse.campus.edu belongs to campus.edu."""
    account = await register(client, f"ravi@cse.{CAMPUS_DOMAIN}")
    assert account["scope"]["slug"] == "testcampus"


@pytest.mark.asyncio
async def test_outside_email_is_refused(client):
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "someone@gmail.com",
            "password": "a-strong-enough-password",
            "display_name": "Outsider",
            "birthdate": "2003-05-17",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "scope_not_recognised"


@pytest.mark.asyncio
async def test_a_domain_that_merely_ends_with_the_campus_string_is_refused(client):
    """notcampus.edu must not match campus.edu."""
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "sneaky@notcampus.edu",
            "password": "a-strong-enough-password",
            "display_name": "Sneaky",
            "birthdate": "2003-05-17",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_under_eighteen_is_refused(client):
    almost = date.today() - timedelta(days=365 * 17)
    response = await client.post(
        "/api/auth/register",
        json={
            "email": f"young@{CAMPUS_DOMAIN}",
            "password": "a-strong-enough-password",
            "display_name": "Young",
            "birthdate": almost.isoformat(),
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "underage"


@pytest.mark.asyncio
async def test_the_day_someone_turns_eighteen_is_allowed(client):
    today = date.today()
    try:
        eighteenth = today.replace(year=today.year - 18)
    except ValueError:  # 29 February
        eighteenth = today.replace(year=today.year - 18, day=28)

    response = await client.post(
        "/api/auth/register",
        json={
            "email": f"exactly18@{CAMPUS_DOMAIN}",
            "password": "a-strong-enough-password",
            "display_name": "Exactly",
            "birthdate": eighteenth.isoformat(),
        },
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_verification_moves_the_account_to_onboarding(client):
    account = await register(client)
    response = await client.post("/api/auth/verify-email", json={"token": account["verification_token"]})
    assert response.status_code == 200
    assert response.json()["status"] == "onboarding"


@pytest.mark.asyncio
async def test_a_verification_token_cannot_be_reused(client):
    account = await register(client)
    token = account["verification_token"]

    assert (await client.post("/api/auth/verify-email", json={"token": token})).status_code == 200
    second = await client.post("/api/auth/verify-email", json={"token": token})
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_a_wrong_verification_token_is_rejected(client):
    response = await client.post("/api/auth/verify-email", json={"token": "not-a-real-token"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_me_reports_verification_state(client):
    account = await register_and_verify(client)
    body = (await client.get("/api/auth/me", headers=account["headers"])).json()

    assert body["email_verified"] is True
    assert body["status"] == "onboarding"
    assert body["age"] >= 18


@pytest.mark.asyncio
async def test_duplicate_email_is_rejected_regardless_of_case(client):
    await register(client)
    response = await client.post(
        "/api/auth/register",
        json={
            "email": f"ADITI@{CAMPUS_DOMAIN.upper()}",
            "password": "another-valid-password",
            "display_name": "Impostor",
            "birthdate": "2003-05-17",
        },
    )
    assert response.status_code == 409
