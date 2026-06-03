"""VRAM-aware model manager: lazy load + LRU eviction + OOM guard so the ML
fleet never OOMs on the 12 GB 5070. See docs/superpowers/specs/2026-06-03-
vram-fabric-design.md.

CUDA access is funnelled through module-level hooks so the logic is unit-testable
without a GPU (tests monkeypatch the hooks). Eviction/loads run on the single
inference-pool thread, so the only lock needed guards the registry against the
rare cross-thread warmup getter.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SAFETY_MARGIN_GB = 0.6


class VramPressure(RuntimeError):
    """Raised when even after evicting all evictables there isn't room."""


@dataclass
class _Entry:
    key: str
    est_gb: float
    evictable: bool
    cache_clear: object = None      # callable to drop the lru_cache singleton
    resident: bool = False
    last_used: float = field(default=0.0)


_registry: dict[str, _Entry] = {}
_lock = threading.Lock()


# --- CUDA hooks (monkeypatched in tests) ---
def _free_gb_hook() -> float:
    import torch
    free, _ = torch.cuda.mem_get_info()
    return free / (1024 ** 3)


def _empty_cache_hook() -> None:
    import gc
    import torch
    gc.collect()
    torch.cuda.empty_cache()


def _evict_hook(key: str) -> None:
    """Drop a model's cached singleton and free its VRAM."""
    e = _registry.get(key)
    if e and callable(e.cache_clear):
        e.cache_clear()
    _empty_cache_hook()


# Overridable in tests; resolved lazily to torch's OOM type otherwise.
_oom_errors = None


def _oom_error_types():
    import torch
    return (torch.cuda.OutOfMemoryError,)


def register(key: str, *, est_gb: float, evictable: bool, cache_clear=None) -> None:
    with _lock:
        cur = _registry.get(key)
        if cur is None:
            _registry[key] = _Entry(key, est_gb, evictable, cache_clear)
        else:
            cur.est_gb, cur.evictable = est_gb, evictable
            if cache_clear is not None:
                cur.cache_clear = cache_clear


def mark_resident(key: str, resident: bool = True) -> None:
    with _lock:
        if key in _registry:
            _registry[key].resident = resident


def touch(key: str) -> None:
    with _lock:
        if key in _registry:
            _registry[key].last_used = time.monotonic()


def _lru_evictable_order() -> list[str]:
    items = [e for e in _registry.values() if e.evictable and e.resident]
    items.sort(key=lambda e: e.last_used)  # oldest first
    return [e.key for e in items]


def ensure_room(need_gb: float, margin: float = SAFETY_MARGIN_GB) -> None:
    """Make room for a `need_gb` load: free PyTorch's reserved cache, then evict
    LRU evictable models until free >= need_gb + margin, else raise.

    `mem_get_info` reports DRIVER-level free, which counts PyTorch's
    reserved-but-unallocated caching-allocator pool as "used". A card that looks
    full often has gigabytes of reusable reserved memory, so we empty that cache
    and re-check BEFORE evicting any model — eviction is the last resort.
    """
    target = need_gb + margin
    if _free_gb_hook() >= target:
        return
    # Return reserved-but-unallocated cache to the driver, then re-check.
    _empty_cache_hook()
    if _free_gb_hook() >= target:
        return
    for key in _lru_evictable_order():
        logger.warning("vram: evicting %s to free room for %.2f GB", key, need_gb)
        _evict_hook(key)
        mark_resident(key, False)
        if _free_gb_hook() >= target:
            return
    if _free_gb_hook() < target:
        raise VramPressure(
            f"need {target:.2f} GB, only {_free_gb_hook():.2f} GB free after eviction"
        )


def _evict_all_evictable() -> None:
    for key in _lru_evictable_order():
        _evict_hook(key)
        mark_resident(key, False)


def run_on_gpu(fn, *args, est_gb: float = 0.0, **kwargs):
    """Run a GPU callable with the never-OOM contract: try -> on OOM free+evict+
    retry once -> on OOM again call fn(_force_cpu=True). fn must accept the
    `_force_cpu` kwarg for the final fallback to work; if it doesn't, the second
    OOM propagates (caller handles)."""
    oom = _oom_errors or _oom_error_types()
    try:
        return fn(*args, **kwargs)
    except oom:
        logger.warning("vram: OOM in %s; freeing + evicting, retrying once", fn)
        _empty_cache_hook()
        _evict_all_evictable()
        try:
            return fn(*args, **kwargs)
        except oom:
            logger.warning("vram: OOM again; CPU fallback for %s", fn)
            _empty_cache_hook()
            return fn(*args, _force_cpu=True, **kwargs)
