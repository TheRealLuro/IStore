// §C2/C4.6 — cloud sync client. The hosted deployment may not have
// OAuth configured (`*_oauth_client_id` empty in `.env`), in which
// case `getCloudProviders` reports those providers as `needs_setup`
// rather than `available`. Callers should gate the Connect button
// on status.

import { api } from "./client";

// §C4.6 — provider IDs from backend.cloud_sync.list_providers. The
// "available + needs_setup" set is real providers; "coming_soon"
// IDs (icloud / mega / box / pcloud) are placeholder slots and
// can't be passed to /cloud/links/{provider}.
export type CloudProvider =
  | "google_drive"
  | "dropbox"
  | "icloud"
  | "mega"
  | "box"
  | "pcloud";

export interface CloudProviderInfo {
  id: CloudProvider;
  name: string;
  kind: "oauth2" | "app_password" | "credentials";
  status: "available" | "needs_setup" | "coming_soon";
  blurb: string;
  docs: string | null;
}

export async function getCloudProviders(): Promise<CloudProviderInfo[]> {
  return api.get<CloudProviderInfo[]>("/cloud/providers");
}

export interface CloudLink {
  id: number;
  provider: CloudProvider;
  status: "active" | "conflicts" | "error";
  scopes: string | null;
  last_synced_at: string | null;
  created_at: string;
  ai_opted_in: boolean;
}

export interface ConnectResponse {
  auth_url: string;
  state: string;
}

export interface SyncResult {
  seen: number;
  pulled: number;
  skipped_unchanged: number;
  conflicts: number;
  conflict_remote_ids: string[];
  provider: CloudProvider;
}

export interface ConflictItem {
  remote_id: string | null;
  remote_path: string | null;
  reason: string | null;
  at: string | null;
}

export async function listCloudLinks(): Promise<CloudLink[]> {
  return api.get<CloudLink[]>("/cloud/links");
}

export async function connectCloud(provider: CloudProvider): Promise<ConnectResponse> {
  return api.post<ConnectResponse>(`/cloud/links/${provider}`);
}

export async function syncCloudLink(linkId: number): Promise<SyncResult> {
  return api.post<SyncResult>(`/cloud/links/${linkId}/sync`);
}

/** Poll the status of an in-flight sync. The /sync endpoint kicks
 *  off a background task and returns immediately; this endpoint
 *  reports the actual progress (state / counts / error). */
export interface SyncStatus {
  state: "idle" | "running" | "done" | "error";
  started_at: string | null;
  finished_at: string | null;
  counts: SyncResult | null;
  error: string | null;
}
export async function getSyncStatus(linkId: number): Promise<SyncStatus> {
  return api.get<SyncStatus>(`/cloud/links/${linkId}/sync-status`);
}

export async function disconnectCloud(linkId: number): Promise<void> {
  await api.delete(`/cloud/links/${linkId}`);
}

export async function setCloudAiOptIn(
  linkId: number,
  optedIn: boolean,
): Promise<{ affected: number; provider: CloudProvider; opted_in: boolean }> {
  return api.post(`/cloud/links/${linkId}/ai-opt-in`, { opted_in: optedIn });
}

export async function listCloudConflicts(linkId: number): Promise<{
  provider: CloudProvider;
  conflicts: ConflictItem[];
}> {
  return api.get(`/cloud/links/${linkId}/conflicts`);
}

/** Total file count + byte size on the provider's side. Walks the
 *  provider API live (~3s on a 50k-file Drive). Returns null when
 *  no link exists, the link is revoked, or the API call failed —
 *  the storage panel hides the totals row in that case. */
export interface ProviderFolderStats {
  provider: string;
  file_count: number;
  total_bytes: number;
}
export async function getProviderFolderStats(
  provider: CloudProvider,
): Promise<ProviderFolderStats | null> {
  return api.get<ProviderFolderStats | null>(`/cloud/folder-stats/${provider}`);
}
