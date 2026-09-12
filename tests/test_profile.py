"""Profile, identity and prompts."""

import pytest

from tests.conftest import CAMPUS_DOMAIN, complete_profile, register_and_verify


@pytest.mark.asyncio
async def test_options_returns_everything_onboarding_needs(client, fake_storage):
    body = (await client.get("/api/profile/options")).json()

    assert len(body["gender_identities"]) >= 30
    assert len(body["sexualities"]) >= 25
    assert len(body["prompts"]) == 81
    assert body["slots"] == {"written": 3, "voice": 1, "voice_max_seconds": 15}
    assert "dating_intentions" in body["fields"]


@pytest.mark.asyncio
async def test_gender_list_is_open_and_not_binary(client, fake_storage):
    labels = {g["id"] for g in (await client.get("/api/profile/options")).json()["gender_identities"]}

    assert {"man", "woman", "nonbinary"} <= labels
    assert {"genderqueer", "agender", "trans_man", "trans_woman"} <= labels
    # Included deliberately for an Indian campus.
    assert {"hijra", "kinnar"} <= labels


@pytest.mark.asyncio
async def test_identity_is_separate_from_matching_fields(client, verified, fake_storage):
    """A non-binary identity can still choose to appear in men's and women's searches."""
    response = await client.patch(
        "/api/profile",
        headers=verified["headers"],
        json={
            "gender_identity_id": "genderqueer",
            "pronouns": "they/them",
            "visible_as": ["nonbinary", "woman"],
            "interested_in": ["man", "nonbinary"],
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["gender_identity_id"] == "genderqueer"
    assert body["pronouns"] == "they/them"
    assert body["visible_as"] == ["nonbinary", "woman"]  # primary first
    assert body["interested_in"] == ["man", "nonbinary"]


@pytest.mark.asyncio
async def test_custom_gender_text_is_kept_alongside_the_structured_value(client, verified, fake_storage):
    response = await client.patch(
        "/api/profile",
        headers=verified["headers"],
        json={"gender_identity_id": "not_listed", "custom_gender": "Agender femme"},
    )
    assert response.status_code == 200
    assert response.json()["custom_gender"] == "Agender femme"


@pytest.mark.asyncio
async def test_unknown_option_values_are_rejected(client, verified, fake_storage):
    response = await client.patch(
        "/api/profile", headers=verified["headers"], json={"dating_intentions": "situationship"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_segment_is_rejected(client, verified, fake_storage):
    response = await client.patch(
        "/api/profile", headers=verified["headers"], json={"interested_in": ["everyone"]}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_empty_selection_is_rejected(client, verified, fake_storage):
    response = await client.patch("/api/profile", headers=verified["headers"], json={"visible_as": []})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_field_is_rejected(client, verified, fake_storage):
    response = await client.patch(
        "/api/profile", headers=verified["headers"], json={"favourite_colour": "red"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_implausible_height_is_rejected(client, verified, fake_storage):
    response = await client.patch("/api/profile", headers=verified["headers"], json={"height_cm": 400})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_is_partial(client, verified, fake_storage):
    await client.patch("/api/profile", headers=verified["headers"], json={"height_cm": 170})
    await client.patch("/api/profile", headers=verified["headers"], json={"religion": "hindu"})

    body = (await client.get("/api/profile", headers=verified["headers"])).json()
    assert body["height_cm"] == 170
    assert body["religion"] == "hindu"


@pytest.mark.asyncio
async def test_prompt_answers_are_stored(client, verified, fake_storage):
    await complete_profile(
        client, verified["headers"], visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    body = (await client.get("/api/profile", headers=verified["headers"])).json()

    assert len(body["prompts"]) == 4
    assert sum(1 for p in body["prompts"] if p["kind"] == "written") == 3
    assert sum(1 for p in body["prompts"] if p["kind"] == "voice") == 1


@pytest.mark.asyncio
async def test_written_prompt_needs_a_body(client, verified, fake_storage):
    response = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={"answers": [{"prompt_id": "life_goal", "slot": 1, "kind": "written"}]},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_voice_prompt_needs_a_recording(client, verified, fake_storage):
    response = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={"answers": [{"prompt_id": "guess_the_song", "slot": 1, "kind": "voice"}]},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_voice_prompt_over_fifteen_seconds_is_rejected(client, verified, fake_storage):
    response = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={
            "answers": [
                {
                    "prompt_id": "guess_the_song",
                    "slot": 1,
                    "kind": "voice",
                    "audio_key": "a.webm",
                    "audio_duration_ms": 20000,
                }
            ]
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_more_than_three_written_prompts_is_rejected(client, verified, fake_storage):
    answers = [
        {"prompt_id": pid, "slot": i + 1, "kind": "written", "body": "Something."}
        for i, pid in enumerate(["life_goal", "simple_pleasures", "greatest_strength", "unusual_skills"])
    ]
    response = await client.put(
        "/api/profile/prompts", headers=verified["headers"], json={"answers": answers}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_voice_prompt_rejects_an_audio_key_that_is_not_a_real_asset(client, verified, fake_storage):
    response = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={
            "answers": [
                {
                    "prompt_id": "guess_the_song",
                    "slot": 1,
                    "kind": "voice",
                    "audio_key": "not-a-real-asset-id",
                    "audio_duration_ms": 5000,
                }
            ]
        },
    )
    assert response.status_code == 400
    assert "recording" in response.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_voice_prompt_rejects_someone_elses_asset(client, verified, fake_storage):
    """audio_key names a MediaAsset id — it must belong to the caller, not
    just exist."""
    from tests.conftest import CAMPUS_DOMAIN, register_and_verify, upload_media

    owner = await register_and_verify(client, f"owner@{CAMPUS_DOMAIN}")
    stolen_asset_id = await upload_media(
        client, owner["headers"], fake_storage, kind="voice", content_type="audio/webm"
    )

    response = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={
            "answers": [
                {
                    "prompt_id": "guess_the_song",
                    "slot": 1,
                    "kind": "voice",
                    "audio_key": stolen_asset_id,
                    "audio_duration_ms": 5000,
                }
            ]
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_voice_prompt_rejects_a_photo_asset_used_as_audio(client, verified, fake_storage):
    """The id has to name a voice asset specifically, not merely something
    this caller owns."""
    from tests.conftest import upload_media

    photo_asset_id = await upload_media(client, verified["headers"], fake_storage)  # kind="photo"

    response = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={
            "answers": [
                {
                    "prompt_id": "guess_the_song",
                    "slot": 1,
                    "kind": "voice",
                    "audio_key": photo_asset_id,
                    "audio_duration_ms": 5000,
                }
            ]
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_unknown_prompt_id_is_rejected(client, verified, fake_storage):
    response = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={"answers": [{"prompt_id": "no_such_prompt", "slot": 1, "kind": "written", "body": "Hi"}]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reanswering_a_slot_keeps_the_same_row(client, verified, fake_storage):
    """Prompt ids must survive an edit — the pairwise loop hangs statistics off them."""
    first = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={"answers": [{"prompt_id": "life_goal", "slot": 1, "kind": "written", "body": "One"}]},
    )
    assert first.status_code == 200

    second = await client.put(
        "/api/profile/prompts",
        headers=verified["headers"],
        json={"answers": [{"prompt_id": "life_goal", "slot": 1, "kind": "written", "body": "Two"}]},
    )
    assert second.json()["prompts"][0]["body"] == "Two"
    assert len(second.json()["prompts"]) == 1


@pytest.mark.asyncio
async def test_completeness_lists_what_is_missing(client, fake_storage):
    account = await register_and_verify(client, f"incomplete@{CAMPUS_DOMAIN}")
    body = (await client.get("/api/profile", headers=account["headers"])).json()

    assert body["completeness"]["complete"] is False
    assert "visible_as" in body["completeness"]["missing"]
    assert "dating_intentions" in body["completeness"]["missing"]


@pytest.mark.asyncio
async def test_profile_is_private_to_its_owner(client, fake_storage):
    assert (await client.get("/api/profile")).status_code == 401
