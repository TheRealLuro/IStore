"""Quick compression benchmark.

Usage:
    python scripts/bench_codecs.py                # synthetic test images
    python scripts/bench_codecs.py path/to/*.jpg  # real images

Prints per-codec size + ratio + encode time for each input.
"""

from __future__ import annotations

import math
import sys
import time
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage, ImageDraw, ImageFilter

# Make `backend.codecs` importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.codecs import (  # noqa: E402
    AVIF_AVAILABLE,
    JXL_AVAILABLE,
    encode_avif,
    encode_jxl,
    encode_mozjpeg,
    encode_webp,
)


def synthetic_photo(w: int = 1920, h: int = 1280) -> bytes:
    """Smooth gradient + filtered noise — approximates a real photograph."""
    import random

    im = PILImage.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            r = int(80 + 120 * x / w + 30 * math.sin(y / 40))
            g = int(60 + 100 * y / h + 20 * math.cos(x / 50))
            b = int(120 + 60 * (x + y) / (w + h))
            px[x, y] = (
                max(0, min(255, r + random.randint(-12, 12))),
                max(0, min(255, g + random.randint(-12, 12))),
                max(0, min(255, b + random.randint(-12, 12))),
            )
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def synthetic_screenshot(w: int = 1920, h: int = 1080) -> bytes:
    """Flat color regions + sharp horizontal lines — mimics UI/text content."""
    im = PILImage.new("RGB", (w, h), (245, 246, 250))
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, w, 80], fill=(30, 41, 59))
    draw.rectangle([0, 80, 280, h], fill=(241, 245, 249))
    draw.rectangle([280, 80, w, h], fill=(255, 255, 255))
    for i in range(20):
        y = 140 + i * 38
        draw.rectangle([320, y, 320 + 600, y + 14], fill=(51, 65, 85))
        draw.rectangle([320, y + 18, 320 + 800, y + 24], fill=(148, 163, 184))
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def synthetic_detail(w: int = 1920, h: int = 1280) -> bytes:
    """High-frequency texture — punishes lossy codecs."""
    import random

    im = PILImage.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            v = random.randint(0, 255)
            px[x, y] = (v, max(0, v - 30), min(255, v + 20))
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def run_codec(name: str, fn, raw: bytes, **kwargs) -> tuple[int, float]:
    t0 = time.perf_counter()
    out = fn(raw, **kwargs)
    return len(out), time.perf_counter() - t0


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:6.1f} {unit}"
        n = n / 1024
    return f"{n:6.1f} GB"


def bench_one(name: str, raw: bytes, max_dim: int | None = 4096) -> None:
    with PILImage.open(BytesIO(raw)) as im:
        w, h = im.size
    print(f"\n=== {name} ({w}x{h}, {fmt_bytes(len(raw))} as PNG) ===")
    print(f"{'codec':10} {'quality':>7} {'size':>10} {'ratio':>8} {'ms':>6}")

    runs: list[tuple[str, callable, dict]] = []
    for q in (55, 75, 82, 92):
        runs.append(("webp", encode_webp, {"quality": q, "max_dim": max_dim}))
        runs.append(("mozjpeg", encode_mozjpeg, {"quality": q, "max_dim": max_dim}))
        if AVIF_AVAILABLE:
            runs.append(("avif", encode_avif, {"quality": q, "max_dim": max_dim}))
        if JXL_AVAILABLE:
            runs.append(("jxl", encode_jxl, {"quality": q, "max_dim": max_dim}))

    for codec, fn, kwargs in runs:
        size, dt = run_codec(codec, fn, raw, **kwargs)
        ratio = size / len(raw)
        print(
            f"{codec:10} {kwargs['quality']:>7} "
            f"{fmt_bytes(size):>10} {ratio:>7.1%} {dt*1000:>6.0f}"
        )


def load_real(path: Path) -> bytes:
    return path.read_bytes()


def main() -> int:
    args = sys.argv[1:]
    if args:
        for path_str in args:
            path = Path(path_str)
            if not path.exists():
                print(f"skip (missing): {path}")
                continue
            try:
                bench_one(path.name, load_real(path))
            except Exception as exc:
                print(f"skip ({exc}): {path}")
        return 0

    print(f"AVIF available: {AVIF_AVAILABLE}    JXL available: {JXL_AVAILABLE}")
    bench_one("synthetic_photo", synthetic_photo())
    bench_one("synthetic_screenshot", synthetic_screenshot())
    bench_one("synthetic_detail", synthetic_detail())
    return 0


if __name__ == "__main__":
    sys.exit(main())
