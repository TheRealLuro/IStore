#!/usr/bin/env python
"""Build side-by-side Florence-vs-LocateAnything comparison montages.

For each base image, pairs <stem>.florence.png (blue boxes) with
<stem>.locate.png (red boxes), scales them to a common height, stacks them
horizontally with a labelled title bar (region counts from the two JSONs), and
writes <stem>.compare.png into OUT_DIR. Run in any container with PIL:
    docker run --rm -v .../locate_anything:/eval -w /app neuthek-backend:latest \
        python /eval/make_montage.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(os.environ.get("LA_OUT", "/eval/out_core"))
BAR_H = 46
GAP = 16
TARGET_H = 760


def _counts() -> tuple[dict, dict]:
    fl, la = {}, {}
    fp = OUT_DIR / "florence_results.json"
    lp = OUT_DIR / "locate_results.json"
    if fp.exists():
        for r in json.loads(fp.read_text()):
            if "regions" in r:
                fl[Path(r["image"]).stem] = r["regions"]
    if lp.exists():
        for r in json.loads(lp.read_text()).get("results", []):
            la[Path(r["image"]).stem] = r.get("boxes")
    return fl, la


def _scaled(p: Path, h: int) -> Image.Image:
    im = Image.open(p).convert("RGB")
    w = max(1, int(im.width * h / im.height))
    return im.resize((w, h))


def main() -> None:
    fl, la = _counts()
    stems = sorted({p.stem.replace(".florence", "").replace(".locate", "")
                    for p in OUT_DIR.glob("*.png")
                    if ".florence" in p.name or ".locate" in p.name})
    made = 0
    for stem in stems:
        fpath = OUT_DIR / f"{stem}.florence.png"
        lpath = OUT_DIR / f"{stem}.locate.png"
        if not (fpath.exists() and lpath.exists()):
            continue
        fimg, limg = _scaled(fpath, TARGET_H), _scaled(lpath, TARGET_H)
        W = fimg.width + GAP + limg.width
        canvas = Image.new("RGB", (W, TARGET_H + BAR_H), (20, 20, 24))
        canvas.paste(fimg, (0, BAR_H))
        canvas.paste(limg, (fimg.width + GAP, BAR_H))
        d = ImageDraw.Draw(canvas)
        fc = fl.get(stem, "?")
        lc = la.get(stem, "?")
        d.text((8, 14), f"Florence (single-orient): {fc} regions", fill=(120, 180, 255))
        d.text((fimg.width + GAP + 8, 14),
               f"LocateAnything-3B: {lc} boxes", fill=(255, 120, 120))
        out = OUT_DIR / f"{stem}.compare.png"
        canvas.save(out)
        made += 1
        print(f"  {out.name}  (Florence {fc} | Locate {lc})")
    print(f"wrote {made} montage(s) to {OUT_DIR}")


if __name__ == "__main__":
    main()
