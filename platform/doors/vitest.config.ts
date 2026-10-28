import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    projects: [
      {
        // Runs in workerd, against the config's own KV namespaces and flags. The test entry
        // serves the doors with a fake upstream sign-in and exports the real OwnerDO beside
        // them, so OWNER, which the config binds by script_name, is bound to it here, and
        // the fake upstream's issuer stands in for AuthKit's as the owner issuer.
        plugins: [
          cloudflareTest({
            main: "./test/entry.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              durableObjects: { OWNER: { className: "OwnerDO", useSQLite: true } },
              bindings: { OWNER_ISSUER: "https://upstream.test" },
            },
          }),
        ],
        test: { name: "workerd", include: ["test/**/*.test.ts"] },
      },
      {
        // Static guards over the source and config, in Node.
        test: { name: "guards", include: ["guards/**/*.test.ts"], environment: "node" },
      },
    ],
  },
});
