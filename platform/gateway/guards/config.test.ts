/**
 * The gateway Worker's config binds nothing and serves no URL: its allow-list
 * (`rules.ts`), held by the platform's config checker (`ci/kit/config.ts`).
 */
import { configTests } from "../../ci/kit/config";

import { CONFIG } from "./rules";

configTests(CONFIG, {
  refused: ["routes", "route", "durable_objects", "kv_namespaces", "r2_buckets", "d1_databases",
            "services", "queues", "vars", "ai", "workflows", "vectorize", "hyperdrive",
            "analytics_engine_datasets", "dispatch_namespaces", "tail_consumers", "unsafe",
            "secrets_store_secrets", "worker_loaders", "env", "migrations"],
  probes: [
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
    ["an extra flag", { compatibility_flags: ["nodejs_compat",
                                              "nodejs_compat_do_not_populate_process_env",
                                              "disable_ctx_exports", "global_fetch_strictly_public",
                                              "enable_ctx_exports"] }],
    ["a flag swapped for another", { compatibility_flags: [
      "nodejs_compat", "nodejs_compat_do_not_populate_process_env", "disable_ctx_exports",
      "global_fetch_private_origin"] }],
    ["another name", { name: "tinyassets-owner" }],
    ["a key wrangler adds later", { agent_memory: [] }],
  ],
});
