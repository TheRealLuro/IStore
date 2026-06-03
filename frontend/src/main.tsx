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
  // Coerce a non-string, non-React-element message to a string. A FastAPI
  // error `detail` can be an ARRAY of objects (422) or a bare object; handing
  // that to react-hot-toast throws "Objects are not valid as a React child",
  // and since the Toaster renders at the root that would blank the whole app.
  const toMessage = (msg: unknown): unknown => {
    if (msg == null || typeof msg === "string") return msg;
    if (React.isValidElement(msg)) return msg;
    if (Array.isArray(msg)) {
      return msg
        .map((m) =>
          m && typeof m === "object"
            ? String((m as Record<string, unknown>).msg ?? JSON.stringify(m))
            : String(m),
        )
        .join("; ");
    }
    if (typeof msg === "object") {
      const o = msg as Record<string, unknown>;
      return String(o.msg ?? o.detail ?? o.message ?? JSON.stringify(o));
    }
    return String(msg);
  };
  const wrap = (kind: "error" | "success" | "loading"): ToastFn => {
    const original = tw[kind].bind(toast) as ToastFn;
    return (msg: unknown, opts: Record<string, unknown> = {}) => {
      const safe = toMessage(msg);
      const id = (opts.id as string) || `${kind}:${hashMessage(safe)}`;
      return original(safe, { ...opts, id });
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

// App-wide crash guard. Before this, a single component throwing during
// render (e.g. a temporal-dead-zone ReferenceError) unmounted the entire
// React tree and left a BLACK SCREEN with nothing to act on. This boundary
// catches any render error below it and shows a recoverable card instead —
// the app degrades to a message + Reload, never a silent void.
type AppErrorBoundaryProps = { children: React.ReactNode };
type AppErrorBoundaryState = { error: Error | null };

class AppErrorBoundary extends React.Component<
  AppErrorBoundaryProps,
  AppErrorBoundaryState
> {
  constructor(props: AppErrorBoundaryProps) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error("neuthek: unhandled render error", error, info);
  }
  render(): React.ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          background: "var(--bg, #0b0b0c)",
          color: "var(--ink, #e9e9ea)",
          fontFamily: "Geist, system-ui, sans-serif",
        }}
      >
        <div
          style={{
            maxWidth: 440,
            width: "100%",
            textAlign: "center",
            background: "var(--surface, #161618)",
            border: "1px solid var(--line, rgba(255,255,255,0.08))",
            borderRadius: 16,
            padding: "28px 24px",
          }}
        >
          <div style={{ fontSize: 17, fontWeight: 600, marginBottom: 8 }}>
            Something went wrong
          </div>
          <div
            style={{ fontSize: 13, opacity: 0.7, marginBottom: 20, lineHeight: 1.5 }}
          >
            neuthek hit an unexpected error and stopped rendering this view.
            Reloading usually clears it.
          </div>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              appearance: "none",
              border: "none",
              borderRadius: 10,
              padding: "10px 18px",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
              background: "var(--ink, #111)",
              color: "var(--surface, #fff)",
            }}
          >
            Reload neuthek
          </button>
          {this.state.error.message && (
            <pre
              style={{
                marginTop: 18,
                textAlign: "left",
                fontSize: 11,
                opacity: 0.55,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: 120,
                overflow: "auto",
              }}
            >
              {String(this.state.error.message)}
            </pre>
          )}
        </div>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppErrorBoundary>
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
      </AppErrorBoundary>
    </QueryClientProvider>
  </React.StrictMode>,
);
