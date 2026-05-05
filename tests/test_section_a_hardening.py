from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from uuid import UUID

import pytest
from PIL import Image as PILImage
from sqlalchemy import select, text

from tests.conftest import fetch_user_id, insert_face, register_and_login


def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (width, height), "blue").save(out, format="PNG")
    return out.getvalue()


def _docx_bytes(extra: tuple[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        zf.writestr("_rels/.rels", b"<Relationships/>")
        zf.writestr("word/document.xml", b"<w:document/>")
        if extra:
            zf.writestr(extra[0], extra[1])
    return out.getvalue()


@pytest.fixture(autouse=True)
def _no_background_jobs(monkeypatch):
    from backend.api import images as images_api

    monkeypatch.setattr(images_api, "_detach", lambda coro: None)


async def test_registration_requires_age_gate(client):
    body = {"email": "age@example.com", "password": "Aa1!aaaaa"}
    r = await client.post("/auth/register", json=body)
    assert r.status_code == 422

    r = await client.post("/auth/register", json={**body, "age_confirmed": False})
    assert r.status_code == 422


async def test_backend_does_not_set_cookies(db_client):
    _, headers = await register_and_login(db_client, email="nocookie@example.com")
    r = await db_client.get("/users/me", headers=headers)
    assert r.status_code == 200
    assert "set-cookie" not in {k.lower() for k in r.headers}


async def test_upload_validation_rejects_scriptable_and_spoofed_files(db_client):
    _, headers = await register_and_login(db_client, email="upload-bad@example.com")

    cases = [
        ("evil.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml"),
        ("evil.html", b"<!doctype html><script>alert(1)</script>", "text/html"),
        ("spoof.jpg", b"plain text, not a jpeg", "image/jpeg"),
        ("bad.png", b"\x89PNG\r\n\x1a\nnot-a-real-png", "image/png"),
    ]
    for name, raw, mime in cases:
        r = await db_client.post(
            "/images/",
            files={"file": (name, raw, mime)},
            headers=headers,
        )
        assert r.status_code in {400, 413, 415}, (name, r.text)


async def test_upload_validation_allows_pdf_text_and_safe_ooxml(db_client):
    _, headers = await register_and_login(db_client, email="upload-good@example.com")
    cases = [
        ("report.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF", "application/pdf"),
        ("notes.txt", b"plain project notes\nno active content", "text/plain"),
        (
            "doc.docx",
            _docx_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]
    for name, raw, mime in cases:
        r = await db_client.post(
            "/images/",
            files={"file": (name, raw, mime)},
            headers=headers,
        )
        assert r.status_code == 201, (name, r.text)


async def test_upload_validation_rejects_unsafe_ooxml_zip(db_client):
    _, headers = await register_and_login(db_client, email="upload-zip@example.com")
    raw = _docx_bytes(("../outside.xml", b"oops"))
    r = await db_client.post(
        "/images/",
        files={
            "file": (
                "bad.docx",
                raw,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=headers,
    )
    assert r.status_code == 415


async def test_signed_download_url_tamper_and_cross_user_checks(db_client):
    _, headers_a = await register_and_login(db_client, email="signed-a@example.com")
    _, headers_b = await register_and_login(db_client, email="signed-b@example.com")
    r = await db_client.post(
        "/images/",
        files={"file": ("pic.png", _png_bytes(), "image/png")},
        headers=headers_a,
    )
    assert r.status_code == 201, r.text
    image_id = r.json()["id"]

    r = await db_client.get(
        f"/images/{image_id}/download-url?variant=served",
        headers=headers_a,
    )
    assert r.status_code == 200
    url = r.json()["url"]
    assert "expires" in url and "sig" in url

    ok = await db_client.get(url)
    assert ok.status_code == 200

    tampered = url.replace("variant=served", "variant=original")
    bad = await db_client.get(tampered)
    assert bad.status_code == 403

    other = await db_client.get(
        f"/images/{image_id}/download-url?variant=served",
        headers=headers_b,
    )
    assert other.status_code == 404


async def test_upload_limits_return_429(monkeypatch):
    from fastapi import Request

    from backend import security
    from backend.config import settings

    security._COUNTERS.clear()
    monkeypatch.setattr(settings, "security_rate_limits_enabled", True)
    monkeypatch.setattr(settings, "upload_max_count_per_hour", 1)
    monkeypatch.setattr(settings, "upload_max_bytes_per_day", 100)

    scope = {
        "type": "http",
        "client": ("203.0.113.5", 12345),
        "headers": [],
        "method": "POST",
        "path": "/images/",
    }
    request = Request(scope)
    await security.enforce_upload_limits("user-a", request, 10)
    with pytest.raises(Exception) as excinfo:
        await security.enforce_upload_limits("user-a", request, 10)
    assert getattr(excinfo.value, "status_code", None) == 429


async def test_admin_role_can_read_but_only_superuser_can_change_roles(db_client):
    _, admin_headers = await register_and_login(db_client, email="role-admin@example.com")
    _, target_headers = await register_and_login(db_client, email="role-target@example.com")
    admin_id = await fetch_user_id("role-admin@example.com")
    target_id = await fetch_user_id("role-target@example.com")

    from backend.db import SessionLocal
    from backend.models import User

    async with SessionLocal() as session:
        admin = await session.get(User, admin_id)
        assert admin is not None
        admin.role = "admin"
        await session.commit()

    r = await db_client.get("/admin/users", headers=admin_headers)
    assert r.status_code == 200

    r = await db_client.patch(
        f"/admin/users/{target_id}/role",
        json={"role": "admin"},
        headers=admin_headers,
    )
    assert r.status_code == 403

    async with SessionLocal() as session:
        superuser = await session.get(User, admin_id)
        assert superuser is not None
        superuser.role = "superuser"
        superuser.is_superuser = True
        await session.commit()

    r = await db_client.patch(
        f"/admin/users/{target_id}/role",
        json={"role": "admin"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert target_headers


async def test_biometric_rls_denies_cross_user_rows(db_client):
    _, headers_a = await register_and_login(db_client, email="rls-a@example.com")
    _, _headers_b = await register_and_login(db_client, email="rls-b@example.com")
    uid_a = await fetch_user_id("rls-a@example.com")
    uid_b = await fetch_user_id("rls-b@example.com")
    await insert_face(uid_a, person_name="A")
    seed_b = await insert_face(uid_b, person_name="B")

    from backend.context import reset_current_user_id, set_current_user_id
    from backend.db import SessionLocal
    from backend.models import Face

    token = set_current_user_id(uid_a)
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(select(Face).where(Face.id == seed_b["face_id"]))
            ).scalars().all()
            assert rows == []
    finally:
        reset_current_user_id(token)
    assert headers_a


async def test_image_delete_removes_derived_rows_objects_and_keeps_audit(db_client):
    _, headers = await register_and_login(db_client, email="delete-image@example.com")
    uid = await fetch_user_id("delete-image@example.com")
    seed = await insert_face(uid, person_name="Delete Me")
    image_id = UUID(str(seed["image_id"]))

    from backend import storage as storage_mod
    from backend.db import SessionLocal
    from backend.models import (
        BanditState,
        CloudFile,
        FeedbackEvent,
        Image,
        ImageGeo,
        ImageTag,
        Tag,
    )

    async with SessionLocal() as session:
        img = await session.get(Image, image_id)
        assert img is not None
        img.thumbnail_blob_key = f"users/{uid}/thumbs/test.webp"
        img.summary = "summary"
        img.summary_topic = "topic"
        img.summary_points = ["point"]
        img.clip_embedding = [0.0] * 768
        session.add(ImageGeo(image_id=image_id, user_id=uid, lat=1.0, lng=2.0))
        tag = Tag(label="synthetic", source="clip")
        session.add(tag)
        await session.flush()
        session.add(ImageTag(image_id=image_id, tag_id=tag.id, confidence=0.9))
        session.add(
            FeedbackEvent(
                user_id=uid,
                image_id=image_id,
                kind="rating",
                rating=5,
                weight=1.0,
                bandit_arm_id=1,
                context_features=[0.1] * 4,
                reward=1.0,
            )
        )
        session.add(
            BanditState(
                user_id=uid,
                arm_id=1,
                a_matrix=b"matrix",
                b_vector=b"vector",
                pulls=1,
            )
        )
        session.add(
            CloudFile(
                user_id=uid,
                provider="google_drive",
                remote_id="remote-1",
                local_image_id=image_id,
            )
        )
        await session.commit()
        await session.refresh(img)
        original_key = img.original_blob_key
        served_key = img.served_blob_key
        thumb_key = img.thumbnail_blob_key

    storage_mod.storage.put(storage_mod.storage.bucket_originals, original_key, b"orig", "image/png")
    storage_mod.storage.put(storage_mod.storage.bucket_served, served_key, b"served", "image/webp")
    storage_mod.storage.put(storage_mod.storage.bucket_served, thumb_key, b"thumb", "image/webp")
    storage_mod.storage.put(storage_mod.storage.bucket_faces, seed["crop_blob_key"], b"crop", "image/jpeg")

    r = await db_client.delete(f"/images/{image_id}", headers=headers)
    assert r.status_code == 204

    async with SessionLocal() as session:
        checks = {
            "images": "SELECT count(*) FROM images WHERE id = :image_id",
            "image_geo": "SELECT count(*) FROM image_geo WHERE image_id = :image_id",
            "image_tags": "SELECT count(*) FROM image_tags WHERE image_id = :image_id",
            "feedback_events": "SELECT count(*) FROM feedback_events WHERE image_id = :image_id",
            "cloud_files": "SELECT count(*) FROM cloud_files WHERE local_image_id = :image_id",
            "face_detections": "SELECT count(*) FROM face_detections WHERE image_id = :image_id",
            "faces": "SELECT count(*) FROM faces WHERE user_id = :uid",
            "persons": "SELECT count(*) FROM persons WHERE user_id = :uid",
            "bandit_state": "SELECT count(*) FROM bandit_state WHERE user_id = :uid",
        }
        for name, sql in checks.items():
            count = (
                await session.execute(text(sql), {"image_id": image_id, "uid": uid})
            ).scalar_one()
            assert int(count) == 0, name
        audit = (
            await session.execute(
                text("SELECT count(*) FROM audit_log WHERE user_id = :uid AND action = 'image.delete'"),
                {"uid": uid},
            )
        ).scalar_one()
        assert int(audit) == 1

    for bucket, key in [
        (storage_mod.storage.bucket_originals, original_key),
        (storage_mod.storage.bucket_served, served_key),
        (storage_mod.storage.bucket_served, thumb_key),
        (storage_mod.storage.bucket_faces, seed["crop_blob_key"]),
    ]:
        with pytest.raises(KeyError):
            storage_mod.storage.get(bucket, key)


def test_production_validation_fails_without_required_security(monkeypatch):
    from backend.config import settings
    from backend.security import validate_production_settings

    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "minio_secure", False)
    monkeypatch.setattr(settings, "frontend_base_url", "http://example.com")
    monkeypatch.setattr(settings, "secret_manager", "env_file")
    monkeypatch.setattr(settings, "postgres_at_rest_encryption", "")
    monkeypatch.setattr(settings, "minio_sse_mode", "off")
    monkeypatch.setattr(settings, "backup_age_recipient", "")

    with pytest.raises(RuntimeError):
        import anyio

        anyio.run(validate_production_settings)
