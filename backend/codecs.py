"""Image codec implementations and the compression policy.

Phase 1 ships a deterministic policy: WebP at quality 82, longest side capped
at 4096px. The four codecs and full arm grid are exposed here so the bandit
in Phase 5 can pick from them without further refactoring.
"""

from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image as PILImage

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    pillow_heif.register_avif_opener()
    AVIF_AVAILABLE = True
except ImportError:
    AVIF_AVAILABLE = False

try:
    import imagecodecs
    import numpy as np

    JXL_AVAILABLE = True
except ImportError:
    JXL_AVAILABLE = False


# `passthrough` is intentionally NOT in ARM_GRID_CODECS — the bandit must
# never choose it. It exists only for inputs we refuse to re-encode at all
# (animated GIFs would lose every frame after the first under any of the
# other codecs). The served bytes are the original bytes, byte-for-byte.
Codec = Literal["webp", "mozjpeg", "avif", "jxl", "passthrough"]

MIME_BY_CODEC: dict[Codec, str] = {
    "webp": "image/webp",
    "mozjpeg": "image/jpeg",
    "avif": "image/avif",
    "jxl": "image/jxl",
    # Filled in dynamically for passthrough — see `CompressionPlan.mime`
    # which special-cases it via `passthrough_mime`.
    "passthrough": "application/octet-stream",
}

EXT_BY_CODEC: dict[Codec, str] = {
    "webp": "webp",
    "mozjpeg": "jpg",
    "avif": "avif",
    "jxl": "jxl",
    "passthrough": "bin",  # overridden by `passthrough_ext` per-call
}

ARM_GRID_CODECS: tuple[Codec, ...] = ("mozjpeg", "webp", "avif", "jxl")
ARM_GRID_QUALITIES: tuple[int, ...] = (55, 65, 75, 85, 92)
ARM_GRID_MAX_DIMS: tuple[int | None, ...] = (None, 4096, 2560, 1920)


@dataclass(frozen=True)
class CompressionPlan:
    codec: Codec
    quality: int
    max_dim: int | None
    lossless: bool = False
    # For `codec=="passthrough"` only — carries the original file's
    # mime + extension through so the served blob keeps its real type
    # (e.g. animated GIF stays as image/gif, not octet-stream).
    passthrough_mime: str | None = None
    passthrough_ext: str | None = None

    @property
    def mime(self) -> str:
        if self.codec == "passthrough" and self.passthrough_mime:
            return self.passthrough_mime
        return MIME_BY_CODEC[self.codec]

    @property
    def extension(self) -> str:
        if self.codec == "passthrough" and self.passthrough_ext:
            return self.passthrough_ext
        return EXT_BY_CODEC[self.codec]


def _resize_if_needed(im: PILImage.Image, max_dim: int | None) -> PILImage.Image:
    if max_dim is None:
        return im
    longest = max(im.size)
    if longest <= max_dim:
        return im
    scale = max_dim / longest
    new_size = (
        int(round(im.size[0] * scale)),
        int(round(im.size[1] * scale)),
    )
    return im.resize(new_size, PILImage.LANCZOS)


def _to_rgb(im: PILImage.Image) -> PILImage.Image:
    if im.mode == "RGB":
        return im
    if im.mode == "RGBA":
        background = PILImage.new("RGB", im.size, (255, 255, 255))
        background.paste(im, mask=im.split()[3])
        return background
    return im.convert("RGB")


def _exif_bytes(im: PILImage.Image) -> bytes | None:
    """Return raw EXIF bytes from an image if present (for re-injection on encode)."""
    raw = im.info.get("exif")
    if raw:
        return raw
    try:
        exif = im.getexif()
        if exif:
            return exif.tobytes()
    except Exception:
        pass
    return None


def encode_webp(
    raw: bytes,
    quality: int,
    max_dim: int | None,
    lossless: bool = False,
) -> bytes:
    with PILImage.open(BytesIO(raw)) as im:
        im.load()
        exif = _exif_bytes(im)
        im = _resize_if_needed(im, max_dim)
        out = BytesIO()
        save_kwargs = {"format": "WEBP", "method": 6}
        if lossless:
            save_kwargs.update({"lossless": True, "quality": 100})
        else:
            save_kwargs["quality"] = quality
        if exif:
            save_kwargs["exif"] = exif
        im.save(out, **save_kwargs)
        return out.getvalue()


def encode_mozjpeg(raw: bytes, quality: int, max_dim: int | None) -> bytes:
    """Pillow JPEG with MozJPEG-style settings (optimize, progressive, 4:2:0).

    True MozJPEG would shave another ~5% off; swap in `imagecodecs.jpeg8_encode`
    or a `mozjpeg` system binding when that's worth the complexity.
    """
    with PILImage.open(BytesIO(raw)) as im:
        im.load()
        exif = _exif_bytes(im)
        im = _to_rgb(im)
        im = _resize_if_needed(im, max_dim)
        out = BytesIO()
        save_kwargs = dict(
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=2,
        )
        if exif:
            save_kwargs["exif"] = exif
        im.save(out, **save_kwargs)
        return out.getvalue()


def encode_avif(raw: bytes, quality: int, max_dim: int | None) -> bytes:
    if not AVIF_AVAILABLE:
        raise RuntimeError("AVIF support requires pillow-heif")
    with PILImage.open(BytesIO(raw)) as im:
        im.load()
        exif = _exif_bytes(im)
        im = _resize_if_needed(im, max_dim)
        out = BytesIO()
        save_kwargs = {"format": "AVIF", "quality": quality}
        if exif:
            save_kwargs["exif"] = exif
        im.save(out, **save_kwargs)
        return out.getvalue()


def encode_jxl(raw: bytes, quality: int, max_dim: int | None) -> bytes:
    if not JXL_AVAILABLE:
        raise RuntimeError("JXL support requires imagecodecs and numpy")
    with PILImage.open(BytesIO(raw)) as im:
        im.load()
        im = _to_rgb(im)
        im = _resize_if_needed(im, max_dim)
        arr = np.asarray(im)
    # JXL "distance": 0.0 is lossless, ~15 is worst. Map quality 0..100 → ~15..0.
    distance = max(0.0, 15.0 * (1.0 - quality / 100.0))
    return imagecodecs.jpegxl_encode(arr, distance=distance)


def compress(raw: bytes, plan: CompressionPlan) -> bytes:
    if plan.codec == "passthrough":
        # The served bytes are the input bytes unchanged. Used for animated
        # GIFs (single-frame re-encoding would discard every frame after
        # the first) and any other format we'd rather not lossily mangle.
        return raw
    if plan.codec == "webp":
        return encode_webp(raw, plan.quality, plan.max_dim, lossless=plan.lossless)
    if plan.codec == "mozjpeg":
        # MozJPEG has no lossless mode; if lossless is requested, fall through
        # to ordinary high-quality encoding. The policy never asks for
        # lossless mozjpeg, so this is just defensive.
        return encode_mozjpeg(raw, plan.quality, plan.max_dim)
    if plan.codec == "avif":
        return encode_avif(raw, plan.quality, plan.max_dim)
    if plan.codec == "jxl":
        return encode_jxl(raw, plan.quality, plan.max_dim)
    raise ValueError(f"Unknown codec: {plan.codec}")


def pick_default_plan(
    input_format: str,
    width: int,
    height: int,
    byte_size: int,
) -> CompressionPlan:
    """Phase-1 deterministic policy.

    Without content classification (added in Phase 2) we keep this safe and
    predictable: WebP q=82 with a 4096-px cap on the longest side. The bandit
    (Phase 5) will pick (codec, quality, max_dim) per image once we have
    feedback to learn from.
    """
    return CompressionPlan(codec="webp", quality=82, max_dim=4096)
