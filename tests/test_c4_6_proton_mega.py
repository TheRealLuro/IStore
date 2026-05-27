"""§C4.6 — Proton Drive + MEGA sync.

Both providers ride on the rclone binary (the Python ecosystem
alternatives — mega.py / proton-python-client — are too brittle for
production). These tests mock the rclone subprocess wrapper so we
exercise the HTTP / persistence layer without needing rclone or a
real Proton / MEGA account.

Coverage:
  - /cloud/proton-drive/start with no 2FA → CloudLink persisted +
    link_id returned.
  - /cloud/proton-drive/start with 2FA required (rclone surfaces a
    "2fa required" stderr) → session_id returned + pending entry
    stashed.
  - /cloud/proton-drive/verify with the right code → CloudLink
    persisted; rclone config rewritten with the 2fa field.
  - /cloud/proton-drive/verify with the wrong code → 400, no link.
  - /cloud/mega/start with good credentials → CloudLink persisted in
    one shot (no /verify endpoint exists).
  - /cloud/mega/start with bad credentials → 400, no link.
  - rclone lsjson output → entry-dict conversion shape pinned.
  - rclone download path returns the bytes passthrough.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from sqlalchemy import select

from tests.conftest import register_and_login, fetch_user_id


def _stub_rclone_writers(monkeypatch, tmp_path: Path):
    """Replace `rclone_wrapper.write_proton_config` /
    `write_mega_config` so the test harness doesn't actually shell
    out to rclone. The stubs touch a file in `tmp_path` so the
    endpoints' `unlink` calls + `_ensure_rclone_config` path-existence
    checks all behave realistically."""
    from backend import rclone_wrapper

    def _fake_write_proton(link_id, email, password, totp=""):
        path = tmp_path / f"{link_id}.conf"
        path.write_text(
            f"[proton-drive]\nusername = {email}\n"
            f"password = OBSCURED\n"
            + (f"2fa = OBSCURED-{totp}\n" if totp else ""),
            encoding="utf-8",
        )
        return path

    def _fake_write_mega(link_id, email, password):
        path = tmp_path / f"{link_id}.conf"
        path.write_text(
            f"[mega]\nuser = {email}\npass = OBSCURED\n",
            encoding="utf-8",
        )
        return path

    def _fake_config_path_for_link(link_id):
        return tmp_path / f"{link_id}.conf"

    monkeypatch.setattr(
        rclone_wrapper, "write_proton_config", _fake_write_proton,
    )
    monkeypatch.setattr(
        rclone_wrapper, "write_mega_config", _fake_write_mega,
    )
    monkeypatch.setattr(
        rclone_wrapper, "config_path_for_link", _fake_config_path_for_link,
    )


async def _cloudlinks_for(user_id: UUID, provider: str) -> list:
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


# -------------------- Proton Drive --------------------


async def test_proton_start_no_2fa_creates_link(db_client, monkeypatch, tmp_path):
    """When rclone's probe returns ok (no 2FA), /proton-drive/start
    should persist a CloudLink and return link_id immediately."""
    _stub_rclone_writers(monkeypatch, tmp_path)

    from backend import rclone_wrapper

    async def _probe_ok(remote, config_path):
        return True, "ok"

    monkeypatch.setattr(rclone_wrapper, "rclone_remote_test", _probe_ok)

    email = "proton-no-2fa@example.com"
    _, headers = await register_and_login(db_client, email=email)
    r = await db_client.post(
        "/cloud/proton-drive/start",
        json={"email": "user@proton.me", "password": "hunter2"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["requires_2fa"] is False
    assert isinstance(payload["link_id"], int)

    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "proton_drive")
    assert len(links) == 1, links
    assert links[0].id == payload["link_id"]
    assert links[0].provider == "proton_drive"
    assert links[0].encrypted_refresh_token


async def test_proton_start_2fa_required_stashes_session(
    db_client, monkeypatch, tmp_path,
):
    """When rclone surfaces a 2FA-required stderr, /proton-drive/start
    should stash the pending session and return requires_2fa=True +
    session_id without persisting a CloudLink yet."""
    _stub_rclone_writers(monkeypatch, tmp_path)

    from backend import rclone_wrapper

    async def _probe_2fa(remote, config_path):
        return False, "Proton: 2FA required to sign in"

    monkeypatch.setattr(rclone_wrapper, "rclone_remote_test", _probe_2fa)

    email = "proton-needs-2fa@example.com"
    _, headers = await register_and_login(db_client, email=email)
    r = await db_client.post(
        "/cloud/proton-drive/start",
        json={"email": "user@proton.me", "password": "hunter2"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["requires_2fa"] is True
    assert payload["session_id"]
    assert payload.get("link_id") in (None,)  # nothing persisted yet

    from backend.api import cloud as cloud_api
    assert payload["session_id"] in cloud_api._PROTON_PENDING

    # And no link row was written.
    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "proton_drive")
    assert links == [], links


async def test_proton_verify_completes_with_code(
    db_client, monkeypatch, tmp_path,
):
    """After /start stashed a session, /verify with the right code
    should rewrite the rclone config with the 2fa field and persist
    the link."""
    _stub_rclone_writers(monkeypatch, tmp_path)

    from backend import rclone_wrapper

    # First call (no 2fa in config) → fail with "2fa required".
    # Second call (after verify rewrites the config) → ok.
    probe_outcomes = iter([
        (False, "Proton: 2FA required"),
        (True, "ok"),
    ])

    async def _probe(remote, config_path):
        try:
            return next(probe_outcomes)
        except StopIteration:
            return True, "ok"

    monkeypatch.setattr(rclone_wrapper, "rclone_remote_test", _probe)

    email = "proton-2fa-good@example.com"
    _, headers = await register_and_login(db_client, email=email)
    start = await db_client.post(
        "/cloud/proton-drive/start",
        json={"email": "user@proton.me", "password": "hunter2"},
        headers=headers,
    )
    session_id = start.json()["session_id"]

    verify = await db_client.post(
        "/cloud/proton-drive/verify",
        json={"session_id": session_id, "code": "123456"},
        headers=headers,
    )
    assert verify.status_code == 200, verify.text
    payload = verify.json()
    assert isinstance(payload["link_id"], int)

    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "proton_drive")
    assert len(links) == 1, links
    assert links[0].id == payload["link_id"]

    # Pending entry was cleaned.
    from backend.api import cloud as cloud_api
    assert session_id not in cloud_api._PROTON_PENDING


async def test_proton_verify_bad_code_returns_400(
    db_client, monkeypatch, tmp_path,
):
    """Wrong code → probe still fails after the rewrite → 400 +
    pending entry stays so the user can retry with a fresh code."""
    _stub_rclone_writers(monkeypatch, tmp_path)

    from backend import rclone_wrapper

    async def _probe_always_fail(remote, config_path):
        return False, "Proton: 2FA required"

    monkeypatch.setattr(rclone_wrapper, "rclone_remote_test", _probe_always_fail)

    email = "proton-2fa-bad@example.com"
    _, headers = await register_and_login(db_client, email=email)
    start = await db_client.post(
        "/cloud/proton-drive/start",
        json={"email": "user@proton.me", "password": "hunter2"},
        headers=headers,
    )
    session_id = start.json()["session_id"]

    verify = await db_client.post(
        "/cloud/proton-drive/verify",
        json={"session_id": session_id, "code": "000000"},
        headers=headers,
    )
    assert verify.status_code == 400, verify.text

    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "proton_drive")
    assert links == [], "No CloudLink should exist after a failed verify"

    # Pending entry preserved so the user can retry.
    from backend.api import cloud as cloud_api
    assert session_id in cloud_api._PROTON_PENDING


# -------------------- MEGA --------------------


async def test_mega_start_creates_link_directly(db_client, monkeypatch, tmp_path):
    """Single-step MEGA flow: probe ok → persist + return link_id."""
    _stub_rclone_writers(monkeypatch, tmp_path)

    from backend import rclone_wrapper

    async def _probe_ok(remote, config_path):
        return True, "ok"

    monkeypatch.setattr(rclone_wrapper, "rclone_remote_test", _probe_ok)

    email = "mega-good@example.com"
    _, headers = await register_and_login(db_client, email=email)
    r = await db_client.post(
        "/cloud/mega/start",
        json={"email": "user@example.com", "password": "hunter2"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert isinstance(payload["link_id"], int)

    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "mega")
    assert len(links) == 1, links
    assert links[0].id == payload["link_id"]
    assert links[0].encrypted_refresh_token


async def test_mega_start_bad_credentials_returns_400(
    db_client, monkeypatch, tmp_path,
):
    _stub_rclone_writers(monkeypatch, tmp_path)

    from backend import rclone_wrapper

    async def _probe_fail(remote, config_path):
        return False, "MEGA: invalid email/password"

    monkeypatch.setattr(rclone_wrapper, "rclone_remote_test", _probe_fail)

    email = "mega-bad@example.com"
    _, headers = await register_and_login(db_client, email=email)
    r = await db_client.post(
        "/cloud/mega/start",
        json={"email": "user@example.com", "password": "wrong"},
        headers=headers,
    )
    assert r.status_code == 400, r.text

    user_id = await fetch_user_id(email)
    links = await _cloudlinks_for(user_id, "mega")
    assert links == [], links


# -------------------- entry conversion --------------------


def test_proton_collect_entries_parses_lsjson_shape():
    """`_rclone_entries_to_dicts` should flatten rclone's lsjson
    output into the same {remote_id, name, ...} shape every other
    sync engine emits. Folders are dropped; files keep their
    relative path as remote_parent_path."""
    from backend.cloud_sync import _rclone_entries_to_dicts

    raw = [
        {"Path": "root.jpg", "Name": "root.jpg", "Size": 100,
         "MimeType": "image/jpeg",
         "ModTime": "2026-05-27T10:00:00Z", "IsDir": False, "ID": "id-a"},
        {"Path": "Trips", "Name": "Trips", "Size": 0,
         "ModTime": "2026-05-27T09:00:00Z", "IsDir": True, "ID": "id-f"},
        {"Path": "Trips/trip.mp4", "Name": "trip.mp4", "Size": 200,
         "MimeType": "video/mp4",
         "ModTime": "2026-05-27T11:00:00Z", "IsDir": False, "ID": "id-b"},
        {"Path": "Trips/Day 1/morning.heic",
         "Name": "morning.heic", "Size": 300,
         "MimeType": "image/heic",
         "ModTime": "2026-05-27T11:30:00Z", "IsDir": False, "ID": "id-c"},
    ]
    out = _rclone_entries_to_dicts(raw)
    assert len(out) == 3, out  # folder dropped
    by_name = {e["name"]: e for e in out}
    assert by_name["root.jpg"]["remote_parent_path"] == ""
    assert by_name["root.jpg"]["size_bytes"] == 100
    assert by_name["root.jpg"]["mime_type"] == "image/jpeg"
    assert by_name["trip.mp4"]["remote_parent_path"] == "Trips"
    assert by_name["morning.heic"]["remote_parent_path"] == "Trips/Day 1"
    # §C4.6 regression — `remote_id` MUST be the rclone-relative
    # path, NOT the upstream `ID` field. MEGA returns IDs like
    # "0vJ3Ab7a" which are unusable for `rclone cat`. Earlier code
    # preferred `ID` if present, which silently broke MEGA downloads.
    # `remote_path` mirrors the same so the gallery surface keeps
    # the folder context (was previously the bare `Name`, dropping
    # any directory structure).
    assert by_name["root.jpg"]["remote_id"] == "root.jpg"
    assert by_name["root.jpg"]["remote_path"] == "root.jpg"
    assert by_name["trip.mp4"]["remote_id"] == "Trips/trip.mp4"
    assert by_name["trip.mp4"]["remote_path"] == "Trips/trip.mp4"
    assert by_name["morning.heic"]["remote_id"] == "Trips/Day 1/morning.heic"
    # The ID column from upstream should NEVER appear in remote_id.
    for e in out:
        assert "id-" not in e["remote_id"], (
            "upstream ID leaked into remote_id; rclone cat will fail"
        )


@pytest.mark.asyncio
async def test_mega_download_runs_cat(monkeypatch, tmp_path):
    """`_mega_download` should delegate to `rclone_copy_to_stdout`
    and pass through the bytes verbatim."""
    _stub_rclone_writers(monkeypatch, tmp_path)

    from backend import cloud_sync, rclone_wrapper

    seen: dict = {}

    async def _fake_cat(remote, path, config_path):
        seen["args"] = (remote, path, str(config_path))
        return b"the-bytes"

    monkeypatch.setattr(rclone_wrapper, "rclone_copy_to_stdout", _fake_cat)

    # Build a fake link object with the minimum surface
    # `_ensure_rclone_config` reads.
    class _FakeLink:
        id = 42
        provider = "mega"
        encrypted_refresh_token = ""  # unused — the file already exists

    # Pre-create the per-link config file so _ensure_rclone_config
    # doesn't try to re-materialize from the (empty) blob.
    (tmp_path / "42.conf").write_text("placeholder")

    out = await cloud_sync._mega_download(
        _FakeLink(),
        {"remote_id": "Trips/trip.mp4", "name": "trip.mp4"},
    )
    assert out == b"the-bytes"
    assert seen["args"][0] == rclone_wrapper.MEGA_REMOTE_NAME
    assert seen["args"][1] == "Trips/trip.mp4"
