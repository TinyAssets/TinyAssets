/**
 * Append-only migrations from the first deploy (tasks "Append-only migrations from the first
 * deploy"; slice P).
 *
 * Once a migration has run on a live owner object it is never edited: a new shape is a new
 * migration at the end. `migrations.json` is the lock:
 * - `first_deploy`: null until the owner Worker's first deploy, then `{ at, commit }`. Slice
 *   L's deploy kit sets it, as part of the deploy (below). Until it is set, nothing is
 *   deployed and slices may still edit migrations in place.
 * - `deployed`: each deployed migration's SHA-256, in order (version n at index n - 1), over
 *   its statements as the code evaluates them (`digest`), so a change to a constant a
 *   statement spells changes the digest too.
 *
 * This guard fails when a pinned migration's text differs from its pin, when a pinned one is
 * gone, or when anything is pinned without the first-deploy marker. A deploy pins with
 * `PIN_MIGRATIONS=<commit> npx vitest run --project guards guards/migrations.test.ts` in
 * `platform/owner`: the marker is set once, the migrations already pinned are checked
 * first (a changed one refuses the pin), and every migration the code now holds is pinned;
 * the deploy commits the lock with the code it deployed.
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { MIGRATIONS, type Migration } from "../src/schema";

const LOCK = join(dirname(fileURLToPath(import.meta.url)), "..", "migrations.json");

/** The lock, as `migrations.json` holds it. */
export interface Lock {
  readonly first_deploy: { readonly at: string; readonly commit: string } | null;
  readonly deployed: readonly string[];
}

/** One migration's digest: SHA-256 over its statements as JSON, as the code evaluates them. */
export function digest(migration: Migration): string {
  return createHash("sha256").update(JSON.stringify(migration)).digest("hex");
}

/** Each way `migrations` breaks `lock`: a pinned one changed or gone, or a pin unmarked. */
export function lockProblems(migrations: readonly Migration[], lock: Lock): string[] {
  const problems: string[] = [];
  if (lock.first_deploy === null && lock.deployed.length > 0) {
    problems.push("migrations are pinned with no first deploy marked");
  }
  for (const [index, pinned] of lock.deployed.entries()) {
    const migration = migrations[index];
    if (migration === undefined) {
      problems.push(`migration ${index + 1} was deployed and is gone`);
    } else if (digest(migration) !== pinned) {
      problems.push(`migration ${index + 1} was deployed and its text changed: add a new ` +
                    "migration instead");
    }
  }
  return problems;
}

/** The lock after a deploy of `migrations` at `commit`: marked once, every migration pinned. */
export function pinned(migrations: readonly Migration[], lock: Lock, commit: string,
                       at: string): Lock {
  const problems = lockProblems(migrations, lock);
  if (problems.length > 0) {
    throw new Error(`refusing to pin: ${problems.join("; ")}`);
  }
  return { first_deploy: lock.first_deploy ?? { at, commit },
           deployed: migrations.map(digest) };
}

function readLock(): Lock {
  const lock = JSON.parse(readFileSync(LOCK, "utf8")) as Lock;
  if (Object.keys(lock).sort().join() !== "deployed,first_deploy"
      || !Array.isArray(lock.deployed)
      || lock.deployed.some((pin) => typeof pin !== "string" || !/^[0-9a-f]{64}$/.test(pin))) {
    throw new Error("migrations.json is out of shape");
  }
  return lock;
}

describe("append-only migrations", () => {
  it("hold every deployed migration to its pinned text", () => {
    const commit = process.env["PIN_MIGRATIONS"];
    if (commit !== undefined && commit !== "") {
      const next = pinned(MIGRATIONS, readLock(), commit, new Date().toISOString());
      writeFileSync(LOCK, `${JSON.stringify(next, null, 2)}\n`);
    }
    expect(lockProblems(MIGRATIONS, readLock())).toEqual([]);
  });

  const first = { at: "2026-11-04T00:00:00.000Z", commit: "abc123" };
  const shipped: Migration[] = [["CREATE TABLE a (x)"], ["CREATE TABLE b (y)"]];
  const lock: Lock = { first_deploy: first, deployed: shipped.map(digest) };

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
    const at = pinned(shipped, { first_deploy: null, deployed: [] }, "abc123", first.at);
    expect(at).toEqual(lock);
    const later = pinned([...shipped, ["CREATE TABLE c (z)"]], at, "def456", "2026-12-01");
    expect(later.first_deploy).toEqual(first);
    expect(later.deployed).toHaveLength(3);
    expect(() => pinned([["CREATE TABLE a (edited)"], shipped[1]!], at, "x", "y"))
      .toThrow("refusing to pin: migration 1 was deployed and its text changed");
  });

  it("digests a migration as the code evaluates it", () => {
    expect(digest(["A", "B"])).not.toBe(digest(["AB"]));
    expect(digest(MIGRATIONS[0]!)).toMatch(/^[0-9a-f]{64}$/);
  });
});
