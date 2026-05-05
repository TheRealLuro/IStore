"""Disjoint LinUCB contextual bandit (Phase 5).

Each arm a in the 80-arm grid has its own (A_a, b_a) sufficient statistics.
Selection (`pick_arm`):
    θ_a   = A_a^-1 · b_a
    UCB_a = θ_a · x  +  α · √(x · A_a^-1 · x)
    pick  = argmax_a UCB_a

Update (`update_state`, used by Phase 6 trainer):
    A_a += x · x^T
    b_a += r · x

State is stored per-(user, arm) in `bandit_state`, falling back to
`bandit_global_prior` for cold-start arms, falling back to A=I, b=0 when
neither row exists. The prior bootstrap script is Phase 5 work too;
v1 just uses the identity prior so the bandit explores from scratch.

Storage layout (BYTEA):
    a_matrix   : float32 little-endian, length d*d, row-major
    b_vector   : float32 little-endian, length d

d=32 is the feature dimension; see `featurize`.

Performance: ~80 arms × O(d²) inverse + O(d²) score = ~80 µs per pick on
typical hardware. The dominant cost is the SQL fetch (~1-5 ms typical).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.codecs import (
    ARM_GRID_CODECS,
    ARM_GRID_MAX_DIMS,
    ARM_GRID_QUALITIES,
    AVIF_AVAILABLE,
    JXL_AVAILABLE,
    Codec,
    CompressionPlan,
)
from backend.models import BanditGlobalPrior, BanditState

# ---------- arm grid ----------

D = 32
ALPHA = 1.0  # UCB exploration constant


def _build_arms() -> list[tuple[Codec, int, Optional[int]]]:
    """Enumerate (codec, quality, max_dim) tuples, skipping codecs whose
    encoder isn't installed in this environment."""
    arms: list[tuple[Codec, int, Optional[int]]] = []
    for codec in ARM_GRID_CODECS:
        if codec == "avif" and not AVIF_AVAILABLE:
            continue
        if codec == "jxl" and not JXL_AVAILABLE:
            continue
        for q in ARM_GRID_QUALITIES:
            for md in ARM_GRID_MAX_DIMS:
                arms.append((codec, q, md))
    return arms


ARMS: list[tuple[Codec, int, Optional[int]]] = _build_arms()
N_ARMS: int = len(ARMS)


def arm_id_to_plan(arm_id: int) -> CompressionPlan:
    codec, q, md = ARMS[arm_id]
    return CompressionPlan(codec=codec, quality=q, max_dim=md, lossless=False)


# ---------- featurizer ----------

CLIP_DIM = 768
JL_DIM = 16
N_SCENES = 8

# Coarse scene supercategory map. Unmapped labels fall into bucket 7 (other).
# Bucket 0 is reserved for "portrait_indoor" and 1 for "portrait_outdoor"
# but we don't have indoor/outdoor classification per-portrait yet — we
# leave those buckets unused for now and use indoor_outdoor on top.
_SCENE_BUCKET = {
    "beach": 2, "park": 2, "mountain": 2, "forest": 2, "outdoor": 2,
    "city": 3, "street": 3,
    "kitchen": 4, "bedroom": 4, "office": 4, "bathroom": 4, "restaurant": 4,
    "indoor": 4, "classroom": 4,
    "food": 5,
    "vehicle": 6,
}

# Fixed Johnson-Lindenstrauss projection from CLIP-768 to 16 dims. Seeded
# so featurization is deterministic across processes — required for the
# bandit, since (A, b) are accumulated against this exact projection.
_JL_SEED = 0x5331_b07b
_JL_PROJECTION = (
    np.random.default_rng(_JL_SEED)
    .standard_normal((CLIP_DIM, JL_DIM))
    .astype(np.float32)
    / np.sqrt(JL_DIM)
)


@dataclass(frozen=True)
class FeatureInput:
    width: int
    height: int
    byte_size: int
    mime_in: Optional[str]
    content_type: Optional[str]
    scene_label: Optional[str]
    indoor_outdoor: Optional[str]
    clip_embedding: Optional[list[float]]


def featurize(inp: FeatureInput) -> np.ndarray:
    """Build the 32-d context vector. Order is fixed and stable; see D const.

    The vector is unit-normalized at the end. LinUCB's exploration bonus
    α·√(x·A⁻¹·x) is sensitive to ||x||, and our raw geometry features (log
    of pixel dims and byte size) are O(10) while content/scene one-hots are
    O(1); without normalization the bonus would swamp the mean term and the
    bandit would never commit to a learned arm. Unit-norm preserves all
    relative direction information, which is what LinUCB actually uses.
    """
    x = np.zeros(D, dtype=np.float32)
    # Geometry / size — 4 dims. Scaled down so they don't dominate the norm.
    x[0] = float(np.log1p(max(0, inp.width))) / 10.0
    x[1] = float(np.log1p(max(0, inp.height))) / 10.0
    x[2] = float(np.log1p(max(0, inp.byte_size))) / 20.0
    x[3] = float(np.log1p(inp.width / inp.height)) if inp.height else 0.0

    # Content-type one-hots — 4 dims.
    mime = (inp.mime_in or "").lower()
    x[4] = 1.0 if mime in ("image/jpeg", "image/jpg") else 0.0
    x[5] = 1.0 if inp.content_type == "screenshot" else 0.0
    x[6] = 1.0 if inp.content_type == "document" else 0.0
    x[7] = 1.0 if inp.content_type in ("illustration", "icon") else 0.0

    # CLIP via fixed JL projection — 16 dims.
    if inp.clip_embedding is not None:
        emb = np.asarray(inp.clip_embedding, dtype=np.float32)
        if emb.shape == (CLIP_DIM,):
            x[8 : 8 + JL_DIM] = emb @ _JL_PROJECTION

    # Scene supercategory one-hot — 8 dims.
    bucket = _SCENE_BUCKET.get((inp.scene_label or "").lower(), 7)
    x[8 + JL_DIM + max(0, min(bucket, N_SCENES - 1))] = 1.0

    # Unit-normalize. Floor with a small epsilon so all-zero contexts (e.g.
    # missing CLIP + missing scene) don't divide-by-zero.
    norm = float(np.linalg.norm(x))
    if norm > 1e-6:
        x = x / norm
    return x


# ---------- (A, b) codec ----------


def encode_matrix(A: np.ndarray) -> bytes:
    return np.ascontiguousarray(A, dtype=np.float32).tobytes()


def decode_matrix(buf: bytes) -> np.ndarray:
    return np.frombuffer(buf, dtype=np.float32).reshape(D, D).copy()


def encode_vector(b: np.ndarray) -> bytes:
    return np.ascontiguousarray(b, dtype=np.float32).tobytes()


def decode_vector(buf: bytes) -> np.ndarray:
    return np.frombuffer(buf, dtype=np.float32).reshape(D).copy()


def default_a() -> np.ndarray:
    return np.eye(D, dtype=np.float32)


def default_b() -> np.ndarray:
    return np.zeros(D, dtype=np.float32)


# ---------- selection ----------


@dataclass
class _ArmStats:
    A: np.ndarray
    b: np.ndarray
    pulls: int


async def _load_user_state(
    session: AsyncSession, user_id
) -> dict[int, _ArmStats]:
    rows = (
        await session.execute(
            select(BanditState).where(BanditState.user_id == user_id)
        )
    ).scalars().all()
    return {
        r.arm_id: _ArmStats(
            A=decode_matrix(r.a_matrix),
            b=decode_vector(r.b_vector),
            pulls=r.pulls,
        )
        for r in rows
    }


async def _load_global_prior(session: AsyncSession) -> dict[int, _ArmStats]:
    rows = (
        await session.execute(select(BanditGlobalPrior))
    ).scalars().all()
    return {
        r.arm_id: _ArmStats(
            A=decode_matrix(r.a_matrix),
            b=decode_vector(r.b_vector),
            pulls=r.pulls,
        )
        for r in rows
    }


def _ucb_score(stats: _ArmStats, x: np.ndarray, alpha: float = ALPHA) -> float:
    A_inv = np.linalg.inv(stats.A)
    theta = A_inv @ stats.b
    mean = float(theta @ x)
    bonus = alpha * float(np.sqrt(max(0.0, x @ A_inv @ x)))
    return mean + bonus


def pick_arm_in_memory(
    user_state: dict[int, _ArmStats],
    global_prior: dict[int, _ArmStats],
    x: np.ndarray,
    alpha: float = ALPHA,
) -> int:
    """Pure-numeric arm selection. Used by tests + by `pick_arm` after I/O."""
    best_arm = 0
    best_score = -np.inf
    for arm_id in range(N_ARMS):
        stats = user_state.get(arm_id) or global_prior.get(arm_id)
        if stats is None:
            stats = _ArmStats(default_a(), default_b(), 0)
        s = _ucb_score(stats, x, alpha=alpha)
        if s > best_score:
            best_score = s
            best_arm = arm_id
    return best_arm


async def pick_arm(
    session: AsyncSession,
    user_id,
    x: np.ndarray,
    alpha: float = ALPHA,
) -> int:
    user_state = await _load_user_state(session, user_id)
    global_prior = await _load_global_prior(session)
    return pick_arm_in_memory(user_state, global_prior, x, alpha=alpha)


# ---------- update (used by Phase 6 trainer) ----------


def update_in_memory(
    stats: _ArmStats, x: np.ndarray, reward: float
) -> _ArmStats:
    """Pure update: A += x x^T;  b += r x;  pulls += 1."""
    return _ArmStats(
        A=stats.A + np.outer(x, x).astype(np.float32),
        b=(stats.b + (reward * x).astype(np.float32)).astype(np.float32),
        pulls=stats.pulls + 1,
    )


async def update_state(
    session: AsyncSession,
    user_id,
    arm_id: int,
    x: np.ndarray,
    reward: float,
) -> None:
    """Persist a feedback observation. Phase 6 trainer calls this."""
    from datetime import datetime, timezone

    existing = (
        await session.execute(
            select(BanditState).where(
                BanditState.user_id == user_id, BanditState.arm_id == arm_id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        # Cold start from prior or default.
        prior_row = (
            await session.execute(
                select(BanditGlobalPrior).where(
                    BanditGlobalPrior.arm_id == arm_id
                )
            )
        ).scalar_one_or_none()
        if prior_row is not None:
            stats = _ArmStats(
                A=decode_matrix(prior_row.a_matrix),
                b=decode_vector(prior_row.b_vector),
                pulls=prior_row.pulls,
            )
        else:
            stats = _ArmStats(default_a(), default_b(), 0)
        new_stats = update_in_memory(stats, x, reward)
        session.add(
            BanditState(
                user_id=user_id,
                arm_id=arm_id,
                a_matrix=encode_matrix(new_stats.A),
                b_vector=encode_vector(new_stats.b),
                pulls=new_stats.pulls,
                last_updated=datetime.now(timezone.utc),
            )
        )
    else:
        stats = _ArmStats(
            A=decode_matrix(existing.a_matrix),
            b=decode_vector(existing.b_vector),
            pulls=existing.pulls,
        )
        new_stats = update_in_memory(stats, x, reward)
        existing.a_matrix = encode_matrix(new_stats.A)
        existing.b_vector = encode_vector(new_stats.b)
        existing.pulls = new_stats.pulls
        existing.last_updated = datetime.now(timezone.utc)
