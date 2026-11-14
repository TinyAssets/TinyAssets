/**
 * The sandbox Worker's config allow-list (slice S), checked by the platform's guard kit
 * (`ci/kit/config.ts`); the owner's config guard holds what it binds to the owner's own
 * allowance for `SANDBOX` (`owner/guards/rules.ts`).
 *
 * Its config binds its own two classes' namespaces, the control record's namespace and the
 * ring queue's producer, and nothing else (no `OWNER`, no service, no bucket, no consumer,
 * no route), carries `global_fetch_strictly_public` and `disable_ctx_exports`, and declares
 * its one secret by name. The ring's one consumer, the doors Worker, turns `{owner}` into that
 * owner's `sandboxRung` (its waiting runs started again through their whole admission) and
 * nothing else: a ring grants nothing. Its build makes the runner it ships, imported as data.
 * The queue's and the namespace's names, the build and its rules are the config's own.
 */
import type { ConfigRules } from "../../ci/kit/config";
import { kernels, type Owed } from "../../ci/kit/owed";

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
  objects: Object.freeze(["SANDBOXES", "SLOTS"]),
  /** The template it starts, pinned (`template.test.ts` holds its tag). */
  vars: Object.freeze({ E2B_TEMPLATE: null }),
  kv: Object.freeze(["CONTROL"]),
  queues: Object.freeze({ producers: Object.freeze(["RING"]), consumes: false }),
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
 * What the sandbox still owes the platform's guards (`ci/kit/owed.ts`): the runner's two
 * streamed SHA-256s beside the contract's (`ci/kit/kernels.ts`), until lane S's B7 adopts
 * them.
 */
export const OWED: readonly Owed[] = Object.freeze([
  ...kernels("B7", "SHA-256", { "platform/sandbox/runner/sync.ts": 2 }),
]);
