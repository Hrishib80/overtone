import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Dict, List
from PIL import Image

embedder = None
audio_transcriber = None
executor = ThreadPoolExecutor(max_workers=4)
model_lock = Lock()

def load_embedder():
    global embedder
    if embedder is not None:
        return
    with model_lock:
        if embedder is None:
            from sentence_transformers import SentenceTransformer
            embedder = SentenceTransformer('clip-ViT-B-32')

def load_transcriber():
    global audio_transcriber
    if audio_transcriber is not None:
        return
    with model_lock:
        if audio_transcriber is None:
            from faster_whisper import WhisperModel
            audio_transcriber = WhisperModel('small', device='cpu', compute_type='int8')

async def _run_blocking(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, fn, *args)

def _embed_texts_sync(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    load_embedder()
    embeddings = embedder.encode(texts)
    return embeddings.tolist()

def _embed_images_sync(image_paths: List[str]) -> List[List[float]]:
    if not image_paths:
        return []
    load_embedder()
    images = [Image.open(p) for p in image_paths]
    embeddings = embedder.encode(images)
    return embeddings.tolist()

def _transcribe_sync(path: str) -> str:
    load_transcriber()
    segments, _ = audio_transcriber.transcribe(path)
    return " ".join([segment.text for segment in segments])

def _mean_vector(vectors: List[List[float]]) -> List[float] | None:
    if not vectors:
        return None
    return [sum(values) / len(values) for values in zip(*vectors)]

def _normalize_vector(vector: List[float]) -> List[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]

def _combine_vectors(weighted_vectors: List[tuple[List[float], float]]) -> List[float]:
    if not weighted_vectors:
        return [0.0] * 512
    total_weight = sum(weight for _, weight in weighted_vectors)
    combined = [0.0] * len(weighted_vectors[0][0])
    for vector, weight in weighted_vectors:
        normalized = _normalize_vector(vector)
        for index, value in enumerate(normalized):
            combined[index] += value * (weight / total_weight)
    return _normalize_vector(combined)

async def build_profile_signals(prompts: List[str], media_paths: List[str]) -> Dict[str, object]:
    prompt_embeddings = await _run_blocking(_embed_texts_sync, prompts)
    prompt_vector = _mean_vector(prompt_embeddings)

    image_paths = [path for path in media_paths if path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
    image_embeddings = await _run_blocking(_embed_images_sync, image_paths)
    visual_vector = _mean_vector(image_embeddings)

    # Video audio is transcribed as well, so short clips influence the match
    # without pretending a still image captures the whole video.
    transcript_paths = [
        path for path in media_paths
        if path.lower().endswith(('.wav', '.mp3', '.m4a', '.webm', '.ogg', '.mp4', '.mov', '.mkv'))
    ]
    transcripts = []
    for path in transcript_paths:
        transcript = await _run_blocking(_transcribe_sync, path)
        if transcript.strip():
            transcripts.append(transcript.strip())
    full_transcript = " ".join(transcripts)
    audio_vector = None
    if full_transcript:
        audio_vector = _mean_vector(await _run_blocking(_embed_texts_sync, [full_transcript]))

    weighted_vectors = []
    if prompt_vector:
        weighted_vectors.append((prompt_vector, 0.60))
    if audio_vector:
        weighted_vectors.append((audio_vector, 0.25))
    if visual_vector:
        weighted_vectors.append((visual_vector, 0.15))

    return {
        "composite_vector": _combine_vectors(weighted_vectors),
        "prompt_vector": prompt_vector,
        "audio_vector": audio_vector,
        "visual_vector": visual_vector,
        "transcript": full_transcript,
    }
