import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { LARGE, medium } from "../ci/classes";

export default defineConfig({
  test: {
    tags: [LARGE],
    projects: [
      {
        // Runs in workerd, against the config's own R2 binding.
        plugins: [cloudflareTest({ wrangler: { configPath: "./wrangler.jsonc" } })],
        test: { name: "workerd", ...medium("files/workerd"), include: ["test/**/*.test.ts"] },
      },
      {
        // Static guards over the source and config, in Node.
        test: { name: "guards", ...medium("files/guards"), include: ["guards/**/*.test.ts"],
                environment: "node" },
      },
    ],
  },
});
