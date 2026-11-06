import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

import { project } from "../ci/project";

export default defineConfig({
  test: {
    projects: [
      // Runs in workerd, against the config's own R2 binding.
      project("files", "workerd", {
        plugins: [cloudflareTest({ wrangler: { configPath: "./wrangler.jsonc" } })],
      }),
      // Static guards over the source and config, in Node.
      project("files", "guards"),
    ],
  },
});
