/**
 * The owner Worker's config binds nothing that reaches another owner's object (ADR-032).
 *
 * `OwnerDO` must have no way to another owner's object, so its Worker's config is an
 * allow-list, at top level and in every environment: the keys that name the Worker, its
 * entry, its runtime and its SQLite class migration, and no binding but the service
 * bindings `SERVICES` names. Each names one Worker and exactly the binding kinds that
 * Worker's own config may hold, each a kind that reaches no object, service, queue,
 * workflow or other Worker: for `FILES`, the files Worker and its one R2 bucket. The
 * bound Worker's config is checked against that allow-list too, so any other binding it
 * gains (a Durable Object, a service, a queue, a workflow, a dispatch namespace, a
 * Worker loader, a tail consumer, a shared store, an unsafe binding) refuses the owner's
 * binding. The gateway joins `SERVICES` with its slice. Its compatibility flags carry
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
/** The keys a bound Worker may set that bind nothing. */
const BOUND_KEYS: ReadonlySet<string> = new Set([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "workers_dev",
  "preview_urls",
]);

interface Bound {
  /** The Worker the binding names. */
  readonly service: string;
  /** The binding kinds its own config may hold, and how many of each, exactly. */
  readonly bindings: Readonly<Record<string, number>>;
}

/** The service bindings the owner Worker may hold, by binding name. */
const SERVICES: Readonly<Record<string, Bound>> = Object.freeze({
  FILES: Object.freeze({ service: "tinyassets-files",
                         bindings: Object.freeze({ r2_buckets: 1 }) }),
});

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

/**
 * Each way a bound Worker's config holds more than `allowed` lets it: a key that binds
 * anything else, a count other than the allowed one, or an environment (which could
 * bind anything).
 */
export function boundProblems(config: Config, allowed: Bound): string[] {
  const problems = Object.keys(config)
    .filter((key) => !BOUND_KEYS.has(key) && !Object.hasOwn(allowed.bindings, key))
    .map((key) => `${key} is not a binding the owner's ${allowed.service} binding allows`);
  for (const [key, many] of Object.entries(allowed.bindings)) {
    const held = config[key];
    if (!Array.isArray(held) || held.length !== many) {
      problems.push(`${key} holds other than ${many}`);
    }
  }
  return problems;
}

/**
 * Each problem with a scope's service bindings: each must be one `SERVICES` allows, to
 * the Worker it names, whose config (`bound`, by Worker name) is in the platform and
 * binds only what that entry allows.
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
    const allowed = typeof binding === "string" && Object.hasOwn(SERVICES, binding)
      ? SERVICES[binding]! : undefined;
    if (allowed === undefined || allowed.service !== name) {
      return [`${where}services binds ${String(binding)} to ${String(name)}, not allowed`];
    }
    const target = bound.get(name);
    if (target === undefined) {
      return [`${where}services binds ${name}, whose config is not in the platform`];
    }
    return boundProblems(target, allowed)
      .map((problem) => `${where}services.${binding}: ${problem}`);
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
  it("binds only FILES, to a Worker that binds one bucket, and turns ctx.exports off", () => {
    expect(configProblems(config, platformConfigs())).toEqual([]);
    expect(config["compatibility_flags"]).toContain(NO_EXPORTS);
    expect(config["services"]).toEqual([{ binding: "FILES", service: "tinyassets-files" }]);
  });

  it("refuses FILES when the files Worker's config is not in the platform", () => {
    expect(configProblems(config)).toEqual(
      ["services binds tinyassets-files, whose config is not in the platform"]);
  });

  it("refuses FILES bound to another platform Worker, even one that binds nothing", () => {
    const other = { name: "tinyassets-other", main: "src/index.ts" };
    const services = [{ binding: "FILES", service: "tinyassets-other" }];
    expect(configProblems({ ...config, services },
                          new Map([["tinyassets-other", other as Config]])))
      .toEqual(["services binds FILES to tinyassets-other, not allowed"]);
  });

  const files = { name: "tinyassets-files", main: "src/index.ts",
                  r2_buckets: [{ binding: "BLOBS", bucket_name: "tinyassets-files" }] };
  it.each([
    ["a Durable Object namespace", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["a service", { services: [{ binding: "OWNER", service: "tinyassets-owner" }] }],
    ["a queue producer", { queues: { producers: [{ binding: "Q", queue: "q" }] } }],
    ["a workflow", { workflows: [{ binding: "W", name: "w", class_name: "W",
                                   script_name: "tinyassets-owner" }] }],
    ["a dispatch namespace", { dispatch_namespaces: [{ binding: "D", namespace: "d" }] }],
    ["a Worker loader", { worker_loaders: [{ binding: "LOADER" }] }],
    ["an unsafe Durable Object binding", { unsafe: { bindings: [{ name: "OWNER",
      type: "durable_object_namespace", class_name: "OwnerDO" }] } }],
    ["a tail consumer", { tail_consumers: [{ service: "tinyassets-owner" }] }],
    ["a shared KV namespace", { kv_namespaces: [{ binding: "KV", id: "shared" }] }],
    ["a shared D1 database", { d1_databases: [{ binding: "DB", database_id: "shared" }] }],
    ["a second bucket", { r2_buckets: [...files.r2_buckets,
                                       { binding: "OTHER", bucket_name: "other" }] }],
    ["no bucket", { r2_buckets: [] }],
    ["a var", { vars: { NS: "x" } }],
    ["a migration", { migrations: [{ tag: "v1", new_sqlite_classes: ["X"] }] }],
    ["an environment", { env: { prod: { name: "tinyassets-files-prod" } } }],
  ])("refuses FILES when the files Worker holds %s", (_what, extra) => {
    expect(configProblems(config, new Map([["tinyassets-files",
                                            { ...files, ...extra } as Config]])))
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
