"""End-to-end Phase 1 + Phase 2 demo.

What's REAL:
- backend.codecs (compression — every byte count below is from running encoders)
- backend.policy.pick_plan (content-aware decision logic)
- The orchestrator data flow (upload -> classify -> plan -> compress -> store -> retrieve)

What's MOCKED (clearly labelled below):
- Vision pipeline: replaced with a heuristic content-type classifier so we can
  exercise the policy branches without the 3 GB torch + open_clip install.
  Real CLIP would produce richer, more accurate signals.
- Storage: in-memory dicts instead of MinIO buckets.
- Database: in-memory list instead of Postgres rows.

What this script does NOT exercise: HTTP, auth, alembic. Phase 1 already covered
those — they need `docker compose up` to run.
"""

from __future__ import annotations

import hashlib
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFilter

from backend.codecs import (
    AVIF_AVAILABLE,
    JXL_AVAILABLE,
    compress,
    pick_default_plan,
)
from backend.policy import VisionContext, pick_plan


# =============================================================================
# Fixture generators — different content types a real user might upload
# =============================================================================

def make_photo_landscape() -> tuple[str, bytes]:
    """Smooth gradient sky + textured ground — proxy for an outdoor landscape."""
    import math
    import random

    w, h = 1920, 1280
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            r = int(120 + 80 * x / w + 30 * math.sin(y / 35))
            g = int(90 + 100 * y / h + 20 * math.cos(x / 50))
            b = int(160 + 50 * (x + y) / (w + h))
            px[x, y] = (
                max(0, min(255, r + random.randint(-15, 15))),
                max(0, min(255, g + random.randint(-15, 15))),
                max(0, min(255, b + random.randint(-15, 15))),
            )
    im = im.filter(ImageFilter.GaussianBlur(0.7))
    out = BytesIO()
    im.save(out, format="PNG")
    return "landscape_photo.png", out.getvalue()


def make_photo_portrait() -> tuple[str, bytes]:
    """Skin-tone-centred soft portrait."""
    import random

    w, h = 1080, 1440
    im = Image.new("RGB", (w, h), (40, 50, 65))
    d = ImageDraw.Draw(im)
    cx, cy = w // 2, h // 2 - 100
    for r in range(360, 0, -1):
        t = (360 - r) / 360
        c = (
            int(220 - t * 35),
            int(185 - t * 45),
            int(160 - t * 35),
        )
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    px = im.load()
    for _ in range(80_000):
        x, y = random.randint(0, w - 1), random.randint(0, h - 1)
        r, g, b = px[x, y]
        px[x, y] = (
            max(0, min(255, r + random.randint(-10, 10))),
            max(0, min(255, g + random.randint(-10, 10))),
            max(0, min(255, b + random.randint(-10, 10))),
        )
    out = BytesIO()
    im.save(out, format="PNG")
    return "portrait_photo.png", out.getvalue()


def make_screenshot() -> tuple[str, bytes]:
    """Application UI: dark header, light sidebar, white body, mocked text rows."""
    w, h = 1920, 1080
    im = Image.new("RGB", (w, h), (245, 246, 250))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, 60], fill=(30, 41, 59))
    d.rectangle([0, 60, 280, h], fill=(241, 245, 249))
    d.rectangle([280, 60, w, h], fill=(255, 255, 255))
    for i in range(22):
        y = 100 + i * 38
        d.rectangle([320, y, 320 + 600, y + 14], fill=(51, 65, 85))
        d.rectangle([320, y + 18, 320 + 800, y + 24], fill=(148, 163, 184))
    out = BytesIO()
    im.save(out, format="PNG")
    return "app_screenshot.png", out.getvalue()


def make_document() -> tuple[str, bytes]:
    """Scanned A4-ish page — black text bars on near-white."""
    w, h = 1700, 2200
    im = Image.new("RGB", (w, h), (252, 252, 252))
    d = ImageDraw.Draw(im)
    for paragraph in range(5):
        y0 = 180 + paragraph * 380
        for line in range(8):
            y = y0 + line * 32
            line_w = 1300 - (40 if line == 7 else 0)
            d.rectangle([200, y, 200 + line_w, y + 18], fill=(60, 60, 60))
    out = BytesIO()
    im.save(out, format="PNG")
    return "document_scan.png", out.getvalue()


def make_icon() -> tuple[str, bytes]:
    """Logo/icon: white background, three flat colour shapes."""
    w, h = 512, 512
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse([60, 60, 452, 452], fill=(59, 130, 246))
    d.ellipse([180, 180, 332, 332], fill=(255, 255, 255))
    d.rectangle([200, 200, 312, 312], fill=(239, 68, 68))
    out = BytesIO()
    im.save(out, format="PNG")
    return "logo_icon.png", out.getvalue()


def make_illustration() -> tuple[str, bytes]:
    """Flat-colour illustration with bold shapes."""
    w, h = 1200, 1200
    im = Image.new("RGB", (w, h), (15, 23, 42))
    d = ImageDraw.Draw(im)
    d.polygon([(600, 200), (900, 700), (300, 700)], fill=(251, 191, 36))
    d.polygon([(400, 800), (800, 800), (600, 1100)], fill=(34, 197, 94))
    d.ellipse([500, 100, 700, 300], fill=(244, 114, 182))
    out = BytesIO()
    im.save(out, format="PNG")
    return "illustration.png", out.getvalue()


# =============================================================================
# Vision stub — hardcoded labels per fixture.
#
# Why hardcoded: CLIP would classify these correctly on real images, but
# stand-in heuristics get fooled by smooth synthetic gradients (only ~50 unique
# colours after quantization, similar to flat-region UI content). The point of
# this demo is to measure the orchestrator + policy + codecs end-to-end given
# correct classifications, not to grade a stand-in classifier.
#
# Run with `[ml]` extras installed and a real upload through the API to
# exercise the genuine CLIP path.
# =============================================================================

EXPECTED_LABELS: dict[str, tuple[str, float]] = {
    "landscape_photo.png":  ("photo",        0.94),
    "portrait_photo.png":   ("photo",        0.96),
    "app_screenshot.png":   ("screenshot",   0.91),
    "document_scan.png":    ("document",     0.93),
    "logo_icon.png":        ("icon",         0.88),
    "illustration.png":     ("illustration", 0.85),
}


def stub_classify(filename: str) -> VisionContext:
    label, conf = EXPECTED_LABELS[filename]
    return VisionContext(content_type=label, content_confidence=conf)


# =============================================================================
# Demo runner — simulates the upload orchestrator end-to-end
# =============================================================================

def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def main() -> None:
    print("=" * 100)
    print("NEUTHEK  --  PHASE 1 + PHASE 2 END-TO-END DEMO")
    print("=" * 100)
    print(f"AVIF available: {AVIF_AVAILABLE}    JXL available: {JXL_AVAILABLE}")
    print("Vision: hardcoded labels per fixture (CLIP would produce these signals "
          "on real images;\n         install [ml] extras + run uvicorn to exercise "
          "the live CLIP path).\n")

    fixtures = [
        make_photo_landscape(),
        make_photo_portrait(),
        make_screenshot(),
        make_document(),
        make_icon(),
        make_illustration(),
    ]

    # Simulated infrastructure.
    storage_originals: dict[str, bytes] = {}      # MinIO bucket "originals"
    storage_served: dict[str, bytes] = {}         # MinIO bucket "served"
    db: list[dict] = []                           # Postgres "images" table
    tag_index: dict[str, list[str]] = {}          # ImageTag many-to-many

    # ---------- Per-image upload pipeline ----------
    for filename, raw in fixtures:
        sha = hashlib.sha256(raw).hexdigest()
        with Image.open(BytesIO(raw)) as im:
            w, h = im.size

        print(f"## POST /images/  ({filename}, {fmt_bytes(len(raw))}, {w}x{h})")

        # 1. Vision pass.
        vctx = stub_classify(filename)
        print(f"|  -> vision (stubbed):    content_type={vctx.content_type:13s}  "
              f"confidence={vctx.content_confidence:.2f}")

        # 2. Compare both policies side-by-side.
        p1_plan = pick_default_plan("png", w, h, len(raw))
        p1_served = compress(raw, p1_plan)

        p2_plan = pick_plan(vctx, w, h, len(raw))
        p2_served = compress(raw, p2_plan)

        # 3. "Persist" — Phase 2 plan wins.
        original_key = f"users/demo/originals/{sha[:12]}/{filename}"
        served_key = f"users/demo/served/{sha[:12]}.{p2_plan.extension}"
        storage_originals[original_key] = raw
        storage_served[served_key] = p2_served

        synthetic_tags = {
            "photo": ["outdoors", "landscape", "sky"],
            "screenshot": ["computer", "interface"],
            "document": ["text", "page"],
            "icon": ["logo", "graphic"],
            "illustration": ["art", "drawing"],
        }.get(vctx.content_type, [])
        tag_index[sha] = synthetic_tags

        db.append({
            "filename": filename,
            "sha256": sha,
            "original_key": original_key,
            "served_key": served_key,
            "byte_size_original": len(raw),
            "byte_size_served": len(p2_served),
            "byte_size_p1": len(p1_served),
            "codec": p2_plan.codec,
            "quality": p2_plan.quality,
            "lossless": p2_plan.lossless,
            "max_dim": p2_plan.max_dim,
            "content_type": vctx.content_type,
            "content_confidence": vctx.content_confidence,
            "tags": synthetic_tags,
            "pending_face_scan": vctx.face_likelihood > 0.5,
        })

        # Reporting.
        print(f"|  -> policy decision:     codec={p2_plan.codec:8s}  "
              f"lossless={str(p2_plan.lossless):5s}  q={p2_plan.quality:>3}  "
              f"max_dim={p2_plan.max_dim}")
        print(f"|  -> encode (Phase 1 plan): {fmt_bytes(len(p1_served)):>10s}  "
              f"({len(p1_served)/len(raw):>6.1%})")
        print(f"|  -> encode (Phase 2 plan): {fmt_bytes(len(p2_served)):>10s}  "
              f"({len(p2_served)/len(raw):>6.1%})  <- STORED")

        delta = len(p1_served) - len(p2_served)
        if abs(delta) > 200:
            sign = "smaller" if delta > 0 else "bigger"
            print(f"|  -> Phase 2 is {fmt_bytes(abs(delta))} {sign} than Phase 1 would have been")

        print(f"|  -> MinIO put:           originals/{filename}, served/{p2_plan.extension}")
        print(f"|  -> DB row:              content_type={vctx.content_type}, "
              f"tags={synthetic_tags}, pending_face_scan={vctx.face_likelihood > 0.5}")
        print(f"++ 201 Created          (sha={sha[:12]}...)\n")

    # ---------- Round-trip integrity ----------
    print("=" * 100)
    print("ROUND-TRIP   --   `GET /images/{id}/original`   (the 'decompress' path)")
    print("=" * 100)
    for row in db:
        fetched = storage_originals[row["original_key"]]
        ok = hashlib.sha256(fetched).hexdigest() == row["sha256"]
        marker = "OK" if ok else "FAIL"
        print(f"  {row['filename']:25s}  uploaded {fmt_bytes(row['byte_size_original']):>9s}  ->  "
              f"fetched {fmt_bytes(len(fetched)):>9s}  "
              f"sha={'matches' if ok else 'MISMATCH':9s}  [{marker}]")

    # ---------- Filter / sort surface ----------
    print("\n" + "=" * 100)
    print("FILTER / SORT API SURFACE")
    print("=" * 100)

    print("\n  GET /images/?content_type=photo")
    for row in db:
        if row["content_type"] == "photo":
            print(f"    {row['filename']:25s}  served as {row['codec']} q={row['quality']}  "
                  f"({fmt_bytes(row['byte_size_served'])})")

    print("\n  GET /images/?content_type=screenshot")
    for row in db:
        if row["content_type"] == "screenshot":
            print(f"    {row['filename']:25s}  served as {row['codec']} lossless="
                  f"{row['lossless']}  ({fmt_bytes(row['byte_size_served'])})")

    print("\n  GET /images/?content_type=document")
    for row in db:
        if row["content_type"] == "document":
            print(f"    {row['filename']:25s}  served as {row['codec']} lossless="
                  f"{row['lossless']}  ({fmt_bytes(row['byte_size_served'])})")

    print("\n  GET /images/?tag=outdoors")
    for row in db:
        if "outdoors" in row["tags"]:
            print(f"    {row['filename']:25s}  tags={row['tags']}")

    print("\n  GET /search/?q=...      (semantic -- needs real CLIP; "
          "mocked path returns nothing meaningful)")

    # ---------- Summary table ----------
    print("\n" + "=" * 100)
    print("SUMMARY TABLE")
    print("=" * 100)
    print(f"\n  {'fixture':25s}  {'detected':13s}  {'orig':>10s}  "
          f"{'P1 served':>10s}  {'P2 served':>10s}  {'P1->P2':>9s}  {'codec/mode':18s}")
    print("  " + "-" * 110)

    total_orig = total_p1 = total_p2 = 0
    for row in db:
        orig = row["byte_size_original"]
        p1 = row["byte_size_p1"]
        p2 = row["byte_size_served"]
        total_orig += orig
        total_p1 += p1
        total_p2 += p2
        delta = (p1 - p2) / max(p1, 1)
        mode = f"{row['codec']} lossless" if row["lossless"] else f"{row['codec']} q={row['quality']}"
        print(f"  {row['filename']:25s}  {row['content_type']:13s}  "
              f"{fmt_bytes(orig):>10s}  {fmt_bytes(p1):>10s}  {fmt_bytes(p2):>10s}  "
              f"{delta:>+8.1%}  {mode:18s}")

    print("  " + "-" * 110)
    print(f"  {'TOTAL':25s}  {'':13s}  {fmt_bytes(total_orig):>10s}  "
          f"{fmt_bytes(total_p1):>10s}  {fmt_bytes(total_p2):>10s}")
    print(f"\n  Phase 1 (vision-less default):   "
          f"served = {total_p1/total_orig:>6.1%} of original  "
          f"({(total_orig - total_p1) / total_orig:>5.1%} saved)")
    print(f"  Phase 2 (content-aware policy):  "
          f"served = {total_p2/total_orig:>6.1%} of original  "
          f"({(total_orig - total_p2) / total_orig:>5.1%} saved)")
    if total_p1:
        print(f"  Phase 2 vs Phase 1 across the whole batch: "
              f"{(total_p1 - total_p2) / total_p1:>+6.1%} change in served bytes")

    print()


if __name__ == "__main__":
    main()
