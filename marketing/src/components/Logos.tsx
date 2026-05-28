/* B&W tech logos for the carousel.
 *
 * Each is an inline SVG rendered in `currentColor` so the carousel's
 * text color flows through and we get a uniform monochrome treatment
 * across every brand. We render approximate shapes of each project's
 * official mark — distinct enough to recognize at a glance, simplified
 * enough to read at 26px and look intentional in monochrome.
 *
 * The marks belong to their respective owners. They appear here
 * nominatively as a credit to the open-source projects neuthek is
 * being built on — see the link on each carousel item which jumps
 * straight to that project's documentation. */

import type { CSSProperties } from "react";

type IconProps = { size?: number; style?: CSSProperties };

const COMMON = (size: number): CSSProperties => ({
  width: size, height: size, display: "block",
});

// ====== React (atomic orbit) ======
export function ReactLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="-12 -12 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      <circle cx="0" cy="0" r="2.1" fill="currentColor" />
      <g fill="none" stroke="currentColor" strokeWidth="1">
        <ellipse rx="10.5" ry="4.3" />
        <ellipse rx="10.5" ry="4.3" transform="rotate(60)" />
        <ellipse rx="10.5" ry="4.3" transform="rotate(120)" />
      </g>
    </svg>
  );
}

// ====== TypeScript (TS square — letters drawn as paths) ======
export function TypeScriptLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      <rect x="0" y="0" width="24" height="24" rx="2.8" fill="currentColor"/>
      {/* "T" — horizontal bar + vertical bar */}
      <rect x="3.5"  y="13"   width="6.5" height="1.6" fill="#ffffff"/>
      <rect x="5.95" y="13"   width="1.6" height="7.5" fill="#ffffff"/>
      {/* "S" — drawn as a stylized path */}
      <path d="M11.4 19.4
               C 11.4 20.7, 12.6 21.3, 14.2 21.3
               C 16   21.3, 17.5 20.4, 17.5 18.8
               C 17.5 17.4, 16.6 16.9, 14.6 16.4
               C 13.4 16.1, 13   15.9, 13   15.4
               C 13   14.9, 13.5 14.7, 14.2 14.7
               C 15.1 14.7, 15.6 15, 15.9 15.6
               L 17.3 14.8
               C 16.9 13.8, 15.9 13.3, 14.4 13.3
               C 12.7 13.3, 11.5 14.2, 11.5 15.6
               C 11.5 17.1, 12.6 17.5, 14.2 17.9
               C 15.3 18.2, 15.9 18.3, 15.9 18.9
               C 15.9 19.5, 15.3 19.8, 14.4 19.8
               C 13.4 19.8, 12.8 19.4, 12.5 18.7
               Z" fill="#ffffff"/>
    </svg>
  );
}

// ====== Vite (lightning V) ======
export function ViteLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      {/* V-shape lightning bolt: outer V + inner flame stripe */}
      <path d="M2 4
               L 12 22
               L 22 4
               L 17.5 4
               L 12 14
               L 6.5 4 Z" fill="currentColor"/>
      <path d="M9.8 6
               L 14.2 6
               L 12.5 11.5
               L 14 11.5
               L 11 17
               L 11.7 13
               L 10.2 13 Z" fill="#ffffff"/>
    </svg>
  );
}

// ====== PostgreSQL (Slonik silhouette — refined) ======
export function PostgresLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="currentColor">
      {/* Head + ear + back + legs */}
      <path d="M6.2 9.5
               C 6.2 6.2, 9.2 4.1, 12.2 4.1
               C 14   4.1, 15.6 4.8, 16.6 6.0
               C 17.5 5.8, 18.4 6.0, 18.9 6.5
               C 19.5 7.2, 19.4 8.4, 19.1 9.3
               L 19.0 13.3
               C 19.0 14.5, 18.4 15.4, 17.5 15.7
               L 17.5 18.7 L 16 18.7 L 16 16
               C 15.1 16.3, 14.1 16.4, 13.1 16.3
               L 13.1 18.7 L 11.6 18.7 L 11.6 16.0
               C 10.7 15.6, 9.95 14.9, 9.7 14.0
               L 9.7 18.7 L 8.2 18.7 L 8.2 12.7
               C 6.95 12.0, 6.2 10.8, 6.2 9.5 Z" />
      {/* Trunk reaching down */}
      <path d="M9.5 11
               C 10 12.5, 11 13.5, 12.3 13.7
               L 12.6 15.6
               C 12.7 16.2, 12.4 16.7, 11.8 16.8
               C 11.2 16.9, 10.8 16.7, 10.7 16.1
               L 10.5 14.8
               C 10 14.5, 9.6 14, 9.4 13.4 Z"
            fill="#ffffff"/>
      {/* Eye */}
      <circle cx="14.5" cy="8.8" r="0.85" fill="#ffffff"/>
      <circle cx="14.5" cy="8.8" r="0.4" fill="currentColor"/>
    </svg>
  );
}

// ====== pgvector (vector field — origin + three arrows) ======
export function PgvectorLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img"
         stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
         strokeLinejoin="round" fill="none">
      <circle cx="5.5" cy="18.5" r="1.4" fill="currentColor" stroke="none"/>
      {/* Three vectors at different angles, each with arrowhead */}
      <path d="M5.5 18.5 L 21 18.5
               M 19 16.5 L 21 18.5 L 19 20.5"/>
      <path d="M5.5 18.5 L 17.5 7.5
               M 14.8 7.7 L 17.5 7.5 L 17.3 10.2"/>
      <path d="M5.5 18.5 L 9 3.5
               M 7 5 L 9 3.5 L 10.7 5"/>
    </svg>
  );
}

// ====== MinIO (5 upward strokes — flame-M) ======
export function MinioLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img"
         stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
         fill="none">
      {/* Five upward strokes of varying height forming an M */}
      <path d="M3.5 19  L 5 7.5" />
      <path d="M8   19  L 8  10.5" />
      <path d="M12  19  L 12 5" />
      <path d="M16  19  L 16 10.5" />
      <path d="M20.5 19 L 19 7.5" />
    </svg>
  );
}

// ====== Redis (isometric stack — three layers) ======
export function RedisLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="none"
         stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
      {/* Three stacked isometric layers */}
      {/* Top */}
      <path d="M12 2.5 L 21 6.5 L 12 10.5 L 3 6.5 Z" />
      {/* Middle layer connecting lines */}
      <path d="M3 10  L 12 14   L 21 10" />
      {/* Bottom layer + sides */}
      <path d="M3 13.5 L 12 17.5 L 21 13.5" />
      <path d="M3 6.5 L 3 17 L 12 21 L 21 17 L 21 6.5" />
      {/* Eye/face marks on the front */}
      <circle cx="8" cy="9" r="0.7" fill="currentColor" stroke="none"/>
    </svg>
  );
}

// ====== Docker (whale + containers) ======
export function DockerLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="currentColor">
      {/* Container grid (3 wide × 2 tall + 1 extra on top-left) */}
      <rect x="2.5"  y="9.5" width="2.2" height="2.2" />
      <rect x="5.2"  y="9.5" width="2.2" height="2.2" />
      <rect x="7.9"  y="9.5" width="2.2" height="2.2" />
      <rect x="10.6" y="9.5" width="2.2" height="2.2" />
      <rect x="5.2"  y="6.8" width="2.2" height="2.2" />
      <rect x="7.9"  y="6.8" width="2.2" height="2.2" />
      <rect x="10.6" y="6.8" width="2.2" height="2.2" />
      <rect x="7.9"  y="4.1" width="2.2" height="2.2" />
      {/* Whale body — broad rounded shape underneath */}
      <path d="M1.5 13
               L 13.5 13
               C 16.5 13, 19 13.6, 20.5 14.6
               C 21.5 15.3, 22.4 15.2, 22.8 14.4
               C 22.9 14.2, 22.7 14, 22.4 14
               L 21.6 14
               C 21.6 14, 21 14.4, 20.3 14.2
               C 19.4 13, 17 12, 13.5 12
               L 1.5 12 Z" />
      {/* Tail */}
      <path d="M19 12.5
               C 19.5 11, 21 10.5, 22 11
               C 22 12, 21 13, 20 13
               L 19 13 Z" fill="none" stroke="currentColor"
            strokeWidth="1" strokeLinejoin="round"/>
      {/* Whale "spray" suggestion line */}
      <path d="M1.5 16 L 22.5 16" stroke="currentColor"
            strokeWidth="0.8" strokeDasharray="2 2"/>
    </svg>
  );
}

// ====== FastAPI (hexagon + lightning bolt) ======
export function FastApiLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      {/* Hexagon outline */}
      <path d="M12 1.8 L 21.2 6.5 L 21.2 17.5 L 12 22.2 L 2.8 17.5 L 2.8 6.5 Z"
            fill="none" stroke="currentColor" strokeWidth="1.7"
            strokeLinejoin="round"/>
      {/* Lightning bolt inside */}
      <path d="M13.6 5.5
               L 8 13.4
               L 11.2 13.4
               L 10.4 18.5
               L 16 10.6
               L 12.8 10.6 Z"
            fill="currentColor"/>
    </svg>
  );
}

// ====== PyTorch (refined flame) ======
export function PyTorchLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="none"
         stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"
         strokeLinecap="round">
      {/* Flame outline — top point curls slightly right */}
      <path d="M14.5 2.8
               C 14.5 2.8, 18 6.5, 19 9.5
               C 20.3 13.5, 18.3 18.5, 14 20.5
               C 9.7  22.5, 5.5 20.5, 4.5 16.5
               C 3.6 12.9, 6 9.5, 8.5 7
               C 9.6 5.9, 10.5 4.8, 11   3.5
               C 11.2 5, 11.5 6.5, 12.5 7.5
               C 13.5 8.5, 14.5 6.5, 14.5 2.8 Z" />
      {/* Center dot (the "eye" of the flame) */}
      <circle cx="14.5" cy="6.5" r="1.3" fill="currentColor" stroke="none"/>
    </svg>
  );
}

// ====== OpenCLIP (stylized paperclip) ======
export function OpenClipLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img"
         stroke="currentColor" strokeWidth="1.7" fill="none"
         strokeLinecap="round" strokeLinejoin="round">
      {/* Classic paperclip shape */}
      <path d="M16 7
               L 16 17
               C 16 19.5, 14 21, 12 21
               C 10 21, 8 19.5, 8 17
               L 8 6
               C 8 4.3, 9.3 3, 11 3
               C 12.7 3, 14 4.3, 14 6
               L 14 16
               C 14 16.8, 13.3 17.5, 12.5 17.5
               C 11.7 17.5, 11 16.8, 11 16
               L 11 8" />
    </svg>
  );
}

// ====== Pillow (cushioned pillow with corner buttons) ======
export function PillowLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="none"
         stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
      {/* Pillow shape with concave sides */}
      <path d="M5 5.5
               C 9 4.5, 15 4.5, 19 5.5
               C 20 9, 20 15, 19 18.5
               C 15 19.5, 9 19.5, 5 18.5
               C 4 15, 4 9, 5 5.5 Z" />
      {/* Corner tufts */}
      <circle cx="7.5"  cy="8"  r="0.9" fill="currentColor" stroke="none"/>
      <circle cx="16.5" cy="8"  r="0.9" fill="currentColor" stroke="none"/>
      <circle cx="7.5"  cy="16" r="0.9" fill="currentColor" stroke="none"/>
      <circle cx="16.5" cy="16" r="0.9" fill="currentColor" stroke="none"/>
    </svg>
  );
}

// ====== Caddy (TLS shield + check) ======
export function CaddyLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="none" stroke="currentColor"
         strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round">
      <path d="M12 2.5 L20 5.3 V11 C20 16.2 16.4 19.6 12 21.5 C7.6 19.6 4 16.2 4 11 V5.3 Z" />
      <path d="M8.6 11.8 L11 14.3 L15.6 9" />
    </svg>
  );
}

// ====== Hugging Face (smiley — the platform we run Florence-2 / Qwen on) ======
export function HuggingFaceLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img">
      <circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="9" cy="10.6" r="1.05" fill="currentColor" />
      <circle cx="15" cy="10.6" r="1.05" fill="currentColor" />
      <path d="M8 14.4 C9.2 16.4, 14.8 16.4, 16 14.4" fill="none"
            stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

// ====== insightface (face + detection brackets) ======
export function InsightFaceLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7.5 V4 H6.5 M17.5 4 H21 V7.5 M21 16.5 V20 H17.5 M6.5 20 H3 V16.5" />
      <circle cx="12" cy="10.3" r="2.3" />
      <path d="M7.7 16.6 C8.8 14.2, 15.2 14.2, 16.3 16.6" />
    </svg>
  );
}

// ====== TanStack Query (stacked data layers) ======
export function TanStackLogo({ size = 26, style }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" style={{ ...COMMON(size), ...style }}
         aria-hidden="true" role="img" fill="none" stroke="currentColor"
         strokeWidth="1.5" strokeLinejoin="round">
      <path d="M3 7 L12 3 L21 7 L12 11 Z" />
      <path d="M3 12 L12 16 L21 12" />
      <path d="M3 17 L12 21 L21 17" />
    </svg>
  );
}

// ====== Stack registry: name + icon + URL to that project's docs ======
//
// URLs are the canonical docs/home for each project. Carousel items
// open these in a new tab as a credit + a useful jump-off for visitors
// who want to learn more.
export type TechEntry = {
  name: string;
  Icon: React.FC<IconProps>;
  href: string;
};

export const TECH_STACK: TechEntry[] = [
  { name: "FastAPI",        Icon: FastApiLogo,     href: "https://fastapi.tiangolo.com" },
  { name: "PostgreSQL",     Icon: PostgresLogo,    href: "https://www.postgresql.org/docs/" },
  { name: "pgvector",       Icon: PgvectorLogo,    href: "https://github.com/pgvector/pgvector" },
  { name: "MinIO",          Icon: MinioLogo,       href: "https://min.io/docs/minio/linux/index.html" },
  { name: "Redis",          Icon: RedisLogo,       href: "https://redis.io/docs/latest/" },
  { name: "Caddy",          Icon: CaddyLogo,       href: "https://caddyserver.com/docs/" },
  { name: "PyTorch",        Icon: PyTorchLogo,     href: "https://pytorch.org/docs/" },
  { name: "OpenCLIP",       Icon: OpenClipLogo,    href: "https://github.com/mlfoundations/open_clip" },
  { name: "Hugging Face",   Icon: HuggingFaceLogo, href: "https://huggingface.co/docs" },
  { name: "InsightFace",    Icon: InsightFaceLogo, href: "https://github.com/deepinsight/insightface" },
  { name: "Pillow",         Icon: PillowLogo,      href: "https://pillow.readthedocs.io" },
  { name: "Docker",         Icon: DockerLogo,      href: "https://docs.docker.com" },
  { name: "React",          Icon: ReactLogo,       href: "https://react.dev" },
  { name: "Vite",           Icon: ViteLogo,        href: "https://vitejs.dev/guide/" },
  { name: "TanStack Query", Icon: TanStackLogo,    href: "https://tanstack.com/query/latest" },
  { name: "TypeScript",     Icon: TypeScriptLogo,  href: "https://www.typescriptlang.org/docs/" },
];
