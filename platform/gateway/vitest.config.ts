import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import ts from "typescript";
import { defineConfig } from "vitest/config";

const TEST = join(dirname(fileURLToPath(import.meta.url)), "test");

/**
 * The fake outside world (test/fake-upstream.ts), as the Worker every global fetch of
 * the gateway goes to: miniflare runs auxiliary Workers from JavaScript, so it is
 * transpiled here.
 */
function fakeUpstream() {
  return {
    name: "fake-upstream",
    modules: [{
      type: "ESModule" as const,
      path: join(TEST, "fake-upstream.js"),
      contents: ts.transpileModule(readFileSync(join(TEST, "fake-upstream.ts"), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
      }).outputText,
    }],
    compatibilityDate: "2026-10-23",
  };
}

/** A keyring made fresh for each run: a rotated ring, so `k1` is an older kid. */
const KEYRING = JSON.stringify({
  active: "k2",
  keys: { k1: randomBytes(32).toString("base64"), k2: randomBytes(32).toString("base64") },
});

export default defineConfig({
  test: {
    projects: [
      {
        // Runs in workerd, under the config's own runtime and flags. The keyring is the
        // tests' (the config declares the secret and holds no value), and every global
        // fetch goes to the fake upstream.
        plugins: [
          cloudflareTest({
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              bindings: { GATEWAY_KEYRING: KEYRING },
              outboundService: "fake-upstream",
              workers: [fakeUpstream()],
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
