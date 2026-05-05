"""Phase 6 acceptance: feedback ingestion + trainer.

Three guarantees per the plan's Done-when:
  1. Trainer is idempotent on replay — re-running with the same backlog
     produces identical state. Implemented by `consumed_by_trainer=true`
     flipping in the same SQL transaction as the bandit_state write.
  2. An explicit bad rating shifts the next decision for similar context to
     a different arm within one trainer cycle.
  3. Reward formula matches the plan: r = α·size_savings − β·(1 − rating/5).

These tests exercise the full DB stack — feedback row writes, trainer
consume, bandit_state updates — so they need the test DB fixture from
conftest.py.
"""
from __future__ import annotations

import uuid

import numpy as np
import pytest
from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


# ---------- helpers ----------


async def _seed_image_with_arm(
    user_id: uuid.UUID,
    *,
    bandit_arm_id: int,
    context_features: list[float],
    byte_size_original: int = 1_000_000,
    byte_size_served: int = 400_000,
):
    """Insert an image row that looks like it was bandit-encoded."""
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as session:
        img = Image(
            user_id=user_id,
            category="image",
            served_blob_key=f"u/{user_id}/served/{uuid.uuid4().hex}.webp",
            original_blob_key=f"u/{user_id}/orig/{uuid.uuid4().hex}",
            original_filename="seed.jpg",
            byte_size_original=byte_size_original,
            byte_size_served=byte_size_served,
            mime_type_served="image/webp",
            codec="webp",
            quality=82,
            max_dim=4096,
            lossless=False,
            bandit_arm_id=bandit_arm_id,
            context_features=context_features,
            pending_face_scan=False,
        )
        session.add(img)
        await session.commit()
        await session.refresh(img)
        return img.id


# ---------- reward math ----------


def test_compute_reward_matches_plan_formula():
    from backend.feedback import compute_reward

    # rating=5, 60% savings → 1.0·0.6 - 2·0 - 0 = 0.6
    r = compute_reward(rating=5, byte_size_original=1000, byte_size_served=400)
    assert r == pytest.approx(0.6, rel=1e-3)

    # rating=1, no savings → 0 - 2·0.8 = -1.6
    r = compute_reward(rating=1, byte_size_original=1000, byte_size_served=1000)
    assert r == pytest.approx(-1.6, rel=1e-3)

    # rating=3, 50% savings → 0.5 - 2·0.4 = -0.3
    r = compute_reward(rating=3, byte_size_original=1000, byte_size_served=500)
    assert r == pytest.approx(-0.3, rel=1e-3)


def test_compute_reward_clamps_rating_to_valid_range():
    from backend.feedback import compute_reward

    # Out-of-range → clamped to [1, 5].
    assert compute_reward(rating=9, byte_size_original=1000, byte_size_served=400) == \
        pytest.approx(compute_reward(rating=5, byte_size_original=1000, byte_size_served=400))
    assert compute_reward(rating=-3, byte_size_original=1000, byte_size_served=400) == \
        pytest.approx(compute_reward(rating=1, byte_size_original=1000, byte_size_served=400))


# ---------- ingest endpoint ----------


async def test_post_feedback_writes_event_with_denormalized_state(db_client):
    from backend.bandit import D
    from backend.db import SessionLocal
    from backend.models import FeedbackEvent

    email = "fb-a@example.com"
    _, headers = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    # Image was encoded by arm 7 with a specific context vector.
    arm_id = 7
    ctx = [0.0] * D
    ctx[5] = 1.0  # screenshot flag — fake but well-formed
    ctx[8] = 0.5
    image_id = await _seed_image_with_arm(
        uid, bandit_arm_id=arm_id, context_features=ctx,
    )

    r = await db_client.post(
        f"/images/{image_id}/feedback",
        json={"rating": 4},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["recorded"] is True

    async with SessionLocal() as session:
        events = (
            await session.execute(
                select(FeedbackEvent).where(FeedbackEvent.user_id == uid)
            )
        ).scalars().all()
    assert len(events) == 1
    ev = events[0]
    assert ev.bandit_arm_id == arm_id
    assert ev.context_features == ctx
    assert ev.kind == "rating"
    assert ev.rating == 4
    assert ev.consumed_by_trainer is False
    # 4-star + 60% savings → 0.6 - 2·0.2 = 0.2
    assert ev.reward == pytest.approx(0.2, rel=1e-3)


async def test_post_feedback_skips_hard_rule_image(db_client):
    """Screenshots forced to lossless have no bandit_arm_id; the endpoint
    should return recorded=False without writing a row."""
    email = "fb-hardrule@example.com"
    _, headers = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import FeedbackEvent, Image

    async with SessionLocal() as session:
        img = Image(
            user_id=uid, category="image",
            served_blob_key=f"u/{uid}/served/x.webp",
            original_blob_key=f"u/{uid}/orig/x",
            byte_size_original=1000, byte_size_served=400,
            bandit_arm_id=None, context_features=None,
            pending_face_scan=False,
        )
        session.add(img)
        await session.commit()
        image_id = img.id

    r = await db_client.post(
        f"/images/{image_id}/feedback", json={"rating": 5}, headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["recorded"] is False

    async with SessionLocal() as session:
        events = (
            await session.execute(
                select(FeedbackEvent).where(FeedbackEvent.user_id == uid)
            )
        ).scalars().all()
    assert events == []


async def test_post_feedback_validates_rating_range(db_client):
    _, headers = await register_and_login(db_client)
    # Need a real image_id so the route gets past 404.
    fake = uuid.uuid4()
    r = await db_client.post(
        f"/images/{fake}/feedback", json={"rating": 0}, headers=headers,
    )
    assert r.status_code == 422
    r = await db_client.post(
        f"/images/{fake}/feedback", json={"rating": 6}, headers=headers,
    )
    assert r.status_code == 422


async def test_post_feedback_404s_for_other_users_image(db_client):
    _, headers_a = await register_and_login(db_client, email="fb-iso-a@example.com")
    _, _ = await register_and_login(db_client, email="fb-iso-b@example.com")
    uid_b = await fetch_user_id("fb-iso-b@example.com")
    img_id = await _seed_image_with_arm(uid_b, bandit_arm_id=3, context_features=[0.0]*32)

    r = await db_client.post(
        f"/images/{img_id}/feedback", json={"rating": 5}, headers=headers_a,
    )
    assert r.status_code == 404


# ---------- trainer ----------


async def test_trainer_consumes_feedback_into_bandit_state(db_client):
    from backend.bandit import D, decode_matrix, decode_vector, default_a
    from backend.db import SessionLocal
    from backend.models import BanditState, FeedbackEvent
    from backend.trainer import consume_feedback

    email = "trainer-a@example.com"
    _, headers = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    arm_id = 12
    ctx = [0.1] * D
    image_id = await _seed_image_with_arm(uid, bandit_arm_id=arm_id, context_features=ctx)

    # Submit two ratings via API.
    for r in (5, 4):
        resp = await db_client.post(
            f"/images/{image_id}/feedback", json={"rating": r}, headers=headers,
        )
        assert resp.status_code == 200

    # Pre-trainer: no bandit state row yet.
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(BanditState).where(BanditState.user_id == uid)
            )
        ).scalars().all()
        assert rows == []

    # Run trainer.
    async with SessionLocal() as session:
        result = await consume_feedback(session)
    assert result.consumed == 2
    assert result.failed == 0
    assert result.updated_arms == 1

    # Post-trainer: bandit_state has the right (A, b).
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(BanditState).where(
                    BanditState.user_id == uid, BanditState.arm_id == arm_id
                )
            )
        ).scalars().all()
        events = (
            await session.execute(
                select(FeedbackEvent).where(FeedbackEvent.user_id == uid)
            )
        ).scalars().all()
    assert len(rows) == 1
    state = rows[0]
    assert state.pulls == 2

    # A should have grown above identity in the direction of x; verify by
    # checking that A - I is positive on the diagonal where x is non-zero.
    A = decode_matrix(state.a_matrix)
    b = decode_vector(state.b_vector)
    A0 = default_a()
    delta = A - A0
    x = np.asarray(ctx, dtype=np.float32)
    assert delta @ x @ x > 0
    # b should align with x scaled by total reward (rating 5 + rating 4 with full weight).
    # r5 = 0.6 - 0 = 0.6;  r4 = 0.6 - 0.4 = 0.2;  total ≈ 0.8 (ignoring weight=1.0).
    expected_b = 0.8 * x
    assert np.allclose(b, expected_b, atol=0.05)

    # Events are flagged consumed.
    assert all(ev.consumed_by_trainer for ev in events)


async def test_trainer_is_idempotent_on_replay(db_client):
    """Running the trainer twice on the same backlog must equal one run."""
    from backend.db import SessionLocal
    from backend.models import BanditState, FeedbackEvent
    from backend.trainer import consume_feedback

    email = "trainer-idem@example.com"
    _, headers = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    image_id = await _seed_image_with_arm(uid, bandit_arm_id=4, context_features=[0.05] * 32)
    for r in (5, 3, 4):
        await db_client.post(
            f"/images/{image_id}/feedback", json={"rating": r}, headers=headers,
        )

    async with SessionLocal() as session:
        first = await consume_feedback(session)
    async with SessionLocal() as session:
        second = await consume_feedback(session)

    assert first.consumed == 3
    assert second.consumed == 0  # everything was already flagged consumed

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(BanditState).where(BanditState.user_id == uid)
            )
        ).scalars().all()
        # Pull through to double-check pulls counter didn't move on replay.
        assert len(rows) == 1
        assert rows[0].pulls == 3

        # Also verify all events stayed consumed.
        events = (
            await session.execute(
                select(FeedbackEvent).where(FeedbackEvent.user_id == uid)
            )
        ).scalars().all()
        assert all(e.consumed_by_trainer for e in events)


async def test_bad_rating_shifts_next_decision_for_similar_context(db_client):
    """An explicit bad rating must move the bandit's preference for the
    same context away from that arm within one trainer pass.

    Given two arms (call them A and B) and a context x, after enough
    negative reward on A the LinUCB selector's UCB for B should exceed A's.
    """
    from backend.bandit import (
        D,
        FeatureInput,
        _ArmStats,
        default_a,
        default_b,
        featurize,
        pick_arm_in_memory,
        update_in_memory,
    )

    # Build a realistic photo context.
    x = featurize(
        FeatureInput(
            width=4032, height=3024, byte_size=2_500_000,
            mime_in="image/jpeg", content_type="photo", scene_label="park",
            indoor_outdoor="outdoor", clip_embedding=[0.3] * 768,
        )
    )

    # Arm A starts hot (10 pulls at reward 0.5) but then gets 5 negative
    # ratings. Arm B is cold.
    arm_a_id = 17
    arm_a = _ArmStats(default_a(), default_b(), 0)
    for _ in range(10):
        arm_a = update_in_memory(arm_a, x, 0.5)
    # Five rating=1 events: r ≈ 0.6 − 1.6 = −1.0 each
    for _ in range(5):
        arm_a = update_in_memory(arm_a, x, -1.0)

    # Pre-update sanity: arm A's UCB should be lower than a fresh arm because
    # the negative pulls dragged the mean down.
    state = {arm_a_id: arm_a}
    pick = pick_arm_in_memory(state, {}, x, alpha=0.3)
    assert pick != arm_a_id, (
        f"After 5 bad ratings, arm {arm_a_id} should not still be top pick; "
        f"got {pick}"
    )
    _ = D  # silence linter; D was imported for parity with other tests
