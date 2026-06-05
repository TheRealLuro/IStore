"""Stage 2 of the fast render loop: load the OCR/translate cache and ONLY re-compose
(erase + render) — runs in seconds, so layout/render tweaks iterate fast.

  CACHE=/tmp/hw_cache.pkl OUT=/tmp/hw_r.png python hw_compose.py
"""
import copy
import importlib
import os
import pickle
import sys

sys.path.insert(0, "/app")
import backend.api.translate_image as ti  # noqa: E402

importlib.reload(ti)  # pick up the latest render code on each run

CACHE = os.environ.get("CACHE", "/tmp/hw_cache.pkl")
OUT = os.environ.get("OUT", "/tmp/hw_r.png")

with open(CACHE, "rb") as f:
    d = pickle.load(f)

for tgt, trans in d["trans"].items():
    png = ti._compose_png(d["best_img"], d["best_k"], copy.deepcopy(d["regions"]), trans)
    dest = OUT.replace(".png", f"_{tgt}.png")
    open(dest, "wb").write(png)
    print(f"[done] {dest} ({len(png)} bytes)")
