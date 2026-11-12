/**
 * The platform's generated files, as one command writes them all: `npm run generate`
 * (`package.json`), which lists every generator. A copy of the tree is regenerated and
 * compared with itself, so nothing outside the copy is written and nothing names the files
 * a generator writes: whatever it changes, adds or removes that git does not ignore is found.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync,
         symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, relative } from "node:path";

import { PLATFORM, REPO } from "../paths.ts";

const MODULES = "node_modules";

/**
 * A copy of the platform's tree at `<temp>/platform`, its packages linked rather than copied
 * (the platform's own and each workspace's), so its generators run there as they run here.
 */
export function platformCopy(): string {
  const copy = join(mkdtempSync(join(tmpdir(), "ta-generated-")), "platform");
  cpSync(PLATFORM, copy, { recursive: true, filter: (path) => basename(path) !== MODULES });
  for (const dir of ["", ...readdirSync(PLATFORM)]) {
    if (existsSync(join(PLATFORM, dir, MODULES))) {
      symlinkSync(join(PLATFORM, dir, MODULES), join(copy, dir, MODULES), "dir");
    }
  }
  return copy;
}

/** Removes a copy `platformCopy` made. */
export function removeCopy(copy: string): void {
  rmSync(join(copy, ".."), { recursive: true, force: true });
}

/** Every file under `root` but the packages, by its path from `root`, as its SHA-256. */
function digests(root: string, dir = root, out = new Map<string, string>()): Map<string, string> {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (name === MODULES) {
      continue;
    }
    if (statSync(path).isDirectory()) {
      digests(root, path, out);
    } else {
      out.set(relative(root, path), createHash("sha256").update(readFileSync(path)).digest("hex"));
    }
  }
  return out;
}

/**
 * Of `paths` (from `platform/`), the ones the repository's ignore rules leave in. A build
 * output a generator's tool writes on the way (the sandbox's `dist/`, which `wrangler types`
 * builds) is never committed, so it is no change, here or on a fresh checkout.
 */
function unignored(paths: readonly string[]): string[] {
  const asked = spawnSync("git", ["check-ignore", "--stdin"], {
    cwd: REPO, encoding: "utf8", input: paths.map((path) => `platform/${path}\n`).join("") });
  if (asked.status !== 0 && asked.status !== 1) {
    throw new Error(`git check-ignore failed: ${asked.stderr || asked.error}`);
  }
  const ignored = new Set(asked.stdout.split("\n"));
  return paths.filter((path) => !ignored.has(`platform/${path}`));
}

/**
 * Runs `act` on the platform tree at `platform`, and returns every path under it that `act`
 * changed, added or removed that git does not ignore, sorted.
 */
export function changedBy(platform: string, act: () => void): string[] {
  const before = digests(platform);
  act();
  const after = digests(platform);
  return unignored([...new Set([...before.keys(), ...after.keys()])]
    .filter((path) => before.get(path) !== after.get(path)).sort());
}

/** What `npm run generate` changes in the platform tree at `platform`: none when current. */
export function regenerated(platform: string): string[] {
  return changedBy(platform, () => {
    execFileSync("npm", ["run", "--silent", "generate"], { cwd: platform, stdio: "pipe" });
  });
}
