"""§C4.6 — iCloud Drive connect handshake.

iCloud is the one cloud provider that doesn't use OAuth — the user
enters their Apple ID + password into a neuthek form; the backend
constructs a pyicloud session; if 2FA is required (almost always)
we stash the session in memory and prompt the user for the 6-digit
code from their iDevice.

These tests mock PyiCloudService so we never touch real Apple
endpoints. The mocks substitute for `backend.api.cloud.PyiCloudService`
which is imported lazily inside the /icloud/start endpoint (the
import lives there, not at module level, so the test environment
doesn't need pyicloud installed to load this test file).

Coverage:
  - /icloud/start with `requires_2fa=True` → stashes session +
    returns session_id.
  - /icloud/start with `requires_2fa=False` (trusted device) →
    persists link immediately + returns link_id.
  - /icloud/verify with wrong code → 400 + no CloudLink row.
  - /icloud/verify with correct code → creates CloudLink + calls
    trust_session() + cleans the pending dict.
  - /icloud/verify with missing session_id → 400.
  - Pending entry beyond TTL → /verify treats as expired.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from sqlalchemy import select

from tests.conftest import register_and_login, fetch_user_id


class _FakePyiCloudService:
    """Stand-in for pyicloud.PyiCloudService. Constructor signature
    matches the real one (apple_id, password, cookie_directory)."""

    def __init__(
        self,
        apple_id: str,
        password: str,
        cookie_directory: str,
        *,
        requires_2fa: bool = True,
        requires_2sa: bool = False,
        validate_returns: bool = True,
    ):
        self.apple_id = apple_id
        self.password = password
        self.cookie_directory = cookie_directory
        self.requires_2fa = requires_2fa
        self.requires_2sa = requires_2sa
        self._validate_returns = validate_returns
        self.validate_calls: list[str] = []
        self.trust_called = False

    def validate_2fa_code(self, code: str) -> bool:
        self.validate_calls.append(code)
        return self._validate_returns

    def trust_session(self) -> bool:
        self.trust_called = True
        return True


def _install_fake_pyicloud(
    monkeypatch, *,
    requires_2fa: bool = True,
    requires_2sa: bool = False,
    validate_returns: bool = True,
    capture: dict | None = None,
):
    """Patch the lazy import inside backend.api.cloud's /icloud/start
    so each test controls the fake service's flags. Returns the
    captured-instances dict so assertions can introspect the
    constructed service."""
    instances: list[_FakePyiCloudService] = []

    def _factory(apple_id, password, cookie_directory):
        svc = _FakePyiCloudService(
            apple_id, password, cookie_directory,
            requires_2fa=requires_2fa,
            requires_2sa=requires_2sa,
            validate_returns=validate_returns,
        )
        instances.append(svc)
        if capture is not None:
            capture["service"] = svc
        return svc

    # The endpoint imports PyiCloudService + PyiCloudFailedLoginException
    # via `from pyicloud import ...` INSIDE the function (lazy), so the
    # patch needs to land on the actual pyicloud module (which is the
    # source of truth that the lazy import resolves).
    monkeypatch.setattr("pyicloud.PyiCloudService", _factory)
    return instances


async def _cloudlinks_for(user_id: UUID, provider: str = "icloud") -> list:
    from backend.db import SessionLocal
    from backend.models import CloudLink
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(CloudLink).where(
                    CloudLink.user_id == user_id,
                    CloudLink.provider == provider,
                )
            )
        ).scalars().all()
        return list(rows)


# -------------------- start --------------------


async def test_icloud_start_2fa_path_stashes_session(db_client, monkeypatch, tmp_path):
    """When pyicloud reports requires_2fa=True, /icloud/start should
    return session_id + requires_2fa=True AND stash the live service
    in the in-memory pending dict."""
    monkeypatch.setattr("backend.config.settings.pyicloud_cookie_root", str(tmp_path))
    _install_fake_pyicloud(monkeypatch, requires_2fa=True)

    _, headers = await register_and_login(db_client)
    r = await db_client.post(
        "/cloud/icloud/start",
        json={"apple_id": "user@icloud.com", "password": "hunter2"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["requires_2fa"] is True
    assert payload["session_id"]
    assert payload.get("link_id") in (None,)  # nothing persisted yet

    # The pending dict should have an entry under the returned id.
    from backend.api import cloud as cloud_api
    assert payload["session_id"] in cloud_api._ICLOUD_PENDING


async def test_icloud_start_trusted_path_creates_link_immediately(
    db_client, monkeypatch, tmp_path,
):
    """When pyicloud reports requires_2fa=False (trusted device or 2FA-
    disabled account), /icloud/start should persist the CloudLink row
    immediately and return link_id without stashing anything."""
    monkeypatch.setattr("backend.config.settings.pyicloud_cookie_root", str(tmp_path))
    _install_fake_pyicloud(monkeypatch, requires_2fa=False, requires_2sa=False)

    email = "trusted-icloud@example.com"
    _, headers = await register_and_login(db_client, email=email)
    r = await db_client.post(
        "/cloud/icloud/start",
        json={"apple_id": "trusted@icloud.com", "password": "hunter2"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["requires_2fa"] is False
    assert isinstance(payload["link_id"], int)
    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "icloud")
    assert len(links) == 1, links
    assert links[0].id == payload["link_id"]
    assert links[0].provider == "icloud"
    assert links[0].encrypted_refresh_token  # not empty


# -------------------- verify --------------------


async def test_icloud_verify_bad_code_returns_400(db_client, monkeypatch, tmp_path):
    """Wrong code from pyicloud → 400 + clear message + no CloudLink
    persisted. Pending entry stays in memory so the user can retry."""
    monkeypatch.setattr("backend.config.settings.pyicloud_cookie_root", str(tmp_path))
    _install_fake_pyicloud(monkeypatch, requires_2fa=True, validate_returns=False)

    email = "icloud-bad-code@example.com"
    _, headers = await register_and_login(db_client, email=email)
    start = await db_client.post(
        "/cloud/icloud/start",
        json={"apple_id": "bad@icloud.com", "password": "hunter2"},
        headers=headers,
    )
    session_id = start.json()["session_id"]

    r = await db_client.post(
        "/cloud/icloud/verify",
        json={"session_id": session_id, "code": "000000"},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    assert "didn" in (r.json().get("detail") or "").lower()  # "didn't match"

    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "icloud")
    assert links == [], "No CloudLink should be persisted on failed verify"


async def test_icloud_verify_success_creates_link_and_trusts_session(
    db_client, monkeypatch, tmp_path,
):
    """Correct code → CloudLink row created, trust_session() called,
    pending dict emptied."""
    monkeypatch.setattr("backend.config.settings.pyicloud_cookie_root", str(tmp_path))
    capture = {}
    _install_fake_pyicloud(
        monkeypatch, requires_2fa=True, validate_returns=True, capture=capture,
    )

    email = "icloud-good-code@example.com"
    _, headers = await register_and_login(db_client, email=email)
    start = await db_client.post(
        "/cloud/icloud/start",
        json={"apple_id": "ok@icloud.com", "password": "hunter2"},
        headers=headers,
    )
    session_id = start.json()["session_id"]

    verify = await db_client.post(
        "/cloud/icloud/verify",
        json={"session_id": session_id, "code": "123456"},
        headers=headers,
    )
    assert verify.status_code == 200, verify.text
    payload = verify.json()
    assert isinstance(payload["link_id"], int)

    # Service got the validate AND the trust calls.
    svc = capture["service"]
    assert svc.validate_calls == ["123456"]
    assert svc.trust_called is True

    # CloudLink row was persisted.
    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "icloud")
    assert len(links) == 1, links
    assert links[0].id == payload["link_id"]

    # Pending dict was cleaned.
    from backend.api import cloud as cloud_api
    assert session_id not in cloud_api._ICLOUD_PENDING


async def test_icloud_verify_missing_session_returns_400(db_client, monkeypatch, tmp_path):
    """Verify against a session_id that was never issued → 400."""
    monkeypatch.setattr("backend.config.settings.pyicloud_cookie_root", str(tmp_path))
    _install_fake_pyicloud(monkeypatch)
    _, headers = await register_and_login(db_client)
    r = await db_client.post(
        "/cloud/icloud/verify",
        json={"session_id": "no-such-session", "code": "123456"},
        headers=headers,
    )
    assert r.status_code == 400, r.text


async def test_pending_session_expires_after_ttl(db_client, monkeypatch, tmp_path):
    """If a pending entry's created_at predates the TTL, the sweeper
    should drop it on the next /verify and the user gets a 400."""
    monkeypatch.setattr("backend.config.settings.pyicloud_cookie_root", str(tmp_path))
    _install_fake_pyicloud(monkeypatch, requires_2fa=True, validate_returns=True)

    _, headers = await register_and_login(db_client)
    start = await db_client.post(
        "/cloud/icloud/start",
        json={"apple_id": "ttl@icloud.com", "password": "hunter2"},
        headers=headers,
    )
    session_id = start.json()["session_id"]

    # Backdate the pending entry so the sweeper considers it stale.
    from backend.api import cloud as cloud_api
    entry = cloud_api._ICLOUD_PENDING[session_id]
    entry["created_at"] = datetime.now(timezone.utc) - timedelta(minutes=10)

    r = await db_client.post(
        "/cloud/icloud/verify",
        json={"session_id": session_id, "code": "123456"},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    # And the stale entry got swept.
    assert session_id not in cloud_api._ICLOUD_PENDING
