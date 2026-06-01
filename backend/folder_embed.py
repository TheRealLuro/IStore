"""D4 — aggregate folder embeddings for semantic folder search.

A folder's `summary_clip_embedding` is an L2-normalised blend of:
  - the folder NAME encoded in CLIP text space, and
  - the centroid of its descendant images' `summary_clip_embedding`
    (the text-space embeddings of those items' AI summaries).

So a query like "the trip to Mexico" matches the folder whose photos'
summaries talk about Mexico even when the folder name is just "2023".

Computed in the API process: the CLIP text encoder already runs here for
search-query encoding, and the aggregation is cheap — one cached encode
of the folder name plus a single SQL `AVG()` over the subtree (pgvector
>= 0.5). The heavy per-image captioning stays in the worker. Recompute
is triggered after folder/child mutations and when a child's summary
lands (see the call sites in api/folders.py, api/images.py, summarize).
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Folder, Image

logger = logging.getLogger(__name__)

# How much the folder NAME contributes vs the child-content centroid.
# The name is a strong, deliberate label ("Mexico 2023"); the centroid
# captures what's actually inside. Both are L2-normalised before the
# blend, and the result is re-normalised after.
_NAME_WEIGHT = 0.45


async def _subtree_folder_ids(session: AsyncSession, root_id: UUID) -> list:
    """Every folder id in the subtree rooted at `root_id` (inclusive),
    skipping soft-deleted folders. Recursive CTE over parent_folder_id."""
    cte = text(
        """
        WITH RECURSIVE sub(id) AS (
            SELECT id FROM folders
            WHERE id = :root AND deleted_at IS NULL
          UNION ALL
            SELECT f.id FROM folders f
            JOIN sub ON f.parent_folder_id = sub.id
            WHERE f.deleted_at IS NULL
        )
        SELECT id FROM sub
        """
    )
    rows = (await session.execute(cte, {"root": str(root_id)})).all()
    return [r[0] for r in rows]


def _coerce_vec(raw) -> "np.ndarray | None":
    """pgvector results arrive as either a Python list / ndarray (when a
    result processor is applied) or a `"[a,b,c]"` string (raw avg() with
    no typed processor). Normalise both to a float32 ndarray."""
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip().lstrip("[").rstrip("]")
        if not s:
            return None
        return np.fromstring(s, sep=",", dtype=np.float32)
    return np.asarray(raw, dtype=np.float32)


async def compute_folder_embedding(session: AsyncSession, folder: Folder):
    """Return the aggregate embedding (list[float], dim 768) for `folder`,
    or None if it has neither a usable name nor any embedded descendants."""
    from backend.vision.inference_pool import run_in_inference_pool
    from backend.vision.runtime import encode_text_cached

    # 1. Centroid of descendant images' summary embeddings — one SQL AVG
    #    over the whole subtree (pgvector >= 0.5 averages vectors natively).
    fids = await _subtree_folder_ids(session, folder.id)
    centroid = None
    if fids:
        raw = (
            await session.execute(
                select(func.avg(Image.summary_clip_embedding)).where(
                    Image.folder_id.in_(fids),
                    Image.deleted_at.is_(None),
                    Image.summary_clip_embedding.is_not(None),
                )
            )
        ).scalar()
        centroid = _coerce_vec(raw)
        if centroid is not None:
            n = float(np.linalg.norm(centroid))
            centroid = centroid / n if n > 1e-6 else None

    # 2. Folder NAME in CLIP text space (cached encode, off the event loop).
    name = (folder.name or "").strip()
    name_vec = None
    if name:
        try:
            nv = await run_in_inference_pool(encode_text_cached, name)
            name_vec = np.asarray(nv, dtype=np.float32)
        except ImportError:
            name_vec = None  # [ml] extras absent — centroid-only
        except Exception:
            logger.exception("folder_embed: name encode failed for %s", folder.id)
            name_vec = None

    # 3. Blend + re-normalise.
    if name_vec is not None and centroid is not None:
        blended = _NAME_WEIGHT * name_vec + (1.0 - _NAME_WEIGHT) * centroid
    elif centroid is not None:
        blended = centroid
    elif name_vec is not None:
        blended = name_vec
    else:
        return None

    n = float(np.linalg.norm(blended))
    if n < 1e-6:
        return None
    return (blended / n).astype(float).tolist()


async def recompute_folder_embedding(
    session: AsyncSession, folder_id: UUID, *, commit: bool = True
) -> bool:
    """Recompute + persist one folder's cached embedding. No-op (returns
    False) if the folder is gone or soft-deleted."""
    folder = (
        await session.execute(
            select(Folder).where(Folder.id == folder_id, Folder.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if folder is None:
        return False
    folder.summary_clip_embedding = await compute_folder_embedding(session, folder)
    folder.embedding_updated_at = func.now()
    if commit:
        await session.commit()
    return True


async def ancestor_chain_ids(session: AsyncSession, folder_id: UUID) -> list:
    """`folder_id` plus every ancestor up to the root — the set whose
    aggregate embedding goes stale when a descendant changes. Walks
    parent_folder_id upward in one recursive CTE."""
    cte = text(
        """
        WITH RECURSIVE up(id, parent_folder_id) AS (
            SELECT id, parent_folder_id FROM folders WHERE id = :start
          UNION ALL
            SELECT f.id, f.parent_folder_id FROM folders f
            JOIN up ON f.id = up.parent_folder_id
        )
        SELECT id FROM up
        """
    )
    rows = (await session.execute(cte, {"start": str(folder_id)})).all()
    return [r[0] for r in rows]


def schedule_folder_recompute(user_id, folder_ids) -> None:
    """DISABLED 2026-05-31 — do NOT re-enable as-is.

    This used to fire `asyncio.create_task(_bg_recompute(...))`, which
    opened a DB transaction and then `await`ed the CLIP inference pool
    while holding it. When the single-threaded inference pool was busy,
    the fire-and-forget task stalled with the transaction still open,
    leaking an `idle in transaction` connection (and any row locks it
    held). Enough of those piled up to block writes — `/summarize-progress`
    and other UPDATEs hung, which starved the frontend and wedged the app.

    Folder-search embeddings are kept fresh via `POST /search/folders/reindex`
    instead. The proper fix (a follow-up) is a leak-proof, worker-side
    incremental recompute that does the CLIP encode OUTSIDE any DB
    transaction. For now this is a deliberate no-op so a move/rename can
    never leak a connection again."""
    return


async def _bg_recompute(user_id_str: str, folder_ids: list) -> None:
    from sqlalchemy import text as _text

    from backend.db import SessionLocal

    try:
        async with SessionLocal() as s:
            # Transaction-local RLS context for this background session.
            await s.execute(
                _text("SELECT set_config('app.current_user_id', :u, true)"),
                {"u": user_id_str},
            )
            seen: set = set()
            for fid in folder_ids:
                for aid in await ancestor_chain_ids(s, fid):
                    if aid in seen:
                        continue
                    seen.add(aid)
                    await recompute_folder_embedding(s, aid, commit=False)
            await s.commit()
    except Exception:
        logger.exception("folder_embed: background recompute failed")
