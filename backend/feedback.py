"""Reward-signal ingestion (Phase 6).

Three signal kinds:
  - 'rating': explicit 1..5 from the post-download modal
  - 'implicit_negative': re-download within 30 s, weight 0.3
  - 'implicit_positive': 90 d no re-download or complaint, weight 0.3

Reward function (per the plan):
    r = α·size_savings − β·(1 − rating/5) − γ·latency_penalty

α=1.0, β=2.0, γ=0.1. We skip latency_penalty in v1 because we don't yet
record encode latency (Phase 9 OTel work). Implicit signals synthesize a
rating value (2 for negatives, 4 for positives) and apply a weight so they
update the bandit with less force than an explicit rating.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import FeedbackEvent, Image, User

logger = logging.getLogger(__name__)

# Reward formula constants. Centralized so the trainer + tests share them.
ALPHA_SIZE = 1.0
BETA_RATING = 2.0
GAMMA_LATENCY = 0.1

IMPLICIT_NEGATIVE_RATING = 2
IMPLICIT_POSITIVE_RATING = 4
IMPLICIT_WEIGHT = 0.3


def _size_savings(byte_size_original: Optional[int], byte_size_served: Optional[int]) -> float:
    if not byte_size_original or not byte_size_served:
        return 0.0
    return max(0.0, 1.0 - (byte_size_served / byte_size_original))


def compute_reward(
    *,
    rating: int,
    byte_size_original: Optional[int],
    byte_size_served: Optional[int],
    latency_penalty: float = 0.0,
) -> float:
    """Reward for a (rating, size, latency) triple. See plan §RL."""
    rating = max(1, min(5, rating))
    savings = _size_savings(byte_size_original, byte_size_served)
    return (
        ALPHA_SIZE * savings
        - BETA_RATING * (1.0 - rating / 5.0)
        - GAMMA_LATENCY * latency_penalty
    )


class CannotRecordFeedback(Exception):
    """Raised when the image lacks the bandit metadata required for a
    feedback row (e.g. a screenshot encoded by the hard-rule, no arm_id)."""


async def _build_event(
    *,
    user: User,
    image: Image,
    kind: str,
    rating: int,
    weight: float,
) -> FeedbackEvent:
    if image.bandit_arm_id is None or image.context_features is None:
        # Hard-rule images (screenshots forced to lossless) have no bandit
        # arm to attribute reward to. Skip silently — there's no signal here.
        raise CannotRecordFeedback(
            "Image was not encoded by the bandit (probably hard-rule lossless)"
        )

    reward = compute_reward(
        rating=rating,
        byte_size_original=image.byte_size_original,
        byte_size_served=image.byte_size_served,
    )
    return FeedbackEvent(
        user_id=user.id,
        image_id=image.id,
        kind=kind,
        rating=rating,
        weight=weight,
        bandit_arm_id=image.bandit_arm_id,
        context_features=image.context_features,
        reward=reward,
        consumed_by_trainer=False,
    )


async def record_rating(
    session: AsyncSession,
    user: User,
    image: Image,
    rating: int,
) -> Optional[FeedbackEvent]:
    """Record an explicit user rating (1..5)."""
    if not 1 <= rating <= 5:
        raise ValueError(f"rating must be 1..5, got {rating}")
    try:
        event = await _build_event(
            user=user,
            image=image,
            kind="rating",
            rating=rating,
            weight=1.0,
        )
    except CannotRecordFeedback as exc:
        logger.info("Feedback skipped: %s", exc)
        return None
    session.add(event)
    await session.flush()
    return event


async def record_implicit_negative(
    session: AsyncSession, user: User, image: Image
) -> Optional[FeedbackEvent]:
    try:
        event = await _build_event(
            user=user,
            image=image,
            kind="implicit_negative",
            rating=IMPLICIT_NEGATIVE_RATING,
            weight=IMPLICIT_WEIGHT,
        )
    except CannotRecordFeedback:
        return None
    session.add(event)
    await session.flush()
    return event


async def record_implicit_positive(
    session: AsyncSession, user: User, image: Image
) -> Optional[FeedbackEvent]:
    try:
        event = await _build_event(
            user=user,
            image=image,
            kind="implicit_positive",
            rating=IMPLICIT_POSITIVE_RATING,
            weight=IMPLICIT_WEIGHT,
        )
    except CannotRecordFeedback:
        return None
    session.add(event)
    await session.flush()
    return event
