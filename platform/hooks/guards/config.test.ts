/**
 * The hooks Worker's config binds its Worker Loader and its own two classes, and serves no
 * URL (agent research §6.4; pi audit F8).
 *
 * Every owner's hook code and code mode script runs in this Worker, so it must reach no
 * owner's object, store, ledger or policy, and nothing but the owner Worker's `HOOKS`
 * service binding may reach it. Its config is an allow-list: the keys that name the
 * Worker, its entry and its runtime; `limits` (exactly `cpu_ms`, room above code mode's
 * interrupt budget); `worker_loaders`, exactly `LOADER`; `durable_objects`, exactly the
 * namespaces of its own `HookRunner` and `CodemodeSandbox` (no `script_name`, so no other
 * Worker's class, `OwnerDO` never); and `migrations` making exactly those two, SQLite.
 * Every other key wrangler's schema offers is refused: each other binding kind (a service,
 * a bucket, a KV namespace, a var, a secret), `routes`, environments, and any key wrangler
 * adds later. `workers_dev` and `preview_urls` are false; the one flag is
 * `disable_ctx_exports`. Its compatibility date is the owner Worker's, which every hook
 * bundle runs at too (`runner.ts`). It is the hooks Worker's only wrangler config.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";

const HOOKS = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG = "wrangler.jsonc";
const KEYS: readonly string[] = Object.freeze([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "workers_dev",
  "preview_urls", "limits", "worker_loaders", "durable_objects", "migrations",
]);
/** The Worker's own classes, and the namespaces it binds them as. */
const CLASSES: readonly string[] = Object.freeze(["HookRunner", "CodemodeSandbox"]);
const OBJECTS = Object.freeze({ bindings: Object.freeze([
  Object.freeze({ name: "RUNNER", class_name: "HookRunner" }),
  Object.freeze({ name: "CODEMODE", class_name: "CodemodeSandbox" })]) });
const LOADERS = Object.freeze([Object.freeze({ binding: "LOADER" })]);
const LIMITS = Object.freeze({ cpu_ms: 30_000 });

type Config = Record<string, unknown>;

function parseConfig(text: string): Config {
  const parsed = ts.parseConfigFileTextToJson(CONFIG, text);
  if (parsed.error) {
    throw new Error(ts.flattenDiagnosticMessageText(parsed.error.messageText, "\n"));
  }
  return parsed.config as Config;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Each way `config` binds more than its loader and its own classes, or serves a URL. */
export function hooksConfigProblems(config: Config, ownerDate: unknown): string[] {
  const problems: string[] = [];
  for (const key of Object.keys(config)) {
    if (!KEYS.includes(key)) {
      problems.push(`${key} is not an allowed key`);
    }
  }
  if (config["name"] !== "tinyassets-hooks" || config["main"] !== "src/index.ts") {
    problems.push("the Worker is tinyassets-hooks at src/index.ts");
  }
  if (config["workers_dev"] !== false || config["preview_urls"] !== false) {
    problems.push("workers_dev and preview_urls are false");
  }
  if (!same(config["compatibility_flags"], ["disable_ctx_exports"])) {
    problems.push("compatibility_flags are exactly disable_ctx_exports");
  }
  if (config["compatibility_date"] !== ownerDate
      || config["compatibility_date"] !== COMPATIBILITY_DATE) {
    problems.push("the compatibility date is the owner Worker's and the bundles'");
  }
  if (!same(config["worker_loaders"], LOADERS)) {
    problems.push("worker_loaders is exactly LOADER");
  }
  if (!same(config["durable_objects"], OBJECTS)) {
    problems.push("durable_objects binds other than RUNNER and CODEMODE, its own classes");
  }
  const made = (Array.isArray(config["migrations"]) ? config["migrations"] as Config[] : [])
    .flatMap((migration) => Object.entries(migration)).filter(([field]) => field !== "tag");
  if (!same(made, [["new_sqlite_classes", CLASSES]])) {
    problems.push(`migrations make other than ${CLASSES.join(" and ")}, SQLite`);
  }
  if (!same(config["limits"], LIMITS)) {
    problems.push(`limits are not ${JSON.stringify(LIMITS)}`);
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

const config = parseConfig(readFileSync(join(HOOKS, CONFIG), "utf8"));
/** The date `runner.ts` loads every bundle at, read from its source (it imports the runtime). */
const COMPATIBILITY_DATE = /export const COMPATIBILITY_DATE = "([0-9-]+)";/
  .exec(readFileSync(join(HOOKS, "src", "runner.ts"), "utf8"))?.[1];
const ownerDate = parseConfig(readFileSync(join(PLATFORM, "owner", CONFIG), "utf8"))
  ["compatibility_date"];
const schema = JSON.parse(readFileSync(
  join(PLATFORM, "node_modules", "wrangler", "config-schema.json"), "utf8")) as
  { definitions: Record<string, { properties: Record<string, unknown> }> };
const offered = Object.keys(schema.definitions["RawConfig"]!.properties);

describe("hooks Worker config", () => {
  it("binds its loader and its own two classes, at the owner Worker's date", () => {
    expect(hooksConfigProblems(config, ownerDate)).toEqual([]);
    expect(config["compatibility_date"]).toBe(ownerDate);
  });

  it("allows only keys wrangler knows", () => {
    expect(KEYS.filter((key) => !offered.includes(key))).toEqual([]);
  });

  it("refuses every other key wrangler offers, each other binding and route among them", () => {
    const others = offered.filter((key) => !KEYS.includes(key));
    for (const key of ["routes", "route", "kv_namespaces", "r2_buckets", "d1_databases",
                       "services", "queues", "vars", "secrets", "ai", "workflows",
                       "dispatch_namespaces", "tail_consumers", "unsafe", "env"]) {
      expect(others).toContain(key);
    }
    for (const key of others) {
      expect(hooksConfigProblems({ ...config, [key]: {} }, ownerDate))
        .toContain(`${key} is not an allowed key`);
    }
  });

  it.each([
    ["a workers.dev URL", { workers_dev: true }],
    ["a preview URL", { preview_urls: true }],
    ["ctx.exports on", { compatibility_flags: [] }],
    ["an extra flag", { compatibility_flags: ["disable_ctx_exports", "nodejs_compat"] }],
    ["another date", { compatibility_date: "2026-01-01" }],
    ["a second loader", { worker_loaders: [...LOADERS, { binding: "MORE" }] }],
    ["no loader", { worker_loaders: [] }],
    ["the owner's class", { durable_objects: { bindings: [
      ...OBJECTS.bindings, { name: "OWNER", class_name: "OwnerDO",
                             script_name: "tinyassets-owner" }] } }],
    ["another Worker's class", { durable_objects: { bindings: [
      { name: "RUNNER", class_name: "HookRunner", script_name: "tinyassets-owner" },
      OBJECTS.bindings[1]] } }],
    ["the thread stores", { durable_objects: { bindings: [
      ...OBJECTS.bindings, { name: "PISTORE", class_name: "ThreadStore",
                             script_name: "tinyassets-owner" }] } }],
    ["a third class made", { migrations: [{ tag: "v1",
                                            new_sqlite_classes: [...CLASSES, "Other"] }] }],
    ["a class made without SQLite", { migrations: [{ tag: "v1", new_classes: CLASSES }] }],
    ["no CPU headroom", { limits: undefined }],
    ["other limits", { limits: { cpu_ms: 30_000, subrequests: 10 } }],
    ["another name", { name: "tinyassets-owner" }],
    ["a key wrangler adds later", { agent_memory: [] }],
  ])("sees %s", (_what, patch) => {
    const probe: Config = { ...config, ...patch };
    for (const [key, value] of Object.entries(patch)) {
      if (value === undefined) {
        delete probe[key];
      }
    }
    expect(hooksConfigProblems(probe, ownerDate)).not.toEqual([]);
  });

  it("sees the owner Worker's date move without the hooks Worker's", () => {
    expect(hooksConfigProblems(config, "2027-01-01")).not.toEqual([]);
  });

  it("is the only wrangler config in the hooks Worker", () => {
    expect(wranglerConfigs(HOOKS)).toEqual([CONFIG]);
  });
});
