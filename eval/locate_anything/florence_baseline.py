#!/usr/bin/env python
"""Florence baseline for the figure-OCR eval — runs the EXACT detection the
in-document figure translator uses today (`_ti_ocr_stage`), so the comparison
against LocateAnything-3B is apples-to-apples.

Run INSIDE neuthek-backend (transformers 4.49.0, GPU attached) with /eval mounted:
    docker exec neuthek-backend python /eval/florence_baseline.py

For each image in IN_DIR it runs the resident pipeline's OCR stage, draws the
detected text regions onto the winning-orientation image, and reports region
count + latency. Output overlays land in OUT_DIR as <name>.florence.png.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Make `backend` importable when run as a throwaway `docker run … python
# /eval/florence_baseline.py` (script dir is /eval, but the package lives at
# /app in the backend image).
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from PIL import ImageDraw

IN_DIR = Path(os.environ.get("LA_IN", "/eval/in"))
OUT_DIR = Path(os.environ.get("LA_OUT", "/eval/out"))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # The figure translator imports this as `_ti_ocr_stage` (alias); the real
    # name in translate_image.py is `_ocr_stage`. Same 4-tuple return:
    # (img, best_k, best_img, regions).
    from backend.api.translate_image import _ocr_stage as _ti_ocr_stage

    imgs = sorted(
        p for p in IN_DIR.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    )
    if not imgs:
        print(f"NO INPUT IMAGES in {IN_DIR}")
        return
    print(f"{len(imgs)} input image(s) — Florence baseline (_ti_ocr_stage)")

    results = []
    for p in imgs:
        img_bytes = p.read_bytes()
        t0 = time.monotonic()
        try:
            _img, best_k, best_img, regions = _ti_ocr_stage(img_bytes)
        except Exception as e:  # noqa: BLE001
            print(f"  {p.name}: FAILED ({type(e).__name__}: {e})")
            results.append({"image": p.name, "error": str(e)})
            continue
        dt = time.monotonic() - t0

        overlay = best_img.convert("RGB").copy()
        d = ImageDraw.Draw(overlay)
        for r in regions:
            x0, y0, x1, y1 = r["box"]
            d.rectangle([x0, y0, x1, y1], outline=(0, 128, 255), width=3)
        out_png = OUT_DIR / f"{p.stem}.florence.png"
        overlay.save(out_png)

        print(f"  {p.name}: {len(regions)} regions in {dt:.2f}s "
              f"(orientation k={best_k}) -> {out_png.name}")
        results.append({
            "image": p.name, "regions": len(regions),
            "infer_s": round(dt, 3), "orientation_k": best_k,
        })

    (OUT_DIR / "florence_results.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT_DIR/'florence_results.json'}")


if __name__ == "__main__":
    main()
