/**
 * Reset-password landing page.
 *
 * Mounted at `/reset?token=<jwt>` by main.tsx routing. The token in
 * the query string is a fastapi-users password-reset JWT minted by
 * `UserManager.on_after_forgot_password` (15-minute TTL by default).
 *
 * Flow:
 *   1. Page mount reads `?token=` from window.location.search. No
 *      token → render a "this link is invalid or expired" card with
 *      a button back to /auth.
 *   2. User types a new password + confirms it. We enforce the same
 *      strength rules as signup (>=10 chars + upper + digit + symbol)
 *      so reset can't downgrade an account.
 *   3. POST /auth/reset-password with the token + the new password.
 *      Success → toast + redirect to /?auth=signin&reset=ok (the
 *      auth screen reads &reset=ok and shows a success banner).
 *      Failure → toast the server's detail; common cases:
 *        - 400 "RESET_PASSWORD_BAD_TOKEN" — link expired or reused.
 *        - 422 "InvalidPasswordException" — password didn't meet
 *          strength rules (matches the same validator signup uses).
 *
 * Why a dedicated page instead of an auth-screen mode: the URL is
 * a deep-link from the email, the user lands cold with no app
 * shell, and the layout doesn't share the auth screen's two-pane
 * marketing copy. Keeping it separate also means the auth screen
 * doesn't have to deal with hash-vs-query routing of a third
 * external state.
 */
import { useState, useEffect } from "react";
import { toast } from "react-hot-toast";

import { resetPassword } from "@/api/auth";
import { ApiError } from "@/api/client";

function readToken() {
  try {
    const p = new URLSearchParams(window.location.search);
    return p.get("token") || "";
  } catch {
    return "";
  }
}

export function ResetPasswordPage() {
  const [token] = useState(readToken);
  const [pwd, setPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState(null);

  // Strip the token from the URL once we've read it so a refresh
  // doesn't re-arm the same flow and so the JWT doesn't sit in the
  // address bar history. Same pattern auth.jsx uses for SSO hash
  // tokens.
  useEffect(() => {
    if (!token) return;
    try {
      window.history.replaceState({}, "", window.location.pathname);
    } catch {}
  }, [token]);

  // Same password-strength gates the signup form uses (auth.jsx
  // line 233). Keeping the rules identical so reset can never land
  // a password that signup would reject.
  const strong =
    pwd.length >= 10 &&
    /[A-Z]/.test(pwd) &&
    /\d/.test(pwd) &&
    /[^A-Za-z0-9]/.test(pwd);
  const matches = pwd.length > 0 && pwd === confirm;
  const canSubmit = !!token && strong && matches && !submitting;

  const onSubmit = async (e) => {
    e?.preventDefault?.();
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      await resetPassword(token, pwd);
      setDone(true);
      toast.success("Password reset. Sign in with your new password.");
      // Hand off to the auth screen with a flag so it can render a
      // success banner above the form.
      setTimeout(() => {
        window.location.href = "/?reset=ok#auth=signin";
      }, 800);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 400
            ? "This reset link has expired or already been used. Request a new one from the sign-in screen."
            : e.status === 422
              ? "Password is too weak. Use at least 10 characters with an uppercase letter, a digit, and a symbol."
              : e.detail
          : "Reset failed. Check your connection and try again.";
      setError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  if (!token) {
    return (
      <main className="auth auth--center">
        <div className="auth__card" style={{ maxWidth: 440 }}>
          <h1>Reset link missing</h1>
          <p className="auth__sub">
            We couldn&rsquo;t find a reset token in this URL. Reset
            links expire 15 minutes after they&rsquo;re sent; if it&rsquo;s
            been longer than that, request a fresh one from the
            sign-in screen.
          </p>
          <a href="/" className="btn btn--primary" style={{ marginTop: 12 }}>
            Back to sign in
          </a>
        </div>
      </main>
    );
  }

  if (done) {
    return (
      <main className="auth auth--center">
        <div className="auth__card" style={{ maxWidth: 440 }}>
          <h1>Password updated</h1>
          <p className="auth__sub">
            Redirecting you to sign in&hellip;
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="auth auth--center">
      <form className="auth__card" onSubmit={onSubmit} style={{ maxWidth: 440 }}>
        <h1>Set a new password</h1>
        <p className="auth__sub">
          Pick something you haven&rsquo;t used here before. At least
          10 characters with an uppercase letter, a digit, and a
          symbol.
        </p>

        <label className="auth__label">
          New password
          <input
            type="password"
            value={pwd}
            onChange={(e) => setPwd(e.target.value)}
            autoComplete="new-password"
            autoFocus
            required
            minLength={10}
            className="auth__input"
            aria-invalid={pwd.length > 0 && !strong}
          />
        </label>

        <label className="auth__label">
          Confirm new password
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
            className="auth__input"
            aria-invalid={confirm.length > 0 && !matches}
          />
        </label>

        {pwd.length > 0 && !strong && (
          <div className="auth__hint">
            Password needs ≥10 chars, an uppercase letter, a digit,
            and a symbol.
          </div>
        )}
        {pwd.length > 0 && confirm.length > 0 && !matches && (
          <div className="auth__hint auth__hint--warn">
            Passwords don&rsquo;t match.
          </div>
        )}

        {error && (
          <div className="auth__error" role="alert">{error}</div>
        )}

        <button
          type="submit"
          className="btn btn--primary"
          disabled={!canSubmit}
          style={{ marginTop: 12 }}
        >
          {submitting ? "Updating…" : "Update password"}
        </button>
      </form>
    </main>
  );
}
