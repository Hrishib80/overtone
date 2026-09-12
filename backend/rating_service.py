"""Database-aware wrapper over the pure Glicko-2 maths in `backend.rating`.

`backend.rating` knows nothing about SQL; this module knows nothing about the
Glicko-2 derivation. Keeping that split means the maths stays testable without
a database and this stays testable without re-deriving the maths.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import Rating as RatingRow
from backend.database import RatingKind, utcnow
from backend.logging_config import get_logger
from backend.rating import Comparison, Rating, update

log = get_logger(__name__)


def _to_dataclass(row: RatingRow) -> Rating:
    return Rating(rating=row.rating, deviation=row.deviation, volatility=row.volatility)


async def get_or_create_rating(
    db: AsyncSession, subject_id: str, segment: str, kind: RatingKind | str
) -> RatingRow:
    """Fetch a subject's rating row for this audience, creating it with
    Glicko-2 defaults if this is the first time they have needed one here.

    A person who has never been shown to a given audience simply has no rating
    there yet — this is the honest representation of that, not a backfill.
    """
    result = await db.execute(
        select(RatingRow)
        .where(RatingRow.subject_id == subject_id)
        .where(RatingRow.audience_segment == segment)
        .where(RatingRow.kind == str(kind))
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row

    row = RatingRow(subject_id=subject_id, audience_segment=segment, kind=str(kind))
    db.add(row)
    await db.flush()
    return row


async def lock_ratings_for_update(
    db: AsyncSession, subject_ids: list[str], segment: str, kind: RatingKind | str
) -> dict[str, RatingRow]:
    """Fetch and, on Postgres, row-lock the rating rows for several subjects.

    Two viewers can decide a pairing involving the same person at the same
    moment. Locking in a fixed order (ascending subject id) — never the order
    the caller happens to supply — is what keeps two such transactions from
    deadlocking on each other's rows.
    """
    ordered = sorted(set(subject_ids))
    rows: dict[str, RatingRow] = {}
    for subject_id in ordered:
        rows[subject_id] = await get_or_create_rating(db, subject_id, segment, kind)

    if settings.db_is_postgres and ordered:
        # The rows already exist after get_or_create above; lock them for the
        # remainder of this transaction in the same fixed order.
        locked = await db.execute(
            select(RatingRow)
            .where(RatingRow.subject_id.in_(ordered))
            .where(RatingRow.audience_segment == segment)
            .where(RatingRow.kind == str(kind))
            .order_by(RatingRow.subject_id)
            .with_for_update()
        )
        rows = {row.subject_id: row for row in locked.scalars().all()}

    return rows


async def apply_comparison(
    db: AsyncSession,
    *,
    winner_id: str,
    loser_id: str,
    segment: str,
    kind: RatingKind | str,
    weight: float,
) -> tuple[RatingRow, RatingRow]:
    """Apply one decided comparison to both subjects' ratings, in place.

    Both ratings are updated from the values as they stood *before* this
    comparison — Glicko-2 defines a period's comparisons against the rating at
    the start of the period, not against each other's post-update values, so
    both dataclasses are snapshotted before either row is mutated.
    """
    rows = await lock_ratings_for_update(db, [winner_id, loser_id], segment, kind)
    winner_row, loser_row = rows[winner_id], rows[loser_id]

    winner_before = _to_dataclass(winner_row)
    loser_before = _to_dataclass(loser_row)

    winner_after = update(winner_before, [Comparison(loser_before, 1.0, weight=weight)])
    loser_after = update(loser_before, [Comparison(winner_before, 0.0, weight=weight)])

    for row, result in ((winner_row, winner_after), (loser_row, loser_after)):
        row.rating = result.rating
        row.deviation = result.deviation
        row.volatility = result.volatility
        row.comparison_count += 1
        row.updated_at = utcnow()

    log.info(
        "rating_applied",
        winner_id=winner_id,
        loser_id=loser_id,
        segment=segment,
        kind=str(kind),
        weight=weight,
        winner_rating=round(winner_after.rating, 1),
        loser_rating=round(loser_after.rating, 1),
    )
    return winner_row, loser_row
