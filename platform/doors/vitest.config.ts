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

import { LARGE, medium, REPORTERS } from "../ci/classes";

const DOORS = dirname(fileURLToPath(import.meta.url));
const GATEWAY = join(DOORS, "..", "gateway");
const HOOKS = join(DOORS, "..", "hooks");
const FILES = join(DOORS, "..", "files");

type WorkerConfig = {
  name: string; compatibility_date: string; compatibility_flags: string[];
  r2_buckets?: { binding: string }[];
  durable_objects?: { bindings: { name: string; class_name: string }[] };
};

function config(dir: string): WorkerConfig {
  return ts.parseConfigFileTextToJson(
    "wrangler.jsonc", readFileSync(join(dir, "wrangler.jsonc"), "utf8")).config as WorkerConfig;
}

/**
 * The real gateway Worker, as the GATEWAY service binding's target (its `ConnectGateway`
 * entrypoint, as the doors config names it): bundled by wrangler from its own config, as
 * a deploy bundles it, with its own runtime and flags, a test keyring as its one secret,
 * and every global fetch it makes going to the doors' fake providers.
 */
function gatewayWorker(keyring: string) {
  const gateway = config(GATEWAY);
  const out = mkdtempSync(join(tmpdir(), "doors-gateway-"));
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
      outboundService: "fake-providers",
    };
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/**
 * The real hooks Worker, as the owner object's HOOKS service binding (code mode's VMs, the
 * `code` tool's): bundled by wrangler from its own config, as a deploy bundles it, with its
 * own runtime, flags and class, as the owner's tests run it.
 */
function hooksWorker() {
  const hooks = config(HOOKS);
  const out = mkdtempSync(join(tmpdir(), "doors-hooks-"));
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
 * The real files Worker, as the FILES service binding's target (the doors' file bytes, and
 * the owner object's in this script, whose export's generations are in R2): its own source,
 * transpiled, with its one bucket and a test keyring as its one secret.
 */
function filesWorker(keyring: string) {
  const files = config(FILES);
  return {
    name: files.name,
    modulesRoot: FILES,
    modules: ["index", "keys", "datakey", "cipher"].map((name) => ({
      type: "ESModule" as const,
      path: join(FILES, "src", name),
      contents: ts.transpileModule(readFileSync(join(FILES, "src", `${name}.ts`), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
      }).outputText,
    })),
    compatibilityDate: files.compatibility_date,
    compatibilityFlags: files.compatibility_flags,
    r2Buckets: (files.r2_buckets ?? []).map((bucket) => bucket.binding),
    bindings: { FILES_KEYRING: keyring },
  };
}

/** The providers a connect reaches (`test/fake-providers.ts`), transpiled. */
function fakeProviders() {
  const path = join(DOORS, "test", "fake-providers.ts");
  return {
    name: "fake-providers",
    modulesRoot: DOORS,
    modules: [{ type: "ESModule" as const, path: join(DOORS, "test", "fake-providers.js"),
                contents: ts.transpileModule(readFileSync(path, "utf8"), {
                  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
                }).outputText }],
    compatibilityDate: config(GATEWAY).compatibility_date,
  };
}

/** The sandbox tokens' routing key (`ROUTING_MAC_KEY`), made fresh for each run. */
const ROUTING_KEY = randomBytes(32).toString("base64url");

/** The shared inbound app's signing secret (`shared-apps.json`'s Slack), for the tests. */
const SLACK_SECRET = "8f742231b10e8888abcd99yyyzzz85a5";

/** A keyring made fresh for each run. */
const KEYRING = JSON.stringify({ active: "k1", keys: { k1: randomBytes(32).toString("base64") } });

export default defineConfig({
  test: {
    tags: [LARGE],
    reporters: [...REPORTERS],
    projects: [
      {
        // Runs in workerd, against the config's own KV namespaces, flags and GATEWAY
        // binding. The test entry serves the doors with a fake upstream sign-in and the
        // records with a test source beside them, and exports the real OwnerDO, so OWNER,
        // which the config binds by script_name, is bound to it here; the fake upstream's
        // issuer stands in for AuthKit's as the owner issuer. GATEWAY reaches the real
        // gateway Worker, whose fetches reach the fake providers; PROVIDERS is the tests'
        // own way to them, to read back what they received. ROUTING_MAC_KEY, the config's
        // one secret, is the owner Worker's too: here both are this Worker.
        plugins: [
          cloudflareTest({
            main: "./test/entry.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              durableObjects: { OWNER: { className: "OwnerDO", useSQLite: true } },
              bindings: { OWNER_ISSUER: "https://upstream.test", ROUTING_MAC_KEY: ROUTING_KEY,
                          E2B_WEBHOOK_SECRET: "test-e2b-webhook-signature-secret",
                          WORKOS_API_KEY: "test-client-secret",
                          INBOUND_SLACK_SIGNING_SECRET: SLACK_SECRET },
              // HOOKS and OWNER_FILES: the owner object's (in this script for the tests), to
              // the real hooks and files Workers, as the owner config binds them: the owner's
              // FILES is the files Worker's whole service, the doors' its upload entrypoint.
              serviceBindings: { PROVIDERS: "fake-providers", HOOKS: "tinyassets-hooks",
                                 OWNER_FILES: "tinyassets-files" },
              workers: [gatewayWorker(KEYRING), hooksWorker(), fakeProviders(),
                        filesWorker(KEYRING)],
            },
          }),
        ],
        test: { name: "workerd", ...medium("doors/workerd"), include: ["test/**/*.test.ts"] },
      },
      {
        // Static guards over the source and config, in Node.
        test: { name: "guards", ...medium("doors/guards"), include: ["guards/**/*.test.ts"],
                environment: "node" },
      },
    ],
  },
});
