"""The database-aware layer over Glicko-2: row creation, isolation between
audiences, and that a decided comparison lands the same way the pure function
would predict."""

import pytest
from sqlalchemy import select

from backend.database import Rating as RatingRow
from backend.database import RatingKind
from backend.rating import Comparison, Rating, update
from backend.rating_service import apply_comparison, get_or_create_rating


@pytest.mark.asyncio
async def test_a_new_rating_starts_at_glicko_defaults(db_sessionmaker):
    async with db_sessionmaker() as db:
        row = await get_or_create_rating(db, "u1", "woman", RatingKind.visual)
        await db.commit()

        assert row.rating == 1500.0
        assert row.deviation == 350.0
        assert row.volatility == 0.06
        assert row.comparison_count == 0


@pytest.mark.asyncio
async def test_fetching_twice_returns_the_same_row(db_sessionmaker):
    async with db_sessionmaker() as db:
        first = await get_or_create_rating(db, "u1", "woman", RatingKind.visual)
        await db.commit()
        first_id = first.id

    async with db_sessionmaker() as db:
        second = await get_or_create_rating(db, "u1", "woman", RatingKind.visual)
        await db.commit()

    assert second.id == first_id


@pytest.mark.asyncio
async def test_segments_are_independent(db_sessionmaker):
    """The same person rated by two different audiences must not share a row —
    a rating only means something within the population that produced it."""
    async with db_sessionmaker() as db:
        await apply_comparison(
            db, winner_id="u1", loser_id="u2", segment="woman", kind=RatingKind.visual, weight=1.0
        )
        await db.commit()

    async with db_sessionmaker() as db:
        woman_row = await get_or_create_rating(db, "u1", "woman", RatingKind.visual)
        man_row = await get_or_create_rating(db, "u1", "man", RatingKind.visual)

    assert woman_row.rating != 1500.0  # moved by the comparison above
    assert man_row.rating == 1500.0  # untouched — different audience entirely


@pytest.mark.asyncio
async def test_kinds_are_independent(db_sessionmaker):
    """Visual and profile ratings for the same person, same audience, must not
    be the same row — round 1 and round 2 measure different things."""
    async with db_sessionmaker() as db:
        await apply_comparison(
            db, winner_id="u1", loser_id="u2", segment="woman", kind=RatingKind.visual, weight=1.0
        )
        await db.commit()

    async with db_sessionmaker() as db:
        visual = await get_or_create_rating(db, "u1", "woman", RatingKind.visual)
        profile = await get_or_create_rating(db, "u1", "woman", RatingKind.profile)

    assert visual.rating != 1500.0
    assert profile.rating == 1500.0


@pytest.mark.asyncio
async def test_winner_rises_and_loser_falls(db_sessionmaker):
    async with db_sessionmaker() as db:
        winner, loser = await apply_comparison(
            db, winner_id="u1", loser_id="u2", segment="woman", kind=RatingKind.visual, weight=1.0
        )
        assert winner.rating > 1500.0
        assert loser.rating < 1500.0
        assert winner.comparison_count == 1
        assert loser.comparison_count == 1


@pytest.mark.asyncio
async def test_matches_the_pure_function_directly(db_sessionmaker):
    """The service layer must not silently diverge from the maths it wraps."""
    async with db_sessionmaker() as db:
        winner, loser = await apply_comparison(
            db, winner_id="u1", loser_id="u2", segment="woman", kind=RatingKind.visual, weight=2.5
        )

    expected_winner = update(Rating(), [Comparison(Rating(), 1.0, weight=2.5)])
    expected_loser = update(Rating(), [Comparison(Rating(), 0.0, weight=2.5)])

    assert winner.rating == pytest.approx(expected_winner.rating)
    assert winner.deviation == pytest.approx(expected_winner.deviation)
    assert loser.rating == pytest.approx(expected_loser.rating)
    assert loser.deviation == pytest.approx(expected_loser.deviation)


@pytest.mark.asyncio
async def test_a_higher_weight_moves_the_rating_further(db_sessionmaker):
    async with db_sessionmaker() as db:
        snap_winner, _ = await apply_comparison(
            db, winner_id="s1", loser_id="s2", segment="woman", kind=RatingKind.visual, weight=1.0
        )
    async with db_sessionmaker() as db:
        informed_winner, _ = await apply_comparison(
            db, winner_id="i1", loser_id="i2", segment="woman", kind=RatingKind.visual, weight=2.5
        )

    assert informed_winner.rating > snap_winner.rating


@pytest.mark.asyncio
async def test_sequential_comparisons_accumulate(db_sessionmaker):
    async with db_sessionmaker() as db:
        for opponent in ("o1", "o2", "o3"):
            await apply_comparison(
                db,
                winner_id="climber",
                loser_id=opponent,
                segment="woman",
                kind=RatingKind.visual,
                weight=1.0,
            )
        await db.commit()

        row = await get_or_create_rating(db, "climber", "woman", RatingKind.visual)
        assert row.comparison_count == 3
        assert row.rating > 1600


@pytest.mark.asyncio
async def test_only_two_rows_exist_after_a_comparison(db_sessionmaker):
    async with db_sessionmaker() as db:
        await apply_comparison(
            db, winner_id="u1", loser_id="u2", segment="woman", kind=RatingKind.visual, weight=1.0
        )
        await db.commit()
        rows = (await db.execute(select(RatingRow))).scalars().all()

    assert len(rows) == 2
    assert {r.subject_id for r in rows} == {"u1", "u2"}
