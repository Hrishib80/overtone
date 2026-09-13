"""What to do about what the detectors found.

Pure functions, no model and no database — the same split as `rating.py`,
and for the same reason: these thresholds are the part that will be argued
about and tuned on real photos, and they should be readable and testable
without loading a neural network.

**There are three outcomes, not two.** A classifier with only pass and fail
either rejects real people's photos or lets things through, because the
middle of its score distribution is genuinely ambiguous — a beach photo, a
breastfeeding photo, a painting, a low-cut top at an unlucky angle. The third
outcome is `hold`: the photo is not shown to anyone and a person looks at it.
That state is the entire reason the review queue exists, and having it is what
lets the automatic thresholds be set where they are honest rather than where
they are least embarrassing.

**Nothing is ever rejected on an age estimate.** buffalo_l's genderage head is
routinely out by several years, and the cost of the two errors is wildly
asymmetric: a wrongly-rejected nineteen-year-old is a person told by a machine
that they look like a child and shown no way to argue, while a wrongly-held
one waits an hour. Under-age photos are held, always, and a human decides.

**A detector that was supposed to run and did not is a hold, not a pass.** The
opposite is how moderation quietly stops working: the model errors, every
photo sails through, and nothing in the product looks different.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from backend.ml.base import FaceResult, NudityResult


class Verdict(enum.StrEnum):
    passed = "passed"
    held = "held"  # nobody sees it until a person has
    rejected = "rejected"  # no ambiguity worth a human's time


# Above this, the detector is not guessing. Explicit content at this
# confidence does not need a person to confirm it, and making somebody look at
# it to say so is a cost with no benefit.
REJECT_NUDITY = 0.75

# Between the two, a human decides. The floor is low on purpose: the cost of a
# hold is a wait, and the cost of a miss is somebody's genitals on a profile.
HOLD_NUDITY = 0.30

# The app is 18+. Held, never rejected — see the module docstring. The margin
# is above 18 because the estimate is noisy in both directions and the point
# of the number is to catch the cases worth a second look, not to be a
# birthday check.
HOLD_UNDER_AGE = 21.0


@dataclass(slots=True)
class Screening:
    verdict: Verdict
    reason: str | None = None
    # Kept so a reviewer sees what the machine actually saw, rather than a
    # verdict with no working shown.
    detail: str | None = None

    @property
    def visible(self) -> bool:
        return self.verdict == Verdict.passed


def decide(face: FaceResult, nudity: NudityResult | None) -> Screening:
    """One verdict from everything known about a photo.

    `nudity=None` means this deployment has no explicit-content detector at
    all — a fact about the environment, not about the photo, so it is not
    grounds to hold. `NudityResult(ran=False)` means one was supposed to run
    and could not, which is.
    """
    if nudity is not None:
        if not nudity.ran:
            return Screening(Verdict.held, "screening_failed", "the detector did not complete")

        if nudity.score >= REJECT_NUDITY:
            return Screening(
                Verdict.rejected,
                "explicit_content",
                f"{', '.join(nudity.labels).lower()} at {nudity.score:.2f}",
            )
        if nudity.score >= HOLD_NUDITY:
            return Screening(
                Verdict.held,
                "possible_explicit_content",
                f"{', '.join(nudity.labels).lower()} at {nudity.score:.2f}",
            )

    # Checked after nudity, so a photo that is both goes to the queue under
    # the finding a reviewer needs to act on first.
    if face.age is not None and face.age < HOLD_UNDER_AGE:
        return Screening(
            Verdict.held,
            "possibly_underage",
            f"estimated age {face.age:.0f}",
        )

    return Screening(Verdict.passed)
