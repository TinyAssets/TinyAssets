/**
 * The sandbox Worker's config allow-list (slice S), checked by the platform's guard kit
 * (`ci/kit/config.ts`); the owner config guard's entry for `SANDBOX` relies on it.
 *
 * Its config binds its own two classes' namespaces, the control record's namespace and the
 * ring queue's producer, and nothing else (no `OWNER`, no service, no bucket, no consumer,
 * no route), carries `global_fetch_strictly_public` and `disable_ctx_exports`, and declares
 * its one secret by name. Its build makes the runner it ships, and the runner alone is
 * imported as data.
 */
import type { ConfigRules } from "../../ci/kit/config";

export const CONFIG: ConfigRules = Object.freeze({
  worker: "sandbox",
  name: "tinyassets-sandbox",
  main: "src/index.ts",
  keys: Object.freeze(["$schema", "build", "compatibility_date", "compatibility_flags",
                       "durable_objects", "kv_namespaces", "main", "migrations", "name",
                       "preview_urls", "queues", "rules", "secrets", "vars", "workers_dev"]),
  flags: Object.freeze({ carries: Object.freeze(["global_fetch_strictly_public",
                                                 "disable_ctx_exports"]) }),
  secrets: Object.freeze(["E2B_API_KEY"]),
  classes: Object.freeze(["SandboxControl", "SandboxSlots"]),
  /** The template it starts, pinned (`template.test.ts` holds its tag). */
  vars: Object.freeze({ E2B_TEMPLATE: null }),
  exact: Object.freeze({
    build: { command: "node template/bundle.mjs dist" },
    rules: [{ type: "Data", globs: ["**/dist/runner.mjs"], fallthrough: false }],
    durable_objects: { bindings: [{ name: "SANDBOXES", class_name: "SandboxControl" },
                                  { name: "SLOTS", class_name: "SandboxSlots" }] },
    kv_namespaces: [{ binding: "CONTROL", id: "not-provisioned-control" }],
    /**
     * The ring queue's producer, whose one consumer, the doors Worker, turns `{owner}` into
     * that owner's `sandboxRung` (its waiting runs started again through their whole
     * admission) and nothing else: a ring grants nothing.
     */
    queues: { producers: [{ binding: "RING", queue: "tinyassets-sandbox-ring" }] },
  }),
});
