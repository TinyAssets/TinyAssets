/**
 * The migration locks' kit (`ci/kit/locks.ts`), and each lock only growing, held against the
 * base branch (slice P review M3): every lock a Worker states (`<worker>/guards/locks.ts`).
 *
 * A lock pins every migration a deploy shipped (`deployed`, each one's digest) and when the
 * first deploy was (`first_deploy`). Each Worker's `migrations.test.ts` holds the
 * code to its locks in the same tree, so a change that edits a deployed migration *and* its
 * pin passes it. This holds each lock itself against the base: once the base marks a first
 * deploy, that marker never changes, and the base's pins stay the head's first pins, in
 * order. A new migration is appended; nothing is edited or taken away. Each object's start
 * check (`migrate`) is the backstop in production; this is the one that stops it in review.
 *
 * The base is `MIGRATION_LOCK_BASE` when set, else the event's own (a pull request's base,
 * a merge group's base, a push's previous main, from `GITHUB_EVENT_PATH`), else
 * `origin/main`. A base this checkout does not have fails the guard: it never passes
 * without one.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { type Migration, migrationDigest as digest } from "../../contract/src/migrate";
import { deployAnswer, deployProblems, digests, type Lock, lockOf, lockProblems, locksOf, pin,
         readLock } from "../kit/locks";
import { PLATFORM, REPO } from "../paths";
import { workerFolders } from "../wrangler";

/** The Workers that state migration locks. */
const MIGRATED = workerFolders()
  .filter((worker) => existsSync(join(PLATFORM, worker, "guards", "locks.ts")));
/** Every migration lock, from the repository's root. */
const LOCKS = (await Promise.all(MIGRATED.map(locksOf)))
  .flatMap((locks) => Object.keys(locks).map((path) => `platform/${path}`));

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
 * The lock at `path` on `base`; null only when the base's tree has no such file. Throws when
 * `base` is unknown, and when the lock is in its tree but cannot be read: the legs check out
 * without file contents (`filter: blob:none`), so reading it fetches its blob, and a fetch
 * that fails is an error, never "no lock". Whether the file exists is read from the tree,
 * which is local.
 */
export function baseLock(base: string, path: string,
                         run: (...args: string[]) => string = git): Lock | null {
  try {
    run("rev-parse", "--verify", "--quiet", `${base}^{commit}`);
  } catch {
    throw new Error(`${base} is not in this checkout: fetch it (the guard never passes ` +
                    "without its base)");
  }
  if (run("ls-tree", "--name-only", base, "--", path).trim() !== path) {
    return null;
  }
  return JSON.parse(run("show", `${base}:${path}`)) as Lock;
}

describe("the migration locks' kit", () => {
  const first = { at: "2026-11-04T00:00:00.000Z", commit: "abc123" };
  const shipped: Migration[] = [["CREATE TABLE a (x)"], ["CREATE TABLE b (y)"]];
  const lock: Lock = { first_deploy: first, deployed: shipped.map(digest) };

  it("finds the owner's and the sandbox Worker's locks", () => {
    expect(MIGRATED).toEqual(["owner", "sandbox"]);
    expect(LOCKS).toEqual(["platform/owner/migrations.json",
                           "platform/sandbox/migrations/SandboxControl.json",
                           "platform/sandbox/migrations/SandboxSlots.json"]);
  });

  it("fails a deployed migration edited in place, or taken away", () => {
    expect(lockProblems(shipped, lock)).toEqual([]);
    expect(lockProblems([["CREATE TABLE a (x, z)"], shipped[1]!], lock)).toEqual([
      "migration 1 was deployed and its text changed: add a new migration instead"]);
    expect(lockProblems([shipped[0]!], lock)).toEqual(["migration 2 was deployed and is gone"]);
    // A new migration at the end is the way a deployed shape changes.
    expect(lockProblems([...shipped, ["ALTER TABLE a ADD COLUMN z"]], lock)).toEqual([]);
  });

  it("lets migrations change in place until the first deploy marks the lock", () => {
    const none: Lock = { first_deploy: null, deployed: [] };
    expect(lockProblems([["CREATE TABLE a (changed)"]], none)).toEqual([]);
    expect(lockProblems(shipped, { first_deploy: null, deployed: lock.deployed }))
      .toEqual(["migrations are pinned with no first deploy marked"]);
  });

  it("pins at a deploy: marked once, each new migration added, a changed one refused", () => {
    const at = pin(shipped, { first_deploy: null, deployed: [] }, "abc123", first.at);
    expect(at).toEqual(lock);
    const later = pin([...shipped, ["CREATE TABLE c (z)"]], at, "def456", "2026-12-01");
    expect(later.first_deploy).toEqual(first);
    expect(later.deployed).toHaveLength(3);
    expect(() => pin([["CREATE TABLE a (edited)"], shipped[1]!], at, "x", "y"))
      .toThrow("refusing to pin: migration 1 was deployed and its text changed");
  });

  it("digests a migration as the code evaluates it", () => {
    expect(digest(["A", "B"])).not.toBe(digest(["AB"]));
    expect(digests({ "w/a.json": shipped })).toEqual({ "w/a.json": lock.deployed });
  });

  it("reads a lock held to its shape", () => {
    const text = JSON.stringify(lock);
    expect(lockOf(text, "w/a.json")).toEqual(lock);
    for (const bad of [null, [], { deployed: [] }, { ...lock, extra: 1 },
                       { ...lock, first_deploy: { at: "x" } }, { ...lock, deployed: ["a"] },
                       { ...lock, deployed: "a".repeat(64) }]) {
      expect(() => lockOf(JSON.stringify(bad), "w/a.json"), JSON.stringify(bad))
        .toThrow("w/a.json is out of shape");
    }
  });

  it("lets a Worker deploy only when each lock is marked and pins exactly its code", () => {
    const held = (each: Lock) => ({ "w/a.json": each });
    const locks = { "w/a.json": shipped };
    expect(deployProblems("w", locks, held(lock))).toEqual([]);
    expect(deployProblems("w", locks, held({ first_deploy: null, deployed: [] })))
      .toEqual([expect.stringMatching(/^the migration lock w\/a\.json marks no first deploy: .*node ci\/kit\/locks\.ts pin w <commit>/)]);
    for (const deployed of [lock.deployed.slice(0, 1), [...lock.deployed, "c".repeat(64)],
                            ["e".repeat(64), lock.deployed[1]!]]) {
      expect(deployProblems("w", locks, held({ first_deploy: first, deployed })))
        .toEqual([expect.stringMatching(/^the migration lock w\/a\.json pins \d migrations and the code ships 2, or one differs/)]);
    }
    expect(deployProblems("w", {}, held(lock)))
      .toEqual(["w states no migration lock: each Worker the deploy holds to locks has one"]);
    // The command line's answer: the digests when pinned, else a refusal, an unpinned
    // migration at the end included (main's locks are pinned, so only this holds that).
    expect(JSON.parse(deployAnswer("w", locks, held(lock)))).toEqual({ "w/a.json": lock.deployed });
    expect(() => deployAnswer("w", { "w/a.json": [...shipped, ["CREATE TABLE c (z)"]] },
                              held(lock)))
      .toThrow(/^the migration lock w\/a\.json pins 2 migrations and the code ships 3/);
  });

  it("answers from its command line as the kit does: each Worker's digests when its locks " +
     "pin them, else each refusal; and refuses a Worker with no locks", async () => {
    const run = (...args: string[]) => spawnSync(process.execPath,
      [join(PLATFORM, "ci", "kit", "locks.ts"), ...args], { cwd: PLATFORM, encoding: "utf8" });
    for (const worker of MIGRATED) {
      const locks = await locksOf(worker);
      const refused = deployProblems(worker, locks, Object.fromEntries(Object.keys(locks)
        .map((path) => [path, readLock(path)])));
      const printed = run("digests", worker);
      expect([printed.status, printed.stderr.trim()], worker)
        .toEqual(refused.length === 0 ? [0, ""] : [1, refused.join("\n")]);
      if (refused.length === 0) {
        expect(JSON.parse(printed.stdout)).toEqual(digests(locks));
      }
    }
    expect(run("digests", "doors").status).toBe(1);
    expect(run("pin", "owner").stderr).toMatch(/^usage: /);
  });
});

describe("the migration lock against the base branch", () => {
  it.each(LOCKS)("only grows: %s", (path) => {
    const base = baseLock(lockBase(), path) ?? { first_deploy: null, deployed: [] };
    const head = JSON.parse(readFileSync(join(REPO, path), "utf8")) as Lock;
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
        return `${LOCKS[0]}\n`;
      }
      if (args[0] === "show") {
        throw new Error("fatal: could not fetch the blob");
      }
      return "";
    };
    expect(() => baseLock("abc", LOCKS[0]!, tree)).toThrow(/could not fetch/);
    expect(baseLock("abc", LOCKS[0]!, (...args) => (args[0] === "ls-tree" ? "" : "")))
      .toBeNull();
  });

  it("reads its base from the event, and refuses one it does not have", () => {
    expect(lockBase({ MIGRATION_LOCK_BASE: "abc" })).toBe("abc");
    expect(lockBase({})).toBe("origin/main");
    expect(() => baseLock("0".repeat(40), LOCKS[0]!)).toThrow(/not in this checkout/);
  });
});
