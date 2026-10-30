import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cloudflareTest } from "@cloudflare/vitest-plugin";
import ts from "typescript";
import { defineConfig } from "vitest/config";

const PLATFORM = join(dirname(fileURLToPath(import.meta.url)), "..");
const FILES = join(PLATFORM, "files");
const GATEWAY = join(PLATFORM, "gateway");

type WorkerConfig = { name: string; compatibility_date: string; compatibility_flags: string[];
                      r2_buckets?: { binding: string }[] };

function config(dir: string): WorkerConfig {
  return ts.parseConfigFileTextToJson(
    "wrangler.jsonc", readFileSync(join(dir, "wrangler.jsonc"), "utf8")).config as WorkerConfig;
}

function transpiled(path: string): string {
  return ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
}

/**
 * The real files Worker, as the FILES service binding's target: its own source, its own
 * config's runtime and its one bucket. Miniflare runs auxiliary Workers from JavaScript,
 * so its modules are transpiled here, named as their sources import them.
 */
function filesWorker() {
  const files = config(FILES);
  const modules = ["index", "keys"].map((name) => ({
    type: "ESModule" as const,
    path: join(FILES, "src", name),
    contents: transpiled(join(FILES, "src", `${name}.ts`)),
  }));
  return {
    name: files.name,
    modulesRoot: FILES,
    modules,
    compatibilityDate: files.compatibility_date,
    compatibilityFlags: files.compatibility_flags,
    r2Buckets: (files.r2_buckets ?? []).map((bucket) => bucket.binding),
  };
}

/**
 * The real gateway Worker, as the GATEWAY service binding's target: bundled by wrangler
 * from its own config, as a deploy bundles it (pi-ai and its provider SDKs in one module),
 * with its own runtime and flags, the test keyring as its one secret, and every global
 * fetch it makes going to the gateway's own fake upstream.
 */
function gatewayWorker(keyring: string) {
  const gateway = config(GATEWAY);
  const out = mkdtempSync(join(tmpdir(), "owner-gateway-"));
  try {
    const wrangler = join(dirname(createRequire(join(GATEWAY, "package.json"))
      .resolve("wrangler/package.json")), "bin", "wrangler.js");
    execFileSync(process.execPath, [wrangler, "deploy", "--dry-run", "--outdir", out,
                                    "--config", join(GATEWAY, "wrangler.jsonc")],
                 { cwd: GATEWAY, stdio: "pipe",
                   env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
    return {
      name: gateway.name,
      modulesRoot: GATEWAY,
      modules: [{ type: "ESModule" as const, path: join(GATEWAY, "index.js"),
                  contents: readFileSync(join(out, "index.js"), "utf8") }],
      compatibilityDate: gateway.compatibility_date,
      compatibilityFlags: gateway.compatibility_flags,
      bindings: { GATEWAY_KEYRING: keyring },
      outboundService: "fake-upstream",
    };
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/** The gateway's fake outside world (`gateway/test/fake-upstream.ts`), transpiled. */
function fakeUpstream() {
  const path = join(GATEWAY, "test", "fake-upstream.ts");
  return {
    name: "fake-upstream",
    modulesRoot: GATEWAY,
    modules: [{ type: "ESModule" as const, path: join(GATEWAY, "test", "fake-upstream.js"),
                contents: transpiled(path) }],
    compatibilityDate: config(GATEWAY).compatibility_date,
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
        // Runs in workerd. OWNER is bound for the tests only: wrangler.jsonc gives the
        // Worker no binding to its own namespace. AGENT, MALFORMED and GATED are the test
        // Worker's own classes of the object (test/worker.ts): its agent on faux models,
        // with a package pi refuses, and on the production gateway provider with its
        // gateway calls watched. FILES and GATEWAY are the config's own bindings, to the
        // files and gateway Workers run beside it; PISTORE is the config's own namespace of
        // thread stores, the real class. TEST_KEYRING is the gateway's keyring,
        // so a test can seal under an older kid; the tests' own fetches reach the fake.
        plugins: [
          cloudflareTest({
            main: "./test/worker.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              durableObjects: {
                OWNER: { className: "OwnerDO", useSQLite: true },
                AGENT: { className: "FauxOwnerDO", useSQLite: true },
                MALFORMED: { className: "MalformedOwnerDO", useSQLite: true },
                GATED: { className: "GatedOwnerDO", useSQLite: true },
                PISTORE: { className: "ThreadStore", useSQLite: true },
              },
              bindings: { TEST_KEYRING: KEYRING },
              outboundService: "fake-upstream",
              workers: [filesWorker(), gatewayWorker(KEYRING), fakeUpstream()],
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
