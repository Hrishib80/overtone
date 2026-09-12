"""Download the self-hosted models Overtone's worker needs.

Run once per machine / per container build:

    python scripts/fetch_models.py            # all three
    python scripts/fetch_models.py face voice # just those

Speech-to-text is NOT downloaded here. Telugu accuracy is the deciding factor
and the best options are hosted APIs (Sarvam, Google Chirp); see DEPLOYMENT.md.
If you later self-host Indic ASR, add ai4bharat/indicwhisper here.

Models land in the usual caches (~/.insightface, ~/.cache/huggingface), so a
Docker build should run this in the image layer, not at container start.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# BGE-M3 is the accurate multilingual choice but it is a large download.
# multilingual-e5-base covers en/hi/te acceptably at a quarter of the size —
# switch here if image size matters more than retrieval quality.
TEXT_MODEL = os.environ.get("OVERTONE_TEXT_MODEL", "BAAI/bge-m3")

MODELS = {
    "face": ("InsightFace buffalo_l (RetinaFace detect + ArcFace r50 embed)", "~280 MB"),
    "voice": ("SpeechBrain ECAPA-TDNN speaker embedding", "~80 MB"),
    "text": (f"{TEXT_MODEL} multilingual text embedding", "~2.2 GB" if "bge-m3" in TEXT_MODEL else "~1.1 GB"),
}


def fetch_face() -> None:
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    print("  detection + 512-d ArcFace recognition ready")


def fetch_voice() -> None:
    from speechbrain.inference.speaker import EncoderClassifier

    EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / ".cache" / "speechbrain" / "ecapa"),
        run_opts={"device": "cpu"},
    )
    print("  192-d speaker embedding ready (language-independent)")


def fetch_text() -> None:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(TEXT_MODEL, device="cpu")
    dim = model.get_sentence_embedding_dimension()
    print(f"  {dim}-d multilingual embedding ready (en / hi / te in one space)")


FETCHERS = {"face": fetch_face, "voice": fetch_voice, "text": fetch_text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("which", nargs="*", choices=[*MODELS, []], default=list(MODELS),
                        help="which models to fetch (default: all)")
    args = parser.parse_args()
    targets = args.which or list(MODELS)

    failed = []
    for name in targets:
        label, size = MODELS[name]
        print(f"\n[{name}] {label}  ({size})")
        try:
            FETCHERS[name]()
        except ImportError as exc:
            print(f"  MISSING DEPENDENCY: {exc}", file=sys.stderr)
            print("  install worker requirements first: pip install -r requirements-worker.txt", file=sys.stderr)
            failed.append(name)
        except Exception as exc:  # noqa: BLE001 - report and continue to the next model
            print(f"  FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed.append(name)

    if failed:
        print(f"\n{len(failed)} of {len(targets)} failed: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"\nAll {len(targets)} model(s) cached.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
