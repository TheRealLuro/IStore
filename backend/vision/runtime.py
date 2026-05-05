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


@lru_cache(maxsize=1)
def get_doc_summarizer():
    """Load the abstractive document summarizer once (Phase 11).

    DistilBART CNN-12-6 (~300 MB) is trained on CNN/DailyMail and produces
    sentence-level paraphrased summaries — "The report covers Q4 revenue
    growth and Phase 11 release plans" instead of pasting the first three
    sentences verbatim. Runs ~2-4 s on CPU per call.

    Returns (model, tokenizer, device).
    """
    import torch

    # Bypass transformers' top-level lazy loader — when other libs in the
    # process (open_clip, insightface) touch transformers' namespace early,
    # the lazy `_get_module` cache can wedge in a partial state where
    # `from transformers import AutoModelForSeq2SeqLM` raises ImportError
    # even though the symbol is reachable from the submodule. Importing
    # by full path skips the cache.
    from transformers.models.auto.modeling_auto import AutoModelForSeq2SeqLM
    from transformers.models.auto.tokenization_auto import AutoTokenizer

    from backend.config import settings

    device = get_device()
    model_name = getattr(
        settings, "summarizer_model_name", "sshleifer/distilbart-cnn-12-6"
    )

    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForSeq2SeqLM.from_pretrained(
        model_name, torch_dtype=dtype
    ).to(device).eval()
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    for p in model.parameters():
        p.requires_grad_(False)

    return model, tokenizer, device


@lru_cache(maxsize=1)
def get_florence2():
    """Load Microsoft Florence-2 once (Phase 11 v2).

    Florence-2 is a multitask vision foundation model. We use two task
    tokens:
      - <MORE_DETAILED_CAPTION>: 2-3 sentence dense scene description.
      - <OCR>: reads visible text in the image (whiteboards, screenshots,
        documents) — replaces a separate easyocr/tesseract dependency.

    First call downloads ~3.4 GB to `${HF_HOME}`. Uses fp16 on CUDA,
    fp32 on CPU (CPU works but is slow ~10-20s per image).

    Returns (model, processor, device).

    Compatibility: Florence-2 ships its own modeling code via
    `trust_remote_code=True`. Newer transformers (4.50+) renamed/removed
    a few attributes the bundled file expects; we patch them defensively
    after load so first inference doesn't AttributeError.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    from backend.config import settings

    device = get_device()
    model_name = settings.caption_model_name

    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype=dtype, trust_remote_code=True
    ).to(device).eval()

    # Florence-2 modeling file predates the `_supports_sdpa` flag the
    # `attn_implementation` resolver checks; missing flag → AttributeError
    # the first time generate() runs. Set it on the class so the resolver
    # picks the eager path without complaint.
    model_cls = type(model)
    if not hasattr(model_cls, "_supports_sdpa"):
        model_cls._supports_sdpa = False

    processor = AutoProcessor.from_pretrained(
        model_name, trust_remote_code=True
    )

    for p in model.parameters():
        p.requires_grad_(False)

    return model, processor, device


@lru_cache(maxsize=1)
def get_caption_model():
    """BLIP fallback for image captioning when Florence-2 fails to load.

    Florence-2 is the primary captioner (`get_florence2`); this exists so
    the pipeline keeps working on transformers minor-version mismatches or
    download failures. Returns (model, processor, device).
    """
    import torch

    # Lazy-loader workaround — see comment in get_doc_summarizer.
    from transformers.models.blip.modeling_blip import (
        BlipForConditionalGeneration,
    )
    from transformers.models.blip.processing_blip import BlipProcessor

    from backend.config import settings

    device = get_device()
    model_name = settings.caption_fallback_model_name

    dtype = torch.float16 if device == "cuda" else torch.float32
    model = BlipForConditionalGeneration.from_pretrained(
        model_name, torch_dtype=dtype
    ).to(device).eval()
    processor = BlipProcessor.from_pretrained(model_name)

    for p in model.parameters():
        p.requires_grad_(False)

    return model, processor, device


@lru_cache(maxsize=1)
def get_summary_rewriter():
    """Small instruction LLM that rewrites raw caption + names + OCR + scene
    into one natural search-friendly sentence (Phase 11 v2).

    Replaces regex-based pronoun fixes with an LLM that handles
    coreference ("a man" → "Mr Koler" → fixes "his cell phone" → "his"
    when third person, "my" when first person) and integrates OCR text
    descriptively ("matrix algebra equations including 2z=-4") rather
    than verbatim copy-paste.

    Qwen2.5-1.5B-Instruct is the cost/quality sweet spot: ~3 GB on disk,
    ~2 GB VRAM in fp16, Apache 2.0. Returns (model, tokenizer, device).
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from backend.config import settings

    device = get_device()
    model_name = settings.rewriter_model_name

    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype=dtype
    ).to(device).eval()
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    for p in model.parameters():
        p.requires_grad_(False)

    return model, tokenizer, device
