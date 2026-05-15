// Sharing client — backend at backend/api/shares.py (todo §1.1 / G1).
//
// Per-image, view-or-view+download grants. Sharer creates one with a
// recipient email + duration; for an existing recipient the duration
// applies as-is, for a brand-new recipient the backend caps it to
// exactly one day at claim time. The plaintext token is returned in
// `share_url` ONCE on create — copy it out of the response and into
// the user's own messenger.

import { api } from "./client";
import type {
  IncomingShare,
  ShareGrant,
  SharePermission,
  SharePreview,
} from "@/types/file";

export interface CreateShareInput {
  recipientEmail: string;
  durationSeconds: number;
  permission?: SharePermission;
}

/** Create a share grant on an image you own. The response includes
 *  `share_url` (the only time the plaintext token is returned). */
export const createShare = (
  imageId: string,
  input: CreateShareInput,
): Promise<ShareGrant> =>
  api.post<ShareGrant>(`/images/${imageId}/shares`, {
    recipient_email: input.recipientEmail,
    duration_seconds: input.durationSeconds,
    permission: input.permission ?? "view_download",
  });

/** List active grants on an image you own. The plaintext token is
 *  intentionally NOT returned here — `share_url` is always undefined. */
export const listShares = (imageId: string): Promise<ShareGrant[]> =>
  api.get<ShareGrant[]>(`/images/${imageId}/shares`);

/** Revoke a grant. Soft-deletes via `revoked_at`; the audit trail
 *  survives. Recipient's next request returns 404. */
export const revokeShare = (imageId: string, shareId: string): Promise<void> =>
  api.delete<void>(`/images/${imageId}/shares/${shareId}`);

/** Cards for the "Shared" sidebar — every grant where I am the
 *  recipient and the grant is live. */
export const listIncomingShares = (): Promise<IncomingShare[]> =>
  api.get<IncomingShare[]>("/shares/incoming");

/** Bind a token to the calling user. For a pending grant this also
 *  starts the 1-day window. Idempotent for a grant already bound to
 *  the calling user. Returns the share id + image id + final expiry. */
export const claimShare = (
  token: string,
): Promise<{ share_id: string; image_id: string; expires_at: string | null; was_pending: boolean }> =>
  api.post("/shares/claim", { token });

/** Mint a fresh 5-minute signed URL for the asset bytes. The recipient
 *  is not the image owner so the standard `/images/{id}/served`
 *  endpoint would 403; this is the only path that yields the bytes. */
export const getShareAsset = (
  shareId: string,
  variant: "served" | "original" = "served",
): Promise<{ url: string; expires_at: string }> =>
  api.get(`/shares/${shareId}/asset?variant=${variant}`);

/** Public landing-page lookup: minimal copy ("Bob shared 'sunset.jpg'
 *  with you") plus a `requires_signup` flag. Pinned by recipient
 *  email so an attacker scanning random tokens can't fish for hits. */
export const previewShare = (
  token: string,
  email: string,
): Promise<SharePreview> => {
  const params = new URLSearchParams({ email });
  return api.get<SharePreview>(`/shares/preview/${token}?${params.toString()}`);
};

/** Encode the recipient email into a URL fragment so it survives a
 *  browser visit without being sent to the server (path: copy → paste
 *  → hand-deliver via SMS/Slack/email). The viewer reads it from the
 *  hash to call `previewShare`. */
export const buildShareUrlWithEmail = (shareUrl: string, email: string): string =>
  `${shareUrl}#email=${encodeURIComponent(email.trim().toLowerCase())}`;

/** Pull the email back out of `window.location.hash` (set by the
 *  sharer's app via buildShareUrlWithEmail). Returns "" if absent. */
export const extractEmailFromHash = (hash: string): string => {
  if (!hash || hash.length < 2) return "";
  const trimmed = hash.startsWith("#") ? hash.slice(1) : hash;
  for (const piece of trimmed.split("&")) {
    const [k, v] = piece.split("=");
    if (k === "email" && v) {
      try { return decodeURIComponent(v); } catch { return ""; }
    }
  }
  return "";
};
