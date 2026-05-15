/* Constellation Glyph — neuthek's brand mark.
 *
 * The design is six points arranged as an asymmetric "memory
 * graph," connected by thin lines. One node (top-right) is larger
 * and pulses — the "active memory" being recalled. Lines breathe
 * their opacity so the whole graph feels like it's thinking.
 *
 * Authored as inline SVG so the same artwork drives the nav mark,
 * the large hero mark, the favicon, and the OG cover — no PNG
 * exports drifting out of sync. */

type Props = { large?: boolean; bare?: boolean };

export default function WordMark({ large = false, bare = false }: Props) {
  const px = large ? 32 : 22;
  return (
    <span
      className={`wordmark${large ? " wordmark--lg" : ""}`}
      aria-label={bare ? undefined : "neuthek"}
      role={bare ? "img" : undefined}
    >
      <span className="wordmark__mark" aria-hidden style={{ width: px, height: px }}>
        <svg viewBox="0 0 32 32" width={px} height={px}>
          {/* Frame */}
          <rect x="0" y="0" width="32" height="32" rx="8" ry="8" fill="#0a0a0a"/>

          {/* Subtle inner halo so the glyph reads as a "screen" */}
          <rect x="1.5" y="1.5" width="29" height="29" rx="6.5" ry="6.5"
                fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="1"/>

          {/* Connection lines — the breathing "thought graph" */}
          <g
            stroke="#ffffff"
            strokeWidth="0.7"
            strokeLinecap="round"
            opacity="0.55"
            className="wordmark__lines"
          >
            <line x1="9"  y1="22" x2="11" y2="9" />
            <line x1="11" y1="9"  x2="22" y2="22" />
            <line x1="22" y1="22" x2="24" y2="10" />
            <line x1="9"  y1="14" x2="17" y2="15" />
            <line x1="17" y1="15" x2="24" y2="10" />
          </g>

          {/* Constellation points */}
          <g fill="#ffffff" className="wordmark__dots">
            <circle cx="9"  cy="22" r="1.4" />
            <circle cx="11" cy="9"  r="1.4" />
            <circle cx="22" cy="22" r="1.4" />
            <circle cx="9"  cy="14" r="1.0" />
            <circle cx="17" cy="15" r="1.0" />
            {/* Active node — pulses */}
            <circle cx="24" cy="10" r="2.1" className="wordmark__dot--active" />
            <circle cx="24" cy="10" r="2.1" fill="none" stroke="#ffffff"
                    strokeWidth="0.6" className="wordmark__halo"/>
          </g>
        </svg>
      </span>
      {!bare && <span>neuthek</span>}
    </span>
  );
}
