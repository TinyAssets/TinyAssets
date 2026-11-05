/**
 * The owner schema's migration lock only grows, held against the base branch (slice P
 * review M3; ported from the Python suite, which the cutover deletes).
 *
 * `migrations.json` pins every migration a deploy shipped (`deployed`, each one's digest) and
 * when the first deploy was (`first_deploy`). `migrations.test.ts` holds the code to the lock
 * in the same tree, so a change that edits a deployed migration *and* its pin passes it.
 * This holds the lock itself against the base: once the base marks a first deploy, that
 * marker never changes, and the base's pins stay the head's first pins, in order. A new
 * migration is appended; nothing is edited or taken away. The owner object's start check
 * (`migrate`) is the backstop in production; this is the one that stops it in review.
 *
 * The base is `MIGRATION_LOCK_BASE` when set, else the event's own (a pull request's base,
 * a merge group's base, a push's previous main, from `GITHUB_EVENT_PATH`), else
 * `origin/main`. A base this checkout does not have fails the guard: it never passes
 * without one.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { Lock } from "./migrations.test";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const LOCK = "platform/owner/migrations.json";

/** Each way `head` is not `base` with pins appended. */
export function lockGrowthProblems(base: Lock, head: Lock): string[] {
  const found: string[] = [];
  if (base.first_deploy !== null
      && JSON.stringify(head.first_deploy) !== JSON.stringify(base.first_deploy)) {
    found.push("the first-deploy marker changed");
  }
  const pinned = base.deployed;
  if (JSON.stringify(head.deployed.slice(0, pinned.length)) !== JSON.stringify(pinned)) {
    found.push("a deployed migration's pin was edited or taken away: add a migration");
  }
  return found;
}

/** The base this run compares against (see the header). */
export function lockBase(env: NodeJS.ProcessEnv = process.env): string {
  if (env.MIGRATION_LOCK_BASE) {
    return env.MIGRATION_LOCK_BASE;
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
 * The lock on `base`; null only when the base's tree has no lock file. Throws when `base` is
 * unknown, and when the lock is in its tree but cannot be read: the legs check out without
 * file contents (`filter: blob:none`), so reading it fetches its blob, and a fetch that fails
 * is an error, never "no lock". Whether the file exists is read from the tree, which is local.
 */
export function baseLock(base: string, run: (...args: string[]) => string = git): Lock | null {
  try {
    run("rev-parse", "--verify", "--quiet", `${base}^{commit}`);
  } catch {
    throw new Error(`${base} is not in this checkout: fetch it (the guard never passes ` +
                    "without its base)");
  }
  if (run("ls-tree", "--name-only", base, "--", LOCK).trim() !== LOCK) {
    return null;
  }
  return JSON.parse(run("show", `${base}:${LOCK}`)) as Lock;
}

describe("the migration lock against the base branch", () => {
  it("only grows", () => {
    const base = baseLock(lockBase()) ?? { first_deploy: null, deployed: [] };
    const head = JSON.parse(readFileSync(join(REPO, LOCK), "utf8")) as Lock;
    expect(lockGrowthProblems(base, head)).toEqual([]);
  });

  it("refuses an edited pin, a dropped pin and a moved marker", () => {
    const first = { at: "2026-11-04T00:00:00.000Z", commit: "abc123" };
    const base: Lock = { first_deploy: first, deployed: ["a".repeat(64), "b".repeat(64)] };
    expect(lockGrowthProblems(base, { first_deploy: first,
      deployed: ["a".repeat(64), "b".repeat(64), "c".repeat(64)] })).toEqual([]);
    // The review's attack: a deployed migration and its pin edited together.
    const edited = "a deployed migration's pin was edited or taken away: add a migration";
    expect(lockGrowthProblems(base, { first_deploy: first,
      deployed: ["e".repeat(64), "b".repeat(64)] })).toEqual([edited]);
    expect(lockGrowthProblems(base, { first_deploy: first, deployed: ["a".repeat(64)] }))
      .toEqual([edited]);
    expect(lockGrowthProblems(base, { first_deploy: null, deployed: [] }))
      .toEqual(["the first-deploy marker changed", edited]);
    // Before the first deploy, migrations may still change in place.
    expect(lockGrowthProblems({ first_deploy: null, deployed: [] },
                              { first_deploy: first, deployed: ["e".repeat(64)] })).toEqual([]);
  });

  it("fails when the base's lock is in its tree but cannot be read", () => {
    const tree = (...args: string[]): string => {
      if (args[0] === "ls-tree") {
        return `${LOCK}\n`;
      }
      if (args[0] === "show") {
        throw new Error("fatal: could not fetch the blob");
      }
      return "";
    };
    expect(() => baseLock("abc", tree)).toThrow(/could not fetch/);
    expect(baseLock("abc", (...args) => (args[0] === "ls-tree" ? "" : ""))).toBeNull();
  });

  it("reads its base from the event, and refuses one it does not have", () => {
    expect(lockBase({ MIGRATION_LOCK_BASE: "abc" })).toBe("abc");
    expect(lockBase({})).toBe("origin/main");
    expect(() => baseLock("0".repeat(40))).toThrow(/not in this checkout/);
  });
});
