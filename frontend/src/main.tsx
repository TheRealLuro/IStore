import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "react-hot-toast";
import { App } from "../neuthek/src/app.jsx";
import "../neuthek/styles/index.css";

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
      <App />
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
