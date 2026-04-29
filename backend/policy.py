"""Compression policy.

Phase 2 ships a content-aware deterministic policy that uses the vision
classifier output to pick a CompressionPlan. This is the fix for the
screenshot-bloat the bench surfaced: lossy WebP at q=82 ballooned a
synthetic screenshot to 108% of the PNG original.

When `vision` is None (vision pipeline unavailable, e.g. ML extras not
installed), we fall back to the Phase 1 default plan from `codecs.py`.

Phase 5 replaces this module with a contextual bandit (LinUCB).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.codecs import CompressionPlan, pick_default_plan


# Content types that should be compressed losslessly. Lossy codecs introduce
# entropy where there is none, blowing up size on flat/text content.
LOSSLESS_CONTENT_TYPES = frozenset({"screenshot", "document", "illustration", "icon"})

# Below this confidence we don't trust the classifier and fall through to
# the photo default.
MIN_CONTENT_CONFIDENCE = 0.55


@dataclass(frozen=True)
class VisionContext:
    """Subset of VisionResult that the policy actually needs.

    Kept as a separate dataclass so policy/tests don't transitively import torch
    via `backend.vision.pipeline`.
    """

    content_type: str
    content_confidence: float
    face_likelihood: float = 0.0


def pick_plan(
    vision: Optional[VisionContext],
    width: int,
    height: int,
    byte_size: int,
) -> CompressionPlan:
    if vision is None or vision.content_confidence < MIN_CONTENT_CONFIDENCE:
        return pick_default_plan("", width, height, byte_size)

    if vision.content_type in LOSSLESS_CONTENT_TYPES:
        return CompressionPlan(
            codec="webp",
            quality=100,
            max_dim=None,
            lossless=True,
        )

    # photo branch — same as Phase 1 default.
    return CompressionPlan(codec="webp", quality=82, max_dim=4096, lossless=False)
