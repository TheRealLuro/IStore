/**
 * Document mini-render helpers — Google Drive-style paper-page bitmaps.
 *
 * Every supported type is rendered into an offscreen "paper" DOM element
 * (Letter at 96 DPI, 816×1056), styled to look like a real document or
 * spreadsheet, then snapshotted via html2canvas into a single PNG dataURL.
 * The consumer (ThumbnailRenderer) just shows it via <img>, identical to
 * how PDF previews are already handled. Result: every doc thumbnail looks
 * paper-shaped with a subtle shadow, matching Drive's visual language.
 *
 * Types:
 *   - PDF              -> first-page bitmap via pdfjs-dist
 *   - DOCX             -> mammoth HTML → paper page → bitmap
 *   - XLSX/XLS         -> sheetjs grid w/ column letters + row numbers → bitmap
 *   - TXT/MD/CSV/JSON  -> monospace paper page → bitmap
 */

export type DocPreview =
  | { kind: "image"; dataUrl: string; width: number; height: number }
  | { kind: "unsupported" };

// US Letter at ~96 DPI, the de-facto digital paper size.
const PAPER_W = 816;
const PAPER_H = 1056;
// Half-res snapshot keeps thumbnails crisp on retina without bloating cache.
const SNAPSHOT_SCALE = 0.5;

// PDF.js worker is configured once on first call.
let pdfjsLibPromise: Promise<typeof import("pdfjs-dist")> | null = null;

async function getPdfjs() {
  if (!pdfjsLibPromise) {
    pdfjsLibPromise = (async () => {
      const lib = await import("pdfjs-dist");
      const workerUrl = new URL(
        "pdfjs-dist/build/pdf.worker.mjs",
        import.meta.url,
      ).toString();
      lib.GlobalWorkerOptions.workerSrc = workerUrl;
      return lib;
    })();
  }
  return pdfjsLibPromise;
}


// ---------- shared paper-page helpers ----------

function makePaperPage(): HTMLDivElement {
  const page = document.createElement("div");
  page.style.cssText = `
    position: absolute;
    left: -100000px;
    top: 0;
    width: ${PAPER_W}px;
    height: ${PAPER_H}px;
    background: #ffffff;
    color: #1a1a1a;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    box-sizing: border-box;
    padding: 72px;
    overflow: hidden;
  `;
  return page;
}

async function snapshotPaper(el: HTMLElement): Promise<DocPreview> {
  const html2canvas = (await import("html2canvas")).default;
  document.body.appendChild(el);
  try {
    const canvas = await html2canvas(el, {
      scale: SNAPSHOT_SCALE,
      backgroundColor: "#ffffff",
      logging: false,
      // Avoid CORS issues from any external resources mammoth might inline.
      useCORS: false,
      allowTaint: false,
    });
    return {
      kind: "image",
      dataUrl: canvas.toDataURL("image/png"),
      width: canvas.width,
      height: canvas.height,
    };
  } finally {
    document.body.removeChild(el);
  }
}


// ---------- PDF ----------

export async function renderPdfThumbnail(blob: Blob, maxWidth = 480): Promise<DocPreview> {
  const lib = await getPdfjs();
  const buffer = await blob.arrayBuffer();
  const doc = await lib.getDocument({ data: buffer }).promise;
  try {
    const page = await doc.getPage(1);
    const viewport = page.getViewport({ scale: 1 });
    const scale = Math.min(2, maxWidth / viewport.width);
    const v = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(v.width);
    canvas.height = Math.ceil(v.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) return { kind: "unsupported" };
    await page.render({ canvasContext: ctx, viewport: v }).promise;
    return {
      kind: "image",
      dataUrl: canvas.toDataURL("image/png"),
      width: canvas.width,
      height: canvas.height,
    };
  } finally {
    doc.destroy();
  }
}


// ---------- DOCX ----------

export async function renderDocxPreview(blob: Blob): Promise<DocPreview> {
  const mammoth = await import("mammoth/mammoth.browser");
  const buffer = await blob.arrayBuffer();
  const { value } = await mammoth.convertToHtml({ arrayBuffer: buffer });

  const page = makePaperPage();
  // Word-document typography. Drop mammoth's default styling pre-emptively
  // since it's variable across versions.
  page.innerHTML = value;
  // Trim any oversized elements so the snapshot focuses on the content.
  for (const img of Array.from(page.querySelectorAll("img"))) {
    (img as HTMLElement).style.maxWidth = "100%";
    (img as HTMLElement).style.height = "auto";
  }
  for (const tbl of Array.from(page.querySelectorAll("table"))) {
    (tbl as HTMLElement).style.borderCollapse = "collapse";
    (tbl as HTMLElement).style.width = "100%";
  }
  for (const td of Array.from(page.querySelectorAll("td, th"))) {
    (td as HTMLElement).style.border = "1px solid #d0d7de";
    (td as HTMLElement).style.padding = "4px 8px";
  }
  for (const h of Array.from(page.querySelectorAll("h1"))) {
    (h as HTMLElement).style.cssText += "font-size:24px;margin:0 0 12px 0;font-weight:600;";
  }
  for (const h of Array.from(page.querySelectorAll("h2"))) {
    (h as HTMLElement).style.cssText += "font-size:19px;margin:16px 0 8px 0;font-weight:600;";
  }
  for (const h of Array.from(page.querySelectorAll("h3, h4"))) {
    (h as HTMLElement).style.cssText += "font-size:16px;margin:12px 0 6px 0;font-weight:600;";
  }
  for (const p of Array.from(page.querySelectorAll("p"))) {
    (p as HTMLElement).style.margin = "0 0 10px 0";
  }
  return snapshotPaper(page);
}


// ---------- XLSX ----------

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!),
  );
}

function columnLetter(i: number): string {
  let n = i + 1;
  let s = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

export async function renderXlsxPreview(blob: Blob): Promise<DocPreview> {
  const xlsx = await import("xlsx");
  const buffer = await blob.arrayBuffer();
  const wb = xlsx.read(buffer, { type: "array" });
  const sheetName = wb.SheetNames[0];
  if (!sheetName) return { kind: "unsupported" };
  const sheet = wb.Sheets[sheetName];
  const allRows: string[][] = xlsx.utils.sheet_to_json(sheet, {
    header: 1,
    defval: "",
    blankrows: false,
  });
  const rows = allRows.slice(0, 36).map((r) => r.map((c) => String(c ?? "")));
  const maxCols = Math.min(
    14,
    Math.max(1, ...rows.map((r) => r.length)),
  );

  const cellStyle =
    "padding:2px 8px;border:1px solid #e5e7eb;min-width:64px;" +
    "max-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" +
    "vertical-align:middle;";
  const headerCellStyle =
    "padding:2px 8px;border:1px solid #d0d7de;background:#f0f3f5;" +
    "color:#5f6368;font-weight:500;font-size:11px;text-align:center;" +
    "vertical-align:middle;";

  let body = "";
  // Column-letter row.
  body +=
    `<tr>` +
    `<th style="${headerCellStyle}min-width:32px;width:32px;">&nbsp;</th>`;
  for (let c = 0; c < maxCols; c++) {
    body += `<th style="${headerCellStyle}">${columnLetter(c)}</th>`;
  }
  body += `</tr>`;
  // Data rows with row numbers.
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    body +=
      `<tr>` +
      `<th style="${headerCellStyle}min-width:32px;width:32px;">${i + 1}</th>`;
    for (let c = 0; c < maxCols; c++) {
      const cell = row[c] ?? "";
      const isNumeric = cell !== "" && !isNaN(Number(cell));
      const align = isNumeric ? "right" : "left";
      body += `<td style="${cellStyle}text-align:${align};">${escapeHtml(cell)}</td>`;
    }
    body += `</tr>`;
  }

  const page = makePaperPage();
  page.style.padding = "20px";
  page.style.fontSize = "12px";
  page.innerHTML =
    `<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;">` +
    `<div style="font-size:13px;color:#1a1a1a;margin-bottom:8px;font-weight:500;">${escapeHtml(sheetName)}</div>` +
    `<table style="border-collapse:collapse;table-layout:fixed;">` +
    `<tbody>${body}</tbody>` +
    `</table>` +
    `</div>`;
  return snapshotPaper(page);
}


// ---------- text-like ----------

export async function renderPlainText(blob: Blob, maxChars = 4000): Promise<DocPreview> {
  const text = (await blob.text()).slice(0, maxChars);
  const page = makePaperPage();
  page.style.fontFamily =
    '"SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace';
  page.style.fontSize = "12px";
  page.style.lineHeight = "1.5";
  page.style.whiteSpace = "pre-wrap";
  page.style.wordBreak = "break-word";
  page.style.color = "#1f2937";
  page.textContent = text;
  return snapshotPaper(page);
}


// ---------- routing ----------

const PDF_EXT = /\.pdf$/i;
const DOCX_EXT = /\.docx$/i;
const XLSX_EXT = /\.xlsx?$/i;
const TEXT_EXT = /\.(txt|md|csv|log|json|xml|html|htm|tsv|yaml|yml|toml|ini|sql)$/i;

export function previewerFor(filename: string | null) {
  if (!filename) return null;
  if (PDF_EXT.test(filename)) return renderPdfThumbnail;
  if (DOCX_EXT.test(filename)) return renderDocxPreview;
  if (XLSX_EXT.test(filename)) return renderXlsxPreview;
  if (TEXT_EXT.test(filename)) return renderPlainText;
  return null;
}


// ---------- module-scoped LRU cache ----------

const cache = new Map<string, DocPreview>();
const MAX_CACHE = 60;

export function getCachedPreview(id: string): DocPreview | undefined {
  return cache.get(id);
}

export function setCachedPreview(id: string, preview: DocPreview): void {
  if (cache.size >= MAX_CACHE) {
    const first = cache.keys().next().value;
    if (first !== undefined) cache.delete(first);
  }
  cache.set(id, preview);
}
