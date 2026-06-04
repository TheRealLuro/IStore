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


# ---- _clean_vl_text (handwriting VL output sanitising) ----
def test_clean_vl_strips_label_prefix_and_quotes():
    assert ti._clean_vl_text('The text reads: "going to Rome"') == "going to Rome"
    assert ti._clean_vl_text("Transcription: Nope") == "Nope"
    assert ti._clean_vl_text('“Loud Noises / yelling”') == "Loud Noises / yelling"


def test_clean_vl_keeps_plain_text():
    assert ti._clean_vl_text("Significant others must be included") == \
        "Significant others must be included"
    assert ti._clean_vl_text("") == ""


# ---- _is_handwriting (auto gate + on/off override) ----
def test_handwriting_gate_auto_threshold(monkeypatch):
    monkeypatch.setenv("IMG_VL_RECOG", "auto")
    monkeypatch.delenv("IMG_VL_CONF_THRESHOLD", raising=False)
    assert ti._is_handwriting(-0.81) is True    # handwriting note (measured)
    assert ti._is_handwriting(-0.36) is False   # digital screenshot (measured)
    assert ti._is_handwriting(None) is False


def test_handwriting_gate_on_off_override(monkeypatch):
    monkeypatch.setenv("IMG_VL_RECOG", "off")
    assert ti._is_handwriting(-0.99) is False
    monkeypatch.setenv("IMG_VL_RECOG", "on")
    assert ti._is_handwriting(-0.01) is True


# ---- _color_close / _pill_bounds (digital pill detection) ----
def test_color_close():
    assert ti._color_close((10, 10, 10), (20, 18, 12), tol=24) is True
    assert ti._color_close((10, 10, 10), (200, 10, 10), tol=24) is False


def test_pill_bounds_detects_contained_button():
    import numpy as np
    # White page with a dark rounded "pill" rectangle; the label box sits inside
    # the pill with padding on every side. _pill_bounds should expand the text
    # box out to (roughly) the pill, not return None.
    img = np.full((120, 300, 3), 255, dtype=np.uint8)
    img[40:80, 80:220] = (30, 30, 30)          # the pill fill
    text_box = (110, 52, 190, 68)              # label inside the pill
    out = ti._pill_bounds(img, text_box, page_bg=(255, 255, 255))
    assert out is not None
    px0, py0, px1, py1 = out
    assert px0 <= 85 and px1 >= 215           # widened toward the pill edges
    assert py0 <= 45 and py1 >= 75


def test_pill_bounds_none_for_plain_text_on_page():
    import numpy as np
    img = np.full((120, 300, 3), 255, dtype=np.uint8)   # text on the page bg
    text_box = (110, 52, 190, 68)
    assert ti._pill_bounds(img, text_box, page_bg=(255, 255, 255)) is None


def test_pill_bounds_detects_outlined_button():
    import numpy as np
    # White page with a thin RECTANGLE BORDER (ghost/outline button) and a label
    # inside it. The interior is page-coloured, so only the 4-sided thin stroke
    # marks the container — _pill_bounds must still find it.
    img = np.full((120, 300, 3), 255, dtype=np.uint8)
    # 2px dark border ring of the button at x:80..220, y:40..80
    img[40:42, 80:220] = (40, 40, 40)   # top
    img[78:80, 80:220] = (40, 40, 40)   # bottom
    img[40:80, 80:82] = (40, 40, 40)    # left
    img[40:80, 218:220] = (40, 40, 40)  # right
    text_box = (110, 54, 190, 66)
    out = ti._pill_bounds(img, text_box, page_bg=(255, 255, 255))
    assert out is not None
    px0, py0, px1, py1 = out
    assert px0 <= 100 and px1 >= 200    # expanded toward the border
    assert py0 <= 50 and py1 >= 70


# ---- _build_context_crops (1:1 with boxes, neighbor context) ----
def test_build_context_crops_is_one_to_one_and_marked():
    from PIL import Image
    import numpy as np
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    boxes = [(10, 10, 90, 30), (10, 40, 120, 60), (10, 70, 80, 90)]
    crops = ti._build_context_crops(img, boxes)
    assert len(crops) == len(boxes)                 # strict 1:1 with input boxes
    for c in crops:
        assert c.width > 0 and c.height > 0
        assert (np.asarray(c) < 200).any()          # each carries the target marker


def test_build_context_crops_single_box():
    from PIL import Image
    img = Image.new("RGB", (100, 50), (255, 255, 255))
    crops = ti._build_context_crops(img, [(10, 10, 60, 30)])
    assert len(crops) == 1


# ---- _context_band / _mark_target (neighbor-context VL crops) ----
def test_context_band_spans_prev_and_next_rows():
    boxes = [(10, 10, 90, 30), (10, 40, 120, 60), (10, 70, 80, 90)]
    band, local = ti._context_band(boxes, 1, iw=200, ih=200)
    bx0, by0, bx1, by1 = band
    assert by0 <= 10 and by1 >= 90          # covers prev top .. next bottom
    # target's local box sits inside the band and is offset by the band origin
    lx0, ly0, lx1, ly1 = local
    assert ly0 == 40 - by0 and ly1 == 60 - by0
    assert 0 <= lx0 and lx1 <= (bx1 - bx0)


def test_context_band_first_and_last_have_no_oob():
    boxes = [(10, 10, 90, 30), (10, 40, 120, 60)]
    b0, _ = ti._context_band(boxes, 0, iw=200, ih=200)   # no prev
    b1, _ = ti._context_band(boxes, 1, iw=200, ih=200)   # no next
    assert b0[1] >= 0 and b1[3] <= 200


def test_mark_target_draws_in_left_margin_without_touching_target_ink():
    from PIL import Image
    crop = Image.new("RGB", (120, 90), (255, 255, 255))
    marked = ti._mark_target(crop, (20, 30, 110, 50))   # target line band
    assert marked.size == crop.size
    import numpy as np
    before = np.asarray(crop)
    after = np.asarray(marked)
    # input is not mutated (a fresh image is returned)
    assert np.array_equal(before, np.full_like(before, 255))
    # some marker pixels appear in the LEFT margin beside the target rows
    left_margin = after[30:50, 0:18]
    assert (left_margin < 200).any()


# ---- _page_pen_color (uniform handwriting ink) ----
def test_page_pen_color_keeps_saturated_blue_pen():
    # A blue ballpoint: every line should share this readable blue, not per-region.
    out = ti._page_pen_color([(30, 42, 175), (22, 36, 160), (28, 40, 168)], bg_dark=False)
    assert out[2] > out[0] and out[2] > out[1]      # still clearly blue
    # readable on a light page (dark enough luma)
    assert 0.299 * out[0] + 0.587 * out[1] + 0.114 * out[2] < 128


def test_page_pen_color_snaps_faint_pencil_to_near_black():
    out = ti._page_pen_color([(176, 176, 180), (182, 178, 181)], bg_dark=False)
    assert out == (24, 24, 24)


def test_page_pen_color_default_when_empty():
    assert ti._page_pen_color([], bg_dark=False) == (26, 29, 41)
    assert ti._page_pen_color([], bg_dark=True) == (236, 236, 236)


def test_orig_line_count():
    # One row of word boxes ⇒ single line.
    assert ti._orig_line_count([(10, 10, 50, 30), (55, 11, 90, 31)]) == 1
    # Three stacked rows ⇒ three lines.
    assert ti._orig_line_count(
        [(10, 10, 200, 30), (10, 40, 200, 60), (10, 70, 200, 90)]) == 3
    assert ti._orig_line_count([]) == 1


def test_pill_bounds_none_for_word_between_neighbours():
    import numpy as np
    # A word with other words to its LEFT and RIGHT (like a nav row) but nothing
    # above/below must NOT be mistaken for a button — no 4-sided enclosure.
    img = np.full((120, 400, 3), 255, dtype=np.uint8)
    img[52:68, 40:70] = (20, 20, 20)     # left neighbour word
    img[52:68, 330:360] = (20, 20, 20)   # right neighbour word
    text_box = (180, 52, 240, 68)        # the middle word
    assert ti._pill_bounds(img, text_box, page_bg=(255, 255, 255)) is None
