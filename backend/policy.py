"""Compression policy.

Two layers:

  1. **Hard rule (always wins)** — content the user can't tolerate ANY loss
     on (screenshots, documents, illustrations, icons) is forced to lossless
     WebP regardless of bandit pulls. This is the screenshot-bloat fix from
     Phase 2 and remains policy because the bandit shouldn't ever learn
     "WebP q=55 on a PNG screenshot" is acceptable — it materially is, but
     users will rate it bottom-of-the-barrel.

  2. **LinUCB bandit (Phase 5)** — for photo content, we hand off to
     `backend.bandit.pick_arm` which selects from the 80-arm grid using the
     user's accumulated (A, b) statistics with cold-start fallback to a
     shared prior. The picked `arm_id` and the 32-d context vector are
     persisted on `images` so the Phase 6 trainer can replay observations.

When the ML extras are missing (no CLIP/insightface) and `vision` is None
we fall back to the Phase 1 default plan from `codecs.py` — bypassing the
bandit because we have no useful context features to feed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

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
    """Rule-based fallback. Used when the bandit has no useful context
    (vision pipeline disabled). Same logic as Phase 2 — preserved for the
    `tests/test_policy.py` contract and as a safety net."""
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


@dataclass(frozen=True)
class PolicyDecision:
    plan: CompressionPlan
    arm_id: Optional[int]
    context_features: Optional[list[float]]


async def pick_plan_with_bandit(
    *,
    session: AsyncSession,
    user_id,
    vision: Optional[VisionContext],
    scene_label: Optional[str],
    indoor_outdoor: Optional[str],
    clip_embedding: Optional[list[float]],
    width: int,
    height: int,
    byte_size: int,
    mime_in: Optional[str],
) -> PolicyDecision:
    """Bandit-aware compression decision.

    Hard rule wins for content that should never be lossily encoded
    (screenshots, documents, etc). For photos, the LinUCB bandit picks
    from the 80-arm grid; we persist `arm_id` + the 32-d context features
    on the image row so the Phase 6 trainer can update sufficient
    statistics when feedback (explicit ratings, implicit signals) arrives.
    """
    # Hard rule: animated GIFs survive end-to-end as the original bytes.
    # Every other codec we have is single-frame, so re-encoding a GIF
    # would silently drop every frame after the first. We can't tell
    # from mime alone whether it's animated, but treating all GIFs as
    # passthrough is the safe choice — they're already compressed and
    # the user explicitly chose this format. Bandit never gets a shot.
    if (mime_in or "").lower() == "image/gif":
        return PolicyDecision(
            plan=CompressionPlan(
                codec="passthrough",
                quality=100,
                max_dim=None,
                passthrough_mime="image/gif",
                passthrough_ext="gif",
            ),
            arm_id=None,
            context_features=None,
        )

    # Hard rule first — bandit should never have authority over screenshots.
    if (
        vision is not None
        and vision.content_confidence >= MIN_CONTENT_CONFIDENCE
        and vision.content_type in LOSSLESS_CONTENT_TYPES
    ):
        return PolicyDecision(
            plan=CompressionPlan(
                codec="webp", quality=100, max_dim=None, lossless=True
            ),
            arm_id=None,
            context_features=None,
        )

    # No vision → no useful context for the bandit. Fall back to Phase 1.
    if vision is None:
        return PolicyDecision(
            plan=pick_default_plan("", width, height, byte_size),
            arm_id=None,
            context_features=None,
        )

    # Photo path: featurize → LinUCB pick.
    from backend.bandit import (
        FeatureInput,
        arm_id_to_plan,
        featurize,
        pick_arm,
    )

    x = featurize(
        FeatureInput(
            width=width,
            height=height,
            byte_size=byte_size,
            mime_in=mime_in,
            content_type=vision.content_type,
            scene_label=scene_label,
            indoor_outdoor=indoor_outdoor,
            clip_embedding=clip_embedding,
        )
    )
    arm_id = await pick_arm(session, user_id, x)
    return PolicyDecision(
        plan=arm_id_to_plan(arm_id),
        arm_id=arm_id,
        context_features=x.tolist(),
    )
