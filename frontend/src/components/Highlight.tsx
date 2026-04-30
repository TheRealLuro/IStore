import { computeRanges } from "@/utils/highlight";

interface HighlightProps {
  text: string;
  query: string;
  className?: string;
  highlightClass?: string;
  fuzzy?: boolean;
}

/** Render text with highlighted spans for matches against `query`. */
export function Highlight({
  text,
  query,
  className,
  highlightClass = "bg-accent/30 text-white rounded-sm px-0.5 -mx-0.5",
  fuzzy = true,
}: HighlightProps) {
  if (!query) return <span className={className}>{text}</span>;
  const ranges = computeRanges(text, query, fuzzy);
  if (!ranges.length) return <span className={className}>{text}</span>;

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  ranges.forEach((r, i) => {
    if (cursor < r.start) parts.push(<span key={`p-${i}`}>{text.slice(cursor, r.start)}</span>);
    parts.push(
      <mark
        key={`m-${i}`}
        data-highlight
        className={highlightClass}
      >
        {text.slice(r.start, r.end)}
      </mark>,
    );
    cursor = r.end;
  });
  if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <span className={className}>{parts}</span>;
}
