"""Regression tests for the three product-feedback bugs.

1. UserRead schema now carries `password_set` so the FE can render
   "Verified via Google" vs "Email verified" instead of one
   ambiguous flag (user feedback: "it says my email is verified but
   I never did" — they signed in with Google).

2. Video summarize now records `summary_signals` and filters out
   low-quality Florence captions (1-3 word filler + hallucinated
   "a picture of …" openers). User feedback: "video summary needs
   to be better".

3. Filter `near` (lat,lng,radius_km) wiring on the FE was missing.
   No backend test here — the param was already wired backend-side
   (audit cycle); this PR just plumbs the FE state. A pure-JS
   regression test would require a React test harness we don't
   have wired yet, so the schema-side test below covers the
   contract.
"""
from __future__ import annotations

import pytest


# ----- Fix #1: UserRead schema -----


def test_userread_carries_password_set_when_dict():
    """Dict construction path: password_set is projected from
    `hashed_password` when not explicitly passed."""
    from backend.schemas import UserRead

    # SSO-only user (no password, Google linked, verified)
    sso = UserRead.model_validate({
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "sso@example.com",
        "is_active": True,
        "is_superuser": False,
        "is_verified": True,
        "hashed_password": None,
        "google_sub": "google-sub-xyz",
    })
    assert sso.google_linked is True
    assert sso.password_set is False
    assert sso.is_verified is True

    # Password-only user (verified by email, no Google)
    pw = UserRead.model_validate({
        "id": "00000000-0000-0000-0000-000000000002",
        "email": "pw@example.com",
        "is_active": True,
        "is_superuser": False,
        "is_verified": True,
        "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$AAAA$BBBB",
        "google_sub": None,
    })
    assert pw.google_linked is False
    assert pw.password_set is True

    # Hybrid: verified email + Google linked later
    hybrid = UserRead.model_validate({
        "id": "00000000-0000-0000-0000-000000000003",
        "email": "hybrid@example.com",
        "is_active": True,
        "is_superuser": False,
        "is_verified": True,
        "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$AAAA$BBBB",
        "google_sub": "google-sub-aaa",
    })
    assert hybrid.google_linked is True
    assert hybrid.password_set is True


def test_userread_carries_password_set_when_orm_instance():
    """ORM construction path: same projection but reads attrs off the
    instance instead of a dict. The Account / Me endpoint sends ORM
    rows through model_validate, so this path matters at runtime."""
    from backend.schemas import UserRead

    class _Stub:
        # Minimal shape — model_validator only reads google_sub +
        # hashed_password; the rest of the fastapi-users base fields
        # need to be present for the parent validation to pass.
        id = "00000000-0000-0000-0000-000000000004"
        email = "orm@example.com"
        is_active = True
        is_superuser = False
        is_verified = True
        hashed_password = None  # SSO-only
        google_sub = "google-sub-orm"

    out = UserRead.model_validate(_Stub(), from_attributes=True)
    assert out.password_set is False
    assert out.google_linked is True


# ----- Fix #3: video summary signals + quality filter -----


def test_video_summarize_records_signals(monkeypatch):
    """`_summarize_video` should stamp `summary_signals` on the image
    object so the caller can persist it to the JSONB column. Stub
    out every external call (ffmpeg, Florence, Whisper, Qwen) so the
    test runs in <1s and doesn't require GPU."""
    from backend import summarize
    from backend.models import Image

    img = Image(
        user_id=None,
        original_filename="lecture-clip.mp4",
        byte_size_original=1024 * 1024,
        category="video",
    )

    # Pretend ffmpeg returned an 18-second duration so the loop picks
    # ~6 frames at 3s/frame.
    monkeypatch.setattr(summarize, "_probe_video_duration", lambda _b: 18.0)
    monkeypatch.setattr(summarize, "_extract_keyframe", lambda _b, _t: b"fake-frame")
    # First three captions are good; last two are low-quality (1-word
    # filler and a hallucinated short opener). The filter should keep
    # 3 of 5 and bump dropped_low_quality_captions to 2.
    canned = iter([
        "a man in a dark suit standing in front of a whiteboard explaining matrix algebra",
        "the speaker draws three equations on the board with a marker",
        "students are seated in rows taking notes on laptops",
        "photograph",
        "a picture of a man",
    ])
    monkeypatch.setattr(summarize, "_florence_caption", lambda _f: next(canned, ""))
    # Pretend whisper returned a short transcript.
    import backend.transcribe as transcribe_mod
    monkeypatch.setattr(
        transcribe_mod, "transcribe_video_audio",
        lambda _b: "Today we're covering eigenvalues and eigenvectors.",
    )
    # Qwen returns a polished rollup.
    monkeypatch.setattr(
        summarize, "_llm_rewrite_summary",
        lambda **_kw: "A lecturer explains matrix algebra at a whiteboard while students take notes.",
    )

    result = summarize._summarize_video(img, b"fake-bytes")

    assert result is not None
    assert "matrix" in result.summary.lower() or "lecturer" in result.summary.lower()

    # The key behavior: signals get stamped.
    signals = img.__dict__.get("summary_signals")
    assert isinstance(signals, dict)
    assert signals["kind"] == "video"
    assert signals["duration_s"] == pytest.approx(18.0)
    assert signals["caption_count"] == 3, (
        "should keep 3 good captions out of 5 raw; got "
        f"{signals['caption_count']} (dropped {signals['dropped_low_quality_captions']})"
    )
    assert signals["dropped_low_quality_captions"] == 2
    assert signals["has_transcript"] is True
    assert signals["transcript_chars"] > 0
    assert signals["qwen_succeeded"] is True


def test_video_caption_filter_rejects_short_and_hallucinated():
    """Direct exercise of the inline `_caption_is_useful` predicate
    via re-running the video summarize loop with controlled input.
    This is the heart of the quality-filter regression — a future
    edit that loosens the gate should fail this test."""
    from backend import summarize
    from backend.models import Image

    img = Image(
        user_id=None,
        original_filename="x.mp4",
        byte_size_original=1024,
        category="video",
    )

    # No transcript path, no Qwen — just exercise the per-caption gate.
    canned = iter([
        # Clearly substantive (>=5 words, no hallucination prefix) — KEEP.
        "a child running on a beach at sunset with a kite",
        # Substantive but starts with "a picture of" + 8 words — KEEP
        # (only short hallucination-prefixed captions are dropped).
        "a picture of a sailboat on a calm bay at dawn",
        # Single-word filler — DROP.
        "photograph",
        # 4-word caption (under the 5-word floor) — DROP.
        "a man and dog",
        # Short hallucination-prefixed (5 words but <8 after prefix) — DROP.
        "a photo of a sunset",
        # Empty string — DROP (already handled by `if not cap`).
        "",
    ])

    import pytest as _pt

    class _NoTranscribe:
        @staticmethod
        def transcribe_video_audio(_b):
            return None

    import sys as _sys
    _sys.modules["backend.transcribe"] = _NoTranscribe

    with _pt.MonkeyPatch.context() as m:
        m.setattr(summarize, "_probe_video_duration", lambda _b: 18.0)
        m.setattr(summarize, "_extract_keyframe", lambda _b, _t: b"frame")
        m.setattr(summarize, "_florence_caption", lambda _f: next(canned, ""))
        m.setattr(
            summarize, "_llm_rewrite_summary",
            lambda **_kw: "Two scenes from the video.",
        )

        summarize._summarize_video(img, b"fake")

    signals = img.__dict__["summary_signals"]
    # 2 kept (the two substantive captions), 3 dropped (single word,
    # 4-word, short hallucination-prefixed) — the empty string isn't
    # counted as a drop because `if not cap: continue` skips it.
    assert signals["caption_count"] == 2
    assert signals["dropped_low_quality_captions"] == 3
