"""Phase 8 acceptance: account export (GDPR Art. 20).

The ZIP must contain:
  - metadata.json with user info, image rows, consent records, audit entries
  - originals/<id>__<filename> for every retained original
  - served/<id>.<ext> when the served variant differs
  - face_crops/face-<face_id>-det-<det_id>.jpg for each crop the system holds

Verifies the exported metadata round-trips through json.loads and that
expected paths are present.
"""
from __future__ import annotations

import io
import json
import zipfile

from tests.conftest import (
    fetch_user_id,
    grant_consent,
    insert_face,
    register_and_login,
)


async def test_export_zip_contains_metadata_and_blobs(db_client):
    email = "export-a@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    uid = await fetch_user_id(email)
    seed = await insert_face(uid, person_name="Mom", cluster_id=1)

    # Pre-populate stub storage so the export has bytes to read.
    from backend import storage as storage_mod
    from backend.db import SessionLocal
    from backend.models import Image
    from sqlalchemy import select

    async with SessionLocal() as session:
        img = (
            await session.execute(select(Image).where(Image.id == seed["image_id"]))
        ).scalar_one()
    storage_mod.storage.put(
        storage_mod.storage.bucket_originals, img.original_blob_key,
        b"original-bytes-XYZ", "application/octet-stream",
    )
    storage_mod.storage.put(
        storage_mod.storage.bucket_served, img.served_blob_key,
        b"served-bytes-abc", "image/webp",
    )
    storage_mod.storage.put(
        storage_mod.storage.bucket_faces, seed["crop_blob_key"],
        b"face-crop-jpg-bytes", "image/jpeg",
    )

    r = await db_client.get("/account/export", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "metadata.json" in names
    assert any(n.startswith("originals/") for n in names), \
        f"originals folder missing; got: {names}"
    assert any(n.startswith("face_crops/") for n in names), \
        f"face_crops folder missing; got: {names}"

    meta = json.loads(zf.read("metadata.json").decode("utf-8"))
    assert meta["user"]["email"] == email
    assert meta["user"]["id"] == str(uid)
    assert len(meta["images"]) == 1
    assert meta["images"][0]["original_filename"] == "test.jpg" or \
        meta["images"][0]["original_filename"] is None  # depending on insert_face
    assert len(meta["persons"]) == 1
    assert meta["persons"][0]["display_name"] == "Mom"
    assert len(meta["faces"]) == 1
    assert len(meta["face_detections"]) == 1
    assert any(c["state"] == "GRANTED" for c in meta["consent_records"])

    # Original blob roundtrips byte-exactly.
    original_entry = next(n for n in names if n.startswith("originals/"))
    assert zf.read(original_entry) == b"original-bytes-XYZ"


async def test_export_includes_users_with_no_blobs(db_client):
    """Even a brand-new user with zero data must get a valid metadata-only ZIP."""
    email = "export-empty@example.com"
    _, headers = await register_and_login(db_client, email=email)

    r = await db_client.get("/account/export", headers=headers)
    assert r.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    meta = json.loads(zf.read("metadata.json").decode("utf-8"))
    assert meta["user"]["email"] == email
    assert meta["images"] == []
    assert meta["persons"] == []
    assert meta["consent_records"] == []


async def test_export_does_not_leak_other_users(db_client):
    email_a = "export-iso-a@example.com"
    email_b = "export-iso-b@example.com"
    _, headers_a = await register_and_login(db_client, email=email_a)
    _, _ = await register_and_login(db_client, email=email_b)
    uid_b = await fetch_user_id(email_b)
    await insert_face(uid_b, person_name="Mom_B", cluster_id=1)

    r = await db_client.get("/account/export", headers=headers_a)
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    meta = json.loads(zf.read("metadata.json").decode("utf-8"))
    assert meta["user"]["email"] == email_a
    assert meta["images"] == []
    assert meta["persons"] == []
    # No originals/ or face_crops/ folders should leak from B.
    assert all(
        not n.startswith("originals/") and not n.startswith("face_crops/")
        for n in zf.namelist()
    )


async def test_export_requires_auth(db_client):
    r = await db_client.get("/account/export")
    assert r.status_code == 401
