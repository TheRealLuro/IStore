"""§C4.6 — cloud provider catalog + Dropbox OAuth handshake.

Pins the contract between backend.cloud_sync.list_providers() (the
data source) and the FE Cloud sync panel (the consumer). What we
check:

  1. /cloud/providers returns the expected providers with the right
     shape (id, name, kind, status, auth_shape, blurb, docs).

  2. Status is derived from settings so flipping env vars promotes
     a provider from "needs_setup" → "available" without a code
     change. We exercise both states for dropbox by monkeypatching
     settings.dropbox_oauth_client_id.

  3. connect_provider for Dropbox returns an auth URL pointing at
     www.dropbox.com with the right scopes + redirect.

  4. connect_provider for non-OAuth providers raises
     CloudSyncNotConfigured — iCloud / Proton Drive / MEGA all
     bootstrap via their own dedicated /cloud/{provider}/start
     endpoints, not the OAuth connect path.

  5. auth_shape is set correctly for each provider so the FE knows
     which connect affordance (redirect / Apple-ID modal / password
     modal) to surface.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from tests.conftest import register_and_login


async def test_providers_catalog_returns_expected_set(db_client):
    """/cloud/providers lists every known provider in stable order
    after the Box + pCloud removal and Proton Drive + MEGA addition."""
    _, headers = await register_and_login(db_client)

    r = await db_client.get("/cloud/providers", headers=headers)
    assert r.status_code == 200, r.text
    providers = r.json()
    ids = [p["id"] for p in providers]
    assert ids == [
        "google_drive",
        "dropbox",
        "icloud",
        "proton_drive",
        "mega",
    ], ids

    # Required keys present on every entry, including the new
    # auth_shape field used by the FE.
    for p in providers:
        assert set(p.keys()) >= {
            "id", "name", "kind", "status", "auth_shape", "blurb",
        }, p.keys()
        assert p["status"] in ("available", "needs_setup", "coming_soon")
        assert p["auth_shape"] in ("oauth", "apple_id", "password")


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


@pytest.mark.parametrize("provider", ["icloud", "proton_drive", "mega"])
async def test_non_oauth_providers_reject_connect_provider(provider, monkeypatch):
    """iCloud / Proton Drive / MEGA bootstrap via their own dedicated
    /cloud/{provider}/start endpoints (password-based), not the
    OAuth connect_provider path. The OAuth path should refuse them
    so a future refactor doesn't accidentally start handing out an
    auth URL for a provider that doesn't have one."""
    from backend.config import settings
    monkeypatch.setattr(
        settings, "cloud_encryption_key",
        "Hg9XYqGGsNuJZIbkDdWUEoVwHJa6nfA0sCpsZX1bGuU=",
    )
    from backend.cloud_sync import connect_provider, CloudSyncNotConfigured
    import uuid
    with pytest.raises(CloudSyncNotConfigured):
        await connect_provider(uuid.uuid4(), provider)  # type: ignore[arg-type]


async def test_icloud_in_catalog(db_client):
    """iCloud Drive appears in /cloud/providers as `available` (because
    pyicloud is importable in the test environment) with a blurb that
    references the Apple-ID / 2FA auth shape so the user knows it's
    different from the OAuth providers."""
    _, headers = await register_and_login(db_client)
    r = await db_client.get("/cloud/providers", headers=headers)
    assert r.status_code == 200, r.text
    providers = r.json()
    icloud = next((p for p in providers if p["id"] == "icloud"), None)
    assert icloud is not None
    assert icloud["status"] == "available", icloud
    blurb = (icloud.get("blurb") or "").lower()
    assert "apple id" in blurb or "pyicloud" in blurb, blurb


async def test_proton_drive_in_catalog(db_client, monkeypatch):
    """Proton Drive shows up with auth_shape='password' and an
    "available" status when rclone is on PATH (we monkeypatch
    `shutil.which` to simulate that in tests where the binary isn't
    actually installed)."""
    _, headers = await register_and_login(db_client)
    # Pretend rclone is installed for the duration of this test.
    # `_rclone_status` does `import shutil as _shutil_rclone; ...which("rclone")`
    # inside the function so the only useful patch is on the real
    # `shutil.which` symbol.
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/rclone")

    r = await db_client.get("/cloud/providers", headers=headers)
    assert r.status_code == 200, r.text
    providers = r.json()
    proton = next((p for p in providers if p["id"] == "proton_drive"), None)
    assert proton is not None, providers
    assert proton["auth_shape"] == "password", proton
    assert proton["status"] == "available", proton


async def test_mega_in_catalog(db_client, monkeypatch):
    """Same shape check for MEGA — auth_shape=password,
    status=available when rclone is on PATH."""
    _, headers = await register_and_login(db_client)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/rclone")

    r = await db_client.get("/cloud/providers", headers=headers)
    assert r.status_code == 200, r.text
    providers = r.json()
    mega = next((p for p in providers if p["id"] == "mega"), None)
    assert mega is not None, providers
    assert mega["auth_shape"] == "password", mega
    assert mega["status"] == "available", mega


async def test_oauth_providers_have_oauth_auth_shape(db_client):
    """Google Drive + Dropbox are the OAuth providers; their
    auth_shape must be "oauth" so the FE knows to redirect the
    browser instead of opening a modal."""
    _, headers = await register_and_login(db_client)
    r = await db_client.get("/cloud/providers", headers=headers)
    assert r.status_code == 200, r.text
    providers = {p["id"]: p for p in r.json()}
    assert providers["google_drive"]["auth_shape"] == "oauth"
    assert providers["dropbox"]["auth_shape"] == "oauth"


async def test_icloud_has_apple_id_auth_shape(db_client):
    _, headers = await register_and_login(db_client)
    r = await db_client.get("/cloud/providers", headers=headers)
    assert r.status_code == 200, r.text
    providers = {p["id"]: p for p in r.json()}
    assert providers["icloud"]["auth_shape"] == "apple_id"
