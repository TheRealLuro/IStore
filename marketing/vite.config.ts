import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, Vite runs on 5180 and proxies /api to the Express server
// on 5181 so the frontend can call same-origin /api/waitlist/signup
// without CORS plumbing. In prod, Express serves both /api and the
// built SPA off the same origin (Render Web Service).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5181",
        changeOrigin: true,
      },
    },
  },
  preview: { port: 4180, host: "127.0.0.1" },
  build: { outDir: "dist", sourcemap: false },
});
