/**
 * The doors Worker's config allow-list (research §13), checked by the platform's guard kit
 * (`ci/kit/config.ts`); what it checks against the other Workers' configs and sources is
 * `config.test.ts`'s.
 *
 * - `OWNER`: the owner Worker's `OwnerDO` namespace, by `script_name`, which must be the
 *   owner Worker's own SQLite class in the platform. The binder is the trust root and
 *   reaches every owner's object by name; only `src/binder.ts` names it.
 * - `OAUTH_KV`, `DIRECTORY` and `CONTROL`: three KV namespaces, distinct, with placeholder ids
 *   (`not-provisioned-…`), so a deploy fails until cutover creates them.
 * - Four vars: `DCR_ENABLED` `"false"`; `OWNER_ISSUER` and `UPSTREAM` unset
 *   (`not-configured`) as checked in, so every sign-in fails loudly until the deploy kit
 *   fills both from the issuer's live discovery (`deploy/deploy.ts`); and
 *   `FOREIGN_ISSUERS` empty (`[]`) until a provider's slice lists its issuer, so any other
 *   issuer is refused, never taken for a new owner.
 * - Three secrets, declared by name (`secrets.required`, which binds nothing):
 *   `ROUTING_MAC_KEY`, the routing key sandbox tokens are MAC'd under, the owner Worker's
 *   too; `WORKOS_API_KEY`, the upstream's client secret; and `E2B_WEBHOOK_SECRET`, E2B's
 *   lifecycle webhook's signature secret (slice P).
 * - No `ratelimits`: no number of ours bounds any caller (the founder's rule, slice L's
 *   review); Cloudflare's own DDoS protection is in front of every Worker.
 * - `ASSETS`: the owner app's files (`app/`), Workers Static Assets, the Worker first on every
 *   request, so only `src/pages.ts`'s named pages and files are served, each with its
 *   headers; no HTML handling or not-found page of the assets' own.
 * - The consumer of the sandbox line's ring queue (slice S, `src/ring.ts`), which turns
 *   `{owner}` into that owner's `sandboxRung` and nothing else, and of the erasure queue.
 * - The cutover's import window (`src/import.ts`), deleted at the cutover with
 *   `platform/import/` and named here as its bindings alone (`IMPORT_WINDOW`): the import
 *   queue's consumer (one message a batch, four owners at once, its dead-letter queue), its
 *   producer (`IMPORTS`, the consumer's own continuation, the doors' one producer), and the
 *   staging bucket (`IMPORT_STAGING`), the doors' one bucket.
 * - No routes, custom domain, workers.dev or preview URL, no environment, no migration
 *   (the doors define no Durable Object class).
 * - Flags: `nodejs_compat` (the owner's shared code), `disable_ctx_exports` (no way to the
 *   Worker's own namespaces), and `global_fetch_strictly_public`, which Client ID Metadata
 *   Documents require: the provider fetches each client's metadata URL, the flag keeps
 *   that fetch off same-zone routing (its SSRF protection), and without it the provider
 *   neither fetches nor advertises CIMD (OP docs/authorization-server.md:62-68); and
 *   `enable_request_signal`, so a sandbox client that leaves before the answer aborts the
 *   egress door's request and its settle comes at once.
 */
import type { ConfigRules } from "../../ci/kit/config";
import { imports, type Owed } from "../../ci/kit/owed";

/**
 * The import window's bindings (`src/import.ts`), each named once here: deleted at the
 * cutover with `platform/import/`, as tasks.md §9's deletion list names them.
 */
export const IMPORT_WINDOW = Object.freeze({
  bucket: Object.freeze({ binding: "IMPORT_STAGING", bucket_name: "tinyassets-import-staging" }),
  producer: Object.freeze({ binding: "IMPORTS", queue: "tinyassets-import" }),
  consumer: Object.freeze({ queue: "tinyassets-import", max_batch_size: 1, max_concurrency: 4,
                            dead_letter_queue: "tinyassets-import-dlq" }),
});

export const CONFIG: ConfigRules = Object.freeze({
  worker: "doors",
  name: "tinyassets-doors",
  main: "src/index.ts",
  keys: Object.freeze(["$schema", "name", "main", "compatibility_date", "compatibility_flags",
                       "workers_dev", "preview_urls", "vars", "durable_objects", "services",
                       "kv_namespaces", "secrets", "queues", "assets", "r2_buckets"]),
  flags: Object.freeze({ exactly: Object.freeze(["nodejs_compat", "disable_ctx_exports",
                                                 "global_fetch_strictly_public",
                                                 "enable_request_signal"]) }),
  secrets: Object.freeze(["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET"]),
  vars: Object.freeze({ DCR_ENABLED: ["false"], OWNER_ISSUER: ["not-configured"],
                        FOREIGN_ISSUERS: ["[]"], UPSTREAM: ["not-configured"] }),
  kv: Object.freeze(["OAUTH_KV", "DIRECTORY", "CONTROL"]),
  buckets: Object.freeze([IMPORT_WINDOW.bucket.binding]),
  exact: Object.freeze({
    assets: { directory: "./app", binding: "ASSETS", run_worker_first: true,
              html_handling: "none", not_found_handling: "none" },
    r2_buckets: [IMPORT_WINDOW.bucket],
    queues: { producers: [IMPORT_WINDOW.producer],
              consumers: [{ queue: "tinyassets-sandbox-ring" }, { queue: "tinyassets-erasure" },
                          IMPORT_WINDOW.consumer] },
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
  ...imports("B23", "platform/doors/test/connect.test.ts", ["platform/owner/src/owner-do.ts"]),
  ...imports("B23", "platform/doors/test/doors-live.test.ts", [
    "platform/owner/src/admission.ts", "platform/owner/src/inbound.ts",
    "platform/owner/src/erasure.ts", "platform/owner/src/ledger.ts",
    "platform/owner/test/runs-support.ts", "platform/owner/test/support.ts"]),
  ...imports("B23", "platform/doors/test/e2b-hooks.test.ts",
             ["platform/owner/src/sandbox-time.ts"]),
  ...imports("B23", "platform/doors/test/egress.test.ts", ["platform/owner/test/runs-support.ts"]),
  ...imports("B23", "platform/doors/test/entry.ts", [
    "platform/owner/src/owner-do.ts", "platform/owner/test/erasure-fake.ts"]),
  ...imports("B23", "platform/doors/test/env.d.ts", ["platform/owner/test/fake-sandbox.ts"]),
  ...imports("B23", "platform/doors/test/events.test.ts", ["platform/owner/src/owner-do.ts"]),
  ...imports("B23", "platform/doors/test/hardening.test.ts", ["platform/owner/src/bodies.ts"]),
  ...imports("B23", "platform/doors/test/runs.test.ts",
             ["platform/owner/test/runs-support.ts", "platform/owner/src/runs.ts"]),
  ...imports("B23", "platform/doors/test/sync.test.ts", ["platform/owner/test/runs-support.ts"]),
  // The hand test env, typed from the owner's class: B9 generates it.
  ...imports("B9", "platform/doors/test/env.d.ts", ["platform/owner/src/owner-do.ts"]),
  // The import window's tests, reaching the importer: B24's cutover layer lists them.
  ...imports("B24", "platform/doors/test/import-setup.ts", [
    "platform/import/bundle.ts", "platform/import/stage.ts", "platform/import/guards/fixture.ts"]),
  ...imports("B24", "platform/doors/test/import.test.ts", ["platform/import/check.ts"]),
]);
