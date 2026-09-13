"""Face detection and embedding.

ArcFace, not CLIP. CLIP embeds scene and style — it will call two photos
similar because both are outdoors in a blue shirt, which is useless for
pairing people who actually resemble each other. ArcFace is trained so that
distance in the embedding *is* facial resemblance, which is exactly the
quantity the pair mechanic needs.

Detection comes with it, so the quality gate costs nothing extra.
"""

from __future__ import annotations

import io

from backend.ml.base import FACE_DIM, DeterministicEncoder, Encoder, FaceResult, l2_normalise

# A face smaller than this in the source image has too little detail for a
# stable embedding — the vector ends up describing JPEG noise.
MIN_FACE_PIXELS = 80
MIN_DETECTION_SCORE = 0.55


class FaceEncoder(Encoder):
    name = "insightface-buffalo_l"
    requires = ("insightface", "onnxruntime", "PIL", "numpy")
    package = "insightface, onnxruntime"
    dim = FACE_DIM

    def _load(self) -> object:
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(640, 640))
        return app

    def analyse(self, image_bytes: bytes) -> FaceResult:
        """Detect, gate, and embed. One result per photo."""
        import numpy as np
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception:
            return FaceResult(ok=False, reason="unreadable_image")

        # InsightFace expects BGR, the order OpenCV uses.
        array = np.asarray(image)[:, :, ::-1]
        faces = self.model.get(array)

        if not faces:
            return FaceResult(ok=False, reason="no_face", face_count=0)
        if len(faces) > 1:
            return FaceResult(ok=False, reason="multiple_faces", face_count=len(faces))

        face = faces[0]
        x1, y1, x2, y2 = (int(v) for v in face.bbox)
        width, height = x2 - x1, y2 - y1

        if min(width, height) < MIN_FACE_PIXELS:
            return FaceResult(ok=False, reason="face_too_small", face_count=1, box=(x1, y1, x2, y2))
        if float(face.det_score) < MIN_DETECTION_SCORE:
            return FaceResult(ok=False, reason="low_confidence", face_count=1, box=(x1, y1, x2, y2))

        return FaceResult(
            ok=True,
            embedding=l2_normalise([float(v) for v in face.normed_embedding]),
            face_count=1,
            box=(x1, y1, x2, y2),
            det_score=float(face.det_score),
            # buffalo_l carries a genderage model, so this is already computed
            # by the time we get here. Absent from a pack without one.
            age=float(face.age) if getattr(face, "age", None) is not None else None,
        )


class StubFaceEncoder(DeterministicEncoder):
    """Accepts anything that is a plausible image and returns a stable vector."""

    def __init__(self) -> None:
        super().__init__(FACE_DIM, "face")

    def analyse(self, image_bytes: bytes) -> FaceResult:
        if not image_bytes:
            return FaceResult(ok=False, reason="unreadable_image")
        return FaceResult(
            ok=True,
            embedding=self.encode(image_bytes),
            face_count=1,
            det_score=0.99,
        )
