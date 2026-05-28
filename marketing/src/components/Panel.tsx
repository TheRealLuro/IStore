/* Panel — a clean, animated product-UI "render" card.
 *
 * Replaces the old terminal/`<pre>` "code-card" demos on the Features
 * page with something that reads as a polished app surface, not a
 * scary code dump: a light frosted card, a refined window chrome
 * (small dots + title), and content rows that cascade in when the
 * panel scrolls into view.
 *
 * The shell uses its own small IntersectionObserver to add `.is-visible`,
 * which the stylesheet keys off to stagger `.panel-row` children. Honors
 * prefers-reduced-motion (CSS zeroes the row animation) and degrades to
 * instantly-visible when IntersectionObserver is unavailable.
 *
 * The row primitives (MeterRow / FlowNode / SpecRow / StepRow) are thin
 * presentational helpers so each Features card stays declarative.
 */

import { useEffect, useRef, type ReactNode } from "react";

interface PanelProps {
  title: string;
  /** Small tag shown at the right of the chrome (e.g. "search"). */
  tag?: string;
  children: ReactNode;
  className?: string;
}

export default function Panel({ title, tag, children, className = "" }: PanelProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduce =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || typeof IntersectionObserver === "undefined") {
      el.classList.add("is-visible");
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
            obs.unobserve(e.target);
          }
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.12 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <div ref={ref} className={`panel ${className}`.trim()}>
      <div className="panel__chrome">
        <span className="panel__dots" aria-hidden="true">
          <span /><span /><span />
        </span>
        <span className="panel__title">{title}</span>
        {tag ? <span className="panel__tag">{tag}</span> : null}
      </div>
      <div className="panel__body">{children}</div>
    </div>
  );
}

/* ---------- row primitives ---------- */

/** Animated 0–100 meter — label, bar that fills on reveal, score. */
export function MeterRow({
  label,
  value,
  note,
  highlight = false,
}: {
  label: string;
  value: number;
  note?: string;
  highlight?: boolean;
}) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className={`panel-row meter${highlight ? " meter--top" : ""}`}>
      <span className="meter__label">{label}</span>
      <span className="meter__track">
        <span className="meter__fill" style={{ ["--meter" as string]: `${pct}%` }} />
      </span>
      <span className="meter__value">{value}</span>
      {note ? <span className="meter__note">{note}</span> : null}
    </div>
  );
}

/** A node in a top-to-bottom flow (query → step → result). */
export function FlowNode({
  kind,
  children,
}: {
  kind: "query" | "step" | "result";
  children: ReactNode;
}) {
  return <div className={`panel-row flow-node flow-node--${kind}`}>{children}</div>;
}

/** Small downward connector between flow nodes. */
export function FlowArrow() {
  return (
    <div className="panel-row flow-arrow" aria-hidden="true">
      <svg width="16" height="20" viewBox="0 0 16 20" fill="none">
        <path d="M8 1v14m0 0 5-5m-5 5-5-5" stroke="currentColor" strokeWidth="1.5"
              strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

/** A labelled chip (result tokens, synonyms). */
export function Chip({ children }: { children: ReactNode }) {
  return <span className="panel-chip">{children}</span>;
}

/** A hairline divider with an optional centered label — separates two
 *  sub-demos inside one panel so they don't read as one cramped block. */
export function PanelDivider({ label }: { label?: string }) {
  return (
    <div className="panel-row panel-divider" aria-hidden="true">
      <span className="panel-divider__line" />
      {label ? <span className="panel-divider__label">{label}</span> : null}
      <span className="panel-divider__line" />
    </div>
  );
}

/** label → value spec row with a leading glyph. */
export function SpecRow({
  glyph,
  label,
  value,
}: {
  glyph?: ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="panel-row spec-row">
      {glyph ? <span className="spec-row__glyph" aria-hidden="true">{glyph}</span> : null}
      <span className="spec-row__label">{label}</span>
      <span className="spec-row__value">{value}</span>
    </div>
  );
}

/** Numbered step row. */
export function StepRow({ n, children }: { n: number; children: ReactNode }) {
  return (
    <div className="panel-row step-row">
      <span className="step-row__n">{n}</span>
      <span className="step-row__text">{children}</span>
    </div>
  );
}
