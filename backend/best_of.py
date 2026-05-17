"""Best-of-N scorer — pick the best photo(s) out of a multi-select.

User flow: select N similar photos in the gallery, hit "Best of",
the modal calls POST /images/best-of with the IDs. We score each
photo on a handful of measurable signals (sharpness, exposure, face
quality, CLIP cosine to a use-case prompt), normalize per batch so
the best in the selection always sits near 100, and return a ranked
list with per-criterion breakdown the UI can show as bars.

Three modes:

  "overall"  — single ranked list. Top result is the keeper.
  "burst"    — cluster by CLIP cosine similarity > 0.85 first, then
               return the best photo per cluster. Lets the user pick
               the keeper for each burst in one pass.
  "use_case" — composite quality × CLIP cosine to a use-case text
               prompt ("portrait" / "landscape" / "social" / ...).

All four signals are real measurements — sharpness via OpenCV
Laplacian variance on the served bytes, exposure via mean luminance
distance from mid-gray, face quality from the existing FaceDetection
rows (no extra model run), and the use-case prompt projected through
the same CLIP encoder the search route uses.

Cost: roughly 50-150ms per image (OpenCV + one CLIP prompt encode
amortized across the batch + one batched face-detection SELECT). For
N=10 photos that's well under a second.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.image import fetch_served
from backend.models import FaceDetection, Image

logger = logging.getLogger(__name__)


# Preset use-case prompts. The frontend renders these as one-tap chips.
# Users can also type any free-text prompt (handled by
# `_resolve_use_case_prompt` below) — these are just the common ones we
# want to give one-click access to.
#
# Keep each prompt short + concrete — CLIP responds better to
# "a sharp professional portrait photograph" than to "the best portrait".
USE_CASE_PROMPTS: dict[str, str] = {
    # People
    "portrait":      "a sharp professional portrait photograph of a person",
    "candid":        "a natural candid photograph with great expression",
    "group":         "a sharp group photograph with everyone visible",
    "kids":          "a sharp photograph of children with natural expression",
    "pet":           "a charming photograph of a pet animal",
    "wedding":       "a beautiful wedding photograph",
    # Scenes
    "landscape":     "a stunning wide landscape photograph",
    "sunset":        "a dramatic sunset or golden-hour photograph",
    "nature":        "a beautiful nature photograph",
    "city":          "a striking urban cityscape photograph",
    "architecture":  "a sharp architectural photograph of a building",
    "interior":      "a well-composed interior photograph of a room",
    "travel":        "a vivid travel photograph",
    # Content
    "food":          "an appetizing photograph of food",
    "product":       "a clean product photograph with good lighting",
    "document":      "a clear photograph of a document or page of text",
    "screenshot":    "a clean screenshot",
    "art":           "an artistic photograph with strong composition",
    "macro":         "a detailed close-up macro photograph",
    "sports":        "an action sports photograph with sharp motion",
    # Style
    "social":        "a vibrant engaging social-media photo",
    "print":         "a high-resolution print-quality photograph",
    "black-and-white": "a striking black-and-white photograph",
    "vintage":       "a warm vintage-style photograph",
    "minimal":       "a clean minimalist photograph",
}


# Free-text use-case prompts are wrapped with framing words so CLIP
# treats the user's nouns like a photo description. Keeps "garden" or
# "vintage car" from matching anything literal in the embedding space.
_FREE_TEXT_TEMPLATE = "a sharp well-composed photograph of {q}"
# Strip control chars + cap length. Lets through plain words, numbers,
# spaces, hyphens, underscores — same alphabet CLIP's tokenizer handles
# well. Anything else gets dropped silently.
_SAFE_PROMPT = re.compile(r"[^\w\s\-]")


def _resolve_use_case_prompt(use_case: Optional[str]) -> Optional[str]:
    """Map the `use_case` string to a CLIP prompt.

      - Empty / None      → no prompt, skip CLIP scoring.
      - Preset key match  → canned prompt from USE_CASE_PROMPTS.
      - Free text         → wrapped in _FREE_TEXT_TEMPLATE, sanitized.
    """
    if not use_case:
        return None
    key = use_case.strip().lower()
    if not key:
        return None
    if key in USE_CASE_PROMPTS:
        return USE_CASE_PROMPTS[key]
    cleaned = _SAFE_PROMPT.sub(" ", key).strip()[:80]
    if not cleaned:
        return None
    return _FREE_TEXT_TEMPLATE.format(q=cleaned)

# Cosine similarity threshold above which two photos are considered
# the same burst. Empirically: hand-held burst frames from the same
# scene land at 0.88-0.95; clearly-different photos sit below 0.80.
BURST_SIM_THRESHOLD = 0.85


@dataclass
class BestOfScore:
    image_id: UUID
    score: float                       # composite, 0–100
    breakdown: dict[str, float]        # criterion -> 0–100
    cluster_id: int                    # burst cluster; 0 if mode != burst
    reasons: list[str] = field(default_factory=list)


def _laplacian_variance(image_bytes: bytes) -> float:
    """Sharpness proxy — variance of the Laplacian over a downsampled
    grayscale image. Higher = sharper edges = less motion-blur / less
    out-of-focus.

    Returns raw variance (typically 0-2000); the caller normalizes
    across the batch so the best photo in this selection lands at 100
    regardless of absolute sharpness.
    """
    try:
        import cv2
    except ImportError:
        # OpenCV not installed — return a neutral 0 so the normalization
        # below treats sharpness as ignored. The other signals still
        # contribute to the composite.
        return 0.0
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape
    if max(h, w) > 1024:
        scale = 1024.0 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def _exposure_score(image_bytes: bytes) -> float:
    """Distance-from-ideal-mid-gray exposure proxy.

    A balanced photo's grayscale mean sits roughly 100-160 (slight
    skew above 128 for slight warmth). Heavy clipping at either tail
    (mean < 40 or > 220) tanks the score; the sweet spot tops out at
    ~95. Returns a direct 0-100 score (already normalized, doesn't
    need batch-level renormalization).
    """
    try:
        import cv2
    except ImportError:
        return 50.0
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 50.0
    mean = float(img.mean())
    if mean < 40 or mean > 220:
        return 30.0
    if 100 <= mean <= 160:
        return 95.0
    return max(30.0, 95.0 - abs(mean - 130.0) * 0.6)


def _normalize_minmax(values: list[float]) -> list[float]:
    """Stretch values to 0-100 so the highest raw score in this batch
    becomes 100 and the lowest becomes 0. Returns 50.0s when the
    range is degenerate (all values equal)."""
    if not values:
        return values
    lo, hi = min(values), max(values)
    if hi - lo < 1e-6:
        return [50.0] * len(values)
    return [100.0 * (v - lo) / (hi - lo) for v in values]


def _greedy_burst_clusters(embeddings: list[Optional[np.ndarray]]) -> list[int]:
    """Greedy single-linkage clustering on already-L2-normalized CLIP
    embeddings. Two photos cluster together if cosine sim > the
    BURST_SIM_THRESHOLD. Photos without embeddings each get their
    own singleton cluster.

    O(N^2) which is fine for N ≤ 30 (the endpoint's body cap).
    """
    n = len(embeddings)
    cluster_of = [-1] * n
    next_cluster = 0
    for i in range(n):
        if cluster_of[i] != -1:
            continue
        cluster_of[i] = next_cluster
        if embeddings[i] is None:
            next_cluster += 1
            continue
        for j in range(i + 1, n):
            if cluster_of[j] != -1 or embeddings[j] is None:
                continue
            sim = float(embeddings[i] @ embeddings[j])
            if sim > BURST_SIM_THRESHOLD:
                cluster_of[j] = next_cluster
        next_cluster += 1
    return cluster_of


def _l2_norm_or_none(vec) -> Optional[np.ndarray]:
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n < 1e-9:
        return None
    return arr / n


async def best_of(
    session: AsyncSession,
    user_id: UUID,
    image_ids: list[UUID],
    mode: str = "overall",
    use_case: Optional[str] = None,
) -> tuple[list[BestOfScore], Optional[str]]:
    """Score the given images and return them ranked best-first.

    Returns `(results, resolved_prompt)` where `resolved_prompt` is the
    actual CLIP text prompt used for use-case mode (after preset
    lookup + free-text wrapping), or None when no prompt was applied.
    The endpoint echoes this back so the UI can show "Scored by: …".

    Owner-scoped via `Image.user_id == user_id` — ids belonging to
    other users are silently dropped from the result rather than
    raising, matching the convention of the other bulk endpoints.
    """
    rows = (
        await session.execute(
            select(Image).where(
                Image.id.in_(image_ids),
                Image.user_id == user_id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not rows:
        return [], None

    # Fetch served bytes in parallel — that's the dominant per-image
    # cost (one MinIO GET each). We use the served (compressed) variant,
    # not the original, because:
    #   1. Sharpness on the served variant is the same signal at 1/4
    #      the bytes; the bandit-picked codec preserves edges.
    #   2. Originals on RAW files are huge and would dominate latency.
    async def _fetch_one(img):
        try:
            return await fetch_served(img)
        except Exception:
            logger.exception("best_of: fetch_served failed for %s", img.id)
            return None
    fetched = await asyncio.gather(*[_fetch_one(img) for img in rows])

    # Run OpenCV in a thread pool — Laplacian + resize are CPU-bound and
    # would block the event loop otherwise.
    def _score_one(blob_pair):
        if blob_pair is None:
            return 0.0, 50.0
        blob, _mime = blob_pair
        return _laplacian_variance(blob), _exposure_score(blob)
    raw_pairs = await asyncio.gather(*[
        asyncio.to_thread(_score_one, p) for p in fetched
    ])
    sharpness_raw = [p[0] for p in raw_pairs]
    exposure_raw  = [p[1] for p in raw_pairs]
    sharpness_norm = _normalize_minmax(sharpness_raw)

    # Face quality: max detection confidence per image, from the
    # existing FaceDetection rows (no new model run). Photos with no
    # detected face get a neutral 50 so they don't dominate the
    # composite when the selection is landscapes / pets.
    face_rows = (
        await session.execute(
            select(FaceDetection.image_id, FaceDetection.detection_confidence)
            .where(FaceDetection.image_id.in_([img.id for img in rows]))
        )
    ).all()
    face_max: dict = {}
    for img_id, conf in face_rows:
        face_max[img_id] = max(face_max.get(img_id, 0.0), float(conf or 0.0))
    face_scores = [
        (face_max[img.id] * 100.0) if img.id in face_max else 50.0
        for img in rows
    ]

    # Use-case CLIP cosine — only computed when mode='use_case'.
    # Accepts either a preset key (mapped via USE_CASE_PROMPTS) OR a
    # free-text string from the user (e.g. "garden", "vintage car"),
    # wrapped via _resolve_use_case_prompt into a photo-framed prompt.
    use_case_scores = [50.0] * len(rows)
    resolved_prompt: Optional[str] = None
    if mode == "use_case":
        resolved_prompt = _resolve_use_case_prompt(use_case)
    if resolved_prompt:
        try:
            from backend.vision.runtime import encode_text_cached
            from backend.vision.inference_pool import run_in_inference_pool
            prompt_vec_raw = await run_in_inference_pool(
                encode_text_cached, resolved_prompt
            )
            prompt_vec = _l2_norm_or_none(prompt_vec_raw)
            if prompt_vec is not None:
                for i, img in enumerate(rows):
                    emb = _l2_norm_or_none(img.clip_embedding)
                    if emb is None:
                        continue
                    cos = float(emb @ prompt_vec)
                    # CLIP text↔image cosine ranges roughly 0.15-0.35 for
                    # well-matched pairs. Stretch [0.15, 0.35] → [0, 100]
                    # and clip.
                    use_case_scores[i] = max(0.0, min(100.0, (cos - 0.15) * 500.0))
        except Exception:
            logger.exception("best_of: use-case CLIP scoring failed")

    # Burst clusters — only computed when mode='burst'. Embeddings
    # are also reused for the use_case path above so we don't pay
    # twice.
    if mode == "burst":
        embs = [_l2_norm_or_none(img.clip_embedding) for img in rows]
        clusters = _greedy_burst_clusters(embs)
    else:
        clusters = [0] * len(rows)

    # Composite score. Weights chosen so sharpness dominates (it's the
    # most user-visible defect) but no single signal can carry. In
    # use-case mode we shift weight onto the prompt match.
    results: list[BestOfScore] = []
    for i, img in enumerate(rows):
        breakdown: dict[str, float] = {
            "sharpness": round(sharpness_norm[i], 1),
            "exposure":  round(exposure_raw[i], 1),
            "face":      round(face_scores[i], 1),
        }
        if mode == "use_case":
            breakdown["use_case"] = round(use_case_scores[i], 1)
            weights = {"sharpness": 0.40, "exposure": 0.15, "face": 0.15, "use_case": 0.30}
        else:
            weights = {"sharpness": 0.55, "exposure": 0.25, "face": 0.20}
        score = sum(breakdown[k] * weights[k] for k in weights if k in breakdown)

        reasons: list[str] = []
        if breakdown["sharpness"] >= 85: reasons.append("sharpest in the batch")
        elif breakdown["sharpness"] < 20: reasons.append("noticeably soft")
        if breakdown["exposure"] >= 90:   reasons.append("well-exposed")
        elif breakdown["exposure"] < 40:  reasons.append("over- or under-exposed")
        if breakdown["face"] >= 85:       reasons.append("clear face")
        if mode == "use_case" and breakdown.get("use_case", 0) >= 80:
            reasons.append(f"good match for '{use_case}'")

        results.append(BestOfScore(
            image_id=img.id,
            score=round(score, 1),
            breakdown=breakdown,
            cluster_id=clusters[i],
            reasons=reasons,
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results, resolved_prompt
