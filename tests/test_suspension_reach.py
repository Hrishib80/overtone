"""Suspension removes somebody from other people's experience — all of it.

The recorded rule is plain: a suspended account keeps its own data and its own
rights, and disappears from everybody else. Pair generation honoured that and
nothing else did. A person suspended for how they treated people still sat on
other members' My type and Keep choosing you with their full profile, could
still be sent an opening message, stayed in other people's Messages where
anybody could keep writing to them, and could still hold a live socket to those
conversations.

A suspension can be lifted, so none of this deletes anything: the affinity
records and the conversations stay, and reinstating brings every surface back
exactly as it was. Several tests below check that half as well, because hiding
something is only safe if unhiding it works.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend import signaling
from backend.database import Connection, User, UserStatus
from tests.conftest import onboard
from tests.test_connections import _force_unlock, _user_id


async def _set_status(db_sessionmaker, user_id, status):
    async with db_sessionmaker() as db:
        (await db.get(User, user_id)).status = status
        await db.commit()


async def _counts(client, account):
    return (await client.get("/api/connections/counts", headers=account["headers"])).json()


async def _inbox(client, account):
    return (await client.get("/api/connections", headers=account["headers"])).json()


@pytest.fixture
def people():
    async def build(client, db_sessionmaker, fake_storage):
        ada = await onboard(client, "ada", visible_as=["woman"], interested_in=["man"], store=fake_storage)
        foil = await onboard(client, "foil", visible_as=["woman"], interested_in=["man"], store=fake_storage)
        ben = await onboard(client, "ben", visible_as=["man"], interested_in=["woman"], store=fake_storage)
        for who, name in ((ada, "ada"), (foil, "foil"), (ben, "ben")):
            who["id"] = await _user_id(db_sessionmaker, name)
        return ada, foil, ben

    return build


# ---------------------------------------------------------------------------
# My type, and Keep choosing you
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_suspended_person_leaves_my_type_and_comes_back(
    client, db_sessionmaker, fake_storage, people
):
    ada, foil, ben = await people(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])
    assert [p["id"] for p in (await _inbox(client, ada))["unlocked"]] == [ben["id"]]
    assert (await _counts(client, ada))["type"] == 1

    await _set_status(db_sessionmaker, ben["id"], UserStatus.suspended)
    assert (await _inbox(client, ada))["unlocked"] == []
    assert (await _counts(client, ada))["type"] == 0

    await _set_status(db_sessionmaker, ben["id"], UserStatus.active)
    assert [p["id"] for p in (await _inbox(client, ada))["unlocked"]] == [ben["id"]]


@pytest.mark.asyncio
async def test_a_suspended_admirer_leaves_keep_choosing_you(client, db_sessionmaker, fake_storage, people):
    """And leaves the count on the page with them. `reach.admirers` counts
    people already in a conversation, on purpose — but it must not count people
    who are hidden, or the page tells somebody "you are already talking to 1 of
    them" about a person they are not talking to at all."""
    ada, foil, ben = await people(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    before = await _inbox(client, ada)
    assert [p["id"] for p in before["admirers"]] == [ben["id"]]
    assert before["reach"]["admirers"] == 1

    await _set_status(db_sessionmaker, ben["id"], UserStatus.suspended)
    after = await _inbox(client, ada)
    assert after["admirers"] == []
    assert after["reach"]["admirers"] == 0
    assert (await _counts(client, ada))["chosen"] == 0


@pytest.mark.asyncio
async def test_a_blocked_admirer_is_not_counted_as_somebody_you_are_talking_to(
    client, db_sessionmaker, fake_storage, people
):
    """The same false sentence, reached by blocking instead of suspension."""
    ada, foil, ben = await people(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ben["id"], ada["id"], foil["id"])
    await client.post("/api/safety/blocks", headers=ada["headers"], json={"user_id": ben["id"]})

    inbox = await _inbox(client, ada)
    assert inbox["admirers"] == []
    assert inbox["reach"]["admirers"] == 0


# ---------------------------------------------------------------------------
# Reaching them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nobody_can_send_an_opening_message_to_a_suspended_account(
    client, db_sessionmaker, fake_storage, people
):
    """Refused in the words a stranger gets, so the refusal does not announce
    that somebody was suspended."""
    ada, foil, ben = await people(client, db_sessionmaker, fake_storage)
    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])
    await _set_status(db_sessionmaker, ben["id"], UserStatus.suspended)

    response = await client.post(
        "/api/connections/requests", headers=ada["headers"], json={"subject_id": ben["id"], "text": "hi"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "You can't reach that person."


async def _open_conversation(client, db_sessionmaker, ada, foil, ben):
    await _force_unlock(db_sessionmaker, ada["id"], ben["id"], foil["id"])
    request = await client.post(
        "/api/connections/requests", headers=ada["headers"], json={"subject_id": ben["id"], "text": "hi"}
    )
    connection_id = request.json()["id"]
    reply = await client.post(
        f"/api/connections/{connection_id}/messages", headers=ben["headers"], json={"text": "hello"}
    )
    assert reply.status_code == 201, reply.text
    return connection_id


@pytest.mark.asyncio
async def test_a_conversation_with_a_suspended_person_is_hidden_and_closed_then_restored(
    client, db_sessionmaker, fake_storage, people
):
    ada, foil, ben = await people(client, db_sessionmaker, fake_storage)
    connection_id = await _open_conversation(client, db_sessionmaker, ada, foil, ben)
    assert [c["id"] for c in (await _inbox(client, ada))["conversations"]] == [connection_id]

    await _set_status(db_sessionmaker, ben["id"], UserStatus.suspended)

    assert (await _inbox(client, ada))["conversations"] == []
    refused = await client.post(
        f"/api/connections/{connection_id}/messages", headers=ada["headers"], json={"text": "still there?"}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == "That conversation is closed."

    # Kept, not deleted: lifting the suspension gives the conversation back.
    async with db_sessionmaker() as db:
        assert await db.get(Connection, connection_id) is not None
    await _set_status(db_sessionmaker, ben["id"], UserStatus.active)
    assert [c["id"] for c in (await _inbox(client, ada))["conversations"]] == [connection_id]


@pytest.mark.asyncio
async def test_no_live_socket_either_way_while_suspended(
    client, db_sessionmaker, fake_storage, people, monkeypatch
):
    """Checked on connect, so a suspension cannot be sat out by a socket that
    was opened before it — the next reconnect is refused."""
    monkeypatch.setattr(signaling, "AsyncSessionLocal", db_sessionmaker)
    ada, foil, ben = await people(client, db_sessionmaker, fake_storage)
    connection_id = await _open_conversation(client, db_sessionmaker, ada, foil, ben)
    assert await signaling._is_participant(connection_id, ada["id"]) is True

    await _set_status(db_sessionmaker, ben["id"], UserStatus.suspended)
    assert await signaling._is_participant(connection_id, ada["id"]) is False
    assert await signaling._is_participant(connection_id, ben["id"]) is False

    await _set_status(db_sessionmaker, ben["id"], UserStatus.active)
    assert await signaling._is_participant(connection_id, ada["id"]) is True


@pytest.mark.asyncio
async def test_the_rows_are_untouched_by_suspension(client, db_sessionmaker, fake_storage, people):
    """Hiding is done at read time. If suspension ever starts rewriting
    connection or affinity rows, lifting it can no longer put things back."""
    ada, foil, ben = await people(client, db_sessionmaker, fake_storage)
    connection_id = await _open_conversation(client, db_sessionmaker, ada, foil, ben)
    async with db_sessionmaker() as db:
        before = (await db.get(Connection, connection_id)).status

    await _set_status(db_sessionmaker, ben["id"], UserStatus.suspended)
    await _inbox(client, ada)
    await _counts(client, ada)

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(Connection))).scalars().all()
    assert [r.status for r in rows] == [before]
