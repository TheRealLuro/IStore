// Drill-in detail pages for Settings — one component per page, each with
// proper section layout, stats, forms, and explanatory copy.
import React, { useState as useStateSP, useMemo as useMemoSP } from "react";
import { useQuery } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import { Switch as SwitchSP } from "./primitives.jsx";
import { getStorageUsage } from "@/api/storage";
import { getRecoveryCodesStatus, regenerateRecoveryCodes, updateMe, login, getAccountActivity, getAccountTrash, emptyAccountTrash } from "@/api/auth";
import { backfillSummaries } from "@/api/files";
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
          {cf && nw !== cf && <div className="det-field__hint" style={{ color: "#cc2f26" }}>Passwords don't match.</div>}
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
// Real recovery-codes endpoint wired (status + regenerate). TOTP / backup
// codes for an authenticator app are still on the backlog — todo.md C6.4.
function TwoFactorPage() {
  const { data: status, refetch } = useQuery({
    queryKey: ["recovery-codes"],
    queryFn: getRecoveryCodesStatus,
    staleTime: 30_000,
  });
  const [issued, setIssued] = useStateSP(null);
  const [busy, setBusy] = useStateSP(false);
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
  const remaining = status?.remaining ?? 0;
  const generatedAt = status?.generated_at ? new Date(status.generated_at).toLocaleDateString() : "never";
  return (
    <>
      <DetSection
        title="Recovery codes"
        right={<StatusPill tone={remaining > 0 ? "green" : "orange"}>{remaining > 0 ? `${remaining} remaining` : "Not generated"}</StatusPill>}
      >
        <div className="det-card">
          <div className="stat-grid">
            <StatTile label="Codes left"   value={String(remaining)} sub={remaining > 0 ? "use any one" : "generate now"}/>
            <StatTile label="Generated"    value={generatedAt}        sub="local time"/>
            <StatTile label="Method"       value="Email + code"       sub="signs you in if locked out"/>
          </div>
        </div>
      </DetSection>

      <DetSection title="Manage" desc="Regenerating issues 8 fresh codes and invalidates the old ones. We only show the plaintext once — save them somewhere safe.">
        <div className="det-card" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button className="btn btn--secondary" onClick={handleRegenerate} disabled={busy}>
            {busy ? "Working…" : (status?.has_codes ? "Regenerate codes" : "Generate codes")}
          </button>
          <span style={{ flex: 1 }}/>
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

      <DetSection title="Trusted devices" desc="Devices that don't need a 2FA code at sign-in. Re-enroll if a device is lost.">
        <div className="det-card" style={{ display: "flex", gap: 8 }}>
          <button className="btn btn--ghost">Reset trusted devices</button>
        </div>
      </DetSection>

      <div className="det-danger">
        <div className="det-danger__title">Disable two-factor authentication</div>
        <div className="det-danger__desc">Your account will be protected only by a password. We strongly recommend keeping 2FA on.</div>
        <button className="btn btn--secondary" style={{ color: "#cc2f26" }}>Turn off 2FA…</button>
      </div>
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
          <button className="btn btn--ghost" style={{ color: "#cc2f26" }}>Cancel subscription</button>
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

// ---------- Face data ----------
function FaceDetailPage() {
  return (
    <>
      <DetSection title="Detection summary">
        <div className="stat-grid">
          <StatTile label="Faces detected" value="1,284" sub="across 3,012 photos"/>
          <StatTile label="People named"    value="14"    sub="of 23 groups"/>
          <StatTile label="Embeddings"      value="On-device" sub="never uploaded"/>
        </div>
      </DetSection>

      <DetSection title="How it works">
        <DetExplain>
          Face detection runs locally in your browser. Embeddings — the math fingerprints used to group people — never leave your device. The original photos are encrypted at rest and only decrypted when you view them.
        </DetExplain>
      </DetSection>

      <DetSection title="Manage data">
        <div className="det-card" style={{ display: "flex", gap: 8 }}>
          <button className="btn btn--secondary">Re-scan library</button>
          <button className="btn btn--ghost">Export face groups</button>
        </div>
      </DetSection>

      <div className="det-danger">
        <div className="det-danger__title">Delete all face data</div>
        <div className="det-danger__desc">Removes every embedding and named-person grouping. Your photos are not affected. This can't be undone.</div>
        <button className="btn btn--secondary" style={{ color: "#cc2f26" }}>Delete face data…</button>
      </div>
    </>
  );
}

// ---------- Location settings ----------
function LocationDetailPage() {
  const [strip, setStrip] = useStateSP(false);
  const [hide,  setHide]  = useStateSP(true);
  return (
    <>
      <DetSection title="Summary">
        <div className="stat-grid">
          <StatTile label="Photos with GPS" value="2,841" sub="of 4,108 total"/>
          <StatTile label="Cities visited"  value="42"    sub="across 12 countries"/>
        </div>
      </DetSection>

      <DetSection title="Privacy controls">
        <div className="det-card" style={{ padding: "4px 18px" }}>
          <div className="det-toggle-row">
            <div className="det-toggle-row__body">
              <div className="det-toggle-row__title">Strip GPS on upload</div>
              <div className="det-toggle-row__desc">Remove location metadata from new photos before storing them.</div>
            </div>
            <SwitchSP on={strip} onChange={setStrip} ariaLabel="Strip GPS"/>
          </div>
          <div className="det-toggle-row">
            <div className="det-toggle-row__body">
              <div className="det-toggle-row__title">Hide locations from shares</div>
              <div className="det-toggle-row__desc">When sharing a photo or album, GPS data isn't visible to recipients.</div>
            </div>
            <SwitchSP on={hide} onChange={setHide} ariaLabel="Hide on share"/>
          </div>
        </div>
      </DetSection>

      <div className="det-danger">
        <div className="det-danger__title">Strip GPS from existing photos</div>
        <div className="det-danger__desc">Permanently removes location data from all 2,841 existing photos. The Map view will lose its pins.</div>
        <button className="btn btn--secondary" style={{ color: "#cc2f26" }}>Strip GPS from all photos…</button>
      </div>
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
  return (
    <>
      <DetSection title="How AI is used">
        <DetExplain>
          AI runs on encrypted blobs in your account. Embeddings and summaries are stored alongside your files — nothing is sent to third-party services.
        </DetExplain>
      </DetSection>

      <DetSection
        title="Library maintenance"
        desc="Re-run the summarizer over every image — useful after upgrading the captioning models or fixing a model that previously crashed."
      >
        <div className="det-card" style={{ display: "flex", gap: 8 }}>
          <button className="btn btn--secondary" onClick={reprocess} disabled={busy}>
            <Icon name="refresh" size={12}/> {busy ? "Queueing…" : "Reprocess entire library"}
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
          <button className="btn btn--secondary" style={{ color: "#cc2f26" }}
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
