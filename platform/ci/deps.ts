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
 * holds its workspace's package; an optional package may be absent (another platform's
 * binary), never another version. npm's hidden lockfile (`node_modules/.package-lock.json`)
 * says what npm last wrote, not what is there now, and `npm ls` checks the ranges a package
 * declares, not the versions locked (`docs/decisions/untangle-2026-11/deps.md`).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { PLATFORM } from "./paths.ts";

interface Locked {
  readonly name?: string;
  readonly version?: string;
  readonly resolved?: string;
  readonly link?: boolean;
  readonly optional?: boolean;
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

/** The first locked package the install at `root` does not hold as locked, or null. */
export function mismatch(root: string): string | null {
  const { packages } = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8")) as
    { packages: Record<string, Locked> };
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
    } else if (found.version !== locked.version) {
      return `${path} is ${found.version}, locked ${locked.version}`;
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
