"""Unit tests for the in-image translation render helpers (region merging,
skip heuristic, whitespace budget, contrast). Pure functions — no GPU/model."""
import backend.api.translate_image as ti


def _r(x0, y0, x1, y1, text):
    return {"box": (x0, y0, x1, y1), "text": text}


# ---- _should_skip_region ----
def test_skip_brand_and_code_tokens():
    for t in ["OpenCLIP", "PyTorch", "MinIO", "h264", "S3", "FAQ", "API"]:
        assert ti._should_skip_region(t) is True, t


def test_keep_normal_words_and_phrases():
    for t in ["Storage", "Hello world", "Features", "neuthek", "the"]:
        assert ti._should_skip_region(t) is False, t


# ---- _merge_regions ----
def test_merge_same_line_adjacent():
    out = ti._merge_regions([_r(10, 10, 50, 30, "Hello"), _r(55, 10, 90, 30, "world")])
    assert len(out) == 1
    assert out[0]["text"] == "Hello world"
    assert len(out[0]["parts"]) == 2


def test_no_merge_across_column_gap():
    out = ti._merge_regions([_r(10, 10, 50, 30, "Left"), _r(400, 10, 440, 30, "Right")])
    assert len(out) == 2  # big horizontal gap = separate columns


def test_block_merge_wrapped_sentence():
    # "Storage that thinks for" / "you." — same left, small gap, similar height.
    out = ti._merge_regions([
        _r(10, 10, 200, 30, "Storage that thinks for"),
        _r(10, 35, 80, 55, "you."),
    ])
    assert len(out) == 1
    assert out[0]["text"] == "Storage that thinks for you."


def test_no_block_merge_across_list_items():
    # Consecutive numbered list items must NOT glue together.
    out = ti._merge_regions([
        _r(10, 10, 120, 30, "9. past"),
        _r(10, 35, 160, 55, "10. favorite movies"),
        _r(10, 60, 150, 80, "11. karaoke"),
    ])
    assert len(out) == 3


def test_no_block_merge_across_sentence_end():
    # Prev line ends a sentence → next line is a new thought, don't merge.
    out = ti._merge_regions([
        _r(10, 10, 200, 30, "This is done."),
        _r(10, 35, 200, 55, "Another separate line here"),
    ])
    assert len(out) == 2


def test_no_block_merge_heading_into_body():
    # Big heading then small body — different sizes must NOT merge.
    out = ti._merge_regions([
        _r(10, 10, 200, 60, "BIG HEADING"),
        _r(10, 66, 200, 86, "small body text here"),
    ])
    assert len(out) == 2


# ---- _dedup_regions ----
def test_dedup_drops_contained_subspan():
    # A sub-span box ("you") sitting inside the phrase box must be dropped,
    # keeping the longer text — the cause of the faint stray over the hero.
    out = ti._dedup_regions([
        _r(10, 10, 300, 40, "Storage that thinks for you"),
        _r(250, 14, 300, 36, "you"),
    ])
    assert len(out) == 1
    assert out[0]["text"] == "Storage that thinks for you"


def test_dedup_keeps_distinct_boxes():
    out = ti._dedup_regions([
        _r(10, 10, 100, 30, "alpha"),
        _r(10, 40, 100, 60, "beta"),
    ])
    assert len(out) == 2


# ---- _avail_height ----
def test_avail_height_uses_gap_below():
    box = (10, 10, 200, 40)            # h=30
    others = [box, (10, 80, 200, 110)]  # a box starting at y=80
    assert ti._avail_height(box, others) == 68  # 80 - 10 - 2


def test_avail_height_caps_when_nothing_below():
    box = (10, 10, 200, 40)            # h=30
    assert ti._avail_height(box, [box]) == 75  # 2.5 * 30


# ---- _ensure_contrast ----
def test_contrast_darkens_faint_on_light_bg():
    assert ti._ensure_contrast((180, 180, 180), bg_dark=False) == (24, 24, 24)


def test_contrast_keeps_dark_ink_on_light_bg():
    assert ti._ensure_contrast((20, 20, 20), bg_dark=False) == (20, 20, 20)


def test_contrast_lightens_dark_ink_on_dark_bg():
    assert ti._ensure_contrast((30, 30, 30), bg_dark=True) == (235, 235, 235)
