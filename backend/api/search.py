"""Semantic search.

GET /search?q=...
    → encode `q` via CLIP text encoder for visual matching
    → run a Postgres FTS pass over summary / summary_topic / summary_points /
      original_filename for textual matching (catches named people, doc
      titles, OCR'd whiteboard content, file names — none of which CLIP
      handles well)
    → merge both result sets, score = w_clip * clip + w_text * text
    → return top-k hits

Hybrid scoring closes the long-standing gap where searching for "Mr Koler"
or "framework showdown" returned only loose visual matches even though the
summary text contained the literal phrase. CLIP still wins for purely
visual queries ("sunset", "snowy mountain"); text wins for proper nouns
and document content. The blend favors text by a small margin (0.55 / 0.45)
because text matches are sparser — when they exist they're almost always
the right answer.
"""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
import sqlalchemy as sa
from sqlalchemy import cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User
from backend.schemas import ImageRead, ImageSearchHit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


# Score blend weights. Tuned for the case where CLIP returns a top-30 list
# and FTS returns a (usually shorter) top-30 list — most queries land in
# one or the other; a few land in both, and those bubble to the top.
_W_CLIP = 0.45
_W_TEXT = 0.55


def _encode_text_sync(query: str):
    from backend.vision.runtime import encode_text_cached

    return encode_text_cached(query)


def _build_haystack():
    """SQL expression: summary + topic + points + filename concatenated.

    Used for both `to_tsvector(...)` (FTS) and `ILIKE` (substring fallback
    for queries Postgres FTS won't tokenize well — e.g. partial filenames
    like "IMG_11"). Cast NULLs to empty strings so concatenation doesn't
    short-circuit.

    `summary_points` is `jsonb`, not `text[]` — `array_to_string()` would
    raise UndefinedFunction. Casting jsonb to text yields `["a","b"]`
    literal text; the FTS tokenizer strips the brackets/quotes/commas so
    each element still indexes as a separate lexeme. Must match
    migration 0017's `_HAYSTACK_EXPR` exactly so the planner uses the
    generated GIN index.
    """
    points_as_text = cast(Image.summary_points, sa.Text)
    return (
        func.coalesce(Image.summary, literal(""))
        + literal(" ")
        + func.coalesce(Image.summary_topic, literal(""))
        + literal(" ")
        + func.coalesce(points_as_text, literal(""))
        + literal(" ")
        + func.coalesce(Image.original_filename, literal(""))
    )


@router.get("/", response_model=list[ImageSearchHit])
async def semantic_search(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: int = 30,
) -> list[dict]:
    # --- text pass (cheap; runs even when CLIP / [ml] extras unavailable) --
    text_hits = await _text_search(session, user.id, q, limit=limit)

    # --- CLIP pass (gracefully degrades when [ml] extras aren't installed)
    clip_hits: dict = {}
    try:
        query_vec = await asyncio.to_thread(_encode_text_sync, q)
        clip_hits = await _clip_search(
            session, user.id, query_vec, limit=limit
        )
    except ImportError:
        # No ML extras — just return text matches.
        if not text_hits:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Semantic search requires the [ml] extras to be installed.",
            )

    # --- merge ----------------------------------------------------------
    merged: dict = {}  # image_id → (image, blended_score)
    for image_id, (image, clip_score) in clip_hits.items():
        merged[image_id] = (image, _W_CLIP * clip_score)
    for image_id, (image, text_score) in text_hits.items():
        if image_id in merged:
            img, prev = merged[image_id]
            merged[image_id] = (img, prev + _W_TEXT * text_score)
        else:
            merged[image_id] = (image, _W_TEXT * text_score)

    ranked = sorted(merged.values(), key=lambda r: r[1], reverse=True)[:limit]

    return [
        ImageSearchHit(
            **ImageRead.model_validate(image, from_attributes=True).model_dump(),
            score=round(score, 4),
        )
        for image, score in ranked
    ]


async def _clip_search(
    session: AsyncSession, user_id, query_vec, limit: int
) -> dict:
    """Top-k visual matches by CLIP cosine similarity.

    Returns {image_id: (image, score)} where score ∈ [0, 1] (cosine sim;
    higher = better). Skips images that have no embedding (fresh uploads
    waiting on the vision pipeline).
    """
    distance = Image.clip_embedding.cosine_distance(query_vec.tolist())
    stmt = (
        select(Image, distance.label("distance"))
        .where(
            Image.user_id == user_id,
            Image.deleted_at.is_(None),
            Image.clip_embedding.is_not(None),
        )
        .order_by(distance.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    out: dict = {}
    for image, dist in result.all():
        sim = 1.0 - float(dist)  # cosine_distance = 1 - cosine_similarity
        if sim <= 0:
            continue
        out[image.id] = (image, sim)
    return out


async def _text_search(
    session: AsyncSession, user_id, q: str, limit: int
) -> dict:
    """Top-k textual matches via Postgres FTS over the summary haystack.

    Falls back to ILIKE when FTS returns nothing (rare — happens when the
    query is below FTS's lexeme threshold, e.g. very short tokens). FTS
    rank is normalized to [0, 1] by dividing by the max rank in the
    result set so the blend with CLIP cosine is fair.
    """
    haystack = _build_haystack()
    tsvec = func.to_tsvector(literal("english"), haystack)
    tsquery = func.plainto_tsquery(literal("english"), q)
    rank = func.ts_rank_cd(tsvec, tsquery).label("rank")

    fts_stmt = (
        select(Image, rank)
        .where(
            Image.user_id == user_id,
            Image.deleted_at.is_(None),
            tsvec.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    rows = (await session.execute(fts_stmt)).all()

    if not rows:
        # FTS gave nothing — try a substring scan. Doesn't rank, just
        # buckets each match at score 0.6 so they're below strong CLIP
        # hits but above weak ones.
        like = f"%{q}%"
        like_stmt = (
            select(Image)
            .where(
                Image.user_id == user_id,
                Image.deleted_at.is_(None),
                or_(
                    Image.summary.ilike(like),
                    Image.summary_topic.ilike(like),
                    Image.original_filename.ilike(like),
                ),
            )
            .limit(limit)
        )
        out: dict = {}
        for image in (await session.execute(like_stmt)).scalars():
            out[image.id] = (image, 0.6)
        return out

    max_rank = max(float(r[1]) for r in rows) or 1.0
    return {
        image.id: (image, min(1.0, float(r) / max_rank))
        for image, r in rows
    }
