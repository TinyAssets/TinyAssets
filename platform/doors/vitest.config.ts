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

const DOORS = dirname(fileURLToPath(import.meta.url));
const GATEWAY = join(DOORS, "..", "gateway");

type WorkerConfig = { name: string; compatibility_date: string; compatibility_flags: string[] };

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

/** A keyring made fresh for each run. */
const KEYRING = JSON.stringify({ active: "k1", keys: { k1: randomBytes(32).toString("base64") } });

export default defineConfig({
  test: {
    projects: [
      {
        // Runs in workerd, against the config's own KV namespaces, flags and GATEWAY
        // binding. The test entry serves the doors with a fake upstream sign-in and the
        // records with a test source beside them, and exports the real OwnerDO, so OWNER,
        // which the config binds by script_name, is bound to it here; the fake upstream's
        // issuer stands in for AuthKit's as the owner issuer. GATEWAY reaches the real
        // gateway Worker, whose fetches reach the fake providers; PROVIDERS is the tests'
        // own way to them, to read back what they received.
        plugins: [
          cloudflareTest({
            main: "./test/entry.ts",
            wrangler: { configPath: "./wrangler.jsonc" },
            miniflare: {
              durableObjects: { OWNER: { className: "OwnerDO", useSQLite: true } },
              bindings: { OWNER_ISSUER: "https://upstream.test" },
              serviceBindings: { PROVIDERS: "fake-providers" },
              workers: [gatewayWorker(KEYRING), fakeProviders()],
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
