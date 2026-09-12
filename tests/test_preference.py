"""The online preference model.

The learning rule is arithmetic and is tested as arithmetic: a fixed input has
a known direction and a known magnitude, and both matter. The database tests
below only cover the wiring — which decisions train it, and what happens when
a face is missing.
"""

from __future__ import annotations

import math
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.database import ProfileEmbedding, User, ViewerPreference, utcnow
from backend.preference import (
    LEARNING_RATE,
    TILT,
    _dot,
    load_weights,
    observe,
    step,
    tilt,
)

# ---------------------------------------------------------------------------
# The learning rule: pure.
# ---------------------------------------------------------------------------

LIKES_LEFT = [1.0, 0.0]
LIKES_RIGHT = [0.0, 1.0]


def test_the_first_decision_points_at_the_difference():
    """From nothing, the vector moves exactly along chosen minus rejected."""
    weights = step([0.0, 0.0], LIKES_LEFT, LIKES_RIGHT, decay=0.0)
    # The difference is (1, -1), normalised; surprise from a zero vector is 0.5.
    expected = LEARNING_RATE * 0.5 / math.sqrt(2)
    assert weights[0] == pytest.approx(expected)
    assert weights[1] == pytest.approx(-expected)


def test_repeated_agreement_strengthens_the_same_direction():
    weights = [0.0, 0.0]
    magnitudes = []
    for _ in range(10):
        weights = step(weights, LIKES_LEFT, LIKES_RIGHT)
        magnitudes.append(_dot(weights, [1.0, -1.0]))

    assert magnitudes == sorted(magnitudes), "each agreeing decision must push further, not oscillate"


def test_a_predicted_decision_barely_moves_anything():
    """Learning is driven by surprise. Once the model is sure, confirming it
    is nearly free — which is what stops a hundred easy decisions from
    drowning out one informative reversal."""
    settled = [0.0, 0.0]
    for _ in range(40):
        settled = step(settled, LIKES_LEFT, LIKES_RIGHT)

    confirmed = step(settled, LIKES_LEFT, LIKES_RIGHT, decay=0.0)
    reversed_ = step(settled, LIKES_RIGHT, LIKES_LEFT, decay=0.0)

    def moved(before, after):
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(before, after, strict=True)))

    assert moved(settled, confirmed) < moved(settled, reversed_) / 2


def test_a_reversal_pulls_the_vector_back():
    weights = [0.0, 0.0]
    for _ in range(10):
        weights = step(weights, LIKES_LEFT, LIKES_RIGHT)
    before = _dot(weights, [1.0, -1.0])

    for _ in range(10):
        weights = step(weights, LIKES_RIGHT, LIKES_LEFT)

    assert _dot(weights, [1.0, -1.0]) < before


def test_decay_stops_the_vector_growing_without_bound():
    """Two hundred agreeing decisions must not produce a vector that can
    never be argued with."""
    weights = [0.0, 0.0]
    for _ in range(200):
        weights = step(weights, LIKES_LEFT, LIKES_RIGHT)
    assert math.sqrt(sum(w * w for w in weights)) < 100


def test_identical_faces_teach_nothing_and_do_not_divide_by_zero():
    weights = step([0.2, 0.2], LIKES_LEFT, LIKES_LEFT, decay=0.0)
    assert weights == [0.2, 0.2]


def test_mismatched_dimensions_raise_rather_than_silently_misalign():
    with pytest.raises(ValueError, match="zip"):
        step([0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])


# ---------------------------------------------------------------------------
# The tilt.
# ---------------------------------------------------------------------------


def test_no_model_means_no_tilt():
    assert tilt(None, LIKES_LEFT) == 1.0
    assert tilt([], LIKES_LEFT) == 1.0
    assert tilt([0.0, 0.0], LIKES_LEFT) == 1.0


def test_the_tilt_favours_what_the_viewer_picks_and_discounts_what_they_pass():
    weights = [0.0, 0.0]
    for _ in range(10):
        weights = step(weights, LIKES_LEFT, LIKES_RIGHT)

    assert tilt(weights, LIKES_LEFT) > 1.0
    assert tilt(weights, LIKES_RIGHT) < 1.0


def test_the_tilt_stays_inside_its_bounds_however_confident_the_model():
    """The bound is what keeps preference from swamping deviation and
    exposure, which is the failure the sampling scheme exists to avoid."""
    extreme = [1000.0, -1000.0]
    for face in (LIKES_LEFT, LIKES_RIGHT):
        assert 1.0 - TILT <= tilt(extreme, face) <= 1.0 + TILT


def test_zero_strength_switches_the_model_off_entirely():
    """The escape hatch has to be real: if the exposure bias this buys turns
    out not to be worth it, one constant removes it."""
    extreme = [1000.0, -1000.0]
    assert tilt(extreme, LIKES_LEFT, strength=0) == 1.0
    assert tilt(extreme, LIKES_RIGHT, strength=0) == 1.0


# ---------------------------------------------------------------------------
# The wiring.
# ---------------------------------------------------------------------------


async def _seed(db_sessionmaker, scope_id, email, face):
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
        await db.flush()
        if face is not None:
            db.add(ProfileEmbedding(user_id=user.id, face_vector=face))
        await db.commit()
        return user.id


@pytest_asyncio.fixture
async def faces(db_sessionmaker, scope):
    viewer = await _seed(db_sessionmaker, scope.id, "v@campus.edu", None)
    left = await _seed(db_sessionmaker, scope.id, "left@campus.edu", LIKES_LEFT)
    right = await _seed(db_sessionmaker, scope.id, "right@campus.edu", LIKES_RIGHT)
    return viewer, left, right


@pytest.mark.asyncio
async def test_observing_creates_and_then_updates_one_row_per_segment(db_sessionmaker, faces):
    viewer, left, right = faces

    for _ in range(3):
        async with db_sessionmaker() as db:
            await observe(db, viewer_id=viewer, segment="woman", chosen_id=left, rejected_id=right)
            await db.commit()

    async with db_sessionmaker() as db:
        rows = (await db.execute(select(ViewerPreference))).scalars().all()
        assert len(rows) == 1
        assert rows[0].observations == 3

        weights = await load_weights(db, viewer, "woman")
        assert tilt(weights, LIKES_LEFT) > 1.0


@pytest.mark.asyncio
async def test_taste_is_learned_per_segment_not_per_person(db_sessionmaker, faces):
    """Someone interested in more than one segment is not one taste applied
    twice — the same split the ratings make, for the same reason."""
    viewer, left, right = faces

    async with db_sessionmaker() as db:
        await observe(db, viewer_id=viewer, segment="woman", chosen_id=left, rejected_id=right)
        await observe(db, viewer_id=viewer, segment="man", chosen_id=right, rejected_id=left)
        await db.commit()

    async with db_sessionmaker() as db:
        as_woman = await load_weights(db, viewer, "woman")
        as_man = await load_weights(db, viewer, "man")

    assert tilt(as_woman, LIKES_LEFT) > 1.0
    assert tilt(as_man, LIKES_LEFT) < 1.0


@pytest.mark.asyncio
async def test_a_missing_face_is_skipped_rather_than_guessed(db_sessionmaker, scope, faces):
    """A photo that has not finished processing is still a valid comparison
    for rating purposes — it just has nothing here to learn from."""
    viewer, left, _right = faces
    faceless = await _seed(db_sessionmaker, scope.id, "pending@campus.edu", None)

    async with db_sessionmaker() as db:
        skipped = await observe(db, viewer_id=viewer, segment="woman", chosen_id=left, rejected_id=faceless)
        assert skipped is None
        await db.commit()

    async with db_sessionmaker() as db:
        assert (await db.execute(select(ViewerPreference))).scalars().all() == []


@pytest.mark.asyncio
async def test_an_unknown_viewer_has_no_weights_rather_than_a_zero_vector(db_sessionmaker, faces):
    """None and a zero vector both tilt nothing, but only one of them is true —
    and the caller should not have to know the dimension to ask."""
    viewer, _left, _right = faces
    async with db_sessionmaker() as db:
        assert await load_weights(db, viewer, "woman") is None
