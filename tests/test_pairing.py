"""Pair generation, serving, and decisions.

Split deliberately: the pure selection logic (`_pick_partner`, similarity,
weighting) is tested in memory against hand-built candidates, with no
database — that is where the interesting arithmetic lives, and it should be
checked precisely. The database-backed tests below exercise the wiring around
it: mutual visibility, scope isolation, the seen-pair constraint, and the
serve/decide/reschedule state machine — the properties that would silently
leak one campus's users into another's, or let the same pair repeat, if they
regressed.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend.database import (
    Pairing,
    PairRound,
    PairStatus,
    ProfileEmbedding,
    RatingKind,
    Scope,
    ScopeStatus,
    User,
    UserInterestedIn,
    UserStatus,
    UserVisibleAs,
    ViewerPreference,
    pair_key,
    utcnow,
)
from backend.database import (
    Rating as RatingRow,
)
from backend.errors import AppError, NotAuthorized, NotFound
from backend.pairing import (
    ROUND_TWO_JITTER,
    ROUND_TWO_MIN_DELAY,
    Candidate,
    _pick_anchor,  # testing the selection core directly, by design — see module docstring
    _pick_partner,
    anchor_weight,
    blended_similarity,
    cosine_similarity,
    expire_stale_round_two,
    generate_one_pair,
    next_pair,
    record_decision,
    schedule_round_two_due_at,
    visual_band_percentile,
)
from backend.rating import Rating as RatingValue

# ---------------------------------------------------------------------------
# Pure-function tests: no database, no fixtures.
# ---------------------------------------------------------------------------


def test_cosine_similarity_of_identical_vectors_is_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_of_opposite_vectors_is_minus_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_of_a_zero_vector_is_zero_not_a_crash():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_mismatched_vector_lengths_raise_rather_than_silently_misalign():
    with pytest.raises(ValueError, match="zip"):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


@pytest.mark.parametrize(
    ("pool_size", "expected"),
    [(1, 1.0), (99, 1.0), (100, 0.20), (499, 0.20), (500, 0.05), (1499, 0.05), (1500, 0.02), (100_000, 0.02)],
)
def test_visual_band_percentile_boundaries(pool_size, expected):
    assert visual_band_percentile(pool_size) == expected


def test_anchor_weight_rises_with_uncertainty():
    assert anchor_weight(deviation=300, exposure=0) > anchor_weight(deviation=50, exposure=0)


def test_anchor_weight_falls_with_exposure():
    assert anchor_weight(deviation=200, exposure=0) > anchor_weight(deviation=200, exposure=20)


def _candidate(user_id, face, *, rating=None, voice=None, text=None, exposure=0):
    return Candidate(
        user_id=user_id,
        face=face,
        voice=voice,
        text=text,
        rating=rating or RatingValue(),
        exposure=exposure,
    )


def test_blended_similarity_uses_face_only_when_voice_and_text_are_absent():
    a = _candidate("a", [1.0, 0.0])
    b = _candidate("b", [1.0, 0.0])
    assert blended_similarity(a, b) == pytest.approx(1.0)


def test_blended_similarity_only_uses_voice_when_both_sides_have_it():
    # Faces identical, voices opposite: if voice were (wrongly) ignored because
    # only one side needs it, this would still read as a perfect match.
    a = _candidate("a", [1.0, 0.0], voice=[1.0, 0.0])
    b = _candidate("b", [1.0, 0.0], voice=[-1.0, 0.0])
    assert blended_similarity(a, b) < 1.0


def test_blended_similarity_ignores_voice_present_on_only_one_side():
    a = _candidate("a", [1.0, 0.0], voice=[1.0, 0.0])
    b = _candidate("b", [1.0, 0.0])  # no voice vector at all
    assert blended_similarity(a, b) == pytest.approx(1.0)


def test_blended_similarity_weights_face_above_voice_and_text():
    close_face_far_rest = _candidate("x", [1.0, 0.0], voice=[-1.0, 0.0], text=[-1.0, 0.0])
    anchor = _candidate("anchor", [1.0, 0.0], voice=[1.0, 0.0], text=[1.0, 0.0])
    far_face_close_rest = _candidate("y", [0.0, 1.0], voice=[1.0, 0.0], text=[1.0, 0.0])

    assert blended_similarity(anchor, close_face_far_rest) > blended_similarity(anchor, far_face_close_rest)


def test_pick_partner_returns_none_with_no_other_candidates():
    anchor = _candidate("anchor", [1.0, 0.0])
    assert _pick_partner(anchor, [anchor]) is None


def test_pick_partner_prefers_the_closest_face_when_ratings_all_overlap():
    """Default ratings carry maximum deviation, so nothing here is excluded on
    the rating axis — the visual band and blended similarity decide alone."""
    anchor = _candidate("anchor", [1.0, 0.0])
    near = _candidate("near", [0.99, 0.14])
    far = _candidate("far", [0.0, 1.0])

    assert _pick_partner(anchor, [anchor, near, far]) is near


def test_pick_partner_excludes_a_close_candidate_whose_rating_does_not_overlap():
    """The closest face in the band is skipped when its rating interval is
    disjoint from the anchor's — proof the two filters are actually ANDed,
    not just the visual one."""
    anchor = _candidate("anchor", [1.0, 0.0], rating=RatingValue(1500, 30))
    closest_but_disjoint = _candidate("closest", [0.999, 0.045], rating=RatingValue(2200, 20))
    farther_but_overlapping = _candidate("farther", [0.9, 0.44], rating=RatingValue(1520, 40))

    assert not anchor.rating.overlaps(closest_but_disjoint.rating)
    assert anchor.rating.overlaps(farther_but_overlapping.rating)

    chosen = _pick_partner(anchor, [anchor, closest_but_disjoint, farther_but_overlapping])
    assert chosen is farther_but_overlapping


def test_pick_partner_falls_back_to_visual_only_when_nothing_overlaps():
    """If every candidate in the band fails the rating filter, the fallback is
    the closest by face alone — never an empty result when candidates exist."""
    anchor = _candidate("anchor", [1.0, 0.0], rating=RatingValue(1500, 20))
    closer = _candidate("closer", [0.99, 0.14], rating=RatingValue(2200, 20))
    farther = _candidate("farther", [0.9, 0.44], rating=RatingValue(2300, 20))

    assert not anchor.rating.overlaps(closer.rating)
    assert not anchor.rating.overlaps(farther.rating)

    assert _pick_partner(anchor, [anchor, closer, farther]) is closer


def test_pick_partner_percentile_band_excludes_the_far_tail_of_a_large_pool():
    """At 'working' scale (>=500), only the nearest 5% counts as in-band —
    a candidate outside that must not be chosen even if nothing closer
    happens to pass some other filter."""
    anchor = _candidate("anchor", [1.0, 0.0])
    near_pool = [
        _candidate(f"near-{i}", [0.999 - i * 0.0001, 0.045])
        for i in range(30)  # comfortably inside the nearest 5% of 520
    ]
    far_pool = [_candidate(f"far-{i}", [0.0, 1.0], rating=RatingValue(1500, 350)) for i in range(490)]
    pool = [anchor, *near_pool, *far_pool]

    chosen = _pick_partner(anchor, pool)
    assert chosen.user_id.startswith("near-")


def test_schedule_round_two_due_at_falls_within_the_documented_window():
    now = utcnow()
    rng = random.Random(42)
    due = schedule_round_two_due_at(now=now, rng=rng)

    assert now + ROUND_TWO_MIN_DELAY <= due <= now + ROUND_TWO_MIN_DELAY + ROUND_TWO_JITTER


def test_pair_key_is_order_independent():
    assert pair_key("a", "b") == pair_key("b", "a")
    assert pair_key("a", "b") != pair_key("a", "c")


# ---------------------------------------------------------------------------
# Database-backed tests: the wiring around the pure logic above.
# ---------------------------------------------------------------------------


async def _seed_user(
    db_sessionmaker,
    *,
    scope_id: str,
    email: str,
    visible_as: list[str],
    interested_in: list[str],
    face: list[float] | None,
    voice: list[float] | None = None,
    text: list[float] | None = None,
    status: str = UserStatus.active,
    deleted: bool = False,
) -> str:
    """Insert a ready-to-pair user directly, bypassing onboarding — these
    tests exercise the pairing algorithm against controlled inputs, not the
    upload pipeline (covered separately in test_media.py)."""
    async with db_sessionmaker() as db:
        user = User(
            email=email,
            password_hash="x",
            display_name=email.split("@")[0],
            birthdate=date(2003, 1, 1),
            scope_id=scope_id,
            status=status,
            cap_segment=visible_as[0] if visible_as else None,
            email_verified_at=utcnow(),
            deleted_at=utcnow() if deleted else None,
        )
        db.add(user)
        await db.flush()

        for segment in visible_as:
            db.add(UserVisibleAs(user_id=user.id, segment=segment, is_primary=segment == visible_as[0]))
        for segment in interested_in:
            db.add(UserInterestedIn(user_id=user.id, segment=segment))

        if face is not None:
            db.add(ProfileEmbedding(user_id=user.id, face_vector=face, voice_vector=voice, text_vector=text))

        await db.commit()
        return user.id


@pytest.fixture
def rng():
    return random.Random(1234)


@pytest.mark.asyncio
async def test_generate_one_pair_is_none_without_interested_in(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["woman"],
        interested_in=[],
        face=[1.0, 0.0],
    )
    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        assert await generate_one_pair(db, viewer, rng=rng) is None


@pytest.mark.asyncio
async def test_generate_one_pair_is_none_with_fewer_than_two_candidates(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="one@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        assert await generate_one_pair(db, viewer, rng=rng) is None


@pytest.mark.asyncio
async def test_generate_one_pair_never_includes_the_viewer(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["woman", "man"],
        interested_in=["man"],
        face=[1.0, 0.0],
    )
    for i in range(3):
        await _seed_user(
            db_sessionmaker,
            scope_id=scope.id,
            email=f"c{i}@campus.edu",
            visible_as=["man"],
            interested_in=["woman"],
            face=[0.9 - i * 0.05, 0.1],
        )

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        pairing = await generate_one_pair(db, viewer, rng=rng)

    assert pairing is not None
    assert viewer_id not in (pairing.subject_a_id, pairing.subject_b_id)


@pytest.mark.asyncio
async def test_a_different_campus_is_never_a_candidate(db_sessionmaker, scope, rng):
    async with db_sessionmaker() as db:
        other = Scope(
            slug="other", name="Other Campus", email_domains=["other.edu"], status=ScopeStatus.building
        )
        db.add(other)
        await db.commit()
        other_id = other.id

    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    # A near-perfect visual and mutual-visibility match, on the wrong campus.
    await _seed_user(
        db_sessionmaker,
        scope_id=other_id,
        email="lookalike@other.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[1.0, 0.0],
    )

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        assert await generate_one_pair(db, viewer, rng=rng) is None


@pytest.mark.asyncio
async def test_visibility_must_be_mutual_not_one_sided(db_sessionmaker, scope, rng):
    """A candidate who is visible_as the target segment but not interested_in
    the viewer's segment must never appear, even though the one-directional
    join would otherwise find them."""
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="uninterested@campus.edu",
        visible_as=["woman"],
        interested_in=["nonbinary"],  # not interested in "man" — the viewer's segment
        face=[0.99, 0.1],
    )

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        assert await generate_one_pair(db, viewer, rng=rng) is None


@pytest.mark.asyncio
async def test_a_candidate_without_a_face_embedding_is_excluded(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="nophoto@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=None,
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="hasphoto@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="alsohasphoto@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        pairing = await generate_one_pair(db, viewer, rng=rng)

    assert pairing is not None
    assert "nophoto" not in (pairing.subject_a_id, pairing.subject_b_id)


@pytest.mark.asyncio
async def test_a_deleted_user_is_excluded(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="gone@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.95, 0.1],
        deleted=True,
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="here@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )
    await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="alsohere@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.4, 0.6],
    )

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        pairing = await generate_one_pair(db, viewer, rng=rng)

    assert pairing is not None
    assert "gone" not in (pairing.subject_a_id, pairing.subject_b_id)


@pytest.mark.asyncio
async def test_the_same_pair_is_never_generated_twice_for_one_viewer(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    for i in range(5):
        await _seed_user(
            db_sessionmaker,
            scope_id=scope.id,
            email=f"c{i}@campus.edu",
            visible_as=["woman"],
            interested_in=["man"],
            face=[0.9 - i * 0.1, 0.1 + i * 0.05],
        )

    seen_keys = set()
    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        for _ in range(30):
            pairing = await generate_one_pair(db, viewer, rng=rng)
            if pairing is None:
                break
            assert pairing.pair_key not in seen_keys
            seen_keys.add(pairing.pair_key)
        await db.commit()


@pytest.mark.asyncio
async def test_next_pair_generates_when_nothing_is_queued(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    for i in range(3):
        await _seed_user(
            db_sessionmaker,
            scope_id=scope.id,
            email=f"c{i}@campus.edu",
            visible_as=["woman"],
            interested_in=["man"],
            face=[0.9 - i * 0.1, 0.1],
        )

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        pairing = await next_pair(db, viewer, rng=rng)
        await db.commit()

    assert pairing is not None
    assert pairing.status == PairStatus.shown
    assert pairing.shown_at is not None


@pytest.mark.asyncio
async def test_next_pair_is_none_when_nobody_is_eligible(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        assert await next_pair(db, viewer, rng=rng) is None


@pytest.mark.asyncio
async def test_next_pair_resumes_an_undecided_pairing_instead_of_generating_a_new_one(
    db_sessionmaker, scope, rng
):
    """Regression test: calling next_pair twice before a decision (a page
    reload, for instance) must not orphan the first pairing and hand out a
    second — the two calls have to be idempotent."""
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    for i in range(3):
        await _seed_user(
            db_sessionmaker,
            scope_id=scope.id,
            email=f"c{i}@campus.edu",
            visible_as=["woman"],
            interested_in=["man"],
            face=[0.9 - i * 0.1, 0.1],
        )

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        first = await next_pair(db, viewer, rng=rng)
        await db.commit()

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        second = await next_pair(db, viewer, rng=rng)
        await db.commit()

    assert second.id == first.id

    async with db_sessionmaker() as db:
        shown = (
            (
                await db.execute(
                    select(Pairing)
                    .where(Pairing.viewer_id == viewer_id)
                    .where(Pairing.status == PairStatus.shown)
                )
            )
            .scalars()
            .all()
        )
    assert len(shown) == 1


@pytest.mark.asyncio
async def test_next_pair_promotes_a_due_round_two_over_generating_a_new_pair(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        db.add(
            Pairing(
                viewer_id=viewer_id,
                subject_a_id=a,
                subject_b_id=b,
                pair_key=pair_key(a, b),
                segment="woman",
                round=PairRound.round_2,
                status=PairStatus.pending,
                due_at=utcnow() - timedelta(minutes=1),
            )
        )
        await db.commit()

        viewer = await db.get(User, viewer_id)
        pairing = await next_pair(db, viewer, rng=rng)
        await db.commit()

    assert pairing.round == PairRound.round_2
    assert pairing.status == PairStatus.shown


@pytest.mark.asyncio
async def test_next_pair_does_not_promote_a_round_two_before_it_is_due(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        db.add(
            Pairing(
                viewer_id=viewer_id,
                subject_a_id=a,
                subject_b_id=b,
                pair_key=pair_key(a, b),
                segment="woman",
                round=PairRound.round_2,
                status=PairStatus.pending,
                due_at=utcnow() + timedelta(hours=47),
            )
        )
        await db.commit()

        viewer = await db.get(User, viewer_id)
        pairing = await next_pair(db, viewer, rng=rng)
        await db.commit()

    # Not due yet, but a fresh round-1 pair is still generated from a/b.
    assert pairing.round == PairRound.round_1


@pytest.mark.asyncio
async def test_record_decision_moves_ratings_the_way_apply_comparison_would(db_sessionmaker, scope, rng):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        pairing = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_1,
            status=PairStatus.shown,
            shown_at=utcnow(),
        )
        db.add(pairing)
        await db.commit()
        pairing_id = pairing.id

        viewer = await db.get(User, viewer_id)
        await record_decision(db, viewer, pairing_id, a)
        await db.commit()

        winner = (
            await db.execute(
                select(RatingRow)
                .where(RatingRow.subject_id == a)
                .where(RatingRow.audience_segment == "woman")
                .where(RatingRow.kind == RatingKind.visual)
            )
        ).scalar_one()
        loser = (
            await db.execute(
                select(RatingRow)
                .where(RatingRow.subject_id == b)
                .where(RatingRow.audience_segment == "woman")
                .where(RatingRow.kind == RatingKind.visual)
            )
        ).scalar_one()

    assert winner.rating > 1500.0
    assert loser.rating < 1500.0


@pytest.mark.asyncio
async def test_a_round_one_decision_schedules_a_round_two(db_sessionmaker, scope):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        pairing = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_1,
            status=PairStatus.shown,
            shown_at=utcnow(),
        )
        db.add(pairing)
        await db.commit()

        viewer = await db.get(User, viewer_id)
        await record_decision(db, viewer, pairing.id, a)
        await db.commit()

        round_two = (
            (
                await db.execute(
                    select(Pairing)
                    .where(Pairing.viewer_id == viewer_id)
                    .where(Pairing.round == PairRound.round_2)
                )
            )
            .scalars()
            .all()
        )

    assert len(round_two) == 1
    r2 = round_two[0]
    assert r2.pair_key == pair_key(a, b)
    assert r2.status == PairStatus.pending
    assert r2.due_at >= utcnow() + ROUND_TWO_MIN_DELAY - timedelta(seconds=5)


@pytest.mark.asyncio
async def test_a_round_two_decision_does_not_schedule_another_round_two(db_sessionmaker, scope):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        pairing = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_2,
            status=PairStatus.shown,
            shown_at=utcnow(),
        )
        db.add(pairing)
        await db.commit()

        viewer = await db.get(User, viewer_id)
        await record_decision(db, viewer, pairing.id, a)
        await db.commit()

        all_pairings = (
            (await db.execute(select(Pairing).where(Pairing.viewer_id == viewer_id))).scalars().all()
        )

    assert len(all_pairings) == 1  # only the one we created — nothing chained on


@pytest.mark.asyncio
async def test_record_decision_rejects_the_wrong_viewer(db_sessionmaker, scope):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    intruder_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="intruder@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[0.5, 0.5],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        pairing = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_1,
            status=PairStatus.shown,
            shown_at=utcnow(),
        )
        db.add(pairing)
        await db.commit()

        intruder = await db.get(User, intruder_id)
        with pytest.raises(NotAuthorized):
            await record_decision(db, intruder, pairing.id, a)


@pytest.mark.asyncio
async def test_record_decision_rejects_an_unknown_pairing(db_sessionmaker, scope):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        with pytest.raises(NotFound):
            await record_decision(db, viewer, "does-not-exist", "someone")


@pytest.mark.asyncio
async def test_record_decision_rejects_an_already_decided_pairing(db_sessionmaker, scope):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        pairing = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_1,
            status=PairStatus.decided,
            chosen_id=a,
            decided_at=utcnow(),
        )
        db.add(pairing)
        await db.commit()

        viewer = await db.get(User, viewer_id)
        with pytest.raises(AppError):
            await record_decision(db, viewer, pairing.id, a)


@pytest.mark.asyncio
async def test_record_decision_rejects_a_choice_outside_the_pair(db_sessionmaker, scope):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )

    async with db_sessionmaker() as db:
        pairing = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_1,
            status=PairStatus.shown,
            shown_at=utcnow(),
        )
        db.add(pairing)
        await db.commit()

        viewer = await db.get(User, viewer_id)
        with pytest.raises(AppError):
            await record_decision(db, viewer, pairing.id, viewer_id)


@pytest.mark.asyncio
async def test_expire_stale_round_two_only_touches_pending_rows_past_the_window(db_sessionmaker, scope):
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.9, 0.1],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.5, 0.5],
    )
    c = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="c@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.4, 0.4],
    )

    async with db_sessionmaker() as db:
        stale = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_2,
            status=PairStatus.pending,
            due_at=utcnow() - timedelta(days=8),
        )
        fresh = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=c,
            pair_key=pair_key(a, c),
            segment="woman",
            round=PairRound.round_2,
            status=PairStatus.pending,
            due_at=utcnow() - timedelta(hours=1),
        )
        already_decided = Pairing(
            viewer_id=viewer_id,
            subject_a_id=b,
            subject_b_id=c,
            pair_key=pair_key(b, c),
            segment="woman",
            round=PairRound.round_2,
            status=PairStatus.decided,
            due_at=utcnow() - timedelta(days=30),
            chosen_id=b,
            decided_at=utcnow(),
        )
        db.add_all([stale, fresh, already_decided])
        await db.commit()

        count = await expire_stale_round_two(db)
        await db.commit()

        await db.refresh(stale)
        await db.refresh(fresh)
        await db.refresh(already_decided)

    assert count == 1
    assert stale.status == PairStatus.expired
    assert fresh.status == PairStatus.pending
    assert already_decided.status == PairStatus.decided


# ---------------------------------------------------------------------------
# Deliberate re-exposure: testing a hypothesis rather than browsing.
# ---------------------------------------------------------------------------


class _ScriptedRng:
    """Enough of `random.Random` for selection, with the coin flip pinned.

    `_pick_anchor` branches on a single `random()` draw, so a scripted value
    is the difference between testing re-exposure and testing luck.
    """

    def __init__(self, coin: float):
        self.coin = coin

    def random(self) -> float:
        return self.coin

    def choice(self, seq):
        return seq[0]

    def choices(self, population, weights=None, k=1):
        return [population[0]]


def test_pick_partner_skips_a_pair_already_shown():
    """The next-best partner, not a dead end — the anchor is the point."""
    anchor = _candidate("anchor", [1.0, 0.0])
    nearest = _candidate("nearest", [0.99, 0.01])
    further = _candidate("further", [0.9, 0.1])
    pool = [anchor, nearest, further]

    assert _pick_partner(anchor, pool).user_id == "nearest"
    seen = {pair_key("anchor", "nearest")}
    assert _pick_partner(anchor, pool, seen).user_id == "further"


def test_pick_partner_is_none_when_every_partner_is_exhausted():
    anchor = _candidate("anchor", [1.0, 0.0])
    other = _candidate("other", [0.9, 0.1])
    seen = {pair_key("anchor", "other")}
    assert _pick_partner(anchor, [anchor, other], seen) is None


def test_the_band_is_sized_on_the_pool_not_on_what_is_left():
    """Otherwise a viewer deep into their queue would see the band tighten
    under them — resemblance is a property of the population, not of how much
    of it one person has already worked through."""
    faces = [[1.0, i * 0.001] for i in range(150)]
    pool = [_candidate(f"c{i}", face) for i, face in enumerate(faces)]
    anchor = pool[0]

    # 150 candidates puts the band at 0.20. Hide all but a handful: if the
    # percentile were re-read off the survivors it would jump to 1.0 and the
    # furthest-away candidate would become eligible.
    keep = {"c1", "c2", "c149"}
    seen = {pair_key(anchor.user_id, c.user_id) for c in pool[1:] if c.user_id not in keep}

    assert _pick_partner(anchor, pool, seen).user_id in {"c1", "c2"}


def test_an_open_question_is_re_anchored_when_the_coin_says_so():
    pool = [_candidate("a", [1.0, 0.0]), _candidate("b", [0.9, 0.1]), _candidate("c", [0.1, 1.0])]
    anchor = _pick_anchor(pool, _ScriptedRng(coin=0.0), focus={"c"})
    assert anchor.user_id == "c"


def test_the_other_half_of_the_time_the_usual_weighting_applies():
    """Re-exposure must not become the only way anyone is ever shown — an
    anchor that is never re-drawn is also never disproved."""
    pool = [_candidate("a", [1.0, 0.0]), _candidate("b", [0.9, 0.1]), _candidate("c", [0.1, 1.0])]
    anchor = _pick_anchor(pool, _ScriptedRng(coin=0.99), focus={"c"})
    assert anchor.user_id == "a"  # the scripted weighted draw, not the focus


def test_a_focus_on_somebody_ineligible_falls_through_rather_than_failing():
    """An open question can leave the pool between pairs — they deactivate,
    change who they are visible to, or are simply not in this segment."""
    pool = [_candidate("a", [1.0, 0.0]), _candidate("b", [0.9, 0.1])]
    anchor = _pick_anchor(pool, _ScriptedRng(coin=0.0), focus={"gone"})
    assert anchor.user_id == "a"


@pytest.mark.asyncio
async def test_generate_one_pair_re_anchors_on_a_picked_subject(db_sessionmaker, scope):
    """End to end: a pick becomes a hypothesis, and the next pair tests it.

    Without this the unlock is theoretical — seven comparisons of one specific
    person, arrived at by chance out of a whole campus, does not happen.
    """
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[1.0, 0.0],
    )
    subjects = [
        await _seed_user(
            db_sessionmaker,
            scope_id=scope.id,
            email=f"c{i}@campus.edu",
            visible_as=["man"],
            interested_in=["woman"],
            face=[1.0 - i * 0.01, i * 0.01],
        )
        for i in range(5)
    ]

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        first = await generate_one_pair(db, viewer, rng=random.Random(5))
        assert first is not None
        first.status = PairStatus.shown
        await db.flush()
        chosen = first.subject_a_id
        await record_decision(db, viewer, first.id, chosen)
        await db.commit()

    async with db_sessionmaker() as db:
        viewer = await db.get(User, viewer_id)
        # Coin pinned to the re-exposure branch: the person just picked has to
        # be in the next pair, against somebody new.
        second = await generate_one_pair(db, viewer, rng=_ScriptedRng(coin=0.0))
        await db.commit()

    assert second is not None
    assert chosen in (second.subject_a_id, second.subject_b_id)
    assert second.pair_key != first.pair_key
    assert set(subjects) >= {second.subject_a_id, second.subject_b_id}


@pytest.mark.asyncio
async def test_only_round_one_trains_the_preference_model(db_sessionmaker, scope):
    """The decision this rests on, pinned.

    A round-2 pick may have been driven by a prompt answer or a degree. Folding
    it into a model over *face* vectors would attribute it to a face and teach
    the system something false, so round 2 updates ratings and affinity and
    deliberately leaves the face model alone.
    """
    viewer_id = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="v@campus.edu",
        visible_as=["man"],
        interested_in=["woman"],
        face=[1.0, 0.0],
    )
    a = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="a@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[1.0, 0.0],
    )
    b = await _seed_user(
        db_sessionmaker,
        scope_id=scope.id,
        email="b@campus.edu",
        visible_as=["woman"],
        interested_in=["man"],
        face=[0.0, 1.0],
    )

    async def observations():
        async with db_sessionmaker() as db:
            row = (await db.execute(select(ViewerPreference))).scalars().first()
            return row.observations if row else 0

    async with db_sessionmaker() as db:
        first = Pairing(
            viewer_id=viewer_id,
            subject_a_id=a,
            subject_b_id=b,
            pair_key=pair_key(a, b),
            segment="woman",
            round=PairRound.round_1,
            status=PairStatus.shown,
            shown_at=utcnow(),
        )
        db.add(first)
        await db.flush()
        viewer = await db.get(User, viewer_id)
        await record_decision(db, viewer, first.id, a)
        await db.commit()

    assert await observations() == 1

    # Deciding the round-2 pairing that the round-1 decision scheduled, rather
    # than a second one: the unique key is per (viewer, pair, round).
    async with db_sessionmaker() as db:
        second = (
            (
                await db.execute(
                    select(Pairing)
                    .where(Pairing.viewer_id == viewer_id)
                    .where(Pairing.round == PairRound.round_2)
                )
            )
            .scalars()
            .one()
        )
        second.status = PairStatus.shown
        second.shown_at = utcnow()
        await db.flush()
        viewer = await db.get(User, viewer_id)
        await record_decision(db, viewer, second.id, a)
        await db.commit()

    assert await observations() == 1, "round 2 must not train a model over faces"
