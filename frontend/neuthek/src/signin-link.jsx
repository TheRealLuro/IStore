/**
 * Magic-link sign-in landing page.
 *
 * Mounted at `/signin?token=<jwt>` by main.tsx routing. The token is
 * the JWT-shaped magic-link token minted by /auth/email-link/request
 * and mailed to the user.
 *
 * Flow:
 *   1. On mount: read `?token=` from window.location.search. Strip
 *      it from the URL via history.replaceState so a refresh or a
 *      screen-share doesn't leak it.
 *   2. Render a "Sign in to neuthek" confirmation button. DO NOT
 *      auto-consume the token. Gmail (and other antivirus / link-
 *      preview services) prefetch URLs in emails — a GET to /signin
 *      would otherwise trigger our auto-POST to /auth/email-link/
 *      consume, marking the jti consumed before the actual user
 *      clicks. When they then click for real they'd see "Link
 *      expired or already used", which is exactly the bug the user
 *      reported. Requiring an explicit user click breaks the
 *      prefetch path because email scanners don't click buttons.
 *   3. On user click: POST /auth/email-link/consume, persist the
 *      JWT, redirect to /.
 *   4. TOTP-required (user has 2FA on) → redirect to / with a hash
 *      flag the AuthScreen reads to drop straight into the TOTP
 *      step with the email pre-filled.
 *   5. Expired / already-consumed → show a card with a "Send a
 *      fresh link" form.
 *   6. Missing token → same fresh-link card.
 */
import { useEffect, useState } from "react";
import { toast } from "react-hot-toast";

import { consumeSigninLink, requestSigninLink, TotpRequiredError } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useAuthStore } from "@/stores/authStore";

function readToken() {
  try {
    const p = new URLSearchParams(window.location.search);
    return p.get("token") || "";
  } catch {
    return "";
  }
}

export function SigninLinkPage() {
  const [token] = useState(readToken);
  // 'ready'   — token is present, waiting for user-click confirm
  // 'pending' — consume in flight
  // 'ok'      — signed in, redirecting
  // 'missing' — no token in URL, show fresh-link request form
  // 'expired' — consume returned 4xx
  // 'error'   — network / unexpected failure
  const [status, setStatus] = useState(token ? "ready" : "missing");
  const [errorMessage, setErrorMessage] = useState(null);
  const [resendEmail, setResendEmail] = useState("");
  const [resending, setResending] = useState(false);
  const setUser = useAuthStore((s) => s.setUser);

  // Strip the token from the URL the moment we read it. Same pattern
  // as /verify and /reset — keeps the JWT out of browser history and
  // out of the address bar for screen-share scenarios.
  useEffect(() => {
    if (!token) return;
    try {
      window.history.replaceState({}, "", window.location.pathname);
    } catch {}
  }, [token]);

  const doSignIn = async () => {
    if (!token || status === "pending") return;
    setStatus("pending");
    try {
      const user = await consumeSigninLink(token);
      setUser(user);
      setStatus("ok");
      toast.success("Signed in.");
      setTimeout(() => {
        window.location.href = "/";
      }, 600);
    } catch (e) {
      if (e instanceof TotpRequiredError) {
        // User has 2FA on. We can't complete here — route to the
        // gallery; the AuthScreen will pick up the totp_after_link
        // hash and drop into the TOTP-required step pre-filled with
        // the email.
        const params = new URLSearchParams();
        params.set("totp_email", e.email || "");
        window.location.href = "/#totp_after_link?" + params.toString();
        return;
      }
      if (
        e instanceof ApiError &&
        (e.status === 400 || e.status === 422)
      ) {
        setStatus("expired");
        setErrorMessage(
          "This sign-in link is expired or already used. Sign-in " +
          "links work once and are good for 15 minutes. Request a " +
          "fresh one below.",
        );
      } else {
        setStatus("error");
        setErrorMessage(
          e instanceof ApiError
            ? (typeof e.detail === "string"
              ? e.detail
              : "Sign-in failed. Try again.")
            : "Couldn't reach the server. Check your connection.",
        );
      }
    }
  };

  const onResend = async (e) => {
    e?.preventDefault?.();
    const trimmed = resendEmail.trim();
    if (!trimmed || resending) return;
    setResending(true);
    try {
      await requestSigninLink(trimmed);
    } catch {
      // Anti-enumeration: server returns 202 either way.
    }
    setResending(false);
    setResendEmail("");
    toast.success(
      "If an account uses that email, a fresh sign-in link is " +
      "on the way. Check your inbox (and spam folder).",
    );
  };

  // ---------- render branches ----------

  if (status === "missing") {
    return (
      <main className="auth auth--center">
        <div className="auth__card">
          <h1>Sign-in link missing</h1>
          <p className="auth__sub">
            We couldn&rsquo;t find a sign-in token in this URL. Sign-in
            links are good for 15 minutes after they&rsquo;re sent;
            if it&rsquo;s been longer, request a fresh one from the
            form below.
          </p>
          <form onSubmit={onResend} style={{ marginTop: 16 }}>
            <label className="auth__label">Email</label>
            <input
              type="email"
              value={resendEmail}
              onChange={(e) => setResendEmail(e.target.value)}
              autoComplete="email"
              required
              className="auth__input"
            />
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

  if (status === "ready") {
    // Token is in hand but NOT consumed yet. The user must click the
    // button to actually sign in. This is the defense against Gmail
    // / link-preview prefetchers which would otherwise burn the
    // single-use token before the user gets a chance.
    return (
      <main className="auth auth--center">
        <div className="auth__card">
          <h1>Sign in to neuthek</h1>
          <p className="auth__sub">
            You&rsquo;re one click away. Press the button below to
            finish signing in.
          </p>
          <button
            type="button"
            className="btn btn--primary btn--lg"
            onClick={doSignIn}
            style={{ marginTop: 16, width: "100%" }}
          >
            Sign in to neuthek
          </button>
          <p style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 12, lineHeight: 1.6 }}>
            Why a button? Email security scanners visit links in
            messages to check for malware. Requiring a click means
            your sign-in link can&rsquo;t be silently consumed by
            those scanners before you use it.
          </p>
        </div>
      </main>
    );
  }

  if (status === "pending") {
    return (
      <main className="auth auth--center">
        <div className="auth__card">
          <h1>Signing you in&hellip;</h1>
          <p className="auth__sub">One moment.</p>
        </div>
      </main>
    );
  }

  if (status === "ok") {
    return (
      <main className="auth auth--center">
        <div className="auth__card">
          <h1>Signed in</h1>
          <p className="auth__sub">
            Taking you to your library&hellip;
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
            : "Couldn't sign you in"}
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
