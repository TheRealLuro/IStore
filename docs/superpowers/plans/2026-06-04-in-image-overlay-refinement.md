# In-Image Overlay Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the in-image translation overlay read handwriting accurately (neighbor context), lay it out cleanly on the page (hybrid placement + one uniform pen color), and keep digital pill/button labels inside their containers in every language.

**Architecture:** All edits are local helpers in `backend/api/translate_image.py`; the FastAPI route and its public contract are unchanged. New pure/PIL-only helpers are unit-tested headless (no GPU); the VL recognizer change is exercised by building context crops 1:1 with the input boxes. Final quality is judged by re-running the Docker eval and eyeballing PNGs.

**Tech Stack:** Python, Pillow (PIL ImageDraw/ImageFont, headless CPU), NumPy, pytest. Qwen2.5-VL (handwriting OCR) and Florence-2 (detection) are only touched at the crop/prompt boundary.

---

## File Structure

- Modify: `backend/api/translate_image.py`
  - OCR context: `_build_context_crops`, `_context_band`, `_mark_target` (new); `_vl_read_regions` (rewire).
  - Handwriting render: `_page_pen_color`, `_pick_font_path`, `_fits_in_box` (new); `_fit_and_draw` (DRY refactor to use `_pick_font_path`); `_render_translations` (hw branch + pen color + pill fallback).
  - Pills: `_pill_bounds` (loosen tolerances for AA/gradient borders); `_synth_container` (new).
- Test: `tests/test_in_image_render.py` (extend; same pure-function style).

Conventions to follow (from the existing test file): `import backend.api.translate_image as ti`, the `_r(x0,y0,x1,y1,text)` region helper, NumPy image fixtures, `monkeypatch` for env. Keep every new helper exception-safe (never raise into the route).

---

## Task 1: One uniform handwriting pen color

**Files:**
- Modify: `backend/api/translate_image.py` (add `_page_pen_color` near `_render_translations`, ~line 1766)
- Test: `tests/test_in_image_render.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_in_image_render.py -k page_pen_color -v`
Expected: FAIL — `AttributeError: module 'backend.api.translate_image' has no attribute '_page_pen_color'`

- [ ] **Step 3: Write minimal implementation**

Add above `_render_translations` (≈ line 1766):

```python
def _page_pen_color(inks: list[tuple], bg_dark: bool) -> tuple:
    """Pick ONE pen color for ALL handwriting lines from the per-region sampled
    `inks` so the whole note renders in a uniform, readable hand instead of a
    different shade per box. Median the sampled inks, then guarantee readable
    contrast for the page background. Falls back to a near-black ('pen on paper')
    or near-white (dark page) when no confident ink was sampled."""
    import numpy as np

    if not inks:
        return (236, 236, 236) if bg_dark else (26, 29, 41)
    arr = np.array(inks, dtype=np.float32)
    med = tuple(int(c) for c in np.median(arr, axis=0))
    return _ensure_contrast(med, bg_dark)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_in_image_render.py -k page_pen_color -v`
Expected: PASS (3 passed). Note: `_ensure_contrast((24,35,167), False)` keeps the blue (luma ≈ 47 < threshold); the faint-gray median snaps to `(24,24,24)`.

- [ ] **Step 5: Commit**

```bash
git add backend/api/translate_image.py tests/test_in_image_render.py
git commit -m "feat(in-image): one uniform readable pen color for handwriting overlay"
```

---

## Task 2: Neighbor-context band geometry + target marker

**Files:**
- Modify: `backend/api/translate_image.py` (add `_context_band`, `_mark_target` near `_vl_read_regions`, ≈ line 686)
- Test: `tests/test_in_image_render.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_in_image_render.py -k "context_band or mark_target" -v`
Expected: FAIL — `AttributeError: ... has no attribute '_context_band'`

- [ ] **Step 3: Write minimal implementation**

Add just above `_vl_read_regions` (≈ line 687):

```python
def _context_band(boxes: list[tuple], i: int, iw: int, ih: int,
                  pad_x_frac: float = 0.06, pad_y_frac: float = 0.35):
    """Crop window for reading line `i` WITH its neighbors visible: a vertical
    band spanning the previous line's top to the next line's bottom (clamped to
    the image), wide enough to show the neighbors' text too. Returns
    (band_box, target_local_box) where target_local_box is line i's box expressed
    in band-local coordinates (for `_mark_target`). Reading order assumed (boxes
    already top->bottom from `_merge_regions`)."""
    x0, y0, x1, y1 = boxes[i]
    px = int((x1 - x0) * pad_x_frac) + 4
    py = int((y1 - y0) * pad_y_frac) + 4
    xs0, xs1 = [x0], [x1]
    top = y0 - py
    bot = y1 + py
    if i > 0:
        p = boxes[i - 1]
        xs0.append(p[0]); xs1.append(p[2]); top = min(top, p[1])
    if i + 1 < len(boxes):
        n = boxes[i + 1]
        xs0.append(n[0]); xs1.append(n[2]); bot = max(bot, n[3])
    bx0 = max(0, min(xs0) - px)
    bx1 = min(iw, max(xs1) + px)
    by0 = max(0, top)
    by1 = min(ih, bot)
    local = (x0 - bx0, y0 - by0, x1 - bx0, y1 - by0)
    return (bx0, by0, bx1, by1), local


def _mark_target(crop, target_local_box):
    """Return a COPY of `crop` with a small translucent bracket drawn in the LEFT
    margin beside the target line, so the VL knows which line to transcribe. Never
    drawn over the text itself. Input image is not mutated."""
    from PIL import ImageDraw

    c = crop.copy()
    d = ImageDraw.Draw(c)
    _x0, y0, _x1, y1 = target_local_box
    yc = (y0 + y1) // 2
    half = max(4, (y1 - y0) // 2)
    # a left-margin "‹" bracket: vertical bar + two short arms
    d.line([(3, yc - half), (3, yc + half)], fill=(220, 40, 40), width=2)
    d.line([(3, yc - half), (10, yc - half)], fill=(220, 40, 40), width=2)
    d.line([(3, yc + half), (10, yc + half)], fill=(220, 40, 40), width=2)
    return c
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_in_image_render.py -k "context_band or mark_target" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/api/translate_image.py tests/test_in_image_render.py
git commit -m "feat(in-image): neighbor-context band geometry + target marker for VL OCR"
```

---

## Task 3: Build context crops 1:1 and rewire the VL recognizer

**Files:**
- Modify: `backend/api/translate_image.py` (add `_build_context_crops`; rewrite `_vl_read_regions` body + prompt, ≈ line 687-755)
- Test: `tests/test_in_image_render.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_in_image_render.py -k build_context_crops -v`
Expected: FAIL — no attribute `_build_context_crops`.

- [ ] **Step 3: Write minimal implementation**

Add `_build_context_crops` above `_vl_read_regions`, then replace the crop-building loop and prompt inside `_vl_read_regions`.

```python
def _build_context_crops(full_img, boxes: list[tuple]) -> list:
    """For each detected line, build a neighbor-context crop (the line plus the
    lines above/below) with the target line marked, upscaling short crops for
    legibility. Returns one RGB crop per box, strictly 1:1 with `boxes`."""
    from PIL import Image as PILImage

    iw, ih = full_img.width, full_img.height
    crops = []
    for i in range(len(boxes)):
        band, local = _context_band(boxes, i, iw, ih)
        bx0, by0, bx1, by1 = band
        c = full_img.crop((bx0, by0, bx1, by1)).convert("RGB")
        c = _mark_target(c, local)
        if c.height and c.height < 96:   # upscale so target glyphs stay legible
            s = 96.0 / c.height
            c = c.resize((max(1, int(c.width * s)), 96), PILImage.LANCZOS)
        crops.append(c)
    return crops
```

Then in `_vl_read_regions` replace the existing per-box crop loop (the block building `crops` at ≈ lines 701-711) with:

```python
    crops = _build_context_crops(full_img, list(boxes))
```

and replace the `prompt = (...)` string (≈ lines 713-717) with:

```python
    prompt = (
        "Several handwritten lines are shown. Transcribe ONLY the line marked "
        "with the red bracket on the left edge, exactly as written, preserving "
        "spelling, numbers and punctuation. Use the other lines only as context "
        "to choose the right word. Output ONLY that one line's transcription — "
        "no quotes, labels or commentary."
    )
```

Leave the batching, `model.generate(...)`, decode, `_clean_vl_text`, and the `reads` 1:1 accumulation unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_in_image_render.py -k "build_context_crops or clean_vl" -v`
Expected: PASS. Then full file: `python -m pytest tests/test_in_image_render.py -v` — all green (no regression).

- [ ] **Step 5: Commit**

```bash
git add backend/api/translate_image.py tests/test_in_image_render.py
git commit -m "feat(in-image): read each handwriting line with neighbor context (fixes meaning-changing misreads)"
```

---

## Task 4: DRY font resolution + in-place fit predicate + hybrid handwriting layout

**Files:**
- Modify: `backend/api/translate_image.py` (`_pick_font_path` new; `_fit_and_draw` refactor at ≈ 1652-1656; `_fits_in_box` new; `_render_translations` hw branch at ≈ 1808-1834)
- Test: `tests/test_in_image_render.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_in_image_render.py -k "fits_in_box or hybrid_handwriting" -v`
Expected: FAIL — no attribute `_fits_in_box`.

- [ ] **Step 3: Write minimal implementation**

(a) Add a shared font-path resolver (DRY) above `_fit_and_draw` (≈ line 1605):

```python
def _pick_font_path(text: str, style: Optional[dict]) -> Optional[str]:
    """Resolve the TTF path for `text` + `style` the same way for both the
    measurement (`_fits_in_box`) and the draw (`_fit_and_draw`): script font for
    non-Latin glyphs first, then the handwriting face for handwritten regions,
    else the class/weight/slant-aware Latin font."""
    bold = bool((style or {}).get("bold", False))
    italic = bool((style or {}).get("italic", False))
    klass = (style or {}).get("klass", "sans")
    handwriting = bool((style or {}).get("handwriting", False))
    fp = _script_font(text, bold)
    if fp is None and handwriting:
        fp = _handwriting_font()
    if fp is None:
        fp = _resolve_font(klass, bold, italic)
    return fp
```

In `_fit_and_draw`, replace the four lines that compute `font_path` (≈ 1652-1656) with:

```python
    font_path = _pick_font_path(text, {"klass": klass, "bold": bold,
                                       "italic": italic, "handwriting": handwriting})
```

(b) Add the in-place fit predicate above `_render_translations` (≈ line 1766):

```python
def _fits_in_box(draw, text: str, box, style: Optional[dict]) -> bool:
    """True when `text`, wrapped to the box width at the ORIGINAL ink size, fits
    within the box height — i.e. it can be drawn in place (hybrid layout) without
    shrinking or flowing into neighbouring whitespace. Mirrors `_fit_and_draw`'s
    ceiling/wrap math so the predicate and the draw agree."""
    from PIL import ImageFont

    if not text.strip():
        return True
    x0, y0, x1, y1 = box
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)
    ink_h = int((style or {}).get("ink_h", 0) or 0)
    size = max(6, min(int(ink_h * 1.15), 200)) if ink_h >= 6 else max(6, int(box_h * 0.72))
    fp = _pick_font_path(text, style)
    try:
        font = ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    lines = _wrap_to_width(draw, text, font, box_w)
    if not lines:
        return True
    widest = max(_text_w(draw, ln, font) for ln in lines)
    lh = _line_h(font)
    gap = max(1, int(lh * 0.12))
    total_h = len(lines) * lh + (len(lines) - 1) * gap
    return widest <= box_w and total_h <= box_h
```

(c) Rewire the handwriting render. In `_render_translations`, change the per-page setup so all handwriting lines share ONE pen color, and replace the hw draw branch.

After `page_bg = ...` (≈ line 1785) add a one-time page pen color computed from the confident hw inks:

```python
    # ONE uniform pen color for every handwriting line (sampled once across the
    # page) so the note reads as a single consistent hand, not a shade per box.
    hw_inks = []
    if orig_img is not None:
        for r in regions:
            if r.get("handwriting") and not r.get("skip"):
                st = _analyze_region(orig_img, r.get("parts", [r["box"]])[0])
                if st.get("ink_confident"):
                    hw_inks.append(st["ink"])
    page_bg_dark = bool(_bg_is_dark(orig_img, (0, 0, min(orig_img.width, 8),
                                               min(orig_img.height, 8)))) \
        if orig_img is not None else False
    hw_pen = _page_pen_color(hw_inks, page_bg_dark)
    iw_img = orig_img.width if orig_img is not None else inpainted.width
    ih_img = orig_img.height if orig_img is not None else inpainted.height
```

Then in the hw block, replace the fill assignment (≈ line 1816) and the hw draw call (≈ 1828-1834) with:

```python
        if hw:
            style = dict(style)
            style["handwriting"] = True
            style["bold"] = False
            style["italic"] = False
            fill = hw_pen            # uniform page pen color
```

and the draw branch:

```python
        if hw:
            # Hybrid placement: draw at the original ink size in place when it
            # fits; else allow it to flow into the whitespace below (clamped to
            # the image) before shrinking — so it stays legible AND on-page.
            box_h = box[3] - box[1]
            if _fits_in_box(draw, text, box, style):
                max_h = box_h
            else:
                max_h = min(_avail_height(box, all_boxes), max(box_h, ih_img - box[1] - 2))
            _fit_and_draw(draw, text, box, fill, style, max_h=max_h, valign="top")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_in_image_render.py -k "fits_in_box or hybrid_handwriting" -v`
Expected: PASS (3 passed). Then `python -m pytest tests/test_in_image_render.py -v` — all green.

- [ ] **Step 5: Commit**

```bash
git add backend/api/translate_image.py tests/test_in_image_render.py
git commit -m "feat(in-image): hybrid handwriting placement (in-place, reflow if needed) + uniform pen; DRY font resolution"
```

---

## Task 5: Robust pill/button detection (rounded + anti-aliased borders)

**Files:**
- Modify: `backend/api/translate_image.py` (`_pill_bounds` tolerances at ≈ 1542, 1548, 1569, 1576-1584, 1599)
- Test: `tests/test_in_image_render.py`

- [ ] **Step 1: Write the failing test**

```python
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
    for (a, b, c) in [(39, 41, 43)]:  # top edge: halo, core, halo rows
        img[a, 80:220] = halo
    img[40:42, 80:220] = core; img[42, 80:220] = halo          # top
    img[77, 80:220] = halo; img[78:80, 80:220] = core; img[80, 80:220] = halo  # bottom
    img[40:80, 79] = halo; img[40:80, 80:82] = core; img[40:80, 82] = halo     # left
    img[40:80, 217] = halo; img[40:80, 218:220] = core; img[40:80, 220] = halo # right
    text_box = (120, 54, 180, 66)
    out = ti._pill_bounds(img, text_box, page_bg=(255, 255, 255))
    assert out is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_in_image_render.py -k "rounded_filled or antialiased_outlined" -v`
Expected: FAIL — the AA-border case (and possibly the rounded case) returns `None` under the current strict tolerances.

- [ ] **Step 3: Write minimal implementation**

In `_pill_bounds`, loosen four tolerances so anti-aliased / gradient edges register while plain page text still returns `None`:

1. Filled-pill uniformity gate (≈ line 1542): change `std <= 18` to `std <= 26`.
2. Filled-pill fill match (≈ line 1548): change `_color_close(px, med, tol=20)` to `tol=28`.
3. Outlined border background tolerance — the two `_color_close(px, page_bg, tol=14)` calls in `_scan_border` (≈ lines 1576, 1582): change both to `tol=20` (JPEG/AA halo still counts as "page" on the outside, but the dark core still deviates).
4. Outlined border thin-run cap (≈ line 1578): change `run <= 7` to `run <= 10` (a 2px core + 1px halo each side still reads as a thin stroke).
5. Four-stroke color agreement (≈ line 1599): change `tol=30` to `tol=40` (AA softens the sampled stroke color per side).

These are the only edits; the scan caps and four-sided requirement are unchanged, so `test_pill_bounds_none_for_plain_text_on_page` and `test_pill_bounds_none_for_word_between_neighbours` still return `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_in_image_render.py -k pill_bounds -v`
Expected: PASS for all pill tests — the two new ones AND the four existing ones (`contained_button`, `outlined_button`, `none_for_plain_text`, `none_for_word_between_neighbours`). If a "none" test regresses, tighten the specific tolerance until both hold.

- [ ] **Step 5: Commit**

```bash
git add backend/api/translate_image.py tests/test_in_image_render.py
git commit -m "feat(in-image): detect rounded + anti-aliased pill/button containers"
```

---

## Task 6: Synthesized fallback container so labels never overflow

**Files:**
- Modify: `backend/api/translate_image.py` (`_synth_container` new ≈ line 1490; `_render_translations` digital-label branch ≈ 1839-1844)
- Test: `tests/test_in_image_render.py`

- [ ] **Step 1: Write the failing test**

```python
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


def test_digital_label_render_stays_within_synth_container():
    from PIL import Image
    import numpy as np
    orig = Image.new("RGB", (400, 120), (245, 245, 245))
    box = (150, 50, 230, 70)
    regions = [{"box": box, "parts": [box], "handwriting": False}]
    long = "Comenzar ahora mismo gratis"      # longer than the EN label
    out = ti._render_translations(orig.copy(), regions, [long], orig_img=orig)
    arr = np.asarray(out)
    # ink only within a padded area around the box, never spilling to the edges
    ink_cols = np.where((arr < 200).any(axis=(0, 2)))[0]
    assert ink_cols.min() >= 110 and ink_cols.max() <= 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_in_image_render.py -k "synth_container or digital_label_render" -v`
Expected: FAIL — no attribute `_synth_container`.

- [ ] **Step 3: Write minimal implementation**

(a) Add `_synth_container` near `_avail_height` (≈ line 1490):

```python
def _synth_container(box, all_boxes, iw: int, ih: int) -> tuple:
    """A fallback 'button' area for a compact label when no real pill/border was
    detected: pad the original box symmetrically into the free space on its row so
    a longer translation can be centred + shrunk to fit inside it (never spilling
    past where the button visually is), without crossing a neighbouring region or
    the image edge."""
    x0, y0, x1, y1 = box
    bw = x1 - x0
    bh = y1 - y0
    want = int(bw * 0.6) + bh           # modest horizontal room, scaled to size
    left_cap, right_cap = x0 - want, x1 + want
    for b in all_boxes:
        if b == tuple(box) or b == box:
            continue
        same_row = min(y1, b[3]) - max(y0, b[1]) > 0.3 * bh
        if not same_row:
            continue
        if b[2] <= x0:                  # neighbour on the left
            left_cap = max(left_cap, b[2] + 2)
        if b[0] >= x1:                  # neighbour on the right
            right_cap = min(right_cap, b[0] - 2)
    nx0 = max(0, left_cap)
    nx1 = min(iw, right_cap)
    pad_v = int(bh * 0.3) + 2
    ny0 = max(0, y0 - pad_v)
    ny1 = min(ih, y1 + pad_v)
    return (nx0, ny0, nx1, ny1)
```

(b) In `_render_translations`, compute `iw_img/ih_img` already added in Task 4. Replace the compact single-line label branch (≈ 1839-1844) so it constrains to a synthesized container and centers (matching the pill behavior — always fit, never overflow):

```python
        elif (len(text.split()) <= 6
              and _orig_line_count(r.get("parts", [box])) == 1):
            # Borderless button / CTA with no detected container: constrain to a
            # synthesized button area and shrink-to-fit + centre, so a longer
            # translation never spills past where the label visually sits.
            cont = _synth_container(box, all_boxes, iw_img, ih_img)
            _fit_and_draw(draw, text, cont, fill, style,
                          max_h=(cont[3] - cont[1]), align="center", valign="center")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_in_image_render.py -k "synth_container or digital_label_render" -v`
Expected: PASS (3 passed). Then `python -m pytest tests/test_in_image_render.py -v` — all green.

- [ ] **Step 5: Commit**

```bash
git add backend/api/translate_image.py tests/test_in_image_render.py
git commit -m "feat(in-image): synthesized fallback container so digital labels never overflow"
```

---

## Task 7: Full unit suite + visual eval iteration (Docker)

**Files:**
- Verify: `tests/test_in_image_render.py`, `tests/test_quant.py`, `tests/test_vram_manager*.py`
- Use: `eval/translate_image_e2e.py`, fixtures in `eval/locate_anything/in/`

- [ ] **Step 1: Run the full in-image unit suite**

Run: `python -m pytest tests/test_in_image_render.py -v`
Expected: ALL pass (the original 20-odd tests + the ~14 added here). No regressions.

- [ ] **Step 2: Bring up the runtime for visual eval**

Start Docker Desktop, then the backend container/service that mounts `/app` (the GPU image used previously). Confirm Qwen2.5-VL + Florence + LaMa load (logs) and the GPU is visible.

- [ ] **Step 3: Re-run the in-image eval on the handwriting + digital fixtures**

Run (inside the container):
```bash
IMGS=/app/eval/locate_anything/in/IMG_1772.png TGTS=es,zh,ar python /app/eval/translate_image_e2e.py
```
plus the handwriting note image and a digital/pills fixture (the same inputs that produced `hw_es_vl.png` and `digital_es_pills4.png`).
Expected: PNGs written to `/app/eval/locate_anything/out_core/`.

- [ ] **Step 4: Eyeball each output against the success criteria**

Deliver the new PNGs to the user (SendUserFile) side-by-side with the originals. Check specifically:
- The four named misreads now read correctly (Nope, Rome, Loud Noises/yelling, "significant other are required to be included") — or fall back to Florence rather than hallucinate.
- Handwriting: one uniform readable pen color, clean structure, every line on-page, looks written on the paper.
- Digital pills/buttons: labels fit inside their containers in es/zh/ar with identical layout and zero overflow.

- [ ] **Step 5: Refine until up-to-quality, then commit any tuning**

If a case is still wrong, iterate on the responsible knob (VL prompt wording / context band padding; `_page_pen_color` thresholds; hybrid `max_h`; pill tolerances / `_synth_container` padding), re-run Step 3-4, and only stop when the visual output is genuinely good. Commit each refinement:
```bash
git add backend/api/translate_image.py tests/test_in_image_render.py
git commit -m "fix(in-image): tune <area> after visual eval"
```

---

## Self-Review

**Spec coverage:**
- Neighbor-context OCR → Tasks 2, 3. ✓
- Hybrid handwriting layout → Task 4. ✓
- One uniform pen color → Task 1 (+ wired in Task 4). ✓
- Robust pill detection (rounded/AA) → Task 5. ✓
- Never-overflow pills + synthesized fallback → Task 6. ✓
- Unit tests (pill bounds, hybrid, neighbor band, pen color, synth) → Tasks 1-6. ✓
- Visual eval + iterate-to-quality → Task 7. ✓
- Out-of-scope (NLLB "significant other" idiom; CJK/Arabic non-handwritten face) → noted in spec, not tasked. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code and exact pytest/`git` commands with expected output.

**Type consistency:** `_page_pen_color(inks, bg_dark)`, `_context_band(boxes,i,iw,ih)->(band,local)`, `_mark_target(crop,target_local_box)`, `_build_context_crops(full_img,boxes)`, `_pick_font_path(text,style)`, `_fits_in_box(draw,text,box,style)`, `_synth_container(box,all_boxes,iw,ih)` are referenced with the same signatures everywhere. `_render_translations` keeps its `(inpainted, regions, translations, orig_img=None)` signature; `iw_img/ih_img` are defined once (Task 4) and reused (Task 6). `_ensure_contrast(ink,bg_dark)` and `_analyze_region(...)["ink"|"ink_confident"|"ink_h"]` match the existing code.
