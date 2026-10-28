/**
 * The owner Worker's config binds nothing that reaches another owner's object (ADR-032).
 *
 * `OwnerDO` must have no way to another owner's object, so its Worker's config is an
 * allow-list, at top level and in every environment: the keys that name the Worker, its
 * entry, its runtime and its SQLite class migration, and no binding but service bindings
 * to Workers that themselves bind no Durable Object namespace and no service, checked
 * against the bound Worker's own config. `SERVICES` names the ones allowed: the files
 * Worker today, the gateway with its slice. No Durable Object namespace, store, queue,
 * var or any other binding wrangler knows. Its compatibility flags carry
 * `disable_ctx_exports`, so the runtime does not hand the object its own namespaces as
 * `ctx.exports`. It is the only wrangler config in the owner Worker. Edge Workers bind
 * `OWNER` from their own configs.
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

/** The keys an environment may set besides `services`: none of them binds anything. */
const ENV_KEYS: ReadonlySet<string> = new Set([
  "name", "main", "compatibility_date", "compatibility_flags", "workers_dev", "preview_urls",
  "migrations",
]);
/** The keys the top level may set: an environment's, the schema, and the environments. */
const TOP_KEYS: ReadonlySet<string> = new Set([...ENV_KEYS, "$schema", "env"]);
/** The service bindings the owner Worker may hold: each binding's name and its Worker. */
const SERVICES: Readonly<Record<string, string>> = Object.freeze({ FILES: "tinyassets-files" });
/** What a bound Worker's config never holds, anywhere: a way on to objects or services. */
const REACHING: readonly string[] = Object.freeze(["durable_objects", "services"]);

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

/** Each way a bound Worker's config could reach an object or another service. */
export function reachProblems(config: Config): string[] {
  const scopes: [string, Config][] = [["", config]];
  const envs = config["env"];
  if (envs !== undefined && !object(envs)) {
    return ["env is not a table of environments"];
  }
  for (const [name, env] of Object.entries(envs ?? {})) {
    if (!object(env)) {
      return [`env.${name} is not an environment`];
    }
    scopes.push([`env.${name}.`, env]);
  }
  return scopes.flatMap(([where, scope]) => REACHING.filter((key) => key in scope)
    .map((key) => `${where}${key} reaches past the bound Worker`));
}

/**
 * Each problem with a scope's service bindings: each must be one `SERVICES` allows, to a
 * Worker whose config (`bound`, by Worker name) is in the platform and reaches nothing.
 */
function serviceProblems(services: unknown, where: string,
                         bound: ReadonlyMap<string, Config>): string[] {
  if (!Array.isArray(services)) {
    return [`${where}services is not a list`];
  }
  return services.flatMap((service: unknown) => {
    if (!object(service) || Object.keys(service).sort().join() !== "binding,service") {
      return [`${where}services holds more than a binding and a service name`];
    }
    const { binding, service: name } = service;
    if (typeof binding !== "string" || typeof name !== "string"
        || !Object.hasOwn(SERVICES, binding) || SERVICES[binding] !== name) {
      return [`${where}services binds ${String(binding)} to ${String(name)}, not allowed`];
    }
    const target = bound.get(name);
    if (target === undefined) {
      return [`${where}services binds ${name}, whose config is not in the platform`];
    }
    return reachProblems(target).map((problem) => `${where}services.${binding}: ${problem}`);
  });
}

/** Each way `config` lets the owner Worker reach past its own object. */
export function configProblems(config: Config,
                               bound: ReadonlyMap<string, Config> = new Map()): string[] {
  const problems: string[] = [];
  const check = (scope: Config, where: string, allowed: ReadonlySet<string>, top: boolean) => {
    for (const key of Object.keys(scope)) {
      if (key === "services") {
        problems.push(...serviceProblems(scope[key], where, bound));
      } else if (!allowed.has(key)) {
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

/** Every Worker config in the platform, by its Worker's name. */
export function platformConfigs(): Map<string, Config> {
  const found = new Map<string, Config>();
  for (const path of wranglerConfigs(PLATFORM)) {
    const parsed = parseConfig(readFileSync(join(PLATFORM, path), "utf8"));
    found.set(String(parsed["name"]), parsed);
  }
  return found;
}

const config = parseConfig(readFileSync(join(OWNER, CONFIG), "utf8"));

describe("owner Worker config", () => {
  it("binds only FILES, to a Worker that reaches nothing, and turns ctx.exports off", () => {
    expect(configProblems(config, platformConfigs())).toEqual([]);
    expect(config["compatibility_flags"]).toContain(NO_EXPORTS);
    expect(config["services"]).toEqual([{ binding: "FILES", service: "tinyassets-files" }]);
  });

  it("refuses FILES when the files Worker's config is not in the platform", () => {
    expect(configProblems(config)).toEqual(
      ["services binds tinyassets-files, whose config is not in the platform"]);
  });

  const files = { name: "tinyassets-files", r2_buckets: [{ binding: "BLOBS" }] };
  it.each([
    ["a Durable Object namespace", { ...files, durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["a service", { ...files, services: [{ binding: "OWNER", service: "tinyassets-owner" }] }],
    ["a service inside an environment", { ...files, env: { prod: {
      services: [{ binding: "EDGE", service: "tinyassets-edge" }] } } }],
    ["a Durable Object inside an environment", { ...files, env: { prod: {
      durable_objects: { bindings: [] } } } }],
    ["an environment that is not a table", { ...files, env: { prod: [] } }],
    ["environments that are not a table", { ...files, env: [] }],
  ])("refuses FILES when the files Worker binds %s", (_what, target) => {
    expect(configProblems(config, new Map([["tinyassets-files", target as Config]])))
      .not.toEqual([]);
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

  it("allows only keys wrangler knows, and none of the other binding keys", () => {
    const schema = JSON.parse(readFileSync(
      join(PLATFORM, "node_modules", "wrangler", "config-schema.json"), "utf8")) as
      { definitions: Record<string, { properties: Record<string, unknown> }> };
    const top = Object.keys(schema.definitions["RawConfig"]!.properties);
    const env = Object.keys(schema.definitions["RawEnvironment"]!.properties);
    expect([...TOP_KEYS, "services"].filter((key) => !top.includes(key))).toEqual([]);
    expect([...ENV_KEYS, "services"].filter((key) => !env.includes(key))).toEqual([]);
    // Every key wrangler offers beyond the allow-list is refused, bindings among them.
    for (const key of ["durable_objects", "kv_namespaces", "r2_buckets",
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
  const bound = new Map([["tinyassets-files", files as Config]]);

  it.each([
    ["a self-binding", { ...base, durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO" }] } }],
    ["a binding inside an environment", { ...base, env: { staging: {
      durable_objects: { bindings: [{ name: "OWNER", class_name: "OwnerDO" }] } } } }],
    ["a services binding", { ...base, services: [{ binding: "SELF", service: "tinyassets-owner" }] }],
    ["a services binding inside an environment", { ...base, env: { prod: {
      services: [{ binding: "EDGE", service: "tinyassets-edge" }] } } }],
    ["FILES bound to another Worker", { ...base, services: [
      { binding: "FILES", service: "tinyassets-owner" }] }],
    ["the files Worker under another name", { ...base, services: [
      { binding: "BYTES", service: "tinyassets-files" }] }],
    ["FILES with an entrypoint", { ...base, services: [
      { binding: "FILES", service: "tinyassets-files", entrypoint: "Other" }] }],
    ["services that are not a list", { ...base, services: { FILES: "tinyassets-files" } }],
    ["the bucket bound directly", { ...base, r2_buckets: [{ binding: "BLOBS",
      bucket_name: "tinyassets-files" }] }],
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
    expect(configProblems(probe as Config, bound)).not.toEqual([]);
  });

  it("allows an environment that only renames and re-dates", () => {
    expect(configProblems({ ...base, env: { staging: { name: "tinyassets-owner-staging",
      compatibility_date: "2026-10-23" } } })).toEqual([]);
  });

  it("allows FILES at top level and in an environment, to a Worker that reaches nothing", () => {
    const services = [{ binding: "FILES", service: "tinyassets-files" }];
    expect(configProblems({ ...base, services, env: { prod: { services } } }, bound))
      .toEqual([]);
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
