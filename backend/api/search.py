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
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
import sqlalchemy as sa
from sqlalchemy import cast, func, literal, or_, select
from sqlalchemy.dialects import postgresql
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
    """SQL expression: summary + topic + points + filename + signals.

    Used for both `to_tsvector(...)` (FTS) and `ILIKE` (substring fallback
    for queries Postgres FTS won't tokenize well — e.g. partial filenames
    like "IMG_11"). Cast NULLs to empty strings so concatenation doesn't
    short-circuit.

    `summary_points` and `summary_signals` are `jsonb`. Casting jsonb to
    text yields `["a","b"]` / `{"concepts":["..."]}` literal text; the
    FTS tokenizer strips brackets/quotes/commas/colons so each element
    still indexes as a separate lexeme. That gets us concepts, objects,
    regions, and the VLM description into the search surface without a
    new generated column. The stored `summary_tsv` index from migration
    0017 doesn't include `summary_signals` yet, so queries that ONLY
    match signal text fall back to the inline tsvector compute (slower
    but correct). Scene/setting/content_type are added as cheap discrete
    keywords so type-of-place queries ("indoor classroom") match even
    when the summary text didn't.
    """
    points_as_text = cast(Image.summary_points, sa.Text)
    signals_as_text = cast(Image.summary_signals, sa.Text)
    return (
        func.coalesce(Image.summary, literal(""))
        + literal(" ")
        + func.coalesce(Image.summary_topic, literal(""))
        + literal(" ")
        + func.coalesce(points_as_text, literal(""))
        + literal(" ")
        + func.coalesce(Image.original_filename, literal(""))
        + literal(" ")
        + func.coalesce(signals_as_text, literal(""))
        + literal(" ")
        + func.coalesce(Image.scene_label, literal(""))
        + literal(" ")
        + func.coalesce(Image.indoor_outdoor, literal(""))
        + literal(" ")
        + func.coalesce(Image.content_type, literal(""))
    )


# Minimum CLIP cosine for a hit to count on its own. Below this, the
# embedding match is basically noise — CLIP's "this image is a little
# bit related to your query" floor is around 0.20-0.22 for unrelated
# images on the same domain. We require either a text/keyword match OR
# a CLIP cosine above this threshold; weak CLIP-only matches don't
# pollute the result list. Raised from 0.24 → 0.26 because at 0.24
# small libraries returned every image (CLIP's "loosely related"
# floor often sits in [0.22, 0.27] for unrelated content on the
# same domain).
_CLIP_MIN_SIM_KEEP = 0.26
# Strong-match floor — CLIP hits at or above this are kept even when
# the user's query also has text matches. Below this and above the
# keep threshold, they only show up if no keyword match was found.
_CLIP_STRONG_SIM = 0.30
# After scoring + filtering, drop results whose score is below
# `top_score * _RELATIVE_FLOOR`. This is the gate that fixes the
# "type anything and everything pulls up" symptom on small libraries:
# if the best match is a 0.50 hit, we only keep things scoring ≥ 0.30.
# Tuned at 0.60 so a tightly-relevant top hit narrows the result
# list but multiple strong matches still co-exist.
_RELATIVE_FLOOR = 0.60


def _tokenize_query(q: str) -> list[str]:
    """Lowercase word tokens from the user's query, stopwords dropped.

    Used by the keyword-overlap pass so a query of "me in the mirror"
    becomes ["me", "mirror"] and we can require at least one keyword to
    appear in the file's searchable surface before we surface it.
    """
    stop = {
        "the", "a", "an", "of", "to", "and", "or", "but", "for", "in",
        "on", "at", "by", "from", "with", "is", "are", "be", "was",
        "were", "this", "that", "these", "those", "it", "its",
    }
    tokens: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9]+", q.lower()):
        if len(raw) <= 1:
            continue
        if raw in stop:
            continue
        tokens.append(raw)
    return tokens


def _file_haystack_text(image) -> str:
    """Concatenated lowercased text of a single Image — for client-side
    keyword overlap so we can require a query token to appear somewhere
    before we surface a row. Mirrors `_build_haystack` but materialized
    in Python (only called on the small candidate set after the SQL
    passes ran)."""
    parts: list[str] = []
    for attr in (
        "summary", "summary_topic", "original_filename",
        "scene_label", "indoor_outdoor", "content_type",
    ):
        v = getattr(image, attr, None)
        if v:
            parts.append(str(v))
    points = getattr(image, "summary_points", None) or []
    if isinstance(points, list):
        parts.extend(str(p) for p in points if p)
    signals = getattr(image, "summary_signals", None) or {}
    if isinstance(signals, dict):
        for key in ("regions", "objects", "concepts"):
            val = signals.get(key) or []
            if isinstance(val, list):
                parts.extend(str(x) for x in val if x)
        if signals.get("vlm"):
            parts.append(str(signals["vlm"]))
    return " ".join(parts).lower()


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
    # CLIP text encoding goes through the shared ML executor so a
    # search query during a running backfill queues briefly behind
    # any in-flight Florence call instead of fanning into another
    # thread that races for the GIL.
    from backend.vision.inference_pool import run_in_inference_pool
    clip_hits: dict = {}
    try:
        query_vec = await run_in_inference_pool(_encode_text_sync, q)
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

    tokens = _tokenize_query(q)

    # --- merge ----------------------------------------------------------
    #
    # Filtering policy (tightened from the old "any positive cosine
    # counts"): a row is kept if
    #   1. it has a keyword hit on its searchable text, OR
    #   2. its CLIP cosine is at least `_CLIP_MIN_SIM_KEEP` (i.e. CLIP
    #      thinks it's plausibly related, not just "marginally not
    #      orthogonal"), OR
    #   3. its CLIP cosine is at least `_CLIP_STRONG_SIM` even when the
    #      query has keyword hits elsewhere — strong visual matches
    #      should always surface.
    #
    # The old behavior returned every image with a positive cosine,
    # which on a small library meant the result list was just "all
    # files, ordered by random CLIP noise."
    merged: dict = {}
    for image_id, (image, clip_score) in clip_hits.items():
        if clip_score < _CLIP_MIN_SIM_KEEP:
            continue
        merged[image_id] = (image, _W_CLIP * clip_score, False)  # False = no text match yet
    for image_id, (image, text_score) in text_hits.items():
        if image_id in merged:
            img, prev, _ = merged[image_id]
            merged[image_id] = (img, prev + _W_TEXT * text_score, True)
        else:
            merged[image_id] = (image, _W_TEXT * text_score, True)

    # Apply the keyword-overlap gate. If the user typed concrete words,
    # we require at least one to match the row's text OR the CLIP hit
    # to be strong on its own — otherwise the row gets dropped.
    have_any_text_match = any(has_text for (_, _, has_text) in merged.values())
    final: list[tuple] = []
    for image_id, (image, score, has_text) in merged.items():
        if tokens:
            haystack = _file_haystack_text(image)
            keyword_hit = any(tok in haystack for tok in tokens)
        else:
            keyword_hit = False
        clip_score = clip_hits.get(image_id, (None, 0.0))[1] if image_id in clip_hits else 0.0
        if has_text or keyword_hit:
            final.append((image, score))
        elif clip_score >= _CLIP_STRONG_SIM:
            final.append((image, score))
        elif not have_any_text_match and not tokens:
            # Query had no usable tokens (all stopwords); fall back to
            # the looser CLIP-only ranking.
            final.append((image, score))
        # else: drop the row — weak CLIP-only match with no keyword hit.

    ranked = sorted(final, key=lambda r: r[1], reverse=True)[:limit]

    # Relative-margin filter — on small libraries, multiple files all
    # pick up weak common-vocabulary keyword hits ("me", "photo",
    # "image") and the result list looked like "all files, ranked by
    # noise." Drop anything more than `_RELATIVE_FLOOR` below the top
    # score so a clearly-best match narrows the list instead of
    # dragging every other row along with it.
    if ranked:
        top_score = ranked[0][1]
        cutoff = top_score * _RELATIVE_FLOOR
        ranked = [(img, sc) for (img, sc) in ranked if sc >= cutoff]

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
    # Cast the language literal to `regconfig` so Postgres binds to the
    # `to_tsvector(regconfig, text)` overload. Without the cast we get a
    # varchar bind param which doesn't match any `to_tsvector` signature
    # and the planner raises `function to_tsvector(varchar, text) does
    # not exist`. Same for plainto_tsquery.
    english = cast(literal("english"), postgresql.REGCONFIG)
    tsvec = func.to_tsvector(english, haystack)
    tsquery = func.plainto_tsquery(english, q)
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
        # FTS gave nothing — try a substring scan over the same surfaces
        # the FTS pass uses, plus `summary_signals` cast to text so a
        # query of "whiteboard" matches an image whose only signal is a
        # Florence-2 region or OpenCLIP concept tag of that word. Score
        # 0.6 so substring hits sit below strong CLIP visual matches
        # but above weak ones.
        like = f"%{q}%"
        signals_as_text = cast(Image.summary_signals, sa.Text)
        like_stmt = (
            select(Image)
            .where(
                Image.user_id == user_id,
                Image.deleted_at.is_(None),
                or_(
                    Image.summary.ilike(like),
                    Image.summary_topic.ilike(like),
                    Image.original_filename.ilike(like),
                    Image.scene_label.ilike(like),
                    Image.indoor_outdoor.ilike(like),
                    Image.content_type.ilike(like),
                    signals_as_text.ilike(like),
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
