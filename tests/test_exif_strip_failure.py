"""Regression tests for U5 + U6 — EXIF / metadata strip privacy gap.

U5: image EXIF strip silently fell through on Pillow failure.
Pre-U5, `_strip_exif_bytes` returned None on any exception and the
caller did `if stripped is not None: raw_bytes = stripped` — so a
Pillow hiccup left the ORIGINAL EXIF-bearing bytes in raw_bytes.
The user opted out of `gps_retention` / `exif_retention`, expected
stripped originals, got EXIF anyway.

U6: video container metadata was never stripped at all. MP4 / MOV
udta atoms (incl. GPS), device model, recording timestamps all
persisted in the originals bucket regardless of consent.

The fix in both cases: helper raises a typed exception on failure;
the caller translates to a 422 upload rejection — never silently
keeps the unstripped bytes.
"""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

from backend.image import (
    ExifStripFailure,
    VideoMetadataStripFailure,
    _strip_exif_bytes,
    _strip_video_metadata,
)


def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (width, height), "blue").save(out, format="PNG")
    return out.getvalue()


def _jpeg_bytes(width: int = 8, height: int = 8) -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (width, height), "red").save(out, format="JPEG", quality=80)
    return out.getvalue()


# ---------- U5: image EXIF ----------


def test_strip_exif_png_is_noop() -> None:
    raw = _png_bytes()
    assert _strip_exif_bytes(raw, "image/png") == raw


def test_strip_exif_jpeg_returns_reencoded_bytes() -> None:
    raw = _jpeg_bytes()
    result = _strip_exif_bytes(raw, "image/jpeg")
    assert isinstance(result, bytes) and len(result) > 0
    PILImage.open(io.BytesIO(result)).verify()


def test_strip_exif_raises_on_unsupported_image_mime() -> None:
    """U5 contract: unsupported image MIME must RAISE, not silently
    fall back to the original bytes."""
    with pytest.raises(ExifStripFailure):
        _strip_exif_bytes(b"<svg/>", "image/svg+xml")


def test_strip_exif_raises_on_pillow_failure() -> None:
    """The U5 attack case: Pillow can't decode the bytes. Pre-U5
    the function returned None and the caller kept the EXIF-bearing
    original. Post-U5 the function raises."""
    with pytest.raises(ExifStripFailure):
        _strip_exif_bytes(b"not a real jpeg", "image/jpeg")


def test_strip_exif_non_image_mime_is_noop() -> None:
    """Defense-in-depth: video MIME accidentally passed to the
    image helper should no-op (video path has its own helper)."""
    raw = b"some bytes"
    assert _strip_exif_bytes(raw, "video/mp4") == raw


def test_strip_exif_blank_mime_is_noop() -> None:
    raw = b"unknown bytes"
    assert _strip_exif_bytes(raw, None) == raw


# ---------- U6: video metadata ----------


def test_strip_video_metadata_non_av_mime_is_noop() -> None:
    raw = _jpeg_bytes()
    assert _strip_video_metadata(raw, "image/jpeg") == raw


def test_strip_video_metadata_raises_on_ffmpeg_failure() -> None:
    """Garbage bytes claimed as video/mp4 → ffmpeg returns non-zero.
    The helper translates this to VideoMetadataStripFailure."""
    with pytest.raises(VideoMetadataStripFailure):
        _strip_video_metadata(b"not actually a video", "video/mp4")


def test_strip_video_metadata_uses_protocol_whitelist() -> None:
    """ffmpeg invocation must include CR-6's safe_input_args()."""
    import subprocess
    captured: list[str] = []

    def fake_run(cmd, *args, **kwargs):
        captured.extend(cmd)
        class _R:
            returncode = 1
            stderr = b"forced fail for test"
            stdout = b""
        return _R()

    with patch.object(subprocess, "run", side_effect=fake_run):
        with pytest.raises(VideoMetadataStripFailure):
            _strip_video_metadata(b"x" * 100, "video/mp4")

    assert "-protocol_whitelist" in captured, (
        "Video metadata strip's ffmpeg call must use safe_input_args() "
        "to inherit the CR-6 protocol whitelist."
    )


def test_strip_video_metadata_drops_global_metadata() -> None:
    """ffmpeg argv must carry the global -map_metadata -1 flag,
    otherwise MP4 udta atoms (incl. udta.gps) survive `-c copy`."""
    import subprocess
    captured: list[str] = []

    def fake_run(cmd, *args, **kwargs):
        captured.extend(cmd)
        class _R:
            returncode = 1
            stderr = b""
            stdout = b""
        return _R()

    with patch.object(subprocess, "run", side_effect=fake_run):
        with pytest.raises(VideoMetadataStripFailure):
            _strip_video_metadata(b"x" * 100, "video/mp4")

    found_global = any(
        captured[i] == "-map_metadata" and captured[i + 1] == "-1"
        for i in range(len(captured) - 1)
    )
    assert found_global


def test_strip_video_metadata_drops_per_stream() -> None:
    """Per-stream metadata (creation_time / encoder tags on each
    track) must also be dropped — -map_metadata:s:v / s:a -1."""
    import subprocess
    captured: list[str] = []

    def fake_run(cmd, *args, **kwargs):
        captured.extend(cmd)
        class _R:
            returncode = 1
            stderr = b""
            stdout = b""
        return _R()

    with patch.object(subprocess, "run", side_effect=fake_run):
        with pytest.raises(VideoMetadataStripFailure):
            _strip_video_metadata(b"x" * 100, "video/mp4")

    assert "-map_metadata:s:v" in captured
    assert "-map_metadata:s:a" in captured


def test_strip_video_metadata_uses_c_copy_no_reencode() -> None:
    """Remux only — no re-encode. -c copy is the perf-critical flag."""
    import subprocess
    captured: list[str] = []

    def fake_run(cmd, *args, **kwargs):
        captured.extend(cmd)
        class _R:
            returncode = 1
            stderr = b""
            stdout = b""
        return _R()

    with patch.object(subprocess, "run", side_effect=fake_run):
        with pytest.raises(VideoMetadataStripFailure):
            _strip_video_metadata(b"x" * 100, "video/mp4")

    found_copy = any(
        captured[i] == "-c" and captured[i + 1] == "copy"
        for i in range(len(captured) - 1)
    )
    assert found_copy, (
        "ffmpeg invocation missing `-c copy`; would force a re-encode "
        "instead of a fast remux."
    )
