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


def test_no_block_merge_across_list_items_without_space():
    # VL often glues the marker to the word ("10.Favorite"); these are still
    # distinct list items and must NOT merge into one blob.
    out = ti._merge_regions([
        _r(10, 10, 200, 30, "10.Favorite Movies"),
        _r(10, 35, 200, 55, "11.Scuba diving"),
        _r(10, 60, 200, 80, "12.plantain chips"),
    ])
    assert len(out) == 3


def test_clean_vl_adds_space_after_list_number():
    assert ti._clean_vl_text("10.Favorite Movies") == "10. Favorite Movies"
    assert ti._clean_vl_text("6.nope") == "6. nope"
    # a decimal must be left alone
    assert ti._clean_vl_text("3.5 mm wide") == "3.5 mm wide"


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


# ---- _wrap_to_width (hard-break CJK / overlong words so nothing runs off-side) ----
def test_wrap_hard_breaks_overlong_word():
    from PIL import Image, ImageDraw, ImageFont
    d = ImageDraw.Draw(Image.new("RGB", (200, 50), "white"))
    f = ImageFont.load_default()
    lines = ti._wrap_to_width(d, "x" * 200, f, 40)     # one unbreakable Latin run
    assert len(lines) > 1
    for ln in lines:
        assert ti._text_w(d, ln, f) <= 40 or len(ln) == 1


def test_wrap_breaks_spaceless_run_to_multiple_lines():
    from PIL import Image, ImageDraw, ImageFont
    d = ImageDraw.Draw(Image.new("RGB", (200, 50), "white"))
    f = ImageFont.load_default()
    # a long space-less run (stands in for CJK/Thai) must wrap, not overflow
    lines = ti._wrap_to_width(d, "abcdefghij" * 10, f, 50)
    assert len(lines) >= 2


def test_wrap_normal_words_unchanged():
    from PIL import Image, ImageDraw, ImageFont
    d = ImageDraw.Draw(Image.new("RGB", (400, 50), "white"))
    f = ImageFont.load_default()
    assert ti._wrap_to_width(d, "hello world", f, 4000) == ["hello world"]


# ---- _cluster_columns / _flow_layout (clean column-aware layout) ----
def test_cluster_columns_two_columns():
    items = [
        {"box": (75, 100, 700, 130)}, {"box": (80, 200, 700, 230)},
        {"box": (1020, 100, 1700, 130)}, {"box": (1030, 200, 1700, 230)},
    ]
    cols = ti._cluster_columns(items, 1880)
    assert len(cols) == 2
    assert len(cols[0]) == 2 and len(cols[1]) == 2


def test_cluster_columns_single_column():
    items = [{"box": (50, 10, 700, 40)}, {"box": (55, 60, 700, 90)}]
    assert len(ti._cluster_columns(items, 1880)) == 1


def test_flow_layout_even_spacing_within_margins():
    regions = [
        {"box": (75, 20, 700, 50), "handwriting": True, "parts": [(75, 20, 700, 50)]},
        {"box": (75, 900, 700, 930), "handwriting": True, "parts": [(75, 900, 700, 930)]},
    ]
    lay = ti._flow_layout(regions, ["uno", "dos"], 1000, 1000)
    b0 = lay[id(regions[0])]
    b1 = lay[id(regions[1])]
    assert b0[0] >= 14 and b0[1] >= 14            # inside the top/left margin
    assert b1[3] <= 1000 - 14                      # inside the bottom margin
    assert b0[3] <= b1[1] + 2                       # ordered, no overlap
    # the top item was pulled DOWN off the very edge into a safe band
    assert b0[1] >= 14


def test_flow_layout_pulls_top_item_into_safe_band():
    # an item hard against the top edge (y0=2) must be moved down to the margin
    regions = [
        {"box": (1020, 2, 1700, 30), "handwriting": True, "parts": [(1020, 2, 1700, 30)]},
        {"box": (1020, 400, 1700, 430), "handwriting": True, "parts": [(1020, 400, 1700, 430)]},
    ]
    lay = ti._flow_layout(regions, ["a", "b"], 1880, 1000)
    assert lay[id(regions[0])][1] >= 14            # top item no longer at the edge


# ---- _slot_height (no-overlap budget for dense handwriting) ----
def test_slot_height_caps_at_next_region_in_column():
    box = (1000, 90, 1800, 255)        # tall right-column box
    nxt = (1150, 197, 1815, 309)       # overlaps just below, same column
    far = (100, 100, 700, 200)         # left column, no horizontal overlap
    assert ti._slot_height(box, [box, nxt, far]) == 197 - 90 - 2


def test_slot_height_last_in_column_uses_box_height():
    box = (1000, 90, 1800, 255)
    assert ti._slot_height(box, [box]) == 255 - 90


def test_slot_height_ignores_other_column():
    box = (1000, 90, 1800, 200)
    other_col = (100, 150, 700, 260)   # left column only — not in this column
    assert ti._slot_height(box, [box, other_col]) == 200 - 90


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


# ---- _split_into_lines (break a multi-line blob into single text lines) ----
def test_split_into_lines_splits_two_rows():
    from PIL import Image
    import numpy as np
    arr = np.full((100, 200, 3), 255, dtype=np.uint8)
    arr[10:30, 10:180] = 20      # line 1 ink
    arr[60:80, 10:180] = 20      # line 2 ink, with a clear gap between
    subs = ti._split_into_lines(Image.fromarray(arr), (0, 0, 200, 100))
    assert len(subs) == 2
    assert subs[0][3] <= subs[1][1] + 6          # first line sits above the second


def test_split_into_lines_single_line_returns_box():
    from PIL import Image
    import numpy as np
    arr = np.full((40, 200, 3), 255, dtype=np.uint8)
    arr[10:30, 10:180] = 20
    box = (0, 0, 200, 40)
    assert ti._split_into_lines(Image.fromarray(arr), box) == [box]


# ---- _split_list_marker (preserve list numbers through VL translation) ----
def test_split_list_marker():
    assert ti._split_list_marker("14. going to Rome") == ("14.", "going to Rome")
    assert ti._split_list_marker("20 Take a bath") == ("20", "Take a bath")
    assert ti._split_list_marker("a.past") == ("a.", "past")
    assert ti._split_list_marker("6. nope") == ("6.", "nope")
    assert ti._split_list_marker("just some words") == ("", "just some words")
    # not a marker: a word that merely starts with a capital + multiple letters
    assert ti._split_list_marker("Shint. 1 pair") == ("", "Shint. 1 pair")


# ---- _translate_regions_best (VL-for-handwriting routing) ----
def test_translate_best_uses_vl_for_handwriting(monkeypatch):
    regions = [{"box": (0, 0, 9, 9), "parts": [(0, 0, 9, 9)], "handwriting": True}]
    monkeypatch.setattr(ti, "_vl_translate_texts", lambda texts, name, **k: ["VL:" + texts[0]])
    out, src, tgt = ti._translate_regions_best(["6. nope"], regions, "es")
    assert out == ["VL:6. nope"]                 # used the VL translator


def test_translate_best_falls_back_to_nllb_per_missing_line(monkeypatch):
    regions = [
        {"box": (0, 0, 9, 9), "parts": [(0, 0, 9, 9)], "handwriting": True},
        {"box": (0, 0, 9, 9), "parts": [(0, 0, 9, 9)], "handwriting": True},
    ]
    # VL translates the first line, returns None for the second
    monkeypatch.setattr(ti, "_vl_translate_texts", lambda texts, name, **k: ["VL:uno", None])
    monkeypatch.setattr(ti, "_translate_regions",
                        lambda texts, target: (["NLLB:" + texts[0]], "eng_Latn", "spa_Latn"))
    out, src, tgt = ti._translate_regions_best(["uno", "dos"], regions, "es")
    assert out[0] == "VL:uno" and out[1] == "NLLB:dos"   # gap filled by NLLB


def test_translate_best_uses_nllb_for_digital(monkeypatch):
    regions = [{"box": (0, 0, 9, 9), "parts": [(0, 0, 9, 9)], "handwriting": False}]
    called = {"vl": False}

    def _no_vl(texts, name, **k):
        called["vl"] = True
        return [None]
    monkeypatch.setattr(ti, "_vl_translate_texts", _no_vl)
    monkeypatch.setattr(ti, "_translate_regions",
                        lambda texts, target: (["NLLB:" + texts[0]], "eng_Latn", "spa_Latn"))
    out, src, tgt = ti._translate_regions_best(["Get started"], regions, "es")
    assert out == ["NLLB:Get started"] and called["vl"] is False   # VL not used for digital


# ---- _looks_degenerate / _accept_vl_read (VL read gating) ----
def test_looks_degenerate_catches_loops():
    assert ti._looks_degenerate("AunderAunderAunderAunder") is True
    assert ti._looks_degenerate("eacheacheacheachyear each year") is True
    assert ti._looks_degenerate("↓ ↓↓↓ ▲ ↓↑↓↓↑↑↓ ↓ ▼ ↓ ▼↓↓▼") is True
    assert ti._looks_degenerate("each year each year each year each year") is True


def test_looks_degenerate_keeps_good_reads():
    assert ti._looks_degenerate("6.nope") is False
    assert ti._looks_degenerate("14.going to Rome or pompeii") is False
    assert ti._looks_degenerate("Significant other are required to be included!!") is False


def test_accept_vl_read_replaces_good_reads():
    assert ti._accept_vl_read("6. hope", "6.nope") is True
    assert ti._accept_vl_read("14. going to Home", "14.going to Rome") is True


def test_accept_vl_read_rejects_bleed_and_garbage():
    # multi-line read (crop bled into a neighbour line)
    assert ti._accept_vl_read("21. A dress", "20 Take bubble bath\nA dress 1 pair") is False
    # degenerate loop
    assert ti._accept_vl_read("6. nope", "AunderAunderAunderAunder") is False
    # runaway: far longer than the detected line
    assert ti._accept_vl_read(
        "8. die", "8. die and then a whole lot of extra hallucinated words here yes") is False


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


# ---- _synth_container (fallback when no pill detected) ----
def test_synth_container_pads_box_into_free_space():
    box = (100, 50, 180, 70)            # a compact label
    cont = ti._synth_container(box, [box], iw=400, ih=120)
    cx0, cy0, cx1, cy1 = cont
    assert cx0 <= box[0] and cx1 >= box[2]      # widened horizontally
    assert cy0 <= box[1] and cy1 >= box[3]      # padded vertically
    assert cx0 >= 0 and cy0 >= 0 and cx1 <= 400 and cy1 <= 120  # on-page


def test_synth_container_does_not_cross_neighbour():
    box = (100, 50, 180, 70)
    right = (240, 50, 320, 70)          # neighbour to the right on the same row
    cont = ti._synth_container(box, [box, right], iw=400, ih=120)
    assert cont[2] <= 240               # never expands into the neighbour


def test_fit_and_draw_halo_renders_outline_and_core():
    from PIL import Image, ImageDraw
    import numpy as np
    img = Image.new("RGB", (300, 80), (255, 255, 255))
    d = ImageDraw.Draw(img)
    ti._fit_and_draw(d, "Hola", (10, 10, 290, 70), (10, 10, 10),
                     style={"klass": "sans", "ink_h": 30}, halo=(255, 0, 0))
    arr = np.asarray(img)
    red = (arr[:, :, 0] > 180) & (arr[:, :, 1] < 80) & (arr[:, :, 2] < 80)
    assert red.any()                              # the halo outline rendered
    assert (arr.sum(axis=2) < 120).any()          # the dark glyph core rendered


def test_digital_nav_long_single_line_stays_one_line():
    from PIL import Image
    import numpy as np
    orig = Image.new("RGB", (900, 120), (255, 255, 255))
    box = (50, 40, 850, 64)                       # one-line nav row (one part)
    regions = [{"box": box, "parts": [box], "handwriting": False}]
    long = ("Características Hosting Desarrolladores Roadmap "
            "Actualizaciones Comparar Preguntas")
    out = ti._render_translations(orig.copy(), regions, [long], orig_img=orig)
    arr = np.asarray(out)
    ink_rows = np.where((arr < 200).any(axis=(1, 2)))[0]
    assert ink_rows.size > 0
    # stayed ~one line in the nav band — did NOT wrap + flow well below the row
    assert ink_rows.max() <= box[3] + 8


def test_digital_label_render_stays_within_synth_container():
    from PIL import Image
    import numpy as np
    orig = Image.new("RGB", (400, 120), (245, 245, 245))
    box = (150, 50, 230, 70)
    regions = [{"box": box, "parts": [box], "handwriting": False}]
    long = "Comenzar ahora mismo gratis"      # longer than the EN label
    out = ti._render_translations(orig.copy(), regions, [long], orig_img=orig)
    arr = np.asarray(out)
    # ink stays INSIDE the synthesized container (never spills toward the edges)
    cont = ti._synth_container(box, [box], 400, 120)
    ink_cols = np.where((arr < 200).any(axis=(0, 2)))[0]
    assert ink_cols.size > 0                       # the label IS rendered (visible)
    assert ink_cols.min() >= cont[0] and ink_cols.max() <= cont[2]
    assert cont[0] > 0 and cont[2] < 400           # and well clear of the page edges


# ---- _pick_font_path / _fits_in_box (hybrid handwriting placement) ----
def test_fits_in_box_true_when_short_at_ink_size():
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (400, 200), (255, 255, 255)))
    box = (10, 10, 300, 50)                      # wide box, h=40
    style = {"klass": "sans", "ink_h": 24}
    assert ti._fits_in_box(d, "Nope", box, style) is True


def test_fits_in_box_false_when_long_text_in_narrow_box():
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (400, 200), (255, 255, 255)))
    box = (10, 10, 90, 34)                        # narrow box, h=24
    style = {"klass": "sans", "ink_h": 20}
    long = "Se requiere incluir a la pareja en la lista de invitados"
    assert ti._fits_in_box(d, long, box, style) is False


def test_hybrid_handwriting_stays_within_image_bounds():
    # A long translation in a small hw box must not draw past the image edge.
    from PIL import Image
    import numpy as np
    orig = Image.new("RGB", (300, 120), (255, 255, 255))
    # paint some dark ink so _analyze_region samples a real (dark) pen color
    np_o = np.asarray(orig).copy()
    np_o[20:36, 12:120] = (20, 20, 20)
    orig = Image.fromarray(np_o)
    regions = [{"box": (12, 18, 120, 38), "parts": [(12, 18, 120, 38)],
                "handwriting": True}]
    translations = ["una traducción mucho más larga que el cuadro original original"]
    out = ti._render_translations(orig.copy(), regions, translations, orig_img=orig)
    arr = np.asarray(out)
    # nothing drawn in the last 2 rows / cols (stayed on-page)
    assert (arr[-2:, :] >= 250).all()
    assert (arr[:, -2:] >= 250).all()


# ---- _page_is_dark (whole-image median, robust to a dark photo corner) ----
def test_page_is_dark_false_for_bright_paper_with_dark_corner():
    from PIL import Image
    import numpy as np
    arr = np.full((100, 100, 3), 230, dtype=np.uint8)   # bright paper
    arr[0:12, 0:12] = 10                                  # dark binding/shadow corner
    assert ti._page_is_dark(Image.fromarray(arr)) is False


def test_page_is_dark_true_for_dark_ui():
    from PIL import Image
    import numpy as np
    assert ti._page_is_dark(Image.fromarray(np.full((60, 60, 3), 18, dtype=np.uint8))) is True


# ---- _page_hw_size (uniform handwriting size) ----
def test_page_hw_size_zero_without_handwriting():
    from PIL import Image
    import numpy as np
    img = Image.fromarray(np.full((50, 50, 3), 255, dtype=np.uint8))
    r = {"box": (0, 0, 10, 10), "parts": [(0, 0, 10, 10)], "handwriting": False}
    assert ti._page_hw_size(img, [r]) == 0
    assert ti._page_hw_size(None, []) == 0


def test_page_hw_size_medians_measured_ink_heights():
    from PIL import Image
    import numpy as np
    arr = np.full((200, 200, 3), 255, dtype=np.uint8)
    arr[10:30, 10:100] = 20      # ~20px ink
    arr[60:84, 10:100] = 20      # ~24px ink
    arr[110:140, 10:100] = 20    # ~30px ink
    img = Image.fromarray(arr)
    regions = [
        {"box": (8, 8, 102, 32), "parts": [(8, 8, 102, 32)], "handwriting": True},
        {"box": (8, 58, 102, 86), "parts": [(8, 58, 102, 86)], "handwriting": True},
        {"box": (8, 108, 102, 142), "parts": [(8, 108, 102, 142)], "handwriting": True},
    ]
    assert 12 <= ti._page_hw_size(img, regions) <= 36   # ~ the median ink height


# ---- _page_pen_color (one clean BLACK pen for all handwriting) ----
def test_page_pen_color_is_strong_black_on_light_page():
    out = ti._page_pen_color(bg_dark=False)
    assert out == (20, 20, 20)
    # genuinely dark (a clean black pen, not a faint grey)
    assert 0.299 * out[0] + 0.587 * out[1] + 0.114 * out[2] < 40


def test_page_pen_color_is_near_white_on_dark_page():
    assert ti._page_pen_color(bg_dark=True) == (236, 236, 236)


# ---- _clean_vl_text: markdown / LaTeX escaping the VL sometimes emits ----
def test_clean_vl_strips_latex_escaping():
    assert "\\" not in ti._clean_vl_text("21\\. A dress \\(underwear\\)s")
    assert ti._clean_vl_text("16\\. A beautiful place") == "16. A beautiful place"
    assert ti._clean_vl_text("a \\quad b") == "a b"
    out = ti._clean_vl_text("19\\. yes.\\, k., kitty,\\ babydoll\\. muffin pie")
    assert "\\" not in out and "muffin pie" in out


def test_orig_line_count():
    # One row of word boxes ⇒ single line.
    assert ti._orig_line_count([(10, 10, 50, 30), (55, 11, 90, 31)]) == 1
    # Three stacked rows ⇒ three lines.
    assert ti._orig_line_count(
        [(10, 10, 200, 30), (10, 40, 200, 60), (10, 70, 200, 90)]) == 3
    assert ti._orig_line_count([]) == 1


def test_pill_bounds_detects_rounded_filled_pill():
    import numpy as np
    # filled pill with rounded corners (corner pixels left as page bg)
    img = np.full((120, 300, 3), 255, dtype=np.uint8)
    img[40:80, 80:220] = (36, 92, 220)        # blue filled pill
    for (yy, xx) in [(40, 80), (40, 219), (79, 80), (79, 219)]:
        img[yy, xx] = (255, 255, 255)         # rounded corners
    text_box = (110, 54, 190, 66)
    out = ti._pill_bounds(img, text_box, page_bg=(255, 255, 255))
    assert out is not None
    px0, py0, px1, py1 = out
    assert px0 <= 90 and px1 >= 210


def test_pill_bounds_detects_antialiased_outlined_button():
    import numpy as np
    # outlined button whose 2px border has a 1px anti-aliased halo on each side
    img = np.full((120, 300, 3), 255, dtype=np.uint8)
    halo = (150, 150, 150)
    core = (60, 60, 60)
    img[39, 80:220] = halo
    img[40:42, 80:220] = core; img[42, 80:220] = halo          # top
    img[77, 80:220] = halo; img[78:80, 80:220] = core; img[80, 80:220] = halo  # bottom
    img[40:80, 79] = halo; img[40:80, 80:82] = core; img[40:80, 82] = halo     # left
    img[40:80, 217] = halo; img[40:80, 218:220] = core; img[40:80, 220] = halo # right
    text_box = (120, 54, 180, 66)
    out = ti._pill_bounds(img, text_box, page_bg=(255, 255, 255))
    assert out is not None


def test_pill_bounds_none_for_word_between_neighbours():
    import numpy as np
    # A word with other words to its LEFT and RIGHT (like a nav row) but nothing
    # above/below must NOT be mistaken for a button — no 4-sided enclosure.
    img = np.full((120, 400, 3), 255, dtype=np.uint8)
    img[52:68, 40:70] = (20, 20, 20)     # left neighbour word
    img[52:68, 330:360] = (20, 20, 20)   # right neighbour word
    text_box = (180, 52, 240, 68)        # the middle word
    assert ti._pill_bounds(img, text_box, page_bg=(255, 255, 255)) is None
