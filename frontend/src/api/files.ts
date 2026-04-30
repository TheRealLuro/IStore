import { api, API_BASE_URL, tokens } from "./client";
import type { FileItem, FileCategory } from "@/types/file";

export interface ListFilters {
  category?: FileCategory | "all";
  limit?: number;
  offset?: number;
}

export async function listFiles(filters: ListFilters = {}): Promise<FileItem[]> {
  const params = new URLSearchParams();
  if (filters.category && filters.category !== "all") {
    params.set("category", filters.category);
  }
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

/** URL for image cards / preview (compressed served variant). Includes auth via fetch wrapper. */
export function servedUrl(id: string): string {
  return `${API_BASE_URL}/images/${id}/served`;
}

export function originalUrl(id: string): string {
  return `${API_BASE_URL}/images/${id}/original`;
}

/** Build a blob URL for a file's served variant (used by <img>/preview). */
export async function fetchAsBlobUrl(url: string): Promise<string> {
  const token = tokens.get();
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Fetch failed: ${res.status}`);
  return URL.createObjectURL(await res.blob());
}
