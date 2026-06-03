// NeuthekLoader — the brand's loading animation.
//
// This is the same neuthek mark + circle motif from the launch film
// (marketing/public/film.html, beat 5 "the hub"): the angular neuthek
// glyph sits centered inside a ring, the mark's strokes draw themselves
// on in a loop, its nodes pulse, and a dashed ring sweeps around the
// rim. The film hand-animates this once with GSAP; here it's a pure,
// continuously-looping CSS/SVG animation so it can stand in for any
// "…loading" moment in the app without a JS animation runtime.
//
// Why it's cheap: every animation is transform / opacity /
// stroke-dashoffset only (all compositor-friendly), no layout or paint
// thrash, no new dependencies. Colors come from the app's CSS tokens so
// it inverts correctly in light/dark. `prefers-reduced-motion` collapses
// the whole thing to a calm, fully-drawn static mark with a soft fade
// (handled in styles-loader.css) — no spinning, no draw loop.
//
// The mark geometry is copied verbatim from the film so the proportions
// are identical:
//   path  d="M48 20 L44 44 L22 18 L18 44 M48 20 L34 30 L18 28"
//   nodes [18,44] [22,18] [44,44] [18,28] [34,30] [48,20]  (48,20 = core)
// The glyph's natural center is ~(33,31); the film centers it with
// translate(-33 -31). Here the SVG viewBox is sized so the mark sits
// centered inside the ring.
import React from "react";

// Logo node coordinates (cx, cy, r) — lifted straight from the film's
// `.wlogo` assembly. The 48,20 node is the larger "core" the mark draws
// out from; the rest are the limb endpoints / joints.
const NODES = [
  [18, 44, 2.9],
  [22, 18, 2.9],
  [44, 44, 2.9],
  [18, 28, 2.4],
  [34, 30, 2.4],
  [48, 20, 3.7], // core
];

// Fun, crafty loading lines — they type on one at a time and swap every
// couple seconds, the way Claude Code narrates while it works. Kept short
// so they sit on one line under the mark.
const CRAFTY_PHRASES = [
  "Untangling memories",
  "Connecting the dots",
  "Dusting off the pixels",
  "Reticulating splines",
  "Gathering your moments",
  "Waking the neurons",
  "Threading the archive",
  "Polishing thumbnails",
  "Summoning your library",
  "Warming up the lens",
  "Aligning constellations",
  "Shuffling the shoebox",
  "Recalling where things live",
  "Brewing your cloud",
];

// Scribe / translator-themed lines for the document & image translation
// loaders — small + funny, in the spirit of an old monk hunched over a
// manuscript. These fade in/out (one at a time, ~1.6s each) under the mark
// while a translation streams. Exported so the translate views share one
// list. (The first batch is the requested set; the rest keep the same
// whimsical scribe energy.)
export const SCRIBE_PHRASES = [
  "Studying",
  "Scribing",
  "Scribbling",
  "Doodling",
  "Translating",
  "Uncovering old runes",
  "Talking with ancestors",
  "Consulting the elders",
  "Decoding hieroglyphs",
  "Whispering to the words",
  "Summoning a polyglot",
  "Polishing the vowels",
  "Sharpening the quill",
  "Dusting the dictionary",
  "Untangling the grammar",
  "Befriending a thesaurus",
  "Negotiating with idioms",
  "Bribing a polyglot",
  "Consulting the oracle",
  "Teaching the robots Spanish",
  "Dusting off the dictionary",
  "Arguing about commas",
  "Reading between the lines",
  "Waking the translator gnome",
  "Cross-checking with the stars",
  "Negotiating with verbs",
  "Translating the vibes",
  "Asking the words nicely",
  "Rolling dice on idioms",
  "Greasing the gears",
  "Conjugating in the dark",
  "Wrangling loose syllables",
  "Pestering the muses",
  "Looking up the hard words",
  "Checking the footnotes",
  "Matching the fonts",
  "Double-checking the idioms",
  "Asking a native speaker",
  "Flipping through the thesaurus",
  "Wrestling with the syntax",
  "Translating the sarcasm",
  "Preserving the puns",
  "Re-reading for tone",
  "Keeping the accents straight",
  "Politely declining false friends",
  "Untwisting the tongue-twisters",
  "Finding le mot juste",
  "Minding the gendered nouns",
  "Smoothing the phrasing",
  "Honoring the original",
  "Counting the syllables",
  "Proofreading it twice",
  "Borrowing a few loanwords",
  "Consulting the grammar police",
  "Picking the perfect synonym",
  "Aligning the paragraphs",
  "Whispering to the verbs",
];

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

// Types out a phrase character-by-character, holds, then advances to the
// next — looping forever (~2s per line). Under reduced-motion it shows a
// single calm line with no typing. Timers are cleaned up on unmount /
// phrase change. aria-hidden so screen readers get the parent's "Loading"
// label once, not every keystroke.
function CyclingText({ phrases }) {
  const reduce = React.useMemo(prefersReducedMotion, []);
  const [i, setI] = React.useState(0);
  const [text, setText] = React.useState(reduce ? phrases[0] : "");

  React.useEffect(() => {
    if (reduce) return undefined;
    const phrase = phrases[i % phrases.length];
    let char = 0;
    let typeTimer;
    let holdTimer;
    const tick = () => {
      char += 1;
      setText(phrase.slice(0, char));
      if (char < phrase.length) {
        typeTimer = setTimeout(tick, 46);
      } else {
        holdTimer = setTimeout(() => setI((v) => (v + 1) % phrases.length), 1150);
      }
    };
    typeTimer = setTimeout(tick, 90);
    return () => {
      clearTimeout(typeTimer);
      clearTimeout(holdTimer);
    };
  }, [i, reduce, phrases]);

  return (
    <div className="nk-loader__label nk-loader__type" aria-hidden="true">
      {text}
      <span
        className={`nk-loader__caret${reduce ? " nk-loader__caret--still" : ""}`}
      />
    </div>
  );
}

// Gently FADES through a set of phrases, one at a time, swapping every
// `interval` ms (default ~1.6s) with a soft cross-fade + an animated
// ellipsis. Used by the translate loaders ("Scribing…", "Decoding
// hieroglyphs…"). Under reduced-motion it parks on the first phrase with a
// static ellipsis and never cycles. The single interval is cleaned up on
// unmount / when the inputs change. aria-hidden — the surrounding loader
// already carries a role="status" label for assistive tech.
//
// `clamp` controls how a long (translated) phrase is constrained so it never
// overflows its container in any language:
//   "line" → the slim top strip: the phrase truncates to ONE line with an
//            ellipsis (the animated dots are dropped — the ellipsis already
//            signals "more"), flex-shrinking so it never collides with the
//            i/n counter beside it.
//   "wrap" → the centered warming card: the phrase clamps to at most TWO
//            lines with an ellipsis (the dots ride along on the last line),
//            wrapping/breaking long words so width can't blow out the card.
function FadingText({ phrases, interval = 1600, clamp = "wrap" }) {
  const reduce = React.useMemo(prefersReducedMotion, []);
  const [i, setI] = React.useState(0);
  // Defensive: a caller may thread a null/undefined/empty phrase list (e.g. a
  // localized-phrases hook that hasn't resolved, or was passed `null` so the
  // `= SCRIBE_PHRASES` default param — which only fires for `undefined` —
  // didn't apply). Coalesce to a non-empty list so neither `.length` nor the
  // indexing below can throw and blank-screen the loader.
  const list =
    Array.isArray(phrases) && phrases.length > 0 ? phrases : SCRIBE_PHRASES;

  React.useEffect(() => {
    if (reduce || list.length <= 1) return undefined;
    const id = setInterval(
      () => setI((v) => (v + 1) % list.length),
      interval,
    );
    return () => clearInterval(id);
  }, [reduce, list, interval]);

  // Final guard: even with a non-empty list, an individual entry could be an
  // empty string (e.g. a malformed localized payload). Fall back to the first
  // built-in scribe word so the loader ALWAYS shows a cycling word, never a
  // bare ellipsis.
  const phrase = list[i % list.length] || SCRIBE_PHRASES[0];
  const line = clamp === "line";
  return (
    <div
      className={`nk-loader__label nk-loader__fade nk-loader__fade--${line ? "line" : "wrap"}`}
      aria-hidden="true"
    >
      {/* `key` re-mounts the span each swap so the fade-in keyframe replays.
          On the slim strip the word itself truncates with an ellipsis, so we
          drop the trailing animated dots to avoid them colliding with the
          ellipsis / counter; the centered card keeps the dots. */}
      <span
        key={i}
        className="nk-loader__fade-word"
        /* Inline style guarantees the word is visible regardless of any CSS
           (the `-webkit-box`/line-clamp collapse that blanked it before, or a
           stale cached stylesheet). Inline wins over the rule cascade. */
        style={{ display: "inline-block", opacity: 1, visibility: "visible" }}
      >
        {phrase}
      </span>
      {!line && (
        <span className="nk-loader__ellipsis">
          <span>.</span><span>.</span><span>.</span>
        </span>
      )}
    </div>
  );
}

// Split the accumulated stream text (blocks joined with blank lines) into
// readable paragraphs. The stream hooks join blocks with "\n\n", so we split on
// blank lines and trim; an empty input yields []. Each paragraph keeps its
// intra-paragraph single newlines (rendered with white-space: pre-wrap) so a
// hard-wrapped source line still reads naturally.
function splitParagraphs(text) {
  return String(text || "")
    .split(/\n{2,}/)
    .map((p) => p.replace(/[ \t]+$/gm, "").trim())
    .filter(Boolean);
}

// Render one TYPED live block as the matching document element so the streaming
// page looks like the real document FORMAT (mirrors image-tool-rail's DocBlock,
// but with the live-render `.nk-xlate__live-*` classes):
//   title → large bold document title
//   h1    → section heading
//   h2    → semibold sub-heading
//   li    → bulleted list item (bullet glyph + hanging indent)
//   p / unknown → body paragraph
// Empty blocks render nothing. White-space is preserved so intra-block line
// breaks survive while long words still wrap.
function LiveBlock({ kind, text }) {
  const body = (text || "").trim();
  if (!body) return null;
  switch (kind) {
    case "title":
      return <h1 className="nk-xlate__live-title">{body}</h1>;
    case "h1":
      return <h2 className="nk-xlate__live-h1">{body}</h2>;
    case "h2":
      return <h3 className="nk-xlate__live-h2">{body}</h3>;
    case "li":
      return (
        <div className="nk-xlate__live-li">
          <span className="nk-xlate__live-bullet" aria-hidden="true">•</span>
          <span className="nk-xlate__live-li-text">{body}</span>
        </div>
      );
    case "p":
    default:
      return <p className="nk-xlate__live-p">{body}</p>;
  }
}

/**
 * DocTranslateLoader — the document / image translation progress UI.
 *
 * TWO surfaces, composed:
 *   1. A READABLE, FORMATTED LIVE RENDER of the translation as it streams in.
 *      While translating we HIDE the original document and show a clean white
 *      page that fills in top-to-bottom, block by block — like watching the
 *      document turn into the new language. Real document styling: white page,
 *      readable paragraph spacing, NORMAL opacity, good typography. When the
 *      caller passes typed `behindBlocks` ([{text, kind}]) each block is STYLED
 *      to its structural role (title / h1 / h2 / li / p) so the streaming page
 *      matches the real document FORMAT instead of a flat wall of paragraphs.
 *      Each block fades in gently. The page does NOT auto-scroll — it starts at
 *      the top and the user scrolls manually (it never jumps as blocks arrive).
 *   2. A COMPACT branded loader that does NOT cover the live text — a slim
 *      progress STRIP across the top of the page (small neuthek mark + a
 *      cycling scribe phrase + a real `i / n` progress bar). Before any text
 *      has streamed (model warming / first chunk) we instead center the full
 *      branded CARD so there's a clear "working" beat, then hand off to the
 *      live render the moment the first block arrives.
 *
 * The parent reveals the EXACT translated result (the real translated PDF /
 * the rendered PNG) once `done` — this component only owns the in-progress
 * "becoming" state.
 *
 * Progress:
 *   - When `total > 0` the bar fills `done / total * 100%` and a "done / total"
 *     count shows (DETERMINATE).
 *   - Before the first progress arrives the bar runs an INDETERMINATE sweep and
 *     the count reads "warming up".
 *
 * @param {{done:number,total:number}} progress  live stream progress
 * @param {string=} pair   optional "English → Spanish" language pair caption
 * @param {string[]=} phrases  cycling status lines (defaults to SCRIBE_PHRASES)
 * @param {number=} size   logo diameter for the centered warming card (def 72)
 * @param {string=} className  extra classes on the outer wrapper
 * @param {string=} behindText  the translated text streamed in so far (blocks
 *   joined with blank lines). When present (and no `behindBlocks`) it's rendered
 *   as the readable live document render (white page, normal opacity) that
 *   REPLACES the original while translating, split into plain paragraphs; the
 *   branded loader shrinks to the slim top strip. When empty, the centered
 *   warming card shows instead. Kept for callers without structural info.
 * @param {{text:string,kind:("title"|"h1"|"h2"|"p"|"li")}[]=} behindBlocks  the
 *   translated blocks streamed in so far, each with its structural `kind`. When
 *   present this is PREFERRED over `behindText`: each block is rendered STYLED to
 *   its role (title / h1 / h2 / li / p) so the live page matches the document
 *   FORMAT. Falsy/empty → falls back to the `behindText` paragraph split.
 * @param {(string|undefined)[]=} pageImages  STREAMED EXACT PAGE IMAGES — object
 *   URLs of the REAL rendered document pages (the translation applied so far,
 *   exact layout), indexed by 0-based page. When present (and at least one URL
 *   is set) this is PREFERRED over `behindBlocks`/`behindText`: the live render
 *   shows the actual document pages (progressively translating) in a scrollable
 *   area, with the slim branded strip on top — instead of an HTML reflow
 *   approximation. Holes (undefined entries for pages not yet streamed) are
 *   skipped. The caller OWNS + revokes these URLs. Empty/falsy → falls back to
 *   the block/text live render (or the warming card when nothing has streamed).
 * @param {boolean=} liveRender  opt into the readable-replace render (default
 *   true). Pass false to keep the old centered-card-only behavior.
 * @param {("full"|"strip")=} variant  "full" (default) renders the whole
 *   surface (warming card OR live-replace page). "strip" renders ONLY the slim
 *   branded progress strip as a normal block — for callers that supply their
 *   OWN readable render below it (e.g. the structured-doc view) and just want
 *   the compact branded loader on top.
 */
export function DocTranslateLoader({
  progress,
  pair,
  phrases = SCRIBE_PHRASES,
  size = 72,
  className = "",
  behindText = "",
  behindBlocks = null,
  pageImages = null,
  liveRender = true,
  variant = "full",
}) {
  const total = progress?.total || 0;
  const done = progress?.done || 0;
  const determinate = total > 0;
  const pct = determinate
    ? Math.max(0, Math.min(100, Math.round((done / total) * 100)))
    : 0;

  const liveEligible = liveRender && variant === "full";

  // STREAMED EXACT PAGE IMAGES — the real rendered document pages translated so
  // far. Keep only the non-empty URLs (skip holes for pages not yet streamed),
  // carrying each page's index for a stable React key. When any exist this is
  // the PREFERRED live render (the actual document, exact layout), shown ahead
  // of the block/text reflow.
  const livePages = React.useMemo(() => {
    if (!liveEligible || !Array.isArray(pageImages)) return [];
    return pageImages
      .map((url, page) => ({ url, page }))
      .filter((p) => typeof p.url === "string" && p.url);
  }, [pageImages, liveEligible]);

  // Prefer the TYPED blocks ([{text, kind}]) so the live page is FORMATTED to
  // the document structure. Keep only blocks with real text; an absent/unknown
  // kind falls back to a body paragraph.
  const liveBlocks = React.useMemo(() => {
    if (!liveEligible || !Array.isArray(behindBlocks)) return [];
    return behindBlocks
      .filter((b) => b && typeof b.text === "string" && b.text.trim())
      .map((b) => ({ text: b.text, kind: b.kind || "p" }));
  }, [behindBlocks, liveEligible]);

  // Fallback path: when no typed blocks were supplied, split the joined text
  // into plain paragraphs (legacy behaviour for callers without structure).
  const paragraphs = React.useMemo(
    () => (liveEligible && liveBlocks.length === 0 ? splitParagraphs(behindText) : []),
    [behindText, liveEligible, liveBlocks.length],
  );

  const hasPages = livePages.length > 0;
  const hasLive = liveEligible && (hasPages || liveBlocks.length > 0 || paragraphs.length > 0);

  // NOTE: the live render intentionally does NOT auto-scroll. It starts at the
  // top and the user scrolls manually; it must not jump as new blocks arrive.

  // The slim branded strip — compact mark + cycling phrase + a real progress
  // bar. Shared by the live-replace mode (where it pins above the live page)
  // and the standalone `strip` variant (a normal block above a caller's own
  // render).
  const strip = (
    <div className="nk-xlate__strip">
      <div className="nk-xlate__strip-brand">
        <NeuthekLoader size={26} className="nk-xlate__strip-mark" />
        <FadingText phrases={phrases} clamp="line" />
      </div>
      <div className="nk-xlate__strip-progress">
        {determinate ? (
          <span className="nk-xlate__count">{done} / {total}</span>
        ) : (
          <span className="nk-xlate__count nk-xlate__count--warming">
            warming up
          </span>
        )}
        {pair && <span className="nk-xlate__pair">{pair}</span>}
      </div>
      <div
        className={
          "nk-xlate__strip-bar" +
          (determinate ? "" : " nk-xlate__bar--indeterminate")
        }
      >
        <div
          className="nk-xlate__bar-fill"
          style={determinate ? { width: `${pct}%` } : undefined}
        />
      </div>
    </div>
  );

  // ── Strip-only variant: just the branded bar as a normal block. ──
  if (variant === "strip") {
    return (
      <div
        className={`nk-xlate__strip-host${className ? " " + className : ""}`}
        role="status"
        aria-live="polite"
        aria-label={
          determinate ? `Translating ${done} of ${total}` : "Translating"
        }
      >
        {strip}
      </div>
    );
  }

  // ── Live-replace mode: the readable formatted render fills the surface, with
  // the branded loader compacted into a slim progress strip at the top. ──
  if (hasLive) {
    return (
      <div
        className={`nk-xlate nk-xlate--live${className ? " " + className : ""}`}
        role="status"
        aria-live="polite"
        aria-label={
          determinate ? `Translating ${done} of ${total}` : "Translating"
        }
      >
        {/* Slim top strip — compact mark + cycling phrase + a real progress
            bar. Sits ABOVE the live page; does not cover it. */}
        {strip}

        {/* The live render. PREFERRED: the STREAMED EXACT PAGE IMAGES — the real
            rendered document pages (exact layout) with the translation applied
            so far, shown as a stack of <img> in a scrollable area that
            progressively translates. The latest snapshot per page wins (the
            caller swaps the src), so each page's slot updates in place. Falls
            back to the readable formatted block/paragraph render when no page
            image has streamed. Does NOT auto-scroll — it starts at the top and
            the user scrolls manually (it must not jump as new pages arrive). */}
        <div className="nk-xlate__live-scroll">
          {hasPages ? (
            <div className="nk-xlate__pages">
              {livePages.map((p) => (
                // Key by page index so a page's slot is STABLE across snapshots:
                // a newer, more-translated render of page k swaps the same <img>
                // in place rather than remounting (no flash, no fade replay).
                <img
                  key={p.page}
                  src={p.url}
                  className="nk-xlate__page-img"
                  alt={`Page ${p.page + 1}`}
                  draggable="false"
                />
              ))}
            </div>
          ) : (
            <article className="nk-xlate__live-page nk-xlate__live-page--structured">
              {liveBlocks.length > 0
                ? liveBlocks.map((b, i) => (
                    // Index key is stable here: blocks only ever append (the last
                    // one may grow), so earlier blocks keep their identity and
                    // don't re-trigger the fade — only the freshly-added one does.
                    <LiveBlock key={i} kind={b.kind} text={b.text} />
                  ))
                : paragraphs.map((p, i) => (
                    <p key={i} className="nk-xlate__live-p">
                      {p}
                    </p>
                  ))}
            </article>
          )}
        </div>
      </div>
    );
  }

  // ── Warming mode: no text yet — the centered branded card carries the
  // "working" signal until the first block lands and we swap to live-replace. ──
  return (
    <div
      className={`nk-xlate${className ? " " + className : ""}`}
      role="status"
      aria-live="polite"
      aria-label={
        determinate ? `Translating ${done} of ${total}` : "Translating"
      }
    >
      <div className="nk-xlate__card">
        {/* The branded logo-in-circle loader (no built-in label — we render
            our own cycling scribe line below it). */}
        <NeuthekLoader size={size} />
        <FadingText phrases={phrases} />
        <div
          className={
            "nk-xlate__bar" +
            (determinate ? "" : " nk-xlate__bar--indeterminate")
          }
        >
          <div
            className="nk-xlate__bar-fill"
            style={determinate ? { width: `${pct}%` } : undefined}
          />
        </div>
        <div className="nk-xlate__meta">
          {determinate ? (
            <span className="nk-xlate__count">{done} / {total}</span>
          ) : (
            <span className="nk-xlate__count nk-xlate__count--warming">
              warming up
            </span>
          )}
          {pair && <span className="nk-xlate__pair">{pair}</span>}
        </div>
      </div>
    </div>
  );
}

/**
 * NeuthekLoader
 * @param {number}  size   px diameter of the loader (default 64)
 * @param {string=} label  optional caption rendered under the mark
 * @param {boolean=} cycle  when true, cycle the crafty typewriter lines
 *                          under the mark instead of a static label
 * @param {boolean=} full   when true, fills its container and centers
 *                          itself (use for the full-screen initial load)
 * @param {string=} className extra classes
 */
export function NeuthekLoader({ size = 64, label, cycle = false, full = false, className = "", ...rest }) {
  // The art is authored in a 0..66 / 0..62 space (the mark plus padding
  // for the ring + orbit). Keeping the SVG square at 66 and centering the
  // ~(33,31) glyph keeps the film's exact proportions.
  return (
    <div
      className={`nk-loader${full ? " nk-loader--full" : ""}${className ? " " + className : ""}`}
      role="status"
      aria-live="polite"
      aria-label={label || "Loading"}
      {...rest}
    >
      <svg
        className="nk-loader__svg"
        width={size}
        height={size}
        viewBox="0 0 66 66"
        fill="none"
        aria-hidden="true"
      >
        {/* Static rim — a solid ring snug AROUND the mark (film's `.wcirc`).
            Inner radius (25) so it frames the glyph tightly and reads as a
            distinct solid line, with the dashed orbit a clear step outside. */}
        <circle className="nk-loader__rim" cx="33" cy="33" r="25" />
        {/* Orbit — a dashed ring OUTSIDE the solid rim (r 31 vs 25) that
            rotates around it (film's `.worbit`). transform: rotate only. */}
        <circle className="nk-loader__orbit" cx="33" cy="33" r="31" />
        {/* The neuthek mark. The OUTER group carries the static centering
            translate (the glyph's natural center is ~(33,31); nudge +2 in
            y so it lands at the SVG center 33,33). The INNER group owns the
            breathe animation, so the CSS `transform` never clobbers the
            translate (animating transform on the translated element would
            drop the translate). Stroke draws on in a loop via
            stroke-dashoffset; nodes pulse via opacity/transform. */}
        <g transform="translate(0 2)">
          <g className="nk-loader__mark">
            <path
              className="nk-loader__stroke"
              d="M48 20 L44 44 L22 18 L18 44 M48 20 L34 30 L18 28"
            />
            {NODES.map(([cx, cy, r], i) => (
              <circle
                key={i}
                className={`nk-loader__node${r >= 3.5 ? " nk-loader__node--core" : ""}`}
                cx={cx}
                cy={cy}
                r={r}
              />
            ))}
          </g>
        </g>
      </svg>
      {cycle
        ? <CyclingText phrases={CRAFTY_PHRASES} />
        : label ? <div className="nk-loader__label">{label}</div> : null}
    </div>
  );
}

export default NeuthekLoader;
