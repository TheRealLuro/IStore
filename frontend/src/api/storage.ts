import { api } from "./client";
import type { StorageUsage } from "@/types/file";

export async function getStorageUsage(): Promise<StorageUsage> {
  return api.get<StorageUsage>("/storage/usage");
}
