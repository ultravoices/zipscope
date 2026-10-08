import { defineConfig } from "vite";
import { existsSync, mkdirSync, copyFileSync, readFileSync } from "fs";
import { join } from "path";
import type { Plugin } from "vite";

const maplibreWorkers: Plugin = {
  name: "maplibre-workers",
  apply: "build" as const,
  closeBundle() {
    const cwd = process.cwd();
    const findFrom = (base: string) => {
      const worker = join(base, "node_modules", "maplibre-gl", "dist", "maplibre-gl-worker.mjs");
      const shared = join(base, "node_modules", "maplibre-gl", "dist", "maplibre-gl-shared.mjs");
      if (existsSync(worker)) {
        const out = join(base, "web", "dist", "static", "worker");
        mkdirSync(out, { recursive: true });
        if (existsSync(shared)) copyFileSync(shared, join(out, "maplibre-gl-shared.mjs"));
        copyFileSync(worker, join(out, "maplibre-gl-worker.mjs"));
      }
    };
    findFrom(cwd);
  },
};

export default defineConfig({
  root: "web",
  plugins: [maplibreWorkers],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  optimizeDeps: { exclude: ["maplibre-gl"] },
  build: {
    commonjsOptions: { include: [/maplibre-gl/, /node_modules/] },
  },
});
