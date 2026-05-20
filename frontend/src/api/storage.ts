import { api } from "./client";
import type { ReclaimResponse, StorageUsage } from "@/types/file";

export async function getStorageUsage(): Promise<StorageUsage> {
  return api.get<StorageUsage>("/storage/usage");
}

/** Force-drop every retained original for the current user. The
 *  served (compressed) copy stays — only the originals bucket bytes
 *  go. Idempotent. */
export async function freeOriginalsNow(): Promise<ReclaimResponse> {
  return api.post<ReclaimResponse>("/storage/free-originals", {});
}

/** Drop the non-default video quality variants for the current
 *  user. Default playback tier survives. Idempotent. */
export async function freeQualityVariants(): Promise<ReclaimResponse> {
  return api.post<ReclaimResponse>("/storage/free-variants", {});
}

export type RetentionPolicy = "immediate" | "24h" | "7d" | "30d" | "forever";

export interface RetentionPolicyResponse {
  policy: RetentionPolicy;
  bytes_freed?: number;
  items_dropped?: number;
  rebased?: number;
}

export async function getRetentionPolicy(): Promise<RetentionPolicyResponse> {
  return api.get<RetentionPolicyResponse>("/storage/retention-policy");
}

export async function setRetentionPolicy(
  policy: RetentionPolicy,
  rebaseExisting = true,
): Promise<RetentionPolicyResponse> {
  return api.patch<RetentionPolicyResponse>(
    "/storage/retention-policy",
    { policy, rebase_existing: rebaseExisting },
  );
}
