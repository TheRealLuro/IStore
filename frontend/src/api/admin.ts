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

// §1.3 — admin overlay un-mock. Each tab below has a dedicated
// backend endpoint that returns a deliberately small, JSON-friendly
// shape. The FE renders "—" when a field comes back null so the
// dashboard stays usable on any platform.

export interface SystemSnapshot {
  version: string;
  env: string;
  uptime: {
    process_uptime_seconds: number;
    host_uptime_seconds: number | null;
    process_boot_iso: string;
    host_boot_iso: string | null;
    python_version: string;
    platform: string;
  };
  db_pool: {
    reachable: boolean;
    size: number | null;
    checked_in: number | null;
    checked_out: number | null;
    overflow: number | null;
    error?: string;
  };
  redis: {
    reachable: boolean;
    memory_used_bytes?: number;
    memory_peak_bytes?: number;
    dbsize?: number;
    queue_key?: string;
    queue_depth?: number;
    active_jobs?: number;
    error?: string;
  };
  minio: {
    reachable: boolean;
    endpoint?: string;
    buckets?: {
      name: string;
      exists?: boolean | null;
      objects?: number;
      size_bytes?: number;
      error?: string;
    }[];
    error?: string;
  };
}

export interface HardwareSnapshot {
  cpu: {
    brand: string | null;
    logical_cores: number | null;
    physical_cores: number | null;
    percent: number;
    per_core_percent: number[];
    load_avg_1_5_15: number[] | null;
    freq: { current_mhz: number | null; max_mhz: number | null } | null;
  };
  memory: {
    total_bytes: number;
    available_bytes: number;
    used_bytes: number;
    percent: number;
    swap_total_bytes: number;
    swap_used_bytes: number;
  };
  disks: {
    device: string;
    mountpoint: string;
    fstype: string;
    total_bytes: number;
    used_bytes: number;
    free_bytes: number;
    percent: number;
  }[];
  gpu: {
    available: boolean;
    backend: string | null;
    devices: {
      index: number;
      name: string;
      vendor?: string;
      total_memory_bytes?: number | null;
      allocated_memory_bytes?: number | null;
      used_memory_bytes?: number;
      utilization_percent?: number;
      driver_version?: string | null;
      video_processor?: string | null;
      // Set when the adapter is visible at the OS/PCIe level but
      // not usable by this process (e.g. Docker without GPU
      // passthrough, integrated GPU on a discrete-NVIDIA workload).
      inaccessible?: boolean;
    }[];
    notes?: string[];
    driver_version?: string;
  };
}

export interface ProcessRow {
  pid: number;
  name: string;
  username: string;
  cpu_percent: number;
  memory_rss_bytes: number;
  kind: "system" | "ai" | "data" | "api";
  cmdline: string;
}

export interface ProcessSnapshot {
  processes: ProcessRow[];
  totals: {
    count: number;
    cpu_percent_sum: number;
    memory_rss_bytes_sum: number;
  };
}

export interface ModelEntry {
  id: string;
  label: string;
  name: string;
  variant: string | null;
  role: string;
  enabled: boolean;
}

export interface ModelsSnapshot {
  models: ModelEntry[];
  inference_backend: string;
  gpu_available: boolean;
}

export interface TaskSnapshot {
  queue: {
    depth: number;
    active: number;
    reachable: boolean;
    queue_key: string | null;
  };
  recent: {
    id: number;
    user_id: string | null;
    action: string;
    details: Record<string, unknown> | null;
    created_at: string;
  }[];
}

export interface LogsSnapshot {
  lines: {
    id: number;
    created_at: string;
    action: string;
    user_id: string | null;
    details: Record<string, unknown> | null;
  }[];
}

export const getAdminSystem = () =>
  api.get<SystemSnapshot>("/admin/system");

export const getAdminHardware = () =>
  api.get<HardwareSnapshot>("/admin/hardware");

export const getAdminProcesses = (top = 12) =>
  api.get<ProcessSnapshot>(`/admin/processes?top=${top}`);

export const getAdminModels = () =>
  api.get<ModelsSnapshot>("/admin/models");

export const getAdminTasks = () =>
  api.get<TaskSnapshot>("/admin/tasks");

export const getAdminLogs = (limit = 200) =>
  api.get<LogsSnapshot>(`/admin/logs?limit=${limit}`);
