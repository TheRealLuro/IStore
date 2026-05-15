import { useEffect, useState } from "react";
import {
  clearAdminAuth,
  hasAdminAuth,
  listWaitlist,
  markWaitlistNotified,
  setAdminAuth,
  type WaitlistEntry,
} from "../api";

/* Admin viewer for marketing-site waitlist signups.
 *
 * Auth: HTTP Basic against the Express /api/admin/* endpoints. The
 * credentials live in sessionStorage so a tab reload survives but a
 * closed tab logs you out. No JWT, no refresh tokens, no cookies —
 * we don't need them for a single-operator admin tool. */

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function csvEscape(value: unknown) {
  const s = value == null ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadCsv(filename: string, rows: unknown[][]) {
  const csv = rows.map((r) => r.map(csvEscape).join(",")).join("\r\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

export default function Admin() {
  const [authed, setAuthed] = useState(hasAdminAuth());

  if (!authed) {
    return <LoginCard onLogin={() => setAuthed(true)} />;
  }
  return (
    <WaitlistTable onLogout={() => {
      clearAdminAuth();
      setAuthed(false);
    }} />
  );
}

// --------------------------------------------------------------------- //

function LoginCard({ onLogin }: { onLogin: () => void }) {
  const [user, setUser] = useState("admin");
  const [pass, setPass] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setAdminAuth(user, pass);
    try {
      await listWaitlist(1);
      onLogin();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      setError(msg === "unauthorized"
        ? "Wrong username or password."
        : "Couldn't reach the server.");
      clearAdminAuth();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page-head" style={{ minHeight: "calc(100vh - 200px)" }}>
      <div className="container" style={{ maxWidth: 420, paddingTop: 40 }}>
        <span className="eyebrow">Admin</span>
        <h1 style={{ fontSize: 36 }}>Waitlist viewer</h1>
        <p className="lead" style={{ fontSize: 16 }}>
          Sign in with the admin credentials set on the server. This is a
          tool for the neuthek team — visitors don't need to log in to
          join the waitlist.
        </p>

        <form className="form" onSubmit={submit} style={{ marginTop: 24 }}>
          <label style={{ fontSize: 13, color: "var(--ink-2)" }}>Username</label>
          <input
            type="text"
            value={user}
            onChange={(e) => setUser(e.target.value)}
            autoComplete="username"
            required
          />
          <label style={{ fontSize: 13, color: "var(--ink-2)" }}>Password</label>
          <input
            type="password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            autoComplete="current-password"
            required
          />
          <button
            type="submit"
            className="btn btn--primary btn--lg"
            disabled={busy}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
          {error && (
            <p style={{ color: "var(--bad)", fontSize: 13 }}>{error}</p>
          )}
        </form>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------- //

function WaitlistTable({ onLogout }: { onLogout: () => void }) {
  const [rows, setRows] = useState<WaitlistEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"all" | "pending" | "notified">("all");
  const [search, setSearch] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const data = await listWaitlist(500);
      setRows(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (msg === "unauthorized") {
        onLogout();
        return;
      }
      setError("Failed to load. Try refreshing.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const q = search.trim().toLowerCase();
  const filtered = rows.filter((r) => {
    if (filter === "pending" && r.notified) return false;
    if (filter === "notified" && !r.notified) return false;
    if (!q) return true;
    return r.email.toLowerCase().includes(q) ||
           (r.use_case || "").toLowerCase().includes(q);
  });

  const pendingCount = rows.filter((r) => !r.notified).length;
  const notifiedCount = rows.length - pendingCount;

  async function copyAll() {
    const emails = filtered.map((r) => r.email).join(", ");
    try {
      await navigator.clipboard.writeText(emails);
    } catch {
      // Clipboard blocked — fall back to a prompt the user can copy from.
      window.prompt("Copy emails:", emails);
    }
  }

  function exportCsv() {
    const data: unknown[][] = [
      ["id", "email", "use_case", "source", "ip", "notified", "notified_at", "created_at", "user_agent"],
      ...rows.map((r) => [
        r.id, r.email, r.use_case, r.source, r.ip || "",
        r.notified ? "yes" : "no",
        r.notified_at || "",
        r.created_at,
        r.user_agent || "",
      ]),
    ];
    downloadCsv(`neuthek-waitlist-${new Date().toISOString().slice(0, 10)}.csv`, data);
  }

  async function markNotified(id: number) {
    setBusyId(id);
    try {
      const updated = await markWaitlistNotified(id);
      setRows((prev) => prev.map((r) => (r.id === id ? updated : r)));
    } catch {
      setError("Couldn't mark as notified.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="section">
      <div className="container">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
          <div>
            <span className="eyebrow">Admin</span>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Waitlist signups</h1>
            <p style={{ marginTop: 8, color: "var(--ink-2)" }}>
              Pending <strong>{pendingCount}</strong> ·
              Notified <strong style={{ marginLeft: 4 }}>{notifiedCount}</strong> ·
              Total <strong style={{ marginLeft: 4 }}>{rows.length}</strong>
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost" onClick={refresh}>Refresh</button>
            <button className="btn btn--ghost" onClick={onLogout}>Sign out</button>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "24px 0 16px", flexWrap: "wrap" }}>
          {(["all", "pending", "notified"] as const).map((f) => (
            <button
              key={f}
              className="btn btn--ghost"
              onClick={() => setFilter(f)}
              style={{
                fontWeight: filter === f ? 600 : 400,
                borderColor: filter === f ? "var(--ink)" : "var(--line)",
                textTransform: "capitalize",
                fontSize: 13,
              }}
            >
              {f}
            </button>
          ))}
          <input
            type="search"
            placeholder="Search email or use case…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              flex: 1, minWidth: 200,
              padding: "10px 14px",
              border: "1px solid var(--line)",
              borderRadius: 8,
              fontSize: 14,
            }}
          />
          <button className="btn btn--ghost" onClick={copyAll}
            disabled={filtered.length === 0}>
            Copy emails
          </button>
          <button className="btn btn--primary" onClick={exportCsv}
            disabled={rows.length === 0}>
            Export CSV
          </button>
        </div>

        {error && (
          <div className="callout" style={{ marginTop: 0 }}>
            <strong>Error:</strong> {error}
          </div>
        )}

        {loading ? (
          <div style={{ padding: 24, color: "var(--ink-3)" }}>Loading…</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 24, color: "var(--ink-3)" }}>
            {rows.length === 0
              ? "No signups yet. The endpoint is live and waiting."
              : "No entries match this filter."}
          </div>
        ) : (
          <div className="compare-wrap" style={{ marginTop: 8 }}>
            <table className="compare">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Use case</th>
                  <th>Signed up</th>
                  <th>IP</th>
                  <th>Status</th>
                  <th style={{ width: 160 }}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id}>
                    <td style={{ fontWeight: 500, whiteSpace: "nowrap" }}>{r.email}</td>
                    <td style={{ textTransform: "capitalize" }}>{r.use_case}</td>
                    <td style={{ fontFamily: "Geist Mono, monospace", fontSize: 12, color: "var(--ink-2)", whiteSpace: "nowrap" }}>
                      {fmtDate(r.created_at)}
                    </td>
                    <td style={{ fontFamily: "Geist Mono, monospace", fontSize: 12, color: "var(--ink-3)" }}>
                      {r.ip || "—"}
                    </td>
                    <td>
                      {r.notified ? (
                        <span className="pill pill--good">
                          Notified · {fmtDate(r.notified_at)}
                        </span>
                      ) : (
                        <span className="pill pill--mid">Pending</span>
                      )}
                    </td>
                    <td>
                      {!r.notified && (
                        <button
                          className="btn btn--ghost"
                          onClick={() => markNotified(r.id)}
                          disabled={busyId === r.id}
                          style={{ fontSize: 12, padding: "6px 12px" }}
                        >
                          {busyId === r.id ? "…" : "Mark notified"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
