import { useEffect, useState } from "react";
import {
  adminNewsletterPreviewUrl,
  adminNewsletterRecipients,
  adminNewsletterSend,
  adminNewsletterTest,
  adminResendVerify,
  clearAdminAuth,
  hasAdminAuth,
  listWaitlist,
  markWaitlistNotified,
  setAdminAuth,
  type NewsletterRecipientsInfo,
  type NewsletterSendResult,
  type WaitlistEntry,
} from "../api";
import { UPDATES } from "../data/updates";

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
  const [filter, setFilter] = useState<
    "all" | "pending" | "notified" | "unverified" | "newsletter"
  >("all");
  const [search, setSearch] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [resendBusyId, setResendBusyId] = useState<number | null>(null);
  const [resendNote, setResendNote] = useState<{ id: number; text: string } | null>(null);

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
    if (filter === "unverified" && r.verified) return false;
    if (filter === "newsletter" && !r.newsletter_opt_in) return false;
    if (!q) return true;
    return r.email.toLowerCase().includes(q) ||
           (r.use_case || "").toLowerCase().includes(q);
  });

  const pendingCount = rows.filter((r) => !r.notified).length;
  const notifiedCount = rows.length - pendingCount;
  const verifiedCount = rows.filter((r) => r.verified).length;
  const newsletterCount = rows.filter((r) => r.newsletter_opt_in).length;

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
      ["id", "email", "use_case", "source", "ip",
       "verified", "verified_at",
       "newsletter_opt_in", "newsletter_consent_at",
       "notified", "notified_at",
       "created_at", "user_agent"],
      ...rows.map((r) => [
        r.id, r.email, r.use_case, r.source, r.ip || "",
        r.verified ? "yes" : "no",
        r.verified_at || "",
        r.newsletter_opt_in ? "yes" : "no",
        r.newsletter_consent_at || "",
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

  async function resendVerify(id: number) {
    setResendBusyId(id);
    setResendNote(null);
    try {
      const result = await adminResendVerify(id);
      if (result.already_verified) {
        setResendNote({ id, text: "Already verified." });
      } else if (result.sent) {
        setResendNote({ id, text: "Sent." });
      } else if (result.verify_url) {
        // Email service not configured — give the admin the link to
        // copy-paste. Same content the server would have emailed.
        try { await navigator.clipboard.writeText(result.verify_url); }
        catch { /* ignore */ }
        setResendNote({ id, text: "No mailer configured — link copied to clipboard." });
      } else {
        setResendNote({ id, text: "Send failed." });
      }
    } catch {
      setResendNote({ id, text: "Couldn't resend." });
    } finally {
      setResendBusyId(null);
    }
  }

  return (
    <section className="section">
      <div className="container">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
          <div>
            <span className="eyebrow">Admin</span>
            <h1 style={{ fontSize: 36, marginTop: 4 }}>Waitlist signups</h1>
            <p style={{ marginTop: 8, color: "var(--ink-2)", lineHeight: 1.7 }}>
              Pending <strong>{pendingCount}</strong> ·
              Notified <strong style={{ marginLeft: 4 }}>{notifiedCount}</strong> ·
              Verified <strong style={{ marginLeft: 4 }}>{verifiedCount}</strong> ·
              Newsletter <strong style={{ marginLeft: 4 }}>{newsletterCount}</strong> ·
              Total <strong style={{ marginLeft: 4 }}>{rows.length}</strong>
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost" onClick={refresh}>Refresh</button>
            <button className="btn btn--ghost" onClick={onLogout}>Sign out</button>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "24px 0 16px", flexWrap: "wrap" }}>
          {(["all", "pending", "notified", "unverified", "newsletter"] as const).map((f) => (
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

        <NewsletterPanel newsletterCount={newsletterCount} verifiedCount={verifiedCount} />


        {loading ? (
          <div style={{ padding: 24, color: "var(--ink-3)" }}>Loading…</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 24, color: "var(--ink-3)" }}>
            {rows.length === 0
              ? "No signups yet. The endpoint is live and waiting."
              : "No entries match this filter."}
          </div>
        ) : (
          <div className="admin-table-wrap" style={{ marginTop: 8 }}>
            <table className="admin-table">
              <colgroup>
                <col style={{ width: "30%" }} />
                <col style={{ width: "12%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "12%" }} />
                <col style={{ width: "12%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Use case</th>
                  <th>Signed up</th>
                  <th>Verified</th>
                  <th>Newsletter</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id}>
                    <td className="admin-table__email">
                      <div className="admin-table__email-line">{r.email}</div>
                      {r.ip && (
                        <div className="admin-table__ip">{r.ip}</div>
                      )}
                    </td>
                    <td data-label="Use case" style={{ textTransform: "capitalize" }}>{r.use_case}</td>
                    <td data-label="Signed up" className="admin-table__date">
                      {fmtDate(r.created_at)}
                    </td>
                    <td data-label="Verified">
                      {r.verified ? (
                        <span className="pill pill--good" title={r.verified_at ? `at ${fmtDate(r.verified_at)}` : ""}>
                          Verified
                        </span>
                      ) : (
                        <span className="pill pill--mid">Unverified</span>
                      )}
                    </td>
                    <td data-label="Newsletter">
                      {r.unsubscribed_at ? (
                        <span
                          className="pill"
                          title={`unsubscribed ${fmtDate(r.unsubscribed_at)}`}
                          style={{ color: "var(--bad)" }}
                        >
                          Unsubscribed
                        </span>
                      ) : r.newsletter_opt_in ? (
                        <span className="pill pill--good" title={r.newsletter_consent_at ? `consent ${fmtDate(r.newsletter_consent_at)}` : ""}>
                          Yes
                        </span>
                      ) : (
                        <span className="pill" style={{ color: "var(--ink-3)" }}>No</span>
                      )}
                    </td>
                    <td data-label="Status">
                      {r.notified ? (
                        <span className="pill pill--good">
                          Notified
                        </span>
                      ) : (
                        <span className="pill pill--mid">Pending</span>
                      )}
                    </td>
                    <td data-label="Actions" className="admin-table__actions">
                      {!r.notified && (
                        <button
                          className="btn btn--ghost"
                          onClick={() => markNotified(r.id)}
                          disabled={busyId === r.id}
                          style={{ fontSize: 12, padding: "6px 10px" }}
                        >
                          {busyId === r.id ? "…" : "Mark notified"}
                        </button>
                      )}
                      {!r.verified && (
                        <button
                          className="btn btn--ghost"
                          onClick={() => resendVerify(r.id)}
                          disabled={resendBusyId === r.id}
                          style={{ fontSize: 12, padding: "6px 10px" }}
                        >
                          {resendBusyId === r.id ? "…" : "Resend verify"}
                        </button>
                      )}
                      {resendNote?.id === r.id && (
                        <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 4 }}>
                          {resendNote.text}
                        </div>
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

// --------------------------------------------------------------------- //

function NewsletterPanel({
  newsletterCount,
  verifiedCount,
}: {
  newsletterCount: number;
  verifiedCount: number;
}) {
  // Default to the most-recent update (UPDATES is newest-first).
  const [slug, setSlug] = useState<string>(UPDATES[0]?.slug || "");
  const [recipients, setRecipients] = useState<NewsletterRecipientsInfo | null>(null);
  const [recipientsBusy, setRecipientsBusy] = useState<boolean>(false);
  const [sendBusy, setSendBusy] = useState<boolean>(false);
  const [sendResult, setSendResult] = useState<NewsletterSendResult | null>(null);
  const [error, setError] = useState<string>("");
  const [confirming, setConfirming] = useState<boolean>(false);
  const [testEmail, setTestEmail] = useState<string>("");
  const [testBusy, setTestBusy] = useState<boolean>(false);
  const [testNote, setTestNote] = useState<string>("");

  // Whenever the operator picks a new slug, look up how many people would
  // get it and how many have already received it. Cheap query — no rate
  // limiting needed.
  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    setRecipientsBusy(true);
    setError("");
    setSendResult(null);
    setConfirming(false);
    adminNewsletterRecipients(slug)
      .then((data) => { if (!cancelled) setRecipients(data); })
      .catch(() => { if (!cancelled) setError("Couldn't load recipient counts."); })
      .finally(() => { if (!cancelled) setRecipientsBusy(false); });
    return () => { cancelled = true; };
  }, [slug]);

  async function doSend() {
    setSendBusy(true);
    setError("");
    try {
      const result = await adminNewsletterSend(slug);
      setSendResult(result);
      // Refresh counts so already_sent reflects the new state.
      try { setRecipients(await adminNewsletterRecipients(slug)); }
      catch { /* non-fatal */ }
    } catch {
      setError("Send failed. Check the server logs.");
    } finally {
      setSendBusy(false);
      setConfirming(false);
    }
  }

  async function doTest() {
    setTestBusy(true);
    setTestNote("");
    try {
      const r = await adminNewsletterTest(slug, testEmail.trim());
      if (r.ok) {
        setTestNote(
          r.resend_configured
            ? `Test sent to ${r.to}.`
            : `Logged to server console (RESEND_API_KEY not set) — no real email sent.`,
        );
      } else {
        setTestNote(`Couldn't send test: ${r.reason || "unknown error"}.`);
      }
    } catch (e) {
      setTestNote(e instanceof Error ? e.message : "Test send failed.");
    } finally {
      setTestBusy(false);
    }
  }

  return (
    <div
      style={{
        margin: "8px 0 24px",
        padding: 18,
        border: "1px solid var(--line)",
        borderRadius: 14,
        background: "var(--surface-2)",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 12, flexWrap: "wrap", marginBottom: 12,
      }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ink)" }}>
            Newsletter broadcast
          </div>
          <div style={{ fontSize: 14, color: "var(--ink)", marginTop: 6, lineHeight: 1.55 }}>
            <strong>{newsletterCount}</strong> opted in &middot;{" "}
            <strong>{verifiedCount}</strong> verified &middot;{" "}
            <span style={{ color: "var(--ink-2)" }}>
              sends go to verified + opted-in + not-unsubscribed addresses
            </span>
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <select
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          // Explicit color — the marketing site inherits a white color from
          // somewhere upstream for some form elements, so dropdown text was
          // rendering invisible. Force the ink + white-bg combo here.
          style={{
            padding: "9px 12px",
            border: "1px solid var(--line)",
            borderRadius: 8,
            fontSize: 14,
            background: "#ffffff",
            color: "#0a0a0a",
            minWidth: 280,
            maxWidth: "100%",
            fontFamily: "inherit",
            appearance: "auto",
          }}
        >
          {UPDATES.map((u) => (
            <option
              key={u.slug}
              value={u.slug}
              style={{ color: "#0a0a0a", background: "#ffffff" }}
            >
              {u.week} — {u.title.length > 60 ? u.title.slice(0, 57) + "..." : u.title}
            </option>
          ))}
        </select>
        <a
          href={adminNewsletterPreviewUrl(slug)}
          target="_blank" rel="noreferrer"
          className="btn btn--ghost"
          style={{ fontSize: 13, padding: "8px 14px" }}
        >
          Preview
        </a>
        {!confirming ? (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => setConfirming(true)}
            disabled={sendBusy || !recipients || recipients.eligible === 0}
            style={{ fontSize: 13, padding: "9px 16px" }}
          >
            Send newsletter
          </button>
        ) : (
          <>
            <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
              Send to <strong>{recipients?.eligible || 0}</strong> recipients?
            </span>
            <button
              type="button"
              className="btn btn--primary"
              onClick={doSend}
              disabled={sendBusy}
              style={{ fontSize: 13, padding: "9px 16px" }}
            >
              {sendBusy ? "Sending…" : "Confirm send"}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => setConfirming(false)}
              disabled={sendBusy}
              style={{ fontSize: 13, padding: "9px 14px" }}
            >
              Cancel
            </button>
          </>
        )}
      </div>

      {recipientsBusy && (
        <div style={{ marginTop: 10, fontSize: 13, color: "var(--ink-3)" }}>Loading…</div>
      )}
      {!recipientsBusy && recipients && (
        <div style={{ marginTop: 10, fontSize: 13, color: "var(--ink-2)" }}>
          <strong>{recipients.eligible}</strong> eligible to send now
          {recipients.already_sent > 0 && (
            <> &middot; {recipients.already_sent} already sent this update (skipped)</>
          )}
          {recipients.previous_failures > 0 && (
            <> &middot; {recipients.previous_failures} previously failed</>
          )}
          {recipients.eligible === 0 && (
            <div style={{ marginTop: 4, color: "var(--ink-3)" }}>
              Nothing to send for this update — everyone eligible has already
              received it, or no verified addresses have opted in yet. Use a
              test send below to preview the email.
            </div>
          )}
        </div>
      )}

      {/* Mailer-config warning surfaced BEFORE sending so "nothing happened"
          is never a surprise. RESEND_API_KEY is set per-deploy on Render. */}
      {!recipientsBusy && recipients && !recipients.resend_configured && (
        <div
          className="callout"
          style={{
            marginTop: 12, marginBottom: 0,
            background: "var(--warn-bg)", borderColor: "var(--warn-line)",
            color: "var(--warn-ink)",
          }}
        >
          <strong>Email sending isn’t configured.</strong> RESEND_API_KEY is
          not set on this deployment, so sends are written to the server log
          instead of actually emailing anyone. Set it in the Render
          environment to send real mail.
        </div>
      )}

      {/* Test send — bypasses eligibility so the operator can always verify
          the pipeline + see the real email, regardless of list state. */}
      <div style={{ marginTop: 14, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          type="email"
          value={testEmail}
          onChange={(e) => setTestEmail(e.target.value)}
          placeholder="you@example.com"
          style={{
            padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 8,
            fontSize: 14, background: "#ffffff", color: "#0a0a0a", minWidth: 220,
            fontFamily: "inherit",
          }}
        />
        <button
          type="button"
          className="btn btn--ghost"
          onClick={doTest}
          disabled={testBusy || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(testEmail.trim())}
          style={{ fontSize: 13, padding: "9px 16px" }}
        >
          {testBusy ? "Sending…" : "Send test to this address"}
        </button>
        {testNote && (
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{testNote}</span>
        )}
      </div>

      {error && (
        <div style={{ marginTop: 10, fontSize: 13, color: "var(--bad)" }}>{error}</div>
      )}

      {sendResult && (
        <div
          className="callout"
          style={{
            marginTop: 14, marginBottom: 0,
            background: sendResult.failed > 0 ? "var(--warn-bg)" : "#ecfdf5",
            borderColor: sendResult.failed > 0 ? "var(--warn-line)" : "#a7f3d0",
            color: sendResult.failed > 0 ? "var(--warn-ink)" : "var(--good)",
          }}
        >
          <strong>Send finished:</strong>{" "}
          {sendResult.sent} of {sendResult.total} delivered
          {sendResult.failed > 0 && <>, {sendResult.failed} failed</>}.
          {!sendResult.resend_configured && (
            <div style={{ marginTop: 6, fontSize: 12 }}>
              <strong>Note:</strong> RESEND_API_KEY isn't set on this
              deployment — sends were logged to the server console
              instead of going out. Set the env var on Render to send
              real email.
            </div>
          )}
          {sendResult.failures.length > 0 && (
            <ul style={{ margin: "8px 0 0", paddingLeft: 20, fontSize: 12 }}>
              {sendResult.failures.slice(0, 5).map((f, i) => (
                <li key={i}>{f.email}{f.reason ? ` — ${f.reason}` : ""}</li>
              ))}
              {sendResult.failures.length > 5 && (
                <li>…and {sendResult.failures.length - 5} more</li>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
