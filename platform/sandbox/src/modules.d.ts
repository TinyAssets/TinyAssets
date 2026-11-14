/**
 * The runner bundle, imported as data (`wrangler.jsonc` `rules`; `runner-bytes.ts`): its one
 * declaration, as wrangler's types would write it (the platform keeps none of theirs, d3 §R).
 */
declare module "*/dist/runner.mjs" {
  const bytes: ArrayBuffer;
  export default bytes;
}
