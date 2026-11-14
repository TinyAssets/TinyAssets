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
import { imports, type Owed, refusals } from "../../ci/kit/owed";

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

/**
 * Where its sources name its own classes' objects (the doors' binder guard,
 * `doors/guards/binder.test.ts`): one owner's sandboxes' E2B calls (`SANDBOXES`) and the
 * platform's line for one more sandbox (`SLOTS`), neither of which binds anything of an
 * owner's object.
 */
const OWN = Object.freeze(["this.env.SANDBOXES", "this.env.SLOTS"]);
export const OWN_OBJECTS = Object.freeze({ "sandbox/src/index.ts": OWN,
                                           "sandbox/src/control.ts": OWN });

/**
 * What the sandbox still owes the platform's guards (`ci/kit/owed.ts`), each until the fix
 * named removes it.
 */
export const OWED: readonly Owed[] = Object.freeze([
  // The template's pin, read from the Worker's config: B18 gives it one reader in `pair.ts`,
  // and the template its row's way to `ci/wrangler.ts`.
  ...imports("B18", "platform/sandbox/template/checks.ts", ["platform/ci/wrangler.ts"]),
  // The runner's answers as error classes of the Worker's own, each a contract refusal by
  // its code (B2, S7).
  ...refusals("B2", "platform/sandbox/src/control.ts",
              ["class RunnerFailed extends Error", "class RunnerRefused extends Error"]),
]);
