// Drill-in detail pages for Settings — one component per page, each with
// proper section layout, stats, forms, and explanatory copy.
import React, { useState as useStateSP, useMemo as useMemoSP } from "react";
import { useQuery } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import { Switch as SwitchSP } from "./primitives.jsx";
import { getStorageUsage } from "@/api/storage";
import { getSubscription, openPortal } from "@/api/billing";
import {
  getRecoveryCodesStatus, regenerateRecoveryCodes, updateMe, login,
  getAccountActivity, getAccountTrash, emptyAccountTrash,
  getTwoFactorStatus, setupTwoFactor, verifyTwoFactor, disableTwoFactor,
  getNotificationPrefs, updateNotificationPrefs,
} from "@/api/auth";
import { backfillSummaries, backfillSummaryEmbeddings, backfillVision } from "@/api/files";
import { API_BASE_URL, tokens } from "@/api/client";

// Small helper for raw fetch() calls that need an Authorization header
// the same way `api.*` builds one. Centralized so future header
// changes (e.g. CSRF) land in one place.
const tokenHeader = () => {
  const t = tokens.get();
  return t ? { Authorization: `Bearer ${t}` } : {};
};
import { activityLabel, activityTone, activityWhen } from "./activity-labels.js";
import { useAuthStore } from "@/stores/authStore";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";

function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  if (n < 1024 ** 4) return (n / 1024 ** 3).toFixed(2) + " GB";
  return (n / 1024 ** 4).toFixed(2) + " TB";
}

// ---------- Shared helpers ----------
function DetExplain({ children }) {
  return (
    <div className="det-explain">
      <span className="det-explain__icon"><Icon name="info" size={14}/></span>
      <span>{children}</span>
    </div>
  );
}

function DetSection({ title, desc, children, right }) {
  return (
    <div className="det-section">
      <div className="det-section__head">
        <div className="det-section__title">{title}</div>
        {right}
      </div>
      {desc && <div className="det-section__desc">{desc}</div>}
      {children}
    </div>
  );
}

function StatTile({ label, value, sub }) {
  return (
    <div className="stat-tile">
      <div className="stat-tile__label">{label}</div>
      <div className="stat-tile__value">{value}</div>
      {sub && <div className="stat-tile__sub">{sub}</div>}
    </div>
  );
}

function StatusPill({ tone, children }) {
  return <span className="det-status-pill" data-tone={tone}><span className="det-status-pill__dot"/>{children}</span>;
}

// ---------- Password ----------
function PasswordPage({ onBack }) {
  const [cur, setCur] = useStateSP("");
  const [nw, setNw] = useStateSP("");
  const [cf, setCf] = useStateSP("");
  const [busy, setBusy] = useStateSP(false);
  const userEmail = useAuthStore((s) => s.user?.email);

  const score = useMemoSP(() => {
    let s = 0;
    if (nw.length >= 8) s++;
    if (nw.length >= 12) s++;
    if (/[A-Z]/.test(nw) && /[a-z]/.test(nw)) s++;
    if (/\d/.test(nw) && /[^A-Za-z0-9]/.test(nw)) s++;
    return s;
  }, [nw]);
  const level = ["weak","weak","fair","good","strong"][score];
  const valid = cur && nw.length >= 12 && nw === cf && score >= 3;

  // fastapi-users' PATCH /users/me doesn't take a current-password arg
  // (the server can't verify it without re-auth), so we re-auth first via
  // /auth/jwt/login. Reject if it fails. Then call updateMe with password.
  const submit = async () => {
    if (!valid || busy || !userEmail) return;
    setBusy(true);
    try {
      try {
        await login(userEmail, cur);
      } catch {
        toast.error("Current password is wrong.");
        setBusy(false);
        return;
      }
      await updateMe({ password: nw });
      toast.success("Password updated");
      setCur(""); setNw(""); setCf("");
      onBack?.();
    } catch (e) {
      toast.error(e?.detail || "Could not update password");
    } finally {
      setBusy(false);
    }
  };

  return (
    <DetSection title="Update password">
      <div className="det-card">
        <div className="det-field">
          <label className="det-field__label">Current password</label>
          <input className="input" type="password" value={cur} onChange={e => setCur(e.target.value)} placeholder="Enter your current password" autoComplete="current-password"/>
        </div>
        <div className="det-field">
          <label className="det-field__label">New password</label>
          <input className="input" type="password" value={nw} onChange={e => setNw(e.target.value)} placeholder="At least 12 characters" autoComplete="new-password"/>
          {nw && (
            <div style={{ marginTop: 8 }}>
              <div className="pwd-strength">
                {[0,1,2,3].map(i => (
                  <div key={i} className="pwd-strength__bar"
                       data-on={score > i} data-level={level}/>
                ))}
              </div>
              <div className="pwd-strength__label" data-level={level} style={{ marginTop: 6 }}>
                {{ weak: "Too weak", fair: "Could be stronger", good: "Good", strong: "Strong password" }[level]}
              </div>
            </div>
          )}
          <div className="det-field__hint">Use 12+ characters with a mix of letters, numbers and symbols.</div>
        </div>
        <div className="det-field">
          <label className="det-field__label">Confirm new password</label>
          <input className="input" type="password" value={cf} onChange={e => setCf(e.target.value)} placeholder="Repeat new password" autoComplete="new-password"/>
          {cf && nw !== cf && <div className="det-field__hint" style={{ color: "var(--danger)" }}>Passwords don't match.</div>}
        </div>
        <div className="det-actions">
          <button className="btn btn--primary" disabled={!valid || busy} onClick={submit}>
            {busy ? "Updating…" : "Update password"}
          </button>
          <button className="btn btn--ghost" onClick={onBack} disabled={busy}>Cancel</button>
        </div>
      </div>
    </DetSection>
  );
}

// ---------- Two-factor / recovery codes ----------
//
// Two layers of account-recovery here:
//   - TOTP authenticator app (§1.2.2) — primary 2FA. Setup writes an
//     encrypted secret + shows a QR; verify flips totp_enabled.
//   - Recovery codes (§C6) — 8 single-use base32 codes, kept for the
//     locked-out case (lost phone, etc.).
function TotpSetupCard({ onSetup, onDone, onCancel, busy }) {
  const [bundle, setBundle] = useStateSP(null);
  const [code, setCode] = useStateSP("");
  const qc = useQueryClient();
  const setUser = useAuthStore((s) => s.setUser);
  const begin = async () => {
    try {
      const b = await onSetup();
      setBundle(b);
    } catch (e) {
      toast.error(e?.detail || "Could not start 2FA setup");
    }
  };
  const submit = async (e) => {
    e?.preventDefault?.();
    if (!code.trim()) return;
    try {
      await verifyTwoFactor(code.trim());
      toast.success("2FA enabled.");
      // Refresh the cached user so user.totp_enabled flips immediately.
      const me = await (await import("@/api/auth")).me();
      setUser(me);
      qc.invalidateQueries({ queryKey: ["2fa-status"] });
      onDone?.();
    } catch (err) {
      toast.error(err?.detail || "That code didn't match — try the next one.");
    }
  };
  if (!bundle) {
    return (
      <div className="det-card" style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn btn--primary" onClick={begin} disabled={busy}>
          {busy ? "Starting…" : "Enable 2FA"}
        </button>
        <span style={{ fontSize: 12, color: "var(--ink-3)", flex: 1 }}>
          We'll show a QR code for your authenticator app.
        </span>
      </div>
    );
  }
  return (
    <div className="det-card" style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <img
          src={`data:image/png;base64,${bundle.qr_png_base64}`}
          alt="2FA QR code"
          style={{ width: 156, height: 156, background: "#fff", borderRadius: 6, padding: 6 }}
        />
        <div style={{ flex: 1, minWidth: 0, display: "grid", gap: 8 }}>
          <div style={{ fontSize: 13 }}>
            Scan this with your authenticator app
            (Google Authenticator, 1Password, Authy, etc.).
          </div>
          <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
            Or paste this secret manually:
          </div>
          <code style={{
            padding: "6px 10px", background: "var(--surface-2)", borderRadius: 6,
            fontSize: 12, wordBreak: "break-all",
          }}>{bundle.secret}</code>
          <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
            Issuer: <span className="mono">{bundle.issuer}</span>
          </div>
        </div>
      </div>
      <form onSubmit={submit} style={{ display: "flex", gap: 8 }}>
        <input
          autoFocus
          inputMode="numeric"
          pattern="\d{6}"
          placeholder="6-digit code"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          style={{
            flex: 1, padding: "8px 10px", borderRadius: 6, fontFamily: "monospace",
            fontSize: 14, letterSpacing: "0.15em", textAlign: "center",
            border: "1px solid var(--line)", background: "var(--surface-2)",
          }}
        />
        <button type="submit" className="btn btn--primary" disabled={code.length !== 6}>
          Verify + enable
        </button>
        <button type="button" className="btn btn--ghost" onClick={onCancel}>
          Cancel
        </button>
      </form>
    </div>
  );
}

function TotpDisableForm({ onDone }) {
  const [code, setCode] = useStateSP("");
  const [password, setPassword] = useStateSP("");
  const [busy, setBusy] = useStateSP(false);
  const qc = useQueryClient();
  const setUser = useAuthStore((s) => s.setUser);
  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!code.trim() && !password) {
      toast.error("Enter a current 2FA code or your password.");
      return;
    }
    if (!window.confirm("Disable 2FA on this account? Your account will be protected by password only.")) return;
    setBusy(true);
    try {
      await disableTwoFactor({
        code: code.trim() || undefined,
        password: password || undefined,
      });
      toast.success("2FA disabled.");
      const me = await (await import("@/api/auth")).me();
      setUser(me);
      qc.invalidateQueries({ queryKey: ["2fa-status"] });
      onDone?.();
    } catch (err) {
      toast.error(err?.detail || "Could not disable 2FA");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} style={{ display: "grid", gap: 8 }}>
      <input
        inputMode="numeric"
        placeholder="Current 6-digit code"
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
        style={{
          padding: "8px 10px", borderRadius: 6, fontFamily: "monospace", fontSize: 14,
          border: "1px solid var(--line)", background: "var(--surface-2)",
        }}
      />
      <div style={{ fontSize: 11, color: "var(--ink-3)", textAlign: "center" }}>— or —</div>
      <input
        type="password"
        placeholder="Account password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
        style={{
          padding: "8px 10px", borderRadius: 6, fontSize: 13,
          border: "1px solid var(--line)", background: "var(--surface-2)",
        }}
      />
      <button type="submit" className="btn btn--secondary" style={{ color: "var(--danger)" }} disabled={busy}>
        {busy ? "Working…" : "Turn off 2FA"}
      </button>
    </form>
  );
}

function TwoFactorPage() {
  const qc = useQueryClient();
  const { data: totp } = useQuery({
    queryKey: ["2fa-status"],
    queryFn: getTwoFactorStatus,
    staleTime: 15_000,
  });
  const { data: status, refetch } = useQuery({
    queryKey: ["recovery-codes"],
    queryFn: getRecoveryCodesStatus,
    staleTime: 30_000,
  });
  const [issued, setIssued] = useStateSP(null);
  const [setupOpen, setSetupOpen] = useStateSP(false);
  const [busy, setBusy] = useStateSP(false);
  const [disableOpen, setDisableOpen] = useStateSP(false);

  const handleRegenerate = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const { codes } = await regenerateRecoveryCodes();
      setIssued(codes);
      refetch();
      toast.success("Recovery codes regenerated. Save them now.");
    } catch (e) {
      toast.error(e?.detail || "Could not regenerate codes");
    } finally {
      setBusy(false);
    }
  };

  const handleSetup = async () => {
    setBusy(true);
    try {
      return await setupTwoFactor();
    } finally {
      setBusy(false);
    }
  };

  const remaining = status?.remaining ?? 0;
  const generatedAt = status?.generated_at ? new Date(status.generated_at).toLocaleDateString() : "never";
  const totpEnabled = !!totp?.enabled;
  const verifiedAt = totp?.verified_at ? new Date(totp.verified_at).toLocaleString() : "never";

  return (
    <>
      <DetSection
        title="Authenticator app (TOTP)"
        desc="A 6-digit code from an app like Google Authenticator or 1Password. Required at every sign-in once enabled."
        right={
          <StatusPill tone={totpEnabled ? "green" : "orange"}>
            {totpEnabled ? "Enabled" : "Off"}
          </StatusPill>
        }
      >
        {totpEnabled ? (
          <>
            <div className="det-card">
              <div className="stat-grid">
                <StatTile label="Status" value="Enabled" sub="required at sign-in"/>
                <StatTile label="Last verified" value={verifiedAt} sub="local time"/>
              </div>
            </div>
            {!disableOpen ? (
              <div className="det-card" style={{ marginTop: 10, display: "flex", gap: 8 }}>
                <button className="btn btn--ghost" onClick={() => setDisableOpen(true)}>
                  Turn off 2FA…
                </button>
              </div>
            ) : (
              <div className="det-card" style={{ marginTop: 10 }}>
                <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 8 }}>
                  Confirm with a current code or your password.
                </div>
                <TotpDisableForm onDone={() => setDisableOpen(false)}/>
                <button type="button" className="btn btn--ghost" style={{ marginTop: 8 }} onClick={() => setDisableOpen(false)}>
                  Cancel
                </button>
              </div>
            )}
          </>
        ) : setupOpen ? (
          <TotpSetupCard
            onSetup={handleSetup}
            onDone={() => setSetupOpen(false)}
            onCancel={() => setSetupOpen(false)}
            busy={busy}
          />
        ) : (
          <div className="det-card" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button className="btn btn--primary" onClick={() => setSetupOpen(true)}>
              Set up 2FA
            </button>
            <span style={{ fontSize: 12, color: "var(--ink-3)", flex: 1 }}>
              Recommended for any account that holds anything you care about.
            </span>
          </div>
        )}
      </DetSection>

      <DetSection
        title="Recovery codes"
        desc="Eight single-use codes you can fall back to if you lose your authenticator."
        right={<StatusPill tone={remaining > 0 ? "green" : "orange"}>{remaining > 0 ? `${remaining} remaining` : "Not generated"}</StatusPill>}
      >
        <div className="det-card">
          <div className="stat-grid">
            <StatTile label="Codes left"   value={String(remaining)} sub={remaining > 0 ? "use any one" : "generate now"}/>
            <StatTile label="Generated"    value={generatedAt}        sub="local time"/>
            <StatTile label="Method"       value="Email + code"       sub="signs you in if locked out"/>
          </div>
        </div>
        <div className="det-card" style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
          <button className="btn btn--secondary" onClick={handleRegenerate} disabled={busy}>
            {busy ? "Working…" : (status?.has_codes ? "Regenerate codes" : "Generate codes")}
          </button>
        </div>
        {issued && (
          <div className="det-card" style={{ marginTop: 12 }}>
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 8 }}>
              Save these. They'll only be shown this once.
            </div>
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(2, 1fr)",
              gap: 6,
              fontFamily: "Geist Mono, monospace",
              fontSize: 13,
            }}>
              {issued.map((c) => <code key={c} style={{ padding: "8px 10px", background: "var(--surface-2)", borderRadius: 6 }}>{c}</code>)}
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
              <button className="btn btn--ghost" onClick={() => { navigator.clipboard.writeText(issued.join("\n")); toast.success("Copied"); }}>
                <Icon name="copy" size={12}/> Copy all
              </button>
              <button className="btn btn--ghost" onClick={() => setIssued(null)}>I've saved them</button>
            </div>
          </div>
        )}
      </DetSection>
    </>
  );
}

// ---------- Trusted devices / sessions ----------
//
// Per-session tracking on the backend hasn't shipped yet — JWTs are
// stateless and we don't persist refresh-token rows per device. The
// only session we can honestly surface is *this* one, derived from
// `navigator.userAgent` on the client. Everything else (other devices,
// "Sign out everywhere", revoke per-session) needs the backend session
// table the auth roadmap covers (see todo.md A4 + C6.4) so we keep
// the section visible but clearly marked rather than showing mock
// "MacBook Pro · Chrome 124" rows.
function describeThisDevice() {
  const ua = (typeof navigator !== "undefined" && navigator.userAgent) || "";
  let device = "This device";
  let browser = "Web";
  if (/iPhone/.test(ua))      device = "iPhone";
  else if (/iPad/.test(ua))   device = "iPad";
  else if (/Android/.test(ua)) device = "Android";
  else if (/Macintosh/.test(ua)) device = "Mac";
  else if (/Windows/.test(ua))  device = "Windows PC";
  else if (/Linux/.test(ua))    device = "Linux";
  if (/Edg\//.test(ua))      browser = "Edge";
  else if (/Chrome\//.test(ua)) browser = "Chrome";
  else if (/Firefox\//.test(ua)) browser = "Firefox";
  else if (/Safari\//.test(ua)) browser = "Safari";
  return { device, browser };
}

function DevicesPage() {
  const { device, browser } = describeThisDevice();
  return (
    <>
      <DetSection title="Active sessions" desc="Sign-ins that are currently allowed access to your library.">
        <div className="det-card" style={{ padding: 0 }}>
          <div className="sess-card">
            <div className="sess-card__icon" data-current="true">
              <Icon name="device" size={16}/>
            </div>
            <div className="sess-card__body">
              <div className="sess-card__name">
                {device} · {browser}
                <StatusPill tone="green">This device</StatusPill>
              </div>
              <div className="sess-card__meta">Active now</div>
            </div>
          </div>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 8 }}>
          Other devices will appear here once per-device session tracking
          ships. For now, signing out from this device ends only this session.
        </div>
      </DetSection>
    </>
  );
}

// ---------- Plan ----------
function PlanPage() {
  const plans = [
    { id: "free", name: "Free", price: "$0",     unit: "/mo", size: "15 GB",  feats: ["Photos & docs",      "Basic search"] },
    { id: "plus", name: "Plus", price: "$2.99",  unit: "/mo", size: "200 GB", feats: ["Everything in Free", "AI summaries"] },
    { id: "pro",  name: "Pro",  price: "$6.99",  unit: "/mo", size: "2 TB",   feats: ["Everything in Plus","Semantic search","Face recognition"], current: true },
    { id: "max",  name: "Max",  price: "$19.99", unit: "/mo", size: "12 TB",  feats: ["Everything in Pro",  "Family sharing", "Priority support"] },
  ];
  const [sel, setSel] = useStateSP("pro");
  const cur = plans.find(p => p.current);
  return (
    <>
      <DetSection title="Current plan" right={<StatusPill tone="green">Active</StatusPill>}>
        <div className="det-card" style={{ display: "flex", gap: 16, alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: "-0.012em" }}>neuthek {cur.name}</div>
            <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 4 }}>Renews May 1, 2027 · {cur.price}{cur.unit} · billed annually</div>
          </div>
          <span style={{ flex: 1 }}/>
          <button className="btn btn--ghost">Manage payment</button>
        </div>
      </DetSection>

      <DetSection title="Choose a plan" desc="Switch any time. Changes are prorated to your next renewal.">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
          {plans.map(p => (
            <button key={p.id} className="plan-card-v3" data-active={sel === p.id} onClick={() => setSel(p.id)}>
              {p.current && <span className="plan-card-v3__cur-badge">Current</span>}
              <div className="plan-card-v3__name">{p.name}</div>
              <div className="plan-card-v3__price">{p.price}<small>{p.unit}</small></div>
              <div className="plan-card-v3__size">{p.size} of storage</div>
              <div className="plan-card-v3__features">
                {p.feats.map((f, i) => (
                  <div key={i} className="plan-card-v3__feature">
                    <span className="plan-card-v3__check"><Icon name="check" size={11}/></span>
                    {f}
                  </div>
                ))}
              </div>
            </button>
          ))}
        </div>
        <div className="det-actions">
          <button className="btn btn--primary" disabled={sel === "pro"}>
            {sel === "pro" ? "Current plan" : "Switch to " + plans.find(p => p.id === sel).name}
          </button>
          <button className="btn btn--ghost" style={{ color: "var(--danger)" }}>Cancel subscription</button>
        </div>
      </DetSection>
    </>
  );
}

// ---------- Invoices ----------
function InvoicesPage() {
  const invs = [
    { date: "May 1, 2026", number: "INV-2026-001", amount: "$79.99", plan: "neuthek Pro · annual",  status: "Paid"   },
    { date: "May 1, 2025", number: "INV-2025-001", amount: "$79.99", plan: "neuthek Pro · annual",  status: "Paid"   },
    { date: "May 1, 2024", number: "INV-2024-001", amount: "$59.99", plan: "neuthek Plus · annual", status: "Paid"   },
    { date: "May 1, 2023", number: "INV-2023-001", amount: "$59.99", plan: "neuthek Plus · annual", status: "Paid"   },
  ];
  return (
    <DetSection title="Receipts" desc="Past invoices and subscription receipts. Click to download a PDF copy.">
      <div className="det-card" style={{ padding: 0 }}>
        <table className="inv-table">
          <thead>
            <tr><th>Date</th><th>Description</th><th>Status</th><th className="right">Amount</th><th></th></tr>
          </thead>
          <tbody>
            {invs.map(iv => (
              <tr key={iv.number}>
                <td style={{ fontWeight: 500 }}>{iv.date}</td>
                <td className="muted">{iv.plan}<br/><span className="mono" style={{ fontSize: 11 }}>{iv.number}</span></td>
                <td><StatusPill tone="green">{iv.status}</StatusPill></td>
                <td className="right amt">{iv.amount}</td>
                <td className="right"><button className="btn btn--ghost btn--sm">PDF</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </DetSection>
  );
}

// ---------- Face data (real-data version) ----------
function FaceDetailPage() {
  // Pull the people list — counts named persons + unlabelled clusters
  // for the summary tiles. The /people/ endpoint already returns both
  // shapes; we just derive totals here.
  const { data: people, isLoading } = useQuery({
    queryKey: ["people"],
    queryFn: async () => {
      const r = await fetch(`${API_BASE_URL}/people/`, {
        headers: tokenHeader(),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    },
    staleTime: 30_000,
  });
  const namedCount = (people?.persons || []).length;
  const clusterCount = (people?.unlabeled_clusters || []).length;
  const totalFaces = (people?.persons || []).reduce((a, p) => a + (p.face_count || 0), 0)
    + (people?.unlabeled_clusters || []).reduce((a, c) => a + (c.face_count || 0), 0);
  const [busy, setBusy] = useStateSP(false);
  const qc = useQueryClient();

  const onRescan = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await fetch(`${API_BASE_URL}/people/rescan`, {
        method: "POST", headers: tokenHeader(),
      });
      if (!r.ok) throw new Error("rescan failed");
      toast.success("Rescan queued. Faces will re-appear as the workers process them.");
      qc.invalidateQueries({ queryKey: ["people"] });
    } catch (e) {
      toast.error("Could not start rescan");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <DetSection title="Detection summary">
        <div className="stat-grid">
          <StatTile
            label="Faces detected"
            value={isLoading ? "…" : totalFaces.toLocaleString()}
            sub={isLoading ? " " : `across ${namedCount + clusterCount} groups`}
          />
          <StatTile
            label="People named"
            value={isLoading ? "…" : namedCount.toString()}
            sub={isLoading ? " " : `${clusterCount} unlabelled groups`}
          />
          <StatTile
            label="Embeddings"
            value="On this server"
            sub="Never sold or shared"
          />
        </div>
      </DetSection>

      <DetSection title="How it works">
        <DetExplain>
          Face detection runs on your neuthek server, not in a third-party
          API. Face embeddings (the 512-dimensional vectors used to
          cluster faces) stay in your Postgres database — they're never
          sent to us, never uploaded anywhere, never used to train
          models, and never sold. On self-hosted deployments the
          embeddings literally never leave your hardware; on the
          managed hosted version they live in <em>your</em> tenant's
          database fenced by Postgres row-level security.
        </DetExplain>
      </DetSection>

      <DetSection title="Manage data">
        <div className="det-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className="btn btn--secondary"
            onClick={onRescan}
            disabled={busy}
          >
            {busy ? "Queuing…" : "Re-scan library"}
          </button>
        </div>
      </DetSection>

      <div className="det-danger">
        <div className="det-danger__title">Withdraw face recognition consent</div>
        <div className="det-danger__desc">
          Stops face detection on future uploads and deletes every face
          embedding + named-person grouping in your library. Your photos
          are not affected. This can't be undone — re-granting consent
          starts fresh.
        </div>
        <button
          className="btn btn--secondary"
          style={{ color: "var(--danger)" }}
          onClick={async () => {
            if (busy) return;
            if (!window.confirm("Withdraw face-recognition consent and delete every embedding? Photos are kept.")) return;
            setBusy(true);
            try {
              const r = await fetch(`${API_BASE_URL}/consent/face-recognition/withdraw`, {
                method: "POST", headers: tokenHeader(),
              });
              if (!r.ok) throw new Error();
              toast.success("Face data deleted.");
              qc.invalidateQueries({ queryKey: ["people"] });
              qc.invalidateQueries({ queryKey: ["consent-scopes"] });
            } catch {
              toast.error("Could not withdraw consent");
            } finally {
              setBusy(false);
            }
          }}
        >
          Withdraw consent + delete face data…
        </button>
      </div>
    </>
  );
}

// ---------- Location settings (real-data version) ----------
function LocationDetailPage() {
  // /images/geo returns { points: [{id, lat, lng}], consent: bool }.
  // We derive counts from points; "cities visited" requires reverse
  // geocoding which we don't ship here, so the second tile shows the
  // raw GPS-bearing-image count instead.
  const { data: geo, isLoading: geoLoading } = useQuery({
    queryKey: ["images-geo"],
    queryFn: async () => {
      const r = await fetch(`${API_BASE_URL}/images/geo`, {
        headers: tokenHeader(),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    },
    staleTime: 30_000,
  });
  const { data: facets } = useQuery({
    queryKey: ["facets"],
    queryFn: async () => {
      const r = await fetch(`${API_BASE_URL}/images/facets`, {
        headers: tokenHeader(),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    },
    staleTime: 30_000,
  });
  const gpsCount = (geo?.points || []).length;
  const totalImages = (facets?.by_category || {}).image || 0;

  return (
    <>
      <DetSection title="Summary">
        <div className="stat-grid">
          <StatTile
            label="Photos with GPS"
            value={geoLoading ? "…" : gpsCount.toLocaleString()}
            sub={`of ${totalImages.toLocaleString()} total`}
          />
          <StatTile
            label="Map pins"
            value={geoLoading ? "…" : gpsCount.toLocaleString()}
            sub={geo?.consent ? "Visible on Map" : "Hidden (consent off)"}
          />
        </div>
      </DetSection>

      <DetSection title="How GPS is handled">
        <DetExplain>
          EXIF location is stripped from every upload by default — your
          photos hit the storage bucket with no embedded coordinates.
          When you opt in via the <strong>Keep GPS from photos</strong> toggle
          in the main Privacy section, coordinates are persisted to a
          separate <code>image_geo</code> table you can clear at any time.
          We never share, sell, or train models on this data; on the
          managed hosted version it's fenced behind Postgres row-level
          security so even other neuthek users can't see your pins.
        </DetExplain>
      </DetSection>

      <DetSection title="Manage existing GPS">
        <div className="det-card" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
            The main toggle is in <strong>Privacy → Photo metadata → Keep GPS from photos</strong>.
            That governs new uploads. To remove location from existing photos:
          </div>
          <button
            className="btn btn--secondary"
            style={{ color: "var(--danger)", alignSelf: "flex-start" }}
            onClick={async () => {
              if (!window.confirm(`Permanently strip GPS from all ${gpsCount} existing photos? Map pins disappear.`)) return;
              try {
                const r = await fetch(`${API_BASE_URL}/images/geo/strip-all`, {
                  method: "POST", headers: tokenHeader(),
                });
                if (!r.ok) throw new Error();
                toast.success("GPS removed from existing photos.");
              } catch {
                toast.error("Could not strip GPS (endpoint may not be deployed yet).");
              }
            }}
          >
            Strip GPS from all {gpsCount > 0 ? gpsCount.toLocaleString() + " " : ""}photos…
          </button>
        </div>
      </DetSection>
    </>
  );
}

// ---------- Telemetry / diagnostics ----------
function TelemetryDetailPage() {
  const [crashes, setCrashes] = useStateSP(true);
  const [perf,    setPerf]    = useStateSP(false);
  const [usage,   setUsage]   = useStateSP(false);
  return (
    <>
      <DetExplain>
        We collect anonymous performance and reliability data to find bugs and optimize the app. We <strong>never</strong> read file names, contents, or AI summaries.
      </DetExplain>

      <DetSection title="What to share" desc="Granular control over each diagnostic stream.">
        <div className="det-card" style={{ padding: "4px 18px" }}>
          <div className="det-toggle-row">
            <div className="det-toggle-row__body">
              <div className="det-toggle-row__title">Crash reports</div>
              <div className="det-toggle-row__desc">Stack traces and app version when something crashes. No file paths.</div>
            </div>
            <SwitchSP on={crashes} onChange={setCrashes} ariaLabel="Crashes"/>
          </div>
          <div className="det-toggle-row">
            <div className="det-toggle-row__body">
              <div className="det-toggle-row__title">Performance metrics</div>
              <div className="det-toggle-row__desc">Render time, scroll smoothness, search latency. Aggregated across all users.</div>
            </div>
            <SwitchSP on={perf} onChange={setPerf} ariaLabel="Perf"/>
          </div>
          <div className="det-toggle-row">
            <div className="det-toggle-row__body">
              <div className="det-toggle-row__title">Feature usage</div>
              <div className="det-toggle-row__desc">Which screens you visit. Helps prioritize what to improve.</div>
            </div>
            <SwitchSP on={usage} onChange={setUsage} ariaLabel="Usage"/>
          </div>
        </div>
      </DetSection>
    </>
  );
}

// ---------- AI Models ----------
function ModelsPage() {
  const list = [
    { id: "vision",  name: "vision-base",       desc: "Image classification & embeddings",     on: true,  size: "210 MB" },
    { id: "summary", name: "text-summary-mini", desc: "One-line caption generator",            on: true,  size: "84 MB"  },
    { id: "face",    name: "face-embed-v2",     desc: "Face detection and grouping",           on: true,  size: "62 MB"  },
    { id: "ocr",     name: "ocr-fast",          desc: "Read text in screenshots and documents",on: false, size: "118 MB" },
    { id: "audio",   name: "audio-transcribe",  desc: "Transcribe video soundtracks",          on: false, size: "240 MB" },
  ];
  const [m, setM] = useStateSP(Object.fromEntries(list.map(x => [x.id, x.on])));
  return (
    <DetSection title="Models" desc="Toggle individual capabilities. Disabling a model frees its space and stops new processing.">
      <div className="det-card" style={{ padding: "4px 20px" }}>
        {list.map(x => (
          <div key={x.id} className="model-row-v3">
            <div className="model-row-v3__body">
              <div className="model-row-v3__name">{x.name}</div>
              <div className="model-row-v3__desc">{x.desc} · <span className="mono">{x.size}</span></div>
            </div>
            <SwitchSP on={!!m[x.id]} onChange={() => setM(s => ({ ...s, [x.id]: !s[x.id] }))} ariaLabel={x.name}/>
          </div>
        ))}
      </div>
    </DetSection>
  );
}

// ---------- AI usage ----------
function AIUsagePage() {
  const qc = useQueryClient();
  const [busy, setBusy] = useStateSP(false);
  const [videosBusy, setVideosBusy] = useStateSP(false);
  const [visionBusy, setVisionBusy] = useStateSP(false);
  const [embedBusy, setEmbedBusy] = useStateSP(false);
  const reprocess = async () => {
    if (busy) return;
    if (!window.confirm("Re-run summarization on every image in your library? This uses your local Florence-2 + Qwen2.5 models and can take several minutes.")) return;
    setBusy(true);
    try {
      const r = await backfillSummaries(500, true);
      toast.success(`Queued ${r.queued} files for re-summarization.`);
      // Invalidate file lists so the UI shows the loading skeleton on
      // affected cards as the worker churns through them.
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || "Could not start re-summarize.");
    } finally {
      setBusy(false);
    }
  };
  // Video-only variant. Same `backfill-summaries` endpoint with
  // `category=video` so it routes through the worker's transcode-
  // aware summarizer (multi-keyframe + Qwen aggregation) without
  // also re-running on every image. Useful after a video-summary
  // upgrade or when the user knows their existing video rows have
  // stale "Video file" stubs.
  const reprocessVideos = async () => {
    if (videosBusy) return;
    if (!window.confirm("Re-run video summarization on every video in your library? Each video gets 4 keyframes captioned and aggregated — typically 5-30 s per video on GPU.")) return;
    setVideosBusy(true);
    try {
      const r = await backfillSummaries(500, true, "video");
      if (r.queued === 0) {
        toast("No videos to re-summarize.");
      } else {
        toast.success(`Queued ${r.queued} video${r.queued === 1 ? "" : "s"} for re-summarization.`);
      }
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || "Could not start video re-summarize.");
    } finally {
      setVideosBusy(false);
    }
  };
  // Compute the CLIP text-space embedding for every summary that has
  // one but no `summary_clip_embedding`. New summaries get encoded
  // inline by the worker; this is the one-shot for rows that
  // pre-date the column. Cheap (~10 ms / row on GPU) and synchronous
  // — the toast tells the user how many were filled.
  const embedSummaries = async () => {
    if (embedBusy) return;
    setEmbedBusy(true);
    try {
      const r = await backfillSummaryEmbeddings(2000);
      if (r.filled === 0 && r.eligible === 0) {
        toast("Every summary already has an embedding.");
      } else if (r.filled === 0) {
        toast.error(`Found ${r.eligible} eligible rows but none could be embedded — check server logs.`);
      } else {
        toast.success(`Embedded ${r.filled} of ${r.eligible} summaries.`);
      }
    } catch (e) {
      toast.error(e?.detail || "Could not start embedding backfill.");
    } finally {
      setEmbedBusy(false);
    }
  };
  const reclassify = async () => {
    if (visionBusy) return;
    setVisionBusy(true);
    try {
      const r = await backfillVision(500);
      if (r.processed > 0) {
        toast.success(`Reclassified ${r.processed} image${r.processed === 1 ? "" : "s"} — gallery filters should populate now.`);
      } else if (r.examined === 0) {
        toast.success("Every image already has scene + content metadata.");
      } else {
        toast.error("Reclassification ran but produced no results. Check server logs.");
      }
      // Facets feed the filter chip choices — invalidate so new
      // scene_label / content_type / indoor_outdoor values render
      // immediately.
      qc.invalidateQueries({ queryKey: ["facets"] });
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || "Could not reclassify.");
    } finally {
      setVisionBusy(false);
    }
  };
  return (
    <>
      <DetSection title="How AI is used">
        <DetExplain>
          AI runs on encrypted blobs in your account. Embeddings and summaries are stored alongside your files — nothing is sent to third-party services.
        </DetExplain>
      </DetSection>

      <DetSection
        title="Library maintenance"
        desc="Re-run the summarizer or scene/content classifier over your library. Cloud-synced (Google Drive) images skip vision at upload — reclassifying populates scene labels so the gallery filter chips become useful. The video pass samples 4 keyframes per clip and aggregates them through the LLM rewriter — typically 5-30 s per video on GPU."
      >
        <div className="det-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn btn--secondary" onClick={reprocess} disabled={busy}>
            <Icon name="refresh" size={12}/> {busy ? "Queueing…" : "Reprocess summaries"}
          </button>
          <button className="btn btn--secondary" onClick={reprocessVideos} disabled={videosBusy}>
            <Icon name="video" size={12}/> {videosBusy ? "Queueing…" : "Re-summarize videos"}
          </button>
          <button className="btn btn--secondary" onClick={reclassify} disabled={visionBusy}>
            <Icon name="sparkles" size={12}/> {visionBusy ? "Classifying…" : "Reclassify images (filters)"}
          </button>
          <button className="btn btn--secondary" onClick={embedSummaries} disabled={embedBusy}>
            <Icon name="search" size={12}/> {embedBusy ? "Embedding…" : "Backfill search embeddings"}
          </button>
        </div>
      </DetSection>
    </>
  );
}

// ---------- Storage breakdown (real /storage/usage) ----------
const STORAGE_COLORS = {
  image:    { name: "Photos",    color: "#34c759" },
  video:    { name: "Videos",    color: "#5856d6" },
  document: { name: "Documents", color: "#0a84ff" },
  other:    { name: "Other",     color: "#ff9500" },
};
function StoragePage() {
  const { data } = useQuery({
    queryKey: ["storage"],
    queryFn: getStorageUsage,
    staleTime: 30_000,
  });
  const used = data?.used_bytes ?? 0;
  const quota = data?.quota_bytes ?? 0;
  const cats = ["image", "video", "document", "other"].map((k) => {
    const def = STORAGE_COLORS[k];
    const bytes = data?.by_category?.[k] ?? 0;
    return {
      key: k,
      name: def.name,
      color: def.color,
      bytes,
      size: fmtBytes(bytes),
      pct: quota > 0 ? (bytes / quota) * 100 : 0,
    };
  });
  return (
    <>
      <DetSection title="Storage used" right={<span style={{ fontSize: 12, color: "var(--ink-3)" }}>{fmtBytes(used)} of {fmtBytes(quota)}</span>}>
        <div className="det-card">
          <div className="storage-bar-v3">
            {cats.map(c => <div key={c.key} className="storage-bar-v3__seg" style={{ width: c.pct + "%", background: c.color }}/>)}
          </div>
          <div className="storage-list">
            {cats.map(c => (
              <div key={c.key} className="storage-list__row">
                <span className="storage-list__dot" style={{ background: c.color }}/>
                <span className="storage-list__name">{c.name}</span>
                <span className="storage-list__size">{c.size}</span>
                <span className="storage-list__pct">{c.pct.toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      </DetSection>

      <DetSection title="Free up space">
        <div className="det-card" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn btn--secondary" disabled title="Coming soon">Find duplicates</button>
          <button className="btn btn--secondary" disabled title="Coming soon">Largest files</button>
          <button className="btn btn--ghost" disabled title="Coming soon">Move videos to cold storage</button>
        </div>
      </DetSection>
    </>
  );
}

// ---------- Activity log ----------
//
// All labeling/tone/relative-time formatting lives in `activity-labels.js`
// so this expanded panel and the in-modal mini-list (account.jsx) render
// the same strings. No local declarations here.
function ActivityLogPage() {
  const { data: events, isLoading } = useQuery({
    queryKey: ["account-activity"],
    queryFn: () => getAccountActivity(50),
    staleTime: 30_000,
  });
  return (
    <DetSection title="Recent activity" desc="Sign-ins, uploads, consent changes, and deletes on your account.">
      <div className="det-card" style={{ padding: "10px 20px 16px" }}>
        {isLoading ? (
          <div style={{ color: "var(--ink-3)", fontSize: 13 }}>Loading…</div>
        ) : (events && events.length > 0) ? (
          <div className="activity-timeline">
            {events.map((e) => (
              <div key={e.id} className="activity-item" data-tone={activityTone(e.action)}>
                <div className="activity-item__what">{activityLabel(e.action, e.details)}</div>
                <div className="activity-item__when">{activityWhen(e.created_at)}</div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ color: "var(--ink-3)", fontSize: 13 }}>No recent activity yet.</div>
        )}
      </div>
    </DetSection>
  );
}

// ---------- Trash ----------
function TrashPage({ onBack }) {
  const [confirm, setConfirm] = useStateSP(false);
  const [busy, setBusy] = useStateSP(false);
  const qc = useQueryClient();
  const { data: trash } = useQuery({
    queryKey: ["account-trash"],
    queryFn: getAccountTrash,
    staleTime: 30_000,
  });
  const count = trash?.count ?? 0;
  const bytes = trash?.total_bytes ?? 0;
  const handleEmpty = async () => {
    setBusy(true);
    try {
      const r = await emptyAccountTrash();
      toast.success(`Permanently deleted ${r.deleted} item${r.deleted === 1 ? "" : "s"}.`);
      qc.invalidateQueries({ queryKey: ["account-trash"] });
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["storage"] });
      onBack?.();
    } catch (e) {
      toast.error(e?.detail || "Could not empty trash");
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };
  return (
    <>
      <DetSection title="Trash">
        <div className="stat-grid">
          <StatTile label="Items in trash" value={String(count)} sub={count > 0 ? "auto-deleted in 30 days" : "nothing here right now"}/>
          <StatTile label="Total size"     value={fmtBytes(bytes)} sub={count > 0 ? "will free on empty" : "—"}/>
        </div>
      </DetSection>

      <DetSection title="What happens">
        <DetExplain>
          Items in trash are kept for 30 days, then permanently deleted. Until then, you can restore any of them. Emptying trash now bypasses the 30-day window.
        </DetExplain>
      </DetSection>

      <div className="det-danger">
        <div className="det-danger__title">Empty trash now</div>
        <div className="det-danger__desc">
          {count > 0
            ? `Permanently removes ${count} item${count === 1 ? "" : "s"} totalling ${fmtBytes(bytes)}. This can't be undone.`
            : "Trash is already empty."}
        </div>
        {confirm ? (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--primary" style={{ background: "#cc2f26" }}
                    disabled={busy}
                    onClick={handleEmpty}>
              {busy ? "Deleting…" : "Yes, permanently delete"}
            </button>
            <button className="btn btn--ghost" onClick={() => setConfirm(false)} disabled={busy}>Cancel</button>
          </div>
        ) : (
          <button className="btn btn--secondary" style={{ color: "var(--danger)" }}
                  onClick={() => setConfirm(true)} disabled={count === 0}>Empty trash…</button>
        )}
      </div>
    </>
  );
}

// ---------- Notifications: per-event channel matrix (kept) ----------
function NotifMatrix({ emailNotif, setEmailNotif, pushNotif, setPushNotif }) {
  const events = [
    { id: "signin",  label: "New sign-in",        desc: "Unrecognized device or location" },
    { id: "upload",  label: "Upload complete",    desc: "Bulk uploads, exports, transfers" },
    { id: "share",   label: "Shared with you",    desc: "Someone sent you a file or album" },
    { id: "storage", label: "Storage warning",    desc: "Approaching plan limit" },
    { id: "ai",      label: "AI processing done", desc: "Summaries, captions, face groups" },
  ];
  const [matrix, setMatrix] = useStateSP({
    signin:  { email: true,  push: true  },
    upload:  { email: false, push: true  },
    share:   { email: true,  push: false },
    storage: { email: true,  push: false },
    ai:      { email: false, push: false },
  });
  const tog = (id, ch) => setMatrix(m => ({ ...m, [id]: { ...m[id], [ch]: !m[id][ch] } }));

  return (
    <>
      <div className="applist__label" style={{ marginTop: 0 }}>Channels</div>
      <div className="applist">
        <div className="applist__row">
          <div className="applist__row-icon" data-tone="red"><Icon name="bell" size={14}/></div>
          <div className="applist__row-body">
            <div className="applist__row-title">Email</div>
            <div className="applist__row-desc">Sent to alex@example.com</div>
          </div>
          <SwitchSP on={emailNotif} onChange={setEmailNotif} ariaLabel="Email"/>
        </div>
        <div className="applist__row">
          <div className="applist__row-icon" data-tone="orange"><Icon name="bell" size={14}/></div>
          <div className="applist__row-body">
            <div className="applist__row-title">Push (browser)</div>
            <div className="applist__row-desc">{pushNotif ? "Allowed in this browser" : "Click to grant browser permission"}</div>
          </div>
          <SwitchSP on={pushNotif} onChange={setPushNotif} ariaLabel="Push"/>
        </div>
      </div>

      <div className="applist__label">What to notify me about</div>
      <div className="notif-matrix">
        <div className="notif-matrix__head">
          <div></div>
          <div>Email</div>
          <div>Push</div>
        </div>
        {events.map(e => (
          <div key={e.id} className="notif-matrix__row">
            <div>
              <div className="notif-matrix__title">{e.label}</div>
              <div className="notif-matrix__desc">{e.desc}</div>
            </div>
            <div><SwitchSP on={matrix[e.id].email && emailNotif} onChange={() => tog(e.id, "email")} disabled={!emailNotif} ariaLabel={`${e.label} email`}/></div>
            <div><SwitchSP on={matrix[e.id].push && pushNotif} onChange={() => tog(e.id, "push")} disabled={!pushNotif} ariaLabel={`${e.label} push`}/></div>
          </div>
        ))}
      </div>

      <div className="applist__label">Quiet hours</div>
      <div className="applist">
        <div className="applist__row">
          <div className="applist__row-icon" data-tone="indigo"><Icon name="moon" size={14}/></div>
          <div className="applist__row-body">
            <div className="applist__row-title">Mute notifications between</div>
            <div className="applist__row-desc">No alerts during these hours, even urgent ones</div>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12.5 }}>
            <input className="input" type="time" defaultValue="22:00" style={{ width: 96 }}/>
            <span style={{ color: "var(--ink-3)" }}>to</span>
            <input className="input" type="time" defaultValue="07:00" style={{ width: 96 }}/>
          </div>
        </div>
      </div>
    </>
  );
}

// Re-export with the names account.jsx expects (the prototype mixed
// "Page" and "Panel" suffixes — we normalize on Panel for callers).
export {
  PasswordPage as PasswordChangePanel,
  TwoFactorPage as TwoFactorPanel,
  DevicesPage as SessionsPanel,
  PlanPage as PlanPanel,
  InvoicesPage as InvoicesPanel,
  FaceDetailPage as FaceDetailPanel,
  LocationDetailPage as LocationDetailPanel,
  TelemetryDetailPage as TelemetryDetailPanel,
  ModelsPage as ModelsPanel,
  AIUsagePage as AIUsagePanel,
  StoragePage as StorageBreakdownPanel,
  ActivityLogPage as ActivityLogPanel,
  TrashPage as TrashPanel,
  NotifMatrix,
};

// Minimal SecurityPanel — the prototype referenced this but never defined it.
// We assemble it from existing panels so the Security tab actually renders.
export function SecurityPanel({ twoFA, setTwoFA, Row, Chev }) {
  return (
    <>
      <div className="appset__main-head">
        <div>
          <h3>Security</h3>
          <p>Two-factor, devices, and sign-in history.</p>
        </div>
      </div>

      <div className="applist__label">Two-factor</div>
      <TwoFactorPage/>

      <div className="applist__label">Active devices</div>
      <DevicesPage/>
    </>
  );
}

// ---------- §1.2.3 Notifications panel ----------
//
// The original NotifMatrix() was an unconnected toggle grid. This one
// reads the real preferences from /account/notifications, mutates with
// optimistic UI, and re-renders from the server's authoritative reply.
// Each (kind, channel) toggle posts a PATCH that the backend dedupes
// against the existing row.
const NOTIF_KIND_LABELS = {
  product_updates:  { title: "Product updates",   desc: "Release notes, new features, important changes." },
  security_alerts:  { title: "Security alerts",   desc: "Sign-ins, password changes, 2FA setup. Recommended on." },
  account_activity: { title: "Account activity",  desc: "Storage milestones, shared-with-you notices, deletions." },
  storage_warnings: { title: "Storage warnings",  desc: "When you're near your quota or originals are aging out." },
};
const NOTIF_CHANNEL_LABELS = {
  email:  "Email",
  in_app: "In-app",
};

export function NotificationsPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["notification-prefs"],
    queryFn: getNotificationPrefs,
    staleTime: 30_000,
  });
  const [busy, setBusy] = useStateSP({});
  const toggle = async (kind, channel, current) => {
    const key = `${kind}:${channel}`;
    if (busy[key]) return;
    setBusy((b) => ({ ...b, [key]: true }));
    try {
      const next = await updateNotificationPrefs([{ kind, channel, enabled: !current }]);
      qc.setQueryData(["notification-prefs"], next);
      toast.success(
        `${NOTIF_KIND_LABELS[kind]?.title || kind} (${NOTIF_CHANNEL_LABELS[channel] || channel}) → ${!current ? "on" : "off"}`,
      );
    } catch (e) {
      toast.error(e?.detail || "Could not save");
    } finally {
      setBusy((b) => { const c = { ...b }; delete c[key]; return c; });
    }
  };
  const byPair = useMemoSP(() => {
    const m = new Map();
    (data?.prefs || []).forEach((p) => m.set(`${p.kind}:${p.channel}`, p.enabled));
    return m;
  }, [data]);
  return (
    <>
      <div className="appset__main-head">
        <div>
          <h3>Notifications</h3>
          <p>Pick how neuthek reaches out to you. Push notifications via the browser are coming later.</p>
        </div>
      </div>
      {isLoading ? (
        <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>
      ) : (
        <>
          <div className="applist__label">Channels</div>
          <div className="det-card" style={{ padding: 0 }}>
            <table className="admin-table admin-table--compact" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>Notification</th>
                  {(data?.channels || []).map((c) => (
                    <th key={c} style={{ textAlign: "center", width: 90 }}>
                      {NOTIF_CHANNEL_LABELS[c] || c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data?.kinds || []).map((k) => {
                  const meta = NOTIF_KIND_LABELS[k] || { title: k, desc: "" };
                  return (
                    <tr key={k}>
                      <td>
                        <strong>{meta.title}</strong>
                        {meta.desc && (
                          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
                            {meta.desc}
                          </div>
                        )}
                      </td>
                      {(data?.channels || []).map((c) => {
                        const on = byPair.get(`${k}:${c}`) ?? false;
                        const key = `${k}:${c}`;
                        return (
                          <td key={c} style={{ textAlign: "center" }}>
                            <SwitchSP
                              on={on}
                              onChange={() => toggle(k, c, on)}
                              disabled={!!busy[key]}
                              aria-label={`${meta.title} via ${c}`}
                            />
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 10, padding: "0 4px" }}>
            Security alerts default to on — we never silently disable them, even when this whole tab is otherwise off.
          </div>
        </>
      )}
    </>
  );
}

// ---------- §1.2.4 Plan card ----------
//
// Reads the user's current subscription tier from /billing/subscription
// and the real used/quota numbers from /storage/usage. Shows an
// Upgrade or Manage-subscription button depending on tier.
//
// The upgrade flow takes the user to /billing (full-page pricing
// cards) so we don't try to fit the embedded-checkout iframe inside
// the Account modal — the embedded form needs vertical room and its
// own URL the user can refresh.
// ---------- Playback preferences ----------
//
// Settings the video / audio players read at mount time. All three
// keys are localStorage, not server-side preferences — they're
// per-device choices (mobile data may want a different default
// quality than the desktop in the same account).

const PB_KEYS = {
  videoAutoplay: "neuthek.video.autoplay_sound",
  audioAutoplay: "neuthek.audio.autoplay_sound",
  defaultQuality: "neuthek.video.default_quality",
};
const PB_QUALITIES = [
  { value: "",       label: "Auto (highest available)" },
  { value: "2160p",  label: "2160p (4K)" },
  { value: "1440p",  label: "1440p" },
  { value: "1080p",  label: "1080p (FHD)" },
  { value: "720p",   label: "720p (HD)" },
  { value: "480p",   label: "480p (SD — saves bandwidth)" },
];

export function PlaybackPanel() {
  // Pull initial state straight from localStorage; useState is per-
  // component-mount so reads stay synchronous + each toggle write
  // is reflected in the next render.
  const readBool = (k) => {
    try { return localStorage.getItem(k) === "on"; } catch { return false; }
  };
  const readStr = (k) => {
    try { return localStorage.getItem(k) || ""; } catch { return ""; }
  };
  const [videoAutoplay, setVideoAutoplay] = useStateSP(readBool(PB_KEYS.videoAutoplay));
  const [audioAutoplay, setAudioAutoplay] = useStateSP(readBool(PB_KEYS.audioAutoplay));
  const [defaultQuality, setDefaultQuality] = useStateSP(readStr(PB_KEYS.defaultQuality));

  const flipVideo = () => {
    const next = !videoAutoplay;
    setVideoAutoplay(next);
    try { localStorage.setItem(PB_KEYS.videoAutoplay, next ? "on" : "off"); } catch {}
  };
  const flipAudio = () => {
    const next = !audioAutoplay;
    setAudioAutoplay(next);
    try { localStorage.setItem(PB_KEYS.audioAutoplay, next ? "on" : "off"); } catch {}
  };
  const pickQuality = (q) => {
    setDefaultQuality(q);
    try {
      if (q) localStorage.setItem(PB_KEYS.defaultQuality, q);
      else localStorage.removeItem(PB_KEYS.defaultQuality);
    } catch {}
  };

  return (
    <>
      <DetSection
        title="Autoplay with sound"
        desc="When you open a video or audio file, neuthek can start playback immediately with sound on. Browsers block unmuted autoplay until you explicitly opt in — these toggles flip that opt-in."
      >
        <div className="det-card" style={{ display: "flex", flexDirection: "column", gap: 0 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 0" }}>
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 500 }}>Videos</div>
              <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
                Off → video opens muted and you click the volume icon to unmute.
              </div>
            </div>
            <SwitchSP on={videoAutoplay} onChange={flipVideo} ariaLabel="Autoplay videos with sound"/>
          </div>
          <div style={{ borderTop: "1px solid var(--line-2)", margin: "8px 0" }}/>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 0" }}>
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 500 }}>Audio files</div>
              <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
                Same behavior for `.mp3` / `.wav` / `.flac` and other audio formats.
              </div>
            </div>
            <SwitchSP on={audioAutoplay} onChange={flipAudio} ariaLabel="Autoplay audio with sound"/>
          </div>
        </div>
      </DetSection>

      <DetSection
        title="Default video quality"
        desc="Which encoded tier the player loads first. The transcoder produces every tier the source supports — picking a lower one cuts bandwidth on mobile data without changing what's stored. You can still switch quality in the player at any time."
      >
        <div className="det-card" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {PB_QUALITIES.map((q) => (
            <label key={q.value} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", cursor: "pointer" }}>
              <input
                type="radio"
                name="default-quality"
                checked={defaultQuality === q.value}
                onChange={() => pickQuality(q.value)}
                style={{ accentColor: "var(--ink)" }}
              />
              <span style={{ fontSize: 13 }}>{q.label}</span>
            </label>
          ))}
          <DetExplain style={{ marginTop: 8 }}>
            "Auto" picks the highest tier available for each video. Older clips (uploaded before the multi-quality transcoder) only have one tier; in that case the player serves that one regardless of this setting.
          </DetExplain>
        </div>
      </DetSection>
    </>
  );
}


export function PlanCard() {
  const { data: usage } = useQuery({
    queryKey: ["storage-usage"],
    queryFn: getStorageUsage,
    staleTime: 30_000,
  });
  const { data: sub } = useQuery({
    queryKey: ["billing-subscription"],
    queryFn: getSubscription,
    staleTime: 30_000,
  });
  const user = useAuthStore((s) => s.user);
  const used = usage?.used_bytes ?? 0;
  const quota = usage?.quota_bytes ?? 0;
  const pct = quota > 0 ? Math.min(100, (used / quota) * 100) : 0;
  const tone = pct >= 90 ? "orange" : pct >= 70 ? "amber" : "green";

  const tier = sub?.tier || "free";
  const tierLabel = tier === "pro" ? "Pro" : tier === "business" ? "Business" : "Free";
  const cadence = sub?.interval === "year" ? "annual" : sub?.interval === "month" ? "monthly" : null;
  const status = sub?.status || "active";
  const periodEnd = sub?.current_period_end ? new Date(sub.current_period_end) : null;

  const onManage = async () => {
    try {
      const { url } = await openPortal(window.location.origin + "/");
      window.location.href = url;
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not open billing portal");
    }
  };

  return (
    <div className="det-card" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {tierLabel}{cadence ? ` — ${cadence}` : ""} plan
          </div>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
            {tier === "free"
              ? "50 GB storage and standard rate limits. Upgrade to unlock larger libraries and faster uploads."
              : status === "past_due"
              ? "Payment failed. Stripe will retry; resolve from the customer portal to avoid downgrade."
              : sub?.cancel_at_period_end && periodEnd
              ? `Set to cancel on ${periodEnd.toLocaleDateString()}. Resubscribe any time from the portal.`
              : periodEnd
              ? `Renews ${periodEnd.toLocaleDateString()}.`
              : "Subscription active."}
          </div>
        </div>
        <StatusPill tone={status === "past_due" ? "orange" : tone}>{pct.toFixed(0)}% used</StatusPill>
      </div>
      <div style={{ marginTop: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--ink-3)" }}>
          <span>{fmtBytes(used)} used</span>
          <span>of {fmtBytes(quota)}</span>
        </div>
        <div
          style={{
            height: 6, marginTop: 6, borderRadius: 4, overflow: "hidden",
            background: "var(--surface-2, rgba(0,0,0,0.06))",
          }}
        >
          <div
            style={{
              width: `${pct}%`, height: "100%",
              background:
                tone === "orange" ? "var(--danger, #c0392b)" :
                tone === "amber"  ? "var(--warn, #b4690e)" :
                "var(--success, #2c7a4b)",
            }}
          />
        </div>
      </div>
      {usage?.by_category && Object.keys(usage.by_category).length > 0 && (
        <div className="stat-grid" style={{ marginTop: 14 }}>
          {Object.entries(usage.by_category)
            .filter(([, b]) => b > 0)
            .map(([cat, bytes]) => (
              <StatTile
                key={cat}
                label={cat}
                value={fmtBytes(bytes)}
                sub={`${usage?.by_count?.[cat] ?? 0} files`}
              />
            ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
        {tier === "free" ? (
          <a
            href="/billing"
            style={{
              display: "inline-block", padding: "10px 16px",
              borderRadius: 8, background: "var(--ink)", color: "var(--surface)",
              textDecoration: "none", fontSize: 13, fontWeight: 600,
            }}
          >
            Upgrade plan
          </a>
        ) : (
          <>
            <button
              onClick={onManage}
              style={{
                padding: "10px 16px", borderRadius: 8, border: "none",
                background: "var(--ink)", color: "var(--surface)",
                fontSize: 13, fontWeight: 600, cursor: "pointer",
              }}
              disabled={!sub?.stripe_configured}
            >
              Manage subscription
            </button>
            <a
              href="/billing"
              style={{
                display: "inline-block", padding: "10px 16px",
                borderRadius: 8, border: "1px solid var(--line)",
                background: "transparent", color: "var(--ink)",
                textDecoration: "none", fontSize: 13, fontWeight: 600,
              }}
            >
              Change plan
            </a>
          </>
        )}
      </div>
      {!sub?.stripe_configured && (
        <div style={{ marginTop: 10, fontSize: 11, color: "var(--ink-3)" }}>
          Billing isn't configured on this deployment — paid plans
          are disabled. Operators: set <code>STRIPE_SECRET_KEY</code>
          and friends in <code>.env</code>.
        </div>
      )}
      {user?.is_superuser && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--ink-3)" }}>
          Superuser? Per-user quota override lives in <code>/admin → Users</code>.
        </div>
      )}
    </div>
  );
}
