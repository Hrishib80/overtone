"""Multilingual text embedding.

BGE-M3 puts English, Hindi and Telugu into one shared space natively. That is
why there is no translation step: machine translation is lossy exactly where
these users live — code-mixed Hinglish and Telugu-English — and it would add a
cost and a failure mode to every profile to reach a space this model already
provides.
"""

from __future__ import annotations

from backend.config import settings
from backend.ml.base import TEXT_DIM, DeterministicEncoder, Encoder, l2_normalise

DEFAULT_MODEL = "BAAI/bge-m3"


class TextEncoder(Encoder):
    name = "bge-m3"
    requires = ("sentence_transformers",)
    package = "sentence-transformers"
    dim = TEXT_DIM

    def _load(self) -> object:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(getattr(settings, "text_model", None) or DEFAULT_MODEL, device="cpu")

    def encode(self, text: str) -> list[float]:
        vector = self.model.encode(text, normalize_embeddings=True)
        return l2_normalise([float(v) for v in vector])

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(texts, normalize_embeddings=True)
        return [l2_normalise([float(v) for v in row]) for row in vectors]


class StubTextEncoder(DeterministicEncoder):
    def __init__(self) -> None:
        super().__init__(TEXT_DIM, "text")

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]
