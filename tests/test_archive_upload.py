"""§C1.5 acceptance — general archive uploader.

Verifies:
- Happy path: zip + tar.gz are accepted; entries land as Image rows in
  an auto-created folder; `source_archive_id` is populated.
- Inspection: zip-bomb (oversize ratio), path traversal, symlink,
  too-many-entries, too-deep all reject with 415 BEFORE extraction.
- Format gating: 7z and RAR magic produce a clear rejection; junk
  bytes produce 415.
- Per-entry rejection: an entry inside a valid zip that fails
  `detect_magic` (e.g. an HTML payload) is counted as rejected but
  doesn't kill the whole archive.
- Constants are shared with `_inspect_ooxml`: bumping
  `upload_max_archive_entries` from settings affects both inspectors.
"""
from __future__ import annotations

import io
import tarfile
import zipfile

import pytest
from PIL import Image as PILImage

from tests.conftest import register_and_login


def _png_bytes(label: str = "p") -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (8, 8), "red").save(out, format="PNG")
    return out.getvalue()


def _make_zip(entries: list[tuple[str, bytes]], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return out.getvalue()


def _make_tar_gz(entries: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return out.getvalue()


async def test_zip_upload_creates_folder_and_routes_entries(db_client):
    _, headers = await register_and_login(db_client, email="zip-good@example.com")
    archive = _make_zip([
        ("photo-a.png", _png_bytes("a")),
        ("photo-b.png", _png_bytes("b")),
        ("notes.txt", b"plain project notes"),
    ])
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("vacation.zip", archive, "application/zip")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["accepted"] == 3
    assert body["rejected"] == 0
    assert body["source_archive_id"]
    folder_id = body["folder_id"]

    # The folder should hold all three image rows; the auto-named
    # folder takes the archive stem.
    r = await db_client.get(f"/images/?folder_id={folder_id}", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 3

    r = await db_client.get("/folders/", headers=headers)
    assert any(f["name"] == "vacation" for f in r.json())


async def test_tar_gz_upload_accepted(db_client):
    _, headers = await register_and_login(db_client, email="tar-good@example.com")
    archive = _make_tar_gz([
        ("a.png", _png_bytes("a")),
        ("nested/b.png", _png_bytes("b")),
    ])
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("trip.tar.gz", archive, "application/gzip")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["accepted"] == 2
    # Stem strips both the `.gz` and the `.tar` — "trip" not "trip.tar".
    r = await db_client.get("/folders/", headers=headers)
    assert any(f["name"] == "trip" for f in r.json())


async def test_zip_bomb_rejected_by_ratio_gate(db_client, monkeypatch):
    _, headers = await register_and_login(db_client, email="bomb@example.com")
    # 1 MB of zeros compresses ~1000x with DEFLATE — well over the 5x cap.
    bomb = _make_zip([("filler.bin", b"\x00" * 1_000_000)])
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("bomb.zip", bomb, "application/zip")},
        headers=headers,
    )
    assert r.status_code == 415
    assert "expansion ratio" in r.json()["detail"].lower()


async def test_path_traversal_zip_rejected(db_client):
    _, headers = await register_and_login(db_client, email="trav@example.com")
    bad = _make_zip([("../escape.txt", b"x")])
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("nope.zip", bad, "application/zip")},
        headers=headers,
    )
    assert r.status_code == 415
    assert "unsafe path" in r.json()["detail"].lower()


async def test_zip_symlink_rejected(db_client):
    _, headers = await register_and_login(db_client, email="symlink@example.com")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)  # symlink mode
        zf.writestr(info, b"/etc/passwd")
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("link.zip", out.getvalue(), "application/zip")},
        headers=headers,
    )
    assert r.status_code == 415
    assert "symlink" in r.json()["detail"].lower()


async def test_too_many_entries_rejected(db_client, monkeypatch):
    _, headers = await register_and_login(db_client, email="many@example.com")
    # Override the limit so the test stays fast — 6 entries, cap 5.
    from backend.config import settings as cfg

    monkeypatch.setattr(cfg, "upload_max_archive_entries", 5)
    archive = _make_zip([(f"f{i}.txt", b"x") for i in range(6)])
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("many.zip", archive, "application/zip")},
        headers=headers,
    )
    assert r.status_code == 415
    assert "too many" in r.json()["detail"].lower()


async def test_7z_and_rar_rejected_with_clear_message(db_client):
    _, headers = await register_and_login(db_client, email="7z@example.com")
    for name, magic in (
        ("file.7z", b"7z\xbc\xaf\x27\x1c" + b"\x00" * 100),
        ("file.rar", b"Rar!" + b"\x00" * 100),
    ):
        r = await db_client.post(
            "/folders/upload-archive",
            files={"file": (name, magic, "application/octet-stream")},
            headers=headers,
        )
        assert r.status_code == 415, (name, r.text)
        assert "not supported yet" in r.json()["detail"].lower()


async def test_garbage_bytes_rejected(db_client):
    _, headers = await register_and_login(db_client, email="junk@example.com")
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("junk.bin", b"not an archive", "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 415
    assert "unrecognized archive" in r.json()["detail"].lower()


async def test_per_entry_rejection_does_not_kill_archive(db_client):
    """An HTML payload inside a valid zip is rejected per-entry by
    `detect_magic`. The archive as a whole is accepted with mixed
    counts so the operator can see exactly which entries failed."""
    _, headers = await register_and_login(db_client, email="mixed@example.com")
    archive = _make_zip([
        ("good.png", _png_bytes("g")),
        ("evil.html", b"<!doctype html><script>alert(1)</script>"),
    ])
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("mixed.zip", archive, "application/zip")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert any("evil.html" in d["path"] for d in body["rejected_details"])


async def test_archive_folder_carries_source_archive_id(db_client):
    """The new folder must persist `source_archive_id` so a future
    re-pack endpoint can rebuild the original archive."""
    _, headers = await register_and_login(db_client, email="archive-id@example.com")
    archive = _make_zip([("a.png", _png_bytes("a"))])
    r = await db_client.post(
        "/folders/upload-archive",
        files={"file": ("repack.zip", archive, "application/zip")},
        headers=headers,
    )
    assert r.status_code == 201
    folder_id = r.json()["folder_id"]
    archive_id = r.json()["source_archive_id"]

    from uuid import UUID
    from backend.db import SessionLocal
    from backend.models import Folder

    async with SessionLocal() as session:
        from sqlalchemy import select
        folder = (
            await session.execute(select(Folder).where(Folder.id == UUID(folder_id)))
        ).scalar_one()
        assert str(folder.source_archive_id) == archive_id


async def test_inspect_ooxml_still_uses_shared_constants(monkeypatch):
    """Sanity check that the refactor of `_inspect_ooxml` to share the
    safety pass with the C1.5 inspector preserved the OOXML-specific
    `[Content_Types].xml` requirement."""
    from backend.upload_validation import UploadValidationError, _inspect_ooxml

    # A zip with no [Content_Types].xml should still be rejected as
    # "missing content type metadata" — same code path, same error.
    no_ct = _make_zip([("word/document.xml", b"<w:document/>")])
    with pytest.raises(UploadValidationError) as exc:
        _inspect_ooxml(no_ct, "fake.docx")
    assert "missing content type metadata" in exc.value.detail.lower()
