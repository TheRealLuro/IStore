"""Regression tests for CS10 — Drive API exponential backoff.

Before this fix, `_drive_download` and `_drive_collect_entries` had
no retry logic: a single Drive 429 (rate limit) or 5xx response on
`.execute()` or `next_chunk()` propagated all the way to
`sync_user_provider`'s per-entry `except Exception:` block, which
`logger.exception`'d and skipped to the next file. For the hourly
cron sweep, that means transient errors silently drop files until
the next sweep — and a sustained rate-limit window can starve a
fresh sync entirely.

`with_drive_retry` wraps each Drive boundary call in a 5-attempt
loop with exponential backoff. The retry triggers on the standard
retryable status codes (408 / 429 / 5xx) and respects Retry-After
when Drive provides it.

Tests use a fake "response" object shaped like googleapiclient's
`HttpError.resp` so the helper's introspection finds the status
without us pulling in the full google-api-python-client dep tree.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.cloud_sync_retry import (
    DEFAULT_MAX_ATTEMPTS,
    _retry_after_seconds,
    _status_of,
    with_drive_retry,
)


class _FakeResp(dict):
    """Mimics googleapiclient.http.HttpResponse minimally — dict-like
    headers + a `.status` integer attribute on the response object."""

    def __init__(self, status: int, headers: dict | None = None):
        super().__init__(headers or {})
        self.status = status


class _FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError just enough for the
    retry helper's introspection."""

    def __init__(self, status: int, headers: dict | None = None):
        super().__init__(f"HTTP {status}")
        self.resp = _FakeResp(status, headers)


# ----- status / header introspection -----


def test_status_of_extracts_resp_status():
    exc = _FakeHttpError(429)
    assert _status_of(exc) == 429


def test_status_of_returns_none_for_non_http():
    assert _status_of(ValueError("not an http error")) is None


def test_retry_after_parses_int_header():
    exc = _FakeHttpError(429, {"Retry-After": "5"})
    assert _retry_after_seconds(exc) == 5.0


def test_retry_after_returns_none_when_absent():
    exc = _FakeHttpError(429)
    assert _retry_after_seconds(exc) is None


def test_retry_after_returns_none_on_garbage():
    exc = _FakeHttpError(429, {"Retry-After": "not-a-number"})
    assert _retry_after_seconds(exc) is None


def test_retry_after_caps_at_max_sleep():
    """Drive occasionally returns Retry-After: 3600 (an hour) — we
    cap it because our HTTP timeout doesn't span that long anyway."""
    exc = _FakeHttpError(429, {"Retry-After": "9999"})
    # Cap is 30s (MAX_SLEEP_SECONDS); anything longer collapses.
    assert _retry_after_seconds(exc) == 30.0


# ----- retry behaviour -----


def test_success_first_try_no_sleep():
    """Happy path: fn succeeds immediately, sleep is never called."""
    sleeps: list[float] = []
    result = with_drive_retry(
        lambda: 42,
        op="happy",
        sleep=lambda s: sleeps.append(s),
    )
    assert result == 42
    assert sleeps == []


def test_retry_on_429_then_succeeds():
    """429 → sleep → retry → success. Verifies the retry path runs
    AND the helper returns the eventual success value."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeHttpError(429)
        return "ok"

    result = with_drive_retry(fn, op="test", sleep=lambda s: sleeps.append(s))
    assert result == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # two retries → two sleeps before the third (winning) attempt


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_retry_on_each_retryable_status(status_code: int):
    """Every status in the retryable set should be caught + retried.
    A non-retryable status (e.g. 403) propagates immediately."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeHttpError(status_code)
        return "ok"

    result = with_drive_retry(fn, op="test", sleep=lambda _s: None)
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_non_retryable_status_raises_immediately(status_code: int):
    """A 4xx other than 408/429 is a bug on our side (bad request,
    revoked token, permission). Retrying doesn't help — propagate."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _FakeHttpError(status_code)

    with pytest.raises(_FakeHttpError):
        with_drive_retry(fn, op="test", sleep=lambda _s: None)
    assert calls["n"] == 1, "non-retryable status should NOT have been retried"


def test_non_http_exception_propagates():
    """A plain Python exception (TypeError, ValueError, …) is a bug,
    not a transient issue — must propagate without sleep."""
    sleeps: list[float] = []

    def fn():
        raise ValueError("not a Drive error")

    with pytest.raises(ValueError):
        with_drive_retry(fn, op="test", sleep=lambda s: sleeps.append(s))
    assert sleeps == []


def test_exhausts_after_max_attempts():
    """After DEFAULT_MAX_ATTEMPTS retryable failures, the last
    exception propagates. Verifies we don't loop forever on a
    sustained outage."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _FakeHttpError(503)

    with pytest.raises(_FakeHttpError):
        with_drive_retry(fn, op="test", sleep=lambda _s: None)
    assert calls["n"] == DEFAULT_MAX_ATTEMPTS


def test_respects_retry_after_header():
    """When Drive sends Retry-After, the helper uses it instead of
    blind exponential backoff. We verify by inspecting the sleeps
    list — the first sleep should match the header."""
    sleeps: list[float] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeHttpError(429, {"Retry-After": "7"})
        return "done"

    result = with_drive_retry(fn, op="test", sleep=lambda s: sleeps.append(s))
    assert result == "done"
    assert sleeps == [7.0]


# ----- integration with cloud_sync.py call sites -----


def test_drive_download_wraps_in_retry():
    """The real production wrapper plumbs `with_drive_retry` around
    the `MediaIoBaseDownload` loop. We verify this with a patched
    import: if _drive_download didn't call with_drive_retry, the
    test would see `op=None`."""
    from backend import cloud_sync

    seen_ops: list[str] = []

    def _stub_retry(fn, *, op, **_kwargs):
        seen_ops.append(op)
        return fn()

    # Build a stub Drive client that returns a fake request whose
    # `MediaIoBaseDownload` ticks done immediately.
    class _StubRequest:
        pass

    class _StubFiles:
        def get_media(self, fileId):  # noqa: N803, ARG002
            return _StubRequest()

    class _StubDrive:
        def files(self):
            return _StubFiles()

    class _StubDownloader:
        def __init__(self, *_args, **_kwargs):
            self._done = False

        def next_chunk(self):
            self._done = True
            return (None, True)

    with patch.object(cloud_sync, "_drive_download") as _ignore:
        # We want the real _drive_download, not the patch — drop the
        # patch and use the real function with the stubbed helpers.
        pass

    with patch(
        "backend.cloud_sync_retry.with_drive_retry",
        side_effect=_stub_retry,
    ), patch(
        "googleapiclient.http.MediaIoBaseDownload",
        side_effect=_StubDownloader,
    ):
        cloud_sync._drive_download(_StubDrive(), "fake-file-id")

    assert any("drive.files.get_media" in op for op in seen_ops), (
        f"_drive_download should wrap MediaIoBaseDownload in with_drive_retry; "
        f"saw ops {seen_ops!r}"
    )
