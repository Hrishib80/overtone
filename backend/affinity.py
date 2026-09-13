"""What a viewer has demonstrated about one other person, and when that
becomes enough to act on.

The pair loop produces a stream of forced choices. Each one is a Bernoulli
trial about a specific person: shown against a visually similar, similarly
rated opponent, were they chosen? Because the pairing controls for
resemblance and for rating, the null hypothesis is a coin flip — an opponent
the viewer should pick half the time. A rate meaningfully above 0.50,
sustained, is a preference rather than noise.

"Sustained" is the whole difficulty. Three picks out of three is a 100% rate
and means nothing at all. The Wilson score interval is the honest reading of a
small sample, and both of its ends are used here: the lower bound opens the
question, the upper bound closes it.

Nothing in this module is ever shown to a viewer as a number. The counts, the
bounds and the state exist to decide one thing — whether a profile is revealed
and a message may be sent — under the same rule that keeps ratings private.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import Affinity, AffinityState, utcnow
from backend.logging_config import get_logger
from backend.rating import wilson_interval

log = get_logger(__name__)

# The rate a viewer must clear to have a preference acted on. Against an
# evenly matched opponent the expected rate is 0.50, so 0.70 is already a
# strong claim — and it is the *lower bound* that has to clear it, not the
# raw rate.
UNLOCK_THRESHOLD = 0.70

# 90% confidence, not 95%. At 95% a viewer needs roughly twenty-five
# comparisons of the same person before the bound can clear 0.85; at launch
# volumes that is unreachable for almost everybody, and an unlock nobody can
# reach is the same as no unlock at all. Tune both of these on real decisions
# before launch — they are the phase 07 calibration dials.
UNLOCK_CONFIDENCE_Z = 1.645

# A backstop, not the mechanism. The upper bound normally retires a question
# on its own after around fifteen even-split comparisons; this catches the
# pathological case where a rate hovers just under the threshold forever and
# the viewer would otherwise keep being asked about the same person.
MAX_TRIALS = 25


def classify(
    picked: int,
    shown: int,
    *,
    threshold: float = UNLOCK_THRESHOLD,
    z: float = UNLOCK_CONFIDENCE_Z,
    max_trials: int = MAX_TRIALS,
) -> tuple[AffinityState, float, float]:
    """Where one viewer/subject record stands. Pure — no database, no clock.

    There is deliberately no minimum-trials constant. The interval sets its
    own floor: at these settings the fastest possible unlock is seven
    consecutive picks, and no smaller run of evidence can clear the bar no
    matter how lopsided. A hand-tuned minimum on top of that would be a second
    opinion about something the statistics already answer.
    """
    low, high = wilson_interval(picked, shown, z)
    if low >= threshold:
        return AffinityState.unlocked, low, high
    if shown > 0 and (high < threshold or shown >= max_trials):
        return AffinityState.settled, low, high
    return AffinityState.learning, low, high


async def _get_or_create(db: AsyncSession, viewer_id: str, subject_id: str) -> Affinity:
    row = (
        await db.execute(
            select(Affinity).where(Affinity.viewer_id == viewer_id).where(Affinity.subject_id == subject_id)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row

    row = Affinity(viewer_id=viewer_id, subject_id=subject_id)
    db.add(row)
    await db.flush()
    return row


async def apply_decision(
    db: AsyncSession, *, viewer_id: str, chosen_id: str, rejected_id: str
) -> Affinity | None:
    """Fold one decided pairing into both subjects' records.

    Returns the row that *newly* unlocked, if one did, so the caller can turn
    it into something the viewer actually sees. Only the chosen subject can
    ever newly unlock — a rejection raises `shown` without raising `picked`,
    which can only move the lower bound down — but the loop below does not
    assume that, because a rule that holds by arithmetic accident is not worth
    encoding twice.

    Both rounds count as one trial each. The round-2 weight lives in the
    *rating*, where it belongs: a confidence interval counts trials, and
    inflating one of them to 2.5 would make the interval a claim about a
    sample size that was never taken. What round 2 contributes here instead is
    that a viewer who reaches an unlock has, by then, usually read the person.
    """
    newly_unlocked: Affinity | None = None
    now = utcnow()

    for subject_id, was_picked in ((chosen_id, True), (rejected_id, False)):
        row = await _get_or_create(db, viewer_id, subject_id)
        row.shown += 1
        if was_picked:
            row.picked += 1
        row.last_decided_at = now

        state, low, high = classify(row.picked, row.shown)
        row.confidence_low, row.confidence_high = low, high

        # An unlock is permanent. The counts above keep accruing either way,
        # but later evidence never revokes something already granted: taking a
        # revealed profile back would be a worse experience than the
        # occasional unlock that fresh evidence would not have repeated.
        if row.state == AffinityState.unlocked:
            continue

        if state == AffinityState.unlocked:
            row.unlocked_at = now
            newly_unlocked = row
            log.info(
                "affinity_unlocked",
                viewer_id=viewer_id,
                subject_id=subject_id,
                picked=row.picked,
                shown=row.shown,
                confidence_low=round(low, 3),
            )
        row.state = state

    return newly_unlocked


async def unlocked_subject_ids(db: AsyncSession, viewer_id: str) -> list[str]:
    """Everyone this viewer has earned the full profile of, most recent first."""
    return list(
        (
            await db.execute(
                select(Affinity.subject_id)
                .where(Affinity.viewer_id == viewer_id)
                .where(Affinity.state == AffinityState.unlocked)
                .order_by(Affinity.unlocked_at.desc())
            )
        )
        .scalars()
        .all()
    )


# Below this many viewers, a share is noise wearing a percent sign. Three
# people and one of them keen is "33%", which is a sentence that means nothing
# and reads like it means a lot. It is the same objection the Wilson interval
# answers for the unlock itself; this is the blunt version of it, because the
# number here is shown to a person rather than used to decide anything.
MIN_AUDIENCE_FOR_SHARE = 10


async def admirer_ids(db: AsyncSession, subject_id: str) -> list[str]:
    """Everyone who has unlocked *this* person — the inbound direction.

    The mirror of `unlocked_subject_ids`, and worth keeping separate rather
    than adding a flag: every other caller in the app means "people I chose",
    and a boolean that silently reverses a query about who is interested in
    whom is the kind of argument that gets passed wrong once.
    """
    return list(
        (
            await db.execute(
                select(Affinity.viewer_id)
                .where(Affinity.subject_id == subject_id)
                .where(Affinity.state == AffinityState.unlocked)
                .order_by(Affinity.unlocked_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def audience(db: AsyncSession, subject_id: str) -> tuple[int, int]:
    """How many people have actually compared this person, and how many of
    them kept choosing them. `(admirers, seen_by)`.

    `seen_by` counts viewers with at least one decided comparison, not
    everyone the pair loop could theoretically show them to — a person who has
    never been put in front of anybody has an audience of nought, not a
    hundred, and the share has to say so.
    """
    seen_by = int(
        await db.scalar(
            select(func.count(Affinity.id)).where(Affinity.subject_id == subject_id).where(Affinity.shown > 0)
        )
        or 0
    )
    admirers = int(
        await db.scalar(
            select(func.count(Affinity.id))
            .where(Affinity.subject_id == subject_id)
            .where(Affinity.state == AffinityState.unlocked)
        )
        or 0
    )
    return admirers, seen_by


def share_of(admirers: int, seen_by: int) -> int | None:
    """The percentage, or None when there is not enough behind it to say."""
    if seen_by < MIN_AUDIENCE_FOR_SHARE:
        return None
    return round(admirers / seen_by * 100)


async def is_unlocked(db: AsyncSession, viewer_id: str, subject_id: str) -> bool:
    state = (
        await db.execute(
            select(Affinity.state)
            .where(Affinity.viewer_id == viewer_id)
            .where(Affinity.subject_id == subject_id)
        )
    ).scalar_one_or_none()
    return state == AffinityState.unlocked


async def open_question_subject_ids(db: AsyncSession, viewer_id: str) -> list[str]:
    """Subjects this viewer has picked at least once and whose record is still
    undecided — the live hypotheses.

    This is what deliberate re-exposure draws from, and it is the reason the
    unlock is reachable at all: seven comparisons of one specific person,
    arrived at by chance out of a pool of hundreds, is not something that
    happens on its own.
    """
    return list(
        (
            await db.execute(
                select(Affinity.subject_id)
                .where(Affinity.viewer_id == viewer_id)
                .where(Affinity.state == AffinityState.learning)
                .where(Affinity.picked > 0)
                .order_by(Affinity.last_decided_at.desc())
            )
        )
        .scalars()
        .all()
    )
