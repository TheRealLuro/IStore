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
  const [theme, setTheme] = useStateMap(readTheme());
  const [zoom, setZoom] = useStateMap(2);
  const [moveTick, setMoveTick] = useStateMap(0);
  const [active, setActive] = useStateMap(null); // {items, lead, pos:{lat,lng}}

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
  //     512). 60px is the visual sweet spot for this map size — large
  //     enough that nearby pins merge cleanly, small enough that two
  //     city-block-apart pins stay distinct at street zoom.
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
    const sc = new SuperclusterCtor({ radius: 60, maxZoom: 16, minPoints: 2 });
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
  // URL keyed by image id, and re-render the marker layer when the cache
  // gains entries (the layer effect lists `thumbBlobs` as a dep).
  //
  // We never fetch thumbs for clusters — only the lead of a 1-item pin
  // gets one. This caps work to "currently visible single pins" instead
  // of the whole library. On unmount, all blob URLs are revoked.
  const [thumbBlobs, setThumbBlobs] = useStateMap({});
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
    map.on("zoomend", () => setZoom(map.getZoom()));
    map.on("move", () => setMoveTick(t => t + 1));
    map.on("click", () => setActive(null));
    // Leaflet measures the container at init; if our parent was hidden or
    // sized at zero (e.g. lazy chunk just resolved, transitions), we get a
    // blank tile pane. invalidateSize after a frame fixes it cheaply.
    requestAnimationFrame(() => map.invalidateSize());
    return () => { map.remove(); mapRef.current = null; };
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

  // Render clustered pins. Two paths:
  //   - Supercluster (preferred): query the spatial index for the
  //     current viewport + zoom. O(N) build + O(visible) per render.
  //   - Pixel-space fallback (if supercluster wasn't available): the
  //     pre-supercluster O(N×K) loop. Fine up to ~2000 pins. We emit
  //     the same {properties: {cluster?, point_count?, cluster_id?, item?},
  //     geometry: {coordinates: [lng, lat]}} shape so the divIcon
  //     code below doesn't branch.
  useEffectMap(() => {
    const map = mapRef.current; if (!map) return;
    if (layerRef.current) { map.removeLayer(layerRef.current); layerRef.current = null; }
    if (!itemsWithLoc.length) return;

    let clusters;
    if (clusterIndex) {
      // Supercluster path. The bbox is [west, south, east, north] in
      // lng/lat; integer zoom matches supercluster's bucketing.
      const bnds = map.getBounds();
      const bbox = [bnds.getWest(), bnds.getSouth(), bnds.getEast(), bnds.getNorth()];
      const z = Math.floor(map.getZoom());
      clusters = clusterIndex.getClusters(bbox, z);
    } else {
      // Pixel-space fallback. Same shape as supercluster output so the
      // marker-building loop below doesn't need to know which path
      // produced it.
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
      clusters = pixelClusters.map((c) => {
        const ll = map.containerPointToLatLng([c.cx, c.cy]);
        if (c.items.length > 1) {
          return {
            properties: { cluster: true, point_count: c.items.length, cluster_id: null, items: c.items },
            geometry: { coordinates: [ll.lng, ll.lat] },
          };
        }
        return {
          properties: { cluster: false, item: c.items[0] },
          geometry: { coordinates: [ll.lng, ll.lat] },
        };
      });
    }

    // Single-pin thumb fetcher. Walk the cluster set, identify each
    // 1-item pin whose lead has a thumb URL but no cached blob, and
    // kick off one auth-fetch per missing entry. Already-fetched blobs
    // are reused; we never re-fetch. The thumbBlobs dep on this
    // effect re-runs the render once a fetch completes.
    const needed = [];
    for (const c of clusters) {
      if (c.properties.cluster) continue;
      const lead = c.properties.item;
      if (lead.thumb && !thumbBlobs[lead.id]) needed.push(lead);
    }
    for (const lead of needed) {
      setThumbBlobs((prev) =>
        prev[lead.id] !== undefined ? prev : { ...prev, [lead.id]: null },
      );
      fetchAsBlobUrl(lead.thumb)
        .then((blob) => {
          setThumbBlobs((prev) => {
            if (prev[lead.id]) {
              try { URL.revokeObjectURL(prev[lead.id]); } catch {}
            }
            return { ...prev, [lead.id]: blob };
          });
        })
        .catch(() => { /* leave as null — pin renders dot variant */ });
    }

    const group = L.layerGroup();
    for (const c of clusters) {
      const [lng, lat] = c.geometry.coordinates;
      const props = c.properties;
      let html;
      let onClick;
      if (props.cluster) {
        const count = props.point_count;
        const label = count > CLUSTER_LABEL_CAP ? `${CLUSTER_LABEL_CAP}+` : String(count);
        html = `<div class="map4-pin map4-pin--cluster" data-size="${count}"><span>${label}</span></div>`;
        // Click a cluster -> zoom in. Two paths:
        //   - Supercluster: ask `getClusterExpansionZoom` for the
        //     exact zoom at which this cluster splits — most
        //     accurate, never over-zooms.
        //   - Fallback: just zoom in by 2 (capped at maxZoom) since
        //     the pixel-space clusterer has no per-cluster expansion
        //     metadata. Fine for the small libraries the fallback
        //     handles in practice.
        const clusterId = props.cluster_id;
        onClick = () => {
          let nextZoom;
          if (clusterIndex && clusterId != null) {
            nextZoom = clusterIndex.getClusterExpansionZoom(clusterId);
          } else {
            nextZoom = map.getZoom() + 2;
          }
          nextZoom = Math.min(nextZoom, map.getMaxZoom());
          map.flyTo([lat, lng], nextZoom, { duration: 0.5 });
        };
      } else {
        const lead = props.item;
        const blob = lead.thumb ? thumbBlobs[lead.id] : null;
        html = blob
          ? `<div class="map4-pin"><span class="map4-pin__thumb" style="background-image:url(${blob})"></span></div>`
          : `<div class="map4-pin"><span class="map4-pin__dot"></span></div>`;
        onClick = (e) => {
          L.DomEvent.stopPropagation(e);
          setActive({
            items: [lead],
            lead,
            pos: L.latLng(lat, lng),
          });
        };
      }
      const isCluster = !!props.cluster;
      const icon = L.divIcon({
        className: "map4-pin-wrap",
        html,
        iconSize: isCluster ? [36, 36] : [40, 48],
        iconAnchor: isCluster ? [18, 18] : [20, 46],
      });
      const marker = L.marker([lat, lng], { icon });
      marker.on("click", onClick);
      marker.addTo(group);
    }

    group.addTo(map);
    layerRef.current = group;
  }, [clusterIndex, itemsWithLoc, zoom, theme, thumbBlobs, moveTick]);

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

  // Compute screen position of active popup
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
