/**
 * The migration locks: append-only migrations from each Worker's first deploy (tasks
 * "Append-only migrations from the first deploy"; slice P). One lock a Durable Object class
 * that keeps tables: the owner's (`owner/migrations.json`) and the sandbox Worker's two
 * (`sandbox/migrations/<class>.json`), each Worker's migrations guard holding its own.
 *
 * Once a migration has run on a live object it is never edited: a new shape is a new
 * migration at the end. A lock holds:
 * - `first_deploy`: null until its Worker's first deploy, then `{ at, commit }`. Until it is
 *   set, nothing is deployed and slices may still edit migrations in place.
 * - `deployed`: each deployed migration's SHA-256, in order (version n at index n - 1), over
 *   its statements as the code evaluates them (`migrationDigest`). The owner's are literal
 *   SQL text (`schema.ts`), so its digest is of the text as written.
 *
 * A guard fails when a pinned migration's text differs from its pin, when a pinned one is
 * gone, or when anything is pinned without the first-deploy marker. A deploy pins with
 * `PIN_MIGRATIONS=<commit> npx vitest run --project guards guards/migrations.test.ts` in
 * the Worker's folder: the marker is set once, the migrations already pinned are checked
 * first (a changed one refuses the pin), and every migration the code now holds is pinned;
 * the deploy commits the lock with the code it deployed. The deploy kit refuses to deploy
 * unless every lock is marked and pins every migration the code ships, in order
 * (`deploy/deploy.ts` `lockRefusal`, its digests from the guards' `MIGRATION_DIGESTS`).
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { type Migration, migrationDigest } from "../../contract/src/migrate";
import { PLATFORM } from "../../ci/paths";

/** A lock, as its file holds it. */
export interface Lock {
  readonly first_deploy: { readonly at: string; readonly commit: string } | null;
  readonly deployed: readonly string[];
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
    } else if (migrationDigest(migration) !== pinned) {
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
           deployed: migrations.map(migrationDigest) };
}

/** The lock at `path` (from `platform/`), held to its shape. */
export function readLock(path: string): Lock {
  const lock = JSON.parse(readFileSync(join(PLATFORM, path), "utf8")) as Lock;
  if (Object.keys(lock).sort().join() !== "deployed,first_deploy"
      || !Array.isArray(lock.deployed)
      || lock.deployed.some((pin) => typeof pin !== "string" || !/^[0-9a-f]{64}$/.test(pin))) {
    throw new Error(`${path} is out of shape`);
  }
  return lock;
}

/**
 * One Worker's guard over its locks (`locks`: each lock's path from `platform/`, to the
 * migrations the code holds for it): pinned first when `PIN_MIGRATIONS` names a commit, then
 * each problem, by its lock. With `MIGRATION_DIGESTS` set, the code's digests are written
 * there for the deploy kit's check (`deploy/deploy.ts` `lockRefusal`), by lock.
 */
export function holdLocks(locks: Readonly<Record<string, readonly Migration[]>>,
                          env: NodeJS.ProcessEnv = process.env): string[] {
  const commit = env["PIN_MIGRATIONS"];
  if (commit !== undefined && commit !== "") {
    const at = new Date().toISOString();
    for (const [path, migrations] of Object.entries(locks)) {
      writeFileSync(join(PLATFORM, path),
                    `${JSON.stringify(pinned(migrations, readLock(path), commit, at), null, 2)}\n`);
    }
  }
  const problems = Object.entries(locks).flatMap(([path, migrations]) =>
    lockProblems(migrations, readLock(path)).map((problem) => `${path}: ${problem}`));
  const out = env["MIGRATION_DIGESTS"];
  if (out !== undefined && out !== "") {
    writeFileSync(out, JSON.stringify(Object.fromEntries(Object.entries(locks)
      .map(([path, migrations]) => [path, migrations.map(migrationDigest)]))));
  }
  return problems;
}
