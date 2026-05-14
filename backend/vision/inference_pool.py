"""Single-thread executor for ML inference.

Every Florence-2 / Qwen / RetinaFace / OpenCLIP call goes through this
pool so concurrent summarize background tasks don't race for the GIL.

Why a single thread? CPU torch ops release the GIL during native compute,
but the surrounding Python — beam search loops, tokenizer post-processing,
Pillow decode — all hold it. With the default `asyncio.to_thread`
executor (~12 threads), N concurrent summarize calls each load Florence
into a different worker thread and then thrash on the GIL. The asyncio
event loop runs on the main thread; when N worker threads are all
trying to make Python progress, the main thread starves and `POST
/auth/jwt/login` ends up queueing for tens of seconds.

Routing every ML call through a single worker:
  - serializes inferences (one-at-a-time instead of N-at-once),
  - leaves N-1 threads of the default pool free for non-ML async I/O
    (DB queries, MinIO fetches, etc.),
  - keeps the event loop responsive — login still goes through within a
    few hundred ms of GIL-release windows.

This is the dev-friendly fix; the proper fix (full process isolation
via `ProcessPoolExecutor`) is a bigger refactor because the worker
needs picklable args. We can layer that on top of this without
changing call sites — the public API is the same `await
run_in_inference_pool(fn, *args)`.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Module-level single-thread executor. Created lazily on first use so
# importing this module from a process that never runs inference
# (test runners, alembic migrations) doesn't spin up a worker.
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ml-inference",
        )
    return _executor


async def run_in_inference_pool(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Submit `fn(*args, **kwargs)` to the dedicated ML thread and await.

    Drop-in replacement for `asyncio.to_thread(fn, *args, **kwargs)` in
    every place that calls a model. The single worker means callers
    queue behind any in-flight inference rather than racing for the
    GIL with it.
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        # `run_in_executor` doesn't accept kwargs; wrap if needed.
        from functools import partial
        return await loop.run_in_executor(_get_executor(), partial(fn, *args, **kwargs))
    return await loop.run_in_executor(_get_executor(), fn, *args)
