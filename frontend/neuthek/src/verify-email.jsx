/**
 * Email-verification landing page.
 *
 * Mounted at `/verify?token=<jwt>` by main.tsx routing. The token in
 * the query string is a fastapi-users verify-email JWT minted by
 * `UserManager.on_after_register` → `request_verify` → mailed via
 * `send_verify_email` (default 1-hour TTL).
 *
 * Flow:
 *   1. Page mount reads `?token=` from window.location.search.
 *   2. Fires POST /auth/verify with the token. The server flips
 *      `users.is_verified = true` on the matching row and returns
 *      the updated user payload.
 *   3. Success → show a "verified" card + auto-redirect to /
 *      (gallery) after 1.5 s. The gallery's VerifyBanner reads
 *      `is_verified` on next refetch and hides itself.
 *   4. Failure modes:
 *        - 400 VERIFY_USER_BAD_TOKEN — link expired (>1 h since
 *          mint) or already consumed. Show a card with a "Send a
 *          fresh link" button that calls /auth/request-verify-
 *          token.
 *        - 400 VERIFY_USER_ALREADY_VERIFIED — the row is already
 *          is_verified=true. Treat as success (the user clicked
 *          a stale email after already verifying).
 *
 * Token-from-URL is stripped on mount (history.replaceState) so a
 * refresh doesn't replay the POST and so the JWT doesn't sit in
 * the address bar.
 */
import { useEffect, useState } from "react";
import { toast } from "react-hot-toast";

import { verifyEmail, requestVerify } from "@/api/auth";
import { ApiError } from "@/api/client";

function readToken() {
  try {
    const p = new URLSearchParams(window.location.search);
    return p.get("token") || "";
  } catch {
    return "";
  }
}

export function VerifyEmailPage() {
  const [token] = useState(readToken);
  // "pending" while the POST is in flight; "ok" / "expired" /
  // "missing" / "error" once a terminal state is reached.
  const [status, setStatus] = useState(token ? "pending" : "missing");
  const [errorMessage, setErrorMessage] = useState(null);
  const [resendEmail, setResendEmail] = useState("");
  const [resending, setResending] = useState(false);

  // Strip the token from the URL once we've read it. Same pattern
  // the reset-password page uses — keeps the JWT out of browser
  // history and prevents a refresh from re-submitting.
  useEffect(() => {
    if (!token) return;
    try {
      window.history.replaceState({}, "", window.location.pathname);
    } catch {}
  }, [token]);

  // Fire the verify POST exactly once on mount.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        await verifyEmail(token);
        if (cancelled) return;
        setStatus("ok");
        // Hand off to the gallery; on next /users/me fetch the
        // VerifyBanner sees is_verified=true and hides.
        toast.success("Email verified.");
        setTimeout(() => {
          window.location.href = "/";
        }, 1500);
      } catch (e) {
        if (cancelled) return;
        // VERIFY_USER_ALREADY_VERIFIED is a 400 too in fastapi-
        // users; treat it as success so a user clicking the link
        // twice doesn't see a confusing error.
        const detail = e instanceof ApiError ? (e.detail || "") : "";
        const detailStr = typeof detail === "string"
          ? detail
          : (detail?.code || JSON.stringify(detail));
        if (
          e instanceof ApiError &&
          /already.verified/i.test(detailStr)
        ) {
          setStatus("ok");
          setTimeout(() => {
            window.location.href = "/";
          }, 1500);
          return;
        }
        if (
          e instanceof ApiError &&
          (e.status === 400 || e.status === 422)
        ) {
          setStatus("expired");
          setErrorMessage(
            "This verification link is expired or already used. " +
            "Verification links are good for one hour. Request a " +
            "fresh one below.",
          );
        } else {
          setStatus("error");
          setErrorMessage(
            e instanceof ApiError
              ? (typeof e.detail === "string"
                ? e.detail
                : "Verification failed. Try again.")
              : "Couldn't reach the server. Check your connection.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const onResend = async (e) => {
    e?.preventDefault?.();
    const trimmed = resendEmail.trim();
    if (!trimmed || resending) return;
    setResending(true);
    try {
      await requestVerify(trimmed);
    } catch {
      // Anti-enumeration: the server returns 202 either way, but
      // a network error here is also possible. Either way the
      // toast is generic.
    }
    setResending(false);
    setResendEmail("");
    toast.success(
      "If an account uses that email, a fresh verification link " +
      "is on the way. Check your inbox (and spam folder).",
    );
  };

  if (status === "missing") {
    return (
      <main className="auth auth--center">
        <div className="auth__card" style={{ maxWidth: 440 }}>
          <h1>Verification link missing</h1>
          <p className="auth__sub">
            We couldn&rsquo;t find a verification token in this URL.
            Verification links are good for one hour after they&rsquo;re
            sent; if it&rsquo;s been longer, request a fresh one from the
            form below.
          </p>
          <form onSubmit={onResend} style={{ marginTop: 16 }}>
            <label className="auth__label">
              Email
              <input
                type="email"
                value={resendEmail}
                onChange={(e) => setResendEmail(e.target.value)}
                autoComplete="email"
                required
                className="auth__input"
              />
            </label>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={
                resending ||
                !resendEmail.trim() ||
                !/^\S+@\S+\.\S+$/.test(resendEmail.trim())
              }
              style={{ marginTop: 12 }}
            >
              {resending ? "Sending…" : "Send a fresh link"}
            </button>
          </form>
        </div>
      </main>
    );
  }

  if (status === "pending") {
    return (
      <main className="auth auth--center">
        <div className="auth__card" style={{ maxWidth: 440 }}>
          <h1>Verifying your email&hellip;</h1>
          <p className="auth__sub">One moment.</p>
        </div>
      </main>
    );
  }

  if (status === "ok") {
    return (
      <main className="auth auth--center">
        <div className="auth__card" style={{ maxWidth: 440 }}>
          <h1>Email verified</h1>
          <p className="auth__sub">
            Thanks. Redirecting you to your library&hellip;
          </p>
        </div>
      </main>
    );
  }

  // expired / error — both show the resend form.
  return (
    <main className="auth auth--center">
      <div className="auth__card" style={{ maxWidth: 440 }}>
        <h1>
          {status === "expired"
            ? "Link expired"
            : "Couldn't verify"}
        </h1>
        {errorMessage && (
          <p className="auth__sub">{errorMessage}</p>
        )}
        <form onSubmit={onResend} style={{ marginTop: 16 }}>
          <label className="auth__label">
            Email
            <input
              type="email"
              value={resendEmail}
              onChange={(e) => setResendEmail(e.target.value)}
              autoComplete="email"
              required
              className="auth__input"
            />
          </label>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={
              resending ||
              !resendEmail.trim() ||
              !/^\S+@\S+\.\S+$/.test(resendEmail.trim())
            }
            style={{ marginTop: 12 }}
          >
            {resending ? "Sending…" : "Send a fresh link"}
          </button>
        </form>
      </div>
    </main>
  );
}
