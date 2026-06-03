# VRAM Fabric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the neuthek ML fleet run on the 12 GB 5070 without ever OOMing — via a VRAM-aware model manager (lazy load + LRU eviction + OOM guard) and bitsandbytes quantization of the large LLMs.

**Architecture:** A new `vram_manager.py` registry wraps the existing `@lru_cache` model getters: before a model loads, `ensure_room()` evicts least-recently-used GPU models until there's headroom; GPU inference runs through `run_on_gpu()` which catches `torch.cuda.OutOfMemoryError`, frees+evicts+retries once, then falls back to CPU. A `quant.py` centralizes bitsandbytes configs (rewriter → 4-bit nf4; MADLAD 8-bit kept, env-flippable; NLLB → 8-bit optional). Built on the existing single-thread `inference_pool` (already serializes GPU work).

**Tech Stack:** Python 3.12, PyTorch 2.12+cu130, transformers 4.49, bitsandbytes (8-bit confirmed working on sm_120), pytest 9 / pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-03-vram-fabric-design.md`

---

## File Structure

- **Create** `backend/vision/quant.py` — bitsandbytes config builders, env-gated. One responsibility: "given a model role + env, return a `BitsAndBytesConfig` or None".
- **Create** `backend/vision/vram_manager.py` — registry, `ensure_room`, eviction, `run_on_gpu` OOM guard, `VramPressure`. One responsibility: "decide what's resident and never let inference OOM".
- **Modify** `backend/vision/runtime.py` — register the getters with the manager; call `ensure_room` before each GPU load; quantize the rewriter via `quant.py`.
- **Modify** `backend/api/ocr.py` — optional NLLB 8-bit via `quant.py`; register NLLB.
- **Create** `tests/test_quant.py`, `tests/test_vram_manager.py` — unit tests (mock torch; no GPU).
- **Reuse** `eval/locate_anything/vram_probe.py` — re-measure footprints after integration.

**Test runner (no pytest in the runtime image — mount repo + pip-install):**
```bash
docker run --rm -v "C:/Users/Jdog1/Desktop/Neuthek:/app" -w /app neuthek-backend:latest \
  sh -c "pip install -q pytest==9.0.3 pytest-asyncio==0.24.0 && python -m pytest tests/test_vram_manager.py tests/test_quant.py -v"
```
(Add `--gpus all` for the GPU-marked integration test in Task 6.)

---

## Task 1: Quantization config builders (`quant.py`)

**Files:**
- Create: `backend/vision/quant.py`
- Test: `tests/test_quant.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quant.py
import importlib, os
import pytest


def _reload():
    import backend.vision.quant as q
    return importlib.reload(q)


def test_rewriter_4bit_on_by_default(monkeypatch):
    monkeypatch.delenv("LLM_REWRITER_4BIT", raising=False)
    q = _reload()
    cfg = q.rewriter_quant_config()
    assert cfg is not None
    assert getattr(cfg, "load_in_4bit", False) is True
    assert cfg.bnb_4bit_quant_type == "nf4"


def test_rewriter_4bit_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LLM_REWRITER_4BIT", "0")
    q = _reload()
    assert q.rewriter_quant_config() is None


def test_nllb_8bit_off_by_default(monkeypatch):
    monkeypatch.delenv("NLLB_8BIT", raising=False)
    q = _reload()
    assert q.nllb_quant_config() is None


def test_nllb_8bit_on_when_set(monkeypatch):
    monkeypatch.setenv("NLLB_8BIT", "1")
    q = _reload()
    cfg = q.nllb_quant_config()
    assert cfg is not None and getattr(cfg, "load_in_8bit", False) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... python -m pytest tests/test_quant.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.vision.quant`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/vision/quant.py
"""Central bitsandbytes quantization configs for the ML fleet, env-gated.

Quantizing the large LLMs is the biggest VRAM lever on the 12 GB 5070 and,
per eval/quant_accuracy/ (2026-06-03), costs ~0 translation quality. bnb 8-bit
is confirmed working on this Blackwell (sm_120) card. Each builder returns a
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


def nllb_quant_config():
    """8-bit for NLLB-200 (NLLB_8BIT, default OFF — it's only ~1.2 GB fp16, so
    quantize only under pressure). Returns None to keep fp16."""
    if not _truthy("NLLB_8BIT", False):
        return None
    from transformers import BitsAndBytesConfig
    return BitsAndBytesConfig(load_in_8bit=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `... python -m pytest tests/test_quant.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/vision/quant.py tests/test_quant.py
git commit -m "feat(vram): central bitsandbytes quant configs (rewriter 4-bit, nllb 8-bit)"
```

---

## Task 2: Registry + budget-aware load/eviction (`vram_manager.py`)

**Files:**
- Create: `backend/vision/vram_manager.py`
- Test: `tests/test_vram_manager.py`

The manager must be testable without a GPU, so all CUDA access goes through two
injectable hooks (`_free_gb_hook`, `_empty_cache_hook`) that tests monkeypatch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vram_manager.py
import importlib
import pytest


def fresh():
    import backend.vision.vram_manager as m
    m = importlib.reload(m)
    return m


def test_register_and_touch_orders_lru():
    m = fresh()
    m.register("a", est_gb=1.0, evictable=True)
    m.register("b", est_gb=1.0, evictable=True)
    m.mark_resident("a"); m.touch("a")
    m.mark_resident("b"); m.touch("b")
    m.touch("a")  # a now most-recently-used
    assert m._lru_evictable_order() == ["b", "a"]


def test_ensure_room_evicts_until_fit(monkeypatch):
    m = fresh()
    freed = []
    # device has 2 GB free; need 3 GB; each eviction frees 1.5 GB.
    state = {"free": 2.0}
    m._free_gb_hook = lambda: state["free"]
    def fake_evict(key):
        freed.append(key)
        state["free"] += 1.5
    m._evict_hook = fake_evict
    m.register("a", est_gb=1.5, evictable=True); m.mark_resident("a"); m.touch("a")
    m.register("b", est_gb=1.5, evictable=True); m.mark_resident("b"); m.touch("b")
    m.ensure_room(3.0, margin=0.0)
    # evicted least-recently-used first (a), enough to clear 3 GB
    assert freed == ["a"]


def test_ensure_room_raises_when_no_evictables(monkeypatch):
    m = fresh()
    m._free_gb_hook = lambda: 0.5
    m._evict_hook = lambda k: None
    with pytest.raises(m.VramPressure):
        m.ensure_room(4.0, margin=0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... python -m pytest tests/test_vram_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.vision.vram_manager`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/vision/vram_manager.py
"""VRAM-aware model manager: lazy load + LRU eviction + OOM guard so the ML
fleet never OOMs on the 12 GB 5070. See docs/superpowers/specs/2026-06-03-
vram-fabric-design.md.

CUDA access is funnelled through hooks so the logic is unit-testable without a
GPU. Eviction/loads run on the single inference-pool thread, so the only lock
needed guards the registry against the rare cross-thread warmup getter.
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
    """Evict LRU evictable models until free >= need_gb + margin, else raise."""
    target = need_gb + margin
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `... python -m pytest tests/test_vram_manager.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/vision/vram_manager.py tests/test_vram_manager.py
git commit -m "feat(vram): model registry + budget-aware LRU eviction"
```

---

## Task 3: OOM guard (`run_on_gpu`)

**Files:**
- Modify: `backend/vision/vram_manager.py`
- Test: `tests/test_vram_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vram_manager.py
def test_run_on_gpu_retries_then_cpu_fallback(monkeypatch):
    m = fresh()
    m._free_gb_hook = lambda: 10.0
    m._empty_cache_hook = lambda: None
    calls = {"n": 0}

    class FakeOOM(Exception):
        pass
    m._oom_errors = (FakeOOM,)  # injectable error tuple

    def fn(*, _force_cpu=False):
        calls["n"] += 1
        if not _force_cpu:
            raise FakeOOM()
        return "cpu-result"

    out = m.run_on_gpu(fn, est_gb=1.0)
    assert out == "cpu-result"
    assert calls["n"] == 3  # try, retry after evict, cpu fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `... python -m pytest tests/test_vram_manager.py::test_run_on_gpu_retries_then_cpu_fallback -v`
Expected: FAIL — `AttributeError: module has no attribute 'run_on_gpu'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to backend/vision/vram_manager.py

def _oom_error_types():
    import torch
    return (torch.cuda.OutOfMemoryError,)


_oom_errors = None  # tests override; else resolved lazily


def _evict_all_evictable() -> None:
    for key in _lru_evictable_order():
        _evict_hook(key)
        mark_resident(key, False)


def run_on_gpu(fn, *args, est_gb: float = 0.0, **kwargs):
    """Run a GPU callable with the never-OOM contract: try → on OOM free+evict+
    retry once → on OOM again call fn(_force_cpu=True). fn must accept the
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `... python -m pytest tests/test_vram_manager.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/vision/vram_manager.py tests/test_vram_manager.py
git commit -m "feat(vram): run_on_gpu OOM guard (retry once, then CPU fallback)"
```

---

## Task 4: Register the runtime getters + quantize the rewriter

**Files:**
- Modify: `backend/vision/runtime.py` (`get_florence2`, `get_clip`, `get_caption_model`, `get_doc_summarizer`, `get_summary_rewriter`)

Wrap each getter so it (a) `ensure_room(est_gb)` before loading, (b) `register`s
itself with its `cache_clear`, (c) `mark_resident`+`touch` after load and `touch`
on cache hit. Seed `est_gb` from the probe: clip 1.75, florence2 1.14, caption
0.87, doc_summarizer 0.3, rewriter 1.0 (4-bit) / 3.0 (fp16). Evictable: clip,
caption, doc_summarizer = True; florence2, rewriter = False (hot path).

- [ ] **Step 1: Add a helper + register Florence (write the change)**

At the top of `runtime.py` add:
```python
from backend.vision import vram_manager as _vram

def _managed_load(key, est_gb, evictable, getter, loaded):
    """Call inside a getter: register + ensure room before first load, then
    touch. `loaded` is True if the lru_cache already holds the singleton."""
    _vram.register(key, est_gb=est_gb, evictable=evictable, cache_clear=getter.cache_clear)
    if loaded:
        _vram.touch(key); return
    _vram.ensure_room(est_gb)
```

In `get_florence2`, immediately after the docstring add:
```python
    _vram.register("florence2", est_gb=1.14, evictable=False,
                   cache_clear=get_florence2.cache_clear)
    _vram.ensure_room(1.14)
```
and just before `return model, processor, device` add:
```python
    _vram.mark_resident("florence2"); _vram.touch("florence2")
```

- [ ] **Step 2: Quantize the rewriter (write the change)**

Replace the fp16 load in `get_summary_rewriter` (lines ~473-477):
```python
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype=dtype, low_cpu_mem_usage=False
    )
    model = _materialize_to(model, device).eval()
```
with:
```python
    from backend.vision.quant import rewriter_quant_config
    quant = rewriter_quant_config() if device == "cuda" else None
    if quant is not None:
        _vram.register("rewriter", est_gb=1.0, evictable=False,
                       cache_clear=get_summary_rewriter.cache_clear)
        _vram.ensure_room(1.0)
        # bnb path: device_map pins to GPU 0, NO low_cpu_mem_usage, NO post .to()
        model = AutoModelForCausalLM.from_pretrained(
            model_name, quantization_config=quant, device_map={"": 0},
            torch_dtype=torch.float16,
        ).eval()
    else:
        dtype = torch.float16 if device == "cuda" else torch.float32
        _vram.register("rewriter", est_gb=3.0, evictable=False,
                       cache_clear=get_summary_rewriter.cache_clear)
        if device == "cuda":
            _vram.ensure_room(3.0)
        model = AutoModelForCausalLM.from_pretrained(
            model_name, torch_dtype=dtype, low_cpu_mem_usage=False
        )
        model = _materialize_to(model, device).eval()
    _vram.mark_resident("rewriter"); _vram.touch("rewriter")
```

- [ ] **Step 3: Register the evictable getters (write the change)**

In `get_clip`, after its docstring:
```python
    _vram.register("clip", est_gb=1.75, evictable=True, cache_clear=get_clip.cache_clear)
    _vram.ensure_room(1.75)
```
and before its return: `_vram.mark_resident("clip"); _vram.touch("clip")`.
Repeat the same pattern for `get_caption_model` (est_gb=0.87) and
`get_doc_summarizer` (est_gb=0.3).

- [ ] **Step 4: Smoke-test import (no GPU needed)**

Run:
```bash
docker run --rm -v "C:/Users/Jdog1/Desktop/Neuthek:/app" -w /app neuthek-backend:latest \
  python -c "import backend.vision.runtime as r; print('import OK', hasattr(r,'_managed_load'))"
```
Expected: `import OK True`.

- [ ] **Step 5: Commit**

```bash
git add backend/vision/runtime.py
git commit -m "feat(vram): register runtime getters with manager; quantize rewriter to 4-bit"
```

---

## Task 5: Register NLLB + optional 8-bit

**Files:**
- Modify: `backend/api/ocr.py` (`_get_nllb`, ~line 990)

- [ ] **Step 1: Read the current loader**

Run: `sed -n '985,1015p' backend/api/ocr.py` to see the exact `_get_nllb` body
(model name, device pick, `.to(device)` call).

- [ ] **Step 2: Add quant + registration (write the change)**

In `_get_nllb`, before the model `from_pretrained`, add:
```python
    from backend.vision import vram_manager as _vram
    from backend.vision.quant import nllb_quant_config
    _vram.register("nllb", est_gb=1.2, evictable=False, cache_clear=_get_nllb.cache_clear)
```
If `device == "cuda"`: `_vram.ensure_room(1.2)`. If `nllb_quant_config()` is not
None, pass `quantization_config=...` + `device_map={"": 0}` and DROP the post-hoc
`.to(device)` (bnb rejects it); else keep the existing fp16 `.to(device)` path.
After load: `_vram.mark_resident("nllb"); _vram.touch("nllb")`.

- [ ] **Step 3: Smoke-test import**

Run:
```bash
docker run --rm -v "C:/Users/Jdog1/Desktop/Neuthek:/app" -w /app neuthek-backend:latest \
  python -c "import backend.api.ocr as o; print('ocr import OK')"
```
Expected: `ocr import OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/api/ocr.py
git commit -m "feat(vram): register NLLB with manager + optional 8-bit"
```

---

## Task 6: GPU integration test + re-measure footprint

**Files:**
- Test: `tests/test_vram_manager_gpu.py` (marked, opt-in)
- Reuse: `eval/locate_anything/vram_probe.py`

- [ ] **Step 1: Write the GPU integration test**

```python
# tests/test_vram_manager_gpu.py
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_GPU_TESTS") != "1", reason="GPU opt-in")


def test_fleet_loads_under_budget_without_oom():
    import torch
    from backend.vision import runtime as rt
    from backend.api import translate_engine as te
    rt.get_florence2(); rt.get_summary_rewriter(); te.get_translator()
    free, total = torch.cuda.mem_get_info()
    used_gb = (total - free) / (1024 ** 3)
    assert used_gb < total / (1024 ** 3)  # didn't pin to 0 free
    # one OCR-sized forward + one translate succeed (no OOM)
    assert te.translate_text("hello world", "es")
```

- [ ] **Step 2: Run it on GPU**

Run:
```bash
docker run --rm --gpus all -v "C:/Users/Jdog1/Desktop/Neuthek:/app" \
  -v "C:/Users/Jdog1/Desktop/Neuthek/data/models:/models" -e HF_HOME=/models \
  -e RUN_GPU_TESTS=1 -e LLM_REWRITER_4BIT=1 -w /app neuthek-backend:latest \
  sh -c "pip install -q pytest==9.0.3 pytest-asyncio==0.24.0 && python -m pytest tests/test_vram_manager_gpu.py -v"
```
Expected: PASS (rewriter loads 4-bit ~1 GB; translate succeeds).

- [ ] **Step 3: Re-measure the fleet footprint**

Run the probe (rewriter now 4-bit) and confirm headroom appears where MADLAD
previously hit 0 free:
```bash
docker run --rm --gpus all -v "C:/Users/Jdog1/Desktop/Neuthek/eval/locate_anything:/eval" \
  -v "C:/Users/Jdog1/Desktop/Neuthek/data/models:/models" -e HF_HOME=/models \
  -e LLM_REWRITER_4BIT=1 -w /app neuthek-backend:latest python /eval/vram_probe.py
```
Expected: rewriter row ~+1.0 GB (was +3.06); device used after translator
< 11.94 with free > 0.

- [ ] **Step 4: Run the full unit suite (no regressions)**

Run:
```bash
docker run --rm -v "C:/Users/Jdog1/Desktop/Neuthek:/app" -w /app neuthek-backend:latest \
  sh -c "pip install -q pytest==9.0.3 pytest-asyncio==0.24.0 && python -m pytest tests/test_vram_manager.py tests/test_quant.py -v"
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_vram_manager_gpu.py
git commit -m "test(vram): GPU integration test + footprint re-measure"
```

---

## Self-Review

- **Spec coverage:** registry+LRU (T2), budget-aware load (T2/T4/T5), global queue (existing inference_pool — no code change needed, noted), OOM guard (T3), quant configs (T1) + applied to rewriter (T4) / MADLAD (already) / NLLB (T5), footprint re-measure (T6). All spec sections mapped.
- **Placeholders:** none — every code step has complete code; Task 5 step 2 references the existing `_get_nllb` body which step 1 prints exactly before editing.
- **Type consistency:** manager API (`register(key, est_gb=, evictable=, cache_clear=)`, `ensure_room(need_gb, margin=)`, `mark_resident`, `touch`, `run_on_gpu(fn, *, est_gb=)`, `VramPressure`) used identically across T2–T6. Hook names (`_free_gb_hook`, `_empty_cache_hook`, `_evict_hook`, `_oom_errors`) consistent between impl and tests.
