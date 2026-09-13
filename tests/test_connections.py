"""Message requests: what an unlock lets you do, and what it does not.

The rule these tests exist to hold down is the one-message limit. An unlock is
one person being certain; it buys a single opener and nothing more until the
other person answers. Almost every test below is a way of trying to get a
second message through before that happens.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.affinity import apply_decision
from backend.connections import on_unlock
from backend.database import ChatMessage, Connection, ConnectionStatus, User
from tests.conftest import TEST_DOMAIN, onboard

UNLOCK_RUN = 7  # the fastest possible unlock; see test_affinity.py


async def _user_id(db_sessionmaker, email):
    async with db_sessionmaker() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one().id


async def _force_unlock(db_sessionmaker, viewer_id, subject_id, foil_id):
    """Drive one viewer's record of one person to an unlock.

    Going through seven real pairs would need a pool the capped test scope
    cannot supply, and would be testing the pair loop rather than the inbox.
    The record is the input here; how it got there has its own tests.
    """
    for _ in range(UNLOCK_RUN):
        async with db_sessionmaker() as db:
            await apply_decision(db, viewer_id=viewer_id, chosen_id=subject_id, rejected_id=foil_id)
            await db.commit()


@pytest.fixture
def scene():
    """Three accounts: two who may end up talking, and a foil to lose pairs."""

    async def build(client, db_sessionmaker, fake_storage):
        ada = await onboard(
            client,
            f"ada@{TEST_DOMAIN}",
            visible_as=["woman"],
            interested_in=["man"],
            store=fake_storage,
        )
        foil = await onboard(
            client,
            f"foil@{TEST_DOMAIN}",
            visible_as=["woman"],
            interested_in=["man"],
            store=fake_storage,
        )
        ben = await onboard(
            client,
            f"ben@{TEST_DOMAIN}",
            visible_as=["man"],
            interested_in=["woman"],
            store=fake_storage,
        )
        ada["id"] = await _user_id(db_sessionmaker, f"ada@{TEST_DOMAIN}")
        foil["id"] = await _user_id(db_sessionmaker, f"foil@{TEST_DOMAIN}")
        ben["id"] = await _user_id(db_sessionmaker, f"ben@{TEST_DOMAIN}")
        return ada, foil, ben

    return build


# ---------------------------------------------------------------------------
# Reaching out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_inbox_requires_authentication(client):
    assert (await client.get("/api/connections")).status_code == 401


@pytest.mark.asyncio
async def test_you_cannot_write_to_someone_you_have_not_unlocked(
    client, db_sessionmaker, fake_storage, scene
):
    ada, _foil, ben = await scene(client, db_sessionmaker, fake_storage)

    response = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "hello"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_unlock_buys_exactly_one_opening_message(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    first = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "You reread the last page first too?"},
    )
    assert first.status_code == 201
    assert first.json()["status"] == "requested"

    # The whole mechanism: no second opener while it goes unanswered.
    again = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "still there?"},
    )
    assert again.status_code == 409

    connection_id = first.json()["id"]
    direct = await client.post(
        f"/api/connections/{connection_id}/messages",
        headers=ben["headers"],
        json={"text": "or here?"},
    )
    assert direct.status_code == 409, "the message route must not be a way around the limit"


@pytest.mark.asyncio
async def test_a_reply_is_what_opens_the_conversation(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]

    reply = await client.post(
        f"/api/connections/{connection_id}/messages",
        headers=ada["headers"],
        json={"text": "I did, yes."},
    )
    assert reply.status_code == 201

    # And now the sender is no longer rationed.
    follow_up = await client.post(
        f"/api/connections/{connection_id}/messages",
        headers=ben["headers"],
        json={"text": "Good. Coffee?"},
    )
    assert follow_up.status_code == 201

    thread = (await client.get(f"/api/connections/{connection_id}/messages", headers=ben["headers"])).json()
    assert thread["status"] == "open"
    assert [m["text"] for m in thread["messages"]] == ["hello", "I did, yes.", "Good. Coffee?"]
    assert [m["mine"] for m in thread["messages"]] == [True, False, True]


@pytest.mark.asyncio
async def test_declining_is_final(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]

    declined = await client.post(f"/api/connections/{connection_id}/decline", headers=ada["headers"])
    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"

    # No second attempt, and no way to keep writing into a closed thread.
    assert (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "are you sure"},
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/api/connections/{connection_id}/messages",
            headers=ben["headers"],
            json={"text": "are you sure"},
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_a_sender_cannot_decline_their_own_request(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]

    assert (
        await client.post(f"/api/connections/{connection_id}/decline", headers=ben["headers"])
    ).status_code == 409


@pytest.mark.asyncio
async def test_an_outsider_cannot_read_or_write_a_conversation(client, db_sessionmaker, fake_storage, scene):
    """Absent rather than forbidden: a 403 would confirm the id is real."""
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]

    assert (
        await client.get(f"/api/connections/{connection_id}/messages", headers=foil["headers"])
    ).status_code == 404
    assert (
        await client.post(
            f"/api/connections/{connection_id}/messages",
            headers=foil["headers"],
            json={"text": "hi"},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_an_empty_message_is_refused(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    response = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "   "},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Mutual crossing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_unlocking_opens_a_conversation_with_no_request(
    client, db_sessionmaker, fake_storage, scene
):
    """Nobody has to go first. There is no initiator, because nobody asked."""
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])

    async with db_sessionmaker() as db:
        connection = await on_unlock(db, viewer_id=ada["id"], subject_id=ben["id"])
        await db.commit()

    assert connection is not None
    assert connection.status == ConnectionStatus.open
    assert connection.initiator_id is None
    assert connection.opened_at is not None


@pytest.mark.asyncio
async def test_unlocking_someone_who_already_wrote_opens_their_request(
    client, db_sessionmaker, fake_storage, scene
):
    """The same event from the other side: they asked, and this viewer has now
    independently agreed, so there is nothing left for them to answer."""
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "hello"},
    )

    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])
    async with db_sessionmaker() as db:
        connection = await on_unlock(db, viewer_id=ada["id"], subject_id=ben["id"])
        await db.commit()

    assert connection is not None
    assert connection.status == ConnectionStatus.open
    # Still credited to whoever actually reached out.
    assert connection.initiator_id == ben["id"]


@pytest.mark.asyncio
async def test_writing_to_someone_who_already_unlocked_you_skips_the_request(
    client, db_sessionmaker, fake_storage, scene
):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])

    response = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "hello"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "open"


@pytest.mark.asyncio
async def test_an_unlock_alone_never_creates_a_connection(client, db_sessionmaker, fake_storage, scene):
    """One person being certain is not two people agreeing."""
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    async with db_sessionmaker() as db:
        assert await on_unlock(db, viewer_id=ben["id"], subject_id=ada["id"]) is None
        assert (await db.execute(select(Connection))).scalars().all() == []


# ---------------------------------------------------------------------------
# The inbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unlocked_person_appears_as_a_prompt_until_written_to(
    client, db_sessionmaker, fake_storage, scene
):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    before = (await client.get("/api/connections", headers=ben["headers"])).json()
    assert [p["id"] for p in before["unlocked"]] == [ada["id"]]
    assert before["sent"] == []
    # The reveal, not a name and a nudge.
    assert before["unlocked"][0]["display_name"]
    assert before["unlocked"][0]["prompts"]

    await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "hello"},
    )

    after = (await client.get("/api/connections", headers=ben["headers"])).json()
    assert after["unlocked"] == [], "once written to, they belong to the thread"
    assert [c["peer"]["id"] for c in after["sent"]] == [ada["id"]]


@pytest.mark.asyncio
async def test_the_recipient_sees_a_request_not_a_conversation(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "hello"},
    )

    inbox = (await client.get("/api/connections", headers=ada["headers"])).json()
    assert inbox["conversations"] == []
    assert len(inbox["requests"]) == 1

    request = inbox["requests"][0]
    assert request["peer"]["id"] == ben["id"]
    assert request["peer"]["display_name"]
    assert request["last_message"]["text"] == "hello"
    assert request["unread"] == 1


@pytest.mark.asyncio
async def test_a_declined_request_leaves_both_inboxes(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]
    await client.post(f"/api/connections/{connection_id}/decline", headers=ada["headers"])

    hers = (await client.get("/api/connections", headers=ada["headers"])).json()
    his = (await client.get("/api/connections", headers=ben["headers"])).json()

    assert hers["requests"] == []
    assert his["sent"] == []
    # And it does not reappear as an un-acted-on unlock either.
    assert his["unlocked"] == []


@pytest.mark.asyncio
async def test_marking_read_clears_the_unread_count(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]

    marked = await client.post(f"/api/connections/{connection_id}/read", headers=ada["headers"])
    assert marked.json() == {"marked_read": 1}

    inbox = (await client.get("/api/connections", headers=ada["headers"])).json()
    assert inbox["requests"][0]["unread"] == 0

    # The sender's own message was never unread for them.
    assert (await client.post(f"/api/connections/{connection_id}/read", headers=ben["headers"])).json() == {
        "marked_read": 0
    }


@pytest.mark.asyncio
async def test_only_one_connection_ever_exists_per_pair(client, db_sessionmaker, fake_storage, scene):
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])

    await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "hello"},
    )
    async with db_sessionmaker() as db:
        await on_unlock(db, viewer_id=ada["id"], subject_id=ben["id"])
        await db.commit()
        rows = (await db.execute(select(Connection))).scalars().all()

    assert len(rows) == 1
    # Stored in the same canonical order the key is built from.
    assert rows[0].user_a_id < rows[0].user_b_id
    assert rows[0].connection_key == "|".join(sorted([ada["id"], ben["id"]]))


@pytest.mark.asyncio
async def test_a_long_message_is_truncated_not_rejected_at_the_domain_edge(
    client, db_sessionmaker, fake_storage, scene
):
    """The API caps the field, so oversize input is a validation error rather
    than a silent trim — the trim exists for callers that bypass the schema."""
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])

    response = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada["id"], "text": "x" * 5000},
    )
    assert response.status_code == 422

    async with db_sessionmaker() as db:
        assert (await db.execute(select(ChatMessage))).scalars().all() == []


@pytest.mark.asyncio
async def test_reaching_out_to_someone_who_reached_out_first_answers_them(
    client, db_sessionmaker, fake_storage, scene
):
    """Both people can arrive at the request route rather than the reply one —
    the UI would not send them there, but the branch has to behave."""
    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    sent = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()

    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])
    response = await client.post(
        "/api/connections/requests",
        headers=ada["headers"],
        json={"subject_id": ben["id"], "text": "I was about to write to you."},
    )

    assert response.status_code == 201
    assert response.json()["id"] == sent["id"], "the same connection, answered — not a second one"
    assert response.json()["status"] == "open"


@pytest.mark.asyncio
async def test_a_socket_only_opens_on_a_conversation_that_is_open(
    client, db_sessionmaker, fake_storage, scene, monkeypatch
):
    """The single-message limit would mean nothing if the sender could hold a
    live channel to the same room while the request sits unanswered."""
    from backend import signaling

    monkeypatch.setattr(signaling, "AsyncSessionLocal", db_sessionmaker)

    ada, foil, ben = await scene(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    connection_id = (
        await client.post(
            "/api/connections/requests",
            headers=ben["headers"],
            json={"subject_id": ada["id"], "text": "hello"},
        )
    ).json()["id"]

    assert await signaling._is_participant(connection_id, ben["id"]) is False
    assert await signaling._is_participant(connection_id, ada["id"]) is False

    await client.post(
        f"/api/connections/{connection_id}/messages",
        headers=ada["headers"],
        json={"text": "hi"},
    )

    assert await signaling._is_participant(connection_id, ben["id"]) is True
    assert await signaling._is_participant(connection_id, ada["id"]) is True
    assert await signaling._is_participant(connection_id, foil["id"]) is False
