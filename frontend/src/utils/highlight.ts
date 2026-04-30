/** A range of characters to highlight in a string. */
export interface HighlightRange {
  start: number;
  end: number;
}

/**
 * Compute character ranges in `text` matching `query` (case-insensitive).
 * Falls back to a fuzzy mode if `fuzzy=true`: any character in `query` is
 * matched in order against `text`, marking each hit (1-character ranges).
 */
export function computeRanges(
  text: string,
  query: string,
  fuzzy = true,
): HighlightRange[] {
  if (!query) return [];
  const lc = text.toLowerCase();
  const lq = query.toLowerCase();

  // 1. exact substring
  const exact = lc.indexOf(lq);
  if (exact !== -1) {
    return [{ start: exact, end: exact + lq.length }];
  }

  // 2. word-boundary substrings (for multi-word queries)
  const words = lq.split(/\s+/).filter(Boolean);
  if (words.length > 1) {
    const ranges: HighlightRange[] = [];
    for (const w of words) {
      const i = lc.indexOf(w);
      if (i !== -1) ranges.push({ start: i, end: i + w.length });
    }
    if (ranges.length) return mergeRanges(ranges);
  }

  // 3. fuzzy: walk both strings
  if (fuzzy) {
    const ranges: HighlightRange[] = [];
    let qi = 0;
    for (let i = 0; i < lc.length && qi < lq.length; i++) {
      if (lc[i] === lq[qi]) {
        ranges.push({ start: i, end: i + 1 });
        qi++;
      }
    }
    if (qi === lq.length) return mergeRanges(ranges);
  }

  return [];
}

function mergeRanges(ranges: HighlightRange[]): HighlightRange[] {
  if (ranges.length <= 1) return ranges;
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  const merged: HighlightRange[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const prev = merged[merged.length - 1];
    const cur = sorted[i];
    if (cur.start <= prev.end) {
      prev.end = Math.max(prev.end, cur.end);
    } else {
      merged.push(cur);
    }
  }
  return merged;
}
