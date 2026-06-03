"""Streaming document translation (NEW, unwired).

A single route, authed + owner-scoped (same gate as backend/api/ocr.py's
`/translate`):

  POST /images/{id}/translate-stream
       Body: {"text": "...", "target": "spa_Latn"}
             `text`   — up to 200_000 chars (the WHOLE document; no 30k
                        sync cap here — streaming shows progress, so we
                        translate the entire thing).
             `target` — a FLORES-200 code (spa_Latn, deu_Latn, …) OR an
                        ISO-639-1 code (es, de, …); empty/unknown → English.

       Response: an `application/x-ndjson` StreamingResponse. The body is
       an ASYNC GENERATOR that translates the document chunk-by-chunk and
       emits ONE JSON object per line as each chunk finishes, so the
       frontend can paint the translation progressively instead of waiting
       on the whole document.

       NDJSON line shapes (one per line, each `\\n`-terminated):
         per chunk : {"i": <0-based idx>, "n": <total chunks>,
                      "text": "<translated chunk>",
                      "source_lang": "<human name>",
                      "target_lang": "<human name>"}
         on finish : {"done": true, "n": <total chunks>}
         on error  : {"error": "<short message>"}   (terminates the stream;
                      the generator never raises, which would hang it)

WHY STREAMING
-------------
The dedicated NLLB-200 translator (`facebook/nllb-200-distilled-600M`) now
runs on the GPU and translates each ~1500-char chunk in ~1-3s. The
synchronous `/translate` route (backend/api/ocr.py) caps a single request at
~30k chars under a wall-clock deadline so it can't hang the HTTP window,
which means long documents come back truncated and the user stares at a
spinner the whole time. This route flips that: it translates the FULL
document and flushes each chunk the instant it's ready, so the panel fills in
top-to-bottom in real time.

IMPLEMENTATION
--------------
We reuse ocr.py's NLLB internals verbatim — `_get_nllb` (the cached, lazily
loaded 600M model + tokenizer + device), `_detect_src_flores` /
`_to_flores` / `_flores_name` (language detection + FLORES mapping +
display names), `_chunk_text` + `_NLLB_CHUNK_CHARS` (the paragraph/sentence/
word-boundary splitter), and `_load_owned_image` (404 for non-owners). The
forced-BOS-token resolution and the per-chunk greedy generate() kwargs are
copied from ocr.py's `_translate_nllb` so the two paths produce identical
output for the same chunk.

Each chunk's blocking `model.generate(...)` runs via
`asyncio.to_thread(...)` so the event loop is NEVER blocked and other
requests stay responsive while a big document streams. NLLB is loaded once
(cached in ocr.py's module global), so only the first request pays the load
cost; the detect + forced-BOS resolution happens ONCE up front, not per
chunk.

If NLLB can't be loaded (sentencepiece/weights missing — `_get_nllb` raises
RuntimeError) or any chunk fails, the generator emits a single `{"error":
...}` line and stops cleanly. There is no Qwen fallback on this route: the
streaming UX is specifically the NLLB-on-GPU win, and the non-streaming
`/translate` route still provides the graceful Qwen fallback for builds
without NLLB.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import User

# Reuse the NLLB internals + owner-scoping from the sibling OCR module so the
# two translate paths share one model load and identical chunk output. These
# are all import-safe (no ML imported at module import time in ocr.py either).
from backend.api.ocr import (
    _NLLB_CHUNK_CHARS,
    _chunk_text,
    _detect_src_flores,
    _flores_name,
    _get_nllb,
    _load_owned_image,
    _to_flores,
)

logger = logging.getLogger(__name__)

# Same /images prefix + tags as ocr.py so the route reads as part of the
# images surface. Wired separately in app.py (see the hand-off at the end of
# this module's task report).
router = APIRouter(prefix="/images", tags=["images", "ocr"])


class TranslateStreamRequest(BaseModel):
    # The WHOLE document. Up to 200k chars — streaming shows progress so we
    # translate everything (no 30k synchronous cap like /translate).
    text: str = Field(min_length=1, max_length=200_000)
    # Target language: a FLORES-200 code (spa_Latn, …) or an ISO-639-1 code
    # (es, …). Unknown/empty falls back to English server-side.
    target: str = Field(default="eng_Latn", max_length=40)


def _resolve_forced_bos(tokenizer, tgt_flores: str):
    """Resolve the forced-BOS token id for the target language. Copied from
    ocr.py's `_translate_nllb`: the NLLB tokenizer exposes lang→id via
    convert_tokens_to_ids (and, on some versions, lang_code_to_id). Returns
    None when neither resolves (generate() then just omits the constraint)."""
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
    return forced_bos


def _translate_one_chunk_sync(
    model, tokenizer, device, chunk: str, gen_kwargs: dict
) -> str:
    """Blocking single-chunk translate. The SHARED per-chunk entry point for
    every streaming translate path (stream / doc / pdf / image).

    PRIMARY engine: the Apache-2.0 `translate_engine` (MADLAD-400 + Opus-MT) —
    the commercial-safe replacement for CC-BY-NC NLLB. The caller threads the
    resolved FLORES target/source codes into `gen_kwargs` as the private keys
    `_nt_target` / `_nt_source`; we route the chunk through
    `translate_engine.translate_text(...)` with those. KEEPS the same inputs /
    output (a translated string) so every caller is unchanged.

    FALLBACK: if the new engine can't be loaded OR errors, we fall back to the
    NLLB `model.generate(...)` exactly as before — the `model`/`tokenizer`/
    `device`/NLLB `gen_kwargs` the caller already resolved. So nothing ever
    returns a 500 just because MADLAD isn't downloaded yet.

    Mirrors the per-chunk body of ocr.py's `_translate_nllb` (dynamic
    max_new_tokens ≈ 1.6× the input length so decode stops near the real end).
    Executed via asyncio.to_thread so the event loop stays free."""
    import torch

    # Read the private routing keys we smuggle through gen_kwargs. We use
    # get() (NOT pop()) on purpose: the SAME gen_kwargs dict is reused across
    # every chunk, so the keys must survive for the next chunk. They are
    # stripped into a local copy (`nllb_kwargs`, below) right before the NLLB
    # model.generate(**...) call so they never leak into generate() — which
    # would TypeError on the unknown kwargs.
    nt_target = gen_kwargs.get("_nt_target")
    nt_source = gen_kwargs.get("_nt_source")

    # 1) PRIMARY — the Apache-2.0 engine. Only attempt when we have a target.
    if nt_target:
        try:
            from backend.api import translate_engine

            out = translate_engine.translate_text(
                chunk, nt_target, source=nt_source
            )
            # translate_text never raises for an empty result; treat a blank
            # return on non-blank input as a soft miss and fall through to NLLB.
            if out and out.strip():
                return out.strip()
        except RuntimeError:
            # Engine unavailable (no weights/deps) — fall back to NLLB below.
            logger.info(
                "translate-stream: new engine unavailable, falling back to NLLB"
            )
        except Exception:
            logger.exception(
                "translate-stream: new engine errored, falling back to NLLB"
            )

    # 2) FALLBACK — NLLB-200 (the original path), with the private keys removed.
    nllb_kwargs = {
        k: v for k, v in gen_kwargs.items() if not k.startswith("_nt_")
    }
    enc = tokenizer(
        chunk, return_tensors="pt", truncation=True, max_length=1024
    )
    enc = {k: v.to(device) for k, v in enc.items()}
    in_len = int(enc["input_ids"].shape[1])
    # Translation ≈ source length; cap new tokens to ~1.6× the input rather
    # than a flat 1024 so decode stops near the real end (copied from ocr.py).
    max_new = max(32, min(1024, int(in_len * 1.6) + 24))
    with torch.no_grad():
        out_ids = model.generate(**enc, max_new_tokens=max_new, **nllb_kwargs)
    decoded = tokenizer.batch_decode(out_ids, skip_special_tokens=True)
    return (decoded[0] if decoded else "").strip()


async def _translate_stream_gen(text: str, target: str):
    """Async NDJSON generator. Detects source + resolves forced-BOS ONCE,
    chunks the FULL document, then translates each chunk in a thread and
    yields one NDJSON line per chunk (flushed as it's produced). Never
    raises out of the generator — any error becomes a final `{"error": ...}`
    line so the HTTP stream always terminates instead of hanging."""
    try:
        # 1) Load NLLB (cached after first use) + resolve languages ONCE.
        #    _get_nllb is blocking (model load on first call) → thread it.
        model, tokenizer, device = await asyncio.to_thread(_get_nllb)

        full = text.strip()
        src_flores = _detect_src_flores(full)
        tgt_flores = _to_flores(target)
        src_name = _flores_name(src_flores)
        tgt_name = _flores_name(tgt_flores)

        # NLLB tokenizer: set src_lang so the source language token is
        # prepended (same as ocr.py's _translate_nllb).
        try:
            tokenizer.src_lang = src_flores
        except Exception:
            pass

        forced_bos = _resolve_forced_bos(tokenizer, tgt_flores)
        # Greedy decode + no-repeat guard (stops the degenerate loops that
        # otherwise run generation to max length). Copied from ocr.py.
        gen_kwargs: dict = dict(
            do_sample=False,
            num_beams=1,
            no_repeat_ngram_size=3,
        )
        if forced_bos is not None:
            gen_kwargs["forced_bos_token_id"] = forced_bos
        # Thread the resolved FLORES codes through so the SHARED per-chunk
        # function can route to the new Apache-2.0 engine (MADLAD/Opus) first
        # and only fall back to this NLLB setup. These private keys are popped
        # before NLLB's generate() so they never leak into model.generate().
        gen_kwargs["_nt_target"] = tgt_flores
        gen_kwargs["_nt_source"] = src_flores

        # 2) Chunk the WHOLE document (no 30k cap — streaming shows progress).
        chunks = [c for c in _chunk_text(full, _NLLB_CHUNK_CHARS) if c.strip()]
        if not chunks:
            chunks = [full]
        n = len(chunks)

        # 3) One generate() per chunk, off the event loop; flush per line.
        for i, chunk in enumerate(chunks):
            translated = await asyncio.to_thread(
                _translate_one_chunk_sync,
                model,
                tokenizer,
                device,
                chunk,
                gen_kwargs,
            )
            yield json.dumps(
                {
                    "i": i,
                    "n": n,
                    "text": translated,
                    "source_lang": src_name,
                    "target_lang": tgt_name,
                }
            ) + "\n"

        # 4) Completion sentinel.
        yield json.dumps({"done": True, "n": n}) + "\n"
    except RuntimeError as exc:
        # NLLB unavailable (sentencepiece / weights missing). Clean error
        # line — the non-streaming /translate route still offers the Qwen
        # fallback for these builds.
        logger.info("translate-stream: NLLB unavailable: %s", exc)
        yield json.dumps(
            {"error": "Translation model is not available in this build."}
        ) + "\n"
    except Exception:
        logger.exception("translate-stream: generation failed")
        yield json.dumps({"error": "Translation failed."}) + "\n"


@router.post("/{image_id}/translate-stream")
async def image_translate_stream(
    image_id: UUID,
    body: TranslateStreamRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Stream a whole-document translation as NDJSON, chunk-by-chunk.

    Owner-scoped on the image (404 for non-owners / missing rows) just like
    `/ocr` and `/translate`, so a caller can only translate against files
    they own. The heavy NLLB work happens inside the async generator (each
    chunk in a thread), so the event loop stays responsive while the
    document streams. Errors are surfaced as a terminal `{"error": ...}`
    NDJSON line rather than an HTTP error, so a partial stream never hangs.
    """
    # Owner check — same gate as /ocr + /translate. We don't read the image
    # bytes here; scoping to an owned image keeps the auth surface tight.
    await _load_owned_image(image_id, user, session)
    text = body.text.strip()
    if not text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Nothing to translate."
        )
    return StreamingResponse(
        _translate_stream_gen(text, body.target),
        media_type="application/x-ndjson",
        # Hint proxies/browsers not to buffer the stream so chunks paint
        # as they arrive.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
