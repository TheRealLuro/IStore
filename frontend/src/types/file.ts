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
  bandit_arm_id: number | null;
  summary: string | null;
  summary_topic: string | null;
  summary_points: string[] | null;
  pending_summary: boolean;
  summary_generated_at: string | null;
  // C2 multi-model signals — see backend.schemas.ImageRead.summary_signals.
  summary_signals: {
    regions?: string[];
    objects?: string[];
    concepts?: string[];
    vlm?: string;
  } | null;
  original_expires_at: string | null;
  // Phase 12 — folders + project status.
  folder_id: string | null;
  status: string | null;
  status_color: string | null;
  // User-toggled star/favorite. `starred_at` is set on the OFF→ON
  // transition only; un-starring leaves it intact for history.
  is_starred: boolean;
  starred_at: string | null;
  uploaded_at: string;
}

export interface Folder {
  id: string;
  parent_folder_id: string | null;
  name: string;
  status: string | null;
  status_color: string | null;
  item_count: number;
  subfolder_count: number;
  created_at: string;
  updated_at: string;
}

export interface StorageUsage {
  /** Grand total of bytes physically on disk for this user. As of
   *  2026-05 this is the sum of served + variants + originals +
   *  trash, NOT just the served default-tier total like older
   *  builds returned. */
  used_bytes: number;
  quota_bytes: number;
  by_category: Record<string, number>;
  by_count: Record<string, number>;
  /** Default served blob per row — the compressed preview shown
   *  by the viewer. For videos this is the default quality tier. */
  served_bytes?: number;
  /** Non-default video quality variants (480p / 720p / 1440p /
   *  2160p) the transcoder emits alongside the default 1080p. */
  variants_bytes?: number;
  /** Originals still retained (rows with original_blob_key). The
   *  retention sweeper drops these on TTL or on /storage/free-
   *  originals. */
  originals_bytes?: number;
  /** Soft-deleted rows still on disk. Emptying Trash zeros this. */
  trash_bytes?: number;
  originals_count?: number;
  variants_count?: number;
  /** Per-provider cloud-link summary. Lets the UI say "57 files
   *  mirrored from your Google Drive; originals stay on Drive."
   *  Empty when no cloud account is linked. */
  linked_services?: LinkedService[];
}

export interface LinkedService {
  provider: string;       // "google_drive" | "github" | …
  status: string;         // "active" | "pending" | "revoked"
  files_mirrored: number;
  served_bytes: number;
  originals_bytes: number;
  last_synced_at: string | null;
  ai_opted_in: boolean;
}

export interface ReclaimResponse {
  bytes_freed: number;
  items_dropped: number;
}

export interface User {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  display_name: string | null;
  role: "user" | "admin" | "superuser";
  age_confirmed: boolean;
  // §1.2.2 — flipped after the user verifies their first TOTP code.
  // Drives the Security tab's Enable/Disable affordance.
  totp_enabled: boolean;
  // True when this neuthek user has a Google account attached — set
  // by Settings → Account → Link Google, or by connecting Drive
  // (which now also asks for openid/email/profile). Drives the
  // Link/Unlink button copy in the Account tab.
  google_linked: boolean;
}

// Sharing (todo §1.1 / G1). Backend: backend/api/shares.py.
export type SharePermission = "view" | "view_download";

export interface ShareGrant {
  id: string;
  image_id: string;
  recipient_email: string;
  recipient_user_id: string | null;
  recipient_display_name: string | null;
  permission: SharePermission;
  sharer_duration_seconds: number;
  // For an existing-user recipient this is the absolute expiry set at
  // create time. For a pending grant (recipient_user_id === null) it
  // stays null until the recipient signs up + claims — at which point
  // the backend sets it to claimed_at + 1 day regardless of the
  // sharer's duration. See SHARE_NEW_USER_WINDOW_SECONDS server-side.
  expires_at: string | null;
  claimed_at: string | null;
  revoked_at: string | null;
  created_at: string;
  // Only populated on the create response (POST /images/{id}/shares).
  // Never returned by the list endpoint — the plaintext token is
  // discarded after argon2 hashing, so a leaky owner list can't hand
  // a usable token to a co-located XSS.
  share_url?: string;
}

export interface IncomingShare {
  share_id: string;
  image_id: string;
  image_filename: string | null;
  image_category: string;
  sharer_display_name: string | null;
  sharer_email: string;
  permission: SharePermission;
  expires_at: string | null;
  claimed_at: string | null;
}

export interface SharePreview {
  sharer_display_name: string | null;
  image_filename: string | null;
  image_category: string;
  requires_signup: boolean;
}
