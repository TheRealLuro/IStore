// Authenticated image rendering.
//
// CSS `background-image: url(...)` and <img src> requests can't carry an
// Authorization header, so every protected `/images/{id}/served` and
// `/faces/{id}/crop` request 401s when used directly. Solution mirrors the
// legacy frontend's AuthedImage: fetch with auth → wrap the response in a
// blob: URL → use that. Honors `VITE_REQUIRE_SIGNED_DOWNLOADS=true` (the
// shared `fetchAsBlobUrl` swaps to a signed URL for those calls).
//
// Per-component caching only. An earlier version shared blob URLs across
// consumers via a global ref-counted cache; that broke under React 18
// StrictMode because the first cleanup pass revokes the URL while the
// second mount re-uses the (now-dead) URL.

import React, { useEffect, useState } from "react";
import { fetchAsBlobUrl } from "@/api/files";

/** Returns a blob: URL for `url`, or null while loading / on error.
 *  Each consumer owns its URL and revokes on unmount — StrictMode-safe. */
export function useAuthedBlobUrl(url) {
  const [blob, setBlob] = useState(null);
  useEffect(() => {
    if (!url) {
      setBlob(null);
      return;
    }
    let cancelled = false;
    let owned = null;
    fetchAsBlobUrl(url)
      .then((b) => {
        if (cancelled) {
          URL.revokeObjectURL(b);
          return;
        }
        owned = b;
        setBlob(b);
      })
      .catch(() => {
        if (!cancelled) setBlob(null);
      });
    return () => {
      cancelled = true;
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [url]);
  return blob;
}

/** Drop-in replacement for a div whose only purpose is showing a
 *  background-image. Falls back to `placeholder` while loading.
 *  Pass any normal div props (className, style, onClick…). */
export function AuthedThumb({ url, className, style, placeholder, children, ...rest }) {
  const blob = useAuthedBlobUrl(url);
  const merged = blob
    ? { ...style, backgroundImage: `url(${blob})` }
    : { ...style, ...(placeholder || {}) };
  return (
    <div className={className} style={merged} {...rest}>
      {children}
    </div>
  );
}

/** Variant that renders an <img> rather than a CSS background. Use when
 *  intrinsic sizing matters (lightbox). */
export function AuthedImg({ url, alt = "", ...rest }) {
  const blob = useAuthedBlobUrl(url);
  if (!blob) return null;
  return <img src={blob} alt={alt} {...rest} />;
}
