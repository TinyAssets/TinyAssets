/**
 * The codemode Worker's allow-lists (agent research §6.4; pi audit F8; narrowed to code mode by
 * ADR-034 decision 12), checked by the platform's guard kit (`ci/kit/`).
 *
 * Every code mode script runs in this Worker, so it must reach no owner's object, store,
 * ledger or policy, and nothing but the owner Worker's `CODEMODE_VM` service binding may reach it.
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
  worker: "codemode",
  name: "tinyassets-codemode",
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
 * or `CODEMODE_VM` (it is never bound to itself), nor `storage` (its objects never use their
 * storage, so a script leaves nothing behind), nor `process`, over its whole import closure.
 * What it may import is the layer table's (`ci/kit/layers.ts`).
 */
export const IMPORTS: ImportRules = Object.freeze({
  worker: "codemode",
  called: "the codemode Worker",
  banned: Object.freeze([{
    pattern: /^(?:OWNER|FILES|GATEWAY|BLOBS|CODEMODE_VM|storage|process|node:process)$/,
    says: "names %s",
  }]),
});
