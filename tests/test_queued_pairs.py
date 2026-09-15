"""A pair queued for later has to still be showable when it is served.

Round 2 is a promise made at round 1: the same two people come back, 48 to 72
hours on, with their full profiles showing. A great deal can change in that
window — the viewer can block one of them, one of them can block the viewer,
be suspended, withdraw permission to analyse their face, or stop being visible
to this audience at all. Pair *generation* already refuses every one of those.
Serving a pair that was generated earlier did not look again, so each of them
still revealed a full profile two days later: to somebody who had blocked the
person, of somebody suspended for how they treated people, of somebody who had
been told in Settings that withdrawing means "you stop appearing in pairs".

Every test here changes exactly one thing between round 1 and round 2, and the
control at the top proves the same setup *does* serve round 2 when nothing
changed — without that, "not served" would prove nothing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from backend.database import (
    Affinity,
    Pairing,
    PairRound,
    PairStatus,
    User,
    UserStatus,
    UserVisibleAs,
    utcnow,
)
from tests.test_pairs import _two_mutual_users
from tests.test_privacy import _uid


async def _round_two_due(client, db_sessionmaker, fake_storage):
    """Decide a round-1 pair and pull its round-2 return forward to now."""
    a, b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    first = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    assert {s["id"] for s in first["subjects"]} == {_uid(a), _uid(b)}
    decided = await client.post(
        f"/api/pairs/{first['id']}/decide", headers=viewer["headers"], json={"chosen_id": _uid(a)}
    )
    assert decided.status_code == 200, decided.text

    async with db_sessionmaker() as db:
        r2 = (await db.execute(select(Pairing).where(Pairing.round == PairRound.round_2))).scalars().one()
        r2.due_at = utcnow() - timedelta(minutes=1)
        await db.commit()
        r2_id = r2.id
    return a, b, viewer, r2_id


async def _status_of(db_sessionmaker, pairing_id):
    async with db_sessionmaker() as db:
        return (await db.get(Pairing, pairing_id)).status


# ---------------------------------------------------------------------------
# The control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_nothing_changed_round_two_is_served(client, db_sessionmaker, fake_storage):
    _a, _b, viewer, r2_id = await _round_two_due(client, db_sessionmaker, fake_storage)
    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    assert pair is not None and pair["id"] == r2_id


# ---------------------------------------------------------------------------
# One change each
# ---------------------------------------------------------------------------


async def _viewer_blocks_a(client, db_sessionmaker, a, b, viewer):
    response = await client.post("/api/safety/blocks", headers=viewer["headers"], json={"user_id": _uid(a)})
    assert response.status_code == 201, response.text


async def _a_blocks_viewer(client, db_sessionmaker, a, b, viewer):
    response = await client.post("/api/safety/blocks", headers=a["headers"], json={"user_id": _uid(viewer)})
    assert response.status_code == 201, response.text


async def _a_is_suspended(client, db_sessionmaker, a, b, viewer):
    async with db_sessionmaker() as db:
        (await db.get(User, _uid(a))).status = UserStatus.suspended
        await db.commit()


async def _a_withdraws_consent(client, db_sessionmaker, a, b, viewer):
    response = await client.delete("/api/account/consent", headers=a["headers"])
    assert response.status_code == 200, response.text


async def _a_stops_being_visible_to_this_audience(client, db_sessionmaker, a, b, viewer):
    async with db_sessionmaker() as db:
        await db.execute(delete(UserVisibleAs).where(UserVisibleAs.user_id == _uid(a)))
        db.add(UserVisibleAs(user_id=_uid(a), segment="man", is_primary=True))
        await db.commit()


CHANGES = [
    pytest.param(_viewer_blocks_a, id="the viewer blocked one of them"),
    pytest.param(_a_blocks_viewer, id="one of them blocked the viewer"),
    pytest.param(_a_is_suspended, id="one of them was suspended"),
    pytest.param(_a_withdraws_consent, id="one of them withdrew face analysis"),
    pytest.param(_a_stops_being_visible_to_this_audience, id="one of them left this audience"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", CHANGES)
async def test_a_due_round_two_is_withdrawn_rather_than_served(client, db_sessionmaker, fake_storage, change):
    a, b, viewer, r2_id = await _round_two_due(client, db_sessionmaker, fake_storage)
    await change(client, db_sessionmaker, a, b, viewer)

    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]

    assert pair is None or pair["id"] != r2_id, "a full profile was revealed after the change"
    # Recorded as withdrawn, not left pending to surface again on the next call
    # and not deleted — the pairings table is also the impression history.
    assert await _status_of(db_sessionmaker, r2_id) == PairStatus.withdrawn


@pytest.mark.asyncio
@pytest.mark.parametrize("change", CHANGES)
async def test_a_pair_already_on_screen_cannot_be_decided_after_the_change(
    client, db_sessionmaker, fake_storage, change
):
    """The pair was served before the change and is still open in the
    viewer's tab. Answering it would feed a rating and this viewer's record of
    somebody they can no longer be shown."""
    a, b, viewer = await _two_mutual_users(client, db_sessionmaker, fake_storage)
    pair = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    await change(client, db_sessionmaker, a, b, viewer)

    response = await client.post(
        f"/api/pairs/{pair['id']}/decide", headers=viewer["headers"], json={"chosen_id": _uid(a)}
    )

    assert response.status_code == 409, response.text
    assert await _status_of(db_sessionmaker, pair["id"]) == PairStatus.withdrawn
    async with db_sessionmaker() as db:
        query = select(Affinity).where(Affinity.viewer_id == _uid(viewer))
        records = (await db.execute(query)).scalars().all()
    assert records == [], "the decision was recorded anyway"

    # And a reload does not bring it back.
    again = (await client.get("/api/pairs/next", headers=viewer["headers"])).json()["pair"]
    assert again is None or again["id"] != pair["id"]
