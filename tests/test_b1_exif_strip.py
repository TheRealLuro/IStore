"""§B1 — strip EXIF (especially GPS) from originals when neither
`gps_retention` nor `exif_retention` consent is active.

Verifies:
- A JPEG with GPS+camera EXIF, no consent → originals has no APP1 /
  no EXIF block at all.
- A JPEG with GPS+camera EXIF, gps_retention=GRANTED → originals
  preserves EXIF.
- A PNG (no EXIF in the format) → bytes unchanged.
- The strip-helper is a no-op on non-image MIME.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

from backend import image as image_mod


def _jpeg_with_exif() -> bytes:
    """Build a minimal JPEG with both GPS and camera-tag EXIF blocks
    so the strip helper has something concrete to remove."""
    img = PILImage.new("RGB", (16, 16), "white")
    exif = img.getexif()
    # Camera tags (0x010F=Make, 0x0110=Model, 0x9003=DateTimeOriginal).
    exif[0x010F] = "neuthek-test-camera"
    exif[0x0110] = "model-X"
    # GPS sub-IFD (tag 0x8825). We just need *something* in the GPS
    # block so the strip is observable; values can be stubs.
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N"
    gps_ifd[2] = ((37, 1), (46, 1), (0, 1))
    gps_ifd[3] = "W"
    gps_ifd[4] = ((122, 1), (25, 1), (0, 1))
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def _has_exif(jpeg_bytes: bytes) -> bool:
    """True iff Pillow can find an EXIF block in the bytes."""
    with PILImage.open(io.BytesIO(jpeg_bytes)) as pil:
        exif = pil.getexif()
        if not exif:
            return False
        # An empty exif dict prints as falsy but isn't always; check
        # for our injected camera tag explicitly.
        return 0x010F in exif or 0x0110 in exif or bool(exif.get_ifd(0x8825))


def test_strip_helper_removes_exif_from_jpeg():
    data = _jpeg_with_exif()
    assert _has_exif(data), "fixture must start with EXIF"
    stripped = image_mod._strip_exif_bytes(data, "image/jpeg")
    assert stripped is not None
    assert not _has_exif(stripped), "EXIF still present after strip"


def test_strip_helper_no_op_on_png():
    """PNG doesn't carry EXIF; helper should return the bytes
    unchanged so the caller's hash math is stable."""
    img = PILImage.new("RGB", (4, 4), "blue")
    out = io.BytesIO()
    img.save(out, format="PNG")
    data = out.getvalue()
    result = image_mod._strip_exif_bytes(data, "image/png")
    assert result == data


def test_strip_helper_returns_none_for_non_image():
    assert image_mod._strip_exif_bytes(b"plain bytes", "text/plain") is None
    assert image_mod._strip_exif_bytes(b"plain bytes", None) is None


# ----- End-to-end: upload pipeline strips EXIF when consent absent -----


async def _grant_scope(session, user_id, kind: str) -> None:
    from datetime import datetime, timezone
    from backend.models import ConsentRecord
    session.add(ConsentRecord(
        user_id=user_id, consent_kind=kind, state="GRANTED",
        granted_at=datetime.now(timezone.utc),
        policy_version="v1", policy_text_sha256=b"x" * 32,
        signature_text="test", user_agent="pytest",
    ))
    await session.commit()


async def test_upload_strips_exif_when_no_consent(db_client, monkeypatch):
    """Upload a JPEG with EXIF + no consent. The bytes that land in
    the originals bucket should be EXIF-free."""
    # Disable rate limits so the upload sails through.
    from backend.config import settings
    monkeypatch.setattr(settings, "security_rate_limits_enabled", False)
    monkeypatch.setattr(settings, "vision_enabled", False)

    from tests.conftest import register_and_login
    email = "exif-strip@example.com"
    _, headers = await register_and_login(db_client, email=email)

    raw = _jpeg_with_exif()
    r = await db_client.post(
        "/images/",
        files={"file": ("with-exif.jpg", raw, "image/jpeg")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    img_id = r.json()["id"]

    # Fetch the stored originals via the in-memory MinIO stub.
    from backend import storage as storage_mod
    from backend.db import SessionLocal
    from backend.models import Image
    from sqlalchemy import select
    from uuid import UUID

    async with SessionLocal() as s:
        row = (
            await s.execute(select(Image).where(Image.id == UUID(img_id)))
        ).scalar_one()
        # Reach into the stub's blob store.
        # db_client fixture stores blobs in a dict keyed by (bucket, key);
        # we can't read it directly here, so use the public fetch.
    from backend.image import fetch_original
    blob, _mime = await fetch_original(row)
    assert not _has_exif(blob), "originals still has EXIF without consent"


async def test_upload_preserves_exif_when_gps_retention_granted(db_client, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "security_rate_limits_enabled", False)
    monkeypatch.setattr(settings, "vision_enabled", False)

    from tests.conftest import fetch_user_id, register_and_login
    email = "exif-keep@example.com"
    _, headers = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    # Grant gps_retention via a direct DB write — bypasses the
    # consent-form-with-signature flow but exercises the same
    # is_scope_active check the pipeline reads.
    from backend.db import SessionLocal
    async with SessionLocal() as s:
        await _grant_scope(s, uid, "gps_retention")

    raw = _jpeg_with_exif()
    r = await db_client.post(
        "/images/",
        files={"file": ("with-exif.jpg", raw, "image/jpeg")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    img_id = r.json()["id"]

    from sqlalchemy import select
    from backend.models import Image
    from uuid import UUID

    async with SessionLocal() as s:
        row = (
            await s.execute(select(Image).where(Image.id == UUID(img_id)))
        ).scalar_one()
    from backend.image import fetch_original
    blob, _ = await fetch_original(row)
    assert _has_exif(blob), "originals lost EXIF despite gps_retention consent"
