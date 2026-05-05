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
    app.prepare(ctx_id=0, det_size=(640, 640), det_thresh=0.3)
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


def detect_and_embed(raw_bytes: bytes) -> list[DetectedFace]:
    """Find every face in the image and return detection + 512-d embedding."""
    app = _get_app()
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
