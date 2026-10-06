import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The interface is served by `agentlab serve` from the API's own origin, so it calls the API with relative paths. While
// developing (`npm run dev`) the same paths are forwarded to a running server (AGENTLAB_DEV_API, default 127.0.0.1:8080).
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};
const api = env.AGENTLAB_DEV_API ?? "http://127.0.0.1:8080";
const prefixes = [
  "health", "projects", "targets", "credentials", "documents", "discover", "test-plans", "test-runs", "reports",
  "comparisons", "providers", "models", "skills", "scoring-profiles", "environment", "settings", "artifacts", "view",
]; // prettier-ignore

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/agentlab/api/static",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
  server: {
    proxy: Object.fromEntries(prefixes.map((p) => [`^/${p}(/|$)`, { target: api, changeOrigin: false }])),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
