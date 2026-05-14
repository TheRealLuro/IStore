// Account & Settings — Apple-style redesign with INLINE expansion panels.
// Two-pane modal: left rail of categories, right pane shows grouped lists.
// Every chevron row is clickable and expands a real form/view in place.
import React, { useState as useStateAcc, useEffect as useEffectAcc } from "react";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalAcc,
  ModalClose as ModalCloseAcc,
  Switch as SwitchAcc,
} from "./primitives.jsx";
import {
  PasswordChangePanel,
  FaceDetailPanel,
  LocationDetailPanel,
  TelemetryDetailPanel,
  StorageBreakdownPanel,
  SecurityPanel,
} from "./account-panels.jsx";
import toast from "react-hot-toast";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { updateMe } from "@/api/auth";
import { useAuthStore } from "@/stores/authStore";
import {
  getConsentScopes,
  grantScope,
  withdrawScope,
  withdrawConsent,
  rescanAllFaces,
} from "@/api/consent";
import { backfillSummaries } from "@/api/files";

function initialsAcc(name) {
  return (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(p => p[0]?.toUpperCase() || "")
    .join("") || "?";
}

// Settings rail. We deliberately omit "Notifications" — it's entirely
// mock (no email/push backend) and was confusing users who toggled it
// expecting it to do something. Re-add when the notifications backend
// ships (todo.md C8 / I.fe.1).
const APPSET_NAV = [
  { id: "profile",   label: "Account",      icon: "user",     tone: "ink"    },
  { id: "privacy",   label: "Privacy",      icon: "shield",   tone: "blue"   },
  { id: "security",  label: "Security",     icon: "lock",     tone: "red"    },
  { id: "ai",        label: "AI features",  icon: "sparkles", tone: "purple" },
  { id: "data",      label: "Your data",    icon: "download", tone: "green"  },
];

// Pretty-print a granted/withdrawn ISO timestamp for the consent rows.
// Returns "" for nullish so the caller can drop empty subtitles.
function fmtConsentDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      month: "short", day: "numeric", year: "numeric",
    });
  } catch { return ""; }
}

export function AccountModal({ open, onClose, onOpenSubmodal, user, onUserChange, onSignOut, initialTab = "profile" }) {
  const [tab, setTab] = useStateAcc(initialTab);
  // 2FA toggle here is the section visibility flag, not a real grant.
  // SecurityPanel below shows the recovery-codes UI which IS wired.
  // emailNotif state retained for SecurityPanel's signature; the
  // notifications surface itself is hidden until the backend ships.
  const [twoFA, setTwoFA] = useStateAcc(true);
  const [emailNotif, setEmailNotif] = useStateAcc(true);
  // Real per-scope consent state. Each toggle hits /consent/{scope}/grant|
  // withdraw and invalidates this query so the UI reflects the server.
  const qc = useQueryClient();
  const { data: scopeData } = useQuery({
    queryKey: ["consent-scopes"],
    queryFn: getConsentScopes,
    enabled: open,
    staleTime: 30_000,
  });
  const scopeOf = (k) => (scopeData?.states?.[k] === "GRANTED");
  const detailOf = (k) => scopeData?.details?.[k];
  // Subtitle copy that shows the user when each consent was set so they
  // can audit their own decisions. "Granted on …" / "Withdrawn on …" /
  // "Not configured yet" — same shape across all five scopes.
  const subFor = (k, onCopy, offCopy) => {
    const d = detailOf(k);
    const stamp = d?.granted_at ? fmtConsentDate(d.granted_at) : "";
    if (d?.state === "GRANTED")  return `${onCopy}${stamp ? ` · Granted ${stamp}` : ""}`;
    if (d?.state === "WITHDRAWN") return `${offCopy}${stamp ? ` · Withdrawn ${stamp}` : ""}`;
    return `${offCopy} · Not configured yet`;
  };
  const aiSummaries    = scopeOf("ai_summary");
  const semanticSearch = scopeOf("semantic_search");
  const faceRecog      = scopeOf("face_recognition");
  const gpsTags        = scopeOf("gps_retention");
  const telemetry      = scopeOf("bandit_compression_telemetry");
  const flipScope = async (scope, currentlyOn) => {
    try {
      if (currentlyOn) await withdrawScope(scope);
      else await grantScope(scope);
      qc.invalidateQueries({ queryKey: ["consent-scopes"] });
    } catch (e) {
      toast.error(e?.detail || "Could not save consent");
    }
  };
  const setAiSummaries    = () => flipScope("ai_summary", aiSummaries);
  const setSemanticSearch = () => flipScope("semantic_search", semanticSearch);
  const setGpsTags        = () => flipScope("gps_retention", gpsTags);
  const setTelemetry      = () => flipScope("bandit_compression_telemetry", telemetry);
  // face_recognition is BIPA-grade — granting requires the signed-statement
  // payload via the dedicated /consent/face-recognition/grant endpoint.
  // The generic /consent/{kind}/grant rejects it with 400. So clicking the
  // toggle when it's currently OFF opens the sign-and-consent modal;
  // clicking when ON withdraws via the dedicated dashed endpoint.
  const setFaceRecog = async () => {
    if (faceRecog) {
      try {
        await withdrawConsent();
        qc.invalidateQueries({ queryKey: ["consent-scopes"] });
        toast.success("Face recognition disabled. Templates queued for deletion.");
      } catch (e) {
        toast.error(e?.detail || "Could not withdraw consent");
      }
    } else {
      onOpenSubmodal?.("face");
    }
  };

  // AI maintenance actions exposed in the AI features tab. Both queue
  // background work; we toast immediately and let the file/people queries
  // re-fetch as the worker completes rows.
  const [busyResummarize, setBusyResummarize] = useStateAcc(false);
  const [busyRescan, setBusyRescan] = useStateAcc(false);
  const reprocessSummaries = async () => {
    if (busyResummarize) return;
    if (!window.confirm("Re-run summarization on every image in your library? Uses your local Florence-2 + Qwen2.5 models and can take several minutes.")) return;
    setBusyResummarize(true);
    try {
      const r = await backfillSummaries(500, true);
      toast.success(`Queued ${r.queued} files for re-summarization.`);
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      toast.error(e?.detail || "Could not start re-summarize.");
    } finally {
      setBusyResummarize(false);
    }
  };
  const rescanFaces = async () => {
    if (busyRescan) return;
    if (!window.confirm("Re-scan every photo for faces? This clears existing face data and rebuilds from scratch.")) return;
    setBusyRescan(true);
    try {
      const r = await rescanAllFaces();
      if (!r.consent_active) {
        toast.error("Face recognition consent isn't active. Enable it first.");
      } else {
        toast.success(`Cleared ${r.cleared_existing_faces} faces · queued ${r.queued} photos.`);
      }
      qc.invalidateQueries({ queryKey: ["people"] });
    } catch (e) {
      toast.error(e?.detail || "Could not start face re-scan.");
    } finally {
      setBusyRescan(false);
    }
  };

  const [editingProfile, setEditingProfile] = useStateAcc(false);
  const [draftName, setDraftName] = useStateAcc(user?.name || "");
  const [draftEmail, setDraftEmail] = useStateAcc(user?.email || "");

  // expansion state — { [rowId]: bool }
  const [exp, setExp] = useStateAcc({});
  const tog = (id) => setExp(e => ({ ...e, [id]: !e[id] }));
  const isOpen = (id) => !!exp[id];

  useEffectAcc(() => {
    if (open) {
      // Honor the deep-linked initialTab (e.g. sidebar attention pill →
      // "privacy") instead of always landing on Account.
      setTab(initialTab || "profile");
      setDraftName(user?.name || "");
      setDraftEmail(user?.email || "");
      setEditingProfile(false);
      setExp({});
    }
  }, [open, initialTab, user?.name, user?.email]);

  // Reset expansion when switching tabs to keep things tidy
  useEffectAcc(() => { setExp({}); }, [tab]);

  const setStoreUser = useAuthStore((s) => s.setUser);

  // Real PATCH /users/me. The server currently treats display_name
  // updates and email changes the same way; password changes go through
  // the Password sub-panel below.
  const saveProfile = async () => {
    const name = draftName.trim();
    const email = draftEmail.trim();
    const body = {};
    if (name && name !== (user?.name || "")) body.display_name = name;
    if (email && email !== (user?.email || "")) body.email = email;
    if (Object.keys(body).length === 0) {
      setEditingProfile(false);
      return;
    }
    try {
      const updated = await updateMe(body);
      setStoreUser(updated);
      onUserChange?.({
        name: updated.display_name || updated.email.split("@")[0],
        email: updated.email,
      });
      toast.success("Profile updated");
      setEditingProfile(false);
    } catch (e) {
      toast.error(e?.detail || "Could not save changes");
    }
  };

  // Apple-style row primitive — supports expanded state
  const Row = ({ id, icon, tone, title, desc, tail, onClick, expanded }) => (
    <div className="applist__row" data-expanded={expanded ? "true" : "false"}
         style={onClick ? { cursor: "pointer" } : null} onClick={onClick}>
      {icon && (
        <div className="applist__row-icon" data-tone={tone}>
          <Icon name={icon} size={14}/>
        </div>
      )}
      <div className="applist__row-body">
        <div className="applist__row-title">{title}</div>
        {desc && <div className="applist__row-desc">{desc}</div>}
      </div>
      {tail && <div className="applist__row-tail">{tail}</div>}
    </div>
  );

  const Chev = ({ rotated }) => (
    <div className="applist__row-chevron" style={{ transform: rotated ? "rotate(90deg)" : null, transition: "transform 0.15s" }}>
      <Icon name="chevronRight" size={14}/>
    </div>
  );

  // Helper to render an expandable row with its panel
  const Expandable = ({ id, ...rowProps }) => (
    <>
      <Row id={id} {...rowProps}
           tail={<>{rowProps.tailExtra}<Chev rotated={isOpen(id)}/></>}
           expanded={isOpen(id)}
           onClick={() => tog(id)}/>
      {isOpen(id) && rowProps.panel}
    </>
  );

  return (
    <ModalAcc open={open} onClose={onClose} size="xl" labelledBy="acc-title">
      <div className="modal__head">
        <h2 id="acc-title">
          <span className="modal__head-icon"><Icon name="settings" size={16}/></span>
          Settings
        </h2>
        <p>Manage your sign-in, privacy, AI features, and stored data.</p>
        <ModalCloseAcc onClose={onClose}/>
      </div>

      <div className="modal__body" style={{ padding: 0 }}>
        <div className="appset">
          <aside className="appset__sidebar">
            {APPSET_NAV.map(item => (
              <button key={item.id}
                      className="appset__navitem"
                      data-active={tab === item.id}
                      onClick={() => setTab(item.id)}>
                <span className="appset__navitem-icon">
                  <Icon name={item.icon} size={14}/>
                </span>
                {item.label}
              </button>
            ))}
            <div className="appset__sidebar-spacer"/>
            <button className="appset__navitem appset__navitem--danger" onClick={() => onSignOut?.()}>
              <span className="appset__navitem-icon"><Icon name="logout" size={14}/></span>
              Sign out
            </button>
          </aside>

          <main className="appset__main">
            {tab === "profile" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>Account</h3>
                    <p>Your name, email, and plan.</p>
                  </div>
                </div>

                <div className="appset__profile">
                  <div className="appset__profile-avatar">{initialsAcc(user?.name)}</div>
                  {editingProfile ? (
                    <div className="appset__profile-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      <input className="input" value={draftName}
                             placeholder="Display name"
                             onChange={e => setDraftName(e.target.value)}/>
                      <input className="input" value={draftEmail}
                             placeholder="you@example.com"
                             onChange={e => setDraftEmail(e.target.value)}/>
                      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                        <button className="btn btn--primary btn--sm" onClick={saveProfile}>Save</button>
                        <button className="btn btn--secondary btn--sm" onClick={() => setEditingProfile(false)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="appset__profile-body">
                        <div className="appset__profile-name">{user?.name || "—"}</div>
                        <div className="appset__profile-email">{user?.email || ""}</div>
                      </div>
                      <button className="btn btn--secondary btn--sm" onClick={() => setEditingProfile(true)}>Edit</button>
                    </>
                  )}
                </div>

                <div className="applist__label">Sign-in & security</div>
                <div className="applist">
                  <Expandable id="pwd" icon="lock" tone="red"
                              title="Password" desc="Last changed 4 months ago"
                              tailExtra={<span style={{ color: "var(--ink-3)", marginRight: 8 }}>•••••••••</span>}
                              panel={<PasswordChangePanel onSaved={() => tog("pwd")}/>}/>
                  {/* 2FA TOTP isn't wired yet (todo.md C6.4). The real
                      lockout-recovery flow lives in the Security tab as
                      "Recovery codes" (wired to /account/recovery-codes).
                      Trusted devices is also still mock — hidden until
                      session enumeration backend ships. */}
                </div>

                {/* Subscription / Plan / Invoices: hidden until billing
                    backend ships (todo.md "Things NOT to work on yet").
                    The 2 TB plan badge above is a placeholder — real
                    quota lives in users.quota_bytes via /storage/usage. */}
              </>
            )}

            {tab === "privacy" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>Privacy</h3>
                    <p>Control what we collect and how it's used.</p>
                  </div>
                </div>

                <div className="applist__label">AI on your library</div>
                <div className="applist">
                  <Row icon="sparkles" tone="purple" title="AI summaries"
                       desc={subFor("ai_summary",
                         "Florence-2 + Qwen describe new uploads.",
                         "Off — no summary generated.")}
                       tail={<SwitchAcc on={aiSummaries} onChange={setAiSummaries} ariaLabel="AI summaries"/>}/>
                  <Row icon="search" tone="blue" title="Semantic search"
                       desc={subFor("semantic_search",
                         "CLIP embeddings stored, queryable by meaning.",
                         "Off — search by filename only.")}
                       tail={<SwitchAcc on={semanticSearch} onChange={setSemanticSearch} ariaLabel="Semantic search"/>}/>
                  <Row icon="users" tone="purple" title="Face recognition"
                       desc={subFor("face_recognition",
                         "Faces detected and grouped on this server.",
                         "Off — requires written consent (BIPA).")}
                       tail={<SwitchAcc on={faceRecog} onChange={setFaceRecog} ariaLabel="Face recognition"/>}/>
                  {faceRecog && (
                    <Expandable id="face-detail" icon="info" tone="indigo"
                                title="Face data details" desc="Detection counts and management"
                                panel={<FaceDetailPanel/>}/>
                  )}
                </div>

                <div className="applist__label">Photo metadata</div>
                <div className="applist">
                  <Row icon="map" tone="green" title="Keep GPS from photos"
                       desc={subFor("gps_retention",
                         "EXIF location stored — pins appear on the Map.",
                         "Stripped on upload — no map pins.")}
                       tail={<SwitchAcc on={gpsTags} onChange={setGpsTags} ariaLabel="GPS retention"/>}/>
                  <Expandable id="loc-detail" icon="info" tone="indigo"
                              title="Location settings" desc="Strip GPS from existing photos"
                              panel={<LocationDetailPanel/>}/>
                </div>

                <div className="applist__label">Diagnostics</div>
                <div className="applist">
                  <Row icon="info" tone="blue" title="Compression telemetry"
                       desc={subFor("bandit_compression_telemetry",
                         "Bandit reward signals from your encodes shared anonymously.",
                         "Off — no telemetry leaves this server.")}
                       tail={<SwitchAcc on={telemetry} onChange={setTelemetry} ariaLabel="Telemetry"/>}/>
                  <Expandable id="tel-detail" icon="layers" tone="ink"
                              title="What's collected" desc="Crash reports, performance, feature usage"
                              panel={<TelemetryDetailPanel/>}/>
                </div>

                <div className="applist__label">Documents</div>
                <div className="applist">
                  <Row icon="document" tone="indigo" title="Privacy Notice"
                       desc="What we collect, why, and how to control it"
                       tail={<Chev/>} onClick={() => onOpenSubmodal?.("privacy")}/>
                  <Row icon="document" tone="indigo" title="Terms of Use"
                       desc="v4.2 · Accepted on signup"
                       tail={<Chev/>} onClick={() => onOpenSubmodal?.("terms")}/>
                  <Row icon="users" tone="purple" title="Face recognition consent"
                       desc={detailOf("face_recognition")?.granted_at
                         ? `Signed ${fmtConsentDate(detailOf("face_recognition").granted_at)} · Withdraw any time`
                         : "Read the BIPA-grade consent text"}
                       tail={<Chev/>} onClick={() => onOpenSubmodal?.("face")}/>
                </div>
              </>
            )}

            {tab === "security" && <SecurityPanel twoFA={twoFA} setTwoFA={setTwoFA} emailNotif={emailNotif} setEmailNotif={setEmailNotif} Row={Row} Chev={Chev}/>}

            {tab === "ai" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>AI features</h3>
                    <p>Florence-2 captions, Qwen2.5 rewrites, CLIP search, RetinaFace clustering — all running on this server, never sent to a third party.</p>
                  </div>
                </div>

                <div className="applist__label">Features</div>
                <div className="applist">
                  <Row icon="sparkles" tone="purple" title="AI summaries"
                       desc={subFor("ai_summary",
                         "Florence-2 + Qwen describe new uploads.",
                         "Off — no summary generated.")}
                       tail={<SwitchAcc on={aiSummaries} onChange={setAiSummaries} ariaLabel="Summaries"/>}/>
                  <Row icon="search" tone="blue" title="Semantic search"
                       desc={subFor("semantic_search",
                         "CLIP embeddings stored, queryable by meaning.",
                         "Off — search by filename only.")}
                       tail={<SwitchAcc on={semanticSearch} onChange={setSemanticSearch} ariaLabel="Semantic"/>}/>
                  <Row icon="users" tone="purple" title="Face recognition"
                       desc={subFor("face_recognition",
                         "Faces grouped on this server.",
                         "Off — requires written consent (BIPA).")}
                       tail={<SwitchAcc on={faceRecog} onChange={setFaceRecog} ariaLabel="Faces"/>}/>
                </div>

                <div className="applist__label">Library maintenance</div>
                <div className="applist">
                  <Row icon="refresh" tone="purple" title="Re-summarize entire library"
                       desc={busyResummarize
                         ? "Queueing… your library will rebuild summaries in the background."
                         : "Re-run Florence-2 + Qwen2.5 over every image. Useful after upgrading models."}
                       tail={<button className="btn btn--secondary btn--sm" onClick={reprocessSummaries} disabled={busyResummarize}>
                         {busyResummarize ? "Working…" : "Run"}
                       </button>}/>
                  <Row icon="users" tone="orange" title="Re-scan all faces"
                       desc={faceRecog
                         ? (busyRescan
                             ? "Queueing… clearing existing faces and re-detecting."
                             : "Clear existing face data and re-run RetinaFace over every photo.")
                         : "Enable Face recognition above first."}
                       tail={<button className="btn btn--secondary btn--sm" onClick={rescanFaces}
                                     disabled={busyRescan || !faceRecog}>
                         {busyRescan ? "Working…" : "Run"}
                       </button>}/>
                </div>
              </>
            )}

            {tab === "data" && (
              <>
                <div className="appset__main-head">
                  <div>
                    <h3>Your data</h3>
                    <p>Storage, exports, and deletion.</p>
                  </div>
                </div>

                <div className="applist">
                  <Expandable id="storage" icon="layers" tone="green"
                              title="Storage"
                              desc="Live breakdown by category"
                              panel={<StorageBreakdownPanel/>}/>
                  <Row icon="download" tone="blue" title="Export everything"
                       desc="ZIP of all files plus a JSON metadata sidecar"
                       tail={<Chev/>} onClick={() => onOpenSubmodal?.("export")}/>
                </div>

                {/* Trash & Activity log are not yet wired to real
                    backends — todo.md 1.11. Hidden until those land so
                    users don't toggle / click into mock data. */}

                <div className="applist__label" style={{ color: "#ff3b30" }}>Danger zone</div>
                <div className="applist">
                  <Row icon="trash" tone="red" title="Delete account" desc="Permanently remove your library"
                       tail={<Chev/>} onClick={() => onOpenSubmodal?.("delete")}/>
                </div>
              </>
            )}
          </main>
        </div>
      </div>
    </ModalAcc>
  );
}

// Named export above; legacy `window.AccountModal` removed.
