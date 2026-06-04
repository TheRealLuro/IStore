"""In-image text translation test: run the full figure/in-image pipeline
(OCR -> translate -> inpaint -> render-in-place) on real images and save the
translated PNGs next to the originals so the replacement can be eyeballed for
naturalness. Mirrors the embedded-figure path used by translate-pdf.

  IMGS=/app/.../a.png,/app/.../b.png TGTS=es,zh,ar python /app/eval/translate_image_e2e.py
"""
import os
import sys
import time

sys.path.insert(0, "/app")
from backend.api.translate_pdf import _translate_embedded_image_sync  # noqa: E402

IMGS = [s.strip() for s in os.environ.get("IMGS", "").split(",") if s.strip()]
TGTS = [s.strip() for s in os.environ.get("TGTS", "es").split(",") if s.strip()]
OUT = "/app/eval/locate_anything/out_core"

for ip in IMGS:
    name = os.path.splitext(os.path.basename(ip))[0]
    try:
        b = open(ip, "rb").read()
    except Exception as e:  # noqa: BLE001
        print(f"{name}: cannot read ({e})")
        continue
    for t in TGTS:
        t0 = time.time()
        try:
            png = _translate_embedded_image_sync(b, t)
        except Exception as e:  # noqa: BLE001
            print(f"{name} [{t}]: ERROR {type(e).__name__}: {e}")
            continue
        dt = round(time.time() - t0, 1)
        if png:
            dest = f"{OUT}/imgtr_{name}_{t}.png"
            open(dest, "wb").write(png)
            print(f"{name} [{t}]: translated in {dt}s -> {os.path.basename(dest)} ({len(png)} bytes)")
        else:
            print(f"{name} [{t}]: no text detected / nothing to replace ({dt}s)")
