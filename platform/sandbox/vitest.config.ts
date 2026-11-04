import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import ts from "typescript";
import { defineConfig } from "vitest/config";

import { LARGE, medium } from "../ci/classes";

const SANDBOX = dirname(fileURLToPath(import.meta.url));

/** The fake E2B (`test/fake-e2b.ts`), transpiled: E2B's two hosts, in tests only. */
function fakeE2b() {
  const path = join(SANDBOX, "test", "fake-e2b.ts");
  return {
    name: "fake-e2b",
    modulesRoot: SANDBOX,
    modules: [{ type: "ESModule" as const, path: join(SANDBOX, "test", "fake-e2b.js"),
                contents: ts.transpileModule(readFileSync(path, "utf8"), {
                  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
                }).outputText }],
    compatibilityDate: "2026-10-23",
    // The ring queue's consumer in tests: each ring recorded at once.
    queueConsumers: { "tinyassets-sandbox-ring": { maxBatchSize: 1, maxBatchTimeout: 0 } },
  };
}

export default defineConfig({
  test: {
    tags: [LARGE],
    projects: [
      {
        // Runs in workerd, from the config's own bindings: its class, the directory and its
        // template var. Every global fetch the Worker makes reaches the fake E2B, which the
        // tests also reach (`E2B`) to script it and read back what it received.
        plugins: [
          cloudflareTest({
            main: "./src/index.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              bindings: { E2B_API_KEY: "e2b_test_key_0000" },
              serviceBindings: { E2B: "fake-e2b" },
              outboundService: "fake-e2b",
              workers: [fakeE2b()],
            },
          }),
        ],
        test: { name: "workerd", ...medium("sandbox/workerd"), include: ["test/**/*.test.ts"] },
      },
      {
        // The runner, in Node, against fake CLIs and a fake doors server.
        test: { name: "runner", ...medium("sandbox/runner"), include: ["runner/**/*.test.ts"],
                environment: "node" },
      },
      {
        // Static guards over the source and config, in Node.
        test: { name: "guards", ...medium("sandbox/guards"), include: ["guards/**/*.test.ts"],
                environment: "node" },
      },
    ],
  },
});
