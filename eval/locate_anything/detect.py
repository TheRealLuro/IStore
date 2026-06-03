#!/usr/bin/env python
"""LocateAnything-3B text-detection eval harness (research/benchmark ONLY).

Runs nvidia/LocateAnything-3B "Detect all the text in box format." over every
image in IN_DIR, parses the <box> coordinate tokens, draws overlays into
OUT_DIR, and reports per-image box counts + latency + peak VRAM. The peak-VRAM
number answers the 12GB feasibility question (can this be co-resident with the
translate models, or must it load-on-demand?).

Usage (inside the eval container, GPU attached):
    python /eval/detect.py
Env overrides: LA_IN, LA_OUT, LA_MODEL, LA_GEN_MODE, LA_MAX_NEW_TOKENS.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw

IN_DIR = Path(os.environ.get("LA_IN", "/eval/in"))
OUT_DIR = Path(os.environ.get("LA_OUT", "/eval/out"))
MODEL_PATH = os.environ.get("LA_MODEL", "nvidia/LocateAnything-3B")
GEN_MODE = os.environ.get("LA_GEN_MODE", "hybrid")  # fast | slow | hybrid
MAX_NEW_TOKENS = int(os.environ.get("LA_MAX_NEW_TOKENS", "8192"))
PROMPT = os.environ.get("LA_PROMPT", "Detect all the text in box format.")
# Cap the longest side before inference. The MoonViT encoder falls back to SDPA
# (flash_attn/magi_attention absent), which materializes the full O(N^2)
# attention matrix — a native-res phone photo OOMs a 12 GB card (40+ GiB alloc).
# 1280 keeps the 1280x900 screenshots near-native while shrinking large slides
# and photos to something that fits. Itself a 12 GB feasibility data point.
MAX_SIDE = int(os.environ.get("LA_MAX_SIDE", "1280"))

_BOX_RE = re.compile(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>")


def parse_boxes(answer: str, w: int, h: int) -> list[dict]:
    """Normalized [0-1000] tokens -> pixel boxes (model-card parser)."""
    out = []
    for m in _BOX_RE.finditer(answer):
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        out.append({
            "x1": x1 / 1000 * w, "y1": y1 / 1000 * h,
            "x2": x2 / 1000 * w, "y2": y2 / 1000 * h,
        })
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    imgs = sorted(
        p for p in IN_DIR.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    )
    print(f"torch {torch.__version__} | cuda={torch.cuda.is_available()} "
          f"| device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}")
    import transformers
    print(f"transformers {transformers.__version__}")
    if not imgs:
        print(f"NO INPUT IMAGES in {IN_DIR} — drop test images there and re-run.")
        return
    print(f"{len(imgs)} input image(s): {[p.name for p in imgs]}")

    from transformers import AutoModel, AutoTokenizer, AutoProcessor

    t0 = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    processor = AutoProcessor.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        MODEL_PATH, torch_dtype=torch.bfloat16, trust_remote_code=True,
    ).to("cuda").eval()
    load_s = time.monotonic() - t0
    weights_gb = torch.cuda.memory_allocated() / 1e9
    print(f"model loaded in {load_s:.1f}s | weights resident ~{weights_gb:.2f} GB")

    results = []
    for p in imgs:
        image = Image.open(p).convert("RGB")
        ow, oh = image.size
        if max(ow, oh) > MAX_SIDE:
            s = MAX_SIDE / max(ow, oh)
            image = image.resize((max(1, round(ow * s)), max(1, round(oh * s))))
        w, h = image.size
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": PROMPT},
        ]}]
        text = processor.py_apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        images, videos = processor.process_vision_info(messages)
        inputs = processor(
            text=[text], images=images, videos=videos, return_tensors="pt"
        ).to("cuda")

        torch.cuda.reset_peak_memory_stats()
        t1 = time.monotonic()
        with torch.no_grad():
            response = model.generate(
                pixel_values=inputs["pixel_values"].to(torch.bfloat16),
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
                image_grid_hws=inputs.get("image_grid_hws", None),
                tokenizer=tokenizer,
                max_new_tokens=MAX_NEW_TOKENS,
                generation_mode=GEN_MODE,
                do_sample=False,
                use_cache=True,  # model asserts this (KV cache required)
            )
        infer_s = time.monotonic() - t1
        peak_gb = torch.cuda.max_memory_allocated() / 1e9
        ans = response if isinstance(response, str) else str(response)
        boxes = parse_boxes(ans, w, h)

        overlay = image.copy()
        d = ImageDraw.Draw(overlay)
        for b in boxes:
            # The model occasionally emits degenerate/inverted boxes (y1<y0 or
            # x1<x0); normalize so PIL's rectangle doesn't raise.
            x0, x1 = sorted((b["x1"], b["x2"]))
            y0, y1 = sorted((b["y1"], b["y2"]))
            d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=3)
        out_png = OUT_DIR / f"{p.stem}.locate.png"
        overlay.save(out_png)

        bps = len(boxes) / infer_s if infer_s else 0.0
        print(f"  {p.name}: {ow}x{oh}->{w}x{h} -> {len(boxes)} boxes in "
              f"{infer_s:.2f}s ({bps:.1f} box/s) | peak VRAM {peak_gb:.2f} GB "
              f"-> {out_png.name}")
        results.append({
            "image": p.name, "orig": f"{ow}x{oh}", "used": f"{w}x{h}",
            "boxes": len(boxes), "infer_s": round(infer_s, 3),
            "peak_vram_gb": round(peak_gb, 2), "raw_answer": ans[:2000],
        })
        del inputs, response
        torch.cuda.empty_cache()

    summary = {
        "model": MODEL_PATH, "gen_mode": GEN_MODE, "load_s": round(load_s, 1),
        "weights_gb": round(weights_gb, 2), "results": results,
    }
    (OUT_DIR / "locate_results.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {OUT_DIR/'locate_results.json'}")


if __name__ == "__main__":
    main()
