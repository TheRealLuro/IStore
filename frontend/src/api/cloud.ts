// §C2 — cloud sync client. The hosted deployment may not have OAuth
// configured (`google_oauth_client_id` / `github_oauth_client_id`
// empty in `.env`), in which case every endpoint returns 503. Callers
// should catch + treat 503 as "show the 'configure cloud sync first'
// hint" rather than a hard error.

import { api } from "./client";

export type CloudProvider = "google_drive" | "github";

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
