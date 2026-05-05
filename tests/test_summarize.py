"""Phase 11 — AI Vision summary tests.

Document and dispatch logic only — Florence-2 / Pillow paths are exercised
indirectly via monkeypatched stubs since the transformers + model weights
aren't a hard test-env dep.
"""
from __future__ import annotations

from uuid import UUID

from tests.conftest import register_and_login


# ---------- pure-function unit tests ----------


def test_extractive_summary_picks_sentences():
    from backend.summarize import _extractive_summary

    text = (
        "The IStore project lets users upload images, videos, and documents. "
        "It compresses images using a learned bandit policy. "
        "Face recognition is opt-in and BIPA-compliant. "
        "Phase 11 adds AI Vision summaries to power content search. "
        "Florence-2 captions images and sumy summarizes documents."
    )
    summary = _extractive_summary(text)
    assert summary
    # The summary is a strict subset of original sentences (extractive). At
    # least one of the source sentences must appear verbatim.
    assert any(
        s.split(".")[0].strip() in summary
        for s in text.split(". ")
        if s
    )


def test_extract_keypoints_from_bullet_list():
    from backend.summarize import _extract_keypoints

    md = """\
# Notes

- First action item.
- Second action item.
* Third bullet.
1. Numbered point.
Some prose that is not a bullet.
"""
    pts = _extract_keypoints(md)
    assert pts == [
        "First action item.",
        "Second action item.",
        "Third bullet.",
        "Numbered point.",
    ]


def test_extract_plaintext_topic_picks_first_short_line():
    from backend.summarize import _extract_plaintext

    raw = b"# Important Topic\n\nThis is the body."
    text, topic = _extract_plaintext(raw, "note.md")
    assert text.startswith("# Important Topic")
    assert topic == "Important Topic"


# ---------- integration: doc upload + summarize ----------


async def test_uploaded_text_document_gets_summarized(db_client):
    token, headers = await register_and_login(db_client)

    raw = (
        b"# Quarterly Report\n\n"
        b"Revenue grew by twelve percent this quarter. "
        b"Operating costs held steady year over year. "
        b"The Phase 11 release shipped to all customers in May.\n\n"
        b"Next steps:\n"
        b"- Hire two more engineers\n"
        b"- Migrate analytics to the new pipeline\n"
    )
    files = {"file": ("report.md", raw, "text/markdown")}
    r = await db_client.post("/images/", files=files, headers=headers)
    assert r.status_code == 201, r.text
    image_id = UUID(r.json()["id"])

    # BackgroundTask may or may not have completed under the test transport;
    # invoke the summarizer directly so the assertion is deterministic.
    from backend.db import SessionLocal
    from backend.summarize import summarize_image_id

    async with SessionLocal() as session:
        await summarize_image_id(session, image_id)

    r = await db_client.get(f"/images/{image_id}", headers=headers)
    body = r.json()
    assert body["pending_summary"] is False
    assert body["summary_topic"] == "Quarterly Report"
    assert body["summary"]
    # The bullet list should produce keypoints.
    assert "Hire two more engineers" in (body["summary_points"] or [])


async def test_resummarize_clears_and_reschedules(db_client, monkeypatch):
    """The /resummarize endpoint clears the summary columns and reschedules
    the BackgroundTask. We stub the task helper so we can assert on the
    cleared-but-not-yet-regenerated state without racing the worker."""
    from backend.api import images as images_api

    async def _noop(image_id):  # noqa: ARG001
        return None

    monkeypatch.setattr(images_api, "_run_summarize_one", _noop)
    monkeypatch.setattr(
        images_api, "_run_face_scan_then_summarize",
        lambda *a, **k: _noop(None),
    )

    token, headers = await register_and_login(db_client)
    raw = b"Topic line\n\nFirst sentence here. Second sentence."
    files = {"file": ("doc.txt", raw, "text/plain")}
    r = await db_client.post("/images/", files=files, headers=headers)
    image_id = UUID(r.json()["id"])

    from backend.db import SessionLocal
    from backend.summarize import summarize_image_id

    async with SessionLocal() as session:
        await summarize_image_id(session, image_id)

    r = await db_client.post(
        f"/images/{image_id}/resummarize", headers=headers
    )
    assert r.status_code == 202

    r = await db_client.get(f"/images/{image_id}", headers=headers)
    body = r.json()
    assert body["pending_summary"] is True
    assert body["summary"] is None


# ---------- image branch with stubbed Florence-2 ----------


async def test_image_branch_splices_named_person(db_client, monkeypatch):
    """When face recognition has named the person in the image, the BLIP
    caption gets that name spliced in — so search by person name works."""
    import uuid as _uuid

    from tests.conftest import (
        fetch_user_id,
        grant_consent,
        insert_face,
    )

    from backend import summarize as sm
    from backend.api import images as images_api

    monkeypatch.setattr(
        sm,
        "_caption_image",
        lambda raw: "A young man taking a selfie in a kitchen.",
    )
    # Force the regex fallback path so this test doesn't need Qwen2.5
    # weights — we're asserting the deterministic name-splice behavior.
    monkeypatch.setattr(sm, "_llm_rewrite_summary", lambda **kw: None)

    async def _noop(image_id):  # noqa: ARG001
        return None

    monkeypatch.setattr(images_api, "_run_summarize_one", _noop)
    monkeypatch.setattr(
        images_api, "_run_face_scan_then_summarize",
        lambda *a, **k: _noop(None),
    )

    email = f"u{_uuid.uuid4().hex[:8]}@example.com"
    token, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    user_id = await fetch_user_id(email)

    valid_png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    files = {"file": ("me.png", valid_png, "image/png")}
    r = await db_client.post("/images/", files=files, headers=headers)
    assert r.status_code == 201, r.text
    image_id = UUID(r.json()["id"])

    # Stand in for Pass B: insert a face row tied to this image with a
    # named person ("Me"). Bypasses insightface so the test doesn't need
    # GPU weights.
    await insert_face(user_id, person_name="Me", image_id=image_id)

    from backend.db import SessionLocal
    from backend.summarize import summarize_image_id

    async with SessionLocal() as session:
        await summarize_image_id(session, image_id)

    r = await db_client.get(f"/images/{image_id}", headers=headers)
    body = r.json()
    assert body["pending_summary"] is False
    # "a young man" → "Me"
    assert "Me" in body["summary"]
    assert "a young man" not in body["summary"].lower()
    # Topic should be selfie-style with the name
    assert body["summary_topic"] == "Selfie of Me"
    points = body["summary_points"] or []
    assert any("People: Me" in p for p in points)


def test_clean_caption_strips_filler_and_normalizes_selfie():
    from backend.summarize import _clean_caption

    # Strips "This is a" / "There is" filler, preserves searchable content,
    # re-capitalizes the first word, and keeps the terminal period.
    assert _clean_caption(
        "This is a black and white photo of a man looking out the window of a car."
    ) == "A black and white photo of a man looking out the window of a car."

    assert _clean_caption(
        "There is a man standing in front of a whiteboard writing on it."
    ) == "A man standing in front of a whiteboard writing on it."

    # Selfie normalization — BLIP says "picture of himself", users search "selfie".
    assert _clean_caption(
        "There is a man taking a picture of himself in the bathroom mirror."
    ) == "A man taking a selfie in the bathroom mirror."

    # Captions without filler pass through (modulo capitalization).
    assert _clean_caption(
        "A black cat sitting on a windowsill at dusk."
    ) == "A black cat sitting on a windowsill at dusk."

    # Empty / None-ish stay safe.
    assert _clean_caption("") == ""


def test_splice_names_unit():
    from backend.summarize import _splice_names

    cap = "A black and white photo of a man looking out the window of a car."
    assert _splice_names(cap, ["Me"]) == (
        "A black and white photo of Me looking out the window of a car."
    )
    assert _splice_names(cap, []) == cap
    assert _splice_names("a sunset over the ocean.", ["Me"]).endswith(
        "(with Me)"
    )
    assert (
        _splice_names("two people standing on a beach.", ["Me", "Mr Koler"])
        == "Me and Mr Koler standing on a beach."
    )


def test_splice_drops_relative_clause_filler():
    from backend.summarize import _splice_names

    # BLIP often emits "a man that is V-ing" — drop the "that is" once the
    # subject is a real name so we get "Mr Koler V-ing".
    out = _splice_names(
        "A man that is standing in front of a whiteboard.", ["Mr Koler"]
    )
    assert out == "Mr Koler standing in front of a whiteboard."

    out = _splice_names(
        "A woman who is reading a book in a cafe.", ["Alice"]
    )
    assert out == "Alice reading a book in a cafe."


def test_splice_first_person_pronoun_rewrite():
    from backend.summarize import _splice_names

    # When name="Me", downstream "his" / "himself" must flip to first person
    # — otherwise we get "Me taking a selfie with his cell phone".
    out = _splice_names(
        "A man taking a selfie with his cell phone.", ["Me"]
    )
    assert out == "Me taking a selfie with my cell phone."

    out = _splice_names(
        "A man looking at himself in the mirror.", ["Me"]
    )
    assert out == "Me looking at myself in the mirror."

    # Third-person names should NOT trigger the rewrite.
    out = _splice_names(
        "A man taking a selfie with his cell phone.", ["Mr Koler"]
    )
    assert out == "Mr Koler taking a selfie with his cell phone."


async def test_image_branch_composes_summary(db_client, monkeypatch):
    """The image dispatch combines the Florence-2 caption with composed
    structured fields (scene, indoor_outdoor, content_type). We stub the
    caption helper so the test doesn't need transformers, and we no-op the
    upload-time BackgroundTask so we can drive summarize manually after
    seeding deterministic vision fields."""
    from backend import summarize as sm
    from backend.api import images as images_api

    monkeypatch.setattr(
        sm,
        "_caption_image",
        lambda raw: "A black cat sitting on a windowsill at dusk.",
    )
    # Same reason as above — keep the assertion deterministic without
    # spinning up the rewriter LLM.
    monkeypatch.setattr(sm, "_llm_rewrite_summary", lambda **kw: None)

    async def _noop(image_id):  # noqa: ARG001
        return None

    monkeypatch.setattr(images_api, "_run_summarize_one", _noop)
    monkeypatch.setattr(
        images_api, "_run_face_scan_then_summarize",
        lambda *a, **k: _noop(None),
    )

    token, headers = await register_and_login(db_client)
    # Smallest valid PNG: 1×1 transparent pixel.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452"
        "00000001000000010806000000"
        "1f15c4890000000d4944415478"
        "9c63000100000005000100"
        "0d0a2db40000000049454e44ae426082"
    )
    # Pad with a known-good tiny PNG so the upload pipeline accepts it.
    valid_png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    files = {"file": ("cat.png", valid_png, "image/png")}
    r = await db_client.post("/images/", files=files, headers=headers)
    assert r.status_code == 201, r.text
    image_id = UUID(r.json()["id"])

    from backend.db import SessionLocal
    from backend.models import Image
    from backend.summarize import summarize_image_id

    # Set deterministic vision fields so the composed topic/points are
    # predictable. (VISION_ENABLED is off in the test env.)
    async with SessionLocal() as session:
        img = await session.get(Image, image_id)
        img.scene_label = "beach"
        img.scene_confidence = 0.8
        img.indoor_outdoor = "outdoor"
        img.content_type = "photo"
        img.face_likelihood = 0.7
        await session.commit()

    async with SessionLocal() as session:
        await summarize_image_id(session, image_id)

    r = await db_client.get(f"/images/{image_id}", headers=headers)
    body = r.json()
    assert body["pending_summary"] is False
    assert "black cat" in body["summary"].lower()
    assert "Beach" in body["summary_topic"]
    points = body["summary_points"] or []
    assert any("people" in p.lower() for p in points)
