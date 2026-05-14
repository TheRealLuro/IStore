// Dev / Admin overlay — for the engineer who deployed the box.
// Tabs: Models · Tasks · Logs · System · Processes · Hardware
//
// All numbers in this overlay are still mock — the real /admin/* endpoints
// (storage / users / audit) live in src/api/admin.ts. todo.md C8 has the
// plan to swap each tab onto the real endpoints.
import React, {
  useState as useStateAd,
  useEffect as useEffectAd,
  useMemo as useMemoAd,
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
} from "@/api/admin";

function admBytes(n) {
  if (!n && n !== 0) return "—";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 ** 3) return (n / 1024 / 1024).toFixed(1) + " MB";
  if (n < 1024 ** 4) return (n / 1024 ** 3).toFixed(2) + " GB";
  return (n / 1024 ** 4).toFixed(2) + " TB";
}

const MODELS = [
  { id: "person",  label: "Person re-ID",        size: "342 MB", trained: "12 hours ago", samples: 1247, accuracy: 96.4, threshold: 0.82 },
  { id: "scene",   label: "Scene classifier",    size:  "98 MB", trained: "3 days ago",   samples: 80211, accuracy: 92.1, threshold: 0.70 },
  { id: "ocr",     label: "OCR (handwriting)",   size: "224 MB", trained: "1 week ago",   samples:  4200, accuracy: 89.7, threshold: 0.65 },
  { id: "summary", label: "Vision-language",     size: "1.2 GB", trained: "shipped",      samples: null,  accuracy: 94.0, threshold: null },
  { id: "embed",   label: "Semantic embeddings", size: "768 MB", trained: "shipped",      samples: null,  accuracy: 91.5, threshold: null },
];

const TASKS_BASE = [
  { id: 1, label: "Indexing 247 new files",         pct: 64, eta: "~3 min",  rate: 0.7 },
  { id: 2, label: "Re-encoding face embeddings",    pct: 32, eta: "~12 min", rate: 0.4 },
  { id: 3, label: "Backup → external SSD",          pct: 88, eta: "~40 sec", rate: 1.6 },
];

const LOG_LINES = [
  "[14:22:09] indexer: ingested IMG_3471.heic (2.4 MB) → embeddings updated",
  "[14:22:11] face: 2 detections, 1 matched (sister, conf=0.94)",
  "[14:22:14] indexer: ingested savefile.pkl → tagged as game-save",
  "[14:22:18] semantic: rebuilt FAISS index (8,402 vectors)",
  "[14:22:21] api: GET /search?q=birthday party → 12 results in 47 ms",
  "[14:22:24] watchdog: nightly backup scheduled for 03:00 UTC",
  "[14:22:30] face: training pass 12/30, loss=0.087",
  "[14:22:33] thumbs: warmed cache for 89 items (8.1 MB)",
  "[14:22:36] api: GET /map/clusters → 23 clusters in 18 ms",
  "[14:22:42] indexer: queue empty · idle",
];

// initial process snapshot — values jitter live
const PROCS_BASE = [
  { id: "neuthek-api",     name: "neuthek-api",        kind: "system", pid: 1142, cpu: 4.2,  mem: 412 },
  { id: "indexer",        name: "indexer-worker",    kind: "ai",     pid: 1156, cpu: 38.1, mem: 1820 },
  { id: "face",           name: "face-engine",       kind: "ai",     pid: 1199, cpu: 12.6, mem:  962 },
  { id: "vlm",            name: "vlm-server",        kind: "ai",     pid: 1208, cpu: 22.4, mem: 4180 },
  { id: "thumbs",         name: "thumbnailer",       kind: "system", pid: 1221, cpu: 6.8,  mem:  248 },
  { id: "backup",         name: "backup-agent",      kind: "system", pid: 1234, cpu: 1.4,  mem:   84 },
  { id: "postgres",       name: "postgres",          kind: "data",   pid: 1080, cpu: 2.1,  mem:  640 },
  { id: "redis",          name: "redis",             kind: "data",   pid: 1082, cpu: 0.4,  mem:  128 },
  { id: "watchdog",       name: "watchdog",          kind: "system", pid: 1003, cpu: 0.2,  mem:   42 },
  { id: "web",            name: "web-frontend",      kind: "system", pid: 1340, cpu: 0.9,  mem:  192 },
];

function jitter(base, amount) {
  return Math.max(0, base + (Math.random() - 0.5) * amount * 2);
}

function Sparkline({ values, color = "var(--ink)", height = 28 }) {
  if (!values.length) return null;
  const w = 120, h = height;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
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
  const [tick, setTick] = useStateAd(0);
  const [procs, setProcs] = useStateAd(PROCS_BASE);
  const [history, setHistory] = useStateAd({ cpu: [], gpu: [], mem: [], net: [] });
  const [filter, setFilter] = useStateAd("all");
  const [retraining, setRetraining] = useStateAd(null);

  // simulate live metrics
  useEffectAd(() => {
    if (!open) return;
    const t = setInterval(() => {
      setTick(x => x + 1);
      setProcs(prev => prev.map(p => ({
        ...p,
        cpu: +Math.max(0.1, jitter(p.cpu, p.cpu * 0.18)).toFixed(1),
        mem: Math.round(jitter(p.mem, p.mem * 0.04)),
      })));
      setHistory(h => {
        const next = (key, base, amp) => [...(h[key] || []), Math.round(jitter(base, amp))].slice(-32);
        return {
          cpu: next("cpu", 25, 14),
          gpu: next("gpu", 60, 18),
          mem: next("mem", 12, 1.6),
          net: next("net", 14, 6),
        };
      });
    }, 800);
    return () => clearInterval(t);
  }, [open]);

  const liveTasks = useMemoAd(() => TASKS_BASE.map(t => ({
    ...t,
    pct: Math.min(99, t.pct + (tick % 5) * t.rate),
  })), [tick]);

  const totalCPU = procs.reduce((s, p) => s + p.cpu, 0);
  const totalMem = procs.reduce((s, p) => s + p.mem, 0);
  const filteredProcs = filter === "all" ? procs : procs.filter(p => p.kind === filter);
  const sortedProcs = [...filteredProcs].sort((a, b) => b.cpu - a.cpu);

  return (
    <ModalAd open={open} onClose={onClose} size="xl" labelledBy="ad-title">
      <div className="modal__head admin__head">
        <div>
          <h2 id="ad-title">
            <span className="admin__chip">DEV</span>
            Admin console
          </h2>
          <p>
            Box: <span className="mono">neuthek-box-7f2a · 192.168.1.41</span> ·
            uptime <span className="mono">17 d 04:22</span> ·
            disk <span className="mono">38% / 4 TB</span>
          </p>
        </div>
        <ModalCloseAd onClose={onClose}/>
      </div>

      <div className="admin__tabs">
        {[
          { id: "storage",   label: "Storage",   real: true },
          { id: "users",     label: "Users",     real: true },
          { id: "audit",     label: "Audit",     real: true },
          { id: "models",    label: "Models" },
          { id: "tasks",     label: "Tasks" },
          { id: "logs",      label: "Logs" },
          { id: "system",    label: "System" },
          { id: "processes", label: "Processes" },
          { id: "hardware",  label: "Hardware" },
        ].map(t => (
          <button key={t.id} className="admin__tab" data-active={tab === t.id} onClick={() => setTab(t.id)}>
            {t.label}
            {!t.real && <span style={{ marginLeft: 6, fontSize: 9, opacity: 0.6 }}>MOCK</span>}
          </button>
        ))}
      </div>

      <div className="modal__body admin__body">
        {tab === "storage"   && <RealStorageTab open={open}/>}
        {tab === "users"     && <RealUsersTab open={open}/>}
        {tab === "audit"     && <RealAuditTab open={open}/>}

        {/* MODELS */}
        {tab === "models" && (
          <div>
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Model</th><th>Size</th><th>Last trained</th><th>Samples</th><th>Threshold</th><th>Acc.</th><th></th>
                </tr>
              </thead>
              <tbody>
                {MODELS.map(m => (
                  <tr key={m.id}>
                    <td>
                      <strong>{m.label}</strong>{" "}
                      <span className="mono" style={{ color: "var(--ink-3)", fontSize: 11 }}>{m.id}.pkl</span>
                    </td>
                    <td className="mono">{m.size}</td>
                    <td>{m.trained}</td>
                    <td className="mono">{m.samples == null ? "—" : m.samples.toLocaleString()}</td>
                    <td className="mono">{m.threshold == null ? "—" : m.threshold.toFixed(2)}</td>
                    <td><span className="admin-acc">{m.accuracy}%</span></td>
                    <td>
                      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                        <button className="btn btn--ghost btn--sm" disabled={m.samples == null || retraining === m.id}
                                onClick={() => { setRetraining(m.id); setTimeout(() => setRetraining(null), 2200); }}>
                          <Icon name={retraining === m.id ? "refresh" : "play"} size={11}/>
                          {retraining === m.id ? "Training…" : "Retrain"}
                        </button>
                        <button className="btn btn--ghost btn--sm">
                          <Icon name="download" size={11}/> Export .pkl
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="admin-callout">
              <div className="admin-callout__title">Person re-ID is improving fastest</div>
              <p>+2.1% accuracy over the last 7 days from 38 user confirmations. Safe to enable auto-tagging at the lower threshold (0.78).</p>
            </div>
          </div>
        )}

        {/* TASKS */}
        {tab === "tasks" && (
          <div className="admin-tasks">
            {liveTasks.map(t => (
              <div key={t.id} className="admin-task">
                <div className="admin-task__head">
                  <div>{t.label}</div>
                  <div className="mono" style={{ color: "var(--ink-3)" }}>{Math.round(t.pct)}% · {t.eta}</div>
                </div>
                <div className="admin-progress"><div className="admin-progress__fill" style={{ width: t.pct + "%" }}/></div>
              </div>
            ))}
            <button className="btn btn--secondary" style={{ marginTop: 14 }}>
              <Icon name="plus" size={12}/> Queue full reindex
            </button>
          </div>
        )}

        {/* LOGS */}
        {tab === "logs" && (
          <pre className="admin-logs">
            {LOG_LINES.map((l, i) => <div key={i}>{l}</div>)}
            <div className="admin-logs__cursor">▍</div>
          </pre>
        )}

        {/* SYSTEM */}
        {tab === "system" && (
          <div className="admin-system">
            <div className="admin-card">
              <div className="admin-card__label">CPU</div>
              <div className="admin-card__num">{(history.cpu[history.cpu.length - 1] ?? 25)}<span>%</span></div>
              <div className="admin-card__sub">8-core · idle {(100 - (history.cpu[history.cpu.length - 1] ?? 25))}%</div>
              <Sparkline values={history.cpu}/>
            </div>
            <div className="admin-card">
              <div className="admin-card__label">GPU</div>
              <div className="admin-card__num">{(history.gpu[history.gpu.length - 1] ?? 60)}<span>%</span></div>
              <div className="admin-card__sub">indexer running</div>
              <Sparkline values={history.gpu} color="var(--ink-2)"/>
            </div>
            <div className="admin-card">
              <div className="admin-card__label">Memory</div>
              <div className="admin-card__num">{((history.mem[history.mem.length - 1] ?? 12)).toFixed(1)}<span>GB</span></div>
              <div className="admin-card__sub">of 32 GB</div>
              <Sparkline values={history.mem} color="var(--ink-2)"/>
            </div>
            <div className="admin-card">
              <div className="admin-card__label">Storage</div>
              <div className="admin-card__num">1.5<span>TB</span></div>
              <div className="admin-card__sub">of 4 TB · 2.5 TB free</div>
            </div>
            <div className="admin-card">
              <div className="admin-card__label">Network</div>
              <div className="admin-card__num">↑{Math.round(history.net[history.net.length - 1] ?? 14)} ↓{Math.round((history.net[history.net.length - 1] ?? 14) / 3)}<span>MB/s</span></div>
              <div className="admin-card__sub">2 active sessions</div>
              <Sparkline values={history.net} color="var(--ink-2)"/>
            </div>
            <div className="admin-card">
              <div className="admin-card__label">Backup</div>
              <div className="admin-card__num">03:00<span>UTC</span></div>
              <div className="admin-card__sub">nightly → SSD</div>
            </div>
          </div>
        )}

        {/* PROCESSES */}
        {tab === "processes" && (
          <div>
            <div className="admin-proc__head">
              <div className="admin-proc__totals">
                <span>Total CPU <strong className="mono">{totalCPU.toFixed(1)}%</strong></span>
                <span style={{ marginLeft: 18 }}>Total RAM <strong className="mono">{(totalMem / 1024).toFixed(1)} GB</strong></span>
                <span style={{ marginLeft: 18 }}>Processes <strong className="mono">{procs.length}</strong></span>
              </div>
              <div className="admin-proc__filters">
                {["all", "ai", "system", "data"].map(f => (
                  <button key={f} className="admin-proc__filter" data-active={filter === f} onClick={() => setFilter(f)}>{f}</button>
                ))}
              </div>
            </div>
            <table className="admin-table admin-table--compact">
              <thead>
                <tr>
                  <th>Process</th><th>PID</th><th>Kind</th><th style={{ textAlign: "right" }}>CPU</th><th style={{ textAlign: "right" }}>RAM</th><th></th>
                </tr>
              </thead>
              <tbody>
                {sortedProcs.map(p => (
                  <tr key={p.id}>
                    <td><span className="mono">{p.name}</span></td>
                    <td className="mono" style={{ color: "var(--ink-3)" }}>{p.pid}</td>
                    <td><span className="admin-kind" data-kind={p.kind}>{p.kind}</span></td>
                    <td className="mono" style={{ textAlign: "right" }}>
                      <span className="admin-bar"><span className="admin-bar__fill" style={{ width: Math.min(100, p.cpu * 2) + "%" }}/></span>
                      {p.cpu.toFixed(1)}%
                    </td>
                    <td className="mono" style={{ textAlign: "right" }}>{p.mem >= 1024 ? (p.mem/1024).toFixed(1) + " GB" : p.mem + " MB"}</td>
                    <td>
                      <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                        <button className="btn btn--ghost btn--sm" title="Restart"><Icon name="refresh" size={10}/></button>
                        <button className="btn btn--ghost btn--sm" title="Stop"><Icon name="x" size={10}/></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* HARDWARE */}
        {tab === "hardware" && (
          <div className="admin-hw">
            <div className="admin-hw__group">
              <div className="admin-hw__group-title">Compute</div>
              <div className="admin-hw__row"><span>CPU</span><strong className="mono">AMD Ryzen 7 7700X · 8C / 16T · 4.5 GHz</strong></div>
              <div className="admin-hw__row"><span>GPU</span><strong className="mono">NVIDIA RTX 4060 Ti · 16 GB VRAM</strong></div>
              <div className="admin-hw__row"><span>Memory</span><strong className="mono">32 GB DDR5-5600 · 2 × 16 GB</strong></div>
              <div className="admin-hw__row"><span>NPU</span><strong className="mono">Hailo-8 · 26 TOPS · idle</strong></div>
            </div>
            <div className="admin-hw__group">
              <div className="admin-hw__group-title">Storage</div>
              <div className="admin-hw__row"><span>Primary</span><strong className="mono">Samsung 990 Pro · 4 TB NVMe · 38% used</strong></div>
              <div className="admin-hw__row"><span>Backup</span><strong className="mono">Seagate IronWolf · 8 TB · 71% used</strong></div>
              <div className="admin-hw__row"><span>SMART</span><strong className="mono" style={{ color: "var(--success, #2c7a4b)" }}>All disks healthy</strong></div>
            </div>
            <div className="admin-hw__group">
              <div className="admin-hw__group-title">Network · Power · Thermal</div>
              <div className="admin-hw__row"><span>NIC</span><strong className="mono">Intel I226-V · 2.5 GbE · link up</strong></div>
              <div className="admin-hw__row"><span>Wi-Fi</span><strong className="mono">Wi-Fi 6E · –42 dBm · 5G band</strong></div>
              <div className="admin-hw__row"><span>PSU</span><strong className="mono">450 W gold · drawing 78 W</strong></div>
              <div className="admin-hw__row"><span>UPS</span><strong className="mono">APC BX1500M · 100% · ~38 min runtime</strong></div>
              <div className="admin-hw__row"><span>CPU temp</span><strong className="mono">52 °C · fan 34%</strong></div>
              <div className="admin-hw__row"><span>GPU temp</span><strong className="mono">61 °C · fan 41%</strong></div>
            </div>
          </div>
        )}
      </div>

      <div className="modal__foot">
        <span className="modal__foot-left mono">neuthek-os v0.4.2 · build 1f3a9c · live</span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </ModalAd>
  );
}

// Real-data sub-tabs — backed by /admin/storage, /admin/users, /admin/audit.
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

// Named export above; legacy `window.AdminOverlay` removed.
