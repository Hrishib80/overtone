"""Pair generation, serving, and decisions.

Four steps, each independently testable: pick an anchor, retrieve their visual
neighbours, narrow to the eligible band, and select. See the architecture
notes inline at each step for why it works the way it does.

Scale note: candidates are fetched in full and scored in Python rather than
with a native ANN query. At a single campus this is comfortable — a few
thousand rows, scored once per pair. It stops being comfortable somewhere
around a few hundred thousand candidates in one pool, which is far past a
single-campus launch; that is the point to revisit this, not before.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.affinity import apply_decision as apply_affinity
from backend.affinity import open_question_subject_ids
from backend.connections import on_unlock
from backend.database import (
    Affinity,
    Connection,
    Pairing,
    PairRound,
    PairStatus,
    ProfileEmbedding,
    RatingKind,
    User,
    UserInterestedIn,
    UserStatus,
    UserVisibleAs,
    pair_key,
    utcnow,
)
from backend.database import (
    Rating as RatingRow,
)
from backend.errors import AppError, NotAuthorized, NotFound
from backend.logging_config import get_logger
from backend.preference import load_weights as load_preference
from backend.preference import observe as observe_preference
from backend.preference import tilt as preference_tilt
from backend.rating import Rating as RatingValue
from backend.rating_service import apply_comparison

log = get_logger(__name__)

# How much more a round-2 (full-profile) choice moves the rating than a
# round-1 (photo-only) one. The most important dial in the system — tune on
# real data. See the architecture notes: too high and one considered pick
# swamps a person's whole visual history; too low and round 2 is pointless.
ROUND_WEIGHTS: dict[str, float] = {
    PairRound.round_1: 1.0,
    PairRound.round_2: 2.5,
}

# Round 2 must not follow round 1 closely, or the viewer is recalling their
# earlier answer rather than forming an independent one. 48h is the floor;
# the extra 0-24h of jitter keeps the return unpredictable.
ROUND_TWO_MIN_DELAY = timedelta(hours=48)
ROUND_TWO_JITTER = timedelta(hours=24)

# A round-2 pair nobody claims within this window expires unshown rather than
# surfacing stale.
ROUND_TWO_EXPIRY = timedelta(days=7)

# How far over `want` to fetch when scoring candidates for one pair. Only
# matters for the eventual pgvector ANN version; kept here as the documented
# knob for that day.
CANDIDATE_OVERFETCH = 200

# How often a pair is built around someone this viewer has already picked and
# not yet resolved, rather than around a fresh weighted draw.
#
# This is not a recommendation feature, it is what makes the unlock reachable
# at all. Clearing the Wilson bound takes at least seven comparisons of one
# specific person; out of a pool of hundreds, chance alone will essentially
# never deliver those. So a pick is treated as a hypothesis and tested: the
# same person, a new similar opponent, does the answer hold?
#
# Half, and not more, because the other half is what keeps the pool from
# collapsing to whoever the viewer liked first — and an anchor that is never
# re-drawn is also never disproved.
RE_EXPOSURE_RATE = 0.5


def schedule_round_two_due_at(now=None, rng: random.Random | None = None):
    rng = rng or random
    jitter_seconds = rng.uniform(0, ROUND_TWO_JITTER.total_seconds())
    return (now or utcnow()) + ROUND_TWO_MIN_DELAY + timedelta(seconds=jitter_seconds)


def visual_band_percentile(pool_size: int) -> float:
    """Fraction of the candidate pool that counts as 'visually near', scaled
    to how large the pool actually is.

    Below a floor there simply isn't anyone who looks similar *and* is rated
    similarly, so the band widens to the whole pool — random pairing, within
    the mutual-visibility filters, falls out as the natural limit rather than
    a separate code path. It tightens continuously as the pool grows, so
    there is no flag to flip and no cliff where behaviour changes overnight.
    """
    if pool_size < 100:
        return 1.0  # bootstrap: no meaningful constraint
    if pool_size < 500:
        return 0.20  # loose
    if pool_size < 1500:
        return 0.05  # working — the design operating point
    return 0.02  # comfortable


def _as_vector(value) -> list[float] | None:
    """pgvector hands back a numpy array on Postgres, a plain list from the
    JSON fallback on SQLite. Normalise both to plain floats immediately so the
    rest of this module never has to think about which backend it's on."""
    if value is None:
        return None
    return [float(v) for v in value]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def anchor_weight(deviation: float, exposure: int) -> float:
    """Higher for a person the system knows less about (wide deviation) or has
    shown less often (low exposure). Sampling anchors this way is what keeps
    ANN retrieval from returning the same central profiles forever and
    stranding the tail of the campus unseen."""
    return deviation / (1.0 + exposure)


@dataclass(slots=True)
class Candidate:
    user_id: str
    face: list[float]
    voice: list[float] | None
    text: list[float] | None
    rating: RatingValue
    exposure: int


def blended_similarity(a: Candidate, b: Candidate) -> float:
    """Face dominates; voice and content break ties among candidates already
    visually close, which is where they can add anything at all — face
    distance has run out of discriminating power there by construction."""
    parts: list[tuple[float, float]] = [(cosine_similarity(a.face, b.face), 0.70)]
    if a.voice is not None and b.voice is not None:
        parts.append((cosine_similarity(a.voice, b.voice), 0.15))
    if a.text is not None and b.text is not None:
        parts.append((cosine_similarity(a.text, b.text), 0.15))

    total_weight = sum(w for _, w in parts)
    return sum(score * w for score, w in parts) / total_weight


async def _eligible_candidates(
    db: AsyncSession, viewer: User, segment: str, viewer_visible_as: list[str]
) -> list[Candidate]:
    """Everyone this viewer could be shown under `segment`: same campus,
    active, visible_as this segment, mutually interested in the viewer, and
    with a face embedding to actually compare."""
    rows = (
        await db.execute(
            select(User, ProfileEmbedding)
            .join(UserVisibleAs, UserVisibleAs.user_id == User.id)
            .join(ProfileEmbedding, ProfileEmbedding.user_id == User.id)
            .where(User.scope_id == viewer.scope_id)
            .where(User.status == UserStatus.active)
            .where(User.deleted_at.is_(None))
            .where(User.id != viewer.id)
            .where(UserVisibleAs.segment == segment)
            .where(ProfileEmbedding.face_vector.is_not(None))
            .where(
                User.id.in_(
                    select(UserInterestedIn.user_id).where(UserInterestedIn.segment.in_(viewer_visible_as))
                )
            )
        )
    ).all()
    if not rows:
        return []

    ids = [u.id for u, _ in rows]

    rating_rows = (
        (
            await db.execute(
                select(RatingRow)
                .where(RatingRow.subject_id.in_(ids))
                .where(RatingRow.audience_segment == segment)
                .where(RatingRow.kind == RatingKind.visual)
            )
        )
        .scalars()
        .all()
    )
    ratings_by_id = {r.subject_id: RatingValue(r.rating, r.deviation, r.volatility) for r in rating_rows}

    exposure_rows = (
        await db.execute(
            select(Pairing.subject_a_id, Pairing.subject_b_id).where(
                or_(Pairing.subject_a_id.in_(ids), Pairing.subject_b_id.in_(ids))
            )
        )
    ).all()
    exposure: dict[str, int] = dict.fromkeys(ids, 0)
    for a_id, b_id in exposure_rows:
        if a_id in exposure:
            exposure[a_id] += 1
        if b_id in exposure:
            exposure[b_id] += 1

    candidates = []
    for user, embedding in rows:
        face = _as_vector(embedding.face_vector)
        if not face:
            continue
        candidates.append(
            Candidate(
                user_id=user.id,
                face=face,
                voice=_as_vector(embedding.voice_vector),
                text=_as_vector(embedding.text_vector),
                rating=ratings_by_id.get(user.id, RatingValue()),
                exposure=exposure.get(user.id, 0),
            )
        )
    return candidates


async def _seen_pair_keys(db: AsyncSession, viewer_id: str, round_: str) -> set[str]:
    rows = (
        (
            await db.execute(
                select(Pairing.pair_key).where(Pairing.viewer_id == viewer_id).where(Pairing.round == round_)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


def _pick_anchor(
    candidates: list[Candidate],
    rng: random.Random,
    focus: set[str] | None = None,
    preference: list[float] | None = None,
) -> Candidate:
    """Choose who the pair is built around.

    `focus` holds this viewer's live hypotheses — people they have picked
    before and whose record has not yet resolved either way. Some of the time
    the anchor is drawn from there, which is what turns a scattering of
    unrelated picks into evidence about one person.

    Otherwise the draw is weighted toward wide deviation and low exposure, so
    the system keeps learning about people it knows least rather than
    circling the same well-measured faces, and then bent — gently, and only
    within that — toward what this viewer has been choosing. The bend is
    bounded and can be switched off; `backend/preference.py` explains what it
    costs.
    """
    if focus and rng.random() < RE_EXPOSURE_RATE:
        live = [c for c in candidates if c.user_id in focus]
        if live:
            # Uniform within the hypothesis set: these are all questions worth
            # answering, and deviation says nothing useful about which one
            # this viewer should be asked next.
            return rng.choice(live)

    # Weighted sampling for pair variety, not a security decision — the
    # default `random` module is the right tool here.
    weights = [
        anchor_weight(c.rating.deviation, c.exposure) * preference_tilt(preference, c.face)
        for c in candidates
    ]
    if sum(weights) <= 0:
        return rng.choice(candidates)
    return rng.choices(candidates, weights=weights, k=1)[0]


def _pick_partner(anchor: Candidate, pool: list[Candidate], seen: set[str] | None = None) -> Candidate | None:
    """The core of §04: narrow to visually-near AND rating-comparable, then
    rank survivors by the full blended similarity.

    `seen` excludes partners that would rebuild a pair this viewer has already
    been shown. Filtering here rather than in the caller is what makes
    re-exposure possible: selection is deterministic given an anchor, so a
    caller that could only reject the finished pair and retry would get the
    same partner back every time and have to abandon the anchor entirely —
    exactly the anchor it was trying to ask about again.
    """
    others = [c for c in pool if c.user_id != anchor.user_id]
    if seen is not None:
        others = [c for c in others if pair_key(anchor.user_id, c.user_id) not in seen]
    if not others:
        return None

    # key= is load-bearing, not style: sorting bare (distance, Candidate) tuples
    # falls through to comparing the Candidate objects whenever two distances
    # tie exactly, and Candidate has no ordering — that raises in production
    # the moment two people are equally similar to an anchor, which ties on
    # floats make more likely than it sounds.
    distances = sorted(
        ((1.0 - cosine_similarity(anchor.face, c.face), c) for c in others), key=lambda item: item[0]
    )
    # Sized on the whole eligible pool, not on what survived the `seen`
    # filter: the band expresses how alike two people have to look, which is a
    # property of the population, not of how much of it this viewer has
    # already worked through.
    percentile = visual_band_percentile(len(pool))
    band_edge_index = max(0, math.ceil(percentile * (len(distances) - 1)))
    band_edge_distance = distances[band_edge_index][0]
    visually_near = [c for d, c in distances if d <= band_edge_distance]

    rating_matched = [c for c in visually_near if anchor.rating.overlaps(c.rating)]
    # Honest fallback, not silent failure: at small pools with a settled
    # anchor rating, the interval can be narrow enough to exclude everyone in
    # the visual band. Widening back to the band alone is documented in §04
    # rather than treated as an edge case to special-case away.
    survivors = rating_matched or visually_near

    return max(survivors, key=lambda c: blended_similarity(anchor, c))


async def generate_one_pair(
    db: AsyncSession, viewer: User, *, rng: random.Random | None = None
) -> Pairing | None:
    """Generate and persist one round-1 pairing for this viewer, or None if
    there is nobody eligible to pair them with right now."""
    rng = rng or random.Random()

    interested = (
        (await db.execute(select(UserInterestedIn.segment).where(UserInterestedIn.user_id == viewer.id)))
        .scalars()
        .all()
    )
    visible = (
        (await db.execute(select(UserVisibleAs.segment).where(UserVisibleAs.user_id == viewer.id)))
        .scalars()
        .all()
    )
    if not interested or not visible:
        return None

    segment = rng.choice(sorted(interested))
    candidates = await _eligible_candidates(db, viewer, segment, sorted(visible))
    if len(candidates) < 2:
        return None

    seen = await _seen_pair_keys(db, viewer.id, PairRound.round_1)
    focus = set(await open_question_subject_ids(db, viewer.id))
    preference = await load_preference(db, viewer.id, segment)

    # A handful of anchor attempts, in case a draw turns out to have no
    # unseen partner left anywhere in its band.
    for _ in range(min(10, len(candidates))):
        anchor = _pick_anchor(candidates, rng, focus, preference)
        partner = _pick_partner(anchor, candidates, seen)
        if partner is None:
            continue

        key = pair_key(anchor.user_id, partner.user_id)

        next_position = (
            await db.execute(
                select(Pairing.position)
                .where(Pairing.viewer_id == viewer.id)
                .where(Pairing.round == PairRound.round_1)
                .order_by(Pairing.position.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        pairing = Pairing(
            viewer_id=viewer.id,
            subject_a_id=anchor.user_id,
            subject_b_id=partner.user_id,
            pair_key=key,
            segment=segment,
            round=PairRound.round_1,
            status=PairStatus.pending,
            position=(next_position or 0) + 1,
        )
        db.add(pairing)
        await db.flush()
        return pairing

    return None


async def _outstanding_pair(db: AsyncSession, viewer_id: str) -> Pairing | None:
    """A pairing already served to this viewer but never decided.

    Resuming this — rather than generating a fresh pair — is what makes a page
    reload or a dropped response harmless. Without it, calling `next_pair`
    twice before a decision silently orphans the first pairing and shows a
    second: the two calls are not idempotent, and a reload becomes a way to
    dodge answering.

    Two 'shown' rows for one viewer at once should not happen — this function
    is the only path that ever sets that status, and it always resumes before
    serving anything new — but concurrent requests (a double click, two open
    tabs) could still race past that. Deterministic ordering keeps the result
    sane rather than undefined if that ever happens; a stronger guarantee
    would need row locking, which is not worth adding until it proves to
    matter in practice.
    """
    result = await db.execute(
        select(Pairing)
        .where(Pairing.viewer_id == viewer_id)
        .where(Pairing.status == PairStatus.shown)
        .order_by(Pairing.shown_at, Pairing.id)
        .limit(1)
    )
    return result.scalars().first()


async def _promote_due_round_two(db: AsyncSession, viewer_id: str) -> Pairing | None:
    result = await db.execute(
        select(Pairing)
        .where(Pairing.viewer_id == viewer_id)
        .where(Pairing.round == PairRound.round_2)
        .where(Pairing.status == PairStatus.pending)
        .where(Pairing.due_at <= utcnow())
        .order_by(Pairing.due_at)
        .limit(1)
    )
    return result.scalars().first()


async def next_pair(db: AsyncSession, viewer: User, *, rng: random.Random | None = None) -> Pairing | None:
    """Serve the next thing this viewer should see.

    Priority: resume anything already shown and undecided; then a due round-2
    pair (a promise made to them earlier); then anything already queued; then
    freshly generated.
    """
    outstanding = await _outstanding_pair(db, viewer.id)
    if outstanding is not None:
        return outstanding

    due = await _promote_due_round_two(db, viewer.id)
    if due is not None:
        due.status = PairStatus.shown
        due.shown_at = utcnow()
        return due

    queued = await db.execute(
        select(Pairing)
        .where(Pairing.viewer_id == viewer.id)
        .where(Pairing.round == PairRound.round_1)
        .where(Pairing.status == PairStatus.pending)
        .order_by(Pairing.position)
        .limit(1)
    )
    pairing = queued.scalars().first()
    if pairing is None:
        pairing = await generate_one_pair(db, viewer, rng=rng)
    if pairing is None:
        return None

    pairing.status = PairStatus.shown
    pairing.shown_at = utcnow()
    return pairing


@dataclass(slots=True)
class Decision:
    """What one choice produced.

    `unlocked` is the point of the whole loop: the moment a viewer's picks
    become confident enough to reveal someone. It is returned rather than left
    to be re-derived, because the caller has to tell the viewer about it
    exactly once, on the response to the choice that caused it.

    `connection` is set only when that unlock was the second half of a mutual
    one — both people crossed independently, so there is nothing left to ask
    and the conversation is already open.
    """

    pairing: Pairing
    unlocked: Affinity | None = None
    connection: Connection | None = None


async def record_decision(db: AsyncSession, viewer: User, pairing_id: str, chosen_id: str) -> Decision:
    """Apply a viewer's choice: update both subjects' ratings and this
    viewer's record of them, close out the pairing, and — for a round-1
    decision — schedule its round-2 return."""
    pairing = await db.get(Pairing, pairing_id)
    if pairing is None:
        raise NotFound("That pair no longer exists.")
    if pairing.viewer_id != viewer.id:
        raise NotAuthorized("That pair isn't yours to decide.")
    if pairing.status != PairStatus.shown:
        raise AppError("That pair has already been decided.")
    if chosen_id not in (pairing.subject_a_id, pairing.subject_b_id):
        raise AppError("That choice isn't one of the two people shown.")

    loser_id = pairing.subject_b_id if chosen_id == pairing.subject_a_id else pairing.subject_a_id
    kind = RatingKind.visual if pairing.round == PairRound.round_1 else RatingKind.profile
    weight = ROUND_WEIGHTS[pairing.round]

    await apply_comparison(
        db,
        winner_id=chosen_id,
        loser_id=loser_id,
        segment=pairing.segment,
        kind=kind,
        weight=weight,
    )

    unlocked = await apply_affinity(db, viewer_id=viewer.id, chosen_id=chosen_id, rejected_id=loser_id)
    # A mutual crossing is a property of the unlock, not of the HTTP layer, so
    # it is resolved here — every path that records a decision gets it, rather
    # than only the one route that happens to serve the pair view today.
    connection = (
        await on_unlock(db, viewer_id=viewer.id, subject_id=unlocked.subject_id)
        if unlocked is not None
        else None
    )

    pairing.status = PairStatus.decided
    pairing.chosen_id = chosen_id
    pairing.decided_at = utcnow()

    if pairing.round == PairRound.round_1:
        # Round 1 is the only clean supervision for a model over faces: the
        # two people looked alike and nothing else was showing, so the
        # difference between them is close to what the viewer responded to.
        await observe_preference(
            db,
            viewer_id=viewer.id,
            segment=pairing.segment,
            chosen_id=chosen_id,
            rejected_id=loser_id,
        )
        db.add(
            Pairing(
                viewer_id=pairing.viewer_id,
                subject_a_id=pairing.subject_a_id,
                subject_b_id=pairing.subject_b_id,
                pair_key=pairing.pair_key,
                segment=pairing.segment,
                round=PairRound.round_2,
                status=PairStatus.pending,
                position=0,
                due_at=schedule_round_two_due_at(),
            )
        )

    return Decision(pairing=pairing, unlocked=unlocked, connection=connection)


async def expire_stale_round_two(db: AsyncSession) -> int:
    """A round-2 pair nobody returns for within the window is withdrawn rather
    than surfacing stale whenever the viewer next opens the app."""
    cutoff = utcnow() - ROUND_TWO_EXPIRY
    stale = (
        (
            await db.execute(
                select(Pairing)
                .where(Pairing.round == PairRound.round_2)
                .where(Pairing.status == PairStatus.pending)
                .where(Pairing.due_at < cutoff)
            )
        )
        .scalars()
        .all()
    )
    for pairing in stale:
        pairing.status = PairStatus.expired
    if stale:
        log.info("round_two_expired", count=len(stale))
    return len(stale)
