"""Upload flow and the worker pipeline.

Storage is faked at the module boundary — these tests are about our logic, not
Supabase's. The models are the deterministic stubs the registry hands out in
test mode, so the pipeline is exercised end to end without torch on disk.
"""

import pytest
from sqlalchemy import select

from backend import jobs
from backend.database import MediaAsset, MediaStatus, ProfileEmbedding
from backend.ml.base import FACE_DIM, TEXT_DIM, VOICE_DIM
from tests.conftest import TEST_DOMAIN, complete_profile, give_consent, register_and_verify, run_jobs

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload" * 8
WEBM = b"\x1aE\xdf\xa3" + b"fake-audio-payload" * 8


async def request_upload(client, headers, *, kind="photo", content_type="image/jpeg", size=1000):
    if kind == "photo":
        await give_consent(client, headers)
    return await client.post(
        "/api/media/upload-url",
        headers=headers,
        json={"kind": kind, "content_type": content_type, "byte_size": size},
    )


@pytest.mark.asyncio
async def test_upload_url_is_issued(client, verified, fake_storage):
    response = await request_upload(client, verified["headers"])
    assert response.status_code == 201

    body = response.json()
    assert body["asset_id"]
    assert body["upload_url"].startswith("https://")
    assert body["object_key"]
    assert body["expires_in"] > 0


@pytest.mark.asyncio
async def test_unsupported_content_type_is_refused(client, verified, fake_storage):
    response = await request_upload(client, verified["headers"], content_type="image/gif")
    assert response.status_code == 400
    assert "supported" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_oversized_file_is_refused_before_a_url_is_issued(client, verified, fake_storage):
    response = await request_upload(client, verified["headers"], size=50 * 1024 * 1024)
    assert response.status_code == 400
    assert "too large" in response.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_upload_requires_authentication(client, fake_storage):
    response = await client.post(
        "/api/media/upload-url",
        json={"kind": "photo", "content_type": "image/jpeg", "byte_size": 100},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_confirm_fails_when_the_bytes_never_arrived(client, verified, fake_storage):
    """A client claiming an upload it never made must not get an asset."""
    asset_id = (await request_upload(client, verified["headers"])).json()["asset_id"]

    response = await client.post(f"/api/media/{asset_id}/confirm", headers=verified["headers"], json={})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_confirm_queues_work(client, verified, fake_storage, db_sessionmaker):
    body = (await request_upload(client, verified["headers"])).json()
    fake_storage[body["object_key"]] = PNG

    response = await client.post(
        f"/api/media/{body['asset_id']}/confirm", headers=verified["headers"], json={}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "uploaded"

    async with db_sessionmaker() as db:
        queued = (await db.execute(select(jobs.Job).where(jobs.Job.kind == "process_photo"))).scalars().all()
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_confirming_twice_is_harmless(client, verified, fake_storage, db_sessionmaker):
    body = (await request_upload(client, verified["headers"])).json()
    fake_storage[body["object_key"]] = PNG

    for _ in range(2):
        response = await client.post(
            f"/api/media/{body['asset_id']}/confirm", headers=verified["headers"], json={}
        )
        assert response.status_code == 200

    async with db_sessionmaker() as db:
        media_jobs = (
            (await db.execute(select(jobs.Job).where(jobs.Job.kind == "process_photo"))).scalars().all()
        )
    assert len(media_jobs) == 1


@pytest.mark.asyncio
async def test_photo_is_gated_and_embedded(client, verified, fake_storage, db_sessionmaker):
    body = (await request_upload(client, verified["headers"])).json()
    fake_storage[body["object_key"]] = PNG
    await client.post(f"/api/media/{body['asset_id']}/confirm", headers=verified["headers"], json={})

    assert await run_jobs(db_sessionmaker) == 1

    async with db_sessionmaker() as db:
        asset = await db.get(MediaAsset, body["asset_id"])
        assert asset.status == MediaStatus.processed
        assert asset.is_primary is True
        assert asset.gate_reason is None

        embedding = await db.get(ProfileEmbedding, asset.user_id)
        assert len(embedding.face_vector) == FACE_DIM
        assert embedding.face_source_id == asset.id


@pytest.mark.asyncio
async def test_only_the_first_photo_becomes_primary(client, verified, fake_storage, db_sessionmaker):
    """The pair view shows one fixed photo, so primary must not drift."""
    ids = []
    for i in range(2):
        body = (await request_upload(client, verified["headers"])).json()
        fake_storage[body["object_key"]] = PNG + bytes([i])
        await client.post(f"/api/media/{body['asset_id']}/confirm", headers=verified["headers"], json={})
        ids.append(body["asset_id"])
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        first = await db.get(MediaAsset, ids[0])
        second = await db.get(MediaAsset, ids[1])
    assert first.is_primary is True
    assert second.is_primary is False


@pytest.mark.asyncio
async def test_voice_produces_transcript_and_two_vectors(client, verified, fake_storage, db_sessionmaker):
    """Timbre and content are separate axes — both must be populated."""
    await complete_profile(
        client,
        verified["headers"],
        visible_as=["woman"],
        interested_in=["man"],
        store=fake_storage,
    )

    body = (await request_upload(client, verified["headers"], kind="voice", content_type="audio/webm")).json()
    fake_storage[body["object_key"]] = WEBM
    await client.post(
        f"/api/media/{body['asset_id']}/confirm",
        headers=verified["headers"],
        json={"duration_ms": 9000},
    )

    # Completing the profile also queues a photo job and a text refresh, so
    # the count is not the point here — the resulting vectors are.
    assert await run_jobs(db_sessionmaker) >= 1

    async with db_sessionmaker() as db:
        asset = await db.get(MediaAsset, body["asset_id"])
        embedding = await db.get(ProfileEmbedding, asset.user_id)

    assert asset.status == MediaStatus.processed
    assert len(embedding.voice_vector) == VOICE_DIM
    assert len(embedding.text_vector) == TEXT_DIM
    assert embedding.transcript
    assert embedding.transcript_language


@pytest.mark.asyncio
async def test_status_reports_outstanding_work(client, verified, fake_storage, db_sessionmaker):
    body = (await request_upload(client, verified["headers"])).json()
    fake_storage[body["object_key"]] = PNG
    await client.post(f"/api/media/{body['asset_id']}/confirm", headers=verified["headers"], json={})

    before = (await client.get("/api/media/status", headers=verified["headers"])).json()
    assert before["pending"] == 1
    assert before["done"] is False

    await run_jobs(db_sessionmaker)

    after = (await client.get("/api/media/status", headers=verified["headers"])).json()
    assert after["pending"] == 0
    assert after["done"] is True


@pytest.mark.asyncio
async def test_media_belongs_to_its_owner(client, verified, fake_storage):
    body = (await request_upload(client, verified["headers"])).json()
    other = await register_and_verify(client, f"intruder@{TEST_DOMAIN}")

    response = await client.delete(f"/api/media/{body['asset_id']}", headers=other["headers"])
    assert response.status_code == 404

    confirm = await client.post(f"/api/media/{body['asset_id']}/confirm", headers=other["headers"], json={})
    assert confirm.status_code == 404


@pytest.mark.asyncio
async def test_text_vector_follows_the_words_not_the_audio(client, verified, fake_storage, db_sessionmaker):
    """Editing prompts must rebuild the content vector on its own.

    The clip and the written answers can arrive in either order, and a prompt
    edited a week later still has to be reflected — so the embedding cannot be
    a side effect of uploading audio.
    """
    await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={
            "answers": [
                {"prompt_id": "life_goal", "slot": 1, "kind": "written", "body": "Sail somewhere far."}
            ]
        },
    )
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        first = (await db.execute(select(ProfileEmbedding))).scalars().one()
        before = list(first.text_vector)
    assert len(before) == TEXT_DIM

    await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={
            "answers": [
                {
                    "prompt_id": "life_goal",
                    "slot": 1,
                    "kind": "written",
                    "body": "Something completely different.",
                }
            ]
        },
    )
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        after = list((await db.execute(select(ProfileEmbedding))).scalars().one().text_vector)

    assert after != before, "editing a prompt must change the content vector"
