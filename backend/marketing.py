"""Marketing-site newsletter integration.

When a signed-in neuthek user ticks the newsletter opt-in, the app
backend records the consent locally (append-only `consent_records` +
`audit_log`, see backend/api/account.py) and then forwards their email
to the *marketing site's* newsletter subscriber list.

The two systems run as separate services with separate databases (the
app's Postgres vs. the marketing Express server's Postgres/SQLite), so
the cleanest integration channel is a small, token-authenticated HTTP
call to the marketing server's internal endpoint
`POST /api/internal/newsletter/subscribe` (see marketing/server.mjs).
Because the app has already password-authenticated the account, the
caller is asserting "this address is proven" — the marketing side
records the row as verified=true and skips its own confirm-email step.

Design notes
------------
* **Best-effort.** A failure here never fails the user's opt-in request:
  the local consent record is the source of truth, and the caller logs
  the sync outcome so a later reconciliation/retry job (or an admin) can
  re-push. This mirrors how recovery-code emails are best-effort in
  backend/api/account.py.
* **Disabled-by-default.** If `marketing_base_url` or
  `marketing_internal_token` is unset (the self-host / dev default), the
  forward is skipped cleanly — `subscribe_to_marketing_newsletter`
  returns a `skipped` result rather than raising. Self-hosters who don't
  run the marketing site are unaffected.
* **Idempotent.** The marketing endpoint upserts on the email UNIQUE
  constraint, so re-ticking the box never creates a duplicate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# Path on the marketing server. Joined to settings.marketing_base_url.
_SUBSCRIBE_PATH = "/api/internal/newsletter/subscribe"


@dataclass(slots=True)
class MarketingSyncResult:
    """Outcome of a marketing-newsletter forward.

    `ok` is True only when the marketing server confirmed the
    subscription. `skipped` is True when the integration is not
    configured (no base URL / token) — distinct from a real failure so
    the caller can record "consent saved, forward not attempted" without
    surfacing an error. `detail` carries a short machine-ish reason for
    logs/audit (never the email).
    """

    ok: bool
    skipped: bool = False
    detail: str | None = None


def is_configured() -> bool:
    """True when both the marketing base URL and the shared token are
    set, so the forward can actually be attempted."""
    return bool(settings.marketing_base_url) and bool(
        settings.marketing_internal_token
    )


async def subscribe_to_marketing_newsletter(
    email: str,
    *,
    source: str = "neuthek-app",
) -> MarketingSyncResult:
    """Forward an opted-in user's email to the marketing newsletter list.

    Never raises — every failure mode returns a `MarketingSyncResult`
    with `ok=False` so the caller's transaction (recording the local
    consent) is never rolled back by a marketing-side hiccup.
    """
    if not is_configured():
        logger.info(
            "marketing newsletter forward skipped: integration not configured"
        )
        return MarketingSyncResult(ok=False, skipped=True, detail="not-configured")

    url = settings.marketing_base_url.rstrip("/") + _SUBSCRIBE_PATH
    headers = {
        "Authorization": f"Bearer {settings.marketing_internal_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.marketing_http_timeout_seconds
        ) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={"email": email, "source": source},
            )
    except httpx.HTTPError as exc:
        # Network-level failure (DNS, connect, timeout, TLS). Log without
        # the email so the audit/log trail stays PII-light.
        logger.warning("marketing newsletter forward network error: %s", exc)
        return MarketingSyncResult(ok=False, detail="network-error")

    if resp.status_code >= 400:
        logger.warning(
            "marketing newsletter forward rejected: HTTP %s", resp.status_code
        )
        return MarketingSyncResult(
            ok=False, detail=f"http-{resp.status_code}"
        )

    return MarketingSyncResult(ok=True)
