/**
 * The migration locks: append-only migrations from each Worker's first deploy (tasks
 * "Append-only migrations from the first deploy"; slice P). One lock a Durable Object class
 * that keeps tables, each Worker's held by its migrations guard: a Worker states its locks,
 * each lock's path from `platform/` to the migrations the code holds for it, as `LOCKS` in
 * its `guards/locks.ts`.
 *
 * Once a migration has run on a live object it is never edited: a new shape is a new
 * migration at the end. A lock holds:
 * - `first_deploy`: null until its Worker's first deploy, then `{ at, commit }`. Until it is
 *   set, nothing is deployed and migrations may still be edited in place.
 * - `deployed`: each deployed migration's SHA-256, in order (version n at index n - 1), over
 *   its statements as the code evaluates them (`migrationDigest`).
 *
 * The guard (`holdLocks`) fails when a pinned migration's text differs from its pin, when a
 * pinned one is gone, or when anything is pinned without the first-deploy marker. Its
 * command line is the deploy's:
 * - `node ci/kit/locks.ts digests <worker>` prints the code's digests by lock, as JSON, when
 *   every lock of the Worker marks its first deploy and pins exactly them, in order; else it
 *   fails, naming each lock that does not. The deploy kit refuses to deploy on its failure.
 * - `node ci/kit/locks.ts pin <worker> <commit>` marks each lock's first deploy once (the
 *   pins already there are checked first: a changed one refuses the pin) and pins every
 *   migration the code holds; the deploy commits the locks with the code it deployed.
 *
 * Node runs this file as it is (type stripping), so its own imports name their `.ts` files.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { type Migration, migrationDigest } from "../../contract/src/migrate.ts";
import { PLATFORM } from "../paths.ts";

/** A lock, as its file holds it. */
export interface Lock {
  readonly first_deploy: { readonly at: string; readonly commit: string } | null;
  readonly deployed: readonly string[];
}

/** A Worker's locks: each lock's path from `platform/`, to the migrations the code holds. */
export type Locks = Readonly<Record<string, readonly Migration[]>>;

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
export function pin(migrations: readonly Migration[], lock: Lock, commit: string,
                    at: string): Lock {
  const problems = lockProblems(migrations, lock);
  if (problems.length > 0) {
    throw new Error(`refusing to pin: ${problems.join("; ")}`);
  }
  return { first_deploy: lock.first_deploy ?? { at, commit },
           deployed: migrations.map(migrationDigest) };
}

/** `text`, a lock's file at `path`, held to its shape. */
export function lockOf(text: string, path: string): Lock {
  const lock = JSON.parse(text) as Lock | null;
  const marked = (first: unknown) => first === null
    || (typeof first === "object" && Object.keys(first).sort().join() === "at,commit");
  if (typeof lock !== "object" || lock === null || Array.isArray(lock)
      || Object.keys(lock).sort().join() !== "deployed,first_deploy"
      || !marked(lock.first_deploy) || !Array.isArray(lock.deployed)
      || lock.deployed.some((each) => typeof each !== "string" || !/^[0-9a-f]{64}$/.test(each))) {
    throw new Error(`${path} is out of shape`);
  }
  return lock;
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

/** How a Worker is pinned: this file's own command line, from `platform/`. */
const PIN = "node ci/kit/locks.ts pin";

/**
 * Why `worker`'s code may not deploy under its `locks`, each as `held` by its path: none, or
 * no lock at all, or a lock that marks no first deploy or does not pin exactly the code's
 * migrations, in order.
 */
export function deployProblems(worker: string, locks: Locks,
                               held: Readonly<Record<string, Lock>>): string[] {
  if (Object.keys(locks).length === 0) {
    return [`${worker} states no migration lock: each Worker the deploy holds to locks has one`];
  }
  const pinThem = `pin them with ${PIN} ${worker} <commit> in platform/ and commit the locks ` +
    "with the code";
  return Object.entries(digests(locks)).flatMap(([path, code]) => {
    const lock = held[path]!;
    if (lock.first_deploy === null) {
      return [`the migration lock ${path} marks no first deploy: ${pinThem}`];
    }
    return lock.deployed.join() === code.join() ? []
      : [`the migration lock ${path} pins ${lock.deployed.length} migrations and the code ` +
         `ships ${code.length}, or one differs: ${pinThem}`];
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
    const locks = await locksOf(worker);
    for (const [path, migrations] of Object.entries(locks)) {
      writeFileSync(join(PLATFORM, path),
                    `${JSON.stringify(pin(migrations, readLock(path), commit, at), null, 2)}\n`);
    }
    return `pinned ${Object.keys(locks).join(", ")} at ${commit}`;
  }
  throw new Error("usage: node ci/kit/locks.ts digests <worker> | pin <worker> <commit>");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  command(process.argv.slice(2)).then((out) => console.log(out), (error: Error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
