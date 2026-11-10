// The platform's module resolution (its tsconfig's `moduleResolution: "bundler"`), for the
// template's TypeScript tools run by Node's own type stripping (`pair.ts` `tsTool`): a
// relative import without an extension is the `.ts` file of that name, and a JSON import
// needs no `with { type: "json" }`, as esbuild, wrangler and vitest read them. Nothing else
// changes: every other specifier resolves as Node resolves it.
//
//   node --experimental-strip-types --import ./resolve.mjs <tool.ts> [args]
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, next) {
    if (/^\.\.?\//.test(specifier) && specifier.endsWith(".json")) {
      return { ...next(specifier, context), importAttributes: { type: "json" } };
    }
    if (/^\.\.?\//.test(specifier) && !/\.[cm]?[jt]s$/.test(specifier)
        && context.parentURL?.endsWith(".ts")
        && existsSync(fileURLToPath(new URL(`${specifier}.ts`, context.parentURL)))) {
      return next(`${specifier}.ts`, context);
    }
    return next(specifier, context);
  },
});
