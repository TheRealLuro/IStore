# In-Image Translation Overlay Refinement — Design

**Date:** 2026-06-04
**Branch:** app-staging-w23
**Scope:** `backend/api/translate_image.py` (+ small change to the VL crop builder it
owns). No changes to the NLLB translator, Florence detector, or LaMa erase.

## Problem

The in-image translate path (OCR → translate → erase → render-in-place) has three
quality gaps, observed on the `eval/locate_anything/out_core` outputs (handwriting
and digital-signage images, → Spanish):

1. **Meaning-changing OCR misreads** on handwriting. The VL recognizer reads each
   detected line crop *in isolation*, so single-word ambiguities resolve wrong:
   - "Nope" → read "hope" → *esperanza*
   - "going to **Rome**" → read "home" → *ir a casa*
   - "Loud Noises / yelling" → hallucinated *Ruidos fuertes en bicicleta*
   - "Significant other are required to be Included" → *Se requieren otros datos…*
2. **Handwriting overlay doesn't fit the page** and looks unstructured: each line is
   shrink-to-fit inside its own cramped original box, so Spanish (≈+20–30% length)
   goes tiny or clips, and dense boxes overlap.
3. **Digital pills/buttons overflow.** When `_pill_bounds` fails to detect a
   container (rounded / gradient / anti-aliased borders), the label falls back to
   the narrow text box and a longer translation shrinks to nothing or spills past
   the button edge. Layout is not identical across languages.

## Root causes (verified in code)

- `_vl_read_regions` (translate_image.py:687–755) crops each box individually with
  small padding and reads it alone — no surrounding context.
- `_render_translations` handwriting branch (1828–1834) calls `_fit_and_draw` with
  `max_h = box height`, `valign="top"` — strictly in-box shrink, no use of nearby
  whitespace. Pen color is already forced uniform near-black `(26,29,41)` at 1816,
  so "same color" is partly solved but the layout fights it.
- `_pill_bounds` (1514–1602) uses straight-scanline edge tests that miss rounded
  corners and anti-aliased/gradient borders; on miss, the render path (1839–1844)
  uses the original text box, not the pill.

## Decisions (user-approved)

- **Handwriting layout → Hybrid:** keep each line in place when the translation
  fits; reflow into nearby free space, then shrink, only when it would clip.
- **OCR accuracy → Per-line + neighbor context:** widen crops to include adjacent
  lines as context, keep 1:1 box alignment (no whole-image re-alignment).
- **Pills → Always shrink to fit inside the container,** with more robust container
  detection so every language renders identically and nothing overflows.

## Design

### A. OCR accuracy — neighbor-context line reads

Change `_vl_read_regions` (and its caller `_vl_rewrite_regions`) so each target
line is read **with its neighbors visible**:

- Use reading order (regions are already top→bottom, left→right after
  `_merge_regions`) to find the previous and next line for each target.
- Build the crop as a **vertical band** spanning prev-top → next-bottom (clamped to
  the image), at full target-line width plus context padding. When there is no
  prev/next (first/last line), the band simply starts/ends at the target.
- **Mark the target line** with a thin translucent bracket "‹" drawn in the LEFT
  margin of a *copy* of the crop (never over the ink), so the model knows which
  line to transcribe.
- Prompt: *"Several handwritten lines are shown. Transcribe ONLY the line marked
  with the ‹ bracket on the left, exactly as written, preserving spelling, numbers
  and punctuation. Use the other lines only as context to choose the right word.
  Output ONLY that line's transcription — no quotes, labels, or commentary."*
- Keep the existing batching, `max_new_tokens`, anti-repetition, `_clean_vl_text`,
  and `_looks_degenerate` fallback-to-Florence guards. Output remains **1:1 with
  the input boxes**.

Out of scope (flagged, not fixed here): NLLB mistranslating the idiom "significant
other"; accurate reading + context should reduce it, but the translator is not
reworked in this pass.

### B. Handwriting overlay — hybrid layout + one uniform pen color

In `_render_translations` (hw branch) and `_fit_and_draw`:

- **Hybrid placement** per line, in order:
  1. Draw at the original ink size at the original top/left if the wrapped text
     fits the box width and height.
  2. Else expand into adjacent free space — `_avail_height` (below) plus a new
     right-gap measurement (to the next region on the same row) — and wrap there,
     keeping the original top.
  3. Else shrink-to-fit (current behavior) as the last resort.
  - Always clamp the drawn block to the image bounds so nothing runs off the page.
- **One page-level pen color:** compute the median ink color across all handwriting
  regions once; keep it if it is a readable saturated pen color (e.g. blue/black),
  else snap to near-black. Apply that single color to every hw line (uniform AND
  faithful), replacing the per-region fill. Keep Patrick Hand, regular weight.

### C. Digital pills/buttons — never overflow, identical per language

In `_pill_bounds` and the pill render branch:

- **More robust detection:** allow corner pixels to read as page background
  (rounded corners); replace the strict thin-stroke test with a run-based edge test
  tolerant of anti-aliasing/gradients; keep the filled-pill path but loosen the
  uniformity tolerance slightly. All scans stay capped (no run-away).
- **Synthesized fallback container:** when no container is detected but the region
  is a compact single-line label, treat a padded version of the original box as the
  container so the label is still constrained, not overflowing.
- **Fit:** always shrink-to-fit *inside* the container, centered, at the original
  line-count — guaranteeing identical layout across languages and zero overflow.

## Components & boundaries

All edits are local to `translate_image.py`. Public route behavior and signatures
are unchanged; only the internal recognition-crop and render helpers change. Each
helper keeps its current contract (never raises into the route; falls back safely).

## Testing

- **Unit (no GPU), extend `tests/test_in_image_render.py`:**
  - Pill fit: rendered text bounds never exceed the detected/synthesized container.
  - Hybrid hw: picks in-place when it fits; only shrinks when forced; never exceeds
    image bounds.
  - Neighbor band: crop spans the right rows; target marker present; read list stays
    1:1 with boxes (mock the VL).
  - Uniform pen color: one color chosen for all hw regions; readable contrast.
  - `_pill_bounds` robustness: synthetic rounded/anti-aliased button fixtures detect;
    plain page text returns None (no false positives).
- **Visual eval (requires Docker up):** re-run `eval/translate_image_e2e.py` on the
  handwriting + digital fixtures, deliver before/after PNGs for review, and iterate
  until quality is right (not merely until tests pass).

## Success criteria

- The four named misreads read correctly (or fall back to Florence rather than
  hallucinate).
- Handwriting page is legible, uniform-colored, structured, and fully on-page.
- Pill/button labels fit inside their containers in every target language with
  identical layout and no overflow.
- All unit tests pass; visual output judged up-to-quality on the eval fixtures.
