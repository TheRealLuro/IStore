/**
 * Magic-link sign-in landing page.
 *
 * Mounted at `/signin?token=<jwt>` by main.tsx routing. The token is
 * the JWT-shaped magic-link token minted by /auth/email-link/request
 * and mailed to the user. Page mount:
 *
 *   1. Read `?token=` from window.location.search.
 *   2. Strip from URL via history.replaceState (defense-in-depth; the
 *      backend already enforces single-use via Redis SET NX).
 *   3. POST /auth/email-link/consume.
 *   4. Success → setUser + redirect to /.
 *   5. TOTP-required (user has 2FA on) → redirect to / with a hash
 *      flag the AuthScreen reads to drop straight into the TOTP step
 *      with the email pre-filled.
 *   6. Expired / already-consumed → show a card with a "Send a fresh
 *      link" form that re-fires requestSigninLink.
 *   7. Missing token → same card.
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
  const [status, setStatus] = useState(token ? "pending" : "missing");
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

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const user = await consumeSigninLink(token);
        if (cancelled) return;
        setUser(user);
        toast.success("Signed in.");
        setTimeout(() => {
          window.location.href = "/";
        }, 800);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof TotpRequiredError) {
          // User has 2FA on. We can't complete here — they need to
          // enter their authenticator code through the normal flow.
          // Route to the gallery; the AuthScreen redirects them to
          // a totp-pre-filled state. (Implementation: pass the email
          // through a hash fragment that the AuthScreen reads on
          // mount.)
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
            "This sign-in link is expired or already used. " +
            "Sign-in links work once and are good for 15 minutes. " +
            "Request a fresh one below.",
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
    })();
    return () => {
      cancelled = true;
    };
  }, [token, setUser]);

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

  if (status === "missing") {
    return (
      <main className="auth auth--center">
        <div className="auth__card" style={{ maxWidth: 440 }}>
          <h1>Sign-in link missing</h1>
          <p className="auth__sub">
            We couldn&rsquo;t find a sign-in token in this URL. Sign-in
            links are good for 15 minutes after they&rsquo;re sent; if
            it&rsquo;s been longer, request a fresh one from the form
            below.
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
          <h1>Signing you in&hellip;</h1>
          <p className="auth__sub">One moment.</p>
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
