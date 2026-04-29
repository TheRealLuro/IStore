from io import BytesIO

from PIL import Image as PILImage

from backend.codecs import (
    AVIF_AVAILABLE,
    JXL_AVAILABLE,
    CompressionPlan,
    compress,
    encode_avif,
    encode_jxl,
    encode_mozjpeg,
    encode_webp,
    pick_default_plan,
)


def _png_bytes(width: int = 200, height: int = 150, color: str = "red") -> bytes:
    im = PILImage.new("RGB", (width, height), color)
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def test_default_plan_is_webp_q82_capped_at_4096():
    plan = pick_default_plan("png", 1024, 768, 5_000_000)
    assert plan.codec == "webp"
    assert plan.quality == 82
    assert plan.max_dim == 4096
    assert plan.mime == "image/webp"


def test_encode_webp_preserves_dimensions_when_under_cap():
    raw = _png_bytes(640, 480)
    out = encode_webp(raw, quality=82, max_dim=None)
    with PILImage.open(BytesIO(out)) as im:
        assert im.size == (640, 480)


def test_resize_caps_longest_side():
    raw = _png_bytes(2000, 1000)
    out = encode_webp(raw, quality=82, max_dim=1000)
    with PILImage.open(BytesIO(out)) as im:
        assert max(im.size) == 1000
        # Aspect ratio preserved (within rounding tolerance).
        assert abs(im.size[0] / im.size[1] - 2.0) < 0.01


def test_encode_mozjpeg_produces_jpeg():
    raw = _png_bytes(200, 200)
    out = encode_mozjpeg(raw, quality=82, max_dim=None)
    # JPEG magic bytes.
    assert out[:3] == b"\xff\xd8\xff"


def test_compress_dispatches_webp():
    raw = _png_bytes(100, 100)
    plan = CompressionPlan(codec="webp", quality=80, max_dim=None)
    out = compress(raw, plan)
    assert out[:4] == b"RIFF"


def test_compress_dispatches_mozjpeg():
    raw = _png_bytes(100, 100)
    plan = CompressionPlan(codec="mozjpeg", quality=80, max_dim=None)
    out = compress(raw, plan)
    assert out[:3] == b"\xff\xd8\xff"


def test_avif_round_trip_when_available():
    if not AVIF_AVAILABLE:
        return
    raw = _png_bytes(120, 120)
    out = encode_avif(raw, quality=70, max_dim=None)
    assert len(out) > 0
    with PILImage.open(BytesIO(out)) as im:
        assert im.size == (120, 120)


def test_jxl_round_trip_when_available():
    if not JXL_AVAILABLE:
        return
    raw = _png_bytes(120, 120)
    out = encode_jxl(raw, quality=70, max_dim=None)
    assert len(out) > 0
