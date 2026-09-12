"""Model interfaces.

Everything heavy is imported inside `load()`, never at module import. That
means the API tier, the test suite and Alembic can all import this package
without torch, onnxruntime or a 2GB model on disk — and a missing dependency
produces a sentence naming the package to install rather than an ImportError
traceback from three libraries down.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Final

from backend.logging_config import get_logger

log = get_logger(__name__)

# Fixed by the chosen models; the database columns are sized to match.
FACE_DIM: Final = 512  # InsightFace buffalo_l / ArcFace r50
VOICE_DIM: Final = 192  # SpeechBrain ECAPA-TDNN
TEXT_DIM: Final = 1024  # BAAI/bge-m3


class ModelUnavailable(RuntimeError):
    """A model was asked for that this process cannot load."""

    def __init__(self, name: str, package: str, cause: BaseException | None = None) -> None:
        super().__init__(
            f"{name} is not available in this process. Install the worker "
            f"dependencies ({package}) and run `python scripts/fetch_models.py`."
        )
        self.name = name
        self.package = package
        self.__cause__ = cause


@dataclass(slots=True)
class FaceResult:
    """One detected face, or the reason there isn't exactly one."""

    ok: bool
    reason: str | None = None
    embedding: list[float] = field(default_factory=list)
    face_count: int = 0
    box: tuple[int, int, int, int] | None = None
    det_score: float = 0.0


@dataclass(slots=True)
class Transcript:
    text: str
    language: str | None = None
    # How sure the provider is about the language. Low confidence on an empty
    # transcript usually means the clip is noise rather than speech.
    language_confidence: float | None = None
    duration_ms: int | None = None


def l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return vector if norm == 0 else [v / norm for v in vector]


class Encoder(ABC):
    """Base for anything that turns bytes or text into a vector."""

    name: str = "encoder"
    package: str = "requirements-worker.txt"
    # Modules that must be importable for this encoder to work. Checked with
    # find_spec so `available` stays free — constructing the model can mean a
    # multi-gigabyte download, which no status check should ever trigger.
    requires: tuple[str, ...] = ()
    dim: int = 0

    def __init__(self) -> None:
        self._model = None

    @abstractmethod
    def _load(self) -> object:
        """Import and construct the underlying model. Called once, lazily."""

    @property
    def model(self) -> object:
        if self._model is None:
            try:
                self._model = self._load()
            except ImportError as exc:
                raise ModelUnavailable(self.name, self.package, exc) from exc
            log.info("model_loaded", model=self.name, dim=self.dim)
        return self._model

    @property
    def available(self) -> bool:
        """Are the dependencies present? Does not load or download anything."""
        return all(importlib.util.find_spec(module) is not None for module in self.requires)


class DeterministicEncoder(Encoder):
    """A stand-in that hashes its input into a unit vector.

    Used by the test suite and by any environment without the real models. It
    is deterministic — the same input always gives the same vector, and similar
    inputs are NOT close, which is the honest behaviour: it proves the pipeline
    moves data end to end without pretending to be a semantic model.
    """

    name = "deterministic-stub"

    def __init__(self, dim: int, label: str) -> None:
        super().__init__()
        self.dim = dim
        self.name = f"{label}-stub"

    requires = ()

    def _load(self) -> object:
        return "stub"

    def encode(self, payload: bytes | str) -> list[float]:
        raw = payload.encode() if isinstance(payload, str) else payload
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(raw + counter.to_bytes(4, "big")).digest()
            out.extend((b - 127.5) / 127.5 for b in digest)
            counter += 1
        return l2_normalise(out[: self.dim])
