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
import {
  connectCloud,
  disconnectCloud,
  listCloudConflicts,
  listCloudLinks,
  setCloudAiOptIn,
  syncCloudLink,
  getSyncStatus,
} from "@/api/cloud";

const PROVIDER_META = {
  google_drive: { label: "Google Drive", note: "Read-only · drive.readonly scope" },
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
      <div style={{ padding: 18, color: "var(--ink-3)" }}>
        Could not load cloud links. {error.message || ""}
      </div>
    );
  }
  if (isLoading) {
    return <div style={{ padding: 18, color: "var(--ink-3)" }}>Loading…</div>;
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
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {(["google_drive"]).map((p) => {
            const meta = PROVIDER_META[p];
            const already = connected.has(p);
            return (
              <button
                key={p}
                className="btn btn--secondary btn--sm"
                onClick={() => onConnect(p)}
                disabled={already}
                title={meta.note}
              >
                <Icon name="cloud" size={12}/>
                {already ? `${meta.label} connected` : `Connect ${meta.label}`}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
