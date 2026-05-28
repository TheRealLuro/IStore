"""Stripe billing — checkout, portal, webhooks, subscription state.

Routes:
  GET    /billing/plans        Public list of tiers + features + price.
                               Drives the FE pricing page.
  GET    /billing/subscription Current user's tier + status. Drives the
                               in-app PlanCard.
  POST   /billing/checkout     Body `{ tier, interval }` → returns a
                               Stripe Embedded Checkout `client_secret`
                               the FE mounts inline.
  POST   /billing/portal       Returns a short-lived Customer Portal URL
                               for plan management + cancellation.
  POST   /billing/webhook      Stripe → us. Signature-verified, idempotent.

Webhook → DB → quota:
  - `checkout.session.completed`         → upsert `subscriptions` with
                                            tier/interval/customer/sub id.
  - `customer.subscription.created`      → ensure row, set period bounds.
  - `customer.subscription.updated`      → tier swap or status flip.
  - `customer.subscription.deleted`      → tier = 'free', status =
                                            'canceled', clear sub id.
  - `invoice.payment_failed`             → status = 'past_due'; don't
                                            yank tier yet (Stripe retries).

Idempotency is enforced via the `stripe_events` table. The webhook
returns 200 on a duplicate without re-applying side effects.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.billing import (
    BillingConfigError,
    BillingDisabledError,
    create_checkout_session,
    create_portal_session,
    load_all_plans,
    load_plan,
    resolve_subscription,
    tier_from_price_id,
    verify_webhook,
)
from backend.config import settings
from backend.db import get_session, SessionLocal
from backend.models import Plan, StripeEvent, Subscription, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


# ---------- Public: plans + current subscription ----------


class PlanRead(BaseModel):
    tier: str
    display_name: str
    monthly_cents: int | None
    annual_cents: int | None
    quota_bytes: int
    upload_max_per_hour: int
    upload_max_bytes_per_day: int
    features: dict[str, Any]
    monthly_available: bool
    annual_available: bool


def _plan_to_read(plan: Plan) -> PlanRead:
    """Public projection of a Plan row. `*_available` flips false for
    paid tiers when the operator hasn't set the matching Price ID
    env var yet — the FE uses this to disable the upgrade button
    rather than 400 on click."""
    monthly_env = plan.monthly_price_id_env
    annual_env = plan.annual_price_id_env
    monthly_avail = bool(monthly_env and getattr(settings, monthly_env.lower(), ""))
    annual_avail = bool(annual_env and getattr(settings, annual_env.lower(), ""))
    if plan.tier == "free":
        monthly_avail = True
        annual_avail = True
    return PlanRead(
        tier=plan.tier,
        display_name=plan.display_name,
        monthly_cents=plan.monthly_cents,
        annual_cents=plan.annual_cents,
        quota_bytes=plan.quota_bytes,
        upload_max_per_hour=plan.upload_max_per_hour,
        upload_max_bytes_per_day=plan.upload_max_bytes_per_day,
        features=plan.features or {},
        monthly_available=monthly_avail,
        annual_available=annual_avail,
    )


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PlanRead]:
    """Public — no auth required. The pricing page on marketing reads
    the same shape, so the in-app and out-of-app pricing stay in sync."""
    plans = await load_all_plans(session)
    return [_plan_to_read(p) for p in plans]


class SubscriptionRead(BaseModel):
    tier: str
    status: str
    interval: str | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    stripe_configured: bool
    # The publishable key is safe to expose — it's how the FE
    # initializes Stripe.js. Returning it via this endpoint avoids
    # baking it into the FE build and lets ops rotate it without a
    # redeploy.
    publishable_key: str


@router.get("/subscription", response_model=SubscriptionRead)
async def get_subscription(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionRead:
    sub = await resolve_subscription(session, user.id)
    if sub is None:
        # No row = free tier (the user never touched billing).
        return SubscriptionRead(
            tier="free",
            status="active",
            interval=None,
            current_period_end=None,
            cancel_at_period_end=False,
            stripe_configured=settings.stripe_enabled,
            publishable_key=settings.stripe_publishable_key,
        )
    return SubscriptionRead(
        tier=sub.tier,
        status=sub.status,
        interval=sub.interval,
        current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        stripe_configured=settings.stripe_enabled,
        publishable_key=settings.stripe_publishable_key,
    )


# ---------- Checkout + portal ----------


class CheckoutRequest(BaseModel):
    tier: str
    # 'monthly' | 'annual' — accepted as aliases for 'month' | 'year'.
    interval: str = "monthly"


class CheckoutResponse(BaseModel):
    client_secret: str
    session_id: str
    publishable_key: str


@router.post("/checkout", response_model=CheckoutResponse)
async def post_checkout(
    body: CheckoutRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CheckoutResponse:
    """Create an Embedded Checkout session. The FE renders the form
    on our domain via `@stripe/stripe-js` `initEmbeddedCheckout`.
    """
    try:
        result = await create_checkout_session(
            session, user, tier=body.tier, interval=body.interval
        )
    except BillingDisabledError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except BillingConfigError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        logger.exception("checkout session create failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Stripe rejected the checkout request: {exc}"
        ) from exc
    return CheckoutResponse(
        client_secret=result["client_secret"],
        session_id=result["session_id"],
        publishable_key=settings.stripe_publishable_key,
    )


class PortalRequest(BaseModel):
    return_url: str = ""


class PortalResponse(BaseModel):
    url: str


@router.post("/portal", response_model=PortalResponse)
async def post_portal(
    body: PortalRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PortalResponse:
    """Build a Stripe Customer Portal URL for plan / card / cancellation
    management. Stripe handles every billing UI we'd otherwise build —
    invoices, swap card, change plan, view receipts."""
    return_url = body.return_url or settings.frontend_base_url
    try:
        url = await create_portal_session(session, user, return_url=return_url)
    except BillingDisabledError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception as exc:
        logger.exception("portal session create failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Stripe rejected the portal request: {exc}"
        ) from exc
    return PortalResponse(url=url)


# ---------- Webhook ----------
#
# Stripe POSTs every billing event here. We verify the signature with
# `STRIPE_WEBHOOK_SECRET`, idempotency-check via `stripe_events.id`,
# then dispatch on `event.type`.
#
# Why we don't trust the redirect-after-checkout for tier flips:
# the user can close the tab before the redirect fires, the
# `return_url` is HTTP and can be tampered with, and a hostile
# client can forge `?session_id=…`. The webhook is signed by Stripe
# and is the only source of truth for subscription state.


_HANDLED_EVENT_TYPES = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "invoice.payment_succeeded",
}


@router.post("/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Stripe webhook receiver. Lives outside auth — Stripe signs
    every delivery and that signature IS the auth boundary.

    On success returns `{"received": true}`; Stripe retries on any
    non-2xx for up to 3 days, so a 500 here is recoverable. The
    handler is intentionally tolerant — unknown event types return
    200 without side effects so we don't accumulate retry storms
    when Stripe adds new event shapes.
    """
    sig = request.headers.get("stripe-signature", "")
    payload = await request.body()
    try:
        event = verify_webhook(payload, sig)
    except BillingDisabledError as exc:
        # Webhook secret missing — fail closed. A misconfigured prod
        # is worse than a missed event; the operator needs to notice.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception as exc:
        logger.warning("stripe webhook signature rejected: %s", exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Invalid webhook signature: {exc}"
        ) from exc

    event_id = event.get("id") or ""
    event_type = event.get("type") or ""
    if not event_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Event missing id")

    # The webhook is unauthenticated (Stripe-signed) and resolves the affected
    # user by Stripe customer id, NOT a logged-in session — a legitimately
    # cross-user operation. Once `subscriptions` is under FORCE RLS (migration
    # 0048) and the app connects as a non-superuser (F7), a no-context session
    # would match zero subscription rows. Opt into the audited RLS bypass for
    # this handler's session; db.py re-stamps it on every transaction so it
    # survives the multiple commits below. ContextVar-scoped → no pool leak.
    from backend.context import reset_rls_bypass, set_rls_bypass
    _bypass_token = set_rls_bypass(True)
    try:
        return await _stripe_webhook_body(event, event_id, event_type)
    finally:
        reset_rls_bypass(_bypass_token)


async def _stripe_webhook_body(event, event_id: str, event_type: str) -> dict[str, Any]:
    # Idempotency: a unique-constraint violation on insert means we've
    # already processed this event. Return 200 so Stripe stops retrying.
    async with SessionLocal() as session:
        existing = await session.get(StripeEvent, event_id)
        if existing and existing.processed_at is not None:
            return {"received": True, "duplicate": True}
        if existing is None:
            session.add(StripeEvent(id=event_id, type=event_type))
            try:
                await session.commit()
            except Exception:
                # Race with a concurrent retry — treat as duplicate.
                await session.rollback()
                return {"received": True, "duplicate": True}

        if event_type in _HANDLED_EVENT_TYPES:
            try:
                await _dispatch(session, event)
                existing = await session.get(StripeEvent, event_id)
                if existing is not None:
                    existing.processed_at = datetime.now(timezone.utc)
                await session.commit()
            except Exception:
                logger.exception("webhook handler failed for %s", event_type)
                # Leave processed_at NULL so Stripe's retry re-runs us.
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "Handler error; Stripe will retry.",
                )

    return {"received": True, "type": event_type}


async def _dispatch(session: AsyncSession, event: dict[str, Any]) -> None:
    """Route a verified event to its handler. Handlers must be
    idempotent — Stripe can deliver the same event multiple times if
    a previous retry timed out before our DB write committed."""
    et = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}
    if et == "checkout.session.completed":
        await _handle_checkout_completed(session, data)
    elif et in ("customer.subscription.created", "customer.subscription.updated"):
        await _handle_subscription_change(session, data)
    elif et == "customer.subscription.deleted":
        await _handle_subscription_deleted(session, data)
    elif et == "invoice.payment_failed":
        await _handle_payment_failed(session, data)
    elif et == "invoice.payment_succeeded":
        # No-op for now — the subscription event already moved us out
        # of `past_due`. We just want to ack so Stripe stops retrying.
        return


def _resolve_tier_from_subscription(sub_obj: dict[str, Any]) -> tuple[str, str] | None:
    """Extract (tier, interval) from a Stripe Subscription payload.

    Preferred path: read our own `metadata.tier` + `metadata.interval`
    that `create_checkout_session` stamped on. Fallback: look up the
    Price ID against our env-var map.
    """
    meta = sub_obj.get("metadata") or {}
    tier = meta.get("tier") or ""
    interval = meta.get("interval") or ""
    if tier and interval:
        return tier, interval

    items = (sub_obj.get("items") or {}).get("data") or []
    if not items:
        return None
    price = (items[0].get("price") or {})
    price_id = price.get("id") or ""
    lookup = tier_from_price_id(price_id)
    if lookup is None:
        # Last resort: derive interval from the recurring shape and
        # leave tier=None so the row stays in its previous state.
        return None
    return lookup.tier, lookup.interval


async def _upsert_subscription_for_customer(
    session: AsyncSession,
    customer_id: str,
    sub_obj: dict[str, Any],
) -> Subscription | None:
    """Find the local subscription row for a Stripe customer and
    update it from a Subscription payload. Returns None if we can't
    map the customer to a user (the only sane reason is a webhook
    arriving before the matching checkout.session.completed; the
    retry will succeed once the row exists)."""
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_customer_id == customer_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        # The checkout.session.completed handler creates the row; if
        # this event arrives first (rare but possible), we no-op and
        # let Stripe retry.
        logger.info(
            "subscription event for unknown customer %s — Stripe will retry",
            customer_id,
        )
        return None

    resolved = _resolve_tier_from_subscription(sub_obj)
    if resolved is not None:
        sub.tier, sub.interval = resolved
    sub.stripe_subscription_id = sub_obj.get("id") or sub.stripe_subscription_id
    sub.status = sub_obj.get("status") or sub.status
    sub.cancel_at_period_end = bool(sub_obj.get("cancel_at_period_end"))
    start = sub_obj.get("current_period_start")
    end = sub_obj.get("current_period_end")
    if isinstance(start, (int, float)):
        sub.current_period_start = datetime.fromtimestamp(start, tz=timezone.utc)
    if isinstance(end, (int, float)):
        sub.current_period_end = datetime.fromtimestamp(end, tz=timezone.utc)
    sub.updated_at = datetime.now(timezone.utc)
    await _apply_tier_to_user(session, sub)
    return sub


async def _handle_checkout_completed(session: AsyncSession, obj: dict[str, Any]) -> None:
    """First successful payment. We have the customer id and (via the
    `subscription` id) the new subscription. Create/update the row,
    flip the tier from `free`, and stamp period bounds.

    The subscription object may not be inlined on the session payload
    depending on API version, so we fall back to a one-shot SDK read
    to fetch it fresh.
    """
    customer_id = obj.get("customer") or ""
    sub_id = obj.get("subscription") or ""
    if not customer_id or not sub_id:
        logger.warning("checkout.session.completed missing customer/subscription ids")
        return

    from backend.billing import _client  # avoid circular at module load

    stripe = _client()
    sub_obj = stripe.Subscription.retrieve(sub_id)
    await _upsert_subscription_for_customer(session, customer_id, dict(sub_obj))


async def _handle_subscription_change(session: AsyncSession, obj: dict[str, Any]) -> None:
    customer_id = obj.get("customer") or ""
    if not customer_id:
        return
    await _upsert_subscription_for_customer(session, customer_id, obj)


async def _handle_subscription_deleted(session: AsyncSession, obj: dict[str, Any]) -> None:
    """Subscription canceled — fall back to free tier. The row stays
    so we keep the `stripe_customer_id` linkage (re-subscribing from
    the same account reuses the customer)."""
    customer_id = obj.get("customer") or ""
    if not customer_id:
        return
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_customer_id == customer_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        return
    sub.tier = "free"
    sub.status = "canceled"
    sub.interval = None
    sub.stripe_subscription_id = None
    sub.cancel_at_period_end = False
    sub.current_period_start = None
    sub.current_period_end = None
    sub.updated_at = datetime.now(timezone.utc)
    await _apply_tier_to_user(session, sub)


async def _handle_payment_failed(session: AsyncSession, obj: dict[str, Any]) -> None:
    """Don't yank the tier yet — Stripe retries dunning for up to ~2
    weeks. Status flip is enough to drive an FE banner; the
    subsequent `customer.subscription.deleted` (or `.updated` →
    `active`) does the real transition."""
    customer_id = obj.get("customer") or ""
    if not customer_id:
        return
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_customer_id == customer_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        return
    sub.status = "past_due"
    sub.updated_at = datetime.now(timezone.utc)


async def _apply_tier_to_user(session: AsyncSession, sub: Subscription) -> None:
    """Sync `users.quota_bytes` to the tier's quota.

    We don't gate on the existing `users.quota_bytes` — admin overrides
    in the C8 admin panel use the same column. The convention is: a
    paid subscription overwrites whatever was there; if the admin
    wants to grant extra space on top, they raise the plan's quota or
    set a custom one after the subscription change.
    """
    plan = await load_plan(session, sub.tier)
    if plan is None:
        return
    user = await session.get(User, sub.user_id)
    if user is None:
        return
    user.quota_bytes = plan.quota_bytes
