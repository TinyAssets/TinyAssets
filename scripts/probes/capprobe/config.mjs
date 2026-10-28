#!/usr/bin/env node
/**
 * Writes copies of both probe configs with `limits.subrequests` pinned, for a run that
 * sets the workflow's `subrequests_limit` (cap-probe.md, "Next"). The committed configs
 * stay as they are; each copy points `main` at the committed script by absolute path.
 *
 *   node scripts/probes/capprobe/config.mjs --limit 50 --out <dir>
 *     -> <dir>/caller.wrangler.json, <dir>/callee.wrangler.json
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const CONFIGS = Object.freeze(["caller", "callee"]);
export const MIN_LIMIT = 25;
export const MAX_LIMIT = 10_000;

/** One committed config, with its whole-line `//` comments dropped (they hold no data). */
export function readCommitted(name) {
  const path = join(HERE, `${name}.wrangler.jsonc`);
  const text = readFileSync(path, "utf8").split("\n").filter((line) => !/^\s*\/\//.test(line)).join("\n");
  return { path, config: JSON.parse(text) };
}

/** Writes both pinned copies into `outDir` and returns their paths, by config name. */
export function writeLimited(limit, outDir) {
  if (!Number.isInteger(limit) || limit < MIN_LIMIT || limit > MAX_LIMIT) {
    throw new Error(`the subrequest limit must be a whole number from ${MIN_LIMIT} to ${MAX_LIMIT}`);
  }
  mkdirSync(outDir, { recursive: true });
  const out = {};
  for (const name of CONFIGS) {
    const { path, config } = readCommitted(name);
    const limited = { ...config, main: resolve(dirname(path), config.main),
                      limits: { ...(config.limits ?? {}), subrequests: limit } };
    out[name] = join(outDir, `${name}.wrangler.json`);
    writeFileSync(out[name], `${JSON.stringify(limited, null, 2)}\n`);
  }
  return out;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) =>
    (i % 2 === 0 ? [...pairs, [arg.replace(/^--/, ""), all[i + 1]]] : pairs), []));
  try {
    const paths = writeLimited(Number(args.limit), args.out ?? "");
    for (const [name, path] of Object.entries(paths)) {
      console.log(`${name}: ${path}`);
    }
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exit(1);
  }
}
