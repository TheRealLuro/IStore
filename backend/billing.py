"""Stripe SDK wrapper.

Lazy-init pattern: the `stripe` module is imported on first use rather
than at app load so that:
  (a) the API still boots when `STRIPE_SECRET_KEY` is empty (dev /
      self-host),
  (b) test fixtures can monkeypatch `_client()` without the real SDK
      ever making a network call.

What lives here:
  - `_client()` — returns the configured stripe module with the
    secret key set, or raises `BillingDisabledError` if not configured.
  - `get_or_create_customer(session, user)` — idempotent Stripe Customer
    fetch keyed by `subscriptions.stripe_customer_id`. The first call
    creates a Customer with the user's email; subsequent calls reuse it.
  - `create_checkout_session(...)` — builds an Embedded Checkout session
    for a (tier, interval) pair and returns Stripe's `client_secret`
    string (the FE mounts the form with this).
  - `create_portal_session(...)` — short-lived Customer Portal URL for
    plan management, card swap, cancellation.
  - `verify_webhook(payload, signature)` — wraps the SDK's signature
    check so callers don't import stripe types.
  - `tier_from_price_id(price_id)` — reverse-maps a Stripe Price ID to
    our internal tier + interval. Used by the webhook handler.

The catalog (plans + which env-var holds which Price ID) lives in
the `plans` table — see migration 0025. This module reads the table
once and caches the env-var-name → tier/interval map for the lifetime
of the process.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Plan, Subscription, User

logger = logging.getLogger(__name__)


class BillingDisabledError(RuntimeError):
    """Raised when a billing operation is attempted while
    `STRIPE_SECRET_KEY` is unset. Route handlers catch this and
    return 503 with an operator-readable detail."""


class BillingConfigError(RuntimeError):
    """Raised when a (tier, interval) pair has no corresponding
    Price ID in env — e.g. the operator forgot to set
    `STRIPE_PRICE_ID_BUSINESS_ANNUAL`. Route handlers map this to 400
    so the FE can surface "this plan isn't available yet"."""


def _client():
    """Return the `stripe` module configured with the live secret key.

    Stripe's SDK is package-global state (you set `stripe.api_key =
    ...` once). We set it on every call — cheap (a string assignment)
    and safer than relying on import-time setup, especially under
    test where settings can be monkeypatched mid-run.
    """
    if not settings.stripe_enabled:
        raise BillingDisabledError(
            "Stripe is not configured on this deployment. "
            "Set STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET in .env."
        )
    import stripe  # noqa: PLC0415 — lazy on purpose; see module docstring

    stripe.api_key = settings.stripe_secret_key
    return stripe


@dataclass(frozen=True)
class TierLookup:
    tier: str
    interval: str  # 'month' | 'year'


_PRICE_ENV_BY_FIELD = {
    ("pro", "month"): "stripe_price_id_pro_monthly",
    ("pro", "year"): "stripe_price_id_pro_annual",
    ("business", "month"): "stripe_price_id_business_monthly",
    ("business", "year"): "stripe_price_id_business_annual",
}


def price_id_for(tier: str, interval: str) -> str:
    """Read the configured Stripe Price ID for a (tier, interval) pair.

    `interval` is the Stripe-side spelling: `month` or `year`. We accept
    `monthly`/`annual` as aliases so the FE doesn't have to remember
    which dialect is which.
    """
    norm_interval = {"monthly": "month", "annual": "year"}.get(interval, interval)
    field = _PRICE_ENV_BY_FIELD.get((tier, norm_interval))
    if field is None:
        raise BillingConfigError(f"Unknown plan: {tier}/{interval}")
    value = getattr(settings, field, "")
    if not value:
        raise BillingConfigError(
            f"This plan isn't available yet — set the Stripe Price ID in env "
            f"({field.upper()})."
        )
    return value


def tier_from_price_id(price_id: str) -> TierLookup | None:
    """Reverse lookup. Walks the configured Price IDs and returns the
    matching (tier, interval) tuple, or None if no field matches.

    Stripe sends `price_id` in the subscription object on
    `customer.subscription.{created,updated}`, so this is how the
    webhook handler decides which `tier` to write to the
    `subscriptions` row.
    """
    if not price_id:
        return None
    for (tier, interval), field in _PRICE_ENV_BY_FIELD.items():
        if getattr(settings, field, "") == price_id:
            return TierLookup(tier=tier, interval=interval)
    return None


async def get_or_create_customer(session: AsyncSession, user: User) -> str:
    """Idempotent — returns the user's Stripe Customer id, creating
    one if the subscription row doesn't have it yet.

    We persist the customer id to `subscriptions.stripe_customer_id`
    so the next call is a single SELECT, not a network round-trip.
    The row is also created here if the user has never touched billing.
    """
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
    ).scalar_one_or_none()

    if sub and sub.stripe_customer_id:
        return sub.stripe_customer_id

    stripe = _client()
    # `email` is what shows up in Stripe Dashboard and on receipts;
    # `metadata.user_id` is how we reconcile if the customer id ever
    # goes missing from our DB (manual recovery path).
    customer = stripe.Customer.create(
        email=user.email,
        metadata={"user_id": str(user.id)},
    )

    if sub is None:
        sub = Subscription(
            user_id=user.id,
            tier="free",
            status="active",
            stripe_customer_id=customer.id,
        )
        session.add(sub)
    else:
        sub.stripe_customer_id = customer.id
    await session.commit()
    return customer.id


async def create_checkout_session(
    session: AsyncSession,
    user: User,
    *,
    tier: str,
    interval: str,
) -> dict[str, Any]:
    """Build an Embedded Checkout session for (tier, interval) and
    return the `client_secret` the FE uses to mount the form.

    Embedded vs hosted: `ui_mode='embedded'` keeps the user on our
    domain — Stripe renders the form inside an iframe-managed widget
    via @stripe/stripe-js. The user never sees `checkout.stripe.com`
    in the URL bar. PCI-compliant by default; card data never touches
    our servers.

    The `return_url` is where Stripe sends the browser after a
    successful submit. The query string `?session_id=...` lets the
    FE poll `/billing/subscription` until the webhook lands and
    flips the tier — that webhook is the source of truth, not the
    return URL.
    """
    # Bail out at the top when Stripe isn't configured at all — gives
    # the user a "billing not enabled" message instead of the more
    # specific "this Price ID isn't set" rejection that would fire
    # next if we kept going.
    if not settings.stripe_enabled:
        raise BillingDisabledError(
            "Stripe is not configured on this deployment. "
            "Set STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET in .env."
        )
    if tier == "free":
        raise BillingConfigError("Free tier does not require checkout.")
    price_id = price_id_for(tier, interval)
    customer_id = await get_or_create_customer(session, user)

    stripe = _client()
    checkout = stripe.checkout.Session.create(
        mode="subscription",
        ui_mode="embedded",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        return_url=settings.stripe_checkout_return_url,
        # Lock the customer-update preferences so the embedded form
        # doesn't surprise the user with "create an account" prompts
        # — they're already signed in to neuthek.
        customer_update={"name": "auto", "address": "auto"},
        # Surface our tier/interval back on the webhook so we don't
        # have to re-derive it from the Price ID if Stripe rotates
        # IDs between deploys.
        subscription_data={
            "metadata": {
                "user_id": str(user.id),
                "tier": tier,
                "interval": interval,
            },
        },
        # Tax handling is opt-in; leave off until the operator
        # configures Tax in the dashboard. Without this Stripe will
        # collect VAT/GST/etc. and we don't have remittance set up.
        automatic_tax={"enabled": False},
    )
    return {
        "client_secret": checkout.client_secret,
        "session_id": checkout.id,
    }


async def create_portal_session(
    session: AsyncSession, user: User, *, return_url: str
) -> str:
    """Short-lived Stripe Customer Portal URL. The portal lets the
    user swap cards, cancel, change plan, and download invoices —
    every billing UI we'd otherwise have to build ourselves.
    """
    customer_id = await get_or_create_customer(session, user)
    stripe = _client()
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return portal.url


def verify_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    """Validate a Stripe webhook payload and return the parsed event.

    Stripe signs every webhook delivery with
    `Stripe-Signature: t=...,v1=...` against `STRIPE_WEBHOOK_SECRET`.
    The SDK's `construct_event` raises if the signature doesn't match
    or the timestamp is stale (default tolerance 5 minutes — prevents
    a captured payload from being re-played later).
    """
    if not settings.stripe_webhook_secret:
        raise BillingDisabledError(
            "Webhook secret is not configured. "
            "Set STRIPE_WEBHOOK_SECRET in .env."
        )
    stripe = _client()
    # `construct_event` returns a `stripe.Event` (dict-like); convert
    # to a plain dict so callers don't depend on SDK types.
    event = stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature,
        secret=settings.stripe_webhook_secret,
    )
    return dict(event)


async def load_plan(session: AsyncSession, tier: str) -> Plan | None:
    return (
        await session.execute(select(Plan).where(Plan.tier == tier))
    ).scalar_one_or_none()


async def load_all_plans(session: AsyncSession) -> list[Plan]:
    return list(
        (
            await session.execute(
                select(Plan).order_by(Plan.sort_order)
            )
        ).scalars().all()
    )


async def resolve_subscription(
    session: AsyncSession, user_id: UUID
) -> Subscription | None:
    return (
        await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
    ).scalar_one_or_none()
