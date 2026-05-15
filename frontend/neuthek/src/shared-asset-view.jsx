// Minimal viewer for a shared asset.
//
// No sidebar, no nav, no other-library exposure — just the file
// the recipient was invited to. Bytes come from
// /shares/{share_id}/asset which mints a fresh 5-minute HMAC URL
// each call (signed against share_id, not user_id, so the recipient
// can fetch even though they're not the owner).
import React, { useEffect, useState } from "react";
import { Icon } from "./icons.jsx";
import { getShareAsset } from "@/api/shares";

function fmtCountdown(iso) {
  if (!iso) return "no expiry";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const d = Math.floor(ms / 86400000);
  if (d >= 1) return `${d} day${d === 1 ? "" : "s"} remaining`;
  const h = Math.floor(ms / 3600000);
  if (h >= 1) return `${h} hour${h === 1 ? "" : "s"} remaining`;
  const m = Math.max(1, Math.floor(ms / 60000));
  return `${m} min remaining`;
}

export function SharedAssetView({ shareId, claim, sharerName }) {
  const [blob, setBlob] = useState(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const filename = claim?.image_filename || "shared file";
  const isImage = (claim?.image_category || "image") === "image";
  const isPdf = (filename.toLowerCase().endsWith(".pdf"));

  useEffect(() => {
    if (!shareId) return;
    let cancelled = false;
    let owned = null;
    (async () => {
      try {
        const signed = await getShareAsset(shareId, "served");
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
  }, [shareId]);

  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      const signed = await getShareAsset(shareId, "original");
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
    <div
      style={{
        minHeight: "100vh",
        background: "var(--surface-2, #f7f7f7)",
        color: "var(--ink, #111)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <header
        style={{
          padding: "14px 24px",
          borderBottom: "1px solid var(--border, #e5e5e5)",
          background: "var(--surface, #fff)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div style={{ display: "grid", gap: 2 }}>
          <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--ink-3)" }}>
            Shared with you{sharerName ? ` by ${sharerName}` : ""}
          </div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{filename}</div>
          <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
            {fmtCountdown(claim?.expires_at)}
          </div>
        </div>
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
      </header>

      <main
        style={{
          flex: 1,
          display: "grid",
          placeItems: "center",
          padding: 24,
        }}
      >
        {error ? (
          <div style={{ color: "var(--danger, #c0392b)", fontSize: 13 }}>{error}</div>
        ) : !blob ? (
          <div style={{ color: "var(--ink-3)", fontSize: 13 }}>Loading shared file…</div>
        ) : isImage ? (
          <img
            src={blob}
            alt={filename}
            style={{ maxWidth: "100%", maxHeight: "84vh", borderRadius: 8, boxShadow: "0 6px 22px rgba(0,0,0,0.12)" }}
          />
        ) : isPdf ? (
          <iframe
            src={blob}
            title={filename}
            style={{ width: "100%", height: "84vh", border: 0, borderRadius: 8, background: "#fff" }}
          />
        ) : (
          <div style={{ color: "var(--ink-3)", fontSize: 13 }}>
            File ready — use the Download button above.
          </div>
        )}
      </main>
    </div>
  );
}
