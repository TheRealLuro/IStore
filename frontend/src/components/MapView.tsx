import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, MapPin } from "lucide-react";
import { getImageGeo } from "@/api/files";

/** C3 — GPS map view.
 *
 * Pulls `GET /images/geo` and renders points on a maplibre-gl map over
 * the OpenStreetMap raster tile server (no API key required, BSD-licensed).
 * Clusters are computed client-side via supercluster — typical libraries
 * have <10k points so it's fine on the main thread.
 *
 * The maplibre + supercluster modules are dynamically imported so the
 * rest of the app builds even before `npm install` picks up the new
 * dependencies. If either module is missing we fall back to a list view
 * with raw coordinates so the user still sees their data. */
export function MapView() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [libsReady, setLibsReady] = useState<boolean | null>(null);
  const [libs, setLibs] = useState<{
    maplibre: typeof import("maplibre-gl");
    Supercluster: typeof import("supercluster");
  } | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["geo"],
    queryFn: getImageGeo,
    refetchOnWindowFocus: false,
  });

  // Lazy-load maplibre and supercluster. The /* @vite-ignore */ comment
  // tells Vite's import analysis to skip statically resolving these,
  // which lets the page still load even if the optional deps haven't
  // been `npm install`ed yet (we degrade to a coordinate list below).
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      import(/* @vite-ignore */ "maplibre-gl"),
      import(/* @vite-ignore */ "supercluster"),
      import(/* @vite-ignore */ "maplibre-gl/dist/maplibre-gl.css"),
    ])
      .then(([maplibre, sc]) => {
        if (cancelled) return;
        setLibs({
          maplibre: maplibre as typeof import("maplibre-gl"),
          Supercluster: (sc as { default: typeof import("supercluster") }).default,
        });
        setLibsReady(true);
      })
      .catch(() => {
        if (cancelled) return;
        setLibsReady(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Render the map once we have libs + data + a non-zero number of points.
  useEffect(() => {
    if (!libsReady || !libs || !data?.consent || !data.points.length) return;
    const node = containerRef.current;
    if (!node) return;

    const map = new libs.maplibre.Map({
      container: node,
      // OSM raster tile style — minimal JSON inline to avoid a fetch.
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          { id: "osm", type: "raster", source: "osm", minzoom: 0, maxzoom: 22 },
        ],
      },
      center: [data.points[0].lng, data.points[0].lat],
      zoom: 4,
    });

    const cluster = new libs.Supercluster({ radius: 60, maxZoom: 16 });
    cluster.load(
      data.points.map((p) => ({
        type: "Feature",
        properties: { imageId: p.id },
        geometry: { type: "Point", coordinates: [p.lng, p.lat] },
      })),
    );

    const update = () => {
      const bounds = map.getBounds();
      const bbox: [number, number, number, number] = [
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ];
      const zoom = Math.round(map.getZoom());
      const features = cluster.getClusters(bbox, zoom);

      // Clear prior markers via a per-render container; cheap because
      // typical viewports show <100 clusters at a time.
      document
        .querySelectorAll(".mlgl-cluster-marker")
        .forEach((el) => el.remove());

      for (const f of features) {
        const [lng, lat] = f.geometry.coordinates as [number, number];
        const props = f.properties as { cluster?: boolean; point_count?: number };
        const el = document.createElement("div");
        el.className = "mlgl-cluster-marker";
        if (props.cluster) {
          el.textContent = String(props.point_count ?? "");
          el.style.cssText =
            "background:rgba(59,130,246,0.92);color:white;border-radius:9999px;padding:4px 10px;font-size:12px;font-weight:600;box-shadow:0 1px 3px rgb(0 0 0 / 0.25);cursor:pointer;";
        } else {
          el.style.cssText =
            "width:10px;height:10px;border-radius:9999px;background:rgb(59,130,246);box-shadow:0 0 0 3px rgba(59,130,246,0.25);";
        }
        new libs.maplibre.Marker({ element: el }).setLngLat([lng, lat]).addTo(map);
      }
    };

    map.on("load", update);
    map.on("moveend", update);
    return () => {
      map.remove();
    };
  }, [libsReady, libs, data]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[60vh] text-fg-secondary gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading map…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-[60vh] text-fg-secondary text-sm">
        Could not load map data.
      </div>
    );
  }

  if (data && !data.consent) {
    return (
      <div className="max-w-md mx-auto mt-12 p-6 bg-card rounded-3xl shadow-card text-center">
        <MapPin className="h-8 w-8 text-accent mx-auto mb-2" />
        <h3 className="text-lg font-semibold text-fg">Map view is off</h3>
        <p className="text-sm text-fg-secondary mt-2">
          IStore strips GPS coordinates from photos by default. Enable
          <strong> GPS retention</strong> in Account → Privacy to plot
          your photos on a map.
        </p>
      </div>
    );
  }

  if (data && data.points.length === 0) {
    return (
      <div className="max-w-md mx-auto mt-12 p-6 bg-card rounded-3xl shadow-card text-center">
        <MapPin className="h-8 w-8 text-accent mx-auto mb-2" />
        <h3 className="text-lg font-semibold text-fg">No GPS data yet</h3>
        <p className="text-sm text-fg-secondary mt-2">
          None of your uploaded photos contain EXIF GPS. Upload a photo
          taken with location services enabled and it&apos;ll appear here.
        </p>
      </div>
    );
  }

  if (libsReady === false) {
    // maplibre-gl / supercluster aren't installed — render a graceful list.
    return (
      <div className="px-6 py-4">
        <div className="rounded-2xl bg-warning/10 border border-warning/30 p-4 mb-4 text-sm text-fg-secondary">
          <strong className="text-warning">Map library not installed.</strong>{" "}
          Run <code className="bg-card px-1 py-0.5 rounded">npm install</code>{" "}
          in <code className="bg-card px-1 py-0.5 rounded">frontend/</code>{" "}
          to enable the interactive map. Coordinates are listed below in the meantime.
        </div>
        <ul className="space-y-1 text-sm">
          {data?.points.map((p) => (
            <li key={p.id} className="font-mono text-[12px] text-fg-secondary">
              {p.lat.toFixed(5)}, {p.lng.toFixed(5)} — {p.original_filename || p.id}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="w-full h-[calc(100vh-260px)] rounded-3xl overflow-hidden shadow-card mx-6"
    />
  );
}
