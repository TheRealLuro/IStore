"""In-place image translation (NEW, unwired) — Google-Lens style.

A single route, authed + owner-scoped (same gate as backend/api/ocr.py's
`/ocr` and `/translate`):

  POST /images/{id}/translate-image
       Body: {"target": "spa_Latn"}
             `target` — a FLORES-200 code (spa_Latn, deu_Latn, …) OR an
                        ISO-639-1 code (es, de, …); empty/unknown → English.

       Response: `image/png` — the SAME image with every detected text
       region's original text ERASED (inpainted away) and the translation
       rendered back in place, fitted to the original box. Two response
       headers carry the languages for the FE to show "English → Spanish":
         X-Source-Lang : human name of the auto-detected source language
                         (e.g. "English"); EMPTY when no text was found.
         X-Target-Lang : human name of the requested target ("Spanish").

       If NO text regions are found we return the ORIGINAL image bytes
       unchanged with X-Source-Lang="" so the FE can message "no text
       found" instead of showing a no-op round-trip.

PIPELINE
--------
  1. Load the owned image bytes → PIL RGB. Very large images are downscaled
     (max side > ~2600 px) so inpaint + render stay fast; the OUTPUT is this
     working image, and inpaint/render live in its coordinate space.
  1a. OCR DOWNSCALE (speed). Florence reads text fine at a modest resolution and
     a 3024×4032 phone photo is very slow per pass — × up to 4 orientation
     passes it crawled. So OCR (orientation detect + Florence) runs on a copy
     downscaled to ~1600 px; the detected boxes are scaled BACK UP to the
     full-res working frame for inpaint/render, so the OUTPUT stays full-res.
  1b. ORIENTATION DETECTION. Text shot sideways/upside-down reads poorly in
     its native orientation, so we run Florence on the 90° rotations (ocr.py's
     shared `_ocr_best_orientation`) and pick the best-reading one — but we
     EARLY-EXIT after the 0° pass when it's clearly upright (the common case),
     so most upright photos cost ONE Florence pass, not four. The ENTIRE
     pipeline (OCR → translate → inpaint → render) then runs on the image IN
     THAT BEST ORIENTATION, and the finished composite is rotated BACK to the
     user's original orientation before returning — so the result is
     right-side-up for the user but the text was read upright.
  2. Florence-2 `<OCR_WITH_REGION>` → per-line regions. We reuse ocr.py's
     `_florence_ocr_region`, which already returns NORMALIZED 0..1 boxes
     (its `_quad_to_box` divides the quad by image w/h) on a fixed
     `OcrResult`; we multiply back to PIXEL axis-aligned bboxes here. Empty /
     whitespace labels are dropped.
  2a. HANDWRITING re-read (TrOCR). Florence DETECTS text regions well even on
     cursive but READS handwriting poorly, so on the winning orientation's
     full-res regions we crop each box and re-read it with TrOCR
     (`microsoft/trocr-base-handwritten`) on CPU, substituting TrOCR's text
     (`_trocr_rewrite_regions` → ocr.py's `_trocr_read_regions`). This is the
     DEFAULT recognizer for in-image translate (handwritten notes are the point
     of this path); it runs ONCE on the winner (not during the 4-way scan) so
     CPU cost is one pass. Any line TrOCR can't improve keeps Florence's text;
     gated by `ocr_handwriting_enabled`. TrOCR is CPU-only on purpose — the GPU
     is full and a resident GPU recognizer isn't affordable (~1-3 s/line).
  3. Translate every region's text with NLLB-200. Source language is
     detected ONCE over the joined text (not per region — one detect, and a
     consistent src token for the whole image); each region is then
     translated with the same forced-BOS target token via ocr.py's cached
     model. Region ↔ translation stays index-aligned.
  4. ERASE: build a uint8 mask the size of the image, paint each (slightly
     dilated) text bbox = 255, then `cv2.inpaint(np_img, mask, 3,
     INPAINT_TELEA)` to remove the original ink cleanly.
  5. RENDER: with PIL ImageDraw on the inpainted image, for each region we
     first INFER the original ink's style from the pre-erase crop
     (`_analyze_region`): family CLASS (serif / sans / mono), BOLD (stroke
     thickness), ITALIC (stroke slant), and the ACTUAL ink COLOR (the median
     foreground-pixel color, not just black/white). We pick the matching
     bundled TTF (Liberation Sans/Serif/Mono + Bold/Italic/BoldItalic, DejaVu
     fallback) via `_resolve_font`, choose the LARGEST size whose wrapped
     translation fits the box width AND height (step-down search), and draw the
     wrapped lines left-aligned with the FIRST line's BASELINE aligned to where
     the original sat (single-line) so the new text occupies the same spot.
     Glyphs are anti-aliased by PIL.
  6. Encode PNG and return it.

REUSE / IMPORT-SAFETY
---------------------
Florence OCR (`_florence_ocr_region`, the 4-way `_ocr_best_orientation`) and
the NLLB internals (`_get_nllb`, `_detect_src_flores`, `_to_flores`,
`_flores_name`, `_resolve_forced_bos`, `_translate_one_chunk_sync`) are
imported from the sibling ocr.py / translate_stream.py modules — the same
model load + chunk output the OCR and document-translate paths use. As in those modules NOTHING
heavy is imported at module-import time: torch / cv2 / PIL only load inside
the worker functions, so importing this module can never crash uvicorn
--reload (a broken import there takes the whole API down).

If NLLB can't be loaded (sentencepiece / weights missing — `_get_nllb`
raises RuntimeError) the route returns a clean 503. There is no Qwen
fallback here: rendering translated text back into a box needs the dedicated
translator's per-region output, and the non-streaming /translate route still
offers the Qwen fallback for text-only translation on builds without NLLB.

LIMITATION (honest note)
------------------------
Best on PRINTED text over SIMPLE / flat backgrounds (signs, screenshots,
labels) — inpaint removes the ink cleanly and the re-rendered box reads well.
Textured or photographic backgrounds under the text leave faint inpaint
smudges. HANDWRITING is now read by TrOCR (step 2a) rather than Florence, which
reads cursive far better; very stylized fonts or severely degraded ink can
still be mistranscribed, and TrOCR reads one line per box so an undetected
region is still missed.

FONT FIDELITY: we match the original's serif-ness, bold, italic, ink color, and
position — NOT the exact typeface. Identifying the precise font from pixels is a
separate font-identification problem (needs a font-recognition model + the
actual font files) and isn't feasible here, so we render with the closest
bundled family CLASS (Liberation/DejaVu Sans·Serif·Mono in the right
weight/slant) carrying the real ink color and baseline/left position. The
serif/bold/italic estimates are pixel heuristics on a single crop and can
mis-read decorative or very small text; each falls back to a safe default
(sans / regular / black-or-white) so a bad guess never breaks the render. CJK /
Arabic targets still render as tofu (none of the bundled families carry those
glyphs); Latin / Cyrillic / Greek render correctly.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import User

# Reuse the OCR + NLLB internals + owner-scoping from the sibling modules so
# this path shares ONE model load and identical detection/translation output
# with /ocr, /translate, and /translate-stream. All import-safe (ocr.py /
# translate_stream.py import no ML at module-import time).
from backend.api.ocr import (
    _detect_src_flores,
    _flores_name,
    _florence_ocr_region,
    _get_nllb,
    _load_owned_image,
    _ocr_best_orientation,
    _read_image_bytes,
    _to_flores,
    _trocr_enabled,
    _trocr_read_regions,
)
from backend.api.translate_stream import (
    _resolve_forced_bos,
    _translate_one_chunk_sync,
)

logger = logging.getLogger(__name__)

# Same /images prefix + tags as ocr.py so the route reads as part of the
# images surface. Wired separately in app.py (see the hand-off at the end of
# this module's task report).
router = APIRouter(prefix="/images", tags=["images", "ocr"])


# Downscale anything whose longest side exceeds this so Florence + inpaint
# stay quick; the output is the working image (one coordinate space).
_MAX_SIDE = 2600

# OCR-ONLY working size. Florence reads text fine at a modest resolution, and a
# 3024×4032 phone photo costs a LOT per pass — × up to 4 orientation passes it
# crawled. So we run OCR (orientation detect + Florence) on a copy downscaled to
# this longest side, then SCALE THE DETECTED BOXES BACK UP to the full-res
# working image for inpaint + render — so the OUTPUT stays full resolution while
# OCR is fast. ~1600 keeps text legible to Florence while roughly halving the
# pixels of a 2600-side image (≈ a quarter of a 3024-side original).
_OCR_MAX_SIDE = 1600

# A 0° read this strong is clearly upright, so we skip the other three Florence
# orientation passes (the 4-way costs up to 4× the per-image OCR time). Passed
# as `early_exit_score` into the SHARED `_ocr_best_orientation` (the OCR panel
# already uses the same opt-in via ocr.py's `_ORIENT_EARLY_EXIT_SCORE`). Mirrors
# that value so the image-translate path early-exits on the same confidence bar.
_ORIENT_EARLY_EXIT_SCORE = 50.0

# SPEED — the in-image-translate STREAMING path defaults to reading a SINGLE
# orientation (0° only) instead of the 4-way sweep. Most photos/screenshots are
# upright, and on a DENSE page where the 0° early-exit never fires (handwriting
# reads low-confidence) the 4-way runs all four full-page Florence passes →
# minutes → the FE's "warming up" feels infinite. One upright pass is the fast,
# never-hanging default; the rare sideways image is the trade-off. The 4-way is
# still available via `_ocr_stage(..., four_way=True)` (the non-streaming route
# keeps it).
_STREAM_FOUR_WAY_DEFAULT = False

# Bound Florence's decode on this path so ONE dense full page can't generate a
# huge token sequence (minutes) and hang. <OCR_WITH_REGION> emits ~ a handful of
# tokens per detected line; 512 comfortably covers a busy page of lines while
# capping the worst case, and greedy (num_beams=1) avoids paying that decode 3×
# over (the OCR panel's beam=3 quality isn't needed when we only translate the
# text). Passed through `_regions_pixels` → ocr.py's `_florence_ocr_region`.
_OCR_MAX_NEW_TOKENS = 512
_OCR_NUM_BEAMS = 1

# HARD wall-clock ceiling for the OCR stage (decode + any orientation passes +
# the optional VL handwriting re-read). The streaming route wraps the to_thread
# OCR in `asyncio.wait_for(timeout=...)` at this value so the stage can NEVER
# hang forever; on timeout the generator emits a terminal {"error": …} line.
# Digital pages return in a few seconds (no VL); the ceiling is generous because
# a handwriting page swaps in Qwen2.5-VL (~6 GB) and re-reads every line crop,
# which legitimately takes up to a couple of minutes on a busy 12 GB card.
_OCR_DEADLINE_S = 180.0

# ---------------------------------------------------------------------------
# FONT REGISTRY — pick a bundled TTF that matches the ORIGINAL region's style.
#
# We can't identify the exact typeface from pixels (font identification is a
# separate, heavyweight problem — see the LIMITATION note in the module
# docstring), so instead we match the three properties we CAN infer from the
# original ink — family CLASS (serif / sans / mono), BOLD, and ITALIC — to a
# bundled font that carries those traits. Matching weight + slant + serif-ness
# (plus the real ink color and position, below) makes the re-rendered text read
# like the original far better than one flat regular sans ever did.
#
# Two families ship in the container (verified with `ls
# /usr/share/fonts/truetype/`):
#   * LIBERATION (Sans/Serif/Mono) — metric-compatible with Arial / Times /
#     Courier and, crucially, ships the COMPLETE Regular+Bold+Italic+BoldItalic
#     set for ALL THREE classes. This is the PRIMARY family because it's the
#     only one with real italics for serif & sans.
#   * DEJAVU (Sans/Serif/Mono) — wide Unicode coverage but NO italic for Sans or
#     Serif (only Mono has Oblique). Used as a FALLBACK when a Liberation file
#     is somehow missing, and for its slightly broader glyph set on the
#     non-italic styles.
# Both cover Latin / Cyrillic / Greek (CJK / Arabic still render as tofu — that
# pre-existing limitation is unchanged).
#
# Keys are (class, bold, italic); each value is an ordered list of candidate
# absolute paths (first existing wins). `_resolve_font` caches the chosen path.
# ---------------------------------------------------------------------------
_LIB = "/usr/share/fonts/truetype/liberation"
_DJV = "/usr/share/fonts/truetype/dejavu"
_MPL = "/opt/venv/lib/python3.12/site-packages/matplotlib/mpl-data/fonts/ttf"

# Handwriting face for re-rendering HANDWRITTEN regions so the translation still
# "looks written on the paper" (clean + legible, not a typewriter font). Patrick
# Hand is a single-weight neat handprint (SIL OFL) downloaded in the Dockerfile.
# Latin/accented coverage only — non-Latin translations fall back to the Noto
# script face (which won't look handwritten, but at least renders the glyphs).
_HANDWRITING_FONT = "/usr/share/fonts/truetype/handwriting/PatrickHand-Regular.ttf"

# (class, bold, italic) -> ordered candidate paths. Liberation first (true
# italics everywhere); DejaVu as the Unicode-broad fallback. For serif/sans
# ITALIC we keep a DejaVu non-italic last-resort so we still get the right
# FAMILY + WEIGHT even if Liberation's italic file is unexpectedly absent.
_FONT_TABLE: dict[tuple[str, bool, bool], tuple[str, ...]] = {
    # --- SANS ---------------------------------------------------------------
    ("sans", False, False): (
        f"{_LIB}/LiberationSans-Regular.ttf",
        f"{_DJV}/DejaVuSans.ttf",
        f"{_MPL}/DejaVuSans.ttf",
    ),
    ("sans", True, False): (
        f"{_LIB}/LiberationSans-Bold.ttf",
        f"{_DJV}/DejaVuSans-Bold.ttf",
        f"{_MPL}/DejaVuSans-Bold.ttf",
    ),
    ("sans", False, True): (
        f"{_LIB}/LiberationSans-Italic.ttf",
        f"{_DJV}/DejaVuSans.ttf",
    ),
    ("sans", True, True): (
        f"{_LIB}/LiberationSans-BoldItalic.ttf",
        f"{_DJV}/DejaVuSans-Bold.ttf",
    ),
    # --- SERIF --------------------------------------------------------------
    ("serif", False, False): (
        f"{_LIB}/LiberationSerif-Regular.ttf",
        f"{_DJV}/DejaVuSerif.ttf",
    ),
    ("serif", True, False): (
        f"{_LIB}/LiberationSerif-Bold.ttf",
        f"{_DJV}/DejaVuSerif-Bold.ttf",
    ),
    ("serif", False, True): (
        f"{_LIB}/LiberationSerif-Italic.ttf",
        f"{_DJV}/DejaVuSerif.ttf",
    ),
    ("serif", True, True): (
        f"{_LIB}/LiberationSerif-BoldItalic.ttf",
        f"{_DJV}/DejaVuSerif-Bold.ttf",
    ),
    # --- MONO ---------------------------------------------------------------
    ("mono", False, False): (
        f"{_LIB}/LiberationMono-Regular.ttf",
        f"{_DJV}/DejaVuSansMono.ttf",
    ),
    ("mono", True, False): (
        f"{_LIB}/LiberationMono-Bold.ttf",
        f"{_DJV}/DejaVuSansMono-Bold.ttf",
    ),
    ("mono", False, True): (
        f"{_LIB}/LiberationMono-Italic.ttf",
        f"{_DJV}/DejaVuSansMono-Oblique.ttf",
    ),
    ("mono", True, True): (
        f"{_LIB}/LiberationMono-BoldItalic.ttf",
        f"{_DJV}/DejaVuSansMono-BoldOblique.ttf",
    ),
}

# Absolute last resort if NOTHING in the table exists (keeps accents working).
_FONT_LAST_RESORT = (
    f"{_DJV}/DejaVuSans.ttf",
    f"{_MPL}/DejaVuSans.ttf",
)

# Cache of (class,bold,italic) -> resolved existing path (or None). Filled on
# first use so we stat each candidate at most once per process.
_FONT_RESOLVED: dict[tuple[str, bool, bool], object] = {}

# ---------------------------------------------------------------------------
# NON-LATIN SCRIPT COVERAGE (sub-project B). Liberation + DejaVu cover only
# Latin / Cyrillic / Greek, so translating image text INTO Chinese / Japanese /
# Korean / Arabic / Hebrew / Indic / Thai rendered as tofu (□□□). Noto fills the
# gap: fonts-noto-cjk (CJK) + fonts-noto-core (per-script NotoSans) are installed
# in the Dockerfile. When the text to RENDER contains a covered non-Latin script
# we pick the matching Noto face; otherwise the class-aware `_resolve_font` wins.
# RTL/Indic shaping comes from Pillow's RAQM layout engine when available (see
# `_make` in the renderer), which also does the bidi reordering for Arabic/Hebrew.
_NOTO_TTF = "/usr/share/fonts/truetype/noto"
_NOTO_OTF = "/usr/share/fonts/opentype/noto"

# (script key, codepoint ranges, regular candidates, bold candidates)
_SCRIPT_FONTS: tuple = (
    ("cjk",
     ((0x4E00, 0x9FFF), (0x3040, 0x30FF), (0xAC00, 0xD7A3), (0x3400, 0x4DBF),
      (0xF900, 0xFAFF)),
     (f"{_NOTO_OTF}/NotoSansCJK-Regular.ttc",
      f"{_NOTO_OTF}/NotoSansCJKsc-Regular.otf"),
     (f"{_NOTO_OTF}/NotoSansCJK-Bold.ttc",
      f"{_NOTO_OTF}/NotoSansCJKsc-Bold.otf")),
    ("arabic",
     ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF),
      (0xFE70, 0xFEFF)),
     (f"{_NOTO_TTF}/NotoSansArabic-Regular.ttf",
      f"{_NOTO_TTF}/NotoNaskhArabic-Regular.ttf"),
     (f"{_NOTO_TTF}/NotoSansArabic-Bold.ttf",
      f"{_NOTO_TTF}/NotoNaskhArabic-Bold.ttf")),
    ("hebrew", ((0x0590, 0x05FF), (0xFB1D, 0xFB4F)),
     (f"{_NOTO_TTF}/NotoSansHebrew-Regular.ttf",),
     (f"{_NOTO_TTF}/NotoSansHebrew-Bold.ttf",)),
    ("devanagari", ((0x0900, 0x097F),),
     (f"{_NOTO_TTF}/NotoSansDevanagari-Regular.ttf",),
     (f"{_NOTO_TTF}/NotoSansDevanagari-Bold.ttf",)),
    ("thai", ((0x0E00, 0x0E7F),),
     (f"{_NOTO_TTF}/NotoSansThai-Regular.ttf",),
     (f"{_NOTO_TTF}/NotoSansThai-Bold.ttf",)),
    ("bengali", ((0x0980, 0x09FF),),
     (f"{_NOTO_TTF}/NotoSansBengali-Regular.ttf",),
     (f"{_NOTO_TTF}/NotoSansBengali-Bold.ttf",)),
    ("tamil", ((0x0B80, 0x0BFF),),
     (f"{_NOTO_TTF}/NotoSansTamil-Regular.ttf",),
     (f"{_NOTO_TTF}/NotoSansTamil-Bold.ttf",)),
)

_SCRIPT_FONT_RESOLVED: dict[tuple[str, bool], object] = {}


def _script_font(text: str, bold: bool) -> Optional[str]:
    """If `text` contains a covered non-Latin script, return a glyph-capable Noto
    face for it (bold variant when present, else regular), or None so the caller
    uses the class-aware Latin font. Picks the script with the most matching
    chars. Cached per (script, bold)."""
    import os

    if not text:
        return None
    counts: dict[int, int] = {}
    for ch in text:
        cp = ord(ch)
        for idx, (_k, ranges, _r, _b) in enumerate(_SCRIPT_FONTS):
            if any(lo <= cp <= hi for lo, hi in ranges):
                counts[idx] = counts.get(idx, 0) + 1
                break
    if not counts:
        return None
    idx = max(counts, key=counts.get)
    cache_key = (_SCRIPT_FONTS[idx][0], bold)
    if cache_key in _SCRIPT_FONT_RESOLVED:
        v = _SCRIPT_FONT_RESOLVED[cache_key]
        return v if isinstance(v, str) else None
    cands = _SCRIPT_FONTS[idx][3 if bold else 2]
    if bold:
        cands = cands + _SCRIPT_FONTS[idx][2]  # regular if no bold file
    chosen = next((p for p in cands if os.path.isfile(p)), None)
    _SCRIPT_FONT_RESOLVED[cache_key] = chosen if chosen else False
    return chosen


def _handwriting_font() -> Optional[str]:
    """Return the bundled handwriting TTF if present, else None (caller falls
    back to the class-aware Latin font). Cached after the first stat."""
    import os
    global _HANDWRITING_FONT_RESOLVED
    try:
        return _HANDWRITING_FONT_RESOLVED  # type: ignore[name-defined]
    except NameError:
        pass
    _HANDWRITING_FONT_RESOLVED = _HANDWRITING_FONT if os.path.isfile(_HANDWRITING_FONT) else None
    return _HANDWRITING_FONT_RESOLVED


class TranslateImageRequest(BaseModel):
    # Target language: a FLORES-200 code (spa_Latn, …) or an ISO-639-1 code
    # (es, …). Unknown/empty falls back to English server-side. max_length
    # covers the longest FLORES code with headroom.
    target: str = Field(default="eng_Latn", max_length=40)


# ---------------------------------------------------------------------------
# Helpers — all blocking; the route runs the whole pipeline via
# asyncio.to_thread so the event loop stays free. Heavy imports (cv2, numpy,
# PIL, torch via the reused fns) are LAZY inside the functions.
# ---------------------------------------------------------------------------
def _resolve_font(klass: str, bold: bool, italic: bool) -> Optional[str]:
    """Return the first EXISTING bundled TTF for the (class, bold, italic) style,
    or a last-resort Unicode TTF, or None (caller falls back to PIL's bitmap
    default). Results are cached per (class,bold,italic) so each candidate is
    stat-ed at most once per process.

    Graceful degradation when an exact style file is missing: we DON'T silently
    fall to a plain regular sans — we step DOWN the style table so the closest
    match keeps as many of the original's traits as possible:
      bold+italic → bold → italic → regular (within the SAME class first),
    and the per-style candidate lists themselves already fall serif/sans italic
    back to the same-family non-italic DejaVu before leaving the family."""
    import os

    key = (klass, bold, italic)
    if key in _FONT_RESOLVED:
        v = _FONT_RESOLVED[key]
        return v if isinstance(v, str) else None

    # Try the requested style, then progressively drop traits but STAY in-class:
    # (b,i) → (b,False) → (False,i) → (False,False). This keeps serif-ness and
    # as much weight/slant as the bundled set allows.
    style_order = [
        (bold, italic),
        (bold, False),
        (False, italic),
        (False, False),
    ]
    seen: set[tuple[bool, bool]] = set()
    for b, i in style_order:
        if (b, i) in seen:
            continue
        seen.add((b, i))
        for p in _FONT_TABLE.get((klass, b, i), ()):  # type: ignore[arg-type]
            if os.path.isfile(p):
                _FONT_RESOLVED[key] = p
                return p

    for p in _FONT_LAST_RESORT:
        if os.path.isfile(p):
            _FONT_RESOLVED[key] = p
            return p

    _FONT_RESOLVED[key] = None
    return None


def _load_rgb(raw_bytes: bytes):
    """Decode bytes → PIL RGB, downscaling if the longest side is huge.
    Returns (pil_image, scale) where scale<1 means it was shrunk."""
    from PIL import Image as PILImage

    img = PILImage.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.width, img.height
    scale = 1.0
    longest = max(w, h)
    if longest > _MAX_SIDE:
        scale = _MAX_SIDE / float(longest)
        img = img.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))),
            PILImage.LANCZOS,
        )
    return img, scale


def _ocr_downscaled(img):
    """Return a copy of the working image downscaled to `_OCR_MAX_SIDE` for the
    OCR pass (returns the SAME image object when it's already small enough).
    OCR runs on this smaller copy so the up-to-4 Florence orientation passes are
    fast; the boxes it finds are scaled back up to `img`'s full coordinate space
    before inpaint/render (see `_scale_regions`), so the OUTPUT stays full-res."""
    from PIL import Image as PILImage

    longest = max(img.width, img.height)
    if longest <= _OCR_MAX_SIDE:
        return img  # already small enough — OCR directly, no extra resize
    s = _OCR_MAX_SIDE / float(longest)
    return img.resize(
        (max(1, round(img.width * s)), max(1, round(img.height * s))),
        PILImage.LANCZOS,
    )


def _scale_regions(regions: list[dict], sx: float, sy: float, iw: int, ih: int) -> list[dict]:
    """Scale pixel regions detected on the OCR-downscaled frame UP to the
    full-res working frame: multiply each box by (sx, sy), clamp into the
    (iw, ih) image, and drop any that go degenerate. Text is carried through
    unchanged. When sx == sy == 1.0 (OCR ran at full res) this is a cheap clamp
    pass. Region ↔ text alignment is preserved (same order, 1:1)."""
    if not regions:
        return []
    out: list[dict] = []
    for r in regions:
        x0, y0, x1, y1 = r["box"]
        nx0 = max(0, min(int(round(x0 * sx)), iw - 1))
        ny0 = max(0, min(int(round(y0 * sy)), ih - 1))
        nx1 = max(0, min(int(round(x1 * sx)), iw))
        ny1 = max(0, min(int(round(y1 * sy)), ih))
        if nx1 - nx0 < 2 or ny1 - ny0 < 2:
            continue
        out.append({"box": (nx0, ny0, nx1, ny1), "text": r["text"]})
    return out


def _regions_pixels(img, return_confidence: bool = False):
    """Run Florence <OCR_WITH_REGION> and return PIXEL axis-aligned regions
    [{box:(x0,y0,x1,y1), text}] for the given PIL image (already at working
    size), plus (iw, ih). ocr.py's `_florence_ocr_region` returns NORMALIZED
    0..1 boxes on an OcrResult; we scale them back to pixels here. Empty on
    failure / no text.

    The decode is BOUNDED here (`_OCR_MAX_NEW_TOKENS` tokens, greedy
    `_OCR_NUM_BEAMS=1`) so a dense full page can't make Florence generate for
    minutes and hang this path — unlike the OCR panel, we only need the text to
    translate, not beam-search quality.

    When `return_confidence=True`, returns (regions, iw, ih, mean_conf) where
    `mean_conf` is Florence's mean per-token log-probability — the orientation
    detector ranks by this so the hallucinated 180° read (low confidence even
    when it invents more characters) is rejected."""
    iw, ih = img.width, img.height
    result, conf = _florence_ocr_region(
        img,
        iw,
        ih,
        return_confidence=True,
        max_new_tokens=_OCR_MAX_NEW_TOKENS,
        num_beams=_OCR_NUM_BEAMS,
    )
    if result is None or not result.lines:
        return ([], iw, ih, conf) if return_confidence else ([], iw, ih)

    regions: list[dict] = []
    for ln in result.lines:
        text = (ln.text or "").strip()
        if not text:
            continue
        x0 = ln.x * iw
        y0 = ln.y * ih
        x1 = (ln.x + ln.w) * iw
        y1 = (ln.y + ln.h) * ih
        # Clamp + integerize; drop degenerate boxes.
        x0i = max(0, min(int(round(x0)), iw - 1))
        y0i = max(0, min(int(round(y0)), ih - 1))
        x1i = max(0, min(int(round(x1)), iw))
        y1i = max(0, min(int(round(y1)), ih))
        if x1i - x0i < 2 or y1i - y0i < 2:
            continue
        regions.append({"box": (x0i, y0i, x1i, y1i), "text": text})
    return (regions, iw, ih, conf) if return_confidence else (regions, iw, ih)


def _trocr_rewrite_regions(full_img, regions: list[dict]) -> list[dict]:
    """Re-read each Florence-detected region with TrOCR (handwriting, CPU) on
    the FULL-RES frame and substitute its text, so handwritten / cursive notes
    translate from an ACCURATE read rather than Florence's poor cursive
    transcription.

    This is the in-image-translate path's DEFAULT recognizer: Florence is used
    only as the DETECTOR (its boxes are excellent even on handwriting), and
    TrOCR reads each box. We run it ONCE here on the winning orientation's
    full-res regions (not during the 4-way orientation scan — that would pay
    CPU TrOCR up to 4×), cropping from `full_img` (the sharpest pixels) for the
    best handwriting read. Region ↔ text stays index-aligned; any line TrOCR
    can't improve keeps Florence's text. Gated by `ocr_handwriting_enabled`.
    NEVER raises — on any failure returns `regions` unchanged."""
    if not regions or not _trocr_enabled():
        return regions
    try:
        boxes = [tuple(r["box"]) for r in regions]
        reads = _trocr_read_regions(full_img, boxes)
        if not any(r for r in reads):
            return regions  # TrOCR added nothing → keep Florence's text
        out: list[dict] = []
        for r, t in zip(regions, reads):
            out.append({"box": r["box"], "text": t if t else r["text"]})
        return out
    except Exception:
        logger.exception("translate-image: TrOCR region re-read failed; keeping Florence")
        return regions


# ---------------------------------------------------------------------------
# CONTEXT-AWARE HANDWRITING RECOGNITION (Qwen2.5-VL).
#
# Florence reads cursive poorly and TrOCR reads a line in isolation, so meaning-
# changing single-char misreads survive ("Rome"->"home", "Nope"->"hope"). A
# vision-language model re-reads each detected LINE crop WITH its in-line context
# and recovers the right word. Gated to handwriting pages only (low Florence
# confidence) because it's far heavier than Florence; controlled by:
#   IMG_VL_RECOG          = auto | on | off   (default auto)
#   IMG_VL_CONF_THRESHOLD = float             (default -0.6; below ⇒ handwriting)
# Calibrated 2026-06-04: Florence mean per-token logprob ≈ -0.81 on a handwritten
# note vs ≈ -0.36 on a clean digital screenshot, so -0.6 separates them cleanly.
# ---------------------------------------------------------------------------
def _vl_recog_mode() -> str:
    v = (os.environ.get("IMG_VL_RECOG", "auto") or "auto").strip().lower()
    return v if v in {"auto", "on", "off"} else "auto"


def _vl_conf_threshold() -> float:
    try:
        return float(os.environ.get("IMG_VL_CONF_THRESHOLD", "-0.6"))
    except (TypeError, ValueError):
        return -0.6


def _is_handwriting(conf) -> bool:
    """Decide whether to run the VL handwriting re-read for this page."""
    mode = _vl_recog_mode()
    if mode == "off":
        return False
    if mode == "on":
        return True
    # auto: a low mean Florence confidence is the handwriting signal.
    return conf is not None and float(conf) < _vl_conf_threshold()


_VL_PREFIX_RE = re.compile(
    r"^\s*(the\s+)?(text|transcription|handwriting|it)\s*(reads?|says?|is)?\s*[:\-]?\s*",
    re.IGNORECASE,
)


def _strip_latex_md(s: str) -> str:
    """Remove the markdown / LaTeX escaping a chat VL sometimes emits in a
    transcription ('21\\. A dress \\(underwear\\)s \\n I black-shirt' ->
    '21. A dress (underwear)s I black-shirt'). Plain handwriting is plain text, so
    these backslashes are always artifacts."""
    if "\\" not in s:
        return s
    # literal escape sequences the model typed as two characters
    s = s.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    # LaTeX spacing macros -> a space
    s = re.sub(r"\\(?:quad|qquad|,|;|:|!|>)", " ", s)
    # backslash-escaped punctuation / space -> the bare character
    s = re.sub(r"\\([(){}\[\].,;:!?+\-*/=&%#_~^'\"| ])", r"\1", s)
    # any remaining lone backslashes
    s = s.replace("\\", "")
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _clean_vl_text(t: str) -> str:
    """Strip the boilerplate a chat VL sometimes wraps a transcription in
    ('The text reads: "…"', surrounding quotes, trailing commentary newlines) and
    any markdown / LaTeX escaping it emits."""
    s = (t or "").strip()
    if not s:
        return ""
    # Keep only the first line block; VLs sometimes append an explanation.
    s = s.strip().strip("`")
    s = _strip_latex_md(s)
    s = _VL_PREFIX_RE.sub("", s)
    # Drop matching surrounding quotes.
    if len(s) >= 2 and s[0] in "\"'“”«" and s[-1] in "\"'“”»":
        s = s[1:-1].strip()
    # Add the space the VL drops after a leading list number ("10.Favorite" ->
    # "10. Favorite") for a clean read; `(?!\d)` leaves a decimal "3.5" alone.
    s = re.sub(r"^(\s*\d+[.)])(?!\d)(\S)", r"\1 \2", s)
    return s


_VL_REPEAT_RE = re.compile(r"(.{1,8})\1{3,}")


def _looks_degenerate(s: str) -> bool:
    """True when a VL transcription looks like decoder garbage (a loop / mostly
    symbols) so the caller falls back to Florence rather than render soup like
    'AunderAunderAunder', 'eacheacheach', '↓ ↓↓↓ ▲', or '323223.33.123'."""
    s = (s or "").strip()
    if len(s) < 6:
        return False
    nospace = s.replace(" ", "")
    if _VL_REPEAT_RE.search(nospace):
        return True  # a short substring repeated many times (AunderAunder, eacheach)
    words = s.split()
    if len(words) >= 4:
        if len(set(words)) / len(words) < 0.5:
            return True  # the same few words repeated
        if any(words.count(w) >= 4 for w in set(words)):
            return True  # one word repeated a lot ("each … each … each … each")
    letters = sum(c.isalpha() for c in s)
    if len(s) > 10 and letters / len(s) < 0.35:
        return True  # almost no letters (digit / symbol / arrow soup)
    return False


def _vl_read_regions(full_img, boxes: list[tuple], batch: int = 6) -> list[Optional[str]]:
    """Transcribe each detected line with Qwen2.5-VL, batched. Returns a read per
    box (None where the VL is unavailable / fails). Each line is cropped TIGHT (its
    own box + a little padding so a descender/ascender isn't clipped) and read on
    its own — the line itself already carries the in-line context that
    disambiguates words ("going to ___ or pompeii" -> Rome). Output stays strictly
    1:1 with `boxes`. NEVER raises — returns Nones on any failure so the caller
    keeps Florence.

    (An earlier multi-line "neighbor context band" variant was tried and reverted:
    on a two-column page the bands spanned both columns, the VL ignored the target
    marker and read neighbours -> duplicate/fragmented reads, and the large crops
    OOMed the 12 GB card. Tight per-line crops are clean 1:1 and fast.)"""
    from backend.vision.runtime import get_qwen_vl

    vl = get_qwen_vl()
    if vl is None or not boxes:
        return [None] * len(boxes)
    model, processor, device = vl
    import torch
    from PIL import Image as PILImage

    iw, ih = full_img.width, full_img.height
    crops = []
    for (x0, y0, x1, y1) in boxes:
        # TIGHT vertical padding so the crop doesn't bleed into the line above /
        # below (dense handwriting) — a bled crop makes the VL transcribe several
        # lines and loop. A little horizontal slack keeps the line's own context.
        pad_x = int((x1 - x0) * 0.05) + 4
        pad_y = int((y1 - y0) * 0.15) + 3
        c = full_img.crop((max(0, x0 - pad_x), max(0, y0 - pad_y),
                           min(iw, x1 + pad_x), min(ih, y1 + pad_y))).convert("RGB")
        # Bound the crop so the VL's vision-token count (attention memory + latency)
        # stays small — a big crop OOMed / ran for minutes on the 12 GB card.
        cap = 640
        longest = max(c.width, c.height)
        if longest > cap:
            s = cap / float(longest)
            c = c.resize((max(1, int(c.width * s)), max(1, int(c.height * s))),
                         PILImage.LANCZOS)
        if c.height and c.height < 64:  # upscale short lines so glyphs stay legible
            s = 64.0 / c.height
            c = c.resize((max(1, int(c.width * s)), 64), PILImage.LANCZOS)
        crops.append(c)

    prompt = (
        "Transcribe the handwritten text in this image exactly as written, "
        "preserving spelling, numbers and punctuation. Output PLAIN TEXT only — "
        "no markdown, no LaTeX, no quotes, labels or commentary."
    )
    reads: list[Optional[str]] = []
    for i in range(0, len(crops), batch):
        chunk = crops[i:i + batch]
        try:
            msgs = [
                [{"role": "user", "content": [
                    {"type": "image"}, {"type": "text", "text": prompt}]}]
                for _ in chunk
            ]
            texts = [
                processor.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
                for m in msgs
            ]
            inputs = processor(
                text=texts, images=chunk, padding=True, return_tensors="pt"
            ).to(device)
            with torch.no_grad():
                # A single text LINE is short; cap tokens low and apply anti-
                # repetition so an ambiguous crop can't send the decoder into a
                # loop (the "323223.33…" / "cyceLLing.cya" digit/letter soup).
                gen = model.generate(
                    **inputs,
                    max_new_tokens=64,
                    do_sample=False,
                    repetition_penalty=1.3,
                    no_repeat_ngram_size=3,
                )
            # LEFT padding (set on the processor) ⇒ all rows share the prompt
            # length, so the new tokens are the tail past input width.
            new = gen[:, inputs.input_ids.shape[1]:]
            outs = processor.batch_decode(
                new, skip_special_tokens=True, clean_up_tokenization_spaces=True
            )
            reads.extend(_clean_vl_text(o) for o in outs)
        except Exception:
            logger.exception("translate-image: VL batch read failed; Florence kept")
            reads.extend([None] * len(chunk))
    return reads


def _accept_vl_read(florence_text: str, vl_text: str) -> bool:
    """Decide whether the VL read REPLACES Florence's read for a line. Reject the
    bleed/loop artifacts a crop can still produce: empty, degenerate, a MULTI-LINE
    read (the crop caught a neighbour line), or a read wildly longer than Florence's
    (a runaway). Keeping Florence on reject is safe — it stays box-accurate even
    when its word choice is poor."""
    v = (vl_text or "").strip()
    if not v or _looks_degenerate(v):
        return False
    if "\n" in v:                       # bled into an adjacent line
        return False
    f = (florence_text or "").strip()
    if f and len(v) > max(2.5 * len(f), len(f) + 24):
        return False                    # runaway vs the detected line length
    return True


def _vl_rewrite_regions(full_img, regions: list[dict]) -> list[dict]:
    """Re-read every (non-skip) region with Qwen2.5-VL on the FULL-RES frame and
    substitute its text — the accurate, context-aware handwriting read. Marks each
    region `handwriting=True` so the renderer uses the handwriting font + clean
    ink. Region ↔ text alignment is preserved; any line whose VL read is rejected
    (`_accept_vl_read`) keeps Florence's text. NEVER raises — returns `regions`
    unchanged on failure."""
    if not regions:
        return regions
    try:
        idxs = [i for i, r in enumerate(regions) if not r.get("skip")]
        boxes = [tuple(regions[i]["box"]) for i in idxs]
        reads = _vl_read_regions(full_img, boxes)
        got = 0
        for i, t in zip(idxs, reads):
            regions[i]["handwriting"] = True  # the page IS handwriting
            if _accept_vl_read(regions[i].get("text", ""), t):
                regions[i]["text"] = t.strip()
                got += 1
        logger.info("translate-image: VL re-read %d/%d handwritten regions",
                    got, len(boxes))
        return regions
    except Exception:
        logger.exception("translate-image: VL region re-read failed; keeping Florence")
        return regions


def _prepare_translation(texts: list[str], target: str):
    """Resolve EVERYTHING needed to translate the image's regions, ONCE, before
    any region is translated. Returns
    (model, tokenizer, device, gen_kwargs, src_flores, tgt_flores).

    Detects the source language ONCE over the joined region text (a single
    consistent src token for the whole image), resolves the forced-BOS target
    token ONCE, and pins the Apache-2.0 engine routing keys. Splitting this out
    of `_translate_regions` lets the STREAMING path detect source + emit the
    language pair up front, then translate region-by-region while yielding each
    one. Raises RuntimeError when NLLB can't load (route → 503)."""
    model, tokenizer, device = _get_nllb()  # cached; RuntimeError if absent

    joined = "\n".join(t for t in texts if t).strip()
    src_flores = _detect_src_flores(joined) if joined else "eng_Latn"
    tgt_flores = _to_flores(target)

    # NLLB tokenizer: set src_lang so the source token is prepended (same as
    # the other NLLB paths).
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
    # the shared per-chunk fn reads these private keys.
    gen_kwargs["_nt_target"] = tgt_flores
    gen_kwargs["_nt_source"] = src_flores
    return model, tokenizer, device, gen_kwargs, src_flores, tgt_flores


def _translate_one_region(model, tokenizer, device, text: str, gen_kwargs: dict) -> str:
    """Translate ONE region's text (blocking). Best-effort: on any failure it
    returns the ORIGINAL text so a region is never lost. Empty in → empty out.
    Shared by the non-streaming batch path and the streaming generator so both
    produce byte-identical per-region output."""
    if not text.strip():
        return ""
    try:
        translated = _translate_one_chunk_sync(
            model, tokenizer, device, text, gen_kwargs
        )
    except Exception:
        logger.exception("translate-image: region translate failed; keeping original")
        translated = text  # never lose the region — fall back to source text
    return translated or text


def _translate_regions(texts: list[str], target: str) -> tuple[list[str], str, str]:
    """Translate each region's text with NLLB-200. Detects the source ONCE
    over the joined text (consistent src token for the whole image), resolves
    the forced-BOS target token ONCE, then translates each region with
    ocr.py/translate_stream.py's cached per-chunk generate. Returns
    (translations aligned to `texts`, src_flores, tgt_flores). Raises
    RuntimeError when NLLB can't load (route → 503)."""
    model, tokenizer, device, gen_kwargs, src_flores, tgt_flores = _prepare_translation(
        texts, target
    )
    out = [
        _fix_spacing(_translate_one_region(model, tokenizer, device, t, gen_kwargs))
        for t in texts
    ]
    return out, src_flores, tgt_flores


# lowercase/digit + .!? + uppercase: add the space the model dropped when it
# glued two sentences ("privacidad primero.El" -> "...primero. El"). Conservative
# so abbreviations (U.S.) and decimals (3.5) are left alone. Mirrors the same
# fix in translate_doc.py for the document path.
_GLUED_RE = re.compile(
    r"(?<=[a-z0-9áéíóúñüàâçèêëîïôûœ])([.!?])(?=[A-ZÁÉÍÓÚÑÜÀÂÇÈÊËÎÏÔÛŒ])"
)


def _fix_spacing(text: str) -> str:
    return _GLUED_RE.sub(r"\1 ", text or "")


def _union_box(boxes):
    xs0 = [b[0] for b in boxes]; ys0 = [b[1] for b in boxes]
    xs1 = [b[2] for b in boxes]; ys1 = [b[3] for b in boxes]
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _overlap_contain(a, b):
    """(IoU, containment) for two boxes; containment = intersection / smaller
    area (catches a sub-span box that sits inside a larger phrase box)."""
    ix0 = max(a[0], b[0]); iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2]); iy1 = min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if inter == 0:
        return 0.0, 0.0
    aa = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    ab = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / (aa + ab - inter), inter / min(aa, ab)


def _dedup_regions(regions: list[dict]) -> list[dict]:
    """Drop near-duplicate / heavily-overlapping OCR boxes — Florence sometimes
    emits a phrase AND a sub-span of it (the cause of the faint stray text that
    overlapped the hero). Keep the box with the longer text; restore reading
    order."""
    order = sorted(regions, key=lambda r: -len((r.get("text") or "")))
    kept: list[dict] = []
    for r in order:
        rb = tuple(r["box"])
        if any(
            (lambda io, co: io > 0.5 or co > 0.7)(*_overlap_contain(rb, k["box"]))
            for k in kept
        ):
            continue
        kept.append(r)
    kept.sort(key=lambda r: (r["box"][1], r["box"][0]))
    return kept


# Looks like a brand / product / code identifier that should NOT be translated.
_CODEISH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9./+_-]*$")
_CAMEL_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2}[a-z]")


def _should_skip_region(text: str) -> bool:
    """True when a region's text should be left UNTOUCHED (not inpainted or
    re-rendered) because translating it would mangle a brand / logo / code
    identifier. Conservative: only single tokens, never multi-word text."""
    t = (text or "").strip()
    if not t or " " in t:
        return False
    # CamelCase product names (OpenCLIP, PyTorch, MinIO), letters+digits mixed
    # (h264, S3), or short ALL-CAPS tokens (FAQ, API) — leave as-is.
    if _CAMEL_RE.search(t):
        return True
    if _CODEISH_RE.match(t) and any(c.isdigit() for c in t) and any(c.isalpha() for c in t):
        return True
    if t.isupper() and t.isalpha() and len(t) <= 4:
        return True
    return False


def _merge_regions(regions: list[dict]) -> list[dict]:
    """Merge raw OCR boxes into logical LINES then conservative BLOCKS so the
    translator gets sentence context (fixes fragment mistranslations) and the
    renderer recomposes the layout (fixes inconsistent per-box sizing). Returns
    merged regions {"box": union, "text": merged, "parts": [orig boxes],
    "line_h": int}. Never merges across a big horizontal gap (column break) or a
    font-size change. Reading order: top->bottom, left->right."""
    regs = [r for r in regions if (r.get("text") or "").strip()]
    regs = _dedup_regions(regs)  # drop overlapping duplicate detections first
    if len(regs) <= 1:
        return [{"box": tuple(r["box"]), "text": (r["text"] or "").strip(),
                 "parts": [tuple(r["box"])], "line_h": r["box"][3] - r["box"][1],
                 "skip": _should_skip_region((r["text"] or "").strip())}
                for r in regs]

    def bh(b):
        return max(1, b[3] - b[1])

    def yc(b):
        return (b[1] + b[3]) / 2.0

    H = sorted(bh(r["box"]) for r in regs)[len(regs) // 2]  # median text height

    # --- pass 1: group boxes into LINES (same row + horizontally adjacent) ---
    ordered = sorted(regs, key=lambda r: (yc(r["box"]), r["box"][0]))
    used = [False] * len(ordered)
    line_segs: list[list[dict]] = []
    for i, r in enumerate(ordered):
        if used[i]:
            continue
        used[i] = True
        row = [r]
        ci = yc(r["box"])
        for j in range(i + 1, len(ordered)):
            if used[j]:
                continue
            bj = ordered[j]["box"]
            if abs(yc(bj) - ci) >= 0.6 * H:
                continue
            ay0, ay1 = r["box"][1], r["box"][3]
            if min(ay1, bj[3]) - max(ay0, bj[1]) > 0:  # vertical overlap
                row.append(ordered[j]); used[j] = True
        row.sort(key=lambda g: g["box"][0])
        # split the row on big horizontal gaps (column boundaries)
        seg = [row[0]]
        for g in row[1:]:
            if g["box"][0] - seg[-1]["box"][2] < 1.5 * H:
                seg.append(g)
            else:
                line_segs.append(seg); seg = [g]
        line_segs.append(seg)

    lines = []
    for seg in line_segs:
        seg.sort(key=lambda g: g["box"][0])
        boxes = [tuple(g["box"]) for g in seg]
        text = " ".join((g["text"] or "").strip() for g in seg).strip()
        ub = _union_box(boxes)
        lines.append({"box": ub, "text": text, "parts": boxes, "line_h": bh(ub)})
    lines.sort(key=lambda r: (r["box"][1], r["box"][0]))

    # --- pass 2: merge consecutive LINES into a paragraph BLOCK (conservative) ---
    blocks: list[dict] = []
    for lr in lines:
        if blocks:
            p = blocks[-1]
            # Thresholds relative to the LINE's OWN height (not the global median)
            # so a huge hero (line_h ~100) merges its wrapped lines while small
            # body text stays conservative.
            lh = max(1, p["line_h"])
            same_left = abs(lr["box"][0] - p["box"][0]) < 0.6 * lh
            gap = lr["box"][1] - p["box"][3]
            small_gap = -0.5 * lh < gap < 0.8 * lh
            similar_h = 0.7 <= (lr["line_h"] / lh) <= 1.4
            # Only merge a TRUE wrapped continuation. NOT across a sentence end
            # (prev ends with .!?…) and NOT when the next line starts its own
            # list item (1. / a) / bullet) — otherwise consecutive list items
            # ("9. … 10. … 11. …") get glued into one blob and lose their
            # structure. This keeps lists intact in every language.
            prev_ends = bool(re.search(r"[.!?:;)。！？]\s*$", p["text"]))
            # A list marker at line start ("10.", "11)", "a.", "•") — do NOT require
            # a space after it: handwriting VL reads often glue it to the word
            # ("10.Favorite"), and requiring the space let SEVEN list items merge
            # into one blob. `(?!\d)` keeps a decimal like "3.5" from looking like
            # an item marker.
            next_is_item = bool(re.match(
                r"^\s*(\d+[.)]|[a-zA-Z][.)]|[•‣◦▪·*\-–—])(?!\d)", lr["text"]))
            if (same_left and small_gap and similar_h
                    and not prev_ends and not next_is_item):
                p["parts"] += lr["parts"]
                p["text"] = (p["text"] + " " + lr["text"]).strip()
                p["box"] = _union_box([p["box"], lr["box"]])
                continue
        blocks.append({"box": lr["box"], "text": lr["text"],
                       "parts": list(lr["parts"]), "line_h": lr["line_h"]})
    for b in blocks:
        b["skip"] = _should_skip_region(b["text"])
    return blocks


def _border_ring(np_img, x0, y0, x1, y1, t: int = 5):
    """Median colour + SPATIAL std of a thin ring of pixels JUST OUTSIDE a box —
    i.e. the local background (paper / page / pill colour) surrounding the text.
    Returns (median_rgb_tuple, std_float) or (None, None) when there's no ring.

    The std is the MEAN of the three PER-CHANNEL stds, i.e. how much the ring
    varies SPATIALLY — NOT the std of the flattened RGB values. A flat but
    saturated colour (e.g. a solid blue pill (36,92,220)) is spatially uniform yet
    has a large cross-channel spread; the old flattened std reported ~77 for it and
    wrongly classified every coloured pill as "textured". Per-channel std reports
    ~0 for any solid colour and stays high only for genuinely textured rings."""
    import numpy as np

    h, w = np_img.shape[:2]
    ty0, by1 = max(0, y0 - t), min(h, y1 + t)
    lx0, rx1 = max(0, x0 - t), min(w, x1 + t)
    parts = []
    if y0 > ty0:
        parts.append(np_img[ty0:y0, lx0:rx1].reshape(-1, 3))
    if by1 > y1:
        parts.append(np_img[y1:by1, lx0:rx1].reshape(-1, 3))
    if x0 > lx0:
        parts.append(np_img[y0:y1, lx0:x0].reshape(-1, 3))
    if rx1 > x1:
        parts.append(np_img[y0:y1, x1:rx1].reshape(-1, 3))
    parts = [p for p in parts if len(p)]
    if not parts:
        return None, None
    ring = np.concatenate(parts, axis=0)
    med = tuple(int(c) for c in np.median(ring, axis=0))
    return med, float(ring.std(axis=0).mean())


def _inpaint_erase(img, regions: list[dict]):
    """Erase the original text and restore the BACKGROUND under it ("magic
    eraser"). Returns a new PIL RGB image.

    PRIMARY: LaMa deep inpainting (runtime.get_lama) — reconstructs textured /
    photographic / gradient backgrounds (e.g. a photographed page) naturally,
    the way editing tools' content-aware erase does.
    FALLBACK (LaMa unavailable): per-box adaptive erase — FLAT-FILL the box with
    the sampled local background colour when it's uniform (paper / solid panel),
    else cv2.inpaint (TELEA) the textured remainder."""
    import numpy as np
    from PIL import Image as PILImage

    base = img.convert("RGB") if hasattr(img, "convert") else img
    np_img = np.array(base)  # HxWx3, RGB, uint8
    ih, iw = np_img.shape[:2]

    # Collect the dilated erase boxes for every non-skip region.
    boxes = []
    for r in regions:
        if r.get("skip"):
            continue  # brand/logo/code or unchanged — leave the original ink
        for (x0, y0, x1, y1) in r.get("parts", [r["box"]]):
            pad = max(3, int(round((y1 - y0) * 0.22)))
            boxes.append((max(0, x0 - pad), max(0, y0 - pad),
                          min(iw, x1 + pad), min(ih, y1 + pad)))
    if not boxes:
        return PILImage.fromarray(np_img)

    mask = np.zeros((ih, iw), dtype=np.uint8)
    for (x0, y0, x1, y1) in boxes:
        mask[y0:y1, x0:x1] = 255

    # 1) LaMa magic-erase over the whole mask (best reconstruction).
    try:
        from backend.vision.runtime import get_lama
        lama = get_lama()
    except Exception:
        lama = None
    if lama is not None:
        try:
            res = lama(PILImage.fromarray(np_img),
                       PILImage.fromarray(mask).convert("L"))
            return res.convert("RGB").resize((iw, ih)) if res.size != (iw, ih) else res.convert("RGB")
        except Exception:
            logger.warning("translate-image: LaMa erase failed; cv2/bg-fill fallback",
                           exc_info=True)

    # 2) Fallback: flat-fill uniform backgrounds, cv2.inpaint the textured rest.
    import cv2
    _UNIFORM_STD = 22.0
    telea = np.zeros((ih, iw), dtype=np.uint8)
    for (x0, y0, x1, y1) in boxes:
        med, std = _border_ring(np_img, x0, y0, x1, y1)
        if med is not None and std is not None and std < _UNIFORM_STD:
            np_img[y0:y1, x0:x1] = med
        else:
            telea[y0:y1, x0:x1] = 255
    if telea.any():
        np_img = cv2.inpaint(np_img, telea, 4, cv2.INPAINT_TELEA)
    return PILImage.fromarray(np_img)


def _bg_is_dark(img, box) -> bool:
    """Sample the ORIGINAL box and decide whether its background is dark, so
    we can pick a contrasting ink color for the re-rendered text. Uses the
    mean luminance of the box crop (Rec. 601). Robust enough for solid /
    near-solid backgrounds; on busy crops it just picks the better-contrast
    of black/white. Retained as the fallback when ink-color sampling can't
    confidently separate foreground from background."""
    import numpy as np

    x0, y0, x1, y1 = box
    crop = np.asarray(img.crop((x0, y0, x1, y1)))
    if crop.size == 0:
        return False
    # Rec.601 luma. crop is RGB uint8.
    r = crop[..., 0].astype(np.float32)
    g = crop[..., 1].astype(np.float32)
    b = crop[..., 2].astype(np.float32)
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return float(luma.mean()) < 128.0


# ---------------------------------------------------------------------------
# Style inference from the ORIGINAL ink.
#
# Florence gives us only text + a box — NO font name (see ocr.py's OcrLine).
# Exact typeface identification from pixels isn't feasible here, but three
# properties that strongly drive how text READS can be estimated from the
# original glyph pixels and matched to a bundled font:
#   * INK COLOR  — the actual foreground color (not just black/white), via a
#                  luma split of the crop into fg/bg and the median fg color;
#   * BOLD       — stroke thickness, via the ink AREA-to-PERIMETER ratio
#                  (thick strokes have more area per edge pixel) normalized by
#                  text height;
#   * ITALIC     — dominant slant, via how far the ink's horizontal centroid
#                  shifts from the top of the text band to the bottom;
#   * SERIF/SANS — a light serif cue (extra thin ink near the top & bottom
#                  rows where serifs/feet sit) — weak, so it defaults to SANS.
# All are heuristics on a single crop; each degrades to a safe default. Together
# with baseline/left alignment they make the render sit where the original did,
# in roughly the right weight, slant, color, and family.
# ---------------------------------------------------------------------------
def _ink_mask_and_color(crop_rgb):
    """From an RGB crop of the ORIGINAL text region, split pixels into
    foreground (ink) vs background by luma and return
    (fg_bool_mask, ink_rgb_tuple, bg_is_dark_bool, confident_bool).

    The text is whichever luma cluster is the MINORITY-ish, darker-or-lighter
    side around the crop's mean: we threshold at the midpoint between the dark
    and light luma modes. `ink_rgb` is the MEDIAN color of the fg pixels (robust
    to anti-aliased edge pixels). `confident` is False when fg/bg don't separate
    (very low contrast / busy crop) so the caller can fall back to black/white.
    Pure numpy; no model."""
    import numpy as np

    if crop_rgb.size == 0:
        return None, (16, 16, 16), False, False
    r = crop_rgb[..., 0].astype(np.float32)
    g = crop_rgb[..., 1].astype(np.float32)
    b = crop_rgb[..., 2].astype(np.float32)
    luma = 0.299 * r + 0.587 * g + 0.114 * b

    lo = float(np.percentile(luma, 10))
    hi = float(np.percentile(luma, 90))
    spread = hi - lo
    mid = (lo + hi) / 2.0

    # Decide which side is the background by majority: most pixels in a text box
    # are background. Foreground is the OTHER side.
    dark_mask = luma <= mid
    dark_frac = float(dark_mask.mean())
    bg_is_dark = dark_frac > 0.5  # majority dark ⇒ dark background
    fg_mask = ~dark_mask if bg_is_dark else dark_mask

    # Confidence: need real contrast AND a plausible ink coverage (not 0/all).
    fg_frac = float(fg_mask.mean())
    confident = spread >= 35.0 and 0.02 <= fg_frac <= 0.6

    if not fg_mask.any():
        return fg_mask, (16, 16, 16), bg_is_dark, False

    fr = np.median(crop_rgb[..., 0][fg_mask])
    fg = np.median(crop_rgb[..., 1][fg_mask])
    fb = np.median(crop_rgb[..., 2][fg_mask])
    ink = (int(fr), int(fg), int(fb))
    return fg_mask, ink, bg_is_dark, confident


def _estimate_bold(fg_mask) -> bool:
    """Heuristic BOLD test from the ink mask: bold strokes have a high ink
    AREA-to-EDGE ratio (thick strokes enclose more area per boundary pixel).
    We compare ink area to its perimeter (4-neighbour edge count) scaled by the
    text band height so the measure is size-independent. Conservative threshold
    — only clear weight reads as bold (false negatives are cheaper than wrongly
    bolding regular text)."""
    import numpy as np

    if fg_mask is None or not fg_mask.any():
        return False
    area = float(fg_mask.sum())
    h = fg_mask.shape[0]
    if h < 4 or area < h:  # too small / too little ink to judge
        return False

    # Perimeter ≈ count of fg pixels that touch a non-fg 4-neighbour.
    m = fg_mask
    up = np.zeros_like(m); up[1:, :] = m[:-1, :]
    dn = np.zeros_like(m); dn[:-1, :] = m[1:, :]
    lf = np.zeros_like(m); lf[:, 1:] = m[:, :-1]
    rt = np.zeros_like(m); rt[:, :-1] = m[:, 1:]
    interior = m & up & dn & lf & rt
    perim = float((m & ~interior).sum())
    if perim <= 0:
        return False

    # Mean stroke "radius" ~ area / perimeter (in px). Normalize by text height:
    # bold faces run thicker relative to their height than regular ones.
    # Threshold calibrated on Liberation Sans/Serif at 44px: regular measures
    # ~0.031–0.035, bold ~0.045–0.051, so 0.040 sits cleanly between with a
    # small bias toward NOT bolding (false negatives are cheaper than wrongly
    # bolding regular text).
    thickness = (area / perim) / float(h)
    return thickness > 0.040


def _estimate_italic(fg_mask) -> bool:
    """ITALIC test by SHEAR CORRELATION: italic glyphs share one consistent
    slant, so de-shearing the ink by the right angle lines vertical strokes up
    into tall, narrow columns. We search candidate shears and pick the one that
    most CONCENTRATES the column ink profile (max sum-of-squares of the
    normalized per-column counts); upright text peaks at ~0 shear, italics at a
    clearly non-zero one.

    Calibrated on Liberation 44px (`x' = x - s*(y - cy)`, image y-down): regular
    AND bold land in |s| <= 0.025 for both serif and sans, while italics land at
    s in [-0.15, -0.25]. So a right-leaning italic shows up as s <= -0.08, a
    clean separation with wide margin. Far more reliable than a centroid-shift
    test (which the original text's own glyph asymmetry swamped)."""
    import numpy as np

    if fg_mask is None or not fg_mask.any():
        return False
    h, w = fg_mask.shape
    if h < 10 or w < 8:
        return False

    ys, xs = np.nonzero(fg_mask)
    if xs.size < 20:
        return False
    cy = h / 2.0

    best_s = 0.0
    best_score = -1.0
    # Shear is px-shift per pixel of vertical distance; +/-0.5 covers any real
    # italic (~12-20°). 41 steps ≈ 0.025 resolution — enough to separate
    # upright (~0) from italic (~-0.2) without being costly on a small crop.
    for s in np.linspace(-0.5, 0.5, 41):
        xx = np.round(xs - s * (ys - cy)).astype(np.int64)
        xx -= xx.min()
        prof = np.bincount(xx).astype(np.float64)
        tot = prof.sum()
        if tot <= 0:
            continue
        prof /= tot
        score = float((prof * prof).sum())  # higher ⇒ ink in fewer columns
        if score > best_score:
            best_score = score
            best_s = float(s)

    # Right-leaning italics de-shear best at a clearly negative slant.
    return best_s <= -0.08


def _estimate_serif(fg_mask) -> bool:
    """VERY light serif cue (the WEAKEST of the three signals): serif faces add
    thin horizontal ink — feet / top serifs — in the extreme top & bottom rows
    of the band, so the edge-row ink coverage runs higher relative to the middle
    than for a sans face. We compare the mean coverage of the top+bottom 16% of
    rows to the middle.

    HONEST CAVEAT: there is NO text-independent absolute threshold here — on
    Liberation 44px the serif/sans edge-ratio gap is real WITHIN one string
    (serif ≈ 1.4–2.1× the sans ratio) but the absolute value swings with the
    text (a sans 'Main Street' edge-ratio can exceed a serif 'document' one).
    So this only fires on a STRONG spike and the DEFAULT is sans; most serif
    regions will be (safely) rendered sans rather than risk wrongly serifing
    sans text. Exact family identification would need font recognition (not
    feasible here)."""
    import numpy as np

    if fg_mask is None or not fg_mask.any():
        return False
    h = fg_mask.shape[0]
    if h < 12:
        return False
    rows = fg_mask.mean(axis=1)  # per-row ink coverage
    band = max(1, int(round(h * 0.16)))
    if h <= 2 * band:
        return False
    edge = float(np.concatenate([rows[:band], rows[-band:]]).mean())
    middle = float(rows[band:-band].mean())
    if middle <= 1e-6:
        return False
    # Only a pronounced edge-row spike (feet+serifs much inkier than the body)
    # flips to serif; tuned high so sans text is not mislabeled.
    return (edge / middle) > 2.2


def _ensure_contrast(ink, bg_dark: bool):
    """Guarantee the rendered text is READABLE. Faithfully sampling the original
    ink reproduces faint pencil/low-contrast writing, which is unreadable after
    re-render — so when the sampled ink is too close to the background luminance
    we snap it to near-black (light bg) / near-white (dark bg)."""
    try:
        r, g, b = ink
    except Exception:
        return (235, 235, 235) if bg_dark else (24, 24, 24)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if bg_dark:
        return ink if lum >= 150 else (235, 235, 235)
    return ink if lum <= 110 else (24, 24, 24)


def _analyze_region(orig_img, box) -> dict:
    """Analyze the ORIGINAL (pre-inpaint) image crop for `box` and return the
    inferred render style:
        {"klass": "sans"|"serif", "bold": bool, "italic": bool,
         "ink": (r,g,b), "bg_dark": bool, "ink_confident": bool}

    `klass` is only ever "sans" or "serif" here — the font registry also carries
    a "mono" family, but monospace can't be reliably inferred from a single crop
    (it needs per-glyph width-uniformity analysis), so we don't guess it; serif
    is itself a weak signal that defaults to sans (see `_estimate_serif`).

    All best-effort heuristics on the crop's ink; any sub-estimate that can't
    decide returns its safe default (sans / not-bold / not-italic / black-or-
    white ink). NEVER raises — on any error returns a plain sans black/white
    style so rendering always proceeds. `orig_img` MUST be the original image
    (the ink is still present there); the inpainted copy has the ink erased."""
    import numpy as np

    try:
        x0, y0, x1, y1 = box
        crop = np.asarray(orig_img.crop((x0, y0, x1, y1)))
        fg_mask, ink, bg_dark, confident = _ink_mask_and_color(crop)

        # ORIGINAL text pixel height (vertical extent of the ink) — drives the
        # render font size so the translation matches the SOURCE size instead of
        # ballooning to fill a loose bounding box.
        ink_h = 0
        try:
            rows = np.where(fg_mask.any(axis=1))[0]
            if len(rows):
                ink_h = int(rows[-1] - rows[0] + 1)
        except Exception:
            ink_h = 0

        bold = _estimate_bold(fg_mask)
        italic = _estimate_italic(fg_mask)
        serif = _estimate_serif(fg_mask)
        klass = "serif" if serif else "sans"

        if not confident:
            # Couldn't cleanly separate ink from background → don't trust the
            # sampled color OR the mask's bg_dark guess (a uniform/low-contrast
            # crop can wrongly report bg_dark, yielding WHITE ink on a light page
            # = invisible text). Decide light/dark from the reliable luma of the
            # crop instead, then fall back to high-contrast black/white. Style
            # estimates from a muddy mask are also unreliable, so keep them off.
            bg_dark = _bg_is_dark(orig_img, box)
            ink = (245, 245, 245) if bg_dark else (16, 16, 16)
            bold = False
            italic = False
            klass = "sans"
        else:
            # Faithful sampling reproduces faint pencil/low-contrast ink, which
            # is unreadable after re-render — guarantee a readable contrast.
            ink = _ensure_contrast(ink, bg_dark)

        return {
            "klass": klass,
            "bold": bold,
            "italic": italic,
            "ink": ink,
            "ink_h": ink_h,
            "bg_dark": bg_dark,
            "ink_confident": confident,
        }
    except Exception:
        logger.debug("translate-image: region style analysis failed", exc_info=True)
        dark = False
        try:
            dark = _bg_is_dark(orig_img, box)
        except Exception:
            pass
        return {
            "klass": "sans",
            "bold": False,
            "italic": False,
            "ink": (245, 245, 245) if dark else (16, 16, 16),
            "bg_dark": dark,
            "ink_confident": False,
        }


def _wrap_to_width(draw, text: str, font, max_w: int) -> list[str]:
    """Greedy word-wrap `text` so each line's rendered width <= max_w. A
    single word longer than max_w is kept on its own line (no mid-word
    breaking — the size search will shrink the font instead)."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if _text_w(draw, trial, font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _text_w(draw, text: str, font) -> int:
    """Rendered pixel width of a single line (PIL textbbox; left-anchored)."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _line_h(font) -> int:
    """Line height for the font (ascent + descent)."""
    try:
        a, d = font.getmetrics()
        return a + d
    except Exception:
        return font.size


def _avail_height(box, all_boxes) -> int:
    """Vertical room a translation may use = box height + whitespace BELOW it
    (down to the nearest region whose x-range overlaps), capped at 2.5x the box
    height. Lets a longer translation flow into real whitespace instead of
    shrinking to nothing or overlapping the next region."""
    x0, y0, x1, y1 = box
    box_h = max(1, y1 - y0)
    bw = max(1, x1 - x0)
    below_top = None
    for b in all_boxes:
        if b[0] == x0 and b[1] == y0 and b[2] == x1 and b[3] == y1:
            continue
        # strictly below + horizontally overlapping this box
        if b[1] >= y1 - 0.3 * box_h and min(x1, b[2]) - max(x0, b[0]) > 0.3 * bw:
            if below_top is None or b[1] < below_top:
                below_top = b[1]
    if below_top is None:
        avail = int(2.5 * box_h)
    else:
        avail = max(box_h, int(below_top - y0 - 2))
    return min(avail, int(2.5 * box_h))


def _synth_container(box, all_boxes, iw: int, ih: int) -> tuple:
    """A fallback 'button' area for a compact label when no real pill/border was
    detected: pad the original box symmetrically into the free space on its row so
    a longer translation can be centred + shrunk to fit inside it (never spilling
    past where the button visually is), without crossing a neighbouring region or
    the image edge."""
    x0, y0, x1, y1 = box
    bw = x1 - x0
    bh = y1 - y0
    want = int(bw * 0.6) + bh           # modest horizontal room, scaled to size
    left_cap, right_cap = x0 - want, x1 + want
    for b in all_boxes:
        if tuple(b) == tuple(box):
            continue
        same_row = min(y1, b[3]) - max(y0, b[1]) > 0.3 * bh
        if not same_row:
            continue
        if b[2] <= x0:                  # neighbour on the left
            left_cap = max(left_cap, b[2] + 2)
        if b[0] >= x1:                  # neighbour on the right
            right_cap = min(right_cap, b[0] - 2)
    nx0 = max(0, left_cap)
    nx1 = min(iw, right_cap)
    pad_v = int(bh * 0.3) + 2
    ny0 = max(0, y0 - pad_v)
    ny1 = min(ih, y1 + pad_v)
    return (nx0, ny0, nx1, ny1)


def _slot_height(box, all_boxes) -> int:
    """Vertical room a HANDWRITING line may use WITHOUT colliding with the next
    line in its column: the distance from this box's TOP to the top of the nearest
    box below it that horizontally overlaps (same column). Unlike `_avail_height`,
    a box counts as 'below' when its TOP is lower (not its bottom) — so a tall,
    overlapping multi-line detection box still yields a BOUNDED slot, guaranteeing
    the rendered (top-aligned) line can't overlap the next on a dense page. Falls
    back to the box's own height when nothing follows in the column."""
    x0, y0, x1, y1 = box
    bw = max(1, x1 - x0)
    nxt = None
    for b in all_boxes:
        if b[0] == x0 and b[1] == y0 and b[2] == x1 and b[3] == y1:
            continue
        if b[1] > y0 and (min(x1, b[2]) - max(x0, b[0])) > 0.3 * bw:
            if nxt is None or b[1] < nxt:
                nxt = b[1]
    if nxt is None:
        return max(8, y1 - y0)
    return max(8, nxt - y0 - 2)


def _orig_line_count(parts) -> int:
    """How many distinct text ROWS the original region spanned (its parts). Used
    to preserve the original line structure: a label that was ONE line should
    stay one line after translation rather than wrapping + flowing downward."""
    if not parts:
        return 1
    centers = sorted(((p[1] + p[3]) / 2.0, p[3] - p[1]) for p in parts)
    avg_h = sum(h for _c, h in centers) / len(centers)
    tol = max(2.0, 0.6 * avg_h)
    rows = 1
    prev = centers[0][0]
    for c, _h in centers[1:]:
        if c - prev > tol:
            rows += 1
        prev = c
    return rows


def _color_close(a, b, tol: int = 24) -> bool:
    """True when two RGB tuples are within `tol` on every channel."""
    try:
        return all(abs(int(a[i]) - int(b[i])) <= tol for i in range(3))
    except Exception:
        return False


def _page_bg(np_img):
    """Dominant page background colour, sampled as the median of the four corner
    patches (corners are almost always background, not text/UI)."""
    import numpy as np

    h, w = np_img.shape[:2]
    s = max(4, min(h, w) // 20)
    corners = [
        np_img[0:s, 0:s], np_img[0:s, w - s:w],
        np_img[h - s:h, 0:s], np_img[h - s:h, w - s:w],
    ]
    px = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    return tuple(int(c) for c in np.median(px, axis=0))


def _pill_bounds(np_img, box, page_bg):
    """If `box` sits inside a CONTAINED UI element (a button / pill / badge),
    return that element's inner rect (x0,y0,x1,y1) so the translation can be
    centred and fit INSIDE it instead of flowing past its edge. Handles BOTH:
      * FILLED pills — the surround is a uniform colour distinct from the page;
      * OUTLINED/ghost buttons — the surround is the page colour but a thin
        border stroke encloses the label within a short distance.
    Returns None for plain text on the page (no container) or a textured
    surround. All scans are capped so a mis-detection can't run across the
    image."""
    import numpy as np

    h, w = np_img.shape[:2]
    x0, y0, x1, y1 = box
    bh = max(1, y1 - y0)
    bw = max(1, x1 - x0)
    cap_v = int(bh * 1.6) + 8          # buttons have modest vertical padding
    cap_h = int(bw * 1.0) + int(bh * 2.0) + 8

    def _line(i, horiz):
        sl = np_img[i, x0:x1] if horiz else np_img[y0:y1, i]
        if sl.size == 0:
            return None
        return tuple(int(c) for c in np.median(sl, axis=0))

    med, std = _border_ring(np_img, x0, y0, x1, y1, t=5)

    # --- FILLED pill: ring uniform AND distinct from the page background -------
    # `std` is now the per-channel spatial std (see `_border_ring`), so a solid
    # COLOURED pill reads as uniform; <=26 also tolerates a mild fill gradient.
    if (med is not None and std is not None and std <= 26
            and not _color_close(med, page_bg, tol=22)):
        def _scan_fill(start, stop, step, horiz):
            ext, i = 0, start
            while i != stop:
                px = _line(i, horiz)
                if px is None or not _color_close(px, med, tol=28):
                    break
                ext += 1; i += step
            return ext
        up = _scan_fill(y0 - 1, max(-1, y0 - 1 - cap_v), -1, True)
        down = _scan_fill(y1, min(h, y1 + cap_v), 1, True)
        left = _scan_fill(x0 - 1, max(-1, x0 - 1 - cap_h), -1, False)
        right = _scan_fill(x1, min(w, x1 + cap_h), 1, False)
        if left >= 2 or right >= 2:    # real horizontal padding ⇒ a pill
            return (max(0, x0 - left), max(0, y0 - up),
                    min(w, x1 + right), min(h, y1 + down))
        return None

    # --- OUTLINED button: page-coloured interior bounded by a THIN border ------
    # Walk outward over page-coloured pixels; the first run that deviates from the
    # page colour is a border candidate. Confirm it's a THIN stroke (page colour
    # resumes within a few px beyond it) so adjacent TEXT/graphics aren't taken
    # for a border, and return the stroke colour. Require a confirmed stroke on
    # ALL FOUR sides AND that the four stroke colours match each other — a real
    # button border is one uniform colour all the way round, which rejects random
    # noise / a word that merely has neighbours. `bg_tol=14` is tight enough that
    # JPEG noise/gradient stays "page" yet a faint grey border (Δ~26) registers.
    def _scan_border(start, stop, step, horiz):
        i = start
        while i != stop:
            px = _line(i, horiz)
            if px is None:
                return None
            if not _color_close(px, page_bg, tol=14):
                j, run = i, 0          # confirm the non-page run is thin
                while j != stop and run <= 7:
                    pj = _line(j, horiz)
                    if pj is None:
                        return None
                    if _color_close(pj, page_bg, tol=14):
                        return (i - step, px)  # (inside edge, stroke colour)
                    run += 1; j += step
                return None            # thick non-page run ⇒ not a border
            i += step
        return None

    sides = [
        _scan_border(x0 - 1, max(-1, x0 - 1 - cap_h), -1, False),
        _scan_border(x1, min(w, x1 + cap_h), 1, False),
        _scan_border(y0 - 1, max(-1, y0 - 1 - cap_v), -1, True),
        _scan_border(y1, min(h, y1 + cap_v), 1, True),
    ]
    if any(s is None for s in sides):  # not a 4-sided enclosure ⇒ not a button
        return None
    (li, lc), (ri, rc), (ti_, tc), (bi, bc) = sides
    cols = [lc, rc, tc, bc]            # the four strokes must be ONE border colour
    if not all(_color_close(cols[0], c, tol=30) for c in cols[1:]):
        return None
    return (max(0, min(li, x0)), max(0, min(ti_, y0)),
            min(w, max(ri, x1)), min(h, max(bi, y1)))


def _pick_font_path(text: str, style: Optional[dict]) -> Optional[str]:
    """Resolve the TTF path for `text` + `style` the same way for both the
    measurement (`_fits_in_box`) and the draw (`_fit_and_draw`): script font for
    non-Latin glyphs first, then the handwriting face for handwritten regions,
    else the class/weight/slant-aware Latin font."""
    bold = bool((style or {}).get("bold", False))
    italic = bool((style or {}).get("italic", False))
    klass = (style or {}).get("klass", "sans")
    handwriting = bool((style or {}).get("handwriting", False))
    fp = _script_font(text, bold)
    if fp is None and handwriting:
        fp = _handwriting_font()
    if fp is None:
        fp = _resolve_font(klass, bold, italic)
    return fp


def _fit_and_draw(
    draw,
    text: str,
    box,
    fill,
    style: Optional[dict] = None,
    max_h: Optional[int] = None,
    align: str = "left",
    valign: str = "baseline",
    clamp_box: bool = True,
) -> None:
    """Pick the LARGEST size of the STYLE-MATCHED bundled font whose word-wrapped
    `text` fits the box WIDTH and HEIGHT, then draw it so it occupies the same
    spot as the original: LEFT edge at the box's left, and the FIRST line's
    BASELINE aligned to where the original text's baseline sat (near the box
    bottom, allowing for the descender) when the text is a single line.

    `style` is the dict from `_analyze_region` ({klass,bold,italic,…}); its
    family/weight/slant select the TTF via `_resolve_font` so the render matches
    the original's serif-ness, bold, and italic. Falls back to a regular sans
    TTF (then the PIL bitmap font) when the matched file is unavailable.

    Steps the size down from a height-based ceiling until the wrapped block fits
    (or hits a small floor). Single-line text is baseline-aligned to the
    original; multi-line text (the translation wrapped wider than the original)
    is top-aligned to the box so it doesn't overflow."""
    from PIL import ImageFont

    x0, y0, x1, y1 = box
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)
    # Total vertical budget: the box plus any whitespace below it (so a longer
    # translation flows into real empty space instead of shrinking/overlapping).
    # `clamp_box=False` lets the budget go BELOW the box height — needed when a
    # dense handwriting line must shrink into a tight slot to avoid overlapping the
    # next line (a tall, overlapping detection box would otherwise force overlap).
    if max_h is None:
        max_h = box_h
    max_h = max(int(max_h), box_h if clamp_box else 8)

    if not text.strip():
        return

    klass = (style or {}).get("klass", "sans")
    bold = bool((style or {}).get("bold", False))
    italic = bool((style or {}).get("italic", False))
    handwriting = bool((style or {}).get("handwriting", False))
    font_path = _pick_font_path(text, {"klass": klass, "bold": bold,
                                       "italic": italic, "handwriting": handwriting})

    def _make(sz):
        if not font_path:
            return ImageFont.load_default()
        # Prefer RAQM layout — proper shaping + bidi for Arabic/Hebrew/Indic, and
        # harmless for CJK/Latin. Falls back to the basic layout when Pillow has
        # no libraqm, then to the bitmap default.
        try:
            return ImageFont.truetype(
                font_path, sz, layout_engine=ImageFont.Layout.RAQM
            )
        except Exception:
            try:
                return ImageFont.truetype(font_path, sz)
            except Exception:
                return ImageFont.load_default()

    # Ceiling = the ORIGINAL text height (measured ink extent) so the
    # translation renders at the SOURCE size instead of ballooning to fill a
    # loose box — the #1 cause of the inconsistent giant/tiny mix. +15% headroom
    # (font size runs a bit larger than cap height). Fall back to a fraction of
    # the box height when the ink couldn't be measured. The loop still shrinks
    # below this when a long translation would overflow the width.
    ink_h = int((style or {}).get("ink_h", 0) or 0)
    if ink_h >= 6:
        hi = max(6, min(int(ink_h * 1.15), max_h, 200))
    else:
        hi = max(6, min(int(box_h * 0.72), 200))
    lo = 6
    chosen_font = None
    chosen_lines: list[str] = []
    line_gap = 0

    size = hi
    while size >= lo:
        font = _make(size)

        lines = _wrap_to_width(draw, text, font, box_w)
        if not lines:
            return
        # Widest line must fit the width.
        widest = max(_text_w(draw, ln, font) for ln in lines)
        lh = _line_h(font)
        gap = max(1, int(lh * 0.12))
        total_h = len(lines) * lh + (len(lines) - 1) * gap
        if widest <= box_w and total_h <= max_h:
            chosen_font = font
            chosen_lines = lines
            line_gap = gap
            break
        # When using the (non-scalable) default bitmap font, truetype failed
        # entirely — don't spin the whole range; just take what we have.
        if font_path is None:
            chosen_font = font
            chosen_lines = lines[:1]  # one line; bitmap font can't shrink
            line_gap = gap
            break
        size -= 2

    if chosen_font is None:
        # Nothing fit even at the floor — render at the floor size, clipped.
        chosen_font = _make(lo)
        chosen_lines = _wrap_to_width(draw, text, chosen_font, box_w) or [text]
        line_gap = max(1, int(_line_h(chosen_font) * 0.12))

    lh = _line_h(chosen_font)
    n_lines = len(chosen_lines)
    block_h = n_lines * lh + (n_lines - 1) * line_gap

    # Vertical placement:
    #  * single line → BASELINE-align to the original. The original baseline sits
    #    near the box bottom minus the font's descender, so the glyph cell's TOP
    #    lands at (box_bottom - descent - ascent). This makes the new text sit
    #    exactly where the original line did instead of floating mid-box.
    #  * multi-line  → the translation wrapped to more lines than the original;
    #    top-align (clamped) so it stays inside the box.
    try:
        ascent, descent = chosen_font.getmetrics()
    except Exception:
        ascent, descent = lh, 0

    if valign == "center":
        # Centre the whole block in the box — used for pills/buttons so the label
        # sits dead-centre in the container regardless of length.
        y = y0 + max(0, (box_h - block_h) // 2)
    elif valign == "top":
        y = y0
    elif n_lines == 1 and block_h <= box_h:
        # top of the single glyph cell = bottom - descent - ascent.
        y = y1 - descent - ascent
        # Keep it inside the box top if the box is shorter than the cell.
        y = max(y0, y)
    else:
        # Multi-line / extends past the original box: TOP-align at the box top so
        # the block flows DOWNWARD into the whitespace budget (max_h) instead of
        # centering (which would push the first line up over the line above).
        y = y0

    for ln in chosen_lines:
        if align == "center":
            lw = _text_w(draw, ln, chosen_font)
            lx = x0 + max(0, (box_w - lw) // 2)
        else:
            lx = x0
        # anchor="la" = left/ascent so y is the top of the glyph cell (consistent
        # line stacking). PIL anti-aliases TTF glyphs by default, so edges smooth.
        draw.text((lx, y), ln, font=chosen_font, fill=fill, anchor="la")
        y += lh + line_gap


def _page_pen_color(bg_dark: bool) -> tuple:
    """ONE clean, uniform PEN color for every handwriting line: a strong near-black
    on a light page (near-white on a dark page). Handwritten notes are usually
    faint pencil / pale ballpoint, so reproducing the SAMPLED ink reads as a
    washed-out grey — instead we render a single bold black ink so the translation
    looks like it was cleanly written on the paper and stays legible everywhere."""
    return (236, 236, 236) if bg_dark else (20, 20, 20)


def _fits_in_box(draw, text: str, box, style: Optional[dict]) -> bool:
    """True when `text`, wrapped to the box width at the ORIGINAL ink size, fits
    within the box height — i.e. it can be drawn in place (hybrid layout) without
    shrinking or flowing into neighbouring whitespace. Mirrors `_fit_and_draw`'s
    ceiling/wrap math so the predicate and the draw agree."""
    from PIL import ImageFont

    if not text.strip():
        return True
    x0, y0, x1, y1 = box
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)
    ink_h = int((style or {}).get("ink_h", 0) or 0)
    size = max(6, min(int(ink_h * 1.15), 200)) if ink_h >= 6 else max(6, int(box_h * 0.72))
    fp = _pick_font_path(text, style)
    try:
        font = ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    lines = _wrap_to_width(draw, text, font, box_w)
    if not lines:
        return True
    widest = max(_text_w(draw, ln, font) for ln in lines)
    lh = _line_h(font)
    gap = max(1, int(lh * 0.12))
    total_h = len(lines) * lh + (len(lines) - 1) * gap
    return widest <= box_w and total_h <= box_h


def _render_translations(inpainted, regions: list[dict], translations: list[str], orig_img=None):
    """Draw each translation back into its box on the inpainted image, matching
    the ORIGINAL ink's family/weight/slant/color and sitting in the original's
    spot. Returns the composited PIL RGB image. Region ↔ translation are
    index-aligned.

    `orig_img` is the PRE-INPAINT image (the original ink is still there) used to
    infer per-region style + ink color via `_analyze_region`; `inpainted` is the
    surface we draw on. When `orig_img` is None we degrade to the previous
    black/white-by-background behavior (sampling the inpainted surface)."""
    import numpy as np
    from PIL import ImageDraw

    draw = ImageDraw.Draw(inpainted)
    all_boxes = [r["box"] for r in regions]

    # One page-background sample + numpy view for pill detection (digital UI).
    orig_np = np.asarray(orig_img) if orig_img is not None else None
    page_bg = _page_bg(orig_np) if orig_np is not None else (255, 255, 255)

    # ONE clean, uniform BLACK pen for every handwriting line (white on a dark
    # page) so the note reads as a single bold, legible hand — not a faint grey
    # shade sampled per box.
    page_bg_dark = bool(_bg_is_dark(orig_img, (0, 0, min(orig_img.width, 8),
                                               min(orig_img.height, 8)))) \
        if orig_img is not None else False
    hw_pen = _page_pen_color(page_bg_dark)
    iw_img = orig_img.width if orig_img is not None else inpainted.width
    ih_img = orig_img.height if orig_img is not None else inpainted.height

    for r, translated in zip(regions, translations):
        if r.get("skip"):
            continue  # brand/logo/code or unchanged — original ink kept
        text = (translated or "").strip()
        if not text:
            continue
        box = r["box"]
        hw = bool(r.get("handwriting", False))
        # Style/ink/ink_h come from the FIRST constituent line (a tight original
        # text box), not the merged union (which spans whitespace/multiple lines)
        # — so the font matches the source size + ink, not the block geometry.
        rep = r.get("parts", [box])[0]

        if orig_img is not None:
            style = _analyze_region(orig_img, rep)
            fill = style["ink"]
        else:
            dark_bg = _bg_is_dark(inpainted, box)
            fill = (245, 245, 245) if dark_bg else (16, 16, 16)
            style = {"klass": "sans", "bold": False, "italic": False}

        if hw:
            # Handwriting: render in the clean handwriting face with ONE uniform
            # readable pen color shared by the whole page (computed once above), so
            # the note reads as a single consistent hand instead of a shade per box.
            style = dict(style)
            style["handwriting"] = True
            style["bold"] = False
            style["italic"] = False
            fill = hw_pen

        # Pills/buttons (digital UI): the label sits on a contained fill, so a
        # longer translation must shrink + CENTRE inside it, never flow past the
        # edge. Only meaningful for non-handwriting pages.
        pill = None
        if orig_np is not None and not hw:
            try:
                pill = _pill_bounds(orig_np, box, page_bg)
            except Exception:
                pill = None

        if hw:
            # Hybrid placement bounded by the line's SLOT (top of this box → top of
            # the next line in its column), top-aligned. The slot guarantees the
            # rendered line can't overlap the next one even when detection boxes are
            # tall and overlapping (the dense column). It renders at the original
            # ink size when the slot allows, and shrinks into the slot otherwise —
            # so the page stays clean and on-page with the list order preserved.
            max_h = _slot_height(box, all_boxes)
            _fit_and_draw(draw, text, box, fill, style, max_h=max_h,
                          valign="top", clamp_box=False)
        elif pill is not None:
            px0, py0, px1, py1 = pill
            _fit_and_draw(draw, text, pill, fill, style,
                          max_h=(py1 - py0), align="center", valign="center")
        elif (len(text.split()) <= 6
              and _orig_line_count(r.get("parts", [box])) == 1):
            # Borderless button / CTA with no detected container: constrain to a
            # synthesized button area and shrink-to-fit + centre, so a longer
            # translation never spills past where the label visually sits.
            cont = _synth_container(box, all_boxes, iw_img, ih_img)
            _fit_and_draw(draw, text, cont, fill, style,
                          max_h=(cont[3] - cont[1]), align="center", valign="center")
        else:
            # Let a longer translation flow into the whitespace beneath the box.
            max_h = _avail_height(box, all_boxes)
            _fit_and_draw(draw, text, box, fill, style, max_h=max_h)

    return inpainted


def _encode_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ocr_stage(raw_bytes: bytes, *, four_way: bool | None = None, on_regions=None):
    """Blocking stage 1: decode → orientation detect → best-orientation OCR
    regions. Returns (img, best_k, best_img, regions) where:
      * `img`       — the loaded full-res working image in NATIVE orientation
                      (returned unchanged when no text is found);
      * `best_k`    — the chosen orientation (rotate(-90*best_k) was applied);
      * `best_img`  — the FULL-RES working image IN that best orientation (the
                      frame `regions` live in — what inpaint/render draw on);
      * `regions`   — pixel axis-aligned regions [{box,text}] in `best_img`,
                      scaled UP from the OCR-downscaled frame (empty list when
                      no text in any orientation).

    SPEED: OCR runs on a copy downscaled to `_OCR_MAX_SIDE` so a big phone photo
    doesn't pay full-res cost per pass, and Florence's decode is bounded (token
    cap + greedy) in `_regions_pixels`.

    `four_way` controls the orientation sweep:
      * False (the STREAMING default) → read ONLY the 0° orientation. One upright
        Florence pass. This is the fast, never-hanging path: on a dense page the
        0° early-exit can't fire (handwriting reads low-confidence), and the
        4-way would then run all four full-page passes → minutes → the FE's
        "warming up" feels infinite. Most photos are upright, so 0°-only is the
        right default here; sideways images are the trade-off.
      * True (the NON-STREAMING route) → the full 4-way `_ocr_best_orientation`
        sweep, EARLY-EXITED when the 0° read is clearly upright
        (`_ORIENT_EARLY_EXIT_SCORE`).
    Defaults to `_STREAM_FOUR_WAY_DEFAULT` (False) when not specified.

    `on_regions(n)` — optional callback invoked with the detected region COUNT
    the moment the winning orientation's regions are known (before any
    translation), so the streaming caller can flush a progress line and the FE
    bar leaves the indeterminate "warming up" state quickly.

    Factored out of the old `_run_pipeline` so BOTH the non-streaming and the
    streaming paths share ONE OCR pass (one Florence load, identical region
    output for a given orientation)."""
    if four_way is None:
        four_way = _STREAM_FOUR_WAY_DEFAULT

    img, _scale = _load_rgb(raw_bytes)

    # OCR on a downscaled copy (fast); boxes get scaled back up below. When the
    # working image is already <= _OCR_MAX_SIDE this returns `img` itself.
    ocr_img = _ocr_downscaled(img)

    # Per-orientation reader: bounded Florence <OCR_WITH_REGION>, returning the
    # pixel regions as payload plus Florence's mean-token confidence (so the
    # scorer rejects the hallucinated 180° read AND drives the early-exit).
    def _read(rot_img):
        regions, _riw, _rih, conf = _regions_pixels(rot_img, return_confidence=True)
        text = "\n".join(r["text"] for r in regions)
        return text, len(regions), conf, regions

    if four_way:
        best_k, best_regions, best_ocr_img, scores = _ocr_best_orientation(
            ocr_img, _read, early_exit_score=_ORIENT_EARLY_EXIT_SCORE
        )
        logger.info(
            "translate-image: best orientation k=%d (%d deg CW); scores=%s",
            best_k, 90 * best_k, [round(s, 1) for s in scores],
        )
        # `_ocr_best_orientation` ranks by an alnum/region score, not Florence's
        # log-prob confidence — re-read the winning frame once for the conf the
        # handwriting gate needs (this route is the rare non-streaming one).
        try:
            _r2, _iw2, _ih2, conf = _regions_pixels(
                best_ocr_img, return_confidence=True
            )
        except Exception:
            conf = None
    else:
        # SINGLE orientation (0° only) — the fast, never-hanging streaming
        # default. One Florence pass on the upright frame; no sweep.
        _text, _n, conf, best_regions = _read(ocr_img)
        best_k, best_ocr_img = 0, ocr_img
        logger.info(
            "translate-image: single-orientation OCR (0deg); %d regions; conf=%s",
            len(best_regions or []), None if conf is None else round(conf, 3),
        )

    best_regions = best_regions or []

    # Progress: announce the detected region count as soon as it's known so the
    # FE leaves the indeterminate "warming up" state (before any translation).
    if on_regions is not None:
        try:
            on_regions(len(best_regions))
        except Exception:
            logger.debug("translate-image: on_regions callback raised", exc_info=True)

    # Rebuild the FULL-RES frame in the winning orientation (the surface
    # inpaint/render draw on) and scale the OCR-frame boxes up into it. The OCR
    # copy and the working image share the native orientation, so after applying
    # the SAME rotation k their axes line up and a per-axis scale factor maps
    # boxes exactly. best_ocr_img is the OCR copy already rotated to k.
    if best_k == 0:
        best_img = img
    else:
        best_img = img.rotate(-90 * best_k, expand=True)

    if not best_regions:
        return img, best_k, best_img, []

    ow, oh = best_ocr_img.width, best_ocr_img.height
    fw, fh = best_img.width, best_img.height
    sx = fw / float(ow) if ow else 1.0
    sy = fh / float(oh) if oh else 1.0
    regions = _scale_regions(best_regions, sx, sy, fw, fh)
    # FAST Florence-only recognizer: the detected boxes carry Florence's own read.
    # The CPU TrOCR handwriting re-read is intentionally OUT of this hot path.
    # `_trocr_rewrite_regions` stays defined but is no longer called.
    #
    # HANDWRITING: a low Florence confidence means cursive it reads poorly
    # (meaning-changing misreads). Re-read each RAW single-line crop with the
    # context-aware VL BEFORE merging — merged blocks span several lines, and a
    # multi-line crop makes the VL bleed into neighbours and loop. Reading single
    # lines first keeps each crop clean; the merge then recomposes the CORRECTED
    # lines. Gated (auto/on/off + conf threshold); degrades to Florence if absent.
    handwriting = _is_handwriting(conf)
    if handwriting:
        logger.info(
            "translate-image: handwriting detected (conf=%s) — VL re-read",
            None if conf is None else round(conf, 3),
        )
        regions = _vl_rewrite_regions(best_img, regions)

    # Merge the (now VL-corrected) per-line/per-phrase boxes into logical lines +
    # blocks so the translator gets sentence context and the renderer recomposes
    # the layout (the single point all three translate paths share).
    regions = _merge_regions(regions)
    if handwriting:
        for r in regions:
            r["handwriting"] = True   # propagate the flag onto the merged blocks

    return img, best_k, best_img, regions


def _compose_png(best_img, best_k: int, regions: list[dict], translations: list[str]) -> bytes:
    """Blocking stage 3+4+5: inpaint the originals away, render the
    translations into their boxes (ALL in the best orientation), rotate the
    finished composite BACK to the user's original orientation, and encode PNG.
    Shared by the non-streaming and streaming paths so the rendered output is
    identical."""
    # Skip regions whose translation didn't change (already in the target
    # language, or a brand/code token the engine echoed) — leave the original
    # ink rather than re-render an identical string in a different font.
    for r, t in zip(regions, translations):
        if not r.get("skip") and (t or "").strip().lower() == (r.get("text") or "").strip().lower():
            r["skip"] = True
    inpainted = _inpaint_erase(best_img, regions)
    # Pass `best_img` (the ORIGINAL, pre-erase) so per-region style + ink color
    # are inferred from the real ink; we DRAW on `inpainted`.
    composited = _render_translations(inpainted, regions, translations, orig_img=best_img)
    # Orientation k was applied as rotate(-90*k); undo with rotate(+90*k).
    if best_k != 0:
        composited = composited.rotate(90 * best_k, expand=True)
    return _encode_png(composited)


def _run_pipeline(raw_bytes: bytes, target: str) -> tuple[bytes, str, str]:
    """Full blocking pipeline (runs in a thread). Returns (png_bytes,
    src_name, tgt_name).

    ORIENTATION-ROBUST: Florence is run on all four 90° rotations and the
    best-reading orientation is chosen (ocr.py's `_ocr_best_orientation`); the
    OCR → translate → inpaint → render all happen IN THAT ORIENTATION, then the
    finished composite is rotated BACK to the user's original orientation
    before returning. So sideways/upside-down text is read upright but the
    returned image is right-side-up for the user.

    When NO text regions are found in any orientation, returns the ORIGINAL
    (native-orientation) image re-encoded as PNG with src_name="" so the route
    can set X-Source-Lang="" and the FE messages "no text found". Raises
    RuntimeError when NLLB can't load (route → 503)."""
    # Non-streaming route keeps the full 4-way orientation sweep (this path is
    # not the one that hangs the FE — it returns a single image with no progress
    # UI). The streaming path defaults to single-orientation for speed.
    img, best_k, best_img, regions = _ocr_stage(raw_bytes, four_way=True)

    tgt_flores = _to_flores(target)
    tgt_name = _flores_name(tgt_flores)

    if not regions:
        # No text in ANY orientation — hand back the ORIGINAL-orientation image
        # unchanged, signal "no text" via an empty source language.
        return _encode_png(img), "", tgt_name

    # Translate every region (detect source ONCE). RuntimeError → 503.
    texts = [r["text"] for r in regions]
    translations, src_flores, tgt_flores = _translate_regions(texts, target)
    src_name = _flores_name(src_flores)
    tgt_name = _flores_name(tgt_flores)

    # Erase + render + rotate-back + encode.
    return _compose_png(best_img, best_k, regions, translations), src_name, tgt_name


@router.post("/{image_id}/translate-image")
async def image_translate_image(
    image_id: UUID,
    body: TranslateImageRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Translate the text INSIDE an owned image, in place (Google-Lens style),
    and return the new PNG.

    Owner-scoped on the image (404 for non-owners / missing rows) just like
    `/ocr`, `/translate`, and `/translate-stream`. The whole heavy pipeline
    (Florence OCR → NLLB translate → cv2 inpaint → PIL render) runs in a
    thread so the event loop stays responsive.

    Response is `image/png`; X-Source-Lang + X-Target-Lang headers carry the
    human language names for the FE to show "English → Spanish". When the
    image has no detectable text the original image is returned unchanged with
    X-Source-Lang="". 503 only when the NLLB translator can't be loaded.
    """
    image = await _load_owned_image(image_id, user, session)
    if image.category not in ("image", None):
        # Only rasters carry translatable in-image text. (Document/PDF
        # translation lives in the doc-translate path.) Soft-allow NULL
        # category for very old rows, mirroring /ocr.
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "In-image translation is only available for images.",
        )

    raw, _mime = await _read_image_bytes(image)
    try:
        png, src_name, tgt_name = await asyncio.to_thread(
            _run_pipeline, raw, body.target
        )
    except RuntimeError as exc:
        # NLLB intentionally unavailable (sentencepiece / weights missing).
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Translation model is not available in this deployment.",
        ) from exc
    except Exception as exc:
        logger.exception("translate-image: pipeline crashed for %s", image_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "In-image translation failed.",
        ) from exc

    return Response(
        content=png,
        media_type="image/png",
        headers={
            # Human names so the FE can show "English → Spanish". Source is
            # empty when no text was found (FE messages "no text found").
            "X-Source-Lang": src_name,
            "X-Target-Lang": tgt_name,
            # Let the browser/JS read the custom headers cross-origin.
            "Access-Control-Expose-Headers": "X-Source-Lang, X-Target-Lang",
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# STREAMING variant — the FE can show the translated text filling in LIVE
# behind the loader while the final image is still rendering.
#
# Same pipeline (4-way orientation → translate regions → inpaint → render →
# rotate back), but driven as an async NDJSON generator so each region's
# translated text is flushed the moment it's produced, and the finished PNG is
# sent as a base64 blob on the final line. All blocking work (OCR, each region
# translate, compose) runs via asyncio.to_thread so the event loop stays free.
# ---------------------------------------------------------------------------
def _b64_png(png: bytes) -> str:
    """Base64-encode PNG bytes for embedding in an NDJSON line."""
    return base64.b64encode(png).decode("ascii")


async def _translate_image_stream_gen(raw_bytes: bytes, target: str):
    """Async NDJSON generator for in-image translation. Yields, in order:

      * ``{"stage": "reading"}``               — OCR (Florence) has started
                                                 (flushed IMMEDIATELY);
      * ``{"i": 0, "n": <count>}``             — region count, the moment OCR
                                                 detects them, so the FE bar
                                                 leaves the indeterminate
                                                 "warming up" state;
      * ``{"i": k, "n": total, "text": "…"}``  — ONE line per region as its
                                                 translation lands (so the
                                                 "writing" fills in live);
      * ``{"done": true, "image_b64": "<b64 PNG>", "source_lang": "<name>",
            "target_lang": "<name>"}``         — the finished composited PNG;
      * ``{"done": true, "image_b64": "<b64 of ORIGINAL>", "source_lang": ""}``
                                                 — when NO text was found (FE
                                                 messages "no text found");
      * ``{"error": "…"}``                     — on ANY failure or timeout.

    NEVER hangs and NEVER raises out of the generator — every failure (NLLB
    unavailable, an OCR stage that exceeds `_OCR_DEADLINE_S`, any crash) becomes a
    terminal ``{"error": …}`` line so the HTTP stream always terminates cleanly
    instead of leaving the FE loader spinning forever. The OCR stage reads a
    SINGLE orientation with a bounded Florence decode (see `_ocr_stage`), so it's
    fast by construction; the `asyncio.wait_for` deadline is the hard safety net
    on top of that. Each line is a single JSON object terminated by ``\\n``
    (NDJSON)."""
    try:
        tgt_flores = _to_flores(target)
        tgt_name = _flores_name(tgt_flores)

        # 1) OCR — announce IMMEDIATELY (FE shows "reading"), then run the single
        #    bounded Florence pass off the event loop under a HARD deadline. On
        #    timeout asyncio.wait_for cancels the await and we emit a terminal
        #    error line — the stream can NEVER hang here. (The worker thread the
        #    cancelled to_thread leaves running finishes on its own quickly: the
        #    decode is token-capped and single-orientation.)
        yield json.dumps({"stage": "reading"}) + "\n"
        try:
            img, best_k, best_img, regions = await asyncio.wait_for(
                asyncio.to_thread(_ocr_stage, raw_bytes),
                timeout=_OCR_DEADLINE_S,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "translate-image-stream: OCR stage exceeded %.0fs deadline",
                _OCR_DEADLINE_S,
            )
            yield json.dumps(
                {
                    "error": "Couldn't read the image in time — try a clearer or "
                    "printed-text image."
                }
            ) + "\n"
            return

        # Progress: announce the detected region count the instant OCR returns so
        # the FE bar leaves the indeterminate "warming up" state and shows it's
        # working (even before the first translation lands). `text:""` is
        # REQUIRED — the FE's region-line dispatcher only routes a line to
        # `onRegion` when both `i` (number) and `text` (string) are present; it
        # then skips the empty text (its `if (text && text.trim())` guard) so this
        # advances the bar WITHOUT rendering a spurious region.
        yield json.dumps({"i": 0, "n": len(regions), "text": ""}) + "\n"

        if not regions:
            # No text → hand back the ORIGINAL image and an empty source language
            # so the FE can say "no text found".
            png = await asyncio.to_thread(_encode_png, img)
            yield json.dumps(
                {"done": True, "image_b64": _b64_png(png), "source_lang": ""}
            ) + "\n"
            return

        # 2) Resolve source + target ONCE (detect over the joined region text),
        #    then translate region-by-region, flushing each as it lands.
        texts = [r["text"] for r in regions]
        (
            model,
            tokenizer,
            device,
            gen_kwargs,
            src_flores,
            tgt_flores,
        ) = await asyncio.to_thread(_prepare_translation, texts, target)
        src_name = _flores_name(src_flores)
        tgt_name = _flores_name(tgt_flores)

        total = len(texts)
        translations: list[str] = []
        for i, t in enumerate(texts):
            translated = await asyncio.to_thread(
                _translate_one_region, model, tokenizer, device, t, gen_kwargs
            )
            translations.append(translated)
            # One line per region so the FE writing fills in live.
            yield json.dumps({"i": i, "n": total, "text": translated}) + "\n"

        # 3) Erase + render + rotate-back + encode (off the event loop), then
        #    send the finished PNG + the language pair.
        png = await asyncio.to_thread(
            _compose_png, best_img, best_k, regions, translations
        )
        yield json.dumps(
            {
                "done": True,
                "image_b64": _b64_png(png),
                "source_lang": src_name,
                "target_lang": tgt_name,
            }
        ) + "\n"
    except RuntimeError as exc:
        # NLLB intentionally unavailable (sentencepiece / weights missing).
        logger.info("translate-image-stream: NLLB unavailable: %s", exc)
        yield json.dumps(
            {"error": "Translation model is not available in this deployment."}
        ) + "\n"
    except Exception:
        logger.exception("translate-image-stream: pipeline crashed")
        yield json.dumps({"error": "In-image translation failed."}) + "\n"


@router.post("/{image_id}/translate-image-stream")
async def image_translate_image_stream(
    image_id: UUID,
    body: TranslateImageRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Stream the in-image translation as NDJSON so the FE can show the
    translated text filling in LIVE behind the loader, then swap in the
    finished PNG.

    Same auth + owner gate as `/translate-image` (404 for non-owners / missing
    rows; 415 for non-image categories). The heavy pipeline (Florence OCR →
    NLLB translate → cv2 inpaint → PIL render) runs inside the async generator
    with each blocking step in a thread, so the event loop stays responsive
    while the stream flushes region-by-region. Errors surface as a terminal
    `{"error": …}` NDJSON line rather than an HTTP error, so a partial stream
    never hangs the loader.

    Line protocol (NDJSON, one JSON object per line):
      `{"stage":"reading"}` · `{"i":k,"n":total,"text":"…"}` (per region) ·
      `{"done":true,"image_b64":"<PNG>","source_lang":"…","target_lang":"…"}`
      (or `{"done":true,"image_b64":"<original>","source_lang":""}` for no
      text) · `{"error":"…"}` on failure.
    """
    image = await _load_owned_image(image_id, user, session)
    if image.category not in ("image", None):
        # Only rasters carry translatable in-image text. Mirrors the gate on
        # the non-streaming /translate-image route.
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "In-image translation is only available for images.",
        )

    raw, _mime = await _read_image_bytes(image)
    return StreamingResponse(
        _translate_image_stream_gen(raw, body.target),
        media_type="application/x-ndjson",
        # no-cache + X-Accel-Buffering:no so proxies/browsers don't buffer the
        # stream — the per-region lines paint as they arrive (mirrors
        # translate_stream.py). The language pair rides INSIDE the final NDJSON
        # line (not response headers), since the body streams before it's known.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
