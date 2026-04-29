"""Pure-unit tests for the content-aware compression policy.

No torch / CLIP / DB / MinIO dependencies — exercises the policy on hand-built
VisionContext fixtures only.
"""

from backend.policy import VisionContext, pick_plan


def test_no_vision_falls_back_to_default_plan():
    plan = pick_plan(None, 1024, 768, 5_000_000)
    assert plan.codec == "webp"
    assert plan.quality == 82
    assert plan.max_dim == 4096
    assert plan.lossless is False


def test_low_confidence_falls_back_to_default():
    vctx = VisionContext(content_type="screenshot", content_confidence=0.4)
    plan = pick_plan(vctx, 1024, 768, 5_000_000)
    assert plan.codec == "webp"
    assert plan.quality == 82
    assert plan.lossless is False


def test_high_confidence_screenshot_picks_lossless():
    vctx = VisionContext(content_type="screenshot", content_confidence=0.9)
    plan = pick_plan(vctx, 1920, 1080, 100_000)
    assert plan.codec == "webp"
    assert plan.lossless is True
    assert plan.max_dim is None


def test_high_confidence_document_picks_lossless():
    vctx = VisionContext(content_type="document", content_confidence=0.7)
    plan = pick_plan(vctx, 2480, 3508, 800_000)
    assert plan.lossless is True


def test_high_confidence_illustration_picks_lossless():
    vctx = VisionContext(content_type="illustration", content_confidence=0.65)
    plan = pick_plan(vctx, 1500, 1500, 400_000)
    assert plan.lossless is True


def test_high_confidence_icon_picks_lossless():
    vctx = VisionContext(content_type="icon", content_confidence=0.8)
    plan = pick_plan(vctx, 256, 256, 5_000)
    assert plan.lossless is True


def test_high_confidence_photo_keeps_lossy_q82():
    vctx = VisionContext(content_type="photo", content_confidence=0.95)
    plan = pick_plan(vctx, 4032, 3024, 6_000_000)
    assert plan.codec == "webp"
    assert plan.quality == 82
    assert plan.max_dim == 4096
    assert plan.lossless is False
