"""OpenCLIP zero-shot concept tagging.

For each image we encode the pixels once, then compare against a
precomputed text embedding for every entry in
`concept_vocab.VOCAB`. Top-K above a similarity threshold become tags
fed to the Qwen synthesis prompt.

The vocab encoding is cached at module level after first call so the
~5k-string text encode (~50 ms GPU / ~2 s CPU) only happens once per
process. Best-effort everywhere: any failure (OpenCLIP missing,
torch unavailable, OOM) returns None — the rest of the summary
pipeline degrades cleanly.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from io import BytesIO
from typing import Optional

from backend.config import settings
from backend.vision.concept_vocab import VOCAB

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _vocab_text_features():
    """Encode the entire concept vocab once. Returns (tensor, device, model)
    where tensor is L2-normalized text features ready for cosine compare.
    Raises if OpenCLIP can't load."""
    import torch  # type: ignore

    from backend.vision.runtime import get_clip

    model, _, tokenizer, device = get_clip()
    with torch.no_grad():
        toks = tokenizer(VOCAB).to(device)
        feats = model.encode_text(toks)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats, device, model


def top_concepts(image_bytes: bytes,
                 k: Optional[int] = None,
                 threshold: Optional[float] = None) -> Optional[list[str]]:
    """Return up to K concept labels with cosine similarity ≥ threshold.

    None on any failure. The labels are ordered by similarity (best first).
    Defaults come from `settings.concept_vocab_top_k` /
    `settings.concept_vocab_threshold` so the values can be overridden
    via `.env` without touching code.
    """
    if k is None:
        k = settings.concept_vocab_top_k
    if threshold is None:
        threshold = settings.concept_vocab_threshold
    try:
        import torch  # type: ignore
        from PIL import Image as PILImage  # type: ignore

        from backend.vision.runtime import get_clip

        model, preprocess, _, device = get_clip()
        text_feats, _, _ = _vocab_text_features()

        image = PILImage.open(BytesIO(image_bytes)).convert("RGB")
        pixel = preprocess(image).unsqueeze(0).to(device)
        if device == "cuda":
            pixel = pixel.half()

        with torch.no_grad():
            img_feat = model.encode_image(pixel)
            img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)
            sims = (img_feat @ text_feats.T).squeeze(0).float().cpu().tolist()

        scored = sorted(
            enumerate(sims),
            key=lambda iv: iv[1],
            reverse=True,
        )
        out: list[str] = []
        for idx, score in scored[: k * 2]:  # peek a bit past K to filter
            if score < threshold:
                break
            out.append(VOCAB[idx])
            if len(out) >= k:
                break
        return out or None
    except Exception:
        logger.exception("concepts: top_concepts failed")
        return None
