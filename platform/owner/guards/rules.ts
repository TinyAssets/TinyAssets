/**
 * The owner Worker's config allow-list (ADR-032), checked by `config.test.ts` with the
 * platform's rule checks (`ci/kit/config.ts`).
 *
 * `OwnerDO` must have no way to another owner's object, so its Worker's config is an
 * allow-list, at top level and in every environment: the keys that name the Worker, its
 * entry, its runtime and its SQLite class migrations, and no binding but the service
 * bindings `SERVICES` names: no Durable Object namespace at all (ADR-034 removed the
 * agent's thread stores), and `OwnerDO` itself is never bound.
 *
 * Each service names one Worker and exactly the binding kinds that Worker's own config may
 * hold, each a kind that reaches no object, service, queue, workflow or other Worker; the
 * rest of what it may hold (its secrets, vars, own classes, limits and queue) is that
 * Worker's own allow-list (its `guards/rules.ts`), held to the owner's own invariants
 * whatever that Worker's rules say (`config.test.ts`'s `ownProblems` and `queueProblems`):
 * no namespace of another Worker's class, never `OwnerDO`, no queue but a ring. The bound
 * Worker's config is checked against that allowance too, so any other binding it gains (a Durable Object, a service, a
 * queue, a workflow, a dispatch namespace, a Worker loader, a tail consumer, a shared store,
 * an unsafe binding) refuses the owner's binding:
 * - `FILES`, the files Worker: its one R2 bucket, its keyring and its cipher var;
 * - `GATEWAY`, the model gateway: no binding at all, and its one secret, the keyring;
 * - `CODEMODE_VM`, the codemode Worker (H0, narrowed to code mode by ADR-034): the namespace of its
 *   own one class, exactly (no `script_name`, so no other Worker's class), made by its
 *   migrations as SQLite, and its `limits` exactly: no `OWNER`, no storage binding and no
 *   Worker Loader, so a script reaches no owner;
 * - `SANDBOX`, the sandbox Worker (slice S): its own classes (one per owner, its E2B calls;
 *   one for the platform's line), the control record's namespace read for its sandbox
 *   numbers alone, the ring queue's producer, and its one secret, the E2B key. It reaches
 *   nothing but E2B and the ring (`sandbox/guards/config.test.ts` reads its every fetch).
 *
 * Its compatibility flags carry `disable_ctx_exports`, so the runtime does not hand the
 * object its own namespaces as `ctx.exports`. Its `limits` are exactly `LIMITS`, set at top
 * level: the platform's count of subrequests per invocation (50 on Free, 10,000 by default
 * on Paid, at most 10,000,000), whose own error is the object's only call limit, so the
 * number the object relies on is explicit and moves only with a review here. It is Paid's
 * most, 10,000,000: live probe P1 found calls stopped at exactly 10,000 at the default and
 * passed at 1,000,000. Edge Workers bind `OWNER` from their own configs.
 *
 * Its one store, at top level only, is `CONTROL` (slice M review M1): the namespace of the
 * platform's control record alone, the doors' own `CONTROL` by the same id, so the object
 * reads the gates again at code mode's calls and reaches no directory entry or other
 * owner's state. Only `control-gate.ts` reads it, by `readControl`, and nothing writes it.
 */
import { CONFIG as FILES } from "../../files/guards/rules";
import { CONFIG as GATEWAY } from "../../gateway/guards/rules";
import { CONFIG as CODEMODE_VM } from "../../codemode/guards/rules";
import { CONFIG as SANDBOX } from "../../sandbox/guards/rules";
import type { Exemption } from "../../ci/kit/module-state";
import { imports, type Owed, refusals } from "../../ci/kit/owed";

/**
 * The module state the owner Worker keeps by design (`ci/kit/module-state.ts`): its isolate's
 * chunk slots (`holds.ts`, with the relay's tails; the pool itself is the contract's,
 * `contract/src/slots.ts`, which holds no state). The pool is the isolate's, shared by every
 * object and request in it; it holds a count of slots in use, the calls waiting and the
 * leases holding units, each with the owner id it waits or holds for, never an owner's data.
 */
export const MODULE_STATE: readonly Exemption[] = Object.freeze([Object.freeze({
  file: "owner/src/holds.ts", symbol: "ISOLATE", reason: "the isolate's one chunk pool" })]);

/** The classes the Worker's migrations make, each SQLite-backed. */
export const CLASSES = Object.freeze(["OwnerDO"]);
/** The keys an environment may set besides `services`: none of them binds anything. */
export const ENV_KEYS: ReadonlySet<string> = new Set([
  "name", "main", "compatibility_date", "compatibility_flags", "workers_dev", "preview_urls",
  "migrations", "limits",
]);
/** The owner Worker's limits, wherever they are set: subrequests, as live probe P1 set them. */
export const LIMITS = Object.freeze({ subrequests: 10_000_000 });
/** The keys the top level may set: an environment's, the schema, the environments, its secret. */
export const TOP_KEYS: ReadonlySet<string> = new Set([...ENV_KEYS, "$schema", "env", "secrets",
                                                      "kv_namespaces", "queues"]);
/**
 * The owner Worker's one queue (slice L): the producer of the erasure queue, whose one
 * consumer, the doors Worker, makes an erasure's outside record and confirms it to that
 * owner's object (`doors/src/erase.ts`); a message is `{owner}`, granting nothing.
 */
export const OWNER_QUEUES = Object.freeze({ producers: Object.freeze([
  Object.freeze({ binding: "ERASURES", queue: "tinyassets-erasure" })]) });
/**
 * The owner Worker's one secret, declared by name (it binds nothing): the routing key the
 * sandbox tokens are MAC'd under (`contract/src/routing-mac.ts`), the doors Worker's too.
 */
export const SECRETS = Object.freeze(["ROUTING_MAC_KEY"]);
/**
 * The keys a bound Worker may set that bind nothing: among them its own build step and its
 * modules' types (`build`, `rules`: the sandbox Worker's runner, built and imported as data).
 */
export const BOUND_KEYS: ReadonlySet<string> = new Set([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "workers_dev",
  "preview_urls", "build", "rules",
]);
/**
 * The only queues a bound Worker may produce onto, whatever its own rules say: the sandbox
 * line's ring, whose one consumer, the doors Worker, turns `{owner}` into that owner's
 * `sandboxRung` and nothing else.
 */
export const RING_QUEUES: readonly string[] = Object.freeze(["tinyassets-sandbox-ring"]);

export interface Bound {
  /** The Worker the binding names. */
  readonly service: string;
  /** The binding kinds its own config may hold, and how many of each, exactly. */
  readonly bindings: Readonly<Record<string, number>>;
  /** The secrets its config may declare by name (each binds nothing); none by default. */
  readonly secrets?: readonly string[];
  /**
   * The namespaces of its own classes it may bind, exactly as given (none with a
   * `script_name`), and the classes its migrations make, SQLite; none by default.
   */
  readonly own?: { readonly durable_objects: unknown; readonly classes: readonly string[] };
  /** Its `limits`, exactly, when it may set any; none by default. */
  readonly limits?: unknown;
  /** The plain-text variables it may set, by name exactly (each binds nothing); none. */
  readonly vars?: readonly string[];
  /** Its queues, exactly: a producer of a queue whose consumer reaches nothing but a ring. */
  readonly queues?: unknown;
}

/** The service bindings the owner Worker may hold, by binding name. */
export const SERVICES: Readonly<Record<string, Bound>> = Object.freeze({
  FILES: Object.freeze({ service: FILES.name, bindings: Object.freeze({ r2_buckets: 1 }),
                         secrets: FILES.secrets! }),
  GATEWAY: Object.freeze({ service: GATEWAY.name, bindings: Object.freeze({}),
                           secrets: GATEWAY.secrets! }),
  CODEMODE_VM: Object.freeze({ service: CODEMODE_VM.name, bindings: Object.freeze({}),
                         own: { durable_objects: CODEMODE_VM.exact!["durable_objects"],
                                classes: CODEMODE_VM.classes! },
                         limits: CODEMODE_VM.exact!["limits"] }),
  SANDBOX: Object.freeze({ service: SANDBOX.name, bindings: Object.freeze({ kv_namespaces: 1 }),
                           own: { durable_objects: SANDBOX.exact!["durable_objects"],
                                  classes: SANDBOX.classes! },
                           vars: Object.keys(SANDBOX.vars!), secrets: SANDBOX.secrets!,
                           queues: SANDBOX.exact!["queues"] }),
});

/**
 * What the owner's tests, guards and memory legs still owe the platform's guards
 * (`ci/kit/owed.ts`): another workspace's internals, each until the fix named removes it.
 */
export const OWED: readonly Owed[] = Object.freeze([
  // Platform-wide guards in the owner's tree: B8 moves each to its subject.
  ...imports("B8", "platform/owner/guards/rules.ts", [
    "platform/files/guards/rules.ts", "platform/gateway/guards/rules.ts",
    "platform/codemode/guards/rules.ts", "platform/sandbox/guards/rules.ts"]),
  // The egress memory leg, the doors' and the gateway's, in the owner's tree: B23.
  ...imports("B23", "platform/owner/memory/egress-probe.ts", [
    "platform/doors/src/egress.ts", "platform/gateway/src/catalog.ts",
    "platform/gateway/src/held.ts", "platform/gateway/src/keyring.ts",
    "platform/gateway/src/meter.ts", "platform/gateway/src/index.ts"]),
  // Tests on the other Workers' internals, not their front doors: B23.
  ...imports("B23", "platform/owner/test/connections.test.ts", [
    "platform/gateway/src/credential.ts", "platform/gateway/src/catalog.ts",
    "platform/gateway/src/keyring.ts"]),
  ...imports("B23", "platform/owner/test/export.test.ts",
             ["platform/files/src/datakey.ts", "platform/files/src/index.ts"]),
  ...imports("B23", "platform/owner/test/gateway-support.ts",
             ["platform/gateway/src/credential.ts", "platform/gateway/src/keyring.ts"]),
  ...imports("B23", "platform/owner/test/holds.test.ts",
             ["platform/files/src/datakey.ts", "platform/files/src/keys.ts"]),
  ...imports("B23", "platform/owner/test/runs-authority.test.ts",
             ["platform/doors/src/binder.ts"]),
  // Error classes of the object's own, each a contract refusal by its code: B2 (O4).
  ...refusals("B2", "platform/owner/src/collections.ts", ["class Conflict extends Error"]),
  ...refusals("B2", "platform/owner/src/files.ts", ["class FileConflict extends Error"]),
  ...refusals("B2", "platform/owner/src/ledger.ts", ["class ParentMoved extends Error"]),
]);
