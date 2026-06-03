import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    // Bind-mounting source from a Windows / WSL2 host into the Linux
    // container drops inotify events, so Vite never sees file edits
    // unless we tell chokidar to poll. The polling interval is small
    // enough to feel like HMR; the CPU cost is negligible for a tree
    // of this size.
    watch: {
      usePolling: true,
      interval: 300,
    },
    // Vite's host-check rejects hostnames it doesn't recognize when
    // accessed through the published port; allow anything since we're
    // a dev-only inner network.
    host: true,
    // Allow this dev server to be embedded in an iframe from any origin
    // (preview tooling / wrappers). Dev-only — the production CSP is set
    // at the SPA edge (F10).
    headers: {
      "Content-Security-Policy": "frame-ancestors *",
      // Opt this dev server in to being reached from a less-private
      // (public) context — the server half of Chrome's Private/Local
      // Network Access gate. Dev-only; the browser still enforces a
      // user-permission prompt on top of this.
      "Access-Control-Allow-Private-Network": "true",
      "Access-Control-Allow-Origin": "*",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
