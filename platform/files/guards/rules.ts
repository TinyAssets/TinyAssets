/**
 * The files Worker's config allow-list (ADR-032, plan §6), checked by the platform's guard
 * kit (`ci/kit/config.ts`).
 *
 * The owner Worker may hold a service binding to this Worker only because this Worker binds
 * no Durable Object and no service (`owner/guards/config.test.ts` checks that against this
 * Worker's config). So its config allows the keys that name it, its entry and runtime, the
 * R2 bucket `BLOBS`, no var (there is one cipher), its one secret, the data keys' ring
 * `FILES_KEYRING` (`secrets.required`, so a deploy without it fails), and no URL of its own
 * (`workers_dev` and `preview_urls` false, no route). No environment may add anything.
 */
import type { ConfigRules } from "../../ci/kit/config";

export const CONFIG: ConfigRules = Object.freeze({
  worker: "files",
  name: "tinyassets-files",
  main: "src/index.ts",
  keys: Object.freeze(["$schema", "name", "main", "compatibility_date", "compatibility_flags",
                       "workers_dev", "preview_urls", "r2_buckets", "secrets"]),
  flags: Object.freeze({ carries: Object.freeze(["disable_ctx_exports"]) }),
  secrets: Object.freeze(["FILES_KEYRING"]),
  buckets: Object.freeze(["BLOBS"]),
});
