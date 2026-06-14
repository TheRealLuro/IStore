"""Handwriting in-image eval: run the REAL pipeline (Florence detect -> VL
neighbor-context read -> NLLB translate -> LaMa erase -> hybrid render) on a
handwritten note and DUMP every region's read + translation so the OCR accuracy
can be checked line-by-line, plus write the final rendered PNG.

  IMG=/tmp/IMG_1772.png TGT=es OUT=/tmp/hw_out.png python handwriting_check.py
"""
import copy
import os
import sys
import time

sys.path.insert(0, "/app")
import backend.api.translate_image as ti  # noqa: E402

IMG = os.environ.get("IMG", "/tmp/IMG_1772.png")
TGTS = [t.strip() for t in os.environ.get("TGTS", os.environ.get("TGT", "es")).split(",") if t.strip()]
OUT = os.environ.get("OUT", "/tmp/hw_out.png")
FOUR_WAY = os.environ.get("FOUR_WAY", "0") == "1"

raw = open(IMG, "rb").read()
t0 = time.time()
img, best_k, best_img, regions = ti._ocr_stage(raw, four_way=FOUR_WAY)
print(f"[ocr] {len(regions)} regions in {time.time()-t0:.1f}s (best_k={best_k}, "
      f"hw={any(r.get('handwriting') for r in regions)})")
texts = [r["text"] for r in regions]

# Translate ALL targets first (Qwen stays resident — no reload per language), then
# compose (LaMa load may evict Qwen, but every translation is already done).
done = []
for TGT in TGTS:
    trans, src, tgt = ti._translate_regions_best(texts, regions, TGT)
    print(f"\n=== [{TGT}] {ti._flores_name(src)} -> {ti._flores_name(tgt)} ===")
    print("idx | box(x0,y0,x1,y1) parts | READ -> TRANSLATION")
    for i, (r, o, t) in enumerate(zip(regions, texts, trans)):
        b = r["box"]
        flag = "HW" if r.get("handwriting") else "  "
        skip = " [SKIP]" if r.get("skip") else ""
        print(f"{i:2d} {flag} {b} p{len(r.get('parts', []))} | {o!r} -> {t!r}{skip}")
    done.append((TGT, trans))

for TGT, trans in done:
    png = ti._compose_png(best_img, best_k, copy.deepcopy(regions), trans)
    dest = OUT.replace(".png", f"_{TGT}.png")
    open(dest, "wb").write(png)
    print(f"[done] wrote {dest} ({len(png)} bytes)")

sys.exit(0)
