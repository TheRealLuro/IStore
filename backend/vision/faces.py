"""Face detection + embedding (Phase 4).

Pass B of the vision pipeline. Runs RetinaFace (detection) and ArcFace
(embedding) via the `insightface` library, gated on user consent. Lazy-loaded
the same way as the CLIP pipeline so the FastAPI app doesn't import torch /
onnxruntime unless the worker needs them.

Install: `pip install insightface onnxruntime` (CPU) or `onnxruntime-gpu`.
The `buffalo_l` model pack is downloaded automatically on first use (~290 MB).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO

import numpy as np
from PIL import Image as PILImage


@dataclass
class DetectedFace:
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    detection_confidence: float
    embedding: list[float]   # 512-d ArcFace vector, L2-normalized
    crop_jpeg: bytes         # JPEG-encoded face crop for the gallery


@lru_cache(maxsize=1)
def _get_app():
    return _build_app(det_thresh=0.3)


@lru_cache(maxsize=1)
def _get_app_low():
    """Lower-threshold detector used by the D8 re-detect cascade. We
    cache separately so the user-signal re-detection doesn't re-init
    `buffalo_l` every request."""
    return _build_app(det_thresh=0.15)


def _build_app(det_thresh: float):
    import insightface

    # `buffalo_l` ships RetinaFace (det) + ArcFace (recognition).
    app = insightface.app.FaceAnalysis(
        name="buffalo_l",
        allowed_modules=["detection", "recognition"],
    )
    # ctx_id=0 selects GPU 0 if onnxruntime-gpu sees CUDA, else falls to CPU.
    # det_thresh lowered from the 0.5 default — the gallery includes B&W and
    # heavy-crop photos where buffalo_l hovers in the 0.3–0.45 range. False
    # positives are filtered downstream by the recognition step (cosine
    # threshold against the gallery), so we'd rather over-detect than miss.
    app.prepare(ctx_id=0, det_size=(640, 640), det_thresh=det_thresh)
    return app


def _to_bgr_array(raw_bytes: bytes) -> np.ndarray:
    with PILImage.open(BytesIO(raw_bytes)) as im:
        im = im.convert("RGB")
    arr = np.asarray(im)
    return arr[:, :, ::-1].copy()  # RGB → BGR for insightface


def _crop_jpeg(rgb_arr: np.ndarray, x: int, y: int, w: int, h: int) -> bytes:
    pad = max(8, int(0.15 * max(w, h)))
    H, W = rgb_arr.shape[:2]
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(W, x + w + pad)
    y1 = min(H, y + h + pad)
    crop = rgb_arr[y0:y1, x0:x1]
    im = PILImage.fromarray(crop)
    out = BytesIO()
    im.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


def detect_and_embed(raw_bytes: bytes, *, low_threshold: bool = False) -> list[DetectedFace]:
    """Find every face in the image and return detection + 512-d embedding.

    `low_threshold=True` runs the secondary cascade detector at
    `det_thresh=0.15` — used by D8 (user manually flags a photo as
    containing a person and the default detector missed them).
    """
    app = _get_app_low() if low_threshold else _get_app()
    bgr = _to_bgr_array(raw_bytes)
    rgb = bgr[:, :, ::-1]
    faces = app.get(bgr)

    out: list[DetectedFace] = []
    for f in faces:
        bbox = f.bbox.astype(int)  # [x0, y0, x1, y1]
        x0, y0, x1, y1 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        w, h = max(1, x1 - x0), max(1, y1 - y0)

        emb = f.normed_embedding.astype(np.float32)  # already L2-normalized
        out.append(
            DetectedFace(
                bbox_x=x0,
                bbox_y=y0,
                bbox_w=w,
                bbox_h=h,
                detection_confidence=float(f.det_score),
                embedding=emb.tolist(),
                crop_jpeg=_crop_jpeg(rgb, x0, y0, w, h),
            )
        )
    return out


def _mediapipe_detect(raw_bytes: bytes) -> list[tuple[int, int, int, int, float]]:
    """Mediapipe face_detection fallback used by the D8 cascade.

    Returns a list of `(x, y, w, h, confidence)` boxes without embeddings.
    The caller (`backend.faces_pipeline`) re-runs ArcFace embedding on
    each crop before storing — so a mediapipe-detected face still ends
    up in the gallery's person clusters.

    Returns `[]` if mediapipe isn't installed or fails.
    """
    try:
        import mediapipe as mp  # type: ignore
    except ImportError:
        return []
    try:
        bgr = _to_bgr_array(raw_bytes)
        rgb = bgr[:, :, ::-1]
        H, W = rgb.shape[:2]
        # model_selection=1 → ranges up to 5 m, better for portrait /
        # close-crop. min_detection_confidence=0.3 is intentionally low —
        # this is the "user said there's a person here, look harder" path.
        with mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.3
        ) as detector:
            result = detector.process(rgb)
        if not result or not result.detections:
            return []
        boxes: list[tuple[int, int, int, int, float]] = []
        for det in result.detections:
            bbox = det.location_data.relative_bounding_box
            x = int(max(0, bbox.xmin * W))
            y = int(max(0, bbox.ymin * H))
            w = int(min(W - x, bbox.width * W))
            h = int(min(H - y, bbox.height * H))
            if w <= 0 or h <= 0:
                continue
            score = float(det.score[0]) if det.score else 0.3
            boxes.append((x, y, w, h, score))
        return boxes
    except Exception:
        return []


def detect_with_cascade(raw_bytes: bytes) -> tuple[list[DetectedFace], str]:
    """D8 user-signal cascade:

        RetinaFace 0.3 → RetinaFace 0.15 → mediapipe face_mesh detection
        → empty (caller falls back to user-drawn-box flow).

    Returns `(detections, stage)` where `stage` is the label that
    actually produced results ("retina-0.3" / "retina-0.15" /
    "mediapipe" / "empty"). Mediapipe-detected boxes carry zeros for
    embedding/crop — the caller is expected to re-run ArcFace embedding
    on each crop before persisting.
    """
    primary = detect_and_embed(raw_bytes)
    if primary:
        return primary, "retina-0.3"
    secondary = detect_and_embed(raw_bytes, low_threshold=True)
    if secondary:
        return secondary, "retina-0.15"
    # Mediapipe gives boxes only — no ArcFace embedding. Wrap them as
    # DetectedFace with an empty embedding so the API contract stays
    # uniform; the persistence layer treats empty embedding as
    # "manual / fallback face, requires labeling."
    mp_boxes = _mediapipe_detect(raw_bytes)
    if mp_boxes:
        bgr = _to_bgr_array(raw_bytes)
        rgb = bgr[:, :, ::-1]
        out = [
            DetectedFace(
                bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h,
                detection_confidence=score,
                embedding=[],  # caller may run ArcFace on the crop
                crop_jpeg=_crop_jpeg(rgb, x, y, w, h),
            )
            for (x, y, w, h, score) in mp_boxes
        ]
        return out, "mediapipe"
    return [], "empty"
