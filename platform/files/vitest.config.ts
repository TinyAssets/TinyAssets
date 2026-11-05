import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    projects: [
      {
        // Runs in workerd, against the config's own R2 binding.
        plugins: [cloudflareTest({ wrangler: { configPath: "./wrangler.jsonc" } })],
        // A test's timeout is a guard against a hang, not a clock on its speed: these run
        // in workerd or Node, CPU-bound, and a shared, loaded host makes them several
        // times slower (30 s, as gateway's and sandbox's).
        test: { name: "workerd", include: ["test/**/*.test.ts"], testTimeout: 30_000 },
      },
      {
        // Static guards over the source and config, in Node.
        test: { name: "guards", include: ["guards/**/*.test.ts"], environment: "node",
                testTimeout: 30_000 },
      },
    ],
  },
});
