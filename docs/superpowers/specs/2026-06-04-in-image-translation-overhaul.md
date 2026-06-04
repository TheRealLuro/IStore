# In-image (and document) translation quality overhaul

**Date:** 2026-06-04 · **Branch:** app-staging-w23 · **Status:** design

## Why

Translating the real status report + handwritten photo into es/zh/ar/he surfaced
problems that hit **every** language (not language-specific). The render-quality
ones (font size, contrast, ghosting) are already fixed; this spec targets the
**structural** root causes that make in-image translation inconsistent across
all ~468 languages.

## Root-cause problems (from screenshot analysis)

1. **Region fragmentation** — Florence OCR returns many small per-line/per-phrase
   boxes; we translate + render each independently. This breaks (a) translation
   quality (no context → "you."→"Te quiero", a hallucinated "$199"), (b) size
   consistency (every box a different height → different font size), and (c)
   layout (fragments don't recompose). **Root cause for most symptoms.**
2. **Translation expansion vs a fixed source-sized box** — translations differ
   in length per language (often +30–60%); each box is sized for the SOURCE, so
   the same layout breaks differently per language (shrink-to-tiny / wrap-overlap
   / overflow). The universal inconsistency driver.
3. **Non-translatable text gets translated** — brand/logo/code ("neuthek",
   "PostgreSQL", "MinIO", "OpenCLIP") get OCR'd + translated, mangling them in
   every language.
4. (Fixed) font-size ballooning → now sized from original ink height.
5. (Fixed) faint/unreadable ink → minimum-contrast guarantee.
6. (Fixed) ghosting → larger inpaint dilation.

## Design

All in `backend/api/translate_image.py`. The pipeline becomes:
`OCR → MERGE regions → skip-filter → translate merged text → inpaint constituent
boxes → render into merged boxes (expansion-aware)`.

### 1. Region merging — `_merge_regions(regions)`

Turns raw OCR boxes into logical units, each: `{box: union, text: merged,
parts: [original boxes]}`. `parts` drive the inpaint mask; `box` is the render
target; `text` is what we translate (with context).

Two passes, thresholds scaled by the median region height `H`:

- **Line grouping:** sort by y-center; two boxes are the same line when their
  vertical centers differ < 0.6·H AND they vertically overlap. Within a line,
  sort by x0 and merge consecutive boxes whose horizontal gap < 1.5·H (a normal
  word gap). A LARGER gap = a column boundary → keep separate (handles the
  2-column notebook + the marketing nav). Line text = parts joined left→right.
- **Block grouping (conservative):** merge consecutive LINES into a paragraph
  when they share a left edge (|Δx0| < 1.0·H), have a small vertical gap
  (< 0.8·line-height), AND similar height (within 30%). This recombines a hero
  wrapped over 2 lines ("Storage that thinks for" + "you.") and body paragraphs
  WITHOUT merging a heading into body (different size) or across columns.

Reading order preserved (top→bottom, left→right). Never merges across a large
horizontal gap or a font-size change.

### 2. Skip non-translatable — `_should_skip_region(text, translated)`

A region is left UNTOUCHED (not inpainted, not re-rendered → original ink kept)
when:
- the translation equals the source (case/space-insensitive) — nothing to change;
- the text is a single token that looks like a brand/code identifier: CamelCase
  (`OpenCLIP`, `PyTorch`, `MinIO`), contains both letters and digits, or is an
  ALL-CAPS token ≤ 4 chars. Conservative — multi-word text is never skipped.

Skipped regions are excluded from BOTH the inpaint mask and the render loop.

### 3. Expansion-aware fitting — `_fit_and_draw(..., max_h)`

`max_h` = box height + available whitespace BELOW the box (distance to the
nearest region beneath it, or the image edge), capped at ~2.5·box_h. The fitter:
1. tries the original size (from `ink_h`), wrapping to the box WIDTH;
2. if the wrapped block is taller than box_h, lets it extend down to `max_h`
   (using real whitespace instead of cramming);
3. only if it still doesn't fit does it shrink (down to a 9px floor).
Result: text stays at a readable, source-matched size across languages; longer
translations flow into whitespace rather than shrinking to nothing or overlapping.

### Document pipeline (lighter, follow-up)

7. Inline **bold lead-in** terms are lost (everything flattened). Pragmatic fix:
   when a block's first sentence was bold in the source, bold the first sentence
   of the translation in the render. Approximate; lower priority than the image
   work. May be deferred.

## Testing

- Unit (pure functions, no GPU): `_merge_regions` line/block grouping +
  column/size guards; `_should_skip_region` cases; expansion fit picks original
  size when it fits and extends into max_h before shrinking.
- Integration (GPU): re-run in-image on the marketing screenshot + handwritten
  photo → es/zh/ar/he. Verify: consistent sizes, readable contrast, no ghosting,
  no overlap, logos/brands preserved, fragments translated coherently.

## Rollout

Additive within `translate_image.py`; the merged-region path replaces the raw
per-box loop in `_compose_png`/`_render_translations`. The embedded-figure path
(`_translate_embedded_image_sync`) and the standalone path share these helpers,
so both improve together.
