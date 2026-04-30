export type FileCategory = "image" | "video" | "document" | "other";

export interface FileItem {
  id: string;
  category: FileCategory;
  original_filename: string | null;
  width: number | null;
  height: number | null;
  byte_size_original: number | null;
  byte_size_served: number | null;
  mime_type_original: string | null;
  mime_type_served: string | null;
  codec: string | null;
  quality: number | null;
  max_dim: number | null;
  lossless: boolean | null;
  content_type: string | null;
  content_confidence: number | null;
  scene_label: string | null;
  scene_confidence: number | null;
  face_likelihood: number | null;
  pending_face_scan: boolean;
  indoor_outdoor: string | null;
  vision_processed_at: string | null;
  uploaded_at: string;
}

export interface StorageUsage {
  used_bytes: number;
  quota_bytes: number;
  by_category: Record<string, number>;
  by_count: Record<string, number>;
}

export interface User {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  display_name: string | null;
}
