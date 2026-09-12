"""Rate limits.

The arithmetic is tested directly, because the interesting part is the window
edge — a limiter that is correct in the middle of a window and wrong at its
boundary is wrong exactly when someone is attacking it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.database import RateLimitWindow, utcnow
from backend.errors import RateLimited
from backend.ratelimit import (
    ALL_LIMITS,
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_ADDRESS,
    REGISTER,
    Limit,
    _window_start,
    consume,
    sweep,
)
from tests.conftest import CAMPUS_DOMAIN

TINY = Limit("test_action", 3, timedelta(minutes=10))


@pytest.mark.asyncio
async def test_the_allowance_is_spent_then_refused(db_sessionmaker):
    async with db_sessionmaker() as db:
        remaining = [await consume(db, TINY, "someone") for _ in range(3)]
        assert remaining == [2, 1, 0]

        with pytest.raises(RateLimited):
            await consume(db, TINY, "someone")


@pytest.mark.asyncio
async def test_subjects_are_counted_separately(db_sessionmaker):
    async with db_sessionmaker() as db:
        for _ in range(3):
            await consume(db, TINY, "a")
        # b has spent nothing, and must not inherit a's exhaustion.
        assert await consume(db, TINY, "b") == 2


@pytest.mark.asyncio
async def test_actions_are_counted_separately(db_sessionmaker):
    other = Limit("other_action", 3, timedelta(minutes=10))
    async with db_sessionmaker() as db:
        for _ in range(3):
            await consume(db, TINY, "a")
        assert await consume(db, other, "a") == 2


@pytest.mark.asyncio
async def test_a_refusal_says_how_long_to_wait(db_sessionmaker):
    now = utcnow()
    async with db_sessionmaker() as db:
        for _ in range(3):
            await consume(db, TINY, "a", now=now)
        with pytest.raises(RateLimited) as caught:
            await consume(db, TINY, "a", now=now)

    assert 0 < caught.value.retry_after <= TINY.window.total_seconds()


@pytest.mark.asyncio
async def test_the_allowance_comes_back_as_the_window_passes(db_sessionmaker):
    start = utcnow()
    async with db_sessionmaker() as db:
        for _ in range(3):
            await consume(db, TINY, "a", now=start)
        with pytest.raises(RateLimited):
            await consume(db, TINY, "a", now=start)

        # A full window later, the old one has aged out entirely.
        later = start + TINY.window * 2
        assert await consume(db, TINY, "a", now=later) == 2


@pytest.mark.asyncio
async def test_the_window_edge_does_not_hand_out_a_fresh_allowance(db_sessionmaker):
    """The reason this is a sliding window and not a fixed one.

    Spend everything in the last moment of a window and a fixed counter resets
    to zero a second later, allowing the whole allowance again — twice the
    limit across the boundary, at exactly the moment an attacker is pushing.

    The sliding count does not make that impossible, only small: the estimate
    assumes the previous window's uses were spread through it, so a burst
    packed into its final moment is slightly under-counted. One more request
    gets through, not another full allowance. That is the honest guarantee and
    it is what this pins.
    """
    base = _window_start(utcnow(), TINY.window)
    last_moment = base + TINY.window * 0.99

    async with db_sessionmaker() as db:
        for _ in range(TINY.allowance):
            await consume(db, TINY, "a", now=last_moment)

        just_over = base + TINY.window + timedelta(seconds=1)
        got_through = 0
        for _ in range(TINY.allowance):
            try:
                await consume(db, TINY, "a", now=just_over)
            except RateLimited:
                break
            got_through += 1

    assert got_through < TINY.allowance, "the boundary handed out a whole fresh allowance"
    assert got_through <= 1


@pytest.mark.asyncio
async def test_the_carried_count_decays_across_the_window(db_sessionmaker):
    """Partway into the next window the old spend counts for less, so the
    allowance returns gradually rather than all at once."""
    now = utcnow()
    async with db_sessionmaker() as db:
        for _ in range(3):
            await consume(db, TINY, "a", now=now)

        # 80% through the following window: only 20% of the old spend carries,
        # which is 0.6 of a use — well under the allowance of 3.
        late = now + TINY.window + TINY.window * 0.8
        assert await consume(db, TINY, "a", now=late) >= 1


@pytest.mark.asyncio
async def test_sweeping_keeps_the_window_that_is_still_being_weighted(db_sessionmaker):
    """The previous window is read to weight the current one, so a sweep that
    took it would quietly hand everyone a fresh allowance at each boundary."""
    now = utcnow()
    async with db_sessionmaker() as db:
        await consume(db, TINY, "a", now=now)
        await db.commit()

    async with db_sessionmaker() as db:
        removed = await sweep(db, now=now)
        await db.commit()
    assert removed == 0

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(RateLimitWindow))).scalars().all()
    assert len(rows) == 1

    # Far enough past the longest window that nothing can still reference it.
    longest = max(limit.window for limit in ALL_LIMITS)
    async with db_sessionmaker() as db:
        removed = await sweep(db, now=now + longest * 3)
        await db.commit()
    assert removed == 1


# ---------------------------------------------------------------------------
# The limits as configured
# ---------------------------------------------------------------------------


def test_address_keyed_limits_are_loose_enough_for_a_campus():
    """A university is a few public addresses in front of thousands of people.
    An address-keyed limit tight enough to be a real brute-force defence locks
    out everyone on wifi during the launch rush — so the tight limit is keyed
    on the account instead, and these two stay loose deliberately.
    """
    assert LOGIN_PER_ADDRESS.allowance >= 100
    assert REGISTER.allowance >= 100
    assert LOGIN_PER_ACCOUNT.allowance <= 20
    assert LOGIN_PER_ACCOUNT.allowance < LOGIN_PER_ADDRESS.allowance


@pytest.mark.asyncio
async def test_repeated_failed_logins_on_one_account_are_cut_off(client, registered):
    """The limit that actually defends a password, keyed where the attack is
    aimed rather than where it comes from."""
    email = registered["headers"] and f"aditi@{CAMPUS_DOMAIN}"

    last = None
    for _ in range(LOGIN_PER_ACCOUNT.allowance + 2):
        last = await client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})

    assert last.status_code == 429
    assert last.headers.get("Retry-After")
    assert int(last.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_the_send_request_limit_is_enforced_before_any_work_happens(
    client, db_sessionmaker, fake_storage
):
    """A refused request must not leave a connection row behind on its way to
    being refused, which is why the limit is consumed before the domain call
    rather than after it."""
    from backend.database import Connection, User
    from backend.ratelimit import SEND_REQUEST
    from backend.ratelimit import consume as consume_limit
    from tests.conftest import onboard

    await onboard(
        client, f"ada@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    ben = await onboard(
        client, f"ben@{CAMPUS_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )

    async def user_id(email):
        async with db_sessionmaker() as db:
            return (await db.execute(select(User).where(User.email == email))).scalar_one().id

    ada_id = await user_id(f"ada@{CAMPUS_DOMAIN}")
    ben_id = await user_id(f"ben@{CAMPUS_DOMAIN}")

    async with db_sessionmaker() as db:
        for _ in range(SEND_REQUEST.allowance):
            await consume_limit(db, SEND_REQUEST, ben_id)
        await db.commit()

    response = await client.post(
        "/api/connections/requests",
        headers=ben["headers"],
        json={"subject_id": ada_id, "text": "hello"},
    )
    assert response.status_code == 429

    async with db_sessionmaker() as db:
        assert (await db.execute(select(Connection))).scalars().all() == []
