import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Mail, KeyRound, Check } from "lucide-react";
import toast from "react-hot-toast";
import { login, updateMe } from "@/api/auth";
import { useAuthStore } from "@/stores/authStore";
import { isPasswordValid, passwordMissing } from "@/utils/password";

/** Profile editor: email + password update. Both flows verify the
 * current password locally (re-auth) before submitting — fastapi-users
 * doesn't gate /users/me by current password by default, so we add the
 * check on the client to avoid a "session-hijack changes password"
 * footgun if a JWT leaks.
 *
 * Email change:
 *   1. user types new email + current password
 *   2. we call login() with current credentials → confirms password
 *   3. PATCH /users/me with the new email
 *   4. force re-login since the JWT subject didn't change but a refresh
 *      keeps the session clean.
 *
 * Password change is the same shape but updates `password`. */
export function ProfileSection() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);

  const [tab, setTab] = useState<"email" | "password" | null>(null);

  if (!user) return null;

  return (
    <div className="rounded-2xl bg-elevated/50 px-4 py-4">
      <div className="flex items-center gap-3">
        <div className="h-9 w-9 rounded-full bg-accent text-white flex items-center justify-center font-semibold uppercase">
          {(user.display_name || user.email).slice(0, 1)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-medium text-fg">
            {user.display_name || "(no display name)"}
          </div>
          <div className="text-[12px] text-fg-secondary truncate">
            {user.email}
          </div>
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={() => setTab(tab === "email" ? null : "email")}
            className="h-8 px-3 rounded-full bg-card hover:bg-hover text-[12px] font-medium transition flex items-center gap-1.5"
          >
            <Mail className="h-3.5 w-3.5" /> Email
          </button>
          <button
            onClick={() => setTab(tab === "password" ? null : "password")}
            className="h-8 px-3 rounded-full bg-card hover:bg-hover text-[12px] font-medium transition flex items-center gap-1.5"
          >
            <KeyRound className="h-3.5 w-3.5" /> Password
          </button>
        </div>
      </div>

      {tab === "email" && (
        <ChangeEmailForm
          currentEmail={user.email}
          onDone={(updatedUser) => {
            setUser(updatedUser);
            setTab(null);
          }}
        />
      )}
      {tab === "password" && <ChangePasswordForm onDone={() => setTab(null)} />}
    </div>
  );
}

function ChangeEmailForm({
  currentEmail,
  onDone,
}: {
  currentEmail: string;
  onDone: (u: ReturnType<typeof useAuthStore.getState>["user"]) => void;
}) {
  const [newEmail, setNewEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");

  const m = useMutation({
    mutationFn: async () => {
      // Re-auth confirms current password locally — see file header.
      await login(currentEmail, currentPassword);
      const updated = await updateMe({ email: newEmail.trim() });
      // Re-issue a JWT for the new email (fastapi-users invalidates
      // session-mode tokens on email change but not stateless JWTs;
      // logging in again is the safest path).
      await login(newEmail.trim(), currentPassword);
      return updated;
    },
    onSuccess: (u) => {
      toast.success("Email updated");
      onDone(u);
    },
    onError: (e) =>
      toast.error(
        e instanceof Error
          ? e.message.includes("LOGIN_BAD_CREDENTIALS")
            ? "Current password is wrong"
            : e.message
          : "Could not update email",
      ),
  });

  const valid = newEmail.includes("@") && currentPassword.length > 0;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid || m.isPending) return;
        m.mutate();
      }}
      className="mt-4 space-y-2.5"
    >
      <input
        type="email"
        value={newEmail}
        onChange={(e) => setNewEmail(e.target.value)}
        placeholder="new@example.com"
        autoComplete="email"
        className="input"
      />
      <input
        type="password"
        value={currentPassword}
        onChange={(e) => setCurrentPassword(e.target.value)}
        placeholder="Current password"
        autoComplete="current-password"
        className="input"
      />
      <SaveButton pending={m.isPending} disabled={!valid} />
    </form>
  );
}

function ChangePasswordForm({ onDone }: { onDone: () => void }) {
  const userEmail = useAuthStore((s) => s.user?.email ?? "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");

  const missing = passwordMissing(newPassword);
  const valid =
    currentPassword.length > 0 &&
    isPasswordValid(newPassword) &&
    newPassword !== currentPassword;

  const m = useMutation({
    mutationFn: async () => {
      await login(userEmail, currentPassword);
      await updateMe({ password: newPassword });
      // Re-login to get a fresh JWT — old session keeps working but
      // we want explicit confirmation the new credential is the source of truth.
      await login(userEmail, newPassword);
    },
    onSuccess: () => {
      toast.success("Password updated");
      onDone();
    },
    onError: (e) =>
      toast.error(
        e instanceof Error
          ? e.message.includes("LOGIN_BAD_CREDENTIALS")
            ? "Current password is wrong"
            : e.message
          : "Could not update password",
      ),
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!valid || m.isPending) return;
        m.mutate();
      }}
      className="mt-4 space-y-2.5"
    >
      <input
        type="password"
        value={currentPassword}
        onChange={(e) => setCurrentPassword(e.target.value)}
        placeholder="Current password"
        autoComplete="current-password"
        className="input"
      />
      <input
        type="password"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        placeholder="New password"
        autoComplete="new-password"
        className="input"
      />
      {newPassword.length > 0 && missing.length > 0 && (
        <ul className="text-[11px] text-fg-secondary space-y-0.5">
          {missing.map((m) => (
            <li key={m} className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-fg-muted" />
              Needs {m}
            </li>
          ))}
        </ul>
      )}
      {newPassword.length > 0 && missing.length === 0 && (
        <div className="flex items-center gap-1.5 text-[11px] text-success">
          <Check className="h-3 w-3" strokeWidth={3} />
          Password meets requirements
        </div>
      )}
      <SaveButton pending={m.isPending} disabled={!valid} />
    </form>
  );
}

function SaveButton({
  pending,
  disabled,
}: {
  pending: boolean;
  disabled: boolean;
}) {
  return (
    <button
      type="submit"
      disabled={disabled || pending}
      className="w-full h-10 rounded-full bg-fg text-fg-inverse text-[13px] font-medium shadow-card hover:shadow-float hover:-translate-y-0.5 active:translate-y-0 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
    >
      {pending && <Loader2 className="h-4 w-4 animate-spin" />}
      Save
    </button>
  );
}
