import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "react-hot-toast";
import { App } from "../neuthek/src/app.jsx";
import { SharedView } from "../neuthek/src/shared-view.jsx";
import "../neuthek/styles/index.css";

// Public share-link viewer (todo §1.1 / G1). Lives at the root so a
// recipient hitting /share/{token} never even loads the gallery
// shell — keeps the owner's library off the wire entirely until the
// recipient claims, and avoids React Router as a dependency. The
// branch reads pathname once at boot; navigating from inside the
// app to a /share URL will hit a fresh document load anyway.
function bootstrap(): JSX.Element {
  const path = window.location.pathname;
  if (path.startsWith("/share/")) {
    const token = decodeURIComponent(path.slice("/share/".length).replace(/\/$/, ""));
    if (token) return <SharedView token={token}/>;
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
