// CSV / TSV viewer — parses the file client-side and renders it as a
// themed scrollable table with a sticky header, sticky row-number
// column, zebra rows, and right-aligned numeric columns. Falls back to
// a raw-text view if the parser detects something that doesn't look
// like delimited data.
//
// Parser is a small RFC-4180 subset: handles quoted fields, escaped
// quotes (""), embedded commas and newlines inside quotes, and
// LF / CRLF line endings. Tab-delimited (TSV) is auto-detected.
import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { ViewerError, ViewerEmpty } from "./viewer-states.jsx";

// Exported for reuse by the vault import-from-file flow (VLT-7), which parses
// password-manager CSV exports client-side into structured Login items using
// this same RFC-4180 subset.
export function parseDelimited(text, delim) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; }
        else inQuotes = false;
      } else {
        cell += c;
      }
    } else {
      if (c === '"') {
        inQuotes = true;
      } else if (c === delim) {
        row.push(cell); cell = "";
      } else if (c === "\n") {
        row.push(cell); cell = "";
        rows.push(row); row = [];
      } else if (c === "\r") {
        // Eat — \r\n handled above by the next iteration's \n.
      } else {
        cell += c;
      }
    }
  }
  if (cell.length > 0 || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

export function pickDelimiter(text) {
  // First non-empty line wins. Tabs beat commas if tabs occur and
  // there are at least 2 tabs (a TSV with one column would be
  // indistinguishable from a CSV with one column, but those render
  // the same anyway).
  const firstLine = text.split(/\r?\n/, 1)[0] || "";
  const tabs = (firstLine.match(/\t/g) || []).length;
  return tabs >= 2 ? "\t" : ",";
}

// A cell counts as numeric if it's blank or parses as a finite number
// (allowing thousands separators, a leading currency sign, and a
// trailing %). A column is numeric when it has at least one number and
// no non-numeric, non-blank cell.
const NUM_RE = /^[$€£¥]?\s*-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?%?$/;
function looksNumeric(s) {
  return s !== "" && NUM_RE.test(s.trim());
}

// Drives the sticky-edge scroll shadows. We toggle two data-attributes on
// the scroll container as the user scrolls: `data-scroll-y` once the body
// has scrolled under the sticky header, and `data-scroll-x` once it has
// scrolled past the sticky row-number column. CSS draws a soft inset
// shadow on those edges only when set — the classic "real data-grid" cue.
function useScrollShadow() {
  const ref = useRef(null);
  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.dataset.scrollY = el.scrollTop > 0 ? "1" : "0";
    el.dataset.scrollX = el.scrollLeft > 0 ? "1" : "0";
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => { el.removeEventListener("scroll", update); ro.disconnect(); };
  }, [update]);
  return ref;
}

export function CsvViewer({ fileId, fileName }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const scrollRef = useScrollShadow();

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setErr(null);
    (async () => {
      try {
        const blob = await fetchMediaBlob(originalMediaUrl(fileId));
        const text = await blob.text();
        if (cancelled) return;
        const delim = pickDelimiter(text);
        const parsed = parseDelimited(text, delim);
        // Strip a single trailing empty row that comes from a final
        // newline — common and noisy.
        if (parsed.length > 1) {
          const last = parsed[parsed.length - 1];
          if (last.length === 1 && last[0] === "") parsed.pop();
        }
        setRows(parsed);
      } catch (e) {
        if (cancelled) return;
        setErr(e?.message || "Could not load file");
        toast.error("Couldn't load CSV");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId, attempt]);

  const header = rows?.[0] || [];
  const body = useMemo(() => (rows ? rows.slice(1) : []), [rows]);

  // Per-column "is this column numeric?" — drives right alignment.
  const numericCols = useMemo(() => {
    const flags = header.map(() => null); // null = unseen, true/false decided
    for (const r of body) {
      for (let j = 0; j < header.length; j++) {
        const v = (r[j] ?? "").trim();
        if (v === "") continue;
        if (looksNumeric(v)) { if (flags[j] === null) flags[j] = true; }
        else flags[j] = false;
      }
    }
    return flags.map((f) => f === true);
  }, [header, body]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const isError = !!err;
  const isLoading = !err && !rows;
  const isEmpty = !err && rows && rows.length === 0;
  const showTable = !isError && !isLoading && !isEmpty;

  return (
    <div className="csv-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="csv-viewer__head">
        <span className="vw-head__icon"><Icon name="spreadsheet" size={15} /></span>
        <span className="csv-viewer__name">{fileName}</span>
        <span className="csv-viewer__meta mono">
          {rows ? `${body.length.toLocaleString()} rows · ${header.length} cols` : ""}
        </span>
      </div>
      <div
        className="csv-viewer__body"
        ref={showTable ? scrollRef : null}
        data-scroll-y="0"
        data-scroll-x="0"
      >
        {isError ? (
          <ViewerError
            title="Couldn't load CSV"
            message={err}
            onRetry={retry}
            downloadUrl={originalMediaUrl(fileId)}
            downloadName={fileName}
          />
        ) : isLoading ? (
          <CsvSkeleton />
        ) : isEmpty ? (
          <ViewerEmpty icon="spreadsheet" title="Empty file" message="There are no rows to display." />
        ) : (
          <table className="csv-viewer__table">
            <thead>
              <tr>
                <th scope="col" className="csv-viewer__rownum mono" title="Row number">#</th>
                {header.map((h, i) => (
                  <th scope="col" key={i} className={numericCols[i] ? "csv-num" : undefined} title={h || `col ${i + 1}`}>
                    {h || `col ${i + 1}`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((r, i) => (
                <tr key={i}>
                  <td className="csv-viewer__rownum mono">{(i + 1).toLocaleString()}</td>
                  {header.map((_, j) => (
                    <td key={j} className={numericCols[j] ? "csv-num mono" : undefined}>
                      {r[j] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// A table-shaped shimmer that mirrors the real grid (sticky-header row +
// a handful of body rows) so the swap to live data is seamless.
function CsvSkeleton() {
  const cols = [40, 150, 110, 130, 90, 120];
  const rowCount = 9;
  return (
    <div className="csv-skel" aria-busy="true" aria-live="polite">
      <span className="vw-sr">Parsing CSV…</span>
      <div className="csv-skel__row csv-skel__row--head">
        {cols.map((w, i) => (
          <div key={i} className="vw-shimmer csv-skel__cell" style={{ width: w }} />
        ))}
      </div>
      {Array.from({ length: rowCount }).map((_, r) => (
        <div key={r} className="csv-skel__row">
          {cols.map((w, i) => (
            <div
              key={i}
              className="vw-shimmer csv-skel__cell"
              style={{ width: i === 0 ? 22 : Math.round(w * (0.6 + (((r + i) % 4) * 0.12))) }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
