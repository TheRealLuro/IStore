"""OpenCLIP text-space embedding for short text chunks.

`concepts.py` already loads the OpenCLIP model + tokenizer to encode
the curated vocab once at process start. This module reuses the same
`get_clip()` runtime to encode arbitrary text strings — for document
chunks (Sprint I D2), summary text (existing `summary_clip_embedding`
backfill), and any future text-vs-image retrieval surface.

Best-effort throughout: any failure (OpenCLIP missing, torch
unavailable, OOM) returns None — the caller persists a NULL embedding
and the search path falls back to FTS for that row.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def embed_text(text: str) -> Optional[list[float]]:
    """Encode `text` into a 768-dim L2-normalized vector matching the
    OpenCLIP ViT-L-14 text space. Returns None if OpenCLIP is not
    available or the encode fails."""
    if not text or not text.strip():
        return None
    try:
        import torch  # type: ignore

        from backend.vision.runtime import get_clip

        model, _, tokenizer, device = get_clip()
        with torch.no_grad():
            # CLIP's text tokenizer caps at 77 tokens — anything longer
            # gets silently truncated. For chunks up to ~2k chars that's
            # acceptable (truncation captures the leading sentences,
            # which usually carry the topic).
            toks = tokenizer([text]).to(device)
            feats = model.encode_text(toks)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        # squeeze batch dim, move to CPU, convert to plain list[float] so
        # SQLAlchemy/pgvector serializes cleanly.
        return feats.squeeze(0).float().cpu().tolist()
    except Exception:
        logger.exception("text_embed: embed_text failed")
        return None


def embed_texts(texts: list[str]) -> list[Optional[list[float]]]:
    """Batch wrapper. Encodes in one CLIP forward pass when possible —
    falls back to a per-item loop on failure so a single bad row doesn't
    kill the whole batch. None per failed entry."""
    if not texts:
        return []
    non_empty: list[tuple[int, str]] = [
        (i, t) for i, t in enumerate(texts) if t and t.strip()
    ]
    out: list[Optional[list[float]]] = [None] * len(texts)
    if not non_empty:
        return out
    try:
        import torch  # type: ignore

        from backend.vision.runtime import get_clip

        model, _, tokenizer, device = get_clip()
        with torch.no_grad():
            toks = tokenizer([t for _, t in non_empty]).to(device)
            feats = model.encode_text(toks)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            arr = feats.float().cpu().tolist()
        for (i, _), vec in zip(non_empty, arr):
            out[i] = vec
        return out
    except Exception:
        logger.exception("text_embed: batch embed failed; falling back to single")
        # Slow path — try each one individually so the batch isn't lost.
        for i, t in non_empty:
            out[i] = embed_text(t)
        return out
