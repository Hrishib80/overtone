"""Speaker embedding — how a voice sounds, independent of what it says.

ECAPA-TDNN models vocal characteristics rather than words, so it works
identically across English, Hindi and Telugu with no extra machinery. That is
the whole reason the voice axis is split in two: this half needs no
transcription at all.
"""

from __future__ import annotations

import io
from pathlib import Path

from backend.ml.base import VOICE_DIM, DeterministicEncoder, Encoder, l2_normalise

TARGET_SAMPLE_RATE = 16000


class VoiceEncoder(Encoder):
    name = "ecapa-tdnn"
    requires = ("speechbrain", "torch", "torchaudio", "soundfile")
    package = "speechbrain, torchaudio"
    dim = VOICE_DIM

    def _load(self) -> object:
        from speechbrain.inference.speaker import EncoderClassifier

        return EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(Path.home() / ".cache" / "speechbrain" / "ecapa"),
            run_opts={"device": "cpu"},
        )

    def encode(self, audio_bytes: bytes) -> list[float]:
        import soundfile as sf
        import torch

        waveform, rate = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
        signal = torch.from_numpy(waveform.mean(axis=1)).unsqueeze(0)

        if rate != TARGET_SAMPLE_RATE:
            import torchaudio

            signal = torchaudio.functional.resample(signal, rate, TARGET_SAMPLE_RATE)

        with torch.no_grad():
            embedding = self.model.encode_batch(signal).squeeze()
        return l2_normalise([float(v) for v in embedding])


class StubVoiceEncoder(DeterministicEncoder):
    def __init__(self) -> None:
        super().__init__(VOICE_DIM, "voice")
