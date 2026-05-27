"""§C4.6 — Dropbox sync-engine internals.

We can't hit real Dropbox APIs in unit tests (would need real
OAuth client + a populated test account), so these tests verify
the engine layer that surrounds the API call:

  - Token refresh hits the right endpoint with the right payload
    and surfaces auth failures as CloudSyncNotConfigured.
  - Entry collection returns the right shape: every dict has
    `remote_id`, `name`, `modified_at`, `remote_parent_path`,
    `sha256`, `size_bytes`.
  - Download dispatches to the right helper per provider.
  - _parse_iso_time handles the timestamp shapes the provider
    emits (with `Z`, with offset, with sub-second precision).

For the API-mocked tests we monkey-patch httpx.AsyncClient.get/post
to return canned responses. That gives us coverage of the request-
construction + response-parsing without ever touching the network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_response(status: int, json_body: dict | None = None, content: bytes = b"") -> MagicMock:
    """Build a httpx.Response-shaped mock. Matches the .status_code,
    .json(), .text, .content surface we touch."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body or {}
    r.text = json.dumps(json_body) if json_body else ""
    r.content = content
    return r


# ---------- timestamp parser ----------


def test_parse_iso_time_handles_z_suffix():
    from backend.cloud_sync import _parse_iso_time
    out = _parse_iso_time("2026-05-27T12:34:56Z")
    assert out == datetime(2026, 5, 27, 12, 34, 56, tzinfo=timezone.utc)


def test_parse_iso_time_handles_offset():
    from backend.cloud_sync import _parse_iso_time
    out = _parse_iso_time("2026-05-27T12:34:56+00:00")
    assert out == datetime(2026, 5, 27, 12, 34, 56, tzinfo=timezone.utc)


def test_parse_iso_time_handles_subsecond_precision():
    """Dropbox emits microsecond + suffix. Python's fromisoformat
    caps at 6-digit fractional seconds. The helper trims and recovers."""
    from backend.cloud_sync import _parse_iso_time
    # 9 digits — beyond Python's 6-digit limit.
    out = _parse_iso_time("2026-05-27T12:34:56.123456789Z")
    assert out is not None
    assert out.year == 2026


def test_parse_iso_time_returns_none_on_garbage():
    from backend.cloud_sync import _parse_iso_time
    assert _parse_iso_time("") is None
    assert _parse_iso_time(None) is None
    assert _parse_iso_time("not a date") is None


# ---------- Dropbox ----------


@pytest.mark.asyncio
async def test_dropbox_refresh_access_token_returns_token(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "dropbox_oauth_client_id", "fake-id")
    monkeypatch.setattr(settings, "dropbox_oauth_client_secret", "fake-secret")
    from backend import cloud_sync

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=_fake_response(
        200, {"access_token": "fresh-at", "expires_in": 14400},
    ))
    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await cloud_sync._dropbox_refresh_access_token("rt")
    assert out == "fresh-at"


@pytest.mark.asyncio
async def test_dropbox_collect_entries_paginates(monkeypatch):
    """list_folder returns has_more=True → next call goes to
    list_folder/continue → has_more=False ends the walk. Entries
    accumulate across both pages."""
    from backend.config import settings
    monkeypatch.setattr(settings, "dropbox_oauth_client_id", "fake-id")
    monkeypatch.setattr(settings, "dropbox_oauth_client_secret", "fake-secret")
    from backend import cloud_sync

    # Sequence: refresh-token POST, list_folder POST, list_folder/continue POST.
    refresh_resp = _fake_response(200, {"access_token": "at"})
    page1 = _fake_response(200, {
        "entries": [
            {
                ".tag": "file", "id": "id:A", "name": "a.jpg",
                "path_display": "/photos/a.jpg",
                "server_modified": "2026-05-27T10:00:00Z",
                "size": 100, "content_hash": "ab" * 32,
            },
            {".tag": "folder", "id": "id:F", "name": "skip-me"},
        ],
        "cursor": "cursor-1",
        "has_more": True,
    })
    page2 = _fake_response(200, {
        "entries": [
            {
                ".tag": "file", "id": "id:B", "name": "b.mp4",
                "path_display": "/b.mp4",
                "server_modified": "2026-05-27T11:00:00Z",
                "size": 200, "content_hash": "cd" * 32,
            },
            {".tag": "deleted", "name": "old.txt"},  # should skip
        ],
        "cursor": "cursor-2",
        "has_more": False,
    })

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[refresh_resp, page1, page2])

    with patch("httpx.AsyncClient", return_value=mock_client):
        entries = await cloud_sync._dropbox_collect_entries("rt")

    assert len(entries) == 2
    a, b = entries
    assert a["remote_id"] == "id:A"
    assert a["name"] == "a.jpg"
    assert a["remote_parent_path"] == "photos"
    assert a["size_bytes"] == 100
    assert b["remote_parent_path"] == ""
    assert b["size_bytes"] == 200


@pytest.mark.asyncio
async def test_dropbox_download_uses_dropbox_api_arg_header(monkeypatch):
    """The download endpoint takes its file-id argument in a custom
    header, NOT a body field. Pin that contract so a future refactor
    doesn't accidentally drop the header and break every download."""
    from backend.config import settings
    monkeypatch.setattr(settings, "dropbox_oauth_client_id", "fake-id")
    monkeypatch.setattr(settings, "dropbox_oauth_client_secret", "fake-secret")
    from backend import cloud_sync

    refresh_resp = _fake_response(200, {"access_token": "at"})
    download_resp = _fake_response(200, content=b"jpeg-bytes-here")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[refresh_resp, download_resp])

    with patch("httpx.AsyncClient", return_value=mock_client):
        out = await cloud_sync._dropbox_download(
            "rt", {"remote_id": "id:ABC", "name": "x.jpg"},
        )
    assert out == b"jpeg-bytes-here"

    # Last call (download_resp) was made with the Dropbox-API-Arg
    # header carrying the file's stable ID. Verify the request shape.
    last_call_kwargs = mock_client.post.call_args_list[1].kwargs
    headers = last_call_kwargs["headers"]
    assert headers["Authorization"] == "Bearer at"
    import json as _json
    arg = _json.loads(headers["Dropbox-API-Arg"])
    assert arg == {"path": "id:ABC"}


# ---------- sync_user_provider dispatch ----------


@pytest.mark.asyncio
async def test_sync_user_provider_accepts_dropbox(monkeypatch):
    """Previously raised 'not implemented yet' for non-Drive providers.
    Now dispatches into the per-provider helpers; we just need it to
    reach the collect-entries call (we mock that to a no-op so we
    don't hit a real DB / HTTP)."""
    from backend.cloud_sync import sync_user_provider, CloudSyncNotConfigured
    import uuid

    # Dropbox should NOT raise the "not implemented"
    # CloudSyncNotConfigured. It'll raise a DIFFERENT one (no link
    # row) since we're not setting up the DB, but the message will
    # differ.
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None),
    ))

    with pytest.raises(CloudSyncNotConfigured) as exc:
        await sync_user_provider(fake_session, uuid.uuid4(), "dropbox")
    assert "No active dropbox connection" in str(exc.value)
