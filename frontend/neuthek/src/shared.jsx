// Shared tab — files the recipient has received from other users.
//
// Replaces the previous "open in its own tab" UX where /share/{token}
// rendered a full-page standalone viewer. Now, signed-in recipients
// land in their own app's Shared tab and the file opens in a modal
// preview without leaving the gallery.
//
// Where the data comes from:
//   GET /shares/incoming      → IncomingShare[] (metadata; no bytes)
//   GET /shares/{id}/asset    → { url, expires_at } for the
//                               served (or original) variant. Stripped
//                               of auth — the URL is a 5-minute HMAC
//                               signed against the share id.
//
// The shared bytes never live in the recipient's `images` table, so
// we can't reuse the owner gallery's <FileCard/>. This file ships a
// small alternative card grid + preview that knows it's looking at
// borrowed bytes.
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import { getShareAsset, listIncomingShares } from "@/api/shares";

function fmtCountdown(iso) {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const d = Math.floor(ms / 86400000);
  if (d >= 1) return `${d}d left`;
  const h = Math.floor(ms / 3600000);
  if (h >= 1) return `${h}h left`;
  const m = Math.max(1, Math.floor(ms / 60000));
  return `${m}m left`;
}

// One signed-URL fetch per shared item, cached for 4 minutes. The
// backend mints URLs with a 5-minute TTL so we re-fetch comfortably
// inside that window. React Query handles dedup if many cards mount
// at once.
function useSharedAssetUrl(shareId, variant = "served") {
  return useQuery({
    queryKey: ["shared-asset", shareId, variant],
    queryFn: () => getShareAsset(shareId, variant),
    enabled: !!shareId,
    staleTime: 4 * 60 * 1000,
  });
}

function SharedCard({ share, selected, onClick }) {
  const { data: asset } = useSharedAssetUrl(share.share_id, "served");
  const isImage = (share.image_category || "image") === "image";
  const filename = share.image_filename || "shared file";
  const ext = (filename.split(".").pop() || "FILE").toUpperCase();
  const sharer = share.sharer_display_name || share.sharer_email;
  const countdown = fmtCountdown(share.expires_at);
  return (
    <div
      className="card"
      data-selected={selected}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); }
      }}
      style={{ cursor: "pointer" }}
      title={`${filename} — from ${sharer}`}
    >
      <div
        className={"card__thumb" + (isImage && asset?.url ? "" : " card__thumb--doc")}
        style={{
          background: "var(--surface-2)",
          backgroundImage: isImage && asset?.url ? `url("${asset.url}")` : undefined,
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      >
        {!isImage && (
          <div className="thumb-icon">
            <Icon
              name={share.image_category === "document" ? "document" : share.image_category === "video" ? "video" : "file"}
              size={32}
              strokeWidth={1.3}
            />
            <span className="mono" style={{ fontSize: 11 }}>{ext}</span>
          </div>
        )}
        <div
          className="card__type"
          style={{
            position: "absolute", top: 8, right: 8,
            padding: "3px 8px", borderRadius: 4,
            background: "var(--ink)", color: "var(--surface)",
            fontSize: 10, fontWeight: 600, letterSpacing: "0.04em",
          }}
        >
          SHARED
        </div>
      </div>
      <div className="card__body">
        <div className="card__title" style={{ color: "var(--ink)" }}>{filename}</div>
        <div
          className="card__meta"
          style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--ink-3)" }}
        >
          <span>from {sharer}</span>
          {countdown && (
            <>
              <span aria-hidden>·</span>
              <span style={{ color: countdown === "expired" ? "var(--danger)" : "var(--ink-3)" }}>
                {countdown}
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// Empty state for the Shared tab. Used to be a single dashed-border
// "nothing here" card centered in a wall of blank space — accurate
// but felt like a dead page. This version turns the blank canvas into
// an explainer: what the tab IS, three concrete steps for getting
// files into it, and a soft prompt back to the user's own gallery so
// they always have somewhere to click.
function SharedEmptyState() {
  const steps = [
    {
      icon: "share",
      title: "Someone sends you a link",
      body:
        "Anyone with a neuthek account can share a single file or a folder " +
        "with you. The link works whether or not you have an account yourself.",
    },
    {
      icon: "user",
      title: "Open it while signed in",
      body:
        "When you click a share link while signed in to neuthek, it lands " +
        "in this tab instead of opening its own preview page. Easier to keep " +
        "track of everything you've been sent.",
    },
    {
      icon: "lock",
      title: "Access lasts as long as the sender chose",
      body:
        "Most shares stay accessible for 1 day. The sender can extend that " +
        "or revoke access at any time — when they do, the file disappears " +
        "from this tab automatically.",
    },
  ];
  return (
    <div className="shared-empty" data-no-marquee="true">
      {/* Hero — quiet glyph + headline. Less alarming than the
          previous "No shares yet" notice; reads more like an
          intentional landing. */}
      <div className="shared-empty__hero">
        <div className="shared-empty__glyph" aria-hidden="true">
          <Icon name="share" size={28} strokeWidth={1.4} />
        </div>
        <div className="shared-empty__heading">
          <h3 className="shared-empty__title">No shares yet — that's expected</h3>
          <p className="shared-empty__lead">
            This is where anything someone shares with you will land.
            Nothing's missing — your inbox is just empty.
          </p>
        </div>
      </div>

      {/* Three-step grid. The cards are static — there's nothing to
          click here because the user can't "make a share appear."
          The point is reassurance, not action. */}
      <ul className="shared-empty__steps">
        {steps.map((s) => (
          <li key={s.title} className="shared-empty__step">
            <span className="shared-empty__step-icon" aria-hidden="true">
              <Icon name={s.icon} size={16} strokeWidth={1.5} />
            </span>
            <div className="shared-empty__step-title">{s.title}</div>
            <div className="shared-empty__step-body">{s.body}</div>
          </li>
        ))}
      </ul>

      {/* Soft footer note — a single line that closes the loop:
          if you want to share something OUT, here's where to go. */}
      <div className="shared-empty__footer">
        Want to share one of your own files? Open it from{" "}
        <strong>All files</strong>, then click <strong>Share</strong> in the
        details panel.
      </div>
    </div>
  );
}

export function SharedGalleryView({ initialShareId, onClearShareParam }) {
  const { data: shares = [], isLoading } = useQuery({
    queryKey: ["incoming-shares"],
    queryFn: listIncomingShares,
    staleTime: 30_000,
  });
  const [selectedId, setSelectedId] = React.useState(initialShareId || null);

  // When ?share=<id> arrives via redirect, open it once shares load.
  React.useEffect(() => {
    if (!initialShareId || !shares.length) return;
    const match = shares.find((s) => s.share_id === initialShareId);
    if (match) setSelectedId(match.share_id);
    onClearShareParam && onClearShareParam();
  }, [initialShareId, shares, onClearShareParam]);

  const selected = shares.find((s) => s.share_id === selectedId) || null;

  return (
    <div style={{ padding: 0 }}>
      {/* No inner header — the app topbar already shows "Shared /
          N items" above this view, and the empty state below has
          its own heading. The previous "Shared with you" h2 + long
          paragraph + bottom border was duplicating the page title
          and crowding the content area. */}
      {isLoading ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--ink-3)", fontSize: 13 }}>
          Loading shares…
        </div>
      ) : shares.length === 0 ? (
        <SharedEmptyState />
      ) : (
        <div
          className="cards"
          style={{
            display: "grid",
            gap: 16,
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          }}
        >
          {shares.map((s) => (
            <SharedCard
              key={s.share_id}
              share={s}
              selected={selectedId === s.share_id}
              onClick={() => setSelectedId(s.share_id)}
            />
          ))}
        </div>
      )}

      {selected && (
        <SharedItemPreview share={selected} onClose={() => setSelectedId(null)} />
      )}
    </div>
  );
}

// Modal preview rendered inside the recipient's app — no full-page
// takeover. Reuses the same signed-URL pattern as SharedAssetView
// (used by the legacy public viewer) but in modal form so the
// surrounding gallery stays visible.
export function SharedItemPreview({ share, onClose }) {
  const [blob, setBlob] = React.useState(null);
  const [error, setError] = React.useState("");
  const [downloading, setDownloading] = React.useState(false);
  const filename = share.image_filename || "shared file";
  const isImage = (share.image_category || "image") === "image";
  const isPdf = filename.toLowerCase().endsWith(".pdf");
  const sharer = share.sharer_display_name || share.sharer_email;
  const countdown = fmtCountdown(share.expires_at);

  React.useEffect(() => {
    let cancelled = false;
    let owned = null;
    (async () => {
      try {
        const signed = await getShareAsset(share.share_id, "served");
        const res = await fetch(signed.url);
        if (!res.ok) throw new Error("Could not load asset");
        const data = await res.blob();
        if (cancelled) return;
        owned = URL.createObjectURL(data);
        setBlob(owned);
      } catch (e) {
        if (!cancelled) setError(e?.detail || e?.message || "Could not load asset");
      }
    })();
    return () => {
      cancelled = true;
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [share.share_id]);

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      const signed = await getShareAsset(share.share_id, "original");
      const res = await fetch(signed.url);
      if (!res.ok) throw new Error("Download failed");
      const data = await res.blob();
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (e) {
      setError(e?.detail || e?.message || "Download failed");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0,
          background: "rgba(0,0,0,0.55)", backdropFilter: "blur(2px)",
          zIndex: 60,
          animation: "fadein 160ms ease",
        }}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Shared ${filename}`}
        style={{
          position: "fixed",
          top: "50%", left: "50%", transform: "translate(-50%, -50%)",
          width: "min(960px, 94vw)",
          maxHeight: "92vh",
          background: "var(--surface)",
          color: "var(--ink)",
          borderRadius: 16,
          border: "1px solid var(--line)",
          boxShadow: "var(--shadow-3)",
          zIndex: 61,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <header
          style={{
            padding: "14px 18px",
            borderBottom: "1px solid var(--line)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
            <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ink-3)" }}>
              Shared by {sharer}
            </div>
            <div
              style={{
                fontSize: 16,
                fontWeight: 600,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={filename}
            >
              {filename}
            </div>
            {countdown && (
              <div style={{ fontSize: 12, color: countdown === "expired" ? "var(--danger)" : "var(--ink-3)" }}>
                {countdown}
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              onClick={handleDownload}
              disabled={downloading}
              className="btn btn--primary"
              style={{ display: "flex", gap: 6, alignItems: "center" }}
            >
              <Icon name="download" size={14}/>
              {downloading ? "Preparing…" : "Download"}
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="btn-icon"
            >
              <Icon name="x" size={16}/>
            </button>
          </div>
        </header>
        <main
          style={{
            flex: 1,
            display: "grid",
            placeItems: "center",
            padding: 24,
            background: "var(--surface-2)",
            overflow: "auto",
          }}
        >
          {error ? (
            <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>
          ) : !blob ? (
            <div style={{ color: "var(--ink-3)", fontSize: 13 }}>Loading shared file…</div>
          ) : isImage ? (
            <img
              src={blob}
              alt={filename}
              style={{
                maxWidth: "100%",
                maxHeight: "72vh",
                borderRadius: 8,
                boxShadow: "var(--shadow-2)",
              }}
            />
          ) : isPdf ? (
            <iframe
              src={blob}
              title={filename}
              style={{
                width: "100%",
                height: "72vh",
                border: 0,
                borderRadius: 8,
                background: "#fff",
              }}
            />
          ) : (
            <div style={{ color: "var(--ink-3)", fontSize: 13 }}>
              File ready — use the Download button above.
            </div>
          )}
        </main>
      </div>
      <style>{`@keyframes fadein { from { opacity: 0; } to { opacity: 1; } }`}</style>
    </>
  );
}
