import { api } from "./client";

export type ConsentState = "GRANTED" | "WITHDRAWN" | "NONE";

export interface ConsentStatus {
  kind: string;
  state: ConsentState;
  granted_at: string | null;
  expires_at: string | null;
  policy_version: string | null;
}

export interface PolicyResponse {
  version: string;
  text: string;
  sha256_hex: string;
}

export const getConsentStatus = () =>
  api.get<ConsentStatus>("/consent/face-recognition");

export const getPolicy = () =>
  api.get<PolicyResponse>("/consent/face-recognition/policy");

export const grantConsent = (payload: {
  signature_text: string;
  consent_collection: boolean;
  consent_retention: boolean;
}) => api.post<ConsentStatus>("/consent/face-recognition/grant", payload);

export const withdrawConsent = () =>
  api.post<ConsentStatus>("/consent/face-recognition/withdraw");

export const backfillFaces = () =>
  api.post<{ queued: number; consent_active: boolean }>(
    "/people/backfill",
  );

export const rescanAllFaces = () =>
  api.post<{ queued: number; consent_active: boolean; cleared_existing_faces: number }>(
    "/people/rescan",
  );

// C4 — per-scope consent toggles. The backend supports these via the
// generic /consent/{kind} routes (defined in backend/consent.py).
export type ConsentScope =
  | "face_recognition"
  | "gps_retention"
  | "exif_retention"
  | "ai_summary"
  | "semantic_search"
  | "bandit_compression_telemetry";

export interface ScopeDetail {
  state: ConsentState;
  granted_at: string | null;
  expires_at: string | null;
  policy_version: string | null;
}

export const getConsentScopes = () =>
  api.get<{
    states: Record<ConsentScope, ConsentState>;
    details: Record<ConsentScope, ScopeDetail>;
  }>("/consent/scopes");

export const grantScope = (scope: ConsentScope) =>
  api.post<ConsentStatus>(`/consent/${scope}/grant`);

export const withdrawScope = (scope: ConsentScope) =>
  api.post<ConsentStatus>(`/consent/${scope}/withdraw`);

// ---------------------------------------------------------------------------
// Marketing-site newsletter opt-in.
//
// Distinct from the privacy scopes above and from the in-app
// notification prefs (product_updates / security_alerts). Opting in
// records consent locally AND forwards the user's email to the
// marketing newsletter list (backend/api/account.py → backend/marketing.py).
// Opting out rides the generic /consent/{kind}/withdraw path under the
// `newsletter` scope.
// ---------------------------------------------------------------------------

export interface NewsletterStatus {
  opted_in: boolean;
  granted_at: string | null;
  // Whether this deployment is wired to the marketing site at all.
  marketing_configured: boolean;
  // True only on a POST when the marketing forward confirmed; null on GET.
  marketing_synced: boolean | null;
}

export const getNewsletterStatus = () =>
  api.get<NewsletterStatus>("/account/newsletter");

export const optInNewsletter = () =>
  api.post<NewsletterStatus>("/account/newsletter");

// Opt-out reuses the generic consent-withdraw route for the `newsletter`
// scope. Returns a ConsentStatus (state === "WITHDRAWN").
export const optOutNewsletter = () =>
  api.post<ConsentStatus>("/consent/newsletter/withdraw");
