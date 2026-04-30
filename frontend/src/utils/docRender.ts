/**
 * Document mini-render helpers. Lazy-loaded to keep the main bundle small.
 *
 * - PDF  -> first-page bitmap via pdfjs-dist
 * - DOCX -> HTML via mammoth
 * - XLSX -> first-sheet preview via xlsx (sheetjs)
 * - TXT/MD/CSV -> plain text excerpt
 *
 * Each function returns a result that the consumer caches by file id.
 */

export type DocPreview =
  | { kind: "image"; dataUrl: string; width: number; height: number }
  | { kind: "html"; html: string }
  | { kind: "table"; rows: string[][]; sheetName: string }
  | { kind: "text"; text: string }
  | { kind: "unsupported" };

// PDF.js worker is configured once on first call.
let pdfjsLibPromise: Promise<typeof import("pdfjs-dist")> | null = null;

async function getPdfjs() {
  if (!pdfjsLibPromise) {
    pdfjsLibPromise = (async () => {
      const lib = await import("pdfjs-dist");
      // Use Vite's `new URL(..., import.meta.url)` so the worker resolves at
      // both dev-time (served by Vite) and build-time (asset hashed).
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

export async function renderDocxPreview(blob: Blob): Promise<DocPreview> {
  const mammoth = await import("mammoth/mammoth.browser");
  const buffer = await blob.arrayBuffer();
  const { value } = await mammoth.convertToHtml({ arrayBuffer: buffer });
  return { kind: "html", html: value };
}

export async function renderXlsxPreview(blob: Blob, maxRows = 12): Promise<DocPreview> {
  const xlsx = await import("xlsx");
  const buffer = await blob.arrayBuffer();
  const wb = xlsx.read(buffer, { type: "array" });
  const sheetName = wb.SheetNames[0];
  if (!sheetName) return { kind: "unsupported" };
  const sheet = wb.Sheets[sheetName];
  const rows: string[][] = xlsx.utils.sheet_to_json(sheet, {
    header: 1,
    defval: "",
    blankrows: false,
  });
  return {
    kind: "table",
    sheetName,
    rows: rows.slice(0, maxRows).map((r) => r.map(String)),
  };
}

export async function renderPlainText(blob: Blob, maxChars = 1000): Promise<DocPreview> {
  const text = await blob.text();
  return { kind: "text", text: text.slice(0, maxChars) };
}

const PDF_EXT = /\.pdf$/i;
const DOCX_EXT = /\.docx$/i;
const XLSX_EXT = /\.xlsx?$/i;
const TEXT_EXT = /\.(txt|md|csv|log|json|xml|html|htm)$/i;

export function previewerFor(filename: string | null) {
  if (!filename) return null;
  if (PDF_EXT.test(filename)) return renderPdfThumbnail;
  if (DOCX_EXT.test(filename)) return renderDocxPreview;
  if (XLSX_EXT.test(filename)) return renderXlsxPreview;
  if (TEXT_EXT.test(filename)) return renderPlainText;
  return null;
}

/** Module-scoped LRU-ish cache so we don't re-render on every viewport. */
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
