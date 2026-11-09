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
 * Its source:
 * - `GATEWAY_KEYRING` is named only in `src/keyring.ts`; and no source names `process` (an
 *   identifier, a string such as `globalThis["process"]`, or the `node:process` module), so
 *   the env handed to a call is the only way to the keyring;
 * - no source names `OWNER` or `getByName`: the gateway is handed the owner and never looks
 *   one up, and its config binds no namespace to look one up in;
 * - it reaches outside `gateway/src/` only the contract's vocabulary it speaks: its own RPC
 *   contract (`contract/src/gateway.ts`), the owner grammar, the one refusal and its codes,
 *   the ids, the limits, the egress wire and scope with the per-API rules and the model
 *   record, the wake records with their callback checks (which the doorbell applies again
 *   where it sends) and their kinds' file, and the isolate's slot pool, and the sealed
 *   envelope its keyring seals by; none of it imports anything but itself;
 * - its packages are pi-ai, `cloudflare:workers`, and `node:async_hooks` in `egress.ts` alone.
 */
export const IMPORTS: ImportRules = Object.freeze({
  worker: "gateway",
  called: "the gateway",
  outside: Object.freeze([
    "contract/src/api-rules", "contract/src/checks", "contract/src/egress",
    "contract/src/egress-scope", "contract/src/envelope", "contract/src/gateway",
    "contract/src/ids",
    "contract/src/limits", "contract/src/model-record", "contract/src/refusal",
    "contract/src/slots", "contract/src/text", "contract/src/wake-records",
    "contract/wake-kinds.json",
  ]),
  packages: Object.freeze(["@earendil-works/pi-ai"]),
  bare: Object.freeze({ "cloudflare:workers": null, "node:async_hooks": "gateway/src/egress.ts" }),
  banned: Object.freeze([
    { pattern: /GATEWAY_KEYRING/, home: KEYRING_HOME, says: "names GATEWAY_KEYRING outside keyring.ts" },
    { pattern: /^(?:process|node:process)$/, says: "names process, whose env the gateway never reads" },
    { pattern: /\bOWNER\b|getByName/, says: "names %s" },
  ]),
});
