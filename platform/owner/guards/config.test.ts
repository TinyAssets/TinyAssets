/**
 * The owner Worker's config binds nothing (ADR-032).
 *
 * `OwnerDO` must have no way to another owner's object, so its Worker's config is an
 * allow-list, at top level and in every environment: the keys that name the Worker,
 * its entry, its runtime and its SQLite class migration, and nothing that binds (no
 * Durable Object namespace, service, store, queue, var or any other binding wrangler
 * knows). Its compatibility flags carry `disable_ctx_exports`, so the runtime does not
 * hand the object its own namespaces as `ctx.exports`. It is the only wrangler config
 * in the project. Edge Workers bind `OWNER` from their own configs.
 */
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "./sources";

const OWNER = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG = "wrangler.jsonc";
const NO_EXPORTS = "disable_ctx_exports";

/** The keys an environment may set: none of them binds anything. */
const ENV_KEYS: ReadonlySet<string> = new Set([
  "name", "main", "compatibility_date", "compatibility_flags", "workers_dev", "preview_urls",
  "migrations",
]);
/** The keys the top level may set: an environment's, the schema, and the environments. */
const TOP_KEYS: ReadonlySet<string> = new Set([...ENV_KEYS, "$schema", "env"]);

type Config = Record<string, unknown>;

function parseConfig(text: string): Config {
  const parsed = ts.parseConfigFileTextToJson(CONFIG, text);
  if (parsed.error) {
    throw new Error(ts.flattenDiagnosticMessageText(parsed.error.messageText, "\n"));
  }
  return parsed.config as Config;
}

function object(value: unknown): value is Config {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Each way `config` lets the owner Worker reach past its own object. */
export function configProblems(config: Config): string[] {
  const problems: string[] = [];
  const check = (scope: Config, where: string, allowed: ReadonlySet<string>, top: boolean) => {
    for (const key of Object.keys(scope)) {
      if (!allowed.has(key)) {
        problems.push(`${where}${key} is not an allowed key`);
      }
    }
    const flags = scope["compatibility_flags"];
    if (flags !== undefined || top) {
      if (!Array.isArray(flags) || !flags.includes(NO_EXPORTS)) {
        problems.push(`${where}compatibility_flags lacks ${NO_EXPORTS}`);
      } else if (flags.some((flag) => flag !== NO_EXPORTS && /ctx_exports/.test(String(flag)))) {
        problems.push(`${where}compatibility_flags turns ctx.exports back on`);
      }
    }
  };
  check(config, "", TOP_KEYS, true);
  const envs = config["env"] ?? {};
  if (!object(envs)) {
    problems.push("env is not a table of environments");
  } else {
    for (const [name, env] of Object.entries(envs)) {
      if (object(env)) {
        check(env, `env.${name}.`, ENV_KEYS, false);
      } else {
        problems.push(`env.${name} is not an environment`);
      }
    }
  }
  return problems;
}

/** Every wrangler config file under `root` (not dependencies), by path from `root`. */
export function wranglerConfigs(root: string): string[] {
  return readdirSync(root, { recursive: true, encoding: "utf8" })
    .map((path) => path.split("\\").join("/"))
    .filter((path) => !path.split("/").some((part) => part === "node_modules")
                      && /^wrangler\./i.test(path.split("/").at(-1) ?? ""))
    .sort();
}

const config = parseConfig(readFileSync(join(OWNER, CONFIG), "utf8"));

describe("owner Worker config", () => {
  it("binds nothing and turns ctx.exports off, at top level and in every environment", () => {
    expect(configProblems(config)).toEqual([]);
    expect(config["compatibility_flags"]).toContain(NO_EXPORTS);
  });

  it("is the only wrangler config in the owner Worker", () => {
    expect(wranglerConfigs(OWNER)).toEqual([CONFIG]);
    expect(relative(PLATFORM, OWNER)).toBe("owner");
  });

  it("declares OwnerDO a SQLite-backed class", () => {
    const migrations = (config["migrations"] ?? []) as
      { new_sqlite_classes?: string[]; new_classes?: string[] }[];
    expect(migrations.flatMap((m) => m.new_sqlite_classes ?? [])).toContain("OwnerDO");
    expect(migrations.flatMap((m) => m.new_classes ?? [])).not.toContain("OwnerDO");
  });

  it("allows only keys wrangler knows, and none of the binding keys", () => {
    const schema = JSON.parse(readFileSync(
      join(PLATFORM, "node_modules", "wrangler", "config-schema.json"), "utf8")) as
      { definitions: Record<string, { properties: Record<string, unknown> }> };
    const top = Object.keys(schema.definitions["RawConfig"]!.properties);
    const env = Object.keys(schema.definitions["RawEnvironment"]!.properties);
    expect([...TOP_KEYS].filter((key) => !top.includes(key))).toEqual([]);
    expect([...ENV_KEYS].filter((key) => !env.includes(key))).toEqual([]);
    // Every key wrangler offers beyond the allow-list is refused, bindings among them.
    for (const key of ["durable_objects", "services", "kv_namespaces", "r2_buckets",
                       "d1_databases", "queues", "vars", "ai", "workflows", "browser",
                       "vectorize", "hyperdrive", "analytics_engine_datasets",
                       "dispatch_namespaces", "mtls_certificates", "send_email",
                       "tail_consumers", "unsafe", "secrets_store_secrets", "worker_loaders",
                       "exports", "define"]) {
      expect(top).toContain(key);
      expect(env).toContain(key);
      expect(TOP_KEYS.has(key) || ENV_KEYS.has(key)).toBe(false);
    }
  });

  const base = { name: "tinyassets-owner", main: "src/index.ts",
                 compatibility_flags: ["nodejs_compat", NO_EXPORTS] };

  it.each([
    ["a self-binding", { ...base, durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO" }] } }],
    ["a binding inside an environment", { ...base, env: { staging: {
      durable_objects: { bindings: [{ name: "OWNER", class_name: "OwnerDO" }] } } } }],
    ["a services binding", { ...base, services: [{ binding: "SELF", service: "tinyassets-owner" }] }],
    ["a services binding inside an environment", { ...base, env: { prod: {
      services: [{ binding: "EDGE", service: "tinyassets-edge" }] } } }],
    ["a var", { ...base, vars: { OWNER: "alice" } }],
    ["an unsafe binding", { ...base, unsafe: { bindings: [] } }],
    ["a key wrangler adds later", { ...base, agent_memory: [] }],
    ["no ctx.exports flag", { ...base, compatibility_flags: ["nodejs_compat"] }],
    ["no flags at all", { name: "tinyassets-owner" }],
    ["an environment's flags without it", { ...base, env: { prod: {
      compatibility_flags: ["nodejs_compat"] } } }],
    ["ctx.exports turned back on", { ...base, compatibility_flags: [NO_EXPORTS,
      "enable_ctx_exports"] }],
    ["an environment that is not a table", { ...base, env: { prod: [] } }],
  ])("sees %s", (_what, probe) => {
    expect(configProblems(probe as Config)).not.toEqual([]);
  });

  it("allows an environment that only renames and re-dates", () => {
    expect(configProblems({ ...base, env: { staging: { name: "tinyassets-owner-staging",
      compatibility_date: "2026-10-23" } } })).toEqual([]);
  });

  it("sees a second config file", () => {
    const root = mkdtempSync(join(tmpdir(), "owner-config-"));
    try {
      mkdirSync(join(root, "src"), { recursive: true });
      mkdirSync(join(root, "node_modules", "dep"), { recursive: true });
      for (const path of [CONFIG, "wrangler.toml", "src/wrangler.json",
                          "node_modules/dep/wrangler.toml"]) {
        writeFileSync(join(root, path), "");
      }
      expect(wranglerConfigs(root)).toEqual(["src/wrangler.json", CONFIG, "wrangler.toml"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
