/**
 * The codemode Worker's config binds its own one class and serves no URL: its allow-list
 * (`rules.ts`), held by the platform's config checker (`ci/kit/config.ts`).
 */
import { describe, expect, it } from "vitest";

import { configProblems, configTests } from "../../ci/kit/config";
import { platformConfigs } from "../../ci/kit/sources";

import { CONFIG } from "./rules";

const OBJECTS = CONFIG.exact!["durable_objects"] as { bindings: object[] };

const config = configTests(CONFIG, {
  refused: ["routes", "route", "kv_namespaces", "r2_buckets", "d1_databases", "services",
            "queues", "vars", "secrets", "ai", "workflows", "worker_loaders",
            "dispatch_namespaces", "tail_consumers", "unsafe", "env"],
  probes: [
    ["a workers.dev URL", { workers_dev: true }],
    ["a preview URL", { preview_urls: true }],
    ["ctx.exports on", { compatibility_flags: [] }],
    ["an extra flag", { compatibility_flags: ["disable_ctx_exports", "nodejs_compat"] }],
    ["another date", { compatibility_date: "2026-01-01" }],
    ["a Worker Loader", { worker_loaders: [{ binding: "LOADER" }] }],
    ["the owner's class", { durable_objects: { bindings: [
      ...OBJECTS.bindings, { name: "OWNER", class_name: "OwnerDO",
                             script_name: "tinyassets-owner" }] } }],
    ["another Worker's class", { durable_objects: { bindings: [
      { name: "CODEMODE", class_name: "CodemodeSandbox", script_name: "tinyassets-owner" }] } }],
    ["a second class made", { migrations: [{ tag: "v1",
                                            new_sqlite_classes: ["CodemodeSandbox", "Other"] }] }],
    ["a class made without SQLite", { migrations: [{ tag: "v1",
                                                    new_classes: ["CodemodeSandbox"] }] }],
    ["no CPU headroom", { limits: undefined }],
    ["other limits", { limits: { cpu_ms: 30_000, subrequests: 10 } }],
    ["another name", { name: "tinyassets-owner" }],
    ["a key wrangler adds later", { agent_memory: [] }],
  ],
});

describe("codemode Worker config", () => {
  it("is at the owner Worker's date", () => {
    expect(config["compatibility_date"])
      .toBe(platformConfigs().get("tinyassets-owner")?.["compatibility_date"]);
  });

  it("sees the owner Worker's date move without the codemode Worker's", () => {
    const platform = platformConfigs();
    platform.set("tinyassets-owner", { ...platform.get("tinyassets-owner"),
                                       compatibility_date: "2027-01-01" });
    expect(configProblems(config, CONFIG, platform)).not.toEqual([]);
  });
});
