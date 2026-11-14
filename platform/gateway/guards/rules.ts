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
import type { Exemption } from "../../ci/kit/module-state";
import { envFile } from "../../ci/kit/sources";

/**
 * The module state the gateway keeps by design (`ci/kit/module-state.ts`), none of it an
 * owner's data:
 * - its global-fetch interposer (`egress.ts`). pi-ai's Google adapters take no custom fetch,
 *   so the gateway replaces the global `fetch`, once, by a function that is the current
 *   call's endpoint guard inside that call's `AsyncLocalStorage` scope and the runtime's own
 *   fetch outside any. That needs a module-level scope, the captured runtime fetch and one
 *   write of the global (gateway spec, "Every adapter request goes through the call's guard");
 * - its isolate's chunk slots (`held.ts`; the pool itself is the contract's,
 *   `contract/src/slots.ts`, which holds no state): the pool is the isolate's, shared by every
 *   call in it, and holds a count of slots in use and the calls waiting, never an owner's data.
 */
const INTERPOSER = "gateway/src/egress.ts";
export const MODULE_STATE: readonly Exemption[] = Object.freeze([
  { file: INTERPOSER, symbol: "SCOPE", reason: "the current call's guard, for its scope alone" },
  { file: INTERPOSER, symbol: "NATIVE", reason: "the runtime's own fetch, captured once" },
  { file: INTERPOSER, symbol: "globalThis.fetch", reason: "the interposer, written once" },
  { file: "gateway/src/held.ts", symbol: "ISOLATE", reason: "the isolate's one chunk pool" },
].map((each) => Object.freeze(each)));

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
 * - `GATEWAY_KEYRING` is named only in `src/keyring.ts` (and declared in the generated
 *   `src/env.ts`); and no source names `process` (an
 *   identifier, a string such as `globalThis["process"]`, or the `node:process` module), so
 *   the env handed to a call is the only way to the keyring;
 * - no source names `OWNER` or `getByName`: the gateway is handed the owner and never looks
 *   one up, and its config binds no namespace to look one up in.
 */
export const IMPORTS: ImportRules = Object.freeze({
  worker: "gateway",
  called: "the gateway",
  banned: Object.freeze([
    { pattern: /GATEWAY_KEYRING/, homes: [KEYRING_HOME, envFile("gateway")],
      says: "names GATEWAY_KEYRING outside keyring.ts" },
    { pattern: /^(?:process|node:process)$/, says: "names process, whose env the gateway never reads" },
    { pattern: /\bOWNER\b|getByName/, says: "names %s" },
  ]),
});
