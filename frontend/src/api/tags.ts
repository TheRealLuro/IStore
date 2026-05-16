// §C1.6 — user-owned tag CRUD + image/folder attach.
//
// The backend tags endpoints unify what the legacy `images.status` /
// `folders.status` columns used to do. Each user owns their own tag
// rows (RLS forced in migration 0028); colors live alongside the
// label so the chip picks its tint without a second lookup.

import { api } from "./client";

export type TagColor =
  | "gray" | "red" | "orange" | "amber" | "yellow" | "lime" | "green"
  | "emerald" | "teal" | "cyan" | "sky" | "blue" | "indigo" | "violet"
  | "purple" | "fuchsia" | "pink" | "rose";

export const TAG_COLORS: TagColor[] = [
  "gray", "red", "orange", "amber", "yellow", "lime", "green",
  "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet",
  "purple", "fuchsia", "pink", "rose",
];

export interface Tag {
  id: number;
  label: string;
  color: TagColor | null;
  source: "user" | "clip";
  image_count?: number;
  folder_count?: number;
}

export interface TagCreate {
  label: string;
  color?: TagColor | null;
}

export interface TagUpdate {
  label?: string;
  color?: TagColor | null;
}

export interface TagAttachBody {
  // Provide exactly one of these. `label` create-and-attaches if
  // the user doesn't already have a same-case-folded tag.
  tag_id?: number;
  label?: string;
  color?: TagColor | null;
}

export async function listTags(): Promise<Tag[]> {
  return api.get<Tag[]>("/tags/");
}

export async function createTag(body: TagCreate): Promise<Tag> {
  return api.post<Tag>("/tags/", body);
}

export async function updateTag(id: number, body: TagUpdate): Promise<Tag> {
  return api.patch<Tag>(`/tags/${id}`, body);
}

export async function deleteTag(id: number): Promise<void> {
  await api.delete(`/tags/${id}`);
}

/** Attach a tag to an image. Returns the resolved Tag (with id
 *  populated even if `label` was used to create on the fly). */
export async function attachImageTag(
  imageId: string,
  body: TagAttachBody,
): Promise<Tag> {
  return api.post<Tag>(`/images/${imageId}/tags`, body);
}

export async function detachImageTag(
  imageId: string,
  tagId: number,
): Promise<void> {
  await api.delete(`/images/${imageId}/tags/${tagId}`);
}

export async function attachFolderTag(
  folderId: string,
  body: TagAttachBody,
): Promise<Tag> {
  return api.post<Tag>(`/folders/${folderId}/tags`, body);
}

export async function detachFolderTag(
  folderId: string,
  tagId: number,
): Promise<void> {
  await api.delete(`/folders/${folderId}/tags/${tagId}`);
}
