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
//
// Performance: a gallery page can mount ~48 cards at once, each an
// AuthedThumb. Firing every authed blob fetch on mount thrashes the main
// thread and the network. Two throttles fix that:
//   1. LAZY — AuthedThumb/AuthedImg observe their own DOM node with an
//      IntersectionObserver and only begin fetching when the node is in or
//      near the viewport (ROOT_MARGIN of preloading). Pass `eager` to skip
//      the gate for an above-the-fold hero. The bare `useAuthedBlobUrl`
//      hook (no element to observe) stays eager — callers that want
//      laziness already gate by passing a null `url` until visible
//      (see pdf-stack.jsx).
//   2. CONCURRENCY CAP — even when 48 cards become visible in one paint,
//      a module-level queue keeps only MAX_CONCURRENT fetches in flight;
//      the rest drain as slots free. A slot is always released when its
//      fetch settles (success OR error) and a queued task that gets
//      cancelled before it runs never takes a slot — so the queue can't
//      deadlock and a failed/aborted fetch can't strand a slot.

import React, { useEffect, useRef, useState } from "react";
import { fetchAsBlobUrl } from "@/api/files";

// Tunables. 6 concurrent authed fetches keeps the network busy without
// burying the main thread under dozens of simultaneous blob decodes;
// the 300px root margin starts a thumbnail loading just before it
// scrolls into view so it's usually ready by the time it's on screen.
const MAX_CONCURRENT = 6;
const ROOT_MARGIN = "300px";

// ── Module-level fetch queue ───────────────────────────────────────────
// Caps concurrent fetchAsBlobUrl calls. Each enqueued task is a function
// returning a promise; we run up to MAX_CONCURRENT and queue the rest.
let active = 0;
const waiting = [];

function pump() {
  while (active < MAX_CONCURRENT && waiting.length > 0) {
    const task = waiting.shift();
    active += 1;
    // Each task settles its OWN caller (resolve/reject inside enqueueFetch)
    // and returns a promise that we use only to free the slot. That
    // promise is built to ALWAYS fulfill (the task swallows errors for
    // slot-tracking purposes), so `.then` here runs on both success and
    // error and we never leak an unhandled rejection from this chain.
    // Result: every slot is released exactly once — no leak, no deadlock.
    task().then(() => {
      active -= 1;
      pump();
    });
  }
}

/** Run `fn` (which returns a promise) once a concurrency slot is free.
 *  `shouldRun()` is checked the moment the slot opens; if it returns
 *  false (e.g. the consumer unmounted while queued) the task resolves
 *  its caller with undefined WITHOUT calling `fn`, so cancelled work
 *  never starves live work.
 *  Returns a promise that settles like `fn` (or resolves undefined when
 *  skipped). The slot-tracking promise the task returns to pump() always
 *  fulfills, regardless of how `fn` settled. */
function enqueueFetch(fn, shouldRun) {
  return new Promise((resolve, reject) => {
    waiting.push(() => {
      if (shouldRun && !shouldRun()) {
        // Skipped: resolve undefined so the awaiter's .then sees no blob.
        resolve(undefined);
        return Promise.resolve();
      }
      // Route the real outcome to the caller via resolve/reject, but hand
      // pump() a promise that always fulfills so a rejected fetch can't
      // surface as an unhandled rejection on the slot-tracking chain.
      return fn().then(resolve, reject).then(
        () => undefined,
        () => undefined
      );
    });
    pump();
  });
}

/** Returns a blob: URL for `url`, or null while loading / on error.
 *  Each consumer owns its URL and revokes on unmount — StrictMode-safe.
 *
 *  Options:
 *    enabled — when false, the hook does nothing (stays null) and starts
 *              no fetch. Lazy components pass `false` until their element
 *              is near the viewport, then flip to `true`. Defaults to
 *              true, so a bare `useAuthedBlobUrl(url)` call fetches as
 *              soon as `url` is set — unchanged from before. */
export function useAuthedBlobUrl(url, { enabled = true } = {}) {
  const [blob, setBlob] = useState(null);
  useEffect(() => {
    if (!url || !enabled) {
      setBlob(null);
      return;
    }
    let cancelled = false;
    let owned = null;
    // Go through the concurrency queue. `shouldRun` lets the queue skip a
    // task whose consumer already unmounted before its slot opened.
    enqueueFetch(() => fetchAsBlobUrl(url), () => !cancelled)
      .then((b) => {
        if (!b) return; // skipped by the queue (cancelled while waiting)
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
  }, [url, enabled]);
  return blob;
}

/** Tracks whether `ref`'s element is in/near the viewport via a single
 *  IntersectionObserver. Once seen, stays true (we don't want to revoke a
 *  loaded thumbnail just because it scrolled off — that would re-fetch on
 *  scroll-back). When `eager` is true, returns true immediately and never
 *  observes. Hook is unconditional; the observer is created/torn down in
 *  the effect and disconnected on unmount. */
function useInView(ref, eager) {
  const [inView, setInView] = useState(eager);
  useEffect(() => {
    if (eager) {
      setInView(true);
      return;
    }
    const el = ref.current;
    // No element (shouldn't happen) or no IO support → degrade to eager
    // so the image still loads (it just won't be lazy).
    if (!el || typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    let done = false;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            done = true;
            setInView(true);
            io.disconnect();
            break;
          }
        }
      },
      { rootMargin: ROOT_MARGIN }
    );
    io.observe(el);
    return () => {
      if (!done) io.disconnect();
    };
  }, [ref, eager]);
  return inView;
}

/** Drop-in replacement for a div whose only purpose is showing a
 *  background-image. Falls back to `placeholder` while loading (and while
 *  off-screen, since the fetch is deferred until near-viewport).
 *  Pass any normal div props (className, style, onClick…).
 *
 *  `eager` (default false): load immediately instead of waiting for the
 *  element to scroll near the viewport. Use only for an above-the-fold
 *  hero; gallery grids should stay lazy. */
export function AuthedThumb({ url, className, style, placeholder, children, eager = false, ...rest }) {
  const ref = useRef(null);
  const inView = useInView(ref, eager);
  const blob = useAuthedBlobUrl(url, { enabled: inView });
  const merged = blob
    ? { ...style, backgroundImage: `url(${blob})` }
    : { ...style, ...(placeholder || {}) };
  return (
    <div ref={ref} className={className} style={merged} {...rest}>
      {children}
    </div>
  );
}

/** Variant that renders an <img> rather than a CSS background. Use when
 *  intrinsic sizing matters (lightbox/hero).
 *
 *  Lazy like AuthedThumb: while off-screen or loading it renders an
 *  inert placeholder <span> that carries the same className/style so
 *  layout is preserved and the IntersectionObserver has a node to watch;
 *  it swaps to the real <img> once the blob is ready. `eager` (default
 *  false) loads immediately — appropriate for hero/lightbox images that
 *  are always on screen when mounted. */
export function AuthedImg({ url, alt = "", eager = false, className, style, ...rest }) {
  const ref = useRef(null);
  const inView = useInView(ref, eager);
  const blob = useAuthedBlobUrl(url, { enabled: inView });
  if (!blob) {
    // Placeholder node holds the ref so the observer can fire. aria-hidden
    // + role=presentation keep it out of the a11y tree. Forwards
    // className/style so it occupies the same box as the eventual <img>.
    return <span ref={ref} className={className} style={style} aria-hidden="true" role="presentation" />;
  }
  return <img ref={ref} src={blob} alt={alt} className={className} style={style} {...rest} />;
}
