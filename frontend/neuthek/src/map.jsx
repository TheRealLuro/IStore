// Map view — Leaflet + OpenStreetMap (CartoDB Voyager/DarkMatter tiles).
// Real interactive world map with proper roads, cities, oceans.
//
// IMPORTANT: the leaflet host (`<div ref={hostRef} className="map4-canvas"/>`)
// must always render, even when there are no GPS points yet. Otherwise the
// init `useEffect([])` runs once with `hostRef.current === null` (because the
// empty-state branch was rendering instead), the early-return fires, and the
// map never initializes. When `runBackfill` later adds points and the parent
// re-renders, the canvas shows up but the init effect — already done firing —
// doesn't run again. Result: the user clicks "scan", the count updates to 1,
// the screen goes blank. Switching tabs forces a remount which "fixes" it.
import React, {
  useState as useStateMap,
  useEffect as useEffectMap,
  useRef as useRefMap,
  useMemo as useMemoMap,
  useCallback as useCallbackMap,
} from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import toast from "react-hot-toast";
import { useQueryClient } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import { AuthedThumb } from "./auth-image.jsx";
import { backfillImageGeo, backfillImagePlaces, fetchAsBlobUrl } from "@/api/files";

// Supercluster is dynamically loaded so a missing install (the package
// has to land in whatever node_modules the dev server actually reads —
// container mounts can be tricky) doesn't crash Vite's import-analysis
// and block the entire app behind a build-error overlay. If the import
// fails, we fall back to a simple O(N×K) pixel-space clusterer (the
// pre-supercluster behavior); it's fine up to ~2000 pins, which covers
// the vast majority of libraries.
let _SuperclusterPromise = null;
function loadSupercluster() {
  if (!_SuperclusterPromise) {
    _SuperclusterPromise = import("supercluster").then(
      (m) => m.default || m,
      () => null,
    );
  }
  return _SuperclusterPromise;
}

// World bounds. Leaflet's default lets you pan into the void above /
// below the map and reveal the body background — feels broken. Lock
// panning to a slightly-padded mercator window so the user can never
// scroll into white space. ±85° latitude is the conventional Mercator
// limit (poles distort to infinity past that).
const WORLD_BOUNDS = L.latLngBounds(
  L.latLng(-85, -180),
  L.latLng(85, 180),
);

// Anything bigger than this in a single cluster shows "500+" instead
// of the literal count. Keeps the badge legible at any zoom.
const CLUSTER_LABEL_CAP = 500;

// Resolve theme from <html data-theme>
function readTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

const TILE_LIGHT = {
  url: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OSM</a> · © <a href="https://carto.com/attributions">CARTO</a>',
};
const TILE_DARK = {
  url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OSM</a> · © <a href="https://carto.com/attributions">CARTO</a>',
};

export function MapView({ items, onPick }) {
  const hostRef = useRefMap(null);
  const mapRef = useRefMap(null);
  const tileRef = useRefMap(null);
  const layerRef = useRefMap(null);
  // Per-pin marker registry, keyed by lead image id. Lets the thumb-blob
  // effect patch one pin's DOM in place instead of rebuilding the layer.
  const markersRef = useRefMap(new Map());
  const [theme, setTheme] = useStateMap(readTheme());
  const [zoom, setZoom] = useStateMap(2);
  // Throttled snapshot of the map view that drives clustering. Updated on
  // moveend/zoomend via a single rAF (see init effect) so a fast pan emits
  // ~one recompute per settled frame instead of one per `move` event. The
  // bbox is rounded to ~5 decimals to absorb sub-pixel jitter.
  const [view, setView] = useStateMap({ bbox: null, z: 3 });
  const viewRafRef = useRefMap(0);
  const [active, setActive] = useStateMap(null); // {items, lead, pos:{lat,lng}}
  // Bumped on every `move` *only while a popup is open*, so the popup tracks
  // its pin during a pan. The marker layer no longer re-renders on `move`
  // (it's gated on clusterSig), so this re-render is cheap — it just
  // reprojects the popup anchor and reconciles the toolbar/popup DOM.
  const [popTick, setPopTick] = useStateMap(0);

  const itemsWithLoc = useMemoMap(() => {
    return (items || [])
      .filter(i => i && i.gps && i.gps.lat != null && i.gps.lng != null)
      .map(i => ({ ...i, lat: i.gps.lat, lng: i.gps.lng, placeName: i.gps.place }));
  }, [items]);

  // Supercluster index. Loaded asynchronously so a missing install
  // falls back gracefully instead of crashing the Vite import graph.
  // `null` ctor = "supercluster module wasn't available" — the render
  // effect below detects that and uses the pixel-space fallback.
  //
  //   radius: cluster radius in PIXELS at the index's "extent" (default
  //     512). 80px merges near-coincident pins into one clickable cluster
  //     instead of leaving them as an unclickable overlapping stack — the
  //     "hard to select one" the user reported. The tradeoff vs the old
  //     60px: two genuinely-distinct places ~80px apart on screen now
  //     cluster together until you zoom one step further in; clicking the
  //     cluster (which fly-zooms to its expansion zoom) splits them, so
  //     they're never permanently hidden.
  //   maxZoom: clustering stops at this zoom level; past it, every
  //     point renders individually. 16 ≈ "block-level" — at higher
  //     zoom the user wants to see each photo, not a cluster.
  //   minPoints: minimum to form a cluster. 2 = standard.
  const [SuperclusterCtor, setSuperclusterCtor] = useStateMap(null);
  useEffectMap(() => {
    let cancelled = false;
    loadSupercluster().then((Ctor) => {
      if (!cancelled) setSuperclusterCtor(() => Ctor);
    });
    return () => { cancelled = true; };
  }, []);

  const clusterIndex = useMemoMap(() => {
    if (!itemsWithLoc.length || !SuperclusterCtor) return null;
    const sc = new SuperclusterCtor({ radius: 80, maxZoom: 16, minPoints: 2 });
    sc.load(
      itemsWithLoc.map((it) => ({
        type: "Feature",
        properties: { item: it },
        geometry: { type: "Point", coordinates: [it.lng, it.lat] },
      })),
    );
    return sc;
  }, [itemsWithLoc, SuperclusterCtor]);

  // Authed blob URL cache for single-pin thumbnails. Pins live inside a
  // leaflet `divIcon` whose HTML is a static string — we can't drop a
  // React component in there, so the `<img src>` / CSS background must
  // already be a blob URL by the time the divIcon is built. Pre-fetch
  // each unique single-pin thumb with the bearer token, store the blob
  // URL keyed by image id, and patch the matching marker's DOM in place
  // when the cache gains entries (a separate effect, below — we do NOT
  // tear down and rebuild the whole marker layer just because one thumb
  // arrived; that was a big part of the lag).
  //
  // We never fetch thumbs for clusters — only the lead of a 1-item pin
  // gets one. Combined with supercluster only returning pins inside the
  // current viewport, this caps work to "visible single pins" instead of
  // the whole library. Fetches are run through a small concurrency-capped
  // queue (MAX_THUMB_INFLIGHT) so 178 pins can't open 178 sockets / blob
  // decodes at once and jam the main thread + network. On unmount, all
  // blob URLs are revoked.
  const [thumbBlobs, setThumbBlobs] = useStateMap({});
  const thumbBlobsRef = useRefMap(thumbBlobs);
  thumbBlobsRef.current = thumbBlobs;
  // Fetch-queue plumbing (refs so it survives re-renders without
  // re-creating the queue): the pending lead list, how many fetches are
  // currently in flight, and the set of ids already queued/fetched so we
  // never enqueue the same id twice.
  const MAX_THUMB_INFLIGHT = 6;
  const thumbQueueRef = useRefMap([]);
  const thumbInflightRef = useRefMap(0);
  const thumbSeenRef = useRefMap(new Set());

  const pumpThumbQueue = useCallbackMap(() => {
    while (
      thumbInflightRef.current < MAX_THUMB_INFLIGHT &&
      thumbQueueRef.current.length
    ) {
      const lead = thumbQueueRef.current.shift();
      if (!lead || !lead.thumb) continue;
      if (thumbBlobsRef.current[lead.id]) continue; // already resolved
      thumbInflightRef.current += 1;
      fetchAsBlobUrl(lead.thumb)
        .then((blob) => {
          setThumbBlobs((prev) => {
            if (prev[lead.id]) {
              try { URL.revokeObjectURL(prev[lead.id]); } catch {}
            }
            return { ...prev, [lead.id]: blob };
          });
        })
        .catch(() => { /* leave unset — pin keeps the dot variant */ })
        .finally(() => {
          thumbInflightRef.current -= 1;
          pumpThumbQueue();
        });
    }
  }, [setThumbBlobs]);

  // Enqueue a list of single-pin leads for thumb fetching. Dedupes via
  // `thumbSeenRef`, skips anything already resolved, then pumps the queue.
  const enqueueThumbs = useCallbackMap((leads) => {
    let added = false;
    for (const lead of leads) {
      if (!lead || !lead.thumb) continue;
      if (thumbSeenRef.current.has(lead.id)) continue;
      if (thumbBlobsRef.current[lead.id]) continue;
      thumbSeenRef.current.add(lead.id);
      thumbQueueRef.current.push(lead);
      added = true;
    }
    if (added) pumpThumbQueue();
  }, [pumpThumbQueue]);

  useEffectMap(() => {
    return () => {
      // Revoke all on unmount to release memory.
      setThumbBlobs((current) => {
        for (const url of Object.values(current)) {
          if (url) URL.revokeObjectURL(url);
        }
        return {};
      });
    };
  }, []);

  // Watch theme attribute on <html>
  useEffectMap(() => {
    const obs = new MutationObserver(() => setTheme(readTheme()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  // Init Leaflet once. Runs unconditionally now that the host always renders.
  useEffectMap(() => {
    if (!hostRef.current || mapRef.current) return;
    const map = L.map(hostRef.current, {
      center: [25, 10],
      zoom: 3,
      // minZoom 3 keeps the entire usable world inside the viewport even
      // on wide displays. Below 3 the mercator projection wraps the map
      // smaller than the container and exposes the body background — the
      // "white when zooming out" the user reported.
      minZoom: 3,
      maxZoom: 18,
      // Pan no further than the world rectangle. viscosity=1.0 means the
      // edges feel hard (no spongy bounce-back); 0.7 would bounce. Apple
      // Maps uses ~1.0, which is what we want here.
      maxBounds: WORLD_BOUNDS,
      maxBoundsViscosity: 1.0,
      worldCopyJump: false,
      zoomControl: false,
      attributionControl: true,
      scrollWheelZoom: true,
      preferCanvas: true,
      // Smoother zoom feel:
      //   zoomSnap: 0.25 lets fractional zoom levels exist (~4× finer
      //     stepping than the default 1.0 — feels much closer to native).
      //   zoomDelta:   what +/- buttons + wheel single-tick step is.
      //   wheelPxPerZoomLevel: 80px of wheel travel per zoom level (default
      //     60). Bigger = slower, more deliberate.
      //   wheelDebounceTime: 35ms — collapses bursts of wheel events into
      //     a single zoom transition so fast scroll wheels don't visibly
      //     "jump" through levels.
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 80,
      wheelDebounceTime: 35,
      // Animation defaults are on, but make them explicit so they don't
      // depend on Leaflet's runtime browser sniffing.
      fadeAnimation: true,
      zoomAnimation: true,
      markerZoomAnimation: true,
      inertia: true,
    });
    mapRef.current = map;

    // Single rAF-throttled view sync. Both panning (`moveend`) and zooming
    // (`zoomend`) funnel here; coalescing on a frame means a flick-pan that
    // fires many intermediate events still only recomputes clusters once
    // the view settles. We snapshot the bbox (rounded) + integer zoom; the
    // clusters memo keys off this, so markers rebuild only when the visible
    // set can actually change — not on every wheel tick mid-pan.
    const syncView = () => {
      if (viewRafRef.current) return;
      viewRafRef.current = requestAnimationFrame(() => {
        viewRafRef.current = 0;
        const m = mapRef.current;
        if (!m) return;
        const b = m.getBounds();
        const r = (n) => Math.round(n * 1e5) / 1e5;
        setView({
          bbox: [r(b.getWest()), r(b.getSouth()), r(b.getEast()), r(b.getNorth())],
          z: Math.floor(m.getZoom()),
        });
        setZoom(m.getZoom());
      });
    };
    map.on("moveend", syncView);
    map.on("zoomend", syncView);
    map.on("click", () => setActive(null));
    // Leaflet measures the container at init; if our parent was hidden or
    // sized at zero (e.g. lazy chunk just resolved, transitions), we get a
    // blank tile pane. invalidateSize after a frame fixes it cheaply.
    requestAnimationFrame(() => { map.invalidateSize(); syncView(); });
    return () => {
      if (viewRafRef.current) cancelAnimationFrame(viewRafRef.current);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Apply tile layer for current theme. The big knob here is `keepBuffer`:
  // Leaflet's default of 2 means only 2 tile-rings around the visible
  // viewport are kept loaded; zoom out fast and you race ahead of the
  // tile loader, exposing the container background until the new pyramid
  // resolves. keepBuffer=4 (~1 extra screen on each side) virtually
  // eliminates that flash for normal interactions.
  useEffectMap(() => {
    const map = mapRef.current; if (!map) return;
    if (tileRef.current) { map.removeLayer(tileRef.current); tileRef.current = null; }
    const cfg = theme === "dark" ? TILE_DARK : TILE_LIGHT;
    tileRef.current = L.tileLayer(cfg.url, {
      attribution: cfg.attribution,
      subdomains: "abcd",
      maxZoom: 19,
      keepBuffer: 4,
      // updateWhenIdle=false → request new tiles continuously during pan;
      // updateWhenZooming=false → defer new requests until zoom settles
      // so we don't waste fetches on intermediate zoom states (those are
      // covered by the still-displayed previous-zoom tiles via Leaflet's
      // zoom animation).
      updateWhenIdle: false,
      updateWhenZooming: false,
      // Cap parallel fetches so a fast pan doesn't open 50 sockets at once.
      crossOrigin: true,
    }).addTo(map);
  }, [theme]);

  // Compute the visible cluster/point set for the current (throttled)
  // view, NORMALIZED into our own lightweight descriptors:
  //   cluster: { cluster:true, count, clusterId, lat, lng }
  //   point:   { cluster:false, item, lat, lng, spider? }
  // We never mutate the objects supercluster returns — for leaf points
  // `getClusters` hands back the very features we loaded, so tagging them
  // in place would leak/persist state across calls. Two source paths:
  //   - Supercluster (preferred): query the spatial index for the
  //     current viewport bbox + integer zoom. O(visible).
  //   - Pixel-space fallback (supercluster unavailable): the original
  //     O(N×K) loop. Fine up to ~2000 pins.
  // Single points that land on the *exact* same coordinate (same photo
  // spot) can never be separated by zooming — supercluster keeps emitting
  // them stacked once past maxZoom. We detect those groups and tag each
  // member with `spider` metadata; the renderer fans them into a ring so
  // every one is individually clickable.
  const clusters = useMemoMap(() => {
    const map = mapRef.current;
    if (!map || !itemsWithLoc.length || !view.bbox) return [];

    const out = [];
    if (clusterIndex) {
      for (const f of clusterIndex.getClusters(view.bbox, view.z)) {
        const [lng, lat] = f.geometry.coordinates;
        if (f.properties.cluster) {
          out.push({
            cluster: true,
            count: f.properties.point_count,
            clusterId: f.properties.cluster_id,
            lat, lng,
          });
        } else {
          out.push({ cluster: false, item: f.properties.item, lat, lng });
        }
      }
    } else {
      // Pixel-space fallback. Project once per point at the current view.
      const pixelClusters = [];
      const radius = 38;
      for (const it of itemsWithLoc) {
        const p = map.latLngToContainerPoint([it.lat, it.lng]);
        let found = null;
        for (const c of pixelClusters) {
          if (Math.hypot(c.cx - p.x, c.cy - p.y) < radius) { found = c; break; }
        }
        if (found) {
          found.items.push(it);
          found.cx = (found.cx * (found.items.length - 1) + p.x) / found.items.length;
          found.cy = (found.cy * (found.items.length - 1) + p.y) / found.items.length;
        } else {
          pixelClusters.push({ cx: p.x, cy: p.y, items: [it] });
        }
      }
      for (const c of pixelClusters) {
        const ll = map.containerPointToLatLng([c.cx, c.cy]);
        if (c.items.length > 1) {
          out.push({ cluster: true, count: c.items.length, clusterId: null, lat: ll.lat, lng: ll.lng });
        } else {
          out.push({ cluster: false, item: c.items[0], lat: ll.lat, lng: ll.lng });
        }
      }
    }

    // Spiderfy exactly-coincident single points. Group by rounded
    // coordinate; any group with >1 member gets fanned in the renderer.
    const groups = new Map();
    for (const c of out) {
      if (c.cluster) continue;
      const key = `${c.lat.toFixed(6)},${c.lng.toFixed(6)}`;
      let g = groups.get(key);
      if (!g) { g = []; groups.set(key, g); }
      g.push(c);
    }
    for (const g of groups.values()) {
      if (g.length < 2) continue;
      g.forEach((c, i) => { c.spider = { i, total: g.length }; });
    }
    return out;
  }, [clusterIndex, itemsWithLoc, view]);

  // Stable signature of the visible set. The marker layer is rebuilt only
  // when THIS changes — i.e. when the clusters/points that should be on
  // screen actually differ (zoom bucket, pan revealing new pins, spiderfy
  // membership). Panning within the same cluster set, or a thumb blob
  // arriving, no longer tears down and recreates ~178 markers.
  const clusterSig = useMemoMap(() => {
    const parts = [];
    for (const c of clusters) {
      if (c.cluster) {
        parts.push(`c${c.clusterId ?? "x"}:${c.count}`);
      } else {
        const sp = c.spider ? `~${c.spider.i}/${c.spider.total}` : "";
        parts.push(`p${c.item.id}${sp}`);
      }
    }
    return parts.join("|");
  }, [clusters]);

  // Build (and rebuild) the Leaflet marker layer. Keyed on the cluster
  // signature + theme only — NOT on thumb blobs (those patch in place,
  // below) and NOT on raw move/zoom events. `clusters` is read live but
  // is recomputed in lockstep with the signature, so this stays correct.
  useEffectMap(() => {
    const map = mapRef.current; if (!map) return;
    if (layerRef.current) { map.removeLayer(layerRef.current); layerRef.current = null; }
    markersRef.current.clear();
    if (!clusters.length) return;

    // Queue thumb fetches for the single pins now on screen (viewport-
    // bounded + concurrency-capped). Clusters never fetch.
    const visibleLeads = [];
    for (const c of clusters) {
      if (!c.cluster) visibleLeads.push(c.item);
    }
    enqueueThumbs(visibleLeads);

    const blobs = thumbBlobsRef.current;
    const group = L.layerGroup();
    for (const c of clusters) {
      const { lat, lng } = c;
      let html;
      let onClick;
      const isCluster = !!c.cluster;
      // Base icon anchor (the icon pixel that sits on the lat/lng). For a
      // spiderfied pin we shift the anchor so the WHOLE marker (its
      // clickable box included) moves to the fanned position, while the
      // geographic latlng — and thus the popup target — stays put.
      let anchorX = isCluster ? 18 : 20;
      let anchorY = isCluster ? 18 : 46;
      if (c.cluster) {
        const count = c.count;
        const label = count > CLUSTER_LABEL_CAP ? `${CLUSTER_LABEL_CAP}+` : String(count);
        html = `<div class="map4-pin map4-pin--cluster" data-size="${count}"><span>${label}</span></div>`;
        // Click a cluster -> smoothly zoom to the level where it splits.
        //   - Supercluster: getClusterExpansionZoom is exact, never over-
        //     zooms.
        //   - Fallback: zoom in by 2 (no per-cluster expansion metadata).
        const clusterId = c.clusterId;
        onClick = () => {
          let nextZoom;
          if (clusterIndex && clusterId != null) {
            nextZoom = clusterIndex.getClusterExpansionZoom(clusterId);
          } else {
            nextZoom = map.getZoom() + 2;
          }
          nextZoom = Math.min(nextZoom, map.getMaxZoom());
          map.flyTo([lat, lng], nextZoom, { duration: 0.45 });
        };
      } else {
        const lead = c.item;
        const blob = lead.thumb ? blobs[lead.id] : null;
        // Spiderfy: fan exactly-coincident photos onto a small pixel ring
        // so each is individually clickable. We move the anchor (not a CSS
        // margin) so the marker's hit box travels with the visible pin —
        // clicking the fanned thumb actually lands. Shifting iconAnchor by
        // (-dx,-dy) moves the icon by (dx,dy) on screen.
        let spiderClass = "";
        if (c.spider && c.spider.total > 1) {
          const { i, total } = c.spider;
          const ring = 26 + Math.max(0, total - 6) * 2;
          const ang = (2 * Math.PI * i) / total - Math.PI / 2;
          const dx = Math.round(Math.cos(ang) * ring);
          const dy = Math.round(Math.sin(ang) * ring);
          anchorX -= dx;
          anchorY -= dy;
          spiderClass = " map4-pin--spider";
        }
        html = blob
          ? `<div class="map4-pin${spiderClass}"><span class="map4-pin__thumb" style="background-image:url(${blob})"></span></div>`
          : `<div class="map4-pin${spiderClass}"><span class="map4-pin__dot"></span></div>`;
        onClick = (e) => {
          L.DomEvent.stopPropagation(e);
          setActive({
            items: [lead],
            lead,
            pos: L.latLng(lat, lng),
          });
        };
      }
      const icon = L.divIcon({
        className: "map4-pin-wrap",
        html,
        iconSize: isCluster ? [36, 36] : [40, 48],
        iconAnchor: [anchorX, anchorY],
      });
      const marker = L.marker([lat, lng], { icon });
      marker.on("click", onClick);
      // Raise this pin above its neighbors while hovered so the topmost of
      // an overlapping set is the one that receives the click. Leaflet's
      // setZIndexOffset bumps the marker's z within the overlay pane.
      marker.on("mouseover", () => marker.setZIndexOffset(1000));
      marker.on("mouseout", () => marker.setZIndexOffset(0));
      marker.addTo(group);
      // Register single pins so the thumb-blob effect can patch them.
      if (!isCluster) markersRef.current.set(c.item.id, marker);
    }

    group.addTo(map);
    layerRef.current = group;
  }, [clusterSig, theme, enqueueThumbs, clusterIndex]);

  // Patch thumbnails into existing single-pin markers as their blobs
  // resolve — in place, without rebuilding the whole layer. We swap the
  // inner `.map4-pin` HTML for the registered marker; cheaper than the
  // old full teardown and it doesn't disturb pins that are already fine.
  useEffectMap(() => {
    for (const [id, marker] of markersRef.current) {
      const blob = thumbBlobs[id];
      if (!blob) continue;
      const el = marker.getElement && marker.getElement();
      if (!el) continue;
      const pin = el.querySelector(".map4-pin");
      const inner = pin && pin.querySelector("span");
      // Already showing this thumb? skip.
      if (inner && inner.classList.contains("map4-pin__thumb")) continue;
      if (inner) {
        inner.className = "map4-pin__thumb";
        inner.style.backgroundImage = `url(${blob})`;
      }
    }
  }, [thumbBlobs, clusterSig]);

  // Keep an open popup glued to its pin during a pan. Only attached while a
  // popup is open, so it costs nothing the rest of the time. We throttle to
  // one reposition per animation frame.
  useEffectMap(() => {
    const map = mapRef.current;
    if (!map || !active) return;
    let raf = 0;
    const onMove = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; setPopTick((t) => t + 1); });
    };
    map.on("move", onMove);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      map.off("move", onMove);
    };
  }, [active]);

  // After the first set of points arrives, fit bounds so the user sees the
  // result of a backfill without manually panning. We only auto-fit once per
  // mount — subsequent updates leave the user's view alone.
  const didFitRef = useRefMap(false);
  useEffectMap(() => {
    if (didFitRef.current) return;
    const map = mapRef.current;
    if (!map || !itemsWithLoc.length) return;
    const bounds = L.latLngBounds(itemsWithLoc.map((i) => [i.lat, i.lng]));
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 12 });
    didFitRef.current = true;
  }, [itemsWithLoc]);

  // Backfill flow — re-extract EXIF GPS from the user's existing
  // originals once they've granted gps_retention consent.
  const qc = useQueryClient();
  const [busy, setBusy] = useStateMap(false);
  const [busyPlaces, setBusyPlaces] = useStateMap(false);
  const runBackfill = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await backfillImageGeo();
      if (r.inserted > 0) {
        toast.success(`Found GPS on ${r.inserted} of ${r.examined} photo(s).`);
      } else if (r.examined > 0) {
        toast(`Scanned ${r.examined} photo(s); none had GPS data.`);
      } else {
        toast("No EXIF-capable photos to scan.");
      }
      qc.invalidateQueries({ queryKey: ["geo"] });
    } catch (e) {
      const detail = e?.detail || "Could not backfill. Make sure GPS retention is on in Settings → Privacy.";
      toast.error(detail);
    } finally {
      setBusy(false);
    }
  };
  const runPlaces = async () => {
    if (busyPlaces) return;
    setBusyPlaces(true);
    try {
      const r = await backfillImagePlaces();
      if (r.filled > 0) {
        toast.success(`Filled location names on ${r.filled} of ${r.examined} pin(s).`);
        qc.invalidateQueries({ queryKey: ["geo"] });
      } else if (r.examined > 0) {
        toast(`Scanned ${r.examined} pin(s); none could be geocoded.`);
      } else {
        toast("No pins need location names yet.");
      }
    } catch (e) {
      toast.error(e?.detail || "Could not fill location names.");
    } finally {
      setBusyPlaces(false);
    }
  };

  const zoomLabel = (z) => {
    if (z <= 3) return "World";
    if (z <= 5) return "Continent";
    if (z <= 7) return "Country";
    if (z <= 9) return "Region";
    if (z <= 12) return "City";
    if (z <= 15) return "District";
    return "Street";
  };

  const zoomBy = (f) => {
    const map = mapRef.current; if (!map) return;
    map.setZoom(Math.max(map.getMinZoom(), Math.min(map.getMaxZoom(), map.getZoom() + f)));
  };
  const reset = () => { mapRef.current?.setView([25, 10], 3); };

  // Compute screen position of active popup. Recomputed every render —
  // `popTick` is bumped on pan while a popup is open (and `zoom` changes
  // on zoom), so the popup re-projects as the map moves under it.
  void popTick;
  let popPos = null;
  if (active && mapRef.current) {
    const p = mapRef.current.latLngToContainerPoint(active.pos);
    popPos = { left: p.x, top: p.y };
  }

  const empty = !itemsWithLoc.length;

  return (
    <div className="map4-shell">
      <div className="map4-toolbar">
        <div className="map4-toolbar__title">{itemsWithLoc.length} files mapped</div>
        <div style={{ flex: 1 }}/>
        {itemsWithLoc.length > 0 && (
          <button
            className="btn btn--ghost btn--sm"
            onClick={runPlaces}
            disabled={busyPlaces}
            title="Reverse-geocode pins so popups show city names instead of coordinates"
            style={{ marginRight: 8 }}
          >
            <Icon name="map_pin" size={12}/>
            {busyPlaces ? "Filling…" : "Fill location names"}
          </button>
        )}
        <div className="map4-toolbar__zoom">
          <button className="btn-icon" onClick={() => zoomBy(-1)} aria-label="Zoom out"><Icon name="minus" size={14}/></button>
          <div className="map4-zoom-label" title={`Zoom level ${zoom}`}>{zoomLabel(zoom)}</div>
          <button className="btn-icon" onClick={() => zoomBy(1)} aria-label="Zoom in"><Icon name="plus" size={14}/></button>
          <button className="btn-icon" onClick={reset} aria-label="Fit all" title="Fit all"><Icon name="maximize" size={14}/></button>
        </div>
      </div>
      <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
        <div ref={hostRef} className="map4-canvas" style={{ position: "absolute", inset: 0 }}/>
        {empty && (
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: "50%",
              transform: "translate(-50%, -50%)",
              zIndex: 500,
              padding: "20px 24px",
              borderRadius: 16,
              background: "var(--surface)",
              border: "1px solid var(--line)",
              boxShadow: "var(--shadow-2)",
              maxWidth: 360,
              textAlign: "center",
            }}
          >
            <div style={{
              width: 40, height: 40, borderRadius: 12,
              background: "var(--surface-2)", color: "var(--ink-2)",
              display: "grid", placeItems: "center",
              margin: "0 auto 10px",
            }}>
              <Icon name="map" size={20}/>
            </div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>No location data yet</div>
            <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 14, lineHeight: 1.5 }}>
              Photos with GPS appear here once you grant <em>GPS retention</em> consent. We can re-scan your existing photos for EXIF locations.
            </div>
            <button className="btn btn--secondary" onClick={runBackfill} disabled={busy}>
              <Icon name="refresh" size={12}/> {busy ? "Scanning…" : "Scan my photos for GPS"}
            </button>
          </div>
        )}
        {active && popPos && (() => {
          const isCluster = active.items.length > 1;
          const lead = active.lead;
          return (
            <div className="map4-pop" style={{ left: popPos.left, top: popPos.top }}>
              <div className="map4-pop__head">
                <div>
                  <div className="map4-pop__title">{isCluster ? `${active.items.length} files` : lead.name}</div>
                  <div className="map4-pop__meta">{lead.placeName || `${lead.lat.toFixed(2)}, ${lead.lng.toFixed(2)}`}</div>
                </div>
                <button className="btn-icon" onClick={() => setActive(null)} aria-label="Close"><Icon name="x" size={14}/></button>
              </div>
              {isCluster ? (
                <div className="map4-pop__grid">
                  {active.items.slice(0, 9).map((it, j) => (
                    <AuthedThumb
                      key={j}
                      url={it.thumb || null}
                      className="map4-pop__tile"
                      onClick={() => { onPick && onPick(it); setActive(null); }}
                      title={it.name}
                    />
                  ))}
                </div>
              ) : (
                <AuthedThumb
                  url={lead.thumb || null}
                  className="map4-pop__tile map4-pop__tile--lg"
                  onClick={() => { onPick && onPick(lead); setActive(null); }}
                />
              )}
            </div>
          );
        })()}
      </div>
    </div>
  );
}

// Named export above; legacy `window.MapView` removed.
