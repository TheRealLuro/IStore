import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster, toast } from "react-hot-toast";
import { App } from "../neuthek/src/app.jsx";
import { SharedView } from "../neuthek/src/shared-view.jsx";
import { VaultLinkView } from "../neuthek/src/vault-link-view.jsx";
import { AdminPage } from "../neuthek/src/admin-page.jsx";
import {
  BillingPage,
  BillingCheckoutPage,
  BillingReturnPage,
} from "../neuthek/src/billing.jsx";
import { ResetPasswordPage } from "../neuthek/src/reset-password.jsx";
import { VerifyEmailPage } from "../neuthek/src/verify-email.jsx";
import { SigninLinkPage } from "../neuthek/src/signin-link.jsx";
import "../neuthek/styles/index.css";

// Apply the saved theme BEFORE any component mounts so every entry
// point — gallery, share viewer, admin dashboard, billing pages —
// renders against the right CSS variable set on first paint. Without
// this, /billing and /admin used to flash light-mode until <App/>'s
// effect ran, which never fired on those routes (they don't mount
// <App/>).
function applyInitialTheme(): void {
  try {
    const saved = localStorage.getItem("neuthek.theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
      return;
    }
  } catch {}
  const prefersDark =
    window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "light");
}
applyInitialTheme();

// Toast deduplication — rapid clicks on a failing button spam the
// toast stack with identical messages. Wrap toast.error/.success/
// .loading so each call passes an id derived from the message content
// (plus the explicit `id` from options if supplied). react-hot-toast
// uses that id to replace, not stack, the toast. Wraps don't recurse
// because we call the original singleton function references.
function installToastDedup(): void {
  const hashMessage = (msg: unknown): string => {
    const s = typeof msg === "string" ? msg : JSON.stringify(msg);
    let h = 0;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) - h) + s.charCodeAt(i);
      h |= 0;
    }
    return "t" + (h >>> 0).toString(36);
  };
  type ToastFn = (m: unknown, opts?: Record<string, unknown>) => string;
  type ToastWithLevels = typeof toast & {
    error: ToastFn;
    success: ToastFn;
    loading: ToastFn;
  };
  const tw = toast as ToastWithLevels;
  const wrap = (kind: "error" | "success" | "loading"): ToastFn => {
    const original = tw[kind].bind(toast) as ToastFn;
    return (msg: unknown, opts: Record<string, unknown> = {}) => {
      const id = (opts.id as string) || `${kind}:${hashMessage(msg)}`;
      return original(msg, { ...opts, id });
    };
  };
  tw.error = wrap("error");
  tw.success = wrap("success");
  tw.loading = wrap("loading");
}
installToastDedup();

// Path-based dispatch at boot. We avoid React Router so the gallery
// app, the share viewer, and the admin dashboard each load only what
// they need. Reads pathname once at mount — switching between them
// triggers a fresh document load, which is the right behavior since
// each surface has very different auth + data needs.
function bootstrap(): JSX.Element {
  const path = window.location.pathname;
  if (path.startsWith("/share/")) {
    const token = decodeURIComponent(path.slice("/share/".length).replace(/\/$/, ""));
    if (token) return <SharedView token={token}/>;
  }
  // VLT-8 P7 — public vault link viewer. The decryption key is in the URL
  // fragment (#…), which this standalone page reads client-side; the server
  // never sees it. Unauthenticated — anyone with the link can open it.
  if (path.startsWith("/v/")) {
    const token = decodeURIComponent(path.slice("/v/".length).replace(/\/$/, ""));
    if (token) return <VaultLinkView token={token}/>;
  }
  if (path === "/admin" || path.startsWith("/admin/")) {
    return <AdminPage/>;
  }
  if (path === "/billing/return") {
    return <BillingReturnPage/>;
  }
  if (path === "/billing/checkout") {
    return <BillingCheckoutPage/>;
  }
  if (path === "/billing" || path === "/billing/") {
    return <BillingPage/>;
  }
  // §C6 — password-reset landing. The backend mails
  // {frontend_base_url}/reset?token=<jwt>; this dispatch reads the
  // path and the page itself pulls the ?token= out of the query
  // string. Visiting /reset without a token shows a "link
  // expired" card with a back-to-sign-in button.
  if (path === "/reset" || path === "/reset/") {
    return <ResetPasswordPage/>;
  }
  // §C6b — email-verification landing. The backend mails
  // {frontend_base_url}/verify?token=<jwt> on signup (via
  // UserManager.on_after_register → request_verify →
  // send_verify_email). This page consumes the token POST /auth/
  // verify, flashes "verified", and hands the user back to the
  // gallery. Visiting /verify without a token shows a card with
  // a "request a fresh link" form.
  if (path === "/verify" || path === "/verify/") {
    return <VerifyEmailPage/>;
  }
  // §H#7 — magic-link passwordless sign-in landing. The backend mails
  // {frontend_base_url}/signin?token=<jwt>; the page consumes the
  // token via POST /auth/email-link/consume and lands the user in the
  // gallery. Visiting /signin without a token shows a "request a fresh
  // link" form.
  if (path === "/signin" || path === "/signin/") {
    return <SigninLinkPage/>;
  }
  return <App/>;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {bootstrap()}
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: "var(--surface, #fff)",
            color: "var(--ink, #111)",
            border: "1px solid var(--line, rgba(0,0,0,0.08))",
            borderRadius: 12,
            padding: "10px 14px",
            fontSize: 13,
            fontFamily: "Geist, system-ui, sans-serif",
          },
        }}
      />
    </QueryClientProvider>
  </React.StrictMode>,
);
