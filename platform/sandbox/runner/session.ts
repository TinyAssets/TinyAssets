/**
 * The run session, shared by the runner (`runner.ts`) and the template's probes
 * (`probes.ts`), so every CLI either starts runs as a run runs it (`withRunUser`, bounded
 * by `spawnBounded`): the platform's CLI records, the engines' stores under their locks and
 * generations, a run's home made from its store and kept back into it, and the run's config
 * files made root's. Nothing here reads a job.
 */
import { sha256Hex } from "../../contract/src/digest";
import { type ChildProcess, spawn, type SpawnOptions, spawnSync } from "node:child_process";
import { chownSync, existsSync, lchownSync, lstatSync, mkdirSync, readdirSync, readFileSync,
         writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { bundledSources } from "../../sources/src/catalog";
import { RUN_BASICS } from "../../sources/src/run-env";
import type { SourceRecord } from "../../sources/src/schema";

import { HOMES, isKept, keptPaths, SYSTEM } from "./engine";
import { HEARTBEAT_MS } from "./frames";
import { remove, safeDirs, safeRead, safeWrite, type Walk, walkFiles } from "./fsafe";
import { withLock } from "./pid-lock";
import { COMMAND_MS, STATE_DIR } from "./protocol";
import { give, group, type Identity, killAll, SHARED_GROUP, take } from "./users";

const STATE = STATE_DIR;
/** Each engine's store: what a run's home is made from and kept back into. */
export const STORE = `${STATE}/store`;
/** The conversations of the engines that resume by their working directory. */
export const CONVERSATIONS = "/var/lib/ta-conv";

export const sources = bundledSources();
export const RECORDS = new Map(sources.ids().map((id) => [id, sources.get(id)!] as const)
  .filter(([, rec]) => rec.cli !== undefined));

export function record(engine: string): SourceRecord {
  const found = RECORDS.get(engine);
  if (found === undefined) {
    throw new Error(`engine ${engine} has no CLI record`);
  }
  return found;
}

export function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

export function shared(): number {
  return group(SHARED_GROUP);
}

/** The engine's record variables, filled for a home (its status check's environment). */
export function engineEnv(rec: SourceRecord, home: string): Record<string, string> {
  const cli = rec.cli!;
  const env: Record<string, string> = {};
  for (const name of RUN_BASICS) {
    const value = process.env[name];
    if (value !== undefined) {
      env[name] = value;
    }
  }
  const fill = (text: string) => text.replaceAll("{home}", home).replaceAll("{agent}", "");
  for (const [name, value] of Object.entries({
    ...cli.home_env, ...cli.env, ...("env" in cli.no_update ? cli.no_update.env : {}) })) {
    if (!value.includes("{system}")) {
      env[name] = fill(value);
    }
  }
  env["HOME"] = home;
  return env;
}

/** A tree's files in a walk's sorted order, each directory read whole before it is offered. */
export function sortedWalk(dir: string, skip?: (rel: string) => boolean): Generator<string> {
  const walk: Walk = skip === undefined ? { sorted: true } : { sorted: true, skip };
  return walkFiles(dir, walk);
}

// -- homes and the engines' stores ----------------------------------------------------

/** How long a store's lock is waited for: one command's bound, then the call fails loudly. */
const LOCK_WAIT_MS = COMMAND_MS;

/**
 * The engine's store held by this process alone while `act` runs (a home copied from it, a
 * run's secrets written back, a sign-in's or sign-out's generation), by its lock
 * (`pid-lock.ts`, review N3). The wait sleeps, so the runner's heartbeats go on, and it is
 * bounded (`LOCK_WAIT_MS`): past it the call fails loudly. Every hold is short and
 * synchronous, and none is nested.
 */
export async function withStore<T>(rec: SourceRecord, act: () => T): Promise<T> {
  const locks = `${STATE}/locks`;
  safeDirs("/", locks, 0o700);
  // `.lock`: a name no earlier runner's lock (a directory at `<id>`) ever had, so neither
  // form is read by the other's runner, across an adoption or a rollback.
  return withLock(`${locks}/${rec.id}.lock`, LOCK_WAIT_MS, () => new Error(
    `the store of ${rec.id} stayed locked past ${LOCK_WAIT_MS} ms`), act);
}

/**
 * The engine's store generation: a sign-in that writes new secrets and a sign-out each move
 * it on (review N2: a sign-in that only began, failed or was stopped moves nothing), and a
 * run's home made under an older one never writes a secret back (review M3: a run live
 * across a sign-out never undoes it, nor clobbers a new sign-in).
 */
export function generation(rec: SourceRecord): number {
  const path = `${STATE}/generations/${rec.id}`;
  return existsSync(path) ? Number(readFileSync(path, "utf8")) : 0;
}

export function nextGeneration(rec: SourceRecord): void {
  safeDirs("/", `${STATE}/generations`, 0o700);
  writeFileSync(`${STATE}/generations/${rec.id}`, String(generation(rec) + 1), { mode: 0o600 });
}

/**
 * What a home was made from: its store's generation, each secret's digest as copied, and
 * whether it carries the store's secrets at all (an API-key run's never does).
 */
export interface Snapshot {
  readonly generation: number;
  readonly secrets: Readonly<Record<string, string>>;
  readonly signedIn: boolean;
}

function snapshotOf(home: string): string {
  return `${STATE}/kept/${home.split("/").at(-1)!}.json`;
}

/** The home a session named `name` runs in. */
export function homeOf(name: string): string {
  return `${HOMES}/${name}`;
}

/** The root-owned directory of a session named `name`, for the files a CLI takes only there. */
export function systemOf(name: string): string {
  return `${SYSTEM}/${name}`;
}

/** A home gone, and the snapshot it was made under (review L2: none is left behind). */
export function dropHome(home: string): void {
  remove(home);
  remove(snapshotOf(home));
}

/**
 * A run's home, made fresh by root in its root-only parent from the engine's store (its
 * kept paths alone), under the store's lock, its snapshot kept (the generation and each
 * secret's digest); `giveTree` then gives it to the run's user entry by entry: no other
 * user can reach it while it is made. A home made for an API-key run (`signedIn` false)
 * carries the store's sessions and never its secrets: the vendor sign-in's files (Codex's
 * `auth.json`, Claude's `.credentials.json`) stay out of a key run's reach, so no key run's
 * CLI can send them anywhere (codexurl_research.md; the route probe proves it live).
 */
async function makeHome(name: string, rec: SourceRecord, signedIn: boolean): Promise<string> {
  safeDirs("/", HOMES, 0o711);
  const home = homeOf(name);
  remove(home);
  mkdirSync(home, { mode: 0o700 });
  const store = `${STORE}/${rec.id}`;
  const { secrets, sessions } = keptPaths(rec);
  const copied: Record<string, string> = {};
  const made = await withStore(rec, () => {
    for (const rel of sortedWalk(store)) {
      if (isKept(signedIn ? [...secrets, ...sessions] : sessions, rel)
          && (signedIn || !isKept(secrets, rel))) {
        const bytes = safeRead(store, join(store, rel));
        if (bytes !== null) {
          safeWrite(home, join(home, rel), bytes, 0o600);
          if (isKept(secrets, rel)) {
            copied[rel] = sha256Hex(bytes);
          }
        }
      }
    }
    return generation(rec);
  });
  safeDirs("/", `${STATE}/kept`, 0o700);
  const snapshot: Snapshot = { generation: made, secrets: copied, signedIn };
  safeWrite(`${STATE}/kept`, snapshotOf(home),
            new TextEncoder().encode(JSON.stringify(snapshot)), 0o600);
  // Each directory the record's home variables name, made: a CLI may refuse a configured
  // home that is not there yet (a first sign-in has nothing in its store).
  for (const value of Object.values(rec.cli!.home_env)) {
    safeDirs(home, value.replaceAll("{home}", home), 0o700);
  }
  mkdirSync(join(home, "tmp"), { mode: 0o700 });
  return home;
}

/** Every entry of a tree root just made given to `as`, links as links. */
export function giveTree(path: string, as: Identity): void {
  lchownSync(path, as.uid, as.gid);
  if (lstatSync(path).isDirectory()) {
    for (const entry of readdirSync(path)) {
      giveTree(join(path, entry), as);
    }
  }
}

/**
 * The run's kept paths back into its engine's store, once no process of its user is left
 * (so nothing changes the home while it is read), no link followed, under the store's lock.
 * A run's secret is written back by compare-and-swap (review M3): only one this run changed,
 * only while the store still holds the copy the run started from, and only under the
 * generation its home was made in. So a stale copy never replaces a newer one (a vendor's
 * refresh token is single-use), and a run live across a sign-in or sign-out never undoes
 * it. A completed sign-in's (`signin`) new secrets are the owner's new login: written
 * whatever the store holds, and they move the generation on, then and only then (N2).
 */
export async function keepHome(home: string, rec: SourceRecord, signin = false): Promise<void> {
  const store = `${STORE}/${rec.id}`;
  safeDirs("/", store, 0o700);
  const { secrets, sessions } = keptPaths(rec);
  const path = snapshotOf(home);
  const snapshot = existsSync(path) ? readJson<Snapshot>(path) : null;
  await withStore(rec, () => {
    const current = generation(rec);
    let renewed = false;
    for (const rel of sortedWalk(home)) {
      if (!isKept([...secrets, ...sessions], rel)) {
        continue;
      }
      const bytes = safeRead(home, join(home, rel));
      if (bytes === null) {
        continue;
      }
      if (isKept(secrets, rel)) {
        const base = snapshot?.secrets[rel] ?? null;
        const stored = safeRead(store, join(store, rel));
        if (sha256Hex(bytes) === base) {
          continue;  // unchanged: never written back
        }
        // A key run's home carried none, so it writes none back (it never had the login).
        if (!signin && (snapshot === null || snapshot.signedIn !== true
                        || snapshot.generation !== current
                        || (stored === null ? null : sha256Hex(stored)) !== base)) {
          continue;
        }
        renewed ||= signin;
      } else {
        // A session the store holds as it is is not written again, so its change time
        // stands and it is not hashed again (`SESSIONS_SEEN`).
        const stored = safeRead(store, join(store, rel));
        if (stored !== null && sha256Hex(stored) === sha256Hex(bytes)) {
          continue;
        }
      }
      safeWrite(store, join(store, rel), bytes, 0o600);
    }
    if (renewed) {
      nextGeneration(rec);
    }
  });
  remove(path);
}

/**
 * The run's config files (its MCP config, its settings, its models file) are root's,
 * readable and never writable by its CLI, so an agent cannot rewrite its own MCP door or
 * trust; a directory in the home that holds nothing but them is root's too. In `{system}`,
 * root's, readable by the run's own group alone.
 */
function configOwned(rec: SourceRecord, home: string, system: string,
                     files: Iterable<string>, as: Identity): void {
  const cli = rec.cli!;
  const kept = [...Object.values(cli.home_env), ...cli.session_paths, ...cli.secret_paths]
    .map((path) => path.replaceAll("{home}", home).replace(/\/+$/, ""));
  for (const file of files) {
    if (file.startsWith(`${system}/`)) {
      lchownSync(file, 0, as.gid);
      continue;
    }
    lchownSync(file, 0, 0);
    for (let dir = dirname(file); dir.startsWith(`${home}/`); dir = dirname(dir)) {
      if (kept.some((path) => path === dir || path.startsWith(`${dir}/`))) {
        break;
      }
      lchownSync(dir, 0, 0);
    }
  }
}

/** Files a session's user left in the shared scratch directories, removed (links as links). */
function cleanScratch(uid: number): void {
  for (const dir of ["/tmp", "/var/tmp", "/dev/shm"]) {
    spawnSync("find", [dir, "-xdev", "-mindepth", "1", "-user", String(uid),
                       "-delete"], { stdio: "ignore" });
  }
}

/** A session's user, its home and its root-owned directory, as `act` is handed them. */
export interface RunSession {
  readonly as: Identity;
  readonly home: string;
  readonly system: string;
}

/**
 * `act` run as a run runs, by every CLI the runner or the probes start: a pool user taken;
 * its home made from the engine's store (with its secrets when `signedIn`) and its own
 * root-owned directory (`systemOf`); `files` (an invocation's, by path) written, those in
 * that directory root's and readable by the user's group, the rest root's in the home
 * (`configOwned`), and the home given to the user. Once `act` is over, however it ends:
 * every process of the user killed, what it left in the shared scratch removed, the home
 * kept back into the store as a run's (`keeps`: by compare-and-swap, `keepHome`), the home
 * and the directory dropped, and the user given back.
 */
export async function withRunUser<T>(rec: SourceRecord, name: string,
                                     opts: { readonly signedIn: boolean;
                                             readonly keeps: boolean;
                                             readonly files?: ReadonlyMap<string, string> },
                                     act: (session: RunSession) => Promise<T>): Promise<T> {
  const as = await take(STATE);
  const system = systemOf(name);
  let home: string | null = null;
  try {
    home = await makeHome(name, rec, opts.signedIn);
    safeDirs("/", SYSTEM, 0o755);
    remove(system);
    mkdirSync(system, { mode: 0o750 });
    chownSync(system, 0, as.gid);
    const files = opts.files ?? new Map<string, string>();
    for (const [path, text] of files) {
      const inSystem = path.startsWith(`${system}/`);
      safeWrite(inSystem ? system : home, path, new TextEncoder().encode(text),
                inSystem ? 0o640 : 0o644, undefined, inSystem ? 0o750 : 0o755);
    }
    giveTree(home, as);
    configOwned(rec, home, system, files.keys(), as);
    return await act({ as, home, system });
  } finally {
    try {
      await killAll(as);
      cleanScratch(as.uid);
      if (home !== null && opts.keeps) {
        await keepHome(home, rec);
      }
    } finally {
      if (home !== null) {
        dropHome(home);
      }
      remove(system);
      await give(STATE, as);
    }
  }
}

/** A sign-in's own files (its record's `vendor_login_in_sandbox` files), its user's. */
export function loginFiles(rec: SourceRecord, { as, home }: RunSession): void {
  const files = rec.cli!.per_credential?.vendor_login_in_sandbox?.files ?? {};
  for (const [path, text] of Object.entries(files)) {
    safeWrite(home, path.replaceAll("{home}", home), new TextEncoder().encode(text), 0o600, as);
  }
}

/** The exit of a CLI killed (by `spawnBounded`'s stop or its bound): SIGTERM's. */
export const KILLED_BY_TERM = 128 + 15;
/**
 * A stopped CLI's grace between its SIGTERM and its group's SIGKILL: one heartbeat, so a
 * cancelled run's end reaches the object well inside the lease's TTL (`LEASE_TTL_MS`), its
 * runner's last post included.
 */
export const KILL_GRACE_MS = HEARTBEAT_MS;

/** A process `spawnBounded` started: its exit, and its stop. */
export interface Bounded {
  readonly child: ChildProcess;
  /** Its exit code once it closed (`KILLED_BY_TERM` when a signal ended it). */
  readonly exit: Promise<number>;
  /** SIGTERM to its process group, then SIGKILL of the group after `KILL_GRACE_MS`. */
  stop(): void;
}

/**
 * `argv` started in a process group of its own, stopped at `ms` (`Infinity`: never, as
 * `withLock`'s wait) as `stop` stops it. No kill outlives its close, so none reaches a
 * group that reused its id.
 */
export function spawnBounded(argv: readonly string[], opts: SpawnOptions, ms: number): Bounded {
  const child = spawn(argv[0]!, argv.slice(1), { ...opts, detached: true });
  let kill: ReturnType<typeof setTimeout> | null = null;
  const signal = (name: NodeJS.Signals) => {
    try {
      process.kill(-child.pid!, name);
    } catch {
      // gone already
    }
  };
  const stop = () => {
    if (child.pid === undefined || child.exitCode !== null || kill !== null) {
      return;
    }
    signal("SIGTERM");
    kill = setTimeout(() => signal("SIGKILL"), KILL_GRACE_MS);
  };
  const bound = Number.isFinite(ms) ? setTimeout(stop, ms) : null;
  const exit = new Promise<number>((resolve, reject) => {
    child.on("error", reject);
    child.on("close", (code) => {
      if (bound !== null) {
        clearTimeout(bound);
      }
      if (kill !== null) {
        clearTimeout(kill);
      }
      resolve(code ?? KILLED_BY_TERM);
    });
  });
  return { child, exit, stop };
}
