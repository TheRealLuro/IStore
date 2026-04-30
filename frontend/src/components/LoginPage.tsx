import { useState } from "react";
import toast from "react-hot-toast";
import { Eye, EyeOff, Loader2, Check, X } from "lucide-react";
import { login, register } from "@/api/auth";
import { useAuthStore } from "@/stores/authStore";
import { ThemeToggle } from "./ThemeToggle";
import {
  PASSWORD_REQUIREMENTS,
  isPasswordValid,
  passwordMissing,
} from "@/utils/password";

export function LoginPage() {
  const setUser = useAuthStore((s) => s.setUser);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showRequirements, setShowRequirements] = useState(false);

  const requirementsMet =
    mode === "login" ? true : isPasswordValid(password);
  const missing = passwordMissing(password);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (mode === "register" && !requirementsMet) {
      toast.error("Password doesn't meet requirements");
      return;
    }
    setBusy(true);
    try {
      if (mode === "register") {
        await register(email, password);
      }
      const user = await login(email, password);
      setUser(user);
      toast.success(`Welcome${mode === "register" ? "" : " back"}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Authentication failed";
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-page p-6 relative">
      <div className="absolute top-6 right-6">
        <ThemeToggle />
      </div>

      <div className="w-full max-w-md">
        <div className="text-center mb-10">
          <div className="text-4xl font-semibold tracking-tight">IStore</div>
          <div className="text-fg-secondary mt-2 text-base">
            {mode === "login" ? "Sign in to your storage" : "Create your account"}
          </div>
        </div>

        <div className="bg-card rounded-3xl shadow-card p-8">
          <form onSubmit={submit} className="space-y-4">
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

            <div>
              <label className="block text-xs font-medium text-fg-secondary mb-1.5 px-1">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onFocus={() => mode === "register" && setShowRequirements(true)}
                  placeholder={mode === "login" ? "Your password" : "Choose a strong password"}
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

            {mode === "register" && showRequirements && (
              <div className="rounded-2xl bg-elevated p-4 animate-fade-in">
                <div className="text-xs font-medium text-fg-secondary mb-2">
                  Password must contain
                </div>
                <ul className="space-y-1.5">
                  {PASSWORD_REQUIREMENTS.map((r) => {
                    const met = r.test(password);
                    return (
                      <li
                        key={r.id}
                        className="flex items-center gap-2 text-sm"
                      >
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
                        <span
                          className={
                            met ? "text-fg" : "text-fg-secondary"
                          }
                        >
                          {r.label}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            <button
              type="submit"
              disabled={busy || (mode === "register" && missing.length > 0)}
              className="btn-primary w-full h-11 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {mode === "login" ? "Sign in" : "Create account"}
            </button>
          </form>
        </div>

        <div className="text-center mt-6 text-sm text-fg-secondary">
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
      </div>
    </div>
  );
}
