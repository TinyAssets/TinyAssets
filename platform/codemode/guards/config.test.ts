/**
 * The codemode Worker's config binds its own one class and serves no URL: its allow-list
 * (`rules.ts`), held by the platform's config checker (`ci/kit/config.ts`).
 */
import { configTests } from "../../ci/kit/config";

import { CONFIG } from "./rules";

/** Its one namespace, as its rules allow it, for the probes. */
const CODEMODE = Object.freeze({ name: "CODEMODE", class_name: "CodemodeSandbox" });

configTests(CONFIG, {
  refused: ["routes", "route", "kv_namespaces", "r2_buckets", "d1_databases", "services",
            "queues", "vars", "secrets", "ai", "workflows", "worker_loaders",
            "dispatch_namespaces", "tail_consumers", "unsafe", "env"],
  probes: [
    ["a workers.dev URL", { workers_dev: true }],
    ["a preview URL", { preview_urls: true }],
    ["ctx.exports on", { compatibility_flags: [] }],
    ["an extra flag", { compatibility_flags: ["disable_ctx_exports", "nodejs_compat"] }],
    ["a Worker Loader", { worker_loaders: [{ binding: "LOADER" }] }],
    ["another Worker's class", { durable_objects: { bindings: [
      CODEMODE, { name: "OWNER", class_name: "OwnerDO", script_name: "other" }] } }],
    ["its own class by another Worker's name", { durable_objects: { bindings: [
      { ...CODEMODE, script_name: "other" }] } }],
    ["a second class made", { migrations: [{ tag: "v1",
                                            new_sqlite_classes: ["CodemodeSandbox", "Other"] }] }],
    ["a class made without SQLite", { migrations: [{ tag: "v1",
                                                    new_classes: ["CodemodeSandbox"] }] }],
    ["no CPU headroom", { limits: undefined }],
    ["other limits", { limits: { cpu_ms: 30_000, subrequests: 10 } }],
    ["another name", { name: "other" }],
    ["a key wrangler adds later", { agent_memory: [] }],
  ],
});
