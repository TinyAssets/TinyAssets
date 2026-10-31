#!/usr/bin/env node
/**
 * Writes the facet probe's deployable configs (facet-probe.md). The committed configs stay
 * as they are:
 * - the Worker sources are copied into platform/node_modules/.facetprobe/, so wrangler
 *   bundles them against platform/'s lockfile (agents, pi-durable, pi-ai and chord at the
 *   owner's pins) rather than a toolchain of the probe's own; child.js goes there twice,
 *   once as child.txt, the text parent.js hands Worker Loader;
 * - four copies of parent.wrangler.jsonc (shape.mjs COPIES): `main` (the owner's runtime),
 *   `exports` (the control: `disable_ctx_exports` dropped, so `ctx.exports` is on),
 *   `classbind` (main plus a Durable Object binding, CHILD_CLASS, to the unmigrated Child)
 *   and `unsafe` (main plus an unsafe `durable_object_class` binding, CHILD_DOCLASS);
 * - two of agents.wrangler.jsonc: `agents` (the owner's runtime) and `agents-exports`;
 * - dummy.wrangler.jsonc, and door.wrangler.jsonc binding only the copies named in `copies`.
 * `kv`, when given, is the id of the dummy KV namespace every parent copy binds as KV.
 *
 *   node scripts/probes/facetprobe/config.mjs --out <dir> [--kv <id>] [--copies main,exports,...]
 *     -> <dir>/<copy>.wrangler.json for each copy, dummy.wrangler.json, door.wrangler.json
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { COPIES, DOOR, DUMMY } from "./shape.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
export const PLATFORM = join(HERE, "..", "..", "..", "platform");
/** Where the Workers are bundled from: inside platform/'s own node_modules, never committed. */
export const BUILD = join(PLATFORM, "node_modules", ".facetprobe");
const SOURCES = ["parent.js", "child.js", "agents.js", "door.js", "dummy.js", "shape.mjs", "stats.mjs"];
const KV_ID = /^[0-9a-f]{32}$/;

/** One committed config, with its whole-line `//` comments dropped (they hold no data). */
export function readCommitted(name) {
  const text = readFileSync(join(HERE, `${name}.wrangler.jsonc`), "utf8")
    .split("\n").filter((line) => !/^\s*\/\//.test(line)).join("\n");
  return JSON.parse(text);
}

const withoutExportsFlag = (flags) => flags.filter((flag) => flag !== "disable_ctx_exports");

/** Every copy's config, by copy name, before paths are filled in. */
export function copyConfigs({ kv } = {}) {
  const parent = readCommitted("parent");
  const agents = readCommitted("agents");
  const base = kv ? { ...parent, kv_namespaces: [{ binding: "KV", id: kv }] } : parent;
  const bindings = base.durable_objects.bindings;
  return {
    main: { ...base, name: COPIES.main.script },
    exports: { ...base, name: COPIES.exports.script,
               compatibility_flags: withoutExportsFlag(base.compatibility_flags) },
    classbind: { ...base, name: COPIES.classbind.script,
                 durable_objects: { bindings: [...bindings, { name: "CHILD_CLASS", class_name: "Child" }] } },
    unsafe: { ...base, name: COPIES.unsafe.script,
              unsafe: { bindings: [{ name: "CHILD_DOCLASS", type: "durable_object_class", class_name: "Child" }] } },
    agents: { ...agents, name: COPIES.agents.script },
    "agents-exports": { ...agents, name: COPIES["agents-exports"].script,
                        compatibility_flags: withoutExportsFlag(agents.compatibility_flags) },
  };
}

/** Writes every config into `outDir` and returns their paths, by copy name, `dummy` and `door`. */
export function writeConfigs({ outDir, kv, copies = Object.keys(COPIES) }) {
  if (kv && !KV_ID.test(kv)) {
    throw new Error("the KV namespace id must be 32 lowercase hex characters");
  }
  if (copies.some((copy) => !(copy in COPIES))) {
    throw new Error(`copies must name only ${Object.keys(COPIES).join(", ")}`);
  }
  if (!existsSync(join(PLATFORM, "node_modules"))) {
    throw new Error("platform/node_modules is missing: run npm ci in platform/ first");
  }
  mkdirSync(BUILD, { recursive: true });
  for (const file of SOURCES) {
    copyFileSync(join(HERE, file), join(BUILD, file));
  }
  copyFileSync(join(HERE, "child.js"), join(BUILD, "child.txt"));
  mkdirSync(outDir, { recursive: true });
  const out = {};
  const write = (key, config) => {
    out[key] = join(outDir, `${key}.wrangler.json`);
    writeFileSync(out[key], `${JSON.stringify(config, null, 2)}\n`);
  };
  for (const [copy, config] of Object.entries(copyConfigs({ kv }))) {
    write(copy, { ...config, main: join(BUILD, config.main) });
  }
  const dummy = readCommitted("dummy");
  write("dummy", { ...dummy, name: DUMMY, main: join(BUILD, dummy.main) });
  const door = readCommitted("door");
  const keep = new Set(copies.map((copy) => COPIES[copy].binding));
  write("door", { ...door, name: DOOR, main: join(BUILD, door.main),
                  durable_objects: { bindings: door.durable_objects.bindings.filter((b) => keep.has(b.name)) } });
  return out;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const argv = process.argv.slice(2);
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    args[argv[i].replace(/^--/, "")] = argv[i + 1];
  }
  try {
    const copies = (args.copies ?? Object.keys(COPIES).join(",")).split(",").filter(Boolean);
    const paths = writeConfigs({ outDir: args.out ?? "", kv: args.kv || undefined, copies });
    for (const [name, path] of Object.entries(paths)) {
      console.log(`${name}: ${path}`);
    }
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exit(1);
  }
}
