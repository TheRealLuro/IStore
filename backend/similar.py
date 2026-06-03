"""Near-duplicate / visually-similar group lookup for one image.

Feature #172 — "Best of usable anywhere." The best-of scorer
(`backend/best_of.py`) only ever ran on an explicit multi-selection.
To make it reachable from a single open photo (the preview tool-rail)
or a single gallery card, we need a way to pull that photo's *burst
group* — the handful of near-identical shots taken seconds apart — so
we can feed them straight into the existing best-of comparison.

This is a NEW, UNWIRED router. The two lines to mount it live at the
bottom of this docstring; drop them into `backend/app.py` alongside the
other `app.include_router(...)` calls.

    from backend.similar import router as similar_router
    app.include_router(similar_router)

The endpoint is `GET /images/{image_id}/similar`:

  - Authed + owner-scoped: the anchor row must belong to the caller
    (`Image.user_id == user.id`), and every candidate is filtered to the
    same `user_id`, so one user can never probe another's library.
  - pgvector KNN, same machinery the search route uses
    (`Image.clip_embedding.cosine_distance(...)` → walks the
    `images_clip_embedding_idx` HNSW index). We order by cosine distance
    ascending and keep the closest N.
  - "Near-duplicate" gate: we only return candidates whose cosine
    similarity to the anchor is above `SIMILAR_THRESHOLD`. That keeps the
    group to true burst frames (hand-held repeats of the same scene land
    ~0.88-0.95) rather than the loose "visually related" tail CLIP would
    otherwise surface. This mirrors `best_of.BURST_SIM_THRESHOLD` so what
    the user sees as "similar here" is the same notion of similarity the
    burst-mode scorer clusters on.
  - The anchor itself is always included as the first result so the
    best-of modal opens on a complete set (anchor + its near-dupes).

Returns a small JSON payload the frontend turns into a best-of run:

    {
      "anchor_id": "...",
      "threshold": 0.86,
      "count": 4,
      "results": [
        {"image_id": "...", "similarity": 1.0,  "is_anchor": true},
        {"image_id": "...", "similarity": 0.93, "is_anchor": false},
        ...
      ]
    }
"""
from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


# Cosine-similarity floor for "this is the same burst." Kept in lockstep
# with best_of.BURST_SIM_THRESHOLD (0.85) — nudged a hair higher here so
# the auto-pulled group leans tight (true repeats) rather than dragging in
# a borderline neighbour the user didn't actually burst. The query param
# below lets the caller relax it when a photo has only loose siblings.
SIMILAR_THRESHOLD = 0.86


@router.get("/{image_id}/similar")
async def image_similar_group(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    # Cap on how many near-duplicates to return (excluding the anchor).
    # Best-of accepts up to 30 in a batch; default 11 → anchor + 11 = 12,
    # a comfortable comparison set that still scores in well under a
    # second. Bounded so a crafted `limit` can't fan out the KNN probe.
    limit: Annotated[int, Query(ge=1, le=29)] = 11,
    # Let the caller loosen the near-duplicate gate when the default
    # surfaces nothing (a photo with no tight burst, only loose cousins).
    # Floored at 0.5 so we never return CLIP's "vaguely related" noise.
    threshold: Annotated[float, Query(ge=0.5, le=0.99)] = SIMILAR_THRESHOLD,
) -> dict:
    """Return the anchor image's visually-similar / burst group.

    Owner-scoped pgvector KNN. The anchor is always result #0 (so the
    best-of modal opens on a full set); the rest are its nearest
    neighbours above `threshold`, closest first.
    """
    # Anchor lookup — owner-scoped, non-deleted. 404 (not 403) for a
    # missing OR foreign id so we don't leak which ids exist.
    anchor = (
        await session.execute(
            select(Image).where(
                Image.id == image_id,
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if anchor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")

    # No embedding on the anchor → nothing to compare against. Return a
    # singleton set so the caller can show a friendly "no similar shots"
    # state instead of erroring.
    if anchor.clip_embedding is None:
        return {
            "anchor_id": str(anchor.id),
            "threshold": threshold,
            "count": 1,
            "results": [
                {"image_id": str(anchor.id), "similarity": 1.0, "is_anchor": True}
            ],
        }

    # pgvector cosine KNN — same operator the search route uses, so this
    # walks the HNSW index on images.clip_embedding. We ask for limit+1
    # because the anchor itself is the nearest point (distance 0) and
    # would otherwise eat a slot.
    qv = list(anchor.clip_embedding)
    dist = Image.clip_embedding.cosine_distance(qv)
    rows = (
        await session.execute(
            select(Image.id, dist.label("distance"))
            .where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                Image.clip_embedding.is_not(None),
            )
            .order_by(dist.asc())
            .limit(limit + 1)
        )
    ).all()

    results: list[dict] = [
        {"image_id": str(anchor.id), "similarity": 1.0, "is_anchor": True}
    ]
    for rid, distance in rows:
        if rid == anchor.id:
            continue  # the anchor is already result #0
        sim = 1.0 - float(distance)
        if sim < threshold:
            # rows are distance-ascending → similarity-descending, so the
            # first miss means every subsequent row also misses. Stop.
            break
        results.append(
            {"image_id": str(rid), "similarity": round(sim, 4), "is_anchor": False}
        )
        if len(results) >= limit + 1:
            break

    return {
        "anchor_id": str(anchor.id),
        "threshold": threshold,
        "count": len(results),
        "results": results,
    }
