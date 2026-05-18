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

from backend.audit import add_audit
from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User
from backend.schemas import ImageRead, ImageSearchHit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


# §C1.4 — clear search history.
#
# Today the recent-searches dropdown is hydrated from the user's
# browser `localStorage` (`neuthek.recentSearches`), so the FE can
# clear it without a round-trip. The DELETE endpoint exists for two
# reasons:
#
#   1. **Audit-log trail.** When a user clicks "Clear history," that
#      action should leave a row in the consent/activity audit so a
#      data-subject access request later can show "user cleared their
#      search history on $date."
#   2. **Forward-compat.** When the recent searches move to a server-
#      side store (per-user, cross-device sync), the FE call site
#      already exists and the contract doesn't break.
#
# Returns 204 unconditionally — no payload, no count, no leaked
# information about whether the user had history to begin with.


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_search_history(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Clear the calling user's recent-search history.

    Today this is a server-side audit write; the actual entries live
    in browser localStorage, which the FE clears in the same handler.
    A future server-side history table can be wiped here without the
    FE caller having to change.
    """
    await add_audit(
        session,
        user_id=user.id,
        action="search.history.cleared",
        details={},
    )
    await session.commit()
    return None


# Score blend weights. CLIP-led because the headline feature is
# "search by what you remember" — semantic queries like "teacher
# teaching math" must surface a whiteboard photo even though none of
# those literal words appear in the summary. FTS is a boost for
# precise hits (filenames, exact terms), not the primary signal.
_W_CLIP = 0.65
_W_TEXT = 0.35


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


# Minimum CLIP cosine for a hit to count. Below this, the embedding
# match is basically noise. We dropped from 0.26 to 0.22 because
# semantic queries that don't share any literal terms with the
# summary (the canonical "teacher teaching math" vs. a whiteboard
# photo with calculus equations) typically land in [0.22, 0.30]
# and were being filtered out as "weak." Unrelated images on the
# same domain usually sit at 0.18-0.20, so 0.22 still excludes
# noise.
_CLIP_MIN_SIM_KEEP = 0.22
# Strong-match floor — CLIP hits at or above this are kept even when
# the user's query also has text matches. Below this and above the
# keep threshold, they only show up if no keyword match was found.
_CLIP_STRONG_SIM = 0.28
# After scoring + filtering, drop results whose score is below
# `top_score * _RELATIVE_FLOOR`. Loosened from 0.60 → 0.45 so a
# tightly-relevant top hit doesn't suppress the second-best semantic
# match. With the keyword-overlap gate gone, this is the main noise
# filter — but it should be permissive enough that "teacher teaching
# math" returns the 2-3 plausibly-relevant photos, not just the top.
_RELATIVE_FLOOR = 0.45


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


def _expand_tokens(tokens: list[str]) -> list[str]:
    """Expand each token with its synonyms so the keyword-overlap gate
    in `semantic_search` accepts synonym matches too.

    Without this, `_text_search`'s FTS expansion would surface a row
    on a synonym hit, but then the post-filter would drop it because
    none of the user's literal tokens appear in `_file_haystack_text`.
    Net result: zero rank-list change. By expanding here too, the
    full pipeline honors synonyms end-to-end.
    """
    from backend.synonyms import SYNONYMS_INDEX

    out: set[str] = set()
    for tok in tokens:
        out.add(tok)
        out.update(SYNONYMS_INDEX.get(tok, set()))
    return sorted(out)


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

    # --- merge ----------------------------------------------------------
    #
    # Policy: keep a row if its CLIP cosine clears `_CLIP_MIN_SIM_KEEP`
    # OR it picked up an FTS hit. CLIP score dominates; FTS adds a
    # boost when it lands. The old hard-gate that required a literal
    # keyword overlap with the user's tokens was removed because it
    # killed every legitimate semantic query whose vocabulary didn't
    # match the captioned vocabulary (e.g. "teacher teaching math"
    # vs. a whiteboard photo whose summary says "matrix algebra").
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

    # No keyword-overlap gate. The previous gate required at least one
    # query token to appear literally in the row's summary/filename,
    # which killed every semantic query that didn't share vocabulary
    # with the image's caption — the canonical failure was "teacher
    # teaching math" vs. a whiteboard photo whose summary talked about
    # "matrix algebra and calculus" instead. CLIP's job is exactly to
    # bridge that vocabulary gap; making it gate on keyword overlap
    # negated the headline "search by what you remember" feature.
    #
    # Quality is now defended by two cheaper filters:
    #   1. `_CLIP_MIN_SIM_KEEP` on entry (drops embeddings that are
    #      basically orthogonal to the query — noise floor).
    #   2. `_RELATIVE_FLOOR` below — drops results far below the top
    #      score so a single weakly-similar image doesn't appear next
    #      to a clearly relevant one.
    final = [(image, score) for (image, score, _has_text) in merged.values()]
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

    Query expansion: we build BOTH a strict `plainto_tsquery(q)` AND an
    OR'd `to_tsquery` of synonyms (e.g. `vibrant` also matches
    `colorful` / `vivid` / `bright`) — see backend/synonyms.py. The
    match predicate accepts either, but the rank uses the strict
    query so exact-token matches still dominate the score. Without
    the expansion, FTS contributed zero to a `vibrant` search whose
    only matching row says "colorful pattern" — and the user's
    intuition that "those words mean the same thing" was right.
    """
    from backend.synonyms import expand_query_to_tsquery

    haystack = _build_haystack()
    # Cast the language literal to `regconfig` so Postgres binds to the
    # `to_tsvector(regconfig, text)` overload. Without the cast we get a
    # varchar bind param which doesn't match any `to_tsvector` signature
    # and the planner raises `function to_tsvector(varchar, text) does
    # not exist`. Same for plainto_tsquery.
    english = cast(literal("english"), postgresql.REGCONFIG)
    tsvec = func.to_tsvector(english, haystack)
    tsquery_strict = func.plainto_tsquery(english, q)
    # Expanded query for the predicate: synonyms OR'd in per token.
    # If expansion comes back empty (no usable tokens) we fall back to
    # the strict query everywhere.
    expanded_str = expand_query_to_tsquery(q)
    if expanded_str:
        try:
            tsquery_expanded = func.to_tsquery(english, expanded_str)
            match_predicate = tsvec.op("@@")(tsquery_strict) | tsvec.op("@@")(tsquery_expanded)
        except Exception:
            # to_tsquery raises on malformed input; fall back to strict.
            match_predicate = tsvec.op("@@")(tsquery_strict)
    else:
        match_predicate = tsvec.op("@@")(tsquery_strict)
    # Rank uses the STRICT query so exact-token matches outrank
    # synonym-only matches. The expansion exists to surface rows,
    # not to rerank them above literal matches.
    rank = func.ts_rank_cd(tsvec, tsquery_strict).label("rank")

    fts_stmt = (
        select(Image, rank)
        .where(
            Image.user_id == user_id,
            Image.deleted_at.is_(None),
            match_predicate,
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
