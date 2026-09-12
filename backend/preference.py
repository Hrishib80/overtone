"""What a viewer's choices say about what a viewer likes.

Every decided pair is a labelled example, and an unusually clean one. The two
people shown were selected to look alike, so almost everything about them
cancels: the difference between the chosen face and the rejected one is close
to the thing the viewer actually responded to. A swipe cannot give you this —
there is no second photograph to subtract.

So the model is the simplest one the data shape asks for: an online logistic
ranker over that difference vector, one weight vector per viewer, updated a
step at a time as decisions arrive. No training job, no batch, no offline
store.

**Only round-1 decisions train it.** Round 2 is a choice between the same two
people with their whole profiles showing, so a round-2 pick may have been
driven by a prompt answer, a degree, a voice. Feeding that into a model over
*face* vectors would attribute it to a face and quietly teach the system
something false. A preference model over text embeddings is a real thing to
build, and this is not it.

The tension worth being honest about: `TILT` lets what a viewer likes
influence who they are shown, and exposure is an input to ratings. A person
seen mostly by viewers already inclined toward them is rated by a friendlier
sample than one seen at random. The tilt is bounded, it never touches who wins
a comparison — the partner is still chosen by resemblance and rating overlap —
and setting `TILT` to zero removes the effect entirely. Which value is right
is a phase 07 question that needs real decisions to answer.
"""

from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import ProfileEmbedding, ViewerPreference
from backend.logging_config import get_logger

log = get_logger(__name__)

# One step's share of the gradient. High enough that a handful of decisions
# move the vector somewhere useful, low enough that one unusual choice does
# not define a person.
LEARNING_RATE = 0.15

# Shrinks every weight slightly on each update. Two jobs: it stops the vector
# growing without bound as decisions accumulate, and it lets taste move —
# evidence from two hundred pairs ago should not outvote this week's.
WEIGHT_DECAY = 0.002

# How far preference may bend anchor sampling. The multiplier lands in
# [1 - TILT, 1 + TILT], so at 0.5 a well-liked face is at most three times as
# likely to be drawn as a disliked one, and deviation and exposure still
# dominate for anyone the system has barely measured. Zero disables it.
TILT = 0.5


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def step(
    weights: list[float],
    chosen_face: list[float],
    rejected_face: list[float],
    *,
    learning_rate: float = LEARNING_RATE,
    decay: float = WEIGHT_DECAY,
) -> list[float]:
    """One online update from a single decision. Pure.

    The difference vector is normalised to unit length first. Its raw
    magnitude says how far apart the two faces were, which the pairing already
    controls for by construction — what carries the information is the
    *direction*. Normalising also keeps the learning rate meaningful instead
    of making it depend on how tight the visual band happened to be.

    The label is always 1: the chosen face beat the rejected one. Learning is
    driven by how *surprised* the model was, so a decision it already predicted
    moves the weights very little and a decision it got wrong moves them a lot.
    """
    difference = _unit([c - r for c, r in zip(chosen_face, rejected_face, strict=True)])
    surprise = 1.0 - _sigmoid(_dot(weights, difference))
    return [
        w * (1.0 - decay) + learning_rate * surprise * d for w, d in zip(weights, difference, strict=True)
    ]


def _sigmoid(x: float) -> float:
    # Split by sign: exp of a large positive number overflows, exp of a large
    # negative one does not.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def tilt(weights: list[float] | None, face: list[float], *, strength: float = TILT) -> float:
    """A bounded multiplier on how likely this face is to be drawn as an anchor.

    `tanh` rather than the raw score: an unbounded dot product would let one
    confident direction swamp the deviation and exposure terms entirely, which
    is the failure this whole sampling scheme exists to avoid.
    """
    if not weights or strength == 0:
        return 1.0
    return 1.0 + strength * math.tanh(_dot(weights, face))


async def load_weights(db: AsyncSession, viewer_id: str, segment: str) -> list[float] | None:
    row = (
        await db.execute(
            select(ViewerPreference.weights)
            .where(ViewerPreference.viewer_id == viewer_id)
            .where(ViewerPreference.segment == segment)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return [float(v) for v in row]


async def _get_or_create(db: AsyncSession, viewer_id: str, segment: str) -> ViewerPreference:
    row = (
        await db.execute(
            select(ViewerPreference)
            .where(ViewerPreference.viewer_id == viewer_id)
            .where(ViewerPreference.segment == segment)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row

    row = ViewerPreference(viewer_id=viewer_id, segment=segment)
    db.add(row)
    await db.flush()
    return row


async def _face(db: AsyncSession, user_id: str) -> list[float] | None:
    vector = (
        await db.execute(select(ProfileEmbedding.face_vector).where(ProfileEmbedding.user_id == user_id))
    ).scalar_one_or_none()
    return [float(v) for v in vector] if vector is not None else None


async def observe(
    db: AsyncSession, *, viewer_id: str, segment: str, chosen_id: str, rejected_id: str
) -> ViewerPreference | None:
    """Fold one round-1 decision into this viewer's vector.

    Returns None when either face is missing — someone whose photo has not
    finished processing can still be compared, and a decision about them is
    still a valid rating update, but there is nothing here to learn from.
    """
    chosen = await _face(db, chosen_id)
    rejected = await _face(db, rejected_id)
    if chosen is None or rejected is None:
        return None

    row = await _get_or_create(db, viewer_id, segment)
    current = [float(v) for v in row.weights] if row.weights is not None else [0.0] * len(chosen)
    if len(current) != len(chosen):
        # A dimension change means a different embedding model, and weights
        # learned in the old space describe nothing in the new one.
        log.warning("preference_reset_on_dimension_change", viewer_id=viewer_id, segment=segment)
        current = [0.0] * len(chosen)

    row.weights = step(current, chosen, rejected)
    row.observations += 1
    return row
