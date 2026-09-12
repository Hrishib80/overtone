"""Blocking and reporting.

The property almost every test here is really checking is symmetry. A block
that only worked in the direction it was made would leave the blocked person
still seeing, and still able to reach, someone who has removed themselves —
which is the entire situation blocking exists to prevent, so it is worth
checking from both sides of every surface.
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.affinity import apply_decision
from backend.database import (
    Affinity,
    Block,
    ConnectionStatus,
    ProfileEmbedding,
    Report,
    ReportStatus,
    User,
    UserInterestedIn,
    UserStatus,
    UserVisibleAs,
    utcnow,
)
from backend.pairing import generate_one_pair
from backend.safety import blocked_ids, is_blocked
from tests.conftest import CAMPUS_DOMAIN, onboard

UNLOCK_RUN = 7


async def _user_id(db_sessionmaker, email):
    async with db_sessionmaker() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one().id


async def _force_unlock(db_sessionmaker, viewer_id, subject_id, foil_id):
    for _ in range(UNLOCK_RUN):
        async with db_sessionmaker() as db:
            await apply_decision(db, viewer_id=viewer_id, chosen_id=subject_id, rejected_id=foil_id)
            await db.commit()


@pytest_asyncio.fixture
async def people(client, db_sessionmaker, fake_storage):
    """Two who may end up talking, and a foil to lose pairs to."""
    ada = await onboard(
        client, f"ada@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    foil = await onboard(
        client, f"foil@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    ben = await onboard(
        client, f"ben@{CAMPUS_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    for who, email in ((ada, "ada"), (foil, "foil"), (ben, "ben")):
        who["id"] = await _user_id(db_sessionmaker, f"{email}@{CAMPUS_DOMAIN}")
    return ada, foil, ben


# ---------------------------------------------------------------------------
# The block itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocking_requires_authentication(client):
    assert (await client.post("/api/safety/blocks", json={"user_id": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_a_block_reads_the_same_from_both_sides(client, db_sessionmaker, people):
    ada, _foil, ben = people
    response = await client.post("/api/safety/blocks", headers=ben["headers"], json={"user_id": ada["id"]})
    assert response.status_code == 201

    async with db_sessionmaker() as db:
        assert await is_blocked(db, ben["id"], ada["id"]) is True
        assert await is_blocked(db, ada["id"], ben["id"]) is True
        assert await blocked_ids(db, ben["id"]) == {ada["id"]}
        # The person who was blocked sees it too, without being told who did it.
        assert await blocked_ids(db, ada["id"]) == {ben["id"]}


@pytest.mark.asyncio
async def test_blocking_twice_is_not_two_blocks(client, db_sessionmaker, people):
    ada, _foil, ben = people
    for _ in range(3):
        await client.post("/api/safety/blocks", headers=ben["headers"], json={"user_id": ada["id"]})

    async with db_sessionmaker() as db:
        assert len((await db.execute(select(Block))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_you_cannot_block_yourself(client, people):
    _ada, _foil, ben = people
    response = await client.post("/api/safety/blocks", headers=ben["headers"], json={"user_id": ben["id"]})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_blocking_somebody_who_does_not_exist_is_a_404(client, people):
    _ada, _foil, ben = people
    response = await client.post("/api/safety/blocks", headers=ben["headers"], json={"user_id": "nobody"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_block_list_shows_only_blocks_you_made(client, people):
    ada, _foil, ben = people
    await client.post("/api/safety/blocks", headers=ben["headers"], json={"user_id": ada["id"]})

    his = (await client.get("/api/safety/blocks", headers=ben["headers"])).json()
    hers = (await client.get("/api/safety/blocks", headers=ada["headers"])).json()

    assert [b["id"] for b in his["blocks"]] == [ada["id"]]
    # Being blocked is never disclosed: telling someone hands them a reason to
    # find another way to reach the person who blocked them.
    assert hers["blocks"] == []


@pytest.mark.asyncio
async def test_unblocking_restores_nothing_but_visibility(client, db_sessionmaker, people):
    ada, _foil, ben = people
    await client.post("/api/safety/blocks", headers=ben["headers"], json={"user_id": ada["id"]})

    response = await client.delete(f"/api/safety/blocks/{ada['id']}", headers=ben["headers"])
    assert response.json() == {"status": "unblocked"}

    async with db_sessionmaker() as db:
        assert await is_blocked(db, ben["id"], ada["id"]) is False

    # Idempotent, and honest about having done nothing.
    again = await client.delete(f"/api/safety/blocks/{ada['id']}", headers=ben["headers"])
    assert again.json() == {"status": "not_blocked"}


# ---------------------------------------------------------------------------
# What a block actually stops
# ---------------------------------------------------------------------------


async def _seed_pairable(db_sessionmaker, scope_id, email, *, visible_as, interested_in, face):
    async with db_sessionmaker() as db:
        user = User(
            email=email,
            password_hash="x",
            display_name=email.split("@")[0],
            birthdate=date(2003, 1, 1),
            scope_id=scope_id,
            status=UserStatus.active,
            cap_segment=visible_as[0],
            email_verified_at=utcnow(),
        )
        db.add(user)
        await db.flush()
        for segment in visible_as:
            db.add(UserVisibleAs(user_id=user.id, segment=segment, is_primary=True))
        for segment in interested_in:
            db.add(UserInterestedIn(user_id=user.id, segment=segment))
        db.add(ProfileEmbedding(user_id=user.id, face_vector=face))
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_a_blocked_person_is_never_paired_again(db_sessionmaker, scope):
    """The surface that matters most: blocking someone has to take them out of
    the deck, not just out of the inbox."""
    viewer = await _seed_pairable(
        db_sessionmaker,
        scope.id,
        "v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    candidates = [
        await _seed_pairable(
            db_sessionmaker,
            scope.id,
            f"c{i}@campus.edu",
            visible_as=["woman"],
            interested_in=["man"],
            face=[1.0 - i * 0.01, i * 0.01],
        )
        for i in range(4)
    ]

    async with db_sessionmaker() as db:
        db.add(Block(blocker_id=viewer, blocked_id=candidates[0]))
        db.add(Block(blocker_id=candidates[1], blocked_id=viewer))  # the other direction
        await db.commit()

    seen: set[str] = set()
    for _ in range(12):
        async with db_sessionmaker() as db:
            user = await db.get(User, viewer)
            pairing = await generate_one_pair(db, user)
            if pairing is None:
                break
            seen.update({pairing.subject_a_id, pairing.subject_b_id})
            await db.commit()

    assert candidates[0] not in seen, "someone this viewer blocked was shown to them"
    assert candidates[1] not in seen, "someone who blocked this viewer was shown to them"
    assert seen == {candidates[2], candidates[3]}


@pytest.mark.asyncio
async def test_blocking_closes_an_open_conversation(client, db_sessionmaker, people):
    ada, foil, ben = people
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]
    await client.post(
        f"/api/connections/{connection_id}/messages", headers=ada["headers"], json={"text": "hi"}
    )

    await client.post("/api/safety/blocks", headers=ada["headers"], json={"user_id": ben["id"]})

    # Neither side can keep writing into it, and neither still sees it.
    for account in (ada, ben):
        sent = await client.post(
            f"/api/connections/{connection_id}/messages",
            headers=account["headers"],
            json={"text": "still there?"},
        )
        assert sent.status_code == 409, account["headers"]

    assert (await client.get("/api/connections", headers=ben["headers"])).json()["conversations"] == []
    assert (await client.get("/api/connections", headers=ada["headers"])).json()["conversations"] == []


@pytest.mark.asyncio
async def test_a_blocked_person_cannot_start_a_conversation(client, db_sessionmaker, people):
    ada, foil, ben = people
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    await client.post("/api/safety/blocks", headers=ada["headers"], json={"user_id": ben["id"]})

    response = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "hello"},
    )
    assert response.status_code == 403
    # The same wording a stranger gets: confirming who blocked you is the
    # thing that turns a block into a provocation.
    assert "blocked" not in response.text.lower()


@pytest.mark.asyncio
async def test_blocking_forgets_what_the_pair_loop_learned(client, db_sessionmaker, people):
    """An unlocked person who is then blocked must not be sitting there
    unlocked if the block is ever lifted."""
    ada, foil, ben = people
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    async with db_sessionmaker() as db:
        assert (
            await db.execute(
                select(Affinity)
                .where(Affinity.viewer_id == ben["id"])
                .where(Affinity.subject_id == ada["id"])
            )
        ).scalar_one_or_none() is not None

    await client.post("/api/safety/blocks", headers=ben["headers"], json={"user_id": ada["id"]})

    async with db_sessionmaker() as db:
        remaining = (
            await db.execute(
                select(Affinity)
                .where(Affinity.viewer_id == ben["id"])
                .where(Affinity.subject_id == ada["id"])
            )
        ).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_a_socket_closes_to_a_blocked_peer(client, db_sessionmaker, people, monkeypatch):
    """A block closes the conversation over HTTP, but a socket already open
    outlives the request that would have noticed."""
    from backend import signaling

    monkeypatch.setattr(signaling, "AsyncSessionLocal", db_sessionmaker)

    ada, foil, ben = people
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]
    await client.post(
        f"/api/connections/{connection_id}/messages", headers=ada["headers"], json={"text": "hi"}
    )
    assert await signaling._is_participant(connection_id, ben["id"]) is True

    await client.post("/api/safety/blocks", headers=ada["headers"], json={"user_id": ben["id"]})
    assert await signaling._is_participant(connection_id, ben["id"]) is False
    assert await signaling._is_participant(connection_id, ada["id"]) is False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_report_is_recorded_and_blocks_by_default(client, db_sessionmaker, people):
    ada, _foil, ben = people
    response = await client.post(
        "/api/safety/reports",
        headers=ada["headers"],
        json={"user_id": ben["id"], "reason": "harassment", "note": "  kept sending the same thing  "},
    )
    assert response.status_code == 201
    assert response.json()["blocked"] is True

    async with db_sessionmaker() as db:
        row = (await db.execute(select(Report))).scalars().one()
        assert row.subject_id == ben["id"]
        assert row.reporter_id == ada["id"]
        assert row.reason == "harassment"
        assert row.note == "kept sending the same thing"  # trimmed
        assert row.status == ReportStatus.open
        assert await is_blocked(db, ada["id"], ben["id"]) is True


@pytest.mark.asyncio
async def test_a_report_can_decline_to_block(client, db_sessionmaker, people):
    """Reporting someone is not always wanting them gone — a report about a
    photo is not the same act as a report about a message."""
    ada, _foil, ben = people
    response = await client.post(
        "/api/safety/reports",
        headers=ada["headers"],
        json={"user_id": ben["id"], "reason": "fake", "block": False},
    )
    assert response.json()["blocked"] is False

    async with db_sessionmaker() as db:
        assert await is_blocked(db, ada["id"], ben["id"]) is False
        assert len((await db.execute(select(Report))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_an_unknown_reason_is_refused(client, people):
    ada, _foil, ben = people
    response = await client.post(
        "/api/safety/reports",
        headers=ada["headers"],
        json={"user_id": ben["id"], "reason": "vibes"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_you_cannot_report_yourself(client, people):
    ada, _foil, _ben = people
    response = await client.post(
        "/api/safety/reports",
        headers=ada["headers"],
        json={"user_id": ada["id"], "reason": "other"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_several_reports_about_one_person_all_survive(client, db_sessionmaker, people):
    """One dismissed report means little; four from unrelated people is the
    pattern that matters, so nothing here overwrites or dedupes."""
    ada, foil, ben = people
    for reporter in (ada, foil):
        await client.post(
            "/api/safety/reports",
            headers=reporter["headers"],
            json={"user_id": ben["id"], "reason": "harassment"},
        )

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(Report).where(Report.subject_id == ben["id"]))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_the_reasons_are_published_so_a_client_need_not_hardcode_them(client, verified):
    response = await client.get("/api/safety/reasons", headers=verified["headers"])
    assert response.status_code == 200
    assert "harassment" in response.json()["reasons"]


@pytest.mark.asyncio
async def test_a_report_response_says_nothing_about_the_outcome(client, people):
    """A reporter learning what happened would learn things about the other
    person they are not entitled to."""
    ada, _foil, ben = people
    body = (
        await client.post(
            "/api/safety/reports",
            headers=ada["headers"],
            json={"user_id": ben["id"], "reason": "hate"},
        )
    ).json()
    assert set(body.keys()) == {"status", "id", "blocked"}
    assert body["status"] == "received"


@pytest.mark.asyncio
async def test_a_declined_connection_is_not_reopened_by_unblocking(client, db_sessionmaker, people):
    ada, foil, ben = people
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]

    await client.post("/api/safety/blocks", headers=ada["headers"], json={"user_id": ben["id"]})
    await client.delete(f"/api/safety/blocks/{ben['id']}", headers=ada["headers"])

    sent = await client.post(
        f"/api/connections/{connection_id}/messages",
        headers=ben["headers"],
        json={"text": "back?"},
    )
    assert sent.status_code == 409
    async with db_sessionmaker() as db:
        from backend.database import Connection

        row = await db.get(Connection, connection_id)
    assert row.status == ConnectionStatus.declined
