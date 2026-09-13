"""Being chosen, seen from the other side.

The app has always known who a viewer keeps picking. It never told the person
being picked, so being chosen was something that happened to you rather than
something you could act on. This is that half: the people who unlocked you,
their profiles, a share, and a way to write back.

The share is the part with a trap in it. A percentage over three viewers is
noise wearing a percent sign, so below a floor there is no number at all —
and that null is tested, because "we could not say" degrading silently into
"0%" would be the worst possible reading of it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from backend import affinity
from backend.database import Affinity, AffinityState, ConnectionStatus, utcnow
from tests.conftest import TEST_DOMAIN, onboard, run_jobs
from tests.test_privacy import _uid


async def _picks(
    db_sessionmaker, viewer_id: str, subject_id: str, *, shown: int, picked: int, unlocked: bool
):
    async with db_sessionmaker() as db:
        db.add(
            Affinity(
                viewer_id=viewer_id,
                subject_id=subject_id,
                shown=shown,
                picked=picked,
                state=AffinityState.unlocked if unlocked else AffinityState.learning,
                unlocked_at=utcnow() if unlocked else None,
            )
        )
        await db.commit()


@pytest_asyncio.fixture
async def star(client, db_sessionmaker, fake_storage):
    """One person, and a crowd who have seen them."""
    subject = await onboard(
        client, f"star@{TEST_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    viewers = []
    for i in range(12):
        viewers.append(
            await onboard(
                client,
                f"viewer{i}@{TEST_DOMAIN}",
                visible_as=["man"],
                interested_in=["woman"],
                store=fake_storage,
            )
        )
    await run_jobs(db_sessionmaker)
    return {"subject": subject, "viewers": viewers}


# ---------------------------------------------------------------------------
# Who is shown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_people_who_keep_choosing_you_appear(client, db_sessionmaker, star):
    me = star["subject"]
    for viewer in star["viewers"][:3]:
        await _picks(db_sessionmaker, _uid(viewer), _uid(me), shown=7, picked=7, unlocked=True)

    inbox = (await client.get("/api/connections", headers=me["headers"])).json()
    assert len(inbox["admirers"]) == 3
    # In full, not as a name and a thumbnail: the point is being able to
    # decide about a person, which needs the same profile an unlock reveals.
    assert all(a["prompts"] and a["photos"] for a in inbox["admirers"])


@pytest.mark.asyncio
async def test_somebody_still_making_up_their_mind_is_not_an_admirer(client, db_sessionmaker, star):
    """`learning` is not `unlocked`. Showing a maybe as a yes would be the
    app inventing certainty nobody expressed."""
    me = star["subject"]
    await _picks(db_sessionmaker, _uid(star["viewers"][0]), _uid(me), shown=4, picked=3, unlocked=False)

    inbox = (await client.get("/api/connections", headers=me["headers"])).json()
    assert inbox["admirers"] == []
    assert inbox["reach"]["admirers"] == 0


@pytest.mark.asyncio
async def test_somebody_already_in_a_conversation_is_not_listed_twice(client, db_sessionmaker, star):
    me, them = star["subject"], star["viewers"][0]
    await _picks(db_sessionmaker, _uid(them), _uid(me), shown=7, picked=7, unlocked=True)
    await _picks(db_sessionmaker, _uid(me), _uid(them), shown=7, picked=7, unlocked=True)

    sent = await client.post(
        "/api/connections/requests",
        headers=me["headers"],
        json={"subject_id": _uid(them), "text": "hello"},
    )
    assert sent.status_code == 201

    inbox = (await client.get("/api/connections", headers=me["headers"])).json()
    assert inbox["admirers"] == []
    # Still counted in the total, though — they are an admirer, just one
    # further along than "interested".
    assert inbox["reach"]["admirers"] == 1


@pytest.mark.asyncio
async def test_a_blocked_admirer_does_not_come_back(client, db_sessionmaker, star):
    me, them = star["subject"], star["viewers"][0]
    await _picks(db_sessionmaker, _uid(them), _uid(me), shown=7, picked=7, unlocked=True)

    await client.post("/api/safety/blocks", headers=me["headers"], json={"user_id": _uid(them)})

    inbox = (await client.get("/api/connections", headers=me["headers"])).json()
    assert inbox["admirers"] == []


# ---------------------------------------------------------------------------
# The share
# ---------------------------------------------------------------------------


def test_a_share_needs_enough_behind_it():
    """One keen person out of three is "33%", which reads like it means a lot
    and means nothing."""
    assert affinity.share_of(1, 3) is None
    assert affinity.share_of(9, affinity.MIN_AUDIENCE_FOR_SHARE - 1) is None
    assert affinity.share_of(5, 10) == 50
    assert affinity.share_of(0, 20) == 0


@pytest.mark.asyncio
async def test_the_share_is_withheld_until_enough_people_have_looked(client, db_sessionmaker, star):
    me = star["subject"]
    for viewer in star["viewers"][:3]:
        await _picks(db_sessionmaker, _uid(viewer), _uid(me), shown=7, picked=7, unlocked=True)

    reach = (await client.get("/api/connections", headers=me["headers"])).json()["reach"]
    assert reach["admirers"] == 3
    assert reach["seen_by"] == 3
    assert reach["share"] is None, "three people is not a percentage"


@pytest.mark.asyncio
async def test_the_share_counts_everyone_who_looked_not_only_the_keen(client, db_sessionmaker, star):
    """The denominator is the audience, not the fan club. Dividing admirers by
    admirers is 100% for everybody, forever."""
    me = star["subject"]
    for viewer in star["viewers"][:3]:
        await _picks(db_sessionmaker, _uid(viewer), _uid(me), shown=7, picked=7, unlocked=True)
    for viewer in star["viewers"][3:]:
        await _picks(db_sessionmaker, _uid(viewer), _uid(me), shown=5, picked=1, unlocked=False)

    reach = (await client.get("/api/connections", headers=me["headers"])).json()["reach"]
    assert reach["seen_by"] == 12
    assert reach["admirers"] == 3
    assert reach["share"] == 25


@pytest.mark.asyncio
async def test_somebody_nobody_has_seen_has_no_share(client, db_sessionmaker, star):
    reach = (await client.get("/api/connections", headers=star["subject"]["headers"])).json()["reach"]
    assert reach == {"admirers": 0, "seen_by": 0, "share": None}


# ---------------------------------------------------------------------------
# Writing back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_you_can_write_to_somebody_who_chose_you(client, db_sessionmaker, star):
    """Without having unlocked them yourself. They declared first; this is the
    answer, and it does not need their permission a second time."""
    me, them = star["subject"], star["viewers"][0]
    await _picks(db_sessionmaker, _uid(them), _uid(me), shown=7, picked=7, unlocked=True)

    sent = await client.post(
        "/api/connections/requests",
        headers=me["headers"],
        json={"subject_id": _uid(them), "text": "you have good taste"},
    )
    assert sent.status_code == 201, sent.text
    # Open, not requested: nobody is waiting on an answer any more.
    assert sent.json()["status"] == ConnectionStatus.open


@pytest.mark.asyncio
async def test_a_stranger_still_cannot_be_written_to(client, db_sessionmaker, star):
    """The relaxation is exactly one step wide. Somebody nobody has chosen in
    either direction is still out of reach."""
    me, them = star["subject"], star["viewers"][0]

    refused = await client.post(
        "/api/connections/requests",
        headers=me["headers"],
        json={"subject_id": _uid(them), "text": "hello"},
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_a_blocked_admirer_cannot_be_written_to(client, db_sessionmaker, star):
    me, them = star["subject"], star["viewers"][0]
    await _picks(db_sessionmaker, _uid(them), _uid(me), shown=7, picked=7, unlocked=True)
    await client.post("/api/safety/blocks", headers=me["headers"], json={"user_id": _uid(them)})

    refused = await client.post(
        "/api/connections/requests",
        headers=me["headers"],
        json={"subject_id": _uid(them), "text": "hello"},
    )
    assert refused.status_code == 403
