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

/** List folders directly inside `parentId` (null = root view). */
export async function listFolders(parentId: string | null): Promise<Folder[]> {
  const params = new URLSearchParams();
  if (parentId) params.set("parent_id", parentId);
  const qs = params.toString();
  return api.get<Folder[]>(`/folders/${qs ? `?${qs}` : ""}`);
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

