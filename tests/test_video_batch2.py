"""Sprint I#6 — Video summary Batch 2 unit tests.

Covers the two pure helpers (frame scene-cut dedup + caption dedup).
The ffmpeg/whisper-bound helpers (_video_has_audio_track) aren't
exercised here — they need a real binary + sample media.
"""

from io import BytesIO

from PIL import Image as PILImage

from backend.summarize import dedup_captions, dedup_frames_by_histogram


def _solid_png(shade: int, size: int = 64) -> bytes:
    """A solid-grey PNG of the given luminance — a stand-in 'frame'."""
    img = PILImage.new("RGB", (size, size), (shade, shade, shade))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------- frame scene-cut dedup ----------


def test_identical_frames_collapse_to_one():
    # A static talking-head clip: 10 identical frames → keep 1.
    frames = [_solid_png(128) for _ in range(10)]
    kept = dedup_frames_by_histogram(frames)
    assert len(kept) == 1


def test_distinct_scenes_all_kept():
    # Three clearly different shades (black, grey, white) — each is a
    # distinct "scene", all kept.
    frames = [_solid_png(0), _solid_png(128), _solid_png(255)]
    kept = dedup_frames_by_histogram(frames)
    assert len(kept) == 3


def test_first_frame_always_kept():
    frames = [_solid_png(50)]
    assert dedup_frames_by_histogram(frames) == frames


def test_mixed_run_keeps_scene_changes_only():
    # black, black, black, white, white → 2 scenes.
    frames = [
        _solid_png(0), _solid_png(0), _solid_png(0),
        _solid_png(255), _solid_png(255),
    ]
    kept = dedup_frames_by_histogram(frames)
    assert len(kept) == 2


def test_undecodable_frames_are_kept():
    # Garbage bytes can't be histogrammed → kept rather than dropped.
    frames = [b"not a png", b"also not a png"]
    kept = dedup_frames_by_histogram(frames)
    assert len(kept) == 2


# ---------- caption dedup ----------


def test_near_duplicate_captions_collapse_keep_longest():
    # The todo's exact example: minor wording variation of one
    # observation → collapse to the more descriptive one.
    caps = ["a man in a suit", "a man in a dark suit"]
    out = dedup_captions(caps)
    assert out == ["a man in a dark suit"]


def test_substantially_richer_caption_not_collapsed():
    # A caption that adds REAL new information (setting, action) is a
    # distinct observation, not a near-dupe — it must survive so the
    # summary keeps that detail. Documents the dedup boundary.
    caps = [
        "a man in a suit",
        "a man wearing a dark suit standing at a podium giving a talk",
    ]
    out = dedup_captions(caps)
    assert len(out) == 2


def test_distinct_captions_preserved():
    caps = [
        "a snowy mountain trail at sunset",
        "a plate of pasta on a wooden table",
        "a laptop showing source code",
    ]
    out = dedup_captions(caps)
    assert len(out) == 3


def test_empty_caption_list():
    assert dedup_captions([]) == []


def test_exact_duplicates_collapse():
    caps = ["a cat on a sofa", "a cat on a sofa", "a cat on a sofa"]
    out = dedup_captions(caps)
    assert out == ["a cat on a sofa"]
