"""End-to-end translate a document through the REAL production path (extract ->
MADLAD/NLLB per-block translate -> structure-preserving PDF render) and save the
output PDF + page PNGs + an orig/translated pair dump for verification.

Mirrors _translate_doc_stream_gen exactly. Run on a clean GPU:
  DOC=/app/.../statusreport.pdf TGT=es python /app/eval/translate_doc_e2e.py
"""
import json
import os
import sys
import time

sys.path.insert(0, "/app")
import fitz  # noqa: E402

import backend.api.translate_doc as td  # noqa: E402

PDF = os.environ.get("DOC", "/app/eval/locate_anything/in/pdfs/statusreport.pdf")
TGTS = [t.strip() for t in os.environ.get("TGTS", os.environ.get("TGT", "es")).split(",") if t.strip()]
MAXB = int(os.environ.get("MAXB", "0"))  # 0 = whole doc

raw = open(PDF, "rb").read()
blocks = [b for b in td._extract_pdf_blocks(raw) if (b.text or "").strip()]
if MAXB > 0:
    blocks = blocks[:MAXB]
model, tok, dev = td._get_nllb()
sample = " ".join(b.text for b in blocks)[:2000]
src = td._detect_src_flores(sample)
src_name = td._flores_name(src)

for TGT in TGTS:
    tgt = td._to_flores(TGT)
    tgt_name = td._flores_name(tgt)
    try:
        tok.src_lang = src
    except Exception:
        pass
    fb = td._resolve_forced_bos(tok, tgt)
    gk = dict(do_sample=False, num_beams=1, no_repeat_ngram_size=3,
              _nt_target=tgt, _nt_source=src)
    if fb is not None:
        gk["forced_bos_token_id"] = fb

    t0 = time.time()
    out = []
    for b in blocks:
        tr = td._translate_block_text_sync(model, tok, dev, b.text, gk)
        out.append({"type": b.type, "text": tr, "marker": b.marker, "_orig": b.text})
    print(f"[{TGT}] translated {len(out)} blocks in {round(time.time()-t0)}s | {src_name} -> {tgt_name}")

    req = td.RenderPdfRequest(
        blocks=[{"type": o["type"], "text": o["text"], "marker": o["marker"]} for o in out],
        target_lang=tgt_name, source_lang=src_name, filename=os.path.basename(PDF),
    )
    pdf, note = td._render_pdf_sync(req)
    base = f"/app/eval/locate_anything/out_core/translated_{TGT}"
    open(base + ".pdf", "wb").write(pdf)
    d = fitz.open("pdf", pdf)
    for p in range(min(2, d.page_count)):
        d.load_page(p).get_pixmap(dpi=110).save(f"{base}_p{p+1}.png")
    open(base + "_pairs.json", "w", encoding="utf-8").write(
        json.dumps([{"type": o["type"], "orig": o["_orig"], "tr": o["text"]} for o in out],
                   ensure_ascii=False, indent=1)
    )
    print(f"[{TGT}] saved {base}.pdf | pages {d.page_count}")
