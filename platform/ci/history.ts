/**
 * Code and files as they stood at an earlier commit, read beside today's: a test proving
 * today's code keeps an older one's bytes reads that code from git (the platform test jobs
 * fetch full history), never from a copy kept in the tree; a guard holding a file to only
 * growing reads it as the base branch has it (`baseOf`, `fileAt`).
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { REPO } from "./paths.ts";

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

/**
 * The base a growth guard compares against: `GUARD_BASE` when set, else the event's own (a
 * pull request's base, a merge group's base, a push's previous main, from
 * `GITHUB_EVENT_PATH`), else `origin/main`.
 */
export function baseOf(env: NodeJS.ProcessEnv = process.env): string {
  if (env.GUARD_BASE) {
    return env.GUARD_BASE;
  }
  if (env.GITHUB_EVENT_PATH) {
    const event = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, "utf8")) as {
      pull_request?: { base?: { sha?: string } };
      merge_group?: { base_sha?: string };
      before?: string;
    };
    const sha = event.pull_request?.base?.sha ?? event.merge_group?.base_sha ?? event.before;
    if (sha && /^[0-9a-f]{40}$/.test(sha) && !/^0+$/.test(sha)) {
      return sha;
    }
  }
  return "origin/main";
}

function git(...args: string[]): string {
  return execFileSync("git", args, { cwd: REPO, encoding: "utf8",
                                     stdio: ["ignore", "pipe", "pipe"] });
}

/**
 * The text of `path` (repo-relative) on `base`; null only when the base's tree has no such
 * file. Throws when `base` is unknown, and when the file is in its tree but cannot be read:
 * the legs check out without file contents (`filter: blob:none`), so reading it fetches its
 * blob, and a fetch that fails is an error, never "no file". Whether the file exists is read
 * from the tree, which is local. A guard never passes without its base.
 */
export function fileAt(base: string, path: string,
                       run: (...args: string[]) => string = git): string | null {
  try {
    run("rev-parse", "--verify", "--quiet", `${base}^{commit}`);
  } catch {
    throw new Error(`${base} is not in this checkout: fetch it (nothing held to it passes ` +
                    "without it)");
  }
  if (run("ls-tree", "--name-only", base, "--", path).trim() !== path) {
    return null;
  }
  return run("show", `${base}:${path}`);
}
