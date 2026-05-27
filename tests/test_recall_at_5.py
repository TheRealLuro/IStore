"""Unit tests for the recall@K measurement logic.

The actual /search HTTP call isn't exercised — that's covered by
test_c1_c2_etc tests. We test that the measurement correctly counts
hits, computes recall + MRR, and breaks down failures.
"""

import pytest

from backend.eval.recall_at_5 import EvalCase, measure_recall


@pytest.mark.asyncio
async def test_recall_all_found():
    eval_set = [
        EvalCase(image_id="img-A", queries=["beach", "sunset"]),
        EvalCase(image_id="img-B", queries=["cat"]),
    ]

    async def fake_search(q: str, k: int) -> list[str]:
        return {
            "beach": ["img-A", "img-X", "img-Y"],
            "sunset": ["img-A", "img-Z"],
            "cat": ["img-B"],
        }.get(q, [])

    result = await measure_recall(fake_search, eval_set, k=5)
    assert result.total_pairs == 3
    assert result.found_pairs == 3
    assert result.recall_at_k == 1.0
    # All hits at rank 1 → MRR = 1.0
    assert result.mrr_at_k == pytest.approx(1.0)
    assert result.misses == []


@pytest.mark.asyncio
async def test_recall_partial():
    eval_set = [
        EvalCase(image_id="img-A", queries=["snowy hike"]),
        EvalCase(image_id="img-B", queries=["whiteboard auth flow"]),
    ]

    async def fake_search(q: str, k: int) -> list[str]:
        if q == "snowy hike":
            # img-A at rank 3
            return ["img-Z", "img-Y", "img-A", "img-X", "img-W"]
        if q == "whiteboard auth flow":
            # img-B missing entirely
            return ["img-Z", "img-Y", "img-X"]
        return []

    result = await measure_recall(fake_search, eval_set, k=5)
    assert result.total_pairs == 2
    assert result.found_pairs == 1
    assert result.recall_at_k == 0.5
    # rank=3 → reciprocal 1/3, miss → 0, average over 2 pairs
    assert result.mrr_at_k == pytest.approx(1 / 3 / 2, abs=1e-9)
    assert len(result.misses) == 1
    assert result.misses[0].query == "whiteboard auth flow"
    assert result.misses[0].top_k_ids == ["img-Z", "img-Y", "img-X"]


@pytest.mark.asyncio
async def test_recall_out_of_k():
    """A hit beyond rank K doesn't count for recall@K."""
    eval_set = [EvalCase(image_id="img-A", queries=["q"])]

    async def fake_search(q: str, k: int) -> list[str]:
        return ["img-X"] * 7 + ["img-A"]  # rank 8, K=5

    result = await measure_recall(fake_search, eval_set, k=5)
    assert result.found_pairs == 0
    assert result.recall_at_k == 0.0
    assert result.mrr_at_k == 0.0


@pytest.mark.asyncio
async def test_recall_handles_search_exception():
    """If /search throws, the query counts as a miss — not a crash."""
    eval_set = [EvalCase(image_id="img-A", queries=["q"])]

    async def broken_search(q: str, k: int) -> list[str]:
        raise RuntimeError("backend down")

    result = await measure_recall(broken_search, eval_set, k=5)
    assert result.total_pairs == 1
    assert result.found_pairs == 0
    assert result.misses[0].top_k_ids == []
