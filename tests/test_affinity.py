"""The unlock: when a run of picks becomes evidence.

Split the same way as test_pairing.py — the arithmetic that decides whether a
record has cleared the bar is tested in memory, precisely, because the exact
number of picks an unlock costs is a product decision rather than an
implementation detail. The database tests below cover accumulation, the
permanence rule, and the queries the pair loop reads back.
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.affinity import (
    MAX_TRIALS,
    UNLOCK_THRESHOLD,
    apply_decision,
    classify,
    is_unlocked,
    open_question_subject_ids,
    unlocked_subject_ids,
)
from backend.database import Affinity, AffinityState, User, utcnow

# ---------------------------------------------------------------------------
# The classifier: pure, no database, no clock.
# ---------------------------------------------------------------------------


def test_no_evidence_is_still_an_open_question():
    state, low, high = classify(0, 0)
    assert state is AffinityState.learning
    # The honest interval over no observations is the whole range.
    assert (low, high) == (0.0, 1.0)


def test_seven_consecutive_picks_is_the_fastest_possible_unlock():
    """Pinned because it is a product number, not an implementation detail.

    Nothing in the code says "seven" — it falls out of the threshold and the
    confidence level together. If either is retuned, this test should be
    updated with the new count deliberately, rather than deleted.
    """
    assert classify(6, 6)[0] is AffinityState.learning
    assert classify(7, 7)[0] is AffinityState.unlocked


def test_a_perfect_record_is_not_enough_on_its_own():
    """Three out of three is a 100% rate and means nothing."""
    for n in range(1, 7):
        assert classify(n, n)[0] is AffinityState.learning, n


def test_one_miss_costs_roughly_twice_as_long():
    assert classify(9, 10)[0] is AffinityState.learning
    assert classify(12, 13)[0] is AffinityState.unlocked


def test_a_clear_no_settles_quickly():
    """One pick in six: the optimistic end is already under the bar, so
    further comparisons would only confirm it."""
    assert classify(1, 6)[0] is AffinityState.settled


def test_a_coin_flip_settles_rather_than_asking_forever():
    assert classify(7, 14)[0] is AffinityState.learning
    assert classify(7, 15)[0] is AffinityState.settled


def test_max_trials_is_a_backstop_for_a_rate_that_hovers():
    """A record sitting just under the threshold would otherwise stay open a
    long time — the interval narrows, but from both sides at once."""
    picked = int(MAX_TRIALS * 0.66)
    state, low, high = classify(picked, MAX_TRIALS)
    assert low < UNLOCK_THRESHOLD < high  # genuinely undecided on the maths
    assert state is AffinityState.settled  # retired anyway


def test_the_bounds_bracket_the_raw_rate():
    """The lower bound never flatters a small sample, which is the whole
    point of using it. The upper bound is allowed to reach 1.0 — after an
    unbroken run, 'always' really is still consistent with the evidence."""
    for picked, shown in [(1, 1), (4, 5), (9, 10), (7, 15), (90, 100)]:
        low, high = classify(picked, shown)[1:]
        assert low < picked / shown <= high


# ---------------------------------------------------------------------------
# Accumulation against a database.
# ---------------------------------------------------------------------------


async def _seed_user(db_sessionmaker, scope_id, email):
    async with db_sessionmaker() as db:
        user = User(
            email=email,
            password_hash="x",
            display_name=email.split("@")[0],
            birthdate=date(2003, 1, 1),
            scope_id=scope_id,
            email_verified_at=utcnow(),
        )
        db.add(user)
        await db.commit()
        return user.id


@pytest_asyncio.fixture
async def three(db_sessionmaker, scope):
    """A viewer and two people to choose between."""
    return [
        await _seed_user(db_sessionmaker, scope.id, f"{name}@campus.edu")
        for name in ("viewer", "liked", "passed")
    ]


async def _decide(db_sessionmaker, viewer, chosen, rejected, times=1):
    """Run the same decision several times over, each in its own session —
    the way separate HTTP requests would."""
    unlocked = None
    for _ in range(times):
        async with db_sessionmaker() as db:
            result = await apply_decision(db, viewer_id=viewer, chosen_id=chosen, rejected_id=rejected)
            await db.commit()
            unlocked = unlocked or result
    return unlocked


async def _row(db_sessionmaker, viewer, subject) -> Affinity:
    async with db_sessionmaker() as db:
        return (
            await db.execute(
                select(Affinity).where(Affinity.viewer_id == viewer).where(Affinity.subject_id == subject)
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_one_decision_records_both_people(db_sessionmaker, three):
    viewer, liked, passed = three
    await _decide(db_sessionmaker, viewer, liked, passed)

    chosen = await _row(db_sessionmaker, viewer, liked)
    rejected = await _row(db_sessionmaker, viewer, passed)

    assert (chosen.shown, chosen.picked) == (1, 1)
    # The person who lost is evidence too — a record of six appearances and no
    # picks is what lets the system stop asking about them.
    assert (rejected.shown, rejected.picked) == (1, 0)


@pytest.mark.asyncio
async def test_the_seventh_pick_unlocks_and_the_sixth_does_not(db_sessionmaker, three):
    viewer, liked, passed = three

    assert await _decide(db_sessionmaker, viewer, liked, passed, times=6) is None
    async with db_sessionmaker() as db:
        assert await is_unlocked(db, viewer, liked) is False

    unlocked = await _decide(db_sessionmaker, viewer, liked, passed)
    assert unlocked is not None
    assert unlocked.subject_id == liked
    assert unlocked.unlocked_at is not None


@pytest.mark.asyncio
async def test_the_loser_of_a_decision_never_unlocks(db_sessionmaker, three):
    viewer, liked, passed = three
    await _decide(db_sessionmaker, viewer, liked, passed, times=10)

    rejected = await _row(db_sessionmaker, viewer, passed)
    assert rejected.state != AffinityState.unlocked
    assert rejected.unlocked_at is None


@pytest.mark.asyncio
async def test_an_unlock_is_permanent(db_sessionmaker, three):
    """Later evidence keeps accruing but never revokes what was granted —
    taking a revealed profile back would be worse than the occasional unlock
    that fresh evidence would not have repeated."""
    viewer, liked, passed = three
    await _decide(db_sessionmaker, viewer, liked, passed, times=7)
    granted_at = (await _row(db_sessionmaker, viewer, liked)).unlocked_at

    # Now pass over them twenty times running.
    await _decide(db_sessionmaker, viewer, passed, liked, times=20)

    row = await _row(db_sessionmaker, viewer, liked)
    assert row.state == AffinityState.unlocked
    assert row.unlocked_at == granted_at  # and the moment itself does not move
    assert row.shown == 27  # while the counts stay honest
    assert row.confidence_low < UNLOCK_THRESHOLD


@pytest.mark.asyncio
async def test_unlocked_subject_ids_lists_only_the_unlocked(db_sessionmaker, three):
    viewer, liked, passed = three
    await _decide(db_sessionmaker, viewer, liked, passed, times=7)

    async with db_sessionmaker() as db:
        assert await unlocked_subject_ids(db, viewer) == [liked]
        assert await is_unlocked(db, viewer, liked) is True
        assert await is_unlocked(db, viewer, passed) is False
        # Someone with no record at all is not unlocked, and does not raise.
        assert await is_unlocked(db, viewer, "nobody") is False


@pytest.mark.asyncio
async def test_open_questions_drive_re_exposure(db_sessionmaker, three):
    viewer, liked, passed = three
    await _decide(db_sessionmaker, viewer, liked, passed, times=2)

    async with db_sessionmaker() as db:
        # Picked twice, nowhere near resolved: a live hypothesis.
        assert await open_question_subject_ids(db, viewer) == [liked]


@pytest.mark.asyncio
async def test_a_resolved_question_stops_being_asked(db_sessionmaker, three):
    """Both exits from `learning` remove someone from re-exposure. There is no
    point spending comparisons on a question already answered, in either
    direction."""
    viewer, liked, passed = three
    await _decide(db_sessionmaker, viewer, liked, passed, times=7)

    async with db_sessionmaker() as db:
        assert await open_question_subject_ids(db, viewer) == []

    assert (await _row(db_sessionmaker, viewer, liked)).state == AffinityState.unlocked
    assert (await _row(db_sessionmaker, viewer, passed)).state == AffinityState.settled


@pytest.mark.asyncio
async def test_someone_never_picked_is_not_an_open_question(db_sessionmaker, three):
    """`shown` without `picked` is evidence, but it is not a hypothesis — the
    re-exposure draw must not spend half a viewer's pairs re-asking about
    people they have consistently passed over."""
    viewer, liked, passed = three
    await _decide(db_sessionmaker, viewer, liked, passed, times=2)

    async with db_sessionmaker() as db:
        assert passed not in await open_question_subject_ids(db, viewer)
