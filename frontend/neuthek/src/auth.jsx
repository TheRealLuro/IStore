// Sign-in / sign-up split screen.
// Sign-up flow: user fills the form -> "Create account" opens the
// consolidated consents popup -> they sign once -> account is created.
//
// Wired to the real backend: `login` and `register` from src/api/auth.ts.
// The consents flow still collects the user's choices client-side; we'll
// persist them via /consent/{kind}/grant in a follow-up pass.
import React, { useState as useStateA, useEffect as useEffectA } from "react";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import NeuthekMark from "./NeuthekMark.jsx";
import { TermsModal as TermsModalA, PrivacyModal as PrivacyModalA } from "./policies.jsx";
import { ConsentsModal } from "./consents.jsx";
import { Modal as PrimitiveModal } from "./primitives.jsx";
import {
  forgotPassword,
  login,
  loginWithTotp,
  me,
  register,
  TotpRequiredError,
} from "@/api/auth";
import { useAuthStore } from "@/stores/authStore";
import { ApiError, API_BASE_URL, tokens } from "@/api/client";
// §B2 — grantScope no longer needed at signup: the register call
// carries the consent bundle. Settings panels still use it for
// post-signup edits via `@/api/consent`.

function PasswordReqs({ value }) {
  const reqs = [
    { id: "len", label: "10+ characters", met: value.length >= 10 },
    { id: "upper", label: "Uppercase", met: /[A-Z]/.test(value) },
    { id: "num", label: "Number", met: /\d/.test(value) },
    { id: "sym", label: "Symbol", met: /[^A-Za-z0-9]/.test(value) },
  ];
  return (
    <div className="password-reqs password-reqs--inline">
      {reqs.map(r => (
        <div className="password-reqs__row" data-met={r.met} key={r.id}>
          <span className="password-reqs__check"><Icon name="check" size={9} strokeWidth={3}/></span>
          {r.label}
        </div>
      ))}
    </div>
  );
}

// Brand-side feature loops on a 3.5s rotation
const BRAND_FEATURES = [
  { icon: "image",      label: "Photos & video",      sub: "Auto-organized by people, place, and moment" },
  { icon: "sparkles",   label: "Search by meaning",   sub: "“Birthday party last summer” actually works" },
  { icon: "shield",     label: "End-to-end encrypted", sub: "Your files, your keys. Nothing leaks to ads" },
  { icon: "users",      label: "Faces stay private",  sub: "Templates live only on your box. Optional" },
];

// Whitelist filter for the post-auth `?next=` redirect target.
//
// Threat model: a phishing link like
//   https://neuthek.app/?next=https://evil.example/login
// would, naively, sign the victim in and then hard-navigate them to
// the attacker's site — which can mimic neuthek's UI and harvest
// follow-up data. The guard accepts ONLY same-origin path-only
// redirects:
//   - Must start with exactly one '/'
//   - Reject leading '//' (protocol-relative)
//   - Reject backslash variants ('\foo', '/\evil.com') — some
//     browser URL parsers normalize backslash → forward slash
//   - Reject embedded scheme tokens ('javascript:', 'data:')
//     before the first '?' or '#'
//   - Reject control chars (newlines, NULs) — anti-CRLF
// On rejection, log + drop silently and the caller acts as if no
// next= was supplied.
function safeNextPath(raw) {
  if (typeof raw !== "string" || !raw) return "";
  if (/[\x00-\x1f\x7f]/.test(raw)) return "";
  if (!raw.startsWith("/")) return "";
  if (raw.startsWith("//")) return "";
  if (raw.includes("\\")) return "";
  // Strip query + fragment before scanning for scheme tokens.
  const pathOnly = raw.split(/[?#]/, 1)[0];
  if (/^\/+[a-z][a-z0-9+\-.]*:/i.test(pathOnly)) return "";
  if (/^\/+javascript:/i.test(raw)) return "";
  if (/^\/+data:/i.test(raw)) return "";
  return raw;
}

// Read post-auth redirect target + initial mode from the URL. When
// shared-view.jsx routes a recipient through the auth screen it sets
// `?next=/share/{token}#email=…` (decoded shape) so we can bounce
// them back to claim the grant once they've authenticated. The
// `#auth=signup|signin` outer hash picks the initial form mode.
function readAuthHandoff() {
  if (typeof window === "undefined") return { next: "", initialMode: "signup" };
  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "";
  const safeNext = safeNextPath(next);
  let initialMode = "signup";
  const hash = window.location.hash || "";
  if (hash.includes("auth=signin")) initialMode = "signin";
  else if (hash.includes("auth=signup")) initialMode = "signup";
  return { next: safeNext, initialMode };
}

export function AuthScreen({ onSignedIn, tweaks = {}, theme = "light", setTheme }) {
  const setUser = useAuthStore((s) => s.setUser);
  const [submitting, setSubmitting] = useStateA(false);
  const [authError, setAuthError] = useStateA(null);
  const handoff = readAuthHandoff();
  const [mode, setMode] = useStateA(handoff.initialMode); // "signin" | "signup"
  const nextUrl = handoff.next;
  // After successful sign-in/up, if a `next` URL was passed (typically
  // `/share/{token}` from the share-link landing) we hard-navigate so
  // main.tsx re-routes through SharedView. Returns true when a redirect
  // fired so callers can skip the usual onSignedIn callback.
  const redirectIfNext = () => {
    if (!nextUrl) return false;
    // CodeQL `js/client-side-unvalidated-url-redirection` was flagging
    // `window.location.href = nextUrl` even though `safeNextPath`
    // above already rejects every non-same-origin pattern (protocol-
    // relative `//`, scheme tokens like `javascript:` / `data:`, raw
    // CR/LF, anything not starting with a single `/`). The taint
    // tracker can't always see through the regex-based whitelist, so
    // we add an explicit defence in depth that the tracker DOES
    // recognise: resolve the target against `window.location.origin`,
    // then assign only the path + search + hash. This guarantees the
    // navigation stays same-origin regardless of what slipped past
    // `safeNextPath` in some future edit, and CodeQL recognises the
    // `URL`-then-`pathname` shape as a sanitiser.
    try {
      const dest = new URL(nextUrl, window.location.origin);
      window.location.assign(dest.pathname + dest.search + dest.hash);
      return true;
    } catch {
      return false;
    }
  };
  const [email, setEmail] = useStateA("");
  const [pwd, setPwd] = useStateA("");
  const [name, setName] = useStateA("");
  const [showPwd, setShowPwd] = useStateA(false);
  const [emailFocused, setEmailFocused] = useStateA(false);
  const [pwdFocused, setPwdFocused] = useStateA(false);
  const [nameFocused, setNameFocused] = useStateA(false);
  const [featureIdx, setFeatureIdx] = useStateA(0);

  const [showTerms, setShowTerms] = useStateA(false);
  const [showPrivacy, setShowPrivacy] = useStateA(false);
  const [showConsents, setShowConsents] = useStateA(false);
  // §1.2.2 — when /auth/jwt/login responds with `totp_required` we
  // flip into a second-step prompt. The email + password are kept in
  // state so the retry uses the same credentials.
  const [totpNeeded, setTotpNeeded] = useStateA(false);
  const [totpCode, setTotpCode] = useStateA("");
  // §C6 — "Forgot password?" modal state. The link below the
  // sign-in password field opens the modal; submit fires the
  // forgot-password endpoint (which always responds the same way
  // whether the email exists or not — see backend comment) so we
  // toast a generic "if that account exists, check your inbox"
  // either way to avoid enumeration leakage.
  const [showForgot, setShowForgot] = useStateA(false);
  const [forgotEmail, setForgotEmail] = useStateA("");
  const [forgotSubmitting, setForgotSubmitting] = useStateA(false);
  // §C6 — after a successful password reset on /reset, the reset
  // page redirects to "/?reset=ok#auth=signin". Read the query
  // flag at mount and surface a one-time banner above the form.
  const [resetSuccessBanner, setResetSuccessBanner] = useStateA(() => {
    if (typeof window === "undefined") return false;
    try {
      const p = new URLSearchParams(window.location.search);
      return p.get("reset") === "ok";
    } catch {
      return false;
    }
  });
  useEffectA(() => {
    if (!resetSuccessBanner) return;
    // Strip the flag from the URL so a refresh doesn't keep
    // showing the banner.
    try {
      const u = new URL(window.location.href);
      u.searchParams.delete("reset");
      window.history.replaceState({}, "", u.pathname + u.search + u.hash);
    } catch {}
  }, [resetSuccessBanner]);

  const handleForgotSubmit = async (e) => {
    e?.preventDefault?.();
    const trimmed = forgotEmail.trim();
    if (!trimmed || forgotSubmitting) return;
    setForgotSubmitting(true);
    try {
      await forgotPassword(trimmed);
    } catch {
      // Backend always responds 202 even if the email isn't on
      // file (anti-enumeration). A genuine error here would be
      // network-level; surface a generic message so we still
      // don't leak account existence.
    }
    setForgotSubmitting(false);
    setShowForgot(false);
    setForgotEmail("");
    toast.success(
      "If an account uses that email, we just sent a reset link. " +
      "Check your inbox (and spam folder).",
    );
  };

  // listen to "open xxx" events fired from inside the consents modal
  useEffectA(() => {
    const handler = (e) => {
      if (e.detail === "terms") setShowTerms(true);
      if (e.detail === "privacy") setShowPrivacy(true);
    };
    window.addEventListener("neuthek-open", handler);
    return () => window.removeEventListener("neuthek-open", handler);
  }, []);

  // Google SSO landing handler. `/auth/google/callback` redirects back
  // to `/#sso_token=<jwt>&sso_new=0|1` on success or `/#sso_error=…`
  // on failure. We pull the JWT out of the fragment, drop it into
  // tokens.set(), fetch the user profile, and feed the existing
  // `onSignedIn` path so downstream code can't tell the difference
  // between SSO and password sign-in. The fragment is stripped before
  // we touch state so a refresh doesn't replay the flow.
  useEffectA(() => {
    if (typeof window === "undefined") return;
    const hash = window.location.hash || "";
    if (!hash || (!hash.includes("sso_token=") && !hash.includes("sso_error="))) return;
    const frag = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
    const ssoToken = frag.get("sso_token");
    const ssoError = frag.get("sso_error");
    // Strip immediately so a reload doesn't re-enter this path.
    try {
      window.history.replaceState({}, "", window.location.pathname + window.location.search);
    } catch {}
    if (ssoError) {
      const msg = ssoError === "not_configured"
        ? "Google sign-in isn't set up yet on this deployment."
        : ssoError === "email_unverified"
          ? "Your Google account hasn't verified its email yet — verify it on Google and try again."
          : ssoError === "totp_required"
            ? "Two-factor is enabled on this account. Sign in with email + password, then your 6-digit code."
            : ssoError === "account_inactive"
              ? "This account is locked. Contact support."
              : `Google sign-in failed (${ssoError}). Try again.`;
      setAuthError(msg);
      toast.error(msg);
      return;
    }
    if (!ssoToken) return;
    (async () => {
      try {
        setSubmitting(true);
        tokens.set(ssoToken);
        const u = await me();
        setUser(u);
        const wasNew = frag.get("sso_new") === "1";
        toast.success(wasNew ? "Welcome to neuthek!" : "Signed in with Google.");
        if (redirectIfNext()) return;
        onSignedIn?.({
          name: u.display_name || (u.email ? u.email.split("@")[0] : "You"),
          email: u.email,
        });
      } catch (e) {
        tokens.clear();
        const msg = "Google sign-in failed. Try again.";
        setAuthError(msg);
        toast.error(msg);
      } finally {
        setSubmitting(false);
      }
    })();
    // Intentionally empty deps — run once on mount; the auth screen
    // unmounts on success, so re-running on state changes is wasted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startGoogleSignIn = () => {
    // Hard navigation so the OAuth redirect flow leaves the SPA — the
    // FE re-mounts after Google bounces us back to /#sso_token=…
    window.location.href = `${API_BASE_URL}/auth/google/login`;
  };

  // rotate the feature pitch
  useEffectA(() => {
    if (mode === "signin") return;
    const t = setInterval(() => setFeatureIdx(i => (i + 1) % BRAND_FEATURES.length), 3500);
    return () => clearInterval(t);
  }, [mode]);

  const emailValid = /^\S+@\S+\.\S+$/.test(email);
  const canSubmit = mode === "signin"
    ? email.length > 3 && pwd.length > 3
    : emailValid && pwd.length >= 10 && /[A-Z]/.test(pwd) && /\d/.test(pwd) && /[^A-Za-z0-9]/.test(pwd) && name.length > 1;

  const strength = Math.min(4, [
    pwd.length >= 10, /[A-Z]/.test(pwd), /\d/.test(pwd), /[^A-Za-z0-9]/.test(pwd),
  ].filter(Boolean).length);
  const strengthLabel = ["—", "Weak", "Fair", "Good", "Strong"][strength];

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return;
    setAuthError(null);
    if (mode === "signin") {
      try {
        setSubmitting(true);
        const u = totpNeeded
          ? await loginWithTotp(email, pwd, totpCode.trim())
          : await login(email, pwd);
        setUser(u);
        if (redirectIfNext()) return;
        onSignedIn?.({
          name: u.display_name || u.email.split("@")[0],
          email: u.email,
        });
      } catch (e) {
        if (e instanceof TotpRequiredError) {
          setTotpNeeded(true);
          setTotpCode("");
          setAuthError(null);
          return;
        }
        const msg = e instanceof ApiError
          ? (e.status === 400 || e.status === 401
              ? (totpNeeded ? "Wrong 2FA code — check your authenticator app and try again." : "Wrong email or password.")
              : e.detail)
          : "Sign-in failed. Check your connection.";
        setAuthError(msg);
        toast.error(msg);
      } finally {
        setSubmitting(false);
      }
      return;
    }
    setShowConsents(true);
  };

  // §B2 — translate the consents modal payload into the
  // {kind, state} bundle the register endpoint accepts. Every
  // scope the modal knows about is reported either as GRANTED or
  // WITHDRAWN so the ledger has an explicit decision for each — the
  // legal posture is "informed choice, not silent default."
  const consentBundleFromPayload = (payload) => {
    if (!payload) return [];
    const { scopes = {}, bipa = false } = payload;
    const decide = (on) => (on ? "GRANTED" : "WITHDRAWN");
    return [
      { kind: "gps_retention",      state: decide(scopes.gps) },
      { kind: "ai_summary",          state: decide(scopes.aiSummary) },
      { kind: "semantic_search",     state: decide(scopes.semanticSearch) },
      { kind: "bandit_compression_telemetry", state: decide(scopes.telemetry) },
      { kind: "face_recognition",    state: decide(bipa) },
      // §B1 — exif_retention scope. The modal hasn't surfaced it
      // yet; default WITHDRAWN until the consents-modal pass
      // brings up the toggle. EXIF strip-by-default is the safer
      // default per §B1.
      { kind: "exif_retention",     state: "WITHDRAWN" },
    ];
  };

  const handleConsentsComplete = async (payload) => {
    setShowConsents(false);
    setAuthError(null);
    // §A6 age gate — the consents modal must have collected an
    // explicit 13+ confirmation. The server's `_require_age_gate`
    // validator rejects the registration if this is false, but we
    // also check here so the user sees a clean error instead of
    // a 400 response.
    if (!payload?.age13) {
      const msg = "You must confirm you are at least 13 years old to use neuthek.";
      setAuthError(msg);
      toast.error(msg);
      setShowConsents(true);
      return;
    }
    try {
      setSubmitting(true);
      // §B2 — bundle the consents INTO the register call so the
      // consent ledger predates the user row's external visibility.
      // The backend UserManager.create() override writes the
      // ConsentRecord rows in the same transaction.
      const consents = consentBundleFromPayload(payload);
      // §C4.1 — display_name is now a first-class field on signup.
      // The form already requires `name.length > 1` in canSubmit
      // (validated above), so by the time we get here the trimmed
      // value is guaranteed non-empty. The consent signature falls
      // back to the same trimmed name OR the email when the field
      // is somehow empty (defensive — covers a future signup-flow
      // refactor that loosens canSubmit).
      const trimmedName = name.trim();
      const signature = trimmedName || email;
      await register(
        email, pwd, trimmedName, payload.age13 === true,
        consents, signature,
      );
      const u = await login(email, pwd);
      setUser(u);
      if (redirectIfNext()) return;
      onSignedIn?.({ name: name.trim() || "You", email });
    } catch (e) {
      const msg = e instanceof ApiError
        ? (e.status === 400
            ? "An account with that email already exists, or the password is too weak."
            : e.detail)
        : "Sign-up failed. Check your connection.";
      setAuthError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const isSignup = mode === "signup";

  return (
    <div className="auth">
      <aside className="auth__brand">
        <div className="auth__brand-top">
          <div className="auth__brand-mark">
            <span className="auth__brand-mark-mark" style={{ background: "transparent", padding: 0 }}>
              <NeuthekMark size={24}/>
            </span>
            neuthek
          </div>
        </div>

        <div className="auth__brand-pitch">
          <h1>Storage that&nbsp;<em>thinks</em> for you.</h1>
          <p>Photos, videos, and documents — all together, encrypted, and searchable by what's actually in them. AI is opt-in, always under your control.</p>

          <div className="auth__feature-loop">
            {BRAND_FEATURES.map((f, i) => (
              <div key={f.label} className="auth__feature-card" data-active={i === featureIdx}>
                <div className="auth__feature-icon"><Icon name={f.icon} size={16}/></div>
                <div>
                  <div className="auth__feature-label">{f.label}</div>
                  <div className="auth__feature-sub">{f.sub}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="auth__feature-dots">
            {BRAND_FEATURES.map((_, i) => (
              <button
                key={i}
                className="auth__feature-dot"
                data-active={i === featureIdx}
                onClick={() => setFeatureIdx(i)}
                aria-label={`Highlight ${BRAND_FEATURES[i].label}`}
              />
            ))}
          </div>
        </div>

        <div className="auth__brand-foot">
          <span>© 2026 neuthek</span>
          <span className="auth__brand-foot-sep">·</span>
          <a href="#" onClick={(e) => { e.preventDefault(); setShowPrivacy(true); }}>Privacy</a>
          <span className="auth__brand-foot-sep">·</span>
          <a href="#" onClick={(e) => { e.preventDefault(); setShowTerms(true); }}>Terms</a>
          <span className="auth__brand-foot-sep">·</span>
          <a href="#">Status</a>
        </div>
      </aside>

      <main className="auth__form">
        {setTheme && (
          <button className="btn-icon auth__theme-toggle" onClick={() => setTheme(theme === "light" ? "dark" : "light")}
            aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}>
            <Icon name={theme === "light" ? "moon" : "sun"} size={15}/>
          </button>
        )}

        {/* mode tabs */}
        <div className="auth__tabs">
          <button className="auth__tab" data-active={mode === "signup"} onClick={() => setMode("signup")}>Create account</button>
          <button className="auth__tab" data-active={mode === "signin"} onClick={() => setMode("signin")}>Sign in</button>
          <span className="auth__tabs-pill" data-mode={mode}/>
        </div>

        <div className="auth__form-inner" key={mode}>
          <h2>{isSignup ? "Welcome to neuthek" : "Welcome back"}</h2>
          <p className="auth__form-sub">
            {isSignup
              ? "30 seconds to set up. We'll walk through permissions next."
              : "Sign in to your private library."}
          </p>

          {/* §C6 — after a successful /reset, the reset page sends
              us back to "/?reset=ok#auth=signin". One-time
              confirmation banner so the user knows the change
              landed. Auto-dismisses on the next user action that
              causes a re-render of this branch (sign-in attempt). */}
          {resetSuccessBanner && !isSignup && (
            <div
              role="status"
              className="auth__banner auth__banner--ok"
              style={{
                background: "rgba(34, 197, 94, 0.10)",
                border: "1px solid rgba(34, 197, 94, 0.30)",
                color: "var(--ink, #111)",
                padding: "10px 12px",
                borderRadius: 10,
                fontSize: 13,
                marginBottom: 12,
              }}
            >
              <strong>Password updated.</strong> Sign in with your new
              password.
              <button
                type="button"
                onClick={() => setResetSuccessBanner(false)}
                aria-label="Dismiss"
                style={{
                  float: "right",
                  background: "transparent",
                  border: 0,
                  cursor: "pointer",
                  color: "inherit",
                  fontSize: 16,
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </div>
          )}

          {/* social — primary on sign-in, secondary on sign-up */}
          <button
            type="button"
            className="btn btn--secondary btn--lg auth__social"
            onClick={startGoogleSignIn}
            disabled={submitting}
          >
            <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">
              <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.91c1.7-1.57 2.69-3.88 2.69-6.62z"/>
              <path fill="#34A853" d="M9 18c2.43 0 4.47-.81 5.96-2.18l-2.91-2.26c-.8.54-1.83.86-3.05.86-2.34 0-4.33-1.58-5.04-3.7H.96v2.33A9 9 0 0 0 9 18z"/>
              <path fill="#FBBC05" d="M3.96 10.71c-.18-.54-.28-1.12-.28-1.71 0-.6.1-1.17.28-1.71V4.96H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.04l3-2.33z"/>
              <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 9 0 9 9 0 0 0 .96 4.96l3 2.33C4.67 5.16 6.66 3.58 9 3.58z"/>
            </svg>
            Continue with Google
          </button>
          <button
            type="button"
            className="btn btn--secondary btn--lg auth__social"
            onClick={() => toast("Apple sign-in is coming soon. Use Google or email for now.", { icon: "🍎" })}
            disabled={submitting}
          >
            <svg width="14" height="16" viewBox="0 0 14 17" aria-hidden="true" fill="currentColor">
              <path d="M11.18 8.92c-.02-2.16 1.76-3.2 1.84-3.25-1-1.47-2.57-1.67-3.13-1.7-1.34-.13-2.6.78-3.27.78-.69 0-1.72-.76-2.83-.74C2.36 4.04.97 4.86.21 6.18c-1.5 2.6-.38 6.45 1.07 8.56.71 1.04 1.56 2.2 2.66 2.16 1.07-.04 1.48-.69 2.78-.69 1.29 0 1.65.69 2.78.66 1.15-.02 1.88-1.05 2.58-2.1.81-1.2 1.15-2.36 1.17-2.42-.03-.01-2.24-.86-2.27-3.42zM9.05 2.75c.59-.71 1-1.7.88-2.69-.85.04-1.89.57-2.5 1.27-.55.62-1.03 1.62-.9 2.59.95.07 1.92-.48 2.52-1.17z"/>
            </svg>
            Continue with Apple
          </button>

          <div className="auth__divider"><span>or with email</span></div>

          {isSignup && (
            <div className="field">
              <label className={"field__label-floating" + (name || nameFocused ? " field__label-floating--up" : "")}>Your name</label>
              <input
                className="input input--lg input--floating"
                placeholder=" "
                value={name}
                onChange={e => setName(e.target.value)}
                onFocus={() => setNameFocused(true)}
                onBlur={() => setNameFocused(false)}
                autoComplete="name"
              />
            </div>
          )}

          <div className="field">
            <label className={"field__label-floating" + (email || emailFocused ? " field__label-floating--up" : "")}>Email</label>
            <input
              type="email"
              className={"input input--lg input--floating" + (email && !emailValid ? " input--err" : "")}
              placeholder=" "
              value={email}
              onChange={e => setEmail(e.target.value)}
              onFocus={() => setEmailFocused(true)}
              onBlur={() => setEmailFocused(false)}
              autoComplete="email"
            />
            {email && !emailValid && (
              <div className="field__hint field__hint--err">Enter a valid email address.</div>
            )}
          </div>

          <div className="field field--pwd">
            <label className={"field__label-floating" + (pwd || pwdFocused ? " field__label-floating--up" : "")}>Password</label>
            <input
              type={showPwd ? "text" : "password"}
              className="input input--lg input--floating input--pwd"
              placeholder=" "
              value={pwd}
              onChange={e => setPwd(e.target.value)}
              onFocus={() => setPwdFocused(true)}
              onBlur={() => setPwdFocused(false)}
              autoComplete={isSignup ? "new-password" : "current-password"}
            />
            <button type="button" onClick={() => setShowPwd(s => !s)} aria-label={showPwd ? "Hide password" : "Show password"} className="auth__pwd-toggle">
              <Icon name={showPwd ? "eyeOff" : "eye"} size={16}/>
            </button>
            {!isSignup && (
              <div style={{ marginTop: 8, textAlign: "right" }}>
                <button
                  type="button"
                  className="auth__forgot"
                  onClick={() => {
                    // Pre-fill the modal with whatever's in the
                    // sign-in email field so the common case
                    // (user typed their email, then realized they
                    // don't remember the password) doesn't make
                    // them type it again.
                    setForgotEmail(email);
                    setShowForgot(true);
                  }}
                >
                  Forgot password?
                </button>
              </div>
            )}
            {isSignup && (
              <div className="auth__pwd-meter">
                <div className="password-bars">
                  {[0,1,2,3].map(i => <div key={i} className="password-bars__bar" data-on={i < strength}/>)}
                </div>
                <span className="auth__pwd-meter-label" data-strength={strength}>{strengthLabel}</span>
              </div>
            )}
            {isSignup && pwd && <PasswordReqs value={pwd}/>}
          </div>

          {/* §1.2.2 — TOTP second-step. When the initial /auth/jwt/login
              call comes back with `totp_required` we flip into this
              prompt; submitting re-fires the auth flow via
              loginWithTotp instead of the password-only endpoint. */}
          {!isSignup && totpNeeded && (
            <div className="field" style={{ marginTop: 4 }}>
              <label
                className="field__label-floating field__label-floating--up"
                style={{ color: "var(--ink-2)" }}
              >
                6-digit code from your authenticator
              </label>
              <input
                autoFocus
                type="text"
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                placeholder=" "
                className="input input--lg input--floating"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
                style={{
                  fontFamily: "monospace", letterSpacing: "0.25em",
                  textAlign: "center", fontSize: 17,
                }}
                autoComplete="one-time-code"
              />
              <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6, textAlign: "right" }}>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setTotpNeeded(false); setTotpCode(""); }}
                >
                  Use a different account
                </a>
              </div>
            </div>
          )}

          {isSignup && (
            <div className="auth__next-step">
              <Icon name="shield" size={14}/>
              <span>You'll review and sign permissions on the next screen.</span>
            </div>
          )}

          {authError && (
            <div className="field__hint field__hint--err" style={{ marginTop: 4 }}>
              {authError}
            </div>
          )}
          <button
            className="btn btn--primary btn--lg auth__cta"
            disabled={(!totpNeeded && !canSubmit) || (totpNeeded && totpCode.length !== 6) || submitting}
            onClick={handleSubmit}
          >
            {submitting
              ? "Working…"
              : (isSignup ? "Continue" : (totpNeeded ? "Verify code" : "Sign in"))}
            <Icon name="arrowRight" size={14}/>
          </button>

          <div className="auth__alt">
            {isSignup ? (
              <>Already have an account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("signin"); }}>Sign in</a></>
            ) : (
              <>New here? <a href="#" onClick={(e) => { e.preventDefault(); setMode("signup"); }}>Create an account</a></>
            )}
          </div>
        </div>

        <div className="auth__form-foot">
          <Icon name="lock" size={11}/>
          <span>Secured by 256-bit encryption</span>
          <span className="auth__form-foot-sep">·</span>
          <span>SOC 2 Type II</span>
        </div>
      </main>

      <TermsModalA open={showTerms} onClose={() => setShowTerms(false)} mode="view"/>
      <PrivacyModalA open={showPrivacy} onClose={() => setShowPrivacy(false)}/>
      <ConsentsModal
        open={showConsents}
        onClose={() => setShowConsents(false)}
        onComplete={handleConsentsComplete}
        requireFace={tweaks.requireFace}
        allowEarlyAI={tweaks.allowEarlyAI !== false}
      />

      {/* §C6 — Forgot-password modal. Anti-enumeration: submit
          always shows the same generic toast whether or not the
          email is on file. Backend returns 202 either way; we
          don't surface the response body. Uses the shared
          PrimitiveModal so we get the same `.scrim` + `.modal`
          shell every other surface uses (proper centered card
          with backdrop blur + escape-key dismiss + the same
          z-index / animation tokens). */}
      <PrimitiveModal
        open={showForgot}
        onClose={() => !forgotSubmitting && setShowForgot(false)}
        labelledBy="forgot-modal-title"
      >
        <form onSubmit={handleForgotSubmit}>
          <div className="modal__head">
            <h2 id="forgot-modal-title">Reset your password</h2>
          </div>
          <div className="modal__body" style={{ padding: 20 }}>
            <p className="auth__sub" style={{ marginTop: 0 }}>
              Enter the email address on your neuthek account. We&rsquo;ll
              send a link that lets you set a new password. The link
              expires in 15 minutes.
            </p>
            <label className="auth__label">
              Email
              <input
                type="email"
                value={forgotEmail}
                onChange={(e) => setForgotEmail(e.target.value)}
                autoComplete="email"
                autoFocus
                required
                className="auth__input"
              />
            </label>
          </div>
          <div className="modal__foot">
            <div className="modal__foot-actions">
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => setShowForgot(false)}
                disabled={forgotSubmitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn--primary"
                disabled={
                  forgotSubmitting ||
                  !forgotEmail.trim() ||
                  !/^\S+@\S+\.\S+$/.test(forgotEmail.trim())
                }
              >
                {forgotSubmitting ? "Sending…" : "Send reset link"}
              </button>
            </div>
          </div>
        </form>
      </PrimitiveModal>
    </div>
  );
}

// Named export above; legacy `window.AuthScreen` removed.
