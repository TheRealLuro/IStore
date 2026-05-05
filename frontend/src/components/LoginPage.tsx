import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Eye, EyeOff, Loader2, Check, X, KeyRound } from "lucide-react";
import {
  forgotPassword,
  login,
  recoveryLogin,
  register,
  resetPassword,
  verifyEmail,
} from "@/api/auth";
import { useAuthStore } from "@/stores/authStore";
import { ThemeToggle } from "./ThemeToggle";
import {
  PASSWORD_REQUIREMENTS,
  isPasswordValid,
  passwordMissing,
} from "@/utils/password";

type Mode = "login" | "register" | "forgot" | "reset" | "recovery" | "verifying";

/** A single page that handles all unauthenticated flows:
 *
 *   - login / register (default)
 *   - forgot password — email entry that triggers a reset mail
 *   - reset            — landed from `?token=` in the email; sets a new password
 *   - recovery         — paste a single-use code instead of a password
 *   - verifying        — landed from `?action=verify&token=`; completes silently
 *                        and returns to login
 *
 * URL params are consumed once on mount (history.replaceState clears them so
 * a refresh doesn't try to re-verify a now-spent token). */
export function LoginPage() {
  const setUser = useAuthStore((s) => s.setUser);
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showRequirements, setShowRequirements] = useState(false);
  const [resetToken, setResetToken] = useState<string | null>(null);
  const [recoveryCode, setRecoveryCode] = useState("");
  const [ageConfirmed, setAgeConfirmed] = useState(false);

  // Pick up email-link tokens from the URL on first mount. Tokens are
  // single-use, so we strip them from the address bar after consumption
  // — a refresh shouldn't replay a verify.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    const action = params.get("action");
    if (!token) return;
    if (action === "verify") {
      setMode("verifying");
      verifyEmail(token)
        .then(() => {
          toast.success("Email verified — you can sign in");
        })
        .catch((e) => {
          toast.error(
            e instanceof Error
              ? `Verification failed: ${e.message}`
              : "Verification failed",
          );
        })
        .finally(() => {
          setMode("login");
          window.history.replaceState({}, "", window.location.pathname);
        });
    } else if (action === "reset") {
      setResetToken(token);
      setMode("reset");
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const requirementsMet =
    mode === "register" || mode === "reset" ? isPasswordValid(password) : true;
  const missing = passwordMissing(password);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if ((mode === "register" || mode === "reset") && !requirementsMet) {
      toast.error("Password doesn't meet requirements");
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        const user = await login(email, password);
        setUser(user);
        toast.success("Welcome back");
      } else if (mode === "register") {
        if (!ageConfirmed) {
          toast.error("You must confirm you are old enough to use IStore");
          return;
        }
        await register(email, password, ageConfirmed);
        const user = await login(email, password);
        setUser(user);
        toast.success("Welcome — check your inbox for a verification link");
      } else if (mode === "forgot") {
        await forgotPassword(email);
        // Server returns 202 regardless — never leak whether the email
        // is registered. We tell the user the same thing either way.
        toast.success(
          "If that account exists, a reset link is on its way.",
        );
        setMode("login");
      } else if (mode === "reset") {
        if (!resetToken) throw new Error("Missing reset token");
        await resetPassword(resetToken, password);
        toast.success("Password reset — sign in with your new password");
        setMode("login");
        setResetToken(null);
        setPassword("");
      } else if (mode === "recovery") {
        const user = await recoveryLogin(email, recoveryCode);
        setUser(user);
        toast.success(
          "Signed in with a recovery code — please reset your password soon",
        );
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Authentication failed";
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  if (mode === "verifying") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-page text-fg-secondary">
        <Loader2 className="h-5 w-5 animate-spin mr-2" /> Verifying email…
      </div>
    );
  }

  const headline = {
    login: "Sign in to your storage",
    register: "Create your account",
    forgot: "Reset your password",
    reset: "Choose a new password",
    recovery: "Sign in with a recovery code",
    verifying: "",
  }[mode];

  const ctaLabel = {
    login: "Sign in",
    register: "Create account",
    forgot: "Send reset link",
    reset: "Update password",
    recovery: "Sign in",
    verifying: "",
  }[mode];

  const showEmailField = mode !== "reset";
  const showPasswordField = mode === "login" || mode === "register" || mode === "reset";
  const showRecoveryField = mode === "recovery";
  const showRequirementsHint = mode === "register" || mode === "reset";

  return (
    <div className="min-h-screen flex items-center justify-center bg-page p-6 relative">
      <div className="absolute top-6 right-6">
        <ThemeToggle />
      </div>

      <div className="w-full max-w-md">
        <div className="text-center mb-10">
          <div className="text-4xl font-semibold tracking-tight">IStore</div>
          <div className="text-fg-secondary mt-2 text-base">{headline}</div>
        </div>

        <div className="bg-card rounded-3xl shadow-card p-8">
          <form onSubmit={submit} className="space-y-4">
            {showEmailField && (
              <div>
                <label className="block text-xs font-medium text-fg-secondary mb-1.5 px-1">
                  Email
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                  autoComplete="email"
                  className="input"
                />
              </div>
            )}

            {showPasswordField && (
              <div>
                <label className="block text-xs font-medium text-fg-secondary mb-1.5 px-1">
                  {mode === "reset" ? "New password" : "Password"}
                </label>
                <div className="relative">
                  <input
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onFocus={() => showRequirementsHint && setShowRequirements(true)}
                    placeholder={
                      mode === "login" ? "Your password" : "Choose a strong password"
                    }
                    required
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                    className="input pr-12"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="absolute right-2 top-1/2 -translate-y-1/2 h-8 w-8 rounded-full text-fg-secondary hover:bg-hover hover:text-fg flex items-center justify-center transition"
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>
            )}

            {showRecoveryField && (
              <div>
                <label className="block text-xs font-medium text-fg-secondary mb-1.5 px-1">
                  Recovery code
                </label>
                <input
                  value={recoveryCode}
                  onChange={(e) => setRecoveryCode(e.target.value)}
                  placeholder="XXXXX-XXXXX"
                  required
                  autoComplete="one-time-code"
                  className="input font-mono tracking-widest uppercase"
                />
                <div className="text-[11px] text-fg-secondary mt-1.5 px-1">
                  Each code works once. Generate fresh codes in Account →
                  Recovery codes after signing in.
                </div>
              </div>
            )}

            {showRequirementsHint && showRequirements && (
              <div className="rounded-2xl bg-elevated p-4 animate-fade-in">
                <div className="text-xs font-medium text-fg-secondary mb-2">
                  Password must contain
                </div>
                <ul className="space-y-1.5">
                  {PASSWORD_REQUIREMENTS.map((r) => {
                    const met = r.test(password);
                    return (
                      <li key={r.id} className="flex items-center gap-2 text-sm">
                        <span
                          className={`h-4 w-4 rounded-full flex items-center justify-center transition-all ${
                            met
                              ? "bg-success/20 text-success"
                              : "bg-elevated text-fg-muted ring-1 ring-border"
                          }`}
                        >
                          {met ? (
                            <Check className="h-2.5 w-2.5" strokeWidth={3} />
                          ) : (
                            <X className="h-2.5 w-2.5" strokeWidth={3} />
                          )}
                        </span>
                        <span className={met ? "text-fg" : "text-fg-secondary"}>
                          {r.label}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            {mode === "register" && (
              <label className="flex items-start gap-2 rounded-2xl bg-elevated p-4 text-[12px] text-fg-secondary">
                <input
                  type="checkbox"
                  checked={ageConfirmed}
                  onChange={(e) => setAgeConfirmed(e.target.checked)}
                  className="mt-0.5"
                  required
                />
                <span>I confirm I am at least 13 years old.</span>
              </label>
            )}

            <button
              type="submit"
              disabled={
                busy ||
                ((mode === "register" || mode === "reset") &&
                  missing.length > 0) ||
                (mode === "register" && !ageConfirmed)
              }
              className="w-full h-12 rounded-full bg-fg text-fg-inverse text-[14px] font-medium shadow-card hover:shadow-float hover:-translate-y-0.5 active:translate-y-0 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {ctaLabel}
            </button>
          </form>
        </div>

        <div className="text-center mt-6 text-sm text-fg-secondary space-y-2">
          {(mode === "login" || mode === "register") && (
            <div>
              {mode === "login" ? "New to IStore?" : "Already have an account?"}{" "}
              <button
                onClick={() => {
                  setMode(mode === "login" ? "register" : "login");
                  setShowRequirements(false);
                }}
                className="text-accent hover:underline font-medium"
              >
                {mode === "login" ? "Create account" : "Sign in"}
              </button>
            </div>
          )}
          {mode === "login" && (
            <div className="flex items-center justify-center gap-3">
              <button
                onClick={() => setMode("forgot")}
                className="text-fg-secondary hover:text-accent hover:underline text-[12px]"
              >
                Forgot password?
              </button>
              <span className="text-fg-muted">·</span>
              <button
                onClick={() => setMode("recovery")}
                className="text-fg-secondary hover:text-accent hover:underline text-[12px] inline-flex items-center gap-1"
              >
                <KeyRound className="h-3 w-3" />
                Use a recovery code
              </button>
            </div>
          )}
          {(mode === "forgot" || mode === "reset" || mode === "recovery") && (
            <div>
              <button
                onClick={() => {
                  setMode("login");
                  setShowRequirements(false);
                  setResetToken(null);
                }}
                className="text-accent hover:underline font-medium"
              >
                Back to sign in
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
