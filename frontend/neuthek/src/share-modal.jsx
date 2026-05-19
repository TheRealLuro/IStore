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

  const [revoking, setRevoking] = React.useState(() => new Set());
  const handleRevoke = async (grantId, recipientLabel) => {
    if (revoking.has(grantId)) return;
    if (!window.confirm(`Revoke this share with ${recipientLabel}?`)) return;
    setRevoking((s) => new Set(s).add(grantId));
    try {
      await revokeShare(imageId, grantId);
      toast.success("Share revoked");
      qc.invalidateQueries({ queryKey: ["image-shares", imageId] });
      qc.invalidateQueries({ queryKey: ["incoming-shares"] });
    } catch (err) {
      toast.error(err?.detail || "Could not revoke");
    } finally {
      setRevoking((s) => {
        const next = new Set(s);
        next.delete(grantId);
        return next;
      });
    }
  };

  return (
    <div
      className="share-modal__overlay"
      onClick={onClose}
      data-no-marquee="true"
    >
      <div
        className="share-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="share-modal__head">
          <div className="share-modal__head-text">
            <div className="share-modal__kicker">SHARE</div>
            <div id="share-modal-title" className="share-modal__title">
              {imageName || "this file"}
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close share dialog">
            <Icon name="x" size={15}/>
          </button>
        </header>

        <form onSubmit={handleCreate} className="share-modal__form">
          <label className="share-modal__field">
            <span className="share-modal__label">Recipient email</span>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="them@example.com"
              className="share-modal__input"
            />
          </label>
          <label className="share-modal__field">
            <span className="share-modal__label">Access window</span>
            <select
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              className="share-modal__input share-modal__input--select"
            >
              {DURATION_PRESETS.map((p) => (
                <option key={p.seconds} value={p.seconds}>{p.label}</option>
              ))}
            </select>
            <span className="share-modal__hint">
              Existing accounts get the duration you pick. Recipients without
              an account get exactly <strong>1 day</strong> after they sign up,
              regardless of this setting.
            </span>
          </label>
          <div className="share-modal__actions">
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? "Creating…" : "Create link"}
            </button>
          </div>
        </form>

        {fullShareUrl && (
          <div className="share-modal__issued">
            <div className="share-modal__issued-note">
              Copy this link and send it to <strong>{issuedEmail}</strong>.
            </div>
            <div className="share-modal__issued-row">
              <input
                readOnly
                value={fullShareUrl}
                onFocus={(e) => e.currentTarget.select()}
                className="share-modal__url"
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
                <Icon name="copy" size={12}/> Copy
              </button>
            </div>
          </div>
        )}

        <section className="share-modal__grants">
          <div className="share-modal__grants-head">Active shares</div>
          {isLoading ? (
            <div className="share-modal__grants-empty">Loading…</div>
          ) : grants.length === 0 ? (
            <div className="share-modal__grants-empty">No active shares.</div>
          ) : (
            <ul className="share-modal__grants-list">
              {grants.map((g) => {
                const label = g.recipient_display_name || g.recipient_email;
                const pending = !g.recipient_user_id;
                return (
                  <li key={g.id} className="share-modal__grant">
                    <div className="share-modal__grant-text">
                      <strong className="share-modal__grant-name">{label}</strong>
                      <span className="share-modal__grant-meta">
                        {pending ? "Pending signup · 1 day window starts at signup" : fmtCountdown(g.expires_at)}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => handleRevoke(g.id, label)}
                      disabled={revoking.has(g.id)}
                      title="Revoke this share"
                    >
                      {revoking.has(g.id) ? "Revoking…" : "Revoke"}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
