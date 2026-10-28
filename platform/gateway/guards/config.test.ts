/**
 * The gateway Worker's config binds nothing and serves no URL (research §1.4, §12 G1).
 *
 * The gateway holds every owner's credentials' only key, so it must reach no owner's
 * object, no store and no other Worker, and nothing may reach it but the service bindings
 * of the owner and doors Workers. Its config is an allow-list, mirroring the owner's:
 * the keys that name the Worker, its entry and its runtime, and `secrets`, which declares
 * the keyring (a declaration that binds nothing). Every other key wrangler's schema
 * offers is refused: each binding kind, `routes` and `route`, a custom domain, `vars`,
 * environments (which could bind anything), migrations, and any key wrangler adds later.
 * `workers_dev` and `preview_urls` are false. The flags are exactly `nodejs_compat`,
 * `nodejs_compat_do_not_populate_process_env` (so the keyring never sits in
 * `process.env`), `disable_ctx_exports` and `global_fetch_strictly_public`. It is the
 * gateway's only wrangler config.
 */
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";

const GATEWAY = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG = "wrangler.jsonc";
const FLAGS: readonly string[] = Object.freeze(["nodejs_compat",
                                                "nodejs_compat_do_not_populate_process_env",
                                                "disable_ctx_exports",
                                                "global_fetch_strictly_public"]);
const KEYS: readonly string[] = Object.freeze([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "workers_dev",
  "preview_urls", "secrets",
]);

type Config = Record<string, unknown>;

function parseConfig(text: string): Config {
  const parsed = ts.parseConfigFileTextToJson(CONFIG, text);
  if (parsed.error) {
    throw new Error(ts.flattenDiagnosticMessageText(parsed.error.messageText, "\n"));
  }
  return parsed.config as Config;
}

/** Each way `config` binds anything, serves a URL, or runs other than declared. */
export function gatewayConfigProblems(config: Config): string[] {
  const problems: string[] = [];
  for (const key of Object.keys(config)) {
    if (!KEYS.includes(key)) {
      problems.push(`${key} is not an allowed key`);
    }
  }
  if (config["name"] !== "tinyassets-gateway" || config["main"] !== "src/index.ts") {
    problems.push("the Worker is tinyassets-gateway at src/index.ts");
  }
  if (config["workers_dev"] !== false || config["preview_urls"] !== false) {
    problems.push("workers_dev and preview_urls are false");
  }
  const flags = config["compatibility_flags"];
  if (!Array.isArray(flags) || flags.length !== FLAGS.length
      || FLAGS.some((flag) => !flags.includes(flag))) {
    problems.push(`compatibility_flags are exactly ${FLAGS.join(", ")}`);
  }
  if (JSON.stringify(config["secrets"]) !== JSON.stringify({ required: ["GATEWAY_KEYRING"] })) {
    problems.push("secrets declares exactly GATEWAY_KEYRING");
  }
  return problems;
}

function wranglerConfigs(root: string): string[] {
  return readdirSync(root, { recursive: true, encoding: "utf8" })
    .map((path) => path.split("\\").join("/"))
    .filter((path) => !path.split("/").includes("node_modules")
                      && /^wrangler\./i.test(path.split("/").at(-1) ?? ""))
    .sort();
}

const config = parseConfig(readFileSync(join(GATEWAY, CONFIG), "utf8"));
const schema = JSON.parse(readFileSync(
  join(PLATFORM, "node_modules", "wrangler", "config-schema.json"), "utf8")) as
  { definitions: Record<string, { properties: Record<string, unknown> }> };
const offered = Object.keys(schema.definitions["RawConfig"]!.properties);

describe("gateway Worker config", () => {
  it("binds nothing, serves no URL, and declares only its keyring", () => {
    expect(gatewayConfigProblems(config)).toEqual([]);
    expect(config["secrets"]).toEqual({ required: ["GATEWAY_KEYRING"] });
  });

  it("allows only keys wrangler knows", () => {
    expect(KEYS.filter((key) => !offered.includes(key))).toEqual([]);
    expect(schema.definitions["RawConfig"]!.properties["secrets"]).toBeDefined();
  });

  it("refuses every other key wrangler offers, each binding and route among them", () => {
    const others = offered.filter((key) => !KEYS.includes(key));
    for (const key of ["routes", "route", "durable_objects", "kv_namespaces", "r2_buckets",
                       "d1_databases", "services", "queues", "vars", "ai", "workflows",
                       "vectorize", "hyperdrive", "analytics_engine_datasets",
                       "dispatch_namespaces", "tail_consumers", "unsafe",
                       "secrets_store_secrets", "worker_loaders", "env", "migrations"]) {
      expect(others).toContain(key);
    }
    for (const key of others) {
      expect(gatewayConfigProblems({ ...config, [key]: {} })).toContain(`${key} is not an allowed key`);
    }
  });

  it.each([
    ["a workers.dev URL", { workers_dev: true }],
    ["a preview URL", { preview_urls: true }],
    ["no workers_dev setting", { workers_dev: undefined }],
    ["another secret", { secrets: { required: ["GATEWAY_KEYRING", "OTHER"] } }],
    ["no secret", { secrets: undefined }],
    ["process.env populated", { compatibility_flags: ["nodejs_compat", "disable_ctx_exports",
                                                      "global_fetch_strictly_public"] }],
    ["ctx.exports on", { compatibility_flags: ["nodejs_compat",
                                               "nodejs_compat_do_not_populate_process_env",
                                               "global_fetch_strictly_public"] }],
    ["private-origin fetch", { compatibility_flags: ["nodejs_compat",
                                                     "nodejs_compat_do_not_populate_process_env",
                                                     "disable_ctx_exports"] }],
    ["an extra flag", { compatibility_flags: [...FLAGS, "enable_ctx_exports"] }],
    ["a flag swapped for another", { compatibility_flags: [...FLAGS.slice(0, -1),
                                                           "global_fetch_private_origin"] }],
    ["another name", { name: "tinyassets-owner" }],
    ["a key wrangler adds later", { agent_memory: [] }],
  ])("sees %s", (_what, patch) => {
    const probe: Config = { ...config, ...patch };
    for (const [key, value] of Object.entries(patch)) {
      if (value === undefined) {
        delete probe[key];
      }
    }
    expect(gatewayConfigProblems(probe)).not.toEqual([]);
  });

  it("is the only wrangler config in the gateway", () => {
    expect(wranglerConfigs(GATEWAY)).toEqual([CONFIG]);
    const root = mkdtempSync(join(tmpdir(), "gateway-config-"));
    try {
      mkdirSync(join(root, "src"), { recursive: true });
      for (const path of [CONFIG, "src/wrangler.toml"]) {
        writeFileSync(join(root, path), "");
      }
      expect(wranglerConfigs(root)).toEqual(["src/wrangler.toml", CONFIG]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
