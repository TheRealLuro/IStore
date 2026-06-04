"""Structure-preserving document translation (NEW, unwired).

Two routes. The first streams a STRUCTURE-PRESERVING translation of a whole
document; the second renders the translated blocks back into a clean,
structured PDF download. Together they let the preview's Translate tool turn
a PDF (or any text document) into a *typed* outline — title, headings,
bullets, paragraphs — in any of NLLB-200's languages, and let the user
download that as a real `.pdf` instead of a flat `.txt` blob.

  POST /images/{id}/translate-doc-stream   (authed + owner-scoped)
       Body: {"target": "spa_Latn"}   (FLORES-200 code OR ISO code; e.g.
             "es" → spa_Latn; empty/unknown → English)

       Loads the OWNED document's file bytes, extracts STRUCTURED BLOCKS in
       reading order — a list of {type, text} where type ∈ {"title", "h1",
       "h2", "p", "li"} — translates each block's text with NLLB-200, and
       streams ONE NDJSON line per block as it finishes so the FE paints a
       typed outline top-to-bottom.

       NDJSON line shapes (each `\\n`-terminated):
         per block : {"i": <0-based idx>, "n": <total blocks>,
                      "type": "<title|h1|h2|p|li>",
                      "text": "<translated block text>",
                      "source_lang": "<human name>",
                      "target_lang": "<human name>"}
         on finish : {"done": true, "n": <total blocks>}
         on error  : {"error": "<short message>"}   (terminates the stream;
                      the generator NEVER raises, which would hang it)

  POST /render-translated-pdf   (authed)
       Body: {"filename": "<original name>",
              "blocks": [{"type": "...", "text": "..."}, ...],
              "source_lang": "...", "target_lang": "..."}
       Renders the blocks the FE already collected from the stream into a
       structured PDF with ReportLab Platypus (NO re-translation) and returns
       it as an `application/pdf` attachment named "<base> (<target_lang>).pdf".

STRUCTURED EXTRACTION
---------------------
PDFs (the rich case): PyMuPDF's `page.get_text("dict")` gives blocks → lines
→ spans, each span carrying `size` (font size in pt), `flags` (bit 2**4 ==
bold) and `text`. We compute the BODY size = the most common span size
across the document, then classify each assembled line:
  * the single largest line on page 1 → "title";
  * lines clearly larger than body, OR bold-and-short, OR numbered headings
    ("1.", "2.1 Scope") → "h1" / "h2" by size tier;
  * lines beginning with a bullet glyph (• ‣ - – * ·) → "li";
  * everything else → "p".
Spans are merged within a line and lines within a block; reading order is
preserved across pages.

Non-PDF documents: we fall back to a text heuristic over the summarizer's
`_extract_doc_text` (the same extractor doctext.py uses) — blank-line-
separated paragraphs, bullet-prefixed lines → li, a short line that is
ALL-CAPS or directly followed by a blank line → heading.

TRANSLATION
-----------
Reuses ocr.py's NLLB internals verbatim — `_get_nllb` (cached 600M model +
tokenizer + device), `_detect_src_flores` / `_to_flores` / `_flores_name`
(detect + FLORES mapping + display names), `_chunk_text` + `_NLLB_CHUNK_CHARS`
(sub-chunk long blocks) — and translate_stream.py's `_resolve_forced_bos` +
`_translate_one_chunk_sync` (one generate() per chunk, dynamic max_new). The
source language + forced-BOS token are resolved ONCE up front. Each block is
translated whole (long blocks sub-chunked, then rejoined) so ONE type stays
per emitted line. All blocking work runs via `asyncio.to_thread`, so the
event loop stays free while a long document streams.

There is no Qwen fallback on these routes (matching translate_stream.py): the
win here is NLLB-on-GPU. If NLLB can't load, the stream emits one `{"error":
...}` line and stops cleanly.

PDF RENDER / FONTS
------------------
ReportLab's built-in Type-1 fonts (Helvetica/Times) only cover Latin-1, so
accented output (á, ñ, ö, ć) would render as black boxes. We register the
**DejaVuSans** TTF that already ships in the container (checked, in order,
under matplotlib's bundled fonts and the system `/usr/share/fonts` tree),
plus its Bold, and map them as the document font so Latin / Cyrillic / Greek
accents render correctly. DejaVuSans does NOT cover CJK or Arabic — those
scripts fall back to the default font and may show as boxes; this is noted as
a known limitation (a CJK/Arabic-capable font would need to be added to the
image). If no Unicode TTF is found at all we degrade to Helvetica with the
same caveat rather than failing the download.

All heavy imports (fitz, reportlab) are LAZY — inside the worker functions —
so importing THIS module at API boot never pulls them and `--reload` stays
fast, mirroring ocr.py / doctext.py.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from collections import Counter
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User
from backend.storage import storage

# Reuse the NLLB internals + owner-scoping from ocr.py and the per-chunk
# helpers from translate_stream.py so all three translate paths share one
# model load and produce identical output for the same chunk. Every import
# here is import-safe (no ML pulled at module import time in either module).
from backend.api.ocr import (
    _NLLB_CHUNK_CHARS,
    _chunk_text,
    _detect_src_flores,
    _flores_name,
    _get_nllb,
    _load_owned_image,
    _to_flores,
)
from backend.api.translate_stream import (
    _resolve_forced_bos,
    _translate_one_chunk_sync,
)

logger = logging.getLogger(__name__)

# Same /images prefix + tags as ocr.py so the streaming route reads as part of
# the images surface. The render route has NO image id (the FE already holds
# the blocks), so it lives at the top level — see its own router below. Both
# are wired separately in app.py (see this module's task hand-off).
router = APIRouter(prefix="/images", tags=["images", "ocr"])
render_router = APIRouter(tags=["ocr"])

# Block type vocabulary the FE renders. Kept tight so the stream + render
# contracts agree.
_BLOCK_TYPES = ("title", "h1", "h2", "p", "li")

# Bullet glyphs that mark a list item at the start of a line.
_BULLET_CHARS = "•‣◦▪·-–—*"
_BULLET_RE = re.compile(r"^\s*[" + re.escape(_BULLET_CHARS) + r"]\s+")
# A line that is ONLY a bullet glyph (PyMuPDF puts the bullet in its own glyph
# column, so the marker and the item text arrive as two separate lines). When
# we see one, the NEXT text line is the list item.
_BULLET_ONLY_RE = re.compile(r"^\s*[" + re.escape("•‣◦▪·") + r"]\s*$")
# Numbered headings: "1.", "2.1", "3.2.4 Scope", "IV.". Captures the depth so
# we can tier h1 vs h2 by how many dotted levels there are.
_NUM_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")


# ===========================================================================
# Structured extraction
# ===========================================================================
class Block(BaseModel):
    type: str = Field(description="one of title|h1|h2|p|li")
    text: str
    # Sub-project D: the original list marker for an `li`. The actual leading
    # glyph ("•" / "-" / "*" …) when the source had one, or "" when it did NOT
    # (e.g. a bold-lead-in pseudo-list the classifier flagged) so the renderer
    # does NOT fabricate a bullet that wasn't there. None = marker unknown
    # (older clients posting to /render-translated-pdf) → renderer keeps the
    # legacy "•" default so we never silently drop a real bullet.
    marker: Optional[str] = None


def _norm_ws(s: str) -> str:
    return re.sub(r"[ \t]+", " ", (s or "").replace(" ", " ")).strip()


def _strip_bullet(s: str) -> str:
    """Drop a leading bullet glyph (and any following space) from a list line
    so the translator never sees the glyph and the renderer re-adds its own."""
    return _BULLET_RE.sub("", s).strip()


# ---------------------------------------------------------------------------
# PDF path — PyMuPDF page.get_text("dict") → typed blocks in reading order.
# ---------------------------------------------------------------------------
def _classify_line_kind(
    text: str,
    size: float,
    bold: bool,
    *,
    page: int,
    body_size: float,
    title_size: float,
    title_used: bool,
    is_bullet: bool,
) -> str:
    """Classify ONE already-assembled line into title|h1|h2|p|li from the font
    signals (size / bold), its page, the bullet flag and the document-wide
    body/title size tiers. PURE — no model, no state mutation; the caller owns
    the `title_used` latch and the body/title size estimates. This is the SAME
    tiering `_extract_pdf_blocks` applies, factored out so translate_pdf.py's
    exact-copy path classifies blocks identically (one source of truth).

    `is_bullet` is True when the line is a list item (a bullet glyph led it, or
    a lone-bullet glyph preceded it). Bullets win over every size/bold tier.
    Numbered headings ("1.", "2.1 …") and the page-1 title are derived from
    `text` + `size` here exactly as in the extractor."""
    words = len(text.split())

    # --- list item: bullet glyph wins regardless of size/bold. ---
    if is_bullet:
        return "li"

    # --- title: the single largest line on page 1, only when it's genuinely
    #     bigger than body and not yet taken (caller latches title_used). ---
    if (
        not title_used
        and page == 0
        and size >= title_size
        and title_size >= body_size * 1.15
        and words <= 30
    ):
        return "title"

    # --- numbered heading: short "1.", "2.1 …" → heading, tier by dotted
    #     depth. A long numbered line is a numbered paragraph, not a heading. ---
    nm = _NUM_HEADING_RE.match(text)
    if nm and words <= 14:
        depth = nm.group(1).count(".") + 1
        return "h2" if depth >= 2 else "h1"

    # --- size/bold heading: clearly larger than body, or bold + short. ---
    is_big = size >= body_size * 1.35
    is_midbig = size >= body_size * 1.15
    is_bold_short = bold and words <= 12 and size >= body_size * 0.95
    if (is_big or is_bold_short) and words <= 20:
        # Tier: well above body → h1; modestly above / bold → h2.
        tier = "h1" if (size >= body_size * 1.5) else "h2"
        if is_bold_short and not is_midbig:
            tier = "h2"
        return tier

    # --- default: body paragraph. ---
    return "p"


def _extract_pdf_blocks(raw: bytes) -> list[Block]:
    """Extract typed structural blocks from a PDF's BYTES using PyMuPDF.

    Walks blocks → lines → spans, merging spans within a line and lines
    within a block, classifying each line by font size / boldness / bullet /
    numbering against the document's BODY (modal) span size. Reading order is
    the order PyMuPDF yields blocks per page, pages in order. Lazy `import
    fitz` so this module stays cheap to import."""
    import fitz  # type: ignore  # PyMuPDF — already pinned in [ml]

    # Pass 1: collect every line as (page_idx, y_top, max_size, any_bold,
    # text). We need a global body-size estimate before we can classify, so
    # we materialize lines first, then tier them.
    raw_lines: list[dict] = []
    with fitz.open(stream=raw, filetype="pdf") as doc:
        n_pages = min(len(doc), 200)  # generous cap; whole doc in practice
        for pidx in range(n_pages):
            page = doc[pidx]
            data = page.get_text("dict") or {}
            for block in data.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue  # 0 == text block; skip images/drawings
                for line in block.get("lines", []):
                    spans = line.get("spans", []) or []
                    parts: list[str] = []
                    max_size = 0.0
                    any_bold = False
                    for sp in spans:
                        t = sp.get("text", "")
                        if not t:
                            continue
                        parts.append(t)
                        sz = float(sp.get("size", 0) or 0)
                        if sz > max_size:
                            max_size = sz
                        # PyMuPDF span flags: bit 4 (2**4 == 16) == bold.
                        if int(sp.get("flags", 0)) & (2 ** 4):
                            any_bold = True
                    text = _norm_ws("".join(parts))
                    if not text:
                        continue
                    bbox = line.get("bbox") or [0, 0, 0, 0]
                    raw_lines.append(
                        {
                            "page": pidx,
                            "y": float(bbox[1]),
                            "size": round(max_size, 1),
                            "bold": any_bold,
                            "text": text,
                        }
                    )

    if not raw_lines:
        return []

    # BODY size = most common rounded span size across the document. Using the
    # mode (not the mean) keeps a few huge headings from dragging the baseline
    # up. Fall back to the median-ish value if Counter is somehow empty.
    size_counts = Counter(ln["size"] for ln in raw_lines if ln["size"] > 0)
    body_size = size_counts.most_common(1)[0][0] if size_counts else 12.0
    # Largest size anywhere on page 1 → candidate title size.
    p1_sizes = [ln["size"] for ln in raw_lines if ln["page"] == 0 and ln["size"] > 0]
    title_size = max(p1_sizes) if p1_sizes else body_size

    blocks: list[Block] = []
    title_used = False
    pending_bullet = False  # set when the previous line was a lone bullet glyph
    pending_marker = ""     # the actual glyph from that lone-bullet line
    for ln in raw_lines:
        text = ln["text"]
        size = ln["size"]
        bold = ln["bold"]

        # --- lone bullet glyph on its own line: the marker for the NEXT line.
        #     Remember it and skip emitting anything for the glyph itself. ---
        if _BULLET_ONLY_RE.match(text):
            pending_bullet = True
            pending_marker = text.strip() or "•"
            continue

        # A pending lone bullet OR an inline bullet-glyph prefix makes this a
        # list item. Capture the ACTUAL glyph (so render reproduces it exactly,
        # not a normalized "•"), then strip it so the translator never sees it.
        marker: Optional[str] = None
        if pending_bullet:
            marker = pending_marker or "•"
            is_bullet = True
        elif _BULLET_RE.match(text):
            m = _BULLET_RE.match(text)
            marker = text[m.start():m.end()].strip() or "•"
            is_bullet = True
        else:
            is_bullet = False
        if is_bullet:
            text = _strip_bullet(text)  # no-op if no inline glyph
        pending_bullet = False
        pending_marker = ""

        # Classify via the shared helper (same tiering translate_pdf reuses).
        kind = _classify_line_kind(
            text,
            size,
            bold,
            page=ln["page"],
            body_size=body_size,
            title_size=title_size,
            title_used=title_used,
            is_bullet=is_bullet,
        )

        if kind == "li":
            if text:  # drop a bullet line that was only the glyph
                # marker = the real glyph when one was present; "" when the
                # classifier flagged li with NO source glyph (bold-lead-in
                # pseudo-list) so render won't fabricate a bullet.
                blocks.append(Block(
                    type="li", text=text,
                    marker=(marker if marker is not None else ""),
                ))
            continue
        if kind == "title":
            blocks.append(Block(type="title", text=text))
            title_used = True  # single title per document
            continue
        blocks.append(Block(type=kind, text=text))

    return _merge_adjacent_paragraphs(blocks)


def _merge_adjacent_paragraphs(blocks: list[Block]) -> list[Block]:
    """Glue consecutive `p` blocks that are obviously one wrapped paragraph.

    PyMuPDF emits one line per visual row, so a flowing paragraph arrives as
    several `p` lines. We join consecutive `p` blocks into one when the prior
    line doesn't end on sentence-final punctuation (a soft wrap) — this gives
    the translator whole sentences (better quality) and the renderer real
    paragraphs. Headings / titles / list items are never merged."""
    out: list[Block] = []
    for b in blocks:
        if (
            b.type == "p"
            and out
            and out[-1].type == "p"
            and not re.search(r"[.!?:;)。！？]\s*$", out[-1].text)
            and len(out[-1].text) < 2000
        ):
            joiner = "" if out[-1].text.endswith("-") else " "
            merged = (out[-1].text.rstrip("-") + joiner + b.text).strip()
            out[-1] = Block(type="p", text=merged)
        else:
            out.append(b)
    return out


# ---------------------------------------------------------------------------
# Non-PDF path — text heuristic over the summarizer's _extract_doc_text.
# ---------------------------------------------------------------------------
def _extract_text_blocks(text: str) -> list[Block]:
    """Classify plain document text into typed blocks.

    Paragraphs are blank-line-separated. Within a paragraph: a bullet-
    prefixed line → li; a SHORT line that is ALL-CAPS or stands alone (its
    paragraph is a single short line) → heading; otherwise body. The first
    qualifying heading-ish line of the doc is promoted to the title."""
    blocks: list[Block] = []
    title_used = False
    # Split on blank lines into paragraph groups, preserving order.
    paras = re.split(r"\n\s*\n", text.strip())
    for para in paras:
        lines = [ln for ln in (l.rstrip() for l in para.splitlines()) if ln.strip()]
        if not lines:
            continue

        # All lines bulleted → a list. Mixed → handle line-by-line below.
        if all(_BULLET_RE.match(ln) for ln in lines):
            for ln in lines:
                stripped = _strip_bullet(_norm_ws(ln))
                if stripped:
                    blocks.append(Block(type="li", text=stripped))
            continue

        # A single short line in its own paragraph reads as a heading.
        if len(lines) == 1:
            one = _norm_ws(lines[0])
            words = len(one.split())
            is_caps = one.upper() == one and any(c.isalpha() for c in one)
            if words <= 12 and (is_caps or len(one) <= 60):
                if not title_used:
                    blocks.append(Block(type="title", text=one))
                    title_used = True
                else:
                    blocks.append(Block(type="h1" if is_caps else "h2", text=one))
                continue
            # Otherwise a one-line paragraph.
            blocks.append(Block(type="p", text=one))
            continue

        # Multi-line paragraph: first line may be an inline heading (short +
        # ALL-CAPS), the rest is body; bullet lines inside become li.
        body_buf: list[str] = []

        def _flush_body():
            if body_buf:
                joined = _norm_ws(" ".join(body_buf))
                if joined:
                    blocks.append(Block(type="p", text=joined))
                body_buf.clear()

        for idx, ln in enumerate(lines):
            n = _norm_ws(ln)
            if _BULLET_RE.match(ln):
                _flush_body()
                stripped = _strip_bullet(n)
                if stripped:
                    blocks.append(Block(type="li", text=stripped))
                continue
            is_caps = n.upper() == n and any(c.isalpha() for c in n)
            if idx == 0 and is_caps and len(n.split()) <= 12:
                if not title_used:
                    blocks.append(Block(type="title", text=n))
                    title_used = True
                else:
                    blocks.append(Block(type="h1", text=n))
                continue
            body_buf.append(n)
        _flush_body()

    return blocks


def _extract_blocks_sync(image: Image, raw: bytes) -> list[Block]:
    """Blocking structured extraction (runs in a thread). PDFs go through the
    PyMuPDF layout path; everything else falls back to the text heuristic over
    the summarizer's `_extract_doc_text`. Lazy heavy imports inside."""
    fname = (image.original_filename or "file")
    mime = (image.mime_type_original or "").lower().split(";")[0].strip()
    is_pdf = fname.lower().endswith(".pdf") or mime == "application/pdf"

    if is_pdf:
        try:
            blocks = _extract_pdf_blocks(raw)
            if blocks:
                return blocks
        except Exception:
            logger.exception("translate-doc: PDF structured extract failed; "
                             "falling back to text heuristic")
        # PDF parse failed or yielded nothing usable → fall through to text.

    # Non-PDF (or PDF fallback): pull plain text with the summarizer's
    # extractor (lazy import — transitively pulls PyMuPDF / python-docx / …).
    from backend.summarize import _extract_doc_text

    text, _topic = _extract_doc_text(fname, raw, image.mime_type_original)
    return _extract_text_blocks(text or "")


def _fetch_original_sync(image: Image) -> bytes:
    """Blocking fetch of the ORIGINAL document bytes. Documents live only in
    the originals bucket (never transcoded to a served variant), so the
    original key is what we read. Mirrors doctext.py."""
    return storage.get(storage.bucket_originals, image.original_blob_key)


# ===========================================================================
# Streaming structured translation
# ===========================================================================
class TranslateDocStreamRequest(BaseModel):
    # Target language: a FLORES-200 code (spa_Latn, …) or an ISO-639-1 code
    # (es, …). Unknown/empty falls back to English server-side. No `text`
    # field — the document bytes are loaded server-side from the owned image.
    target: str = Field(default="eng_Latn", max_length=40)


def _translate_block_text_sync(
    model, tokenizer, device, text: str, gen_kwargs: dict
) -> str:
    """Translate ONE block's text whole. Long blocks are sub-chunked on
    paragraph/sentence/word boundaries (so NLLB never overflows its token
    window) and the translated pieces are rejoined — but the block keeps its
    single type, so the caller still emits exactly one NDJSON line for it.
    Runs in a thread via asyncio.to_thread. Reuses translate_stream.py's
    per-chunk generate so output matches the other translate paths."""
    text = (text or "").strip()
    if not text:
        return ""
    chunks = [c for c in _chunk_text(text, _NLLB_CHUNK_CHARS) if c.strip()] or [text]
    parts: list[str] = []
    for ch in chunks:
        parts.append(
            _translate_one_chunk_sync(model, tokenizer, device, ch, gen_kwargs)
        )
    # Most blocks are a single chunk; multi-chunk blocks rejoin with a space
    # (they were split mid-paragraph), collapsing accidental double spaces.
    return _norm_ws(" ".join(p for p in parts if p))


async def _translate_doc_stream_gen(image: Image, raw: bytes, target: str):
    """Async NDJSON generator. Extracts typed blocks, loads NLLB + resolves
    source/forced-BOS ONCE, then translates each block in a thread and yields
    one typed NDJSON line per block (flushed as produced). NEVER raises out of
    the generator — any failure becomes a terminal `{"error": ...}` line so
    the HTTP stream always terminates instead of hanging."""
    try:
        # 1) Structured extraction (blocking → thread).
        blocks = await asyncio.to_thread(_extract_blocks_sync, image, raw)
        blocks = [b for b in blocks if (b.text or "").strip()]
        n = len(blocks)
        if n == 0:
            yield json.dumps(
                {"error": "No readable text found in this document."}
            ) + "\n"
            return

        # 2) Load NLLB (cached after first use) + resolve languages ONCE. We
        #    detect the source over a sample of the extracted text (joined
        #    block text), not per block, so the whole doc translates from one
        #    consistent source language.
        model, tokenizer, device = await asyncio.to_thread(_get_nllb)

        sample = " ".join(b.text for b in blocks)[:2000]
        src_flores = _detect_src_flores(sample)
        tgt_flores = _to_flores(target)
        src_name = _flores_name(src_flores)
        tgt_name = _flores_name(tgt_flores)

        try:
            tokenizer.src_lang = src_flores
        except Exception:
            pass

        forced_bos = _resolve_forced_bos(tokenizer, tgt_flores)
        gen_kwargs: dict = dict(
            do_sample=False,
            num_beams=1,
            no_repeat_ngram_size=3,
        )
        if forced_bos is not None:
            gen_kwargs["forced_bos_token_id"] = forced_bos
        # Route through the new Apache-2.0 engine first (NLLB stays the
        # fallback) — the shared per-chunk fn reads these private keys.
        gen_kwargs["_nt_target"] = tgt_flores
        gen_kwargs["_nt_source"] = src_flores

        # 3) One translation per block, off the event loop; flush per line.
        for i, b in enumerate(blocks):
            translated = await asyncio.to_thread(
                _translate_block_text_sync,
                model,
                tokenizer,
                device,
                b.text,
                gen_kwargs,
            )
            yield json.dumps(
                {
                    "i": i,
                    "n": n,
                    "type": b.type,
                    "text": translated,
                    # Sub-project D: carry the original list marker so the FE can
                    # echo it back to /render-translated-pdf for a faithful list.
                    "marker": b.marker,
                    "source_lang": src_name,
                    "target_lang": tgt_name,
                }
            ) + "\n"

        # 4) Completion sentinel.
        yield json.dumps({"done": True, "n": n}) + "\n"
    except RuntimeError as exc:
        # NLLB unavailable (sentencepiece / weights missing). Clean error line.
        logger.info("translate-doc-stream: NLLB unavailable: %s", exc)
        yield json.dumps(
            {"error": "Translation model is not available in this build."}
        ) + "\n"
    except Exception:
        logger.exception("translate-doc-stream: generation failed")
        yield json.dumps({"error": "Translation failed."}) + "\n"


@router.post("/{image_id}/translate-doc-stream")
async def translate_doc_stream(
    image_id: UUID,
    body: TranslateDocStreamRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Stream a STRUCTURE-PRESERVING translation of an owned document.

    Loads the document's bytes, extracts typed blocks (title/headings/
    bullets/paragraphs) in reading order, and streams one translated NDJSON
    line per block. Owner-scoped (404 for non-owners / missing rows) like
    `/ocr` + `/translate-stream`. The heavy work (PDF parse + NLLB) happens
    inside the async generator (each step in a thread) so the event loop stays
    responsive. Errors surface as a terminal `{"error": ...}` NDJSON line
    rather than an HTTP error, so a partial stream never hangs."""
    image = await _load_owned_image(image_id, user, session)
    if image.original_blob_key is None:
        # Hybrid retention may have dropped the original; without it there are
        # no bytes to extract structure from.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes unavailable"
        )
    try:
        raw = await asyncio.to_thread(_fetch_original_sync, image)
    except Exception as exc:  # storage miss
        logger.exception("translate-doc: could not read bytes for %s", image_id)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes unavailable"
        ) from exc

    return StreamingResponse(
        _translate_doc_stream_gen(image, raw, body.target),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===========================================================================
# Render translated blocks → structured PDF (no re-translation)
# ===========================================================================
class RenderBlock(BaseModel):
    type: str = Field(max_length=16)
    text: str = Field(default="", max_length=20_000)
    # Sub-project D: original list marker (see Block.marker). None when the
    # client didn't supply one → renderer uses the legacy "•" default.
    marker: Optional[str] = Field(default=None, max_length=8)


class RenderPdfRequest(BaseModel):
    filename: str = Field(default="document", max_length=300)
    # The blocks the FE already collected from the stream. Capped so a
    # malicious/huge body can't exhaust memory; total text is additionally
    # capped in the worker.
    blocks: list[RenderBlock] = Field(default_factory=list, max_length=5000)
    source_lang: str = Field(default="", max_length=80)
    target_lang: str = Field(default="", max_length=80)


# Total translated text budget for one render (defence-in-depth on top of the
# per-block + block-count caps). 300k chars ≈ a long book; well within
# ReportLab's comfort zone but bounds memory/CPU.
_MAX_RENDER_CHARS = 300_000

# Candidate Unicode TTFs, in preference order. DejaVuSans ships in the
# container (matplotlib's bundle + the system fonts tree). We check both so a
# rebuild that drops one still finds the other.
_DEJAVU_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/opt/venv/lib/python3.12/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf",
)
_DEJAVU_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/opt/venv/lib/python3.12/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans-Bold.ttf",
)

# Module-level latch so we register the TTF with ReportLab's global font
# registry exactly once per process (re-registering is harmless but wasteful).
_FONTS_REGISTERED = False
_UNICODE_FONT: Optional[str] = None  # "DejaVuSans" once registered, else None


def _first_existing(paths) -> Optional[str]:
    import os

    for p in paths:
        try:
            if os.path.isfile(p):
                return p
        except Exception:
            continue
    return None


# Sub-project D: Noto coverage for the RENDERED translation PDF so a document
# translated INTO CJK/Arabic/Hebrew/Indic/Thai shows real glyphs instead of tofu
# (the same gap sub-project B closed for in-image text). Registered with
# ReportLab on demand and selected per block by the block's dominant script.
_PDF_NOTO = {
    # ReportLab can't embed NotoSansCJK (CFF/PostScript outlines); WenQuanYi
    # Micro Hei is TrueType and covers CJK. (The PIL image path uses Noto CJK.)
    "cjk": ("CJKHei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    "arabic": ("NotoArabic", "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf", None),
    "hebrew": ("NotoHebrew", "/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf", None),
    "devanagari": ("NotoDeva", "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf", None),
    "thai": ("NotoThai", "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf", None),
}
_PDF_SCRIPT_RANGES = (
    ("cjk", ((0x4E00, 0x9FFF), (0x3040, 0x30FF), (0xAC00, 0xD7A3), (0x3400, 0x4DBF))),
    ("arabic", ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))),
    ("hebrew", ((0x0590, 0x05FF),)),
    ("devanagari", ((0x0900, 0x097F),)),
    ("thai", ((0x0E00, 0x0E7F),)),
)
# script key -> registered ReportLab font name (populated by _register_unicode_font).
_PDF_SCRIPT_FONTS: dict[str, str] = {}


def _pdf_script_for(text: str) -> Optional[str]:
    """Dominant covered non-Latin script in `text`, or None (Latin/Cyrillic/Greek
    -> the base DejaVu font is fine)."""
    if not text:
        return None
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for key, ranges in _PDF_SCRIPT_RANGES:
            if any(lo <= cp <= hi for lo, hi in ranges):
                counts[key] = counts.get(key, 0) + 1
                break
    return max(counts, key=counts.get) if counts else None


def _register_unicode_font() -> Optional[str]:
    """Register DejaVuSans (regular + bold) with ReportLab so accented Latin /
    Cyrillic / Greek glyphs render. Returns the registered family base name
    ("DejaVuSans") or None when no Unicode TTF is available (caller then uses
    Helvetica with a coverage caveat). Idempotent + cached."""
    global _FONTS_REGISTERED, _UNICODE_FONT
    if _FONTS_REGISTERED:
        return _UNICODE_FONT
    _FONTS_REGISTERED = True

    import os

    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Register Noto faces for non-Latin scripts first (independent of DejaVu) so
    # translated documents in CJK/Arabic/etc. render real glyphs, not tofu.
    for _key, (_name, _path, _idx) in _PDF_NOTO.items():
        if _key in _PDF_SCRIPT_FONTS or not os.path.isfile(_path):
            continue
        try:
            if _idx is None:
                pdfmetrics.registerFont(TTFont(_name, _path))
            else:
                pdfmetrics.registerFont(TTFont(_name, _path, subfontIndex=_idx))
            _PDF_SCRIPT_FONTS[_key] = _name
        except Exception:
            logger.exception("render-pdf: Noto %s registration failed", _key)

    reg = _first_existing(_DEJAVU_REGULAR_CANDIDATES)
    if not reg:
        _UNICODE_FONT = None
        return None
    bold = _first_existing(_DEJAVU_BOLD_CANDIDATES)
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", reg))
        if bold:
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
        # Map the family so Platypus' <b> / Heading bold resolves to the bold
        # TTF (regular,bold,italic,boldItalic) — fall back to regular when we
        # have no bold/italic face.
        addMapping("DejaVuSans", 0, 0, "DejaVuSans")
        addMapping("DejaVuSans", 1, 0, "DejaVuSans-Bold" if bold else "DejaVuSans")
        addMapping("DejaVuSans", 0, 1, "DejaVuSans")
        addMapping("DejaVuSans", 1, 1, "DejaVuSans-Bold" if bold else "DejaVuSans")
        _UNICODE_FONT = "DejaVuSans"
    except Exception:
        logger.exception("render-pdf: DejaVuSans registration failed")
        _UNICODE_FONT = None
    return _UNICODE_FONT


def _pdf_escape(s: str) -> str:
    """Escape text for ReportLab Paragraph mini-HTML. Platypus parses a tiny
    XML/HTML subset, so a literal `&`, `<`, or `>` in translated text would be
    mis-parsed (or raise). Escape them; everything else (incl. accented
    Unicode) is passed through untouched."""
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_styles(font: Optional[str]):
    """Build the paragraph styles for each block type. When a Unicode `font`
    is registered we point every style (and its bold variant) at it so accents
    render; otherwise the sample stylesheet's Helvetica/Times defaults stand."""
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    ss = getSampleStyleSheet()
    bold_font = f"{font}-Bold" if font else None

    def _style(base_name: str, **over):
        base = ss[base_name]
        want_bold = over.pop("_bold", False)  # pop unconditionally — never a
        # valid ParagraphStyle kwarg, so it must not leak into the constructor.
        kw = dict(
            name=f"nt_{base_name}",
            parent=base,
        )
        if font:
            # Headings/title use bold; body/list use regular.
            kw["fontName"] = bold_font if want_bold else font
        kw.update(over)
        return ParagraphStyle(**kw)

    styles = {
        "title": _style("Title", _bold=True, spaceAfter=14, spaceBefore=4),
        "h1": _style("Heading1", _bold=True, spaceBefore=12, spaceAfter=6),
        "h2": _style("Heading2", _bold=True, spaceBefore=10, spaceAfter=5),
        "p": _style("BodyText", spaceBefore=2, spaceAfter=8, leading=15),
        # List item: indented body paragraph; the bullet is added as a glyph
        # prefix so we don't depend on ListFlowable styling.
        "li": _style(
            "BodyText",
            name="nt_li",
            leftIndent=18,
            bulletIndent=6,
            spaceBefore=1,
            spaceAfter=4,
            leading=15,
        ),
    }
    return styles


def _render_pdf_sync(req: RenderPdfRequest) -> tuple[bytes, str]:
    """Blocking PDF render with ReportLab Platypus. Returns (pdf_bytes,
    note). Maps each typed block to a Platypus flowable:
      title → Title, h1 → Heading1, h2 → Heading2, li → bulleted/indented
      paragraph, p → BodyText. Uses DejaVuSans (Unicode) when available so
      accents render; otherwise Helvetica + a coverage note."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font = _register_unicode_font()
    styles = _build_styles(font)

    note_bits: list[str] = []
    if font:
        extra = (
            f" Non-Latin scripts use bundled Noto fonts "
            f"({', '.join(sorted(_PDF_SCRIPT_FONTS))})."
            if _PDF_SCRIPT_FONTS else
            " CJK/Arabic scripts are not covered and may appear as boxes."
        )
        note_bits.append(
            "Rendered with the DejaVuSans Unicode font (Latin/Cyrillic/Greek "
            "accents render correctly)." + extra
        )
    else:
        note_bits.append(
            "No Unicode TTF was available, so the PDF uses the built-in "
            "Helvetica font, which only covers Latin-1; non-Latin-1 accented "
            "characters may be dropped."
        )

    flow: list = []
    used = 0
    truncated = False
    for blk in req.blocks:
        btype = blk.type if blk.type in _BLOCK_TYPES else "p"
        text = (blk.text or "").strip()
        if not text:
            if btype in ("title", "h1", "h2"):
                continue  # skip empty headings; keep blank body spacing minimal
            continue
        if used + len(text) > _MAX_RENDER_CHARS:
            truncated = True
            break
        used += len(text)
        safe = _pdf_escape(text)
        style = styles[btype]
        # Sub-project D: render non-Latin blocks with the matching Noto face so
        # CJK/Arabic/etc. don't become tofu; Latin blocks keep the base font.
        sf = _PDF_SCRIPT_FONTS.get(_pdf_script_for(text) or "")
        if sf:
            from reportlab.lib.styles import ParagraphStyle
            style = ParagraphStyle(name=f"{style.name}_sf", parent=style, fontName=sf)
        if btype == "li":
            # Reproduce the source marker faithfully: None (unknown client) ->
            # legacy "•"; "" (source had no glyph) -> no fabricated bullet;
            # otherwise the original glyph (•/-/* …).
            mk = blk.marker
            if mk is None:
                prefix = "•&nbsp;"
            elif mk.strip():
                prefix = _pdf_escape(mk) + "&nbsp;"
            else:
                prefix = ""
            flow.append(Paragraph(prefix + safe, style))
        else:
            flow.append(Paragraph(safe, style))

    if not flow:
        # Always produce a valid, non-empty PDF rather than a 0-byte body.
        flow.append(Paragraph("(No content to render.)", styles["p"]))

    if truncated:
        note_bits.append(
            f"Rendered the first {used:,} characters; the document exceeded the "
            f"{_MAX_RENDER_CHARS:,}-character render cap."
        )

    title_meta = req.filename or "Translated document"
    if req.target_lang:
        subj = f"Translated to {req.target_lang}"
        if req.source_lang:
            subj = f"Translated {req.source_lang} → {req.target_lang}"
    else:
        subj = "Translated document"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title_meta,
        subject=subj,
        author="neuthek",
    )
    doc.build(flow)
    return buf.getvalue(), " ".join(note_bits)


def _safe_attachment_name(filename: str, target_lang: str) -> str:
    """Build the download filename: "<original base> (<target_lang>).pdf".
    Strips any path + the original extension, drops characters that would
    break a Content-Disposition header, and guards against an empty base."""
    import os

    base = os.path.basename(filename or "document")
    base = os.path.splitext(base)[0].strip() or "document"
    lang = (target_lang or "").strip()
    stem = f"{base} ({lang})" if lang else base
    # Keep it header-safe: no quotes / CR / LF / control chars.
    stem = re.sub(r'[\r\n"\\/]+', " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip() or "document"
    return f"{stem}.pdf"


@render_router.post("/render-translated-pdf")
async def render_translated_pdf(
    body: RenderPdfRequest,
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    """Render translated blocks (from the stream the FE already consumed) into
    a clean STRUCTURED PDF and return it as a download. NO re-translation —
    the FE supplies the final translated blocks. Authed (any logged-in user);
    there's no image id because the bytes are reconstructed entirely from the
    request body. Heavy ReportLab work runs in a thread."""
    if not body.blocks:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No blocks to render."
        )
    try:
        pdf_bytes, _note = await asyncio.to_thread(_render_pdf_sync, body)
    except Exception as exc:
        logger.exception("render-translated-pdf: render failed")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Could not render the translated PDF.",
        ) from exc

    fname = _safe_attachment_name(body.filename, body.target_lang)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
        },
    )
