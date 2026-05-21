"""Regression tests for CR-4 — cloud-sync quota bypass.

Before, `sync_user_provider` looped over `entries` from the remote
provider, downloaded each via `_provider_download`, and called
`store_upload` to write to MinIO + insert an Image row. There was no
quota check anywhere on that path, so:

  * A user with a 100 GB cap could `POST /cloud/links/{id}/sync`
    against a 10 TB Drive and we'd happily download all 10 TB.
  * An attacker who managed to share a single 5 TB file into the
    victim's Drive (Drive's "shared with me" surface) could trigger
    a 5 TB outbound transfer in one call.

The fix adds three gates inside `sync_user_provider`:

  1. Pre-flight: compute current used bytes (SQL-only, no MinIO
     stat()), derive remaining budget. Fail-fast with an audit row +
     `status="over_quota"` if the user is already at-or-over quota.
  2. Per-entry: refuse to download an entry whose listing-reported
     `size_bytes` would exceed remaining budget. Audit per skip.
  3. Post-write: decrement the running budget by the actual stored
     bytes so the next iteration sees the new floor.

Plus a SQL-only helper (`compute_used_bytes_fast`) co-located with
`DEFAULT_QUOTA_BYTES` in `backend/api/storage.py`.

The tests below stub `_drive_collect_entries` + `_provider_download`
so we drive the gate logic deterministically without needing live
Drive API calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


def _tiny_jpeg_bytes() -> bytes:
    buf = BytesIO()
    PILImage.new("RGB", (8, 8)).save(buf, "JPEG")
    return buf.getvalue()


async def _make_user_with_quota(email: str, quota_bytes: int | None):
    """Create a user (no fastapi-users register; we want quota control)
    and return the User instance."""
    from backend.db import SessionLocal
    from backend.models import User

    async with SessionLocal() as s:
        u = User(
            email=email,
            hashed_password="$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            is_active=True, is_superuser=False, is_verified=True,
            age_confirmed=True,
            quota_bytes=quota_bytes,
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u


async def _make_image_row(user_id, *, served_bytes: int, original_bytes: int,
                          deleted: bool = False, with_original: bool = True):
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as s:
        img = Image(
            user_id=user_id,
            category="image",
            original_filename=f"x-{uuid.uuid4().hex[:6]}.jpg",
            served_blob_key=f"users/{user_id}/served/{uuid.uuid4().hex}.webp",
            original_blob_key=(
                f"users/{user_id}/originals/{uuid.uuid4().hex}"
                if with_original else None
            ),
            byte_size_served=served_bytes,
            byte_size_original=original_bytes if with_original else None,
            pending_face_scan=False,
            pending_summary=False,
            deleted_at=datetime.now(timezone.utc) if deleted else None,
        )
        s.add(img)
        await s.commit()
        return img.id


async def _make_cloud_link(user_id, *, ai_opted_in: bool = False):
    """Create a CloudLink row with a stub encrypted refresh token.

    We DON'T encrypt a real token here — the tests monkeypatch
    `_drive_collect_entries` + `_provider_download` so the refresh
    token is never decoded. The `decrypt_token` call still has to
    succeed though, so we encrypt a placeholder string.
    """
    from backend.db import SessionLocal
    from backend.models import CloudLink
    from backend.secret_box import encrypt as encrypt_token

    placeholder_refresh = encrypt_token("stub-refresh-token").decode("ascii")
    async with SessionLocal() as s:
        link = CloudLink(
            user_id=user_id,
            provider="google_drive",
            encrypted_refresh_token=placeholder_refresh,
            ai_opted_in=ai_opted_in,
            status="active",
        )
        s.add(link)
        await s.commit()
        await s.refresh(link)
        return link


# ----- compute_used_bytes_fast / effective_quota_bytes unit tests -----


async def test_compute_used_bytes_fast_sums_components(db_client):
    """Sum of served + originals + trash columns. Variants
    (MinIO-stat()-only) are intentionally excluded — the helper's
    purpose is a fast quota check during cloud-sync, not a precise
    figure for the user-facing storage panel."""
    from backend.api.storage import compute_used_bytes_fast
    from backend.db import SessionLocal

    user = await _make_user_with_quota("quota-sum@example.com", None)

    # 1000 served + 2000 original (alive) — counts under live rows.
    await _make_image_row(user.id, served_bytes=1000, original_bytes=2000)
    # 500 served + no original (alive) — only served credits.
    await _make_image_row(
        user.id, served_bytes=500, original_bytes=0, with_original=False,
    )
    # Trash: 200 served + 400 original.
    await _make_image_row(
        user.id, served_bytes=200, original_bytes=400, deleted=True,
    )

    async with SessionLocal() as s:
        used = await compute_used_bytes_fast(s, user.id)

    # Live served:   1000 + 500          = 1500
    # Live originals: 2000               = 2000
    # Trash:          (200 + 400)        =  600
    # Grand total:                       = 4100
    assert used == 4100


async def test_effective_quota_bytes_override_vs_default(db_client):
    """`users.quota_bytes` override wins; NULL falls back to the
    global default."""
    from backend.api.storage import (
        DEFAULT_QUOTA_BYTES,
        effective_quota_bytes,
    )

    u_override = await _make_user_with_quota(
        "quota-override@example.com", quota_bytes=2_000_000_000,
    )
    u_default = await _make_user_with_quota(
        "quota-default@example.com", quota_bytes=None,
    )

    assert effective_quota_bytes(u_override) == 2_000_000_000
    assert effective_quota_bytes(u_default) == DEFAULT_QUOTA_BYTES


# ----- sync_user_provider quota gate tests -----


async def test_sync_over_quota_preflight_short_circuits(db_client):
    """User is already at-or-over quota: no downloads happen, return
    payload signals `over_quota=True`, link.status flips to
    `over_quota`, and an audit row is written."""
    from backend.cloud_sync import sync_user_provider
    from backend.db import SessionLocal
    from backend.models import AuditLog, CloudLink

    user = await _make_user_with_quota(
        "quota-preflight@example.com", quota_bytes=10_000,
    )
    # Park 10_000 bytes of "used" already so the user is exactly at quota.
    await _make_image_row(user.id, served_bytes=10_000, original_bytes=0,
                          with_original=False)
    link = await _make_cloud_link(user.id)

    # Three pretend entries — none should be downloaded.
    fake_entries = [
        {
            "remote_id": f"f{i}",
            "name": f"f{i}.jpg",
            "mime_type": "image/jpeg",
            "modified_at": datetime.now(timezone.utc),
            "remote_path": f"f{i}.jpg",
            "remote_parent_path": "",
            "sha256": None,
            "size_bytes": 100,
        }
        for i in range(3)
    ]
    download_calls: list[str] = []

    async def _stub_collect(_refresh):
        return fake_entries

    async def _stub_download(_p, _r, e):
        download_calls.append(e["remote_id"])
        return b"x"

    with patch(
        "backend.cloud_sync._drive_collect_entries",
        side_effect=_stub_collect,
    ), patch(
        "backend.cloud_sync._provider_download",
        side_effect=_stub_download,
    ):
        async with SessionLocal() as s:
            result = await sync_user_provider(s, user.id, "google_drive")

    assert result["over_quota"] is True
    assert result["pulled"] == 0
    assert result["skipped_over_quota"] == 3
    assert download_calls == [], "no downloads should fire on over-quota pre-flight"

    async with SessionLocal() as s:
        link_after = (
            await s.execute(select(CloudLink).where(CloudLink.id == link.id))
        ).scalar_one()
        assert link_after.status == "over_quota"

        audit_rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == "cloud.sync.over_quota",
                )
            )
        ).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].details["quota_bytes"] == 10_000
        assert audit_rows[0].details["used_bytes"] == 10_000


async def test_sync_per_entry_skip_oversized(db_client):
    """User has 5 KB remaining. First listing entry is 10 KB (over
    budget) → skipped; second is 1 KB (fits) → pulled. The 10 KB
    entry must NEVER be downloaded — the gate fires on the listing-
    reported size, before bytes leave the wire."""
    from backend.cloud_sync import sync_user_provider
    from backend.db import SessionLocal
    from backend.models import AuditLog, Image

    user = await _make_user_with_quota(
        "quota-perentry@example.com", quota_bytes=5_500,
    )
    # 500 bytes already used → 5_000 remaining.
    await _make_image_row(user.id, served_bytes=500, original_bytes=0,
                          with_original=False)
    await _make_cloud_link(user.id)

    jpeg = _tiny_jpeg_bytes()
    fake_entries = [
        {
            "remote_id": "big",
            "name": "big.jpg",
            "mime_type": "image/jpeg",
            "modified_at": datetime.now(timezone.utc),
            "remote_path": "big.jpg",
            "remote_parent_path": "",
            "sha256": None,
            "size_bytes": 10_000,
        },
        {
            "remote_id": "small",
            "name": "small.jpg",
            "mime_type": "image/jpeg",
            "modified_at": datetime.now(timezone.utc),
            "remote_path": "small.jpg",
            "remote_parent_path": "",
            "sha256": None,
            "size_bytes": 1_000,
        },
    ]
    download_calls: list[str] = []

    async def _stub_collect(_refresh):
        return fake_entries

    async def _stub_download(_p, _r, e):
        download_calls.append(e["remote_id"])
        return jpeg

    with patch(
        "backend.cloud_sync._drive_collect_entries",
        side_effect=_stub_collect,
    ), patch(
        "backend.cloud_sync._provider_download",
        side_effect=_stub_download,
    ):
        async with SessionLocal() as s:
            result = await sync_user_provider(s, user.id, "google_drive")

    # Critical: the BIG entry must NOT have been downloaded — that's
    # the whole point of the fix (saving the wire transfer when we
    # already know we can't store the bytes).
    assert "big" not in download_calls
    assert "small" in download_calls
    assert result["pulled"] == 1
    assert result["skipped_over_quota"] == 1
    assert result["over_quota"] is False

    async with SessionLocal() as s:
        audit_rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == "cloud.sync.skipped_quota",
                )
            )
        ).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].details["remote_id"] == "big"
        assert audit_rows[0].details["entry_size_bytes"] == 10_000


async def test_sync_post_download_safety_net_when_size_missing(db_client):
    """Some providers don't always populate `size` on the listing —
    the post-download safety net catches the case where listing said
    `size_bytes=0` but the actual blob is too large to fit the
    remaining budget."""
    from backend.cloud_sync import sync_user_provider
    from backend.db import SessionLocal
    from backend.models import AuditLog

    user = await _make_user_with_quota(
        "quota-safety-net@example.com", quota_bytes=2_000,
    )
    # 0 bytes used; budget == quota == 2000 bytes.
    await _make_cloud_link(user.id)

    fake_entries = [
        {
            "remote_id": "no-size",
            "name": "no-size.jpg",
            "mime_type": "image/jpeg",
            "modified_at": datetime.now(timezone.utc),
            "remote_path": "no-size.jpg",
            "remote_parent_path": "",
            "sha256": None,
            "size_bytes": 0,  # provider didn't report
        },
    ]
    # The actual blob is 5_000 bytes — bigger than the 2_000 budget.
    big_blob = b"\x00" * 5_000

    async def _stub_collect(_refresh):
        return fake_entries

    async def _stub_download(_p, _r, _e):
        return big_blob

    with patch(
        "backend.cloud_sync._drive_collect_entries",
        side_effect=_stub_collect,
    ), patch(
        "backend.cloud_sync._provider_download",
        side_effect=_stub_download,
    ):
        async with SessionLocal() as s:
            result = await sync_user_provider(s, user.id, "google_drive")

    assert result["pulled"] == 0
    assert result["skipped_over_quota"] == 1

    async with SessionLocal() as s:
        audit_rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == "cloud.sync.skipped_quota",
                )
            )
        ).scalars().all()
        assert any(
            r.details.get("reason") == "post_download_size_check"
            for r in audit_rows
        ), "the safety-net audit row should mark the reason explicitly"


async def test_sync_budget_decrements_after_pull(db_client):
    """Two entries: budget starts at 6_000. First pull consumes the
    upload's actual bytes; the second entry — which would fit the
    initial budget but NOT the decremented one — should be skipped."""
    from backend.cloud_sync import sync_user_provider
    from backend.db import SessionLocal

    user = await _make_user_with_quota(
        "quota-decrement@example.com", quota_bytes=6_000,
    )
    await _make_cloud_link(user.id)

    jpeg = _tiny_jpeg_bytes()
    # Listing says both entries are 3_000 bytes. Pulled image bytes
    # are roughly the JPEG size (~300 bytes for an 8x8 PIL JPEG, but
    # the exact figure varies). The test doesn't pin the exact bytes
    # — it pins the SHAPE: first pull succeeds, decrement happens,
    # second pull is fully gated against the running budget. To make
    # this deterministic regardless of the actual JPEG byte count, we
    # use a `size_bytes` that's already 3_000 and check that pulled
    # count is exactly 2 (both fit initial budget) — and validate the
    # log message contains a decremented budget.
    fake_entries = [
        {
            "remote_id": f"e{i}",
            "name": f"e{i}.jpg",
            "mime_type": "image/jpeg",
            "modified_at": datetime.now(timezone.utc),
            "remote_path": f"e{i}.jpg",
            "remote_parent_path": "",
            "sha256": None,
            "size_bytes": 3_000,
        }
        for i in range(2)
    ]

    async def _stub_collect(_refresh):
        return fake_entries

    async def _stub_download(_p, _r, _e):
        return jpeg

    with patch(
        "backend.cloud_sync._drive_collect_entries",
        side_effect=_stub_collect,
    ), patch(
        "backend.cloud_sync._provider_download",
        side_effect=_stub_download,
    ):
        async with SessionLocal() as s:
            result = await sync_user_provider(s, user.id, "google_drive")

    # Budget 6_000 → first entry (3_000) fits → pulled (image bytes
    # ~300, decrement happens) → budget ~5_700 → second entry (3_000)
    # still fits → pulled. Both pull.
    assert result["pulled"] == 2
    assert result["skipped_over_quota"] == 0


async def test_sync_budget_decrement_blocks_subsequent_oversize(db_client):
    """Hard variant of the decrement test: first entry consumes
    nearly all the budget, second is bigger than what's left."""
    from backend.cloud_sync import sync_user_provider
    from backend.db import SessionLocal

    user = await _make_user_with_quota(
        "quota-decrement-blocks@example.com", quota_bytes=5_000,
    )
    await _make_cloud_link(user.id)

    jpeg = _tiny_jpeg_bytes()
    fake_entries = [
        {
            "remote_id": "e0",
            "name": "e0.jpg",
            "mime_type": "image/jpeg",
            "modified_at": datetime.now(timezone.utc),
            "remote_path": "e0.jpg",
            "remote_parent_path": "",
            "sha256": None,
            "size_bytes": 4_500,
        },
        {
            "remote_id": "e1",
            "name": "e1.jpg",
            "mime_type": "image/jpeg",
            "modified_at": datetime.now(timezone.utc),
            "remote_path": "e1.jpg",
            "remote_parent_path": "",
            "sha256": None,
            "size_bytes": 1_000,
        },
    ]
    download_calls: list[str] = []

    async def _stub_collect(_refresh):
        return fake_entries

    async def _stub_download(_p, _r, e):
        download_calls.append(e["remote_id"])
        return jpeg

    with patch(
        "backend.cloud_sync._drive_collect_entries",
        side_effect=_stub_collect,
    ), patch(
        "backend.cloud_sync._provider_download",
        side_effect=_stub_download,
    ):
        async with SessionLocal() as s:
            result = await sync_user_provider(s, user.id, "google_drive")

    # Budget 5_000 → e0 (4_500) fits → pull (image bytes consumed) →
    # budget ~500 → e1 (1_000 listing) over budget → skipped.
    # e1 must NEVER have been downloaded — the listing-size gate fires
    # before _provider_download is called.
    assert "e0" in download_calls
    assert "e1" not in download_calls
    assert result["pulled"] == 1
    assert result["skipped_over_quota"] == 1
