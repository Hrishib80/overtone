"""Speech-to-text via Sarvam.

Chosen on Telugu: general-purpose providers handle English and Hindi well and
degrade noticeably on Telugu, which is a first-class language for this campus
rather than an afterthought. Sarvam also keeps the audio in India, which
shortens the data-protection story.

This is a plain HTTP client — no heavy dependency, so it lives happily in any
process. The API tier still never calls it; transcription is worker work.
"""

from __future__ import annotations

import httpx

from backend.config import settings
from backend.logging_config import get_logger
from backend.ml.base import Transcript

log = get_logger(__name__)

# The three the pipeline supports, in Sarvam's BCP-47 form.
LANGUAGE_CODES = {
    "en": "en-IN",
    "hi": "hi-IN",
    "te": "te-IN",
}
AUTO_DETECT = "unknown"


class SttUnavailable(RuntimeError):
    pass


class SarvamTranscriber:
    name = "sarvam-saarika"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else settings.sarvam_api_key
        self.base_url = (base_url or settings.sarvam_base_url).rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def transcribe(
        self, audio_bytes: bytes, *, filename: str = "clip.webm", language: str | None = None
    ) -> Transcript:
        if not self.available:
            raise SttUnavailable(
                "SARVAM_API_KEY is not set; speech-to-text cannot run. Set it in the worker environment."
            )

        # Let the provider identify the language rather than guessing from the
        # profile: students code-switch mid-sentence, and a wrong hint costs
        # more accuracy than no hint.
        code = LANGUAGE_CODES.get(language or "", AUTO_DETECT)

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/speech-to-text",
                headers={"api-subscription-key": self.api_key},
                files={"file": (filename, audio_bytes, "audio/webm")},
                data={"model": "saarika:v2", "language_code": code},
            )
            response.raise_for_status()
            body = response.json()

        detected = (body.get("language_code") or "").split("-")[0] or None
        log.info("transcribed", chars=len(body.get("transcript") or ""), language=detected)

        return Transcript(
            text=(body.get("transcript") or "").strip(),
            language=detected,
        )


class StubTranscriber:
    """Returns a fixed, obviously-fake transcript so the pipeline can be tested
    end to end without a key or a network call."""

    name = "stub-transcriber"
    available = True

    async def transcribe(
        self, audio_bytes: bytes, *, filename: str = "clip.webm", language: str | None = None
    ) -> Transcript:
        return Transcript(
            text=f"[stub transcript of {len(audio_bytes)} bytes]",
            language=language or "en",
        )
