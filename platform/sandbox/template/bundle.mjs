// The runner as the template carries it: one ES module for Node, its records and checks
// bundled in (`runner/runner.ts`), built with the workspace's own esbuild (wrangler's).
// `node sandbox/template/bundle.mjs <out>` writes `<out>/runner.mjs` and prints its SHA-256.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "..", "..", "package.json"));
const esbuild = require("esbuild");
const out = process.argv[2] === undefined ? undefined : resolve(process.argv[2]);
if (!out) {
  throw new Error("bundle.mjs <out dir>");
}
mkdirSync(out, { recursive: true });
await esbuild.build({
  entryPoints: [join(here, "..", "runner", "runner.ts")],
  outfile: join(out, "runner.mjs"),
  bundle: true,
  platform: "node",
  target: "node22",
  format: "esm",
  banner: { js: "import { createRequire as __ta } from 'node:module'; " +
                "const require = __ta(import.meta.url);" },
  legalComments: "none",
  // Paths in the bundle's comments from the workspace root, whoever runs it, so its digest
  // (the template's tag) is the same everywhere.
  absWorkingDir: join(here, "..", ".."),
  logLevel: "warning",
});
const bytes = readFileSync(join(out, "runner.mjs"));
process.stdout.write(`${createHash("sha256").update(bytes).digest("hex")}\n`);
