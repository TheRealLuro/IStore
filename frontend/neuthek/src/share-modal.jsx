// Share-grant create + manage modal (todo §1.1 / G1).
//
// Sharer enters a recipient email + picks a duration. On submit we
// POST /images/{id}/shares; backend returns `share_url` exactly once
// (the plaintext token isn't stored, only its argon2 hash). We copy
// the URL to the clipboard immediately and show it inline so the
// sharer can paste it into their own messenger — V1 doesn't send
// the link via email because §C6 has email infra deferred.
//
// Existing grants on the same image are listed below the form with
// a per-row revoke button. Re-sharing to the same recipient supersedes
// the prior grant server-side and writes a `share.replaced` audit row.
import React, { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import {
  buildShareUrlWithEmail,
  createShare,
  listShares,
  revokeShare,
} from "@/api/shares";

// UI offers a small set so the sharer doesn't fiddle with seconds.
// Server caps at 30 days (SHARE_MAX_DURATION_SECONDS) and clamps
// to 1 day for new-user recipients regardless of what's chosen.
const DURATION_PRESETS = [
  { label: "1 hour",  seconds: 3600 },
  { label: "1 day",   seconds: 86400 },
  { label: "7 days",  seconds: 7 * 86400 },
  { label: "30 days", seconds: 30 * 86400 },
];

function fmtCountdown(iso) {
  if (!iso) return "pending signup";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const d = Math.floor(ms / 86400000);
  if (d >= 1) return `${d} day${d === 1 ? "" : "s"} left`;
  const h = Math.floor(ms / 3600000);
  if (h >= 1) return `${h} hour${h === 1 ? "" : "s"} left`;
  const m = Math.max(1, Math.floor(ms / 60000));
  return `${m} min left`;
}

export function ShareModal({ imageId, imageName, onClose }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [duration, setDuration] = useState(86400);
  const [submitting, setSubmitting] = useState(false);
  const [issuedUrl, setIssuedUrl] = useState("");
  const [issuedEmail, setIssuedEmail] = useState("");

  const { data: grants = [], isLoading } = useQuery({
    queryKey: ["image-shares", imageId],
    queryFn: () => listShares(imageId),
    enabled: !!imageId,
  });

  // Esc closes the modal — matches PreviewPanel behavior so the
  // shortcut feels consistent.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const fullShareUrl = useMemo(
    () => (issuedUrl && issuedEmail ? buildShareUrlWithEmail(issuedUrl, issuedEmail) : ""),
    [issuedUrl, issuedEmail],
  );

  const handleCreate = async (e) => {
    e?.preventDefault?.();
    const trimmed = email.trim();
    if (!trimmed) {
      toast.error("Enter a recipient email.");
      return;
    }
    setSubmitting(true);
    try {
      const grant = await createShare(imageId, {
        recipientEmail: trimmed,
        durationSeconds: duration,
      });
      const fullUrl = buildShareUrlWithEmail(grant.share_url || "", trimmed);
      setIssuedUrl(grant.share_url || "");
      setIssuedEmail(trimmed);
      try {
        await navigator.clipboard.writeText(fullUrl);
        toast.success("Link copied to clipboard");
      } catch {
        toast.success("Share link ready — copy it below");
      }
      setEmail("");
      qc.invalidateQueries({ queryKey: ["image-shares", imageId] });
      qc.invalidateQueries({ queryKey: ["incoming-shares"] });
    } catch (err) {
      toast.error(err?.detail || "Could not create share");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (grantId, recipientLabel) => {
    if (!window.confirm(`Revoke this share with ${recipientLabel}?`)) return;
    try {
      await revokeShare(imageId, grantId);
      toast.success("Share revoked");
      qc.invalidateQueries({ queryKey: ["image-shares", imageId] });
      qc.invalidateQueries({ queryKey: ["incoming-shares"] });
    } catch (err) {
      toast.error(err?.detail || "Could not revoke");
    }
  };

  return (
    <div
      className="share-modal__overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Share file"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)",
        display: "grid", placeItems: "center", zIndex: 100,
      }}
    >
      <div
        className="share-modal"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(540px, 92vw)", background: "var(--surface, #fff)",
          color: "var(--ink, #111)", borderRadius: 12, padding: 22,
          boxShadow: "0 12px 40px rgba(0,0,0,0.18)", maxHeight: "86vh",
          overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ink-3)" }}>Share</div>
            <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{imageName || "this file"}</div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close share dialog">
            <Icon name="x" size={15}/>
          </button>
        </div>

        <form onSubmit={handleCreate} style={{ display: "grid", gap: 10 }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>Recipient email</span>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="them@example.com"
              style={{
                padding: "9px 10px", borderRadius: 8,
                border: "1px solid var(--border, #ddd)", background: "var(--surface-2, #f7f7f7)",
                color: "var(--ink, #111)", fontSize: 13,
              }}
            />
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>Access window</span>
            <select
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              style={{
                padding: "9px 10px", borderRadius: 8,
                border: "1px solid var(--border, #ddd)", background: "var(--surface-2, #f7f7f7)",
                color: "var(--ink, #111)", fontSize: 13,
              }}
            >
              {DURATION_PRESETS.map((p) => (
                <option key={p.seconds} value={p.seconds}>{p.label}</option>
              ))}
            </select>
            <span style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.4 }}>
              Existing accounts get the duration you pick. Recipients without
              an account get exactly <strong>1 day</strong> after they sign up,
              regardless of this setting.
            </span>
          </label>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={submitting}>Cancel</button>
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? "Creating…" : "Create link"}
            </button>
          </div>
        </form>

        {fullShareUrl && (
          <div
            style={{
              marginTop: 14, padding: 12, borderRadius: 8,
              border: "1px solid var(--border, #ddd)", background: "var(--surface-2, #f7f7f7)",
            }}
          >
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 6 }}>
              Copy this link and send it to {issuedEmail}.
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                readOnly
                value={fullShareUrl}
                onFocus={(e) => e.currentTarget.select()}
                style={{
                  flex: 1, padding: "8px 10px", borderRadius: 6,
                  border: "1px solid var(--border, #ddd)", background: "var(--surface, #fff)",
                  color: "var(--ink, #111)", fontSize: 12, fontFamily: "monospace",
                }}
              />
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  navigator.clipboard.writeText(fullShareUrl).then(
                    () => toast.success("Copied"),
                    () => toast.error("Copy failed — select and copy manually"),
                  );
                }}
              >
                Copy
              </button>
            </div>
          </div>
        )}

        <div style={{ marginTop: 18 }}>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Active shares
          </div>
          {isLoading ? (
            <div style={{ fontSize: 12, color: "var(--ink-3)" }}>Loading…</div>
          ) : grants.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No active shares.</div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
              {grants.map((g) => {
                const label = g.recipient_display_name || g.recipient_email;
                const pending = !g.recipient_user_id;
                return (
                  <li
                    key={g.id}
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      padding: "8px 10px", borderRadius: 6,
                      background: "var(--surface-2, #f7f7f7)",
                      border: "1px solid var(--border, #eee)",
                    }}
                  >
                    <div style={{ display: "grid", gap: 2 }}>
                      <strong style={{ fontSize: 13 }}>{label}</strong>
                      <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
                        {pending ? "Pending signup · 1 day window starts at signup" : fmtCountdown(g.expires_at)}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => handleRevoke(g.id, label)}
                      title="Revoke this share"
                    >
                      Revoke
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
