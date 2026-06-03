// Account & Settings — Apple-style redesign with INLINE expansion panels.
// Two-pane modal: left rail of categories, right pane shows grouped lists.
// Every chevron row is clickable and expands a real form/view in place.
import React, { useState as useStateAcc, useEffect as useEffectAcc } from "react";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalAcc,
  ModalClose as ModalCloseAcc,
  Switch as SwitchAcc,
  Collapsible,
} from "./primitives.jsx";
import {
  PasswordChangePanel,
  FaceDetailPanel,
  LocationDetailPanel,
  TelemetryDetailPanel,
  StorageBreakdownPanel,
  SecurityPanel,
  NotificationsPanel,
  PlaybackPanel,
  SftpPanel,
  PlanCard,
} from "./account-panels.jsx";
import { CloudSyncPanel } from "./cloud-sync-panel.jsx";
import toast from "react-hot-toast";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { updateMe, linkGoogleAccount, unlinkGoogleAccount } from "@/api/auth";
import { useAuthStore } from "@/stores/authStore";
import {
  getConsentScopes,
  grantScope,
  withdrawScope,
  withdrawConsent,
  rescanAllFaces,
  optInNewsletter,
  optOutNewsletter,
} from "@/api/consent";
import { backfillSummaries, backfillSummaryEmbeddings, backfillVision, getSummarizeProgress, backfillDocThumbs } from "@/api/files";
import {
  getAccountActivity,
  getAccountTrash,
  emptyAccountTrash,
} from "@/api/auth";
import { activityLabel, activityTone, activityWhen } from "./activity-labels.js";

// Live activity-log panel — reads the user's audit-log slice and
// renders friendly English labels via the shared activity-labels
// helper. Raw action codes (`consent.bandit_compression_telemetry.grant`,
// `auth.login.succeeded`) get mapped to natural sentences ("Enabled
// compression telemetry", "Signed in") so the panel is readable.
function RealActivityLogPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ["account-activity"],
    queryFn: () => getAccountActivity(50),
    staleTime: 30_000,
  });
  if (isLoading) {
    return (
      <div style={{ padding: "10px 18px 14px" }}>
        {[0, 1, 2].map((i) => (
          <div key={i} className="set-skel set-skel--line" style={{ width: `${70 - i * 12}%` }}/>
        ))}
      </div>
    );
  }
  if (!data || data.length === 0) {
    return (
      <div className="set-empty">
        <span className="set-empty__icon"><Icon name="document" size={16}/></span>
        <span className="set-empty__title">No activity yet</span>
        <span className="set-empty__desc">Sign-ins, consent changes, renames, and deletes will show up here.</span>
      </div>
    );
  }
  return (
    <div style={{ padding: "8px 18px 14px", fontSize: 12.5 }}>
      {data.map((e) => (
        <div
          key={e.id}
          style={{
            display: "flex", gap: 12, padding: "8px 0",
            borderBottom: "1px solid var(--line-soft, var(--line))",
            alignItems: "baseline",
          }}
        >
          <span
            style={{
              minWidth: 8, alignSelf: "stretch",
              borderLeft: "2px solid",
              borderColor: activityTone(e.action) === "green" ? "var(--success)"
                          : activityTone(e.action) === "orange" ? "var(--warning)"
                          : "var(--ink-3)",
              marginRight: 4,
            }}
          />
          <span style={{ flex: 1, color: "var(--ink)" }}>
            {activityLabel(e.action, e.details)}
          </span>
          <span className="mono" style={{ color: "var(--ink-3)", minWidth: 90, textAlign: "right" }}>
            {activityWhen(e.created_at)}
          </span>
        </div>
      ))}
    </div>
  );
}

// Live trash panel — count + Empty button calling the real endpoint.
function RealTrashPanel({ onEmptied }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["account-trash"],
    queryFn: getAccountTrash,
    staleTime: 10_000,
  });
  const [busy, setBusy] = useStateAcc(false);
  const fmtBytes = (n) => {
    if (!n) return "0 B";
    if (n < 1024 ** 2) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + " MB";
    return (n / 1024 ** 3).toFixed(2) + " GB";
  };
  const onEmpty = async () => {
    if (busy) return;
    if (!data || data.count === 0) return;
    if (!window.confirm(`Permanently delete ${data.count} item(s) (${fmtBytes(data.total_bytes)})? This can't be undone.`)) return;
    setBusy(true);
    try {
      const r = await emptyAccountTrash();
      toast.success(`Permanently deleted ${r.deleted} item(s).`);
      qc.invalidateQueries({ queryKey: ["account-trash"] });
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["storage"] });
      onEmptied?.();
    } catch (e) {
      toast.error(e?.detail || "Could not empty trash.");
    } finally {
      setBusy(false);
    }
  };
  const count = data?.count ?? 0;
  return (
    <div style={{ padding: "10px 18px 14px", fontSize: 13 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <span><strong>{count}</strong> {count === 1 ? "item" : "items"}</span>
        <span className="mono" style={{ color: "var(--ink-3)" }}>{fmtBytes(data?.total_bytes ?? 0)}</span>
      </div>
      <button className="btn btn--secondary btn--sm" disabled={busy || count === 0} onClick={onEmpty}>
        {busy ? "Emptying…" : "Empty trash"}
      </button>
    </div>
  );
}

function initialsAcc(name) {
  return (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(p => p[0]?.toUpperCase() || "")
    .join("") || "?";
}

// Small chip-row of "truths" about the user — rendered next to the
// name/email block on the Account tab so the user can see their
// verified / 2FA / Google-linked / age-confirmed status at a glance.
// Each badge maps a boolean on the User payload to a color + label.
function UserStatusBadges({ user }) {
  // §B / user-feedback fix: when an account was created via Sign in with
  // Google, the SSO callback flips `is_verified=true` automatically
  // (Google attested the address via the id_token's `email_verified=true`
  // claim). The previous label here read "Email verified" for that case
  // too, which was confusing — users reported "it says my email is
  // verified but I never clicked a verify link". Distinguish the two
  // confirmation paths in the chip text so the source of verification
  // is visible. Heuristic: a Google-linked + verified account where no
  // password has been set is almost certainly SSO-only ⇒ "Verified via
  // Google". A Google-linked account WITH a password is ambiguous (the
  // user may have verified by email first, then linked Google later);
  // fall back to the plain "Email verified" label in that case so we
  // don't claim Google-attestation that didn't happen.
  const verifiedViaGoogle =
    user?.is_verified && user?.google_linked && user?.password_set === false;
  const verifiedLabel = verifiedViaGoogle
    ? "Verified via Google"
    : "Email verified";
  const items = [
    { ok: user?.is_verified, on: verifiedLabel, off: "Email unverified" },
    { ok: user?.totp_enabled, on: "2FA on", off: "2FA off" },
    { ok: user?.google_linked, on: "Google linked", off: null },
    { ok: user?.age_confirmed, on: null, off: "Age not confirmed" },
    { ok: user?.is_superuser || user?.role === "admin", on: "Admin", off: null },
  ];
  const chips = items
    .map((it) => {
      if (it.ok && it.on) return { label: it.on, tone: "good" };
      if (!it.ok && it.off) return { label: it.off, tone: "warn" };
      return null;
    })
    .filter(Boolean);
  if (!chips.length) return null;
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
      {chips.map((c) => (
        <span
          key={c.label}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 8px",
            borderRadius: 999,
            fontSize: 11,
            fontWeight: 500,
            color: c.tone === "good" ? "var(--success)" : "var(--warning)",
            // §C7 — use the new opaque-tint palette so the chip
            // reads cleanly in light mode. The previous
            // color-mix(...transparent) pattern produced a near-
            // invisible pale tint over white surfaces.
            background: c.tone === "good"
              ? "var(--success-soft)"
              : "var(--warning-soft)",
            border: c.tone === "good"
              ? "1px solid color-mix(in oklab, var(--success) 28%, transparent)"
              : "1px solid color-mix(in oklab, var(--warning) 28%, transparent)",
          }}
        >
          <span style={{
            width: 5, height: 5, borderRadius: 999,
            background: "currentColor",
          }}/>
          {c.label}
        </span>
      ))}
    </div>
  );
}

// 2FA status row — bridges the Account tab to the Security tab where
// the real Enable / Disable flow lives. Reads `user.totp_enabled` so
// the row reflects reality without an extra round trip.
// §C6b/C7 — verified-email status row for Settings → Account → Sign-
// in & security, also surfaced inside the Privacy tab. The chip in
// UserStatusBadges already shows the bare verified/unverified state,
// but that's a tiny inline tag with no affordance to do anything
// about it. This row matches the visual weight + interaction shape
// of the surrounding rows (TwoFactor / GoogleLink) so users can see
// the status AND take action (resend the email) without leaving
// Settings.
//
// Verification can land via three paths: clicking the email link
// (POST /auth/verify), having signed up with Google (the SSO
// callback flips is_verified=true via the id_token's
// email_verified=true claim), or a future admin override. The row
// distinguishes the Google path with sub-copy because users who
// signed up via SSO never receive a "verify your email" mail and
// would otherwise be confused by a "Resend" button they'd never
// asked for.
function EmailVerifyRow({ user }) {
  const verified = !!user?.is_verified;
  const verifiedViaGoogle =
    verified && user?.google_linked && user?.password_set === false;
  const [resending, setResending] = useStateAcc(false);

  const onResend = async () => {
    if (!user?.email || resending) return;
    setResending(true);
    try {
      // Lazy-import keeps the auth client out of the critical
      // path for users who land in Settings without ever needing
      // to resend (the common case once verified).
      const { requestVerify } = await import("@/api/auth");
      await requestVerify(user.email);
      toast.success(
        `A fresh verification link is on the way to ${user.email}.`,
      );
    } catch {
      // Anti-enumeration: the server returns 202 either way, so a
      // network blip is the only path that lands here.
      toast.success(
        `If your account is active, a fresh verification link is on its way.`,
      );
    } finally {
      setResending(false);
    }
  };

  const desc = verifiedViaGoogle
    ? "Verified via Google. No email link needed."
    : verified
      ? `Verified. ${user?.email || ""}`.trim()
      : "Some sensitive actions stay locked until you confirm.";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 14px",
        borderTop: "1px solid var(--line-soft, var(--line))",
      }}
    >
      <div style={{
        width: 28, height: 28, borderRadius: 8,
        display: "grid", placeItems: "center",
        // §C7 — opaque-tint palette so the icon background reads
        // in light mode. The previous color-mix(...transparent)
        // came out nearly invisible over white surfaces.
        background: verified ? "var(--success-soft)" : "var(--warning-soft)",
        color: verified ? "var(--success)" : "var(--warning)",
        flexShrink: 0,
      }}>
        <Icon name={verified ? "check" : "alert"} size={14}/>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>
          Email address
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
          {desc}
        </div>
      </div>
      {!verified && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onResend}
          disabled={resending}
        >
          {resending ? "Sending…" : "Resend"}
        </button>
      )}
    </div>
  );
}

function UserTwoFactorRow({ user, onOpenTwoFA }) {
  const enabled = !!user?.totp_enabled;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 14px",
        borderTop: "1px solid var(--line-soft, var(--line))",
      }}
    >
      <div style={{
        width: 28, height: 28, borderRadius: 8,
        display: "grid", placeItems: "center",
        // §C7 — opaque-tint palette in light mode (was a near-
        // invisible color-mix transparent green).
        background: enabled ? "var(--success-soft)" : "var(--surface-2)",
        color: enabled ? "var(--success)" : "var(--ink-3)",
        flexShrink: 0,
      }}>
        <Icon name="shield" size={14}/>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>
          Two-factor authentication
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
          {enabled
            ? "Enabled. A 6-digit code is required at sign-in."
            : "Disabled. Anyone with your password can sign in."}
        </div>
      </div>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={onOpenTwoFA}
      >
        Manage
      </button>
    </div>
  );
}

// Read-only key/value grid showing every truth about the user we can
// derive client-side. The Settings tab now becomes the authoritative
// "this is who you are to neuthek" view.
function UserStateGrid({ user }) {
  const rows = [
    { k: "User ID", v: user?.id, mono: true },
    { k: "Email", v: user?.email || "—" },
    { k: "Display name", v: user?.display_name || user?.name || "—" },
    { k: "Role", v: user?.role || "user" },
    { k: "Account active", v: user?.is_active ? "Yes" : "No" },
    { k: "Email verified", v: user?.is_verified
        ? (user?.google_linked && user?.password_set === false
            ? "Yes — via Google"
            : "Yes")
        : "No" },
    { k: "Age confirmed", v: user?.age_confirmed ? "Yes (13+)" : "Not yet" },
    { k: "Two-factor auth", v: user?.totp_enabled ? "Enabled" : "Off" },
    { k: "Google linked", v: user?.google_linked ? "Yes" : "No" },
    { k: "Admin access", v: (user?.is_superuser || user?.role === "admin") ? "Yes" : "No" },
  ];
  return (
    <div className="applist" style={{ padding: "4px 14px" }}>
      {rows.map((r) => (
        <div
          key={r.k}
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 12,
            padding: "8px 0",
            borderBottom: "1px solid var(--line-soft, var(--line))",
            fontSize: 12.5,
          }}
        >
          <span style={{ color: "var(--ink-3)" }}>{r.k}</span>
          <span className={r.mono ? "mono" : undefined} style={{ color: "var(--ink)", textAlign: "right", wordBreak: "break-all" }}>
            {String(r.v ?? "—")}
          </span>
        </div>
      ))}
    </div>
  );
}

// Pinned card at the top of the Privacy tab. States the project's
// posture on data unambiguously so the user doesn't have to read the
// full Privacy Notice to know whether their photos get scraped into
// model training somewhere. Wording covers BOTH delivery modes —
// self-hosted (you own the box) and managed hosted (we run the box
// for you) — so the same string works on either deployment.
function PrivacyStanceCard() {
  return (
    <div
      style={{
        margin: "0 0 20px",
        padding: "14px 16px",
        borderRadius: 14,
        background: "color-mix(in oklab, var(--success, #22c55e) 8%, transparent)",
        border: "1px solid color-mix(in oklab, var(--success, #22c55e) 28%, transparent)",
        display: "flex",
        gap: 12,
        alignItems: "flex-start",
      }}
    >
      <div style={{ color: "var(--success)", flexShrink: 0, marginTop: 2 }}>
        <Icon name="shield" size={18}/>
      </div>
      <div style={{ flex: 1, fontSize: 12.5, lineHeight: 1.55 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--ink)", marginBottom: 4 }}>
          Your data isn't ours. We never train on it. We never sell it.
        </div>
        <div style={{ color: "var(--ink-2, var(--ink-3))" }}>
          neuthek runs on a server <em>you</em> control (self-hosted) or
          a tenant fenced behind Postgres row-level security (hosted).
          Your photos, videos, documents, face embeddings, summaries,
          and search history are not exported to third parties, are
          not used to train AI models — ours or anyone else's — and
          are not sold to ad networks, brokers, or partners. AI is
          opt-in per scope; everything off the AI pages stays local.
        </div>
      </div>
    </div>
  );
}

// Sign-in-with-Google link/unlink row for Settings → Account.
//
// When the user hasn't connected Google yet, the row shows a button
// that POSTs to /auth/google/link and hard-navigates the browser to
// the returned consent URL. After Google bounces back, the callback
// stamps `google_sub` on the user row and redirects to the FE with
// `#sso_linked=1`, which `app.jsx` reads to invalidate the user
// query so this row flips to "Linked" without a manual refresh.
//
// When already linked, the row offers an Unlink button that DELETEs
// /auth/google/link and clears the column.
function GoogleLinkRow({ user }) {
  const qc = useQueryClient();
  const linked = !!user?.google_linked;
  const [busy, setBusy] = useStateAcc(false);

  const onLink = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await linkGoogleAccount();
      window.location.href = r.auth_url;
    } catch (e) {
      toast.error(e?.detail || "Could not start the Google link flow");
      setBusy(false);
    }
  };
  const onUnlink = async () => {
    if (busy) return;
    if (!window.confirm("Unlink Google? You'll still be able to sign in with your email and password.")) return;
    setBusy(true);
    try {
      await unlinkGoogleAccount();
      toast.success("Google account unlinked.");
      qc.invalidateQueries({ queryKey: ["me"] });
    } catch (e) {
      toast.error(e?.detail || "Could not unlink");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 14px",
        borderTop: "1px solid var(--line-soft, var(--line))",
      }}
    >
      <div style={{
        width: 28, height: 28, borderRadius: 8,
        display: "grid", placeItems: "center",
        background: "color-mix(in oklab, #4285F4 14%, transparent)",
        color: "#4285F4",
        flexShrink: 0,
      }}>
        <svg width="14" height="14" viewBox="0 0 18 18" aria-hidden="true">
          <path fill="currentColor" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.88 2.69-6.62z"/>
          <path fill="currentColor" d="M9 18c2.43 0 4.47-.81 5.96-2.18l-2.91-2.26c-.8.54-1.83.86-3.05.86-2.34 0-4.33-1.58-5.04-3.7H.96v2.33A9 9 0 0 0 9 18z" opacity="0.85"/>
          <path fill="currentColor" d="M3.96 10.71c-.18-.54-.28-1.12-.28-1.71 0-.6.1-1.17.28-1.71V4.96H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.04l3-2.33z" opacity="0.6"/>
          <path fill="currentColor" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 9 0 9 9 0 0 0 .96 4.96l3 2.33C4.67 5.16 6.66 3.58 9 3.58z" opacity="0.45"/>
        </svg>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>Google account</div>
        <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
          {linked
            ? "Sign in with Google lands in this account."
            : "Link so you can Sign in with Google."}
        </div>
      </div>
      {linked ? (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onUnlink}
          disabled={busy}
        >
          {busy ? "Working…" : "Unlink"}
        </button>
      ) : (
        <button
          type="button"
          className="btn btn--secondary btn--sm"
          onClick={onLink}
          disabled={busy}
        >
          {busy ? "Redirecting…" : "Link Google"}
        </button>
      )}
    </div>
  );
}

// Settings rail. Notifications was previously hidden because the
// backend wasn't wired; §1.2.3 landed `notification_prefs` + endpoints
// so it's back.
const APPSET_NAV = [
  { id: "profile",       label: "Account",       icon: "user",     tone: "ink"    },
  { id: "privacy",       label: "Privacy",       icon: "shield",   tone: "blue"   },
  { id: "security",      label: "Security",      icon: "lock",     tone: "red"    },
  { id: "notifications", label: "Notifications", icon: "bell",     tone: "amber"  },
  // §C10 — playback preferences. Lives between Notifications and AI
  // features because it's another "how the app behaves while I'm
  // using it" surface, distinct from privacy / consent.
  { id: "playback",      label: "Playback",      icon: "play",     tone: "sky"    },
  { id: "ai",            label: "AI features",   icon: "sparkles", tone: "purple" },
  // §C2 — Drive sync. Lives between AI features and Your data because
  // it's where most users will go after enabling AI features
  // ("now let me pull in my Drive photos").
  { id: "cloud",         label: "Cloud sync",    icon: "cloud",    tone: "sky"    },
  // SFTP access — mount the library as a drive. Sits next to Cloud sync
  // because it's the other "get files in and out" surface; it's where a
  // user goes to bulk-move files without the browser uploader.
  { id: "sftp",          label: "SFTP access",   icon: "key",      tone: "green"  },
  { id: "data",          label: "Your data",     icon: "download", tone: "green"  },
];

// Pretty-print a granted/withdrawn ISO timestamp for the consent rows.
// Returns "" for nullish so the caller can drop empty subtitles.
function fmtConsentDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      month: "short", day: "numeric", year: "numeric",
    });
  } catch { return ""; }
}

export function AccountModal({ open, onClose, onOpenSubmodal, user, onUserChange, onSignOut, initialTab = "profile" }) {
  const [tab, setTab] = useStateAcc(initialTab);
  // 2FA toggle here is the section visibility flag, not a real grant.
  // SecurityPanel below shows the recovery-codes UI which IS wired.
  // (Dropped the dead `emailNotif`/`setEmailNotif` state — they
  // were passed to SecurityPanel but its signature ignores them;
  // the real notifications surface lives in NotificationsPanel and
  // hits /account/notifications directly.)
  const [twoFA, setTwoFA] = useStateAcc(true);
  // Real per-scope consent state. Each toggle hits /consent/{scope}/grant|
  // withdraw and invalidates this query so the UI reflects the server.
  const qc = useQueryClient();
  const { data: scopeData } = useQuery({
    queryKey: ["consent-scopes"],
    queryFn: getConsentScopes,
    enabled: open,
    staleTime: 30_000,
  });
  const scopeOf = (k) => (scopeData?.states?.[k] === "GRANTED");
  const detailOf = (k) => scopeData?.details?.[k];
  // Subtitle copy that shows the user when each consent was set so they
  // can audit their own decisions. "Granted on …" / "Withdrawn on …" /
  // "Not configured yet" — same shape across all five scopes.
  const subFor = (k, onCopy, offCopy) => {
    const d = detailOf(k);
    const stamp = d?.granted_at ? fmtConsentDate(d.granted_at) : "";
    // §1.2.4 — surface "Expires …" alongside "Granted …" so the user
    // sees when a consent will auto-lapse (face_recognition is the
    // big one, set to a 3-year horizon by backend.consent on grant).
    const expires = d?.expires_at ? fmtConsentDate(d.expires_at) : "";
    if (d?.state === "GRANTED") {
      const parts = [onCopy];
      if (stamp) parts.push(`Granted ${stamp}`);
      if (expires) parts.push(`Expires ${expires}`);
      return parts.join(" · ");
    }
    if (d?.state === "WITHDRAWN") return `${offCopy}${stamp ? ` · Withdrawn ${stamp}` : ""}`;
    return `${offCopy} · Not configured yet`;
  };
  // Per-scope in-flight guard. Without this, a rapid double-click fires
  // two flipScope calls before the first one returns; the second uses
  // the still-cached state and either no-ops or fights the first call.
  // We also use it to drive an optimistic preview so the switch flips
  // immediately instead of waiting for the round-trip to finish.
  const [scopeBusy, setScopeBusy] = useStateAcc({});
  const [scopeOverride, setScopeOverride] = useStateAcc({});
  // Each scope reads its override first (set during the in-flight call),
  // then falls back to the server-driven GRANTED check.
  const readScope = (k) => {
    if (k in scopeOverride) return scopeOverride[k];
    return scopeOf(k);
  };
  const aiSummaries    = readScope("ai_summary");
  const semanticSearch = readScope("semantic_search");
  const faceRecog      = readScope("face_recognition");
  const gpsTags        = readScope("gps_retention");
  const telemetry      = readScope("bandit_compression_telemetry");
  // Marketing-site newsletter opt-in. State rides the same
  // /consent/scopes payload (the `newsletter` scope), but turning it ON
  // hits the dedicated POST /account/newsletter (which also forwards the
  // email to the marketing list); OFF uses the generic withdraw. So this
  // can't go through `flipScope` — see `setNewsletter` below.
  const newsletter     = readScope("newsletter");
  // §B1 — EXIF retention. OFF by default = strip Make/Model/lens/timestamp
  // on upload (safer). ON = keep them in the original so the user can
  // see and export the full camera metadata. Without this toggle the
  // scope sits in NONE forever and the sidebar "needs your attention"
  // pill never reaches zero — that's the bug we just fixed.
  const exifRetention  = readScope("exif_retention");
  const flipScope = async (scope, currentlyOn) => {
    if (scopeBusy[scope]) return;
    setScopeBusy((m) => ({ ...m, [scope]: true }));
    // Optimistic flip — the switch animates immediately so the click
    // feels responsive. We rollback below if the network call fails.
    setScopeOverride((m) => ({ ...m, [scope]: !currentlyOn }));
    try {
      if (currentlyOn) await withdrawScope(scope);
      else await grantScope(scope);
      await qc.invalidateQueries({ queryKey: ["consent-scopes"] });
      // Drop the override once the server-side state lands in the
      // cache; from here on `scopeOf` is the source of truth again.
      setScopeOverride((m) => {
        const { [scope]: _, ...rest } = m;
        return rest;
      });
      toast.success(currentlyOn ? "Turned off." : "Turned on.");
    } catch (e) {
      // Rollback optimistic flip on failure so the UI reflects reality.
      setScopeOverride((m) => {
        const { [scope]: _, ...rest } = m;
        return rest;
      });
      toast.error(e?.detail || "Could not save consent");
    } finally {
      setScopeBusy((m) => {
        const { [scope]: _, ...rest } = m;
        return rest;
      });
    }
  };
  const setAiSummaries    = () => flipScope("ai_summary", aiSummaries);
  const setSemanticSearch = () => flipScope("semantic_search", semanticSearch);
  const setGpsTags        = () => flipScope("gps_retention", gpsTags);
  const setTelemetry      = () => flipScope("bandit_compression_telemetry", telemetry);
  const setExifRetention  = () => flipScope("exif_retention", exifRetention);
  // Newsletter: ON → POST /account/newsletter (records consent + forwards
  // the email to the marketing list); OFF → generic withdraw. Reuses the
  // same scopeBusy/scopeOverride machinery as flipScope for the optimistic
  // flip + in-flight guard, but with the newsletter-specific endpoints.
  const setNewsletter = async () => {
    const scope = "newsletter";
    if (scopeBusy[scope]) return;
    const currentlyOn = newsletter;
    setScopeBusy((m) => ({ ...m, [scope]: true }));
    setScopeOverride((m) => ({ ...m, [scope]: !currentlyOn }));
    try {
      if (currentlyOn) {
        await optOutNewsletter();
        toast.success("Unsubscribed from the newsletter.");
      } else {
        const res = await optInNewsletter();
        // The local opt-in always succeeds; the marketing forward is
        // best-effort. Tell the user the truth about what happened so a
        // self-host build (or a transient marketing outage) doesn't look
        // like a silent failure.
        if (res?.marketing_synced) {
          toast.success("Subscribed to the newsletter.");
        } else if (res?.marketing_configured === false) {
          toast.success("Newsletter opt-in saved.");
        } else {
          toast.success(
            "Opt-in saved — we'll finish adding you to the list shortly.",
          );
        }
      }
      await qc.invalidateQueries({ queryKey: ["consent-scopes"] });
      setScopeOverride((m) => {
        const { [scope]: _, ...rest } = m;
        return rest;
      });
    } catch (e) {
      setScopeOverride((m) => {
        const { [scope]: _, ...rest } = m;
        return rest;
      });
      toast.error(e?.detail || "Could not update newsletter preference");
    } finally {
      setScopeBusy((m) => {
        const { [scope]: _, ...rest } = m;
        return rest;
      });
    }
  };
  // face_recognition is BIPA-grade — granting requires the signed-statement
  // payload via the dedicated /consent/face-recognition/grant endpoint.
  // The generic /consent/{kind}/grant rejects it with 400. So clicking the
  // toggle when it's currently OFF opens the sign-and-consent modal;
  // clicking when ON withdraws via the dedicated dashed endpoint.
  const setFaceRecog = async () => {
    if (faceRecog) {
      try {
        await withdrawConsent();
        qc.invalidateQueries({ queryKey: ["consent-scopes"] });
        toast.success("Face recognition disabled. Templates queued for deletion.");
      } catch (e) {
        toast.error(e?.detail || "Could not withdraw consent");
      }
    } else {
      onOpenSubmodal?.("face");
    }
  };

  // AI maintenance actions exposed in the AI features tab. Both queue
  // background work; we toast immediately and let the file/people queries
  // re-fetch as the worker completes rows.
  const [busyResummarize, setBusyResummarize] = useStateAcc(false);
  const [busyResummarizeVideos, setBusyResummarizeVideos] = useStateAcc(false);
  const [busyRescan, setBusyRescan] = useStateAcc(false);
  const [busyDocThumbs, setBusyDocThumbs] = useStateAcc(false);
  const [busyReclassify, setBusyReclassify] = useStateAcc(false);
  const [busyEmbed, setBusyEmbed] = useStateAcc(false);

  const reclassifyImages = async () => {
    if (busyReclassify) return;
    setBusyReclassify(true);
    try {
      const r = await backfillVision(500);
      if (r.processed > 0) {
        toast.success(`Reclassified ${r.processed} image${r.processed === 1 ? "" : "s"} — gallery filter chips should populate now.`);
      } else if (r.examined === 0) {
        toast("Every image already has scene + content metadata.");
      } else {
        toast.error("Reclassification ran but produced no results. Check the server logs.");
      }
      // Facets feed the filter chip choices — invalidate so new
      // scene_label / content_type / indoor_outdoor values render
      // immediately in the gallery.
      qc.invalidateQueries({ queryKey: ["facets"] });
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || "Could not reclassify.");
    } finally {
      setBusyReclassify(false);
    }
  };

  const generateDocThumbs = async () => {
    if (busyDocThumbs) return;
    setBusyDocThumbs(true);
    try {
      const r = await backfillDocThumbs();
      if (r.generated > 0) {
        toast.success(`Generated ${r.generated} of ${r.examined} PDF thumbnail(s).`);
        qc.invalidateQueries({ queryKey: ["files"] });
      } else if (r.examined > 0) {
        toast(`Scanned ${r.examined} document(s); nothing to rasterize.`);
      } else {
        toast("No documents need thumbnails yet.");
      }
    } catch (e) {
      toast.error(e?.detail || "Could not generate PDF thumbnails.");
    } finally {
      setBusyDocThumbs(false);
    }
  };

  // Summary regen progress poll. Refetches every 2 s while pending > 0;
  // stops polling when zero so we don't spam the backend forever after
  // the queue drains. Shared cache key — the top SummarizingBanner in
  // app.jsx reuses the same data via React Query's dedupe.
  const { data: summaryProgress } = useQuery({
    queryKey: ["summarize-progress"],
    queryFn: getSummarizeProgress,
    enabled: open,
    refetchInterval: (q) =>
      q.state.data && q.state.data.pending > 0 ? 2000 : false,
    staleTime: 1000,
  });
  const reprocessSummaries = async () => {
    if (busyResummarize) return;
    if (!window.confirm("Re-run summarization on every image in your library? Uses your local Florence-2 + Qwen2.5 models and can take several minutes.")) return;
    setBusyResummarize(true);
    try {
      const r = await backfillSummaries(500, true);
      toast.success(`Queued ${r.queued} files for re-summarization.`);
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || "Could not start re-summarize.");
    } finally {
      setBusyResummarize(false);
    }
  };
  // Video-only variant — Whisper transcription + multi-keyframe
  // Florence + Qwen aggregation. ~30-90 s per video on GPU.
  const reprocessVideos = async () => {
    if (busyResummarizeVideos) return;
    if (!window.confirm("Re-run video summarization on every video in your library? Each video samples 4 keyframes, transcribes audio with Whisper, and aggregates via Qwen — typically 30-90 s per video on GPU.")) return;
    setBusyResummarizeVideos(true);
    try {
      const r = await backfillSummaries(500, true, "video");
      if (r.queued === 0) toast("No videos to re-summarize.");
      else toast.success(`Queued ${r.queued} video${r.queued === 1 ? "" : "s"} for re-summarization.`);
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || "Could not start video re-summarize.");
    } finally {
      setBusyResummarizeVideos(false);
    }
  };
  // Synchronous backfill for rows that have a summary but no
  // search embedding — cheap (~10 ms/row on GPU) so we surface
  // the eligible / filled counts inline instead of polling.
  const embedSummaries = async () => {
    if (busyEmbed) return;
    setBusyEmbed(true);
    try {
      const r = await backfillSummaryEmbeddings(2000);
      if (r.eligible === 0) toast("Every summary already has a search embedding.");
      else if (r.filled === 0) toast.error(`Found ${r.eligible} eligible rows but none could be embedded — check server logs.`);
      else toast.success(`Embedded ${r.filled} of ${r.eligible} summaries.`);
    } catch (e) {
      toast.error(e?.detail || "Could not start embedding backfill.");
    } finally {
      setBusyEmbed(false);
    }
  };
  const rescanFaces = async () => {
    if (busyRescan) return;
    if (!window.confirm("Re-scan every photo for faces? This clears existing face data and rebuilds from scratch.")) return;
    setBusyRescan(true);
    try {
      const r = await rescanAllFaces();
      if (!r.consent_active) {
        toast.error("Face recognition consent isn't active. Enable it first.");
      } else {
        toast.success(`Cleared ${r.cleared_existing_faces} faces · queued ${r.queued} photos.`);
      }
      qc.invalidateQueries({ queryKey: ["people"] });
    } catch (e) {
      toast.error(e?.detail || "Could not start face re-scan.");
    } finally {
      setBusyRescan(false);
    }
  };

  const [editingProfile, setEditingProfile] = useStateAcc(false);
  const [draftName, setDraftName] = useStateAcc(user?.name || "");
  const [draftEmail, setDraftEmail] = useStateAcc(user?.email || "");

  // expansion state — { [rowId]: bool }
  const [exp, setExp] = useStateAcc({});
  const tog = (id) => setExp(e => ({ ...e, [id]: !e[id] }));
  const isOpen = (id) => !!exp[id];

  useEffectAcc(() => {
    if (open) {
      // Honor the deep-linked initialTab (e.g. sidebar attention pill →
      // "privacy") instead of always landing on Account.
      setTab(initialTab || "profile");
      setDraftName(user?.name || "");
      setDraftEmail(user?.email || "");
      setEditingProfile(false);
      setExp({});
    }
  }, [open, initialTab, user?.name, user?.email]);

  // Reset expansion when switching tabs to keep things tidy
  useEffectAcc(() => { setExp({}); }, [tab]);

  const setStoreUser = useAuthStore((s) => s.setUser);

  // Real PATCH /users/me. The server currently treats display_name
  // updates and email changes the same way; password changes go through
  // the Password sub-panel below.
  const saveProfile = async () => {
    const name = draftName.trim();
    const email = draftEmail.trim();
    const body = {};
    if (name && name !== (user?.name || "")) body.display_name = name;
    if (email && email !== (user?.email || "")) body.email = email;
    if (Object.keys(body).length === 0) {
      setEditingProfile(false);
      return;
    }
    // §C4.3 — when the email actually changes, fastapi-users clears
    // is_verified server-side AND the on_after_update hook fires a
    // fresh verification email to the new address. We track this so
    // the post-save toast can tell the user to check their inbox
    // instead of the generic "Profile updated", and so the existing
    // VerifyEmailBanner (§C6b) takes over the persistent nudge.
    const emailChanged = "email" in body;
    try {
      const updated = await updateMe(body);
      setStoreUser(updated);
      onUserChange?.({
        name: updated.display_name || updated.email.split("@")[0],
        email: updated.email,
      });
      if (emailChanged) {
        toast.success(
          `We sent a verification link to ${updated.email}. ` +
          `Some sensitive actions stay locked until you confirm.`,
          // Longer duration than the default 4s — this is the only
          // place the user is told about the verify mail, so make
          // sure it's not missed if they're not watching the toast
          // stack.
          { duration: 7000 },
        );
      } else {
        toast.success("Profile updated");
      }
      setEditingProfile(false);
    } catch (e) {
      toast.error(e?.detail || "Could not save changes");
    }
  };

  // Apple-style row primitive — supports expanded state
  const Row = ({ id, icon, tone, title, desc, tail, onClick, expanded }) => (
    <div className="applist__row" data-expanded={expanded ? "true" : "false"}
         style={onClick ? { cursor: "pointer" } : null} onClick={onClick}>
      {icon && (
        <div className="applist__row-icon" data-tone={tone}>
          <Icon name={icon} size={14}/>
        </div>
      )}
      <div className="applist__row-body">
        <div className="applist__row-title">{title}</div>
        {desc && <div className="applist__row-desc">{desc}</div>}
      </div>
      {tail && <div className="applist__row-tail">{tail}</div>}
    </div>
  );

  const Chev = ({ rotated }) => (
    <div className="applist__row-chevron" style={{ transform: rotated ? "rotate(90deg)" : null, transition: "transform 0.15s" }}>
      <Icon name="chevronRight" size={14}/>
    </div>
  );

  // Helper to render an expandable row with its panel
  const Expandable = ({ id, ...rowProps }) => (
    <>
      <Row id={id} {...rowProps}
           tail={<>{rowProps.tailExtra}<Chev rotated={isOpen(id)}/></>}
           expanded={isOpen(id)}
           onClick={() => tog(id)}/>
      {isOpen(id) && rowProps.panel}
    </>
  );

  return (
    <ModalAcc open={open} onClose={onClose} size="xl" labelledBy="acc-title">
      <div className="modal__head">
        <h2 id="acc-title">
          <span className="modal__head-icon"><Icon name="settings" size={16}/></span>
          Settings
        </h2>
        <p>Manage your sign-in, privacy, AI features, and stored data.</p>
        <ModalCloseAcc onClose={onClose}/>
      </div>

      <div className="modal__body" style={{ padding: 0 }}>
        <div className="appset">
          <aside className="appset__sidebar">
            {APPSET_NAV.map(item => (
              <button key={item.id}
                      className="appset__navitem"
                      data-active={tab === item.id}
                      onClick={() => setTab(item.id)}>
                <span className="appset__navitem-icon">
                  <Icon name={item.icon} size={14}/>
                </span>
                {item.label}
              </button>
            ))}
            <div className="appset__sidebar-spacer"/>
            <button className="appset__navitem appset__navitem--danger" onClick={() => onSignOut?.()}>
              <span className="appset__navitem-icon"><Icon name="logout" size={14}/></span>
              Sign out
            </button>
          </aside>

          <main className="appset__main">
            {tab === "profile" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>Account</h3>
                    <p>Your name, email, and plan.</p>
                  </div>
                </div>

                <div className="appset__profile">
                  <div className="appset__profile-avatar">{initialsAcc(user?.name)}</div>
                  {editingProfile ? (
                    <div className="appset__profile-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      <input className="input" value={draftName}
                             placeholder="Display name"
                             onChange={e => setDraftName(e.target.value)}/>
                      <input className="input" value={draftEmail}
                             placeholder="you@example.com"
                             onChange={e => setDraftEmail(e.target.value)}/>
                      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                        <button className="btn btn--primary btn--sm" onClick={saveProfile}>Save</button>
                        <button className="btn btn--secondary btn--sm" onClick={() => setEditingProfile(false)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="appset__profile-body">
                        <div className="appset__profile-name">{user?.name || "—"}</div>
                        <div className="appset__profile-email">{user?.email || ""}</div>
                        <UserStatusBadges user={user}/>
                      </div>
                      <button className="btn btn--secondary btn--sm" onClick={() => setEditingProfile(true)}>Edit</button>
                    </>
                  )}
                </div>

                <Collapsible label="Sign-in & security" count={4} id="acct-signin" alwaysOpen>
                  <div className="applist">
                    <EmailVerifyRow user={user}/>
                    <Expandable id="pwd" icon="lock" tone="red"
                                title="Password"
                                desc={user?.password_set === false
                                  ? "No password set — you sign in with Google. Add one for email sign-in."
                                  : "Change the password you use to sign in."}
                                tailExtra={<span aria-hidden="true" style={{ color: "var(--ink-3)", marginRight: 8, letterSpacing: 2 }}>•••••••••</span>}
                                panel={<PasswordChangePanel onSaved={() => tog("pwd")}/>}/>
                    <GoogleLinkRow user={user}/>
                    <UserTwoFactorRow user={user} onOpenTwoFA={() => setTab("security")}/>
                  </div>
                </Collapsible>

                <Collapsible label="Plan" id="acct-plan" alwaysOpen>
                  <PlanCard/>
                </Collapsible>

                <Collapsible label="Account state" id="acct-state" alwaysOpen>
                  <UserStateGrid user={user}/>
                </Collapsible>
              </>
            )}

            {tab === "privacy" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>Privacy</h3>
                    <p>Control what we collect and how it's used.</p>
                  </div>
                </div>

                <PrivacyStanceCard/>

                {/*
                  §C7 — verification + sign-in status, surfaced inside
                  the Privacy tab because a user concerned about who
                  can act on their account wants to see "is my email
                  confirmed?" / "is 2FA on?" without hunting through
                  Account. The rows are read-only summaries; tapping
                  "Manage" sends them to the canonical Account or
                  Security tab so we don't duplicate the editing UI.
                */}
                <Collapsible label="Account verification" count={2} id="priv-verify" alwaysOpen>
                  <div className="applist">
                    <EmailVerifyRow user={user}/>
                    <UserTwoFactorRow user={user} onOpenTwoFA={() => setTab("security")}/>
                  </div>
                </Collapsible>

                <Collapsible label="AI on your library" count={3} id="priv-ai" alwaysOpen>
                  <div className="applist">
                    <Row icon="sparkles" tone="purple" title="AI summaries"
                         desc={subFor("ai_summary",
                           "Florence-2 + Qwen describe new uploads.",
                           "Off — no summary generated.")}
                         tail={<SwitchAcc on={aiSummaries} onChange={setAiSummaries} ariaLabel="AI summaries"/>}/>
                    <Row icon="search" tone="blue" title="Semantic search"
                         desc={subFor("semantic_search",
                           "CLIP embeddings stored, queryable by meaning.",
                           "Off — search by filename only.")}
                         tail={<SwitchAcc on={semanticSearch} onChange={setSemanticSearch} ariaLabel="Semantic search"/>}/>
                    <Row icon="users" tone="purple" title="Face recognition"
                         desc={subFor("face_recognition",
                           "Faces detected and grouped on this server.",
                           "Off — requires written consent (BIPA).")}
                         tail={<SwitchAcc on={faceRecog} onChange={setFaceRecog} ariaLabel="Face recognition"/>}/>
                    {faceRecog && (
                      <Expandable id="face-detail" icon="info" tone="indigo"
                                  title="Face data details" desc="Detection counts and management"
                                  panel={<FaceDetailPanel/>}/>
                    )}
                  </div>
                </Collapsible>

                <Collapsible label="Photo metadata" count={2} id="priv-meta" alwaysOpen>
                  <div className="applist">
                    <Row icon="map" tone="green" title="Keep GPS from photos"
                         desc={subFor("gps_retention",
                           "EXIF location stored — pins appear on the Map.",
                           "Stripped on upload — no map pins.")}
                         tail={<SwitchAcc on={gpsTags} onChange={setGpsTags} ariaLabel="GPS retention"/>}/>
                    <Expandable id="loc-detail" icon="info" tone="indigo"
                                title="Location settings" desc="Strip GPS from existing photos"
                                panel={<LocationDetailPanel/>}/>
                    <Row icon="camera" tone="amber" title="Keep camera EXIF"
                         desc={subFor("exif_retention",
                           "Make / model / lens / shutter speed kept on originals.",
                           "Stripped on upload — only pixels remain.")}
                         tail={<SwitchAcc on={exifRetention} onChange={setExifRetention} ariaLabel="EXIF retention"/>}/>
                  </div>
                </Collapsible>

                <Collapsible label="Diagnostics" count={1} id="priv-diag" alwaysOpen>
                  <div className="applist">
                    <Row icon="info" tone="blue" title="Compression telemetry"
                         desc={subFor("bandit_compression_telemetry",
                           "Bandit reward signals from your encodes shared anonymously.",
                           "Off — no telemetry leaves this server.")}
                         tail={<SwitchAcc on={telemetry} onChange={setTelemetry} ariaLabel="Telemetry"/>}/>
                    <Expandable id="tel-detail" icon="layers" tone="ink"
                                title="What's collected" desc="Crash reports, performance, feature usage"
                                panel={<TelemetryDetailPanel/>}/>
                  </div>
                </Collapsible>

                <Collapsible label="Email from us" count={1} id="priv-newsletter" alwaysOpen>
                  <div className="applist">
                    <Row icon="mail" tone="blue" title="Weekly newsletter"
                         desc={subFor("newsletter",
                           "Release notes each Friday — what shipped and why. Unsubscribe any time.",
                           "Off — no marketing email.")}
                         tail={<SwitchAcc on={newsletter} onChange={setNewsletter} ariaLabel="Newsletter"/>}/>
                  </div>
                </Collapsible>

                <Collapsible label="Documents" count={3} id="priv-docs" alwaysOpen>
                  <div className="applist">
                    <Row icon="document" tone="indigo" title="Privacy Notice"
                         desc="What we collect, why, and how to control it"
                         tail={<Chev/>} onClick={() => onOpenSubmodal?.("privacy")}/>
                    <Row icon="document" tone="indigo" title="Terms of Use"
                         desc="v4.2 · Accepted on signup"
                         tail={<Chev/>} onClick={() => onOpenSubmodal?.("terms")}/>
                    <Row icon="users" tone="purple" title="Face recognition consent"
                         desc={detailOf("face_recognition")?.granted_at
                           ? `Signed ${fmtConsentDate(detailOf("face_recognition").granted_at)} · Withdraw any time`
                           : "Read the BIPA-grade consent text"}
                         tail={<Chev/>} onClick={() => onOpenSubmodal?.("face")}/>
                  </div>
                </Collapsible>
              </>
            )}

            {tab === "security" && <SecurityPanel twoFA={twoFA} setTwoFA={setTwoFA} Row={Row} Chev={Chev}/>}

            {tab === "notifications" && <NotificationsPanel/>}

            {tab === "playback" && <PlaybackPanel/>}

            {tab === "ai" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>AI features</h3>
                    <p>Florence-2 captions, Qwen2.5 rewrites, CLIP search, RetinaFace clustering — all running on this server, never sent to a third party.</p>
                  </div>
                </div>

                <Collapsible label="Features" count={3} id="ai-features" alwaysOpen>
                  <div className="applist">
                    <Row icon="sparkles" tone="purple" title="AI summaries"
                         desc={subFor("ai_summary",
                           "Florence-2 + Qwen describe new uploads.",
                           "Off — no summary generated.")}
                         tail={<SwitchAcc on={aiSummaries} onChange={setAiSummaries} ariaLabel="Summaries"/>}/>
                    <Row icon="search" tone="blue" title="Semantic search"
                         desc={subFor("semantic_search",
                           "CLIP embeddings stored, queryable by meaning.",
                           "Off — search by filename only.")}
                         tail={<SwitchAcc on={semanticSearch} onChange={setSemanticSearch} ariaLabel="Semantic"/>}/>
                    <Row icon="users" tone="purple" title="Face recognition"
                         desc={subFor("face_recognition",
                           "Faces grouped on this server.",
                           "Off — requires written consent (BIPA).")}
                         tail={<SwitchAcc on={faceRecog} onChange={setFaceRecog} ariaLabel="Faces"/>}/>
                  </div>
                </Collapsible>

                <Collapsible label="Library maintenance" count={6} id="ai-maintenance" alwaysOpen>
                  <div className="applist">
                  <Row icon="refresh" tone="purple" title="Re-summarize entire library"
                       desc={(() => {
                         if (busyResummarize) return "Queueing… your library will rebuild summaries in the background.";
                         const p = summaryProgress;
                         if (p && p.pending > 0) {
                           const pct = p.total ? Math.round((p.completed / p.total) * 100) : 0;
                           return `Summarizing ${p.completed} of ${p.total} · ${pct}%`;
                         }
                         return "Re-run Florence-2 + Qwen2.5 over every image. Useful after upgrading models.";
                       })()}
                       tail={<button className="btn btn--secondary btn--sm" onClick={reprocessSummaries} disabled={busyResummarize || (summaryProgress?.pending ?? 0) > 0}>
                         {busyResummarize ? "Working…" : ((summaryProgress?.pending ?? 0) > 0 ? "Running…" : "Run")}
                       </button>}/>
                  {summaryProgress && summaryProgress.pending > 0 && (
                    <div style={{ padding: "0 18px 14px" }}>
                      <div style={{
                        height: 4, borderRadius: 2,
                        background: "var(--surface-2)", overflow: "hidden",
                      }}>
                        <div style={{
                          height: "100%",
                          width: `${summaryProgress.total ? (summaryProgress.completed / summaryProgress.total) * 100 : 0}%`,
                          background: "var(--ink)",
                          transition: "width 250ms ease-out",
                        }}/>
                      </div>
                    </div>
                  )}
                  <Row icon="users" tone="orange" title="Re-scan all faces"
                       desc={faceRecog
                         ? (busyRescan
                             ? "Queueing… clearing existing faces and re-detecting."
                             : "Clear existing face data and re-run RetinaFace over every photo.")
                         : "Enable Face recognition above first."}
                       tail={<button className="btn btn--secondary btn--sm" onClick={rescanFaces}
                                     disabled={busyRescan || !faceRecog}>
                         {busyRescan ? "Working…" : "Run"}
                       </button>}/>
                  <Row icon="document" tone="indigo" title="Generate PDF thumbnails"
                       desc={busyDocThumbs
                         ? "Rasterizing page 1 of each PDF…"
                         : "Render page 1 of every PDF so the gallery shows a thumbnail instead of an icon. Safe to re-run."}
                       tail={<button className="btn btn--secondary btn--sm" onClick={generateDocThumbs}
                                     disabled={busyDocThumbs}>
                         {busyDocThumbs ? "Working…" : "Run"}
                       </button>}/>
                  <Row icon="sparkles" tone="blue" title="Reclassify images (populates filter chips)"
                       desc={busyReclassify
                         ? "Running CLIP scene + content classifier on each image…"
                         : "Re-run scene / content_type / indoor-outdoor classification on every image that's missing those columns. Cloud-synced (Google Drive) images skip vision at upload — without this they have no scene labels, so the gallery filter chips show nothing but \"Has people\". Safe to re-run."}
                       tail={<button className="btn btn--secondary btn--sm" onClick={reclassifyImages}
                                     disabled={busyReclassify}>
                         {busyReclassify ? "Working…" : "Run"}
                       </button>}/>
                  <Row icon="video" tone="purple" title="Re-summarize videos"
                       desc={busyResummarizeVideos
                         ? "Queueing… each video re-samples 4 keyframes, transcribes audio with Whisper, and aggregates via Qwen."
                         : "Re-run the multi-keyframe + Whisper audio transcription + Qwen aggregation pipeline on every video. Use this after upgrading the video summarizer or to refresh outdated descriptions. ~30-90 s per video on GPU."}
                       tail={<button className="btn btn--secondary btn--sm" onClick={reprocessVideos}
                                     disabled={busyResummarizeVideos}>
                         {busyResummarizeVideos ? "Working…" : "Run"}
                       </button>}/>
                  <Row icon="search" tone="blue" title="Backfill search embeddings"
                       desc={busyEmbed
                         ? "Computing CLIP text-space vectors for each summary…"
                         : "Compute the search-side embedding for rows that have a summary but no vector yet. New summaries embed automatically — this is the one-shot for older rows so semantic search finds them by meaning. Fast (~10 ms/row)."}
                       tail={<button className="btn btn--secondary btn--sm" onClick={embedSummaries}
                                     disabled={busyEmbed}>
                         {busyEmbed ? "Working…" : "Run"}
                       </button>}/>
                  </div>
                </Collapsible>
              </>
            )}

            {tab === "cloud" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>Cloud sync</h3>
                    <p>Pull files from Google Drive, iCloud, Proton Drive, MEGA, and more. Read-only — AI stays off until you turn it on per source.</p>
                  </div>
                </div>
                <CloudSyncPanel/>
              </>
            )}

            {tab === "sftp" && <SftpPanel user={user}/>}

            {tab === "data" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>Your data</h3>
                    <p>Storage, exports, and deletion.</p>
                  </div>
                </div>

                <Collapsible label="Overview" count={3} id="data-overview" alwaysOpen>
                  <div className="applist">
                    <Expandable id="storage" icon="layers" tone="green"
                                title="Storage"
                                desc="Live breakdown by category"
                                panel={<StorageBreakdownPanel/>}/>
                    <Row icon="download" tone="blue" title="Export everything"
                         desc="ZIP of all files plus a JSON metadata sidecar"
                         tail={<Chev/>} onClick={() => onOpenSubmodal?.("export")}/>
                    <Expandable id="activity" icon="document" tone="teal"
                                title="Activity log"
                                desc="Your sign-ins, consents, renames, deletes"
                                panel={<RealActivityLogPanel/>}/>
                  </div>
                </Collapsible>

                <Collapsible label="Trash" count={1} id="data-trash" alwaysOpen>
                  <div className="applist">
                    <Expandable id="trash" icon="trash" tone="orange"
                                title="Empty trash"
                                desc="Permanently delete soft-deleted items"
                                panel={<RealTrashPanel onEmptied={() => tog("trash")}/>}/>
                  </div>
                </Collapsible>

                <Collapsible label="Danger zone" count={1} id="data-danger" alwaysOpen>
                  <div className="applist">
                    <Row icon="trash" tone="red" title="Delete account" desc="Permanently remove your library"
                         tail={<Chev/>} onClick={() => onOpenSubmodal?.("delete")}/>
                  </div>
                </Collapsible>
              </>
            )}
          </main>
        </div>
      </div>
    </ModalAcc>
  );
}

// Named export above; legacy `window.AccountModal` removed.
