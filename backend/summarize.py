"""Phase 11 v2 — AI Vision content summary.

Runs after upload as a BackgroundTask (mirroring the Pass B face scan).
Per-category dispatch:

    image    → Florence-2 detailed caption (+ <OCR> for whiteboard /
               document / screenshot scenes) → Qwen2.5-Instruct rewriter
               composes one natural search-friendly sentence using the
               caption, named people, OCR text, and scene metadata.
    video    → ffmpeg single keyframe → image branch.
    document → server-side text extraction (pypdf / python-docx / openpyxl /
               plaintext) → BART abstractive (sumy LSA fallback).

All three paths populate `images.summary`, `summary_topic`,
`summary_points`, `summary_generated_at`, and flip `pending_summary =
false` in one update.

Each helper is best-effort: optional deps that fail to import (Florence-2
without `[ml]` extras, Qwen2.5 weights uncached, sumy without nltk data)
fall through to a regex-based deterministic path so a torch-less test
environment still produces a usable summary.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.image import fetch_original, fetch_served
from backend.models import Face, FaceDetection, Image, Person

logger = logging.getLogger(__name__)


# --- public dataclass ------------------------------------------------------


@dataclass
class SummaryResult:
    topic: str
    summary: str
    points: list[str]
    # Optional category written to `Image.content_type` when the
    # summarizer can infer one — populated for video / document /
    # audio rows by the rule-based classifier below. Images get
    # their content_type from the vision pipeline upstream so leave
    # it None here and the existing image path's `content_type`
    # column-write isn't disturbed.
    content_type: Optional[str] = None


# --- public entry point ----------------------------------------------------


async def summarize_image_id(session: AsyncSession, image_id: UUID) -> None:
    """Load the image row, run the right strategy, write the summary back.

    Always overwrites the existing summary — switching captioning models or
    upgrading the doc extractor needs the row to be regenerable. Callers
    that don't want to redo work should check `pending_summary` themselves
    before invoking. The upload path only invokes this once per upload.
    """
    if not settings.summarize_enabled:
        return

    image = await session.get(Image, image_id)
    if image is None or image.deleted_at is not None:
        return

    # Prefer the original bytes when they're still around (highest
    # fidelity for vision models). For video rows whose original has
    # already been dropped by the transcode pipeline (per the "only
    # keep the served copy" policy), fall back to the served MP4 —
    # ffmpeg can extract keyframes from either, and the served
    # variant is the only copy that exists. Documents similarly
    # use whatever the row has.
    try:
        if image.original_blob_key is not None:
            raw_bytes, _mime = await fetch_original(image)
        elif image.served_blob_key is not None:
            raw_bytes, _mime = await fetch_served(image)
        else:
            raise RuntimeError("image row has neither original nor served bytes")
    except Exception:
        logger.exception(
            "summarize: failed to fetch source bytes for %s", image_id
        )
        await _mark_done(image_id, None)
        return

    # Look up named people while we're still in the async event loop —
    # `_dispatch` runs in a thread and can't issue DB queries against the
    # async engine. Empty list when no consent / no faces yet / no
    # named persons in this image.
    named_people: list[str] = []
    if image.category == "image":
        named_people = await _load_named_people(
            session, image.id, image.user_id
        )

    # Pre-load the existing tag labels in async land for the same
    # reason. The previous code accessed `image.tags` inside the sync
    # `_dispatch` thread; lazy-loading a relationship there blows up
    # with "greenlet_spawn has not been called" and leaves the session
    # in a PendingRollback state that makes `_mark_done` fail too.
    # Materializing the labels upfront keeps the sync path read-only.
    pre_tag_labels = await _load_image_tag_labels(session, image.id)

    # Route through the dedicated single-thread ML executor so two
    # concurrent summarize calls serialize instead of fighting for the
    # GIL with the asyncio event loop. With the default thread pool
    # the user couldn't log in while a backfill was running because
    # every request handler queued behind the model threads.
    from backend.vision.inference_pool import run_in_inference_pool
    try:
        result = await run_in_inference_pool(
            _dispatch, image, raw_bytes, named_people, pre_tag_labels
        )
    except Exception:
        logger.exception("summarize: dispatch failed for %s", image_id)
        result = None

    # Capture summary_signals BEFORE awaiting anything else — the
    # `image` object's session may be poisoned by a greenlet-less
    # lazy load that happened inside _dispatch; reading attributes
    # later goes through state-load paths that re-raise. We grab the
    # in-memory dict (set by `_summarize_image`) now, while we're
    # still in the same thread context, and pass it positionally
    # to `_mark_done` which uses a fresh session for the UPDATE.
    signals = None
    try:
        signals = image.__dict__.get("summary_signals")
    except Exception:
        pass

    try:
        await _mark_done(image_id, result, signals)
    except Exception:
        logger.exception("summarize: _mark_done failed for %s", image_id)


async def _load_image_tag_labels(
    session: AsyncSession, image_id
) -> list[str]:
    """Return the labels on this image's existing tags.

    Run from the async side so the sync `_dispatch` thread doesn't have
    to lazy-load `image.tags` itself (which fails with a greenlet
    error). Caller passes the resulting list to `_summarize_image` as
    `pre_tag_labels`.
    """
    from backend.models import ImageTag, Tag
    rows = (
        await session.execute(
            select(Tag.label)
            .join(ImageTag, ImageTag.tag_id == Tag.id)
            .where(ImageTag.image_id == image_id)
        )
    ).scalars().all()
    return [r for r in rows if r]


async def _load_named_people(
    session: AsyncSession, image_id, user_id
) -> list[str]:
    """Distinct, ordered names of identified people in this image.

    Pass B (faces) populates `face_detections.face_id → faces.person_id →
    persons.display_name`. Anonymous detections (face exists but no person
    named yet) are filtered out — generic captions are better than ones
    that say "and another person" with no anchor.
    """
    # Min(bbox_x) lets us order persons left-to-right even with DISTINCT —
    # plain ORDER BY bbox_x violates the SELECT-list rule on Postgres.
    rows = (
        await session.execute(
            select(Person.display_name, func.min(FaceDetection.bbox_x).label("x"))
            .join(Face, Face.person_id == Person.id)
            .join(FaceDetection, FaceDetection.face_id == Face.id)
            .where(
                FaceDetection.image_id == image_id,
                FaceDetection.user_id == user_id,
                Person.display_name.is_not(None),
                Person.display_name != "",
            )
            .group_by(Person.display_name)
            .order_by("x")
        )
    ).all()
    return [r[0] for r in rows if r[0]]


async def _mark_done(
    image_id,
    result: Optional[SummaryResult],
    signals: Optional[dict] = None,
) -> None:
    """Persist the summary row after a worker run.

    Takes only the id + flat data (no ORM-tracked Image instance).
    Accessing attributes on an Image object that's bound to a
    poisoned session re-triggers the same PendingRollbackError on a
    fresh session, because the attribute access goes through the
    InstanceState's load_expired/refresh path. Writing via a plain
    `UPDATE ... WHERE id = :id` on a fresh session sidesteps that
    entirely.

    When `result is None` the dispatch produced nothing usable — the LLM
    crashed, the model wasn't loadable, the file was corrupt, etc. In
    that case we deliberately keep `pending_summary=True` so the row
    stays visible to the regular backfill pass (which targets
    `pending_summary=true OR summary IS NULL`). Marking it complete
    would silently drop the row from the queue and leave the user with
    no summary forever.
    """
    from sqlalchemy import update as sa_update

    from backend.db import SessionLocal

    values: dict = {}
    if result is not None:
        values["summary"] = result.summary
        values["summary_topic"] = result.topic
        values["summary_points"] = result.points
        values["pending_summary"] = False
        values["summary_generated_at"] = datetime.now(timezone.utc)
        if signals:
            values["summary_signals"] = signals
        if result.content_type:
            values["content_type"] = result.content_type
        # Encode the summary in CLIP text space so search can score
        # against text-to-text semantic distance (not just image-to-text
        # visual cosine). Topic is prepended because it often carries
        # the noun phrase that anchors the semantics ("Cat Sleeping
        # On Laptop Keyboard" vs. the longer prose summary).
        try:
            embedding = await asyncio.to_thread(
                _encode_summary_for_search, result.summary, result.topic,
            )
        except Exception:
            logger.exception("summary embed: to_thread failed")
            embedding = None
        if embedding is not None:
            values["summary_clip_embedding"] = embedding
    else:
        values["summary_generated_at"] = datetime.now(timezone.utc)
        # Keep pending_summary alone so the row gets retried.

    # SYNC WRITE on purpose. The previous `async with SessionLocal()`
    # path opens a connection from the process-wide async pool, which
    # is bound to whatever event loop FIRST created it. When the ML
    # worker's heartbeat task crashes (asyncpg's "Task got Future
    # attached to a different loop" error pattern), the pool's
    # connection futures get poisoned. The next `_mark_done` call
    # from a different task inherits the bad state and the row write
    # silently fails — summarize ran, summary was generated, but the
    # row stays at `pending_summary=true` forever.
    #
    # psycopg2 sync connection sidesteps all of that: own its own
    # connection, no event-loop binding, no shared pool. The write
    # is one round-trip so the blocking call is cheap (~5 ms).
    # `asyncio.to_thread` keeps the event loop responsive while we
    # do it, same pattern as the embedding call above.
    await asyncio.to_thread(_mark_done_sync, image_id, values)


def _mark_done_sync(image_id, values: dict) -> None:
    """Synchronous row update — runs in a thread to avoid the
    asyncpg-pool loop-binding issue described in `_mark_done`.
    psycopg2 dependency is already in the base deps for Alembic;
    no new package required."""
    import json
    import psycopg2

    sync_url = settings.database_url_sync.replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    cols: list[str] = []
    params: list = []
    for k, v in values.items():
        if k == "summary_points":
            cols.append(f"{k} = %s::jsonb")
            params.append(json.dumps(v) if v is not None else None)
        elif k == "summary_signals":
            cols.append(f"{k} = %s::jsonb")
            params.append(json.dumps(v) if v is not None else None)
        elif k == "summary_clip_embedding":
            cols.append(f"{k} = %s::vector")
            params.append(v)
        else:
            cols.append(f"{k} = %s")
            params.append(v)
    params.append(str(image_id))
    sql = f"UPDATE images SET {', '.join(cols)} WHERE id = %s"
    conn = psycopg2.connect(sync_url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ---------- Content-type classification (rule-based) -----------------
#
# Maps the AI summary + filename + extension to one of a small fixed
# taxonomy. The values land in `Image.content_type` so the gallery
# can facet by them and search can match queries like "find my
# receipts" or "show me tutorials" against this column directly.
#
# Rule-based on purpose: a separate Qwen round-trip per upload would
# double the summarize budget for a benefit that mostly disappears
# behind keyword overlap. Easy to evolve into LLM-prompted
# classification later if precision matters more than latency.

_VIDEO_CATEGORIES: dict[str, list[str]] = {
    # category → marker phrases (lowercase, substring match against summary)
    "tutorial":    ["tutorial", "how to", "step-by-step", "guide", "walkthrough", "lesson"],
    "screencast":  ["screen recording", "desktop", "screencast", "tutorial recording"],
    "recipe":      ["recipe", "cooking", "kitchen", "baking", "ingredients", "stove", "oven"],
    "sports":      ["game", "match", "ball", "field", "court", "stadium", "running", "swimming"],
    "music":       ["concert", "band", "musician", "guitar", "drums", "singing", "performance"],
    "gaming":      ["gameplay", "gaming", "controller", "console", "minecraft", "fortnite"],
    "vlog":        ["vlog", "talking", "selfie", "blogger", "speaking to camera"],
    "family":      ["family", "child", "kids", "baby", "birthday", "wedding", "anniversary"],
    "travel":      ["travel", "vacation", "trip", "mountain", "beach", "city skyline"],
    "animation":   ["animation", "animated", "cartoon", "3d render"],
    "presentation":["slide", "presentation", "powerpoint", "lecture", "speaker"],
}

_DOC_CATEGORIES: dict[str, list[str]] = {
    "code":         [],  # by extension (see DOC_EXT_CATEGORIES)
    "spreadsheet":  [],
    "presentation":[],
    "contract":     ["contract", "agreement", "lease", "terms and conditions", "party of the first", "hereby"],
    "receipt":      ["receipt", "invoice", "total due", "subtotal", "transaction", "purchase"],
    "recipe":       ["recipe", "ingredients", "preheat", "tablespoon", "cooking instruction"],
    "manual":       ["manual", "user guide", "installation", "specifications", "warranty"],
    "report":       ["report", "executive summary", "findings", "methodology", "abstract"],
    "letter":       ["dear", "sincerely", "regards", "letter", "memo"],
    "form":         ["please fill", "checkbox", "questionnaire", "form", "applicant"],
    "notes":        ["notes", "todo", "to-do", "journal", "diary"],
    "legal":        ["court", "plaintiff", "defendant", "statute", "ordinance"],
    "research":     ["abstract", "hypothesis", "references", "doi:", "citation", "et al."],
}

_DOC_EXT_CATEGORIES: dict[str, str] = {
    "py": "code", "js": "code", "ts": "code", "tsx": "code", "jsx": "code",
    "rs": "code", "go": "code", "rb": "code", "java": "code", "kt": "code",
    "c": "code", "cpp": "code", "h": "code", "hpp": "code", "cs": "code",
    "swift": "code", "m": "code", "mm": "code", "sh": "code", "ps1": "code",
    "sql": "code", "r": "code", "lua": "code", "scala": "code",
    "xlsx": "spreadsheet", "xls": "spreadsheet", "csv": "spreadsheet",
    "tsv": "spreadsheet", "ods": "spreadsheet",
    "pptx": "presentation", "ppt": "presentation", "odp": "presentation",
    "key": "presentation",
}


def _classify_content(
    summary: str | None,
    filename: str | None,
    kind: str,  # "video" | "document" | "audio"
) -> Optional[str]:
    """Return a content_type label for the row, or None when no
    rule fires. `kind` selects the taxonomy. Filename extension is
    consulted first when it's a strong signal (code / spreadsheet /
    presentation) — extension trumps body content in those cases
    because a .py file is code regardless of what its contents
    parse as in plain English.
    """
    if kind == "document" and filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in _DOC_EXT_CATEGORIES:
            return _DOC_EXT_CATEGORIES[ext]
    table = _VIDEO_CATEGORIES if kind == "video" else _DOC_CATEGORIES
    text = (summary or "").lower()
    if not text:
        return None
    # Score each category by how many marker phrases it hits;
    # winner takes the top, tie → first in insertion order.
    best_cat = None
    best_hits = 0
    for cat, markers in table.items():
        if not markers:
            continue
        hits = sum(1 for m in markers if m in text)
        if hits > best_hits:
            best_cat = cat
            best_hits = hits
    return best_cat


def _encode_summary_for_search(
    summary: str | None, topic: str | None,
) -> list[float] | None:
    """Run the summary + topic through the CLIP text encoder and
    return a list[float] suitable for pgvector. Returns None on any
    failure (ml extras not installed, encoder OOM, empty text) so
    `_mark_done` can carry on with a NULL embedding — the search
    path treats NULL as "no text-side semantic signal" and falls
    back to image-cosine + FTS, which is the prior behavior.
    """
    parts = [s for s in (topic, summary) if s and s.strip()]
    if not parts:
        return None
    text = " — ".join(parts)
    # Truncate to a safe token budget so the encoder doesn't reject
    # very long summaries. CLIP ViT-L-14 tokenizer caps at 77 tokens;
    # ~250 characters is well under that for English text.
    if len(text) > 250:
        text = text[:250]
    try:
        from backend.vision.runtime import encode_text_cached
        vec = encode_text_cached(text)
    except Exception:
        logger.exception("summary embed failed for text=%r", text[:60])
        return None
    return [float(x) for x in vec]


def _dispatch(
    image: Image,
    raw_bytes: bytes,
    named_people: list[str],
    pre_tag_labels: list[str] | None = None,
) -> Optional[SummaryResult]:
    if image.category == "image":
        return _summarize_image(image, raw_bytes, named_people, pre_tag_labels or [])
    if image.category == "video":
        return _summarize_video(image, raw_bytes)
    if image.category == "document":
        return _summarize_document(image, raw_bytes)
    # Catch-all for "other" — archives (.zip, .tar.gz, etc.) and any
    # mime type we don't have a dedicated summarizer for. Without this,
    # the row's `summary` stays NULL forever and the progress banner
    # is stuck at N-1 of N. We emit a minimal description derived from
    # the filename + ext so the row counts as summarized and the FE
    # can search by the filename.
    return _summarize_other(image)


def _summarize_other(image: Image) -> SummaryResult:
    fname = image.original_filename or "file"
    stem = fname.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    ext = (fname.rsplit(".", 1)[-1].lower() if "." in fname else "") or ""
    ext_label = {
        "zip": "Archive (.zip)",
        "tar": "Archive (.tar)",
        "gz":  "Archive (.gz)",
        "rar": "Archive (.rar)",
        "7z":  "Archive (.7z)",
    }.get(ext, ext.upper() if ext else "File")
    topic = stem[:80] if stem else ext_label
    summary = f"{ext_label} — {stem}." if stem else f"{ext_label}."
    return SummaryResult(topic=topic or "File", summary=summary, points=[])


# --- image -----------------------------------------------------------------


# Scenes / content types where reading visible text materially improves
# the summary. We don't OCR every cat photo — Florence-2 OCR is ~1-2 s on
# GPU, would dominate latency on bulk re-summarize.
_OCR_SCENES = frozenset({
    "classroom", "lecture_room", "conference_room", "computer_room",
    "library", "office", "bookstore", "whiteboard",
})

_OCR_CONTENT_TYPES = frozenset({"screenshot", "document"})


def _summarize_image(
    image: Image,
    raw_bytes: bytes,
    named_people: list[str],
    pre_tag_labels: list[str] | None = None,
) -> SummaryResult:
    """Florence-2 detailed caption + scene-gated OCR + LLM rewrite.

    Flow:
      1. Florence-2 <MORE_DETAILED_CAPTION>  — dense scene description.
      2. Florence-2 <OCR>                    — only for whiteboard /
                                               classroom / screenshot etc.
      3. Qwen2.5-Instruct rewrite            — one natural sentence
                                               combining all signals.
      4. Regex fallback (clean+splice)       — when the LLM is unavailable.

    The fallback chain means the function still returns a usable summary
    when only some of the models are loadable, instead of crashing or
    emitting a generic placeholder.
    """
    raw_caption = (_caption_image(raw_bytes) or "").strip()

    needs_ocr = (
        (image.content_type or "") in _OCR_CONTENT_TYPES
        or (image.scene_label or "") in _OCR_SCENES
    )
    ocr_text = _ocr_image(raw_bytes) if needs_ocr else None

    # C2a — multi-model image pipeline. Each stage is best-effort and
    # returns None on failure so a missing model degrades the summary
    # instead of breaking it.
    regions = _florence_regions(raw_bytes)
    objects = _florence_objects(raw_bytes)
    # CLIP concept tags + heavy VLM are wired in C2b / C2e.
    concepts: Optional[list[str]] = None
    vlm_description: Optional[str] = None
    try:
        from backend.vision.concepts import top_concepts
        concepts = top_concepts(raw_bytes)
    except Exception:
        concepts = None
    try:
        if getattr(settings, "heavy_vlm_enabled", False):
            from backend.vision.runtime import get_internvl2  # noqa: F401
            vlm_description = _vlm_describe(raw_bytes)
    except Exception:
        vlm_description = None

    # Existing image_tags labels — pre-loaded in async land by
    # `summarize_image_id` so the sync thread doesn't trip on lazy
    # relationship loading.
    pre_tags: list[str] = list(pre_tag_labels or [])

    summary = _llm_rewrite_summary(
        caption=raw_caption,
        names=named_people,
        ocr_text=ocr_text,
        scene=image.scene_label,
        setting=image.indoor_outdoor,
        content_type=image.content_type,
        tags=pre_tags or None,
        regions=regions,
        objects=objects,
        concepts=concepts,
        vlm_description=vlm_description,
    )

    # Persist the structured signals so re-summarization is idempotent
    # without re-running every stage from scratch and so C9 multi-axis
    # filtering can query them later. Empty / None inputs are dropped.
    #
    # Stash on `image.__dict__` directly (NOT `image.summary_signals = ...`)
    # to avoid going through SQLAlchemy's InstanceState. A setattr on the
    # session-tracked ORM attribute can trigger a state-load if the
    # column's expired in this thread, which calls await_only() without
    # a greenlet and poisons the session for `_mark_done`. Reading from
    # `image.__dict__` in the async wrapper is symmetric and equally safe.
    signals: dict = {}
    if regions: signals["regions"] = regions
    if objects: signals["objects"] = objects
    if concepts: signals["concepts"] = concepts
    if vlm_description: signals["vlm"] = vlm_description
    if signals:
        image.__dict__["summary_signals"] = signals

    if not summary:
        # Deterministic fallback — same path as v1.
        cleaned = _clean_caption(raw_caption)
        spliced = _splice_names(cleaned, named_people)
        summary = spliced or _fallback_image_summary(image, named_people)
        if ocr_text and "text" not in summary.lower():
            excerpt = ocr_text[:120].replace("\n", " ").strip()
            if excerpt:
                summary = f"{summary.rstrip('.')}. Visible text: {excerpt}."

    # Prefer the LLM topic when available — much richer than the heuristic
    # "Photo of Me" fallback. Pass the same signals the rewriter saw so
    # the topic stays consistent with the description.
    topic = _llm_compose_topic(
        caption=raw_caption,
        names=named_people,
        regions=regions,
        objects=objects,
        concepts=concepts,
        scene=image.scene_label,
        setting=image.indoor_outdoor,
        content_type=image.content_type,
    ) or _compose_topic(image, summary, named_people)

    points: list[str] = []
    if named_people:
        # Lead with names — the most useful search anchor.
        points.append("People: " + ", ".join(named_people))
    elif image.face_likelihood and image.face_likelihood > 0.5:
        points.append("Likely contains people")
    if image.content_type and image.content_type != "photo":
        points.append(f"Type: {image.content_type}")
    if image.scene_confidence and image.scene_label:
        points.append(
            f"Scene: {image.scene_label.replace('_', ' ')} "
            f"({int(image.scene_confidence * 100)}%)"
        )
    if image.indoor_outdoor and image.indoor_outdoor != "unknown":
        points.append(f"Setting: {image.indoor_outdoor}")
    if ocr_text:
        excerpt = ocr_text[:80].replace("\n", " ").strip()
        if excerpt:
            ellipsis = "…" if len(ocr_text) > 80 else ""
            points.append(f"Text: {excerpt}{ellipsis}")
    # Top tags from image_tags relationship if loaded; otherwise skip silently.
    tag_labels = []
    try:
        for it in image.tags or []:
            if it.tag is not None and it.tag.label:
                tag_labels.append(it.tag.label)
        if tag_labels:
            points.append("Tags: " + ", ".join(tag_labels[:5]))
    except Exception:
        pass

    return SummaryResult(topic=topic, summary=summary, points=points[:5])


# Filler phrases BLIP frequently leads with — "This is a photo of a man"
# is filler around the actual content "a man". Stripping them keeps the
# searchable terms (subject, scene, objects) and drops the framing.
# Order matters: longer, more specific prefixes must come before shorter
# subsets so we don't half-strip them.
_FILLER_PREFIXES = (
    r"^the (?:image|photo|picture) shows\s+",
    r"^in the (?:image|photo|picture),?\s+",
    r"^in this (?:image|photo|picture),?\s+",
    r"^this is a (?:photo|picture|photograph|image) of\s+",
    r"^this is an? (?:photo|picture|photograph|image)\s+",
    r"^an? (?:image|picture|photo|photograph) of\s+",
    r"^this is\s+",
    r"^there are\s+",
    r"^there is\s+",
)


# Patterns BLIP uses for generic person references, in order of priority
# (most specific first). Each gets exactly one substitution per caption.
_PERSON_PATTERNS = (
    r"\btwo people\b",
    r"\bthree people\b",
    r"\bseveral people\b",
    r"\ba group of people\b",
    r"\bgroup of people\b",
    r"\bpeople\b",
    r"\ba young man\b",
    r"\ba young woman\b",
    r"\ba man\b",
    r"\ba woman\b",
    r"\ba boy\b",
    r"\ba girl\b",
    r"\ba person\b",
    r"\bthe man\b",
    r"\bthe woman\b",
)


def _clean_caption(caption: str) -> str:
    """Strip BLIP filler and normalize awkward phrasings before name splicing.

    BLIP often pads captions with "This is a..." / "There is a..." which
    adds no semantic value and dilutes search relevance — the user is
    searching for "whiteboard math classroom", not for "this is a photo".
    Removing the filler also lets `_splice_names` produce natural sentences:
    "There is a man writing" → "There is Mr Koler writing" (awkward) becomes
    "A man writing" → "Mr Koler writing" (clean).

    Also rewrites "taking a picture/photo of himself|herself|themselves" to
    "taking a selfie", which is the term people actually search by.
    """
    if not caption:
        return caption

    cleaned = caption
    for pattern in _FILLER_PREFIXES:
        new, n = re.subn(pattern, "", cleaned, count=1, flags=re.IGNORECASE)
        if n > 0:
            cleaned = new
            break  # one prefix at most — chained strips would over-cut

    # Plural first so "pictures of themselves" doesn't get half-matched by
    # the singular pattern.
    cleaned = re.sub(
        r"\btaking (?:pictures|photos) of themselves\b",
        "taking selfies",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\btaking a (?:picture|photo) of (?:himself|herself|themselves)\b",
        "taking a selfie",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = cleaned.strip()
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
        if cleaned[-1] not in ".!?":
            cleaned += "."
    return cleaned


def _splice_names(caption: str, names: list[str]) -> str:
    """Substitute the first generic person reference with the named people.

    "A black and white photo of a man looking out a car window."
        + names=["Me"]
    → "A black and white photo of Me looking out a car window."

    Returns the caption unchanged when nothing matches or no names are
    known, so BLIP captions stay grammatical and search-friendly.
    """
    if not caption or not names:
        return caption
    if len(names) == 1:
        replacement = names[0]
    elif len(names) == 2:
        replacement = f"{names[0]} and {names[1]}"
    else:
        replacement = ", ".join(names[:-1]) + f", and {names[-1]}"

    spliced = caption
    matched = False
    for pattern in _PERSON_PATTERNS:
        new_caption, n = re.subn(
            pattern, replacement, spliced, count=1, flags=re.IGNORECASE
        )
        if n > 0:
            spliced = new_caption
            matched = True
            break
    if not matched:
        # No person pattern matched (e.g. landscape) — append parenthetical
        # so names still index for search without mangling the sentence.
        return f"{caption} (with {replacement})"

    return _polish_after_splice(spliced, names)


def _splice_names_all(caption: str, names: list[str]) -> str:
    """Replace every generic person reference in a (possibly multi-sentence)
    caption with the named people. Sister to `_splice_names` for the
    Qwen rewriter output path.

    The single-replacement `_splice_names` works for BLIP's one-clause
    captions ("a man taking a selfie" → "Me taking a selfie"). The C2
    Qwen rewriter emits 1–3 sentence descriptions that mention the
    subject multiple times ("A young man stands… The man wears… He
    holds…"). Replacing only the first reference left every later
    "the man" / "he" / "his" pointing at an unnamed entity, which is
    exactly the "summaries don't replace pronouns with detected
    people" complaint.

    Strategy:
      1. Replace each generic-noun pattern (a/the man, young man,
         person, etc.) with the name — but only the *first*
         occurrence per pattern, so we don't double-replace already-
         normalized references in the same sentence.
      2. Run the existing first-person pronoun polish so "his/her/him"
         flip to "my/me" when the subject is "Me" / "I".

    Returns the caption unchanged when `names` is empty.
    """
    if not caption or not names:
        return caption
    if len(names) == 1:
        replacement = names[0]
    elif len(names) == 2:
        replacement = f"{names[0]} and {names[1]}"
    else:
        replacement = ", ".join(names[:-1]) + f", and {names[-1]}"

    spliced = caption
    # First substitution per pattern, scanning each pattern in turn —
    # avoids cascading replacements like "a young man" → "Me" then
    # "a man" matching part of the new text. Patterns are ordered
    # specific-to-general in `_PERSON_PATTERNS` already.
    for pattern in _PERSON_PATTERNS:
        spliced, _ = re.subn(
            pattern, replacement, spliced, count=0, flags=re.IGNORECASE
        )

    return _polish_after_splice(spliced, names)


# Pronoun / grammar cleanup that only makes sense once names are inserted.
# BLIP says "a man that is taking a picture of himself with his phone";
# splicing yields "Me that is taking a picture of himself with his phone";
# this pass rewrites it to "Me taking a selfie with my phone".
_FIRST_PERSON_NAMES = {"me", "i"}


def _polish_after_splice(caption: str, names: list[str]) -> str:
    # 1. "[name] that is V-ing" / "[name] who is V-ing" → "[name] V-ing".
    #    Reads natural in English ("Mr Koler standing" beats "Mr Koler that
    #    is standing") and is what BLIP really meant.
    for name in names:
        esc = re.escape(name)
        caption = re.sub(
            rf"\b{esc}\s+(?:that|who)\s+is\s+",
            f"{name} ",
            caption,
        )

    # 2. First-person pronoun rewrite. When the spliced subject is "Me" or
    #    "I", any third-person pronouns BLIP emitted refer back to that
    #    subject and need to flip to first person — otherwise we get
    #    "Me taking a selfie with his cell phone".
    if any(n.lower() in _FIRST_PERSON_NAMES for n in names):
        caption = re.sub(
            r"\b(?:himself|herself|themselves)\b",
            "myself",
            caption,
            flags=re.IGNORECASE,
        )
        # "his" is unambiguously possessive; "her" is mostly possessive in
        # caption contexts ("her phone", "her face"), so map both to "my".
        caption = re.sub(r"\bhis\b", "my", caption, flags=re.IGNORECASE)
        caption = re.sub(r"\bher\b", "my", caption, flags=re.IGNORECASE)
        # Object pronoun "him" is rare but happens ("a dog next to him").
        caption = re.sub(r"\bhim\b", "me", caption, flags=re.IGNORECASE)

    return caption


def _compose_topic(
    image: Image, caption: str, named_people: list[str]
) -> str:
    """Pick a short, content-rich topic line. Named people lead — Drive's
    "People" facet works the same way."""
    cap_lower = caption.lower()
    is_selfie = "selfie" in cap_lower or "self portrait" in cap_lower
    is_group = (
        "group photo" in cap_lower
        or "group of people" in cap_lower
        or len(named_people) >= 2
    )

    if named_people:
        names = ", ".join(named_people)
        if is_selfie:
            return f"Selfie of {names}"
        if is_group:
            return f"Group photo · {names}"
        return f"Photo of {names}"

    if is_selfie:
        return "Selfie"
    if is_group:
        return "Group photo"
    if image.scene_label == "portrait" or image.scene_label == "group_photo":
        return image.scene_label.replace("_", " ").title()
    if image.face_likelihood and image.face_likelihood > 0.7 and (
        "person" in cap_lower or "man" in cap_lower or "woman" in cap_lower
    ):
        return "Portrait"
    if image.scene_label and image.scene_label not in ("portrait",):
        return image.scene_label.replace("_", " ").title()
    return "Photo"


def _fallback_image_summary(image: Image, named_people: list[str] | None = None) -> str:
    """Last-resort summary built from classifier columns + identified people.

    The earlier version produced "A classroom indoor image." with no
    reference to the named subjects — defeating the point of running the
    face pipeline in the first place. When we do have names, lead with
    them so the description reads "Photo of Mr Koler in a classroom
    (indoor)." which is both more useful and matches what a user would
    actually search by.
    """
    scene = (image.scene_label or "").replace("_", " ").strip()
    indoor = image.indoor_outdoor if image.indoor_outdoor and image.indoor_outdoor != "unknown" else None
    names = [n for n in (named_people or []) if n]
    if names:
        if len(names) == 1:
            who = names[0]
        elif len(names) == 2:
            who = f"{names[0]} and {names[1]}"
        else:
            who = ", ".join(names[:-1]) + f", and {names[-1]}"
        tail = []
        if scene:
            tail.append(f"in a {scene}" if scene[0].lower() not in "aeiou" else f"in an {scene}")
        if indoor:
            tail.append(f"({indoor})")
        return f"Photo of {who} " + " ".join(tail) + "." if tail else f"Photo of {who}."
    bits = []
    if scene:
        bits.append(scene)
    if indoor:
        bits.append(indoor)
    if not bits:
        return "Image."
    return "A " + " ".join(bits) + " image."


def _caption_image(raw_bytes: bytes) -> Optional[str]:
    """Detailed image caption. Florence-2 primary, BLIP fallback.

    Returns a 1-3 sentence detailed description, or None if both models
    fail to load. The caller folds None into the regex/fallback path.
    """
    cap = _florence_caption(raw_bytes)
    if cap:
        return cap
    return _blip_caption(raw_bytes)


def _florence_caption(raw_bytes: bytes) -> Optional[str]:
    """Florence-2 <MORE_DETAILED_CAPTION>. Returns None on any failure.

    Outputs are dense ("The image shows a man wearing glasses standing in
    front of a large whiteboard covered in handwritten mathematical
    equations and diagrams in blue marker"), much richer than BLIP's
    one-clause captions. Filler ("The image shows...") is stripped by the
    LLM rewriter or by `_clean_caption` in the regex fallback path.
    """
    global _FLORENCE_BROKEN
    if _FLORENCE_BROKEN:
        return None
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_florence2

        model, processor, device = get_florence2()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        prompt = "<MORE_DETAILED_CAPTION>"
        inputs = processor(text=prompt, images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=256,
                num_beams=3,
                do_sample=False,
            )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            text, task=prompt, image_size=(image.width, image.height)
        )
        out = (parsed.get(prompt) or "").strip()
        return out or None
    except KeyError as e:
        # transformers >=4.50 Cache-shape mismatch — Florence-2's
        # bundled modeling code is incompatible. Disable globally so
        # BLIP fallback runs instantly on every later image.
        logger.warning("florence2: caption disabled (KeyError: %s)", e)
        _FLORENCE_BROKEN = True
        return None
    except Exception:
        logger.exception("florence2: caption failed")
        return None


def _ocr_image(raw_bytes: bytes) -> Optional[str]:
    """Florence-2 <OCR>. Returns extracted text or None.

    Reads visible text in the image — whiteboards, document scans,
    screenshots. Empty/whitespace-only output → None so callers don't
    propagate a useless empty string.
    """
    global _FLORENCE_BROKEN
    if _FLORENCE_BROKEN:
        return None
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_florence2

        model, processor, device = get_florence2()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        prompt = "<OCR>"
        inputs = processor(text=prompt, images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=512,
                num_beams=3,
                do_sample=False,
            )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            text, task=prompt, image_size=(image.width, image.height)
        )
        ocr = (parsed.get(prompt) or "").strip()
        return ocr or None
    except KeyError as e:
        logger.warning("florence2: ocr disabled (KeyError: %s)", e)
        _FLORENCE_BROKEN = True
        return None
    except Exception:
        logger.exception("florence2: ocr failed")
        return None


# Florence-2's bundled `prepare_inputs_for_generation` expects the
# pre-Cache-object past_key_values shape (tuple of tuples). With
# transformers >= 4.50 that shape is gone and EVERY Florence-2 task
# (caption, OCR, dense regions, OD) raises
# `KeyError: Cache only has 0 layers` the moment beam search starts.
# The bundled code doesn't get upgraded in lock-step, so once we see
# the failure we mark Florence-2 entirely disabled — every later
# image falls straight through to BLIP for captioning + Qwen for
# rewriting, instead of wasting ~30 s per task reloading the model
# and re-crashing in the same spot.
_FLORENCE_BROKEN: bool = False


def _florence_regions(raw_bytes: bytes) -> Optional[list[str]]:
    """Florence-2 `<DENSE_REGION_CAPTION>` — per-region descriptive
    phrases like "a kettle on the floor" or "a row of mathematical
    equations in blue marker."

    Returns a deduped list of phrases (typically 5–25 per image) or
    None on failure. Feeds the Qwen synthesis prompt as
    "Objects and regions:" context so the final summary mentions
    grounded specifics instead of generic captions.
    """
    global _FLORENCE_BROKEN
    if _FLORENCE_BROKEN:
        return None
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_florence2

        model, processor, device = get_florence2()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        prompt = "<DENSE_REGION_CAPTION>"
        inputs = processor(text=prompt, images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
                do_sample=False,
            )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            text, task=prompt, image_size=(image.width, image.height)
        )
        # Florence-2 DENSE_REGION_CAPTION returns either
        # {"labels": [...], "bboxes": [...]} or a similar shape — the exact
        # keys vary across transformers versions. Handle both.
        result = parsed.get(prompt) or {}
        labels = result.get("labels") or result.get("captions") or []
        # Dedup while preserving order, strip whitespace, drop empties.
        seen = set()
        out: list[str] = []
        for label in labels:
            s = (label or "").strip()
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            out.append(s)
        return out or None
    except KeyError as e:
        # Cache shape mismatch from transformers >= 4.50 — flip the
        # permanent disable flag so we stop re-trying every image.
        logger.warning("florence2: dense region caption disabled (KeyError: %s)", e)
        _FLORENCE_BROKEN = True
        return None
    except Exception:
        logger.exception("florence2: dense region caption failed")
        return None


def _florence_objects(raw_bytes: bytes) -> Optional[list[str]]:
    """Florence-2 `<OD>` (object detection with labels). Returns a
    deduped list of detected object labels (e.g. "person", "laptop",
    "coffee mug"). Independent from `_florence_regions`: this gives
    canonical category names useful for filtering / search, while
    regions give descriptive phrases for the LLM prompt.
    """
    global _FLORENCE_BROKEN
    if _FLORENCE_BROKEN:
        return None
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_florence2

        model, processor, device = get_florence2()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        prompt = "<OD>"
        inputs = processor(text=prompt, images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
                do_sample=False,
            )
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            text, task=prompt, image_size=(image.width, image.height)
        )
        result = parsed.get(prompt) or {}
        labels = result.get("labels") or []
        seen = set()
        out: list[str] = []
        for label in labels:
            s = (label or "").strip().lower()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out or None
    except KeyError as e:
        logger.warning("florence2: OD disabled (KeyError: %s)", e)
        _FLORENCE_BROKEN = True
        return None
    except Exception:
        logger.exception("florence2: OD failed")
        return None


def _vlm_describe(raw_bytes: bytes) -> Optional[str]:
    """C2e — heavy vision-language description via InternVL2-4B.

    Returns a 1-paragraph natural-language description of the image
    that captures nuance Florence-2 misses (mood, action, fine-grained
    object relationships). Gated behind `settings.heavy_vlm_enabled`;
    returns None whenever the model can't load — the rest of the
    summary pipeline degrades cleanly.

    InternVL2's chat API expects a `<image>` token in the prompt that
    the model's `chat()` method swaps for image embeddings. Exact
    invocation varies between versions; we use the canonical pattern
    from the InternVL2 model card and catch any incompatibility.
    """
    try:
        import torch  # type: ignore
        from PIL import Image as PILImage  # type: ignore

        from backend.vision.runtime import get_internvl2

        model, tokenizer, device = get_internvl2()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        # InternVL2 exposes a `chat()` helper on the model that handles
        # image preprocessing internally given a PIL image. The exact
        # signature is `model.chat(tokenizer, pixel_values, question,
        # generation_config, history=None)` where pixel_values comes
        # from the model's `_preprocess_image` (also exposed as a class
        # method on the modeling file). We use the simpler `chat` path
        # that accepts a PIL image directly — newer revisions support it.
        question = (
            "Describe this image in 2-3 specific sentences. Cover every "
            "visible person, object, action, environment cue, and any "
            "readable text. Use concrete nouns; avoid filler like 'The "
            "image shows'. Output only the description."
        )
        generation_config = dict(
            max_new_tokens=256,
            do_sample=False,
        )

        # Run inference. Wrap broadly — different InternVL2 builds expose
        # slightly different surfaces.
        with torch.no_grad():
            if hasattr(model, "chat"):
                # Newer builds: model.chat(tokenizer, image, question, ...)
                try:
                    out = model.chat(
                        tokenizer,
                        image,
                        question,
                        generation_config=generation_config,
                    )
                except TypeError:
                    out = None
                if isinstance(out, str):
                    text = out.strip()
                elif isinstance(out, tuple) and out and isinstance(out[0], str):
                    text = out[0].strip()
                else:
                    text = None
            else:
                text = None

        if not text:
            return None
        # Collapse whitespace; cap at 1000 chars so a runaway model
        # doesn't dump a wall of text into the Qwen prompt budget.
        text = re.sub(r"\s+", " ", text).strip().strip('"').strip("'").strip()
        if len(text) > 1000:
            text = text[:1000].rsplit(".", 1)[0] + "."
        return text or None
    except Exception:
        logger.exception("vlm: heavy VLM description failed")
        return None


def _blip_caption(raw_bytes: bytes) -> Optional[str]:
    """BLIP fallback when Florence-2 fails. Same shape as v1.

    BLIP produces short single-clause captions ("a man taking a selfie in
    a kitchen"); kept as a safety net so first-time installs that haven't
    pulled Florence-2 weights still work.
    """
    try:
        from PIL import Image as PILImage
        import torch

        from backend.vision.runtime import get_caption_model

        model, processor, device = get_caption_model()
        image = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

        inputs = processor(images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            generated_ids = model.generate(
                **inputs,
                max_new_tokens=80,
                num_beams=5,
                do_sample=False,
                repetition_penalty=1.2,
            )
        caption = processor.decode(generated_ids[0], skip_special_tokens=True)
        caption = caption.strip()
        if caption:
            caption = caption[0].upper() + caption[1:]
            if caption[-1] not in ".!?":
                caption += "."
        return caption or None
    except Exception:
        logger.exception("blip: caption failed")
        return None


def _llm_rewrite_summary(
    caption: str,
    names: list[str],
    ocr_text: Optional[str],
    scene: Optional[str],
    setting: Optional[str],
    content_type: Optional[str],
    tags: Optional[list[str]] = None,
    regions: Optional[list[str]] = None,
    objects: Optional[list[str]] = None,
    concepts: Optional[list[str]] = None,
    vlm_description: Optional[str] = None,
) -> Optional[str]:
    """Qwen2.5-Instruct synthesizes raw multi-model signals into one
    grounded description.

    Accepts everything the C2 pipeline produces:
      - `caption`: Florence-2 <MORE_DETAILED_CAPTION>.
      - `ocr_text`: Florence-2 <OCR> (scene-gated).
      - `regions`: Florence-2 <DENSE_REGION_CAPTION> phrases.
      - `objects`: Florence-2 <OD> label list.
      - `concepts`: OpenCLIP top-K against the curated concept vocab.
      - `vlm_description`: optional InternVL2-4B paragraph (heavy, gated).
      - `tags`: any existing Image.tags labels for the row.
      - plus `scene`, `setting`, `content_type` from the existing
        classifier pass.

    Each signal is optional — missing inputs just drop the corresponding
    context line, so a stage that errors out degrades the summary
    instead of breaking it. Returns None on rewriter failure; caller
    falls back to the regex pipeline.
    """
    if not settings.rewriter_enabled:
        return None
    if not caption and not ocr_text and not vlm_description and not regions:
        return None

    try:
        import torch

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        ctx_lines: list[str] = []
        if caption:
            ctx_lines.append(f"Caption: {caption}")
        if vlm_description:
            ctx_lines.append(f"Rich description: {vlm_description}")
        if names:
            ctx_lines.append(f"People in image: {', '.join(names)}")
        if regions:
            ctx_lines.append(
                "Objects and regions:\n- " + "\n- ".join(regions[:30])
            )
        if objects:
            ctx_lines.append(f"Detected objects: {', '.join(objects[:30])}")
        if concepts:
            ctx_lines.append(f"Concept tags: {', '.join(concepts[:20])}")
        if tags:
            ctx_lines.append(f"Existing tags: {', '.join(tags[:20])}")
        if ocr_text:
            # 1500 chars fits the prompt budget alongside the multi-signal
            # context above; whiteboards and screenshots stop truncating
            # mid-equation. Qwen2.5 has ~3500-token effective input.
            ctx_lines.append(f"Visible text in image: {ocr_text[:1500]}")
        if scene:
            ctx_lines.append(f"Scene: {scene.replace('_', ' ')}")
        if setting and setting != "unknown":
            ctx_lines.append(f"Setting: {setting}")
        if content_type:
            ctx_lines.append(f"Content type: {content_type}")

        first_person = any(
            (n or "").strip().lower() in {"me", "i"} for n in names
        )

        instructions = (
            "Write a dense, keyword-rich description (1-3 sentences, up "
            "to ~70 words) of what's in this image. Pack in EVERY "
            "distinct concrete noun, named person, scene cue, lighting "
            "detail, action, and object from the inputs above — these "
            "are the search keywords users will type later, so "
            "redundancy with the inputs is good, not bad. "
            "Use named people instead of phrases like 'a man'. "
            "If visible text is provided, describe what the text is "
            "ABOUT (e.g. 'matrix algebra equations', 'a chat "
            "conversation', 'a recipe') rather than quoting it verbatim. "
            "Do NOT begin with 'The image shows', 'This is a', or "
            "'There is'. Output only the description — no preamble, no "
            "quotes, no bullet lists."
        )
        if first_person:
            instructions += (
                " The named person 'Me' is the photo owner — use first "
                "person ('I', 'my', 'myself')."
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You write natural, search-friendly descriptions from "
                    "structured image-analysis signals. You are concrete, "
                    "factual, and never invent details that aren't in the "
                    "input."
                ),
            },
            {
                "role": "user",
                "content": instructions + "\n\n" + "\n".join(ctx_lines),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        prompt_len = inputs.input_ids.shape[1]

        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=140,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()

        # Guard against the LLM going off — JSON dumps, multi-paragraph
        # ramble, etc. Collapse internal whitespace; keep up to ~700
        # chars (was 400 — allowing 1-2 sentence descriptions).
        reply = re.sub(r"\s+", " ", reply).strip().strip('"').strip("'").strip()
        if not reply or len(reply) > 700:
            return None
        # Ensure terminal punctuation.
        if reply[-1] not in ".!?":
            reply = reply.rstrip(",;:") + "."
        # Capitalize.
        reply = reply[0].upper() + reply[1:]
        # Post-process: substitute every generic person reference
        # ("a man", "the man", "a young man", "he", etc.) with the
        # detected named people. Qwen2.5-1.5B follows the "Use named
        # people" instruction only sometimes — its multi-sentence
        # descriptions often re-introduce "the man" / "he" mid-paragraph
        # because each sentence is generated against the prior caption,
        # not the people-list metadata. Running the regex splice on the
        # final output makes the substitution deterministic so summaries
        # are always indexed under the user's actual search terms.
        if names:
            reply = _splice_names_all(reply, names)
        return reply
    except Exception:
        logger.exception("rewriter: failed")
        return None


def _llm_compose_topic(
    caption: Optional[str],
    names: list[str],
    regions: Optional[list[str]] = None,
    objects: Optional[list[str]] = None,
    concepts: Optional[list[str]] = None,
    scene: Optional[str] = None,
    setting: Optional[str] = None,
    content_type: Optional[str] = None,
) -> Optional[str]:
    """Ask Qwen for a short search-friendly topic line (6-12 words).

    Replaces the v1 heuristic that produced "Photo of Me" / "Selfie" /
    "Photo of Mr Koler" — too generic for search. The topic should
    surface 1-2 specific nouns plus the named person when applicable
    ("Selfie at home in a desert-themed room", "Mr Koler at a
    whiteboard with linear algebra equations").

    Same context signals as the rewriter so topic + summary stay
    consistent. Returns None on failure; caller falls back to the
    heuristic `_compose_topic`.
    """
    if not settings.rewriter_enabled:
        return None
    if not caption and not regions and not objects:
        return None

    try:
        import torch  # type: ignore

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        ctx_lines: list[str] = []
        if caption:
            ctx_lines.append(f"Caption: {caption}")
        if names:
            ctx_lines.append(f"People: {', '.join(names)}")
        if regions:
            ctx_lines.append(f"Regions: {'; '.join(regions[:10])}")
        if objects:
            ctx_lines.append(f"Objects: {', '.join(objects[:15])}")
        if concepts:
            ctx_lines.append(f"Concepts: {', '.join(concepts[:10])}")
        if scene:
            ctx_lines.append(f"Scene: {scene.replace('_', ' ')}")
        if setting and setting != "unknown":
            ctx_lines.append(f"Setting: {setting}")
        if content_type:
            ctx_lines.append(f"Content type: {content_type}")

        instructions = (
            "Write a 6-12 word topic line for this image that a user "
            "could type into a search bar to find it again. Include "
            "the most distinctive 1-3 specific nouns or names from "
            "the inputs (a person's name, the place, an activity, a "
            "specific object). No filler words. Title-case is fine but "
            "not required. No trailing punctuation. Output only the "
            "topic line — no quotes, no preamble."
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You write short search-friendly topic lines from "
                    "structured image-analysis signals. Concrete, "
                    "specific, never inventive."
                ),
            },
            {
                "role": "user",
                "content": instructions + "\n\n" + "\n".join(ctx_lines),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        prompt_len = inputs.input_ids.shape[1]

        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=40,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        reply = re.sub(r"\s+", " ", reply).strip()
        # Strip surrounding quotes, leading/trailing punctuation,
        # 'Topic:' / 'Title:' prefixes the LLM occasionally adds.
        reply = reply.strip('"').strip("'").strip()
        reply = re.sub(r"^(topic|title)\s*:\s*", "", reply, flags=re.IGNORECASE)
        # Drop the trailing period some models add.
        reply = reply.rstrip(".,;:!? ")
        if not reply or len(reply) > 100:
            return None
        # Take only the first line if the model returned multiple.
        reply = reply.split("\n")[0].strip()
        if not reply:
            return None
        return reply
    except Exception:
        logger.exception("topic rewriter: failed")
        return None


def _llm_compose_doc_topic(text: str, filename: str) -> Optional[str]:
    """Same as `_llm_compose_topic` but for documents. Caller passes the
    extracted text and original filename; we produce a 6-12 word topic
    line that surfaces the most distinctive nouns / sections / numbers.
    Returns None on failure; caller falls back to the heuristic
    (filename stem)."""
    if not settings.rewriter_enabled:
        return None
    if not text:
        return None

    try:
        import torch  # type: ignore

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        # Keep the doc head short — topic lines don't need the whole
        # body and a smaller context speeds up the call.
        head = text[:2000]
        instructions = (
            "Write a 6-12 word topic line for this document that a "
            "user could type into a search bar to find it again. "
            "Use the most distinctive concrete nouns from the content "
            "— project names, sections, numbers, dates, parties. "
            "No filler words. No trailing punctuation. Output only "
            "the topic — no quotes, no preamble, no 'Title:' prefix."
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You write short search-friendly topic lines for "
                    "documents. Concrete, specific, never inventive."
                ),
            },
            {
                "role": "user",
                "content": (
                    instructions
                    + f"\n\nFilename: {filename}\n\nContent:\n{head}"
                ),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=3500
        ).to(device)
        prompt_len = inputs.input_ids.shape[1]

        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=40,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        reply = re.sub(r"\s+", " ", reply).strip().strip('"').strip("'").strip()
        reply = re.sub(r"^(topic|title)\s*:\s*", "", reply, flags=re.IGNORECASE)
        reply = reply.rstrip(".,;:!? ")
        if not reply or len(reply) > 100:
            return None
        reply = reply.split("\n")[0].strip()
        return reply or None
    except Exception:
        logger.exception("doc topic rewriter: failed")
        return None


# --- video -----------------------------------------------------------------


def _summarize_video(image: Image, raw_bytes: bytes) -> Optional[SummaryResult]:
    """Multi-keyframe video summary.

    1. Probe duration via ffprobe.
    2. Sample 4 keyframes evenly across the duration (15% / 40% / 65% / 90%
       — avoid the very start/end which are often black or logos).
    3. Florence-caption each frame.
    4. Send the caption set + filename to Qwen via `_llm_rewrite_summary`
       — Qwen synthesizes one coherent description from the per-frame
       observations. Same path image summaries already use.
    5. Topic: short Qwen-style title derived from the aggregated summary.
       Falls back to a cleaned filename when Qwen produces nothing.
    6. Content-type / scene inference: run the existing CLIP concept
       vocabulary over the middle keyframe and surface the top hits as
       `summary_signals.concepts` (search uses these later).

    Each step degrades independently:
      - No ffmpeg → filename-only summary
      - Florence failed on every frame → filename-only summary
      - Qwen disabled / unavailable → concatenated raw captions
    so installs without [ml] extras still get *something* useful.
    """
    duration_s = _probe_video_duration(raw_bytes)
    if duration_s and duration_s > 0:
        # 4 samples evenly inside [15%, 90%] of the duration. We bias
        # away from the absolute start (logos, black) and the
        # absolute end (credits, fade-out).
        sample_pcts = [0.15, 0.40, 0.65, 0.90]
        offsets = [max(0.5, duration_s * p) for p in sample_pcts]
    else:
        # Unknown duration — fall back to fixed offsets, the last of
        # which (60s) will fail silently on shorter clips. ffmpeg just
        # returns the last frame at that point.
        offsets = [1.0, 5.0, 15.0, 60.0]
    frames = [_extract_keyframe(raw_bytes, t) for t in offsets]
    frames = [f for f in frames if f]

    captions: list[str] = []
    for frame in frames:
        cap = _florence_caption(frame)
        if cap:
            captions.append(cap.strip().rstrip("."))

    # Audio transcription — captures what was SAID, which is usually
    # more informative than what the visuals show on talking-head /
    # interview / lecture clips. Failure modes (whisper not installed,
    # no audio track, transcription crash) return None and the rest
    # of the pipeline ignores it.
    transcript: str | None = None
    try:
        from backend.transcribe import transcribe_video_audio
        transcript = transcribe_video_audio(raw_bytes)
    except Exception:
        logger.exception("video: transcribe step crashed")
        transcript = None
    # Cap the transcript size before sending to Qwen. Whisper can
    # emit kilobytes of text on a long video; Qwen's context window
    # is finite and we'd rather spend it on the per-frame captions
    # too. ~1500 chars ≈ 250 words of speech, plenty to anchor the
    # subject of a video.
    if transcript and len(transcript) > 1500:
        transcript = transcript[:1500].rsplit(" ", 1)[0] + "…"

    # Filename → fallback topic. Strip the extension, swap _/- for
    # spaces, condense whitespace.
    fname_topic = ""
    if image.original_filename:
        fname_topic = (
            image.original_filename.rsplit(".", 1)[0]
            .replace("_", " ")
            .replace("-", " ")
        ).strip()
        fname_topic = re.sub(r"\s+", " ", fname_topic)

    # Pack visual captions + spoken transcript into one Qwen call.
    # `caption` carries the per-keyframe visual observations;
    # `ocr_text` field is repurposed to carry the audio transcript
    # because the Qwen prompt already treats it as authoritative
    # extracted text (the "USER EXPERIENCE AND AI TOOLS" on-screen
    # caption Florence picked up still lands here too, just merged
    # in with the spoken content). Qwen weighs both when synthesizing
    # — spoken content typically dominates when present because it's
    # longer and carries the actual subject of the video.
    summary: Optional[str] = None
    if captions or transcript:
        joined = " | ".join(captions) if captions else ""
        caption_for_llm = (
            f"Video — observed across {len(captions)} keyframes: {joined}"
            if captions
            else "Video — no visual frames captured."
        )
        ocr_for_llm = (
            f"Spoken content (transcribed): {transcript}"
            if transcript
            else None
        )
        try:
            summary = _llm_rewrite_summary(
                caption=caption_for_llm,
                names=[],
                ocr_text=ocr_for_llm,
                scene=None,
                setting=None,
                content_type="video",
                tags=None,
                regions=None,
                objects=None,
                concepts=None,
                vlm_description=None,
            )
        except Exception:
            logger.exception("video: qwen rewrite failed; using raw captions")
            summary = None
        if not summary:
            # Qwen disabled or failed — prefer the transcript when we
            # have one (most information per character), then the
            # longest single caption.
            if transcript:
                summary = transcript
            elif captions:
                summary = max(captions, key=len)
    if not summary:
        summary = "Video file. Preview unavailable."

    # Topic generation: derive from the summary's first noun phrase
    # when we can. The simplest heuristic that works well: take the
    # first sentence and Title-Case it, capped at ~6 words. Falls
    # back to the cleaned filename.
    topic = _derive_video_topic(summary, fname_topic)

    points: list[str] = []
    if duration_s and duration_s > 0:
        mins, secs = divmod(int(duration_s), 60)
        if mins > 0:
            points.append(f"Duration: {mins}m {secs}s")
        else:
            points.append(f"Duration: {secs}s")
    if image.byte_size_original:
        mb = image.byte_size_original / (1024 * 1024)
        points.append(f"Size: {mb:.1f} MB")
    if captions:
        points.append(f"Sampled {len(captions)} keyframes")

    cat = _classify_content(summary, image.original_filename, "video")
    return SummaryResult(
        topic=topic, summary=summary, points=points[:5], content_type=cat,
    )


def _derive_video_topic(summary: str, fallback: str) -> str:
    """Short, title-cased noun phrase for the gallery card.

    Trim the summary to its first sentence, drop opening filler
    ("A video showing", "The video depicts"), Title-Case, cap at
    6 words. Falls back to `fallback` (typically the cleaned
    filename) when the summary doesn't yield anything useful.
    """
    if not summary:
        return fallback or "Video"
    first = re.split(r"[.!?]", summary, maxsplit=1)[0].strip()
    if not first:
        return fallback or "Video"
    # Strip openers — Qwen often produces "A video showing X" or
    # "The clip depicts Y." The opener is conversational filler,
    # not information.
    first = re.sub(
        r"^(?:a |the )?(?:video |clip |scene |shot |sequence |footage )?"
        r"(?:that )?(?:is )?(?:showing|depicts|features|captures|shows)\s+",
        "",
        first,
        flags=re.IGNORECASE,
    )
    # Cap words.
    words = first.split()
    if len(words) > 6:
        words = words[:6]
    topic = " ".join(w[0].upper() + w[1:] if w else w for w in words)
    return topic or (fallback or "Video")


def _probe_video_duration(raw_bytes: bytes) -> Optional[float]:
    """Use ffprobe to read duration in seconds. Returns None on any
    failure — the caller substitutes fixed offsets in that case."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-loglevel", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                "pipe:0",
            ],
            input=raw_bytes,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return None
        s = proc.stdout.decode(errors="replace").strip()
        return float(s) if s else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        return None


def _extract_keyframe(raw_bytes: bytes, seek_seconds: float = 5) -> Optional[bytes]:
    """Pipe video bytes through ffmpeg, capture one PNG frame, return bytes.

    Requires `ffmpeg` on PATH. Returns None on any failure (missing binary,
    unsupported container, seek past end of file, etc.) so the caller can
    degrade gracefully.
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-loglevel", "error",
                "-ss", f"{seek_seconds:.3f}",
                "-i", "pipe:0",
                "-frames:v", "1",
                "-f", "image2pipe",
                "-vcodec", "png",
                "pipe:1",
            ],
            input=raw_bytes,
            capture_output=True,
            timeout=30,
            check=True,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


# --- document --------------------------------------------------------------


def _summarize_document(
    image: Image, raw_bytes: bytes
) -> Optional[SummaryResult]:
    text, topic = _extract_doc_text(image.original_filename or "", raw_bytes)
    fname = image.original_filename or ""
    if not text.strip():
        return SummaryResult(
            topic=topic or _filename_stem(fname) or "Document",
            summary=_doc_summary_fallback(fname, topic),
            points=[],
        )

    # We still cap at `summarize_doc_max_chars` (default 20 000) so a 200-
    # page PDF doesn't blow up generation memory, but the LLM path below
    # internally chunk-summarizes within that budget instead of skimming
    # the head.
    truncated = text[: settings.summarize_doc_max_chars]

    # Try the instruction-LLM first. Qwen2.5-Instruct (already in memory
    # for image rewriting) produces a short "what is this doc ABOUT"
    # blurb the user can search by from memory, instead of dumping the
    # first 400 chars of body text (the old fallback's failure mode).
    summary = _llm_doc_summary(truncated, fname, topic)
    if not summary:
        summary = _extractive_summary(truncated)

    # Reject body-text leaks. Both the small LLM and the sumy
    # extractive fallback occasionally produce output that's a verbatim
    # chunk of the input ("Part 1 — Inventory Think about or list…"
    # was the first paragraph of LBLF.pdf, not a description of it).
    # A search-friendly summary never has 60 consecutive characters
    # identical to the source. When it does, prefer the filename-stub
    # fallback so the preview panel doesn't quote the doc back to the
    # user as its own description.
    if summary and _looks_like_body_excerpt(summary, truncated):
        logger.info("doc-summary: body-text leak detected for %s, falling back", fname)
        summary = None

    # Don't ever emit raw body content as the description. If both the
    # LLM and the extractive paths failed, hand back a stub the user can
    # at least recognize. A wall of `truncated[:400]` is what made
    # LBLF.pdf's description quote the first questionnaire prompt in
    # full instead of summarizing the doc.
    summary = _clamp_doc_summary(summary) or _doc_summary_fallback(fname, topic)

    # Auto-derive points when the LLM ran (it produces a single paragraph,
    # not a bulleted list). Heuristic keypoints still work for docs that
    # already use markdown list syntax.
    points = _extract_keypoints(truncated)
    if not points and summary:
        points = _llm_keypoints(truncated, fname)

    # Always prefer the LLM-generated topic for documents — the
    # extraction heuristic (first 4-120-char line) produces things
    # like "LBLF" or page numbers. Falls back to extracted topic,
    # then filename stem.
    llm_topic = _llm_compose_doc_topic(truncated, fname)
    if llm_topic:
        topic = llm_topic
    elif not topic or len(topic) < 4 or topic.isdigit():
        topic = _llm_doc_topic(truncated, fname) or topic

    return SummaryResult(
        topic=topic or _filename_stem(fname) or "Document",
        summary=summary,
        points=points[:5],
        content_type=_classify_content(summary, fname, "document"),
    )


def _filename_stem(filename: str) -> str:
    if not filename:
        return ""
    stem = filename.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip()


def _doc_summary_fallback(filename: str, topic: Optional[str]) -> str:
    """When we have no usable LLM output, write a recognizable stub from
    the filename + topic instead of quoting body text verbatim. Keeps
    the description short and searchable."""
    stem = _filename_stem(filename)
    if topic and 3 <= len(topic) <= 80:
        return f"Document about {topic}."
    if stem:
        return f"Document: {stem}."
    return "Document content could not be summarized."


def _clamp_doc_summary(summary: Optional[str]) -> Optional[str]:
    """Cap description length and strip leading body-text fingerprints.

    The doc preview panel only has room for ~3 lines. Long extractive
    output (sumy LSA pulling 4 sentences out of a 200-page contract)
    overflows the panel and is the opposite of "searchable by memory."
    Trim to ~280 chars at a sentence boundary so the description stays
    glanceable.
    """
    if not summary:
        return summary
    s = re.sub(r"\s+", " ", summary).strip()
    if not s:
        return None
    # Strip markdown / label prefixes Qwen sometimes prepends despite
    # the prompt asking for plain text:
    #   "Document Description:**"  /  "**Description:**"  /  "## Summary"
    # All of those leak the LLM's internal scaffolding into the
    # preview panel — strip leading labels and any orphan stars/hashes.
    s = re.sub(
        r"^(?:#+\s*|\*+\s*)?(?:document\s+)?(?:summary|description|overview|tl;dr)\s*[:\-—]\s*\*+\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = s.lstrip("*# \t-—:").strip()
    if not s:
        return None
    if len(s) <= 280:
        return s
    head = s[:280]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut > 120:
        return head[: cut + 1].strip()
    return head.rstrip(",;: ") + "…"


def _looks_like_body_excerpt(summary: str, body: str) -> bool:
    """True when the LLM regurgitated a chunk of the input verbatim.

    Qwen2.5-1.5B sometimes copies the opening lines of a document instead
    of paraphrasing them ("Part 1 — Inventory Think about or list…"
    instead of "Class assignment about an inventory of skills."). The
    extractive sumy fallback path also produces these. Detect by
    sliding-window: if any 60-char run from the summary appears
    verbatim in the body, treat it as a leak and reject.

    60 chars is wide enough to skip incidental phrase overlap ("the
    document", "first page") but narrow enough to catch any real
    body quote — a search-friendly description never has 60
    consecutive characters identical to the source.
    """
    if not summary or not body:
        return False
    s = re.sub(r"\s+", " ", summary).lower()
    b = re.sub(r"\s+", " ", body).lower()
    if len(s) < 60:
        return False
    window = 60
    for i in range(0, len(s) - window + 1, 20):
        if s[i : i + window] in b:
            return True
    return False


def _extract_doc_text(
    filename: str, raw: bytes
) -> tuple[str, Optional[str]]:
    """Return (text, topic). Topic falls back to None when nothing usable."""
    name = filename.lower()
    try:
        if name.endswith(".pdf"):
            return _extract_pdf(raw)
        if name.endswith(".docx"):
            return _extract_docx(raw)
        if name.endswith((".xlsx", ".xls")):
            return _extract_xlsx(raw)
        if name.endswith((".txt", ".md", ".csv", ".log", ".json", ".tsv",
                          ".yaml", ".yml", ".toml", ".ini", ".sql", ".html",
                          ".htm", ".xml")):
            return _extract_plaintext(raw, filename)
    except Exception:
        logger.exception("doc extract failed for %s", filename)
    return "", None


def _extract_pdf(raw: bytes) -> tuple[str, Optional[str]]:
    """PDF text extraction. pypdf is the default; if it produces almost
    nothing (typical for layout-heavy / two-column / image-PDFs) we fall
    back to pdfminer.six which handles those better. Image-only PDFs
    return empty text — caller emits a "could not extract" summary."""
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(BytesIO(raw))
    pages_text = []
    for page in reader.pages[:50]:  # cap at 50 pages (was 30)
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n\n".join(pages_text).strip()

    # If pypdf bailed (returned <80 visible chars from a multi-page doc),
    # try pdfminer.six. It's slower but parses encoded fonts and column
    # layouts pypdf trips on.
    if len(text) < 80 and len(reader.pages) > 0:
        miner_text = _pdfminer_extract(raw)
        if miner_text and len(miner_text) > len(text):
            text = miner_text.strip()

    # If BOTH pypdf and pdfminer returned almost nothing, the PDF is
    # likely image-only (scanned docs, contracts photographed page-by-
    # page). Rasterize each page with PyMuPDF and run Florence-2 <OCR>
    # over the rasters. Slow (~3-5 s/page on GPU) but unlocks summaries
    # for previously-opaque PDFs.
    if len(text) < 80 and len(reader.pages) > 0:
        ocr_text = _pdf_ocr_fallback(raw)
        if ocr_text and len(ocr_text) > len(text):
            text = ocr_text.strip()

    topic: Optional[str] = None
    md = reader.metadata or {}
    if md and md.title:
        topic = str(md.title).strip() or None
    if not topic:
        # First non-blank line truncated.
        for line in text.splitlines():
            line = line.strip()
            if 4 <= len(line) <= 120:
                topic = line
                break
    return text, topic


def _pdfminer_extract(raw: bytes) -> str:
    """pdfminer.six fallback. Returns "" on any failure (incl. missing
    optional dep) so the caller keeps whatever pypdf produced."""
    try:
        from pdfminer.high_level import extract_text  # type: ignore

        return extract_text(BytesIO(raw)) or ""
    except Exception:
        return ""


def _pdf_pages_to_images(raw: bytes, max_pages: int = 10) -> list[bytes]:
    """Rasterize the first N pages of a PDF to PNG bytes via PyMuPDF.

    Returns a list of PNG bytes (empty list on failure). Cap of 10 pages
    is a cost guard — for the summary use case we don't need the whole
    doc, just enough text to produce a meaningful description. The
    summary path already truncates to `summarize_doc_max_chars` anyway.
    """
    try:
        import fitz  # type: ignore  # PyMuPDF

        out: list[bytes] = []
        with fitz.open(stream=raw, filetype="pdf") as doc:
            for page_idx in range(min(len(doc), max_pages)):
                page = doc[page_idx]
                # 2x zoom for OCR readability without ballooning memory.
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                out.append(pix.tobytes("png"))
        return out
    except Exception:
        logger.exception("pymupdf: PDF rasterize failed")
        return []


def _pdf_ocr_fallback(raw: bytes) -> str:
    """Last-resort PDF text extraction for image-only PDFs.

    Walks the first 10 pages, rasterizes each via PyMuPDF, runs
    `_ocr_image` (Florence-2 <OCR>) on the raster, concatenates the
    page-level OCR output with double newlines between pages.
    Returns "" if PyMuPDF or Florence-2 are unavailable.
    """
    pages = _pdf_pages_to_images(raw, max_pages=10)
    if not pages:
        return ""
    chunks: list[str] = []
    for png_bytes in pages:
        try:
            page_text = _ocr_image(png_bytes)
        except Exception:
            page_text = None
        if page_text:
            chunks.append(page_text.strip())
    return "\n\n".join(chunks)


def _extract_docx(raw: bytes) -> tuple[str, Optional[str]]:
    import docx  # type: ignore

    doc = docx.Document(BytesIO(raw))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs)

    topic: Optional[str] = None
    # Heading 1 wins; otherwise first paragraph.
    for p in doc.paragraphs:
        if p.style and p.style.name and p.style.name.startswith("Heading 1"):
            if p.text.strip():
                topic = p.text.strip()
                break
    if not topic and paragraphs:
        topic = paragraphs[0][:120].strip() or None
    return text, topic


def _extract_xlsx(raw: bytes) -> tuple[str, Optional[str]]:
    import openpyxl  # type: ignore

    wb = openpyxl.load_workbook(BytesIO(raw), read_only=True, data_only=True)
    rows_out: list[str] = []
    sheet_names = wb.sheetnames
    for name in sheet_names[:3]:
        ws = wb[name]
        rows_out.append(f"# Sheet: {name}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 200:
                break
            cells = [str(c) for c in row if c not in (None, "")]
            if cells:
                rows_out.append(" | ".join(cells))
    text = "\n".join(rows_out)
    topic = sheet_names[0] if sheet_names else None
    return text, topic


def _extract_plaintext(raw: bytes, filename: str) -> tuple[str, Optional[str]]:
    text = raw.decode("utf-8", errors="replace")
    topic: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if 4 <= len(stripped) <= 120:
            topic = stripped
            break
    if not topic:
        topic = filename.rsplit(".", 1)[0]
    return text, topic


# --- extractive summarization ---------------------------------------------


def _extractive_summary(text: str) -> str:
    """Abstractive document summary via DistilBART CNN.

    Tries BART first (real paraphrase, "The report covers Q4 growth and the
    Phase 11 release"). Falls back to sumy LSA, then to leading sentences,
    so summarization keeps working even if the model can't load.
    """
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    bart = _bart_summary(text)
    if bart:
        return bart

    n = settings.summarize_doc_sentence_count
    try:
        from sumy.parsers.plaintext import PlaintextParser  # type: ignore
        from sumy.nlp.tokenizers import Tokenizer  # type: ignore
        from sumy.summarizers.lsa import LsaSummarizer  # type: ignore

        parser = PlaintextParser.from_string(text, Tokenizer("english"))
        summarizer = LsaSummarizer()
        sentences = summarizer(parser.document, n)
        joined = " ".join(str(s) for s in sentences).strip()
        if joined:
            return joined
    except Exception:
        logger.exception("sumy LSA failed; using leading sentences")

    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()


def _llm_doc_summary(
    text: str, filename: str, topic: Optional[str]
) -> Optional[str]:
    """Summarize a document with Qwen2.5-Instruct.

    For docs that fit in one prompt window (~6 000 chars / ~1 500 tokens
    of context for the LLM, leaving room for instructions and reply) we
    summarize directly. Longer docs get chunked, each chunk is
    micro-summarized in one pass, then the chunk summaries are merged in
    a second pass — classic map-reduce. The merged summary captures
    breadth instead of skimming the first page (which was DistilBART's
    failure mode).

    Returns None when the LLM is disabled or unavailable; caller falls
    back to BART → sumy → leading sentences.
    """
    if not settings.rewriter_enabled:
        return None
    text = text.strip()
    if not text:
        return None

    # Qwen2.5-1.5B handles ~3-4 K tokens cleanly; ~6 000 chars is a safe
    # English budget that leaves room for the instruction and reply. Above
    # that we chunk-and-merge.
    CHUNK_BUDGET = 6000
    chunks = _split_for_summary(text, CHUNK_BUDGET)

    if len(chunks) == 1:
        return _llm_summarize_chunk(chunks[0], filename, topic, is_full=True)

    # Map: per-chunk summaries.
    partials: list[str] = []
    for c in chunks[:8]:  # cap fan-out — diminishing returns past 8 chunks
        s = _llm_summarize_chunk(c, filename, topic, is_full=False)
        if s:
            partials.append(s)
    if not partials:
        return None

    # Reduce: condense the partials into one summary.
    merged_input = "\n\n".join(f"- {p}" for p in partials)
    return _llm_merge_summaries(merged_input, filename, topic)


def _split_for_summary(text: str, budget_chars: int) -> list[str]:
    """Split on paragraph boundaries when possible, falling back to fixed-
    size windows. Keeps each chunk ≤ budget_chars."""
    if len(text) <= budget_chars:
        return [text]
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""
    for p in paras:
        if not p.strip():
            continue
        if len(current) + len(p) + 2 <= budget_chars:
            current = (current + "\n\n" + p) if current else p
        else:
            if current:
                chunks.append(current)
            if len(p) <= budget_chars:
                current = p
            else:
                # Single paragraph longer than the budget — hard-split.
                for i in range(0, len(p), budget_chars):
                    chunks.append(p[i : i + budget_chars])
                current = ""
    if current:
        chunks.append(current)
    return chunks


def _llm_summarize_chunk(
    chunk: str, filename: str, topic: Optional[str], is_full: bool
) -> Optional[str]:
    """One LLM call. is_full=True asks for a final-quality summary; False
    asks for a terse partial that will be merged later."""
    try:
        import torch

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        if is_full:
            instructions = (
                "Write a SHORT, search-friendly description of what this "
                "document IS — the kind of thing someone would type into "
                "a search bar months later to find it by memory. "
                "1-2 natural English sentences, under 35 words total. "
                "Describe the document's PURPOSE and SUBJECT (what it's "
                "about, what kind of doc it is), NOT its body content. "
                "Pack in the most distinctive concrete nouns — project "
                "names, parties, topics, dates — but DO NOT quote or "
                "list the document's questions, prompts, or paragraphs "
                "verbatim. NEVER copy a sentence from the input — "
                "always paraphrase. Do NOT begin with 'This document', "
                "'The document', or 'In summary'. Output only the "
                "description.\n\n"
                "Examples of GOOD descriptions:\n"
                "- 'Class assignment asking students to inventory what "
                "they built or trained during the course.'\n"
                "- 'Group exercise comparing four AI agent frameworks "
                "(LangChain, AutoGen, CrewAI, Semantic Kernel) for a "
                "research write-up.'\n"
                "- 'Q4 2025 financial report covering revenue growth, "
                "operating margin, and Phase 11 product release plans.'\n"
                "Examples of BAD descriptions (verbatim body, do NOT "
                "produce these):\n"
                "- 'Part 1 — Inventory Think about or list everything "
                "you can remember…'\n"
                "- 'Phase 1: Framework Research (10 minutes) Your "
                "group has been assigned…'"
            )
        else:
            instructions = (
                "Briefly state what this section of a document is ABOUT "
                "in ONE specific English sentence (under 25 words). "
                "Describe its subject/purpose, do NOT quote body text "
                "verbatim. Output only the sentence."
            )

        ctx_lines = []
        if filename:
            ctx_lines.append(f"Filename: {filename}")
        if topic:
            ctx_lines.append(f"Title: {topic}")
        ctx_lines.append("Content:\n" + chunk)

        messages = [
            {
                "role": "system",
                "content": (
                    "You write concise, factual document summaries. "
                    "You never invent details that are not in the input."
                ),
            },
            {
                "role": "user",
                "content": instructions + "\n\n" + "\n".join(ctx_lines),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=3500
        ).to(device)
        prompt_len = inputs.input_ids.shape[1]

        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=240 if is_full else 120,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        # Strip surrounding quotes / leading dashes.
        reply = reply.lstrip("-•* ").strip().strip('"').strip("'").strip()
        if not reply:
            return None
        # Keep the reply within sane bounds; the LLM occasionally rambles.
        if len(reply) > 600:
            reply = reply[:600].rsplit(".", 1)[0] + "."
        return reply
    except Exception:
        logger.exception("qwen: doc summary chunk failed")
        return None


def _llm_merge_summaries(
    partials_block: str, filename: str, topic: Optional[str]
) -> Optional[str]:
    try:
        import torch

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        ctx_lines = []
        if filename:
            ctx_lines.append(f"Filename: {filename}")
        if topic:
            ctx_lines.append(f"Title: {topic}")
        ctx_lines.append(
            "Partial summaries (each line summarizes a different "
            "section of the same document):"
        )
        ctx_lines.append(partials_block)

        instructions = (
            "Combine the partial summaries below into ONE SHORT "
            "search-friendly description of what the document IS — "
            "the kind of thing someone would type into a search bar "
            "months later to find it. 1-2 natural English sentences, "
            "under 35 words total. Describe purpose and subject, drop "
            "redundancy and any verbatim body content. Keep the most "
            "distinctive specific nouns (names, parties, topics, dates). "
            "Do NOT begin with 'This document', 'The document', or 'In "
            "summary'. Output only the combined description."
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You write concise, factual document summaries. "
                    "You never invent details."
                ),
            },
            {
                "role": "user",
                "content": instructions + "\n\n" + "\n".join(ctx_lines),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=3500
        ).to(device)
        prompt_len = inputs.input_ids.shape[1]

        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=240,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        reply = reply.lstrip("-•* ").strip().strip('"').strip("'").strip()
        if not reply:
            return None
        if len(reply) > 600:
            reply = reply[:600].rsplit(".", 1)[0] + "."
        return reply
    except Exception:
        logger.exception("qwen: doc merge failed")
        return None


def _llm_doc_topic(text: str, filename: str) -> Optional[str]:
    """Ask the LLM for a 3–8 word topic line when extraction failed."""
    try:
        import torch

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        head = text[:2000]
        messages = [
            {
                "role": "system",
                "content": (
                    "You write concise, factual document titles. "
                    "You never invent details."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Give a 3–8 word title that captures what this "
                    "document is about. Use noun phrases (no verbs, no "
                    "punctuation at the end). Output only the title.\n\n"
                    f"Filename: {filename}\n\nContent:\n{head}"
                ),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=3000
        ).to(device)
        prompt_len = inputs.input_ids.shape[1]
        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=24,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        reply = reply.split("\n")[0].strip().strip('"').strip("'").strip()
        if 3 <= len(reply) <= 80:
            return reply
        return None
    except Exception:
        logger.exception("qwen: doc topic failed")
        return None


def _llm_keypoints(text: str, filename: str) -> list[str]:
    """Ask the LLM for 3 short key points when the heuristic returned
    nothing (i.e. the doc has no markdown list syntax)."""
    try:
        import torch

        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()

        head = text[:6000]
        messages = [
            {
                "role": "system",
                "content": (
                    "You extract concrete key points from documents. "
                    "You never invent details."
                ),
            },
            {
                "role": "user",
                "content": (
                    "List up to 3 KEY POINTS from this document, one per "
                    "line. Each point: a short clause (under 12 words) "
                    "covering a specific fact, decision, or section. "
                    "Output ONLY the lines, no numbering, no bullets, "
                    "no preamble.\n\n"
                    f"Filename: {filename}\n\nContent:\n{head}"
                ),
            },
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=3500
        ).to(device)
        prompt_len = inputs.input_ids.shape[1]
        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=120,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        points = []
        for line in reply.splitlines():
            cleaned = line.strip().lstrip("-•*").strip()
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
            if 4 <= len(cleaned) <= 160:
                points.append(cleaned)
            if len(points) >= 3:
                break
        return points
    except Exception:
        logger.exception("qwen: keypoints failed")
        return []


def _bart_summary(text: str) -> Optional[str]:
    """Run DistilBART abstractive summarization. Returns None on any failure
    (model unavailable, OOM, tokenizer error). Caller falls back to sumy.

    BART has a 1024-token input cap; we truncate to ~3500 chars (≈ 800
    tokens for English) to leave room for the model's encoder context.
    """
    try:
        import torch

        from backend.vision.runtime import get_doc_summarizer

        model, tokenizer, device = get_doc_summarizer()
        truncated = text[:3500]
        inputs = tokenizer(
            truncated,
            return_tensors="pt",
            truncation=True,
            max_length=1024,
        ).to(device)

        with torch.no_grad():
            ids = model.generate(
                **inputs,
                max_length=140,
                min_length=40,
                num_beams=4,
                length_penalty=2.0,
                no_repeat_ngram_size=3,
                early_stopping=True,
            )
        out = tokenizer.decode(ids[0], skip_special_tokens=True).strip()
        return out or None
    except Exception:
        logger.exception("bart: summary failed")
        return None


def _extract_keypoints(text: str) -> list[str]:
    """Cheap keypoint heuristic: list-formatted lines + frequent capitalized
    nouns. Better than nothing without spinning up an LLM."""
    points: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^[\-\*•–—]\s+(.{4,160})$", line)
        if m:
            points.append(m.group(1).strip())
        elif re.match(r"^\d+[\.\)]\s+(.{4,160})$", line):
            points.append(re.sub(r"^\d+[\.\)]\s+", "", line).strip())
        if len(points) >= 5:
            break
    return points
