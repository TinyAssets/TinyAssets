/**
 * The files Worker's config binds its one bucket and nothing else: its allow-list
 * (`rules.ts`), held by the platform's config checker (`ci/kit/config.ts`).
 */
import { configTests } from "../../ci/kit/config";
import { platformConfigs } from "../../ci/kit/sources";

import { CONFIG } from "./rules";

/** The owner Worker's name and the files bucket's, as their configs say them. */
const CONFIGS = platformConfigs();
const OWNER = String(CONFIGS.get("owner")!["name"]);
const BUCKET = (CONFIGS.get("files")!["r2_buckets"] as { bucket_name: string }[])[0]!.bucket_name;

configTests(CONFIG, {
  refused: ["durable_objects", "services", "env", "routes", "route", "kv_namespaces", "queues",
            "migrations", "worker_loaders", "unsafe"],
  probes: [
    ["a Durable Object binding", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: OWNER }] } }],
    ["a services binding", { services: [{ binding: "OWNER", service: OWNER }] }],
    ["an environment", { env: { prod: { name: "tinyassets-files-prod" } } }],
    ["a second bucket", { r2_buckets: [{ binding: "BLOBS", bucket_name: BUCKET },
                                       { binding: "OTHER", bucket_name: "other" }] }],
    ["a renamed bucket binding", { r2_buckets: [{ binding: "R2", bucket_name: "x" }] }],
    ["a bucket with a jurisdiction", { r2_buckets: [{ binding: "BLOBS",
                                                      bucket_name: BUCKET,
                                                      jurisdiction: "eu" }] }],
    ["no bucket", { r2_buckets: [] }],
    ["a route", { routes: ["files.tinyassets.io/*"] }],
    ["a workers.dev URL", { workers_dev: true }],
    ["workers_dev left to its default", { workers_dev: undefined }],
    ["a preview URL", { preview_urls: true }],
    ["a var", { vars: { NS: "x" } }],
    ["no ctx.exports flag", { compatibility_flags: [] }],
    ["no keyring", { secrets: undefined }],
    ["a second secret", { secrets: { required: ["FILES_KEYRING", "OTHER"] } }],
    ["ctx.exports turned back on", { compatibility_flags: ["disable_ctx_exports",
                                                           "enable_ctx_exports"] }],
  ],
});
