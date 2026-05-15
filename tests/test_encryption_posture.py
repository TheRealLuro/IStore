"""§A2 — encryption-at-rest + in-transit posture.

Verifies:
- Boot in `prod` rejects an empty CLOUD_ENCRYPTION_KEY (otherwise the
  secret_box auto-bootstrap silently generates an ephemeral key that
  doesn't survive container rebuilds — exact failure mode we hit
  with TOTP).
- Boot in `prod` rejects a malformed CLOUD_ENCRYPTION_KEY (must be a
  valid Fernet key).
- Boot in `prod` rejects MINIO_SSE_MODE=sse-kms when either content
  or biometric KMS key id is missing.
- Boot in `prod` rejects same-key separation (content == biometric).
- Boot in `dev` is permissive — auto-bootstrap is fine.
- /admin/system surfaces an `encryption` block with the rolled-up
  posture so the admin Storage tab can read it.
"""
from __future__ import annotations

import pytest

from tests.conftest import register_and_login


def _reset_settings(monkeypatch, **overrides):
    """Apply a coherent set of prod-grade defaults so the only
    failure surfaced is the one each test is checking for. Without
    this scaffolding the validator would fail on the first
    unrelated knob and the test would pass for the wrong reason."""
    from backend.config import settings

    monkeypatch.setattr(settings, "app_env", overrides.get("app_env", "prod"))
    monkeypatch.setattr(settings, "minio_secure", overrides.get("minio_secure", True))
    monkeypatch.setattr(settings, "frontend_base_url", overrides.get("frontend_base_url", "https://neuthek.com"))
    monkeypatch.setattr(settings, "jwt_secret", overrides.get("jwt_secret", "x" * 48))
    monkeypatch.setattr(settings, "secret_manager", overrides.get("secret_manager", "docker_secrets"))
    monkeypatch.setattr(settings, "postgres_at_rest_encryption", overrides.get("postgres_at_rest_encryption", "host_volume_confirmed"))
    monkeypatch.setattr(settings, "minio_sse_mode", overrides.get("minio_sse_mode", "sse-s3"))
    monkeypatch.setattr(settings, "minio_sse_kms_key_id_content", overrides.get("minio_sse_kms_key_id_content", ""))
    monkeypatch.setattr(settings, "minio_sse_kms_key_id_biometric", overrides.get("minio_sse_kms_key_id_biometric", ""))
    monkeypatch.setattr(settings, "backup_age_recipient", overrides.get("backup_age_recipient", "age1...someone"))
    monkeypatch.setattr(settings, "cloud_encryption_key", overrides.get("cloud_encryption_key", _valid_fernet()))
    monkeypatch.setattr(settings, "security_rate_limits_enabled", False)


def _valid_fernet() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# ---------- Boot-time validation ----------


async def test_prod_rejects_missing_cloud_encryption_key(monkeypatch):
    _reset_settings(monkeypatch, cloud_encryption_key="")
    from backend.security import validate_production_settings

    with pytest.raises(RuntimeError) as exc:
        # require_redis_when_production raises before our check if
        # Redis is unreachable — bypass by stubbing it out.
        from backend import security as security_mod
        async def _stub(): return None
        monkeypatch.setattr(security_mod, "require_redis_when_production", _stub)
        await validate_production_settings()
    assert "CLOUD_ENCRYPTION_KEY" in str(exc.value)


async def test_prod_rejects_malformed_cloud_encryption_key(monkeypatch):
    _reset_settings(monkeypatch, cloud_encryption_key="not-a-real-fernet-key")
    from backend.security import validate_production_settings
    from backend import security as security_mod
    async def _stub(): return None
    monkeypatch.setattr(security_mod, "require_redis_when_production", _stub)

    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "Fernet" in str(exc.value)


async def test_prod_rejects_sse_kms_with_missing_biometric_key(monkeypatch):
    _reset_settings(
        monkeypatch,
        minio_sse_mode="sse-kms",
        minio_sse_kms_key_id_content="arn:aws:kms:us-east-1:111:key/content",
        minio_sse_kms_key_id_biometric="",
    )
    from backend.security import validate_production_settings
    from backend import security as security_mod
    async def _stub(): return None
    monkeypatch.setattr(security_mod, "require_redis_when_production", _stub)

    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "BIOMETRIC" in str(exc.value)


async def test_prod_rejects_sse_kms_same_key_for_content_and_biometric(monkeypatch):
    _reset_settings(
        monkeypatch,
        minio_sse_mode="sse-kms",
        minio_sse_kms_key_id_content="arn:aws:kms:us-east-1:111:key/shared",
        minio_sse_kms_key_id_biometric="arn:aws:kms:us-east-1:111:key/shared",
    )
    from backend.security import validate_production_settings
    from backend import security as security_mod
    async def _stub(): return None
    monkeypatch.setattr(security_mod, "require_redis_when_production", _stub)

    with pytest.raises(RuntimeError) as exc:
        await validate_production_settings()
    assert "must differ" in str(exc.value)


async def test_prod_accepts_well_configured_sse_kms(monkeypatch):
    _reset_settings(
        monkeypatch,
        minio_sse_mode="sse-kms",
        minio_sse_kms_key_id_content="arn:aws:kms:us-east-1:111:key/content",
        minio_sse_kms_key_id_biometric="arn:aws:kms:us-east-1:111:key/biometric",
    )
    from backend.security import validate_production_settings
    from backend import security as security_mod
    async def _stub(): return None
    monkeypatch.setattr(security_mod, "require_redis_when_production", _stub)

    # Should not raise.
    await validate_production_settings()


async def test_dev_is_permissive_with_empty_cloud_key(monkeypatch):
    """The auto-bootstrap path is intentionally a dev convenience —
    don't double-check it via validate_production_settings."""
    from backend.config import settings

    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "cloud_encryption_key", "")
    monkeypatch.setattr(settings, "minio_sse_mode", "off")

    from backend.security import validate_production_settings
    await validate_production_settings()  # no-op in dev


# ---------- Admin encryption posture endpoint ----------


async def test_admin_system_returns_encryption_posture(db_client, monkeypatch):
    from backend.config import settings

    # Pin posture knobs to a hardened set so we know exactly what
    # the rollup should report.
    monkeypatch.setattr(settings, "app_env", "dev")  # bypass prod validator
    monkeypatch.setattr(settings, "minio_secure", True)
    monkeypatch.setattr(settings, "frontend_base_url", "https://neuthek.com")
    monkeypatch.setattr(settings, "minio_sse_mode", "sse-s3")
    monkeypatch.setattr(settings, "cloud_encryption_key", _valid_fernet())
    monkeypatch.setattr(settings, "postgres_at_rest_encryption", "host_volume_confirmed")
    monkeypatch.setattr(settings, "backup_age_recipient", "age1stub")

    email = "enc-admin@example.com"
    _, headers = await register_and_login(db_client, email=email)
    # Promote to superuser so /admin/system passes the gate.
    from tests.conftest import fetch_user_id
    uid = await fetch_user_id(email)
    from backend.db import SessionLocal
    from backend.models import User
    from sqlalchemy import update as sa_update

    async with SessionLocal() as s:
        await s.execute(sa_update(User).where(User.id == uid).values(is_superuser=True))
        await s.commit()

    r = await db_client.get("/admin/system", headers=headers)
    assert r.status_code == 200, r.text
    enc = r.json()["encryption"]
    assert enc["transit"]["minio_secure"] is True
    assert enc["transit"]["frontend_https"] is True
    assert enc["object_storage"]["sse_mode"] == "sse-s3"
    assert enc["object_storage"]["kms_keys_separated"] is False  # sse-s3 doesn't use KMS
    assert enc["secret_box"]["fernet_key"] == "valid"
    assert enc["database"]["at_rest"] == "host_volume_confirmed"
    assert enc["backups"]["age_recipient_set"] is True
    # The endpoint never leaks key material.
    body_str = r.text
    assert "FERNET" not in body_str.upper().replace("FERNET_KEY", "")  # no key body
    assert "BEGIN" not in body_str  # no PEM


async def test_admin_system_flags_kms_misconfig_in_posture(db_client, monkeypatch):
    """SSE-KMS with only one key set → kms_keys_separated=False so the
    operator can see the drift from the dashboard without piecing it
    together from env vars."""
    from backend.config import settings

    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "minio_sse_mode", "sse-kms")
    monkeypatch.setattr(settings, "minio_sse_kms_key_id_content", "key-A")
    monkeypatch.setattr(settings, "minio_sse_kms_key_id_biometric", "")
    monkeypatch.setattr(settings, "cloud_encryption_key", _valid_fernet())

    email = "enc-kms-admin@example.com"
    _, headers = await register_and_login(db_client, email=email)
    from tests.conftest import fetch_user_id
    uid = await fetch_user_id(email)
    from backend.db import SessionLocal
    from backend.models import User
    from sqlalchemy import update as sa_update

    async with SessionLocal() as s:
        await s.execute(sa_update(User).where(User.id == uid).values(is_superuser=True))
        await s.commit()

    r = await db_client.get("/admin/system", headers=headers)
    enc = r.json()["encryption"]
    assert enc["object_storage"]["sse_mode"] == "sse-kms"
    assert enc["object_storage"]["kms_keys_separated"] is False
    assert enc["ok"] is False  # rollup says "not hardened"
