#!/usr/bin/env python
"""Definitive VRAM probe: load EVERY GPU model the app uses, one at a time, and
report device-level memory after each (torch.cuda.mem_get_info captures torch +
onnxruntime + any other CUDA allocator on the device, so the totals are honest).

Answers: "does the whole model fleet fit resident on the 12 GB 5070?"

Run in a throwaway backend container with the GPU + HF cache mounted:
    docker run --rm --gpus all -v .../locate_anything:/eval \
      -v .../data/models:/models -e HF_HOME=/models -w /app \
      neuthek-backend:latest python /eval/vram_probe.py
"""
from __future__ import annotations

import sys
import traceback

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

import torch

GB = 1024 ** 3


def dev_used_free():
    free, total = torch.cuda.mem_get_info()
    return (total - free) / GB, free / GB, total / GB


def step(label, fn):
    base_used, _, total = dev_used_free()
    try:
        fn()
        torch.cuda.synchronize()
    except Exception as e:  # noqa: BLE001
        print(f"  [SKIP] {label}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return
    used, free, _ = dev_used_free()
    print(f"  {label:32s} +{used-base_used:5.2f} GB | device used {used:6.2f}/"
          f"{total:.2f} GB | free {free:5.2f} GB")


def main():
    used, free, total = dev_used_free()
    print(f"device: {torch.cuda.get_device_name(0)} | total {total:.2f} GB | "
          f"baseline used {used:.2f} GB\n")

    # --- vision/runtime.py getters ---
    from backend.vision import runtime as rt
    step("CLIP (OpenCLIP ViT-L-14)", lambda: rt.get_clip())
    step("doc summarizer (distilbart)", lambda: rt.get_doc_summarizer())
    step("Florence-2-large", lambda: rt.get_florence2())
    step("caption model", lambda: rt.get_caption_model())
    step("summary rewriter (Qwen2.5-1.5B)", lambda: rt.get_summary_rewriter())

    # --- translate engine: MADLAD-400-3B (8-bit) + Opus en-mul/mul-en ---
    from backend.api import translate_engine as te
    step("translator (MADLAD-8bit+Opus)", lambda: te.get_translator())

    # --- NLLB-200-distilled-600M ---
    from backend.api import ocr
    step("NLLB-200-600M", lambda: ocr._get_nllb())

    # --- TrOCR forced onto GPU (the A upgrade) ---
    def _trocr_gpu():
        from transformers import (VisionEncoderDecoderModel,
                                   TrOCRProcessor)
        name = "microsoft/trocr-base-handwritten"
        m = VisionEncoderDecoderModel.from_pretrained(name).to("cuda").eval()
        _p = TrOCRProcessor.from_pretrained(name)
        globals()["_trocr_keep"] = (m, _p)
    step("TrOCR-base-handwritten (GPU)", _trocr_gpu)

    # --- faces: insightface buffalo_l (RetinaFace det + ArcFace r50) ---
    def _faces():
        # Lazy-loads insightface buffalo_l on first detect; feed it a real img.
        from pathlib import Path
        from backend.vision.faces import detect_and_embed
        imgs = sorted(Path("/eval/in").glob("*.png"))
        if not imgs:
            raise RuntimeError("no /eval/in image to trigger face load")
        detect_and_embed(imgs[0].read_bytes())
    step("faces (insightface buffalo_l)", _faces)

    used, free, total = dev_used_free()
    print(f"\n=== FLEET RESIDENT: {used:.2f} / {total:.2f} GB used | "
          f"{free:.2f} GB free ===")
    headroom_ok = free > 1.5
    print(f"verdict: {'FITS with >1.5GB headroom' if headroom_ok else 'TIGHT/OVER — <1.5GB free'}")
    print("(note: InternVL2-4B heavy VLM is gated OFF by default and NOT loaded "
          "here; sub-project C will swap Qwen-1.5B -> a 7B/9B, adding ~4-9 GB.)")


if __name__ == "__main__":
    main()
