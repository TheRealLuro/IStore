/* B&W tech logos for the carousel.
 *
 * Each is an inline SVG rendered in `currentColor` so the carousel's
 * `--ink-2` text color flows through and we get a uniform monochrome
 * treatment across every brand. Where the project has a distinctive
 * official logo (React, TypeScript, Docker, etc.) we authentically
 * render it monochrome; where it doesn't (pgvector, OpenCLIP) we use
 * a clean geometric icon representing what the tool does.
 *
 * Use is nominative: these marks identify the technologies neuthek is
 * built on, not partnerships or endorsements. Brand names and marks
 * belong to their respective owners — see /terms. */

import type { CSSProperties } from "react";

type IconProps = { size?: number; style?: CSSProperties };

const COMMON = (size: number): CSSProperties => ({
  width: size, height: size, display: "block", fill: "currentColor",
});

// ----- React (atomic orbit) -----
export function ReactLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="-12 -12 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      <circle cx="0" cy="0" r="1.6" />
      <g fill="none" stroke="currentColor" strokeWidth="1">
        <ellipse rx="10" ry="4.2" />
        <ellipse rx="10" ry="4.2" transform="rotate(60)" />
        <ellipse rx="10" ry="4.2" transform="rotate(120)" />
      </g>
    </svg>
  );
}

// ----- TypeScript (TS square wordmark) -----
export function TypeScriptLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      <rect x="0" y="0" width="24" height="24" rx="2.4" fill="currentColor"/>
      <text x="12" y="17" textAnchor="middle"
            fontFamily="Geist, system-ui, sans-serif"
            fontWeight="700" fontSize="11"
            fill="var(--surface)">TS</text>
    </svg>
  );
}

// ----- Vite (lightning bolt) -----
export function ViteLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      <path d="M14.5 2 L7 13 L11 13 L9 22 L17 9 L13 9 Z" />
    </svg>
  );
}

// ----- PostgreSQL (Slonik silhouette) -----
export function PostgresLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      {/* Simplified elephant head: dome + trunk + ear */}
      <path d="M5 11
               C 5 6.5, 9 4, 12 4
               C 16 4, 19 6.5, 19 11
               L 19 14
               C 19 16, 18 17, 17 17
               L 17 19 L 15.5 19 L 15.5 17.6
               C 14.5 17.9, 13.5 18, 12.5 18
               L 12.5 19 L 11 19 L 11 17.8
               C 10 17.4, 9.2 16.8, 9 16
               L 9 19 L 7.5 19 L 7.5 14
               C 6 13.5, 5 12.5, 5 11 Z" />
      {/* Eye */}
      <circle cx="14" cy="10" r="0.7" fill="var(--surface)"/>
    </svg>
  );
}

// ----- pgvector (3 vectors radiating from a point) -----
export function PgvectorLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" fill="none">
      {/* Origin point */}
      <circle cx="6" cy="18" r="1.4" fill="currentColor" stroke="none" />
      {/* Three vector arrows */}
      <line x1="6" y1="18" x2="20" y2="18" />
      <line x1="6" y1="18" x2="18" y2="8" />
      <line x1="6" y1="18" x2="9" y2="4" />
      {/* Arrowheads */}
      <path d="M20 18 L17 16 M20 18 L17 20"  />
      <path d="M18 8 L14.5 8 M18 8 L17 11.5" />
      <path d="M9 4 L7 6 M9 4 L11 5.5"       />
    </svg>
  );
}

// ----- MinIO (stylized M formed by upward strokes) -----
export function MinioLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" fill="none">
      {/* Five upward strokes forming an M */}
      <path d="M3 19 L 6 5" />
      <path d="M9 19 L 9 9" />
      <path d="M12 19 L 12 5" />
      <path d="M15 19 L 15 9" />
      <path d="M18 19 L 21 5" />
    </svg>
  );
}

// ----- Redis (isometric cube) -----
export function RedisLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="none"
         stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
      {/* Isometric cube */}
      <path d="M12 3 L21 7.5 L12 12 L3 7.5 Z" fill="currentColor"
            fillOpacity="0.15" />
      <path d="M3 7.5 L3 16.5 L12 21 L12 12 Z" />
      <path d="M21 7.5 L21 16.5 L12 21 L12 12 Z" />
      {/* Two highlight bands */}
      <path d="M3 11 L12 15.5 L21 11" />
      <path d="M3 14 L12 18.5 L21 14" />
    </svg>
  );
}

// ----- Docker (whale carrying containers) -----
export function DockerLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      {/* Container grid on top: 6 small rects */}
      <rect x="3"  y="8"  width="2.4" height="2.4" />
      <rect x="6"  y="8"  width="2.4" height="2.4" />
      <rect x="9"  y="8"  width="2.4" height="2.4" />
      <rect x="6"  y="5"  width="2.4" height="2.4" />
      <rect x="9"  y="5"  width="2.4" height="2.4" />
      <rect x="12" y="8"  width="2.4" height="2.4" />
      {/* Whale body (rounded shape underneath) */}
      <path d="M2 12
               C 2 11.5, 2.3 11.2, 2.8 11.2
               L 16.5 11.2
               C 18.5 11.2, 20.5 11.8, 21.5 13
               C 22 13.5, 22 14.2, 21.5 14.5
               C 20 15.5, 18 16, 15 16
               L 6 16
               C 4 16, 2.5 15, 2 13.5
               C 1.8 13, 2 12.5, 2 12 Z" />
      {/* Whale spout */}
      <path d="M17 9.5 C 17.5 8, 19 7.5, 19.5 9" fill="none"
            stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

// ----- FastAPI (hexagonal frame + lightning bolt) -----
export function FastApiLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      {/* Hexagon frame */}
      <path d="M12 2 L20.5 6.5 L20.5 17.5 L12 22 L3.5 17.5 L3.5 6.5 Z"
            fill="none" stroke="currentColor" strokeWidth="1.5"
            strokeLinejoin="round" />
      {/* Lightning bolt inside */}
      <path d="M13 6 L8 13 L11 13 L10 18 L15 11 L12.5 11 Z" />
    </svg>
  );
}

// ----- PyTorch (flame) -----
export function PyTorchLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      {/* Flame outline */}
      <path d="M12 2
               C 12 2, 7 6.5, 6 11
               C 5 15, 7.5 21, 12 21
               C 16.5 21, 19 15, 18 11
               C 17.4 8.5, 15.5 6.2, 14 4.5
               C 14 6.5, 13.2 8, 12 9.5
               C 11 8, 11 5.5, 12 2 Z"
            fill="none" stroke="currentColor" strokeWidth="1.6"
            strokeLinejoin="round" />
      {/* Center dot */}
      <circle cx="12" cy="6" r="1.2" />
    </svg>
  );
}

// ----- OpenCLIP (paperclip + lens) -----
export function OpenClipLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img"
         stroke="currentColor" strokeWidth="1.6" fill="none"
         strokeLinecap="round" strokeLinejoin="round">
      {/* Stylized paperclip */}
      <path d="M9 4
               L 9 16
               C 9 17.7, 10.3 19, 12 19
               C 13.7 19, 15 17.7, 15 16
               L 15 7
               C 15 5.9, 14.1 5, 13 5
               C 11.9 5, 11 5.9, 11 7
               L 11 15" />
    </svg>
  );
}

// ----- Pillow (the PIL pillow) -----
export function PillowLogo({ size = 24, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      {/* Pillow shape — soft square with corner tufts */}
      <path d="M5 6
               C 6 4.5, 18 4.5, 19 6
               C 20.5 7, 20.5 17, 19 18
               C 18 19.5, 6 19.5, 5 18
               C 3.5 17, 3.5 7, 5 6 Z"
            fill="none" stroke="currentColor" strokeWidth="1.6"
            strokeLinejoin="round" />
      {/* Corner tufts (the four buttons) */}
      <circle cx="7"  cy="8"  r="0.8" />
      <circle cx="17" cy="8"  r="0.8" />
      <circle cx="7"  cy="16" r="0.8" />
      <circle cx="17" cy="16" r="0.8" />
    </svg>
  );
}

// ----- The carousel ordering -----
export const TECH_STACK: { name: string; Icon: React.FC<IconProps> }[] = [
  { name: "FastAPI",        Icon: FastApiLogo    },
  { name: "PostgreSQL",     Icon: PostgresLogo   },
  { name: "pgvector",       Icon: PgvectorLogo   },
  { name: "MinIO",          Icon: MinioLogo      },
  { name: "Redis",          Icon: RedisLogo      },
  { name: "OpenCLIP",       Icon: OpenClipLogo   },
  { name: "PyTorch",        Icon: PyTorchLogo    },
  { name: "Pillow",         Icon: PillowLogo     },
  { name: "Docker",         Icon: DockerLogo     },
  { name: "Vite",           Icon: ViteLogo       },
  { name: "React",          Icon: ReactLogo      },
  { name: "TypeScript",     Icon: TypeScriptLogo },
];
