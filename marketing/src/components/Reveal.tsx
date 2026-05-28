/* Reveal — Apple-style scroll-triggered entrance animation.
 *
 * Wraps any content and fades + slides it up the moment it scrolls
 * into view, using a single shared IntersectionObserver (cheap, no
 * per-element observer churn). This is the signature feel of Apple's
 * marketing pages: content isn't all painted at once, it arrives as
 * you scroll, with a calm expo-out easing.
 *
 * Design choices:
 *   - One module-level observer, ref-counted, so a page with 40
 *     reveal elements still only runs ONE observer.
 *   - `once` (default true): unobserve after the first reveal so we
 *     don't re-animate on scroll-up — matches Apple's behavior and
 *     avoids distracting re-triggers.
 *   - `delay` enables staggered groups (card 1, 2, 3 cascade in) via
 *     a CSS custom property the stylesheet reads.
 *   - prefers-reduced-motion: the CSS zeroes the transform + duration,
 *     so this degrades to "just visible" with no motion. We ALSO add
 *     `is-visible` immediately in that case so nothing depends on the
 *     observer firing.
 *   - SSR/no-IO fallback: if IntersectionObserver is missing, we mark
 *     everything visible on mount so content never gets stuck hidden.
 */

import {
  useEffect,
  useRef,
  type ElementType,
  type ReactNode,
} from "react";

let _observer: IntersectionObserver | null = null;
let _refCount = 0;

function getObserver(): IntersectionObserver | null {
  if (typeof IntersectionObserver === "undefined") return null;
  if (_observer) return _observer;
  _observer = new IntersectionObserver(
    (entries, obs) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          // `once` semantics: stop watching after first reveal.
          if ((entry.target as HTMLElement).dataset.revealOnce !== "false") {
            obs.unobserve(entry.target);
          }
        } else if (
          (entry.target as HTMLElement).dataset.revealOnce === "false"
        ) {
          entry.target.classList.remove("is-visible");
        }
      }
    },
    {
      // Fire a touch BEFORE the element is fully in view so the motion
      // reads as "already arriving" rather than "popping in late".
      rootMargin: "0px 0px -10% 0px",
      threshold: 0.08,
    },
  );
  return _observer;
}

const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

interface RevealProps {
  children: ReactNode;
  /** Render as this element. Default: div. */
  as?: ElementType;
  /** Stagger delay in ms (0–600 sensible). Drives --reveal-delay. */
  delay?: number;
  /** Re-animate on scroll-out/in. Default false (animate once). */
  repeat?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

export default function Reveal({
  children,
  as: Tag = "div",
  delay = 0,
  repeat = false,
  className = "",
  style,
}: RevealProps) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // No motion wanted, or no IO available → just show it.
    if (prefersReducedMotion()) {
      el.classList.add("is-visible");
      return;
    }
    const obs = getObserver();
    if (!obs) {
      el.classList.add("is-visible");
      return;
    }

    el.dataset.revealOnce = repeat ? "false" : "true";
    _refCount += 1;
    obs.observe(el);

    return () => {
      obs.unobserve(el);
      _refCount -= 1;
      if (_refCount <= 0 && _observer) {
        _observer.disconnect();
        _observer = null;
        _refCount = 0;
      }
    };
  }, [repeat]);

  return (
    <Tag
      ref={ref as React.Ref<HTMLElement>}
      className={`reveal ${className}`.trim()}
      style={{ ...(style || {}), ["--reveal-delay" as string]: `${delay}ms` }}
    >
      {children}
    </Tag>
  );
}
