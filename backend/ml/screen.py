"""Explicit-content detection.

NudeNet: an ONNX detector, small enough to run on the same CPU worker as
everything else, returning labelled boxes rather than one opaque "nsfw"
probability. The labels matter — "exposed buttocks" and "exposed genitalia"
are different findings and a reviewer reading the queue deserves to know
which one fired.

It is *detection only*. What to do about a score is a policy question and
lives in `backend/screening.py`, away from the model, so the thresholds can
be argued about and changed without touching inference.

Deliberately not doing age here. buffalo_l already carries a genderage model
and the face pass already ran, so the estimate arrives on `FaceResult` for
free; loading a second model to answer the same question would be a second
copy of a network in memory for nothing.
"""

from __future__ import annotations

import importlib.util
import io

from backend.logging_config import get_logger
from backend.ml.base import NudityResult

log = get_logger(__name__)

# The classes that mean skin that a dating profile should not be showing.
# NudeNet also reports covered and non-explicit classes (FACE, FEET, an arm);
# those are not findings and are ignored rather than scored low.
EXPLICIT_LABELS = frozenset(
    {
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "FEMALE_BREAST_EXPOSED",
        "BUTTOCKS_EXPOSED",
        "ANUS_EXPOSED",
    }
)


class NudityScreener:
    name = "nudenet"
    package = "nudenet"
    requires = ("nudenet", "PIL", "numpy")
    # This deployment screens. The flag exists because "the detector found
    # nothing" and "this environment has no detector" must not collapse into
    # the same answer — the first is a finding, the second is an absence, and
    # a moderation system that treats an absence as approval is worse than one
    # that is visibly switched off.
    screens = True

    def __init__(self) -> None:
        self._detector = None

    @property
    def available(self) -> bool:
        """Dependencies present? Loads and downloads nothing — same rule as
        the encoders, for the same reason."""
        return all(importlib.util.find_spec(module) is not None for module in self.requires)

    @property
    def detector(self):
        if self._detector is None:
            from nudenet import NudeDetector

            self._detector = NudeDetector()
            log.info("model_loaded", model=self.name)
        return self._detector

    def screen(self, image_bytes: bytes) -> NudityResult:
        import numpy as np
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception:
            # An unreadable image is the face gate's finding to report, not
            # this one's. Saying nothing here leaves that message intact.
            return NudityResult(ran=False)

        try:
            findings = self.detector.detect(np.asarray(image))
        except Exception:
            # A detector that fell over must not take the upload with it — but
            # it must not wave the photo through either. `ran=False` from a
            # screener that was *supposed* to run means "we tried and do not
            # know", and screening.verdict holds on it.
            log.exception("nudity_screen_failed")
            return NudityResult(ran=False)

        hits = [f for f in findings if f.get("class") in EXPLICIT_LABELS]
        return NudityResult(
            ran=True,
            score=max((float(f.get("score", 0.0)) for f in hits), default=0.0),
            labels=sorted({str(f["class"]) for f in hits}),
        )


class StubScreener:
    """No detector here, and it says so rather than approving.

    `screens = False` is what keeps development and the test suite working
    without pretending a photo was checked. The policy in
    `backend/screening.py` never sees a result from this at all — it is told
    that screening is off, which is a different sentence from "clean".
    """

    name = "screen-stub"
    package = "nudenet"
    requires = ()
    available = True
    screens = False

    def screen(self, image_bytes: bytes) -> NudityResult:
        return NudityResult(ran=False)
