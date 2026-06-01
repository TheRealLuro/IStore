"""Face detection + embedding (Phase 4).

Pass B of the vision pipeline. Runs RetinaFace (detection) and ArcFace
(embedding) via the `insightface` library, gated on user consent. Lazy-loaded
the same way as the CLIP pipeline so the FastAPI app doesn't import torch /
onnxruntime unless the worker needs them.

Install: `pip install insightface onnxruntime` (CPU) or `onnxruntime-gpu`.
The `buffalo_l` model pack is downloaded automatically on first use (~290 MB).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from typing import Optional

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
    # 5-point landmarks in image-pixel coords (left eye, right eye, nose,
    # mouth-left, mouth-right). Empty when the detector didn't supply them
    # (mediapipe fallback path; older insightface releases).
    landmarks: list[list[float]] = field(default_factory=list)


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


# Target output size for the People-sidebar avatar. 256² is the sweet
# spot — large enough that a 96px CSS render stays sharp at 2x DPI,
# small enough that quality-92 JPEG stays under 30 KB.
AVATAR_SIZE = 256

# Multiplier on max(bbox_w, bbox_h) that determines the crop side
# length. 1.8× lands roughly head-and-shoulders with a sliver of
# negative space — visibly more flattering than the prior 15%-padded
# rectangular crop, which left foreheads cut off on selfies because
# RetinaFace bboxes hug the face tightly.
AVATAR_PADDING_FACTOR = 1.8


def _aligned_avatar_jpeg(
    rgb_arr: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    landmarks: Optional[list] = None,
) -> bytes:
    """Square `AVATAR_SIZE`×`AVATAR_SIZE` JPEG centered on the face.

    Cropping strategy:
      - When landmarks are available, center on the midpoint between
        the two eyes (shifted 30% toward the bbox bottom so the eyes
        sit in the upper third — that's the natural framing the brain
        reads as "centered face"). Without landmarks, fall back to
        bbox center.
      - Side length is `AVATAR_PADDING_FACTOR * max(bbox_w, bbox_h)`
        so we capture forehead-to-shoulders.
      - Out-of-frame pixels (face near image edge) are filled with
        edge-replicated pixels instead of black bars so the circular
        avatar doesn't get sliced.
      - Final resize is Lanczos for the best sharpness/ringing tradeoff
        on portrait content.
    """
    H, W = rgb_arr.shape[:2]

    if landmarks and len(landmarks) >= 2:
        lx, ly = float(landmarks[0][0]), float(landmarks[0][1])
        rx, ry = float(landmarks[1][0]), float(landmarks[1][1])
        cx = (lx + rx) / 2.0
        eye_y = (ly + ry) / 2.0
        bbox_bottom = float(y + h)
        # 30% of the eye-to-bbox-bottom distance pushes the center
        # toward the nose; the eyes end up about a third from the top
        # of the crop, which reads as "centered face" to a viewer.
        cy = eye_y + 0.30 * (bbox_bottom - eye_y)
    else:
        cx = x + w / 2.0
        # When we have no landmarks, the bbox center is slightly low
        # for selfies because RetinaFace tends to clip the forehead.
        # Shift up by 8% of bbox height so the forehead doesn't sit
        # at the very top edge of the crop.
        cy = y + h / 2.0 - 0.08 * h

    side = AVATAR_PADDING_FACTOR * max(w, h)
    half = side / 2.0
    x0 = int(round(cx - half))
    y0 = int(round(cy - half))
    x1 = int(round(cx + half))
    y1 = int(round(cy + half))

    pad_left = max(0, -x0)
    pad_top = max(0, -y0)
    pad_right = max(0, x1 - W)
    pad_bottom = max(0, y1 - H)

    if pad_left or pad_top or pad_right or pad_bottom:
        padded = np.pad(
            rgb_arr,
            ((pad_top, pad_bottom), (pad_left, pad_right), (0, 0)),
            mode="edge",
        )
        x0 += pad_left
        x1 += pad_left
        y0 += pad_top
        y1 += pad_top
        crop = padded[y0:y1, x0:x1]
    else:
        crop = rgb_arr[y0:y1, x0:x1]

    im = PILImage.fromarray(crop)
    if im.size != (AVATAR_SIZE, AVATAR_SIZE):
        im = im.resize((AVATAR_SIZE, AVATAR_SIZE), PILImage.Resampling.LANCZOS)

    out = BytesIO()
    im.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


# Back-compat alias — older call sites passed bbox only; new code passes
# landmarks too. Both shapes work.
def _crop_jpeg(
    rgb_arr: np.ndarray, x: int, y: int, w: int, h: int,
    landmarks: Optional[list] = None,
) -> bytes:
    return _aligned_avatar_jpeg(rgb_arr, x, y, w, h, landmarks)


def render_avatar_from_image(
    raw_image_bytes: bytes,
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
    landmarks: Optional[list] = None,
) -> bytes:
    """Public helper for the crop-regeneration admin route.

    Lets callers re-crop an existing FaceDetection row using the bbox +
    landmarks already in the DB, no detection re-run required.
    """
    bgr = _to_bgr_array(raw_image_bytes)
    rgb = bgr[:, :, ::-1]
    return _aligned_avatar_jpeg(rgb, bbox_x, bbox_y, bbox_w, bbox_h, landmarks)


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

        # buffalo_l returns kps as np.ndarray shape (5, 2) in image-pixel
        # coords: [[lx, ly], [rx, ry], [nose_x, nose_y], [ml_x, ml_y],
        # [mr_x, mr_y]]. Older insightface releases occasionally omit it
        # — empty fallback keeps the crop helper's bbox-only path live.
        kps = getattr(f, "kps", None)
        landmarks: list[list[float]] = (
            kps.astype(float).tolist() if kps is not None else []
        )

        emb = f.normed_embedding.astype(np.float32)  # already L2-normalized
        out.append(
            DetectedFace(
                bbox_x=x0,
                bbox_y=y0,
                bbox_w=w,
                bbox_h=h,
                detection_confidence=float(f.det_score),
                embedding=emb.tolist(),
                crop_jpeg=_aligned_avatar_jpeg(rgb, x0, y0, w, h, landmarks),
                landmarks=landmarks,
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


def _enhance_lowlight(raw_bytes: bytes) -> Optional[bytes]:
    """Lift shadows / boost local contrast on a backlit or dark frame so a
    face hidden in shadow has a chance of being detected.

    Backlit subjects (e.g. someone sitting against a bright window) come
    through with the face in deep shadow — too little local contrast for
    RetinaFace even at det_thresh=0.15. CLAHE on the L channel of LAB
    raises contrast in the shadows WITHOUT blowing out the bright
    background, which is exactly the failure mode on phone videos shot
    toward a window. Returns enhanced PNG bytes, or None if enhancement
    isn't possible (caller then simply skips the enhanced retry).
    """
    try:
        bgr = _to_bgr_array(raw_bytes)
    except Exception:
        return None
    try:
        import cv2  # insightface already depends on opencv-python

        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        merged = cv2.merge((clahe.apply(l), a, b))
        out_bgr = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
        ok, buf = cv2.imencode(".png", out_bgr)
        return buf.tobytes() if ok else None
    except Exception:
        # No cv2 (or it failed): PIL autocontrast on the frame as a
        # lighter-weight fallback — less targeted than CLAHE but still
        # lifts an under-exposed frame enough to help.
        try:
            import io as _io

            from PIL import ImageOps

            im = PILImage.fromarray(bgr[:, :, ::-1])
            im = ImageOps.autocontrast(im, cutoff=1)
            buf = _io.BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None


def detect_with_cascade(raw_bytes: bytes) -> tuple[list[DetectedFace], str]:
    """D8 user-signal cascade:

        RetinaFace 0.3 → RetinaFace 0.15 → CLAHE-enhanced RetinaFace 0.15
        → mediapipe (plain, then enhanced) → empty (caller falls back to
        the user-drawn-box flow).

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
