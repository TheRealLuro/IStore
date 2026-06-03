"""Exact-copy PDF translation (NEW, unwired).

A single route, authed + owner-scoped (same gate as backend/api/ocr.py's
`/translate`, doctext.py's `/text`, and translate_doc.py's
`/translate-doc-stream`):

  POST /images/{id}/translate-pdf   (authed + owner-scoped)
       Body: {"target": "spa_Latn"}   (FLORES-200 code OR ISO code; e.g.
             "es" → spa_Latn; empty/unknown → English)

       Produces an EXACT COPY of the owned PDF with ONLY the text translated:
       same page geometry, images, vector graphics, colors and positions —
       the original text is redacted IN PLACE and the translation reinserted
       into the SAME boxes (wrapping + auto-shrinking so a longer translation
       never overflows). This is NOT the structured-outline path
       (translate_doc.py renders a fresh typed PDF); here the output is a
       pixel-faithful clone whose words are in another language.

       The file MUST be a PDF — otherwise 415 {"detail": "not_a_pdf"}.

       Response: an `application/x-ndjson` StreamingResponse. The body is an
       ASYNC GENERATOR that emits ONE JSON object per line:
         per unit   : {"i": <1-based count>, "n": <total units>,
                       "text": "<translated block (marker + body)>",
                       "kind": "<title|h1|h2|p|li>"}
                      (the FE shows "Translating k / total" AND renders a
                      FORMATTED live preview using `kind`; `kind` is derived
                      from the per-block font signals — see PIPELINE step 2b.
                      TTS still consumes `text` from these lines.)
         page image : {"i": <0|1-based count>, "n": <total units>,
                       "page": <0-based page index>, "pages": <total pages>,
                       "page_png_b64": "<base64 PNG of the REAL page>"}
                      (Change 1: the FIRST such line is emitted IMMEDIATELY with
                      "i":0 — the ORIGINAL, UNMODIFIED first page (zero
                      translated blocks) — BEFORE the model loads, so the FE
                      instantly shows the REAL document in its EXACT format
                      instead of the HTML reflow. Then ~9 FREQUENT checkpoints
                      (every ~11% of blocks incl. the final state) stream the
                      ACTUAL document — redaction + reinsertion of the blocks
                      translated SO FAR rasterized at DPI ~110 — so the page
                      visibly fills in, progressively turning into the target
                      language, not an HTML approximation. One line PER rendered
                      page; several page lines may share an `i`. Best-effort: a
                      render miss simply emits no page line for that checkpoint.)
         on finish  : {"done": true, "pdf_b64": "<base64 of the new PDF>",
                       "source_lang": "<human name>",
                       "target_lang": "<human name>"}
                      (UNCHANGED — the full exact-resolution translated PDF.)
         on error   : {"error": "<short message>"}   (the generator NEVER
                      raises out — a raise would hang the HTTP stream)

PIPELINE  (PyMuPDF / fitz; all blocking work via asyncio.to_thread)
-------------------------------------------------------------------
1. Open the PDF bytes with `fitz.open(stream=..., filetype="pdf")`.
2. For every page, extract text UNITS with positions from
   `page.get_text("dict")` → blocks → lines → spans. We use LINE-level units
   (a line reads as one phrase and keeps a tight bbox) with a representative
   bbox + the DOMINANT span size / color / font / bold of the line. Units are
   collected across all pages; the total count is `n` (drives progress).
2b. CLASSIFY each unit into a structural `kind` ∈ {title, h1, h2, p, li} from
   the size / bold / page / marker we ALREADY have (no extra parse, no model):
   compute the BODY size (mode of line sizes) and the page-1 TITLE size (max on
   page 0), then call translate_doc.py's shared `_classify_line_kind` — the
   SAME tiering the structured-doc view uses (largest page-1 line → title;
   numbered/larger/bold-short lines → h1/h2 by size tier; bullet-glyph markers
   → li; else p). `kind` rides each progress line so the FE renders a formatted
   live preview.
3. Detect the source language ONCE (over a sample of the joined unit text),
   resolve the forced-BOS target token ONCE, then translate each unit's text
   with the SAME cached NLLB-200 model the other translate paths use. A
   leading bullet / number marker ("•", "-", "1.", "2.1") is preserved
   verbatim — only the text after the marker is translated. A {"i": k, "n": n}
   progress line is flushed as each unit finishes.
4. ERASE the original text in place: each unit adds a redaction annot over its
   bbox (`page.add_redact_annot(bbox, fill=None, cross_out=False)` — no fill,
   so we don't paint a white box), then `page.apply_redactions(...)` is called
   with images=PDF_REDACT_IMAGE_NONE and graphics=PDF_REDACT_LINE_ART_NONE so
   IMAGES and VECTOR GRAPHICS are PRESERVED and only the text glyphs under the
   annots are removed.
5. REINSERT the translated text into each unit's bbox so it WRAPS and
   AUTO-SHRINKS to fit: `page.insert_htmlbox(rect, html, css=...)` returns
   `(spare_height, scale)`; scale < 1 means it was shrunk to fit (longer
   translations therefore never overflow). The HTML/CSS matches the original:
   start at the original font size, use the original span color, bold when the
   original was bold, and a serif-vs-sans family guess from the original font
   name. Left-aligned, top-anchored.
5b. TRANSLATE EMBEDDED FIGURES IN PLACE: after the text redact+reinsert, each
   page's embedded RASTER images (figures / charts / screenshots) still carry
   their original-language text in PIXELS. We enumerate them
   (`page.get_images(full=True)` → `doc.extract_image(xref)` for bytes,
   `page.get_image_rects(xref)` for placement) and, for each one big enough to
   plausibly hold readable text (skip tiny icons / bullets / rule lines), run the
   EXISTING in-image translate pipeline (translate_image.py: Florence OCR →
   translate-to-target → cv2 inpaint → PIL render) to the SAME target language,
   then cover the original placement and stamp the translated PNG over the SAME
   rect (`page.insert_image(rect, stream=...)`). If OCR finds no text the image
   is left unchanged. This reuses the ALREADY-RESIDENT Florence + NLLB/MADLAD
   models in-process (no new model, no subprocess). It is strictly BOUNDED
   (≤ `_MAX_EMBEDDED_IMAGES` per doc, each under the in-image OCR deadline
   `_TI_OCR_DEADLINE_S`, all blocking work via asyncio.to_thread) and NON-FATAL
   per image (each wrapped in try/except — one failure is skipped, never breaks
   the stream). It runs AFTER the text path so the primary text win is never
   slowed; figures are purely additive.
6. `doc.tobytes(deflate=True, garbage=3)` → base64 → the final NDJSON line.
   Pages with NO text layer (scanned / image-only) are left UNTOUCHED and the
   pipeline continues — they simply contribute no units (an image-only page's
   embedded figures are still translated by step 5b).

REUSE / IMPORT-SAFETY
---------------------
The NLLB internals (`_get_nllb`, `_detect_src_flores`, `_to_flores`,
`_flores_name`), the owner-scoping (`_load_owned_image`) and the per-chunk
generate (`_resolve_forced_bos`, `_translate_one_chunk_sync`) are imported
from ocr.py / translate_stream.py — the SAME model load + chunk output the
OCR, /translate, /translate-stream and /translate-doc-stream paths use. The
owned PDF bytes are loaded exactly like doctext.py / translate_doc.py:
`storage.get(storage.bucket_originals, image.original_blob_key)` in a thread.

The embedded-FIGURE translation (step 5b) reuses translate_image.py's in-image
pipeline directly (`_ocr_stage`, `_prepare_translation`, `_translate_one_region`,
`_compose_png` + its `_OCR_DEADLINE_S`) — the SAME Florence OCR + NLLB/MADLAD
models a standalone /translate-image call drives, called IN-PROCESS so no second
model copy is loaded on the (tight) GPU. translate_image.py is itself import-safe
(no ML at import), so importing it here is free.

As in those modules NOTHING heavy is imported at module-import time — `fitz`
and `torch` (via the reused fns) only load INSIDE the worker functions, so
importing this module can never crash uvicorn --reload (a broken import there
would take the whole API down).

There is no Qwen fallback here (matching translate_stream.py /
translate_doc.py): the win is NLLB-on-GPU. If NLLB can't load the generator
emits one `{"error": ...}` line and stops cleanly.

LIMITATIONS (honest notes)
--------------------------
* Font matching is APPROXIMATE: PyMuPDF's `insert_htmlbox` lays the text out
  with its own serif/sans web fonts (it cannot embed the document's exact
  font), so the translated glyphs match the original's COLOR, weight and a
  serif-vs-sans family — but not the precise typeface. Color and position are
  faithful.
* Overflow handling relies on insert_htmlbox's auto-scale: a much longer
  translation in a tight box shrinks to fit, so it can render smaller than the
  original. The text never spills outside its box.
* SCANNED / image-only PDFs (no text layer) have no TEXT units to redact or
  reinsert, but their embedded raster images ARE translated in place by step 5b
  (the in-image pipeline OCRs the pixels). A page that is one big full-page scan
  is still better served by the standalone in-image route (we skip embedded
  images above `_MAX_EMBEDDED_IMAGE_BYTES`).
* EMBEDDED-FIGURE translation carries the in-image pipeline's own limitations
  (best on PRINTED text over flat backgrounds; matches the original ink's
  serif-ness / weight / color / position, not the exact typeface; CJK / Arabic
  targets render as tofu). It is bounded (≤ `_MAX_EMBEDDED_IMAGES` figures per
  document) and non-fatal per image, so a long/figure-heavy PDF translates its
  first dozen qualifying figures and leaves the rest unchanged rather than
  spending unbounded time on the shared GPU.
"""
from __future__ import annotations

import asyncio
import os
import base64
import json
import logging
import re
import time
from collections import Counter
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User
from backend.storage import storage

# Reuse the NLLB internals + owner-scoping from ocr.py and the per-chunk
# helpers from translate_stream.py so every translate path shares ONE model
# load and identical output for the same chunk. All import-safe — neither
# module pulls ML at import time.
from backend.api.ocr import (
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
# Reuse the FULL in-image translate pipeline (NEW) so an embedded figure /
# screenshot inside a PDF page gets its printed text translated IN PLACE with
# the EXACT same OCR (Florence, single-orientation, bounded decode) →
# translate-to-target → cv2 inpaint → PIL render path a standalone image uses.
# We import the resident pipeline's helpers and call them IN-PROCESS (same
# cached Florence + NLLB/MADLAD models — NO new model, NO subprocess load):
#   * `_ti_ocr_stage`        — decode bytes → orientation → OCR regions;
#   * `_ti_prepare`          — load NLLB (cached) + detect source ONCE;
#   * `_ti_translate_region` — translate ONE region's text (best-effort);
#   * `_ti_compose_png`      — inpaint originals away + render translations +
#                              rotate back + encode PNG;
#   * `_TI_OCR_DEADLINE_S`   — the SAME 45s OCR wall-clock ceiling (reused as
#                              the per-embedded-image deadline).
# Import-safe: translate_image.py (like this module) pulls no ML at import time
# — torch / cv2 / PIL / fitz only load inside its worker fns — so importing it
# here can never crash uvicorn --reload.
from backend.api.translate_image import (
    _OCR_DEADLINE_S as _TI_OCR_DEADLINE_S,
    _compose_png as _ti_compose_png,
    _ocr_stage as _ti_ocr_stage,
    _prepare_translation as _ti_prepare,
    _translate_one_region as _ti_translate_region,
)
# The Apache-2.0 batch translator (MADLAD-400 + Opus-MT). We translate the
# document's blocks in PADDED BATCHES through `translate_batch` (one generate()
# per batch instead of one per block) — the single biggest win for a multi-
# hundred-block document. Import-safe: translate_engine pulls no ML at import.
from backend.api import translate_engine
# Reuse translate_doc.py's per-line block classifier so the live-preview `kind`
# this module emits matches the structured-doc view EXACTLY (one source of
# truth for title/h1/h2/p/li tiering). Pure + import-safe — no ML at import.
from backend.api.translate_doc import _classify_line_kind

logger = logging.getLogger(__name__)

# Same /images prefix + tags as ocr.py so the route reads as part of the
# images surface. Wired separately in app.py (see this module's task hand-off).
router = APIRouter(prefix="/images", tags=["images", "ocr"])


# Hard cap on the source PDF size we will process (defence-in-depth: a huge
# book would otherwise pin a worker thread for a long time). 50 MB comfortably
# covers normal documents.
_MAX_PDF_BYTES = 50 * 1024 * 1024
# Generous page cap so a pathological PDF can't run unbounded; whole document
# in practice.
_MAX_PAGES = 300

# How many block bodies we translate per PADDED BATCH through
# `translate_engine.translate_batch`. MADLAD's per-generate() cost is large and
# roughly fixed, so translating ~322 blocks ONE AT A TIME (the old path) cost
# ~322 generate() calls and crawled (the FE saw "2/322, 4/322 …" for minutes).
# Batching ~16 blocks per generate() collapses that to ~20 calls — minutes →
# ~10–25s — while strict 1:1 alignment back to each block is preserved (see
# `_translate_units_batched`). 16 keeps the padded batch tensor modest on the
# shared 8 GB card; the per-item token budget (`_BLOCK_ITEM_MAX_TOKENS`) means a
# longer line isn't truncated the way the 64-token loader-phrase path would cut.
_PDF_BATCH_SIZE = 16

# --- Live page-image snapshots (Change 1) ------------------------------------
# While translating we stream rasterized snapshots of the REAL PDF page with the
# blocks done SO FAR redacted-and-reinserted in the target language, so the FE
# shows the actual document's EXACT layout/fonts/bullets progressively turning
# into the target language (not an HTML approximation). To keep this cheap on
# the shared GPU/CPU we cap the number of snapshot *renders* and rasterize at a
# modest DPI — we never re-translate, we only re-render the already-translated
# text onto a fresh working copy of the doc.
#
# Number of snapshot points across the whole document (the LAST one is the
# fully-translated state). ~9 makes the page VISIBLY fill in (every ~11% of
# blocks) instead of jumping in quarters; each render is kept cheap (low DPI,
# ≤6 pages) so even ~9 redact+reinsert+rasterize passes stay negligible next to
# the translation itself. NOTE: the user ALSO gets an immediate block-0 snapshot
# of the ORIGINAL page BEFORE the loop (see `_translate_pdf_gen`), so the very
# first thing shown is the real document in its exact format — these checkpoints
# then progressively turn it into the target language.
_SNAPSHOT_POINTS = 9
# Rasterization DPI for the streamed page images — per the task, low enough to
# be cheap (110 dpi ≈ 1.5× a 72-dpi page) yet sharp enough to read the layout.
_SNAPSHOT_DPI = 110
# Cap how many PAGES we rasterize per snapshot, so a 200-page book can't emit a
# huge burst of base64 PNGs at every checkpoint. The pages that have received
# the most translated blocks so far are prioritized (the ones actually changing).
_SNAPSHOT_MAX_PAGES = 6

# --- Embedded-image (figure / screenshot) translation -----------------------
# After the per-page TEXT redact+reinsert, we ALSO translate the printed text
# INSIDE each page's embedded raster images (figures, charts, screenshots) IN
# PLACE, reusing the resident in-image pipeline (Florence OCR → NLLB translate →
# inpaint → render — see `_ti_*` imports above). The original-language text in a
# figure is otherwise untouched by the text path (it lives in pixels, not a text
# layer), so this is the missing half of an exact-copy translation.
#
# This work is STRICTLY BOUNDED so it never dominates the (text-first) stream or
# pins the shared GPU:
#   * cap the TOTAL embedded images translated per document — a slide deck or a
#     scan-heavy report could otherwise hold dozens of large rasters, each an
#     OCR+inpaint pass on the busy card;
# Sub-project A: raised for the 12 GB box (was 12, sized for the 8 GB 4060).
# Env-overridable. The wall-clock budget below still bounds the stage.
_MAX_EMBEDDED_IMAGES = int(os.environ.get("FIGURE_OCR_MAX", "40"))
#   * skip anything too small to plausibly hold READABLE text (icons, bullets,
#     rule lines, logos, decorative glyphs) — both the raw pixel size of the
#     extracted image AND its placed size on the page must clear this bar, so a
#     large source bitmap scaled down to a 20px logo is skipped too;
_MIN_EMBEDDED_IMAGE_PX = 64
#   * the per-image OCR runs under the SAME 45s deadline the standalone in-image
#     path uses (`_TI_OCR_DEADLINE_S`), via asyncio.wait_for, so one pathological
#     figure fails fast instead of hanging the document stream;
#   * EVERY image is wrapped in its own try/except — a failure (OCR miss, decode
#     error, unsupported colorspace, timeout) skips that image and is NEVER fatal
#     to the translate-pdf stream; the text translation already succeeded.
#
# We hard-skip images whose extracted byte size is implausibly large for a
# figure (defence-in-depth: a giant full-page scan embedded as one image would
# be slow to OCR+inpaint and is better served by the standalone in-image route).
_MAX_EMBEDDED_IMAGE_BYTES = 24 * 1024 * 1024

# Overall wall-clock budget for the ENTIRE embedded-image stage (all figures in
# the doc combined), separate from the per-image `_TI_OCR_DEADLINE_S`. Without
# this, an image-heavy doc (e.g. a 12-figure slide deck) would serially burn up
# to `_MAX_EMBEDDED_IMAGES * _TI_OCR_DEADLINE_S` ≈ 9 MINUTES of OCR AFTER the
# text is already translated — the loader just spins, which reads as "stuck /
# does nothing". We translate as many figures as fit in this budget, then stop
# and finalize the (already text-translated) PDF. On the 12GB box OCR is fast
# enough that this rarely trips; on 8GB it bounds the tail so the doc returns
# promptly with whatever figures we managed.
_FIGURES_TOTAL_BUDGET_S = float(os.environ.get("FIGURE_OCR_BUDGET_S", "240"))
# Sub-project A: process figures CONCURRENTLY (bounded) on the 12 GB GPU's
# headroom instead of strictly one-at-a-time. Each figure keeps its own per-image
# deadline; the semaphore caps in-flight GPU work (default 2 — conservative given
# the fleet nearly fills the card).
_FIGURE_CONCURRENCY = int(os.environ.get("FIGURE_OCR_CONCURRENCY", "2"))
# Use the 4-way orientation sweep for figures (was single-orientation for speed
# on 8 GB). The 0-degree early-exit keeps upright figures at a single pass, so
# this only costs extra on genuinely rotated/ambiguous figures.
_FIGURE_FOUR_WAY = os.environ.get(
    "FIGURE_OCR_FOUR_WAY", "1"
).strip().lower() in {"1", "true", "yes", "on"}

# Leading bullet / number marker we KEEP verbatim and translate only the rest.
# Covers bullet glyphs and dotted/numbered markers ("1.", "2.1", "3.2.4",
# "IV.", "a)"). The marker plus its trailing whitespace is captured in group 1.
_MARKER_RE = re.compile(
    r"^(\s*(?:[•‣◦▪·\-–—*]|\d+(?:\.\d+)*\.?|[IVXLCDM]+\.|[a-zA-Z][.)])\s+)(.*)$",
    re.DOTALL,
)

# Bullet glyphs (a SUBSET of the marker chars above): when a unit's preserved
# marker starts with one of these it's a LIST item, so `kind` == "li". Numeric/
# alpha/roman markers are NOT bullets — those classify by size like any line
# (and may be numbered headings). Matches translate_doc._BULLET_CHARS.
_BULLET_MARKER_CHARS = "•‣◦▪·-–—*"


class TranslatePdfRequest(BaseModel):
    # Target language: a FLORES-200 code (spa_Latn, …) or an ISO-639-1 code
    # (es, …). Unknown/empty falls back to English server-side. No `text`
    # field — the PDF bytes are loaded server-side from the owned image.
    target: str = Field(default="eng_Latn", max_length=40)


# ---------------------------------------------------------------------------
# Owned-PDF byte loading — mirrors doctext.py / translate_doc.py. Documents
# live only in the originals bucket (never transcoded to a served variant), so
# the original key is what we read.
# ---------------------------------------------------------------------------
def _fetch_original_sync(image: Image) -> bytes:
    return storage.get(storage.bucket_originals, image.original_blob_key)


def _is_pdf(image: Image) -> bool:
    """A document is a PDF when its filename ends in .pdf OR its stored MIME is
    application/pdf — same test translate_doc.py uses."""
    fname = (image.original_filename or "").lower()
    mime = (image.mime_type_original or "").lower().split(";")[0].strip()
    return fname.endswith(".pdf") or mime == "application/pdf"


# ---------------------------------------------------------------------------
# Helpers — colors, fonts, markers, HTML. All pure / cheap; the heavy fitz +
# NLLB work lives in the worker functions below.
# ---------------------------------------------------------------------------
def _int_to_rgb_hex(color: int) -> str:
    """PyMuPDF span `color` is a packed 24-bit sRGB int (0xRRGGBB). Convert to
    a CSS hex string. Defaults to black on anything odd."""
    try:
        c = int(color) & 0xFFFFFF
    except Exception:
        return "#000000"
    r = (c >> 16) & 0xFF
    g = (c >> 8) & 0xFF
    b = c & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def _font_is_serif(font_name: str) -> bool:
    """Guess serif vs sans from the original PDF font NAME. PDF font names are
    like 'ABCDEF+Times-Bold', 'Arial,Bold', 'Georgia'. We look for the common
    serif family tokens; everything else is treated as sans (the safe default
    for UI/most body PDFs)."""
    n = (font_name or "").lower()
    serif_tokens = (
        "times", "serif", "georgia", "garamond", "minion", "roman",
        "book antiqua", "palatino", "cambria", "baskerville", "caslon",
        "century", "merriweather", "constantia", "noto serif",
    )
    # 'sans-serif' contains 'serif' — guard against it explicitly.
    if "sans" in n:
        return False
    return any(tok in n for tok in serif_tokens)


def _font_is_mono(font_name: str) -> bool:
    n = (font_name or "").lower()
    return "mono" in n or "courier" in n or "consol" in n


def _split_marker(text: str) -> tuple[str, str]:
    """Split a leading bullet/number marker off `text`. Returns
    (marker_with_trailing_ws, rest). When there's no marker, ("", text)."""
    m = _MARKER_RE.match(text)
    if not m:
        return "", text
    marker, rest = m.group(1), m.group(2)
    # Only treat it as a marker when there is real text after it — otherwise a
    # standalone glyph/number IS the content and we translate it whole.
    if not rest.strip():
        return "", text
    return marker, rest


def _html_escape(s: str) -> str:
    """Escape text for the tiny HTML insert_htmlbox parses. Unicode (accents,
    Cyrillic, …) passes through untouched."""
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Unit extraction — page.get_text("dict") → line-level units with a tight
# bbox + dominant size / color / font / bold.
# ---------------------------------------------------------------------------
def _extract_units(doc) -> list[dict]:
    """Walk every page's text dict and build LINE-level translation units.

    Each unit = {page, bbox(x0,y0,x1,y1), text, size, color(hex), serif, mono,
    bold}. The bbox is the line bbox; size/color/font/bold are the DOMINANT
    span values of the line (the span covering the most characters), which
    reads as the line's visual style. Empty / whitespace-only lines are
    skipped. Image-only pages simply contribute nothing."""
    units: list[dict] = []
    n_pages = min(len(doc), _MAX_PAGES)
    for pidx in range(n_pages):
        page = doc[pidx]
        data = page.get_text("dict") or {}
        for block in data.get("blocks", []):
            if block.get("type", 0) != 0:
                continue  # 0 == text block; skip image blocks entirely
            for line in block.get("lines", []):
                spans = line.get("spans", []) or []
                parts: list[str] = []
                # Pick the DOMINANT span (most characters) for the line's
                # representative style — robust when a line mixes a tiny
                # super-script or a single bold word with body text.
                best_len = -1
                size = 0.0
                color_hex = "#000000"
                font_name = ""
                bold = False
                for sp in spans:
                    t = sp.get("text", "")
                    if not t:
                        continue
                    parts.append(t)
                    tl = len(t.strip())
                    if tl > best_len:
                        best_len = tl
                        size = float(sp.get("size", 0) or 0)
                        color_hex = _int_to_rgb_hex(sp.get("color", 0))
                        font_name = str(sp.get("font", "") or "")
                        # PyMuPDF span flags: bit 4 (2**4 == 16) == bold; some
                        # PDFs instead encode boldness only in the font name.
                        flags = int(sp.get("flags", 0))
                        bold = bool(flags & (2 ** 4)) or "bold" in font_name.lower()
                text = "".join(parts)
                if not text.strip():
                    continue
                bbox = line.get("bbox") or [0, 0, 0, 0]
                x0, y0, x1, y1 = (
                    float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
                )
                if x1 - x0 < 1.0 or y1 - y0 < 1.0:
                    continue  # degenerate box — nothing to redact/reinsert
                units.append(
                    {
                        "page": pidx,
                        "bbox": (x0, y0, x1, y1),
                        "text": text,
                        "size": size if size > 0 else 11.0,
                        "color": color_hex,
                        "serif": _font_is_serif(font_name),
                        "mono": _font_is_mono(font_name),
                        "bold": bold,
                    }
                )
    return units


def _marker_is_bullet(marker: str) -> bool:
    """True when a preserved unit marker is a BULLET glyph (•, ‣, -, –, *, …),
    as opposed to a numbered/alpha/roman marker. The marker still carries its
    trailing whitespace; we test its first non-space char."""
    m = (marker or "").strip()
    return bool(m) and m[0] in _BULLET_MARKER_CHARS


def _classify_units(units: list[dict]) -> None:
    """Assign a structural `kind` ∈ title|h1|h2|p|li to EVERY unit IN PLACE,
    reusing the font signals (`size`, `bold`, page, marker) `_extract_units`
    already captured — no new model / heavy work. Mirrors translate_doc.py's
    PDF block classification (and calls its shared `_classify_line_kind`) so the
    live preview matches the structured-doc view.

    Computes the document BODY size (mode of per-line sizes, rounded to 1dp like
    the doc extractor) and the page-1 TITLE size (largest size on page 0), then
    classifies each unit. The leading marker is split ONCE here and stored on the
    unit (`marker`/`body`) so the translate step reuses it instead of re-splitting.
    A bullet-glyph marker forces `li`; a numeric marker keeps the number in the
    text so the numbered-heading rule can still fire. The single largest page-1
    line is the only `title` (latched)."""
    if not units:
        return

    # Split the leading marker off every unit once (bullet/number/alpha/roman),
    # and pre-compute whether it's a bullet glyph. `body` is the text without the
    # marker (used by the translate step); for classification we keep the number
    # in-line (pass the full text) so `_NUM_HEADING_RE` still matches.
    for u in units:
        marker, body = _split_marker(u["text"])
        u["marker"] = marker
        u["body"] = body
        u["_is_bullet"] = _marker_is_bullet(marker)

    # BODY size = most common rounded line size (mode, not mean — a few huge
    # headings can't drag the baseline up). Matches _extract_pdf_blocks.
    size_counts = Counter(
        round(float(u["size"]), 1) for u in units if float(u["size"]) > 0
    )
    body_size = size_counts.most_common(1)[0][0] if size_counts else 12.0
    # Largest size anywhere on page 1 (page index 0) → candidate title size.
    p1_sizes = [
        round(float(u["size"]), 1)
        for u in units
        if u["page"] == 0 and float(u["size"]) > 0
    ]
    title_size = max(p1_sizes) if p1_sizes else body_size

    title_used = False
    for u in units:
        is_bullet = u["_is_bullet"]
        # Bullet → classify on the body (glyph stripped); otherwise classify the
        # full text so a numeric marker can still register as a numbered heading.
        ctext = u["body"] if is_bullet else u["text"]
        kind = _classify_line_kind(
            ctext,
            round(float(u["size"]), 1),
            bool(u["bold"]),
            page=u["page"],
            body_size=body_size,
            title_size=title_size,
            title_used=title_used,
            is_bullet=is_bullet,
        )
        if kind == "title":
            title_used = True  # single title per document
        u["kind"] = kind


def _unit_html_css(unit: dict, translated: str) -> tuple[str, str]:
    """Build the (html, css) pair for one unit's translated text so
    insert_htmlbox renders it matching the original's color / weight / family,
    starting at the original size and shrinking to fit. Left-aligned,
    top-anchored. The leading marker (if any) is rendered verbatim before the
    translation."""
    marker = unit.get("marker", "")
    body = _html_escape(translated)
    inner = (_html_escape(marker) + body) if marker else body

    if unit["mono"]:
        family = "monospace"
    elif unit["serif"]:
        family = "serif"
    else:
        family = "sans-serif"
    weight = "bold" if unit["bold"] else "normal"
    # px == pt in PyMuPDF's htmlbox CSS box (72 dpi user space). Start at the
    # original size; insert_htmlbox's scale_low shrinks it to fit if needed.
    size_pt = max(4.0, float(unit["size"]))
    color = unit["color"]

    # line-height:1.0 keeps multi-line wraps tight to the original leading;
    # margin/padding:0 so the text starts hard at the box's top-left.
    css = (
        "* { margin:0; padding:0; }"
        f"body {{ font-family:{family}; font-size:{size_pt:.2f}px; "
        f"font-weight:{weight}; color:{color}; line-height:1.05; "
        "text-align:left; }"
    )
    html = f"<div>{inner}</div>"
    return html, css


def _redact_and_reinsert(doc, units: list[dict]) -> None:
    """ERASE every unit's original text in place, then REINSERT its translated
    text into the same bbox (wrapping + auto-shrinking to fit).

    Redaction is grouped per page: add ALL of a page's redaction annots, then
    apply_redactions ONCE with images + line-art PRESERVED (so the page's
    pictures and vector graphics survive — only the text glyphs under the
    annots are removed), and only THEN insert the translated boxes (insertion
    must come after apply_redactions, which would otherwise wipe freshly
    inserted text)."""
    import fitz  # type: ignore  # PyMuPDF — pinned in [ml]

    # Group unit indices by page so we redact+reinsert one page at a time.
    by_page: dict[int, list[dict]] = {}
    for u in units:
        by_page.setdefault(u["page"], []).append(u)

    for pidx, page_units in by_page.items():
        page = doc[pidx]

        # 1) Mark every unit's box for redaction. fill=None + cross_out=False
        #    => no fill is painted and no strike-through is drawn; we only want
        #    the glyphs gone, leaving the original background/graphics intact.
        for u in page_units:
            rect = fitz.Rect(*u["bbox"])
            page.add_redact_annot(rect, fill=None, cross_out=False)

        # 2) Remove the text under the annots while KEEPING images + vector
        #    graphics. Defaults would blank images (PIXELS) and drop covered
        #    line-art; force NONE for both so the exact-copy graphics survive.
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )

        # 3) Reinsert each translation into its (now-cleared) box. insert_htmlbox
        #    wraps to the rect width and, via scale_low<1, shrinks the text down
        #    until it fits the rect height — so a longer translation never
        #    overflows. We give a touch of bottom headroom by not padding the
        #    rect (the original line bbox is already glyph-tight).
        for u in page_units:
            translated = u.get("translated", "")
            if not translated.strip() and not u.get("marker"):
                continue
            rect = fitz.Rect(*u["bbox"])
            html, css = _unit_html_css(u, translated)
            try:
                # scale_low=0 lets htmlbox scale all the way down if it must to
                # fit; returns (spare_height, scale). A negative spare or
                # scale<1 means it shrank — already fitted, nothing more to do.
                page.insert_htmlbox(rect, html, css=css, scale_low=0)
            except Exception:
                # Never let one box kill the whole document; the worst case is
                # that box stays empty (text already redacted away).
                logger.exception(
                    "translate-pdf: insert_htmlbox failed on page %s", pidx
                )


# ---------------------------------------------------------------------------
# Embedded-image (figure / screenshot) translation — reuse the resident
# in-image pipeline to translate printed text INSIDE a page's raster images.
#
# After the text path has redacted+reinserted a page's TEXT, the page's
# embedded figures still carry their original-language text (it's pixels, not a
# text layer). We enumerate those rasters, and for each one big enough to
# plausibly hold readable text we run the EXACT same pipeline a standalone image
# uses — Florence OCR (single orientation, bounded decode) → translate every
# region to the target → cv2 inpaint the originals away → render the
# translations back in place → PNG — then cover the original placement and
# stamp the translated PNG over the SAME rect. All in-process against the
# already-resident Florence + NLLB/MADLAD models (no new model, no subprocess).
# Strictly bounded + non-fatal per image (see the constants above).
# ---------------------------------------------------------------------------
def _img_item_xref(img_item) -> int:
    """The xref (int) of a `page.get_images(full=True)` tuple. The xref is the
    first element; be defensive about the tuple shape across PyMuPDF versions."""
    try:
        return int(img_item[0])
    except Exception:
        return 0


def _rect_is_big_enough(rect) -> bool:
    """True when a placement rect is large enough on the page to plausibly hold
    readable text (both sides clear `_MIN_EMBEDDED_IMAGE_PX`, in PDF points ≈ px
    at 72 dpi). Filters tiny icons / rule lines / bullets / decorative marks even
    when their SOURCE bitmap is large (a big logo scaled down to 18px on the page
    isn't worth an OCR+inpaint pass)."""
    try:
        return (
            (rect.x1 - rect.x0) >= _MIN_EMBEDDED_IMAGE_PX
            and (rect.y1 - rect.y0) >= _MIN_EMBEDDED_IMAGE_PX
        )
    except Exception:
        return False


def _translate_embedded_image_sync(img_bytes: bytes, target: str) -> Optional[bytes]:
    """Blocking: run the RESIDENT in-image translate pipeline on one embedded
    image's raw bytes and return translated PNG bytes, or None when there's
    NOTHING to change (no text detected) or anything fails.

    REUSES `translate_image.py` verbatim — the SAME Florence OCR (single
    orientation, bounded decode), NLLB/MADLAD translate-to-target, cv2 inpaint
    and PIL render a standalone /translate-image call uses — so a figure's text
    is translated identically to a standalone image. Runs the OCR stage itself
    here (the async wrapper applies the wall-clock deadline around the whole
    call). Returns None (caller leaves the original image untouched) when:
      * OCR finds no text regions (a photo / chart with no labels), or
      * the translator can't load / any stage raises (best-effort — never fatal).
    Mirrors `_run_pipeline` in translate_image.py but without its HTTP shell."""
    try:
        # OCR: decode bytes → orientation → regions. Single-orientation +
        # bounded decode (the streaming default) so one figure can't spin.
        img, best_k, best_img, regions = _ti_ocr_stage(
            img_bytes, four_way=_FIGURE_FOUR_WAY
        )
    except Exception:
        logger.exception("translate-pdf: embedded-image OCR failed; skipping")
        return None

    if not regions:
        # No readable text in this figure → leave it exactly as-is.
        return None

    try:
        texts = [r["text"] for r in regions]
        # Detect source + load NLLB ONCE for this image (cached model — the same
        # load every translate path shares). Raises RuntimeError if NLLB absent.
        model, tokenizer, device, gen_kwargs, _src, _tgt = _ti_prepare(texts, target)
        translations = [
            _ti_translate_region(model, tokenizer, device, t, gen_kwargs)
            for t in texts
        ]
        # Inpaint the originals away, render the translations into their boxes,
        # rotate back to the user's orientation, encode PNG. Same compose the
        # standalone image route uses, so the figure reads identically.
        return _ti_compose_png(best_img, best_k, regions, translations)
    except Exception:
        # RuntimeError (NLLB unavailable) or any render error → skip this image.
        logger.exception(
            "translate-pdf: embedded-image translate/render failed; skipping"
        )
        return None


def _collect_embedded_image_targets(doc) -> list[dict]:
    """Enumerate per-page embedded raster images that are worth translating, in
    document order, capped at `_MAX_EMBEDDED_IMAGES`.

    For every page we read `page.get_images(full=True)` and, per image xref,
    `page.get_image_rects(xref)` for its placement rect(s). An image is a
    candidate only when at least one placement rect is big enough to plausibly
    hold readable text (`_rect_is_big_enough`) — this skips icons / bullets /
    rule lines. We DEDUPE by (page, xref) so an image referenced once per page is
    handled once (its biggest placement rect is used for re-insertion). The
    actual bytes are extracted later (in the worker) so this stays cheap.

    Returns a list of {"page": int, "xref": int, "rect": fitz.Rect} dicts; the
    rect is the LARGEST qualifying placement on that page (where we stamp the
    translated PNG). Pure enumeration — no OCR, no model, no decode."""
    targets: list[dict] = []
    seen: set[tuple[int, int]] = set()
    n_pages = min(len(doc), _MAX_PAGES)
    for pidx in range(n_pages):
        if len(targets) >= _MAX_EMBEDDED_IMAGES:
            break
        page = doc[pidx]
        try:
            images = page.get_images(full=True)
        except Exception:
            logger.debug(
                "translate-pdf: get_images failed on page %s", pidx, exc_info=True
            )
            continue
        for img_item in images:
            if len(targets) >= _MAX_EMBEDDED_IMAGES:
                break
            xref = _img_item_xref(img_item)
            if xref <= 0:
                continue
            key = (pidx, xref)
            if key in seen:
                continue
            seen.add(key)
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                logger.debug(
                    "translate-pdf: get_image_rects failed (page %s xref %s)",
                    pidx, xref, exc_info=True,
                )
                continue
            # Keep the LARGEST placement that's big enough; skip if none qualify.
            big = [r for r in (rects or []) if _rect_is_big_enough(r)]
            if not big:
                continue
            best_rect = max(big, key=lambda r: (r.x1 - r.x0) * (r.y1 - r.y0))
            targets.append({"page": pidx, "xref": xref, "rect": best_rect})
    return targets


def _extract_image_bytes_sync(doc, xref: int) -> Optional[bytes]:
    """Extract one embedded image's raw encoded bytes by xref via
    `doc.extract_image(xref)`. Returns the image bytes, or None when extraction
    fails or the payload is implausibly large for a figure
    (`_MAX_EMBEDDED_IMAGE_BYTES` — a full-page scan is better served by the
    standalone in-image route). Blocking; called from the worker thread."""
    try:
        info = doc.extract_image(xref)
    except Exception:
        logger.debug(
            "translate-pdf: extract_image failed for xref %s", xref, exc_info=True
        )
        return None
    if not info:
        return None
    data = info.get("image")
    if not data:
        return None
    if len(data) > _MAX_EMBEDDED_IMAGE_BYTES:
        logger.info(
            "translate-pdf: embedded image xref %s too large (%d bytes); skipping",
            xref, len(data),
        )
        return None
    return data


def _reinsert_translated_image(doc, pidx: int, rect, png_bytes: bytes) -> None:
    """Stamp the translated PNG over the SAME placement rect on the page so the
    translated figure lands exactly where the original sat.

    We first paint an OPAQUE WHITE rectangle over the original placement so the
    underlying original-language figure is fully covered (insert_image draws on
    top, but a PNG with any transparency or a slightly different aspect ratio
    could otherwise let the original show through), then `page.insert_image`
    with `keep_proportion=False` so the translated image fills the exact rect the
    original occupied (the OCR pipeline preserved the figure's own aspect ratio
    internally; we want pixel-for-pixel placement here). Blocking; best-effort —
    raises are caught by the caller per-image."""
    import fitz  # type: ignore  # PyMuPDF

    page = doc[pidx]
    # Cover the original figure with white so nothing of the source shows through
    # if the re-inserted PNG doesn't perfectly overpaint every pixel.
    try:
        page.draw_rect(
            fitz.Rect(rect),
            color=None,
            fill=(1, 1, 1),
            overlay=True,
        )
    except Exception:
        logger.debug(
            "translate-pdf: cover-rect draw failed (page %s)", pidx, exc_info=True
        )
    # Stamp the translated PNG into the exact original rect.
    page.insert_image(
        fitz.Rect(rect),
        stream=png_bytes,
        keep_proportion=False,
        overlay=True,
    )


async def _translate_embedded_images(doc, target: str) -> int:
    """Translate the printed text inside the document's embedded figures IN
    PLACE, reusing the resident in-image pipeline. Returns the count of images
    actually replaced (those where OCR found text and translation+render
    succeeded).

    STRICTLY BOUNDED + NON-FATAL (the contract for this extra, after-text work):
      * only up to `_MAX_EMBEDDED_IMAGES` images are processed per document
        (enumeration is already capped in `_collect_embedded_image_targets`);
      * only images whose placement is big enough to hold readable text;
      * each image's OCR+translate+render runs in a worker thread via
        asyncio.to_thread, wrapped in asyncio.wait_for(`_TI_OCR_DEADLINE_S`) so a
        pathological figure fails fast instead of hanging the document stream;
      * EACH image is wrapped in its own try/except — any failure (extract error,
        decode error, OCR miss, NLLB unavailable, render error, timeout) SKIPS
        that one image and is never fatal; the already-translated text stands.
    Re-insertion (cover + stamp) happens on the live `doc` so the final
    `pdf_b64` (and the snapshots, which re-render from the original bytes) pick
    up the translated figures automatically.

    This runs AFTER the text redact+reinsert so the text path (the primary win)
    is never slowed by the figure work — figures are purely additive."""
    targets = await asyncio.to_thread(_collect_embedded_image_targets, doc)
    if not targets:
        # OBSERVABILITY: the silent case the user hit — the document has no
        # raster image big enough to hold readable text. Either the "figure" is
        # vector/real-PDF-text (so the NORMAL text path owns it, not this stage),
        # or every embedded raster is below _MIN_EMBEDDED_IMAGE_PX. Logged so a
        # missing figure translation is diagnosable instead of a silent no-op.
        logger.info(
            "translate-pdf: no embedded raster figures qualified for in-image "
            "translation (none ≥ %dpx). If a figure stayed in the source "
            "language, its text is likely real PDF text (text path) or a vector "
            "drawing, not a raster image.",
            _MIN_EMBEDDED_IMAGE_PX,
        )
        return 0

    # Sub-project A: process figures CONCURRENTLY (bounded) on the 12 GB GPU's
    # headroom. PyMuPDF docs are NOT thread-safe, so we (1) extract all figure
    # bytes serially, (2) OCR+translate+render concurrently (pure on bytes,
    # bounded by a semaphore), then (3) reinsert into the doc serially. Each
    # figure keeps its own per-image deadline + try/except, and the overall
    # wall-clock budget still bounds the whole stage.
    deadline = time.monotonic() + _FIGURES_TOTAL_BUDGET_S

    # (1) Extract bytes serially from the shared fitz doc.
    extracted: list[tuple] = []
    for t in targets:
        try:
            b = await asyncio.to_thread(_extract_image_bytes_sync, doc, t["xref"])
            if b:
                extracted.append((t["page"], t["rect"], b))
        except Exception:
            logger.exception(
                "translate-pdf: embedded image extract failed (page %s xref %s); "
                "skipping", t.get("page"), t.get("xref"),
            )
    attempted = len(extracted)

    # (2) OCR + translate + render concurrently, bounded by the semaphore.
    sem = asyncio.Semaphore(max(1, _FIGURE_CONCURRENCY))

    async def _render_one(pidx, rect, img_bytes):
        if time.monotonic() >= deadline:
            return None
        async with sem:
            if time.monotonic() >= deadline:
                return None
            try:
                png = await asyncio.wait_for(
                    asyncio.to_thread(
                        _translate_embedded_image_sync, img_bytes, target
                    ),
                    timeout=_TI_OCR_DEADLINE_S,
                )
                return (pidx, rect, png) if png else None
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "translate-pdf: embedded image (page %s) exceeded %.0fs; "
                    "skipping", pidx, _TI_OCR_DEADLINE_S,
                )
                return None
            except Exception:
                # One figure failing must NEVER break the translate-pdf stream.
                logger.exception(
                    "translate-pdf: embedded image (page %s) OCR/translate "
                    "failed; skipping", pidx,
                )
                return None

    rendered = await asyncio.gather(
        *(_render_one(p, r, b) for (p, r, b) in extracted)
    )

    # (3) Reinsert results into the doc serially (fitz is not thread-safe).
    replaced = 0
    for item in rendered:
        if item is None:
            continue
        pidx, rect, png = item
        try:
            await asyncio.to_thread(_reinsert_translated_image, doc, pidx, rect, png)
            replaced += 1
        except Exception:
            logger.exception(
                "translate-pdf: embedded image reinsert failed; skipping"
            )

    if time.monotonic() >= deadline and replaced < attempted:
        logger.info(
            "translate-pdf: embedded-image budget (%.0fs) reached; translated "
            "%d/%d figure(s), remainder left untranslated.",
            _FIGURES_TOTAL_BUDGET_S, replaced, len(targets),
        )
    if replaced:
        logger.info(
            "translate-pdf: translated %d/%d embedded image(s)",
            replaced, len(targets),
        )
    else:
        # OBSERVABILITY: figures WERE found and worth translating, but every one
        # produced nothing — OCR detected no text, the figure had only graphics,
        # or each hit the per-image deadline. This is the 8GB Florence ceiling
        # (single-orientation, bounded decode) that the 5070 lifts. Logged so the
        # user's "figure stayed English" maps to a concrete reason, not silence.
        logger.info(
            "translate-pdf: found %d embedded figure(s) but translated 0 — OCR "
            "read no text in any (printed-text detection miss / graphics-only / "
            "per-image deadline). This is the in-image OCR limit queued for the "
            "12GB machine.",
            len(targets),
        )
    return replaced


# ---------------------------------------------------------------------------
# Live page-image snapshots (Change 1) — render the REAL PDF page with the
# blocks translated SO FAR, rasterized to PNG, so the FE shows the actual
# document's exact layout progressively turning into the target language.
# ---------------------------------------------------------------------------
def _snapshot_checkpoints(n: int, points: int = _SNAPSHOT_POINTS) -> set[int]:
    """The set of 1-based block counts at which we emit a page-image snapshot.

    Spreads `points` checkpoints EVENLY across the document (every ~100/points %;
    ≈11% apart for the default 9) and ALWAYS includes the final block `n` (the
    fully-translated state) so the page visibly fills in. This is SEPARATE from
    the immediate block-0 snapshot of the original page that `_translate_pdf_gen`
    streams before the loop. For a tiny document with fewer blocks than points,
    every block is a checkpoint."""
    if n <= 0:
        return set()
    if n <= points:
        return set(range(1, n + 1))
    cuts = {
        max(1, round(n * (j + 1) / points)) for j in range(points)
    }
    cuts.add(n)  # always snapshot the final, fully-translated state
    return cuts


def _pages_for_snapshot(done_units: list[dict]) -> list[int]:
    """Choose which page indices to rasterize for a snapshot, capped at
    `_SNAPSHOT_MAX_PAGES`. Prioritizes the pages that have received the MOST
    translated blocks so far (those are the ones visibly changing), then returns
    them in ascending page order so the FE sees pages in document order."""
    counts: Counter = Counter(u["page"] for u in done_units)
    if not counts:
        return []
    # Most-populated pages first (cap), then sort ascending for display.
    top = [pg for pg, _ in counts.most_common(_SNAPSHOT_MAX_PAGES)]
    return sorted(top)


def _render_original_pages_sync(
    raw: bytes, n_pages: int = 1, dpi: int = _SNAPSHOT_DPI
) -> list[dict]:
    """Rasterize the FIRST `n_pages` of the ORIGINAL, UNMODIFIED PDF (zero
    translated blocks) and return per-page snapshot dicts in the SAME shape as
    `_render_snapshot_sync`:

        [{"page": <0-based>, "pages": <total>, "page_png_b64": "<...>"}, …]

    This is the block-0 snapshot streamed BEFORE the translation loop so the FE
    instantly shows the REAL document page in its EXACT format (layout, fonts,
    bullets, images) — never the HTML reflow — while the engine warms up. NO
    redaction, NO reinsertion: the page is the untouched original. Blocking —
    call via asyncio.to_thread. Returns [] on any failure (best-effort: a render
    miss simply means the FE keeps showing whatever it had until the first real
    checkpoint lands)."""
    import fitz  # type: ignore  # PyMuPDF

    snap_doc = None
    try:
        snap_doc = fitz.open(stream=raw, filetype="pdf")
        total_pages = len(snap_doc)
        out: list[dict] = []
        for pidx in range(min(max(1, n_pages), total_pages)):
            try:
                pix = snap_doc[pidx].get_pixmap(dpi=dpi)
                png = pix.tobytes("png")
            except Exception:
                logger.exception(
                    "translate-pdf: original-page rasterize failed on page %s",
                    pidx,
                )
                continue
            out.append(
                {
                    "page": pidx,
                    "pages": total_pages,
                    "page_png_b64": base64.b64encode(png).decode("ascii"),
                }
            )
        return out
    except Exception:
        logger.exception("translate-pdf: original-page render failed")
        return []
    finally:
        if snap_doc is not None:
            try:
                snap_doc.close()
            except Exception:
                pass


def _render_snapshot_sync(
    raw: bytes, done_units: list[dict], dpi: int = _SNAPSHOT_DPI
) -> list[dict]:
    """Build a FRESH working copy of the PDF from the original `raw` bytes,
    apply redaction + reinsertion for `done_units` (the blocks translated SO
    FAR — using their ALREADY-translated text; we never re-translate), rasterize
    the affected pages at `dpi`, and return a list of per-page snapshot dicts:

        [{"page": <0-based>, "pages": <total>, "page_png_b64": "<...>"}, …]

    A fresh copy is essential because redaction is DESTRUCTIVE (it permanently
    removes the original glyphs); we must not mutate the live doc that the final
    full-resolution PDF is serialized from. The `done_units` dicts carry only a
    page index + bbox + the translated text/marker/style (no reference to any
    doc object), so the SAME unit list applies cleanly to a fresh copy whose
    page geometry is identical to the original. Blocking — call via
    asyncio.to_thread. Returns [] on any failure (a snapshot is best-effort and
    must never break the translation stream)."""
    import fitz  # type: ignore  # PyMuPDF

    if not done_units:
        return []
    snap_doc = None
    try:
        snap_doc = fitz.open(stream=raw, filetype="pdf")
        total_pages = len(snap_doc)
        # Only redact+reinsert the pages we're about to rasterize (cheaper than
        # processing the whole document every checkpoint), but apply ALL done
        # units on those pages so the page reads correctly.
        want_pages = set(_pages_for_snapshot(done_units))
        if not want_pages:
            return []
        page_units = [u for u in done_units if u["page"] in want_pages]
        # Reuse the EXACT redact+reinsert pipeline the final PDF uses, so a
        # snapshot page is pixel-faithful to what the finished document will be.
        _redact_and_reinsert(snap_doc, page_units)

        out: list[dict] = []
        for pidx in sorted(want_pages):
            if pidx < 0 or pidx >= total_pages:
                continue
            try:
                pix = snap_doc[pidx].get_pixmap(dpi=dpi)
                png = pix.tobytes("png")
            except Exception:
                logger.exception(
                    "translate-pdf: snapshot rasterize failed on page %s", pidx
                )
                continue
            out.append(
                {
                    "page": pidx,
                    "pages": total_pages,
                    "page_png_b64": base64.b64encode(png).decode("ascii"),
                }
            )
        return out
    except Exception:
        logger.exception("translate-pdf: snapshot render failed")
        return []
    finally:
        if snap_doc is not None:
            try:
                snap_doc.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Translation — detect source ONCE, resolve forced-BOS ONCE, translate each
# unit with the cached NLLB model (per-chunk generate reused verbatim).
# ---------------------------------------------------------------------------
def _engine_target_name(target: str) -> Optional[str]:
    """Human language name for the LOW-RESOURCE gap-languages (Tongan, Fijian,
    Samoan, Tahitian) the Apache-2.0 engine routes to its LLM/Opus tier, or None
    for everything else (the caller then uses the FLORES display name).

    The picker sends these as a bare ISO code ("to"/"fj"/"sm"/"ty") that
    `_to_flores` collapses to "eng_Latn", so `_flores_name` would mislabel the
    finish line as "English". The engine keeps the real names in
    `_LLM_ROUTE_NAMES` / `_OPUS_NAMES`; we look both up case-insensitively
    (full code then base subtag), matching the engine's own resolution. Pure /
    import-safe — reads only the engine's static tables, never loads a model."""
    try:
        low = translate_engine._norm_lookup(target)
    except Exception:
        return None
    base = low.split("_", 1)[0]
    for table in (
        getattr(translate_engine, "_LLM_ROUTE_NAMES", {}),
        getattr(translate_engine, "_OPUS_NAMES", {}),
    ):
        name = table.get(low) or table.get(base)
        if name:
            return name
    return None


def _prepare_nllb(units: list[dict], target: str):
    """Load NLLB (cached), detect the source language over a sample of the
    unit text, set src_lang + resolve the forced-BOS token. Returns
    (model, tokenizer, device, gen_kwargs, src_name, tgt_name). Blocking —
    call via asyncio.to_thread. Raises RuntimeError when NLLB can't load."""
    model, tokenizer, device = _get_nllb()  # cached; RuntimeError if absent

    sample = " ".join(u["text"] for u in units)[:2000]
    src_flores = _detect_src_flores(sample) if sample.strip() else "eng_Latn"
    # FLORES form of the target — used ONLY for the NLLB fallback's forced-BOS
    # token and for the human display name. It is LOSSY for the low-resource
    # gap-languages: `_to_flores("to")` has no Tongan entry and collapses to
    # "eng_Latn", which is why it must NOT be what we hand the Apache-2.0
    # engine (see `_nt_target` below).
    tgt_flores = _to_flores(target)
    src_name = _flores_name(src_flores)

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
    # Route through the new Apache-2.0 engine first (NLLB stays the fallback) —
    # the shared per-chunk fn reads these private keys and pops them before any
    # NLLB generate() call.
    #
    # IMPORTANT (low-resource routing): hand the engine the ORIGINAL caller
    # `target`, NOT the `_to_flores`-normalized code. The picker sends Tongan as
    # the bare ISO code "to" (Fijian "fj", Samoan "sm", Tahitian "ty"); these are
    # NOT in `_ISO_TO_FLORES`, so `_to_flores` collapses every one of them to
    # "eng_Latn" — which the engine then routes to MADLAD English and echoes back
    # garbage (the user's "tista"/date-noise). The engine's own resolver already
    # accepts ISO codes, FLORES codes, and explicit MADLAD/Opus tokens, and its
    # LOW-RESOURCE LLM tier (`_llm_target_name` → Qwen) only fires when it sees
    # the real "to"/"ton"/… code. Passing the raw target therefore sends Tongan &
    # the other Pacific gaps through Qwen while routing every common language
    # (es/spa_Latn, fr, de, zh, ru, … — all verified identical to the FLORES
    # routing) exactly as before. This fixes BOTH the batched path
    # (`translate_batch`) AND the per-unit fallback (`_translate_one_chunk_sync`
    # → `translate_text`) in one place, since both read `_nt_target` directly.
    gen_kwargs["_nt_target"] = (target or "").strip() or tgt_flores
    gen_kwargs["_nt_source"] = src_flores

    # Human display name for the finish line. `_flores_name(tgt_flores)` is wrong
    # for the low-resource gaps (it would say "English" for collapsed "to"), so
    # prefer the engine's name for those (`_OPUS_NAMES`/LLM tier) and fall back to
    # the FLORES name for everything else.
    tgt_name = _engine_target_name(target) or _flores_name(tgt_flores)

    return model, tokenizer, device, gen_kwargs, src_name, tgt_name


def _translate_unit_sync(model, tokenizer, device, unit: dict, gen_kwargs: dict) -> str:
    """Translate ONE unit's text, preserving a leading bullet/number marker.
    Stores unit['marker'] (verbatim) and returns the translated body. Runs in
    a thread. Reuses translate_stream.py's per-chunk generate so output matches
    every other translate path. On failure the original text is kept (we never
    drop a unit)."""
    # The marker / body were split once in _classify_units; reuse them (fall
    # back to splitting here if classification was skipped for any reason).
    if "body" in unit and "marker" in unit:
        marker, body = unit["marker"], unit["body"]
    else:
        marker, body = _split_marker(unit["text"])
        unit["marker"] = marker
    if not body.strip():
        return ""
    try:
        out = _translate_one_chunk_sync(model, tokenizer, device, body, gen_kwargs)
    except Exception:
        logger.exception("translate-pdf: unit translate failed; keeping original")
        out = body
    return out or body


def _unit_body(unit: dict) -> str:
    """Return the unit's translatable body (text after a leading bullet/number
    marker), splitting + caching the marker on the unit if it wasn't done in
    `_classify_units`. The marker itself is preserved verbatim and re-prepended
    at render time, so only the body is ever sent to the translator."""
    if "body" in unit and "marker" in unit:
        return unit["body"]
    marker, body = _split_marker(unit["text"])
    unit["marker"] = marker
    unit["body"] = body
    return body


def _llm_target_name_for(target: str) -> Optional[str]:
    """Return the English LANGUAGE NAME when `target` is one of the engine's
    LOW-RESOURCE LLM-tier gap languages (Tongan, Fijian, Samoan, Tahitian — the
    `_LLM_ROUTE_NAMES` set), else None. Thin pass-through to
    `translate_engine._llm_target_name` so this module can decide, BEFORE any
    NLLB-direct generate(), whether a target MUST stay on the Qwen route.

    WHY THIS GUARD EXISTS (the real Tongan→English collapse):
    `_to_flores("to")` has no Tongan entry and collapses to "eng_Latn", so the
    NLLB forced-BOS token resolves to ENGLISH (256047). Any code path that falls
    through to `_translate_one_chunk_sync`'s NLLB branch for a low-resource
    target therefore emits the English passthrough ("The quick brown fox…" → the
    same English) — the exact "English → English" bug. For these languages the
    ONLY correct engine is the resident Qwen LLM (`_llm_translate_*`); we must
    never let the NLLB-direct forced-BOS-English path run for them. Pure /
    import-safe (reads the engine's static table; no model load)."""
    try:
        return translate_engine._llm_target_name(target)
    except Exception:
        return None


def _llm_retry_batch(target: str, bodies: list[str]) -> Optional[list[str]]:
    """Qwen-ONLY retry for a LOW-RESOURCE target, returning a list 1:1 with
    `bodies` (blank body → "" at its index) or None when the LLM is unavailable.

    Used as the fallback for `_LLM_ROUTE_NAMES` languages INSTEAD of the
    NLLB-direct single-chunk path: `translate_engine._llm_translate_batch` drives
    the SAME resident Qwen model `translate_batch` would have used, so a blank or
    failed `translate_batch` is re-tried on Qwen (never on NLLB, which would force
    English for the collapsed FLORES target). Returns None only when Qwen itself
    can't be loaded — the caller then keeps the ORIGINAL source text for those
    units rather than emitting NLLB English."""
    name = _llm_target_name_for(target)
    if name is None:
        return None
    try:
        return translate_engine._llm_translate_batch(name, bodies)
    except Exception:
        logger.exception("translate-pdf: low-resource Qwen retry failed")
        return None


def _translate_units_batched(
    units: list[dict],
    gen_kwargs: dict,
    model,
    tokenizer,
    device,
) -> list[str]:
    """Translate ONE batch of units' bodies in a single padded
    `translate_engine.translate_batch` call, returning a list 1:1 with `units`
    (index k of the return is the translation of units[k]).

    Strict 1:1 alignment: we send `[_unit_body(u) for u in units]` (a per-unit
    LIST, never a newline-join the model could merge/split) and get back a list
    of the SAME length and order; an empty body maps to "" at its index without
    a model call (translate_batch handles that internally). Routes EXACTLY like
    the single-block path — `translate_batch` shares `_resolve_route` with
    `translate_text`, so an exotic Opus target (Tongan `>>ton<<`, …) batches
    through opus-mt-en-mul the same way a single call would.

    Robustness mirrors `_translate_unit_sync` (never drop a unit):
      * the FLORES target/source the caller already resolved are read from the
        smuggled `_nt_target` / `_nt_source` gen_kwargs keys and passed to
        translate_batch as `target=` / `source=`;
      * if the WHOLE batch can't run (engine unavailable → RuntimeError, or any
        other error) we fall back to translating each unit individually via the
        existing `_translate_one_chunk_sync` path (which itself falls back from
        the engine to NLLB), so batched failures degrade to the old per-block
        behaviour rather than blanking the batch;
      * for any single item the batch returned blank on a NON-blank body, we
        re-translate just that one via the single-chunk path and finally keep
        the original body — so a unit is never lost.
    Runs in a thread via asyncio.to_thread."""
    bodies = [_unit_body(u) for u in units]
    # Nothing to translate in this batch (all markers-only / blank).
    if not any(b.strip() for b in bodies):
        return ["" for _ in units]

    target = gen_kwargs.get("_nt_target") or ""
    source = gen_kwargs.get("_nt_source")

    try:
        results = translate_engine.translate_batch(
            bodies,
            target=target,
            source=source,
            batch_size=len(bodies),  # the gen-loop already sized this batch
            item_max_tokens=translate_engine._BLOCK_ITEM_MAX_TOKENS,
        )
    except Exception:
        # Engine unavailable (RuntimeError) or batch error.
        #
        # LOW-RESOURCE GUARD (Tongan, …): for a `_LLM_ROUTE_NAMES` target we must
        # NOT drop to the per-unit `_translate_one_chunk_sync` NLLB path — that
        # path's forced-BOS token is ENGLISH (the collapsed FLORES code), so it
        # would emit the English passthrough and produce the "English → English"
        # bug. Re-try the WHOLE batch on the resident Qwen LLM instead; only if
        # Qwen is unavailable do we keep the ORIGINAL source text (never NLLB
        # English). For every OTHER language the existing engine→NLLB per-unit
        # fallback is correct and unchanged.
        logger.exception(
            "translate-pdf: batch translate failed; fallback"
        )
        retry = _llm_retry_batch(target, bodies)
        if retry is not None:
            return [
                (r.strip() if (r and r.strip()) else b)
                for b, r in zip(bodies, retry)
            ]
        if _llm_target_name_for(target) is not None:
            # Low-resource target but Qwen unavailable: keep source rather than
            # forcing English through NLLB.
            return list(bodies)
        return [
            _translate_unit_sync(model, tokenizer, device, u, gen_kwargs)
            for u in units
        ]

    # Repair any per-item miss (blank result for a non-blank body) and never
    # drop a unit. The retry path depends on the target:
    #   * LOW-RESOURCE (Tongan, …): re-try ONLY on the resident Qwen LLM. The
    #     single-chunk NLLB path would force ENGLISH (collapsed FLORES BOS) and
    #     re-introduce the "English → English" bug, so it is forbidden here; if
    #     Qwen still returns blank we keep the ORIGINAL body, never English.
    #   * EVERY OTHER language: the existing single-chunk path (engine→NLLB) is
    #     correct, so the behaviour is unchanged.
    llm_name = _llm_target_name_for(target)
    out: list[str] = []
    for u, body, res in zip(units, bodies, results):
        if not body.strip():
            out.append("")
            continue
        if res and res.strip():
            out.append(res)
            continue
        if llm_name is not None:
            # Low-resource: Qwen-only single-item retry, then keep the original.
            one = ""
            retry = _llm_retry_batch(target, [body])
            if retry:
                one = (retry[0] or "").strip()
            out.append(one if one else body)
            continue
        # Common language: single-chunk retry (engine→NLLB), then original.
        try:
            one = _translate_one_chunk_sync(
                model, tokenizer, device, body, gen_kwargs
            )
        except Exception:
            one = ""
        out.append(one.strip() if (one and one.strip()) else body)
    return out


# ---------------------------------------------------------------------------
# Async NDJSON generator — the streaming body.
# ---------------------------------------------------------------------------
async def _translate_pdf_gen(raw: bytes, target: str):
    """Async NDJSON generator. Opens the PDF, extracts units, translates each
    (emitting a {"i":k,"n":n} progress line as it finishes), redacts the
    originals + reinserts translations, then emits a final
    {"done":true,"pdf_b64":...} line. NEVER raises out — any failure becomes a
    terminal {"error": ...} line so the HTTP stream always terminates instead
    of hanging."""
    import fitz  # type: ignore

    doc = None
    try:
        # 1) Open the PDF bytes. A non-PDF / corrupt stream raises here → error
        #    line (the route already 415s on a non-PDF file type before we get
        #    here, so this is the corrupt-bytes guard).
        try:
            doc = await asyncio.to_thread(
                lambda: fitz.open(stream=raw, filetype="pdf")
            )
        except Exception:
            logger.exception("translate-pdf: could not open PDF bytes")
            yield json.dumps({"error": "Could not open this PDF."}) + "\n"
            return

        # 2) Extract line-level units across all pages, then classify each into
        #    a structural kind (title|h1|h2|p|li) from the size/bold/marker info
        #    we already captured — no model load, pure CPU. The FE uses `kind`
        #    to render a FORMATTED live preview matching the document structure.
        units = await asyncio.to_thread(_extract_units, doc)
        await asyncio.to_thread(_classify_units, units)
        n = len(units)
        if n == 0:
            # No text layer anywhere (scanned/image-only). Return the PDF
            # unchanged so the caller still gets a valid file.
            pdf_bytes = await asyncio.to_thread(
                lambda: doc.tobytes(deflate=True, garbage=3)
            )
            b64 = base64.b64encode(pdf_bytes).decode("ascii")
            tgt_name = _engine_target_name(target) or _flores_name(
                _to_flores(target)
            )
            yield json.dumps(
                {
                    "done": True,
                    "pdf_b64": b64,
                    "source_lang": "",
                    "target_lang": tgt_name,
                }
            ) + "\n"
            return

        # 2c) IMMEDIATE block-0 snapshot (Change 1): BEFORE loading the model or
        #     translating anything, rasterize the FIRST page of the ORIGINAL,
        #     UNMODIFIED document (zero translated blocks) and stream it as an
        #     {"i":0,"n":n,"page":…,"pages":…,"page_png_b64":…} line. This means
        #     the very first thing the FE shows is the REAL document page in its
        #     EXACT format (layout/fonts/bullets/images) — never the HTML reflow
        #     — and it appears INSTANTLY (no wait for the NLLB/MADLAD/Qwen load),
        #     while the per-checkpoint snapshots below then progressively turn the
        #     page into the target language. Best-effort: a render miss yields no
        #     line and the stream proceeds straight to translation.
        first_pages = await asyncio.to_thread(
            _render_original_pages_sync, raw, 1, _SNAPSHOT_DPI
        )
        for pg in first_pages:
            yield json.dumps(
                {
                    "i": 0,
                    "n": n,
                    "page": pg["page"],
                    "pages": pg["pages"],
                    "page_png_b64": pg["page_png_b64"],
                }
            ) + "\n"

        # 3) Load NLLB + resolve languages ONCE (blocking → thread).
        (
            model,
            tokenizer,
            device,
            gen_kwargs,
            src_name,
            tgt_name,
        ) = await asyncio.to_thread(_prepare_nllb, units, target)

        # Snapshot checkpoints (Change 1): the 1-based block counts at which we
        # stream a rasterized image of the REAL page with the blocks done so far
        # already in the target language. ~9 FREQUENT points (every ~11% incl.
        # the final fully-translated state) so the page visibly FILLS IN — on top
        # of the immediate block-0 original-page snapshot streamed above.
        snapshot_at = _snapshot_checkpoints(n, _SNAPSHOT_POINTS)

        # 4) Translate the units in PADDED BATCHES (one generate() per batch
        #    instead of one per block — the big speed win), then flush this
        #    batch's per-block progress lines. Progress jumps by the batch size,
        #    which the FE handles. Strict 1:1 alignment: each batch translates a
        #    LIST of that batch's bodies and gets back a list of the SAME length
        #    and order, so result j maps to batch_units[j] (and thus to its
        #    global unit). The per-block line shape ({"i","n","text","kind"}) is
        #    UNCHANGED — the marker (if any) is prepended so the streamed text
        #    matches what gets inserted into the PDF box. When the cumulative
        #    count k hits a snapshot checkpoint we ALSO render the REAL page(s)
        #    with the blocks translated SO FAR and stream one image line per page
        #    (shape: {"i","n","page","pages","page_png_b64"}) so the FE shows the
        #    document's EXACT layout/fonts/bullets turning into the target lang.
        for bstart in range(0, n, _PDF_BATCH_SIZE):
            batch_units = units[bstart:bstart + _PDF_BATCH_SIZE]
            translations = await asyncio.to_thread(
                _translate_units_batched,
                batch_units,
                gen_kwargs,
                model,
                tokenizer,
                device,
            )
            for j, unit in enumerate(batch_units):
                unit["translated"] = translations[j]
                k = bstart + j + 1  # 1-based count across the whole document
                block_text = (
                    (unit.get("marker", "") or "") + (unit["translated"] or "")
                )
                # `kind` (title|h1|h2|p|li) was computed in _classify_units from
                # the font signals; default to "p" if classification was skipped.
                yield json.dumps(
                    {
                        "i": k,
                        "n": n,
                        "text": block_text,
                        "kind": unit.get("kind", "p"),
                    }
                ) + "\n"

                # Page-image snapshot at this checkpoint: re-render the REAL page
                # on a FRESH doc copy with units[:k] (already-translated text —
                # NO re-translation) redacted+reinserted, rasterize at modest
                # DPI, and stream one line per rendered page. Best-effort: a
                # render failure returns [] and the stream continues. The DPI +
                # the ≤5 checkpoints + ≤6 pages/snapshot keep this cheap.
                if k in snapshot_at:
                    done_units = units[:k]
                    pages = await asyncio.to_thread(
                        _render_snapshot_sync, raw, done_units, _SNAPSHOT_DPI
                    )
                    for pg in pages:
                        yield json.dumps(
                            {
                                "i": k,
                                "n": n,
                                "page": pg["page"],
                                "pages": pg["pages"],
                                "page_png_b64": pg["page_png_b64"],
                            }
                        ) + "\n"

        # 5) Redact originals + reinsert translations (blocking → thread).
        await asyncio.to_thread(_redact_and_reinsert, doc, units)

        # 5b) Translate the printed text INSIDE the page's embedded figures /
        #     screenshots IN PLACE, reusing the resident in-image pipeline
        #     (Florence OCR → NLLB/MADLAD translate → inpaint → render). This is
        #     EXTRA, after-text work: it's strictly bounded (≤ _MAX_EMBEDDED_IMAGES
        #     images, each under the in-image OCR deadline, run in worker threads)
        #     and NON-FATAL per image — any figure that fails/has no text is
        #     skipped, never breaking the stream. The translated PNGs are stamped
        #     onto the live `doc`, so the final pdf_b64 below includes them
        #     automatically. Wrapped defensively so even an unexpected error in the
        #     enumeration can't turn the (already-successful) text translation into
        #     a failed stream.
        try:
            await _translate_embedded_images(doc, target)
        except Exception:
            logger.exception(
                "translate-pdf: embedded-image stage failed; "
                "returning text-only translation"
            )

        # 6) Serialize the translated PDF → base64 → final line.
        pdf_bytes = await asyncio.to_thread(
            lambda: doc.tobytes(deflate=True, garbage=3)
        )
        b64 = base64.b64encode(pdf_bytes).decode("ascii")
        yield json.dumps(
            {
                "done": True,
                "pdf_b64": b64,
                "source_lang": src_name,
                "target_lang": tgt_name,
            }
        ) + "\n"
    except RuntimeError as exc:
        # NLLB unavailable (sentencepiece / weights missing). Clean error line.
        logger.info("translate-pdf: NLLB unavailable: %s", exc)
        yield json.dumps(
            {"error": "Translation model is not available in this build."}
        ) + "\n"
    except Exception:
        logger.exception("translate-pdf: generation failed")
        yield json.dumps({"error": "PDF translation failed."}) + "\n"
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


@router.post("/{image_id}/translate-pdf")
async def translate_pdf(
    image_id: UUID,
    body: TranslatePdfRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Translate an owned PDF into an EXACT COPY with only the text changed.

    Loads the PDF bytes, redacts the original text in place, and reinserts the
    NLLB-200 translation into the same boxes — preserving layout, images,
    vector graphics, colors and positions. Owner-scoped (404 for non-owners /
    missing rows) like `/ocr` + `/translate-doc-stream`. 415 {"detail":
    "not_a_pdf"} when the file isn't a PDF. The heavy work (fitz parse +
    NLLB) happens inside the async generator (each step in a thread) so the
    event loop stays responsive; the new PDF is base64'd into the terminal
    NDJSON line. Errors surface as a terminal {"error": ...} line rather than
    an HTTP error so a partial stream never hangs."""
    image = await _load_owned_image(image_id, user, session)

    if not _is_pdf(image):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "not_a_pdf"
        )
    if image.original_blob_key is None:
        # Hybrid retention may have dropped the original; without it there are
        # no bytes to translate.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes unavailable"
        )

    try:
        raw = await asyncio.to_thread(_fetch_original_sync, image)
    except Exception as exc:  # storage miss
        logger.exception("translate-pdf: could not read bytes for %s", image_id)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes unavailable"
        ) from exc

    if len(raw) > _MAX_PDF_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "PDF is too large to translate.",
        )

    return StreamingResponse(
        _translate_pdf_gen(raw, body.target),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
