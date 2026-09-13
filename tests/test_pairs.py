"""The pair view's HTTP surface: GET /api/pairs/next and POST .../decide.

backend/pairing.py already has 47 tests exercising the generation and
decision logic directly. These go over the same ground through the actual
API — the thin layer that matters here is authentication, request/response
shape, and the one rule the module docstring is emphatic about: round 1 must
never leak anything beyond a photo.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.affinity import apply_decision, is_unlocked
from backend.database import MediaAsset, MediaStatus, Pairing, PairRound, User, pair_key, utcnow
from tests.conftest import TEST_DOMAIN, onboard, run_jobs


async def _two_mutual_users(client, db_sessionmaker, fake_storage, *, prefix="p"):
    """Two fully onboarded, mutually visible accounts with processed photos —
    enough for generate_one_pair to actually find a match."""
    a = await onboard(
        client,
        f"{prefix}a@{TEST_DOMAIN}",
        visible_as=["woman"],
        interested_in=["man"],
        store=fake_storage,
    )
    b = await onboard(
        client,
        f"{prefix}b@{TEST_DOMAIN}",
        visible_as=["woman"],
        interested_in=["man"],
        store=fake_storage,
    )
    viewer = await onboard(
        client,
        f"{prefix}v@{TEST_DOMAIN}",
        visible_as=["man"],
        interested_in=["woman"],
        store=fake_storage,
    )
    await run_jobs(db_sessionmaker)
    return a, b, viewer


@pytest.mark.asyncio
async def test_next_requires_authentication(client):
    response = await client.get("/api/pairs/next")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_next_is_null_when_nobody_is_eligible(client, verified, fake_storage):
    response = await client.get("/api/pairs/next", headers=verified["headers"])
    assert response.status_code == 200
    assert response.json() == {"pair": None}


@pytest.mark.asyncio
async def test_next_serves_a_round_one_pair_with_photos_only(client, db_sessionmaker, fake_storage):
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)

    response = await client.get("/api/pairs/next", headers=viewer["headers"])
    assert response.status_code == 200

    pair = response.json()["pair"]
    assert pair is not None
    assert pair["round"] == "round_1"
    assert len(pair["subjects"]) == 2
    # The viewer's own interested_in choice, reflected back so the heading can
    # name what it is showing. Says nothing about either subject.
    assert pair["segment"] == "woman"

    for subject in pair["subjects"]:
        # The invariant the module docstring is emphatic about: round 1 is a
        # photo and nothing else.
        assert set(subject.keys()) == {"id", "photo_url"}
        assert subject["photo_url"], "a processed primary photo must be present"


@pytest.mark.asyncio
async def test_next_is_idempotent_before_a_decision(client, db_sessionmaker, fake_storage):
    """Calling next twice without deciding must resume the same pairing, not
    hand out a second one — a page reload must not be a way to dodge
    answering, and must not orphan the first pairing."""
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)

    first = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    second = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]

    assert first["id"] == second["id"]
    assert first["subjects"] == second["subjects"]


@pytest.mark.asyncio
async def test_decide_requires_authentication(client):
    response = await client.post("/api/pairs/some-id/decide", json={"chosen_id": "x"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_decide_accepts_a_valid_choice_and_schedules_round_two(client, db_sessionmaker, fake_storage):
    a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)

    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    chosen_id = pair["subjects"][0]["id"]

    response = await client.post(
        f"/api/pairs/{pair['id']}/decide", headers=viewer["headers"], json={"chosen_id": chosen_id}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "decided"
    assert body["round"] == "round_1"
    assert body["round_two_scheduled"] is True

    async with db_sessionmaker() as db:
        round_two = (
            (await db.execute(select(Pairing).where(Pairing.round == PairRound.round_2))).scalars().all()
        )
    assert len(round_two) == 1


@pytest.mark.asyncio
async def test_decide_rejects_a_choice_outside_the_pair(client, db_sessionmaker, fake_storage):
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]

    response = await client.post(
        f"/api/pairs/{pair['id']}/decide",
        headers=viewer["headers"],
        json={"chosen_id": "not-either-subject"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_decide_rejects_someone_elses_pairing(client, db_sessionmaker, fake_storage):
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage, prefix="q")
    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]

    intruder = await onboard(
        client,
        f"intruder@{TEST_DOMAIN}",
        visible_as=["man"],
        interested_in=["woman"],
        store=fake_storage,
    )

    response = await client.post(
        f"/api/pairs/{pair['id']}/decide",
        headers=intruder["headers"],
        json={"chosen_id": pair["subjects"][0]["id"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_decide_rejects_an_unknown_pairing(client, verified, fake_storage):
    response = await client.post(
        "/api/pairs/does-not-exist/decide", headers=verified["headers"], json={"chosen_id": "x"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deciding_twice_is_rejected(client, db_sessionmaker, fake_storage):
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    chosen_id = pair["subjects"][0]["id"]

    first = await client.post(
        f"/api/pairs/{pair['id']}/decide", headers=viewer["headers"], json={"chosen_id": chosen_id}
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/pairs/{pair['id']}/decide", headers=viewer["headers"], json={"chosen_id": chosen_id}
    )
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_round_two_reveals_the_full_profile(client, db_sessionmaker, fake_storage):
    """Once a round-2 pairing is due, its payload must carry everything a
    round-1 pairing deliberately withheld."""
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)

    # `onboard` doesn't return the raw user id directly — read it the same way
    # a real client would, from the round-1 payload itself.
    round_one = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    subject_a_id, subject_b_id = round_one["subjects"][0]["id"], round_one["subjects"][1]["id"]

    # Decide round 1 (which schedules round 2), then pull the due time forward
    # to "now" so it promotes on the next call instead of waiting 48h.
    await client.post(
        f"/api/pairs/{round_one['id']}/decide",
        headers=viewer["headers"],
        json={"chosen_id": subject_a_id},
    )

    async with db_sessionmaker() as db:
        r2 = (await db.execute(select(Pairing).where(Pairing.round == PairRound.round_2))).scalars().one()
        r2.due_at = utcnow() - timedelta(minutes=1)
        await db.commit()

    response = await client.get("/api/pairs/next", headers=viewer["headers"])
    pair = response.json()["pair"]

    assert pair["round"] == "round_2"
    subjects_by_id = {s["id"]: s for s in pair["subjects"]}
    assert set(subjects_by_id) == {subject_a_id, subject_b_id}

    revealed = subjects_by_id[subject_a_id]
    assert revealed["display_name"]
    assert revealed["age"] is not None
    assert len(revealed["photos"]) >= 1
    # Each photo and each answer carries its own id, so a viewer can report
    # *that one* rather than filing a complaint a reviewer cannot act on.
    assert all(p["id"] and p["url"] for p in revealed["photos"])
    assert len(revealed["prompts"]) == 4
    assert all(p["id"] for p in revealed["prompts"])
    written = [p for p in revealed["prompts"] if p["kind"] == "written"]
    voice = [p for p in revealed["prompts"] if p["kind"] == "voice"]
    assert len(written) == 3
    assert all(p["body"] for p in written)
    assert len(voice) == 1
    assert voice[0]["audio_url"], "a processed voice asset must resolve to a url"


@pytest.mark.asyncio
async def test_round_two_omits_audio_url_for_an_unprocessed_recording(client, db_sessionmaker, fake_storage):
    """The gate hasn't run yet (or failed) — the reveal must not surface a
    clip that cannot actually be played, or crash trying."""
    a, b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)

    round_one = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    subject_a_id = round_one["subjects"][0]["id"]

    async with db_sessionmaker() as db:
        # Roll the winner's voice asset back to "uploaded" — as if the worker
        # had not processed it yet.
        asset = (
            (
                await db.execute(
                    select(MediaAsset)
                    .where(MediaAsset.user_id == subject_a_id)
                    .where(MediaAsset.kind == "voice")
                )
            )
            .scalars()
            .first()
        )
        asset.status = MediaStatus.uploaded
        await db.commit()

    await client.post(
        f"/api/pairs/{round_one['id']}/decide",
        headers=viewer["headers"],
        json={"chosen_id": subject_a_id},
    )
    async with db_sessionmaker() as db:
        r2 = (await db.execute(select(Pairing).where(Pairing.round == PairRound.round_2))).scalars().one()
        r2.due_at = utcnow() - timedelta(minutes=1)
        await db.commit()

    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    revealed = next(s for s in pair["subjects"] if s["id"] == subject_a_id)
    voice_prompt = next(p for p in revealed["prompts"] if p["kind"] == "voice")

    assert voice_prompt["audio_url"] is None


@pytest.mark.asyncio
async def test_pair_key_prevents_repeating_the_same_pair(client, db_sessionmaker, fake_storage):
    """A directly-inserted, already-seen pairing for round 1 must stop that
    exact pair from being generated again for this viewer."""
    a, b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)

    round_one = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    subject_ids = {s["id"] for s in round_one["subjects"]}

    async with db_sessionmaker() as db:
        seen = (
            (await db.execute(select(Pairing.pair_key).where(Pairing.round == PairRound.round_1)))
            .scalars()
            .all()
        )
    assert pair_key(*subject_ids) in seen


# ---------------------------------------------------------------------------
# The unlock, over HTTP.
# ---------------------------------------------------------------------------


async def _user_id(db_sessionmaker, email):
    async with db_sessionmaker() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one().id


async def _prime_affinity(db_sessionmaker, viewer_id, chosen_id, rejected_id, times):
    """Put a viewer most of the way to an unlock without seven real pairs.

    The test scope caps each segment at two people, so there is exactly one
    pair that can ever be built here and it may only be shown once per round.
    Driving the record directly is the honest way to reach the boundary; the
    decision that actually crosses it still goes through the API.
    """
    for _ in range(times):
        async with db_sessionmaker() as db:
            await apply_decision(db, viewer_id=viewer_id, chosen_id=chosen_id, rejected_id=rejected_id)
            await db.commit()


@pytest.mark.asyncio
async def test_an_ordinary_decision_unlocks_nothing(client, db_sessionmaker, fake_storage):
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]

    response = await client.post(
        f"/api/pairs/{pair['id']}/decide",
        headers=viewer["headers"],
        json={"chosen_id": pair["subjects"][0]["id"]},
    )
    assert response.status_code == 200
    assert response.json()["unlocked"] is None


@pytest.mark.asyncio
async def test_the_deciding_choice_returns_the_full_profile(client, db_sessionmaker, fake_storage):
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    viewer_id = await _user_id(db_sessionmaker, f"pv@{TEST_DOMAIN}")

    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    chosen, rejected = (s["id"] for s in pair["subjects"])
    await _prime_affinity(db_sessionmaker, viewer_id, chosen, rejected, times=6)

    body = (
        await client.post(
            f"/api/pairs/{pair['id']}/decide",
            headers=viewer["headers"],
            json={"chosen_id": chosen},
        )
    ).json()

    unlocked = body["unlocked"]
    assert unlocked is not None
    assert unlocked["id"] == chosen
    # A reveal, not a notification: everything round 2 would have shown.
    assert unlocked["display_name"]
    assert unlocked["age"]
    assert unlocked["photos"]
    assert unlocked["prompts"]


@pytest.mark.asyncio
async def test_an_unlock_never_carries_a_rating_or_an_address(client, db_sessionmaker, fake_storage):
    """The line the whole design rests on: a viewer learns that someone opened
    up, never how anyone is scored, and never anything private."""
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    viewer_id = await _user_id(db_sessionmaker, f"pv@{TEST_DOMAIN}")

    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    chosen, rejected = (s["id"] for s in pair["subjects"])
    await _prime_affinity(db_sessionmaker, viewer_id, chosen, rejected, times=6)

    body = (
        await client.post(
            f"/api/pairs/{pair['id']}/decide",
            headers=viewer["headers"],
            json={"chosen_id": chosen},
        )
    ).json()

    serialised = json.dumps(body)
    for forbidden in ("rating", "deviation", "confidence", "picked", "shown", "@", "birthdate", "password"):
        assert forbidden not in serialised, forbidden


@pytest.mark.asyncio
async def test_the_person_passed_over_is_not_unlocked(client, db_sessionmaker, fake_storage):
    _a, _b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    viewer_id = await _user_id(db_sessionmaker, f"pv@{TEST_DOMAIN}")

    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    chosen, rejected = (s["id"] for s in pair["subjects"])
    await _prime_affinity(db_sessionmaker, viewer_id, chosen, rejected, times=6)

    await client.post(
        f"/api/pairs/{pair['id']}/decide",
        headers=viewer["headers"],
        json={"chosen_id": chosen},
    )

    async with db_sessionmaker() as db:
        assert await is_unlocked(db, viewer_id, chosen) is True
        assert await is_unlocked(db, viewer_id, rejected) is False
