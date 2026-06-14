"""Central bitsandbytes quantization configs for the ML fleet, env-gated.

Quantizing the large LLMs is the biggest VRAM lever on the 12 GB 5070 and,
per eval/quant_accuracy/ (2026-06-03), costs ~0 translation quality (MADLAD
8-bit vs 4-bit round-trip chrF 76.7 vs 77.2). bnb 8-bit is confirmed working
on this Blackwell (sm_120) card. Each builder returns a
`transformers.BitsAndBytesConfig` or None (None = full precision / unquantized).
"""
from __future__ import annotations

import os


def _truthy(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def rewriter_quant_config():
    """4-bit nf4 for the instruction rewriter / future 7B (LLM_REWRITER_4BIT,
    default ON). nf4 loads faster than int8 (no autotune stall) and ~quarters
    the footprint. Returns None to keep fp16."""
    if not _truthy("LLM_REWRITER_4BIT", True):
        return None
    import torch
    from transformers import BitsAndBytesConfig
    return BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )


def vl_quant_config():
    """4-bit nf4 for the Qwen2.5-VL handwriting recognizer (VL_4BIT, default ON).
    A 7B VL model is ~16 GB in fp16 — far too big to sit resident alongside the
    rest of the fleet on a 12 GB card — but ~6 GB in nf4, which the VRAM fabric
    can swap in on demand. Returns None to keep fp16 (only viable on a big card)."""
    if not _truthy("VL_4BIT", True):
        return None
    import torch
    from transformers import BitsAndBytesConfig
    return BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )


def nllb_quant_config():
    """8-bit for NLLB-200 (NLLB_8BIT, default OFF — it's only ~1.2 GB fp16, so
    quantize only under pressure). Returns None to keep fp16."""
    if not _truthy("NLLB_8BIT", False):
        return None
    from transformers import BitsAndBytesConfig
    return BitsAndBytesConfig(load_in_8bit=True)
