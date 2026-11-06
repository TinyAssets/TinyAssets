/**
 * The files Worker's config binds its one bucket and nothing else: its allow-list
 * (`rules.ts`), held by the platform's config checker (`ci/kit/config.ts`).
 */
import { configTests } from "../../ci/kit/config";

import { CONFIG } from "./rules";

configTests(CONFIG, {
  refused: ["durable_objects", "services", "env", "routes", "route", "kv_namespaces", "queues",
            "migrations", "worker_loaders", "unsafe"],
  probes: [
    ["a Durable Object binding", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["a services binding", { services: [{ binding: "OWNER", service: "tinyassets-owner" }] }],
    ["an environment", { env: { prod: { name: "tinyassets-files-prod" } } }],
    ["a second bucket", { r2_buckets: [{ binding: "BLOBS", bucket_name: "tinyassets-files" },
                                       { binding: "OTHER", bucket_name: "other" }] }],
    ["a renamed bucket binding", { r2_buckets: [{ binding: "R2", bucket_name: "x" }] }],
    ["a bucket with a jurisdiction", { r2_buckets: [{ binding: "BLOBS",
                                                      bucket_name: "tinyassets-files",
                                                      jurisdiction: "eu" }] }],
    ["no bucket", { r2_buckets: [] }],
    ["a route", { routes: ["files.tinyassets.io/*"] }],
    ["a workers.dev URL", { workers_dev: true }],
    ["workers_dev left to its default", { workers_dev: undefined }],
    ["a preview URL", { preview_urls: true }],
    ["a var", { vars: { FILES_CIPHER: "ssec", NS: "x" } }],
    ["another cipher", { vars: { FILES_CIPHER: "none" } }],
    ["no cipher", { vars: undefined }],
    ["no ctx.exports flag", { compatibility_flags: [] }],
    ["no keyring", { secrets: undefined }],
    ["a second secret", { secrets: { required: ["FILES_KEYRING", "OTHER"] } }],
    ["ctx.exports turned back on", { compatibility_flags: ["disable_ctx_exports",
                                                           "enable_ctx_exports"] }],
  ],
});
