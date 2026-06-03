"""Full-document text extraction endpoint (NEW, unwired).

  GET /images/{id}/text  → extract the FULL text of a document file
                           (PDF / docx / xlsx / pptx / plaintext / code /
                           …) so the preview's "Translate" tool can feed
                           the whole document to the NLLB-200 translator.

       Response:
         {
           "text":      "full extracted document text…",
           "chars":     12345,        # len(text) actually returned
           "topic":     "Invoice #42" | null,   # AI-ish topic guess, best-effort
           "truncated": false         # true when the source was longer than
                                      # the cap and we clipped it
         }

WHY A SEPARATE ROUTE FROM /ocr
------------------------------
`/images/{id}/ocr` rasterizes an IMAGE and runs Florence-2 OCR — it only
makes sense for picture files. Documents already carry real, selectable
text, so there's nothing to OCR: we pull the text straight out of the
original bytes with the summarizer's `_extract_doc_text` (the same
extractor that powers document summaries — pdf via PyMuPDF, docx/xlsx/pptx
via python-docx/openpyxl/python-pptx, and a plaintext/UTF-8 fallback for
everything else). That text is what the FE hands to `POST
/images/{id}/translate` (NLLB-200, 200 languages) verbatim.

OWNER SCOPE / SAFETY
--------------------
Mirrors `backend/api/ocr.py:_load_owned_image` — the document must belong
to the requesting user (404 otherwise) so this is never a free
text-extraction oracle over other people's files.

PERFORMANCE
-----------
Extraction (PDF parse / Office unzip) is blocking + occasionally heavy, so
it runs in a threadpool (`run_in_threadpool`) off the event loop, exactly
like ocr.py runs Florence in `asyncio.to_thread`. The heavy import
(`backend.summarize`, which transitively pulls PyMuPDF etc.) is LAZY inside
the worker function so importing THIS module at API boot stays cheap and
`--reload` stays fast.

The returned text is capped at `MAX_DOC_CHARS` (200k) with `truncated`
flagged accordingly, so a 900-page book can't blow up the JSON response or
overwhelm the translator (which itself chunks/limits server-side).
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User
from backend.storage import storage

logger = logging.getLogger(__name__)

# Same prefix/tags as images.py / ocr.py so the route reads as part of the
# images surface. Wired separately in app.py (see the 2 lines in this
# module's task hand-off — placed right after the ocr_router include).
router = APIRouter(prefix="/images", tags=["images", "doctext"])

# Cap the returned text so a huge book doesn't blow up the response body or
# the downstream translator. The FE shows a "truncated" note when hit.
MAX_DOC_CHARS = 200_000


# ---------------------------------------------------------------------------
# Owner-scoped lookup — copy of ocr.py:_load_owned_image so this module stays
# self-contained (the same pattern images.py uses).
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return img


def _fetch_original_sync(image: Image) -> bytes:
    """Blocking fetch of the ORIGINAL bytes (the full document). Documents
    only ever live in the originals bucket — they aren't transcoded to a
    served variant — so the original key is what we read."""
    return storage.get(storage.bucket_originals, image.original_blob_key)


class DocTextResult(BaseModel):
    text: str
    chars: int
    topic: Optional[str] = None
    truncated: bool = False


def _extract_sync(image: Image, raw: bytes) -> DocTextResult:
    """Blocking extraction (runs in a threadpool). Lazy-imports the
    summarizer (which transitively pulls PyMuPDF / python-docx / openpyxl /
    python-pptx) so this module stays cheap to import at API boot."""
    # Lazy heavy import — mirrors ocr.py keeping ML imports inside the worker.
    from backend.summarize import _extract_doc_text

    fname = image.original_filename or "file"
    text, topic = _extract_doc_text(fname, raw, image.mime_type_original)
    text = text or ""
    truncated = False
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS]
        truncated = True
    return DocTextResult(
        text=text,
        chars=len(text),
        topic=topic or None,
        truncated=truncated,
    )


@router.get("/{image_id}/text", response_model=DocTextResult)
async def document_text(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DocTextResult:
    """Extract the FULL text of an owned document so the preview's Translate
    tool can translate the whole thing. Owner-scoped (404 for non-owners /
    missing rows). Heavy extraction runs in a threadpool. Text is capped at
    MAX_DOC_CHARS with `truncated` flagged when clipped."""
    image = await _load_owned_image(image_id, user, session)
    if image.original_blob_key is None:
        # Hybrid retention may have dropped the original; without it there
        # are no bytes to extract document text from.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes unavailable"
        )
    try:
        raw = await run_in_threadpool(_fetch_original_sync, image)
    except Exception as exc:  # storage miss / decode issue
        logger.exception("doctext: could not read bytes for %s", image_id)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes unavailable"
        ) from exc
    try:
        result = await run_in_threadpool(_extract_sync, image, raw)
    except Exception as exc:
        logger.exception("doctext: extraction crashed for %s", image_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Could not read text from this document.",
        ) from exc
    return result
