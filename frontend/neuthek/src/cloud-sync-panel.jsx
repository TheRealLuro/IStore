// §C2 — cloud sync settings panel.
//
// Shown inside the Account → Cloud sync tab. Drives the full
// /cloud/* surface: connect a provider (redirects out to the OAuth
// page), list links, trigger a sync, view + dismiss conflicts,
// toggle the per-source AI Limited-Use flag, and disconnect.
//
// 503 from the backend means "operator hasn't configured the OAuth
// client for this provider." We surface that as an inline
// instruction rather than a toast so the user has a path forward
// without re-opening the panel.

import React, { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { Modal, ModalClose } from "./primitives.jsx";
import {
  connectCloud,
  disconnectCloud,
  getCloudProviders,
  listCloudConflicts,
  listCloudLinks,
  setCloudAiOptIn,
  syncCloudLink,
  getSyncStatus,
  icloudStart,
  icloudVerify,
  icloudResendCode,
  protonStart,
  protonVerify,
  megaStart,
} from "@/api/cloud";

const PROVIDER_META = {
  google_drive: { label: "Google Drive",  note: "Read-only · drive.readonly scope" },
  dropbox:      { label: "Dropbox",       note: "Read-only · files.content.read scope" },
  icloud:       { label: "iCloud Drive",  note: "Read-only · Apple ID + 2FA" },
  proton_drive: { label: "Proton Drive",  note: "Read-only · Proton email + 2FA · via rclone" },
  mega:         { label: "MEGA",          note: "Read-only · email + password · via rclone" },
};

function fmtRel(iso) {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (!t) return "never";
  const ms = Date.now() - t;
  if (ms < 60_000) return "just now";
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

// Stable empty-array reference. Using a literal `[]` in
// `useQuery({...}).data || []` creates a NEW array each render —
// any useEffect that depends on `links` then sees a different
// reference every cycle and fires on every render, which became
// an infinite update-depth loop (the effect's setState triggered
// the next render, which re-defaulted to `[]`, which fired the
// effect again, …).
const EMPTY_LINKS = Object.freeze([]);

export function CloudSyncPanel() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(null); // link id currently syncing
  const [conflictsByLink, setConflictsByLink] = useState({});
  // §C4.6 — iCloud / Proton Drive / MEGA each use a different
  // (non-OAuth) connect dance, so they open modals instead of
  // redirecting. `null` = closed.
  const [icloudOpen, setIcloudOpen] = useState(false);
  const [protonOpen, setProtonOpen] = useState(false);
  const [megaOpen, setMegaOpen] = useState(false);
  const { data: links = EMPTY_LINKS, isLoading, error } = useQuery({
    queryKey: ["cloud-links"],
    queryFn: listCloudLinks,
    staleTime: 30_000,
  });

  // Pull conflicts for any link whose status is "conflicts" so the
  // banner has something to show. One request per affected link.
  //
  // Depend on a STABLE PRIMITIVE — the comma-joined list of conflict
  // link ids — so the effect only re-runs when the conflict set
  // actually changes, not on every refetch that returns the same
  // links with a new last_synced_at timestamp.
  const conflictLinkIds = links
    .filter((l) => l.status === "conflicts")
    .map((l) => l.id)
    .sort()
    .join(",");
  useEffect(() => {
    if (!conflictLinkIds) {
      setConflictsByLink({});
      return;
    }
    let cancelled = false;
    (async () => {
      const next = {};
      for (const id of conflictLinkIds.split(",")) {
        try {
          const resp = await listCloudConflicts(Number(id));
          if (!cancelled) next[id] = resp.conflicts;
        } catch {
          if (!cancelled) next[id] = [];
        }
      }
      if (!cancelled) setConflictsByLink(next);
    })();
    return () => { cancelled = true; };
  }, [conflictLinkIds]);

  const onConnect = async (provider) => {
    // §C4.6 — route based on the provider's auth shape: OAuth
    // providers redirect to a browser consent page; the
    // password-based ones (iCloud / Proton Drive / MEGA) open a
    // modal that POSTs credentials directly.
    if (provider === "icloud") {
      setIcloudOpen(true);
      return;
    }
    if (provider === "proton_drive") {
      setProtonOpen(true);
      return;
    }
    if (provider === "mega") {
      setMegaOpen(true);
      return;
    }
    try {
      const r = await connectCloud(provider);
      // Hard redirect to the provider's OAuth page; on success we
      // come back to `/?cloud_connected=...` and refetch.
      window.location.href = r.auth_url;
    } catch (e) {
      if (e?.status === 503) {
        toast.error(
          "Cloud sync isn't configured on this deployment yet — operator needs to set OAuth credentials.",
        );
      } else {
        toast.error(e?.detail || e?.message || "Could not start OAuth");
      }
    }
  };

  const onSync = async (link) => {
    setBusy(link.id);
    // Sync flow (2026-05): the /sync endpoint kicks off a background
    // task and returns immediately — for accounts with thousands of
    // files the actual walk can take minutes, and the previous
    // synchronous request used to die mid-flight with "failed to
    // fetch" once the proxy / browser timed out. Now we POST once,
    // then POLL /sync-status every 2 s until state ≠ "running". The
    // gallery/folders/storage queries refresh on the same poll so
    // the UI fills in as files land.
    let toastId;
    let pollHandle = null;
    const stopPolling = () => {
      if (pollHandle) { clearInterval(pollHandle); pollHandle = null; }
    };
    try {
      toastId = toast.loading(
        `Syncing ${PROVIDER_META[link.provider]?.label || link.provider}…`,
      );
      await syncCloudLink(link.id);  // returns immediately
      await new Promise((resolve) => {
        pollHandle = setInterval(async () => {
          // Refresh the gallery + counters every tick so the user
          // sees files appearing while the walk is still running.
          qc.invalidateQueries({ queryKey: ["files"] });
          qc.invalidateQueries({ queryKey: ["folders"] });
          qc.invalidateQueries({ queryKey: ["storage"] });
          qc.invalidateQueries({ queryKey: ["cloud-links"] });
          try {
            const s = await getSyncStatus(link.id);
            if (s.state === "done") {
              const c = s.counts || {};
              const skipped = c.skipped_unchanged ? `, ${c.skipped_unchanged} unchanged` : "";
              const conflicts = c.conflicts ? `, ${c.conflicts} conflicts` : "";
              toast.success(
                `${c.pulled || 0} pulled from ${PROVIDER_META[link.provider]?.label || link.provider}${skipped}${conflicts}`,
                { id: toastId },
              );
              stopPolling();
              resolve();
            } else if (s.state === "error") {
              toast.error(s.error || "Sync failed", { id: toastId });
              stopPolling();
              resolve();
            }
            // state === "running" or "idle" → keep polling
          } catch (e) {
            // Don't fail the whole flow on a transient status-fetch
            // error — the next tick will retry. Log to console.
            console.warn("sync-status poll failed:", e);
          }
        }, 2000);
      });
    } catch (e) {
      toast.error(e?.detail || e?.message || "Sync failed", { id: toastId });
    } finally {
      stopPolling();
      setBusy(null);
    }
  };

  const onDisconnect = async (link) => {
    if (!window.confirm(`Disconnect ${PROVIDER_META[link.provider]?.label}? Local files stay.`)) return;
    try {
      await disconnectCloud(link.id);
      toast.success("Disconnected");
      qc.invalidateQueries({ queryKey: ["cloud-links"] });
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not disconnect");
    }
  };

  // Per-link AI opt-in. Reads from the server's `link.ai_opted_in`
  // first (persistent across reloads now that migration 0030 lives),
  // overlaid with a local optimistic flip while a toggle request is
  // in flight. Without the overlay, the button would lag behind the
  // server roundtrip; without the server read, a refresh would forget
  // the user's choice (the original bug).
  const [aiOptedByLink, setAiOptedByLink] = useState({});
  const readAiOpted = (link) => {
    if (link.id in aiOptedByLink) return aiOptedByLink[link.id];
    return !!link.ai_opted_in;
  };
  const onToggleAi = async (link, opted) => {
    setAiOptedByLink((m) => ({ ...m, [link.id]: opted }));
    try {
      const r = await setCloudAiOptIn(link.id, opted);
      const label = PROVIDER_META[link.provider]?.label || link.provider;
      if (opted) {
        toast.success(
          r.affected
            ? `AI features enabled for ${r.affected} ${label} file${r.affected === 1 ? "" : "s"}`
            : `AI features enabled. They'll run on every new ${label} file as it's synced.`,
        );
      } else {
        toast.success(
          r.affected
            ? `AI features paused for ${r.affected} ${label} file${r.affected === 1 ? "" : "s"}`
            : `AI features paused. Future ${label} files won't be processed.`,
        );
      }
      qc.invalidateQueries({ queryKey: ["cloud-links"] });
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      // Rollback the optimistic flip so the active-button indicator
      // doesn't lie about the server state.
      setAiOptedByLink((m) => {
        const { [link.id]: _, ...rest } = m;
        return rest;
      });
      toast.error(e?.detail || e?.message || "Could not change AI opt-in");
    }
  };

  if (error) {
    return (
      <div style={{ padding: 18 }}>
        <div className="set-note" data-tone="error">
          <Icon name="alert" size={14}/>
          <span>Could not load cloud links. {error.message || ""}</span>
        </div>
      </div>
    );
  }
  if (isLoading) {
    return (
      <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 12 }}>
        {[0, 1].map((i) => (
          <div key={i} className="set-skel" style={{ height: 96, borderRadius: 12 }}/>
        ))}
      </div>
    );
  }

  const connected = new Set(links.map((l) => l.provider));

  return (
    <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.5 }}>
        Pull-only. Files synced from these sources never get sent back; we never
        write to your remote storage. AI features (summaries, face recognition,
        semantic search) are <strong>off by default</strong> on synced files
        per the source's Limited Use policy — flip them on per source below.
      </div>

      {/* Existing links */}
      {links.map((link) => {
        const meta = PROVIDER_META[link.provider] || { label: link.provider };
        const conflicts = conflictsByLink[link.id] || [];
        return (
          <div
            key={link.id}
            style={{
              padding: 14,
              borderRadius: 12,
              border: "1px solid var(--line)",
              background: "var(--surface)",
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Icon name="cloud" size={16}/>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{meta.label}</div>
                <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
                  Last synced {fmtRel(link.last_synced_at)} ·
                  {link.status === "active" && <> <span style={{ color: "var(--success)" }}>healthy</span></>}
                  {link.status === "conflicts" && <> <span style={{ color: "var(--warning)" }}>conflicts</span></>}
                  {link.status === "error" && <> <span style={{ color: "var(--danger)" }}>error</span></>}
                </div>
              </div>
              <button
                className="btn btn--secondary btn--sm"
                onClick={() => onSync(link)}
                disabled={busy === link.id}
              >
                <Icon name="refresh" size={12}/>{" "}
                {busy === link.id ? "Syncing…" : "Sync now"}
              </button>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => onDisconnect(link)}
              >
                Disconnect
              </button>
            </div>

            {/* §C2 conflict banner */}
            {conflicts.length > 0 && (
              <div
                role="alert"
                style={{
                  padding: 10,
                  background: "var(--danger-soft, rgba(255,180,40,0.10))",
                  border: "1px solid var(--warning)",
                  borderRadius: 8,
                  fontSize: 12,
                  color: "var(--ink)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                  <Icon name="alert" size={12}/>
                  <strong>{conflicts.length} files weren't synced</strong>
                </div>
                <div style={{ color: "var(--ink-3)", marginBottom: 6 }}>
                  We refused to overwrite your local edits. Open the
                  remote file in {meta.label} and rename / re-upload
                  it manually, or delete the local copy and re-sync.
                </div>
                <ul style={{ margin: 0, paddingLeft: 18, listStyle: "disc" }}>
                  {conflicts.slice(0, 5).map((c, i) => (
                    <li key={i} className="mono" style={{ fontSize: 11 }}>
                      {c.remote_path || c.remote_id}
                    </li>
                  ))}
                  {conflicts.length > 5 && (
                    <li style={{ fontSize: 11, color: "var(--ink-3)" }}>
                      …and {conflicts.length - 5} more.
                    </li>
                  )}
                </ul>
              </div>
            )}

            {/* AI opt-in toggle */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "6px 8px",
                background: "var(--surface-2)",
                borderRadius: 6,
              }}
            >
              <div style={{ flex: 1, fontSize: 12.5 }}>
                <div><strong>Enable AI features for {meta.label} files</strong></div>
                <div style={{ color: "var(--ink-3)", fontSize: 11 }}>
                  Off by default. Turning this on runs summarization +
                  face scan on every file synced from this source.
                </div>
              </div>
              {(() => {
                const opted = readAiOpted(link);
                return (
                  <>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={opted === true}
                      onClick={() => onToggleAi(link, true)}
                      className={opted === true ? "btn btn--primary btn--sm" : "btn btn--secondary btn--sm"}
                    >
                      {opted === true ? "Enabled ✓" : "Enable"}
                    </button>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={opted === false}
                      onClick={() => onToggleAi(link, false)}
                      className={opted === false ? "btn btn--secondary btn--sm" : "btn btn--ghost btn--sm"}
                    >
                      {opted === false ? "Paused ✓" : "Pause"}
                    </button>
                  </>
                );
              })()}
            </div>
          </div>
        );
      })}

      {/* Connect buttons */}
      <div
        style={{
          marginTop: 6,
          padding: 14,
          borderRadius: 12,
          border: "1px dashed var(--line)",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600 }}>Connect a source</div>
        <ProviderCatalog connected={connected} onConnect={onConnect}/>
      </div>

      {/* §C4.6 — iCloud Drive connect modal. Lives here (not as a
          sibling to the panel) so it shares the QueryClient
          invalidation closure for the cloud-links list. */}
      <ICloudConnectModal
        open={icloudOpen}
        onClose={() => setIcloudOpen(false)}
        onConnected={() => {
          setIcloudOpen(false);
          qc.invalidateQueries({ queryKey: ["cloud-links"] });
        }}
      />

      {/* §C4.6 — Proton Drive connect modal. Same two-step shape as
          iCloud (credentials → optional 2FA), but the underlying
          /cloud/proton-drive/* endpoints drive rclone instead of
          pyicloud. */}
      <ProtonConnectModal
        open={protonOpen}
        onClose={() => setProtonOpen(false)}
        onConnected={() => {
          setProtonOpen(false);
          qc.invalidateQueries({ queryKey: ["cloud-links"] });
        }}
      />

      {/* §C4.6 — MEGA connect modal. Single-step (no 2FA hook in
          rclone's MEGA backend) — credentials in, link out. */}
      <MegaConnectModal
        open={megaOpen}
        onClose={() => setMegaOpen(false)}
        onConnected={() => {
          setMegaOpen(false);
          qc.invalidateQueries({ queryKey: ["cloud-links"] });
        }}
      />
    </div>
  );
}


// §C4.6 — iCloud Drive sign-in modal. Three steps:
//   credentials → Apple ID + password
//   code        → 6-digit 2FA code from the user's iDevice
//   done        → brief success state before auto-close
//
// Errors at each step surface as toasts; on transient failures the
// user can retry without losing context (the step stays the same).
// Apple's session state lives on the backend keyed by `sessionId`
// for ~5 minutes; if the user dawdles past that, the next /verify
// returns a "session expired" 400 and we bounce back to step 1.
function ICloudConnectModal({ open, onClose, onConnected }) {
  const [step, setStep] = useState("credentials");
  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [busy, setBusy] = useState(false);
  // §C4.6 — track WHERE Apple sent the code so the user can confirm
  // it actually went somewhere (Apple's auto-push sometimes fails
  // silently for accounts with stale device state). The device list
  // lets the user resend to a different trusted device if the first
  // one doesn't deliver.
  const [codeSentTo, setCodeSentTo] = useState(null);
  const [trustedDevices, setTrustedDevices] = useState([]);
  const [resending, setResending] = useState(false);

  // Reset whenever the modal re-opens so a previous failed attempt
  // doesn't leak its state into the next try.
  useEffect(() => {
    if (open) {
      setStep("credentials");
      setAppleId("");
      setPassword("");
      setCode("");
      setSessionId(null);
      setBusy(false);
      setCodeSentTo(null);
      setTrustedDevices([]);
      setResending(false);
    }
  }, [open]);

  const onSubmitCredentials = async (e) => {
    e?.preventDefault?.();
    if (busy) return;
    if (!appleId || !password) {
      toast.error("Enter your Apple ID and password.");
      return;
    }
    setBusy(true);
    try {
      const r = await icloudStart(appleId, password);
      if (r.requires_2fa) {
        setSessionId(r.session_id || null);
        setCodeSentTo(r.code_sent_to || null);
        setTrustedDevices(r.trusted_devices || []);
        setStep("code");
      } else {
        // Trusted device — link was persisted immediately.
        setStep("done");
        toast.success(r.message || "iCloud connected.");
        setTimeout(() => {
          onConnected && onConnected();
        }, 800);
      }
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not sign in to iCloud.");
    } finally {
      setBusy(false);
    }
  };

  const onSubmitCode = async (e) => {
    e?.preventDefault?.();
    if (busy) return;
    const clean = (code || "").trim();
    if (!clean) {
      toast.error("Enter the 6-digit code from your iDevice.");
      return;
    }
    if (!sessionId) {
      toast.error("Session expired. Sign in again.");
      setStep("credentials");
      return;
    }
    setBusy(true);
    try {
      await icloudVerify(sessionId, clean);
      setStep("done");
      toast.success("iCloud connected — sync starting…");
      setTimeout(() => {
        onConnected && onConnected();
      }, 800);
    } catch (e) {
      toast.error(e?.detail || e?.message || "Code didn't match. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={busy ? undefined : onClose} size="md" labelledBy="icloud-modal-title">
      <ModalClose onClose={busy ? undefined : onClose}/>
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <h2 id="icloud-modal-title" style={{ margin: 0, fontSize: 18 }}>
          Connect iCloud Drive
        </h2>

        {step === "credentials" && (
          <form onSubmit={onSubmitCredentials} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
              Apple doesn't offer OAuth for iCloud Drive, so we connect
              directly with your Apple ID. Your credentials are
              encrypted at rest; the trust token from your iDevice is
              what keeps the session alive (~30 days) without
              re-prompting.
            </div>
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Apple ID
              <input
                type="email"
                autoComplete="username"
                className="input input--lg"
                value={appleId}
                onChange={(e) => setAppleId(e.target.value)}
                placeholder="you@icloud.com"
                disabled={busy}
                autoFocus
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Password
              <input
                type="password"
                autoComplete="current-password"
                className="input input--lg"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Apple ID password"
                disabled={busy}
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn btn--primary" disabled={busy}>
                {busy ? "Signing in…" : "Continue"}
              </button>
            </div>
          </form>
        )}

        {step === "code" && (
          <form onSubmit={onSubmitCode} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {/* §C4.6 — the backend now explicitly calls
                send_verification_code() before stashing the
                session, so by the time we render this view Apple
                HAS been asked to deliver a code somewhere. Show
                the user exactly where, and offer to retry on a
                different device if the first one doesn't arrive
                (Apple's push fails silently for offline devices /
                anti-abuse heuristics). */}
            <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>
              {codeSentTo ? (
                <>Code sent to <strong style={{ color: "var(--ink)" }}>{codeSentTo}</strong>. Enter it below.</>
              ) : (
                <>Apple should send a 6-digit code to one of your trusted devices. Enter it below.</>
              )}
            </div>
            {/* If the code doesn't arrive within ~30 s, the user
                can pick a different device from the dropdown OR
                fall back to "Get Verification Code" in iDevice
                Settings. Both paths are surfaced so the user is
                never stuck. */}
            {trustedDevices.length > 0 ? (
              // HSA-1 / 2SA path: we triggered the send + know which
              // devices are trusted. Let the user retry on a
              // different one.
              <details style={{
                fontSize: 11.5,
                color: "var(--ink-3)",
                background: "var(--surface-2)",
                border: "1px solid var(--line)",
                borderRadius: 8,
                padding: "8px 12px",
              }}>
                <summary style={{ cursor: "pointer", color: "var(--ink-2)" }}>
                  Didn&rsquo;t get the code? Try a different device.
                </summary>
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                  {trustedDevices.map((dev) => (
                    <button
                      key={dev.id}
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={resending || busy}
                      onClick={async () => {
                        if (!sessionId) return;
                        setResending(true);
                        try {
                          const r = await icloudResendCode(sessionId, dev.id);
                          setCodeSentTo(r.code_sent_to || dev.label);
                          toast.success(`Code sent to ${r.code_sent_to || dev.label}`);
                        } catch (e) {
                          toast.error(e?.detail || "Couldn't resend the code.");
                        } finally {
                          setResending(false);
                        }
                      }}
                      style={{
                        justifyContent: "flex-start",
                        textAlign: "left",
                        fontFamily: "monospace",
                        fontSize: 12,
                      }}
                    >
                      Send to {dev.label}
                    </button>
                  ))}
                  <div style={{ marginTop: 4, fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5 }}>
                    Still nothing? On any iPhone / iPad / Mac signed in
                    to this Apple ID, open{" "}
                    <strong>Settings → [your name] → Sign-In &amp;
                    Security → Get Verification Code</strong>.
                  </div>
                </div>
              </details>
            ) : (
              // HSA-2 path (the default since 2017): the backend
              // can't trigger Apple to send another code, since
              // those APIs only work for the older 2SA stack. The
              // user's only fallback is the iDevice's own Settings
              // menu. Codes also expire quickly (~30 s once
              // displayed) — typing speed matters.
              <div style={{
                fontSize: 11.5,
                color: "var(--ink-3)",
                background: "var(--surface-2)",
                border: "1px solid var(--line)",
                borderRadius: 8,
                padding: "10px 12px",
                lineHeight: 1.55,
              }}>
                <div style={{ color: "var(--ink-2)" }}>
                  <strong>Code didn&rsquo;t arrive or expired?</strong>
                </div>
                On any iPhone / iPad / Mac signed in to this Apple
                ID, open{" "}
                <strong>Settings → [your name] → Sign-In &amp;
                Security → Get Verification Code</strong>. Apple&rsquo;s
                codes expire within ~30 seconds once shown, so type
                fast.
              </div>
            )}
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Verification code
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                className="input input--lg"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                disabled={busy}
                autoFocus
                maxLength={10}
                style={{ width: "100%", marginTop: 4, letterSpacing: 4, fontFamily: "monospace" }}
              />
            </label>
            <div style={{ display: "flex", gap: 8, justifyContent: "space-between", alignItems: "center" }}>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setStep("credentials");
                  setCode("");
                  setSessionId(null);
                }}
                disabled={busy}
              >
                Use a different account
              </button>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
                  Cancel
                </button>
                <button type="submit" className="btn btn--primary" disabled={busy}>
                  {busy ? "Verifying…" : "Verify"}
                </button>
              </div>
            </div>
          </form>
        )}

        {step === "done" && (
          <div style={{ fontSize: 13, color: "var(--ink-2)", padding: "6px 0" }}>
            iCloud connected — sync will start shortly.
          </div>
        )}
      </div>
    </Modal>
  );
}


// §C4.6 — Proton Drive sign-in modal. Same two-step shape as iCloud:
//   credentials → Proton email + password
//   code        → 6-digit 2FA code (only if the account has 2FA on)
//   done        → brief success state before auto-close
//
// Backed by /cloud/proton-drive/{start,verify}, which write a per-
// link rclone config file and probe Proton's auth with `rclone about
// proton-drive:`. Errors at each step surface as toasts; on transient
// failures the user can retry without losing context.
function ProtonConnectModal({ open, onClose, onConnected }) {
  const [step, setStep] = useState("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setStep("credentials");
      setEmail("");
      setPassword("");
      setCode("");
      setSessionId(null);
      setBusy(false);
    }
  }, [open]);

  const onSubmitCredentials = async (e) => {
    e?.preventDefault?.();
    if (busy) return;
    if (!email || !password) {
      toast.error("Enter your Proton email and password.");
      return;
    }
    setBusy(true);
    try {
      const r = await protonStart(email, password);
      if (r.requires_2fa) {
        setSessionId(r.session_id || null);
        setStep("code");
      } else {
        setStep("done");
        toast.success(r.message || "Proton Drive connected.");
        setTimeout(() => onConnected && onConnected(), 800);
      }
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not sign in to Proton.");
    } finally {
      setBusy(false);
    }
  };

  const onSubmitCode = async (e) => {
    e?.preventDefault?.();
    if (busy) return;
    const clean = (code || "").trim();
    if (!clean) {
      toast.error("Enter the 6-digit code from your authenticator.");
      return;
    }
    if (!sessionId) {
      toast.error("Session expired. Sign in again.");
      setStep("credentials");
      return;
    }
    setBusy(true);
    try {
      await protonVerify(sessionId, clean);
      setStep("done");
      toast.success("Proton Drive connected — sync starting…");
      setTimeout(() => onConnected && onConnected(), 800);
    } catch (e) {
      toast.error(e?.detail || e?.message || "Code didn't match. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={busy ? undefined : onClose} size="md" labelledBy="proton-modal-title">
      <ModalClose onClose={busy ? undefined : onClose}/>
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <h2 id="proton-modal-title" style={{ margin: 0, fontSize: 18 }}>
          Connect Proton Drive
        </h2>

        {step === "credentials" && (
          <form onSubmit={onSubmitCredentials} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
              Proton Drive is end-to-end encrypted. We drive the sync
              with rclone, which decrypts your files locally so neuthek
              can re-encrypt them with your account key. Your password
              is stored encrypted at rest in a per-link rclone config.
            </div>
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Proton email
              <input
                type="email"
                autoComplete="username"
                className="input input--lg"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@proton.me"
                disabled={busy}
                autoFocus
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Password
              <input
                type="password"
                autoComplete="current-password"
                className="input input--lg"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Proton account password"
                disabled={busy}
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn btn--primary" disabled={busy}>
                {busy ? "Signing in…" : "Continue"}
              </button>
            </div>
          </form>
        )}

        {step === "code" && (
          <form onSubmit={onSubmitCode} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
              Your Proton account has 2FA enabled. Enter the 6-digit
              code from your authenticator app below.
            </div>
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Verification code
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                className="input input--lg"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                disabled={busy}
                autoFocus
                maxLength={10}
                style={{ width: "100%", marginTop: 4, letterSpacing: 4, fontFamily: "monospace" }}
              />
            </label>
            <div style={{ display: "flex", gap: 8, justifyContent: "space-between", alignItems: "center" }}>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setStep("credentials");
                  setCode("");
                  setSessionId(null);
                }}
                disabled={busy}
              >
                Use a different account
              </button>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
                  Cancel
                </button>
                <button type="submit" className="btn btn--primary" disabled={busy}>
                  {busy ? "Verifying…" : "Verify"}
                </button>
              </div>
            </div>
          </form>
        )}

        {step === "done" && (
          <div style={{ fontSize: 13, color: "var(--ink-2)", padding: "6px 0" }}>
            Proton Drive connected — sync will start shortly.
          </div>
        )}
      </div>
    </Modal>
  );
}


// §C4.6 — MEGA sign-in modal. Single-step: rclone's MEGA backend
// doesn't surface a 2FA hook, so the password either works on first
// try or doesn't. (MEGA's web 2FA layer kicks in for the browser
// sign-in, not for the SDK rclone uses under the hood.)
function MegaConnectModal({ open, onClose, onConnected }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (open) {
      setEmail("");
      setPassword("");
      setBusy(false);
      setDone(false);
    }
  }, [open]);

  const onSubmit = async (e) => {
    e?.preventDefault?.();
    if (busy) return;
    if (!email || !password) {
      toast.error("Enter your MEGA email and password.");
      return;
    }
    setBusy(true);
    try {
      await megaStart(email, password);
      setDone(true);
      toast.success("MEGA connected — sync starting…");
      setTimeout(() => onConnected && onConnected(), 800);
    } catch (e) {
      toast.error(e?.detail || e?.message || "Could not sign in to MEGA.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={busy ? undefined : onClose} size="md" labelledBy="mega-modal-title">
      <ModalClose onClose={busy ? undefined : onClose}/>
      <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
        <h2 id="mega-modal-title" style={{ margin: 0, fontSize: 18 }}>
          Connect MEGA
        </h2>
        {!done && (
          <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
              MEGA is end-to-end encrypted. We drive the sync with
              rclone, which decrypts files locally so neuthek can
              re-encrypt them with your account key. Your credentials
              are stored encrypted at rest in a per-link rclone config.
            </div>
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              MEGA email
              <input
                type="email"
                autoComplete="username"
                className="input input--lg"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                disabled={busy}
                autoFocus
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <label style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Password
              <input
                type="password"
                autoComplete="current-password"
                className="input input--lg"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="MEGA account password"
                disabled={busy}
                style={{ width: "100%", marginTop: 4 }}
              />
            </label>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn btn--primary" disabled={busy}>
                {busy ? "Signing in…" : "Connect"}
              </button>
            </div>
          </form>
        )}
        {done && (
          <div style={{ fontSize: 13, color: "var(--ink-2)", padding: "6px 0" }}>
            MEGA connected — sync will start shortly.
          </div>
        )}
      </div>
    </Modal>
  );
}


// §C4.6 — provider catalog. Renders one card per known cloud
// provider with status-gated affordances:
//   "available"    — Connect button (or "Connected" chip if active).
//   "needs_setup"  — disabled card + small "Operator needs to set
//                    OAuth credentials" hint + Docs link.
//   "coming_soon"  — greyed-out card + "Notify me" placeholder.
//
// Catalog is pulled from /cloud/providers so the same FE code
// shows different status mixes depending on which env vars the
// operator set. No code change required to promote a provider
// from needs_setup → available — just set the env var + recreate
// the backend.
function ProviderCatalog({ connected, onConnect }) {
  const { data, isLoading } = useQuery({
    queryKey: ["cloud-providers"],
    queryFn: getCloudProviders,
    staleTime: 60_000,
  });

  if (isLoading || !data) {
    return (
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
        gap: 8,
        marginTop: 6,
      }}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="set-skel" style={{ height: 52, borderRadius: 10 }}/>
        ))}
      </div>
    );
  }

  return (
    // §C7 — tighter grid: the cards collapsed to single-line
    // header+chip after the blurb removal, so we can fit more per
    // row. min-width drops from 180 → 220 (wider rows + fewer
    // visual seams when 5 cards wrap to two rows of 2+3).
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
      gap: 8,
      marginTop: 6,
    }}>
      {data.map((p) => (
        <ProviderCard
          key={p.id}
          provider={p}
          connected={connected.has(p.id)}
          onConnect={() => onConnect(p.id)}
        />
      ))}
    </div>
  );
}

// §C7 — short labels keyed by auth_shape from the backend
// catalog. The earlier card showed a long `provider.blurb`
// describing each integration in 2-3 sentences; users reported
// it was visually noisy ("more clean, less text filled"). Drop
// the blurb and surface only the auth method as a tiny subtitle
// (one line, no period). Tooltip on the chip holds the longer
// description for users who want details.
const AUTH_SHAPE_LABEL = {
  oauth: "OAuth",
  apple_id: "Apple ID + 2FA",
  password: "Email + password",
};

function ProviderCard({ provider, connected, onConnect }) {
  const isAvailable = provider.status === "available";
  const isNeedsSetup = provider.status === "needs_setup";
  const isComingSoon = provider.status === "coming_soon";

  // Status chip palette + label. Connected state takes precedence
  // over the catalog status so the user sees green-Connected on
  // their actively-linked providers without having to read the
  // sub-row first.
  const chipColor =
    connected ? "var(--success)" :
    isAvailable ? "var(--ink-2, var(--ink-3))" :
    isNeedsSetup ? "var(--warning, #f59e0b)" :
    "var(--ink-3)";
  const chipBg =
    connected ? "var(--success-soft, color-mix(in oklab, var(--success) 12%, transparent))" :
    "var(--surface-2)";
  const chipLabel =
    connected ? "Connected" :
    isAvailable ? "Available" :
    isNeedsSetup ? "Needs setup" :
    "Coming soon";

  // Subtitle: tiny one-liner identifying the auth method.
  // No long blurb. Hover the chip if you want the wordy version.
  const subtitle =
    AUTH_SHAPE_LABEL[provider.auth_shape] ||
    AUTH_SHAPE_LABEL[provider.auth_shape || "oauth"] ||
    "Cloud sync";

  const onClick = () => {
    if (!isAvailable || connected) return;
    onConnect();
  };

  return (
    <div
      onClick={onClick}
      title={provider.blurb || undefined}
      style={{
        padding: "10px 12px",
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        cursor: isAvailable && !connected ? "pointer" : "default",
        opacity: isComingSoon ? 0.55 : 1,
        display: "flex",
        alignItems: "center",
        gap: 10,
        transition: "border-color 120ms ease, background 120ms ease",
      }}
      onMouseEnter={(e) => {
        if (isAvailable && !connected) {
          e.currentTarget.style.borderColor = "var(--ink-2, var(--ink-3))";
          e.currentTarget.style.background = "var(--surface-2)";
        }
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--line)";
        e.currentTarget.style.background = "var(--surface)";
      }}
    >
      <Icon name="cloud" size={14} style={{ color: "var(--ink-3)", flexShrink: 0 }}/>
      <div style={{ flex: 1, minWidth: 0, lineHeight: 1.3 }}>
        <div style={{
          fontSize: 13,
          fontWeight: 600,
          color: "var(--ink)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {provider.name}
        </div>
        <div style={{
          fontSize: 11,
          color: "var(--ink-3)",
          marginTop: 1,
        }}>
          {subtitle}
        </div>
      </div>
      <span style={{
        fontSize: 10,
        padding: "2px 8px",
        borderRadius: 999,
        background: chipBg,
        color: chipColor,
        fontWeight: 600,
        letterSpacing: 0.02,
        flexShrink: 0,
      }}>
        {chipLabel}
      </span>
    </div>
  );
}
