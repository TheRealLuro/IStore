/* AutoReveal — universal Apple-style scroll entrance for EVERY page.
 *
 * <Reveal> handles deliberately-wrapped content (the Home hero, etc.).
 * This brings the same calm "content arrives as you scroll" motion to
 * every other page — Updates, Roadmap, Compare, FAQ, the legal pages —
 * WITHOUT hand-wrapping each block.
 *
 * On every route change it tags the structural content blocks of each
 * section with `.reveal-auto` (in a useLayoutEffect, i.e. before the
 * browser paints, so there's no flash-of-visible-then-hidden), gives
 * siblings a gentle capped stagger, and reveals them with a single
 * IntersectionObserver. Async-rendered content (e.g. /updates fetching
 * posts) is caught by a MutationObserver whose callback runs as a
 * microtask — before paint — so late content never flashes either.
 *
 * Skips anything that animates via its own system (hero, page-head,
 * <Reveal>, <Panel>) and honors prefers-reduced-motion.
 *
 * Renders nothing.
 */

import { useLayoutEffect } from "react";
import { useLocation } from "react-router-dom";

const TAG = "reveal-auto";
const VISIBLE = "is-visible";

// Content that should NOT be auto-tagged — it animates via its own
// system, or tagging it would double-animate.
function skip(el: Element): boolean {
  return (
    el.classList.contains(TAG) ||
    el.classList.contains("reveal") ||
    el.classList.contains("fade-in") ||
    el.classList.contains("panel") ||
    el.closest(".panel, .hero, .page-head, .reveal") !== null
  );
}

// Grids whose CHILDREN should stagger in individually, rather than the
// whole block rising as one unit.
const GRID =
  ".cards, .features-grid, .guarantees-strip, .checklist, .split, " +
  ".roadmap__list, .roadmap-stats, .kv, .compare-grid, " +
  ".updates__list, .tiles, .grid, .logos";

export default function AutoReveal() {
  const { pathname } = useLocation();

  useLayoutEffect(() => {
    if (typeof window === "undefined") return;
    const reduce =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || typeof IntersectionObserver === "undefined") return;

    const main = document.querySelector("main");
    if (!main) return;

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add(VISIBLE);
            io.unobserve(e.target);
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.06 },
    );

    const tagOne = (el: HTMLElement, idx: number) => {
      if (skip(el)) return;
      el.classList.add(TAG);
      el.style.setProperty("--reveal-delay", `${Math.min(idx, 6) * 55}ms`);
      io.observe(el);
    };

    const tagSection = (section: Element) => {
      const container =
        section.querySelector(":scope > .container") || section;
      const blocks = Array.from(container.children) as HTMLElement[];
      let i = 0;
      for (const block of blocks) {
        if (skip(block)) continue;
        if (block.matches(GRID)) {
          (Array.from(block.children) as HTMLElement[]).forEach((k, ki) =>
            tagOne(k, ki),
          );
        } else {
          tagOne(block, i);
          i += 1;
        }
      }
    };

    const scan = (root: ParentNode) => {
      root
        .querySelectorAll("section:not(.hero):not(.page-head)")
        .forEach(tagSection);
    };

    scan(main);

    // Catch content rendered after the first paint (data fetches, etc.).
    const mo = new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          const el = n as Element;
          const sec = el.closest("section:not(.hero):not(.page-head)");
          if (sec) tagSection(sec);
          else scan(el);
        }
      }
    });
    mo.observe(main, { childList: true, subtree: true });

    return () => {
      mo.disconnect();
      io.disconnect();
    };
  }, [pathname]);

  return null;
}
