// Dev / Admin overlay — for the engineer who deployed the box.
// Tabs: Storage · Users · Audit · Models · Tasks · Logs · System · Processes · Hardware.
//
// All nine tabs now read real backend state. Storage / Users / Audit
// hit the same endpoints they always did; the other six were
// previously mock JSX with hardcoded constants — todo §1.3 — and now
// resolve through /admin/{models,tasks,logs,system,processes,hardware}
// in backend/api/admin.py (powered by backend/system_probes.py).
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
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m ${seconds % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
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

export function AdminOverlay({ open, onClose }) {
  const [tab, setTab] = useStateAd("storage");

  // Probe the live system snapshot once on open to drive the header
  // line (host uptime). The same query feeds the System tab so the
  // round-trip is shared.
  const { data: systemSnap } = useQuery({
    queryKey: ["admin-system"],
    queryFn: getAdminSystem,
    enabled: open,
    staleTime: 8_000,
    refetchInterval: open ? 8_000 : false,
  });

  const headerUptime = systemSnap?.uptime?.host_uptime_seconds;
  const headerDisks = useQuery({
    queryKey: ["admin-hardware-header"],
    queryFn: getAdminHardware,
    enabled: open,
    staleTime: 20_000,
  }).data?.disks?.[0];

  return (
    <ModalAd open={open} onClose={onClose} size="xl" labelledBy="ad-title">
      <div className="modal__head admin__head">
        <div>
          <h2 id="ad-title">
            <span className="admin__chip">DEV</span>
            Admin console
          </h2>
          <p>
            {systemSnap ? (
              <>
                Host: <span className="mono">{systemSnap.uptime.platform}</span> ·
                uptime <span className="mono">{fmtDuration(headerUptime)}</span>
                {headerDisks && (
                  <> · disk <span className="mono">{headerDisks.percent}% / {admBytes(headerDisks.total_bytes)}</span></>
                )}
              </>
            ) : (
              <span style={{ color: "var(--ink-3)" }}>Loading host…</span>
            )}
          </p>
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
        {tab === "storage"   && <RealStorageTab open={open}/>}
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

// ---------- Storage (unchanged from before §1.3) ----------

function RealStorageTab({ open }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-storage"],
    queryFn: () => getAdminStorage(50),
    enabled: open,
    staleTime: 15_000,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (error) return <div style={{ color: "var(--danger)", padding: 20 }}>Error: {String(error.message || error)}</div>;
  if (!data) return null;
  return (
    <div>
      <div className="admin-system">
        <div className="admin-card">
          <div className="admin-card__label">Total stored</div>
          <div className="admin-card__num">{admBytes(data.total_bytes)}</div>
          <div className="admin-card__sub">across {data.total_images.toLocaleString()} images</div>
        </div>
        {Object.entries(data.by_category || {}).map(([k, v]) => (
          <div className="admin-card" key={k}>
            <div className="admin-card__label">{k}</div>
            <div className="admin-card__num">{admBytes(v)}</div>
          </div>
        ))}
      </div>

      <div className="admin-callout">
        <div className="admin-callout__title">Top users by storage</div>
      </div>
      <table className="admin-table admin-table--compact">
        <thead>
          <tr>
            <th>Email</th><th>Display name</th>
            <th style={{ textAlign: "right" }}>Used</th>
            <th style={{ textAlign: "right" }}>Quota</th>
            <th style={{ textAlign: "right" }}>Images</th>
          </tr>
        </thead>
        <tbody>
          {data.top_users.map((u) => (
            <tr key={u.user_id}>
              <td className="mono">{u.email}</td>
              <td>{u.display_name || <span style={{ color: "var(--ink-3)" }}>—</span>}</td>
              <td className="mono" style={{ textAlign: "right" }}>{admBytes(u.used_bytes)}</td>
              <td className="mono" style={{ textAlign: "right" }}>{admBytes(u.quota_bytes)}</td>
              <td className="mono" style={{ textAlign: "right" }}>{u.image_count.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------- Users (unchanged) ----------

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
              <th>Email</th>
              <th>Role</th>
              <th>Verified</th>
              <th style={{ textAlign: "right" }}>Used</th>
              <th style={{ textAlign: "right" }}>Quota (click to edit)</th>
              <th style={{ textAlign: "right" }}>Images</th>
            </tr>
          </thead>
          <tbody>
            {(data || []).map((u) => (
              <tr key={u.id}>
                <td className="mono">{u.email}</td>
                <td><RoleCell user={u}/></td>
                <td>{u.is_verified ? <Icon name="check" size={12}/> : <span style={{ color: "var(--ink-3)" }}>—</span>}</td>
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

// ---------- Audit (unchanged) ----------

function RealAuditTab({ open }) {
  const [actionPrefix, setActionPrefix] = useStateAd("");
  const { data, isLoading } = useQuery({
    queryKey: ["admin-audit", actionPrefix],
    queryFn: () => listAdminAudit({ limit: 200, actionPrefix: actionPrefix || null }),
    enabled: open,
    staleTime: 10_000,
  });
  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
        <input
          className="input"
          placeholder="Filter by action prefix (e.g. consent.)"
          value={actionPrefix}
          onChange={(e) => setActionPrefix(e.target.value)}
          style={{ maxWidth: 320 }}
        />
        <span style={{ color: "var(--ink-3)", fontSize: 12 }}>
          {data ? `${data.length} entries` : ""}
        </span>
      </div>
      {isLoading ? (
        <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>
      ) : (
        <pre className="admin-logs">
          {(data || []).map((e) => (
            <div key={e.id}>
              [{new Date(e.created_at).toISOString().slice(0, 19).replace("T", " ")}] {e.action}
              {e.user_id ? ` user=${e.user_id.slice(0, 8)}` : ""}
              {e.details ? ` ${JSON.stringify(e.details)}` : ""}
            </div>
          ))}
        </pre>
      )}
    </div>
  );
}

// ---------- §1.3 — un-mocked tabs ----------

function RealModelsTab({ open }) {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-models"],
    queryFn: getAdminModels,
    enabled: open,
    staleTime: 60_000,
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
            <th>Role</th><th>Model</th><th>Variant</th><th>Enabled</th>
          </tr>
        </thead>
        <tbody>
          {data.models.map((m) => (
            <tr key={m.id}>
              <td>
                <strong>{m.label}</strong>
                <div style={{ color: "var(--ink-3)", fontSize: 11 }}>{m.role}</div>
              </td>
              <td className="mono">{m.name}</td>
              <td className="mono" style={{ color: "var(--ink-3)" }}>{m.variant || "—"}</td>
              <td>
                {m.enabled
                  ? <span className="admin-acc">on</span>
                  : <span style={{ color: "var(--ink-3)" }}>off</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ color: "var(--ink-3)", fontSize: 11, marginTop: 12 }}>
        Per-model memory and run history will appear here once
        C8.2 lands the model_runs table.
      </div>
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
  const queue = data.queue;
  return (
    <div>
      <div className="admin-system" style={{ marginBottom: 14 }}>
        <div className="admin-card">
          <div className="admin-card__label">Queue depth</div>
          <div className="admin-card__num">{queue.reachable ? queue.depth : "—"}</div>
          <div className="admin-card__sub">{queue.queue_key || "redis unreachable"}</div>
        </div>
        <div className="admin-card">
          <div className="admin-card__label">In-flight</div>
          <div className="admin-card__num">{queue.reachable ? queue.active : "—"}</div>
          <div className="admin-card__sub">dedupe set size</div>
        </div>
        <div className="admin-card">
          <div className="admin-card__label">Redis</div>
          <div className="admin-card__num" style={{ color: queue.reachable ? "var(--success, #2c7a4b)" : "var(--danger, #c0392b)" }}>
            {queue.reachable ? "OK" : "DOWN"}
          </div>
          <div className="admin-card__sub">job queue backend</div>
        </div>
      </div>
      <div className="admin-callout">
        <div className="admin-callout__title">Recent task activity</div>
        <p>
          Most-recent 50 image / share / recovery-code events from the
          audit log. True per-job progress lands with the C8.2
          background_jobs table.
        </p>
      </div>
      <pre className="admin-logs">
        {data.recent.length === 0 ? (
          <div style={{ color: "var(--ink-3)" }}>No recent activity.</div>
        ) : data.recent.map((e) => (
          <div key={e.id}>
            [{new Date(e.created_at).toISOString().slice(0, 19).replace("T", " ")}] {e.action}
            {e.user_id ? ` user=${e.user_id.slice(0, 8)}` : ""}
            {e.details ? ` ${JSON.stringify(e.details).slice(0, 220)}` : ""}
          </div>
        ))}
      </pre>
    </div>
  );
}

function RealLogsTab({ open }) {
  const { data, isLoading } = useQuery({
    queryKey: ["admin-logs"],
    queryFn: () => getAdminLogs(200),
    enabled: open,
    staleTime: 5_000,
    refetchInterval: open ? 5_000 : false,
  });
  if (isLoading) return <div style={{ color: "var(--ink-3)", padding: 20 }}>Loading…</div>;
  if (!data) return null;
  return (
    <pre className="admin-logs">
      {data.lines.length === 0 ? (
        <div style={{ color: "var(--ink-3)" }}>No log lines yet — every audit event will appear here.</div>
      ) : data.lines.map((l) => (
        <div key={l.id}>
          [{new Date(l.created_at).toISOString().slice(0, 19).replace("T", " ")}] {l.action}
          {l.user_id ? ` user=${l.user_id.slice(0, 8)}` : ""}
          {l.details ? ` ${JSON.stringify(l.details).slice(0, 220)}` : ""}
        </div>
      ))}
      <div className="admin-logs__cursor">▍</div>
    </pre>
  );
}

function RealSystemTab({ open, snap }) {
  // System cards: uptime, env, DB pool, Redis, MinIO. The host CPU /
  // memory live on /admin/hardware; we render them here too because
  // operators expect the System tab to show "is the box happy."
  const { data: hw } = useQuery({
    queryKey: ["admin-hardware-system"],
    queryFn: getAdminHardware,
    enabled: open,
    staleTime: 5_000,
    refetchInterval: open ? 5_000 : false,
  });

  // Keep a small rolling history per metric so the sparkline shows
  // motion. Sourced from real samples — no jitter.
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
          <span>GPU</span>
          <strong className="mono" style={{ color: data.gpu.available ? "var(--success, #2c7a4b)" : "var(--ink-3)" }}>
            {data.gpu.available
              ? `${data.gpu.devices.length} device${data.gpu.devices.length === 1 ? "" : "s"} · ${data.gpu.backend}`
              : "no GPU detected"}
          </strong>
        </div>
        {data.gpu.devices.map((g) => (
          <div className="admin-hw__row" key={g.index} style={{ paddingLeft: 16 }}>
            <span>↳ {g.name}</span>
            <strong className="mono">
              {g.total_memory_bytes ? admBytes(g.total_memory_bytes) : "—"}
              {g.utilization_percent != null ? ` · ${g.utilization_percent}% util` : ""}
              {g.allocated_memory_bytes != null ? ` · ${admBytes(g.allocated_memory_bytes)} in use` : ""}
            </strong>
          </div>
        ))}
      </div>

      <div className="admin-hw__group">
        <div className="admin-hw__group-title">Storage</div>
        {data.disks.length === 0 ? (
          <div style={{ color: "var(--ink-3)" }}>No disks reported by psutil.</div>
        ) : data.disks.map((d) => (
          <div className="admin-hw__row" key={d.mountpoint}>
            <span>{d.mountpoint}</span>
            <strong className="mono">
              {d.device} ({d.fstype}) · {admBytes(d.used_bytes)} / {admBytes(d.total_bytes)} · {d.percent.toFixed(0)}% used
            </strong>
          </div>
        ))}
      </div>

      <div style={{ color: "var(--ink-3)", fontSize: 11, marginTop: 12 }}>
        SMART, fan, thermal, NIC and PSU probes need vendor-specific
        tools (per §F1 hardware-vendor dispatch). They will surface here
        once that pass lands.
      </div>
    </div>
  );
}
