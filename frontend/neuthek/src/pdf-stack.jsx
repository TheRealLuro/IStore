// PDF page-stack viewer.
//
// Replaces the previous `<iframe src="blob:.../#view=Fit">` PDF preview.
// The iframe approach put the document inside Chromium's PDFium viewer,
// which means:
//   - the scrollbar belonged to PDFium (we could only nudge its palette
//     via `color-scheme`, not actually theme it),
//   - `view=Fit` paginated the doc — the user saw one page at a time and
//     had to click through, instead of seeing the full document height,
//   - PDFium's own chrome (page nav, side thumbs) leaks through on hover
//     even with all the `#toolbar=0&navpanes=0` flags set.
//
// The replacement renders each page server-side via PyMuPDF
// (`GET /images/{id}/pdf-page/{n}?width=...`) and streams the pages into
// a vertically-stacked, themed scroll container. Pages lazy-load via
// IntersectionObserver so a 200-page PDF doesn't blow up the browser on
// modal open; each page wrapper reserves the right scroll height from
// the meta call before its raster lands so the scrollbar doesn't jump.

import React, { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getPdfMeta, pdfPageUrl } from "@/api/files";
import { useAuthedBlobUrl } from "./auth-image.jsx";

// Default render width per page (CSS pixels before DPR). The modal body
// caps at ~1100px wide on desktop minus 28px padding × 2; rendering at
// 1100 means we ask the backend for ~1100 × devicePixelRatio physical
// pixels (capped at 4096 by the endpoint), which stays crisp at 2x
// retina without bloating JPEG size.
const DEFAULT_CSS_WIDTH = 1100;

function PdfPage({ fileId, pageIndex, aspectRatio, cssWidth, rootRef }) {
  const wrapRef = useRef(null);
  const [inView, setInView] = useState(false);

  // Trip `inView` once the page wrapper crosses into the scroll container's
  // viewport (or within 600px of it — root margin pre-fetches the next
  // page so a fast scroll doesn't show empty boxes). After the first
  // trip we stop observing — the blob URL is kept in memory by
  // `useAuthedBlobUrl` for the lifetime of this component.
  useEffect(() => {
    if (inView) return;
    const el = wrapRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setInView(true);
            obs.disconnect();
            break;
          }
        }
      },
      { root: rootRef?.current || null, rootMargin: "600px 0px 600px 0px", threshold: 0.01 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [inView, rootRef]);

  // DPR-aware width keeps page rasters crisp on retina without doubling
  // bandwidth on standard displays. Clamped to the endpoint's 4096 max.
  const dpr = typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 2) : 1;
  const renderWidth = Math.min(4096, Math.round(cssWidth * dpr));
  const src = inView ? pdfPageUrl(fileId, pageIndex, renderWidth) : null;
  const blob = useAuthedBlobUrl(src);

  // The wrapper height is derived from the page's aspect ratio so the
  // scroll container's total height is correct from the moment meta
  // resolves — pages slot in without nudging the user's scroll position.
  const pxHeight = Math.round(cssWidth / Math.max(aspectRatio, 0.001));

  return (
    <div
      ref={wrapRef}
      className="pdf-page"
      style={{ width: `${cssWidth}px`, height: `${pxHeight}px` }}
      data-page={pageIndex + 1}
    >
      {blob ? (
        <img src={blob} alt={`Page ${pageIndex + 1}`} className="pdf-page__img" />
      ) : (
        <div className="pdf-page__skeleton">
          <span>{pageIndex + 1}</span>
        </div>
      )}
    </div>
  );
}

export function PdfPageStack({ fileId }) {
  const scrollRef = useRef(null);
  const [cssWidth, setCssWidth] = useState(DEFAULT_CSS_WIDTH);

  // Pages are sized to the available width of the scroll container so
  // they fit cleanly without horizontal scroll. We observe the
  // container's width and resize the page wrappers — uniform width
  // means uniform page raster width, which lets the backend cache
  // heavily by (image_id, page, width).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setCssWidth(Math.max(320, Math.min(DEFAULT_CSS_WIDTH, w)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { data: meta, isLoading, error } = useQuery({
    queryKey: ["pdf-meta", fileId],
    queryFn: () => getPdfMeta(fileId),
    enabled: !!fileId,
    staleTime: 5 * 60 * 1000,
  });

  if (error) {
    return (
      <div className="pdf-stack__state">
        Could not read PDF
        <div className="pdf-stack__state-sub">{error?.detail || error?.message || ""}</div>
      </div>
    );
  }
  if (isLoading || !meta) {
    return <div className="pdf-stack__state">Loading document…</div>;
  }
  if (meta.page_count === 0) {
    return <div className="pdf-stack__state">Empty PDF</div>;
  }

  return (
    <div className="pdf-stack" ref={scrollRef}>
      <div className="pdf-stack__inner">
        {meta.pages.map((p, i) => (
          <PdfPage
            key={i}
            fileId={fileId}
            pageIndex={i}
            aspectRatio={p.w / Math.max(p.h, 1)}
            cssWidth={cssWidth - 28}
            rootRef={scrollRef}
          />
        ))}
      </div>
    </div>
  );
}
