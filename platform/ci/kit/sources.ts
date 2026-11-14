/**
 * The platform's guard kit: what every workspace's static guards read (each Worker's
 * config files and source, parsed) and how they report.
 */
import { readdirSync, readFileSync } from "node:fs";
import { extname, join, relative } from "node:path";

import ts from "typescript";

import { PLATFORM } from "../paths.ts";
import { type Config, parseWrangler } from "../wrangler.ts";

export type { Config } from "../wrangler.ts";

/** Directories no config guard looks in: dependencies, and wrangler's own local state. */
const UNWALKED: ReadonlySet<string> = new Set(["node_modules", ".wrangler"]);

/**
 * Every wrangler config under `root` (`wrangler.json`, `.jsonc` or `.toml`, in any case; a
 * script named for wrangler, `ci/wrangler.ts`, is none), by path from `root`, sorted. The
 * walk never enters `node_modules` or `.wrangler`: listing them whole and filtering after
 * (28,000 entries under `platform/` once installed) was most of each config guard's time,
 * enough to pass a test's 5 s bound on a loaded CI runner.
 */
export function configFiles(root: string): string[] {
  const found: string[] = [];
  const walk = (dir: string, at: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = at === "" ? entry.name : `${at}/${entry.name}`;
      if (entry.isDirectory()) {
        if (!UNWALKED.has(entry.name)) {
          walk(join(dir, entry.name), path);
        }
      } else if (/^wrangler\.(?:jsonc?|toml)$/i.test(entry.name)) {
        found.push(path);
      }
    }
  };
  walk(root, "");
  return found.sort();
}

/** The platform's configs, read and parsed once: they do not change while the guards run. */
let PLATFORM_CONFIGS: ReadonlyMap<string, Readonly<Config>> | null = null;

/** Every Worker config in the platform, by its Worker's name; a map of the caller's own. */
export function platformConfigs(): Map<string, Config> {
  if (PLATFORM_CONFIGS === null) {
    const found = new Map<string, Config>();
    for (const path of configFiles(PLATFORM)) {
      const parsed = parseWrangler(readFileSync(join(PLATFORM, path), "utf8"), path);
      found.set(String(parsed["name"]), deepFreeze(parsed));
    }
    PLATFORM_CONFIGS = found;
  }
  return new Map(PLATFORM_CONFIGS);
}

/** `value` frozen all the way down, so a cached config is never changed by a probe. */
function deepFreeze<T>(value: T): T {
  if (typeof value === "object" && value !== null && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const inner of Object.values(value)) {
      deepFreeze(inner);
    }
  }
  return value;
}

/** Every extension a Worker's bundler would take as script, and how to parse it. */
export const KINDS: Readonly<Record<string, ts.ScriptKind>> = Object.freeze({
  ".ts": ts.ScriptKind.TS, ".mts": ts.ScriptKind.TS, ".cts": ts.ScriptKind.TS,
  ".tsx": ts.ScriptKind.TSX, ".js": ts.ScriptKind.JS, ".mjs": ts.ScriptKind.JS,
  ".cjs": ts.ScriptKind.JS, ".jsx": ts.ScriptKind.JSX,
});

/**
 * A Worker's env, by path from `platform/`: written by `npm run generate` (`types.ts`), it
 * declares every binding the Worker's config makes, and reaches none of them.
 */
export function envFile(worker: string): string {
  return `${worker}/src/env.ts`;
}

/** Every script under each Worker project's `src/`, keyed by path from `platform/`. */
export function workerSources(root: string = PLATFORM): Map<string, string> {
  const found = new Map<string, string>();
  for (const project of readdirSync(root, { withFileTypes: true })) {
    if (!project.isDirectory() || project.name === "node_modules" || project.name.startsWith(".")) {
      continue;
    }
    const src = join(root, project.name, "src");
    let entries: string[];
    try {
      entries = readdirSync(src, { recursive: true, encoding: "utf8" });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (extname(entry) in KINDS) {
        const path = join(src, entry);
        found.set(relative(root, path).split("\\").join("/"), readFileSync(path, "utf8"));
      }
    }
  }
  return found;
}

/**
 * Every script directly in `folder` (a job's own), keyed by path from `platform/`: its
 * tooling's config (`vitest.config.ts`) is none of the job's.
 */
export function scriptsIn(folder: string, root: string = PLATFORM): Map<string, string> {
  return new Map(readdirSync(join(root, folder), { withFileTypes: true })
    .filter((entry) => entry.isFile() && extname(entry.name) in KINDS
                       && !/\.config\.[cm]?[jt]s$/.test(entry.name))
    .map((entry) => [`${folder}/${entry.name}`,
                     readFileSync(join(root, folder, entry.name), "utf8")] as const));
}

/** The sources of one Worker (`<worker>/src/`), from `all`. */
export function sourcesOf(worker: string,
                          all: ReadonlyMap<string, string> = workerSources()): Map<string, string> {
  return new Map([...all].filter(([path]) => path.startsWith(`${worker}/src/`)));
}

/** `source` parsed as its extension says, with parent links. */
export function parse(fileName: string, source: string): ts.SourceFile {
  return ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true,
                             KINDS[extname(fileName)] ?? ts.ScriptKind.TS);
}

/** Each problem as `file:line: what`. */
export function reporter(file: ts.SourceFile, problems: string[]) {
  return (node: ts.Node, what: string) => {
    const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
    problems.push(`${file.fileName}:${line}: ${what}`);
  };
}
