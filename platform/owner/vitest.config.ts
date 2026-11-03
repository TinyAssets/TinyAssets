import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
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
const HOOKS = join(PLATFORM, "hooks");
const DOORBELL = join(PLATFORM, "doorbell");

type WorkerConfig = { name: string; compatibility_date: string; compatibility_flags: string[];
                      r2_buckets?: { binding: string }[];
                      kv_namespaces?: { binding: string }[];
                      durable_objects?: { bindings: { name: string; class_name: string }[] } };

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

/**
 * The real hooks Worker, as the HOOKS service binding's target: bundled by wrangler from its
 * own config, as a deploy bundles it (pi's code mode sandbox and the QuickJS module it
 * compiles), with its own runtime, flags and class.
 */
function hooksWorker() {
  const hooks = config(HOOKS);
  const out = mkdtempSync(join(tmpdir(), "owner-hooks-"));
  try {
    const wrangler = join(dirname(createRequire(join(HOOKS, "package.json"))
      .resolve("wrangler/package.json")), "bin", "wrangler.js");
    execFileSync(process.execPath, [wrangler, "deploy", "--dry-run", "--outdir", out,
                                    "--config", join(HOOKS, "wrangler.jsonc")],
                 { cwd: HOOKS, stdio: "pipe",
                   env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
    // The entry first: miniflare takes the first module as the main one.
    const built = readdirSync(out).filter((file) => /\.(?:js|wasm)$/.test(file))
      .sort((a, b) => Number(b === "index.js") - Number(a === "index.js")
                      || a.localeCompare(b));
    return {
      name: hooks.name,
      modulesRoot: HOOKS,
      modules: built.map((file) => ({
        type: file.endsWith(".wasm") ? "CompiledWasm" as const : "ESModule" as const,
        path: join(HOOKS, file),
        contents: readFileSync(join(out, file)),
      })),
      compatibilityDate: hooks.compatibility_date,
      compatibilityFlags: hooks.compatibility_flags,
      durableObjects: Object.fromEntries((hooks.durable_objects?.bindings ?? [])
        .map((binding) => [binding.name, { className: binding.class_name, useSQLite: true }])),
    };
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/**
 * The real doorbell Worker, as the DOORBELL service binding's target: bundled by wrangler
 * from its own config (it imports the doors' control record reader), with its own runtime
 * and flags, and its one binding, the directory, on the namespace the tests' own
 * `DIRECTORY` binding writes, so a test sets the kill switch the doorbell reads.
 */
function doorbellWorker() {
  const doorbell = config(DOORBELL);
  const out = mkdtempSync(join(tmpdir(), "owner-doorbell-"));
  try {
    const wrangler = join(dirname(createRequire(join(HOOKS, "package.json"))
      .resolve("wrangler/package.json")), "bin", "wrangler.js");
    execFileSync(process.execPath, [wrangler, "deploy", "--dry-run", "--outdir", out,
                                    "--config", join(DOORBELL, "wrangler.jsonc")],
                 { cwd: DOORBELL, stdio: "pipe",
                   env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
    return {
      name: doorbell.name,
      modulesRoot: DOORBELL,
      modules: [{ type: "ESModule" as const, path: join(DOORBELL, "index.js"),
                  contents: readFileSync(join(out, "index.js"), "utf8") }],
      compatibilityDate: doorbell.compatibility_date,
      compatibilityFlags: doorbell.compatibility_flags,
      kvNamespaces: Object.fromEntries((doorbell.kv_namespaces ?? [])
        .map((kv) => [kv.binding, DIRECTORY_NAMESPACE])),
    };
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/** The directory's namespace, which the tests and the doorbell Worker share. */
const DIRECTORY_NAMESPACE = "owner-tests-directory";

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

/** The sandbox tokens' routing key (`ROUTING_MAC_KEY`), made fresh for each run. */
const ROUTING_KEY = randomBytes(32).toString("base64url");

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
        // Worker no binding to its own namespace. AGENT, GATED, BOUNDED and RUNG are the test
        // Worker's own classes of the object (test/worker.ts): its internal paths in reach,
        // its gateway calls watched, code mode's bounds small, and its doorbell's requests
        // recorded. FILES, GATEWAY, HOOKS and DOORBELL are the config's own bindings, to the
        // files, gateway, hooks and doorbell Workers run beside it; DIRECTORY, the tests'
        // own, is the doorbell's directory, where a test sets the kill switch. TEST_KEYRING is the gateway's
        // keyring, so a test can seal under an older kid; the tests' own fetches reach the
        // fake. ROUTING_MAC_KEY is the config's one secret, the sandbox tokens' routing key.
        plugins: [
          cloudflareTest({
            main: "./test/worker.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              durableObjects: {
                OWNER: { className: "OwnerDO", useSQLite: true },
                AGENT: { className: "FauxOwnerDO", useSQLite: true },
                GATED: { className: "GatedOwnerDO", useSQLite: true },
                BOUNDED: { className: "BoundedOwnerDO", useSQLite: true },
                RUNG: { className: "RungOwnerDO", useSQLite: true },
              },
              kvNamespaces: { DIRECTORY: DIRECTORY_NAMESPACE },
              bindings: { TEST_KEYRING: KEYRING, ROUTING_MAC_KEY: ROUTING_KEY },
              outboundService: "fake-upstream",
              workers: [filesWorker(), gatewayWorker(KEYRING), hooksWorker(), doorbellWorker(),
                        fakeUpstream()],
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
