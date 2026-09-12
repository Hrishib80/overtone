import pytest


@pytest.mark.asyncio
async def test_health_is_liveness_only(client):
    """Liveness must not touch the database, or a DB blip restarts every pod."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_reports_database(client):
    response = await client.get("/api/ready")
    assert response.status_code in (200, 503)
    assert "status" in response.json()


@pytest.mark.asyncio
async def test_security_headers_present(client):
    response = await client.get("/api/health")
    headers = response.headers

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in headers
    assert headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_camera_is_disabled_microphone_is_not(client):
    """Calls are gone; the voice prompt still needs a microphone."""
    policy = (await client.get("/api/health")).headers["Permissions-Policy"]
    assert "camera=()" in policy
    assert "microphone=(self)" in policy


@pytest.mark.asyncio
async def test_request_id_is_echoed_when_supplied(client):
    response = await client.get("/api/health", headers={"X-Request-ID": "trace-me-123"})
    assert response.headers["X-Request-ID"] == "trace-me-123"
