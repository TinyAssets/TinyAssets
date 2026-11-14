/**
 * The doors Worker's config allow-list (research §13), checked by the platform's guard kit
 * (`ci/kit/config.ts`); what it checks against the other Workers' configs and sources is
 * `config.test.ts`'s. It names each binding and each Worker the doors reach, and the values
 * that are themselves a security statement; a queue's, bucket's or namespace's own name is the
 * config's alone.
 *
 * - `OWNER` (`OBJECTS`): the owner Worker's `OwnerDO` namespace, by the owner Worker's own
 *   name as `script_name`, which must be its own SQLite class. The binder is the trust root
 *   and reaches every owner's object by name; only `src/binder.ts` names it.
 * - `GATEWAY`, `EGRESS` and `FILES` (`SERVICES`): one entrypoint each of the gateway and
 *   files Workers, each serving exactly what is listed, so the doors make no model call, hold
 *   no keyring and read no chunk, generation or key (review M5-1).
 * - `OAUTH_KV`, `DIRECTORY` and `CONTROL`: three KV namespaces, distinct, with placeholder ids
 *   (`not-provisioned-…`), so a deploy fails until cutover creates them.
 * - Four vars, each as checked in its inert value: `DCR_ENABLED` `"false"`; `OWNER_ISSUER`
 *   and `UPSTREAM` unset (`not-configured`), so every sign-in fails loudly until the deploy
 *   kit fills both from the issuer's live discovery (`deploy/deploy.ts`); and
 *   `FOREIGN_ISSUERS` empty (`[]`) until a provider's slice lists its issuer, so any other
 *   issuer is refused, never taken for a new owner.
 * - Three secrets, declared by name (`secrets.required`, which binds nothing):
 *   `ROUTING_MAC_KEY`, the routing key sandbox tokens are MAC'd under, the owner Worker's
 *   too (`SHARED_SECRETS`); `WORKOS_API_KEY`, the upstream's client secret; and
 *   `E2B_WEBHOOK_SECRET`, E2B's lifecycle webhook's signature secret (slice P).
 * - No `ratelimits`: no number of ours bounds any caller (the founder's rule, slice L's
 *   review); Cloudflare's own DDoS protection is in front of every Worker.
 * - `ASSETS`: the owner app's files (`app/`), Workers Static Assets, the Worker first on every
 *   request, so only `src/pages.ts`'s named pages and files are served, each with its
 *   headers; no HTML handling or not-found page of the assets' own.
 * - The consumer of every queue the platform's Workers produce onto, and of no other: the
 *   sandbox line's ring (slice S, `src/ring.ts`), which turns `{owner}` into that owner's
 *   `sandboxRung` and nothing else, the erasure queue, and the import window's.
 * - The cutover's import window (`src/import/`), deleted at the cutover with
 *   `platform/import/` and named here as its bindings alone (`IMPORT_WINDOW`): the import
 *   queue's producer (`IMPORTS`, the consumer's own continuation, the doors' one producer)
 *   and the staging bucket (`IMPORT_STAGING`), the doors' one bucket, which no other Worker
 *   binds.
 * - No routes, custom domain, workers.dev or preview URL, no environment, no migration
 *   (the doors define no Durable Object class).
 * - Flags carrying `disable_ctx_exports` (no way to the Worker's own namespaces) and
 *   `global_fetch_strictly_public`, which Client ID Metadata Documents require: the provider
 *   fetches each client's metadata URL, the flag keeps that fetch off same-zone routing (its
 *   SSRF protection), and without it the provider neither fetches nor advertises CIMD (OP
 *   docs/authorization-server.md:62-68). The config's others (`nodejs_compat`,
 *   `enable_request_signal`) are its own.
 */
import type { ConfigRules } from "../../ci/kit/config";
import { imports, type Owed } from "../../ci/kit/owed";

/**
 * The import window's bindings (`src/import/`), each named once here: deleted at the
 * cutover with `platform/import/`, as the cutover's manifest names them (`platform/cutover.json`).
 */
export const IMPORT_WINDOW = Object.freeze({ bucket: "IMPORT_STAGING", producer: "IMPORTS" });

/** The secrets the doors share with another Worker, by its folder: the routing key, the owner's. */
export const SHARED_SECRETS: Readonly<Record<string, readonly string[]>> = Object.freeze({
  owner: Object.freeze(["ROUTING_MAC_KEY"]) });

/** The other Workers' namespaces the doors bind, by binding: each Worker's folder and class. */
export const OBJECTS = Object.freeze({
  OWNER: Object.freeze({ worker: "owner", class_name: "OwnerDO" }) });

/**
 * The doors' service bindings, in order, by binding: each Worker's folder, the one entrypoint
 * bound, which extends `WorkerEntrypoint` itself (RPC serves inherited methods too, review
 * L6-1), and exactly the members it serves.
 */
export const SERVICES = Object.freeze({
  GATEWAY: Object.freeze({ worker: "gateway", entrypoint: "ConnectGateway",
                           serves: Object.freeze(["exchange", "seal", "sealDoorbell", "verify",
                                                  "verifyDoorbell"]) }),
  EGRESS: Object.freeze({ worker: "gateway", entrypoint: "EgressGateway",
                          serves: Object.freeze(["forward"]) }),
  FILES: Object.freeze({ worker: "files", entrypoint: "FilesUpload",
                         serves: Object.freeze(["putChunks"]) }),
});

export const CONFIG: ConfigRules = Object.freeze({
  worker: "doors",
  name: "tinyassets-doors",
  main: "src/index.ts",
  keys: Object.freeze(["$schema", "name", "main", "compatibility_date", "compatibility_flags",
                       "workers_dev", "preview_urls", "vars", "durable_objects", "services",
                       "kv_namespaces", "secrets", "queues", "assets", "r2_buckets"]),
  flags: Object.freeze({ carries: Object.freeze(["disable_ctx_exports",
                                                 "global_fetch_strictly_public"]) }),
  secrets: Object.freeze([...SHARED_SECRETS["owner"]!, "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET"]),
  vars: Object.freeze({ DCR_ENABLED: ["false"], OWNER_ISSUER: ["not-configured"],
                        FOREIGN_ISSUERS: ["[]"], UPSTREAM: ["not-configured"] }),
  kv: Object.freeze(["OAUTH_KV", "DIRECTORY", "CONTROL"]),
  buckets: Object.freeze([IMPORT_WINDOW.bucket]),
  queues: Object.freeze({ producers: Object.freeze([IMPORT_WINDOW.producer]), consumes: true }),
  exact: Object.freeze({
    assets: { directory: "./app", binding: "ASSETS", run_worker_first: true,
              html_handling: "none", not_found_handling: "none" },
  }),
});

/**
 * What the doors' tests still owe the platform's guards (`ci/kit/owed.ts`): another
 * workspace's internals, each until the fix named removes the reach.
 */
export const OWED: readonly Owed[] = Object.freeze([
  // Tests on the owner's internals, not its front door: B23.
  ...imports("B23", "platform/doors/test/api-keys.test.ts", ["platform/owner/src/grants.ts"]),
  ...imports("B23", "platform/doors/test/app-routes.test.ts", ["platform/owner/src/owner-do.ts"]),
  ...imports("B23", "platform/doors/test/env.d.ts", ["platform/owner/src/owner-do.ts"]),
  ...imports("B23", "platform/doors/test/connect.test.ts", ["platform/owner/src/owner-do.ts"]),
  ...imports("B23", "platform/doors/test/doors-live.test.ts", [
    "platform/owner/src/admission.ts", "platform/owner/src/inbound.ts",
    "platform/owner/src/erasure.ts", "platform/owner/src/ledger.ts",
    "platform/owner/test/runs-support.ts", "platform/owner/test/support.ts"]),
  ...imports("B23", "platform/doors/test/e2b-hooks.test.ts",
             ["platform/owner/src/sandbox-time.ts"]),
  ...imports("B23", "platform/doors/test/egress.test.ts", ["platform/owner/test/runs-support.ts"]),
  ...imports("B23", "platform/doors/test/entry.ts", ["platform/owner/src/owner-do.ts"]),
  ...imports("B23", "platform/doors/test/env.d.ts", ["platform/owner/test/fake-sandbox.ts",
                                                      "platform/owner/test/fake-erasures.ts"]),
  ...imports("B23", "platform/doors/test/events.test.ts", ["platform/owner/src/owner-do.ts"]),
  ...imports("B23", "platform/doors/test/hardening.test.ts", ["platform/owner/src/bodies.ts"]),
  ...imports("B23", "platform/doors/test/runs.test.ts",
             ["platform/owner/test/runs-support.ts", "platform/owner/src/runs.ts"]),
  ...imports("B23", "platform/doors/test/sync.test.ts", ["platform/owner/test/runs-support.ts"]),
]);
