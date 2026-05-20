"""Regression test for CR-10 — extended production-settings validator.

The original `validate_production_settings` covered MINIO_SECURE, SSE
key separation, JWT_SECRET length, BACKUP_AGE_RECIPIENT,
CLOUD_ENCRYPTION_KEY shape, Redis-in-prod, and (after CR-8) weak
DB/MinIO credentials. CR-10 adds the eight remaining gaps the audit
flagged in `audit_findings/auth.md` (A4) and
`audit_findings/config_infra.md` (F4):

  - TRUST_PROXY_HEADERS must be true
  - SECURITY_RATE_LIMITS_ENABLED must be true
  - JWT_LIFETIME_SECONDS upper bound (7 d ceiling)
  - ACCOUNT_DELETE_GRACE_DAYS > 0
  - GOOGLE_OAUTH_CLIENT_ID + _SECRET set together (or both blank)
  - FRONTEND_BASE_URL must appear in CORS allowlist
  - STRIPE_WEBHOOK_SECRET set when STRIPE_SECRET_KEY is set
  - SMTP_HOST required

Each case below pins the rejection so a future PR that drops one of
these clauses fails loudly at test collection.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def _prod_baseline(monkeypatch):
    """Force production-shape settings with every required field set
    to a passing value. Tests override individual fields to trigger
    one specific validator clause at a time."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("MINIO_SECURE", "true")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://localhost:5173")
    monkeypatch.setenv("JWT_SECRET", "A" * 48)
    monkeypatch.setenv("SECRET_MANAGER", "docker_secrets")
    monkeypatch.setenv("POSTGRES_AT_REST_ENCRYPTION", "host_volume_confirmed")
    monkeypatch.setenv("MINIO_SSE_MODE", "sse-s3")
    monkeypatch.setenv("BACKUP_AGE_RECIPIENT", "age1qqqqqqqqqqqq")
    monkeypatch.setenv("CLOUD_ENCRYPTION_KEY", "9p0gJ4F8MMRfqRz8GqGQrCBwG3kfOVqCgZpQ4hWcA8s=")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:strong-random-Pw!@db:5432/app",
    )
    monkeypatch.setenv(
        "DATABASE_URL_SYNC",
        "postgresql+psycopg2://u:strong-random-Pw!@db:5432/app",
    )
    monkeypatch.setenv("MINIO_ACCESS_KEY", "strong-random-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "strong-random-secret-Pw!")
    # CR-10 new prerequisites
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("SECURITY_RATE_LIMITS_ENABLED", "true")
    monkeypatch.setenv("JWT_LIFETIME_SECONDS", "86400")
    monkeypatch.setenv("ACCOUNT_DELETE_GRACE_DAYS", "30")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    # Monkeypatch the CORS allowlist to include the baseline FE
    # origin so the CORS-vs-FRONTEND_BASE_URL cross-check passes by
    # default. Real prod deploys would set the prod hostname here
    # before deploy.
    import backend.app as app_mod
    monkeypatch.setattr(
        app_mod,
        "ALLOWED_ORIGINS",
        tuple(list(app_mod.ALLOWED_ORIGINS) + ["https://localhost:5173"]),
        raising=True,
    )
    import backend.config as cfg
    import backend.security as sec
    fresh = cfg.Settings()
    monkeypatch.setattr(cfg, "settings", fresh, raising=True)
    monkeypatch.setattr(sec, "settings", fresh, raising=True)
    return cfg


def _reload_with(monkeypatch, **overrides):
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
    import backend.config as cfg
    import backend.security as sec
    fresh = cfg.Settings()
    monkeypatch.setattr(cfg, "settings", fresh, raising=True)
    monkeypatch.setattr(sec, "settings", fresh, raising=True)


# ---------- per-clause rejection cases ----------


@pytest.mark.asyncio
async def test_rejects_when_trust_proxy_headers_is_false(_prod_baseline, monkeypatch):
    _reload_with(monkeypatch, TRUST_PROXY_HEADERS="false")
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "TRUST_PROXY_HEADERS" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_when_rate_limits_disabled(_prod_baseline, monkeypatch):
    _reload_with(monkeypatch, SECURITY_RATE_LIMITS_ENABLED="false")
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "SECURITY_RATE_LIMITS_ENABLED" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_jwt_lifetime_above_ceiling(_prod_baseline, monkeypatch):
    # 8 days, just above the 7-day ceiling.
    _reload_with(monkeypatch, JWT_LIFETIME_SECONDS=str(8 * 24 * 60 * 60))
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "JWT_LIFETIME_SECONDS" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_jwt_lifetime_zero(_prod_baseline, monkeypatch):
    _reload_with(monkeypatch, JWT_LIFETIME_SECONDS="0")
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "JWT_LIFETIME_SECONDS" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_zero_grace_window(_prod_baseline, monkeypatch):
    _reload_with(monkeypatch, ACCOUNT_DELETE_GRACE_DAYS="0")
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "ACCOUNT_DELETE_GRACE_DAYS" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_half_configured_google_oauth(_prod_baseline, monkeypatch):
    # Client id set, secret blank.
    _reload_with(
        monkeypatch,
        GOOGLE_OAUTH_CLIENT_ID="123456-abcdef.apps.googleusercontent.com",
        GOOGLE_OAUTH_CLIENT_SECRET="",
    )
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "GOOGLE_OAUTH_CLIENT" in str(exc.value)


@pytest.mark.asyncio
async def test_accepts_both_google_oauth_fields_unset(_prod_baseline):
    """Both blank = SSO intentionally disabled. Validator passes."""
    from backend.security import validate_production_settings
    # Baseline already has both blank — no GOOGLE_* set.
    await validate_production_settings()  # must not raise


@pytest.mark.asyncio
async def test_rejects_frontend_url_outside_cors_allowlist(_prod_baseline, monkeypatch):
    _reload_with(
        monkeypatch,
        FRONTEND_BASE_URL="https://app.example.com",  # not in ALLOWED_ORIGINS
    )
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "CORS" in str(exc.value) or "allowlist" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_stripe_secret_without_webhook(_prod_baseline, monkeypatch):
    _reload_with(
        monkeypatch,
        STRIPE_SECRET_KEY="sk_live_test_keynotreallyused",
        STRIPE_WEBHOOK_SECRET="",
    )
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "STRIPE_WEBHOOK_SECRET" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_blank_smtp_host(_prod_baseline, monkeypatch):
    _reload_with(monkeypatch, SMTP_HOST="")
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "SMTP_HOST" in str(exc.value)


@pytest.mark.asyncio
async def test_baseline_passes_clean(_prod_baseline):
    """Sanity: the strong-everything baseline must validate clean.
    If this fails, a regression in OTHER validator clauses is
    making the test fixture look broken — surface it."""
    from backend.security import validate_production_settings
    try:
        await validate_production_settings()
    except RuntimeError as exc:
        pytest.fail(f"Validator rejected the baseline: {exc}")
