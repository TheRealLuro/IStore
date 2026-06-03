"""Image OCR + translate endpoints (NEW, unwired).

Two routes, both authed + owner-scoped (mirrors backend/api/images.py's
`_load_owned_image` pattern):

  GET  /images/{id}/ocr        → run OCR over the image, return the
                                 extracted text plus per-line boxes
                                 (NORMALIZED 0..1 so the FE can overlay
                                 them on the rendered <img> regardless of
                                 object-fit scaling).
       Response:
         {
           "text": "full extracted text\\n...",
           "lines": [{"text": "...", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04}, ...],
           "width": 1280, "height": 720,         # source pixel size
           "engine": "florence2-ocr-with-region", # or "florence2-ocr", or
                                                  # "florence2-detect+trocr-handwritten"
                                                  # when handwriting was re-read
           "boxes": true                          # whether line boxes are present
         }

  POST /images/{id}/translate  → detect the source language of the
                                 supplied text and translate it to a
                                 target language.
       Body: {"text": "...", "target": "en"}
             target accepts either an ISO-639-1 code (en, es, zh, ar …)
             OR a FLORES-200 code (eng_Latn, spa_Latn, zho_Hans …);
             defaults to "eng_Latn". An empty/unknown target falls back
             to English.
       Response:
         {
           "source_lang": "Spanish",          # human name of detected src
           "target_lang": "English",          # human name of the target
           "original": "...",
           "translation": "...",
           "engine": "nllb-200",              # or "qwen2.5-instruct" (fallback)
           "src_lang": "spa_Latn",            # FLORES code actually used (NLLB)
           "tgt_lang": "eng_Latn",            # FLORES code actually used (NLLB)
           "note": "<engine / limitation string>"
         }

OCR ENGINE
----------
Reuses the EXISTING OCR dependency — Microsoft Florence-2 (loaded once via
`backend.vision.runtime.get_florence2`, the same model the summarizer's
`_ocr_image` uses). For boxes we issue the `<OCR_WITH_REGION>` task token
(the summarizer only uses bare `<OCR>`, which returns text with no
coordinates). Florence-2's processor returns `quad_boxes` (8-value polygon
per detected line, in absolute pixel coords) + `labels` (the line text); we
reduce each quad to an axis-aligned bounding box and normalize by the image
dimensions. No new dependency: Florence-2, torch, transformers, and Pillow
are all already installed for the [ml] extras.

If Florence-2 has globally disabled itself (the transformers >=4.50
Cache-shape KeyError the summarizer guards against) OR `<OCR_WITH_REGION>`
yields nothing usable, we fall back to bare `<OCR>` (text only, no boxes).

HANDWRITING (TrOCR). Florence reads PRINTED text well but transcribes cursive
poorly. So when Florence's read of a region set looks LOW-CONFIDENCE (mean
per-token logprob below `_TROCR_FLORENCE_CONF_FLOOR` — the signature of
handwriting), and `ocr_handwriting_enabled` is on, we treat Florence as a pure
DETECTOR and re-read each detected box with TrOCR
(`microsoft/trocr-base-handwritten`), substituting TrOCR's text for that line
(`_apply_trocr_to_region` → `_trocr_read_regions`). The engine string becomes
`florence2-detect+trocr-handwritten`. TrOCR runs on CPU on purpose (the GPU is
full; no resident GPU recognizer) at ~1-3 s/line, lazily loaded + cached in
`backend.vision.runtime.get_trocr`. Confident PRINTED reads skip TrOCR entirely
and keep the fast Florence-only path.

TRANSLATION METHOD  (NLLB-200, with a Qwen fallback)
-----------------------------------------------------
Primary engine: Meta's **NLLB-200** (`facebook/nllb-200-distilled-600M`),
a dedicated many-to-many machine-translation model covering **200
languages** — the right tool for "translate to ALL languages". It is
loaded lazily on the FIRST /translate call (module import never pulls it,
so `--reload` stays fast) and cached in a module global; inference runs in
a threadpool off the event loop. Weights (~2.4 GB) auto-download to
`${HF_HOME}` on first use.

Source-language detection: `langdetect` (lightweight, pure-Python) gives
an ISO-639-1 code which we map to the model's **FLORES-200** code
(en→eng_Latn, es→spa_Latn, zh→zho_Hans, ar→arb_Arab, hi→hin_Deva …). The
request's `target` is likewise normalized: it may already be a FLORES code
(`spa_Latn`) or an ISO code (`es`) — both resolve to a FLORES code; an
unknown/empty target falls back to English (`eng_Latn`). NLLB needs the
source FLORES code set on the tokenizer and the target FLORES token forced
as the first generated token, which is exactly what we do.

Returns `{translation, src_lang, tgt_lang, engine:"nllb-200", ...}` where
`src_lang`/`tgt_lang` are the FLORES codes actually used.

GRACEFUL FALLBACK (never 500):
NLLB's tokenizer requires `sentencepiece`. If `sentencepiece` or the NLLB
weights aren't available (fresh deploy before `pip install`, offline box,
download failure), or `langdetect` is missing, we fall back to the Qwen2.5-
1.5B-Instruct rewriter (`backend.vision.runtime.get_summary_rewriter`) —
the same general LLM the summarizer uses — prompting it to name the source
language and translate to the target, parsed from strict JSON. The
response's `engine` field tells the UI which path ran ("nllb-200" vs
"qwen2.5-instruct") so it can note the difference. Only if BOTH engines are
unavailable do we return a clean 503 (never a fake result).

LIMITATION (fallback only): Qwen2.5-1.5B is a small general-purpose model,
not a specialised translator — fine for short common-language on-image
text, weaker on long passages / rare languages / jargon, and it can
paraphrase. With NLLB installed (the default once deps are added) this
path is rarely hit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.image import fetch_original, fetch_served
from backend.models import Image, User
from backend.storage import storage

logger = logging.getLogger(__name__)

# Same prefix/tags as images.py so the routes read as part of the images
# surface. Wired separately in app.py (see the 2 lines at the bottom of
# this module's docstring in the task hand-off).
router = APIRouter(prefix="/images", tags=["images", "ocr"])


# ---------------------------------------------------------------------------
# Owner-scoped lookup — copy of images.py:_load_owned_image so this module
# stays self-contained and importing it can never reach back into images.py
# internals (which the safety note keeps off-limits for edits).
# ---------------------------------------------------------------------------
async def _load_owned_image(
    image_id: UUID, user: User, session: AsyncSession,
) -> Image:
    result = await session.execute(
        select(Image).where(
            Image.id == image_id,
            Image.user_id == user.id,
            Image.deleted_at.is_(None),
        )
    )
    img = result.scalar_one_or_none()
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    return img


async def _read_image_bytes(image: Image) -> tuple[bytes, str]:
    """Fetch the bytes we OCR. Prefer the ORIGINAL (full resolution =
    sharper text); fall back to the served variant when the original has
    been dropped by hybrid retention. Both helpers already pick the right
    bucket. Runs the blocking storage read in a thread."""
    try:
        if image.original_blob_key is not None:
            return await asyncio.to_thread(_fetch_original_sync, image)
        return await asyncio.to_thread(_fetch_served_sync, image)
    except Exception as exc:  # storage miss / decode issue
        logger.exception("ocr: could not read bytes for %s", image.id)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Image bytes unavailable"
        ) from exc


def _fetch_original_sync(image: Image) -> tuple[bytes, str]:
    blob = storage.get(storage.bucket_originals, image.original_blob_key)
    return blob, image.mime_type_original or "application/octet-stream"


def _fetch_served_sync(image: Image) -> tuple[bytes, str]:
    bucket = (
        storage.bucket_originals
        if image.served_blob_key == image.original_blob_key
        else storage.bucket_served
    )
    blob = storage.get(bucket, image.served_blob_key)
    return blob, image.mime_type_served or "application/octet-stream"


# ---------------------------------------------------------------------------
# OCR — Florence-2 <OCR_WITH_REGION> (boxes) with <OCR> (text-only) fallback.
# All ML imports are lazy + inside the worker fn so importing this module
# never pulls torch/transformers at API boot.
# ---------------------------------------------------------------------------
class OcrLine(BaseModel):
    text: str
    x: float = Field(description="left, normalized 0..1 of image width")
    y: float = Field(description="top, normalized 0..1 of image height")
    w: float = Field(description="width, normalized 0..1 of image width")
    h: float = Field(description="height, normalized 0..1 of image height")


class OcrResult(BaseModel):
    text: str
    lines: list[OcrLine]
    width: int
    height: int
    engine: str
    boxes: bool


def _quad_to_box(quad: list[float], iw: int, ih: int) -> Optional[OcrLine | dict]:
    """Reduce an 8-value Florence quad [x1,y1,...,x4,y4] (absolute pixels)
    to a normalized axis-aligned {x,y,w,h}. Returns a dict (caller wraps in
    OcrLine with the text). None when the quad is degenerate."""
    if not quad or len(quad) < 8 or iw <= 0 or ih <= 0:
        return None
    xs = quad[0::2]
    ys = quad[1::2]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    # Clamp into the image, normalize.
    x0 = max(0.0, min(float(x0), iw))
    x1 = max(0.0, min(float(x1), iw))
    y0 = max(0.0, min(float(y0), ih))
    y1 = max(0.0, min(float(y1), ih))
    w = (x1 - x0) / iw
    h = (y1 - y0) / ih
    if w <= 0 or h <= 0:
        return None
    return {"x": x0 / iw, "y": y0 / ih, "w": w, "h": h}


# ---------------------------------------------------------------------------
# ORIENTATION-ROBUST OCR
#
# Florence (like every OCR model) reads text far better when the glyphs are
# upright. A photo of a sign / page shot sideways or upside-down reads poorly
# in its native orientation. `_ocr_best_orientation` tries all FOUR 90°
# rotations, scores each read, and returns the best one — so rotated text is
# transcribed accurately. The scorer + the rotation math are deliberately
# model-agnostic so the SAME helper drives the OCR panel, the in-image
# translator, AND (later) per-frame video OCR/translation: any caller just
# supplies an `ocr_fn(pil_img) -> (text, n_regions, confidence, payload)`
# callable (confidence may be None — see `_orientation_score`).
# ---------------------------------------------------------------------------

# A 0° read whose MEAN-CONFIDENCE score reaches this is already a strong,
# clearly-upright read, so we may skip the other three rotations to save ~3
# Florence passes. The confidence score is `100*exp(mean_logprob)` (see
# `_orientation_score`); empirically an upright Florence read lands ~45-65 and
# a hallucinated 180° read ~30-40, so 50 is a conservative "clearly upright"
# bar. When confidence is unavailable the early-exit falls back to an
# alphanumeric-character threshold of the same numeric value. OPT-IN per call
# via `early_exit_score`.
_ORIENT_EARLY_EXIT_SCORE = 50.0


def _alnum_count(text: str) -> int:
    """Count alphanumeric characters in `text`. Unicode-aware (str.isalnum
    counts Latin, Cyrillic, Greek, CJK, Arabic-Indic digits, …). Used both as
    a text-presence gate and as the FALLBACK orientation score when the OCR
    backend can't supply a model confidence."""
    if not text:
        return 0
    return sum(1 for ch in text if ch.isalnum())


def _orientation_score(
    text: str, n_regions: int, confidence: float | None
) -> float:
    """Score one orientation's OCR — used to pick the best of the four 90°
    reads. Returns -inf for an EMPTY read (no alphanumerics) so a confident
    "no text here" can never beat a real read.

    For a read that DOES contain text:
      * When the backend supplies a per-token model `confidence` (mean
        log-probability, <=0), that is the PRIMARY signal — exponentiated to a
        0..100 scale. This is the robust discriminator: Florence-2 reads 90°
        rotations fine and only badly mis-reads the 180° flip, and that
        hallucinated read is the LEAST confident even though it often
        hallucinates MORE characters. Ranking by character count alone would
        wrongly prefer the garbage; ranking by confidence does not. A tiny
        per-region + length term only breaks confidence ties.
      * When confidence is None (e.g. the plain <OCR> fallback, or a
        non-Florence ocr_fn), fall back to the alphanumeric character count
        plus a small per-region bonus.
    """
    alnum = _alnum_count(text)
    if alnum == 0:
        return float("-inf")  # no real text → not a candidate orientation
    if confidence is not None:
        import math

        # exp(mean_logprob) ∈ (0,1]; ×100 for a readable 0..100 scale. Add a
        # hair of alnum/region so exact-confidence ties resolve toward the
        # fuller read (negligible vs the confidence term).
        base = 100.0 * math.exp(max(-20.0, float(confidence)))
        return base + 0.001 * alnum + 0.01 * float(max(0, n_regions))
    # No confidence available — character count is the proxy.
    return float(alnum) + 0.5 * float(max(0, n_regions))


def _ocr_best_orientation(
    pil_img,
    ocr_fn,
    *,
    early_exit_score: float | None = None,
):
    """Run `ocr_fn` on all four 90° rotations of `pil_img` and return the
    best-reading one.

    `ocr_fn(rotated_pil_img)` must return a 4-tuple
        (text: str, n_regions: int, confidence: float | None, payload)
    where `confidence` is a mean per-token log-probability (<=0; None if the
    backend can't supply one) and `payload` is whatever the caller needs back
    at that orientation (an OcrResult, a list of pixel regions, …). Each read
    is ranked by `_orientation_score(text, n_regions, confidence)`.

    Rotation convention: orientation k applies `img.rotate(-90*k,
    expand=True)` (clockwise, growing the canvas so nothing is cropped). k is
    the number of 90° CW turns that make the text upright. To map a result
    rendered in the best frame back to the ORIGINAL frame, undo with
    `composite.rotate(+90*k, expand=True)` (see translate_image.py).

    Returns (best_k, best_payload, best_rotated_img, scores) where `scores`
    is the list[float] of all four scores (index = k; -inf for an
    empty/failed/short-circuited orientation), for logging/tests.

    Tries all four orientations (the user explicitly wants every rotation
    read). When `early_exit_score` is given and the 0° pass already meets it,
    the other three are skipped (their scores stay -inf) — an optional, purely
    speed-saving short-circuit for the common clearly-upright case.
    """
    scores: list[float] = [float("-inf")] * 4
    best_k = 0
    best_score = float("-inf")
    best_payload = None
    best_img = pil_img

    for k in range(4):
        rot = pil_img if k == 0 else pil_img.rotate(-90 * k, expand=True)
        try:
            text, n_regions, confidence, payload = ocr_fn(rot)
        except Exception:
            logger.exception("ocr: orientation k=%d pass failed", k)
            text, n_regions, confidence, payload = "", 0, None, None
        score = _orientation_score(text or "", n_regions or 0, confidence)
        scores[k] = score
        if score > best_score:
            best_score = score
            best_k = k
            best_payload = payload
            best_img = rot
        # Optional conservative short-circuit: a strong upright read means we
        # needn't pay for the other three Florence passes.
        if k == 0 and early_exit_score is not None and score >= early_exit_score:
            break

    return best_k, best_payload, best_img, scores


def _run_ocr_sync(raw_bytes: bytes) -> OcrResult:
    """Blocking OCR. Reads the image in ALL FOUR 90° orientations and keeps
    the best-scoring read (so sideways / upside-down text is transcribed
    accurately), then tries <OCR_WITH_REGION> for boxes and falls back to
    <OCR> for text-only. Always returns an OcrResult (possibly empty)."""
    from io import BytesIO

    from PIL import Image as PILImage

    img = PILImage.open(BytesIO(raw_bytes)).convert("RGB")

    # Per-orientation reader: <OCR_WITH_REGION> for boxes (with a model
    # confidence so the scorer can reject the hallucinated 180° read), falling
    # back to the bare <OCR> text path. Returns (text, n_regions, confidence,
    # OcrResult) so the orientation scorer can rank reads and the winner is
    # ready to return.
    def _read(rot_img):
        riw, rih = rot_img.width, rot_img.height
        region, conf = _florence_ocr_region(
            rot_img, riw, rih, return_confidence=True
        )
        if region is not None:
            # FAST Florence-only path: Florence DETECTED and READ the regions,
            # and we return its text/lines directly. The CPU TrOCR handwriting
            # re-read is intentionally OUT of this live hot path — on a full
            # handwritten page it stalled the OCR for 60s+ ("warming up") and
            # forced a 1.3 GB model load. `_apply_trocr_to_region` stays defined
            # but is no longer called here. Orientation is ranked by Florence's
            # own confidence, exactly as before TrOCR was added.
            return region.text, len(region.lines), conf, region
        # Text-only fallback at this orientation (re-encode the rotated frame
        # so the summarizer's bytes-based <OCR> path sees the SAME pixels). No
        # confidence on this path → scorer falls back to character count.
        plain = _florence_ocr_plain(_encode_for_ocr(rot_img))
        res = OcrResult(
            text=plain or "",
            lines=[],
            width=riw,
            height=rih,
            engine="florence2-ocr",
            boxes=False,
        )
        return res.text, 0, None, res

    best_k, best_result, _best_img, scores = _ocr_best_orientation(
        img, _read, early_exit_score=_ORIENT_EARLY_EXIT_SCORE
    )
    logger.info(
        "ocr: best orientation k=%d (%d deg CW); scores=%s",
        best_k, 90 * best_k, [round(s, 1) for s in scores],
    )

    if best_result is None:  # every orientation failed
        return OcrResult(
            text="", lines=[], width=img.width, height=img.height,
            engine="florence2-ocr", boxes=False,
        )

    # The OCR panel overlays line boxes on the image AS DISPLAYED (native
    # orientation). Boxes detected in a rotated frame don't map onto that
    # display, so we only keep boxes when the winning orientation IS native
    # (k==0); for a rotated winner we return the accurate TEXT with boxes
    # dropped (boxes=False), which is exactly how the text-only path already
    # behaves. Text accuracy — what the user asked for — is preserved either
    # way.
    if best_k != 0 and best_result.boxes:
        return OcrResult(
            text=best_result.text,
            lines=[],
            width=img.width,
            height=img.height,
            engine=best_result.engine,
            boxes=False,
        )
    return best_result


def _encode_for_ocr(pil_img) -> bytes:
    """Encode a PIL image to PNG bytes for the bytes-based plain <OCR> path.
    PNG is lossless so the rotated frame keeps its text crisp."""
    from io import BytesIO

    buf = BytesIO()
    pil_img.save(buf, format="PNG")
    return buf.getvalue()


def _florence_ocr_region(
    img,
    iw: int,
    ih: int,
    return_confidence: bool = False,
    *,
    max_new_tokens: int = 1024,
    num_beams: int = 3,
):
    """Florence-2 <OCR_WITH_REGION>. Returns an OcrResult with per-line
    boxes, or None on any failure (caller falls back to plain OCR).

    When `return_confidence=True`, returns a 2-tuple `(result_or_None,
    mean_conf_or_None)` where `mean_conf` is the MEAN PER-TOKEN log-probability
    of the decoded sequence (closer to 0 = more confident). The orientation
    detector (`_ocr_best_orientation`) uses this as its primary score because
    Florence-2 is already robust to 90° turns and only badly mis-reads the
    180° flip — and that hallucinated read is reliably the LEAST confident one
    (raw character count is NOT a safe discriminator: a garbage upside-down
    read often hallucinates MORE characters than the correct upright read).
    Existing callers omit the flag and get the unchanged single return.

    `max_new_tokens` / `num_beams` BOUND the decode. The defaults (1024, 3)
    preserve the OCR-panel behavior, but a dense full page can make Florence
    generate a very long sequence — and with beam search that cost is paid
    `num_beams`× over. The in-image-translate path passes a SMALLER token cap
    and greedy `num_beams=1` so one dense (e.g. handwritten) page can't generate
    for minutes and hang the request."""
    result: Optional[OcrResult] = None
    mean_conf: Optional[float] = None
    try:
        import torch

        from backend.vision.runtime import get_florence2

        model, processor, device = get_florence2()

        prompt = "<OCR_WITH_REGION>"
        inputs = processor(text=prompt, images=img, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        if device == "cuda" and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.no_grad():
            gen = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=max(16, int(max_new_tokens)),
                num_beams=max(1, int(num_beams)),
                do_sample=False,
                # Ask for scores only when the caller wants a confidence — it
                # adds a little overhead and changes the return type to a
                # GenerateOutput, which we normalize back to the id tensor.
                output_scores=return_confidence,
                return_dict_in_generate=return_confidence,
            )
        if return_confidence:
            ids = gen.sequences
            mean_conf = _mean_token_confidence(model, gen)
        else:
            ids = gen
        decoded = processor.batch_decode(ids, skip_special_tokens=False)[0]
        # Florence-2's post_process_generation expects image_size as
        # (WIDTH, HEIGHT) — its Florence2PostProcessor divides x-coords by
        # image_size[0] and y-coords by image_size[1]. Passing (height,
        # width) here swapped the axes, which scaled every quad wrong: lines
        # landed off the bottom of tall images and were dropped by
        # _quad_to_box as "degenerate" (h==0 after clamping), forcing a
        # silent fall-through to the text-only <OCR> path — and where they
        # survived, the overlay boxes were mis-positioned. The summarizer's
        # own _ocr_image already uses (width, height); match it.
        parsed = processor.post_process_generation(
            decoded, task=prompt, image_size=(iw, ih)
        )
        block = parsed.get(prompt) or {}
        quads = block.get("quad_boxes") or []
        labels = block.get("labels") or []

        lines: list[OcrLine] = []
        for quad, label in zip(quads, labels):
            text = _clean_label(str(label))
            if not text:
                continue
            box = _quad_to_box(list(quad), iw, ih)
            if box is None:
                continue
            lines.append(OcrLine(text=text, **box))

        # Sort top-to-bottom, then left-to-right so the listed text reads
        # in natural order regardless of detection order.
        lines.sort(key=lambda ln: (round(ln.y, 3), round(ln.x, 3)))
        full = "\n".join(ln.text for ln in lines).strip()

        if lines:
            result = OcrResult(
                text=full,
                lines=lines,
                width=iw,
                height=ih,
                engine="florence2-ocr-with-region",
                boxes=True,
            )
        # else: no usable boxes — leave result None so the caller tries plain
        # OCR (which sometimes still returns text when region parsing yields
        # nothing).
    except Exception:
        logger.exception("ocr: <OCR_WITH_REGION> failed; will try plain OCR")
        result, mean_conf = None, None

    if return_confidence:
        return result, mean_conf
    return result


def _mean_token_confidence(model, gen) -> Optional[float]:
    """Mean per-token log-probability of a `generate(..., output_scores=True,
    return_dict_in_generate=True)` result — a model-confidence signal in
    (-inf, 0]. Higher (closer to 0) = the model was more certain, which is the
    hallmark of the correctly-oriented read. Returns None if scores aren't
    available (then the orientation scorer falls back to character count)."""
    try:
        import torch

        scores = getattr(gen, "scores", None)
        seq = getattr(gen, "sequences", None)
        if not scores or seq is None:
            return None
        ts = model.compute_transition_scores(
            seq, scores, getattr(gen, "beam_indices", None), normalize_logits=True
        )
        row = ts[0]
        finite = row[torch.isfinite(row)]
        if finite.numel() == 0:
            return None
        return float(finite.mean())
    except Exception:
        logger.exception("ocr: could not compute token confidence")
        return None


def _florence_ocr_plain(raw_bytes: bytes) -> Optional[str]:
    """Bare-text OCR. Reuses the summarizer's `_ocr_image` (Florence-2
    <OCR>) so we share the exact same code path / global disable flag.
    Read-only import — does not mutate the summarizer."""
    try:
        from backend.summarize import _ocr_image

        return _ocr_image(raw_bytes)
    except Exception:
        logger.exception("ocr: plain <OCR> fallback failed")
        return None


def _clean_label(s: str) -> str:
    """Florence sometimes leaves stray special-token fragments or
    surrounding whitespace on a line label. Strip the obvious ones."""
    s = s.replace("<s>", "").replace("</s>", "").replace("<pad>", "")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# TrOCR HANDWRITING RECOGNITION (CPU).
#
# Florence-2 is a strong text DETECTOR — its `<OCR_WITH_REGION>` boxes are
# placed well even on handwriting — but a weak handwriting READER: it
# transcribes cursive poorly. TrOCR (`microsoft/trocr-base-handwritten`) is the
# opposite: it can't detect anything (it reads a SINGLE pre-cropped line) but it
# reads handwriting accurately. So we combine them: Florence supplies the boxes,
# and for each box we crop the ORIGINAL image and hand that crop to TrOCR, then
# substitute TrOCR's text for Florence's on that line.
#
# This re-read is gated (see `_florence_read_is_weak`) so PRINTED text — which
# Florence already reads well, often more confidently than a 1-line handwriting
# model — keeps the original fast Florence-only path. TrOCR runs on CPU on
# purpose (the GPU is full; a resident GPU recognizer isn't affordable), lazily
# loaded + cached in runtime.get_trocr's lru_cache. ~1-3 s per cropped line.
# ---------------------------------------------------------------------------

# Florence mean-token log-probability below which its read looks shaky enough to
# be worth a TrOCR second opinion. The orientation scorer already exposes this
# confidence (mean per-token logprob, <=0; 100*exp(x) is its 0..100 scale). An
# upright PRINTED read sits high (logprob ~ -0.2..-0.6, i.e. exp ~ 0.55..0.82);
# a cursive read Florence is unsure of drops well below. -0.85 (exp ~ 0.43)
# keeps clean printed pages on the Florence-only path while routing the
# uncertain (handwriting-y) reads through TrOCR. When confidence is unavailable
# (None) we treat the read as weak so handwriting still gets the TrOCR pass.
_TROCR_FLORENCE_CONF_FLOOR = -0.85

# Skip crops smaller than this (px) on either side — too tiny for TrOCR to read
# anything useful, and not worth a CPU pass.
_TROCR_MIN_CROP_PX = 6
# Pad each Florence box by this fraction of its height before cropping, so
# ascenders/descenders and the start/end of a cursive stroke aren't clipped.
_TROCR_CROP_PAD_FRAC = 0.12
# Cap how many regions one image sends through CPU TrOCR so a text-dense page
# can't stall for minutes (~1-3 s/line). Beyond this we keep Florence's text for
# the overflow lines. Generous enough for handwritten notes / a letter.
_TROCR_MAX_REGIONS = 40
# Regions per TrOCR forward pass. Batching amortizes the model call across
# lines; small enough to keep CPU memory modest.
_TROCR_BATCH = 8


def _trocr_enabled() -> bool:
    """Whether the TrOCR handwriting recognizer is switched on (config
    `ocr_handwriting_enabled`, default True). Import-safe; defaults True if
    config can't be read."""
    try:
        from backend.config import settings

        return bool(getattr(settings, "ocr_handwriting_enabled", True))
    except Exception:
        return True


def _florence_read_is_weak(text: str, mean_conf: Optional[float]) -> bool:
    """Decide whether Florence's read of a region set is shaky enough to be
    worth a TrOCR handwriting re-read.

    PRINTED text is read confidently by Florence (high mean-token logprob), so
    we leave it on the fast Florence-only path. A low confidence (or none at
    all) is the signature of cursive / messy handwriting Florence struggles
    with, so we route those through TrOCR. Empty text never triggers a re-read
    (nothing to recognize)."""
    if not text or not text.strip():
        return False
    if mean_conf is None:
        # No confidence signal (e.g. the plain <OCR> path) — be permissive so
        # handwriting still gets the TrOCR pass; printed text loses nothing
        # because TrOCR on a clean printed line reproduces it.
        return True
    return float(mean_conf) < _TROCR_FLORENCE_CONF_FLOOR


def _trocr_read_regions(pil_img, boxes: list[tuple[int, int, int, int]]) -> list[Optional[str]]:
    """Read each PIXEL box `(x0, y0, x1, y1)` of `pil_img` with TrOCR
    (handwriting), on CPU. Returns a list aligned 1:1 with `boxes`: the
    recognized text for that crop, or None when the crop was too small / TrOCR
    produced nothing / the recognizer is unavailable.

    Florence is the detector that produced `boxes`; this is the handwriting
    READER for those regions. Crops are padded slightly so ascenders/descenders
    aren't clipped, then batched through one TrOCR forward pass at a time.
    NEVER raises — any failure yields all-None so the caller transparently keeps
    Florence's text."""
    if not boxes:
        return []
    out: list[Optional[str]] = [None] * len(boxes)
    try:
        import torch

        from backend.vision.runtime import get_trocr

        model, processor, device = get_trocr()  # device == "cpu"

        iw, ih = pil_img.width, pil_img.height
        # Build padded crops, remembering which original index each maps to so
        # skipped (too-small) boxes stay None.
        crops: list = []
        idx_map: list[int] = []
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            if i >= _TROCR_MAX_REGIONS:
                break  # overflow lines keep Florence's text
            pad = max(1, int(round((y1 - y0) * _TROCR_CROP_PAD_FRAC)))
            cx0 = max(0, x0 - pad)
            cy0 = max(0, y0 - pad)
            cx1 = min(iw, x1 + pad)
            cy1 = min(ih, y1 + pad)
            if cx1 - cx0 < _TROCR_MIN_CROP_PX or cy1 - cy0 < _TROCR_MIN_CROP_PX:
                continue
            crop = pil_img.crop((cx0, cy0, cx1, cy1)).convert("RGB")
            crops.append(crop)
            idx_map.append(i)

        if not crops:
            return out

        for bstart in range(0, len(crops), _TROCR_BATCH):
            batch = crops[bstart:bstart + _TROCR_BATCH]
            pixel_values = processor(
                images=batch, return_tensors="pt"
            ).pixel_values.to(device)
            with torch.no_grad():
                generated = model.generate(pixel_values, max_new_tokens=128)
            texts = processor.batch_decode(generated, skip_special_tokens=True)
            for j, t in enumerate(texts):
                cleaned = (t or "").strip()
                if cleaned:
                    out[idx_map[bstart + j]] = cleaned
    except Exception:
        logger.exception("ocr: TrOCR handwriting read failed; keeping Florence text")
        return [None] * len(boxes)
    return out


def _apply_trocr_to_region(pil_img, result: "OcrResult", mean_conf: Optional[float]) -> "OcrResult":
    """If Florence's read for `result` looks weak (likely handwriting) AND TrOCR
    is enabled, re-read every line's box with TrOCR (CPU) and substitute its
    text; otherwise return `result` unchanged.

    `pil_img` is the SAME (upright) frame Florence read, so `result.lines`'
    normalized boxes map straight back to pixel crops here. Line ↔ box stays
    index-aligned; lines TrOCR couldn't improve keep Florence's text. The
    full-text string and the engine label are rebuilt to reflect the substituted
    lines. NEVER raises — on any error returns the original Florence result."""
    try:
        if result is None or not result.lines:
            return result
        if not _trocr_enabled():
            return result
        if not _florence_read_is_weak(result.text, mean_conf):
            return result  # printed / confident read → Florence-only path

        iw, ih = result.width, result.height
        boxes: list[tuple[int, int, int, int]] = []
        for ln in result.lines:
            x0 = int(round(ln.x * iw))
            y0 = int(round(ln.y * ih))
            x1 = int(round((ln.x + ln.w) * iw))
            y1 = int(round((ln.y + ln.h) * ih))
            boxes.append((x0, y0, x1, y1))

        reads = _trocr_read_regions(pil_img, boxes)
        if not any(r for r in reads):
            return result  # TrOCR added nothing usable → keep Florence

        new_lines: list[OcrLine] = []
        for ln, r in zip(result.lines, reads):
            text = r if r else ln.text
            new_lines.append(
                OcrLine(text=text, x=ln.x, y=ln.y, w=ln.w, h=ln.h)
            )
        full = "\n".join(l.text for l in new_lines).strip()
        return OcrResult(
            text=full,
            lines=new_lines,
            width=result.width,
            height=result.height,
            engine="florence2-detect+trocr-handwritten",
            boxes=result.boxes,
        )
    except Exception:
        logger.exception("ocr: applying TrOCR to region failed; keeping Florence")
        return result


@router.get("/{image_id}/ocr", response_model=OcrResult)
async def image_ocr(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OcrResult:
    """Run OCR over the owner's image. Returns extracted text + normalized
    per-line boxes. Heavy work runs in a thread so the event loop stays
    free. 404 for non-owners / missing rows (owner scope in
    `_load_owned_image`)."""
    image = await _load_owned_image(image_id, user, session)
    if image.category not in ("image", None):
        # Only images carry OCR-able rasters here. (PDF OCR lives in the
        # summarizer's document path.) Soft-allow NULL category for very
        # old rows.
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "OCR is only available for images.",
        )
    raw, _mime = await _read_image_bytes(image)
    try:
        result = await asyncio.to_thread(_run_ocr_sync, raw)
    except Exception as exc:
        logger.exception("ocr: pipeline crashed for %s", image_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "OCR failed for this image.",
        ) from exc
    return result


# ---------------------------------------------------------------------------
# Translate — NLLB-200 (dedicated 200-language MT) with a Qwen fallback.
# See the module docstring for the method. All ML imports are lazy + inside
# the worker fn so importing this module never pulls torch/transformers/
# sentencepiece at API boot (keeps --reload fast).
# ---------------------------------------------------------------------------
class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    # Target language. Accepts an ISO-639-1 code (en, es, zh, ar, …) OR a
    # FLORES-200 code (eng_Latn, spa_Latn, zho_Hans, …). Defaults to
    # English. Unknown/empty values fall back to English server-side.
    # max_length covers the longest FLORES code with headroom.
    target: str = Field(default="eng_Latn", max_length=40)


class TranslateResult(BaseModel):
    source_lang: str
    target_lang: str
    original: str
    translation: str
    engine: str
    # FLORES-200 codes actually used by NLLB (e.g. "spa_Latn" → "eng_Latn").
    # On the Qwen fallback these are best-effort: tgt is the resolved FLORES
    # target; src may be "" when only a human name was detected.
    src_lang: str = ""
    tgt_lang: str = ""
    note: str


_NLLB_NOTE = (
    "Translated with Meta NLLB-200 (facebook/nllb-200-distilled-600M), a "
    "dedicated 200-language machine-translation model. Source language is "
    "auto-detected; the target is the FLORES-200 code shown."
)
_TRANSLATE_NOTE = (
    "Translated with the local Qwen2.5-1.5B-Instruct model (fallback path) "
    "because the dedicated NLLB-200 translator isn't available in this "
    "build yet. Good for short on-image text in common languages; may "
    "paraphrase or under-perform on long or rare-language passages. Install "
    "`sentencepiece` + `langdetect` to enable NLLB-200 for all languages."
)


# ---------------------------------------------------------------------------
# FLORES-200 code mapping.
#
# NLLB-200 identifies every language by a FLORES-200 code of the form
# `<lang>_<Script>` (e.g. eng_Latn, zho_Hans, arb_Arab). `langdetect` and the
# incoming request give us ISO-639-1 (mostly) 2-letter codes; this table maps
# the common ones to their dominant FLORES code. It is intentionally broad —
# it covers the major world languages plus a long tail — but it does NOT need
# to enumerate all 200: any value the caller passes that is ALREADY a FLORES
# code (contains "_") is used as-is, so the UI's full picker works even for
# codes not listed here. Unknown 2-letter codes fall back to English.
# ---------------------------------------------------------------------------
_ISO_TO_FLORES: dict[str, str] = {
    # Western / Central Europe
    "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "nl": "nld_Latn", "ca": "cat_Latn",
    "gl": "glg_Latn", "eu": "eus_Latn", "ga": "gle_Latn", "cy": "cym_Latn",
    "is": "isl_Latn", "lb": "ltz_Latn", "mt": "mlt_Latn",
    # Nordic
    "sv": "swe_Latn", "da": "dan_Latn", "nb": "nob_Latn", "no": "nob_Latn",
    "nn": "nno_Latn", "fi": "fin_Latn",
    # Baltic / Slavic
    "pl": "pol_Latn", "cs": "ces_Latn", "sk": "slk_Latn", "sl": "slv_Latn",
    "hr": "hrv_Latn", "bs": "bos_Latn", "sr": "srp_Cyrl", "mk": "mkd_Cyrl",
    "bg": "bul_Cyrl", "ru": "rus_Cyrl", "uk": "ukr_Cyrl", "be": "bel_Cyrl",
    "lt": "lit_Latn", "lv": "lvs_Latn", "et": "est_Latn",
    # Greek / Romance tail / misc Europe
    "el": "ell_Grek", "ro": "ron_Latn", "hu": "hun_Latn", "sq": "als_Latn",
    # Middle East / Semitic / Iranian
    "ar": "arb_Arab", "he": "heb_Hebr", "iw": "heb_Hebr", "fa": "pes_Arab",
    "ur": "urd_Arab", "ps": "pbt_Arab", "ku": "kmr_Latn", "ckb": "ckb_Arab",
    "az": "azj_Latn", "hy": "hye_Armn", "ka": "kat_Geor",
    # Turkic / Central Asia
    "tr": "tur_Latn", "kk": "kaz_Cyrl", "uz": "uzn_Latn", "ky": "kir_Cyrl",
    "tk": "tuk_Latn", "tt": "tat_Cyrl", "ba": "bak_Cyrl", "mn": "khk_Cyrl",
    # South Asia (Indic)
    "hi": "hin_Deva", "bn": "ben_Beng", "pa": "pan_Guru", "gu": "guj_Gujr",
    "or": "ory_Orya", "ta": "tam_Taml", "te": "tel_Telu", "kn": "kan_Knda",
    "ml": "mal_Mlym", "mr": "mar_Deva", "ne": "npi_Deva", "si": "sin_Sinh",
    "sd": "snd_Arab", "as": "asm_Beng", "sa": "san_Deva", "mai": "mai_Deva",
    # East / Southeast Asia
    "zh": "zho_Hans", "zh-cn": "zho_Hans", "zh-tw": "zho_Hant",
    "yue": "yue_Hant", "ja": "jpn_Jpan", "ko": "kor_Hang", "th": "tha_Thai",
    "lo": "lao_Laoo", "my": "mya_Mymr", "km": "khm_Khmr", "vi": "vie_Latn",
    "id": "ind_Latn", "ms": "zsm_Latn", "tl": "tgl_Latn", "fil": "tgl_Latn",
    "jv": "jav_Latn", "su": "sun_Latn", "bo": "bod_Tibt",
    # Africa
    "sw": "swh_Latn", "am": "amh_Ethi", "ha": "hau_Latn", "yo": "yor_Latn",
    "ig": "ibo_Latn", "zu": "zul_Latn", "xh": "xho_Latn", "af": "afr_Latn",
    "so": "som_Latn", "rw": "kin_Latn", "ny": "nya_Latn", "sn": "sna_Latn",
    "st": "sot_Latn", "tn": "tsn_Latn", "lg": "lug_Latn", "wo": "wol_Latn",
    "ff": "fuv_Latn", "ti": "tir_Ethi", "om": "gaz_Latn", "ln": "lin_Latn",
    # Other
    "eo": "epo_Latn", "la": "lat_Latn", "ht": "hat_Latn",
}

# Human display names for FLORES codes we want to label nicely in the
# response. Falls back to the FLORES code itself when not listed.
_FLORES_NAMES: dict[str, str] = {
    "eng_Latn": "English", "spa_Latn": "Spanish", "fra_Latn": "French",
    "deu_Latn": "German", "ita_Latn": "Italian", "por_Latn": "Portuguese",
    "nld_Latn": "Dutch", "pol_Latn": "Polish", "rus_Cyrl": "Russian",
    "ukr_Cyrl": "Ukrainian", "jpn_Jpan": "Japanese", "kor_Hang": "Korean",
    "zho_Hans": "Chinese (Simplified)", "zho_Hant": "Chinese (Traditional)",
    "arb_Arab": "Arabic", "hin_Deva": "Hindi", "ben_Beng": "Bengali",
    "tur_Latn": "Turkish", "vie_Latn": "Vietnamese", "tha_Thai": "Thai",
    "ell_Grek": "Greek", "heb_Hebr": "Hebrew", "pes_Arab": "Persian",
    "ind_Latn": "Indonesian", "swh_Latn": "Swahili", "ron_Latn": "Romanian",
    "ces_Latn": "Czech", "hun_Latn": "Hungarian", "swe_Latn": "Swedish",
    "fin_Latn": "Finnish", "dan_Latn": "Danish", "nob_Latn": "Norwegian",
}

# Default FLORES target when nothing usable is supplied.
_DEFAULT_FLORES = "eng_Latn"


def _to_flores(code: str) -> str:
    """Normalize a caller/detector language code to a FLORES-200 code.

    Accepts:
      * a FLORES code already (anything containing "_") → used verbatim;
      * an ISO-639-1 code (en, es, zh, zh-TW …) → mapped via _ISO_TO_FLORES;
      * anything unknown/empty → English.
    Case/locale-suffix tolerant (e.g. "ES", "pt-BR" → por_Latn)."""
    if not code:
        return _DEFAULT_FLORES
    c = code.strip()
    if "_" in c:  # already FLORES (e.g. spa_Latn) — trust it.
        return c
    lc = c.lower().replace("_", "-")
    if lc in _ISO_TO_FLORES:  # full match incl. zh-cn / zh-tw
        return _ISO_TO_FLORES[lc]
    base = lc.split("-", 1)[0]  # strip region: pt-BR → pt
    return _ISO_TO_FLORES.get(base, _DEFAULT_FLORES)


def _flores_name(code: str) -> str:
    """Human-readable name for a FLORES code, best-effort."""
    return _FLORES_NAMES.get(code, code)


# ---------------------------------------------------------------------------
# NLLB-200 lazy loader. Cached in a module global (NOT lru_cache on a method)
# so a single 600M model is shared across requests. Import-safe: nothing here
# runs at module import — the first call inside the threadpool triggers the
# download/load.
# ---------------------------------------------------------------------------
_NLLB_CACHE: dict | None = None
_NLLB_UNAVAILABLE = False  # latched True once we know NLLB can't load.


def _nllb_model_name() -> str:
    try:
        from backend.config import settings
        return getattr(
            settings, "translate_model_name", "facebook/nllb-200-distilled-600M"
        )
    except Exception:
        return "facebook/nllb-200-distilled-600M"


def _get_nllb():
    """Return (model, tokenizer, device) for NLLB-200, loading + caching on
    first use. Raises RuntimeError if it can't be loaded (missing
    sentencepiece, missing weights, download failure) — the caller catches
    and falls back to Qwen. The failure is latched so we don't retry a slow
    failing load on every request."""
    global _NLLB_CACHE, _NLLB_UNAVAILABLE
    if _NLLB_CACHE is not None:
        return _NLLB_CACHE["model"], _NLLB_CACHE["tokenizer"], _NLLB_CACHE["device"]
    if _NLLB_UNAVAILABLE:
        raise RuntimeError("NLLB previously failed to load")

    try:
        # sentencepiece is required by the NLLB tokenizer; surface a clear
        # error early so the fallback kicks in instead of a cryptic one.
        import sentencepiece  # noqa: F401
    except Exception as exc:  # pragma: no cover - env-dependent
        _NLLB_UNAVAILABLE = True
        raise RuntimeError("sentencepiece not installed (NLLB tokenizer needs it)") from exc

    try:
        import torch

        # Import by FULL submodule path (not `from transformers import …`).
        # When other libs in this process (open_clip / insightface / the
        # Florence loader) have touched transformers' lazy top-level loader,
        # the bare `from transformers import AutoModelForSeq2SeqLM` can wedge
        # in a partial state and raise ImportError even though the symbol is
        # reachable. runtime.get_doc_summarizer hit exactly this and uses the
        # same full-path import as the fix.
        from transformers.models.auto.modeling_auto import (
            AutoModelForSeq2SeqLM,
        )
        from transformers.models.auto.tokenization_auto import AutoTokenizer

        from backend.vision.runtime import (
            _materialize_to,
            _report_loaded,
            get_device,
        )

        device = get_device()
        model_name = _nllb_model_name()
        dtype = torch.float16 if device == "cuda" else torch.float32
        # low_cpu_mem_usage=False mirrors the other loaders in runtime.py:
        # it forces full (non-meta) materialization so the later .to(device)
        # can't hit the meta-tensor NotImplementedError.
        model = AutoModelForSeq2SeqLM.from_pretrained(
            model_name, torch_dtype=dtype, low_cpu_mem_usage=False
        )
        model = _materialize_to(model, device).eval()
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        for p in model.parameters():
            p.requires_grad_(False)
        try:
            _report_loaded("nllb-200", device)
        except Exception:
            pass
        _NLLB_CACHE = {"model": model, "tokenizer": tokenizer, "device": device}
        return model, tokenizer, device
    except Exception as exc:
        _NLLB_UNAVAILABLE = True
        logger.exception("translate: NLLB-200 load failed; will fall back to Qwen")
        raise RuntimeError("NLLB-200 unavailable") from exc


def _detect_src_flores(text: str) -> str:
    """Detect the source language with langdetect and map to FLORES. Returns
    a FLORES code; defaults to English on any detection failure or when
    langdetect isn't installed (NLLB still translates fine — it just assumes
    the source is English, which is the safest default for Latin text)."""
    try:
        from langdetect import detect  # type: ignore

        iso = detect(text[:1000])  # 1k chars is plenty + keeps it fast
        return _to_flores(iso)
    except Exception:
        return _DEFAULT_FLORES


# Whole-document translation budget. NLLB runs one generate() per chunk, so a
# long document is translated piece-by-piece and the pieces are joined. We cap
# the SYNCHRONOUS request at this many characters so it finishes inside the HTTP
# window; anything past it is flagged truncated (a future async path can do the
# whole thing). ~1500 chars/chunk keeps every call comfortably under NLLB's
# token limit.
_NLLB_SYNC_CHAR_BUDGET = 30_000
_NLLB_CHUNK_CHARS = 1_500
# Hard wall-clock ceiling for one synchronous translate request. NLLB on CPU
# costs real seconds per chunk, so without this a long document spins for
# minutes and the request feels hung. We translate until this many seconds
# elapse, then return what's done. (Whole-document translation = async path.)
_NLLB_SYNC_TIME_BUDGET = 18.0
# Chunks per batched GPU generate() call. Batching parallelizes decode across
# chunks — the biggest win for a multi-page doc on a GPU; 8 keeps VRAM modest.
_NLLB_BATCH = 8


def _chunk_text(text: str, chunk_chars: int) -> list[str]:
    """Split `text` into <= chunk_chars pieces, preferring paragraph, then
    sentence, then word boundaries so a chunk never cuts mid-word."""
    text = text.strip()
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        end = min(i + chunk_chars, n)
        if end < n:
            window = text[i:end]
            for sep in ("\n\n", "\n", ". ", "! ", "? ", "; ", " "):
                cut = window.rfind(sep)
                if cut > chunk_chars * 0.5:
                    end = i + cut + len(sep)
                    break
        out.append(text[i:end])
        i = end
    return out


# Display note for the new Apache-2.0 engine (commercial-safe replacement for
# the CC-BY-NC NLLB note). Surfaced in the /translate response so the UI can
# label which model ran.
_ENGINE_NOTE = (
    "Translated with Google MADLAD-400 (3B, Apache-2.0) — a dedicated "
    "400-language machine-translation model — with Helsinki-NLP Opus-MT "
    "(Apache-2.0) covering exotic languages MADLAD lacks (e.g. Tongan). "
    "Source language is auto-detected."
)


def _translate_engine_first(text: str, target: str) -> TranslateResult:
    """Translate via the new Apache-2.0 engine (MADLAD-400 + Opus-MT).

    This is the PRIMARY path for /translate. Detects the source FLORES code for
    display only (the engine itself auto-detects + is target-token driven),
    chunks the budgeted input, and translates each chunk under the same hard
    wall-clock ceiling the NLLB path uses so the synchronous request can never
    hang. Returns a TranslateResult with `engine` naming the sub-model used.

    Raises RuntimeError when the engine can't be loaded — `_translate_nllb`
    catches it and falls back to the NLLB path (which itself falls back to
    Qwen), so /translate never returns a 500 just because MADLAD isn't
    downloaded yet."""
    import time

    from backend.api import translate_engine

    # Ensure the engine is actually loadable BEFORE we start — a RuntimeError
    # here cleanly triggers the NLLB fallback in the caller.
    translate_engine.get_translator()

    src_flores = _detect_src_flores(text)
    tgt_flores = _to_flores(target)

    full = text.strip()
    budget = full[:_NLLB_SYNC_CHAR_BUDGET]
    chunks = [c for c in _chunk_text(budget, _NLLB_CHUNK_CHARS) if c.strip()] or [budget]

    # Same hard wall-clock ceiling as the NLLB path: the first chunk always
    # runs (never return empty), then stop once the deadline passes and flag
    # the remainder as truncated.
    deadline = time.monotonic() + _NLLB_SYNC_TIME_BUDGET
    parts: list[str] = []
    done = 0
    stopped_early = False
    engine_label = translate_engine.route_for(tgt_flores, source=src_flores)
    for idx, chunk in enumerate(chunks):
        if idx > 0 and time.monotonic() > deadline:
            stopped_early = True
            break
        out = translate_engine.translate_text(chunk, tgt_flores, source=src_flores)
        if out:
            parts.append(out.strip())
        done += len(chunk)

    translation = "\n\n".join(p for p in parts if p).strip()
    # If the engine produced nothing at all on non-empty input, treat it as a
    # miss so the caller falls back to NLLB rather than returning an empty box.
    if not translation:
        raise RuntimeError("translate_engine produced empty output")

    truncated = stopped_early or len(full) > done
    note = _ENGINE_NOTE
    if truncated:
        extra = (
            f"Translated {done:,} of {len(full):,} characters here — the full "
            "document translates in the dedicated long-document path."
        )
        note = f"{note} {extra}" if note else extra

    return TranslateResult(
        source_lang=_flores_name(src_flores),
        target_lang=_flores_name(tgt_flores),
        original=text,
        translation=translation,
        engine="madlad400-3b" if "madlad" in engine_label else "opus-mt",
        src_lang=src_flores,
        tgt_lang=tgt_flores,
        note=note,
    )


def _translate_nllb(text: str, target: str) -> TranslateResult:
    """Translate orchestrator for the /translate route. Tries the new
    Apache-2.0 engine (MADLAD-400 + Opus-MT) FIRST; on any load/inference
    failure it FALLS BACK to the original NLLB-200 path below — keeping the
    function name + return type so every caller is unchanged. Raises
    RuntimeError only when BOTH the new engine AND NLLB can't be loaded (the
    /translate route then falls back to Qwen)."""
    # PRIMARY — the commercial-safe Apache-2.0 engine.
    try:
        return _translate_engine_first(text, target)
    except RuntimeError:
        logger.info("translate: new engine unavailable, falling back to NLLB-200")
    except Exception:
        logger.exception("translate: new engine errored, falling back to NLLB-200")
    # FALLBACK — the original NLLB-200 path (may itself RuntimeError → Qwen).
    return _translate_nllb_only(text, target)


def _translate_nllb_only(text: str, target: str) -> TranslateResult:
    """Translate via NLLB-200 (the FALLBACK path). Detects the source language,
    sets it on the tokenizer, and forces the target FLORES token as the first
    generated token. LONG inputs are chunked and translated piece-by-piece so a
    whole document (not just the first paragraph) comes back. Raises
    RuntimeError when NLLB can't be loaded (caller falls back to Qwen)."""
    import time

    import torch

    model, tokenizer, device = _get_nllb()

    src_flores = _detect_src_flores(text)
    tgt_flores = _to_flores(target)

    # NLLB tokenizer: set src_lang so the source language token is prepended.
    try:
        tokenizer.src_lang = src_flores
    except Exception:
        pass

    # Resolve the forced-BOS token id for the target language. The NLLB
    # tokenizer exposes lang→id via convert_tokens_to_ids (and, on some
    # versions, lang_code_to_id). Fall back gracefully.
    forced_bos = None
    try:
        forced_bos = tokenizer.convert_tokens_to_ids(tgt_flores)
    except Exception:
        forced_bos = None
    if forced_bos is None and hasattr(tokenizer, "lang_code_to_id"):
        try:
            forced_bos = tokenizer.lang_code_to_id[tgt_flores]
        except Exception:
            forced_bos = None

    full = text.strip()
    budget = full[:_NLLB_SYNC_CHAR_BUDGET]
    chunks = [c for c in _chunk_text(budget, _NLLB_CHUNK_CHARS) if c.strip()] or [budget]

    # Greedy for multi-chunk docs (beam multiplies cost); the no-repeat guard
    # stops the degenerate loops that otherwise run generation to max length.
    gen_kwargs = dict(
        do_sample=False,
        num_beams=4 if len(chunks) <= 1 else 1,
        no_repeat_ngram_size=3,
    )
    if forced_bos is not None:
        gen_kwargs["forced_bos_token_id"] = forced_bos

    # Translate chunk-by-chunk under a HARD wall-clock ceiling so the request
    # can NEVER hang: the first chunk always runs (so we never return empty),
    # then we stop once the deadline passes and flag the remainder.
    deadline = time.monotonic() + _NLLB_SYNC_TIME_BUDGET
    parts: list[str] = []
    done = 0
    stopped_early = False
    # Batch chunks through the GPU so they decode in PARALLEL — the single
    # biggest win for a multi-page document (autoregressive decode is otherwise
    # sequential per chunk). The deadline is checked between batches so the
    # request still can never hang. Each sequence stops at its own EOS, so the
    # max_new ceiling doesn't force over-generation on the short chunks.
    for bstart in range(0, len(chunks), _NLLB_BATCH):
        if bstart > 0 and time.monotonic() > deadline:
            stopped_early = True
            break
        batch = chunks[bstart:bstart + _NLLB_BATCH]
        enc = tokenizer(
            batch, return_tensors="pt", truncation=True, max_length=1024, padding=True
        )
        enc = {k: v.to(device) for k, v in enc.items()}
        # Translation ≈ source length; cap new tokens to ~1.6× the input rather
        # than a flat 1024 so decode stops near the real end.
        in_len = int(enc["input_ids"].shape[1])
        max_new = max(32, min(1024, int(in_len * 1.6) + 24))
        with torch.no_grad():
            out_ids = model.generate(**enc, max_new_tokens=max_new, **gen_kwargs)
        for d in tokenizer.batch_decode(out_ids, skip_special_tokens=True):
            parts.append(d.strip())
        done += sum(len(c) for c in batch)

    translation = "\n\n".join(p for p in parts if p).strip()

    truncated = stopped_early or len(full) > done
    note = _NLLB_NOTE
    if truncated:
        extra = (
            f"Translated {done:,} of {len(full):,} characters here — the full "
            "document translates in the dedicated long-document path (coming next)."
        )
        note = f"{note} {extra}" if note else extra

    return TranslateResult(
        source_lang=_flores_name(src_flores),
        target_lang=_flores_name(tgt_flores),
        original=text,
        translation=translation,
        engine="nllb-200",
        src_lang=src_flores,
        tgt_lang=tgt_flores,
        note=note,
    )


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # Drop the opening fence (``` or ```json) and the closing fence.
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _translate_qwen(text: str, target: str) -> TranslateResult:
    """Blocking FALLBACK translate via the Qwen rewriter LLM. Used only when
    NLLB-200 isn't available. Raises RuntimeError when the model isn't
    available so the route can return a clean 503."""
    import torch

    from backend.config import settings
    from backend.vision.runtime import get_summary_rewriter

    if not getattr(settings, "rewriter_enabled", True):
        raise RuntimeError("rewriter disabled")

    model, tokenizer, device = get_summary_rewriter()

    # Resolve the target to a FLORES code (so we can report tgt_lang) and a
    # human language NAME (so the LLM prompt is unambiguous — feeding it
    # "spa_Latn" would confuse it; "Spanish" is what it understands).
    tgt_flores = _to_flores(target)
    target_clean = _flores_name(tgt_flores)
    snippet = text.strip()[:6000]

    system = (
        "You are a precise translator. You translate text faithfully and "
        "you never add commentary. You always reply with a single JSON "
        "object and nothing else."
    )
    user = (
        "Detect the language of the TEXT below, then translate it into "
        f"the target language: {target_clean}.\n"
        "Reply with ONLY a JSON object of this exact shape:\n"
        '{"source_language": "<name of detected source language in English>", '
        '"target_language": "<name of the target language in English>", '
        '"translation": "<the translated text>"}\n'
        "Preserve line breaks inside the translation. Do not transliterate "
        "names unnecessarily. Do not wrap the JSON in code fences.\n\n"
        f"TEXT:\n{snippet}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    prompt_len = inputs.input_ids.shape[1]
    with torch.no_grad():
        out_ids = model.generate(
            **inputs,
            max_new_tokens=1024,
            do_sample=False,
            num_beams=1,
            pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    reply = tokenizer.decode(
        out_ids[0][prompt_len:], skip_special_tokens=True
    ).strip()
    reply = _strip_code_fence(reply)

    source_lang = "Unknown"
    target_lang = target_clean
    translation = ""
    # Primary path: parse the JSON object the model was asked for.
    try:
        # Grab the first {...} span so trailing prose can't break json.loads.
        m = re.search(r"\{.*\}", reply, re.DOTALL)
        obj = json.loads(m.group(0) if m else reply)
        source_lang = str(obj.get("source_language") or "Unknown").strip() or "Unknown"
        target_lang = str(obj.get("target_language") or target_clean).strip() or target_clean
        translation = str(obj.get("translation") or "").strip()
    except Exception:
        # Fallback: the model ignored the JSON instruction. Treat the whole
        # reply as the translation so the user still gets something usable.
        translation = reply.strip()

    if not translation:
        translation = reply.strip()

    return TranslateResult(
        source_lang=source_lang,
        target_lang=target_lang,
        original=text,
        translation=translation,
        engine="qwen2.5-instruct",
        src_lang="",  # Qwen detects by name only, not a FLORES code.
        tgt_lang=tgt_flores,
        note=_TRANSLATE_NOTE,
    )


def _translate_sync(text: str, target: str) -> TranslateResult:
    """Blocking translate orchestrator (runs in a threadpool).

    Tries the dedicated NLLB-200 translator FIRST (covers all 200
    languages). If NLLB can't be loaded — `sentencepiece` missing, weights
    absent, download failure — it falls back to the Qwen rewriter LLM so the
    endpoint keeps working (the response `engine` field tells the UI which
    ran). Raises RuntimeError only when BOTH engines are unavailable, so the
    route can return a clean 503 instead of a 500 or a fake result."""
    # 1) NLLB-200 — the right tool for "translate to ALL languages".
    try:
        return _translate_nllb(text, target)
    except RuntimeError:
        # NLLB unavailable (sentencepiece/weights). Fall through to Qwen.
        logger.info("translate: NLLB-200 unavailable, falling back to Qwen")
    except Exception:
        # Unexpected NLLB inference error — don't 500; try the fallback.
        logger.exception("translate: NLLB-200 inference error, falling back to Qwen")

    # 2) Qwen fallback (general LLM). May itself raise RuntimeError when the
    #    rewriter is disabled / not downloaded → propagates to a clean 503.
    return _translate_qwen(text, target)


@router.post("/{image_id}/translate", response_model=TranslateResult)
async def image_translate(
    image_id: UUID,
    body: TranslateRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TranslateResult:
    """Translate caller-supplied text (typically the OCR output for this
    image) to a target language. Uses the dedicated NLLB-200 translator
    (200 languages) with a Qwen-rewriter fallback — see the module
    docstring. `target` may be an ISO code (en, es, zh…) or a FLORES-200
    code (eng_Latn…); defaults to English. Owner-scoped on the image so a
    caller can only translate against files they own (keeps the surface
    consistent with /ocr and avoids being a free open translation oracle).
    503 only when neither translation engine is available."""
    # Owner check — same gate as /ocr. We don't use the image bytes here,
    # but scoping the route to an owned image keeps the auth surface tight.
    await _load_owned_image(image_id, user, session)
    text = body.text.strip()
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Nothing to translate."
        )
    try:
        return await asyncio.to_thread(_translate_sync, text, body.target)
    except RuntimeError as exc:
        # Model intentionally unavailable (disabled / not downloaded).
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Translation model is not available in this deployment.",
        ) from exc
    except Exception as exc:
        logger.exception("translate: crashed for %s", image_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Translation failed.",
        ) from exc
