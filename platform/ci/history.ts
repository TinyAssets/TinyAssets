/**
 * Code as it stood at an earlier commit, run beside today's: a test proving today's code
 * keeps an older one's bytes reads that code from git (the platform test jobs fetch full
 * history), never from a copy kept in the tree.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { REPO } from "./paths";

/** `folders` (repo-relative, whole, so each file's imports resolve as they did) at `commit`. */
export interface Tree {
  /** The module at `path` (repo-relative) as it was. */
  readonly load: <T>(path: string) => Promise<T>;
  readonly remove: () => void;
}

export function treeAt(commit: string, folders: readonly string[]): Tree {
  const root = mkdtempSync(join(tmpdir(), "history-"));
  try {
    const archive = execFileSync("git", ["-C", REPO, "archive", commit, ...folders],
                                 { maxBuffer: 64 * 1024 * 1024 });
    execFileSync("tar", ["-x", "-C", root], { input: archive });
  } catch (error) {
    rmSync(root, { recursive: true, force: true });
    throw error;
  }
  return Object.freeze({
    load: <T>(path: string) => import(join(root, path)) as Promise<T>,
    remove: () => rmSync(root, { recursive: true, force: true }),
  });
}
