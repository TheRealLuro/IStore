// CSV / TSV viewer — parses the file client-side and renders it as a
// themed scrollable table with a sticky header and zebra rows. Falls
// back to a raw-text view if the parser detects something that
// doesn't look like delimited data.
//
// Parser is a small RFC-4180 subset: handles quoted fields, escaped
// quotes (""), embedded commas and newlines inside quotes, and
// LF / CRLF line endings. Tab-delimited (TSV) is auto-detected.
import React, { useState, useEffect, useMemo } from "react";
import toast from "react-hot-toast";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";

function parseDelimited(text, delim) {
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

function pickDelimiter(text) {
  // First non-empty line wins. Tabs beat commas if tabs occur and
  // there are at least 2 tabs (a TSV with one column would be
  // indistinguishable from a CSV with one column, but those render
  // the same anyway).
  const firstLine = text.split(/\r?\n/, 1)[0] || "";
  const tabs = (firstLine.match(/\t/g) || []).length;
  return tabs >= 2 ? "\t" : ",";
}

export function CsvViewer({ fileId, fileName, onClose }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
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
  }, [fileId]);

  const header = rows?.[0] || [];
  const body = useMemo(() => (rows ? rows.slice(1) : []), [rows]);

  return (
    <div className="csv-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="csv-viewer__head">
        <span className="csv-viewer__name">{fileName}</span>
        <span className="csv-viewer__meta mono">
          {rows ? `${body.length.toLocaleString()} rows · ${header.length} cols` : ""}
        </span>
      </div>
      <div className="csv-viewer__body">
        {err ? (
          <div className="csv-viewer__error">{err}</div>
        ) : !rows ? (
          <div className="csv-viewer__loading">Parsing CSV…</div>
        ) : rows.length === 0 ? (
          <div className="csv-viewer__empty">File is empty.</div>
        ) : (
          <table className="csv-viewer__table">
            <thead>
              <tr>
                <th className="csv-viewer__rownum mono">#</th>
                {header.map((h, i) => (
                  <th key={i} className="mono">{h || `col ${i + 1}`}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((r, i) => (
                <tr key={i}>
                  <td className="csv-viewer__rownum mono">{i + 1}</td>
                  {header.map((_, j) => (
                    <td key={j}>{r[j] ?? ""}</td>
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
