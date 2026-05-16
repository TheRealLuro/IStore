import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster, toast } from "react-hot-toast";
import { App } from "../neuthek/src/app.jsx";
import { SharedView } from "../neuthek/src/shared-view.jsx";
import { AdminPage } from "../neuthek/src/admin-page.jsx";
import {
  BillingPage,
  BillingCheckoutPage,
  BillingReturnPage,
} from "../neuthek/src/billing.jsx";
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
