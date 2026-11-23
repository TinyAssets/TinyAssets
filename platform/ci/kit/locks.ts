/**
 * The migration locks: append-only migrations from each Worker's first deploy. One lock a
 * Durable Object class that keeps tables, each Worker's held by its migrations guard: a Worker
 * states its locks, each lock's path from `platform/` to the migrations the code holds for it,
 * as `LOCKS` in its `guards/locks.ts`.
 *
 * Any deploy from `main` may run any migration on it, so once a lock marks its first deploy,
 * the change that adds a migration pins it, and from its merge on it is never edited: a new
 * shape is a new migration at the end. A lock holds:
 * - `first_deploy`: null until its Worker's first deploy, then `{ at, commit }`. Until it is
 *   set, nothing is deployed, nothing need be pinned, and migrations may still be edited in
 *   place.
 * - `deployed`: each pinned migration's SHA-256, in order (version n at index n - 1), over
 *   its statements as the code evaluates them (`migrationDigest`).
 *
 * The migrations guard (`holdLocks`) fails when a pinned migration's text differs from its
 * pin, when a pinned one is gone, when anything is pinned without the first-deploy marker,
 * and, once it is marked, when a migration is not pinned: the change that adds one fails
 * until it pins it, so `main` is always pinned. Each lock only growing against `main` is
 * `ci/guards/locks.test.ts`'s. The command line:
 * - `node ci/kit/locks.ts digests <worker>` prints the code's digests by lock, as JSON, when
 *   every lock of the Worker marks its first deploy and pins exactly them, in order; else it
 *   fails, naming each lock that does not. The deploy kit refuses to deploy on its failure.
 * - `node ci/kit/locks.ts pin <worker> <commit>` marks each lock's first deploy once, at
 *   `<commit>` as git resolves it to its full sha (`HEAD` included), and pins every migration
 *   the code holds. The pins already there are checked first: a changed one refuses the pin.
 *   A lock's first deploy lands on `main` by pull request before that deploy
 *   (`deploy/checklist.md`).
 *
 * Node runs this file as it is (type stripping), so its own imports name their `.ts` files.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { keyed } from "../../contract/src/checks.ts";
import { HEX64 } from "../../contract/src/ids.ts";
import { type Migration, migrationDigest } from "../../contract/src/migrate.ts";
import { PLATFORM } from "../paths.ts";
import { workerFolders } from "../wrangler.ts";

/** A lock, as its file holds it. */
export interface Lock {
  readonly first_deploy: { readonly at: string; readonly commit: string } | null;
  readonly deployed: readonly string[];
}

/** A Worker's locks: each lock's path from `platform/`, to the migrations the code holds. */
export type Locks = Readonly<Record<string, readonly Migration[]>>;

/** How a Worker is pinned: this file's own command line, from `platform/`. */
const PIN = "node ci/kit/locks.ts pin";

/**
 * Each way `migrations` break `lock`: a pin with no first deploy marked, a pinned migration
 * changed or gone, or, once the first deploy is marked, migrations past the pins.
 */
export function lockProblems(migrations: readonly Migration[], lock: Lock): string[] {
  const problems: string[] = [];
  if (lock.first_deploy === null && lock.deployed.length > 0) {
    problems.push("migrations are pinned with no first deploy marked");
  }
  for (const [index, pinned] of lock.deployed.entries()) {
    const migration = migrations[index];
    if (migration === undefined) {
      problems.push(`migration ${index + 1} is pinned and gone`);
    } else if (migrationDigest(migration) !== pinned) {
      problems.push(`migration ${index + 1} is pinned and its text changed: add a new ` +
                    "migration instead (one only this branch pins is pinned again from " +
                    "main's copy of the lock)");
    }
  }
  if (lock.first_deploy !== null && migrations.length > lock.deployed.length) {
    problems.push(`the code holds ${migrations.length} migrations and ${lock.deployed.length} ` +
                  `are pinned: pin each in the change that adds it (${PIN} <worker> HEAD, ` +
                  "in platform/)");
  }
  return problems;
}

/** The lock after a pin of `migrations` at `commit`: marked once, every migration pinned. */
export function pin(migrations: readonly Migration[], lock: Lock, commit: string,
                    at: string): Lock {
  const problems = lockProblems(migrations.slice(0, lock.deployed.length), lock);
  if (problems.length > 0) {
    throw new Error(`refusing to pin: ${problems.join("; ")}`);
  }
  return { first_deploy: lock.first_deploy ?? { at, commit },
           deployed: migrations.map(migrationDigest) };
}

/** `text`, a lock's file at `path`, held to its shape. */
export function lockOf(text: string, path: string): Lock {
  const lock: unknown = JSON.parse(text);
  if (!keyed(lock, ["deployed", "first_deploy"])
      || (lock["first_deploy"] !== null && !keyed(lock["first_deploy"], ["at", "commit"]))
      || !Array.isArray(lock["deployed"])
      || lock["deployed"].some((each) => typeof each !== "string" || !HEX64.test(each))) {
    throw new Error(`${path} is out of shape`);
  }
  return lock as unknown as Lock;
}

/** The lock at `path` (from `platform/`). */
export function readLock(path: string): Lock {
  return lockOf(readFileSync(join(PLATFORM, path), "utf8"), path);
}

/** The code's digests of each lock's migrations, by lock. */
export function digests(locks: Locks): Record<string, string[]> {
  return Object.fromEntries(Object.entries(locks)
    .map(([path, migrations]) => [path, migrations.map(migrationDigest)]));
}

/** Each problem of each of `locks` (`lockProblems`), by its lock: the migrations guard. */
export function holdLocks(locks: Locks): string[] {
  return Object.entries(locks).flatMap(([path, migrations]) =>
    lockProblems(migrations, readLock(path)).map((problem) => `${path}: ${problem}`));
}

/**
 * Why `worker`'s code may not deploy under its `locks`, each as `held` by its path: none, or
 * no lock at all, or a lock that marks no first deploy, or any of its `lockProblems`.
 */
export function deployProblems(worker: string, locks: Locks,
                               held: Readonly<Record<string, Lock>>): string[] {
  if (Object.keys(locks).length === 0) {
    return [`${worker} states no migration lock: each Worker the deploy holds to locks has one`];
  }
  return Object.entries(locks).flatMap(([path, migrations]) => {
    const lock = held[path]!;
    return lock.first_deploy === null
      ? [`the migration lock ${path} marks no first deploy: pin it with ${PIN} ${worker} ` +
         "<commit> in platform/ and land the locks on main before the deploy"]
      : lockProblems(migrations, lock).map((problem) => `the migration lock ${path}: ${problem}`);
  });
}

/** What `digests` prints for `worker`: the code's digests by lock; else it throws why not. */
export function deployAnswer(worker: string, locks: Locks,
                             held: Readonly<Record<string, Lock>>): string {
  const problems = deployProblems(worker, locks, held);
  if (problems.length > 0) {
    throw new Error(problems.join("\n"));
  }
  return JSON.stringify(digests(locks));
}

/** The Workers whose Durable Objects keep tables: each states its locks (`guards/locks.ts`). */
export function migrated(): string[] {
  return workerFolders()
    .filter((worker) => existsSync(join(PLATFORM, worker, "guards", "locks.ts")));
}

/** `worker`'s locks, as its `guards/locks.ts` states them. */
export async function locksOf(worker: string): Promise<Locks> {
  const { LOCKS } = await import(join(PLATFORM, worker, "guards", "locks.ts")) as
    { LOCKS?: Locks };
  if (LOCKS === undefined) {
    throw new Error(`${worker}/guards/locks.ts states no LOCKS`);
  }
  return LOCKS;
}

/** The command line (see the header); what it prints, or why it fails. */
export async function command(argv: readonly string[]): Promise<string> {
  const [verb, worker, commit] = argv;
  if (verb === "digests" && worker !== undefined && argv.length === 2) {
    const locks = await locksOf(worker);
    return deployAnswer(worker, locks, Object.fromEntries(Object.keys(locks)
      .map((path) => [path, readLock(path)])));
  }
  if (verb === "pin" && worker !== undefined && commit !== undefined && argv.length === 3) {
    const at = new Date().toISOString();
    const sha = execFileSync("git", ["rev-parse", "--verify", `${commit}^{commit}`],
                             { cwd: PLATFORM, encoding: "utf8" }).trim();
    const locks = await locksOf(worker);
    for (const [path, migrations] of Object.entries(locks)) {
      writeFileSync(join(PLATFORM, path),
                    `${JSON.stringify(pin(migrations, readLock(path), sha, at), null, 2)}\n`);
    }
    return `pinned ${Object.keys(locks).join(", ")} at ${sha}`;
  }
  throw new Error("usage: node ci/kit/locks.ts digests <worker> | pin <worker> <commit>");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  command(process.argv.slice(2)).then((out) => console.log(out), (error: Error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
