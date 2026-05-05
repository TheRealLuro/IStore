import { api } from "./client";

export type CloudProvider =
  | "google_drive"
  | "github"
  | "dropbox"
  | "onedrive";

export interface CloudLink {
  id: number;
  provider: string;
  status: string;
  scopes: string | null;
  last_synced_at: string | null;
  created_at: string;
}

export const listCloudLinks = () => api.get<CloudLink[]>("/cloud/links");

/** Initiates the OAuth handshake. Returns the auth URL the FE should
 * redirect the user to. The backend stub currently returns 503 until
 * encrypted secret storage (A2/A3) is in place — the FE shows
 * "Coming soon" copy in that case. */
export const connectProvider = (provider: CloudProvider) =>
  api.post<{ auth_url: string; state: string }>(`/cloud/links/${provider}`);

export const revokeCloudLink = (id: number) =>
  api.delete<void>(`/cloud/links/${id}`);

export const syncCloudLink = (id: number) =>
  api.post<{ seen: number; pulled: number; provider: string }>(
    `/cloud/links/${id}/sync`,
  );
