/**
 * The doors Worker's config binds exactly what the doors need, and cannot deploy
 * (research §13). Mirrors the owner Worker's config guard: an allow-list of keys, and
 * each binding pinned.
 *
 * - `OWNER`: the owner Worker's `OwnerDO` namespace, by `script_name`, which must be the
 *   owner Worker's own SQLite class in the platform. The binder is the trust root and
 *   reaches every owner's object by name; only `src/binder.ts` names it.
 * - `OAUTH_KV` and `DIRECTORY`: two KV namespaces, distinct, with placeholder ids
 *   (`not-provisioned-…`), so a deploy fails until cutover creates them.
 * - Two vars: `DCR_ENABLED` `"false"`, and `OWNER_ISSUER` unset (`not-configured`) until
 *   the AuthKit slice names AuthKit's issuer, so every sign-in fails loudly.
 * - No service: `FILES` joins with the file-bytes door (tasks 2, owed by 6.1), and rate
 *   limits with their binding (tasks 2).
 * - No routes, custom domain, workers.dev or preview URL, no environment, no migration
 *   (the doors define no Durable Object class).
 * - Flags: `nodejs_compat` (the owner's shared code), `disable_ctx_exports` (no way to the
 *   Worker's own namespaces), and `global_fetch_strictly_public`, which Client ID Metadata
 *   Documents require: the provider fetches each client's metadata URL, the flag keeps
 *   that fetch off same-zone routing (its SSRF protection), and without it the provider
 *   neither fetches nor advertises CIMD (OP docs/authorization-server.md:62-68).
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";

const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG = "wrangler.jsonc";
const FLAGS: readonly string[] = Object.freeze(["nodejs_compat", "disable_ctx_exports",
                                                "global_fetch_strictly_public"]);
const KEYS: ReadonlySet<string> = new Set([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "workers_dev",
  "preview_urls", "vars", "durable_objects", "kv_namespaces",
]);
const KV: readonly string[] = Object.freeze(["DIRECTORY", "OAUTH_KV"]);

type Config = Record<string, unknown>;

function parseConfig(text: string): Config {
  const parsed = ts.parseConfigFileTextToJson(CONFIG, text);
  if (parsed.error) {
    throw new Error(ts.flattenDiagnosticMessageText(parsed.error.messageText, "\n"));
  }
  return parsed.config as Config;
}

/** Every wrangler config under `root` (not dependencies), by path from `root`. */
function wranglerConfigs(root: string): string[] {
  return readdirSync(root, { recursive: true, encoding: "utf8" })
    .map((path) => path.split("\\").join("/"))
    .filter((path) => !path.split("/").includes("node_modules")
                      && /^wrangler\./i.test(path.split("/").at(-1) ?? ""))
    .sort();
}

/** Every Worker config in the platform, by its Worker's name. */
function platformConfigs(): Map<string, Config> {
  const found = new Map<string, Config>();
  for (const path of wranglerConfigs(PLATFORM)) {
    const parsed = parseConfig(readFileSync(join(PLATFORM, path), "utf8"));
    found.set(String(parsed["name"]), parsed);
  }
  return found;
}

/** Each way `config` binds more than the doors need, or could deploy. */
export function doorsConfigProblems(config: Config,
                                    platform: ReadonlyMap<string, Config>): string[] {
  const problems: string[] = [];
  for (const key of Object.keys(config)) {
    if (!KEYS.has(key)) {
      problems.push(`${key} is not an allowed key`);
    }
  }
  if (config["workers_dev"] !== false || config["preview_urls"] !== false) {
    problems.push("workers_dev and preview_urls are false");
  }
  const flags = config["compatibility_flags"];
  if (!Array.isArray(flags) || FLAGS.some((flag) => !flags.includes(flag))
      || flags.some((flag) => !FLAGS.includes(String(flag)))) {
    problems.push(`compatibility_flags are exactly ${FLAGS.join(", ")}`);
  }
  if (JSON.stringify(config["vars"])
      !== JSON.stringify({ DCR_ENABLED: "false", OWNER_ISSUER: "not-configured" })) {
    problems.push("vars are exactly DCR_ENABLED \"false\" and OWNER_ISSUER unset");
  }
  const objects = config["durable_objects"] as { bindings?: unknown } | undefined;
  const bindings = objects?.bindings;
  if (Object.keys(objects ?? {}).join() !== "bindings" || !Array.isArray(bindings)
      || bindings.length !== 1
      || JSON.stringify(bindings[0]) !== JSON.stringify(
        { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" })) {
    problems.push("durable_objects binds exactly OWNER to tinyassets-owner's OwnerDO");
  } else {
    const owner = platform.get("tinyassets-owner");
    const classes = ((owner?.["migrations"] ?? []) as { new_sqlite_classes?: string[] }[])
      .flatMap((m) => m.new_sqlite_classes ?? []);
    if (!classes.includes("OwnerDO")) {
      problems.push("OWNER's script is not the platform's owner Worker with its OwnerDO class");
    }
  }
  const kv = config["kv_namespaces"];
  if (!Array.isArray(kv) || kv.length !== KV.length
      || JSON.stringify(kv.map((n: { binding?: unknown }) => n.binding).sort())
        !== JSON.stringify(KV)
      || kv.some((n: Record<string, unknown>) => Object.keys(n).sort().join() !== "binding,id"
                                                 || !/^not-provisioned-[a-z]+$/.test(String(n["id"])))
      || new Set(kv.map((n: { id?: unknown }) => n.id)).size !== kv.length) {
    problems.push("kv_namespaces are exactly OAUTH_KV and DIRECTORY, distinct placeholders");
  }
  return problems;
}

const config = parseConfig(readFileSync(join(DOORS, CONFIG), "utf8"));
const platform = platformConfigs();

describe("doors Worker config", () => {
  it("binds OWNER, OAUTH_KV and DIRECTORY, and cannot deploy", () => {
    expect(doorsConfigProblems(config, platform)).toEqual([]);
  });

  it("is the only wrangler config in the doors Worker", () => {
    expect(wranglerConfigs(DOORS)).toEqual([CONFIG]);
    expect(platform.get("tinyassets-doors")).toEqual(config);
    expect(PLATFORM).toBe(join(DOORS, ".."));
  });

  it.each([
    ["a route", { routes: [{ pattern: "tinyassets.io/mcp*", zone_name: "tinyassets.io" }] }],
    ["a custom domain", { route: { pattern: "tinyassets.io", custom_domain: true } }],
    ["workers.dev", { workers_dev: true }],
    ["preview URLs", { preview_urls: true }],
    ["an environment", { env: { prod: { routes: ["tinyassets.io/*"] } } }],
    ["a service", { services: [{ binding: "FILES", service: "tinyassets-files" }] }],
    ["a bucket", { r2_buckets: [{ binding: "BLOBS", bucket_name: "tinyassets-files" }] }],
    ["a migration", { migrations: [{ tag: "v1", new_sqlite_classes: ["X"] }] }],
    ["a rate limit", { ratelimits: [{ name: "KEY_RL", namespace_id: "1",
                                      simple: { limit: 120, period: 60 } }] }],
    ["no strictly-public fetch", { compatibility_flags: ["nodejs_compat", "disable_ctx_exports"] }],
    ["ctx.exports", { compatibility_flags: [...FLAGS, "enable_ctx_exports"] }],
    ["DCR on", { vars: { DCR_ENABLED: "true", OWNER_ISSUER: "not-configured" } }],
    ["an owner issuer before AuthKit", { vars: { DCR_ENABLED: "false",
                                                 OWNER_ISSUER: "https://evil.example" } }],
    ["another var", { vars: { DCR_ENABLED: "false", OWNER_ISSUER: "not-configured",
                              OWNER: "alice" } }],
    ["OWNER to another script", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-files" }] } }],
    ["OWNER in this script", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO" }] } }],
    ["a second namespace", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" },
      { name: "OTHER", class_name: "X", script_name: "tinyassets-owner" }] } }],
    ["a real KV id", { kv_namespaces: [{ binding: "OAUTH_KV", id: "0f2ac74b498b48028cb68387c421e279" },
                                       { binding: "DIRECTORY", id: "not-provisioned-directory" }] }],
    ["one shared KV namespace", { kv_namespaces: [{ binding: "OAUTH_KV", id: "not-provisioned-x" },
                                                  { binding: "DIRECTORY", id: "not-provisioned-x" }] }],
    ["a third KV namespace", { kv_namespaces: [
      { binding: "OAUTH_KV", id: "not-provisioned-oauth" },
      { binding: "DIRECTORY", id: "not-provisioned-directory" },
      { binding: "CACHE", id: "not-provisioned-cache" }] }],
  ])("sees %s", (_what, change) => {
    expect(doorsConfigProblems({ ...config, ...change }, platform)).not.toEqual([]);
  });

  it("sees OWNER bound to a script whose class is not in the platform", () => {
    const without = new Map(platform);
    without.delete("tinyassets-owner");
    expect(doorsConfigProblems(config, without)).not.toEqual([]);
  });
});
