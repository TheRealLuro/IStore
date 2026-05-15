"""Stripe billing acceptance.

These tests stub the `stripe` SDK so they run offline. We verify:
  - GET /billing/plans returns the seeded tiers with correct shape.
  - GET /billing/subscription returns 'free' for a new user.
  - POST /billing/checkout returns the Embedded client_secret when
    Stripe is configured; 503 when not; 400 when tier/interval
    Price ID is missing.
  - Webhook signature is verified — unsigned/forged payloads 400.
  - Webhook handlers are idempotent (duplicate event id is a no-op).
  - `checkout.session.completed` → tier flip → `users.quota_bytes`
    bumped.
  - `customer.subscription.deleted` → revert to free.
  - Tier-aware rate limits in `enforce_upload_limits` read from
    the `plans` table.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


@pytest.fixture
async def stripe_enabled(monkeypatch):
    """Pretend Stripe is configured + stub the SDK module."""
    from backend.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    monkeypatch.setattr(settings, "stripe_publishable_key", "pk_test_stub")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_stub")
    monkeypatch.setattr(settings, "stripe_price_id_pro_monthly", "price_test_pro_monthly")
    monkeypatch.setattr(settings, "stripe_price_id_pro_annual", "price_test_pro_annual")
    monkeypatch.setattr(settings, "stripe_price_id_business_monthly", "price_test_biz_monthly")
    monkeypatch.setattr(settings, "stripe_price_id_business_annual", "price_test_biz_annual")
    return settings


@pytest.fixture
def stub_stripe_sdk(monkeypatch):
    """Replace the lazy `_client()` with a fake that records calls."""
    calls: list[tuple[str, dict]] = []

    def _fake_customer_create(**kwargs):
        calls.append(("Customer.create", kwargs))
        return SimpleNamespace(id="cus_test_" + (kwargs.get("metadata") or {}).get("user_id", "x")[:8])

    def _fake_checkout_create(**kwargs):
        calls.append(("Checkout.create", kwargs))
        return SimpleNamespace(client_secret="cs_test_secret_123", id="cs_test_id_456")

    def _fake_portal_create(**kwargs):
        calls.append(("Portal.create", kwargs))
        return SimpleNamespace(url="https://billing.stripe.com/p/session/test_xxx")

    def _fake_subscription_retrieve(sub_id):
        calls.append(("Subscription.retrieve", {"id": sub_id}))
        return {
            "id": sub_id,
            "status": "active",
            "metadata": {"tier": "pro", "interval": "month"},
            "items": {"data": [{"price": {"id": "price_test_pro_monthly"}}]},
            "current_period_start": int(time.time()),
            "current_period_end": int(time.time()) + 30 * 24 * 3600,
            "cancel_at_period_end": False,
        }

    def _fake_webhook_construct(payload, sig_header, secret):  # noqa: ARG001
        if sig_header == "BAD":
            raise ValueError("Bad signature")
        return json.loads(payload.decode("utf-8"))

    fake_stripe = SimpleNamespace(
        api_key="",
        Customer=SimpleNamespace(create=_fake_customer_create),
        checkout=SimpleNamespace(
            Session=SimpleNamespace(create=_fake_checkout_create)
        ),
        billing_portal=SimpleNamespace(
            Session=SimpleNamespace(create=_fake_portal_create)
        ),
        Subscription=SimpleNamespace(retrieve=_fake_subscription_retrieve),
        Webhook=SimpleNamespace(construct_event=_fake_webhook_construct),
    )
    from backend import billing as billing_mod

    monkeypatch.setattr(billing_mod, "_client", lambda: fake_stripe)
    return calls


# ---------- plans + subscription ----------


async def test_plans_returns_seeded_tiers(db_client):
    r = await db_client.get("/billing/plans")
    assert r.status_code == 200
    plans = r.json()
    assert {p["tier"] for p in plans} == {"free", "pro", "business"}
    pro = next(p for p in plans if p["tier"] == "pro")
    assert pro["monthly_cents"] == 3000
    assert pro["annual_cents"] == 30000
    assert pro["quota_bytes"] == 500 * 1024 * 1024 * 1024
    # Without env config the paid tiers report unavailable.
    assert pro["monthly_available"] is False


async def test_plans_show_available_when_env_set(db_client, stripe_enabled):
    r = await db_client.get("/billing/plans")
    assert r.status_code == 200
    pro = next(p for p in r.json() if p["tier"] == "pro")
    assert pro["monthly_available"] is True
    assert pro["annual_available"] is True


async def test_subscription_defaults_to_free(db_client):
    _, headers = await register_and_login(db_client, email="sub-default@example.com")
    r = await db_client.get("/billing/subscription", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "free"
    assert body["status"] == "active"


# ---------- checkout ----------


async def test_checkout_returns_503_when_stripe_disabled(db_client):
    _, headers = await register_and_login(db_client, email="co-disabled@example.com")
    r = await db_client.post(
        "/billing/checkout",
        json={"tier": "pro", "interval": "monthly"},
        headers=headers,
    )
    assert r.status_code == 503


async def test_checkout_creates_embedded_session(
    db_client, stripe_enabled, stub_stripe_sdk
):
    _, headers = await register_and_login(db_client, email="co-good@example.com")
    r = await db_client.post(
        "/billing/checkout",
        json={"tier": "pro", "interval": "monthly"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client_secret"] == "cs_test_secret_123"
    assert body["publishable_key"] == "pk_test_stub"

    # Stripe should see ui_mode='embedded' and our Price ID.
    checkout_calls = [c for c in stub_stripe_sdk if c[0] == "Checkout.create"]
    assert len(checkout_calls) == 1
    kw = checkout_calls[0][1]
    assert kw["ui_mode"] == "embedded"
    assert kw["mode"] == "subscription"
    assert kw["line_items"][0]["price"] == "price_test_pro_monthly"
    assert kw["subscription_data"]["metadata"]["tier"] == "pro"


async def test_checkout_rejects_free_tier(db_client, stripe_enabled, stub_stripe_sdk):
    _, headers = await register_and_login(db_client, email="co-free@example.com")
    r = await db_client.post(
        "/billing/checkout",
        json={"tier": "free", "interval": "monthly"},
        headers=headers,
    )
    assert r.status_code == 400


async def test_checkout_rejects_missing_price_id(
    db_client, stripe_enabled, monkeypatch
):
    """If the operator forgot to set a Price ID for the requested
    tier/interval, the endpoint should 400 with an actionable message."""
    from backend.config import settings

    monkeypatch.setattr(settings, "stripe_price_id_business_annual", "")
    _, headers = await register_and_login(db_client, email="co-missing@example.com")
    r = await db_client.post(
        "/billing/checkout",
        json={"tier": "business", "interval": "annual"},
        headers=headers,
    )
    assert r.status_code == 400
    assert "STRIPE_PRICE_ID_BUSINESS_ANNUAL" in r.json()["detail"]


# ---------- portal ----------


async def test_portal_returns_url_when_configured(
    db_client, stripe_enabled, stub_stripe_sdk
):
    _, headers = await register_and_login(db_client, email="portal-good@example.com")
    r = await db_client.post(
        "/billing/portal", json={"return_url": "http://test/account"}, headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://billing.stripe.com/")


# ---------- webhook ----------


async def test_webhook_rejects_bad_signature(db_client, stripe_enabled, stub_stripe_sdk):
    r = await db_client.post(
        "/billing/webhook",
        content=b'{"id":"evt_x","type":"checkout.session.completed"}',
        headers={"stripe-signature": "BAD"},
    )
    assert r.status_code == 400


async def test_webhook_checkout_completed_promotes_user(
    db_client, stripe_enabled, stub_stripe_sdk
):
    """End-to-end: register a user, send a fake checkout.session.completed
    webhook, verify subscription row + users.quota_bytes both flip to pro."""
    email = "wh-promote@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    # Seed a customer on the subscription row so the webhook can map it.
    from backend.db import SessionLocal
    from backend.models import Subscription, User

    async with SessionLocal() as s:
        s.add(
            Subscription(
                user_id=uid,
                tier="free",
                status="active",
                stripe_customer_id="cus_test_user",
            )
        )
        await s.commit()

    payload = {
        "id": "evt_test_promote",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_test_user",
                "subscription": "sub_test_active",
            }
        },
    }
    r = await db_client.post(
        "/billing/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers={"stripe-signature": "GOOD"},
    )
    assert r.status_code == 200, r.text

    async with SessionLocal() as s:
        sub = (
            await s.execute(select(Subscription).where(Subscription.user_id == uid))
        ).scalar_one()
        assert sub.tier == "pro"
        assert sub.interval == "month"
        assert sub.status == "active"
        user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        assert user.quota_bytes == 500 * 1024 * 1024 * 1024


async def test_webhook_is_idempotent(db_client, stripe_enabled, stub_stripe_sdk):
    """A duplicate event id should be acked but not re-processed."""
    email = "wh-idem@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import StripeEvent, Subscription

    async with SessionLocal() as s:
        s.add(
            Subscription(
                user_id=uid, tier="free", status="active",
                stripe_customer_id="cus_test_idem",
            )
        )
        await s.commit()

    payload = {
        "id": "evt_test_idempotent",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_test_idem",
                "subscription": "sub_test_idem",
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")

    first = await db_client.post(
        "/billing/webhook", content=body, headers={"stripe-signature": "GOOD"},
    )
    second = await db_client.post(
        "/billing/webhook", content=body, headers={"stripe-signature": "GOOD"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json().get("duplicate") is True

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(StripeEvent).where(StripeEvent.id == "evt_test_idempotent")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].processed_at is not None


async def test_webhook_subscription_deleted_reverts_to_free(
    db_client, stripe_enabled, stub_stripe_sdk
):
    email = "wh-cancel@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import Subscription, User

    async with SessionLocal() as s:
        s.add(
            Subscription(
                user_id=uid, tier="pro", status="active", interval="month",
                stripe_customer_id="cus_test_cancel",
                stripe_subscription_id="sub_test_cancel",
            )
        )
        u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        u.quota_bytes = 500 * 1024 * 1024 * 1024
        await s.commit()

    payload = {
        "id": "evt_test_cancel",
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_test_cancel"}},
    }
    r = await db_client.post(
        "/billing/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers={"stripe-signature": "GOOD"},
    )
    assert r.status_code == 200

    async with SessionLocal() as s:
        sub = (
            await s.execute(select(Subscription).where(Subscription.user_id == uid))
        ).scalar_one()
        assert sub.tier == "free"
        assert sub.status == "canceled"
        user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        assert user.quota_bytes == 50 * 1024 * 1024 * 1024


# ---------- tier-aware rate limits ----------


async def test_rate_limit_lookup_uses_subscription_tier(db_client, monkeypatch):
    """`_tier_limits_for` should return Pro's caps after the user
    is promoted, even though the global `upload_max_*` settings are
    set to Free-tier defaults."""
    from backend.config import settings
    from backend.security import _tier_limits_for

    monkeypatch.setattr(settings, "upload_max_count_per_hour", 100)
    monkeypatch.setattr(settings, "upload_max_bytes_per_day", 2 * 1024 * 1024 * 1024)

    email = "tier-rate@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    # Free tier — should fall back to settings (which we just set
    # to free-tier values).
    free_count, free_bytes = await _tier_limits_for(str(uid))
    # The lookup queries the plans table; we expect the seeded
    # free-tier row to apply.
    assert free_count == 100
    assert free_bytes == 2 * 1024 * 1024 * 1024

    from backend.db import SessionLocal
    from backend.models import Subscription

    async with SessionLocal() as s:
        s.add(
            Subscription(
                user_id=uid, tier="pro", status="active", interval="month",
                stripe_customer_id="cus_rate_test",
            )
        )
        await s.commit()

    pro_count, pro_bytes = await _tier_limits_for(str(uid))
    assert pro_count == 300
    assert pro_bytes == 50 * 1024 * 1024 * 1024
