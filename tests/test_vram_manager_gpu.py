import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_GPU_TESTS") != "1", reason="GPU opt-in")


def test_fleet_loads_under_budget_without_oom():
    import torch
    from backend.api import translate_engine as te
    from backend.vision import runtime as rt

    rt.get_florence2()
    rt.get_summary_rewriter()   # 4-bit when LLM_REWRITER_4BIT=1
    te.get_translator()

    free, total = torch.cuda.mem_get_info()
    used_gb = (total - free) / (1024 ** 3)
    total_gb = total / (1024 ** 3)
    # Did not pin the card to 0 free (the pre-fabric failure mode).
    assert free / (1024 ** 3) > 0.3, f"only {free/(1024**3):.2f} GB free"
    assert used_gb < total_gb

    # A real translate succeeds (no OOM, models co-resident).
    out = te.translate_text("hello world", "es")
    assert isinstance(out, str) and out.strip()
