"""CS10 — exponential backoff for Drive API calls.

Before this module, every `_drive_list_*` / `_drive_download` call
was a single-shot — a Drive 429 (rate limit) or 500/502/503/504
(server hiccup) propagated straight into the per-entry except clause
in `sync_user_provider`, which `logger.exception`'s + continues. For
a manual sync that means "we skipped 12 files; check the logs"; for
the hourly cron sweep it means "those 12 files are dark until the
operator manually re-syncs."

Wrap the Drive HTTP boundary with a small retry loop:

  * Retry on HTTP 429 + 5xx (no retry on 4xx other than 429 — those
    are bugs on our side and retrying doesn't help).
  * Exponential backoff with jitter: 1s, 2s, 4s, 8s, 16s. Five
    attempts total; if the fifth fails, propagate the original
    exception.
  * Honour `Retry-After` headers when Drive sends them — it usually
    does for 429s, and the server's number is more useful than our
    blind backoff.
  * Each retry logs at warning so operators can see the pattern in
    `journalctl` without enabling debug.

This is a synchronous helper because the Drive client itself is
synchronous; the cloud_sync module already wraps the whole call in
`asyncio.to_thread`, so blocking inside the retry is fine. Async
wrappers (Dropbox / OneDrive when they land) can either depend on
this synchronously or grow an async sibling — they're not pulled in
today, so the synchronous shape stays minimal.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 429 = rate limit; 5xx = server-side hiccup. 408 is "request timeout"
# which Drive occasionally returns on slow listings.
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Per-attempt base sleep (multiplied by 2**attempt) with a jitter
# window. The cap is 30s so we don't sleep longer than the per-
# request HTTP timeout.
_BASE_SLEEP_SECONDS = 1.0
_MAX_SLEEP_SECONDS = 30.0

DEFAULT_MAX_ATTEMPTS = 5


def _status_of(exc: BaseException) -> int | None:
    """Pull the HTTP status off a googleapiclient.errors.HttpError
    without importing the class up-front (the cloud_sync optional
    extra owns that dep). Returns None if `exc` isn't a known
    HTTP-style exception we can introspect."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if isinstance(status, int):
        return status
    # urllib3 / requests-style: exc.response.status_code
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    # Some googleapiclient builds expose status_code directly.
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Pull a Retry-After hint off the exception's response headers.

    Drive includes Retry-After on 429s when it knows the next
    available window. Respect it if present — the server's number
    almost always beats our blind exponential ramp.

    Returns None when no header is present, the header is malformed,
    or it parses to a non-positive number."""
    resp = getattr(exc, "resp", None)
    headers = None
    if resp is not None:
        # googleapiclient httplib2 response is dict-like.
        try:
            headers = dict(resp)
        except Exception:
            headers = None
    if headers is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
    if not headers:
        return None
    # Headers may be a dict or a Headers-like object; both support
    # `get` with case-insensitive lookup on most implementations.
    raw = None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return None
    if secs <= 0:
        return None
    return min(secs, _MAX_SLEEP_SECONDS)


def _sleep_for(attempt: int, exc: BaseException) -> float:
    """Compute the delay before the next attempt.

    Server-supplied Retry-After wins. Otherwise: 2**attempt seconds
    with [0, 0.5s] jitter, capped at MAX_SLEEP_SECONDS."""
    hint = _retry_after_seconds(exc)
    if hint is not None:
        return hint
    base = _BASE_SLEEP_SECONDS * (2 ** attempt)
    jitter = random.uniform(0, 0.5)  # noqa: S311 — non-crypto
    return min(base + jitter, _MAX_SLEEP_SECONDS)


def with_drive_retry(
    fn: Callable[[], T], *,
    op: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `fn` with retry-on-429-or-5xx semantics.

    `op` is a short label for logging (e.g. `"drive.files.list"`).
    `sleep` is injectable so tests can pass a no-op without burning
    real seconds in the suite.

    Non-retryable exceptions (4xx other than 429, or things that
    aren't HTTP-shaped at all) propagate immediately — there's no
    point retrying an `invalid_grant` or a Python TypeError."""
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — caller catches downstream
            status = _status_of(exc)
            if status is None or status not in _RETRYABLE_STATUSES:
                raise
            last_exc = exc
            if attempt + 1 >= max_attempts:
                logger.warning(
                    "drive_retry: %s exhausted after %d attempts (last status=%s)",
                    op, max_attempts, status,
                )
                raise
            delay = _sleep_for(attempt, exc)
            logger.warning(
                "drive_retry: %s got %s, sleeping %.2fs before retry %d/%d",
                op, status, delay, attempt + 2, max_attempts,
            )
            sleep(delay)
    # Defensive — loop returns or raises by attempt N-1.
    assert last_exc is not None
    raise last_exc


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "with_drive_retry",
]
