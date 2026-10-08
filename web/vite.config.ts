import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, mkdirSync, copyFileSync } from "fs";
import { join } from "path";
import type { Plugin } from "vite";

// Simple Vite plugin: copy worker files from public/ to dist/ during build.
// Works whether build runs from web/ or repo root.
const maplibreWorkers: Plugin = {
  name: "maplibre-workers",
  apply: "build" as const,
  closeBundle() {
    const tryCopy = (base: string) => {
      const nm = join(base, "public", "static", "worker");
      if (!existsSync(nm)) return;
      const worker = join(nm, "maplibre-gl-worker.mjs");
      const shared = join(nm, "maplibre-gl-shared.mjs");
      const out = join(base, "dist", "static", "worker");
      mkdirSync(out, { recursive: true });
      if (existsSync(worker)) copyFileSync(worker, join(out, "maplibre-gl-worker.mjs"));
      if (existsSync(shared)) copyFileSync(shared, join(out, "maplibre-gl-shared.mjs"));
    };

    // Check current project root (where build runs from)
    tryCopy(process.cwd());
    // Check parent directory (if build runs from web/, check repo root)
    const parent = join(process.cwd(), "..");
    if (parent !== process.cwd()) {
      tryCopy(parent);
    }
  },
};

export default defineConfig({
  plugins: [react(), maplibreWorkers],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  optimizeDeps: {
    exclude: ["maplibre-gl"],
  },
  build: {
    commonjsOptions: {
      include: [/maplibre-gl/, /node_modules/],
    },
  },
});
