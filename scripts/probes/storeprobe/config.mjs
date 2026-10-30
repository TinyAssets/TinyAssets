#!/usr/bin/env node
/**
 * Writes the store probe's deployable configs (store-probe.md). The committed configs stay
 * as they are:
 * - owner.js and shape.mjs are copied into platform/node_modules/.storeprobe/, so wrangler bundles it
 *   against platform/'s lockfile (pi-durable, pi-ai and chord at the owner's pins) rather
 *   than a toolchain of the probe's own;
 * - three copies of owner.wrangler.jsonc, each pointing at that copy: `main`
 *   (ta-storeprobe, the owner's 10,000), `low` (ta-storeprobe-low, LOW_LIMIT, where P1's
 *   attribution cases discriminate) and `raised` (ta-storeprobe-raised, the limit asked);
 * - door.wrangler.jsonc, binding the Owner class of each copy named in `copies`.
 *
 *   node scripts/probes/storeprobe/config.mjs --out <dir> --raised 1000000 [--copies main,low,raised]
 *     -> <dir>/main.wrangler.json, low..., raised..., door.wrangler.json
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const PLATFORM = join(HERE, "..", "..", "..", "platform");
/** Where owner.js is bundled from: inside platform/'s own node_modules, never committed. */
export const BUILD = join(PLATFORM, "node_modules", ".storeprobe");
/** The owner Worker's own pin (platform/owner/wrangler.jsonc). */
export const MAIN_LIMIT = 10_000;
/** P1a and P1b's limit: low enough that a few hundred calls discriminate. */
export const LOW_LIMIT = 100;
/** The raised limit must clear P1c's 10,100 calls; Paid's documented maximum is 10M. */
export const MIN_RAISED = 20_000;
export const MAX_RAISED = 10_000_000;
export const COPIES = Object.freeze({
  main: "ta-storeprobe", low: "ta-storeprobe-low", raised: "ta-storeprobe-raised",
});
export const DOOR = "ta-storeprobe-door";
const DOOR_BINDINGS = Object.freeze({ main: "OWNER", low: "OWNER_LOW", raised: "OWNER_RAISED" });

/** One committed config, with its whole-line `//` comments dropped (they hold no data). */
export function readCommitted(name) {
  const path = join(HERE, `${name}.wrangler.jsonc`);
  const text = readFileSync(path, "utf8").split("\n").filter((line) => !/^\s*\/\//.test(line)).join("\n");
  return JSON.parse(text);
}

/** The limit each copy deploys with. */
export function limits(raised) {
  return { main: MAIN_LIMIT, low: LOW_LIMIT, raised };
}

/** Writes every config into `outDir` and returns their paths, by copy name and `door`. */
export function writeConfigs({ raised, outDir, copies = Object.keys(COPIES) }) {
  if (!Number.isInteger(raised) || raised < MIN_RAISED || raised > MAX_RAISED) {
    throw new Error(`the raised limit must be a whole number from ${MIN_RAISED} to ${MAX_RAISED}`);
  }
  if (!copies.includes("main") || copies.some((copy) => !(copy in COPIES))) {
    throw new Error(`copies must include main and name only ${Object.keys(COPIES).join(", ")}`);
  }
  if (!existsSync(join(PLATFORM, "node_modules"))) {
    throw new Error("platform/node_modules is missing: run npm ci in platform/ first");
  }
  mkdirSync(BUILD, { recursive: true });
  for (const file of ["owner.js", "shape.mjs"]) {
    copyFileSync(join(HERE, file), join(BUILD, file));
  }
  mkdirSync(outDir, { recursive: true });
  const owner = readCommitted("owner");
  const limit = limits(raised);
  const out = {};
  for (const copy of copies) {
    const config = { ...owner, name: COPIES[copy], main: join(BUILD, "owner.js"),
                     limits: { ...(owner.limits ?? {}), subrequests: limit[copy] } };
    out[copy] = join(outDir, `${copy}.wrangler.json`);
    writeFileSync(out[copy], `${JSON.stringify(config, null, 2)}\n`);
  }
  const door = readCommitted("door");
  const bindings = door.durable_objects.bindings
    .filter((binding) => copies.some((copy) => DOOR_BINDINGS[copy] === binding.name));
  out.door = join(outDir, "door.wrangler.json");
  writeFileSync(out.door, `${JSON.stringify({ ...door, main: join(HERE, "door.js"),
                                               durable_objects: { bindings } }, null, 2)}\n`);
  return out;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) =>
    (i % 2 === 0 ? [...pairs, [arg.replace(/^--/, ""), all[i + 1]]] : pairs), []));
  try {
    const copies = (args.copies ?? Object.keys(COPIES).join(",")).split(",").filter(Boolean);
    const paths = writeConfigs({ raised: Number(args.raised), outDir: args.out ?? "", copies });
    for (const [name, path] of Object.entries(paths)) {
      console.log(`${name}: ${path}`);
    }
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exit(1);
  }
}
