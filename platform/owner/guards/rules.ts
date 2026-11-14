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
 * Each service names one Worker, by its folder, and the binding kinds that Worker's config
 * may hold, with how many of each, exactly: the owner's own statement of how far a bound
 * Worker reaches, whatever that Worker's own rules say. Every other key a bound Worker sets
 * is one that binds nothing (`BOUND_KEYS`), so any other binding it gains (a service, a
 * bucket, a store, a queue, a workflow, a dispatch namespace, a Worker loader, a tail
 * consumer, an unsafe binding, an environment) refuses the owner's binding. What each binding
 * of those kinds may be is the owner's invariant over every bound Worker (`config.test.ts`'s
 * `boundProblems`): a namespace only of a class the Worker's own migrations make (no
 * `script_name`), never `OwnerDO`, by migrations that only make classes; a queue only a
 * ring's producer (`RING_QUEUES`). What binds nothing (its secrets, vars, limits, flags and
 * build) is that Worker's own allow-list, its `guards/rules.ts`:
 * - `FILES`, the files Worker: its one R2 bucket;
 * - `GATEWAY`, the model gateway: no binding at all;
 * - `CODEMODE_VM`, the codemode Worker (H0, narrowed to code mode by ADR-034): one namespace
 *   of its own class, and no `OWNER`, no storage binding and no Worker Loader, so a script
 *   reaches no owner;
 * - `SANDBOX`, the sandbox Worker (slice S): two namespaces of its own classes (one per
 *   owner, its E2B calls; one for the platform's line), one KV namespace (the control
 *   record's, read for its sandbox numbers alone) and one queue producer, the ring's. It
 *   reaches nothing but E2B and the ring (`sandbox/guards/config.test.ts` reads its every
 *   fetch).
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
import type { Exemption } from "../../ci/kit/module-state";
import { imports, kernels, type Owed, refusals } from "../../ci/kit/owed";

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
 * The keys a bound Worker may set that bind nothing: its name, entry and runtime, its build
 * step and its modules' types (`build`, `rules`: the sandbox Worker's runner, built and
 * imported as data), and its secrets (declared by name), vars and limits.
 */
export const BOUND_KEYS: ReadonlySet<string> = new Set([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "workers_dev",
  "preview_urls", "build", "rules", "secrets", "vars", "limits",
]);
/**
 * The only queues a bound Worker may produce onto: the sandbox line's ring, whose one
 * consumer, the doors Worker, turns `{owner}` into that owner's `sandboxRung` and nothing
 * else. The owner's own statement, so it is named here: read from the producers' configs (the
 * generated `QUEUES`), it would follow a producer that moved onto the erasure queue.
 */
export const RING_QUEUES: readonly string[] = Object.freeze(["tinyassets-sandbox-ring"]);

export interface Bound {
  /** The Worker the binding names, by its folder under `platform/`. */
  readonly worker: string;
  /**
   * The binding kinds its config may hold, and how many bindings of each, exactly: a
   * namespace kind (`durable_objects`) also lets its migrations make its own classes, and
   * `queues` counts its producers.
   */
  readonly bindings: Readonly<Record<string, number>>;
}

/** The service bindings the owner Worker may hold, by binding name. */
export const SERVICES: Readonly<Record<string, Bound>> = Object.freeze({
  FILES: Object.freeze({ worker: "files", bindings: Object.freeze({ r2_buckets: 1 }) }),
  GATEWAY: Object.freeze({ worker: "gateway", bindings: Object.freeze({}) }),
  CODEMODE_VM: Object.freeze({ worker: "codemode",
                               bindings: Object.freeze({ durable_objects: 1 }) }),
  SANDBOX: Object.freeze({ worker: "sandbox", bindings: Object.freeze({
    durable_objects: 2, kv_namespaces: 1, queues: 1 }) }),
});

/**
 * What the owner's tests, guards and memory legs still owe the platform's guards
 * (`ci/kit/owed.ts`): another workspace's internals, its own errors and its own kernels' work,
 * each until the fix named removes it.
 */
export const OWED: readonly Owed[] = Object.freeze([
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
  // The owner's own hashes and clock reads beside its kernels (`ci/kit/kernels.ts`): B7 (O4).
  ...kernels("B7", "SHA-256", {
    "platform/owner/src/archive.ts": 1, "platform/owner/src/backup.ts": 1,
    "platform/owner/src/connections.ts": 1, "platform/owner/src/edit.ts": 2,
    "platform/owner/src/sandbox-time.ts": 1, "platform/owner/src/segments.ts": 2 }),
  ...kernels("B7", "Date.now()", {
    "platform/owner/src/backup.ts": 11, "platform/owner/src/codemode.ts": 2,
    "platform/owner/src/collections.ts": 1, "platform/owner/src/connections.ts": 4,
    "platform/owner/src/control-gate.ts": 1, "platform/owner/src/egress.ts": 1,
    "platform/owner/src/erase.ts": 7, "platform/owner/src/files.ts": 9,
    "platform/owner/src/relay.ts": 1, "platform/owner/src/sandbox-time.ts": 4,
    "platform/owner/src/schedule.ts": 1, "platform/owner/src/wakes.ts": 1 }),
]);
