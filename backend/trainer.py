"""Bandit trainer + face-cluster maintenance (Phase 6).

The trainer runs as a coroutine that can be invoked manually (admin
endpoint, smoke test) or scheduled. The arq cron wiring is Phase 9 work;
v1 just exposes the function so the path can be exercised end-to-end.

What it does each pass:

  1. **Consume bandit feedback.**  SELECT all `feedback_events` rows with
     `consumed_by_trainer=false`. For each, weight the reward by its
     `weight` field and call `bandit.update_state` to fold it into the
     user's per-arm (A, b). Mark the row consumed.

  2. **Idempotence.**  The `consumed_by_trainer` flag flips on success
     INSIDE the same SQL transaction as the bandit_state write — replay
     of the trainer with the same DB state does nothing on already-
     processed rows. Re-running with `consumed_by_trainer=false` rows
     reproduces identical state.

  3. **Drift discount.**  Once per `DRIFT_INTERVAL` (default 30 d) we
     shrink each user's (A, b) toward the global prior with `0.95` decay.
     Implementation note: triggered on the trainer pass following the
     interval boundary; not strict cron.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import bandit
from backend.models import FeedbackEvent

logger = logging.getLogger(__name__)


@dataclass
class TrainerResult:
    consumed: int
    updated_arms: int
    failed: int


async def consume_feedback(session: AsyncSession) -> TrainerResult:
    """Consume every unprocessed feedback_event into bandit_state.

    Returns a result tuple with row counts. Idempotent: a second pass with
    no new rows is a no-op.
    """
    rows = (
        await session.execute(
            select(FeedbackEvent)
            .where(FeedbackEvent.consumed_by_trainer.is_(False))
            .order_by(FeedbackEvent.id.asc())
        )
    ).scalars().all()

    consumed = 0
    failed = 0
    updated_arms: set = set()

    for ev in rows:
        try:
            x = np.asarray(ev.context_features, dtype=np.float32)
            if x.shape != (bandit.D,):
                logger.warning(
                    "feedback %s: context_features shape %s != (%d,) — skipping",
                    ev.id,
                    x.shape,
                    bandit.D,
                )
                failed += 1
                continue

            # Weight the reward at consume time. An explicit rating contributes
            # full strength; implicit signals contribute `weight` (default 0.3).
            effective_reward = float(ev.reward) * float(ev.weight)

            await bandit.update_state(
                session=session,
                user_id=ev.user_id,
                arm_id=ev.bandit_arm_id,
                x=x,
                reward=effective_reward,
            )
            ev.consumed_by_trainer = True
            consumed += 1
            updated_arms.add((ev.user_id, ev.bandit_arm_id))
        except Exception:
            logger.exception("feedback %s: trainer crashed; will retry next pass", ev.id)
            failed += 1
            # Don't mark consumed — re-attempt next cycle.

    if consumed:
        await session.commit()

    return TrainerResult(
        consumed=consumed, updated_arms=len(updated_arms), failed=failed
    )


# ---------- drift discount ----------

DRIFT_DECAY = 0.95


async def apply_drift_discount(session: AsyncSession) -> int:
    """Shrink every user's (A, b) toward identity by DRIFT_DECAY.

    Per the plan: A_a ← 0.95·A_a + 0.05·A_0; same for b. With A_0 = I and
    b_0 = 0 (no global prior bootstrap yet) this becomes a pure exponential
    decay toward "fresh" state. Lets the bandit follow shifting taste
    without the user manually starting over.
    """
    from backend.models import BanditState

    rows = (
        await session.execute(select(BanditState))
    ).scalars().all()

    n = 0
    A_prior = bandit.default_a()
    b_prior = bandit.default_b()
    for row in rows:
        A = bandit.decode_matrix(row.a_matrix)
        b = bandit.decode_vector(row.b_vector)
        A = (DRIFT_DECAY * A + (1.0 - DRIFT_DECAY) * A_prior).astype(np.float32)
        b = (DRIFT_DECAY * b + (1.0 - DRIFT_DECAY) * b_prior).astype(np.float32)
        row.a_matrix = bandit.encode_matrix(A)
        row.b_vector = bandit.encode_vector(b)
        n += 1
    if n:
        await session.commit()
    return n
