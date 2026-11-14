// The sandbox Worker's build (`wrangler.jsonc`'s `build.command`): the runner it ships, and the
// template's probes beside it, two ES modules for Node, built with the workspace's own esbuild
// (wrangler's), each from its own entry (`runner/runner.ts`, `runner/probes.ts`) in a build of
// its own, so neither output depends on the other. The probes are given the runner's SHA-256
// (`TA_RUNNER_SHA`), so they run only beside the runner they were built with. The pair the
// deploy looks for names both digests (`template/pair.ts`, which names both files).
//
//   node sandbox/build/bundle.mjs <runner> <probes>   writes the runner to <runner> and the
//                                                      probes to <probes>
import { createHash } from "node:crypto";
import { mkdirSync, realpathSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const platform = join(here, "..", "..");
const require = createRequire(join(platform, "package.json"));
const esbuild = require("esbuild");

/** Each bundle's entry. */
const ENTRIES = Object.freeze({ runner: "sandbox/runner/runner.ts",
                                probes: "sandbox/runner/probes.ts" });

/** One bundle's build: the same options for both, `define` the probes' runner digest. */
async function one(root, entry, define) {
  const built = await esbuild.build({
    entryPoints: [join(root, entry)],
    bundle: true,
    platform: "node",
    target: "node22",
    format: "esm",
    banner: { js: "import { createRequire as __ta } from 'node:module'; " +
                  "const require = __ta(import.meta.url);" },
    legalComments: "none",
    define,
    // Paths in the bundle's comments from the workspace root, whoever runs it, so its digest
    // (the pair's tag) is the same everywhere.
    absWorkingDir: root,
    logLevel: "warning",
    write: false,
    outfile: join(root, "bundle.mjs"),
    metafile: true,
  });
  const bytes = Buffer.from(built.outputFiles[0].contents);
  return { bytes, sha: createHash("sha256").update(bytes).digest("hex"),
           inputs: Object.keys(built.metafile.inputs).sort() };
}

/**
 * Both bundles of the platform tree at `root` (this one by default), in memory: each one's
 * bytes, SHA-256 and the source files it was built from.
 */
export async function build(root = platform) {
  const runner = await one(root, ENTRIES.runner, {});
  const probes = await one(root, ENTRIES.probes,
                           { TA_RUNNER_SHA: JSON.stringify(runner.sha) });
  return { runner, probes };
}

if (process.argv[1] !== undefined
    && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const files = process.argv.slice(2).map((file) => resolve(file));
  if (files.length !== 2) {
    throw new Error("bundle.mjs <runner> <probes>");
  }
  const built = await build();
  for (const [file, bundle] of [[files[0], built.runner], [files[1], built.probes]]) {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, bundle.bytes);
  }
}
