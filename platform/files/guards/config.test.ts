/**
 * The files Worker's config binds its one bucket and nothing else (ADR-032, plan §6).
 *
 * The owner Worker may hold a service binding to this Worker only because this Worker
 * binds no Durable Object and no service (`owner/guards/config.test.ts` checks that
 * against this file). So its config is an allow-list too: the keys that name it, its
 * entry and runtime, the R2 bucket `BLOBS`, and no URL of its own (`workers_dev` and
 * `preview_urls` false, no route). No environment may add anything. It is the only
 * wrangler config in the files Worker.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { wranglerConfigs } from "../../owner/guards/config.test";

const FILES = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG = "wrangler.jsonc";
const NO_EXPORTS = "disable_ctx_exports";
const KEYS: ReadonlySet<string> = new Set([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "workers_dev",
  "preview_urls", "r2_buckets",
]);

type Config = Record<string, unknown>;

export function readConfig(path: string): Config {
  const parsed = ts.parseConfigFileTextToJson(path, readFileSync(path, "utf8"));
  if (parsed.error) {
    throw new Error(ts.flattenDiagnosticMessageText(parsed.error.messageText, "\n"));
  }
  return parsed.config as Config;
}

/** Each way `config` lets the files Worker bind past its bucket or serve a URL. */
export function filesConfigProblems(config: Config): string[] {
  const problems = Object.keys(config).filter((key) => !KEYS.has(key))
    .map((key) => `${key} is not an allowed key`);
  const flags = config["compatibility_flags"];
  if (!Array.isArray(flags) || !flags.includes(NO_EXPORTS)
      || flags.some((flag) => flag !== NO_EXPORTS && /ctx_exports/.test(String(flag)))) {
    problems.push(`compatibility_flags must carry ${NO_EXPORTS} alone of its kind`);
  }
  if (config["workers_dev"] !== false || config["preview_urls"] !== false) {
    problems.push("the files Worker serves no workers.dev or preview URL");
  }
  const buckets = config["r2_buckets"];
  if (!Array.isArray(buckets) || buckets.length !== 1 || typeof buckets[0] !== "object"
      || buckets[0] === null || Object.keys(buckets[0]).sort().join() !== "binding,bucket_name"
      || (buckets[0] as Config)["binding"] !== "BLOBS"
      || typeof (buckets[0] as Config)["bucket_name"] !== "string") {
    problems.push("r2_buckets is exactly the one bucket BLOBS");
  }
  return problems;
}

const config = readConfig(join(FILES, CONFIG));

describe("files Worker config", () => {
  it("binds the BLOBS bucket alone and serves no URL", () => {
    expect(filesConfigProblems(config)).toEqual([]);
    expect(config["name"]).toBe("tinyassets-files");
  });

  it("is the only wrangler config in the files Worker", () => {
    expect(wranglerConfigs(FILES)).toEqual([CONFIG]);
  });

  const base = { name: "tinyassets-files", main: "src/index.ts",
                 compatibility_flags: [NO_EXPORTS], workers_dev: false, preview_urls: false,
                 r2_buckets: [{ binding: "BLOBS", bucket_name: "tinyassets-files" }] };

  it.each([
    ["a Durable Object binding", { ...base, durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["a services binding", { ...base, services: [{ binding: "OWNER", service: "tinyassets-owner" }] }],
    ["an environment", { ...base, env: { prod: { name: "tinyassets-files-prod" } } }],
    ["a second bucket", { ...base, r2_buckets: [...base.r2_buckets,
      { binding: "OTHER", bucket_name: "other" }] }],
    ["a renamed bucket binding", { ...base, r2_buckets: [{ binding: "R2", bucket_name: "x" }] }],
    ["a bucket with a jurisdiction", { ...base, r2_buckets: [{ ...base.r2_buckets[0],
      jurisdiction: "eu" }] }],
    ["no bucket", { ...base, r2_buckets: [] }],
    ["a route", { ...base, routes: ["files.tinyassets.io/*"] }],
    ["a workers.dev URL", { ...base, workers_dev: true }],
    ["workers_dev left to its default", { ...base, workers_dev: undefined }],
    ["a preview URL", { ...base, preview_urls: true }],
    ["a var", { ...base, vars: { NS: "x" } }],
    ["no ctx.exports flag", { ...base, compatibility_flags: [] }],
    ["ctx.exports turned back on", { ...base, compatibility_flags: [NO_EXPORTS,
      "enable_ctx_exports"] }],
  ])("sees %s", (_what, probe) => {
    const shown = JSON.parse(JSON.stringify(probe)) as Config;
    expect(filesConfigProblems(shown)).not.toEqual([]);
  });
});
