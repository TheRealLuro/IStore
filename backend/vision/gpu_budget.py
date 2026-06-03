"""Per-process GPU memory cap so the backend and ml-worker (two separate
processes sharing one 12 GB card) can't starve each other into OOM.

`torch.cuda.set_per_process_memory_fraction(f)` caps THIS process's torch
allocations to f x total. With backend=0.8 and worker=0.6 each process is
guaranteed a minimum slice (the other can never take more than its cap), so the
common case — backend serving interactive translate/OCR while the worker runs
background jobs — never has one side grab the whole card. Allocations beyond the
cap raise CUDA OOM, which the vram_manager OOM guard catches (evict / CPU
fallback) rather than crashing. Env: GPU_MEMORY_FRACTION (unset/<=0 = no cap).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def apply_process_memory_cap() -> None:
    """Apply the GPU_MEMORY_FRACTION cap for this process. No-op without CUDA,
    without the env var, or on any error (best-effort hygiene, never fatal)."""
    raw = os.environ.get("GPU_MEMORY_FRACTION")
    if not raw:
        return
    try:
        frac = float(raw)
    except ValueError:
        logger.warning("GPU_MEMORY_FRACTION=%r is not a number; ignoring", raw)
        return
    if frac <= 0 or frac > 1:
        return
    try:
        import torch
        if not torch.cuda.is_available():
            return
        torch.cuda.set_per_process_memory_fraction(frac, 0)
        logger.info("gpu: capped this process to %.0f%% of device 0 VRAM", frac * 100)
    except Exception:  # noqa: BLE001
        logger.warning("gpu: failed to apply memory cap", exc_info=True)
