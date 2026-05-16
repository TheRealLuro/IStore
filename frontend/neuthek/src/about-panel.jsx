// Feature inventory — the "About this app" panel inside Settings.
//
// Lists every capability shipped in the current build, grouped by area,
// so users (and self-host operators) can see at a glance what neuthek
// actually does. The data is hand-curated to match what's wired up in
// the backend + FE today — keep this list in sync as new features land.
//
// Each row has a status badge:
//   ● live      — feature is shipped and routable.
//   ◐ optional  — feature is implemented but requires per-deploy config
//                 (e.g. Stripe keys, Google OAuth client).
//   ○ planned   — placeholder so the surface is honest about what's next.
import React, { useState as useStateA } from "react";
import { Icon } from "./icons.jsx";

const SECTIONS = [
  {
    id: "core",
    title: "Core library",
    desc: "Day-to-day file management.",
    items: [
      { name: "Uploads (single + bulk)",          status: "live", note: "Drag, drop, paste, or pick — 200 MB cap per file by default" },
      { name: "Folders + nested organization",    status: "live", note: "Tree under your root, with cycle protection" },
      { name: "Drive-style folder filters",       status: "live", note: "Show only photos / videos / docs / audio per folder" },
      { name: "Archive expansion (zip, tar, 7z, rar)", status: "live", note: "Auto-extracted into a per-archive folder" },
      { name: "Tags (per-user, with colors)",     status: "live", note: "Attach to images or folders; replaces the old status field" },
      { name: "Trash + restore",                  status: "live", note: "Soft delete with empty-trash from Settings → Your data" },
      { name: "Sharing via signed links",         status: "live", note: "Per-recipient grants, optional expiry, optional download" },
    ],
  },
  {
    id: "ai",
    title: "AI features",
    desc: "Opt-in vision + language models. All run on your server.",
    items: [
      { name: "Caption + dense scene description", status: "live", note: "Florence-2 large with BLIP fallback" },
      { name: "OCR (handwriting, whiteboards, scans)", status: "live", note: "Florence-2 <OCR> task token — no separate Tesseract" },
      { name: "Semantic search by meaning",         status: "live", note: "CLIP ViT-L/14 + Qwen 2.5 rewriter for natural-language queries" },
      { name: "AI-suggested file names",            status: "live", note: "Click Rename → Suggest to pull a name from the caption + OCR" },
      { name: "Face recognition (BIPA-compliant)",  status: "live", note: "InsightFace embeddings; templates stay on your box" },
      { name: "Document summaries",                 status: "live", note: "DistilBART for long PDFs and text docs" },
      { name: "Heavy VLM (InternVL2-4B)",           status: "optional", note: "Off by default; flip HEAVY_VLM_ENABLED=true on a ≥12 GB GPU" },
    ],
  },
  {
    id: "search",
    title: "Search",
    desc: "Find anything across your library.",
    items: [
      { name: "Full-text + semantic hybrid",        status: "live", note: "Postgres FTS for names/tags + CLIP for meaning, merged" },
      { name: "Search history per user",            status: "live", note: "Clear from Settings → Privacy" },
      { name: "Filters by date, type, person, tag", status: "live", note: "Filter chips collapse to a single bar on mobile" },
    ],
  },
  {
    id: "cloud",
    title: "Cloud sync",
    desc: "Pull files from external sources, read-only.",
    items: [
      { name: "Google Drive (drive.readonly)",      status: "optional", note: "Connect from Settings → Cloud sync. Pull-only; we never write back" },
      { name: "GitHub repositories (read scope)",   status: "optional", note: "Same panel. Limited Use AI off by default" },
      { name: "Hourly background sweep",            status: "live", note: "Re-syncs every active link; conflicts surface as a banner" },
      { name: "Per-source AI opt-in",               status: "live", note: "Drive content excluded from training by default per Google Limited Use" },
      { name: "Dropbox / OneDrive",                 status: "planned", note: "Same OAuth shape, just not wired yet" },
    ],
  },
  {
    id: "privacy",
    title: "Privacy + compliance",
    desc: "How your data is handled.",
    items: [
      { name: "Per-scope consent ledger",           status: "live", note: "GDPR/BIPA-shaped record of every grant + withdrawal" },
      { name: "EXIF strip on upload (default ON)",  status: "live", note: "GPS + camera metadata removed unless you opt in" },
      { name: "Age gate at signup",                 status: "live", note: "13+ confirmation required before account creation" },
      { name: "Cookie banner with granular choice", status: "live", note: "Essential / all — choice stored locally" },
      { name: "Row-Level Security (Postgres RLS)",  status: "live", note: "Database-level fence: a leaked query can't cross tenants" },
      { name: "Audit log of every action",          status: "live", note: "Settings → Your data → Activity log" },
      { name: "Right to export + delete",           status: "live", note: "Full ZIP + JSON sidecar; 30-day grace before hard delete" },
      { name: "PRIVACY, SECURITY, DPA docs",        status: "live", note: "Shipped at the repo root for review" },
    ],
  },
  {
    id: "security",
    title: "Security",
    desc: "Account + transport protection.",
    items: [
      { name: "Password policy (10+, mixed-case, symbol)", status: "live", note: "Server-side validator + common-password denylist" },
      { name: "TOTP 2FA (RFC 6238)",                       status: "live", note: "QR setup + recovery codes; secret encrypted at rest" },
      { name: "Auth lockout + rate limit",                 status: "live", note: "5 failures → exponential backoff up to 15 min" },
      { name: "Encrypted refresh tokens (Fernet)",         status: "live", note: "Cloud OAuth refresh tokens never sit in plaintext" },
      { name: "Signed download URLs (≤5 min TTL)",         status: "live", note: "Originals are never served from a public URL" },
      { name: "MinIO SSE (S3 or KMS)",                     status: "optional", note: "Per-bucket server-side encryption; biometric bucket isolated" },
      { name: "TLS proxy + encrypted backup sidecar",      status: "optional", note: "Production-only; see SECURITY.md" },
      { name: "Gitleaks pre-commit + CI",                  status: "live", note: "Repo-level secret scanning" },
    ],
  },
  {
    id: "account",
    title: "Account",
    desc: "Sign-in + identity.",
    items: [
      { name: "Email + password sign-in",           status: "live", note: "fastapi-users JWT — same shape across all flows" },
      { name: "Google Sign-In / register",          status: "optional", note: "One-click registration; needs GOOGLE_OAUTH_CLIENT_ID set" },
      { name: "Email verification",                 status: "live", note: "Verification mail issued on signup + email change" },
      { name: "Password reset over email",          status: "live", note: "/auth/forgot-password — Argon2 hashes never leave the server" },
      { name: "Apple Sign-In",                      status: "planned", note: "Same SSO shape; not wired yet" },
    ],
  },
  {
    id: "billing",
    title: "Billing",
    desc: "Self-host free; managed tiers via Stripe.",
    items: [
      { name: "Stripe Embedded Checkout",           status: "optional", note: "Pro + Business tiers; configure STRIPE_SECRET_KEY" },
      { name: "Webhook-driven subscription state",  status: "optional", note: "Signed events drive quota_bytes + plan card" },
      { name: "Per-user quota override (admin)",    status: "live", note: "Set in /admin without going through Stripe" },
    ],
  },
  {
    id: "admin",
    title: "Admin",
    desc: "Ops surface for self-hosters.",
    items: [
      { name: "Admin dashboard at /admin",          status: "live", note: "Gated by users.role ∈ {admin, superuser}" },
      { name: "User list + quota controls",         status: "live", note: "Set quota_bytes, suspend, hard-delete" },
      { name: "VRAM / model footprint inspector",   status: "live", note: "See which models are loaded right now" },
      { name: "Posture probe at boot",              status: "live", note: "Production refuses to start with weak settings" },
    ],
  },
];

function Badge({ status }) {
  const map = {
    live:     { color: "var(--success, #22c55e)", label: "Live" },
    optional: { color: "var(--warning, #f59e0b)", label: "Optional" },
    planned:  { color: "var(--ink-3, #9ca3af)",   label: "Planned" },
  };
  const s = map[status] || map.planned;
  return (
    <span
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 10.5, lineHeight: 1.4,
        fontWeight: 600,
        color: s.color,
        background: "color-mix(in oklab, currentColor 14%, transparent)",
        border: "1px solid color-mix(in oklab, currentColor 28%, transparent)",
        whiteSpace: "nowrap",
      }}
    >
      <span style={{
        width: 6, height: 6, borderRadius: 999,
        background: "currentColor",
      }}/>
      {s.label}
    </span>
  );
}

export function AboutPanel() {
  const totals = SECTIONS.reduce(
    (acc, sec) => {
      sec.items.forEach((it) => { acc[it.status] = (acc[it.status] || 0) + 1; });
      return acc;
    },
    {},
  );
  // Only the first section ("core") is expanded by default — the
  // other 8 sections all collapsed so the page fits in the viewport
  // without scrolling. Open sets are stored locally so toggling
  // doesn't lose state when other parts of the modal re-render.
  const [openIds, setOpenIds] = useStateA(new Set([SECTIONS[0]?.id]));
  const toggle = (id) => setOpenIds((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const expandAll = () => setOpenIds(new Set(SECTIONS.map((s) => s.id)));
  const collapseAll = () => setOpenIds(new Set());
  const allExpanded = openIds.size === SECTIONS.length;

  return (
    <div style={{ padding: "4px 0 24px" }}>
      <div
        style={{
          display: "flex", flexWrap: "wrap", gap: 10,
          padding: "4px 18px 8px",
          color: "var(--ink-3)", fontSize: 12.5,
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <span>
            <strong style={{ color: "var(--ink)" }}>{totals.live || 0}</strong> live
          </span>
          <span>·</span>
          <span>
            <strong style={{ color: "var(--ink)" }}>{totals.optional || 0}</strong> needs config
          </span>
          <span>·</span>
          <span>
            <strong style={{ color: "var(--ink)" }}>{totals.planned || 0}</strong> planned
          </span>
        </div>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={allExpanded ? collapseAll : expandAll}
        >
          {allExpanded ? "Collapse all" : "Expand all"}
        </button>
      </div>

      {SECTIONS.map((sec) => {
        const isOpen = openIds.has(sec.id);
        const live = sec.items.filter((i) => i.status === "live").length;
        return (
          <div key={sec.id} style={{ margin: "0 12px 8px" }}>
            <button
              type="button"
              onClick={() => toggle(sec.id)}
              aria-expanded={isOpen}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 12px",
                background: isOpen ? "var(--surface-2)" : "transparent",
                border: "1px solid var(--line)",
                borderRadius: isOpen ? "10px 10px 0 0" : 10,
                cursor: "pointer",
                color: "var(--ink)",
                textAlign: "left",
                font: "inherit",
              }}
            >
              <span style={{
                display: "inline-flex",
                transform: isOpen ? "rotate(90deg)" : "rotate(0deg)",
                transition: "transform 120ms ease",
                color: "var(--ink-3)",
              }}>
                <Icon name="chevronRight" size={12}/>
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{sec.title}</div>
                <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2 }}>
                  {sec.desc}
                </div>
              </div>
              <span style={{
                fontSize: 11,
                color: "var(--ink-3)",
                whiteSpace: "nowrap",
              }}>
                {live} / {sec.items.length} live
              </span>
            </button>
            {isOpen && (
              <div
                className="applist"
                style={{
                  border: "1px solid var(--line)",
                  borderTop: "none",
                  borderRadius: "0 0 10px 10px",
                  overflow: "hidden",
                }}
              >
                {sec.items.map((it, idx) => (
                  <div
                    key={it.name}
                    style={{
                      display: "flex", alignItems: "flex-start", gap: 12,
                      padding: "10px 14px",
                      borderBottom: idx === sec.items.length - 1 ? "none" : "1px solid var(--line-soft, var(--line))",
                    }}
                  >
                    <span
                      aria-hidden="true"
                      style={{
                        marginTop: 4,
                        color: it.status === "live" ? "var(--success)" :
                               it.status === "optional" ? "var(--warning)" : "var(--ink-3)",
                      }}
                    >
                      <Icon name={it.status === "planned" ? "refresh" : "check"} size={12} strokeWidth={2.6}/>
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: "var(--ink)", fontWeight: 500 }}>
                        {it.name}
                      </div>
                      {it.note && (
                        <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2 }}>
                          {it.note}
                        </div>
                      )}
                    </div>
                    <Badge status={it.status}/>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      <div
        style={{
          padding: "10px 18px", margin: "12px 12px 0",
          fontSize: 11.5, color: "var(--ink-3)",
          borderTop: "1px solid var(--line)",
        }}
      >
        Have something you'd like to see here? Open Settings → Notifications →
        Send feedback, or file an issue at the project's repo.
      </div>
    </div>
  );
}
