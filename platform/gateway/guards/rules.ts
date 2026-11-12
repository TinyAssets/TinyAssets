/**
 * The gateway Worker's allow-lists (research §1.4, §12 G1), checked by the platform's guard
 * kit (`ci/kit/`).
 *
 * The gateway holds every owner's credentials' only key, so it must reach no owner's
 * object, no store and no other Worker, and nothing may reach it but the service bindings
 * of the owner and doors Workers.
 */
import type { ConfigRules } from "../../ci/kit/config";
import type { ImportRules } from "../../ci/kit/imports";

/**
 * Its config: the keys that name the Worker, its entry and its runtime, and `secrets`, which
 * declares the keyring (a declaration that binds nothing). Every other key wrangler's schema
 * offers is refused: each binding kind, `routes` and `route`, a custom domain, `vars`,
 * environments (which could bind anything), migrations, and any key wrangler adds later.
 * The flags are exactly `nodejs_compat`, `nodejs_compat_do_not_populate_process_env` (so the
 * keyring never sits in `process.env`), `disable_ctx_exports` and
 * `global_fetch_strictly_public`.
 */
export const CONFIG: ConfigRules = Object.freeze({
  worker: "gateway",
  env: Object.freeze(["src/keyring.ts#KeyringEnv"]),
  name: "tinyassets-gateway",
  main: "src/index.ts",
  keys: Object.freeze(["$schema", "name", "main", "compatibility_date", "compatibility_flags",
                       "workers_dev", "preview_urls", "secrets"]),
  flags: Object.freeze({ exactly: Object.freeze([
    "nodejs_compat", "nodejs_compat_do_not_populate_process_env", "disable_ctx_exports",
    "global_fetch_strictly_public"]) }),
  secrets: Object.freeze(["GATEWAY_KEYRING"]),
});

/** Where the keyring may be named: its one reader. */
export const KEYRING_HOME = "gateway/src/keyring.ts";

/**
 * Its source, over its whole import closure (what it may import is the layer table's,
 * `ci/kit/layers.ts`: its own source, the contract and the source records):
 * - `GATEWAY_KEYRING` is named only in `src/keyring.ts`; and no source names `process` (an
 *   identifier, a string such as `globalThis["process"]`, or the `node:process` module), so
 *   the env handed to a call is the only way to the keyring;
 * - no source names `OWNER` or `getByName`: the gateway is handed the owner and never looks
 *   one up, and its config binds no namespace to look one up in.
 */
export const IMPORTS: ImportRules = Object.freeze({
  worker: "gateway",
  called: "the gateway",
  banned: Object.freeze([
    { pattern: /GATEWAY_KEYRING/, home: KEYRING_HOME, says: "names GATEWAY_KEYRING outside keyring.ts" },
    { pattern: /^(?:process|node:process)$/, says: "names process, whose env the gateway never reads" },
    { pattern: /\bOWNER\b|getByName/, says: "names %s" },
  ]),
});
