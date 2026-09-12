import pytest


@pytest.mark.asyncio
async def test_register_returns_a_token(client):
    response = await client.post(
        "/api/auth/register",
        json={"email": "New.User@Campus.edu", "password": "correct-horse-battery", "display_name": "Ravi"},
    )
    assert response.status_code == 201
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_email_is_normalised_so_case_cannot_duplicate_an_account(client, registered):
    response = await client.post(
        "/api/auth/register",
        json={
            "email": registered["payload"]["email"].upper(),
            "password": "another-valid-password",
            "display_name": "Impostor",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_short_password_is_rejected(client):
    response = await client.post(
        "/api/auth/register",
        json={"email": "short@campus.edu", "password": "abc", "display_name": "Short"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_malformed_email_is_rejected(client):
    response = await client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "a-valid-password", "display_name": "Nope"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_succeeds_with_correct_credentials(client, registered):
    response = await client.post(
        "/api/auth/login",
        json={"email": registered["payload"]["email"], "password": registered["payload"]["password"]},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_login_fails_with_wrong_password(client, registered):
    response = await client.post(
        "/api/auth/login",
        json={"email": registered["payload"]["email"], "password": "definitely-not-it"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_for_unknown_email_matches_wrong_password_response(client, registered):
    """Identical status and body, so the endpoint cannot be used to enumerate accounts."""
    unknown = await client.post(
        "/api/auth/login",
        json={"email": "nobody@campus.edu", "password": "definitely-not-it"},
    )
    wrong = await client.post(
        "/api/auth/login",
        json={"email": registered["payload"]["email"], "password": "definitely-not-it"},
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


@pytest.mark.asyncio
async def test_me_requires_a_token(client):
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_me_rejects_a_garbage_token(client):
    response = await client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_rejects_a_non_bearer_scheme(client, registered):
    response = await client.get("/api/auth/me", headers={"Authorization": registered["token"]})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_the_profile(client, registered):
    response = await client.get("/api/auth/me", headers=registered["headers"])
    assert response.status_code == 200

    body = response.json()
    assert body["email"] == registered["payload"]["email"]
    assert body["display_name"] == "Aditi"
    assert body["prompts"] == []
    assert body["photos"] == []
    assert "password_hash" not in body
