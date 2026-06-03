# Sub-project 0 — VRAM Fabric (model manager + quantization)

**Date:** 2026-06-03
**Branch:** app-staging-w23
**Status:** design / awaiting review

## Problem

The neuthek backend serves many ML models (Florence-2 OCR, OpenCLIP, caption,
distilbart summarizer, Qwen rewriter, MADLAD-400 + Opus translators, NLLB,
TrOCR, insightface faces) on a **single RTX 5070 with 11.94 GB**. Measured fact
(`eval/locate_anything/vram_probe.py`, 2026-06-03): the fleet does **not** fit
resident — the GPU saturates (0 GB free) the moment MADLAD-8bit loads on top of
CLIP + Florence + caption + Qwen-1.5B, **before** NLLB / TrOCR / faces and
before any inference activations. Today the only thing preventing OOM is luck
about load order; a request that warms the wrong combination crashes with CUDA
OOM. Sub-project C will replace Qwen-1.5B with a 7B/9B, making "all resident"
impossible by a wide margin.

## Goals

1. **Never OOM, never hard-fail a request.** A request degrades (evict → retry →
   CPU fallback) rather than 500-ing with CUDA OOM.
2. **Quantize the large LLMs** to shrink the footprint so more of the fleet
   coexists and eviction is rarer.
3. **Zero call-site churn where possible** — wrap the existing `lru_cache`
   getters and the existing `inference_pool`, don't rewrite every consumer.

Non-goals: process isolation (ProcessPoolExecutor), multi-GPU, InternVL2 heavy
VLM (stays gated off), changing model *choices* (that's sub-projects A/C).

## Existing infrastructure we build on

- `backend/vision/runtime.py` — `@lru_cache(maxsize=1)` getters
  (`get_clip`, `get_florence2`, `get_trocr`, `get_caption_model`,
  `get_doc_summarizer`, `get_summary_rewriter`, …). Each returns
  `(model, …, device)` and caches the singleton.
- `backend/api/translate_engine.py:get_translator()` — MADLAD + Opus.
- `backend/api/ocr.py:_get_nllb()` — NLLB-200.
- `backend/vision/inference_pool.py` — a **single-thread** `ThreadPoolExecutor`;
  every ML call already routes through `run_in_inference_pool(fn, …)`, so GPU
  work is **already serialized** (this is the request queue). Eviction therefore
  runs on the same single thread → no races on GPU memory state.

## Architecture

A new module `backend/vision/vram_manager.py` plus a thin quant-config helper.
Four parts:

### 1. Model registry

A registry of entries, one per GPU model:

```
ModelEntry:
  key: str                      # "florence2", "clip", "translator_madlad", ...
  loader: Callable[[], Any]     # the existing getter (get_florence2, ...)
  cache_clear: Callable[[], None]  # getter.cache_clear (lru_cache) or custom
  est_gb: float                 # measured footprint (seeded from vram_probe)
  evictable: bool               # CLIP/caption/summarizer yes; in-flight model no
  last_used: float              # monotonic; updated on each access
  resident: bool                # currently on GPU
```

Registration is declarative at import: each getter is wrapped by
`@managed("florence2", est_gb=1.14, evictable=True)` which records the entry and
returns a wrapper that updates `last_used`/`resident` on call. The wrapper does
NOT change the getter's return shape — call sites are unchanged.

### 2. Budget-aware load + LRU eviction

`ensure_room(need_gb)` (called inside a managed getter before it loads weights):

1. Read live free VRAM via `torch.cuda.mem_get_info()`.
2. If `free >= need_gb + SAFETY_MARGIN_GB` → proceed.
3. Else evict resident `evictable` entries in LRU order until there's room:
   `entry.cache_clear()` → drop refs → `gc.collect()` → `torch.cuda.empty_cache()`,
   re-checking free after each. Log every eviction.
4. If still short after evicting all evictables → raise `VramPressure` (the
   loader's caller decides: CPU fallback or smaller variant).

Because all loads/inferences run on the single inference-pool thread,
`ensure_room` needs no locking against itself. A module `threading.Lock` guards
the registry for the rare cross-thread getter (warmup task) for safety.

### 3. Global GPU queue

Already provided by `inference_pool` (single worker). We add a helper
`run_on_gpu(fn, *args, est_gb=...)` that the hot paths use; it routes through
`run_in_inference_pool` and is the natural place to attach the OOM guard (#4).
Existing `run_in_inference_pool` call sites keep working; we migrate the
translate/OCR hot paths to `run_on_gpu` incrementally.

### 4. OOM guard (the "never hard-fail" guarantee)

`run_on_gpu` wraps the call:

```
try:
    return fn(*args)
except torch.cuda.OutOfMemoryError:
    torch.cuda.empty_cache(); evict_all_evictable(); 
    try:
        return fn(*args)            # retry once with max room
    except torch.cuda.OutOfMemoryError:
        return fn(*args, _force_cpu=True)   # final fallback: CPU
```

Models that support a CPU path (TrOCR, NLLB, summarizer) take `_force_cpu`;
those that don't (Florence) instead surface a clean, non-500 "busy, retry"
result to the caller. The request degrades; it never crashes the process.

### 5. Quantization configs

`backend/vision/quant.py` centralizes bitsandbytes configs, env-gated:

| Model | Config | Env | Default |
|---|---|---|---|
| Qwen rewriter / future 7B | 4-bit nf4 (`BitsAndBytesConfig` nf4, bf16 compute) | `LLM_REWRITER_4BIT` | on |
| MADLAD-400 | 8-bit (existing) / 4-bit | `TRANSLATE_MADLAD_4BIT` | 0 (8-bit) |
| NLLB-200 | 8-bit | `NLLB_8BIT` | off (small already) |
| Florence / CLIP / caption / distilbart | fp16 (unchanged) | — | — |

bitsandbytes 8-bit is confirmed working on this Blackwell (sm_120) card. Each
quantized loader is validated to load **and** run one inference on sm_120 in
tests. 4-bit nf4 is preferred for the rewriter (faster load, ~3→1 GB at 1.5B,
~4.5 GB at 7B) over 8-bit (which pays the int8 autotune "warming up" stall).

**Accuracy evidence (2026-06-03, `eval/quant_accuracy/`).** MADLAD 8-bit vs
4-bit over 8 languages × 8 sentences, round-trip chrF (EN→L→EN): overall
**8-bit 76.7 vs 4-bit 77.2 (Δ +0.5)** — statistically a wash; sample forward
translations near-identical. Per-language swings (hi +9.3, ja −6.1, zh +4.3)
are round-trip-proxy noise at n=8; the only mild real signal is Japanese
degrading at 4-bit. Conclusion: quantization does **not** meaningfully hurt
translation quality, so (a) MADLAD stays 8-bit by default but the 4-bit
under-pressure fallback is trustworthy, and (b) 4-bit for the rewriter / C's 7B
is safe. Caveat: round-trip chrF is a proxy; the side-by-side samples are the
stronger signal and confirm parity.

## Footprint after this (hot translation set)

Florence 1.14 + MADLAD-8bit 3 + rewriter-4bit ~1 + NLLB 1.2 + TrOCR 0.3 ≈ 6.6 GB
+ 1.2 baseline ≈ **7.8 GB** → ~4 GB headroom. CLIP/caption/distilbart load for
image-tagging and evict when translation needs room. With C's 7B-4bit (~4.5 GB)
replacing the rewriter the hot set is ~11 GB — tight but safe under the guard.

## Error handling

- Eviction and OOM events are logged (which model, freed GB, retry/fallback
  taken) so the dashboard Models tab can surface thrash.
- `VramPressure` and the CPU-fallback path are the only new error states; no
  request returns a CUDA OOM 500.

## Testing (TDD)

- Unit: registry registration + LRU ordering; `ensure_room` evicts the right
  entries in the right order until free ≥ need; `VramPressure` raised when no
  evictables remain.
- Unit: OOM guard retries once then CPU-falls-back (inject a fake fn that raises
  `OutOfMemoryError` once/twice).
- Unit: quant config builders return the right `BitsAndBytesConfig` per env.
- Integration (GPU, opt-in marker): load Florence + MADLAD + rewriter-4bit
  together, assert resident ≤ budget and one OCR + one translate succeed; force
  a synthetic over-budget load and assert an eviction happened (not an OOM).
- Reuse `eval/locate_anything/vram_probe.py` to re-measure footprints and seed
  `est_gb`.

## Rollout

Additive and incremental: the manager wraps existing getters, so unmigrated call
sites keep working unmanaged. Migrate the translate + figure-OCR hot paths first
(they're what sub-projects A and C touch), then faces/summarize.
