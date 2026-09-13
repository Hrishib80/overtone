"""Consent to face analysis, and erasure that actually erases.

Both are DPDP obligations, and both are the kind of thing that is easy to
implement so that it *looks* done. So the tests here are deliberately blunt:
consent is checked at the two moments a face can be read, and deletion is
checked by scanning every table in the database for any remaining mention of
the person — not by asserting that the calls we happen to remember writing
were made.

That last one is the important test in this file. It fails when somebody adds
a table holding a user id and forgets `OWNED_BY_USER`, which is exactly the
way this requirement breaks in practice.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend import privacy
from backend.auth import verify_ws_token
from backend.database import (
    Affinity,
    AffinityState,
    Base,
    Block,
    ChatMessage,
    Connection,
    ConnectionStatus,
    MediaAsset,
    MediaStatus,
    Pairing,
    PairRound,
    PairStatus,
    ProfileEmbedding,
    Rating,
    RatingKind,
    Report,
    User,
    ViewerPreference,
    pair_key,
    utcnow,
)
from tests.conftest import (
    TEST_DOMAIN,
    give_consent,
    onboard,
    run_jobs,
    upload_media,
)

PASSWORD = "a-strong-enough-password"


def _uid(account: dict) -> str:
    """The account's id, read back out of its own token."""
    return verify_ws_token(account["access_token"])


async def _request_photo(client, headers):
    return await client.post(
        "/api/media/upload-url",
        headers=headers,
        json={"kind": "photo", "content_type": "image/jpeg", "byte_size": 1000},
    )


async def _mentions(db, user_id: str) -> list[str]:
    """Every column, in every table, still carrying this id.

    Substring rather than equality, because two of the places a user id hides
    are inside composite strings — a rate-limit bucket (`action:id`) and a
    connection key (`id|id`) — and those are precisely the ones a targeted
    delete would miss.
    """
    found: set[str] = set()
    for table in Base.metadata.sorted_tables:
        for row in (await db.execute(select(table))).mappings().all():
            for column, value in row.items():
                if value is not None and user_id in str(value):
                    found.add(f"{table.name}.{column}")
    return sorted(found)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_photo_cannot_be_uploaded_before_consent(client, verified):
    refused = await _request_photo(client, verified["headers"])
    assert refused.status_code == 400
    assert refused.json()["error"]["message"]

    await give_consent(client, verified["headers"])
    assert (await _request_photo(client, verified["headers"])).status_code == 201


@pytest.mark.asyncio
async def test_a_voice_clip_needs_no_face_consent(client, verified):
    """The consent is to reading faces. Asking for it before a voice note
    would be asking for more than the thing being done needs."""
    allowed = await client.post(
        "/api/media/upload-url",
        headers=verified["headers"],
        json={"kind": "voice", "content_type": "audio/webm", "byte_size": 1000},
    )
    assert allowed.status_code == 201


@pytest.mark.asyncio
async def test_the_notice_is_shown_with_the_question(client, verified):
    """Consent to an unstated purpose is not consent. The words have to be
    available at the moment of asking, so they come from the API rather than
    living only in the frontend."""
    body = (await client.get("/api/account/consent", headers=verified["headers"])).json()

    assert body["granted"] is False
    assert body["notice_version"] == privacy.NOTICE_VERSION
    assert "face" in body["purpose"].lower()


@pytest.mark.asyncio
async def test_granting_twice_is_one_grant(client, verified, db_sessionmaker):
    await give_consent(client, verified["headers"])
    await give_consent(client, verified["headers"])

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(privacy.BiometricConsent))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_a_reworded_notice_has_to_be_agreed_to_again(client, verified, db_sessionmaker, monkeypatch):
    """Consent is to a statement, not to a checkbox. When the statement
    changes, the old answer is an answer to the old question — recorded, kept,
    and no longer sufficient on its own."""
    original = privacy.NOTICE_VERSION
    await give_consent(client, verified["headers"])
    monkeypatch.setattr(privacy, "NOTICE_VERSION", "2027-04-1")

    stale = (await client.get("/api/account/consent", headers=verified["headers"])).json()
    assert stale["granted"] is True
    assert stale["needs_restatement"] is True
    assert stale["agreed_version"] != stale["notice_version"]

    await give_consent(client, verified["headers"])
    fresh = (await client.get("/api/account/consent", headers=verified["headers"])).json()
    assert fresh["needs_restatement"] is False
    assert fresh["agreed_version"] == "2027-04-1"

    # The earlier answer survives, because which words were agreed to and when
    # is the whole record.
    async with db_sessionmaker() as db:
        versions = (await db.execute(select(privacy.BiometricConsent.notice_version))).scalars().all()
    assert sorted(versions) == sorted([original, "2027-04-1"])


@pytest.mark.asyncio
async def test_withdrawing_deletes_the_face_vector(client, db_sessionmaker, fake_storage):
    person = await onboard(
        client,
        f"withdraws@{TEST_DOMAIN}",
        visible_as=["woman"],
        interested_in=["man"],
        store=fake_storage,
    )
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        before = await db.get(ProfileEmbedding, _uid(person))
        assert before is not None and before.face_vector is not None

    gone = await client.delete("/api/account/consent", headers=person["headers"])
    assert gone.status_code == 200
    assert gone.json()["granted"] is False

    async with db_sessionmaker() as db:
        after = await db.get(ProfileEmbedding, _uid(person))
        assert after.face_vector is None
        assert after.face_source_id is None


@pytest.mark.asyncio
async def test_withdrawing_closes_the_upload_door_too(client, verified):
    await give_consent(client, verified["headers"])
    await client.delete("/api/account/consent", headers=verified["headers"])

    assert (await _request_photo(client, verified["headers"])).status_code == 400


@pytest.mark.asyncio
async def test_a_photo_waiting_in_the_queue_is_not_embedded_after_a_withdrawal(
    client, verified, db_sessionmaker, fake_storage
):
    """The gap the upload-time check cannot cover. A queue that is behind is
    exactly when somebody has time to change their mind."""
    asset_id = await upload_media(client, verified["headers"], fake_storage)
    await client.delete("/api/account/consent", headers=verified["headers"])

    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        asset = await db.get(MediaAsset, asset_id)
        assert asset.status == MediaStatus.rejected
        assert asset.gate_reason == "consent_withdrawn"

        embedding = await db.get(ProfileEmbedding, _uid(verified))
        assert embedding is None or embedding.face_vector is None


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _a_life(client, db_sessionmaker, fake_storage):
    """Two finished accounts with everything one person accumulates.

    Built by hand rather than by playing the app forward, because the point is
    coverage of every table that keys on a user — including the ones that are
    slow or awkward to reach through the product.
    """
    mine = await onboard(
        client, f"leaving@{TEST_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    theirs = await onboard(
        client, f"staying@{TEST_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    await run_jobs(db_sessionmaker)

    me, them = _uid(mine), _uid(theirs)
    async with db_sessionmaker() as db:
        connection = Connection(
            user_a_id=me,
            user_b_id=them,
            connection_key=pair_key(me, them),
            initiator_id=me,
            status=ConnectionStatus.open,
        )
        db.add(connection)
        await db.flush()
        db.add_all(
            [
                ChatMessage(connection_id=connection.id, from_user_id=me, message_text="hello"),
                ChatMessage(connection_id=connection.id, from_user_id=them, message_text="hello back"),
                Pairing(
                    viewer_id=them,
                    subject_a_id=me,
                    subject_b_id=them,
                    pair_key=pair_key(me, them),
                    segment="woman",
                    round=PairRound.round_1,
                    status=PairStatus.decided,
                    position=1,
                    chosen_id=me,
                ),
                Rating(subject_id=me, audience_segment="man", kind=RatingKind.visual),
                Affinity(viewer_id=them, subject_id=me, shown=7, picked=7, state=AffinityState.unlocked),
                Affinity(viewer_id=me, subject_id=them, shown=2, picked=1),
                ViewerPreference(viewer_id=me, segment="man", observations=4),
                Block(blocker_id=me, blocked_id="some-stranger-id"),
            ]
        )
        await db.commit()

    # Reports go through the API so that the block-by-default behaviour that
    # comes with them is real too.
    await client.post(
        "/api/safety/reports",
        headers=mine["headers"],
        json={"user_id": them, "reason": "harassment", "note": "said something awful"},
    )
    await client.post(
        "/api/safety/reports",
        headers=theirs["headers"],
        json={"user_id": me, "reason": "fake", "note": "not who they say"},
    )
    return {"mine": mine, "theirs": theirs}


@pytest.mark.asyncio
async def test_deleting_an_account_leaves_nothing_that_names_them(client, db_sessionmaker, _a_life):
    mine = _a_life["mine"]

    response = await client.post("/api/account/delete", headers=mine["headers"], json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True

    async with db_sessionmaker() as db:
        assert await _mentions(db, _uid(mine)) == []


@pytest.mark.asyncio
async def test_the_deletion_says_what_it_deleted(client, _a_life):
    """ "We deleted your data" is a claim. A count per table is a claim
    somebody can check."""
    response = await client.post(
        "/api/account/delete", headers=_a_life["mine"]["headers"], json={"password": PASSWORD}
    )
    removed = response.json()["removed"]

    assert removed["users"] == 1
    assert removed["connections"] == 1
    assert removed["chat_messages"] == 2
    assert removed["media_assets"] >= 2
    assert removed["storage_objects"] >= 2
    assert "storage_objects_failed" not in removed


@pytest.mark.asyncio
async def test_their_files_leave_storage_as_well_as_the_database(
    client, db_sessionmaker, fake_storage, _a_life
):
    """A row that no longer points at a file is not a deleted file."""
    mine = _a_life["mine"]
    async with db_sessionmaker() as db:
        keys = (
            (await db.execute(select(MediaAsset.object_key).where(MediaAsset.user_id == _uid(mine))))
            .scalars()
            .all()
        )
    assert keys and all(key in fake_storage for key in keys)

    await client.post("/api/account/delete", headers=mine["headers"], json={"password": PASSWORD})

    assert not any(key in fake_storage for key in keys)


@pytest.mark.asyncio
async def test_the_other_person_is_untouched(client, db_sessionmaker, _a_life):
    theirs = _a_life["theirs"]

    await client.post("/api/account/delete", headers=_a_life["mine"]["headers"], json={"password": PASSWORD})

    still_there = await client.get("/api/profile", headers=theirs["headers"])
    assert still_there.status_code == 200

    async with db_sessionmaker() as db:
        assert await db.get(User, _uid(theirs)) is not None
        photos = (
            (await db.execute(select(MediaAsset).where(MediaAsset.user_id == _uid(theirs)))).scalars().all()
        )
    assert photos


@pytest.mark.asyncio
async def test_a_report_they_made_outlives_them_without_their_name(client, db_sessionmaker, _a_life):
    """One dismissed report means little; four from unrelated people is the
    pattern. Losing the count because a reporter left would hide exactly the
    behaviour the count exists to show."""
    mine, theirs = _a_life["mine"], _a_life["theirs"]

    await client.post("/api/account/delete", headers=mine["headers"], json={"password": PASSWORD})

    async with db_sessionmaker() as db:
        about_them = (
            (await db.execute(select(Report).where(Report.subject_id == _uid(theirs)))).scalars().all()
        )
        assert len(about_them) == 1
        assert about_them[0].reporter_id is None
        assert about_them[0].note == "said something awful"

        # And a report about the person who left goes with them.
        assert (
            await db.execute(select(Report).where(Report.subject_id == _uid(mine)))
        ).scalars().first() is None


@pytest.mark.asyncio
async def test_a_reviewer_who_leaves_takes_their_name_off_their_decisions(client, db_sessionmaker, _a_life):
    """The decision stays and the decider goes.

    Whoever inherits the queue still needs to read what was decided and why;
    what they do not need is the name of somebody who has left.
    """
    mine, theirs = _a_life["mine"], _a_life["theirs"]

    async with db_sessionmaker() as db:
        reviewer = await db.get(User, _uid(mine))
        reviewer.is_reviewer = True
        await db.commit()

    decided = await client.post(
        f"/api/moderation/subjects/{_uid(theirs)}/decide",
        headers=mine["headers"],
        json={"action": "dismiss", "note": "Looked at it; nothing in it."},
    )
    assert decided.status_code == 200, decided.text

    await client.post("/api/account/delete", headers=mine["headers"], json={"password": PASSWORD})

    async with db_sessionmaker() as db:
        report = (await db.execute(select(Report).where(Report.subject_id == _uid(theirs)))).scalars().first()
    assert report is not None
    assert report.reviewer_id is None
    assert report.reviewer_note == "Looked at it; nothing in it."


@pytest.mark.asyncio
async def test_the_token_stops_working(client, _a_life):
    headers = _a_life["mine"]["headers"]
    await client.post("/api/account/delete", headers=headers, json={"password": PASSWORD})

    assert (await client.get("/api/profile", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_the_wrong_password_deletes_nothing(client, db_sessionmaker, _a_life):
    """The session token alone must not be enough. It is the one credential
    somebody else might be holding, and this is the only thing they could do
    with it that nobody can undo."""
    mine = _a_life["mine"]

    refused = await client.post(
        "/api/account/delete", headers=mine["headers"], json={"password": "not-the-password"}
    )
    assert refused.status_code == 403

    async with db_sessionmaker() as db:
        assert await db.get(User, _uid(mine)) is not None
    assert (await client.get("/api/profile", headers=mine["headers"])).status_code == 200


@pytest.mark.asyncio
async def test_guessing_the_password_runs_out(client, _a_life):
    headers = _a_life["mine"]["headers"]
    codes = set()
    for _ in range(12):
        response = await client.post("/api/account/delete", headers=headers, json={"password": "wrong"})
        codes.add(response.status_code)

    assert 429 in codes


@pytest.mark.asyncio
async def test_a_regrant_wins_even_when_the_clock_cannot_tell_them_apart(
    client, verified, db_sessionmaker, monkeypatch
):
    """Two grants on the same timestamp, and the newer one still has to win.

    `current_consent` orders by `granted_at`, and on Windows the system clock
    is coarser than the gap between a grant and the re-grant that follows a
    notice change — so the two landed on the *same* microsecond about one time
    in twenty. With no tiebreak the database was free to return either row,
    and when it returned the older one the app asked somebody to re-agree to a
    notice they had just agreed to, and answered "which words did they agree
    to" with the wrong version. That record is why this table exists.

    The clock is frozen here rather than raced, because a bug that shows up 5%
    of the time is one that passes CI and reaches production. This is the
    version of the test that fails every time if the fix is removed.
    """
    frozen = utcnow()
    monkeypatch.setattr(privacy, "utcnow", lambda: frozen)

    await give_consent(client, verified["headers"])
    monkeypatch.setattr(privacy, "NOTICE_VERSION", "2027-04-1")
    await give_consent(client, verified["headers"])

    async with db_sessionmaker() as db:
        rows = (
            (
                await db.execute(
                    select(privacy.BiometricConsent).where(privacy.BiometricConsent.user_id == _uid(verified))
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2, "the old grant is kept, not edited"

    # Both were written against a clock that could not separate them, so the
    # ordering has to come from somewhere other than luck.
    live = (await client.get("/api/account/consent", headers=verified["headers"])).json()
    assert live["agreed_version"] == "2027-04-1"
    assert live["needs_restatement"] is False
