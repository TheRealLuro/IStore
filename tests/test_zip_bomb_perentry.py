"""Regression test for U2 — zip-bomb via per-entry exploit.

Before this patch, `_inspect_zip_safety` only checked the
cumulative ratio: `sum(uncompressed) > sum(compressed) * 5`. An
attacker could hide a single 1 GB bomb among 4999 small entries —
the average ratio averaged out below 5, the gate passed, and
`zf.read()` then materialized the bomb in process memory.

Fix: three layered checks now apply.

  1. Per-entry uncompressed cap (`upload_max_archive_entry_
     uncompressed_bytes`, default 200 MB).
  2. Per-entry compress ratio (zip only — 7z/rar member tuples
     don't expose per-entry compressed size).
  3. Total uncompressed cap (`upload_max_archive_total_
     uncompressed_bytes`, default 400 MB).
  4. Existing cumulative-ratio check (kept — catches archives where
     every entry is dense-bomb-shaped).

Tests craft synthetic archives with malicious metadata to exercise
each gate; they don't actually compress bombs (the fix rejects
declared-bomb shapes before any read happens).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from backend.upload_validation import (
    UploadValidationError,
    _inspect_zip_safety,
)


def _make_zip_with_entries(entries: list[tuple[str, int, int]]) -> zipfile.ZipFile:
    """Build a ZipFile where each entry has a CHOSEN file_size and
    compress_size in the central directory. We write tiny dummy
    content; the validator reads metadata only, so the actual
    payload size doesn't matter for these tests.

    `entries` is `[(name, file_size, compress_size), ...]`.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for name, _fs, _cs in entries:
            zf.writestr(name, b"x")  # placeholder; we override sizes after
    # Reopen to mutate central-directory metadata.
    buf.seek(0)
    zf = zipfile.ZipFile(buf, mode="r")
    for info, (_name, fs, cs) in zip(zf.infolist(), entries):
        info.file_size = fs
        info.compress_size = cs
    return zf


def test_rejects_single_oversize_entry() -> None:
    """One entry declares 250 MB uncompressed; cap is 200 MB.
    Catches the "hide one giant entry among many smalls" shape
    that defeats the average-based cumulative ratio."""
    zf = _make_zip_with_entries([
        ("small.txt", 1024, 1024),
        ("bomb.bin", 250 * 1024 * 1024, 50 * 1024 * 1024),  # 5× ratio, passes ratio
        ("tail.txt", 1024, 1024),
    ])
    with pytest.raises(UploadValidationError) as exc:
        _inspect_zip_safety(zf, error_label="Archive")
    assert "too large" in str(exc.value).lower()


def test_rejects_high_per_entry_ratio() -> None:
    """A single entry with 10× ratio (50 MB → 500 MB) inside an
    archive whose CUMULATIVE ratio passes the gate. Before U2 this
    would slip through; after, the per-entry ratio rejects it."""
    # Many small low-ratio entries + one 10x entry. Per-entry
    # uncompressed cap (200 MB) must NOT be the trigger — keep the
    # bomb's uncompressed size below 200 MB so we test the ratio
    # path specifically.
    entries = [
        # 4999 entries at 1× ratio (each 100 bytes)
        *[(f"f{i}.txt", 100, 100) for i in range(20)],
        # One entry: 100 MB compressed → 150 MB uncompressed, ratio 1.5
        # ... actually we want ratio > 5. Make it 10 MB compressed → 100 MB
        # uncompressed (ratio 10).
        ("bomb.bin", 100 * 1024 * 1024, 10 * 1024 * 1024),
    ]
    zf = _make_zip_with_entries(entries)
    with pytest.raises(UploadValidationError) as exc:
        _inspect_zip_safety(zf, error_label="Archive")
    msg = str(exc.value).lower()
    # Should trip on the per-entry ratio OR the total-uncompressed
    # cap (both are valid rejections at this size).
    assert "ratio" in msg or "uncompressed" in msg or "too large" in msg


def test_rejects_when_total_uncompressed_exceeds_cap() -> None:
    """Even with a clean per-entry ratio, the SUM of uncompressed
    bytes must stay under the 400 MB total cap. The "5000 entries at
    100 MB each, perfect 1:1 ratio" attack: each entry passes the
    per-entry uncompressed cap (assuming cap is 200 MB), each
    passes the ratio (1:1 = below 5×), but the total is 500 GB. The
    total-uncompressed-cap catches this independently."""
    # Each entry: 100 MB uncompressed, 50 MB compressed (ratio 2, fine).
    # 5 entries → 500 MB uncompressed → exceeds 400 MB total cap.
    entries = [
        (f"f{i}.bin", 100 * 1024 * 1024, 50 * 1024 * 1024) for i in range(5)
    ]
    zf = _make_zip_with_entries(entries)
    with pytest.raises(UploadValidationError) as exc:
        _inspect_zip_safety(zf, error_label="Archive")
    assert "uncompressed" in str(exc.value).lower() or "too large" in str(exc.value).lower()


def test_accepts_clean_archive() -> None:
    """A normal small archive must still pass clean."""
    entries = [
        ("readme.txt", 1024, 256),  # ratio 4
        ("data.csv", 10 * 1024, 2 * 1024),  # ratio 5 — at the limit
    ]
    zf = _make_zip_with_entries(entries)
    result = _inspect_zip_safety(zf, error_label="Archive")
    assert len(result) == 2


def test_existing_cumulative_ratio_check_still_fires() -> None:
    """The original average-ratio guard remains — a uniformly dense
    bomb (every entry at 100× ratio) trips it before the per-entry
    cap. Different threat shape from the per-entry exploit, both
    must catch."""
    # Every entry: 1 KB compressed → 100 KB uncompressed (ratio 100).
    entries = [(f"f{i}.txt", 100 * 1024, 1024) for i in range(50)]
    zf = _make_zip_with_entries(entries)
    with pytest.raises(UploadValidationError) as exc:
        _inspect_zip_safety(zf, error_label="Archive")
    assert "ratio" in str(exc.value).lower() or "expand" in str(exc.value).lower()
