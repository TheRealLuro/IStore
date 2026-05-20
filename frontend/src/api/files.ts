import { api, API_BASE_URL } from "./client";
import type { FileItem, FileCategory } from "@/types/file";

const REQUIRE_SIGNED_DOWNLOADS =
  import.meta.env.VITE_REQUIRE_SIGNED_DOWNLOADS === "true";

export interface ListFilters {
  category?: FileCategory | "all";
  person?: string | null;
  personId?: number | null;
  /** null = root view, uuid = inside that folder, "ALL" sentinel = ignore filter (search). */
  folderId?: string | null | "ALL";
  /** Cross-folder "starred only" view (overrides folderId server-side). */
  starred?: boolean;
  /** Cross-folder soft-deleted view (overrides folderId server-side). */
  trashed?: boolean;
  /** CLIP-classified scene label (e.g. "office", "portrait", "whiteboard"). */
  scene?: string | null;
  /** "photo" | "screenshot" | "document" | etc. — CLIP content_type column. */
  contentType?: string | null;
  /** "indoor" | "outdoor". */
  indoorOutdoor?: string | null;
  /** Restrict to images whose face_detections row count > 0. */
  hasFaces?: boolean | null;
  /** Restrict to images that have GPS coordinates persisted. */
  hasGps?: boolean | null;
  /** §C9 — single-tag label filter; AND-composes with everything else. */
  tag?: string | null;
  /** §C9 — `lat,lng,radius_km` Haversine bounding-box on image_geo.
   *  Requires gps_retention consent (server returns 403 otherwise). */
  near?: string | null;
  /** §C9 — `ISO,ISO` date range against COALESCE(image_geo.taken_at,
   *  uploaded_at). Either side can be empty for open-ended. */
  takenBetween?: string | null;
  limit?: number;
  offset?: number;
}

export interface FacetPerson {
  id: number;
  display_name: string;
  count: number;
}

export interface FacetTag {
  id: number;
  label: string;
  color: string | null;
  count: number;
}

export interface FacetsResponse {
  total: number;
  scenes: { value: string; count: number }[];
  content_types: { value: string; count: number }[];
  indoor_outdoor: { value: string; count: number }[];
  with_gps: number;
  with_faces: number;
  by_category?: Record<string, number>;
  trash_count?: number;
  /** §C9 — added in this pass. */
  starred_count: number;
  date_range: { earliest: string | null; latest: string | null };
  tags: FacetTag[];
  /** Empty list when face_recognition consent is off. */
  persons: FacetPerson[];
}

/** Available filter axes + counts for the gallery filter chips. The
 *  frontend uses this to render only chips that would return results. */
export const getFacets = (): Promise<FacetsResponse> =>
  api.get<FacetsResponse>("/images/facets");

export async function listFiles(filters: ListFilters = {}): Promise<FileItem[]> {
  const params = new URLSearchParams();
  if (filters.category && filters.category !== "all") {
    params.set("category", filters.category);
  }
  if (filters.person) {
    params.set("person", filters.person);
  }
  if (filters.personId != null) {
    params.set("person_id", String(filters.personId));
  }
  if (filters.trashed) {
    params.set("trashed", "true");
  } else if (filters.starred) {
    params.set("starred", "true");
  } else if (filters.folderId === "ALL") {
    // Bypass the folder scope — used by global search and the people tray.
    params.set("all", "true");
  } else if (filters.folderId) {
    params.set("folder_id", filters.folderId);
  }
  if (filters.scene) params.set("scene", filters.scene);
  if (filters.contentType) params.set("content_type", filters.contentType);
  if (filters.indoorOutdoor) params.set("indoor_outdoor", filters.indoorOutdoor);
  if (filters.hasFaces != null) params.set("has_faces", String(filters.hasFaces));
  if (filters.hasGps != null) params.set("has_gps", String(filters.hasGps));
  // §C9 — multi-axis additions: tag label, near=lat,lng,radius_km,
  // taken_between=ISO,ISO. All AND-compose with the chips above.
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.near) params.set("near", filters.near);
  if (filters.takenBetween) params.set("taken_between", filters.takenBetween);
  // Default behavior (no folderId / no starred) returns root images only —
  // same as passing folder_id=null on the backend.
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.offset) params.set("offset", String(filters.offset));
  const qs = params.toString();
  return api.get<FileItem[]>(`/images/${qs ? `?${qs}` : ""}`);
}

export async function uploadFile(file: File): Promise<FileItem> {
  const fd = new FormData();
  fd.append("file", file);
  return api.post<FileItem>("/images/", fd);
}

export async function deleteFile(id: string, { purge = false }: { purge?: boolean } = {}): Promise<void> {
  // Default = soft delete → row lands in Trash with deleted_at set,
  // user can restore. purge=true bypasses the trash and hard-deletes
  // immediately, used by the per-row "Permanently delete" action in
  // the trash view.
  await api.delete(`/images/${id}${purge ? "?purge=true" : ""}`);
}

export async function bulkDelete(ids: string[], { purge = false }: { purge?: boolean } = {}): Promise<{
  count: number;
  deleted: string[];
  skipped: string[];
  skipped_count: number;
}> {
  return api.post<{
    count: number;
    deleted: string[];
    skipped: string[];
    skipped_count: number;
  }>(`/images/bulk-delete${purge ? "?purge=true" : ""}`, ids);
}

export async function bulkRestore(ids: string[]): Promise<{ count: number }> {
  return api.post<{ count: number }>("/images/bulk-restore", ids);
}

/** Move N images to a folder (or back to root with `folderId=null`).
 *  Owner-scoped server-side — backend reports `skipped` for ids it
 *  refused (foreign-owned, in trash, or not found). */
export async function bulkMove(
  ids: string[],
  folderId: string | null,
): Promise<{ moved: number; skipped: number; folder_id: string | null }> {
  return api.post<{ moved: number; skipped: number; folder_id: string | null }>(
    "/images/bulk-move",
    { ids, folder_id: folderId },
  );
}

/** Create a new folder AND move the supplied images into it in one
 *  transaction. Replaces the awkward two-step "create folder, then go
 *  back and move files" flow when the user multi-selects. */
export async function createFolderWithImages(
  name: string,
  imageIds: string[],
  parentFolderId: string | null = null,
): Promise<{ id: string; name: string; item_count: number }> {
  return api.post<{ id: string; name: string; item_count: number }>(
    "/folders/with-images",
    { name, image_ids: imageIds, parent_folder_id: parentFolderId },
  );
}

/** §C1.4 — clear the user's search history. Today the recent
 *  queries live in browser localStorage so the FE clears them
 *  locally too; this call writes an audit row + is the hook for the
 *  future server-side recent-queries store. Returns 204; safe to
 *  catch + ignore for offline operation. */
export async function clearSearchHistory(): Promise<void> {
  await api.delete("/search/history");
}

export async function searchSemantic(q: string, limit = 30): Promise<(FileItem & { score: number })[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return api.get<(FileItem & { score: number })[]>(`/search/?${params.toString()}`);
}

/** Compute the CLIP text-space embedding for every summary that
 *  has one but no `summary_clip_embedding`. Runs synchronously
 *  server-side (no Redis queue) because the cost is ~10 ms / row
 *  on GPU. Returns `{eligible, filled}` so the FE can show a
 *  progress chip. */
export async function backfillSummaryEmbeddings(
  limit = 1000,
): Promise<{ eligible: number; filled: number }> {
  return api.post<{ eligible: number; filled: number }>(
    `/images/backfill-summary-embeddings?limit=${limit}`,
  );
}

export async function backfillSummaries(
  limit = 500,
  force = false,
  category?: "image" | "video" | "document" | "audio" | "other",
): Promise<{ queued: number; limit: number; force: boolean }> {
  const params = new URLSearchParams({
    limit: String(limit),
    force: String(force),
  });
  if (category) params.set("category", category);
  return api.post<{ queued: number; limit: number; force: boolean }>(
    `/images/backfill-summaries?${params.toString()}`,
  );
}

/** Best-of-N: score a multi-selection and return them ranked. The modal
 *  uses the returned breakdown to render per-criterion bars; the top
 *  result is the recommended keeper. `mode='burst'` returns one
 *  winner per detected cluster; `mode='use_case'` tilts the composite
 *  toward CLIP-cosine similarity to the named prompt
 *  ("portrait" / "landscape" / "social" / "print" / "candid" / "pet"). */
export interface BestOfScore {
  image_id: string;
  score: number;
  breakdown: Record<string, number>;
  cluster_id: number;
  reasons: string[];
}
export interface BestOfResponse {
  mode: "overall" | "burst" | "use_case";
  use_case: string | null;
  /** The CLIP prompt the backend actually ran — preset prompt looked up
   *  via the canned dictionary OR free text wrapped in the
   *  "a sharp well-composed photograph of <text>" template. Null when
   *  no use-case prompt was applied. UI surfaces this so users can see
   *  exactly what their typed prompt got expanded to. */
  resolved_prompt: string | null;
  results: BestOfScore[];
}
export async function pickBestOf(
  imageIds: string[],
  opts: { mode?: "overall" | "burst" | "use_case"; useCase?: string } = {},
): Promise<BestOfResponse> {
  return api.post<BestOfResponse>("/images/best-of", {
    image_ids: imageIds,
    mode: opts.mode || "overall",
    use_case: opts.useCase || null,
  });
}

/** Re-run the vision classification pass on images missing scene/content_type.
 *  Mainly used to populate filter-chip metadata on cloud-synced (Drive)
 *  images, since the cloud-sync ingest path intentionally skips vision at
 *  upload. Without this, gallery filters except "Has people" show no
 *  choices because the facets query returns empty lists for everything
 *  except face counts.
 */
export async function backfillVision(
  limit = 500,
): Promise<{ examined: number; processed: number; failed: number; limit: number }> {
  const params = new URLSearchParams({ limit: String(limit) });
  return api.post<{ examined: number; processed: number; failed: number; limit: number }>(
    `/images/backfill-vision?${params.toString()}`,
  );
}

export interface SummarizeProgress {
  total: number;
  pending: number;
  completed: number;
  has_any_summary: boolean;
}

export const getSummarizeProgress = () =>
  api.get<SummarizeProgress>("/images/summarize-progress");

export async function resummarize(id: string): Promise<{ image_id: string; pending_summary: boolean }> {
  return api.post<{ image_id: string; pending_summary: boolean }>(
    `/images/${id}/resummarize`,
  );
}

/** Toggle the star/favorite flag on an image. Backend flips current state
 *  and stamps `starred_at` on the OFF→ON transition. Returns the updated row. */
export async function toggleStar(id: string): Promise<FileItem> {
  return api.post<FileItem>(`/images/${id}/star`);
}

/** Rename an image's display filename. Backend validates path separators,
 *  Windows-reserved names, extension preservation, and 255-byte cap. */
export async function renameImage(id: string, name: string): Promise<FileItem> {
  return api.patch<FileItem>(`/images/${id}/name`, { name });
}

/** §C1.2 — fetch ≤ N AI-suggested filename proposals. Read-only;
 *  reuses the cached summary signals on the image row, so a rapid
 *  "Regenerate" press is cheap. The user picks one and the existing
 *  `renameImage()` path validates + persists. */
export interface NameSuggestion {
  name: string;
  why: string;
}
export interface SuggestNamesResponse {
  image_id: string;
  current_name: string | null;
  pending_summary: boolean;
  suggestions: NameSuggestion[];
}
export async function suggestNames(
  id: string,
  limit = 3,
): Promise<SuggestNamesResponse> {
  return api.get<SuggestNamesResponse>(
    `/images/${id}/suggest-names?limit=${limit}`,
  );
}

/** URL for image cards / preview (compressed served variant). Includes auth via fetch wrapper. */
export function servedUrl(id: string, opts: { maxDim?: number } = {}): string {
  // The backend supports a `?max_dim=` query param that returns a
  // resized + re-encoded variant (LRU-cached server-side). Pass it
  // for gallery cards / preview-pane thumbs so we don't ship a 3 MB
  // WebP just to render a 200 px square.
  const qs = opts.maxDim ? `?max_dim=${Math.max(64, Math.min(4096, opts.maxDim | 0))}` : "";
  return `${API_BASE_URL}/images/${id}/served${qs}`;
}

export function originalUrl(id: string): string {
  return `${API_BASE_URL}/images/${id}/original`;
}

export interface PdfMeta {
  page_count: number;
  pages: { w: number; h: number }[];
}

/** Read PDF page count + per-page dimensions (in PDF points). Used by the
 *  preview modal page stack to reserve scroll height before rasters land. */
export const getPdfMeta = (id: string): Promise<PdfMeta> =>
  api.get<PdfMeta>(`/images/${id}/pdf-meta`);

/** URL for a single rasterized PDF page. Hit via `useAuthedBlobUrl` so the
 *  JWT travels in the Authorization header (same as `originalUrl`).
 *  `width` is the desired bitmap width in physical pixels — call sites
 *  multiply CSS width × devicePixelRatio for crisp rendering. */
export const pdfPageUrl = (id: string, page: number, width: number): string =>
  `${API_BASE_URL}/images/${id}/pdf-page/${page}?width=${Math.round(width)}`;

export async function getDownloadUrl(
  id: string,
  variant: "original" | "served",
): Promise<string> {
  const params = new URLSearchParams({ variant });
  const res = await api.get<{ url: string; expires_at: string }>(
    `/images/${id}/download-url?${params.toString()}`,
  );
  return res.url;
}

/** Signed streaming URL for video / audio playback. The returned URL
 *  embeds an HMAC signature + expiry, so the browser can stick it
 *  straight into <video src=>/<audio src=> without needing to send
 *  the Bearer token (which media elements can't carry). Default TTL
 *  is 1 hour, refresh from the FE on the element's `error` event.
 *
 *  Pass `quality` (e.g. "1080p", "720p", "480p") to request a
 *  specific tier; the label is baked into the signature so the URL
 *  can't be replayed against a different tier. Omit to get the
 *  default (highest available). */
export async function getStreamUrl(
  id: string, quality?: string,
): Promise<string> {
  const qs = quality ? `?q=${encodeURIComponent(quality)}` : "";
  const res = await api.get<{ url: string; expires_at: string }>(
    `/images/${id}/stream-url${qs}`,
  );
  return res.url;
}

export interface StreamQuality {
  label: string;
  url: string;
  expires_at: string;
}

export interface HlsRendition {
  label: string;
  width: number;
  height: number;
  bandwidth_bps: number;
}

export interface StreamInfo {
  /** "hls" when the row was transcoded by the 2026-05+ HLS pipeline.
   *  Older / non-HLS rows omit this field. */
  mode?: "hls";
  /** Relative URL to the master.m3u8 manifest. Frontend resolves
   *  against API_BASE_URL and feeds to hls.js. Present only when
   *  `mode === "hls"`. */
  hls_url?: string;
  renditions?: HlsRendition[];
  /** Legacy per-quality signed mp4 URLs. Empty array when
   *  `mode === "hls"` since hls.js handles quality switching. */
  default_quality: string;
  qualities: StreamQuality[];
}

/** Enumerate the quality tiers available for a video / audio file.
 *  The player's quality menu reads this once when the modal opens,
 *  renders one entry per tier, and swaps `video.src` to the
 *  picked tier's URL when the user changes the selection. Rows
 *  uploaded before multi-quality landed return a single "auto"
 *  entry pointing at the legacy served bytes. */
export async function getStreamInfo(id: string): Promise<StreamInfo> {
  return api.get<StreamInfo>(`/images/${id}/stream-info`);
}

/** Build an authed URL for the raw original bytes — used by the
 *  client-side parsers (CSV / ICS / VCF) that read the file as text
 *  via `fetchMediaBlob`. Centralizes the API_BASE prefix so callers
 *  don't recompute it. */
export const originalMediaUrl = (id: string): string =>
  `${API_BASE_URL}/images/${id}/original`;

function parseMediaUrl(url: string): { id: string; variant: "original" | "served" } | null {
  try {
    const u = new URL(url, API_BASE_URL);
    const m = u.pathname.match(/\/images\/([^/]+)\/(original|served)$/);
    if (!m) return null;
    return { id: m[1], variant: m[2] as "original" | "served" };
  } catch {
    return null;
  }
}

export async function fetchMediaBlob(url: string): Promise<Blob> {
  let target = url;
  const parsed = parseMediaUrl(url);
  if (REQUIRE_SIGNED_DOWNLOADS && parsed) {
    target = await getDownloadUrl(parsed.id, parsed.variant);
  }
  // Cookie auth: send the session cookie via `credentials: "include"`.
  // Legacy localStorage Bearer is added as a transitional fallback for
  // users mid-migration; will be removed once all login paths emit a
  // cookie. Signed-URL downloads carry their own HMAC in the query
  // string and don't need auth.
  let legacyAuth: HeadersInit | undefined;
  try {
    const legacy = localStorage.getItem("neuthek.jwt") || localStorage.getItem("istore.jwt");
    if (legacy) legacyAuth = { Authorization: `Bearer ${legacy}` };
  } catch { /* private browsing */ }
  const res = await fetch(target, {
    credentials: REQUIRE_SIGNED_DOWNLOADS && parsed ? "omit" : "include",
    headers: REQUIRE_SIGNED_DOWNLOADS && parsed ? {} : (legacyAuth || {}),
  });
  if (!res.ok) throw new Error(`Fetch failed: ${res.status}`);
  return await res.blob();
}

/** Build a blob URL for a file's served variant (used by <img>/preview). */
export async function fetchAsBlobUrl(url: string): Promise<string> {
  return URL.createObjectURL(await fetchMediaBlob(url));
}

// C3 — GPS map view.
export interface GeoPoint {
  id: string;
  lat: number;
  lng: number;
  taken_at: string | null;
  original_filename: string | null;
}

export interface ImageGeoResponse {
  consent: boolean;
  points: GeoPoint[];
}

export const getImageGeo = () => api.get<ImageGeoResponse>("/images/geo");

/** Re-extracts EXIF GPS from existing originals and populates the
 *  `image_geo` table. Used when the user grants `gps_retention` consent
 *  *after* uploading — the photos retain EXIF in MinIO so a backfill
 *  brings them onto the map. Requires the consent scope to be active. */
export const backfillImageGeo = () =>
  api.post<{ examined: number; inserted: number }>("/images/geo/backfill");

/** Reverse-geocode every `image_geo` row that has lat/lng but no
 *  human-readable place. Rate-limited per Nominatim ToS (1 rps); a
 *  large backfill can take minutes. Returns counts so the UI can toast
 *  progress; safe to call repeatedly. */
export const backfillImagePlaces = () =>
  api.post<{ examined: number; filled: number }>("/images/geo/backfill-places");

/** Generate page-1 thumbnail rasters for existing PDFs that don't have
 *  one (uploaded before the at-upload rasterizer was wired). Returns
 *  `{examined, generated}`; safe to call repeatedly. */
export const backfillDocThumbs = () =>
  api.post<{ examined: number; generated: number }>("/images/backfill-doc-thumbs");
