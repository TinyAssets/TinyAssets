/**
 * The migration locks' kit (`ci/kit/locks.ts`), and each lock only growing, held against the
 * base branch: every lock a Worker states (`<worker>/guards/locks.ts`).
 *
 * A lock pins every migration (`deployed`, each one's digest) once its first deploy is marked
 * (`first_deploy`). Each Worker's `migrations.test.ts` holds the code to its locks in the
 * same tree, so a change that edits a pinned migration *and* its pin passes it. This holds each lock itself against the base: once the base marks a first
 * deploy, that marker never changes, and the base's pins stay the head's first pins, in
 * order. A new migration is appended; nothing is edited or taken away. Each object's start
 * check (`migrate`) is the backstop in production; this is the one that stops it in review.
 *
 * The base is `ci/history.ts`'s (`baseOf`), and a base this checkout does not have fails the
 * guard: it never passes without one.
 */
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { type Migration, migrationDigest as digest } from "../../contract/src/migrate";
import { deployAnswer, deployProblems, digests, holdLocks, type Lock, lockOf, lockProblems,
         locksOf, migrated, pin, readLock } from "../kit/locks";
import { baseOf, fileAt } from "../history";
import { PLATFORM, REPO } from "../paths";

const MIGRATED = migrated();
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

/** The lock at `path` on `base` (`fileAt`); null only when the base's tree has none. */
function baseLock(base: string, path: string,
                  run?: (...args: string[]) => string): Lock | null {
  const text = fileAt(base, path, run);
  return text === null ? null : JSON.parse(text) as Lock;
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

  it("fails a pinned migration edited in place or taken away, and one added unpinned", () => {
    expect(lockProblems(shipped, lock)).toEqual([]);
    expect(lockProblems([["CREATE TABLE a (x, z)"], shipped[1]!], lock)).toEqual([
      "migration 1 is pinned and its text changed: add a new migration instead (one only " +
      "this branch pins is pinned again from main's copy of the lock)"]);
    expect(lockProblems([shipped[0]!], lock)).toEqual(["migration 2 is pinned and gone"]);
    // A new migration at the end is the way a deployed shape changes, pinned with it.
    const added = [...shipped, ["ALTER TABLE a ADD COLUMN z"]];
    expect(lockProblems(added, lock)).toEqual(["the code holds 3 migrations and 2 are " +
      "pinned: pin each in the change that adds it (node ci/kit/locks.ts pin <worker> HEAD, " +
      "in platform/)"]);
    expect(lockProblems(added, pin(added, lock, "x", "y"))).toEqual([]);
  });

  it("fails the change that adds a migration to any Worker's lock unpinned", async () => {
    for (const worker of MIGRATED) {
      const locks = await locksOf(worker);
      for (const [path, migrations] of Object.entries(locks)) {
        expect(holdLocks({ ...locks, [path]: [...migrations, ["CREATE TABLE added (x)"]] }))
          .toEqual([`${path}: the code holds ${migrations.length + 1} migrations and ` +
                    `${migrations.length} are pinned: pin each in the change that adds it ` +
                    "(node ci/kit/locks.ts pin <worker> HEAD, in platform/)"]);
      }
    }
  });

  it("lets migrations change in place until the first deploy marks the lock", () => {
    const none: Lock = { first_deploy: null, deployed: [] };
    expect(lockProblems([["CREATE TABLE a (changed)"]], none)).toEqual([]);
    expect(lockProblems(shipped, { first_deploy: null, deployed: lock.deployed }))
      .toEqual(["migrations are pinned with no first deploy marked"]);
  });

  it("pins: marked once, each new migration added, a changed one refused", () => {
    const at = pin(shipped, { first_deploy: null, deployed: [] }, "abc123", first.at);
    expect(at).toEqual(lock);
    const later = pin([...shipped, ["CREATE TABLE c (z)"]], at, "def456", "2026-12-01");
    expect(later.first_deploy).toEqual(first);
    expect(later.deployed).toHaveLength(3);
    expect(() => pin([["CREATE TABLE a (edited)"], shipped[1]!], at, "x", "y"))
      .toThrow("refusing to pin: migration 1 is pinned and its text changed");
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
    for (const [deployed, problem] of [
      [lock.deployed.slice(0, 1), "the code holds 2 migrations and 1 are pinned"],
      [[...lock.deployed, "c".repeat(64)], "migration 3 is pinned and gone"],
      [["e".repeat(64), lock.deployed[1]!], "migration 1 is pinned and its text changed"],
    ] as const) {
      expect(deployProblems("w", locks, held({ first_deploy: first, deployed })))
        .toEqual([expect.stringContaining(`the migration lock w/a.json: ${problem}`)]);
    }
    expect(deployProblems("w", {}, held(lock)))
      .toEqual(["w states no migration lock: each Worker the deploy holds to locks has one"]);
    // The command line's answer: the digests when pinned, else a refusal.
    expect(JSON.parse(deployAnswer("w", locks, held(lock)))).toEqual({ "w/a.json": lock.deployed });
    expect(() => deployAnswer("w", { "w/a.json": [...shipped, ["CREATE TABLE c (z)"]] },
                              held(lock)))
      .toThrow(/^the migration lock w\/a\.json: the code holds 3 migrations and 2 are pinned/);
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
    // A first-deploy marker records the commit git names, never the argument's spelling.
    expect(run("pin", "owner", "not-a-commit").status).toBe(1);
  });
});

describe("the migration lock against the base branch", () => {
  it.each(LOCKS)("only grows: %s", (path) => {
    const base = baseLock(baseOf(), path) ?? { first_deploy: null, deployed: [] };
    const head = JSON.parse(readFileSync(join(REPO, path), "utf8")) as Lock;
    expect(lockGrowthProblems(base, head)).toEqual([]);
  });

  it("refuses an edited pin, a dropped pin and a moved marker", () => {
    const first = { at: "2026-11-04T00:00:00.000Z", commit: "abc123" };
    const base: Lock = { first_deploy: first, deployed: ["a".repeat(64), "b".repeat(64)] };
    expect(lockGrowthProblems(base, { first_deploy: first,
      deployed: ["a".repeat(64), "b".repeat(64), "c".repeat(64)] })).toEqual([]);
    // A deployed migration and its pin edited together, which the head's own guard passes.
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
    expect(baseOf({ GUARD_BASE: "abc" })).toBe("abc");
    expect(baseOf({})).toBe("origin/main");
    expect(() => baseLock("0".repeat(40), LOCKS[0]!)).toThrow(/not in this checkout/);
  });
});
