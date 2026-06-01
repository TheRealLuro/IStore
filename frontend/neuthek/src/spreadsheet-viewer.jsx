// Spreadsheet viewer — renders the ACTUAL cells of a workbook as a live
// grid: sticky A/B/C column headers, sticky 1/2/3 row numbers, sheet
// tabs, zebra rows, right-aligned numbers/dates, an active-cell
// crosshair, and a clean toolbar. No rasterized PDF — the cells are
// live and selectable.
//
// Parsing is SERVER-SIDE. Earlier this file parsed `.xlsx` in the
// browser with exceljs, but exceljs only reads the modern zip-based
// OOXML — legacy binary `.xls` (BIFF) and OpenDocument `.ods` threw and
// dropped the user to a "couldn't render, download instead" dead-end.
// The backend now parses ALL formats (.xlsx / .xls / .ods / .xlsm /
// .xlsb) with python-calamine and returns normalized JSON via
// `GET /images/{id}/spreadsheet` — see backend/spreadsheet_extract.py.
// One code path, native typed values, and no risky second JS lib. The
// dead-end message is gone: every supported format renders the same way.
//
// JSON shape (already display-formatted server-side, so the FE just
// renders it):
//   { sheets: [ { name, rows: [[{v,t}, …], …], n_rows, n_cols,
//                 shown_rows, shown_cols, truncated } ],
//     sheet_count, sheets_truncated }
// where `t` ∈ "s"(text) "n"(number) "d"(date/time) "b"(bool) "e"(error)
// ""(empty). Numbers/dates render right-aligned + tabular; the server
// already did the date/number formatting so there's no Excel-serial
// decoding here.
//
// Props: { fileId, fileName } — identical contract to csv-viewer.jsx so
// preview.jsx mounts it inside `.pdf-modal__body` the same way (the
// HUMAN routes .xlsx/.xls/.ods here).
import React, {
  useState, useEffect, useMemo, useCallback, useRef,
} from "react";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { getSpreadsheet } from "@/api/files";

// A1-style column label from a 0-based ordinal: 0→A, 25→Z, 26→AA …
function colLabel(n) {
  let s = "";
  n += 1;
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

// Locale-group a numeric display string the server already trimmed of
// float noise. We only add thousands separators here (so the grouping
// char matches the VIEWER's locale, not the server's) and leave the
// decimal part untouched. Falls back to the raw string if it doesn't
// parse — never throws on a weird cell.
function groupNumber(v) {
  // Split sign / integer / fraction so we group only the integer part.
  const m = /^(-?)(\d+)(\.\d+)?$/.exec(v);
  if (!m) return v;
  const [, sign, intPart, frac = ""] = m;
  // Don't group small integers (≤ 4 digits) — "1234" reads fine, and
  // grouping a year like 2026 to "2,026" looks wrong.
  if (!frac && intPart.length <= 4) return v;
  const grouped = Number(intPart).toLocaleString(undefined, {
    useGrouping: true, maximumFractionDigits: 0,
  });
  return sign + grouped + frac;
}

// Per-cell display text. Numbers get locale grouping; everything else is
// passed through verbatim (the server already formatted dates/bools).
function cellText(cell) {
  if (!cell) return "";
  if (cell.t === "n") return groupNumber(cell.v);
  return cell.v;
}

// Numbers + dates align right (and get the tabular mono face); booleans
// and formula errors center; text left.
function cellAlign(t) {
  if (t === "n" || t === "d") return "right";
  if (t === "b" || t === "e") return "center";
  return "left";
}

// Estimate a column's pixel width from its content so columns auto-fit
// instead of all sharing one width (or letting one long cell blow the
// layout). We sample the header + up to ~60 rows, take the longest
// rendered text, and clamp to [MIN, MAX]. ~7.3px/char ≈ the 12.5px
// Geist face; +24 for cell padding. Cheap, good enough, and stable
// across renders since it's memoized per sheet.
const COL_MIN = 56;
const COL_MAX = 340;
const CHAR_PX = 7.3;
const CELL_PAD = 24;
function computeColWidths(rows, nCols) {
  const widths = new Array(nCols).fill(COL_MIN);
  const sample = Math.min(rows.length, 60);
  for (let c = 0; c < nCols; c++) {
    let longest = 1; // header letter is ≥ 1 char
    for (let r = 0; r < sample; r++) {
      const cell = rows[r][c];
      const len = cell && cell.v ? cell.v.length : 0;
      if (len > longest) longest = len;
    }
    const px = Math.round(longest * CHAR_PX + CELL_PAD);
    widths[c] = Math.max(COL_MIN, Math.min(COL_MAX, px));
  }
  return widths;
}

export function SpreadsheetViewer({ fileId, fileName }) {
  const [data, setData] = useState(null);   // full server payload
  const [active, setActive] = useState(0);   // active sheet index
  const [err, setErr] = useState(null);
  // {r,c} of the hovered data cell, for the crosshair highlight. r/c are
  // 0-based into the visible grid; -1 means none.
  const [hover, setHover] = useState({ r: -1, c: -1 });
  // Whether the grid has scrolled (drives the sticky-header shadow).
  const [scrolledX, setScrolledX] = useState(false);
  const [scrolledY, setScrolledY] = useState(false);

  const gridWrapRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setActive(0);
    setErr(null);
    setHover({ r: -1, c: -1 });
    (async () => {
      try {
        const payload = await getSpreadsheet(fileId);
        if (cancelled) return;
        if (!payload || !payload.sheets || payload.sheets.length === 0) {
          setErr("This workbook has no sheets to display.");
          return;
        }
        setData(payload);
      } catch (e) {
        if (cancelled) return;
        // 415 = not a spreadsheet we can read; 422 = unreadable bytes.
        // Either way the user can still download from the toolbar.
        const status = e && e.status;
        if (status === 415) {
          setErr("This file isn't a spreadsheet format we can display.");
        } else if (status === 422) {
          setErr(
            "This spreadsheet couldn't be read — it may be corrupt or " +
            "password-protected. You can still download it from the toolbar."
          );
        } else {
          setErr(
            "Couldn't open this spreadsheet. You can still download it " +
            "from the toolbar."
          );
        }
        toast.error("Couldn't open spreadsheet");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId]);

  const sheets = data ? data.sheets : null;
  const sheet = sheets ? sheets[Math.min(active, sheets.length - 1)] : null;

  // Reset scroll position + crosshair when switching sheets.
  useEffect(() => {
    setHover({ r: -1, c: -1 });
    setScrolledX(false);
    setScrolledY(false);
    if (gridWrapRef.current) {
      gridWrapRef.current.scrollLeft = 0;
      gridWrapRef.current.scrollTop = 0;
    }
  }, [active]);

  const colWidths = useMemo(() => {
    if (!sheet) return [];
    return computeColWidths(sheet.rows, sheet.shown_cols);
  }, [sheet]);

  const onTab = useCallback((i) => setActive(i), []);

  const onScroll = useCallback((e) => {
    const el = e.currentTarget;
    setScrolledX(el.scrollLeft > 0);
    setScrolledY(el.scrollTop > 0);
  }, []);

  // Keyboard scrolling when the grid is focused. Arrow keys nudge by a
  // row/col-ish step; Page keys by a viewport; Home/End jump to edges.
  const onKeyDown = useCallback((e) => {
    const el = gridWrapRef.current;
    if (!el) return;
    const STEP = 40;
    let handled = true;
    switch (e.key) {
      case "ArrowDown": el.scrollTop += STEP; break;
      case "ArrowUp": el.scrollTop -= STEP; break;
      case "ArrowRight": el.scrollLeft += STEP; break;
      case "ArrowLeft": el.scrollLeft -= STEP; break;
      case "PageDown": el.scrollTop += el.clientHeight * 0.9; break;
      case "PageUp": el.scrollTop -= el.clientHeight * 0.9; break;
      case "Home": el.scrollTop = 0; el.scrollLeft = 0; break;
      case "End": el.scrollTop = el.scrollHeight; break;
      default: handled = false;
    }
    if (handled) e.preventDefault();
  }, []);

  // Toolbar meta: active-sheet dimensions + sheet count.
  const meta = useMemo(() => {
    if (!sheet) return null;
    const dims =
      `${sheet.n_rows.toLocaleString()} × ${sheet.n_cols.toLocaleString()} ` +
      `${sheet.n_cols === 1 ? "col" : "cols"}`;
    const count =
      data && data.sheet_count > 1
        ? `${data.sheet_count} sheets`
        : null;
    return { dims, count };
  }, [sheet, data]);

  return (
    <div className="sheet-viewer" onClick={(e) => e.stopPropagation()}>
      {/* Toolbar: filename · sheet count · dimensions */}
      <div className="sheet-viewer__head">
        <Icon name="spreadsheet" size={14} />
        <span className="sheet-viewer__name" title={fileName}>{fileName}</span>
        {meta && (
          <span className="sheet-viewer__meta mono">
            {meta.count && <span className="sheet-viewer__meta-chip">{meta.count}</span>}
            <span className="sheet-viewer__meta-dims">{meta.dims}</span>
          </span>
        )}
      </div>

      <div className="sheet-viewer__body">
        {err ? (
          <div className="sheet-viewer__error">
            <Icon name="spreadsheet" size={34} strokeWidth={1.1} />
            <div>{err}</div>
          </div>
        ) : !sheets ? (
          // Loading skeleton — a ghosted grid so the modal never flashes
          // blank or shows raw "Parsing…" text against empty space.
          <div className="sheet-viewer__skeleton" aria-hidden="true">
            <div className="sheet-viewer__skeleton-head" />
            {Array.from({ length: 12 }, (_, i) => (
              <div key={i} className="sheet-viewer__skeleton-row">
                <div className="sheet-viewer__skeleton-rownum" />
                {Array.from({ length: 6 }, (_, j) => (
                  <div key={j} className="sheet-viewer__skeleton-cell" />
                ))}
              </div>
            ))}
          </div>
        ) : sheet.rows.length === 0 ? (
          <div className="sheet-viewer__empty">
            <Icon name="grid" size={32} strokeWidth={1.1} />
            <div>This sheet is empty.</div>
          </div>
        ) : (
          <>
            {sheet.truncated && (
              <div className="sheet-viewer__clip mono">
                <Icon name="alert" size={12} strokeWidth={1.6} />
                Showing first {sheet.shown_rows.toLocaleString()} of{" "}
                {sheet.n_rows.toLocaleString()} rows
                {sheet.shown_cols < sheet.n_cols
                  ? ` and first ${sheet.shown_cols} of ${sheet.n_cols} columns`
                  : ""}.
              </div>
            )}
            <div
              ref={gridWrapRef}
              className={
                "sheet-viewer__grid-wrap" +
                (scrolledX ? " is-scrolled-x" : "") +
                (scrolledY ? " is-scrolled-y" : "")
              }
              tabIndex={0}
              role="grid"
              aria-label={`${sheet.name} — ${sheet.n_rows} rows by ${sheet.n_cols} columns`}
              onScroll={onScroll}
              onKeyDown={onKeyDown}
              onMouseLeave={() => setHover({ r: -1, c: -1 })}
            >
              <table className="sheet-viewer__grid">
                <colgroup>
                  <col className="sheet-viewer__col-corner" />
                  {colWidths.map((w, c) => (
                    <col key={c} style={{ width: `${w}px` }} />
                  ))}
                </colgroup>
                <thead>
                  <tr>
                    <th className="sheet-viewer__corner mono" />
                    {Array.from({ length: sheet.shown_cols }, (_, c) => (
                      <th
                        key={c}
                        className={
                          "sheet-viewer__colhdr mono" +
                          (c === hover.c ? " is-active" : "")
                        }
                      >
                        {colLabel(c)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sheet.rows.map((row, r) => (
                    <tr
                      key={r}
                      className={r === hover.r ? "is-active-row" : undefined}
                    >
                      <th
                        className={
                          "sheet-viewer__rowhdr mono" +
                          (r === hover.r ? " is-active" : "")
                        }
                      >
                        {r + 1}
                      </th>
                      {row.map((cell, c) => {
                        const t = cell ? cell.t : "";
                        return (
                          <td
                            key={c}
                            className={
                              "sheet-viewer__cell" +
                              (t === "n" || t === "d" ? " is-num" : "") +
                              (t === "e" ? " is-err" : "") +
                              (c === hover.c ? " is-active-col" : "")
                            }
                            style={{ textAlign: cellAlign(t) }}
                            title={cell && cell.v ? cell.v : undefined}
                            onMouseEnter={() => setHover({ r, c })}
                          >
                            {cellText(cell)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {sheets && sheets.length > 1 && (
        <div className="sheet-viewer__tabs" role="tablist">
          {sheets.map((s, i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={i === active}
              className={
                "sheet-viewer__tab" + (i === active ? " is-active" : "")
              }
              onClick={() => onTab(i)}
              title={s.name}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
