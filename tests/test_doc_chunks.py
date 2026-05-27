"""Unit tests for the per-chunk document split helper (Sprint I D2)."""

from backend.summarize import split_doc_for_embedding


def test_short_text_one_chunk():
    text = "Short paragraph.\n\nAnother one."
    chunks = split_doc_for_embedding(text, budget_chars=2000)
    assert chunks == [text]


def test_splits_on_paragraph_boundaries():
    # Two paragraphs, each ~700 chars, with budget=1000 — should
    # split into two chunks at the paragraph boundary, not mid-sentence.
    p1 = "alpha " * 100  # ~600 chars
    p2 = "bravo " * 100
    text = p1 + "\n\n" + p2
    chunks = split_doc_for_embedding(text, budget_chars=1000)
    assert len(chunks) == 2
    assert "alpha" in chunks[0] and "bravo" not in chunks[0]
    assert "bravo" in chunks[1] and "alpha" not in chunks[1]


def test_hard_splits_oversized_paragraph():
    """A single paragraph longer than the budget is hard-split into
    fixed-size windows rather than dropped or kept whole."""
    text = "x" * 5000
    chunks = split_doc_for_embedding(text, budget_chars=2000)
    assert len(chunks) >= 3
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == text


def test_packs_multiple_small_paragraphs():
    """Many small paragraphs that fit in one budget get packed into
    one chunk rather than emitted individually."""
    paras = "\n\n".join([f"p{i}" * 5 for i in range(20)])
    chunks = split_doc_for_embedding(paras, budget_chars=2000)
    assert len(chunks) == 1


def test_empty_input():
    assert split_doc_for_embedding("", budget_chars=2000) == [""]


def test_chunk_index_stable_across_calls():
    """Re-splitting the same text gives the same chunks in the same
    order — important because upsert uses (image_id, chunk_index)
    as the conflict key. Stable index → re-summarize overwrites
    the same row instead of duplicating."""
    text = "alpha\n\nbravo\n\ncharlie\n\n" + ("delta " * 200)
    a = split_doc_for_embedding(text, budget_chars=500)
    b = split_doc_for_embedding(text, budget_chars=500)
    assert a == b
    assert len(a) > 1
