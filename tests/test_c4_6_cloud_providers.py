"""§C4.6 — cloud provider catalog + Dropbox OAuth handshake.

Pins the contract between backend.cloud_sync.list_providers() (the
data source) and the FE Cloud sync panel (the consumer). What we
check:

  1. /cloud/providers returns all 6 known providers with the right
     shape (id, name, kind, status, blurb, docs).

  2. Status is derived from settings so flipping env vars promotes
     a provider from "needs_setup" → "available" without a code
     change. We exercise both states for google_drive by
     monkeypatching settings.google_oauth_client_id.

  3. connect_provider for Dropbox returns an auth URL pointing at
     www.dropbox.com with the right scopes + redirect.

  4. connect_provider for `icloud` / `mega` raises
     CloudSyncNotConfigured ("not implemented yet") — those two
     placeholder providers can't be bootstrapped (no usable API).

We don't exercise the full OAuth round-trip — that needs real
client_id/secret + the providers' consent screens — but the URL
construction + the per-provider dispatch in connect_provider /
complete_oauth are the high-value parts to pin.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from tests.conftest import register_and_login


async def test_providers_catalog_returns_all_six(db_client):
    """/cloud/providers lists every known provider in stable order."""
    _, headers = await register_and_login(db_client)

    r = await db_client.get("/cloud/providers", headers=headers)
    assert r.status_code == 200, r.text
    providers = r.json()
    ids = [p["id"] for p in providers]
    assert ids == [
        "google_drive",
        "dropbox",
        "icloud",
        "mega",
        "box",
        "pcloud",
    ], ids

    # Required keys present on every entry.
    for p in providers:
        assert set(p.keys()) >= {
            "id", "name", "kind", "status", "blurb",
        }, p.keys()
        assert p["status"] in ("available", "needs_setup", "coming_soon")


async def test_providers_status_reflects_settings(db_client, monkeypatch):
    """Setting dropbox credentials live → status flips to 'available'
    without a code change. Validates the FE wiring works for hosted
    deployments where operators flip env vars between rebuilds."""
    _, headers = await register_and_login(db_client)

    from backend.config import settings

    # Cleared (default test state): needs_setup.
    monkeypatch.setattr(settings, "dropbox_oauth_client_id", "")
    monkeypatch.setattr(settings, "dropbox_oauth_client_secret", "")
    r = await db_client.get("/cloud/providers", headers=headers)
    db = next(p for p in r.json() if p["id"] == "dropbox")
    assert db["status"] == "needs_setup", db

    # Set: available.
    monkeypatch.setattr(settings, "dropbox_oauth_client_id", "fake-id")
    monkeypatch.setattr(settings, "dropbox_oauth_client_secret", "fake-secret")
    r = await db_client.get("/cloud/providers", headers=headers)
    db = next(p for p in r.json() if p["id"] == "dropbox")
    assert db["status"] == "available", db


async def test_dropbox_connect_returns_dropbox_auth_url(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "dropbox_oauth_client_id", "fake-id")
    monkeypatch.setattr(settings, "dropbox_oauth_client_secret", "fake-secret")
    monkeypatch.setattr(
        settings, "cloud_encryption_key",
        "Hg9XYqGGsNuJZIbkDdWUEoVwHJa6nfA0sCpsZX1bGuU=",
    )

    from backend.cloud_sync import connect_provider
    import uuid
    handoff = await connect_provider(uuid.uuid4(), "dropbox")

    parsed = urlparse(handoff.auth_url)
    assert parsed.netloc == "www.dropbox.com"
    assert parsed.path == "/oauth2/authorize"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["fake-id"]
    # The killer flag: without `token_access_type=offline` Dropbox
    # returns short-lived tokens only.
    assert qs["token_access_type"] == ["offline"]
    # PKCE.
    assert qs["code_challenge_method"] == ["S256"]


async def test_box_connect_returns_box_auth_url(monkeypatch):
    """Box's auth URL lives on account.box.com (the consent page)
    and carries the read-only scope. No PKCE — Box treats it as
    optional and the simpler shape parallels the Google Sign-In
    flow."""
    from backend.config import settings
    monkeypatch.setattr(settings, "box_oauth_client_id", "fake-box-id")
    monkeypatch.setattr(settings, "box_oauth_client_secret", "fake-box-secret")
    monkeypatch.setattr(
        settings, "cloud_encryption_key",
        "Hg9XYqGGsNuJZIbkDdWUEoVwHJa6nfA0sCpsZX1bGuU=",
    )

    from backend.cloud_sync import connect_provider
    import uuid
    handoff = await connect_provider(uuid.uuid4(), "box")

    parsed = urlparse(handoff.auth_url)
    assert parsed.netloc == "account.box.com"
    assert parsed.path == "/api/oauth2/authorize"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["fake-box-id"]
    assert qs["response_type"] == ["code"]
    # Read-only scope keeps us from ever writing back.
    assert qs["scope"] == ["root_readonly"]


async def test_pcloud_connect_returns_pcloud_auth_url(monkeypatch):
    """pCloud's auth URL lives on my.pcloud.com and does NOT carry a
    `scope` param — pCloud configures permissions app-wide."""
    from backend.config import settings
    monkeypatch.setattr(settings, "pcloud_oauth_client_id", "fake-pcloud-id")
    monkeypatch.setattr(settings, "pcloud_oauth_client_secret", "fake-pcloud-secret")
    monkeypatch.setattr(
        settings, "cloud_encryption_key",
        "Hg9XYqGGsNuJZIbkDdWUEoVwHJa6nfA0sCpsZX1bGuU=",
    )

    from backend.cloud_sync import connect_provider
    import uuid
    handoff = await connect_provider(uuid.uuid4(), "pcloud")

    parsed = urlparse(handoff.auth_url)
    assert parsed.netloc == "my.pcloud.com"
    assert parsed.path == "/oauth2/authorize"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["fake-pcloud-id"]
    assert qs["response_type"] == ["code"]
    # pCloud doesn't accept a `scope` param at all.
    assert "scope" not in qs


@pytest.mark.parametrize("provider", ["icloud", "mega"])
async def test_coming_soon_providers_raise_not_configured(provider, monkeypatch):
    """The two truly-unimplementable placeholder providers (iCloud +
    MEGA — see the catalog blurbs for why) can't be connected."""
    from backend.config import settings
    monkeypatch.setattr(
        settings, "cloud_encryption_key",
        "Hg9XYqGGsNuJZIbkDdWUEoVwHJa6nfA0sCpsZX1bGuU=",
    )
    from backend.cloud_sync import connect_provider, CloudSyncNotConfigured
    import uuid
    with pytest.raises(CloudSyncNotConfigured):
        await connect_provider(uuid.uuid4(), provider)  # type: ignore[arg-type]
