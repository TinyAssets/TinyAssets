import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import ts from "typescript";
import { defineConfig } from "vitest/config";

const FILES = join(dirname(fileURLToPath(import.meta.url)), "..", "files");

/**
 * The real files Worker, as the FILES service binding's target: its own source, its own
 * config's runtime and its one bucket. Miniflare runs auxiliary Workers from JavaScript,
 * so its modules are transpiled here, named as their sources import them.
 */
function filesWorker() {
  const parsed = ts.parseConfigFileTextToJson(
    "wrangler.jsonc", readFileSync(join(FILES, "wrangler.jsonc"), "utf8"));
  const config = parsed.config as { name: string; compatibility_date: string;
                                    compatibility_flags: string[];
                                    r2_buckets: { binding: string }[] };
  const modules = ["index", "keys"].map((name) => ({
    type: "ESModule" as const,
    path: join(FILES, "src", name),
    contents: ts.transpileModule(readFileSync(join(FILES, "src", `${name}.ts`), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    }).outputText,
  }));
  return {
    name: config.name,
    modulesRoot: FILES,
    modules,
    compatibilityDate: config.compatibility_date,
    compatibilityFlags: config.compatibility_flags,
    r2Buckets: config.r2_buckets.map((bucket) => bucket.binding),
  };
}

export default defineConfig({
  test: {
    projects: [
      {
        // Runs in workerd. OWNER is bound for the tests only: wrangler.jsonc gives the
        // Worker no binding to its own namespace. AGENT and MALFORMED are the test
        // Worker's own classes of the object (test/worker.ts), its agent on faux models.
        // FILES is the config's own binding, to the files Worker run beside it.
        plugins: [
          cloudflareTest({
            main: "./test/worker.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              durableObjects: {
                OWNER: { className: "OwnerDO", useSQLite: true },
                AGENT: { className: "FauxOwnerDO", useSQLite: true },
                MALFORMED: { className: "MalformedOwnerDO", useSQLite: true },
              },
              workers: [filesWorker()],
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
