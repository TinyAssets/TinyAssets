/**
 * The hooks Worker's allow-lists (agent research §6.4; pi audit F8; narrowed to code mode by
 * ADR-034 decision 12), checked by the platform's guard kit (`ci/kit/`).
 *
 * Every code mode script runs in this Worker, so it must reach no owner's object, store,
 * ledger or policy, and nothing but the owner Worker's `HOOKS` service binding may reach it.
 */
import type { ConfigRules } from "../../ci/kit/config";
import type { ImportRules } from "../../ci/kit/imports";
import type { Config } from "../../ci/kit/sources";

/**
 * Its config: the keys that name the Worker, its entry and its runtime; `limits` (exactly
 * `cpu_ms`, room above code mode's interrupt budget); `durable_objects`, exactly the
 * namespace of its own `CodemodeSandbox` (no `script_name`, so no other Worker's class,
 * `OwnerDO` never); and `migrations` making exactly that one, SQLite. Every other key
 * wrangler's schema offers is refused: each other binding kind (a service, a bucket, a KV
 * namespace, a var, a secret, a Worker Loader), `routes`, environments, and any key wrangler
 * adds later. The one flag is `disable_ctx_exports`. Its compatibility date is the owner
 * Worker's.
 */
export const CONFIG: ConfigRules = Object.freeze({
  worker: "hooks",
  env: Object.freeze(["src/index.ts#HooksEnv"]),
  name: "tinyassets-hooks",
  main: "src/index.ts",
  keys: Object.freeze(["$schema", "name", "main", "compatibility_date", "compatibility_flags",
                       "workers_dev", "preview_urls", "limits", "durable_objects", "migrations"]),
  flags: Object.freeze({ exactly: Object.freeze(["disable_ctx_exports"]) }),
  classes: Object.freeze(["CodemodeSandbox"]),
  exact: Object.freeze({
    durable_objects: { bindings: [{ name: "CODEMODE", class_name: "CodemodeSandbox" }] },
    limits: { cpu_ms: 300_000 },
  }),
  across: (config: Config, platform: ReadonlyMap<string, Config>) =>
    config["compatibility_date"] === platform.get("tinyassets-owner")?.["compatibility_date"]
      ? [] : ["the compatibility date is the owner Worker's"],
});

/**
 * Its source names no binding of the owner's world (`OWNER`, `FILES`, `GATEWAY`, `BLOBS`)
 * or `HOOKS` (it is never bound to itself), nor `storage` (its objects never use their
 * storage, so a script leaves nothing behind), nor `process`. It imports only its own
 * sources, its RPC contract (`contract/src/hooks.ts`, types alone), `cloudflare:workers` and
 * pi's code mode (`/cloudflare` in `codemode.ts` alone, which bundles QuickJS; `/portable`
 * for its types).
 */
export const IMPORTS: ImportRules = Object.freeze({
  worker: "hooks",
  called: "the hooks Worker",
  outside: Object.freeze(["contract/src/hooks"]),
  bare: Object.freeze({
    "cloudflare:workers": null,
    "@earendil-works/pi-codemode/cloudflare": "hooks/src/codemode.ts",
    "@earendil-works/pi-codemode/portable": null,
  }),
  banned: Object.freeze([{
    pattern: /^(?:OWNER|FILES|GATEWAY|BLOBS|HOOKS|storage|process|node:process)$/,
    says: "names %s",
  }]),
});
