"""§B3 — /account/export rate limit + payload completeness.

The previous suite (test_account_delete.py adjacent) verified the
ZIP shape. This file focuses on the §B3-specific additions:
- 1 successful export per N hours (default 24); 429 with Retry-After
  after that
- clip_embedding + summary fields present in the metadata
- audit row written on each successful export
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


async def test_export_includes_embeddings_and_summary(db_client, monkeypatch):
    """Each image entry in metadata.json carries the user's own CLIP
    embedding + summary text. The user owns this data — exporting it
    is their right under §B3 portability."""
    from backend.config import settings
    monkeypatch.setattr(settings, "vision_enabled", False)

    email = "exp-payload@example.com"
    _, headers = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    # Seed an image directly with a fake embedding + summary so we
    # don't need the full vision pipeline.
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as s:
        img = Image(
            user_id=uid, category="image",
            served_blob_key=f"users/{uid}/served/x.webp",
            original_blob_key=f"users/{uid}/originals/x",
            byte_size_original=100, byte_size_served=50,
            mime_type_served="image/webp",
            pending_face_scan=False, pending_summary=False,
            summary="a short summary",
            summary_topic="topic",
            summary_points=["point one", "point two"],
            clip_embedding=[0.01 * i for i in range(768)],
        )
        s.add(img)
        await s.commit()

    r = await db_client.get("/account/export", headers=headers)
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        meta = json.loads(zf.read("metadata.json"))
    img_meta = meta["images"][0]
    assert img_meta["summary"] == "a short summary"
    assert img_meta["summary_topic"] == "topic"
    assert img_meta["summary_points"] == ["point one", "point two"]
    assert img_meta["clip_embedding"] is not None
    assert len(img_meta["clip_embedding"]) == 768


async def test_export_rate_limited_once_per_window(db_client, monkeypatch):
    """First call: 200 + ZIP. Second call within the window: 429 with
    Retry-After. Adjust the window to 1 hour to keep the assertion
    fast."""
    from backend.config import settings
    monkeypatch.setattr(settings, "account_export_min_hours_between", 1)

    email = "exp-rate@example.com"
    _, headers = await register_and_login(db_client, email=email)

    r1 = await db_client.get("/account/export", headers=headers)
    assert r1.status_code == 200, r1.text

    r2 = await db_client.get("/account/export", headers=headers)
    assert r2.status_code == 429
    assert "Retry-After" in r2.headers
    # Retry-After should be a positive integer of seconds.
    ra = int(r2.headers["Retry-After"])
    assert 0 < ra <= 3600


async def test_export_writes_audit_row(db_client, monkeypatch):
    """Each successful export leaves an `account.export` audit row so
    the operator (and the rate limiter) can prove "this user
    exported their data at T". Also surfaces on the user's own
    activity timeline."""
    from backend.config import settings
    monkeypatch.setattr(settings, "account_export_min_hours_between", 24)

    email = "exp-audit@example.com"
    _, headers = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    r = await db_client.get("/account/export", headers=headers)
    assert r.status_code == 200, r.text

    from backend.db import SessionLocal
    from backend.models import AuditLog

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == uid,
                    AuditLog.action == "account.export",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].details["bytes"] > 0
        assert "filename" in rows[0].details


async def test_export_rate_limit_disabled_when_threshold_zero(db_client, monkeypatch):
    """`account_export_min_hours_between=0` disables the rate limit —
    used for tests and admin tooling that needs to re-export quickly."""
    from backend.config import settings
    monkeypatch.setattr(settings, "account_export_min_hours_between", 0)

    email = "exp-unlimited@example.com"
    _, headers = await register_and_login(db_client, email=email)

    r1 = await db_client.get("/account/export", headers=headers)
    r2 = await db_client.get("/account/export", headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
