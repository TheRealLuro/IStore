import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "react-hot-toast";
import { App } from "../neuthek/src/app.jsx";
import { SharedView } from "../neuthek/src/shared-view.jsx";
import { AdminPage } from "../neuthek/src/admin-page.jsx";
import "../neuthek/styles/index.css";

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
