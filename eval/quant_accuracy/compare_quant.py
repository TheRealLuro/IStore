#!/usr/bin/env python
"""Diff two translate_quant_eval.py result JSONs (8-bit vs 4-bit) and report
per-language + overall round-trip chrF, the 4-bit delta, and a few sample
forward translations side by side for human judgment.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

D = os.environ.get("QE_DIR", "/eval/quant_accuracy")


def load(name):
    with open(os.path.join(D, name), encoding="utf-8") as f:
        return json.load(f)


def by_tgt(rows):
    agg = defaultdict(list)
    for r in rows:
        agg[r["tgt"]].append(r["roundtrip_chrf"])
    return {t: sum(v) / len(v) for t, v in agg.items()}


def main():
    a = load("r8bit.json")   # 8-bit
    b = load("r4bit.json")   # 4-bit
    ta, tb = by_tgt(a["rows"]), by_tgt(b["rows"])
    print(f"{'lang':6s} {'8bit':>7s} {'4bit':>7s} {'delta':>7s}")
    for t in sorted(ta):
        d = tb.get(t, 0) - ta[t]
        flag = "  <-- drop" if d < -3 else ""
        print(f"{t:6s} {ta[t]:7.1f} {tb.get(t,0):7.1f} {d:+7.1f}{flag}")
    do = b["overall_chrf"] - a["overall_chrf"]
    print(f"\nOVERALL  8bit {a['overall_chrf']:.1f}  4bit {b['overall_chrf']:.1f}"
          f"  delta {do:+.1f}")
    print(f"verdict: {'4-bit OK (<=3 chrF drop overall)' if do >= -3 else '4-bit DEGRADES — keep 8-bit'}")

    # sample forward translations for two languages
    print("\n--- sample forward translations (8bit | 4bit) ---")
    idx = {(r['tgt'], r['src']): r['fwd'] for r in b['rows']}
    shown = 0
    for r in a['rows']:
        if r['tgt'] in ("es", "zh", "ar") and shown < 9:
            other = idx.get((r['tgt'], r['src']), "")
            print(f"[{r['tgt']}] {r['src'][:48]}")
            print(f"   8bit: {r['fwd']}")
            print(f"   4bit: {other}")
            shown += 1


if __name__ == "__main__":
    main()
