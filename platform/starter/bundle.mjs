// Everything built from the starter package, by one build, so no change to it can leave one
// of them stale:
// - `starter.json`, the one JSON document the owner's object carries: `manifest.json`'s
//   version, OKF pin, files and retired paths, each file with its text read from the same
//   path under this directory;
// - the connector-directory plugins (`../plugins/chatgpt/`, `../plugins/claude/`), each
//   carrying every starter skill file a manifest entry does not mark `"plugin": false`
//   (`scratch`, which is about a sandbox's own disk), at the starter's version.
// `node platform/starter/bundle.mjs` writes them all; `--check` exits 1 when anything
// committed differs from what this makes (the owner's starter guard and the doors' apps
// guard run it). Writing, a path the committed bundle named that the new one names nowhere,
// neither as a file nor as `retired`, fails: a dropped path must be retired, so an owner who
// deleted it keeps it deleted if a later version ships it again.
import { mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { plugins } from "../plugins/plugins.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "starter.json");
const PLUGINS = join(HERE, "..", "plugins");
const PLUGIN_DIRS = Object.freeze(["chatgpt", "claude"]);
const SKILLS = "/workspace/.agents/skills/";

function manifest() {
  return JSON.parse(readFileSync(join(HERE, "manifest.json"), "utf8"));
}

function textOf(path) {
  if (!path.startsWith("/workspace/")) {
    throw new Error(`${path} is not under /workspace`);
  }
  return readFileSync(join(HERE, path.slice(1)), "utf8");
}

export function bundle() {
  const { version, okf, files, retired } = manifest();
  const carried = files.map(({ plugin: _plugin, ...file }) => ({ ...file, text: textOf(file.path) }));
  return `${JSON.stringify({ version, okf, files: carried, retired: retired ?? [] }, null, 1)}\n`;
}

/** The plugins' files, by path under `platform/plugins/`. */
export function pluginFiles() {
  const { version, files } = manifest();
  const skills = Object.fromEntries(files
    .filter((file) => file.path.startsWith(SKILLS) && file.plugin !== false)
    .map((file) => [file.path.slice(SKILLS.length), textOf(file.path)]));
  return plugins(skills, version);
}

/** The paths `previous` (a bundle's JSON) names that `next` names nowhere. */
export function dropped(previous, next) {
  const named = (pkg) => [...pkg.files.map((file) => file.path), ...(pkg.retired ?? [])];
  const kept = new Set(named(next));
  return named(previous).filter((path) => !kept.has(path));
}

/** Every file under `dir`, as paths relative to it; none when it does not exist. */
function walk(dir, under = dir) {
  let names;
  try {
    names = readdirSync(dir);
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
  return names.sort().flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path, under) : [relative(under, path)];
  });
}

/** The plugins' files as committed, by path under `platform/plugins/`. */
function committedPlugins() {
  return Object.fromEntries(PLUGIN_DIRS.flatMap((dir) => walk(join(PLUGINS, dir)).map((file) =>
    [join(dir, file), readFileSync(join(PLUGINS, dir, file), "utf8")])));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const made = bundle();
  const madePlugins = pluginFiles();
  let committed = "";
  try {
    committed = readFileSync(OUT, "utf8");
  } catch {
    // none yet: nothing was dropped, and a check differs
  }
  if (process.argv.includes("--check")) {
    const now = committedPlugins();
    const stale = [...(committed === made ? [] : ["starter/starter.json"]),
                   ...[...new Set([...Object.keys(madePlugins), ...Object.keys(now)])]
                     .filter((path) => madePlugins[path] !== now[path]).sort()
                     .map((path) => `plugins/${path}`)];
    if (stale.length > 0) {
      console.error(`stale (${stale.join(", ")}): run node platform/starter/bundle.mjs`);
      process.exit(1);
    }
  } else {
    const lost = committed === "" ? [] : dropped(JSON.parse(committed), JSON.parse(made));
    if (lost.length > 0) {
      console.error(`these paths left the package without being retired: ${lost.join(", ")}; ` +
                    "list each in manifest.json's retired");
      process.exit(1);
    }
    writeFileSync(OUT, made);
    for (const dir of PLUGIN_DIRS) {
      rmSync(join(PLUGINS, dir), { recursive: true, force: true });
    }
    for (const [path, text] of Object.entries(madePlugins)) {
      mkdirSync(dirname(join(PLUGINS, path)), { recursive: true });
      writeFileSync(join(PLUGINS, path), text);
    }
  }
}
