"""CLIP runtime — lazy-loaded, CPU/CUDA autodetect.

Only imported by the vision pipeline. The base FastAPI app does not depend on
torch — install the `[ml]` extras (`pip install -e ".[dev,ml]"`) before using
the upload pipeline or semantic search.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import torch as _torch_t  # noqa: F401


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


@lru_cache(maxsize=1)
def get_device() -> str:
    return _device()


@lru_cache(maxsize=1)
def get_clip():
    """Load OpenCLIP image+text model and tokenizer once.

    Returns (model, preprocess, tokenizer, device).
    First call downloads weights via open_clip; subsequent calls hit the cache.
    """
    import open_clip
    import torch

    from backend.config import settings

    device = get_device()
    model, _, preprocess = open_clip.create_model_and_transforms(
        settings.clip_model_name,
        pretrained=settings.clip_pretrained,
    )
    model = model.to(device).eval()
    if device == "cuda":
        model = model.half()
    tokenizer = open_clip.get_tokenizer(settings.clip_model_name)

    # Disable autograd permanently for inference.
    for p in model.parameters():
        p.requires_grad_(False)

    return model, preprocess, tokenizer, device


@lru_cache(maxsize=512)
def encode_text_cached(text: str):
    """Encode a single text prompt and L2-normalise. Cached for repeated queries."""
    import torch

    model, _, tokenizer, device = get_clip()
    with torch.no_grad():
        tokens = tokenizer([text]).to(device)
        feats = model.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].float().cpu().numpy()
