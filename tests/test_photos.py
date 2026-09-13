"""The photo rules.

Three slots, at least one filled, and the first one is not like the other two.
It is the photo the pair view shows, so it can be swapped but never removed —
an account with no face has nothing for the whole mechanic to work on.

Most of these are about the awkward corners of that: replacing while all three
slots are full, deleting the one that happens to be primary, and a ticket that
was issued and never used.
"""

from __future__ import annotations

import pytest

from backend.media import MAX_PHOTOS, MIN_PHOTOS
from tests.conftest import PNG_BYTES, upload_media


async def _photos(client, headers):
    body = (await client.get("/api/media", headers=headers)).json()
    return [m for m in body["media"] if m["kind"] == "photo"]


async def _ticket(client, headers, *, replaces=None):
    payload = {"kind": "photo", "content_type": "image/jpeg", "byte_size": 1000}
    if replaces:
        payload["replaces"] = replaces
    return await client.post("/api/media/upload-url", headers=headers, json=payload)


async def _replace(client, headers, store, target_id):
    ticket = await _ticket(client, headers, replaces=target_id)
    assert ticket.status_code == 201, ticket.text
    body = ticket.json()
    store[body["object_key"]] = PNG_BYTES
    confirmed = await client.post(
        f"/api/media/{body['asset_id']}/confirm",
        headers=headers,
        json={"replaces": target_id},
    )
    assert confirmed.status_code == 200, confirmed.text
    return body["asset_id"]


# ---------------------------------------------------------------------------
# How many
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_photos_fit_and_a_fourth_does_not(client, verified, fake_storage):
    for _ in range(MAX_PHOTOS):
        await upload_media(client, verified["headers"], fake_storage)

    refused = await _ticket(client, verified["headers"])
    assert refused.status_code == 400
    assert "3" in refused.json()["error"]["message"]
    assert len(await _photos(client, verified["headers"])) == MAX_PHOTOS


@pytest.mark.asyncio
async def test_a_ticket_nobody_uploaded_to_does_not_eat_a_slot(client, verified, fake_storage):
    """An abandoned ticket is not a photo. Counting one would let a person who
    changed their mind twice lock themselves out of their own third slot."""
    await upload_media(client, verified["headers"], fake_storage)

    for _ in range(4):
        issued = await _ticket(client, verified["headers"])
        assert issued.status_code == 201  # ticket only; no bytes ever sent

    # Two real photos still fit.
    await upload_media(client, verified["headers"], fake_storage)
    await upload_media(client, verified["headers"], fake_storage)
    assert len(await _photos(client, verified["headers"])) == MAX_PHOTOS


# ---------------------------------------------------------------------------
# The first one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_first_photo_becomes_primary_without_waiting_for_the_worker(client, verified, fake_storage):
    """The pair view shows the primary. If nothing claimed it until a
    background job ran, a profile with one photo would show nothing."""
    await upload_media(client, verified["headers"], fake_storage)

    photos = await _photos(client, verified["headers"])
    assert [p["is_primary"] for p in photos] == [True]
    assert photos[0]["url"], "the owner should see their own photo immediately"


@pytest.mark.asyncio
async def test_the_only_photo_cannot_be_removed(client, verified, fake_storage):
    asset_id = await upload_media(client, verified["headers"], fake_storage)

    refused = await client.delete(f"/api/media/{asset_id}", headers=verified["headers"])
    assert refused.status_code == 400
    assert "replaced" in refused.json()["error"]["message"]
    assert len(await _photos(client, verified["headers"])) == MIN_PHOTOS


@pytest.mark.asyncio
async def test_the_only_photo_can_be_replaced(client, verified, fake_storage):
    """The distinction the whole rule rests on: swapping is allowed, emptying
    is not."""
    first = await upload_media(client, verified["headers"], fake_storage)
    second = await _replace(client, verified["headers"], fake_storage, first)

    photos = await _photos(client, verified["headers"])
    assert [p["id"] for p in photos] == [second]
    assert photos[0]["is_primary"] is True


@pytest.mark.asyncio
async def test_replacing_works_when_every_slot_is_full(client, verified, fake_storage):
    """Otherwise the cap makes replacement impossible at exactly the moment it
    is the only thing left to do."""
    ids = [await upload_media(client, verified["headers"], fake_storage) for _ in range(MAX_PHOTOS)]

    replacement = await _replace(client, verified["headers"], fake_storage, ids[0])

    photos = await _photos(client, verified["headers"])
    assert len(photos) == MAX_PHOTOS
    assert ids[0] not in [p["id"] for p in photos]
    assert replacement in [p["id"] for p in photos]


@pytest.mark.asyncio
async def test_a_replacement_takes_the_slot_it_replaced(client, verified, fake_storage):
    """Replacing the first photo has to make the new one first. Landing it at
    the end would leave the pair view showing the second-oldest photo."""
    ids = [await upload_media(client, verified["headers"], fake_storage) for _ in range(3)]
    primary = next(p for p in await _photos(client, verified["headers"]) if p["is_primary"])
    assert primary["id"] == ids[0]

    replacement = await _replace(client, verified["headers"], fake_storage, ids[0])

    photos = await _photos(client, verified["headers"])
    now_primary = [p["id"] for p in photos if p["is_primary"]]
    assert now_primary == [replacement]


@pytest.mark.asyncio
async def test_you_cannot_replace_somebody_elses_photo(client, db_sessionmaker, fake_storage):
    from tests.conftest import TEST_DOMAIN, register_and_verify

    mine = await register_and_verify(client, f"mine@{TEST_DOMAIN}")
    theirs = await register_and_verify(client, f"theirs@{TEST_DOMAIN}")

    their_photo = await upload_media(client, theirs["headers"], fake_storage)
    await upload_media(client, mine["headers"], fake_storage)

    refused = await _ticket(client, mine["headers"], replaces=their_photo)
    assert refused.status_code == 404


# ---------------------------------------------------------------------------
# The other two
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_later_photo_can_be_removed(client, verified, fake_storage):
    await upload_media(client, verified["headers"], fake_storage)
    second = await upload_media(client, verified["headers"], fake_storage)

    removed = await client.delete(f"/api/media/{second}", headers=verified["headers"])
    assert removed.status_code == 204
    assert len(await _photos(client, verified["headers"])) == 1


@pytest.mark.asyncio
async def test_removing_the_primary_hands_it_on(client, verified, fake_storage):
    """Somebody has to hold it, or the account keeps photos and shows none."""
    first = await upload_media(client, verified["headers"], fake_storage)
    second = await upload_media(client, verified["headers"], fake_storage)

    assert (await client.delete(f"/api/media/{first}", headers=verified["headers"])).status_code == 204

    photos = await _photos(client, verified["headers"])
    assert [p["id"] for p in photos] == [second]
    assert photos[0]["is_primary"] is True


@pytest.mark.asyncio
async def test_removing_down_to_one_then_stopping(client, verified, fake_storage):
    ids = [await upload_media(client, verified["headers"], fake_storage) for _ in range(MAX_PHOTOS)]

    assert (await client.delete(f"/api/media/{ids[2]}", headers=verified["headers"])).status_code == 204
    assert (await client.delete(f"/api/media/{ids[1]}", headers=verified["headers"])).status_code == 204

    last = await client.delete(f"/api/media/{ids[0]}", headers=verified["headers"])
    assert last.status_code == 400
    assert len(await _photos(client, verified["headers"])) == 1


@pytest.mark.asyncio
async def test_a_voice_clip_is_not_subject_to_the_photo_rules(client, verified, fake_storage):
    """The minimum is about having a face to compare, not about media in
    general — a voice prompt can be removed freely."""
    voice = await upload_media(
        client, verified["headers"], fake_storage, kind="voice", content_type="audio/webm"
    )
    removed = await client.delete(f"/api/media/{voice}", headers=verified["headers"])
    assert removed.status_code == 204
