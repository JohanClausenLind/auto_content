import { defineConfig } from "vitest/config";
// Real (small) renders run in the determinism suite; allow generous per-test time under load.
export default defineConfig({ test: { include: ["test/**/*.test.ts"], testTimeout: 300_000, hookTimeout: 300_000 } });
