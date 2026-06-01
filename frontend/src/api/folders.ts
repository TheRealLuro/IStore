import { api } from "./client";
import type { Folder } from "@/types/file";

export interface FolderCreate {
  name: string;
  parent_folder_id?: string | null;
  status?: string | null;
  status_color?: string | null;
}

export interface FolderUpdate {
  name?: string;
  parent_folder_id?: string | null;
  status?: string | null;
  status_color?: string | null;
}

/** §C1.3 — narrow the type-pill filter applied to folder visibility.
 *  When set, only folders whose entire subtree contains ≥ 1 file of
 *  this category come back. `"image" | "video" | "document"` — matches
 *  `Image.category`. */
export type FolderContainsType = "image" | "video" | "document";

/** List folders directly inside `parentId` (null = root view).
 *  Pass `containsType` when the gallery's type pill is active so the
 *  folder grid hides containers that have no matching files anywhere
 *  in their subtree. */
export async function listFolders(
  parentId: string | null,
  containsType?: FolderContainsType | null,
): Promise<Folder[]> {
  const params = new URLSearchParams();
  if (parentId) params.set("parent_id", parentId);
  if (containsType) params.set("contains_type", containsType);
  const qs = params.toString();
  return api.get<Folder[]>(`/folders/${qs ? `?${qs}` : ""}`);
}

/** A single folder match from semantic folder search (`GET /search/folders`).
 *  Mirrors the image search hit shape: the regular folder fields plus a
 *  `score` (0..1, higher = more relevant). `item_count` is the number of
 *  files directly in the folder, used for the "· N items" caption. */
export interface FolderHit {
  id: string;
  name: string;
  parent_folder_id: string | null;
  status: string | null;
  status_color: string | null;
  item_count: number;
  score: number;
}

/** Semantic folder search — the folder analogue of `searchSemantic`.
 *  Returns folders whose name / contents match `q`, ranked by score
 *  (highest first). Empty array when nothing is relevant. Auth is handled
 *  centrally by the api client (session cookie / Bearer), same as image
 *  search. */
export async function searchFolders(q: string, limit = 12): Promise<FolderHit[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return api.get<FolderHit[]>(`/search/folders?${params.toString()}`);
}

export async function createFolder(body: FolderCreate): Promise<Folder> {
  return api.post<Folder>("/folders/", body);
}

export async function updateFolder(
  id: string,
  body: FolderUpdate,
): Promise<Folder> {
  return api.patch<Folder>(`/folders/${id}`, body);
}

export async function deleteFolder(id: string): Promise<void> {
  await api.delete(`/folders/${id}`);
}

/** Move a single image into a folder, or back to the root with `null`. */
export async function moveImageToFolder(
  imageId: string,
  folderId: string | null,
): Promise<void> {
  await api.patch(`/images/${imageId}/move`, { folder_id: folderId });
}

/** Set or clear the project status on an image (label + color key). */
export async function setImageStatus(
  imageId: string,
  status: string | null,
  status_color: string | null,
): Promise<void> {
  await api.patch(`/images/${imageId}/status`, { status, status_color });
}

