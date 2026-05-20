"""Regression test for CR-8 + F14 — compose port bindings + weak-credential rejection.

Two related properties this PR has to keep:

  1. **Host ports in docker-compose.yml bind to 127.0.0.1, never 0.0.0.0.**
     Before this patch every `"host:container"` mapping defaulted to
     0.0.0.0 (Docker Desktop on Win/Mac binds to all NICs). Postgres,
     Redis, MinIO, the API, AND the Vite dev server were reachable
     from any LAN attacker — Postgres + MinIO with their dev-default
     `istore` / `istorepass` credentials. Vite served the source tree
     unauthenticated.

  2. **`validate_production_settings` rejects the weak dev credentials.**
     A production deploy that forgets to set POSTGRES_PASSWORD or
     MINIO_SECRET_KEY in .env must fail to boot, not boot with the
     known-weak literal.

The compose check is static-text inspection (we don't need to start
Docker to verify the YAML), and the validator check builds a fresh
Settings instance with the weak credential and asserts the validator
raises.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_COMPOSE = _REPO_ROOT / "docker-compose.yml"


# ---------- 1. Compose port-binding scan ----------


def test_every_compose_port_mapping_binds_to_localhost() -> None:
    """Every `host:container` port mapping in docker-compose.yml must
    explicitly bind to 127.0.0.1. A bare `"8000:8000"` would expose
    the port on every NIC (the default on Docker Desktop)."""
    src = _COMPOSE.read_text(encoding="utf-8")

    # Match port-mapping lines under a `ports:` block: a leading dash,
    # whitespace, then a quoted "host:container" or "ip:host:container"
    # spec. We accept both 127.0.0.1: bindings and IPv6 [::1]:; reject
    # anything else (bare `port:port` or explicit 0.0.0.0).
    port_line = re.compile(r'^\s*-\s*"(?P<spec>[^"]+)"\s*$', re.MULTILINE)
    bad: list[str] = []
    for m in port_line.finditer(src):
        spec = m.group("spec")
        # Skip non-port specs (volume mounts use the same dash-quote
        # syntax in other sections; we're inside `ports:` only when
        # the spec has the form "...:N:N" or "N:N").
        if not re.match(r"^[\d.:\[\]]+:\d+(:\d+)?$", spec):
            continue
        # Acceptable forms:
        #   "127.0.0.1:HOST:CONT"   "[::1]:HOST:CONT"
        if spec.startswith("127.0.0.1:") or spec.startswith("[::1]:"):
            continue
        # Reject "HOST:CONT" (binds to 0.0.0.0) and any explicit 0.0.0.0.
        bad.append(spec)
    assert not bad, (
        "docker-compose.yml port mappings exposed to all NICs: "
        f"{bad}. Bind each to 127.0.0.1 (or [::1]) so the dev "
        "service is only reachable from the host."
    )


def test_no_explicit_0_0_0_0_binding_in_compose() -> None:
    """Defense in depth: even with the test above, someone could
    write `"0.0.0.0:8000:8000"` deliberately. Catch that too."""
    src = _COMPOSE.read_text(encoding="utf-8")
    forbidden_lines = [
        line for line in src.splitlines()
        if "0.0.0.0:" in line and not line.lstrip().startswith("#")
    ]
    assert not forbidden_lines, (
        f"Explicit 0.0.0.0 host binding in compose: {forbidden_lines}"
    )


# ---------- 2. Validator rejects weak DB credentials ----------


@pytest.fixture
def _prod_env(monkeypatch):
    """Force settings into production-shape so the validator runs.

    `is_production` is derived from `app_env not in {dev, test, local}`.
    We set APP_ENV=prod and back-fill the other prod-required env vars
    with valid values so the only failure we test for is the weak DB
    password — every other validator clause should already pass."""
    # Minimal valid prod config — anything we omit here that the
    # validator requires will surface as a noisy unrelated error.
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("MINIO_SECURE", "true")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("JWT_SECRET", "A" * 48)  # ≥32 chars, not in _weak_jwt
    monkeypatch.setenv("SECRET_MANAGER", "docker_secrets")
    monkeypatch.setenv("POSTGRES_AT_REST_ENCRYPTION", "host_volume_confirmed")
    monkeypatch.setenv("MINIO_SSE_MODE", "sse-s3")
    monkeypatch.setenv("BACKUP_AGE_RECIPIENT", "age1qqqqqqqqqqqq")
    monkeypatch.setenv("CLOUD_ENCRYPTION_KEY", "9p0gJ4F8MMRfqRz8GqGQrCBwG3kfOVqCgZpQ4hWcA8s=")
    # Strong DB + MinIO baseline; tests override these.
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
    # Force the Settings singleton to re-read env on each test.
    # backend.security imports `settings` at module load time, so
    # patching `backend.config.settings` alone isn't enough — the
    # validator reads its own bound copy. Patch both.
    import backend.config as cfg
    import backend.security as sec
    fresh = cfg.Settings()
    monkeypatch.setattr(cfg, "settings", fresh, raising=True)
    monkeypatch.setattr(sec, "settings", fresh, raising=True)
    return cfg


def _reload_settings_with_env(monkeypatch, **overrides):
    """Apply env overrides and re-instantiate `backend.config.settings`."""
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
    import backend.config as cfg
    monkeypatch.setattr(cfg, "settings", cfg.Settings(), raising=True)
    # The validator imports `settings` from this module at call time;
    # backend.security keeps its own reference, so we patch there too.
    import backend.security as sec
    monkeypatch.setattr(sec, "settings", cfg.settings, raising=True)


@pytest.mark.asyncio
async def test_validator_rejects_istore_db_password(_prod_env, monkeypatch) -> None:
    _reload_settings_with_env(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://istore:istore@db:5432/app",
        DATABASE_URL_SYNC="postgresql+psycopg2://istore:istore@db:5432/app",
    )
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "DATABASE_URL" in str(exc.value)
    assert "weak" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_validator_rejects_blank_db_password(_prod_env, monkeypatch) -> None:
    """An empty-password DSN (some operators do `postgres://u@host/db`
    by accident) must also fail."""
    _reload_settings_with_env(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://istore@db:5432/app",
        DATABASE_URL_SYNC="postgresql+psycopg2://istore@db:5432/app",
    )
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "DATABASE_URL" in str(exc.value)


@pytest.mark.asyncio
async def test_validator_rejects_minio_default_secret(_prod_env, monkeypatch) -> None:
    _reload_settings_with_env(
        monkeypatch,
        MINIO_SECRET_KEY="istorepass",
    )
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "MINIO_SECRET_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_validator_rejects_minio_default_access_key(_prod_env, monkeypatch) -> None:
    _reload_settings_with_env(
        monkeypatch,
        MINIO_ACCESS_KEY="istore",
    )
    from backend.security import validate_production_settings
    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "MINIO_ACCESS_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_validator_accepts_strong_credentials(_prod_env) -> None:
    """Smoke test for the baseline: the prod-shape env fixture sets
    strong creds; the validator should pass clean. (If it raises, an
    unrelated validator clause is failing — useful diagnostic.)"""
    from backend.security import validate_production_settings
    try:
        await validate_production_settings()
    except RuntimeError as exc:
        # Surface the failure so a regression in OTHER validator
        # clauses doesn't look like our test broke.
        pytest.fail(f"Validator rejected the strong-credential baseline: {exc}")
