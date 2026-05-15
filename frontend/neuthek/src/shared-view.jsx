// Public landing for /share/{token} — todo §1.1 / G1.
//
// Three states drive the UI:
//   1. No JWT in localStorage → call previewShare() (unauth) and show
//      a "sign up to view" card. Sign-up button hands off to /auth?next=...
//      with the token preserved so the post-signup callback claims it.
//   2. JWT present → call claimShare() to bind the grant to the calling
//      user (or refresh claimed_at if already bound) and render the
//      stripped-down asset viewer.
//   3. Wrong-recipient or revoked / expired → show a generic "link
//      not available" message; the backend returns 404 for every
//      mismatch path so we don't leak which step failed.
import React, { useEffect, useState } from "react";
import { tokens } from "@/api/client";
import { claimShare, extractEmailFromHash, previewShare } from "@/api/shares";
import { SharedAssetView } from "./shared-asset-view.jsx";
import { useAuthStore } from "@/stores/authStore";

export function SharedView({ token }) {
  const user = useAuthStore((s) => s.user);
  const authLoading = useAuthStore((s) => s.loading);
  const bootstrap = useAuthStore((s) => s.bootstrap);

  const [preview, setPreview] = useState(null);
  const [claim, setClaim] = useState(null);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("loading"); // loading | needs_signup | viewing | error

  useEffect(() => { bootstrap(); }, [bootstrap]);

  // Preserve the email from the URL fragment so the unauthenticated
  // preview lookup has something to scope the argon2-verify against.
  // The email never reaches the server in #email=…, only the
  // explicit `previewShare(token, email)` call sends it.
  const emailFromHash = extractEmailFromHash(window.location.hash);

  useEffect(() => {
    if (authLoading) return;
    let cancelled = false;
    (async () => {
      const hasToken = !!tokens.get();
      if (hasToken && user) {
        // Logged in — try to claim. If it works, we're a recipient.
        // Fetch the public preview metadata in parallel so the
        // viewer header has the filename + sharer name without a
        // second round trip. Email comes from the URL fragment if
        // the sharer included it; if not we can't run preview (it
        // requires email pinning), and the viewer falls back to
        // generic copy.
        try {
          const [c, p] = await Promise.all([
            claimShare(token),
            emailFromHash
              ? previewShare(token, emailFromHash).catch(() => null)
              : Promise.resolve(null),
          ]);
          if (cancelled) return;
          setClaim(c);
          if (p) setPreview(p);
          setPhase("viewing");
        } catch (e) {
          // Claim failed — fall back to the public preview so we can
          // tell the user "this link isn't for this account" without
          // confusing them with a raw 404.
          if (!emailFromHash) {
            setError("This share link isn't for this account.");
            setPhase("error");
            return;
          }
          try {
            const p = await previewShare(token, emailFromHash);
            if (cancelled) return;
            setPreview(p);
            setPhase("needs_signup");
          } catch {
            setError("Invalid or expired share link.");
            setPhase("error");
          }
        }
        return;
      }
      // Not logged in — show the public landing.
      if (!emailFromHash) {
        setError("This share link is missing required context. Ask the sender to resend it.");
        setPhase("error");
        return;
      }
      try {
        const p = await previewShare(token, emailFromHash);
        if (cancelled) return;
        setPreview(p);
        setPhase("needs_signup");
      } catch {
        setError("Invalid or expired share link.");
        setPhase("error");
      }
    })();
    return () => { cancelled = true; };
  }, [authLoading, user, token, emailFromHash]);

  // Build the auth-screen handoff URL with the share path preserved.
  // The auth screen reads ?next= and bounces back here after signup.
  const authNextUrl = (() => {
    const next = `/share/${token}${window.location.hash || ""}`;
    return `/?next=${encodeURIComponent(next)}#auth=signup`;
  })();

  if (phase === "loading" || authLoading) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", color: "var(--ink-3)" }}>
        Loading shared file…
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <div
          style={{
            background: "var(--surface, #fff)",
            color: "var(--ink, #111)",
            padding: 24,
            borderRadius: 10,
            maxWidth: 420,
            border: "1px solid var(--border, #e5e5e5)",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 6 }}>
            Share unavailable
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.5 }}>
            {error}
          </div>
        </div>
      </div>
    );
  }

  if (phase === "viewing") {
    return (
      <SharedAssetView
        shareId={claim.share_id}
        claim={{
          ...claim,
          image_filename: preview?.image_filename,
          image_category: preview?.image_category || "image",
        }}
        sharerName={preview?.sharer_display_name}
      />
    );
  }

  // needs_signup — public landing card.
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16, background: "var(--surface-2, #f7f7f7)" }}>
      <div
        style={{
          background: "var(--surface, #fff)",
          color: "var(--ink, #111)",
          padding: 28,
          borderRadius: 12,
          maxWidth: 460,
          border: "1px solid var(--border, #e5e5e5)",
          boxShadow: "0 4px 20px rgba(0,0,0,0.05)",
        }}
      >
        <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--ink-3)" }}>
          Shared via neuthek
        </div>
        <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4 }}>
          {preview?.sharer_display_name || "Someone"} shared a file with you
        </div>
        <div style={{ fontSize: 14, color: "var(--ink-2)", marginTop: 10 }}>
          <strong>{preview?.image_filename || "shared file"}</strong>
        </div>
        <div style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 12, lineHeight: 1.5 }}>
          To view it, create a free neuthek account using the email this
          link was sent to. New accounts get <strong>1 day</strong> of
          access to this file. Existing accounts use the duration the
          sender chose.
        </div>
        <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
          <a
            href={authNextUrl}
            className="btn btn--primary"
            style={{ flex: 1, textAlign: "center", textDecoration: "none" }}
          >
            Sign up free
          </a>
          <a
            href={authNextUrl.replace("#auth=signup", "#auth=signin")}
            className="btn btn--ghost"
            style={{ flex: 1, textAlign: "center", textDecoration: "none" }}
          >
            I already have an account
          </a>
        </div>
        <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 14, textAlign: "center" }}>
          neuthek never shares files with anyone except the email the
          sender chose.
        </div>
      </div>
    </div>
  );
}
