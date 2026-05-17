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
  getAdminQueue,
  adminDrainUserQueue,
  getAdminModels,
  getAdminTasks,
  getAdminLogs,
  adminSearch,
  bulkUpdateQuota,
  bulkDeleteUsers,
  bulkRevokeConsent,
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

// 250ms debounce keeps the search box quiet while you're typing —
// don't fire a /admin/search round trip on every keystroke.
function useDebounced(value, ms = 250) {
  const [v, setV] = useStateAd(value);
  useEffectAd(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

// Header search box + results dropdown. Sits in the admin modal head
// so it's visible from every tab. Clicking a result row jumps to the
// owning tab (Users / Audit / Files) so the operator can drill in.
function GlobalSearch({ onJumpToTab }) {
  const [q, setQ] = useStateAd("");
  const [focused, setFocused] = useStateAd(false);
  const debounced = useDebounced(q.trim(), 250);
  const { data, isLoading } = useQuery({
    queryKey: ["admin-search", debounced],
    queryFn: () => adminSearch(debounced, 8),
    enabled: focused && debounced.length >= 2,
    staleTime: 5_000,
  });
  const shouldShow = focused && debounced.length >= 2;
  const total = (data?.users.length || 0) + (data?.audit.length || 0) + (data?.images.length || 0);
  return (
    <div style={{ position: "relative", marginTop: 10 }}>
      <input
        className="input"
        placeholder="Search users · audit events · files (min 2 chars)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 200)}
        style={{
          width: "100%", padding: "8px 12px", fontSize: 13,
          background: "var(--surface-2)", border: "1px solid var(--line)",
          borderRadius: 8, color: "var(--ink)",
        }}
      />
      {shouldShow && (
        <div
          style={{
            position: "absolute", top: "100%", left: 0, right: 0, marginTop: 6,
            background: "var(--surface, #fff)", border: "1px solid var(--line)",
            borderRadius: 8, boxShadow: "0 6px 22px rgba(0,0,0,0.12)",
            maxHeight: 360, overflowY: "auto", zIndex: 50,
          }}
        >
          {isLoading ? (
            <div style={{ padding: 14, color: "var(--ink-3)", fontSize: 12 }}>Searching…</div>
          ) : total === 0 ? (
            <div style={{ padding: 14, color: "var(--ink-3)", fontSize: 12 }}>No matches.</div>
          ) : (
            <>
              {data.users.length > 0 && (
                <SearchGroup label={`Users (${data.users.length})`} onClickAll={() => onJumpToTab("users")}>
                  {data.users.map((u) => (
                    <SearchRow
                      key={u.id}
                      primary={u.display_name || u.email.split("@")[0]}
                      secondary={u.email}
                      tag={u.role}
                      onClick={() => onJumpToTab("users")}
                    />
                  ))}
                </SearchGroup>
              )}
              {data.audit.length > 0 && (
                <SearchGroup label={`Audit (${data.audit.length})`} onClickAll={() => onJumpToTab("audit")}>
                  {data.audit.map((a) => (
                    <SearchRow
                      key={a.id}
                      primary={a.action}
                      secondary={`${fmtRelativeTime(a.created_at)}${a.user_email ? ` · ${a.user_email}` : ""}`}
                      onClick={() => onJumpToTab("audit")}
                    />
                  ))}
                </SearchGroup>
              )}
              {data.images.length > 0 && (
                <SearchGroup label={`Files (${data.images.length})`} onClickAll={() => onJumpToTab("storage")}>
                  {data.images.map((img) => (
                    <SearchRow
                      key={img.id}
                      primary={img.original_filename || "(unnamed)"}
                      secondary={
                        (img.summary_topic ? `${img.summary_topic} · ` : "") +
                        (img.byte_size_served ? admBytes(img.byte_size_served) : "")
                      }
                      onClick={() => onJumpToTab("storage")}
                    />
                  ))}
                </SearchGroup>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function SearchGroup({ label, children, onClickAll }) {
  return (
    <div style={{ borderBottom: "1px solid var(--line, rgba(0,0,0,0.05))" }}>
      <div
        onClick={onClickAll}
        style={{
          padding: "6px 12px", fontSize: 10, fontWeight: 600, letterSpacing: "0.08em",
          textTransform: "uppercase", color: "var(--ink-3)",
          background: "var(--surface-2, rgba(0,0,0,0.02))",
          cursor: "pointer",
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function SearchRow({ primary, secondary, tag, onClick }) {
  return (
    <div
      onMouseDown={(e) => { e.preventDefault(); onClick?.(); }}
      style={{
        padding: "8px 12px", cursor: "pointer", display: "flex",
        gap: 10, alignItems: "center", borderBottom: "1px solid var(--line, rgba(0,0,0,0.04))",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-2, rgba(0,0,0,0.03))")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "")}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 500 }}>{primary}</div>
        {secondary && (
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 1 }}>{secondary}</div>
        )}
      </div>
      {tag && (
        <span style={{
          fontSize: 10, padding: "1px 6px", borderRadius: 4,
          background: "rgba(0,0,0,0.05)", color: "var(--ink-3)",
        }}>{tag}</span>
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

  const isRoutedPage = typeof window !== "undefined" && window.location.pathname === "/admin";

  return (
    <ModalAd open={open} onClose={onClose} size="xl" labelledBy="ad-title">
      <div className="modal__head admin__head">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 id="ad-title">
            <span className="admin__chip">DEV</span>
            Admin console
            {!isRoutedPage && (
              <a
                href="/admin"
                style={{ marginLeft: 10, fontSize: 11, fontWeight: 400, color: "var(--ink-3)", textDecoration: "underline dotted" }}
                title="Open as full page"
              >
                open as page →
              </a>
            )}
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
          <GlobalSearch onJumpToTab={(t) => setTab(t)}/>
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
          { id: "queue",     label: "Queue" },
          { id: "developer", label: "Developer" },
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
        {tab === "queue"     && <RealQueueTab open={open}/>}
        {tab === "developer" && <RealDeveloperTab/>}
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
  const qc = useQueryClient();
  const [q, setQ] = useStateAd("");
  const [selected, setSelected] = useStateAd(() => new Set());
  const { data, isLoading } = useQuery({
    queryKey: ["admin-users", q],
    queryFn: () => listAdminUsers(q || null, 100, 0),
    enabled: open,
    staleTime: 10_000,
  });
  const rows = data || [];
  const allChecked = rows.length > 0 && rows.every(u => selected.has(u.id));
  const someChecked = rows.some(u => selected.has(u.id));
  const toggleOne = (id) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleAll = () => {
    setSelected(allChecked ? new Set() : new Set(rows.map(u => u.id)));
  };
  const clearSelection = () => setSelected(new Set());

  const doBulkQuota = async () => {
    const raw = window.prompt("New quota for selected users (GB, blank = clear override):", "");
    if (raw === null) return;
    const trimmed = raw.trim();
    let bytes = null;
    if (trimmed !== "") {
      const gb = parseFloat(trimmed);
      if (isNaN(gb) || gb < 0) { toast.error("Invalid GB value"); return; }
      bytes = Math.round(gb * 1024 ** 3);
    }
    try {
      const r = await bulkUpdateQuota(Array.from(selected), bytes);
      toast.success(`Quota updated for ${r.affected} user${r.affected === 1 ? "" : "s"}`);
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      qc.invalidateQueries({ queryKey: ["admin-storage"] });
      clearSelection();
    } catch (e) {
      toast.error(e?.detail || "Bulk quota failed");
    }
  };
  const doBulkDelete = async () => {
    if (!window.confirm(`Hard-delete ${selected.size} user${selected.size === 1 ? "" : "s"}? This drops their images, faces, share grants — irreversible.`)) return;
    try {
      const r = await bulkDeleteUsers(Array.from(selected));
      toast.success(`Deleted ${r.affected} user${r.affected === 1 ? "" : "s"}`);
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      qc.invalidateQueries({ queryKey: ["admin-storage"] });
      qc.invalidateQueries({ queryKey: ["admin-system"] });
      clearSelection();
    } catch (e) {
      toast.error(e?.detail || "Bulk delete failed");
    }
  };
  const doBulkRevokeConsent = async () => {
    const kind = window.prompt(
      "Revoke which consent kind? (leave blank to revoke ALL granted scopes)\n" +
      "Common kinds: face_recognition · gps_retention · ai_summary · semantic_search",
      "",
    );
    if (kind === null) return;
    try {
      const r = await bulkRevokeConsent(Array.from(selected), kind.trim() || null);
      toast.success(`Revoked consent for ${r.affected} user${r.affected === 1 ? "" : "s"}`);
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      clearSelection();
    } catch (e) {
      toast.error(e?.detail || "Bulk revoke failed");
    }
  };

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
        {selected.size > 0 && (
          <div style={{
            marginLeft: "auto", display: "flex", gap: 6, alignItems: "center",
            padding: "4px 10px", borderRadius: 8, fontSize: 12,
            background: "var(--surface-2, rgba(0,0,0,0.04))",
            border: "1px solid var(--line, rgba(0,0,0,0.08))",
          }}>
            <strong style={{ marginRight: 4 }}>{selected.size} selected</strong>
            <button type="button" className="btn btn--ghost btn--sm" onClick={doBulkQuota}>
              <Icon name="hard-drive" size={10}/> Quota
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={doBulkRevokeConsent}>
              <Icon name="shield" size={10}/> Revoke consent
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ color: "var(--danger, #c0392b)" }}
              onClick={doBulkDelete}
            >
              <Icon name="trash" size={10}/> Delete
            </button>
            <button type="button" className="btn-icon" onClick={clearSelection} title="Clear selection" aria-label="Clear selection">
              <Icon name="x" size={12}/>
            </button>
          </div>
        )}
      </div>
      {isLoading ? (
        <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>
      ) : (
        <table className="admin-table admin-table--compact">
          <thead>
            <tr>
              <th style={{ width: 28 }}>
                <input
                  type="checkbox"
                  checked={allChecked}
                  ref={(el) => { if (el) el.indeterminate = someChecked && !allChecked; }}
                  onChange={toggleAll}
                  aria-label="Select all"
                />
              </th>
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
            {rows.map((u) => (
              <tr key={u.id} style={{ background: selected.has(u.id) ? "var(--surface-2, rgba(0,0,0,0.03))" : undefined }}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(u.id)}
                    onChange={() => toggleOne(u.id)}
                    aria-label={`Select ${u.email}`}
                  />
                </td>
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
  // "Concurrent users" estimator — 1 ml-worker per user is the
  // realistic shape today (queue is serial, but you'd scale workers
  // to handle parallel load). Slider goes 1–20, default 1.
  const [concurrent, setConcurrent] = useStateAd(1);
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;

  // Per-model totals at the current N. enabled_only — disabled models
  // don't count toward the fleet's VRAM footprint.
  const fmtMb = (mb) => {
    if (mb == null || mb < 0) return "—";
    if (mb < 1024) return `${Math.round(mb)} MB`;
    return `${(mb / 1024).toFixed(1)} GB`;
  };
  const vram = data.vram || {};
  const enabledModels = data.models.filter((m) => m.enabled);
  const residentTotalMb = vram.resident_mb_total || 0;
  const perInferenceTotalMb = vram.per_inference_mb_total || 0;
  const totalForNMb = residentTotalMb + concurrent * perInferenceTotalMb;
  const gpuTotalMb = vram.gpu_total_mb;
  const gpuFreeMb = vram.gpu_free_mb;
  // Color tone for the summary header — green if comfortable,
  // amber if within 25% of total, red if over.
  let fitsTone = "var(--ink-3)";
  let fitsLabel = null;
  if (gpuTotalMb != null) {
    if (totalForNMb <= gpuTotalMb * 0.75) {
      fitsTone = "var(--success)";
      fitsLabel = "comfortable headroom";
    } else if (totalForNMb <= gpuTotalMb) {
      fitsTone = "var(--warning)";
      fitsLabel = "tight — close to capacity";
    } else {
      fitsTone = "var(--danger)";
      fitsLabel = "over budget — won't fit at this concurrency";
    }
  }

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

      {/* VRAM estimator — drives the right-hand "Total for N" column
          and the summary card via the slider. */}
      <div
        className="admin-card"
        style={{
          display: "grid",
          gridTemplateColumns: "1fr auto",
          gap: 16,
          alignItems: "center",
          marginBottom: 14,
          padding: 16,
          background: "var(--surface)",
          border: "1px solid var(--line)",
          borderRadius: 12,
        }}
      >
        <div style={{ display: "grid", gap: 4 }}>
          <div className="admin-card__label" style={{ color: "var(--ink-3)", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase" }}>
            Estimated VRAM for {concurrent} concurrent user{concurrent === 1 ? "" : "s"}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ fontSize: 24, fontWeight: 700, color: fitsTone }}>
              {fmtMb(totalForNMb)}
            </span>
            {gpuTotalMb != null && (
              <span style={{ fontSize: 13, color: "var(--ink-3)" }}>
                of {fmtMb(gpuTotalMb)} {gpuFreeMb != null && `(${fmtMb(gpuFreeMb)} free)`}
              </span>
            )}
            {fitsLabel && (
              <span style={{ fontSize: 12, color: fitsTone, fontWeight: 600 }}>
                · {fitsLabel}
              </span>
            )}
          </div>
          <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
            {fmtMb(residentTotalMb)} resident + {concurrent} × {fmtMb(perInferenceTotalMb)} per-inference
            · {enabledModels.length} enabled model{enabledModels.length === 1 ? "" : "s"}
          </div>
        </div>
        <div style={{ display: "grid", gap: 6, minWidth: 220, justifyItems: "stretch" }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", textAlign: "right" }}>
            Concurrent users
          </div>
          <input
            type="range"
            min={1}
            max={20}
            step={1}
            value={concurrent}
            onChange={(e) => setConcurrent(Number(e.target.value))}
            style={{ width: "100%", accentColor: "var(--ink)" }}
            aria-label="Concurrent users to estimate"
          />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
            <span>1</span><span>5</span><span>10</span><span>15</span><span>20</span>
          </div>
        </div>
      </div>

      <table className="admin-table">
        <thead>
          <tr>
            <th>Role</th>
            <th>Model</th>
            <th>State</th>
            <th>Device</th>
            <th style={{ textAlign: "right" }}>Resident VRAM</th>
            <th style={{ textAlign: "right" }}>+/inference</th>
            <th style={{ textAlign: "right" }}>Total for {concurrent}</th>
            <th>Last used</th>
          </tr>
        </thead>
        <tbody>
          {data.models.map((m) => {
            const resMb = m.vram_resident_mb || 0;
            const perMb = m.vram_per_inference_mb || 0;
            const total = m.enabled ? resMb + concurrent * perMb : 0;
            return (
              <tr key={m.id}>
                <td>
                  <strong>{m.label}</strong>
                  <div style={{ color: "var(--ink-3)", fontSize: 11 }}>{m.role}</div>
                  <div style={{ color: "var(--ink-3)", fontSize: 11, fontFamily: "monospace", marginTop: 2 }}>{m.name}</div>
                </td>
                <td className="mono" style={{ color: "var(--ink-3)" }}>{m.variant || "—"}</td>
                <td>
                  <span style={{
                    fontSize: 11, padding: "2px 6px", borderRadius: 4,
                    background: m.state === "loaded" ? "rgba(74, 222, 128, 0.14)" :
                                m.state === "error" ? "rgba(248, 113, 113, 0.14)" :
                                "var(--surface-3)",
                    color: m.state === "loaded" ? "var(--success)" :
                           m.state === "error" ? "var(--danger)" :
                           "var(--ink-3)",
                  }}>
                    {m.state}
                  </span>
                </td>
                <td className="mono" style={{ color: m.device ? undefined : "var(--ink-3)" }}>
                  {m.device || (m.enabled ? "—" : "disabled")}
                </td>
                <td className="mono" style={{ textAlign: "right", color: m.enabled ? "var(--ink-2)" : "var(--ink-3)" }}>
                  {fmtMb(resMb)}
                </td>
                <td className="mono" style={{ textAlign: "right", color: m.enabled ? "var(--ink-2)" : "var(--ink-3)" }}>
                  {fmtMb(perMb)}
                </td>
                <td className="mono" style={{ textAlign: "right", fontWeight: 600, color: m.enabled ? "var(--ink)" : "var(--ink-3)" }}>
                  {m.enabled ? fmtMb(total) : "—"}
                </td>
                <td style={{ fontSize: 11, color: "var(--ink-3)" }}>{fmtRelativeTime(m.last_used_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ color: "var(--ink-3)", fontSize: 11, marginTop: 12, lineHeight: 1.5 }}>
        VRAM numbers are conservative estimates from each model card +
        our profile harness, assuming fp16 where the model supports
        it. "Resident" is the cost of holding weights loaded;
        "+/inference" is peak activation memory for a single forward
        pass. "Total for N" = resident + N × per-inference — accurate
        when one ml-worker is dedicated per concurrent inference (the
        scale-out shape). A single serial worker pays the resident
        cost plus one inference regardless of queue length, so the
        N=1 row is the floor for any deployment. Tweak the per-model
        defaults in <code>backend/system_probes.py:list_configured_models</code>
        when you change a variant or quantization level.
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

// ---------- Queue (per-user ML jobs + rate limits) ----------

function RealQueueTab({ open }) {
  const qc = useQueryClient();
  const [busyDrainId, setBusyDrainId] = useStateAd(null);
  const { data, isLoading } = useQuery({
    queryKey: ["admin-queue"],
    queryFn: () => getAdminQueue(50),
    enabled: open,
    staleTime: 2_000,
    // Poll every 3 s — the queue moves fast under backfills, so a
    // looser cadence would feel laggy. The endpoint is cheap (one
    // ZRANGE + pipeline of LLENs / GETs).
    refetchInterval: open ? 3_000 : false,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;

  const drainUser = async (uid, email) => {
    if (!window.confirm(`Drop every pending job for ${email || uid}? In-flight work is unaffected.`)) return;
    setBusyDrainId(uid);
    try {
      const r = await adminDrainUserQueue(uid);
      toast.success(`Removed ${r.removed} pending job(s) for ${email || uid}.`);
      qc.invalidateQueries({ queryKey: ["admin-queue"] });
    } catch (e) {
      toast.error(e?.detail || "Drain failed.");
    } finally {
      setBusyDrainId(null);
    }
  };

  const cfg = data.config;
  const totals = data.totals;
  const limitActions = Object.keys(cfg.rate_limits);

  return (
    <div>
      {/* ---------- Top stats strip ---------- */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
        <StatPill label="Pending jobs (all users)" value={totals.pending}/>
        <StatPill label="Users with work in queue" value={totals.active_users}/>
        <StatPill label="Users at in-flight cap" value={totals.users_at_inflight_cap}
                  tone={totals.users_at_inflight_cap > 0 ? "amber" : undefined}/>
        <StatPill label="Users at queue cap" value={totals.users_at_queue_cap}
                  tone={totals.users_at_queue_cap > 0 ? "red" : undefined}/>
      </div>

      {/* ---------- Config rollup ---------- */}
      <div style={{
        padding: "10px 14px",
        background: "var(--surface-2)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        fontSize: 12,
        color: "var(--ink-2)",
        marginBottom: 18,
      }}>
        <strong style={{ color: "var(--ink)" }}>Scheduler:</strong>{" "}
        per-user FIFO &middot; round-robin across active users &middot;{" "}
        <code>{cfg.per_user_inflight_cap}</code> in-flight cap per user &middot;{" "}
        <code>{cfg.per_user_queue_limit}</code> max pending per user.{" "}
        <strong style={{ color: "var(--ink)" }}>Rate limits (per user per hour):</strong>{" "}
        {limitActions.map((a, i) => (
          <span key={a}>
            {i > 0 && " · "}
            <code>{a}</code>{" "}<strong className="mono">{cfg.rate_limits[a].limit}</strong>
          </span>
        ))}
      </div>

      {/* ---------- ML worker status ---------- */}
      {data.workers.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>
            ML workers ({data.workers.length})
          </div>
          <div className="admin-system">
            {data.workers.map((w) => <WorkerCard key={w.worker_id} w={w}/>)}
          </div>
        </div>
      )}

      {/* ---------- Per-user table ---------- */}
      {data.users.length === 0 ? (
        <div style={{ padding: 24, color: "var(--ink-3)", textAlign: "center", border: "1px dashed var(--line)", borderRadius: 8 }}>
          Queue is empty — no users currently have pending ML jobs.
        </div>
      ) : (
        <>
          <div style={{ fontSize: 11, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>
            Per-user queue ({data.users.length} active)
          </div>
          <table className="admin-table admin-table--compact">
            <thead>
              <tr>
                <th>User</th>
                <th style={{ textAlign: "right" }}>Pending</th>
                <th style={{ textAlign: "right" }}>In-flight</th>
                {limitActions.map((a) => (
                  <th key={a} style={{ textAlign: "right", fontSize: 10, whiteSpace: "nowrap" }}>
                    {a}
                  </th>
                ))}
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.user_id}>
                  <td>
                    <div style={{ fontWeight: 500, color: "var(--ink)" }}>
                      {u.email || u.display_name || u.user_id.slice(0, 8)}
                    </div>
                    <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                      {u.user_id.slice(0, 13)}…
                    </div>
                  </td>
                  <td className="mono" style={{
                    textAlign: "right",
                    color: u.pending >= cfg.per_user_queue_limit ? "var(--bad)" : "var(--ink)",
                    fontWeight: u.pending > 0 ? 600 : 400,
                  }}>{u.pending}</td>
                  <td className="mono" style={{
                    textAlign: "right",
                    color: u.inflight >= cfg.per_user_inflight_cap ? "var(--warn-ink)" : "var(--ink-2)",
                  }}>{u.inflight}</td>
                  {limitActions.map((a) => {
                    const rl = u.rate_limits[a];
                    const exhausted = rl && rl.remaining === 0;
                    return (
                      <td key={a} className="mono" style={{
                        textAlign: "right",
                        fontSize: 11,
                        color: exhausted ? "var(--bad)" : (rl && rl.used > 0 ? "var(--ink)" : "var(--ink-3)"),
                        fontWeight: exhausted ? 600 : 400,
                      }}
                          title={rl ? `${rl.used} of ${rl.limit} used this hour` : ""}>
                        {rl ? `${rl.used}/${rl.limit}` : "—"}
                      </td>
                    );
                  })}
                  <td style={{ textAlign: "right" }}>
                    {u.pending > 0 && (
                      <button
                        className="admin-proc__filter"
                        onClick={() => drainUser(u.user_id, u.email)}
                        disabled={busyDrainId === u.user_id}
                        style={{ fontSize: 11 }}
                      >
                        {busyDrainId === u.user_id ? "…" : "Drain"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function StatPill({ label, value, tone }) {
  const toneColor = {
    amber: "var(--warn-ink)",
    red:   "var(--bad)",
  }[tone] || "var(--ink)";
  return (
    <div style={{
      flex: "1 1 180px",
      minWidth: 180,
      padding: "12px 14px",
      border: "1px solid var(--line)",
      borderRadius: 10,
      background: "var(--surface)",
    }}>
      <div style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
        {label}
      </div>
      <div className="mono" style={{ fontSize: 22, fontWeight: 600, marginTop: 4, color: toneColor }}>
        {value}
      </div>
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

// ---------- Developer (stack inventory + sizing calc + perf + API) ----------
//
// Reference content surfaced inside the admin overlay so operators can
// answer "what runs the engine?", "how much hardware do I need for N
// users?", and "what's the API surface?" without leaving the app. The
// resource numbers and benchmarks mirror the ones documented on the
// public /developers marketing page — keep them in sync if you change
// one source, or move both behind a shared module if drift becomes a
// pain.

const STACK_CARDS = [
  { title: "API",          body: "FastAPI on Python 3.12, async SQLAlchemy + asyncpg, Alembic migrations, fastapi-users for JWT auth, Argon2 password hashing, TOTP 2FA via pyotp, Fernet for at-rest encryption of OAuth refresh tokens." },
  { title: "Database",     body: "PostgreSQL 16 with the pgvector extension — 512-dim face templates + 768-dim CLIP embeddings indexed for cosine similarity. FORCE Row-Level Security per user on every multi-tenant table, enforced at the DB layer." },
  { title: "Object storage", body: "MinIO (S3 API) with optional SSE-S3 / SSE-KMS. Three buckets: originals, served (compressed for delivery), and face crops. Signed URLs with TTL for client downloads; redirect proxy preserves auth in dev." },
  { title: "Vision",       body: "open-clip-torch (ViT-L-14), microsoft/Florence-2-large via transformers for image captions, insightface (RetinaFace detection + ArcFace embeddings) for the opt-in face pipeline, rawpy / LibRaw for camera RAW decoding (NEF / CR2 / ARW / DNG / RAF / ORF / RW2 / PEF)." },
  { title: "Compression",  body: "LinUCB contextual bandit picks per-image codec (WebP / MozJPEG / AVIF / JXL) and quality (55–92) from a 32-dim feature vector. Per-user telemetry feeds reward back to the arm; screenshots route to lossless WebP, animated GIFs are passthrough." },
  { title: "Workers & queue", body: "Redis 7 + per-user fair queue (round-robin across users with pending work, one in-flight per user). A dedicated ML worker container holds the CLIP + Florence + face weights so the API container never blocks on GPU inference." },
  { title: "Search",       body: "Hybrid scorer: CLIP cosine via pgvector + Postgres FTS over summary + topic + filename + signals. Query expansion via WordNet (NLTK) so \"vibrant\" matches summaries that say \"colorful\" / \"vivid\". Strict-rank stays on the literal query so exact tokens win." },
  { title: "Cloud sync",   body: "Google Drive sync via OAuth 2.0 with PKCE, read-only drive.readonly scope, Fernet-encrypted refresh tokens. Hourly background sweep mirrors the Drive folder tree under a top-level \"Google Drive\" folder. AI off by default per Drive's Limited Use policy; per-source opt-in." },
  { title: "Billing",      body: "Stripe Embedded Checkout — Pro and Business tiers, webhook-driven subscription state, hosted invoices. Empty Stripe env vars short-circuit /billing/* to 503 in dev." },
  { title: "Frontend",     body: "React 18 + TypeScript + Vite, TanStack Query for server state, Zustand for auth, Prism (~40 grammars eager-loaded) for syntax-highlighted code preview. Geist + Geist Mono type stack." },
  { title: "Infra & ops",  body: "Docker Compose for the full stack (API + ML worker + Postgres + Redis + MinIO + Caddy TLS). Optional Intel iGPU/NPU device-passthrough overlay. Healthcheck endpoint on the API, structured logs via the standard logging module." },
  { title: "Testing",      body: "pytest + pytest-asyncio covering codec dispatch, resize behavior, bandit decisions, upload validation, consent gates, RLS enforcement, and end-to-end auth flows. Migrations have idempotency + downgrade tests." },
];

// Capacity model constants. The calculator below feeds inputs through
// these to produce a single "for N users you need X TB / Y GB / Z GB
// VRAM / M workers" answer. Numbers derive from the perf benchmarks
// below + the same storage breakdown the marketing /developers page
// uses, just rolled into bottom-line totals instead of per-component
// bars.
const CAP = {
  // RAM floors (constant regardless of user count):
  ramBaseGb: 2,              // OS, init, Redis, Caddy
  ramPostgresGb: 4,          // shared_buffers + working set
  ramModelWeightsGb: 5.5,    // Florence-2 + CLIP + insightface in the ML worker
  ramFsCacheGb: 1.5,         // hot blob page cache
  ramPerConcurrentUserMb: 30, // queryset state per concurrent user
  concurrentUserFraction: 0.1, // assume 10% of users are active at once

  // VRAM per ML worker. Florence-2-large is the dominant block.
  vramPerWorkerGb: 10,       // CLIP 2 + Florence 4.6 + insightface 0.5 + activations + slack

  // Throughput per worker. Florence-2 caption is the bottleneck;
  // CLIP embedding compute is ~10x cheaper so it's never the binding
  // constraint at our worker counts.
  secondsPerJob: 10,         // avg Florence-2 caption time on a mid-tier GPU
  uploadsPerUserPerDay: 5,   // amortized — adjustable knob if needed

  // Reference latencies for the "what speed" question. These are
  // floor → ceiling for a warm, single-tenant deployment. Match
  // PERF_BENCHMARKS below.
  searchLatency: "40–80 ms",
  uploadLatency: "120–250 ms",
  clipEmbedGpu: "65 ms",
  florenceCaption: "5–15 s",
};

const PERF_BENCHMARKS = [
  { metric: "Image upload (single)",                dev: "120–250 ms",      prod: "80–150 ms",       note: "End-to-end: validate, EXIF strip, compress, MinIO put, DB row." },
  { metric: "CLIP embedding (ViT-L-14, GPU)",       dev: "65 ms",           prod: "40–90 ms",        note: "Per image. Florence-2 caption is a separate ~5–15 s pass." },
  { metric: "CLIP embedding (CPU fallback)",        dev: "1.2–2.0 s",       prod: "0.8–1.5 s",       note: "Use for solo self-host without GPU; consider batch backfill overnight." },
  { metric: "Florence-2 caption (GPU)",             dev: "5–15 s",          prod: "3–8 s",           note: "Per image; runs in a dedicated ML worker thread to keep the API loop free." },
  { metric: "Semantic search (warm CLIP encoder)",  dev: "40–80 ms",        prod: "20–50 ms",        note: "FTS pass + CLIP cosine, hybrid scored. WordNet expansion adds <1 ms." },
  { metric: "Gallery list (100 rows)",              dev: "30–60 ms",        prod: "10–30 ms",        note: "Indexed read; cross-folder + filters use the same path." },
  { metric: "Admin dashboard (cached bucket walk)", dev: "86 ms",           prod: "60–120 ms",       note: "60-second LRU + worker thread + 20k-object cap (W19 fix)." },
  { metric: "Face detection (RetinaFace, per image)", dev: "150–400 ms",    prod: "80–250 ms",       note: "Includes ArcFace embedding per detected face." },
  { metric: "API median latency (warm cache)",      dev: "< 80 ms",         prod: "< 40 ms",         note: "Excludes upload + summary endpoints — those run on background workers." },
];

const API_SECTIONS = [
  { header: "Auth", rows: [
    ["POST",  "/auth/jwt/login",          "JWT login (TOTP-gated if enabled)"],
    ["POST",  "/auth/jwt/login-totp",     "2-factor follow-up"],
    ["POST",  "/auth/register",           "account create"],
    ["GET",   "/auth/google/login",       "Google SSO start"],
    ["GET",   "/users/me",                "current user + linked identities"],
  ]},
  { header: "Account & security", rows: [
    ["POST",  "/account/totp/enroll",     "2FA setup"],
    ["POST",  "/account/totp/codes",      "regen recovery codes"],
    ["POST",  "/account/google/link",     "attach Google to existing account"],
    ["GET",   "/account/trash",           "soft-deleted rows"],
  ]},
  { header: "Images", rows: [
    ["POST",  "/images/",                 "upload"],
    ["GET",   "/images/",                 "list (scene, content_type, indoor_outdoor, has_faces, has_gps, person_id, folder_id, starred, trashed, tag, all)"],
    ["GET",   "/images/facets",           "filter chip options + counts"],
    ["GET",   "/images/summarize-progress","banner counter + self-heal drain"],
    ["GET",   "/images/{id}/original",    "original bytes"],
    ["GET",   "/images/{id}/served",      "compressed (?max_dim=N for thumbs)"],
    ["POST",  "/images/{id}/star",        "toggle favorite"],
    ["POST",  "/images/{id}/resummarize", "force re-caption (30/hr/user)"],
    ["PATCH", "/images/{id}/name",        "rename"],
    ["DELETE","/images/{id}",             "soft delete (?purge=true → hard)"],
    ["POST",  "/images/bulk-delete",      "bulk soft / hard delete"],
    ["POST",  "/images/bulk-restore",     "restore from trash"],
    ["POST",  "/images/bulk-move",        "move to folder"],
    ["POST",  "/images/backfill-summaries","queue resummarize (3/hr/user)"],
    ["POST",  "/images/backfill-vision",  "run scene/content classifier (3/hr/user)"],
    ["POST",  "/images/geo/backfill",     "extract EXIF GPS"],
  ]},
  { header: "Folders & sharing", rows: [
    ["GET",   "/folders/",                "tree"],
    ["POST",  "/folders/with-images",     "create + move N images atomically"],
    ["POST",  "/shares/",                 "grant a share"],
    ["GET",   "/shares/incoming",         "files shared with me"],
  ]},
  { header: "People (opt-in)", rows: [
    ["GET",   "/people/",                 "named + unlabeled clusters"],
    ["POST",  "/people/clusters/{cluster_id}", "name a cluster"],
    ["POST",  "/people/detect-and-label", "multi-select tag (10/hr/user × 50 imgs)"],
  ]},
  { header: "Search", rows: [
    ["GET",   "/search/?q=<text>",        "hybrid CLIP + FTS + WordNet expansion"],
  ]},
  { header: "Cloud sync (Google Drive)", rows: [
    ["POST",  "/cloud/google_drive/connect", "PKCE start"],
    ["GET",   "/cloud/callback/google_drive", "OAuth callback"],
    ["POST",  "/cloud/google_drive/sync", "manual sweep"],
    ["POST",  "/cloud/{src}/ai-opt-in",   "enable AI on synced files"],
  ]},
  { header: "Billing", rows: [
    ["POST",  "/billing/checkout",        "Stripe Embedded Checkout"],
    ["POST",  "/billing/webhook",         "subscription events"],
    ["GET",   "/billing/subscription",    "current tier + period"],
  ]},
  { header: "Admin (superuser-gated)", rows: [
    ["GET",   "/admin/system",            "health rollup + DB/Redis/MinIO probes"],
    ["GET",   "/admin/queue",             "per-user pending depth + rate-limit headroom"],
    ["POST",  "/admin/queue/drain-user",  "clear one user's pending jobs"],
    ["GET",   "/admin/hardware",          "CPU / RAM / GPU / thermals"],
    ["GET",   "/admin/processes",         "psutil + heartbeat-tracked workers"],
    ["GET",   "/admin/models",            "registered model state from heartbeats"],
    ["GET",   "/admin/users",             "paginated user list + storage usage"],
    ["PATCH", "/admin/users/{id}/quota",  "set/clear per-user quota override"],
    ["GET",   "/admin/audit",             "audit log with filters"],
  ]},
  { header: "Health", rows: [
    ["GET",   "/health",                  "liveness + DB ping"],
  ]},
];

// Storage sizing constants — must match the marketing /developers
// page so the public + internal calculators agree.
const SZ = {
  servedRatio: 0.25,         // ratio of served/compressed to original
  embeddingBytes: 3072,      // 768-dim float32 CLIP vector
  metadataKbPerFile: 5,      // Postgres row + indexes per file
  faceTemplateBytes: 2048,   // 512-dim ArcFace template + overhead
  avgFacesPerPhoto: 0.6,     // mixed libraries average <1 face/photo
  modelWeightsBytes: 5.5 * 1024 ** 3,  // Florence-2 + OpenCLIP + insightface
};

function fmtBytes(n) {
  if (n < 1024) return `${n.toFixed(0)} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n < 1024 ** 4) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  return `${(n / 1024 ** 4).toFixed(2)} TB`;
}

function computeCapacity(users, photosPerUser, avgPhotoMb) {
  // Total storage in TB — the bottom-line answer for "how big do I
  // need the disk". Aggregates originals + served + Postgres + CLIP
  // embeddings + face templates + constant ML model weights, then
  // adds 20% headroom for WAL + backups + growth.
  const totalPhotos = users * photosPerUser;
  const originalsB = totalPhotos * avgPhotoMb * 1024 ** 2;
  const servedB = originalsB * SZ.servedRatio;
  const embeddingsB = totalPhotos * SZ.embeddingBytes;
  const postgresB = totalPhotos * SZ.metadataKbPerFile * 1024;
  const facesB = totalPhotos * SZ.avgFacesPerPhoto * SZ.faceTemplateBytes;
  const weightsB = SZ.modelWeightsBytes;
  const storageRawB = originalsB + servedB + embeddingsB + postgresB + facesB + weightsB;
  const storageTb = (storageRawB * 1.20) / (1024 ** 4);

  // Concurrent active users → RAM. We assume 10% concurrent which is
  // generous for a personal-cloud workload; bump if you expect lots
  // of simultaneous syncs.
  const concurrentUsers = Math.max(1, Math.ceil(users * CAP.concurrentUserFraction));
  const ramFloorGb =
    CAP.ramBaseGb + CAP.ramPostgresGb + CAP.ramModelWeightsGb + CAP.ramFsCacheGb;
  const ramConcurrentGb = (concurrentUsers * CAP.ramPerConcurrentUserMb) / 1024;
  const ramGb = ramFloorGb + ramConcurrentGb;

  // Workers needed = daily ML jobs / single-worker daily capacity.
  // One Florence run per upload + cushion for retries.
  const uploadsPerSecond = (users * CAP.uploadsPerUserPerDay) / 86400;
  const jobsPerWorkerPerSecond = 1 / CAP.secondsPerJob;
  const workers = Math.max(1, Math.ceil(uploadsPerSecond / jobsPerWorkerPerSecond));

  // VRAM scales with worker count; each worker loads its own copy of
  // the model weights.
  const vramGb = workers * CAP.vramPerWorkerGb;

  // ML throughput at the recommended worker count.
  const throughputPhotosPerHour = workers * (3600 / CAP.secondsPerJob);

  return {
    storageTb,
    ramGb,
    vramGb,
    workers,
    concurrentUsers,
    throughputPhotosPerHour,
  };
}

function RealDeveloperTab() {
  const [users, setUsers] = useStateAd(100);
  const [photos, setPhotos] = useStateAd(5000);
  const [avgMb, setAvgMb] = useStateAd(2.0);
  const cap = computeCapacity(users, photos, avgMb);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      {/* ===== Stack inventory ===== */}
      <section>
        <h3 style={{ margin: "0 0 4px", fontSize: 14 }}>Stack inventory</h3>
        <p style={{ margin: "0 0 12px", color: "var(--ink-3)", fontSize: 12 }}>
          Every name is a real dependency in the engine today. Add a new card here when a real dependency lands.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
          {STACK_CARDS.map((c) => (
            <div key={c.title} style={{
              padding: 12,
              border: "1px solid var(--line)",
              borderRadius: 10,
              background: "var(--surface)",
            }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6, color: "var(--ink)" }}>
                {c.title}
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-2)", lineHeight: 1.55 }}>
                {c.body}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ===== Capacity estimate ===== */}
      <section>
        <h3 style={{ margin: "0 0 4px", fontSize: 14 }}>Capacity estimate</h3>
        <p style={{ margin: "0 0 12px", color: "var(--ink-3)", fontSize: 12 }}>
          Plug in a user count → get the storage, RAM, VRAM, worker count, and predicted speeds you need. Numbers include 20% headroom on storage and assume 10% concurrent users on a typical personal-cloud workload.
        </p>
        <div style={{
          display: "grid",
          gridTemplateColumns: "minmax(220px, 280px) 1fr",
          gap: 18,
          padding: 14,
          border: "1px solid var(--line)",
          borderRadius: 10,
          background: "var(--surface-2)",
        }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <NumInput label="Users" value={users} onChange={setUsers} min={1} max={1_000_000} step={10} hint="Total accounts to host." />
            <NumInput label="Photos per user (avg)" value={photos} onChange={setPhotos} min={100} max={500_000} step={100} hint="iPhone users 5–10k. Power photographers 50k+." />
            <NumInput label="Original photo size (MB, avg)" value={avgMb} onChange={setAvgMb} min={0.1} max={50} step={0.1} hint="HEIC ~1.5, 24MP JPEG ~6, 50MP RAW ~25." />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
            <BigStat label="Storage"   value={`${cap.storageTb.toFixed(2)} TB`} note="Originals + served + Postgres + embeddings + weights, +20% headroom" tone="ink"/>
            <BigStat label="RAM"       value={`${cap.ramGb.toFixed(1)} GB`}    note={`Base + ML weights + ${cap.concurrentUsers} concurrent active users`} tone="green"/>
            <BigStat label="VRAM"      value={`${cap.vramGb} GB`}               note={`${CAP.vramPerWorkerGb} GB per ML worker × ${cap.workers} worker${cap.workers > 1 ? "s" : ""}`} tone="purple"/>
            <BigStat label="ML workers" value={cap.workers}                     note={`Each handles ~${Math.round(3600 / CAP.secondsPerJob)} photos/hour`} tone="amber"/>

            <BigStat label="Search latency"   value={CAP.searchLatency}        note="Warm CLIP encoder, p50–p95" tone="blue"/>
            <BigStat label="Upload latency"   value={CAP.uploadLatency}        note="Validate + EXIF strip + compress + put + DB" tone="blue"/>
            <BigStat label="CLIP embed (GPU)" value={CAP.clipEmbedGpu}         note="Per image; CPU fallback ~1.5 s" tone="blue"/>
            <BigStat label="Florence caption" value={CAP.florenceCaption}      note={`Per image; total throughput ${cap.throughputPhotosPerHour.toLocaleString()} photos/hour`} tone="blue"/>
          </div>
        </div>
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 8 }}>
          Math is bytes-exact. Assumptions: 10% concurrent active users, {CAP.uploadsPerUserPerDay} new uploads per user per day, single ML worker handles {Math.round(3600 / CAP.secondsPerJob)} jobs/hour. Tune the inputs to match your actual mix.
        </div>
      </section>

      {/* ===== Performance benchmarks ===== */}
      <section>
        <h3 style={{ margin: "0 0 4px", fontSize: 14 }}>Performance benchmarks</h3>
        <p style={{ margin: "0 0 12px", color: "var(--ink-3)", fontSize: 12 }}>
          Dev numbers from a 16-core / 32 GB / mid-tier GPU box. Production numbers project the same code on dedicated hardware.
        </p>
        <table className="admin-table admin-table--compact">
          <thead>
            <tr>
              <th>Operation</th><th>Dev</th><th>Production</th><th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {PERF_BENCHMARKS.map((r) => (
              <tr key={r.metric}>
                <td style={{ fontWeight: 500 }}>{r.metric}</td>
                <td className="mono">{r.dev}</td>
                <td className="mono">{r.prod}</td>
                <td style={{ color: "var(--ink-2)", fontSize: 11 }}>{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* ===== API surface ===== */}
      <section>
        <h3 style={{ margin: "0 0 4px", fontSize: 14 }}>API surface</h3>
        <p style={{ margin: "0 0 12px", color: "var(--ink-3)", fontSize: 12 }}>
          What the engine exposes today. Every route is JWT-gated and scoped per-user by FORCE RLS in Postgres. OpenAPI spec at <code>/docs</code>.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {API_SECTIONS.map((sec) => (
            <div key={sec.header}>
              <div style={{
                fontSize: 11, color: "var(--ink-3)",
                textTransform: "uppercase", letterSpacing: "0.08em",
                marginBottom: 4,
              }}>
                {sec.header}
              </div>
              <table className="admin-table admin-table--compact">
                <tbody>
                  {sec.rows.map(([verb, path, note], i) => (
                    <tr key={i}>
                      <td className="mono" style={{ width: 70, color: verbColor(verb), fontWeight: 600 }}>{verb}</td>
                      <td className="mono" style={{ width: "40%" }}>{path}</td>
                      <td style={{ color: "var(--ink-2)", fontSize: 11 }}>{note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function BigStat({ label, value, note, tone }) {
  const accent = {
    ink:    "#0a0a0a",
    blue:   "#2563eb",
    green:  "#16a34a",
    purple: "#7c3aed",
    amber:  "#d97706",
  }[tone] || "#0a0a0a";
  return (
    <div style={{
      padding: "10px 12px",
      border: "1px solid var(--line)",
      borderRadius: 8,
      background: "var(--surface)",
      borderLeft: `3px solid ${accent}`,
    }}>
      <div style={{
        fontSize: 10, color: "var(--ink-3)",
        textTransform: "uppercase", letterSpacing: "0.08em",
      }}>
        {label}
      </div>
      <div className="mono" style={{
        fontSize: 20, fontWeight: 600,
        color: "var(--ink)", marginTop: 4, lineHeight: 1.15,
      }}>
        {value}
      </div>
      {note && (
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4, lineHeight: 1.45 }}>
          {note}
        </div>
      )}
    </div>
  );
}

function NumInput({ label, value, onChange, min, max, step, hint }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{
        fontSize: 11, color: "var(--ink-3)",
        textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        {label}
      </span>
      <input
        type="number" min={min} max={max} step={step} value={value}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (Number.isFinite(n)) onChange(Math.max(min, Math.min(max, n)));
        }}
        className="mono"
        style={{
          padding: "8px 10px",
          border: "1px solid var(--line)",
          borderRadius: 8,
          background: "var(--surface)",
          color: "var(--ink)",
          fontSize: 14,
        }}
      />
      {hint && <span style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{hint}</span>}
    </label>
  );
}

function verbColor(verb) {
  return {
    GET:    "#16a34a",
    POST:   "#2563eb",
    PATCH:  "#d97706",
    DELETE: "#b91c1c",
    PUT:    "#7c3aed",
  }[verb] || "var(--ink)";
}
