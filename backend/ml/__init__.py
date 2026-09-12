"""Model registry.

One place decides whether this process gets real models or deterministic
stubs, so nothing downstream has to care. The rule:

* tests always get stubs — fast, offline, deterministic;
* anywhere else, a real model is used if its dependencies are importable,
  and a stub otherwise, with a warning naming what to install.

That last part is deliberate. A half-installed worker should say so in the log
on every job rather than crash on the first photo, because the failure a
developer actually hits is "I haven't run pip yet", not "the model is broken".
Production is the exception: there, a missing model is fatal at startup.
"""

from __future__ import annotations

from functools import lru_cache

from backend.config import settings
from backend.logging_config import get_logger
from backend.ml.base import (
    FACE_DIM,
    TEXT_DIM,
    VOICE_DIM,
    FaceResult,
    ModelUnavailable,
    Transcript,
)
from backend.ml.face import FaceEncoder, StubFaceEncoder
from backend.ml.stt import SarvamTranscriber, StubTranscriber
from backend.ml.text import StubTextEncoder, TextEncoder
from backend.ml.voice import StubVoiceEncoder, VoiceEncoder

__all__ = [
    "FACE_DIM",
    "TEXT_DIM",
    "VOICE_DIM",
    "FaceResult",
    "ModelUnavailable",
    "Transcript",
    "face_encoder",
    "text_encoder",
    "transcriber",
    "voice_encoder",
    "model_status",
]

log = get_logger(__name__)


def _pick(real_factory, stub_factory, label: str):
    if settings.is_test or not settings.real_models_enabled:
        return stub_factory()

    real = real_factory()
    if real.available:
        return real

    if settings.is_production:
        raise ModelUnavailable(real.name, real.package)

    log.warning(
        "model_unavailable_using_stub",
        model=label,
        install=real.package,
        hint="pip install -r requirements-worker.txt && python scripts/fetch_models.py",
    )
    return stub_factory()


@lru_cache(maxsize=1)
def face_encoder():
    return _pick(FaceEncoder, StubFaceEncoder, "face")


@lru_cache(maxsize=1)
def voice_encoder():
    return _pick(VoiceEncoder, StubVoiceEncoder, "voice")


@lru_cache(maxsize=1)
def text_encoder():
    return _pick(TextEncoder, StubTextEncoder, "text")


@lru_cache(maxsize=1)
def transcriber():
    if settings.is_test or not settings.real_models_enabled:
        return StubTranscriber()

    real = SarvamTranscriber()
    if real.available:
        return real
    if settings.is_production:
        raise ModelUnavailable("sarvam", "SARVAM_API_KEY")

    log.warning("stt_unavailable_using_stub", install="set SARVAM_API_KEY")
    return StubTranscriber()


def model_status() -> dict[str, str]:
    """What this process would actually use. Reported by the worker at startup
    so a misconfigured deploy is visible in the first log line, not the tenth job."""
    return {
        "face": type(face_encoder()).__name__,
        "voice": type(voice_encoder()).__name__,
        "text": type(text_encoder()).__name__,
        "stt": type(transcriber()).__name__,
    }
