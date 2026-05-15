// Dev / Admin overlay.
//
// Tabs: Storage · Users · Audit · Models · Tasks · Logs · System · Processes · Hardware.
// Every tab reads real backend state (todo §1.3, §C8.2). A health
// banner pinned to the header surfaces DB / Redis / MinIO / Disk /
// Queue status so an operator can tell at a glance whether anything
// is on fire without clicking through tabs.
import React, {
  useState as useStateAd,
  useEffect as useEffectAd,
  useMemo as useMemoAd,
  useRef as useRefAd,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalAd,
  ModalClose as ModalCloseAd,
} from "./primitives.jsx";
import {
  getAdminStorage,
  listAdminUsers,
  updateUserQuota,
  updateUserRole,
  listAdminAudit,
  getAdminSystem,
  getAdminHardware,
  getAdminProcesses,
  getAdminModels,
  getAdminTasks,
  getAdminLogs,
} from "@/api/admin";

function admBytes(n) {
  if (!n && n !== 0) return "—";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 ** 3) return (n / 1024 / 1024).toFixed(1) + " MB";
  if (n < 1024 ** 4) return (n / 1024 ** 3).toFixed(2) + " GB";
  return (n / 1024 ** 4).toFixed(2) + " TB";
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m ${seconds % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

function fmtRelativeTime(iso) {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "soon";
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

// Map a state to a tint that matches the existing var(--success|warn|danger)
// tokens used elsewhere in the app.
function healthColor(state) {
  if (state === "ok") return "var(--success, #2c7a4b)";
  if (state === "warn") return "var(--warn, #b4690e)";
  return "var(--danger, #c0392b)";
}
function healthDot(state) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8, height: 8, borderRadius: "50%",
        background: healthColor(state),
        marginRight: 6,
        verticalAlign: "middle",
      }}
    />
  );
}

// Pretty-print an audit entry as a human sentence. Falls back to
// "{user} did {action}" when we don't have a template for the action,
// so an unknown event type still reads as English rather than JSON.
function formatAuditLine(e) {
  const who = e.user_display_name || e.user_email || "system";
  const det = e.details || {};
  switch (e.action) {
    case "auth.login.succeeded":
      return `${who} signed in${det.ip ? ` from ${det.ip}` : ""}.`;
    case "auth.login.failed":
      return `Failed sign-in attempt for ${det.identity || "?"}${det.ip ? ` from ${det.ip}` : ""}.`;
    case "auth.rate_limit":
      return `Auth rate-limit triggered for ${det.identity || det.ip || "?"}.`;
    case "auth.lockout":
    case "auth.login.locked":
      return `Account temporarily locked${det.identity ? ` (${det.identity})` : ""}.`;
    case "share.created":
      return `${who} shared an image with ${det.recipient_email || "?"} for ${
        det.duration_seconds ? fmtDuration(det.duration_seconds) : "?"
      }.`;
    case "share.claimed":
      return `${who} accepted a share invite${det.was_pending ? " (new account)" : ""}.`;
    case "share.revoked":
      return `${who} revoked a share.`;
    case "share.replaced":
      return `${who} re-shared an image (superseded a prior link).`;
    case "share.asset.viewed":
      return `${who} viewed a shared image.`;
    case "image.delete":
      return `${who} deleted an image.`;
    case "image.bulk_delete":
      return `${who} deleted ${det.count ?? "?"} images.`;
    case "image.upload":
      return `${who} uploaded an image.`;
    case "admin.user.quota.update":
      return `${who} changed user ${det.target_user_id?.slice(0, 8) || "?"}'s quota to ${
        det.quota_bytes ? admBytes(det.quota_bytes) : "default"
      }.`;
    case "admin.user.role.update":
      return `${who} changed user ${det.target_user_id?.slice(0, 8) || "?"}'s role to ${det.role || "?"}.`;
    case "consent.face_recognition.grant":
      return `${who} granted face-recognition consent.`;
    case "consent.face_recognition.withdraw":
      return `${who} withdrew face-recognition consent (${det.faces_deleted ?? 0} faces purged).`;
    case "account.recovery_codes.regenerate":
      return `${who} regenerated recovery codes (${det.count ?? 0}).`;
    case "account.recovery_codes.login":
      return `${who} signed in with a recovery code.`;
    default: {
      const action = e.action.replace(/\./g, " · ");
      return `${who} · ${action}`;
    }
  }
}

function Sparkline({ values, color = "var(--ink)", height = 28 }) {
  if (!values || !values.length) return null;
  const w = 120, h = height;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / Math.max(1, values.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
  const last = values[values.length - 1];
  const lx = w;
  const ly = h - ((last - min) / range) * h;
  return (
    <svg width={w} height={h} className="admin-spark" viewBox={`0 0 ${w} ${h}`}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5"/>
      <circle cx={lx} cy={ly} r="2" fill={color}/>
    </svg>
  );
}

// Compact health banner that sits in the modal header. Renders one
// pill per check; the overall verdict colors the banner border so a
// red box screams "go look" even from across the room.
function HealthBanner({ system }) {
  if (!system) return null;
  const overall = system.health?.overall || "ok";
  const checks = system.health?.checks || [];
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        padding: "8px 12px", borderRadius: 8, marginTop: 8,
        border: `1px solid ${healthColor(overall)}`,
        background:
          overall === "ok" ? "rgba(44, 122, 75, 0.07)" :
          overall === "warn" ? "rgba(180, 105, 14, 0.08)" :
          "rgba(192, 57, 43, 0.08)",
      }}
    >
      <strong style={{ fontSize: 12, color: healthColor(overall), textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {healthDot(overall)}
        {overall === "ok" ? "All systems normal" :
         overall === "warn" ? "Degraded" :
         "Action needed"}
      </strong>
      {checks.map((c) => (
        <span key={c.name} style={{ fontSize: 11.5, color: "var(--ink-2)" }} title={c.detail}>
          {healthDot(c.state)}
          <strong style={{ marginRight: 4 }}>{c.name}:</strong>
          {c.detail}
        </span>
      ))}
      {system.user_activity && (
        <span style={{ fontSize: 11.5, color: "var(--ink-3)", marginLeft: "auto" }}>
          <strong>{system.user_activity.total_users}</strong> users ·
          {" "}<strong>{system.user_activity.active_24h}</strong> active 24h ·
          {" "}<strong>{system.user_activity.active_7d}</strong> active 7d
        </span>
      )}
    </div>
  );
}

export function AdminOverlay({ open, onClose }) {
  const [tab, setTab] = useStateAd("storage");

  // Single shared system snapshot drives the header (host + uptime),
  // the health banner, and the System tab. React Query dedupes the
  // round trip across consumers.
  const { data: systemSnap } = useQuery({
    queryKey: ["admin-system"],
    queryFn: getAdminSystem,
    enabled: open,
    staleTime: 6_000,
    refetchInterval: open ? 6_000 : false,
  });

  const { data: hwSnap } = useQuery({
    queryKey: ["admin-hardware-header"],
    queryFn: getAdminHardware,
    enabled: open,
    staleTime: 20_000,
  });

  const headerUptime = systemSnap?.uptime?.host_uptime_seconds;
  const headerDisk = hwSnap?.disks?.[0];

  return (
    <ModalAd open={open} onClose={onClose} size="xl" labelledBy="ad-title">
      <div className="modal__head admin__head">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 id="ad-title">
            <span className="admin__chip">DEV</span>
            Admin console
          </h2>
          <p>
            {systemSnap ? (
              <>
                Host: <span className="mono">{systemSnap.uptime.platform}</span> ·
                uptime <span className="mono">{fmtDuration(headerUptime)}</span>
                {headerDisk && (
                  <> · disk <span className="mono">{headerDisk.percent}% / {admBytes(headerDisk.total_bytes)}</span></>
                )}
              </>
            ) : (
              <span style={{ color: "var(--ink-3)" }}>Loading host…</span>
            )}
          </p>
          <HealthBanner system={systemSnap}/>
        </div>
        <ModalCloseAd onClose={onClose}/>
      </div>

      <div className="admin__tabs">
        {[
          { id: "storage",   label: "Storage" },
          { id: "users",     label: "Users" },
          { id: "audit",     label: "Audit" },
          { id: "models",    label: "Models" },
          { id: "tasks",     label: "Tasks" },
          { id: "logs",      label: "Logs" },
          { id: "system",    label: "System" },
          { id: "processes", label: "Processes" },
          { id: "hardware",  label: "Hardware" },
        ].map(t => (
          <button key={t.id} className="admin__tab" data-active={tab === t.id} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="modal__body admin__body">
        {tab === "storage"   && <RealStorageTab open={open} activity={systemSnap?.user_activity}/>}
        {tab === "users"     && <RealUsersTab open={open}/>}
        {tab === "audit"     && <RealAuditTab open={open}/>}
        {tab === "models"    && <RealModelsTab open={open}/>}
        {tab === "tasks"     && <RealTasksTab open={open}/>}
        {tab === "logs"      && <RealLogsTab open={open}/>}
        {tab === "system"    && <RealSystemTab open={open} snap={systemSnap}/>}
        {tab === "processes" && <RealProcessesTab open={open}/>}
        {tab === "hardware"  && <RealHardwareTab open={open}/>}
      </div>

      <div className="modal__foot">
        <span className="modal__foot-left mono">
          neuthek {systemSnap?.version || "0.1.0"} · env {systemSnap?.env || "?"} · live
        </span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </ModalAd>
  );
}

// ---------- Storage ----------

function RealStorageTab({ open, activity }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-storage"],
    queryFn: () => getAdminStorage(50),
    enabled: open,
    staleTime: 15_000,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (error) return <div style={{ color: "var(--danger)", padding: 20 }}>Error: {String(error.message || error)}</div>;
  if (!data) return null;

  // Only render categories with non-zero bytes so an early-stage box
  // doesn't fill the dashboard with empty "video 0 B" cards.
  const cats = Object.entries(data.by_category || {}).filter(([, v]) => v > 0);
  const avgPerUser = activity && activity.total_users
    ? data.total_bytes / activity.total_users
    : null;

  return (
    <div>
      <div className="admin-system">
        <div className="admin-card">
          <div className="admin-card__label">Total stored</div>
          <div className="admin-card__num">{admBytes(data.total_bytes)}</div>
          <div className="admin-card__sub">across {data.total_images.toLocaleString()} files</div>
        </div>
        <div className="admin-card">
          <div className="admin-card__label">Users</div>
          <div className="admin-card__num">{activity?.total_users ?? "—"}</div>
          <div className="admin-card__sub">
            {activity ? `${activity.active_24h} active 24h · ${activity.active_7d} active 7d` : "loading…"}
          </div>
        </div>
        {avgPerUser != null && (
          <div className="admin-card">
            <div className="admin-card__label">Avg / user</div>
            <div className="admin-card__num">{admBytes(avgPerUser)}</div>
            <div className="admin-card__sub">storage divided by user count</div>
          </div>
        )}
        {cats.map(([k, v]) => (
          <div className="admin-card" key={k}>
            <div className="admin-card__label">{k}</div>
            <div className="admin-card__num">{admBytes(v)}</div>
            <div className="admin-card__sub">
              {data.total_bytes ? `${((v / data.total_bytes) * 100).toFixed(0)}% of total` : ""}
            </div>
          </div>
        ))}
      </div>

      <div className="admin-callout">
        <div className="admin-callout__title">Top users by storage</div>
      </div>
      <table className="admin-table admin-table--compact">
        <thead>
          <tr>
            <th>User</th>
            <th style={{ textAlign: "right" }}>Used</th>
            <th style={{ textAlign: "right" }}>Quota</th>
            <th style={{ textAlign: "right" }}>% of quota</th>
            <th style={{ textAlign: "right" }}>Files</th>
          </tr>
        </thead>
        <tbody>
          {data.top_users.map((u) => {
            const pct = u.quota_bytes ? Math.round((u.used_bytes / u.quota_bytes) * 100) : 0;
            return (
              <tr key={u.user_id}>
                <td>
                  <strong>{u.display_name || u.email.split("@")[0]}</strong>
                  <div style={{ color: "var(--ink-3)", fontSize: 11 }}>{u.email}</div>
                </td>
                <td className="mono" style={{ textAlign: "right" }}>{admBytes(u.used_bytes)}</td>
                <td className="mono" style={{ textAlign: "right" }}>{admBytes(u.quota_bytes)}</td>
                <td className="mono" style={{ textAlign: "right", color: pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warn, #b4690e)" : undefined }}>
                  {pct}%
                </td>
                <td className="mono" style={{ textAlign: "right" }}>{u.image_count.toLocaleString()}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------- Users ----------

function RoleCell({ user }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useStateAd(false);
  const change = async (e) => {
    const next = e.target.value;
    if (next === user.role) return;
    setBusy(true);
    try {
      await updateUserRole(user.id, next);
      toast.success(`${user.email} → ${next}`);
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    } catch (err) {
      toast.error(err?.detail || "Could not update role");
    } finally {
      setBusy(false);
    }
  };
  return (
    <select
      value={user.role}
      disabled={busy}
      onChange={change}
      className="input"
      style={{ padding: "2px 6px", fontSize: 12, width: 110 }}
      aria-label="Role"
    >
      <option value="user">user</option>
      <option value="admin">admin</option>
      <option value="superuser">superuser</option>
    </select>
  );
}

function QuotaCell({ user }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useStateAd(false);
  const [val, setVal] = useStateAd("");
  const [busy, setBusy] = useStateAd(false);
  const begin = () => {
    setVal(String(Math.round((user.quota_bytes || 0) / (1024 ** 3) * 10) / 10));
    setEditing(true);
  };
  const commit = async () => {
    const gb = parseFloat(val);
    if (isNaN(gb) || gb < 0) {
      setEditing(false);
      return;
    }
    const bytes = Math.round(gb * 1024 ** 3);
    if (bytes === user.quota_bytes) {
      setEditing(false);
      return;
    }
    setBusy(true);
    try {
      await updateUserQuota(user.id, bytes);
      toast.success(`${user.email} quota → ${gb} GB`);
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    } catch (err) {
      toast.error(err?.detail || "Could not update quota");
    } finally {
      setBusy(false);
      setEditing(false);
    }
  };
  if (!editing) {
    return (
      <span
        className="mono"
        onClick={begin}
        title="Click to edit"
        style={{ cursor: "pointer", textDecoration: "underline dotted", textDecorationColor: "var(--ink-4)" }}
      >
        {admBytes(user.quota_bytes)}
      </span>
    );
  }
  return (
    <input
      autoFocus
      type="number"
      step="0.1"
      min="0"
      disabled={busy}
      value={val}
      onChange={(e) => setVal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        else if (e.key === "Escape") setEditing(false);
      }}
      className="input"
      style={{ padding: "2px 6px", fontSize: 12, width: 90, textAlign: "right" }}
      aria-label="Quota in GB"
    />
  );
}

function RealUsersTab({ open }) {
  const [q, setQ] = useStateAd("");
  const { data, isLoading } = useQuery({
    queryKey: ["admin-users", q],
    queryFn: () => listAdminUsers(q || null, 100, 0),
    enabled: open,
    staleTime: 10_000,
  });
  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
        <input
          className="input"
          placeholder="Search by email"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ maxWidth: 280 }}
        />
        <span style={{ color: "var(--ink-3)", fontSize: 12 }}>
          {data ? `${data.length} users` : ""}
        </span>
      </div>
      {isLoading ? (
        <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>
      ) : (
        <table className="admin-table admin-table--compact">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Verified</th>
              <th>Last seen</th>
              <th style={{ textAlign: "right" }}>Used</th>
              <th style={{ textAlign: "right" }}>Quota</th>
              <th style={{ textAlign: "right" }}>Files</th>
            </tr>
          </thead>
          <tbody>
            {(data || []).map((u) => (
              <tr key={u.id}>
                <td>
                  <strong>{u.display_name || u.email.split("@")[0]}</strong>
                  <div className="mono" style={{ color: "var(--ink-3)", fontSize: 11 }}>{u.email}</div>
                </td>
                <td><RoleCell user={u}/></td>
                <td>{u.is_verified
                  ? <Icon name="check" size={12}/>
                  : <span style={{ color: "var(--ink-3)" }}>—</span>}</td>
                <td style={{ fontSize: 11, color: "var(--ink-3)" }}>{fmtRelativeTime(u.last_seen_at)}</td>
                <td className="mono" style={{ textAlign: "right" }}>{admBytes(u.used_bytes)}</td>
                <td style={{ textAlign: "right" }}><QuotaCell user={u}/></td>
                <td className="mono" style={{ textAlign: "right" }}>{u.image_count.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------- Audit ----------

// Buckets the action prefixes into operator-meaningful groups so the
// Audit and Logs tabs both get a one-click filter row instead of
// requiring the user to know exact prefix strings.
const ACTION_FILTERS = [
  { id: "all",     label: "All",      prefix: "",         color: "var(--ink-2)" },
  { id: "auth",    label: "Auth",     prefix: "auth.",    color: "#4a6bf5" },
  { id: "share",   label: "Sharing",  prefix: "share.",   color: "#2c7a4b" },
  { id: "image",   label: "Files",    prefix: "image.",   color: "#b4690e" },
  { id: "consent", label: "Consent",  prefix: "consent.", color: "#7b3fc2" },
  { id: "admin",   label: "Admin",    prefix: "admin.",   color: "#c0392b" },
  { id: "account", label: "Account",  prefix: "account.", color: "#0e7a98" },
];

function RealAuditTab({ open }) {
  const [filter, setFilter] = useStateAd("all");
  const prefix = ACTION_FILTERS.find(f => f.id === filter)?.prefix || "";
  const { data, isLoading } = useQuery({
    queryKey: ["admin-audit", prefix],
    queryFn: () => listAdminAudit({ limit: 200, actionPrefix: prefix || null }),
    enabled: open,
    staleTime: 10_000,
  });
  return (
    <div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        {ACTION_FILTERS.map(f => (
          <button
            key={f.id}
            className="btn btn--ghost btn--sm"
            onClick={() => setFilter(f.id)}
            style={{
              borderColor: filter === f.id ? f.color : undefined,
              color: filter === f.id ? f.color : undefined,
              fontWeight: filter === f.id ? 600 : undefined,
              fontSize: 11.5,
            }}
          >
            {f.label}
          </button>
        ))}
        <span style={{ color: "var(--ink-3)", fontSize: 12, marginLeft: "auto" }}>
          {data ? `${data.length} entries` : ""}
        </span>
      </div>
      {isLoading ? (
        <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>
      ) : (data || []).length === 0 ? (
        <div style={{ color: "var(--ink-3)", padding: 14 }}>No entries match this filter.</div>
      ) : (
        <div>
          {(data || []).map((e) => <AuditLineRow key={e.id} entry={e}/>)}
        </div>
      )}
    </div>
  );
}

// ---------- Models ----------

function RealModelsTab({ open }) {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-models"],
    queryFn: getAdminModels,
    enabled: open,
    staleTime: 30_000,
    refetchInterval: open ? 30_000 : false,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;
  return (
    <div>
      <div className="admin-callout" style={{ marginBottom: 14 }}>
        <div className="admin-callout__title">
          Inference backend: <span className="mono">{data.inference_backend}</span>
        </div>
        <p>
          {data.gpu_available
            ? "GPU detected; vision models load on CUDA when the worker boots."
            : "No GPU detected — models load on CPU. Performance will be lower."}
        </p>
      </div>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Role</th>
            <th>Model</th>
            <th>State</th>
            <th>Device</th>
            <th style={{ textAlign: "right" }}>GPU mem</th>
            <th>Last used</th>
          </tr>
        </thead>
        <tbody>
          {data.models.map((m) => (
            <tr key={m.id}>
              <td>
                <strong>{m.label}</strong>
                <div style={{ color: "var(--ink-3)", fontSize: 11 }}>{m.role}</div>
                <div style={{ color: "var(--ink-4)", fontSize: 10, fontFamily: "monospace" }}>{m.name}</div>
              </td>
              <td className="mono" style={{ color: "var(--ink-3)" }}>{m.variant || "—"}</td>
              <td>
                <span style={{
                  fontSize: 11, padding: "2px 6px", borderRadius: 4,
                  background: m.state === "loaded" ? "rgba(44, 122, 75, 0.12)" :
                              m.state === "error" ? "rgba(192, 57, 43, 0.12)" :
                              "rgba(0, 0, 0, 0.05)",
                  color: m.state === "loaded" ? "var(--success, #2c7a4b)" :
                         m.state === "error" ? "var(--danger, #c0392b)" :
                         "var(--ink-3)",
                }}>
                  {m.state}
                </span>
              </td>
              <td className="mono" style={{ color: m.device ? undefined : "var(--ink-3)" }}>
                {m.device || (m.enabled ? "—" : "disabled")}
              </td>
              <td className="mono" style={{ textAlign: "right" }}>
                {m.memory_allocated_bytes ? admBytes(m.memory_allocated_bytes) : "—"}
              </td>
              <td style={{ fontSize: 11, color: "var(--ink-3)" }}>{fmtRelativeTime(m.last_used_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ color: "var(--ink-3)", fontSize: 11, marginTop: 12 }}>
        Models report load/unload via the worker_heartbeats / model_runs
        tables (C8.2). State stays "configured" until the worker first
        loads the model on a job — that's the truthful cold state.
      </div>
    </div>
  );
}

// ---------- Tasks ----------

function WorkerCard({ w }) {
  const meta = w.metadata || {};
  return (
    <div className="admin-card" style={{ minWidth: 220 }}>
      <div className="admin-card__label">{w.kind}</div>
      <div className="admin-card__num" style={{ color: w.alive ? "var(--success, #2c7a4b)" : "var(--danger, #c0392b)", fontSize: 16 }}>
        {w.alive ? "alive" : "stale"}
      </div>
      <div className="admin-card__sub">
        seen {fmtRelativeTime(w.last_seen)} · pid {w.pid ?? "?"} · {w.hostname || "?"}
      </div>
      {meta.queue_depth != null && (
        <div className="admin-card__sub">queue depth (worker view): <strong>{meta.queue_depth}</strong></div>
      )}
    </div>
  );
}

function RealTasksTab({ open }) {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-tasks"],
    queryFn: getAdminTasks,
    enabled: open,
    staleTime: 4_000,
    refetchInterval: open ? 4_000 : false,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;
  const q = data.queue;
  return (
    <div>
      <div className="admin-system" style={{ marginBottom: 14 }}>
        <div className="admin-card">
          <div className="admin-card__label">Queue depth</div>
          <div className="admin-card__num" style={{ color: q.depth > 50 ? "var(--warn, #b4690e)" : undefined }}>
            {q.reachable ? q.depth : "—"}
          </div>
          <div className="admin-card__sub">{q.queue_key || "redis unreachable"}</div>
        </div>
        <div className="admin-card">
          <div className="admin-card__label">In-flight</div>
          <div className="admin-card__num">{q.reachable ? q.active : "—"}</div>
          <div className="admin-card__sub">dedupe set size</div>
        </div>
        <div className="admin-card">
          <div className="admin-card__label">Workers</div>
          <div className="admin-card__num" style={{ color: data.workers.some(w => w.alive) ? "var(--success, #2c7a4b)" : "var(--danger, #c0392b)" }}>
            {data.workers.filter(w => w.alive).length}<span>/{data.workers.length}</span>
          </div>
          <div className="admin-card__sub">alive / total tracked</div>
        </div>
        {data.workers.map((w) => <WorkerCard key={w.worker_id} w={w}/>)}
      </div>

      <div className="admin-callout">
        <div className="admin-callout__title">Recent activity</div>
        <p>50 most-recent events visible to the operator. Click any line for full details.</p>
      </div>
      <div style={{ display: "grid", gap: 0 }}>
        {data.recent.length === 0 ? (
          <div style={{ color: "var(--ink-3)", padding: 14 }}>No recent activity.</div>
        ) : data.recent.map((e) => (
          <AuditLineRow key={e.id} entry={e}/>
        ))}
      </div>
    </div>
  );
}

function AuditLineRow({ entry }) {
  const [expanded, setExpanded] = useStateAd(false);
  const summary = formatAuditLine(entry);
  return (
    <div
      onClick={() => setExpanded(x => !x)}
      style={{
        display: "grid", gridTemplateColumns: "110px 1fr", gap: 12,
        padding: "8px 10px",
        borderBottom: "1px solid var(--line, rgba(0,0,0,0.06))",
        cursor: "pointer",
        background: expanded ? "var(--surface-2, rgba(0,0,0,0.02))" : undefined,
      }}
    >
      <div style={{ fontSize: 11, color: "var(--ink-3)", fontFamily: "monospace" }}>
        {fmtRelativeTime(entry.created_at)}
      </div>
      <div>
        <div style={{ fontSize: 13 }}>{summary}</div>
        {expanded && entry.details && (
          <pre style={{
            fontSize: 11, color: "var(--ink-3)", marginTop: 6,
            padding: 8, background: "var(--surface, #fff)",
            border: "1px solid var(--line, rgba(0,0,0,0.06))",
            borderRadius: 6, overflow: "auto",
          }}>
{JSON.stringify(entry.details, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}

// ---------- Logs ----------

function RealLogsTab({ open }) {
  const [filter, setFilter] = useStateAd("all");
  const { data, isLoading } = useQuery({
    queryKey: ["admin-logs"],
    queryFn: () => getAdminLogs(500),
    enabled: open,
    staleTime: 5_000,
    refetchInterval: open ? 5_000 : false,
  });
  const filtered = useMemoAd(() => {
    if (!data) return [];
    const prefix = ACTION_FILTERS.find(f => f.id === filter)?.prefix || "";
    if (!prefix) return data.lines;
    return data.lines.filter((e) => (e.action || "").startsWith(prefix));
  }, [data, filter]);
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;
  return (
    <div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        {ACTION_FILTERS.map(f => (
          <button
            key={f.id}
            className="btn btn--ghost btn--sm"
            onClick={() => setFilter(f.id)}
            style={{
              borderColor: filter === f.id ? f.color : undefined,
              color: filter === f.id ? f.color : undefined,
              fontWeight: filter === f.id ? 600 : undefined,
              fontSize: 11.5,
            }}
          >
            {f.label}
          </button>
        ))}
        <span style={{ color: "var(--ink-3)", fontSize: 12, marginLeft: "auto" }}>
          {filtered.length} of {data.lines.length} · live
        </span>
      </div>
      <div style={{ maxHeight: "60vh", overflowY: "auto", borderRadius: 8, border: "1px solid var(--line, rgba(0,0,0,0.06))" }}>
        {filtered.length === 0 ? (
          <div style={{ color: "var(--ink-3)", padding: 14 }}>
            No log lines for this filter. The stream auto-refreshes every 5 s.
          </div>
        ) : filtered.map((e) => (
          <AuditLineRow key={e.id} entry={e}/>
        ))}
      </div>
    </div>
  );
}

// ---------- System ----------

function RealSystemTab({ open, snap }) {
  const { data: hw } = useQuery({
    queryKey: ["admin-hardware-system"],
    queryFn: getAdminHardware,
    enabled: open,
    staleTime: 5_000,
    refetchInterval: open ? 5_000 : false,
  });

  const historyRef = useRefAd({ cpu: [], mem: [], queue: [] });
  useEffectAd(() => {
    if (!hw || !snap) return;
    const h = historyRef.current;
    h.cpu = [...h.cpu, hw.cpu.percent].slice(-32);
    h.mem = [...h.mem, Math.round((hw.memory.used_bytes / 1024 / 1024 / 1024) * 10) / 10].slice(-32);
    h.queue = [...h.queue, snap.redis?.queue_depth ?? 0].slice(-32);
  }, [hw, snap]);

  if (!snap) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  const memUsedGB = hw ? (hw.memory.used_bytes / 1024 / 1024 / 1024).toFixed(1) : "—";
  const memTotalGB = hw ? (hw.memory.total_bytes / 1024 / 1024 / 1024).toFixed(0) : "—";
  return (
    <div className="admin-system">
      <div className="admin-card">
        <div className="admin-card__label">CPU</div>
        <div className="admin-card__num">{hw ? hw.cpu.percent.toFixed(0) : "—"}<span>%</span></div>
        <div className="admin-card__sub">
          {hw ? `${hw.cpu.logical_cores} cores · idle ${(100 - hw.cpu.percent).toFixed(0)}%` : ""}
        </div>
        <Sparkline values={historyRef.current.cpu}/>
      </div>
      <div className="admin-card">
        <div className="admin-card__label">Memory</div>
        <div className="admin-card__num">{memUsedGB}<span>GB</span></div>
        <div className="admin-card__sub">of {memTotalGB} GB · {hw ? hw.memory.percent.toFixed(0) : "—"}%</div>
        <Sparkline values={historyRef.current.mem} color="var(--ink-2)"/>
      </div>
      <div className="admin-card">
        <div className="admin-card__label">Uptime</div>
        <div className="admin-card__num">{fmtDuration(snap.uptime.process_uptime_seconds).split(" ")[0]}</div>
        <div className="admin-card__sub">API process · py {snap.uptime.python_version}</div>
      </div>
      <div className="admin-card">
        <div className="admin-card__label">Host</div>
        <div className="admin-card__num" style={{ fontSize: 16 }}>{snap.uptime.platform.split("-").slice(0, 2).join(" ")}</div>
        <div className="admin-card__sub">env {snap.env} · up {fmtDuration(snap.uptime.host_uptime_seconds)}</div>
      </div>
      <div className="admin-card">
        <div className="admin-card__label">DB pool</div>
        <div className="admin-card__num" style={{ color: snap.db_pool.reachable ? undefined : "var(--danger)" }}>
          {snap.db_pool.checked_out ?? "—"}<span>/{snap.db_pool.size ?? "—"}</span>
        </div>
        <div className="admin-card__sub">checked-out / pool size</div>
      </div>
      <div className="admin-card">
        <div className="admin-card__label">Redis</div>
        <div className="admin-card__num" style={{ color: snap.redis.reachable ? undefined : "var(--danger)" }}>
          {snap.redis.reachable ? admBytes(snap.redis.memory_used_bytes || 0) : "DOWN"}
        </div>
        <div className="admin-card__sub">
          {snap.redis.reachable ? `${snap.redis.dbsize} keys · queue ${snap.redis.queue_depth}` : (snap.redis.error || "")}
        </div>
        <Sparkline values={historyRef.current.queue} color="var(--ink-2)"/>
      </div>
      <div className="admin-card" style={{ gridColumn: "span 2" }}>
        <div className="admin-card__label">MinIO buckets</div>
        {snap.minio.reachable ? (
          <table className="admin-table admin-table--compact" style={{ marginTop: 8 }}>
            <thead>
              <tr><th>Bucket</th><th style={{ textAlign: "right" }}>Objects</th><th style={{ textAlign: "right" }}>Size</th></tr>
            </thead>
            <tbody>
              {(snap.minio.buckets || []).map((b) => (
                <tr key={b.name}>
                  <td className="mono">{b.name}</td>
                  <td className="mono" style={{ textAlign: "right" }}>{(b.objects ?? 0).toLocaleString()}</td>
                  <td className="mono" style={{ textAlign: "right" }}>{admBytes(b.size_bytes ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div style={{ color: "var(--danger)" }}>{snap.minio.error || "unreachable"}</div>
        )}
      </div>
    </div>
  );
}

// ---------- Processes ----------

function RealProcessesTab({ open }) {
  const [filter, setFilter] = useStateAd("all");
  const { data, isLoading } = useQuery({
    queryKey: ["admin-processes"],
    queryFn: () => getAdminProcesses(20),
    enabled: open,
    staleTime: 5_000,
    refetchInterval: open ? 5_000 : false,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;
  const rows = filter === "all" ? data.processes : data.processes.filter(p => p.kind === filter);
  return (
    <div>
      {data.workers.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>
            Heartbeat-tracked workers
          </div>
          <div className="admin-system">
            {data.workers.map((w) => <WorkerCard key={w.worker_id} w={w}/>)}
          </div>
        </div>
      )}
      <div className="admin-proc__head">
        <div className="admin-proc__totals">
          <span>Top {data.processes.length} CPU <strong className="mono">{data.totals.cpu_percent_sum.toFixed(1)}%</strong></span>
          <span style={{ marginLeft: 18 }}>Total RAM <strong className="mono">{admBytes(data.totals.memory_rss_bytes_sum)}</strong></span>
          <span style={{ marginLeft: 18 }}>Sampled <strong className="mono">{data.totals.count}</strong></span>
        </div>
        <div className="admin-proc__filters">
          {["all", "api", "ai", "data", "system"].map(f => (
            <button key={f} className="admin-proc__filter" data-active={filter === f} onClick={() => setFilter(f)}>{f}</button>
          ))}
        </div>
      </div>
      <table className="admin-table admin-table--compact">
        <thead>
          <tr>
            <th>Process</th><th>PID</th><th>Kind</th><th>User</th>
            <th style={{ textAlign: "right" }}>CPU</th>
            <th style={{ textAlign: "right" }}>RAM</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(p => (
            <tr key={p.pid}>
              <td>
                <span className="mono">{p.name}</span>
                {p.cmdline && (
                  <div style={{ color: "var(--ink-3)", fontSize: 10, marginTop: 2 }} title={p.cmdline}>
                    {p.cmdline.length > 70 ? p.cmdline.slice(0, 70) + "…" : p.cmdline}
                  </div>
                )}
              </td>
              <td className="mono" style={{ color: "var(--ink-3)" }}>{p.pid}</td>
              <td><span className="admin-kind" data-kind={p.kind}>{p.kind}</span></td>
              <td className="mono" style={{ color: "var(--ink-3)", fontSize: 11 }}>{p.username || "—"}</td>
              <td className="mono" style={{ textAlign: "right" }}>
                <span className="admin-bar"><span className="admin-bar__fill" style={{ width: Math.min(100, p.cpu_percent) + "%" }}/></span>
                {p.cpu_percent.toFixed(1)}%
              </td>
              <td className="mono" style={{ textAlign: "right" }}>{admBytes(p.memory_rss_bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------- Hardware ----------

function RealHardwareTab({ open }) {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-hardware"],
    queryFn: getAdminHardware,
    enabled: open,
    staleTime: 10_000,
    refetchInterval: open ? 10_000 : false,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;
  return (
    <div className="admin-hw">
      <div className="admin-hw__group">
        <div className="admin-hw__group-title">Compute</div>
        <div className="admin-hw__row">
          <span>CPU</span>
          <strong className="mono">
            {data.cpu.brand || "Unknown"} · {data.cpu.physical_cores ?? "?"}C / {data.cpu.logical_cores ?? "?"}T
            {data.cpu.freq?.current_mhz ? ` · ${(data.cpu.freq.current_mhz/1000).toFixed(1)} GHz` : ""}
          </strong>
        </div>
        <div className="admin-hw__row">
          <span>CPU load</span>
          <strong className="mono">
            {data.cpu.percent.toFixed(0)}% now
            {data.cpu.load_avg_1_5_15 ? ` · load ${data.cpu.load_avg_1_5_15.map(n => n.toFixed(2)).join(" / ")}` : ""}
          </strong>
        </div>
        <div className="admin-hw__row">
          <span>Memory</span>
          <strong className="mono">
            {admBytes(data.memory.used_bytes)} / {admBytes(data.memory.total_bytes)} ({data.memory.percent.toFixed(0)}%)
          </strong>
        </div>
        <div className="admin-hw__row">
          <span>Swap</span>
          <strong className="mono">
            {data.memory.swap_total_bytes
              ? `${admBytes(data.memory.swap_used_bytes)} / ${admBytes(data.memory.swap_total_bytes)}`
              : "—"}
          </strong>
        </div>
        <div className="admin-hw__row">
          <span>Accelerators</span>
          <strong className="mono" style={{ color: data.gpu.available ? "var(--success, #2c7a4b)" : "var(--ink-3)" }}>
            {data.gpu.available
              ? `${data.gpu.devices.length} device${data.gpu.devices.length === 1 ? "" : "s"} · ${data.gpu.backend}${data.gpu.source ? ` (${data.gpu.source})` : ""}`
              : "no accelerator detected"}
          </strong>
        </div>
        {data.gpu.devices.map((g, i) => {
          const kindColor =
            g.kind === "CUDA" ? "#2c7a4b" :
            g.kind === "NPU" ? "#7b3fc2" :
            (g.kind || "").startsWith("iGPU") ? "#0e7a98" :
            "var(--ink-3)";
          return (
            <div className="admin-hw__row" key={g.index ?? i} style={{ paddingLeft: 16 }}>
              <span>
                {g.kind && (
                  <span
                    style={{
                      marginRight: 6, padding: "1px 6px", borderRadius: 4,
                      fontSize: 9.5, fontWeight: 600, letterSpacing: "0.04em",
                      textTransform: "uppercase",
                      background: kindColor === "var(--ink-3)" ? "rgba(0,0,0,0.05)" : `${kindColor}22`,
                      color: kindColor,
                    }}
                  >
                    {g.kind}
                  </span>
                )}
                {g.name}
                {g.vendor && (
                  <span style={{ marginLeft: 6, fontSize: 10, color: "var(--ink-3)" }}>
                    {g.vendor}
                  </span>
                )}
                {g.inaccessible && (
                  <span
                    style={{
                      marginLeft: 6, fontSize: 10, padding: "1px 5px", borderRadius: 4,
                      background: "var(--warn-bg, #fff7e6)", color: "var(--warn, #b4690e)",
                    }}
                    title="Visible to the host OS but not usable from this process"
                  >
                    inaccessible
                  </span>
                )}
                {g.openvino_device && (
                  <span
                    style={{
                      marginLeft: 6, fontSize: 10, padding: "1px 5px", borderRadius: 4,
                      background: "rgba(14, 122, 152, 0.12)", color: "#0e7a98",
                    }}
                    title="Targetable from OpenVINO"
                  >
                    openvino: {g.openvino_device}
                  </span>
                )}
              </span>
              <strong className="mono">
                {g.total_memory_bytes ? admBytes(g.total_memory_bytes) : "—"}
                {g.utilization_percent != null ? ` · ${g.utilization_percent}% util` : ""}
                {g.allocated_memory_bytes != null ? ` · ${admBytes(g.allocated_memory_bytes)} in use` : ""}
                {g.driver_version ? ` · drv ${g.driver_version}` : ""}
              </strong>
            </div>
          );
        })}
        {Array.isArray(data.gpu.notes) && data.gpu.notes.length > 0 && (
          <div
            style={{
              marginTop: 10, padding: "8px 12px", borderRadius: 6,
              background: "var(--warn-bg, #fff7e6)", color: "var(--warn-fg, #6b4a0e)",
              fontSize: 11.5, lineHeight: 1.5,
            }}
          >
            {data.gpu.notes.map((n, i) => (
              <div key={i} style={{ marginTop: i === 0 ? 0 : 4 }}>• {n}</div>
            ))}
          </div>
        )}
      </div>

      <div className="admin-hw__group">
        <div className="admin-hw__group-title">Storage</div>
        {data.disks.length === 0 ? (
          <div style={{ color: "var(--ink-3)" }}>No disks reported by psutil.</div>
        ) : data.disks.map((d) => (
          <div className="admin-hw__row" key={d.mountpoint}>
            <span>{d.mountpoint}</span>
            <strong className="mono" style={{ color: d.percent >= 95 ? "var(--danger)" : d.percent >= 85 ? "var(--warn, #b4690e)" : undefined }}>
              {d.device} ({d.fstype}) · {admBytes(d.used_bytes)} / {admBytes(d.total_bytes)} · {d.percent.toFixed(0)}% used
            </strong>
          </div>
        ))}
      </div>

      <div className="admin-hw__group">
        <div className="admin-hw__group-title">Network</div>
        {data.network.interfaces.length === 0 ? (
          <div style={{ color: "var(--ink-3)" }}>No active NICs.</div>
        ) : data.network.interfaces.map((n) => (
          <div className="admin-hw__row" key={n.name}>
            <span>{n.name}</span>
            <strong className="mono">
              {n.ipv4 || "(no v4)"}
              {n.speed_mbps ? ` · ${n.speed_mbps} Mbps` : ""}
              {" · "}↑{admBytes(n.bytes_sent)} ↓{admBytes(n.bytes_recv)}
            </strong>
          </div>
        ))}
      </div>

      {(data.thermals.temps.length > 0 || data.thermals.fans.length > 0) && (
        <div className="admin-hw__group">
          <div className="admin-hw__group-title">Thermals</div>
          {data.thermals.temps.map((t, i) => (
            <div className="admin-hw__row" key={`t${i}`}>
              <span>{t.label}</span>
              <strong className="mono" style={{ color: (t.current_c != null && t.critical_c && t.current_c > t.critical_c * 0.9) ? "var(--danger)" : undefined }}>
                {t.current_c != null ? `${t.current_c.toFixed(1)} °C` : "—"}
                {t.high_c ? ` · high ${t.high_c.toFixed(0)}` : ""}
                {t.critical_c ? ` · crit ${t.critical_c.toFixed(0)}` : ""}
              </strong>
            </div>
          ))}
          {data.thermals.fans.map((f, i) => (
            <div className="admin-hw__row" key={`f${i}`}>
              <span>{f.label}</span>
              <strong className="mono">{f.rpm != null ? `${f.rpm} RPM` : "—"}</strong>
            </div>
          ))}
        </div>
      )}

      <div style={{ color: "var(--ink-3)", fontSize: 11, marginTop: 12 }}>
        Thermal sensors are platform-specific. Linux reads /sys/class/hwmon;
        Windows queries ACPI thermal zones (requires admin) — most laptops
        only expose a handful via WMI. SMART, PSU, and per-NIC link
        details still need vendor adapters per todo §F1.
      </div>
    </div>
  );
}
