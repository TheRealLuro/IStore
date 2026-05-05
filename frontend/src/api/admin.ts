import { api } from "./client";

export interface UserStorageRow {
  user_id: string;
  email: string;
  display_name: string | null;
  used_bytes: number;
  image_count: number;
  quota_bytes: number;
}

export interface StorageSnapshot {
  total_bytes: number;
  total_images: number;
  by_category: Record<string, number>;
  top_users: UserStorageRow[];
}

export interface AdminUserRead {
  id: string;
  email: string;
  display_name: string | null;
  role: "user" | "admin" | "superuser";
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  quota_bytes: number;
  used_bytes: number;
  image_count: number;
}

export interface AuditEntry {
  id: number;
  user_id: string | null;
  action: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

export const getAdminStorage = (top = 50) =>
  api.get<StorageSnapshot>(`/admin/storage?top=${top}`);

export const listAdminUsers = (q: string | null = null, limit = 100, offset = 0) => {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  if (q) params.set("q", q);
  return api.get<AdminUserRead[]>(`/admin/users?${params.toString()}`);
};

export const updateUserQuota = (userId: string, quotaBytes: number | null) =>
  api.patch<AdminUserRead>(`/admin/users/${userId}/quota`, {
    quota_bytes: quotaBytes,
  });

export const updateUserRole = (
  userId: string,
  role: "user" | "admin" | "superuser",
) => api.patch<AdminUserRead>(`/admin/users/${userId}/role`, { role });

export const listAdminAudit = (params: {
  limit?: number;
  offset?: number;
  userId?: string | null;
  actionPrefix?: string | null;
} = {}) => {
  const q = new URLSearchParams();
  q.set("limit", String(params.limit ?? 200));
  q.set("offset", String(params.offset ?? 0));
  if (params.userId) q.set("user_id", params.userId);
  if (params.actionPrefix) q.set("action_prefix", params.actionPrefix);
  return api.get<AuditEntry[]>(`/admin/audit?${q.toString()}`);
};
