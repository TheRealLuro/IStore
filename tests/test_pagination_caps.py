"""Regression tests for D1 — unbounded `limit`/`offset` params.

Before, paginated endpoints had `limit: int = 100` with no
ge/le validator. A request with `limit=10_000_000` materialized
every Pydantic row for every match → memory DoS. Same shape on
the offset side: a request with `offset=-1` would either crash or
silently behave like 0, depending on the SQL driver.

The fix adds `Annotated[int, Query(ge=..., le=...)]` validators
at the routing layer. Bad input now 422s at FastAPI's validation
boundary before any DB work.

Tests confirm the signature-level guards on every endpoint the
audit flagged.
"""
from __future__ import annotations

import inspect
import typing

import pytest

from fastapi import Query


# Map of (module_path, function_name) → expected (min, max) for the
# `limit` arg. Set to `None` for endpoints where we don't care
# about a specific cap, only that one exists.
_GUARDED_ENDPOINTS = [
    ("backend.api.images", "list_images", "limit", 500),
    ("backend.api.images", "backfill_summary_embeddings", "limit", 5000),
    ("backend.api.images", "backfill_summaries", "limit", 2000),
    ("backend.api.images", "backfill_vision", "limit", 2000),
    ("backend.api.account", "get_account_activity", "limit", 200),
    ("backend.api.people", "regenerate_crops", "limit", 5000),
    ("backend.api.search", "semantic_search", "limit", 100),
]


def _query_metadata(fn, param_name: str) -> dict:
    """Pull ge/le constraints out of an `Annotated[int, Query(...)]`
    parameter. Two complications:

      1. FastAPI's Query stores constraints as a list of
         annotated_types objects on `.metadata` (e.g. `Ge(ge=1)`,
         `Le(le=500)`), NOT as direct attributes.
      2. Modules using `from __future__ import annotations` (in
         this codebase: account.py, people.py) keep all annotations
         as strings. `inspect.signature` returns those strings
         verbatim; we use `typing.get_type_hints(include_extras=True)`
         to evaluate them into real Annotated types.

    Returns {} if no ge/le metadata is present.
    """
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception:
        # If type-hint resolution fails (cycle, missing forward
        # ref), fall back to inspect.signature — covers the
        # not-stringified case.
        sig = inspect.signature(fn)
        param = sig.parameters.get(param_name)
        if param is None or param.annotation is inspect.Parameter.empty:
            return {}
        annotation = param.annotation
    else:
        annotation = hints.get(param_name)
        if annotation is None:
            return {}
    # Capture default separately since hints don't carry it.
    default = inspect.signature(fn).parameters[param_name].default

    args = typing.get_args(annotation)
    for a in args:
        meta = getattr(a, "metadata", None)
        if meta is None:
            continue
        ge_val: int | None = None
        le_val: int | None = None
        for m in meta:
            if hasattr(m, "ge") and getattr(m, "ge") is not None:
                ge_val = m.ge
            if hasattr(m, "le") and getattr(m, "le") is not None:
                le_val = m.le
        if ge_val is not None or le_val is not None:
            return {"ge": ge_val, "le": le_val, "default": default}
    return {}


@pytest.mark.parametrize(
    "module_path,fn_name,param,expected_max",
    _GUARDED_ENDPOINTS,
    ids=[f"{m.split('.')[-1]}.{n}" for m, n, _, _ in _GUARDED_ENDPOINTS],
)
def test_paginated_endpoint_has_le_cap(
    module_path: str, fn_name: str, param: str, expected_max: int,
) -> None:
    """Every endpoint the audit D1 flagged must have a `le=` cap on
    `limit`. The exact max is pinned so a future PR that loosens it
    has to update this test (forces explicit auditor sign-off)."""
    import importlib
    module = importlib.import_module(module_path)
    fn = getattr(module, fn_name)
    meta = _query_metadata(fn, param)
    assert meta, (
        f"{module_path}.{fn_name}: param `{param}` has no Query metadata. "
        "Was the `Annotated[int, Query(ge=…, le=…)]` annotation dropped?"
    )
    assert meta["le"] == expected_max, (
        f"{module_path}.{fn_name}: param `{param}` cap is "
        f"{meta['le']!r}, expected {expected_max}. If the cap intentionally "
        "changed, update _GUARDED_ENDPOINTS in this test."
    )


@pytest.mark.parametrize(
    "module_path,fn_name,param,_max",
    _GUARDED_ENDPOINTS,
    ids=[f"{m.split('.')[-1]}.{n}" for m, n, _, _ in _GUARDED_ENDPOINTS],
)
def test_paginated_endpoint_has_ge_floor(
    module_path: str, fn_name: str, param: str, _max: int,
) -> None:
    """Negative limits are nonsense — pin `ge=1` so callers
    submitting `limit=-1` or `limit=0` get a 422 instead of an
    empty list or a DB error."""
    import importlib
    module = importlib.import_module(module_path)
    fn = getattr(module, fn_name)
    meta = _query_metadata(fn, param)
    assert meta.get("ge") == 1, (
        f"{module_path}.{fn_name}: param `{param}` ge is "
        f"{meta.get('ge')!r}, expected 1."
    )


def test_list_images_offset_has_ge_floor() -> None:
    """The `offset` param on list_images was the other half of the
    D1 finding — negative offsets shouldn't make it past validation."""
    from backend.api.images import list_images
    meta = _query_metadata(list_images, "offset")
    assert meta.get("ge") == 0
    # Upper bound is more permissive (operators may page deep into
    # a long library) but still finite to discourage scan attacks.
    assert meta.get("le") is not None and meta["le"] >= 100_000
