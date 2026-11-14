/**
 * Each queue's name, by its producer's binding: written by `npm run generate`
 * (`ci/kit/types.ts`) from the Workers' `wrangler.jsonc`, the names' one home. Edit the
 * configs, never this file.
 */
export const QUEUES = Object.freeze({
  ERASURES: "tinyassets-erasure",
  IMPORTS: "tinyassets-import",
  RING: "tinyassets-sandbox-ring",
});
