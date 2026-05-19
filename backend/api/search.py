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
from backend.models import Face, FaceDetection, Image, ImagePerson, Person, User
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
# Weights bias toward CLIP because text-matches are sparser — when
# they exist they're almost always the right answer, but for a
# natural-language query like "teacher teaching matrix math" the
# image summary won't share every keyword. CLIP bridges that gap.
# Text is the boost; CLIP is the lead.
_W_CLIP = 0.70
_W_TEXT = 0.30


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
# Was 0.22 — produced false negatives on legitimate semantic queries
# where the image cosine sits in the 0.18-0.21 band. Live debug on
# "teacher teaching matrix math" → the matching whiteboard photo
# scored 0.194 (just below 0.22) and got dropped before the merge
# ever saw it, even though FTS could have boosted it. 0.16 keeps
# real semantic matches while still excluding orthogonal noise
# (unrelated images on the same domain usually sit at 0.10-0.14).
_CLIP_MIN_SIM_KEEP = 0.16
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


# Tokens that, when present in a query, mean "the logged-in user."
# Matched case-insensitively as standalone tokens (so "memory" /
# "items" / "midnight" don't trigger). Lowercase here; the regex
# below adds word boundaries.
_SELF_TOKENS: frozenset[str] = frozenset({"me", "myself", "i", "my"})
_SELF_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _SELF_TOKENS) + r")\b",
    re.IGNORECASE,
)


def _extract_self_intent(q: str) -> tuple[bool, str]:
    """Return (mentions_self, query_with_self_tokens_stripped).

    The stripped query is what we pass to CLIP / FTS so the model
    doesn't try to literal-match the word "me" against captioned
    text (which catches anything saying "the man" / "memorial" /
    "remember" depending on the encoder's vocabulary). The intent
    flag drives the downstream image-set restriction.

    "photo of me"          → (True, "photo of")
    "me at the beach"      → (True, "at the beach")
    "i was hiking"         → (True, "was hiking")
    "matrix math"          → (False, "matrix math")
    "remembering my trip"  → (False, "remembering trip")  # `my` stripped
                                                          # because it
                                                          # adds nothing
    """
    if not _SELF_TOKEN_RE.search(q):
        return False, q
    stripped = _SELF_TOKEN_RE.sub(" ", q)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    # If stripping leaves filler words like "of" / "at" hanging,
    # those would noise CLIP slightly — clean trailing/leading
    # short prepositions. Don't be aggressive; just the obvious ones.
    stripped = re.sub(r"^(of|at|in|with|by|on|for|to|from)\b\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s+(of|at|in|with|by|on|for|to|from)$", "", stripped, flags=re.IGNORECASE)
    return True, stripped


async def _resolve_self_image_ids(
    session: AsyncSession, user: User,
) -> set | None:
    """Find every image of the logged-in user.

    A user's "self" Person is the row whose `display_name` equals
    the user's account `display_name` (per §C4.2 — the "Me" → real
    name binding). Once that Person is identified, an image counts
    as "of them" if it has either:

      - A `face_detections.face_id.person_id` match (face was
        clustered / explicitly labelled into that Person), OR
      - An `image_persons` direct association (user multi-select
        tagged the image as containing them, even when no face
        was detected — covers tag-as-person flow from §D8).

    Returns the set of matching image UUIDs, or `None` when we
    can't determine "self" (no display name, no Person, no
    consent — all degrade to "skip the person filter, fall back
    to text/visual ranking alone").
    """
    # The user's self-Person can be labeled in one of two ways:
    #   1. With their account's display_name (C4.2 binding — what new
    #      labels go through).
    #   2. The literal string "Me" (legacy convention from before
    #      C4.2 or when the user hasn't set a display name yet).
    # We accept either. If a user has BOTH (rare — typically a Person
    # merge collapsed them), their image sets union.
    candidates: list[str] = ["me"]  # always include literal "Me"
    dname = (user.display_name or "").strip()
    if dname:
        candidates.append(dname.lower())

    persons = (
        await session.execute(
            select(Person.id)
            .where(
                Person.user_id == user.id,
                func.lower(Person.display_name).in_(candidates),
            )
        )
    ).scalars().all()
    if not persons:
        return None

    # Image IDs via face_detections.face -> face.person_id IN persons.
    fd_rows = (
        await session.execute(
            select(FaceDetection.image_id)
            .join(Face, FaceDetection.face_id == Face.id)
            .where(
                FaceDetection.user_id == user.id,
                Face.person_id.in_(persons),
            )
        )
    ).all()
    # Image IDs via direct image_persons association.
    ip_rows = (
        await session.execute(
            select(ImagePerson.image_id)
            .where(ImagePerson.person_id.in_(persons))
        )
    ).all()

    image_ids = {r[0] for r in fd_rows} | {r[0] for r in ip_rows}
    return image_ids


@router.get("/", response_model=list[ImageSearchHit])
async def semantic_search(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: int = 30,
) -> list[dict]:
    # Reject empty-after-trim and ultra-short queries early. The CLIP +
    # FTS layers downstream both degrade badly on single characters
    # (CLIP gives ~0.25 to every image, FTS falls back to ILIKE which
    # then matches every file that contains the letter). Returning
    # nothing for q < 2 is the only correct answer to "what does the
    # user mean by 'a'?".
    q_clean = q.strip()
    if len(q_clean) < 2:
        return []

    # --- person-aware rewrite ------------------------------------------
    # "photo of me" used to land on whatever image happened to contain
    # the literal words "photo" and "me" in its summary — usually a
    # random photo of someone else. Now we detect self-tokens (me /
    # myself / i / my), resolve them to the logged-in user's Person
    # (via §C4.2 display-name binding), and after the regular CLIP +
    # FTS scoring runs, hard-filter results to images that actually
    # contain that person's face or have been tagged as them.
    #
    # The CLIP / FTS query passed downstream is the SCRUBBED version
    # — "photo of me" → "photo" — so the encoder isn't biased toward
    # whatever it learned "me" looks like (typically: any human in a
    # selfie pose).
    mentions_self, q_for_models = _extract_self_intent(q_clean)
    self_image_ids: set | None = None
    if mentions_self:
        try:
            self_image_ids = await _resolve_self_image_ids(session, user)
        except Exception:
            logger.exception("search: resolve self image ids failed")
            self_image_ids = None
        # If "me" was the ONLY thing in the query, q_for_models is
        # empty — there's nothing for CLIP / FTS to score, so we
        # short-circuit and just return the user's photos by recency.
        if not q_for_models:
            if not self_image_ids:
                return []
            rows = (
                await session.execute(
                    select(Image)
                    .where(
                        Image.user_id == user.id,
                        Image.deleted_at.is_(None),
                        Image.id.in_(self_image_ids),
                    )
                    .order_by(Image.uploaded_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return [
                ImageSearchHit(
                    **ImageRead.model_validate(img, from_attributes=True).model_dump(),
                    score=1.0,
                )
                for img in rows
            ]
        # Otherwise fall through with the scrubbed query.
        q_clean = q_for_models

    # --- text pass (cheap; runs even when CLIP / [ml] extras unavailable) --
    text_hits = await _text_search(session, user.id, q_clean, limit=limit)

    # --- CLIP pass (gracefully degrades when [ml] extras aren't installed)
    # CLIP text encoding goes through the shared ML executor so a
    # search query during a running backfill queues briefly behind
    # any in-flight Florence call instead of fanning into another
    # thread that races for the GIL.
    from backend.vision.inference_pool import run_in_inference_pool
    clip_hits: dict = {}
    try:
        query_vec = await run_in_inference_pool(_encode_text_sync, q_clean)
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
    #
    # When the candidate set is big (> 20 rows passed all earlier
    # gates), tighten the floor. A large candidate set after the
    # noise gates have already fired usually means the query is
    # ambiguous AND the library is doing broad keyword expansion —
    # the user wants the top few, not 50 weak matches. Stepped
    # tightening so the threshold doesn't slam shut on a legitimately-
    # broad query.
    if ranked:
        # Two-gate cutoff. Live "matrix math" debug: top was 0.6652
        # (whiteboard math photo, correct), then 9 results in
        # 0.30-0.43 band that have ZERO connection to matrices or
        # math (stairs, snowy fields, eerie room, driving). The old
        # `_RELATIVE_FLOOR = 0.45` cutoff (0.6652 × 0.45 = 0.30)
        # passed every one of them through.
        #
        # New rule: always keep result #1, then for the rest require
        # BOTH:
        #   - score >= top_score × 0.70  (within 30 % of best)
        #   - score >= 0.40 absolute      (above CLIP's noise band)
        #
        # CLIP's typical "vague visual relatedness" floor is ~0.18-0.30
        # — anything below 0.40 is essentially "image contains a
        # human / scene" with no semantic match to the query. The 70 %
        # rule catches the case where the top is mid-band (0.4-0.5)
        # and a single peer is competitive but the tail is noise.
        #
        # Quality: "matrix math" → just the whiteboard photo. "person
        # on stairs" → both stair photos (their scores are tightly
        # clustered and both above 0.40). "cat on laptop" → just the
        # cat-on-laptop photo.
        top_score = ranked[0][1]
        rel_cut = top_score * 0.70
        abs_cut = 0.40
        ranked = [
            (img, sc) for (i, (img, sc)) in enumerate(ranked)
            if i == 0 or (sc >= rel_cut and sc >= abs_cut)
        ]

    # Person-aware hard filter (see _extract_self_intent above). If the
    # query mentioned self-tokens AND we resolved the user's Person,
    # drop every result that doesn't actually contain them. This is the
    # difference between "photo of me" returning a random man's selfie
    # vs. returning the user's own selfie. When we couldn't resolve a
    # Person (no display name set, or no Person row yet), we silently
    # skip the filter — the search degrades to text+visual on the
    # scrubbed query, which is still better than returning nothing.
    if mentions_self and self_image_ids is not None:
        ranked = [(img, sc) for (img, sc) in ranked if img.id in self_image_ids]

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
    """Top-k visual + textual semantic matches by CLIP cosine.

    Returns {image_id: (image, score)} where score is the MAX of:
      - cosine(query, clip_embedding)        — image visual semantics
      - cosine(query, summary_clip_embedding) — image summary semantics
    Either signal can pull a row through. That's the difference
    between "place where I had coffee" matching a cafe photo via
    its summary's text vs. only via the visual CLIP score (which
    might be weak when the photo is, say, a closeup of a cup
    instead of a wide cafe shot).

    Two rounds (image side + summary side) because the HNSW indexes
    are PER COLUMN — we can't ORDER BY a min-of-two-distances and
    still hit the index. Unioning the top-k from each pass is
    fast (each is a sub-millisecond index probe) and gives both
    signals an equal shot at the candidate set.
    """
    qv = query_vec.tolist()
    image_dist = Image.clip_embedding.cosine_distance(qv)
    text_dist = Image.summary_clip_embedding.cosine_distance(qv)

    base_where = (
        Image.user_id == user_id,
        Image.deleted_at.is_(None),
    )

    # Image-side top-k: walks images_clip_embedding_idx (HNSW).
    image_stmt = (
        select(Image, image_dist.label("distance"))
        .where(*base_where, Image.clip_embedding.is_not(None))
        .order_by(image_dist.asc())
        .limit(limit)
    )
    # Summary-side top-k: walks images_summary_clip_emb_idx (HNSW).
    summary_stmt = (
        select(Image, text_dist.label("distance"))
        .where(*base_where, Image.summary_clip_embedding.is_not(None))
        .order_by(text_dist.asc())
        .limit(limit)
    )

    out: dict = {}
    # Image-side pass.
    for image, dist in (await session.execute(image_stmt)).all():
        sim = 1.0 - float(dist)
        if sim <= 0:
            continue
        out[image.id] = (image, sim)
    # Summary-side pass — MAX into the same dict so the stronger
    # signal wins per row.
    for image, dist in (await session.execute(summary_stmt)).all():
        sim = 1.0 - float(dist)
        if sim <= 0:
            continue
        prev = out.get(image.id)
        if prev is None or sim > prev[1]:
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

    # FTS path reads the stored `summary_tsv` column (generated-always
    # tsvector, GIN-indexed via `ix_images_summary_tsv`). Index gives
    # bitmap-index-scan + ts_rank_cd recheck — O(log N + k) instead of
    # O(N) seq-scan-with-on-the-fly-tsvector.
    #
    # Trade-off: the stored column covers summary + summary_topic +
    # summary_points + original_filename. NOT summary_signals /
    # scene_label / indoor_outdoor / content_type — queries that hit
    # ONLY those fall through to the ILIKE substring path below.
    english = cast(literal("english"), postgresql.REGCONFIG)
    tsvec = Image.summary_tsv

    # KEY CHANGE: switched from AND-joined `plainto_tsquery` to
    # OR-joined token query. `plainto_tsquery` produces
    # `'a' & 'b' & 'c'` — every word must match. For natural-language
    # queries like "teacher teaching matrix math" against an image
    # whose summary says "Man Writing Math Equations in Classroom
    # Whiteboard", only "math" overlaps and the AND form returns 0
    # hits even though it's clearly the right answer. OR-joining lets
    # partial keyword matches surface; ts_rank_cd then up-weights rows
    # that match MORE of the query, so "more keywords matched" still
    # outranks "one weak keyword matched."
    #
    # `to_tsquery` requires sanitized tokens — strip non-word chars
    # before joining with ` | `. `synonyms.expand_query_to_tsquery`
    # produces a similarly OR'd expansion with synonyms per token,
    # which we union into the predicate.
    raw_tokens = [t for t in re.split(r"\W+", q) if t]
    or_query_str = " | ".join(raw_tokens) if raw_tokens else None

    tsquery_or = None
    if or_query_str:
        try:
            tsquery_or = func.to_tsquery(english, or_query_str)
        except Exception:
            tsquery_or = None
    if tsquery_or is None:
        # Fall back to plainto if our manual tokenization choked
        # (rare — non-ASCII queries, etc.).
        tsquery_or = func.plainto_tsquery(english, q)

    expanded_str = expand_query_to_tsquery(q)
    if expanded_str:
        try:
            tsquery_expanded = func.to_tsquery(english, expanded_str)
            match_predicate = tsvec.op("@@")(tsquery_or) | tsvec.op("@@")(tsquery_expanded)
        except Exception:
            match_predicate = tsvec.op("@@")(tsquery_or)
    else:
        match_predicate = tsvec.op("@@")(tsquery_or)
    # Rank uses the OR'd token query so partial-overlap rows still
    # score — but ts_rank_cd inherently weights by "how many of the
    # query lexemes matched the doc," so a row matching 3 of 4
    # tokens outranks one matching 1 of 4. Better than the previous
    # plainto-based rank which required ALL tokens to even land in
    # the candidate set.
    rank = func.ts_rank_cd(tsvec, tsquery_or).label("rank")

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
        # FTS gave nothing — try a substring scan, but only when the
        # query is long enough that substring matching is meaningful.
        # `%a%` matched every file's summary (every English summary
        # contains the letter "a"); the result was the search bar
        # returning the entire library on any single-character or
        # very common prefix. 3-char minimum AND a regex
        # word-boundary match (`\m<term>\M`) so "math" doesn't
        # match "mathematics" → matrix? Yes it does, but "ma" won't
        # match every word that starts with "ma".
        if len(q.strip()) < 3:
            return {}
        # Use `~*` (case-insensitive regex match) with \m \M as
        # word boundaries. Single regex bind across each surface
        # column; on rare malformed input we fall back to the old
        # %q% form so a "(" in the query doesn't break search
        # entirely.
        import re as _re
        boundary = rf"\m{_re.escape(q.strip())}\M"
        like = f"%{q.strip()}%"
        signals_as_text = cast(Image.summary_signals, sa.Text)
        try:
            like_stmt = (
                select(Image)
                .where(
                    Image.user_id == user_id,
                    Image.deleted_at.is_(None),
                    or_(
                        Image.summary.op("~*")(boundary),
                        Image.summary_topic.op("~*")(boundary),
                        Image.original_filename.ilike(like),  # filenames keep substring — "neuthek" should match "neuthek.mp4"
                        Image.scene_label.op("~*")(boundary),
                        Image.indoor_outdoor.op("~*")(boundary),
                        Image.content_type.op("~*")(boundary),
                        signals_as_text.op("~*")(boundary),
                    ),
                )
                .limit(limit)
            )
            execed = await session.execute(like_stmt)
        except Exception:
            # Malformed regex (rare) — re-run with plain ILIKE.
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
            execed = await session.execute(like_stmt)
        out: dict = {}
        for image in execed.scalars():
            out[image.id] = (image, 0.6)
        return out

    max_rank = max(float(r[1]) for r in rows) or 1.0
    return {
        image.id: (image, min(1.0, float(r) / max_rank))
        for image, r in rows
    }
