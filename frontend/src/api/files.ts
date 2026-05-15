import { api, API_BASE_URL, tokens } from "./client";
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
  limit?: number;
  offset?: number;
}

export interface FacetsResponse {
  total: number;
  scenes: { value: string; count: number }[];
  content_types: { value: string; count: number }[];
  indoor_outdoor: { value: string; count: number }[];
  with_gps: number;
  with_faces: number;
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

export async function deleteFile(id: string): Promise<void> {
  await api.delete(`/images/${id}`);
}

export async function bulkDelete(ids: string[]): Promise<{ count: number }> {
  return api.post<{ count: number }>("/images/bulk-delete", ids);
}

export async function bulkRestore(ids: string[]): Promise<{ count: number }> {
  return api.post<{ count: number }>("/images/bulk-restore", ids);
}

export async function searchSemantic(q: string, limit = 30): Promise<(FileItem & { score: number })[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return api.get<(FileItem & { score: number })[]>(`/search/?${params.toString()}`);
}

export async function backfillSummaries(
  limit = 500,
  force = false,
): Promise<{ queued: number; limit: number; force: boolean }> {
  const params = new URLSearchParams({
    limit: String(limit),
    force: String(force),
  });
  return api.post<{ queued: number; limit: number; force: boolean }>(
    `/images/backfill-summaries?${params.toString()}`,
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

/** URL for image cards / preview (compressed served variant). Includes auth via fetch wrapper. */
export function servedUrl(id: string): string {
  return `${API_BASE_URL}/images/${id}/served`;
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
  const token = tokens.get();
  const res = await fetch(target, {
    headers:
      REQUIRE_SIGNED_DOWNLOADS && parsed
        ? {}
        : token
          ? { Authorization: `Bearer ${token}` }
          : {},
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
