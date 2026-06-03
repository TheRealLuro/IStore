#!/usr/bin/env python
"""Quantization accuracy comparison for the translation stack.

Translates a fixed English set to several languages with MADLAD at whatever
precision the env selects (TRANSLATE_MADLAD_4BIT=0 -> 8-bit, =1 -> 4-bit),
round-trips each back to English, and scores fidelity with a self-contained
chrF (character 6-gram F2). Writes JSON to QE_OUT. Run once per precision:

  docker run --rm --gpus all -v .../eval:/eval -v .../data/models:/models \
    -e HF_HOME=/models -e TRANSLATE_MADLAD_4BIT=0 -e QE_OUT=/eval/quant_accuracy/r8bit.json \
    -w /app neuthek-backend:latest python /eval/quant_accuracy/translate_quant_eval.py

Then compare_quant.py diffs the two JSONs.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

OUT = os.environ.get("QE_OUT", "/eval/quant_accuracy/result.json")

# Varied English: technical, list-y, idiomatic, numbers/units, a long sentence.
SRC = [
    "Click Save to upload your files, then open the big preview.",
    "The quarterly report shows a 23% increase in active users since March.",
    "Mix two cups of flour with one teaspoon of salt and a pinch of sugar.",
    "Our team shipped the new search ranking, fixed eleven bugs, and cut latency.",
    "She decided to bite the bullet and rewrite the whole module from scratch.",
    "Photosynthesis converts carbon dioxide and water into glucose using sunlight.",
    "Please confirm your email address before the verification link expires.",
    "The contract must be signed by both parties no later than June 30th, 2026.",
]
# MADLAD-strong languages across scripts (Tongan is Opus-routed -> sub-project C).
TARGETS = ["es", "fr", "de", "zh", "ar", "hi", "ja", "ru"]


def chrf(hyp: str, ref: str, n: int = 6, beta: float = 2.0) -> float:
    """Self-contained chrF: char n-gram F-beta averaged over 1..n."""
    def grams(s, k):
        s = "".join(s.split())
        return Counter(s[i:i + k] for i in range(len(s) - k + 1)) if len(s) >= k else Counter()
    if not hyp or not ref:
        return 0.0
    f_sum, cnt = 0.0, 0
    for k in range(1, n + 1):
        h, r = grams(hyp, k), grams(ref, k)
        if not h or not r:
            continue
        inter = sum((h & r).values())
        p = inter / max(1, sum(h.values()))
        rec = inter / max(1, sum(r.values()))
        if p + rec == 0:
            f = 0.0
        else:
            f = (1 + beta**2) * p * rec / (beta**2 * p + rec)
        f_sum += f
        cnt += 1
    return 100.0 * f_sum / cnt if cnt else 0.0


def main() -> None:
    from backend.api.translate_engine import translate_text, _load_4bit
    prec = "4bit" if _load_4bit() else "8bit"
    print(f"MADLAD precision: {prec}")
    rows = []
    t0 = time.monotonic()
    for tgt in TARGETS:
        scores = []
        for en in SRC:
            try:
                fwd = translate_text(en, tgt)
                back = translate_text(fwd, "en")
                c = chrf(back, en)
            except Exception as e:  # noqa: BLE001
                print(f"  [{tgt}] FAILED: {type(e).__name__}: {e}")
                fwd, back, c = "", "", 0.0
            scores.append(c)
            rows.append({"tgt": tgt, "src": en, "fwd": fwd, "back": back,
                         "roundtrip_chrf": round(c, 1)})
        avg = sum(scores) / len(scores)
        print(f"  {tgt}: round-trip chrF avg {avg:5.1f}  (n={len(scores)})")
    overall = sum(r["roundtrip_chrf"] for r in rows) / len(rows)
    dt = time.monotonic() - t0
    print(f"OVERALL round-trip chrF: {overall:.1f}  | {len(rows)} pairs in {dt:.0f}s")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"precision": prec, "overall_chrf": round(overall, 2),
                   "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
