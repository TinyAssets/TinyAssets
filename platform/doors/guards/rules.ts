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
 * - One queue role: the consumer of the sandbox line's ring queue (slice S, `src/ring.ts`),
 *   which turns `{owner}` into that owner's `sandboxRung` and nothing else, and of the
 *   erasure queue. No producer: the doors put nothing on a queue.
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

export const CONFIG: ConfigRules = Object.freeze({
  worker: "doors",
  env: Object.freeze(["src/config.ts#DoorsEnv"]),
  name: "tinyassets-doors",
  main: "src/index.ts",
  keys: Object.freeze(["$schema", "name", "main", "compatibility_date", "compatibility_flags",
                       "workers_dev", "preview_urls", "vars", "durable_objects", "services",
                       "kv_namespaces", "secrets", "queues", "assets"]),
  flags: Object.freeze({ exactly: Object.freeze(["nodejs_compat", "disable_ctx_exports",
                                                 "global_fetch_strictly_public",
                                                 "enable_request_signal"]) }),
  secrets: Object.freeze(["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET"]),
  vars: Object.freeze({ DCR_ENABLED: ["false"], OWNER_ISSUER: ["not-configured"],
                        FOREIGN_ISSUERS: ["[]"], UPSTREAM: ["not-configured"] }),
  kv: Object.freeze(["OAUTH_KV", "DIRECTORY", "CONTROL"]),
  exact: Object.freeze({
    assets: { directory: "./app", binding: "ASSETS", run_worker_first: true,
              html_handling: "none", not_found_handling: "none" },
    queues: { consumers: [{ queue: "tinyassets-sandbox-ring" }, { queue: "tinyassets-erasure" }] },
  }),
});
