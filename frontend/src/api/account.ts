import { api, API_BASE_URL, tokens } from "./client";

export interface DeleteResult {
  deleted_user_id: string;
  images_deleted: number;
  faces_deleted: number;
  persons_deleted: number;
  blobs_deleted: number;
  blob_errors: number;
}

export const deleteAccount = () => api.post<DeleteResult>("/account/delete");

/** Trigger a browser download of the export ZIP. Authenticated via JWT. */
export async function downloadExport(): Promise<void> {
  const token = tokens.get();
  const res = await fetch(`${API_BASE_URL}/account/export`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    throw new Error(`Export failed: ${res.status} ${res.statusText}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `istore-export-${Date.now()}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
