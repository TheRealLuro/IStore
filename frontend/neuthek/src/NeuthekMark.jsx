// neuthek brand mark — same constellation glyph as the marketing site
// favicon + WordMark. Five connected dots forming a "thought graph"
// with one larger active node. Inline SVG so the artwork stays
// pixel-aligned at every render size; no PNG / external asset drift.
//
// Sizes used in the app today:
//   - 14px in the gallery sidebar brand strip
//   - 16-22px in the auth screen brand block
//   - 32px+ for any future hero / splash usage
//
// The mark renders the dark rounded background by default so it reads
// on either a light or dark surface. Pass `bare` to skip the
// background frame (rare — used when the parent already paints a chip
// with the same shape).
import React from "react";

export default function NeuthekMark({ size = 22, bare = false }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-label="neuthek"
      style={{ display: "block", flexShrink: 0 }}
    >
      {!bare && (
        <>
          <rect x="0" y="0" width="32" height="32" rx="8" ry="8" fill="#0a0a0a" />
          <rect
            x="1.5" y="1.5" width="29" height="29" rx="6.5" ry="6.5"
            fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="1"
          />
        </>
      )}
      <g stroke="#ffffff" strokeWidth="0.7" strokeLinecap="round" opacity="0.55">
        <line x1="9" y1="22" x2="11" y2="9" />
        <line x1="11" y1="9" x2="22" y2="22" />
        <line x1="22" y1="22" x2="24" y2="10" />
        <line x1="9" y1="14" x2="17" y2="15" />
        <line x1="17" y1="15" x2="24" y2="10" />
      </g>
      <g fill="#ffffff">
        <circle cx="9" cy="22" r="1.4" />
        <circle cx="11" cy="9" r="1.4" />
        <circle cx="22" cy="22" r="1.4" />
        <circle cx="9" cy="14" r="1.0" />
        <circle cx="17" cy="15" r="1.0" />
        {/* Active node — slightly larger, anchors the graph */}
        <circle cx="24" cy="10" r="2.3" />
      </g>
    </svg>
  );
}
