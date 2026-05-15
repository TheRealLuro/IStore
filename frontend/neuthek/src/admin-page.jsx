// Full-page wrapper around <AdminOverlay/> for the /admin route.
//
// The modal version stays available from the gear button in the
// sidebar for quick checks; this routed page is what an operator
// pins to a browser tab when they're working through the dashboard
// for any length of time. The two share the same render tree —
// <AdminOverlay open={true}/> with a back-to-root onClose — so we
// don't fork the codebase.
import React from "react";
import { useAuthStore } from "@/stores/authStore";
import { AdminOverlay } from "./admin.jsx";

export function AdminPage() {
  const user = useAuthStore((s) => s.user);
  const authLoading = useAuthStore((s) => s.loading);
  const bootstrap = useAuthStore((s) => s.bootstrap);

  React.useEffect(() => { bootstrap(); }, [bootstrap]);

  // Send any non-superuser back home; the dashboard is operator-only.
  React.useEffect(() => {
    if (authLoading) return;
    if (!user) {
      window.location.href = "/?next=/admin";
      return;
    }
    if (!user.is_superuser && user.role !== "admin" && user.role !== "superuser") {
      window.location.href = "/";
    }
  }, [user, authLoading]);

  if (authLoading || !user) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", color: "var(--ink-3)" }}>
        Loading admin console…
      </div>
    );
  }

  return (
    <AdminOverlay
      open={true}
      onClose={() => { window.location.href = "/"; }}
    />
  );
}
