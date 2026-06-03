#!/usr/bin/env python
"""Extract embedded raster figures from dropped PDFs for the figure-OCR eval.

Run INSIDE neuthek-backend (PyMuPDF/fitz is in the [ml] image):
    docker exec neuthek-backend python /eval/extract_figures.py

Reads every PDF in PDF_DIR (/eval/in/pdfs), writes each embedded raster image
big enough to plausibly hold text into IN_DIR (/eval/in) as
<pdf>_p<page>_x<xref>.png. If a PDF has NO qualifying embedded raster (its
figures are vector / real PDF text), it falls back to rasterizing each page at
200 DPI so the detector still has something faithful to read.

Mirrors the figure translator's intent (`_collect_embedded_image_targets`) but
extracts ALL qualifying figures (no 12-cap) since this is an eval, not the
bounded production path.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

IN_DIR = Path(os.environ.get("LA_IN", "/eval/in"))
PDF_DIR = Path(os.environ.get("LA_PDF", "/eval/in/pdfs"))
MIN_PX = int(os.environ.get("LA_MIN_PX", "64"))
PAGE_DPI = int(os.environ.get("LA_PAGE_DPI", "200"))


def _save(img_bytes: bytes, dest: Path) -> bool:
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return False
    if min(im.size) < MIN_PX:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    print(f"  wrote {dest.name} ({im.size[0]}x{im.size[1]})")
    return True


def _convert_heic() -> None:
    """iPhone HEIC/HEIF -> PNG so the detectors (which glob PNG/JPG/WebP) see it.
    pillow-heif is in the [ml] image."""
    heics = [p for p in IN_DIR.iterdir()
             if p.suffix.lower() in {".heic", ".heif"}]
    if not heics:
        return
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception as e:  # noqa: BLE001
        print(f"  pillow-heif unavailable ({e}); skipping HEIC")
        return
    for p in heics:
        dest = p.with_suffix(".png")
        if dest.exists():
            continue
        try:
            Image.open(p).convert("RGB").save(dest)
            print(f"  HEIC -> {dest.name}")
        except Exception as e:  # noqa: BLE001
            print(f"  HEIC convert failed for {p.name}: {e}")


def main() -> None:
    IN_DIR.mkdir(parents=True, exist_ok=True)
    print("HEIC conversion:")
    _convert_heic()
    if not PDF_DIR.exists():
        print(f"No PDF dir at {PDF_DIR} — nothing to extract.")
        return
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs in {PDF_DIR}.")
        return

    for pdf in pdfs:
        print(f"{pdf.name}:")
        doc = fitz.open(pdf)
        saved = 0
        for pidx in range(doc.page_count):
            page = doc.load_page(pidx)
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base = doc.extract_image(xref)
                except Exception:
                    continue
                dest = IN_DIR / f"{pdf.stem}_p{pidx+1}_x{xref}.png"
                if dest.exists():
                    continue
                if _save(base.get("image", b""), dest):
                    saved += 1
        if saved == 0:
            print(f"  no embedded rasters >= {MIN_PX}px — rasterizing pages @ {PAGE_DPI}dpi")
            for pidx in range(doc.page_count):
                page = doc.load_page(pidx)
                pix = page.get_pixmap(dpi=PAGE_DPI)
                dest = IN_DIR / f"{pdf.stem}_page{pidx+1}.png"
                pix.save(dest)
                print(f"  wrote {dest.name} ({pix.width}x{pix.height})")
        doc.close()
    print("done.")


if __name__ == "__main__":
    main()
