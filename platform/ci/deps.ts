/**
 * The installed dependencies are the lockfile's, or nothing runs. CI runs `npm ci` before
 * every leg, so there it always holds; on a host with a stale install, or a `node_modules`
 * linked from another checkout whose lockfile has moved on, a test run would pass or fail
 * against libraries CI does not use. Every vitest run makes its projects by `project()`
 * (`project.ts`), which checks first; the runs in plain Node that load packages (the owner's
 * memory leg, the doors' app test) check at their top.
 *
 * What is checked is what Node loads: each `packages` entry of `platform/package-lock.json`
 * against the `package.json` at its path. A package is its locked version; a workspace link
 * holds its workspace's package. An optional package can be absent when this machine
 * excludes it or all its locked dependency paths. Installed versions must still match.
 * Every package in a `node_modules` is locked: a stale one left there is loaded, or shadows
 * the hoisted one its parent would load in CI. npm's hidden lockfile
 * (`node_modules/.package-lock.json`) says what npm last wrote, not what is there now, and
 * `npm ls` checks the ranges a package declares, not the versions locked
 * (`docs/decisions/untangle-2026-11/deps.md`).
 */
import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, relative, resolve, sep } from "node:path";

import { PLATFORM } from "./paths.ts";

interface Locked {
  readonly name?: string;
  readonly version?: string;
  readonly resolved?: string;
  readonly link?: boolean;
  readonly optional?: boolean;
  readonly dependencies?: Record<string, string>;
  readonly optionalDependencies?: Record<string, string>;
  readonly peerDependencies?: Record<string, string>;
  readonly devDependencies?: Record<string, string>;
  readonly os?: readonly string[];
  readonly cpu?: readonly string[];
}

/** Whether npm installs on `mine` a package locked to `list` (`!x` excludes x). */
function fits(list: readonly string[] | undefined, mine: string): boolean {
  const named = list?.filter((entry) => !entry.startsWith("!")) ?? [];
  return !list?.includes(`!${mine}`) && (named.length === 0 || named.includes(mine));
}

/** The names in `dir` (a `node_modules`), scoped ones as `@scope/name`; none when it is absent. */
function names(dir: string): string[] {
  try {
    return readdirSync(dir).filter((name) => !name.startsWith("."))
      .flatMap((name) => name.startsWith("@") ? readdirSync(join(dir, name)).map((n) => `${name}/${n}`)
                                              : [name]);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return [];
    }
    throw error;
  }
}

/** The `package.json` at `folder`, or null when nothing is installed there. */
function manifest(folder: string): Locked | null {
  try {
    return JSON.parse(readFileSync(join(folder, "package.json"), "utf8")) as Locked;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

/** npm can omit an otherwise portable package when all its parents belong to absent platform
 * variants. Resolve each edge to its locked install path, including nested workspace versions. */
function excludedOptionals(root: string, packages: Record<string, Locked>, missing: Set<string>): Set<string> {
  const excluded = new Set([...missing].filter((path) => {
    const { os, cpu } = packages[path]!;
    return !fits(os, process.platform) || !fits(cpu, process.arch);
  }));
  if (excluded.size === missing.size || excluded.size === 0) return excluded;

  const missingNames = new Set([...missing].map((path) => path.split("node_modules/").at(-1)!));
  const parents = new Map<string, Set<string>>();
  for (const [parent, locked] of Object.entries(packages)) {
    const dependencies = { ...locked.dependencies, ...locked.optionalDependencies, ...locked.peerDependencies,
      ...(!parent.includes("node_modules/") && locked.devDependencies) };
    const names = Object.keys(dependencies).filter((name) => missingNames.has(name));
    if (!names.length) continue;
    const search = createRequire(resolve(root, parent, "package.json")).resolve.paths("dependency") ?? [];
    for (const name of names) {
      for (const dir of search) {
        const path = relative(root, join(dir, name)).split(sep).join("/");
        if (!Object.hasOwn(packages, path)) continue;
        if (missing.has(path)) {
          if (!parents.has(path)) parents.set(path, new Set());
          parents.get(path)!.add(parent);
        }
        break;
      }
    }
  }
  // First find everything reachable from an absent variant, including optional cycles.
  // Then withdraw that proof wherever another parent needs the same install.
  const reachable = new Set(excluded);
  let previousSize;
  do {
    previousSize = reachable.size;
    for (const [path, incoming] of parents) {
      if ([...incoming].some((parent) => reachable.has(parent))) reachable.add(path);
    }
  } while (reachable.size !== previousSize);
  do {
    previousSize = reachable.size;
    for (const [path, incoming] of parents) {
      if (!excluded.has(path) && [...incoming].some((parent) => !reachable.has(parent))) {
        reachable.delete(path);
      }
    }
  } while (reachable.size !== previousSize);
  return reachable;
}

/** The first locked package the install at `root` does not hold as locked, or null. */
export function mismatch(root: string): string | null {
  const { packages } = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8")) as
    { packages: Record<string, Locked> };
  const missing = new Set<string>();
  for (const [path, locked] of Object.entries(packages)) {
    // The root's and each workspace's own entries are the checkout's sources, not installs.
    if (!path.includes("node_modules/")) {
      continue;
    }
    const found = manifest(join(root, path));
    if (locked.link) {
      const workspace = packages[locked.resolved ?? ""]?.name;
      if (found?.name !== workspace) {
        return `${path} is not linked to the workspace ${locked.resolved}`;
      }
    } else if (found === null) {
      if (!locked.optional) {
        return `${path} is missing (locked ${locked.version})`;
      }
      missing.add(path);
    } else if (found.version !== locked.version) {
      return `${path} is ${found.version}, locked ${locked.version}`;
    }
  }
  const excluded = excludedOptionals(root, packages, missing);
  for (const path of missing) {
    if (!excluded.has(path)) return `${path} is missing (locked ${packages[path]!.version})`;
  }
  // The root, each workspace and each installed package may hold a `node_modules`.
  for (const [path, locked] of Object.entries(packages)) {
    const dir = path === "" ? "node_modules" : `${path}/node_modules`;
    const extra = locked.link ? undefined
      : names(join(root, dir)).find((name) => packages[`${dir}/${name}`] === undefined);
    if (extra !== undefined) {
      return `${dir}/${extra} is installed but not locked`;
    }
  }
  return null;
}

/** Throws, naming the first mismatched package, unless the platform's install is locked. */
export function installed(): void {
  const first = mismatch(PLATFORM);
  if (first !== null) {
    throw new Error(`The installed dependencies are not platform/package-lock.json's: ${first}; `
                    + "run `npm ci` in platform/.");
  }
}
