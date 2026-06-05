"""Stage 1 of a fast render-iteration loop: run OCR + VL translate ONCE and pickle
(best_img, best_k, regions, translations-per-target) so the render (hw_compose.py)
can be re-run in seconds without re-doing the slow OCR/VL.

  IMG=/tmp/IMG_1772.png TGTS=es,zh,ar python hw_cache.py
"""
import os
import pickle
import sys

sys.path.insert(0, "/app")
import backend.api.translate_image as ti  # noqa: E402

IMG = os.environ.get("IMG", "/tmp/IMG_1772.png")
TGTS = [t.strip() for t in os.environ.get("TGTS", "es").split(",") if t.strip()]
CACHE = os.environ.get("CACHE", "/tmp/hw_cache.pkl")

raw = open(IMG, "rb").read()
img, best_k, best_img, regions = ti._ocr_stage(raw, four_way=False)
trans_by_tgt = {}
for TGT in TGTS:
    trans, _src, _tgt = ti._translate_regions_best([r["text"] for r in regions], regions, TGT)
    trans_by_tgt[TGT] = trans

with open(CACHE, "wb") as f:
    pickle.dump({"best_img": best_img, "best_k": best_k, "regions": regions,
                 "trans": trans_by_tgt}, f)
print(f"[cache] {len(regions)} regions, targets={list(trans_by_tgt)} -> {CACHE}")
