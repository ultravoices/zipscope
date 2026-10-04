import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the FastAPI backend so the SPA works on one origin.
// In production the FastAPI app serves web/dist at /, so the same relative paths work.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  optimizeDeps: {
    // maplibre-gl v6 ships plain ESM with a separately-imported worker module
    // that the dep optimizer mishandles; serve it un-optimized in dev.
    exclude: ["maplibre-gl"],
  },
});
