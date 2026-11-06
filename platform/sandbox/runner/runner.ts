/**
 * The in-VM runner (`ta-runner`; ADR-034 decision 4.3, `engines-replan.md` §2.1 steps 4-7),
 * shipped by the sandbox Worker (`src/runner-bytes.ts`) and run as root through envd by the
 * template's loader (`template/ta-runner`), which names it by its SHA-256 and checks its
 * bytes before every command (`/opt/ta/runner/<sha256>/runner.mjs`, `TA_RUNNER_DIGEST` set).
 * Its on-disk format and the rule that keeps a rollback one deploy deep are
 * `state-format.ts`'s. Root never follows a path another user controls (slice S review
 * H-1 to H-3): every file it writes or reads in a tree another user can write goes through
 * `fsafe.ts` (no link followed, owner and mode set on the descriptor), the sync agent runs
 * as its own user (`ta-sync`), and each run as a user of its own from the template's pool
 * (`users.ts`), its own home and its own root-owned system directory made fresh for it.
 *
 * - `prepare <dns...>`: the sandbox just started or resumed: `/etc/resolv.conf` pinned to
 *   the network's resolvers (E2B opens the firewall only), `/proc` remounted `hidepid=2`,
 *   and nothing root runs left writable by another user (`lockDown`), fixed and checked
 *   at every start and resume.
 * - `adopt`: this runner to be made the sandbox's own, while nothing lives (3 otherwise):
 *   the disk held to its format. Its `prepare` then drops every other runner's bytes.
 * - `signin-check <engine>`: the record's `status_argv`, as a run user on a home made from
 *   the engine's store: 0 signed in, 3 not.
 * - `launch <run>`: the start document the Worker put in the root-only inbox taken, and the
 *   run's runner started detached; 0 once it runs.
 * - `run <run>` (the detached runner): a first empty post (the run's first heartbeat), the
 *   sandbox's sign-ins reported, the owner's files synced in (as `ta-sync`), the run's user
 *   taken and its home made from the engine's store (its `secret_paths` and
 *   `session_paths`, nothing else: a CLI's other home files never outlive a run), the
 *   refusal of env files the CLI would load (its home's, the agent's, and every ancestor's
 *   of its cwd), the CLI as that user from the record, its events framed and posted (a batch
 *   at least every `HEARTBEAT_MS`); at its end every process of the user killed, the kept
 *   paths back into the store, the files synced out, the user given back, then the end.
 * - `cancel <run>`: SIGTERM to the CLI, a kill after `CANCEL_GRACE_MS`; 3 when not live.
 * - `status <run>`: 0 while its runner runs, else 3.
 * - `state`: the live runs and whether every CLI is quiet (`{live, quiet}`).
 * - `quiet-wait <until_ms>`: until every live CLI is quiet (between turns, as its events
 *   show) or `until`, then the live runs and whether they are quiet (`{live, quiet}`): the
 *   renewal's look for a quiet moment, signalling nothing.
 * - `renew-wait <deadline_ms>`: until every live CLI is quiet or the deadline, then each
 *   run's runner posts `ta.renewing` and holds its posts; `renewed`: each posts
 *   `ta.renewed` and goes on.
 * - `recover`: after a resume that rebooted the sandbox, each run that was live is started
 *   again from its session (`resume_argv`).
 * - `sync` (internal, as `ta-sync`, its job on stdin): the sync agent and the projection.
 * - `login-launch <id>`: the vendor's own sign-in (slice SIGNIN; `login.ts`), its document
 *   the Worker put in the root-only inbox, started detached (`login-run <id>`): the record's
 *   login as a user of its own, its home made from the engine's store, the URL or code the
 *   record's patterns find and its pinned page admits relayed (`ta.signin` frames on the
 *   sign-in's event door: fixed states, the URL and code, a refusal's fixed reason), a pasted
 *   code (`login-input <id>`, the Worker's upload, read and deleted at once; `login-drop
 *   <id>` deletes one never handed over) written to its stdin, then its secrets kept in the
 *   engine's store by compare-and-swap and the engine's presence reported (`urls.signin`). A
 *   sign-in and a sign-out each move the engine's store generation on, so no run live across
 *   one writes an older login back; a sign-out (`mode: out`) also stops that engine's live
 *   runs, runs the vendor's logout, deletes the engine's `secret_paths` from the store and
 *   reports the same. `login-cancel <id>` stops it. `login-probe <engine> [region]`: the
 *   template build's standing check, the login run up to its prompt and stopped, its shape
 *   checked.
 */
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { chownSync, closeSync, existsSync, lchownSync, lstatSync, mkdirSync, openSync,
         readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createServer as createHttpServer, type IncomingMessage } from "node:http";
import { createConnection, createServer } from "node:net";
import { createInterface } from "node:readline";

import type { EgressScope } from "../../gateway/src/credential";
import { bundledRecords, bundledSources, type LoginSpec, RUN_BASICS, runEnv, SIGNIN_REFUSALS,
         type SigninRefusal, signinShape, type SourceRecord } from "../../doors/src/sources";
import { EGRESS_ORIGIN, egressBaseUrl, sandboxEnv } from "../../owner/src/network";
import { FRAME_UNITS_MAX, readEvent } from "../../owner/src/run-events";
import { SIGNIN_PASTE_MAX } from "../../owner/src/run-rows";

import { AGENTS_DIR, HOMES, invocation, isKept, keptPaths, type StartDoc, SYSTEM,
         WORKSPACE } from "./engine";
import { lineEvent, Outbox } from "./frames";
import { remove, safeDirs, safeRead, safeWalk, safeWrite, walkFiles } from "./fsafe";
import { ancestorEnvFile, heldSecret, homeEnvFile, projectEnvFile, projectEnvRefusal,
         secretAtoms } from "./guards";
import { project } from "./project";
import { diskOf, keepReserve, pruneScratch, readIndex, RESERVE_BYTES, SCRATCH, scratchEnv,
         scratchNotice,
         markUsed,
         writeOrRelease } from "./scratch";
import { type Captured, outsideConnects, reply, routeVerdict } from "./route";
import { credentialShaped, loginArgv, onTerminal, plain, type Prompt, prompted,
         statesHold } from "./login";
import { AUTOMATON_STATES, LIST_BYTES, stepNanos } from "../../owner/src/sync-ignore";
import { openMirror, SESSIONS, SyncClient, SyncFenced, syncIn, syncOut, type SyncReport }
  from "./sync";
import { PROTOCOL } from "./protocol";
import { checkFormat, dropRunners } from "./state-format";
import { asUser, give, group, type Identity, killAll, POOL_PREFIX, take, user } from "./users";

/** Every path the runner touches is under this root: `/` in the sandbox, a temp dir in tests. */
const ROOT = process.env["TA_RUNNER_ROOT"] ?? "";
const TOP = ROOT === "" ? "/" : ROOT;
const STATE = `${ROOT}/var/lib/ta`;
const STORE = `${STATE}/store`;
/** The sync agent's own directory (`ta-sync`, 0700): its mirror, sessions in and out. */
const SYNC_HOME = `${ROOT}/var/lib/ta-sync`;
const CONVERSATIONS = `${ROOT}/var/lib/ta-conv`;
const LOCAL_WORKSPACE = `${ROOT}${WORKSPACE}`;
/** The agents' scratch (`scratch.ts`): kept between runs, never synced, never counted. */
const LOCAL_SCRATCH = `${ROOT}${SCRATCH}`;
/** Each agent's stamp of its scratch's last use (`scratch.ts`), and the runner's reserve. */
const SCRATCH_INDEX = `${STATE}/scratch-used`;
const RESERVE = `${STATE}/reserve`;
/** The engines' sessions as last hashed, so an unchanged one is not read again a run. */
const SESSIONS_SEEN = `${STATE}/sessions-seen.json`;
/** The runners installed by digest (`template/ta-runner`): `<sha256>/runner.mjs` each. */
const RUNNERS = `${ROOT}/opt/ta/runner`;
/** What the runner keeps that is a cache alone, discarded when its format changes. */
const CACHES: readonly string[] = Object.freeze([SESSIONS_SEEN]);
/** A change time the runner trusts: older than the coarsest filesystem tick (`sync.ts`). */
const RACY_MS = 2_000;
/** Its size: `RESERVE_BYTES` in the sandbox; a mebibyte under a test's root, the same path. */
const RESERVE_SIZE = ROOT === "" ? RESERVE_BYTES : 1024 * 1024;

/**
 * The runner's heartbeat: slice R's lease renewal cadence (a batch at least every 20 s). A
 * test's root alone may shorten it (`TA_HEARTBEAT_MS`), so a test of a sync-out longer than
 * a heartbeat is seconds long; the sandbox's root (`/`) never reads it.
 */
export const HEARTBEAT_MS = ROOT !== "" && process.env["TA_HEARTBEAT_MS"] !== undefined
  ? Number(process.env["TA_HEARTBEAT_MS"]) : 20_000;
/** How often the runner flushes what its CLI wrote: the frames' own cadence (§2.2). */
const FLUSH_MS = 250;
/**
 * A cancelled CLI's grace before the kill: one heartbeat, so its end reaches the object
 * well inside the lease's TTL (`LEASE_TTL_MS`), its runner's last post included.
 */
export const CANCEL_GRACE_MS = HEARTBEAT_MS;
/**
 * The lease's TTL (`owner/src/files.ts`'s `LEASE_TTL_S`, which `guards/config.test.ts` holds
 * this equal to; the runner's bundle takes nothing of the object's storage modules).
 */
export const LEASE_TTL_MS = 60_000;
/**
 * The longest a status check, or the wait for a runner's ack, may take: the lease's TTL,
 * past which a start's run is lost anyway (a run with no post within it is settled lost).
 */
const COMMAND_MS = LEASE_TTL_MS;
const OK = 0;
const NO = 3;
const FENCED = 4;
/** The input a run resumed after a renewal or a reboot is continued with. */
export const CONTINUE_PROMPT = "The sandbox paused to renew its runtime and your last step " +
  "was interrupted. Continue the task where you left off.";

const sources = bundledSources();
const RECORDS = new Map(sources.ids().map((id) => [id, sources.get(id)!] as const)
  .filter(([, rec]) => rec.cli !== undefined));

function record(engine: string): SourceRecord {
  const found = RECORDS.get(engine);
  if (found === undefined) {
    throw new Error(`engine ${engine} has no CLI record`);
  }
  return found;
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

/** A file of root's own state directory (no other user reaches it). */
function writeJson(path: string, value: unknown): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  writeFileSync(`${path}.tmp`, JSON.stringify(value), { mode: 0o600 });
  renameSync(`${path}.tmp`, path);
}

const RUN = /^run_[0-9a-f]{32}$/;
function runDir(run: string): string {
  if (!RUN.test(run)) {
    throw new Error("a run id is run_ and 32 hex");
  }
  return `${STATE}/runs/${run}`;
}

/** A run's live state on disk, which outlives a reboot of the sandbox. */
interface RunState {
  pid: number;
  cli_pid: number | null;
  seq: number;
  session_id: string | null;
  last_kind: string | null;
  ended: boolean;
  renewing: boolean;
}

function readState(run: string): RunState | null {
  const path = `${runDir(run)}/state.json`;
  return existsSync(path) ? readJson<RunState>(path) : null;
}

function alive(pid: number | null): boolean {
  if (pid === null || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** The runs whose runners still run. */
function liveRuns(): { run: string; state: RunState }[] {
  const dir = `${STATE}/runs`;
  if (!existsSync(dir)) {
    return [];
  }
  return readdirSync(dir).filter((run) => RUN.test(run)).flatMap((run) => {
    const state = readState(run);
    return state !== null && !state.ended && alive(state.pid) ? [{ run, state }] : [];
  });
}

function shared(): number {
  return group(ROOT, "ta");
}

/** The engine's record variables, filled for a home (its status check's environment). */
function engineEnv(rec: SourceRecord, home: string): Record<string, string> {
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

function binOf(rec: SourceRecord): string {
  const install = rec.cli!.install;
  return install.manager === "npm"
    ? `${ROOT}/opt/ta/engines/${rec.id}/node_modules/.bin/${install.bin}`
    : `${ROOT}/opt/ta/engines/${rec.id}/bin/${install.bin}`;
}

// -- homes and the engines' stores ----------------------------------------------------

/** This boot's id: a pid names a process only within the boot it was taken in. */
function bootId(): string {
  try {
    return readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim();
  } catch {
    return "";
  }
}

/** How long a store's lock is waited for: one command's bound, then the call fails loudly. */
const LOCK_WAIT_MS = COMMAND_MS;

/**
 * The engine's store held by this process alone while `act` runs (a home copied from it, a
 * run's secrets written back, a sign-in's or sign-out's generation): a lock directory naming
 * its holder as its pid and its boot (review N3). It is taken over once that holder is gone:
 * its process dead, or of another boot (a pid repeats after a reboot, which a lock on disk
 * outlives), or never named and older than a command's bound. The wait sleeps, so the
 * runner's heartbeats go on, and it is bounded (`LOCK_WAIT_MS`): past it the call fails
 * loudly. Every hold is short and synchronous, and none is nested.
 */
async function withStore<T>(rec: SourceRecord, act: () => T): Promise<T> {
  const locks = `${STATE}/locks`;
  safeDirs(TOP, locks, 0o700);
  const lock = `${locks}/${rec.id}`;
  const boot = bootId();
  const until = Date.now() + LOCK_WAIT_MS;
  for (;;) {
    try {
      mkdirSync(lock, { mode: 0o700 });
      writeFileSync(`${lock}/holder`, `${process.pid} ${boot}`, { mode: 0o600 });
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
        throw error;
      }
      let gone: boolean;
      try {
        const [pid, of] = readFileSync(`${lock}/holder`, "utf8").split(" ");
        gone = of !== boot || !alive(Number(pid));
      } catch {
        // its holder has not named itself yet, or it died before it could
        try {
          gone = lstatSync(lock).mtimeMs + COMMAND_MS < Date.now();
        } catch {
          gone = false;  // let go meanwhile: the next pass takes it
        }
      }
      if (gone) {
        remove(lock);
        continue;
      }
      if (Date.now() >= until) {
        throw new Error(`the store of ${rec.id} stayed locked past ${LOCK_WAIT_MS} ms`);
      }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
  try {
    return act();
  } finally {
    remove(lock);
  }
}

/**
 * The engine's store generation: a sign-in that writes new secrets and a sign-out each move
 * it on (review N2: a sign-in that only began, failed or was stopped moves nothing), and a
 * run's home made under an older one never writes a secret back (review M3: a run live
 * across a sign-out never undoes it, nor clobbers a new sign-in).
 */
function generation(rec: SourceRecord): number {
  const path = `${STATE}/generations/${rec.id}`;
  return existsSync(path) ? Number(readFileSync(path, "utf8")) : 0;
}

function nextGeneration(rec: SourceRecord): void {
  safeDirs(TOP, `${STATE}/generations`, 0o700);
  writeFileSync(`${STATE}/generations/${rec.id}`, String(generation(rec) + 1), { mode: 0o600 });
}

/**
 * What a home was made from: its store's generation, each secret's digest as copied, and
 * whether it carries the store's secrets at all (an API-key run's never does).
 */
interface Snapshot {
  readonly generation: number;
  readonly secrets: Readonly<Record<string, string>>;
  readonly signedIn: boolean;
}

function snapshotOf(home: string): string {
  return `${STATE}/kept/${home.split("/").at(-1)!}.json`;
}

/** A home gone, and the snapshot it was made under (review L2: none is left behind). */
function dropHome(home: string): void {
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
async function makeHome(name: string, rec: SourceRecord, signedIn = true): Promise<string> {
  const homes = `${ROOT}${HOMES}`;
  safeDirs(TOP, homes, 0o711);
  const home = `${homes}/${name}`;
  remove(home);
  mkdirSync(home, { mode: 0o700 });
  const store = `${STORE}/${rec.id}`;
  const { secrets, sessions } = keptPaths(rec);
  const copied: Record<string, string> = {};
  const made = await withStore(rec, () => {
    for (const rel of safeWalk(store)) {
      if (isKept(signedIn ? [...secrets, ...sessions] : sessions, rel)
          && (signedIn || !isKept(secrets, rel))) {
        const bytes = safeRead(store, join(store, rel));
        if (bytes !== null) {
          safeWrite(home, join(home, rel), bytes, 0o600);
          if (isKept(secrets, rel)) {
            copied[rel] = digest(bytes);
          }
        }
      }
    }
    return generation(rec);
  });
  safeDirs(TOP, `${STATE}/kept`, 0o700);
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

function digest(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/** Every entry of a tree root just made given to `as`, links as links. */
function giveTree(path: string, as: Identity): void {
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
async function keepHome(home: string, rec: SourceRecord, signin = false): Promise<void> {
  const store = `${STORE}/${rec.id}`;
  safeDirs(TOP, store, 0o700);
  const { secrets, sessions } = keptPaths(rec);
  const path = snapshotOf(home);
  const snapshot = existsSync(path) ? readJson<Snapshot>(path) : null;
  await withStore(rec, () => {
    const current = generation(rec);
    let renewed = false;
    for (const rel of safeWalk(home)) {
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
        if (digest(bytes) === base) {
          continue;  // unchanged: never written back
        }
        // A key run's home carried none, so it writes none back (it never had the login).
        if (!signin && (snapshot === null || snapshot.signedIn !== true
                        || snapshot.generation !== current
                        || (stored === null ? null : digest(stored)) !== base)) {
          continue;
        }
        renewed ||= signin;
      } else {
        // A session the store holds as it is is not written again, so its change time
        // stands and it is not hashed again (`SESSIONS_SEEN`).
        const stored = safeRead(store, join(store, rel));
        if (stored !== null && digest(stored) === digest(bytes)) {
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

/** A session in an engine's store as last hashed: its identity, and a secret refusal's. */
interface SeenSession {
  readonly size: number;
  readonly mtime_ms: number;
  readonly sha256: string;
  /** The digest of the secrets it was refused against, when it held one's bytes. */
  readonly refused?: string;
}

/** The engines' sessions as last hashed (`SESSIONS_SEEN`), or none. */
function readSeen(): Record<string, SeenSession> {
  try {
    return existsSync(SESSIONS_SEEN) ? readJson<Record<string, SeenSession>>(SESSIONS_SEEN) : {};
  } catch {
    return {};  // unreadable: every session is hashed again, nothing skipped
  }
}

/** The engines' sessions the sync agent took from the feed, into each store it lacks. */
function importSessions(): void {
  const staged = `${SYNC_HOME}/in`;
  for (const rel of safeWalk(staged)) {
    const [engine, ...rest] = rel.split("/");
    const rec = RECORDS.get(engine ?? "");
    const inHome = rest.join("/");
    if (rec === undefined || !isKept(keptPaths(rec).sessions, inHome)) {
      continue;
    }
    const store = join(STORE, rec.id);
    const target = join(store, inHome);
    if (!existsSync(target)) {
      const bytes = safeRead(staged, join(staged, rel));
      if (bytes !== null) {
        safeDirs(TOP, store, 0o700);
        safeWrite(store, target, bytes, 0o600);
      }
    }
  }
}

/** Every secret's atoms, from every engine's store (its `secret_paths` files). */
function atoms(): string[] {
  const found: string[] = [];
  for (const rec of RECORDS.values()) {
    const store = `${STORE}/${rec.id}`;
    for (const rel of safeWalk(store)) {
      if (isKept(keptPaths(rec).secrets, rel)) {
        const bytes = safeRead(store, join(store, rel));
        if (bytes !== null) {
          found.push(...secretAtoms(bytes.toString("utf8")));
        }
      }
    }
  }
  return found;
}

/**
 * Whether the engine is signed in inside this sandbox: its record's `status_argv` (and
 * `status_equals`), or, for a record with none, its `state_checks` over the engine's store.
 */
export async function signedIn(rec: SourceRecord): Promise<boolean> {
  const login = rec.cli?.login as { status_argv?: string[];
                                    status_equals?: Record<string, unknown> } | undefined;
  if (login === undefined) {
    return false;
  }
  if (login.status_argv === undefined) {
    const store = `${STORE}/${rec.id}`;
    return statesHold(login as Record<string, unknown>, (path) => {
      const rel = path.replace(/^\{home\}\//, "");
      return existsSync(store) ? safeRead(store, join(store, rel))?.toString("utf8") ?? null
        : null;
    });
  }
  const name = `check-${rec.id}-${process.pid}`;
  const as = take(ROOT, STATE, name);
  const home = await makeHome(name, rec);
  try {
    giveTree(home, as);
    const argv = asUser(as, [shared()], login.status_argv.map((arg) =>
      arg.replaceAll("{bin}", binOf(rec)).replaceAll("{home}", home)));
    const done = spawnSync(argv[0]!, argv.slice(1), {
      env: engineEnv(rec, home), cwd: home, timeout: COMMAND_MS, encoding: "utf8" });
    if (done.status !== 0) {
      return false;
    }
    if (login.status_equals === undefined) {
      return true;
    }
    try {
      const status = JSON.parse(done.stdout) as Record<string, unknown>;
      return Object.entries(login.status_equals).every(([key, value]) => status[key] === value);
    } catch {
      return false;
    }
  } finally {
    killAll(as.uid);
    dropHome(home);
    give(STATE, as);
  }
}

// -- prepare --------------------------------------------------------------------------

function prepare(servers: readonly string[]): number {
  if (servers.length === 0 || !servers.every((server) => /^[0-9]{1,3}(?:\.[0-9]{1,3}){3}$/
    .test(server))) {
    throw new Error("prepare takes the network's resolvers, IPv4");
  }
  writeFileSync(`${ROOT}/etc/resolv.conf`,
                `${servers.map((server) => `nameserver ${server}`).join("\n")}\n`);
  const done = spawnSync("mount", ["-o", "remount,hidepid=2", "/proc"], { encoding: "utf8" });
  const mounts = readFileSync("/proc/mounts", "utf8");
  if (done.status !== 0 || !/ \/proc proc [^ ]*hidepid=(?:2|invisible)/.test(mounts)) {
    throw new Error(`/proc could not be hidden: ${done.stderr}`);
  }
  lockDown();
  lockSudo();
  // The runner's own room on the disk, kept while there is room for it (`scratch.ts`).
  mkdirSync(STATE, { recursive: true, mode: 0o700 });
  checkFormat(STATE, CACHES);
  dropOtherRunners();
  keepReserve(RESERVE, RESERVE_SIZE);
  // A pasted code is never left at rest (review L1): one a pause or a failed hand-over left
  // in the inbox goes at every boot and resume, before anything else reads it.
  const inbox = `${STATE}/inbox`;
  for (const name of existsSync(inbox) ? readdirSync(inbox) : []) {
    if (name.endsWith(".code")) {
      remove(`${inbox}/${name}`);
    }
  }
  return OK;
}

/**
 * No user but root may `sudo`, checked at every boot: E2B's provisioning puts its default
 * `user` back in group `sudo` and grants it `NOPASSWD: ALL` in `/etc/sudoers` at each boot
 * (probed live: the template's build removed both, and a fresh sandbox had both again). No
 * run's user can become `user` (it has no password, and only the Worker reaches envd), so
 * this is the template's promise kept, not a door closed: `/etc/sudoers` keeps its defaults
 * and root's line and nothing else (no include), the `sudo` group has no member, and each of
 * `user`, `ta-sync` and a pool user is checked refused.
 */
function lockSudo(): void {
  // Its defaults and root's line, and no include of any kind (`@include`, `#include`,
  // their `dir` forms): an included file is one more grant this check would not read.
  const kept = readFileSync("/etc/sudoers", "utf8").split("\n").filter((line) =>
    line.trim() === "" || (/^\s*(#|Defaults|root\s)/.test(line)
                           && !/^\s*[@#]include/.test(line)));
  writeFileSync("/etc/sudoers.ta", `${kept.join("\n").trimEnd()}\n`, { mode: 0o440 });
  const valid = spawnSync("visudo", ["-cf", "/etc/sudoers.ta"], { encoding: "utf8" });
  if (valid.status !== 0) {
    throw new Error(`the sudoers kept are not valid: ${valid.stdout}${valid.stderr}`);
  }
  renameSync("/etc/sudoers.ta", "/etc/sudoers");
  for (const line of readFileSync("/etc/group", "utf8").split("\n")) {
    const [name, , , members] = line.split(":");
    if ((name === "sudo" || name === "admin") && members !== undefined && members !== "") {
      for (const member of members.split(",")) {
        spawnSync("gpasswd", ["-d", member, name], { encoding: "utf8" });
      }
    }
  }
  for (const file of existsSync("/etc/sudoers.d") ? readdirSync("/etc/sudoers.d") : []) {
    rmSync(`/etc/sudoers.d/${file}`, { force: true });
  }
  for (const name of ["user", "ta-sync", `${POOL_PREFIX}0`]) {
    const listed = spawnSync("sudo", ["-l", "-U", name], { encoding: "utf8" });
    if (!/is not allowed to run sudo/.test(listed.stdout)) {
      throw new Error(`${name} may still sudo: ${listed.stdout}`);
    }
  }
}

/**
 * What may stay writable by a user other than root, each for its reason:
 * - `/workspace`, group `ta`: the owner's files every run shares by design;
 * - `/scratch`, group `ta` below its top: the agents' working files (`scratch.ts`), every
 *   run of the sandbox's by design, as the founder's rule has every agent of a user full
 *   power by default;
 * - each run's own home, each conversation's own cwd, the sync agent's directory: their
 *   own users', 0700, reached by no one else;
 * - `/proc` and `/sys`: the kernel's own;
 * and, wherever they are, sticky directories (`/tmp`, `/var/tmp`, `/dev/shm`, `/run/lock`:
 * every user's own entries, none another's), and device, socket and pipe nodes and links
 * (a node is no file root runs or reads as code; a link's own mode means nothing).
 */
const WRITABLE: readonly string[] = Object.freeze(["/proc", "/sys", WORKSPACE, HOMES,
                                                   "/var/lib/ta-conv", "/var/lib/ta-sync",
                                                   SCRATCH]);
/**
 * Every path another user may write made root's alone, then checked: anything left fails
 * `prepare` loudly. E2B's base image boots with root's own programs world-writable
 * (probed: `/usr/bin/envd`, `/usr/local/bin`, systemd's units), and a template build's
 * modes do not survive into the sandbox. A path is writable by another user when others
 * may write it, or group `ta` (every run user's) may. Every mount is walked (`/run`,
 * `/dev`), at every start and resume.
 */
function lockDown(): void {
  const ta = String(shared());
  const prune = WRITABLE.flatMap((path, i) => [...(i === 0 ? [] : ["-o"]), "-path", path]);
  const find = (action: readonly string[]) => spawnSync("find", [
    "/", "(", ...prune, ")", "-prune", "-o", "!", "-type", "l", "!", "-type", "c", "!",
    "-type", "b", "!", "-type", "s", "!", "-type", "p", "(", "(", "-perm", "-0002", "!",
    "-perm", "-1000", ")", "-o", "(", "-perm", "-0020", "-group", ta, ")", ")", ...action],
  { encoding: "utf8", maxBuffer: Number.POSITIVE_INFINITY });
  find(["-exec", "chmod", "go-w", "{}", "+"]);
  const left = find(["-print"]);
  if (left.status !== 0 || left.stdout.trim() !== "") {
    throw new Error(`writable paths remain: ${left.stdout}`);
  }
}

/** The digest the loader ran this runner by, or null (not run by the loader: a test's). */
function ownDigest(): string | null {
  const own = process.env["TA_RUNNER_DIGEST"];
  return own !== undefined && /^[0-9a-f]{64}$/.test(own) ? own : null;
}

/**
 * Every other runner's bytes gone from `/opt/ta/runner`, at `prepare`, only while nothing
 * lives (a live run's children start again from their own bytes) and only by the runner the
 * loader names, which the sandbox's row already names (the Worker commits it before it runs
 * this runner's `prepare`): a sandbox keeps the one it runs, and no row ever names bytes
 * that are gone.
 */
function dropOtherRunners(): void {
  if (liveRuns().length + liveLogins().length === 0) {
    dropRunners(RUNNERS, ownDigest());
  }
}

/**
 * This runner to be made the sandbox's own (`adopt`, by the sandbox Worker, which ships it,
 * only when nothing lives: no run, no sign-in, as `state` saw): the disk held to its format
 * (`checkFormat`), changing nothing else. Refused (`NO`) while anything lives, whose runner
 * must stay. The other runners' bytes go at this runner's `prepare`, once the row names it.
 */
function adopt(): number {
  if (ownDigest() === null) {
    throw new Error("adopt is run by the loader, which names the runner's digest");
  }
  if (liveRuns().length + liveLogins().length > 0) {
    process.stderr.write("ta-runner: a run or a sign-in lives; its runner stays\n");
    return NO;
  }
  mkdirSync(STATE, { recursive: true, mode: 0o700 });
  checkFormat(STATE, CACHES);
  return OK;
}

// -- launch, cancel, status, state, renewal ------------------------------------------

function launch(run: string): number {
  const dir = runDir(run);
  const inbox = `${STATE}/inbox/${run}.json`;
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  renameSync(inbox, `${dir}/start.json`);
  return detach(run, []);
}

function detach(run: string, extra: readonly string[]): number {
  const dir = runDir(run);
  const log = openSync(`${dir}/runner.log`, "a", 0o600);
  const child = spawn(process.execPath, [...process.execArgv, process.argv[1]!, "run", run,
                                         ...extra],
                      { detached: true, stdio: ["ignore", log, log], env: process.env });
  closeSync(log);
  child.unref();
  writeJson(`${dir}/state.json`, { ...(readState(run) ?? { seq: 0, session_id: null,
                                                            last_kind: null, cli_pid: null }),
                                   pid: child.pid, ended: false, renewing: false });
  return OK;
}

function cancel(run: string): number {
  const state = readState(run);
  if (state === null || state.ended || !alive(state.pid)) {
    return NO;
  }
  writeFileSync(`${runDir(run)}/cancel`, "");
  process.kill(state.pid, "SIGTERM");
  return OK;
}

function status(run: string): number {
  const state = readState(run);
  return state !== null && !state.ended && alive(state.pid) ? OK : NO;
}

/**
 * Whether a run's CLI is between turns, as its events show: none running (its runner is
 * syncing, or between a renewal's resume and its CLI's), or its last event a tool call, a
 * result or a usage report (its model call answered). A CLI that is never quiet is renewed
 * at the ceiling's edge regardless.
 */
function quiet(state: RunState): boolean {
  return !alive(state.cli_pid) || state.last_kind === "tool_call"
    || state.last_kind === "result" || state.last_kind === "usage";
}

function stateCommand(): number {
  const live = liveRuns();
  // A sign-in in progress keeps its sandbox from the idle reap; it is quiet (no model call).
  process.stdout.write(JSON.stringify({ live: live.length + liveLogins().length,
                                        quiet: live.every(({ state }) => quiet(state)),
                                        protocol: PROTOCOL }));
  return OK;
}

/** Each live runner signalled, and its ack waited for while its runner lives. */
async function signalled(signal: "SIGUSR1" | "SIGUSR2", ack: string,
                         deadline: number): Promise<void> {
  const live = liveRuns();
  for (const { run, state } of live) {
    rmSync(`${runDir(run)}/${ack}`, { force: true });
    process.kill(state.pid, signal);
  }
  while (Date.now() < deadline && !live.every(({ run, state }) =>
    existsSync(`${runDir(run)}/${ack}`) || !alive(state.pid))) {
    await new Promise((resolve) => setTimeout(resolve, FLUSH_MS / 5));
  }
}

async function quietWait(until: number): Promise<number> {
  if (!Number.isSafeInteger(until)) {
    throw new Error("quiet-wait takes its end, in ms");
  }
  for (;;) {
    const live = liveRuns();
    const still = live.every(({ state }) => quiet(state));
    if (live.length === 0 || still || Date.now() >= until) {
      process.stdout.write(JSON.stringify({ live: live.length, quiet: still }));
      return OK;
    }
    await new Promise((resolve) => setTimeout(resolve, FLUSH_MS));
  }
}

async function renewWait(deadline: number): Promise<number> {
  if (!Number.isSafeInteger(deadline)) {
    throw new Error("renew-wait takes its deadline, in ms");
  }
  for (;;) {
    const live = liveRuns();
    if (live.length === 0) {
      process.stdout.write(JSON.stringify({ live: 0 }));
      return OK;
    }
    if (live.every(({ state }) => quiet(state)) || Date.now() >= deadline) {
      await signalled("SIGUSR1", "renewing-ack", deadline);
      process.stdout.write(JSON.stringify({ live: live.length }));
      return OK;
    }
    await new Promise((resolve) => setTimeout(resolve, FLUSH_MS));
  }
}

async function recover(): Promise<number> {
  const dir = `${STATE}/runs`;
  for (const run of existsSync(dir) ? readdirSync(dir).filter((each) => RUN.test(each)) : []) {
    const state = readState(run);
    if (state !== null && !state.ended && !alive(state.pid)) {
      // The rebooted run's home: its kept paths into the store first (its processes died
      // with the boot), so its session resumes from where it was.
      const start = readJson<StartDoc>(`${runDir(run)}/start.json`);
      const home = `${ROOT}${HOMES}/${run}`;
      if (existsSync(home)) {
        await keepHome(home, record(start.engine));
        dropHome(home);
      }
      detach(run, ["--recover"]);
    }
  }
  return OK;
}

// -- the sync agent, as its own user ---------------------------------------------------

interface LeaseFile {
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly authority: string;
  readonly tokens: { readonly runner_token: string; readonly sync_token: string };
}

interface SyncJob {
  /** In, out, or `known`: the mirror's hash of each of `paths`, read under the lock. */
  readonly direction: "in" | "out" | "known";
  readonly origin: string;
  readonly token: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly authority: string;
  readonly agent: string;
  readonly engine: string;
  readonly run: string;
  readonly atoms: readonly string[];
  readonly paths?: readonly string[];
}

/**
 * The sync agent run as `ta-sync`, its job on stdin; its report, or its refusal. It runs
 * as a child the runner waits on without blocking, so the run's heartbeat goes on posting
 * (each post renews the lease) however long a sync takes.
 */
async function syncAgent(job: SyncJob):
    Promise<{ report: Record<string, unknown> | null; code: number }> {
  const as = user(ROOT, "ta-sync");
  const argv = asUser(as, [shared()], [process.execPath, ...process.execArgv,
                                       process.argv[1]!, "sync"]);
  const env: Record<string, string> = {};
  for (const name of [...RUN_BASICS, "TA_RUNNER_ROOT"]) {
    if (process.env[name] !== undefined) {
      env[name] = process.env[name]!;
    }
  }
  const child = spawn(argv[0]!, argv.slice(1), { env, stdio: ["pipe", "pipe", "pipe"] });
  const out: Buffer[] = [];
  const err: Buffer[] = [];
  child.stdout!.on("data", (chunk: Buffer) => { out.push(chunk); });
  child.stderr!.on("data", (chunk: Buffer) => { err.push(chunk); });
  // A child that dies before reading its whole job closes its stdin under the write: the
  // write's EPIPE is let go here, and the child's exit status says what happened.
  child.stdin!.on("error", () => undefined);
  child.stdin!.end(JSON.stringify(job));
  const status = await new Promise<number | null>((resolve, reject) => {
    child.on("error", reject);
    child.on("close", (code) => { resolve(code); });
  });
  // Its report whole, however many paths.
  const done = { status, stdout: Buffer.concat(out).toString("utf8"),
                 stderr: Buffer.concat(err).toString("utf8") };
  let report: Record<string, unknown> | null = null;
  try {
    report = JSON.parse(done.stdout) as Record<string, unknown>;
  } catch {
    report = null;
  }
  if (done.status !== OK && done.status !== FENCED) {
    throw new Error(`the sync agent failed (${done.status}): ${done.stderr}`);
  }
  return { report, code: done.status };
}

/** The sync agent's own side (as `ta-sync`), one at a time in the sandbox by its lock. */
async function syncCommand(): Promise<number> {
  const job = JSON.parse(readFileSync(0, "utf8")) as SyncJob;
  const lock = `${SYNC_HOME}/lock`;
  for (let wait = FLUSH_MS / 10; ; wait = Math.min(wait * 2, FLUSH_MS * 4)) {
    try {
      mkdirSync(lock, { mode: 0o700 });
      writeFileSync(`${lock}/pid`, String(process.pid));
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
        throw error;
      }
      let holder = 0;
      try {
        holder = Number(readFileSync(`${lock}/pid`, "utf8"));
      } catch {
        holder = 0;
      }
      if (holder !== 0 && !alive(holder)) {
        remove(lock);  // its holder died
        continue;
      }
      await new Promise((resolve) => setTimeout(resolve, wait));
    }
  }
  try {
    const client = new SyncClient(job.origin, job.token, FLUSH_MS, HEARTBEAT_MS);
    const place = { local: LOCAL_WORKSPACE, sessionsIn: `${SYNC_HOME}/in`,
                    sessionsOut: `${SYNC_HOME}/out/${job.run}`, authority: job.authority,
                    records: RECORDS };
    safeDirs(SYNC_HOME, place.sessionsIn, 0o700);
    // The mirror is a database on disk, written as each change lands (`openMirror`): a sync
    // cut short keeps what it sent.
    const mirror = openMirror(`${SYNC_HOME}/mirror.db`);
    try {
      if (job.direction === "known") {
        // What the owner's files hold of each path, as this sandbox last synced it.
        process.stdout.write(JSON.stringify({ known: Object.fromEntries((job.paths ?? [])
          .map((path) => [path, mirror.get(path)?.sha256 ?? null])) }));
        return OK;
      }
      if (job.direction === "in") {
        await syncIn(client, place, mirror);
        const projected = `${SYNC_HOME}/projection-${job.agent}.json`;
        const previous = existsSync(projected) ? readJson<string[]>(projected) : [];
        const made = project(LOCAL_WORKSPACE, job.agent, record(job.engine).cli!.skills_paths,
                             previous);
        safeWrite(SYNC_HOME, projected, new TextEncoder().encode(JSON.stringify(made)), 0o600);
        process.stdout.write(JSON.stringify({ ok: true }));
        return OK;
      }
      const report = await syncOut(client, place, mirror, job.lease, job.atoms);
      process.stdout.write(JSON.stringify(report));
      return OK;
    } finally {
      mirror.close();
    }
  } catch (error) {
    if (error instanceof SyncFenced) {
      process.stdout.write(JSON.stringify({ fenced: true }));
      return FENCED;
    }
    throw error;
  } finally {
    remove(lock);
  }
}

/** How long matching the sync-ignore list may take before the run's report says so. */
const SLOW_MATCH_MS = 5_000;

/** The paths an event names at most: past them, its count says how many more. */
const EVENT_PATHS = 100;

/**
 * What a sync-out reported, to the run's events: each refused file, a full storage's (and
 * open uploads') by its code alone; the one `ta.storage_full` with the object's whole
 * message, once a sync, the only place an upgrade is offered, what did not fit kept in the
 * sandbox and sent again by the next run; `ta.uploads_open` likewise, never an upgrade; what
 * was skipped (changed or gone while the sync ran) once; the conflicts.
 */
function reportSync(outbox: Outbox, report: SyncReport | null): void {
  const refused = report?.refused ?? [];
  const codeOf = (reason: string) => /^(storage_full|uploads_open):/.exec(reason)?.[1] ?? null;
  for (const each of refused) {
    outbox.push({ type: "ta.sync_refused", path: each.path,
                  reason: codeOf(each.reason) ?? each.reason });
  }
  for (const code of ["storage_full", "uploads_open"] as const) {
    const these = refused.filter((each) => codeOf(each.reason) === code);
    if (these.length > 0) {
      outbox.push({ type: `ta.${code}`, message: these[0]!.reason, files: these.length,
                    kept_in_sandbox: these.slice(0, EVENT_PATHS).map((each) => each.path) });
    }
  }
  const skipped = report?.skipped ?? [];
  if (skipped.length > 0) {
    outbox.push({ type: "ta.sync_skipped",
                  files: skipped.length + (report?.skipped_unlisted ?? 0),
                  skipped: skipped.slice(0, EVENT_PATHS) });
  }
  if ((report?.conflicts ?? []).length > 0) {
    outbox.push({ type: "ta.sync_conflicts", paths: report!.conflicts });
  }
  // Lines past a ceiling are not applied, never in silence: which, how many, and why.
  const unapplied = report?.unapplied ?? [];
  const past = report?.list_bytes_past ?? 0;
  if ((report?.unapplied_count ?? 0) > 0 || past > 0) {
    const states = unapplied.filter((each) => each.reason === "states").length;
    const said = [
      states === 0 ? null : `${states} line${states === 1 ? "" : "s"} past the ${
        AUTOMATON_STATES.toLocaleString("en")} automaton states the lines with *, ? or [ ] ` +
        "may take together",
      past === 0 && unapplied.every((each) => each.reason !== "bytes") ? null
        : `the list past its ${LIST_BYTES.toLocaleString("en")} bytes (${
          past.toLocaleString("en")} more bytes)`,
    ].filter((each) => each !== null);
    outbox.push({ type: "ta.sync_ignore_unapplied", lines: unapplied,
                  count: report?.unapplied_count ?? 0, list_bytes_past: past,
                  message: `In /workspace/.ta/sync-ignore, ${said.join(", and ")} ${
                    said.length === 1 && states === 1 ? "is" : "are"} not applied, in file ` +
                    "order: the files they name go on syncing." });
  }
  // Names that are not UTF-8 never sync (a path is text at the owner's files): said, with
  // their bytes escaped, never silently missed; the object makes it the owner's notice.
  if ((report?.unnamed_count ?? 0) > 0) {
    const count = report!.unnamed_count;
    outbox.push({ type: "ta.sync_unnamed", paths: report!.unnamed, count,
                  message: `${count} file${count === 1 ? "" : "s"} or folder${
                    count === 1 ? "" : "s"} in your sandbox ${count === 1 ? "has a name" :
                    "have names"} that ${count === 1 ? "is" : "are"} not UTF-8, so ${
                    count === 1 ? "it does" : "they do"} not sync to your files: ${
                    report!.unnamed.slice(0, 5).join(", ")}${count > 5 ? ", …" : ""}. ` +
                    "Rename them to sync them." });
  }
  // A slow list says so, so the owner sees why a sync took long: its pattern lines (`*`,
  // `?`) cost the most. Said past `SLOW_MATCH_MS`, which refuses nothing.
  if ((report?.match_ms ?? 0) >= SLOW_MATCH_MS) {
    outbox.push({ type: "ta.sync_ignore_slow", match_ms: report!.match_ms,
                  message: `Matching /workspace/.ta/sync-ignore took ${
                    Math.round(report!.match_ms / 1000)} s of this sync; its lines with * or ? ` +
                    "cost the most." });
  }
}

// -- a run ----------------------------------------------------------------------------

/** The run's posts to its event door: one in flight, held during a renewal's pause. */
class Poster {
  holding = false;
  #last = 0;
  #busy = false;
  #wait = FLUSH_MS;
  #refused: string | null = null;

  constructor(readonly url: string, readonly token: string, readonly outbox: Outbox,
              readonly onRefused: (why: string) => void) {}

  get refused(): string | null {
    return this.#refused;
  }

  /**
   * One post of the next frames, with `end` when they are the last ones: `taken`, `retry`
   * (the network, or `events_busy` after its `Retry-After`, a retry's wait doubling to at
   * most a heartbeat), or `refused` (the object ended the run: cancelled, revoked, settled,
   * its lease closed).
   */
  async #post(end: { exit_code: number } | null): Promise<"taken" | "retry" | "refused"> {
    const frames = this.outbox.peek();
    const last = end !== null && frames.length === this.outbox.pending;
    let response: Response;
    try {
      response = await fetch(this.url, {
        method: "POST",
        headers: { "content-type": "application/json", "x-ta-runner": this.token },
        body: JSON.stringify({ frames, ...(last ? { end } : {}) }) });
    } catch {
      await new Promise((resolve) => setTimeout(resolve, this.#wait));
      return "retry";
    }
    const text = await response.text();
    if (response.status === 200) {
      this.outbox.taken(frames);
      this.#last = Date.now();
      return "taken";
    }
    if (response.status === 503 || response.status === 429) {
      const after = Number(response.headers.get("retry-after") ?? "0") * 1000;
      await new Promise((resolve) => setTimeout(resolve, Math.min(HEARTBEAT_MS,
        Number.isFinite(after) && after > 0 ? after : this.#wait)));
      return "retry";
    }
    if (response.status === 401 || response.status === 403 || response.status === 404) {
      this.#refused = text;
      this.onRefused(this.#refused);
      return "refused";
    }
    await new Promise((resolve) => setTimeout(resolve, this.#wait));
    return "retry";
  }

  async #once(end: { exit_code: number } | null): Promise<"taken" | "retry" | "refused"> {
    this.#busy = true;
    try {
      const answer = await this.#post(end);
      this.#wait = answer === "retry" ? Math.min(this.#wait * 2, HEARTBEAT_MS) : FLUSH_MS;
      return answer;
    } finally {
      this.#busy = false;
    }
  }

  /** Post whatever waits, and a heartbeat when nothing went for `HEARTBEAT_MS`. */
  async tick(): Promise<void> {
    if (this.holding || this.#refused !== null || this.#busy) {
      return;
    }
    if (this.outbox.pending > 0 || Date.now() - this.#last >= HEARTBEAT_MS) {
      await this.#once(null);
    }
  }

  /** The post in flight finished, and every frame that waits posted (or refused). */
  async drain(): Promise<void> {
    while (this.#busy) {
      await new Promise((resolve) => setTimeout(resolve, FLUSH_MS / 5));
    }
    while (this.outbox.pending > 0 && this.#refused === null) {
      await this.#once(null);
    }
  }

  /** Every frame, then the end with the last of them, until taken or refused. */
  async finish(exitCode: number): Promise<void> {
    for (;;) {
      if (this.#refused !== null) {
        return;
      }
      if (this.holding || this.#busy) {
        await new Promise((resolve) => setTimeout(resolve, FLUSH_MS));
        continue;
      }
      const last = this.outbox.peek().length === this.outbox.pending;
      if (await this.#once({ exit_code: exitCode }) === "taken" && last) {
        return;
      }
    }
  }
}

class RunRefused extends Error {
  override readonly name = "RunRefused";
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

/** The env files a CLI would load: its home's, its cwd's, and each ancestor's of its cwd. */
function envFiles(home: string, cwd: string): { home: Map<string, string>;
                                                 project: Map<string, string>;
                                                 above: Map<string, string> } {
  const read = (base: string, rel: string) => safeRead(base, join(base, rel))?.toString("utf8");
  const out = { home: new Map<string, string>(), project: new Map<string, string>(),
                above: new Map<string, string>() };
  for (const rel of safeWalk(home)) {
    const text = homeEnvFile(rel) === null ? undefined : read(home, rel);
    if (text !== undefined) {
      out.home.set(rel, text);
    }
  }
  const depth = (rel: string) => {
    const parts = rel.split("/");
    return parts.length > 2 || (parts.length === 2 && !parts[0]!.startsWith("."))
      || (parts.length === 1 && !parts[0]!.startsWith("."));
  };
  for (const rel of safeWalk(cwd, depth)) {
    const text = projectEnvFile(rel) === null ? undefined : read(cwd, rel);
    if (text !== undefined) {
      out.project.set(rel, text);
    }
  }
  for (let dir = dirname(cwd); ; dir = dirname(dir)) {
    for (const rel of safeWalk(dir, depth)) {
      const text = ancestorEnvFile(rel) === null ? undefined : read(dir, rel);
      if (text !== undefined) {
        out.above.set(`${dir}/${rel}`, text);
      }
    }
    if (dir === dirname(dir) || dir === TOP) {
      break;
    }
  }
  return out;
}

/** Files a run's user left in the shared scratch directories, removed (links as links). */
function cleanScratch(uid: number): void {
  for (const dir of ["/tmp", "/var/tmp", "/dev/shm"]) {
    spawnSync("find", [`${ROOT}${dir}`, "-xdev", "-mindepth", "1", "-user", String(uid),
                       "-delete"], { stdio: "ignore" });
  }
}

/**
 * The run's agent's scratch (`scratch.ts`): made (root's at the top, group `ta`'s below), old
 * scratch let go first (unused past the control record's retention, then least recently used
 * while the disk is low, never a live run's agent), and the agent's marked used now. What
 * went, in plain words for the agent and the user, or null.
 */
function scratchFor(start: StartDoc, run: string): { dir: string; notice: string | null } {
  const dir = `${LOCAL_SCRATCH}/${start.agent}`;
  safeDirs(TOP, LOCAL_SCRATCH, 0o755);
  const live = new Set([start.agent, ...liveRuns().filter((each) => each.run !== run)
    .flatMap((each) => {
      try {
        return [readJson<StartDoc>(`${runDir(each.run)}/start.json`).agent];
      } catch {
        return [];
      }
    })]);
  const index = readIndex(SCRATCH_INDEX);
  const pruned = pruneScratch(LOCAL_SCRATCH, index, Date.now(),
                              start.scratch_retention_days ?? null, live);
  safeDirs(LOCAL_SCRATCH, dir, 0o2775, { uid: 0, gid: shared() });
  writeOrRelease(() => markUsed(index, start.agent, Date.now()), RESERVE);
  keepReserve(RESERVE, RESERVE_SIZE);
  return { dir, notice: scratchNotice(pruned) };
}

async function runCommand(run: string, recovering: boolean): Promise<number> {
  const dir = runDir(run);
  const start = readJson<StartDoc>(`${dir}/start.json`);
  const lease = readJson<LeaseFile>(`${STATE}/lease.json`);
  const rec = record(start.engine);
  const cli = rec.cli!;
  const prior = readState(run);
  const state: RunState = { pid: process.pid, cli_pid: null, seq: prior?.seq ?? 0,
                            session_id: prior?.session_id ?? start.session_id,
                            last_kind: null, ended: false, renewing: false };
  // A full disk never stops the runner: its reserve goes first, and a write that still
  // fails leaves the state in memory, the run going on and reporting its end.
  const save = () => {
    writeOrRelease(() => writeJson(`${dir}/state.json`, state), RESERVE);
  };
  const mark = (name: string) => {
    writeOrRelease(() => writeFileSync(`${dir}/${name}`, ""), RESERVE);
  };
  const outbox = new Outbox(state.seq);
  const cli_: { child: ChildProcess | null; kill: ReturnType<typeof setTimeout> | null;
                stopped: boolean; renewed: boolean } =
    { child: null, kill: null, stopped: false, renewed: false };
  // SIGTERM to the CLI's process group, then a kill after its grace.
  const stop = () => {
    cli_.stopped = true;
    const child = cli_.child;
    if (child !== null && child.exitCode === null && child.pid !== undefined) {
      const pid = child.pid;
      try {
        process.kill(-pid, "SIGTERM");
      } catch {
        child.kill("SIGTERM");
      }
      cli_.kill ??= setTimeout(() => {
        try {
          process.kill(-pid, "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
      }, CANCEL_GRACE_MS);
    }
  };
  const poster = new Poster(start.urls.events, lease.tokens.runner_token, outbox, stop);
  // The signals first, so none arriving early takes its default (ending the runner).
  process.on("SIGTERM", stop);
  process.on("SIGUSR1", () => {
    outbox.push({ type: "ta.renewing" });
    // Held at once, so no post starts; the one in flight and what waits are posted, then
    // the ack: nothing is in flight when the sandbox pauses.
    poster.holding = true;
    void (async () => {
      await poster.drain();
      state.renewing = true;
      save();
      mark("renewing-ack");
    })();
  });
  process.on("SIGUSR2", () => {
    poster.holding = false;
    state.renewing = false;
    cli_.renewed = cli_.child !== null && cli_.child.exitCode === null;
    outbox.push({ type: "ta.renewed" });
    save();
    mark("renewed-ack");
  });
  if (existsSync(`${dir}/cancel`)) {
    stop();  // cancelled before its runner was up
  }
  // The run's first heartbeat, before anything slow: a run with no post within the lease's
  // TTL of its admission is settled lost.
  await poster.tick();
  const pump = setInterval(() => {
    void poster.tick();
  }, FLUSH_MS);
  let exitCode = 1;
  let as: Identity | null = null;
  let home: string | null = null;
  const system = `${ROOT}${SYSTEM}/${run}`;
  const job = (direction: SyncJob["direction"], secrets: readonly string[] = []): SyncJob => ({
    direction, origin: new URL(start.urls.events).origin, token: lease.tokens.sync_token,
    lease: lease.lease, authority: start.authority, agent: start.agent, engine: start.engine,
    run, atoms: secrets });
  let synced = false;
  try {
    if (recovering) {
      outbox.push({ type: "ta.recovered" });
    }
    await reportSignins(start, lease);
    if ((await syncAgent(job("in"))).code === FENCED) {
      throw new SyncFenced("the sync door fenced the lease");
    }
    synced = true;
    importSessions();
    as = take(ROOT, STATE, run);
    home = await makeHome(run, rec, start.credential === "vendor_login_in_sandbox");
    safeDirs(TOP, `${ROOT}${SYSTEM}`, 0o755);
    remove(system);
    mkdirSync(system, { mode: 0o750 });
    chownSync(system, 0, as.gid);
    const agentDir = `${ROOT}${AGENTS_DIR}/${start.agent}`;
    if (!existsSync(`${agentDir}/agent.json`)) {
      throw new Error(`the agent ${start.agent} has no definition in the sandbox`);
    }
    const scratch = scratchFor(start, run);
    if (scratch.notice !== null) {
      outbox.push({ type: "ta.scratch_cleared", text: scratch.notice });
    }
    let conversationDir: string | undefined;
    let continued = false;
    if (cli.engine.resume === "continue_in_cwd") {
      conversationDir = `${CONVERSATIONS}/${rec.id}/${start.conversation}`;
      continued = existsSync(conversationDir);
      safeDirs(TOP, conversationDir, 0o700);
      lchownSync(conversationDir, as.uid, as.gid);
    }
    let call = invocation(rec, start, process.env, {
      continued, root: ROOT, home, system,
      ...(conversationDir === undefined ? {} : { conversationDir }),
      ...(recovering ? { prompt: CONTINUE_PROMPT, resume: true } : {}) });
    if (recovering && !call.resumed) {
      throw new RunRefused("recover_unresumable: the run's session is unknown, so the " +
                           "rebooted sandbox cannot continue it");
    }
    // The env files the CLI would load: never one that sets what the run's own
    // environment does not take.
    const forbid = cli.per_credential?.[start.credential]?.forbid_env ?? [];
    const found = envFiles(home, call.cwd);
    const refusal = projectEnvRefusal(found.home, forbid, homeEnvFile)
      ?? projectEnvRefusal(found.project, forbid)
      ?? projectEnvRefusal(found.above, forbid, () => "dotenv");
    if (refusal !== null) {
      outbox.push({ type: "ta.refused", reason: refusal });
      throw new RunRefused(refusal);
    }
    for (const [path, text] of call.files) {
      const inSystem = path.startsWith(`${system}/`);
      safeWrite(inSystem ? system : home, path, new TextEncoder().encode(text),
                inSystem ? 0o640 : 0o644, undefined, inSystem ? 0o750 : 0o755);
    }
    giveTree(home, as);
    configOwned(rec, home, system, call.files.keys(), as);
    const events = cli.engine.events;
    for (let attempt = 0; ; attempt += 1) {
      cli_.renewed = false;
      const argv = asUser(as, [shared()], call.argv);
      const runHome = home;
      exitCode = await new Promise<number>((resolve, reject) => {
        if (cli_.stopped) {
          resolve(128 + 15);
          return;
        }
        process.umask(0o002);
        const child = spawn(argv[0]!, argv.slice(1), {
          cwd: call.cwd, env: { ...call.env, TMPDIR: join(runHome, "tmp"),
                                ...scratchEnv(scratch.dir) }, detached: true,
          stdio: [call.stdin === null ? "ignore" : "pipe", "pipe", "pipe"] });
        cli_.child = child;
        child.on("error", reject);
        state.cli_pid = child.pid ?? null;
        save();
        if (call.stdin !== null) {
          child.stdin!.end(call.stdin);
        }
        let stderr = "";
        child.stderr!.on("data", (data: Buffer) => {
          // Its tail, as much as one frame shows (`FRAME_UNITS_MAX`; the frame cuts the rest).
          stderr = (stderr + data.toString("utf8")).slice(-FRAME_UNITS_MAX);
        });
        createInterface({ input: child.stdout! }).on("line", (line) => {
          if (line.trim() === "") {
            return;
          }
          const event = lineEvent(line);
          outbox.push(event);
          const read = readEvent(events, event);
          if (read.session_id !== null) {
            state.session_id = read.session_id;
          }
          state.last_kind = read.kind;
          state.seq = outbox.seq;
          save();
        });
        child.on("close", (code, signal) => {
          if (cli_.kill !== null) {
            clearTimeout(cli_.kill);
            cli_.kill = null;
          }
          if (code !== 0 && stderr !== "") {
            outbox.push({ type: "ta.stderr", text: stderr });
          }
          resolve(code ?? (signal === null ? 1 : 128 + 15));
        });
      });
      state.cli_pid = null;
      save();
      // A CLI that failed across a renewal (its model call cut by the pause) is resumed
      // from its session, once; a cancel never is.
      if (exitCode === 0 || attempt > 0 || cli_.stopped || existsSync(`${dir}/cancel`)
          || !cli_.renewed) {
        break;
      }
      killAll(as.uid);
      call = invocation(rec, { ...start, session_id: state.session_id }, process.env, {
        continued: true, root: ROOT, home, system, prompt: CONTINUE_PROMPT,
        ...(conversationDir === undefined ? {} : { conversationDir }) });
      if (!call.resumed) {
        break;
      }
      outbox.push({ type: "ta.resumed", after: "renewal" });
    }
  } catch (error) {
    if (error instanceof SyncFenced) {
      outbox.push({ type: "ta.sync_fenced" });
    } else if (!(error instanceof RunRefused)) {
      outbox.push({ type: "ta.runner_error", message: String(error) });
    }
    exitCode = exitCode === 0 ? 1 : exitCode;
  } finally {
    try {
      // A full disk: ext4 keeps root's own blocks (probed: root still wrote at 0 free), but
      // what follows copies the run's changed sessions back and stages the unsent ones, and
      // the sync agent is its own user, so the runner's reserve goes first when the disk is
      // short of it, and both have their room (`scratch.ts`).
      if (diskOf(STATE).free_bytes < RESERVE_SIZE) {
        rmSync(RESERVE, { force: true });
      }
      if (as !== null) {
        // Every process the run's user left is gone before its home is read.
        killAll(as.uid);
        cleanScratch(as.uid);
        if (home !== null) {
          await keepHome(home, rec);
        }
      }
      if (synced) {
        const secrets = atoms();
        const out = `${SYNC_HOME}/out/${run}`;
        remove(out);
        // Every engine's session the owner's files do not hold as it is now (the sync
        // mirror's hash of its path, read under the sync agent's lock) is staged: this run's,
        // and any an earlier sync-out left unsent (refused at a full storage, changed while
        // sent, fenced, cut short), so each is offered again until it lands, whether or not
        // a later run touches it. A session the files hold already is never copied.
        // A session's hash is read again only when its size or change time moved since it
        // was last hashed (or that time was too recent to trust, `RACY_MS`); one refused for
        // a secret's bytes is said once, until it or the secrets change.
        const seen = readSeen();
        const next: Record<string, SeenSession> = {};
        const against = digest(new TextEncoder().encode(JSON.stringify([...secrets].sort())));
        const sessions: { rec: SourceRecord; rel: string; path: string; digest: string }[] = [];
        for (const each of RECORDS.values()) {
          const store = `${STORE}/${each.id}`;
          for (const rel of walkFiles(store)) {
            if (!isKept(keptPaths(each).sessions, rel)) {
              continue;
            }
            const path = `${SESSIONS}/${start.authority}/${each.id}/${rel}`;
            const at = Date.now();
            const stat = lstatSync(join(store, rel));
            const was = seen[path];
            let sha = was !== undefined && was.size === stat.size && was.mtime_ms === stat.mtimeMs
              && stat.mtimeMs < at - RACY_MS ? was.sha256 : null;
            if (sha === null) {
              const bytes = safeRead(store, join(store, rel));
              if (bytes === null) {
                continue;
              }
              sha = digest(bytes);
            }
            next[path] = { size: stat.size, mtime_ms: stat.mtimeMs, sha256: sha,
                           ...(was?.sha256 === sha && was.refused !== undefined
                             ? { refused: was.refused } : {}) };
            sessions.push({ rec: each, rel, digest: sha, path });
          }
        }
        const held = sessions.length === 0 ? {}
          : ((await syncAgent({ ...job("known"), paths: sessions.map((each) => each.path) }))
            .report?.["known"] ?? {}) as Record<string, string | null>;
        for (const session of sessions) {
          if (held[session.path] === session.digest
              || next[session.path]!.refused === against) {
            continue;
          }
          const store = `${STORE}/${session.rec.id}`;
          const bytes = safeRead(store, join(store, session.rel));
          if (bytes === null) {
            continue;
          }
          if (heldSecret(bytes.toString("latin1"), secrets) !== null) {
            outbox.push({ type: "ta.sync_refused", path: `{home}/${session.rel}`,
                          reason: "holds a secret's bytes" });
            next[session.path] = { ...next[session.path]!, refused: against };
            continue;
          }
          safeWrite(SYNC_HOME, join(out, session.rec.id, session.rel), bytes, 0o644, undefined,
                    0o755);
        }
        safeWrite(STATE, SESSIONS_SEEN, new TextEncoder().encode(JSON.stringify(next)), 0o600);
        const done = await syncAgent(job("out", secrets));
        remove(out);
        if (done.code === FENCED) {
          outbox.push({ type: "ta.sync_fenced" });
        } else {
          reportSync(outbox, done.report as SyncReport | null);
        }
      }
    } catch (error) {
      outbox.push({ type: "ta.runner_error", message: String(error) });
      exitCode = exitCode === 0 ? 1 : exitCode;
    } finally {
      if (home !== null) {
        dropHome(home);
      }
      remove(system);
      if (as !== null) {
        give(STATE, as);
      }
      // The heartbeat went on through sync-out, so a long sync kept its lease.
      clearInterval(pump);
      await poster.finish(exitCode);
      state.ended = true;
      state.seq = outbox.seq;
      save();
    }
  }
  return OK;
}

async function reportSignins(start: StartDoc, lease: LeaseFile): Promise<void> {
  const signins: { engine: string; signed_in: boolean }[] = [];
  for (const rec of RECORDS.values()) {
    if (rec.unwired === undefined && rec.cli!.credential.includes("vendor_login_in_sandbox")
        && rec.cli!.login !== undefined) {
      signins.push({ engine: rec.id, signed_in: await safeSignedIn(rec) });
    }
  }
  await postSignins(start.urls.signin, lease, signins);
}

/** The engines' presence, posted to the sign-in report door (presence only, no credential). */
async function postSignins(url: string, lease: LeaseFile,
                           signins: readonly { engine: string; signed_in: boolean }[]):
    Promise<void> {
  try {
    await fetch(url, {
      method: "POST", body: JSON.stringify({ signins }),
      headers: { "content-type": "application/json", "x-ta-runner": lease.tokens.runner_token } });
  } catch {
    // a report that did not land is made again at the next start
  }
}

async function safeSignedIn(rec: SourceRecord): Promise<boolean> {
  try {
    return await signedIn(rec);
  } catch {
    return false;
  }
}


// -- the vendor's own sign-in (slice SIGNIN) ------------------------------------------

const SIGNIN = /^si_[0-9a-f]{32}$/;

function signinDir(id: string): string {
  if (!SIGNIN.test(id)) {
    throw new Error("a sign-in id is si_ and 32 hex");
  }
  return `${STATE}/signin/${id}`;
}

/** A sign-in's document, as the sandbox Worker leaves it: no secret. */
interface SigninDoc {
  readonly id: string;
  readonly engine: string;
  readonly region: string | null;
  readonly mode: "in" | "out";
  readonly authority: string;
  readonly urls: { readonly events: string; readonly signin: string };
}

function loginState(id: string): { pid: number; ended: boolean } | null {
  const path = `${signinDir(id)}/state.json`;
  return existsSync(path) ? readJson<{ pid: number; ended: boolean }>(path) : null;
}

/** The sign-ins whose runners still run. */
function liveLogins(): string[] {
  const dir = `${STATE}/signin`;
  if (!existsSync(dir)) {
    return [];
  }
  return readdirSync(dir).filter((id) => SIGNIN.test(id)).filter((id) => {
    const state = loginState(id);
    return state !== null && !state.ended && alive(state.pid);
  });
}

function loginLaunch(id: string): number {
  const dir = signinDir(id);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  renameSync(`${STATE}/inbox/${id}.json`, `${dir}/start.json`);
  const log = openSync(`${dir}/runner.log`, "a", 0o600);
  const child = spawn(process.execPath, [...process.execArgv, process.argv[1]!, "login-run", id],
                      { detached: true, stdio: ["ignore", log, log], env: process.env });
  closeSync(log);
  child.unref();
  writeJson(`${dir}/state.json`, { pid: child.pid, ended: false });
  return OK;
}

/**
 * A pasted code the Worker left in the inbox: read once and deleted at once, then handed to
 * the live sign-in's runner over its socket, which writes it to the CLI's stdin alone.
 */
async function loginInput(id: string): Promise<number> {
  const dir = signinDir(id);
  const state = loginState(id);
  const inbox = `${STATE}/inbox/${id}.code`;
  if (state === null || state.ended || !alive(state.pid)) {
    remove(inbox);
    return NO;
  }
  let code: string;
  try {
    code = readFileSync(inbox, "utf8");
  } finally {
    remove(inbox);
  }
  return new Promise<number>((resolve) => {
    const socket = createConnection(`${dir}/input.sock`, () => {
      socket.end(code, () => resolve(OK));
    });
    socket.on("error", () => resolve(NO));
  });
}

/** A pasted code the Worker left that was never handed over, gone (review L1). */
function loginDrop(id: string): number {
  signinDir(id);  // its id checked
  remove(`${STATE}/inbox/${id}.code`);
  return OK;
}

function loginCancel(id: string): number {
  const state = loginState(id);
  if (state === null || state.ended || !alive(state.pid)) {
    return NO;
  }
  writeFileSync(`${signinDir(id)}/cancel`, "");
  process.kill(state.pid, "SIGTERM");
  return OK;
}

/**
 * The sign-in's own runner: the record's login run as a pool user, its prompt relayed, a
 * pasted code to its stdin; then its kept paths into the engine's store, its presence
 * reported, and the sign-in's end posted (`signed_in`, `failed`, `cancelled`, `refused`,
 * `expired`, `signed_out`). Its frames carry only a sign-in's fixed states, the record's own
 * URL and code, and a refusal's fixed reason (`SIGNIN_REFUSALS`): no text the VM chose.
 */
async function loginRun(id: string): Promise<number> {
  const dir = signinDir(id);
  const doc = readJson<SigninDoc>(`${dir}/start.json`);
  const lease = readJson<LeaseFile>(`${STATE}/lease.json`);
  const rec = record(doc.engine);
  const login = rec.cli!.login as (LoginSpec & Readonly<Record<string, unknown>>) | undefined;
  const outbox = new Outbox(0);
  const proc: { child: ChildProcess | null; kill: ReturnType<typeof setTimeout> | null;
                stopped: boolean } = { child: null, kill: null, stopped: false };
  const stop = () => {
    proc.stopped = true;
    const child = proc.child;
    if (child !== null && child.exitCode === null && child.pid !== undefined) {
      const pid = child.pid;
      try {
        process.kill(-pid, "SIGTERM");
      } catch {
        child.kill("SIGTERM");
      }
      proc.kill ??= setTimeout(() => {
        try {
          process.kill(-pid, "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
      }, CANCEL_GRACE_MS);
    }
  };
  const poster = new Poster(doc.urls.events, lease.tokens.runner_token, outbox, stop);
  process.on("SIGTERM", stop);
  await poster.tick();  // the sign-in's first heartbeat: it holds the lease
  const pump = setInterval(() => {
    void poster.tick();
  }, FLUSH_MS);
  let end = "failed";
  let reason: SigninRefusal | null = null;
  let as: Identity | null = null;
  let home: string | null = null;
  let watch: ReturnType<typeof setInterval> | null = null;
  let inbound: ReturnType<typeof createServer> | null = null;
  let expiry: ReturnType<typeof setTimeout> | null = null;
  let expired = false;
  try {
    if (login === undefined || !rec.cli!.credential.includes("vendor_login_in_sandbox")) {
      throw new Error(`engine ${rec.id} has no vendor sign-in`);
    }
    if (doc.mode === "out") {
      // The store's generation moved on first, so no run live now writes its secret back
      // afterwards; and each such run stopped, so none keeps using the login signed out.
      await withStore(rec, () => nextGeneration(rec));
      for (const { run } of liveRuns()) {
        if (readJson<StartDoc>(`${runDir(run)}/start.json`).engine === rec.id) {
          cancel(run);
        }
      }
      // The vendor's own sign-out first, which may revoke the login on the vendor's side.
      const logout = login["logout_argv"] as string[] | undefined;
      if (logout !== undefined) {
        as = take(ROOT, STATE, id);
        home = await makeHome(id, rec);
        giveTree(home, as);
        const madeHome = home;
        const argv = asUser(as, [shared()], logout.map((arg) => arg.replaceAll("{bin}", binOf(rec))
          .replaceAll("{home}", madeHome)));
        spawnSync(argv[0]!, argv.slice(1), { env: engineEnv(rec, madeHome), cwd: madeHome,
                                             timeout: COMMAND_MS, stdio: "ignore" });
      }
      // Its secrets gone from the store: no later run's home carries the sign-in.
      const store = `${STORE}/${rec.id}`;
      await withStore(rec, () => {
        for (const rel of existsSync(store) ? safeWalk(store) : []) {
          if (isKept(keptPaths(rec).secrets, rel)) {
            remove(join(store, rel));
          }
        }
      });
      end = "signed_out";
    } else {
      as = take(ROOT, STATE, id);
      home = await makeHome(id, rec);
      const madeHome = home;
      const files = rec.cli!.per_credential?.vendor_login_in_sandbox?.files ?? {};
      for (const [path, text] of Object.entries(files)) {
        safeWrite(madeHome, path.replaceAll("{home}", madeHome), new TextEncoder().encode(text),
                  0o600);
      }
      giveTree(madeHome, as);
      const command = loginArgv(login, binOf(rec), doc.region);
      // Always on a terminal of its own: some CLIs prompt only on one.
      const argv = asUser(as, [shared()], onTerminal(command));
      const secrets = atoms();
      let shown: Prompt = { url: null, code: null };
      let text = "";
      let complete = false;
      const exit = await new Promise<number>((resolve) => {
        const child = spawn(argv[0]!, argv.slice(1), {
          env: engineEnv(rec, madeHome), cwd: madeHome, detached: true,
          stdio: ["pipe", "pipe", "pipe"] });
        proc.child = child;
        // Only the record's patterns' groups leave the VM, and none credential-shaped.
        const read = (data: Buffer) => {
          // Its tail, as much as one frame shows: a prompt is a line of it.
          text = (text + plain(data.toString("utf8"))).slice(-FRAME_UNITS_MAX);
          const next = prompted(login, text, shown);
          if (next.url === shown.url && next.code === shown.code) {
            return;
          }
          // Only the record's own shapes leave the VM (its login hosts, a device code); a
          // credential-shaped value is refused as a second line.
          const why = signinShape(rec, doc.region, next.url, next.code)
            ?? ([next.url, next.code].some((value) => value !== null
              && credentialShaped(value, secrets)) ? "credential" : null);
          if (why !== null) {
            reason = why;
            stop();
            return;
          }
          shown = next;
          outbox.push({ type: "ta.signin", state: "open", url: shown.url, code: shown.code,
                        accepts_code: login["accepts_code"] === true });
        };
        child.stdout!.on("data", read);
        child.stderr!.on("data", read);
        child.stdin!.on("error", () => undefined);
        child.on("close", (code) => {
          // Its group gone: no late kill may reach a group that reused its id.
          if (proc.kill !== null) {
            clearTimeout(proc.kill);
            proc.kill = null;
          }
          resolve(code ?? 128 + 15);
        });
        child.on("error", () => resolve(127));
        // A pasted code comes over a socket in the sign-in's root-only directory, straight to
        // the CLI's stdin: never on disk, never logged.
        inbound = createServer((socket) => {
          let pasted = "";
          socket.on("data", (data: Buffer) => {
            pasted = (pasted + data.toString("utf8")).slice(0, SIGNIN_PASTE_MAX + 2);
          });
          socket.on("end", () => {
            const code = pasted.trim();
            pasted = "";
            // Only a sign-in that takes a pasted code, shown its prompt, gets one: one line,
            // at most a terminal line's length.
            if (login.accepts_code !== true || (shown.url === null && shown.code === null)
                || code === "" || code.length > SIGNIN_PASTE_MAX || /[\r\n]/.test(code)) {
              return;
            }
            child.stdin!.write(`${code}\n`);
            outbox.push({ type: "ta.signin", state: "code_sent" });
          });
        });
        inbound.listen(`${dir}/input.sock`);
        // The vendor's code's lifetime (`expires`, cited), past which its prompt is dead.
        expiry = setTimeout(() => {
          expired = true;
          stop();
        }, login.expires.s * 1000);
        watch = setInterval(() => {
          if (existsSync(`${dir}/cancel`)) {
            stop();
          }
          if (login["complete_on_state"] === true && !complete && statesHold(login, (path) =>
            safeRead(madeHome, path.replaceAll("{home}", madeHome))?.toString("utf8") ?? null)) {
            complete = true;  // signed in: a CLI that stays up after is stopped
            stop();
          }
        }, FLUSH_MS);
      });
      clearInterval(watch!);
      watch = null;
      (inbound as ReturnType<typeof createServer> | null)?.close();
      if (expiry !== null) {
        clearTimeout(expiry);
      }
      killAll(as.uid);
      // Only a sign-in that completed keeps what it wrote, as the owner's new login (which
      // moves the store's generation on); one refused, stopped, expired or failed keeps
      // nothing, so it never discards a live run's refresh (review N2).
      const stopped = reason !== null || (existsSync(`${dir}/cancel`) && !complete)
        || (expired && !complete);
      if (!stopped && (exit === 0 || complete)) {
        await keepHome(madeHome, rec, true);
      }
      end = reason !== null ? "refused"
        : existsSync(`${dir}/cancel`) && !complete ? "cancelled"
          : expired && !complete ? "expired"
            : (exit === 0 || complete) && await safeSignedIn(rec) ? "signed_in" : "failed";
    }
  } catch (error) {
    // Its why stays in the VM (this sign-in's own root-only log), never in a frame.
    console.error(`the sign-in failed: ${String(error)}`);
    end = "failed";
    reason = null;
  } finally {
    if (watch !== null) {
      clearInterval(watch);
    }
    // A pasted code that came after the login ended is never left at rest.
    remove(`${STATE}/inbox/${id}.code`);
    if (as !== null) {
      try {
        killAll(as.uid);
      } finally {
        if (home !== null) {
          dropHome(home);
        }
        give(STATE, as);
      }
    }
  }
  await postSignins(doc.urls.signin, lease, [{ engine: rec.id,
                                               signed_in: await safeSignedIn(rec) }]);
  outbox.push({ type: "ta.signin", state: end,
                ...(end === "refused" && reason !== null ? { reason } : {}) });
  clearInterval(pump);
  await poster.finish(end === "signed_in" || end === "signed_out" ? 0 : 1);
  writeJson(`${dir}/state.json`, { pid: process.pid, ended: true });
  return OK;
}


/**
 * The standing check of a record's sign-in (`template/build.py`, at every template build, so
 * at every pinned CLI's version bump): the record's login run, as a sign-in runs it, up to
 * the prompt its patterns find, then stopped. Its answer: whether the prompt is the record's
 * own shape (`signinShape`), the URL's host, whether a code was shown, and whether the URL
 * would send the owner's browser back to `localhost` (which strands it). No account needed.
 */
async function loginProbe(engine: string, region: string | null): Promise<number> {
  const rec = record(engine);
  const login = rec.cli!.login as Readonly<Record<string, unknown>> | undefined;
  if (login === undefined) {
    throw new Error(`engine ${engine} has no vendor sign-in`);
  }
  const name = `probe-${rec.id}-${process.pid}`;
  const as = take(ROOT, STATE, name);
  const home = await makeHome(name, rec);
  let shown: Prompt = { url: null, code: null };
  try {
    const files = rec.cli!.per_credential?.vendor_login_in_sandbox?.files ?? {};
    for (const [path, text] of Object.entries(files)) {
      safeWrite(home, path.replaceAll("{home}", home), new TextEncoder().encode(text), 0o600);
    }
    giveTree(home, as);
    const argv = asUser(as, [shared()], onTerminal(loginArgv(login, binOf(rec), region)));
    await new Promise<void>((resolve) => {
      const child = spawn(argv[0]!, argv.slice(1), { env: engineEnv(rec, home), cwd: home,
                                                     detached: true,
                                                     stdio: ["pipe", "pipe", "pipe"] });
      let text = "";
      // Its group killed; the answer waits for its close, so no exited child is left unreaped
      // for the user's processes' kill to wait on.
      const done = () => {
        try {
          process.kill(-child.pid!, "SIGKILL");
        } catch {
          // gone already
        }
      };
      // As long as a command may take: a CLI that shows no prompt by then shows none.
      const timer = setTimeout(done, COMMAND_MS);
      const read = (data: Buffer) => {
        text = (text + plain(data.toString("utf8"))).slice(-FRAME_UNITS_MAX);
        shown = prompted(login, text, shown);
        const wantsCode = typeof login["code_pattern"] === "string";
        if (shown.url !== null && (!wantsCode || shown.code !== null)) {
          clearTimeout(timer);
          done();
        }
      };
      child.stdout!.on("data", read);
      child.stderr!.on("data", read);
      child.on("close", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  } finally {
    killAll(as.uid);
    dropHome(home);
    give(STATE, as);
  }
  const redirect = shown.url === null ? null : new URL(shown.url).searchParams.get("redirect_uri");
  const local = redirect !== null && /^https?:\/\/(?:localhost|127\.|\[::1\])/.test(redirect);
  const refused = shown.url === null ? null : signinShape(rec, region, shown.url, shown.code);
  const why = shown.url === null ? "no sign-in URL shown"
    : local ? "the URL sends the browser back to localhost"
      : refused === null ? null : SIGNIN_REFUSALS[refused];
  process.stdout.write(JSON.stringify({
    engine, region, ok: why === null, reason: why,
    host: shown.url === null ? null : new URL(shown.url).hostname, code_shown: shown.code !== null }));
  return why === null ? OK : NO;
}

// -- the route probe (codexurl_research.md Q4) ------------------------------------------

/** How long one probe turn may take: a command's bound. */
const ROUTE_PROBE_MS = COMMAND_MS;

const hex = (bytes: number) => randomBytes(bytes).toString("hex");

/** The sealed API a provider's key goes to, as the gateway's record for it says. */
function sealedScope(provider: string, engine: string): EgressScope {
  const found = (bundledRecords() as Record<string, unknown>[]).filter((raw) =>
    raw["provider"] === provider && raw["unwired"] === undefined
    && typeof raw["base_url"] === "string" && Array.isArray(raw["connect"])
    && (raw["connect"] as string[]).includes("api_key")
    && Array.isArray(raw["engines"]) && (raw["engines"] as string[]).includes(engine))
    .sort((a, b) => String(a["id"]).localeCompare(String(b["id"])))[0];
  if (found === undefined) {
    throw new Error(`no gateway record seals ${provider}'s key for ${engine}`);
  }
  return { api: String(found["api"]), base_url: String(found["base_url"]), endpoints: [],
           meter: null };
}

/** One probe turn's outcome: its exit, the session it named, whether it gave its final text. */
interface ProbeTurn {
  /** Its stderr's tail, named when it failed. */
  readonly stderr: string;
  readonly exit: number;
  readonly session_id: string | null;
  readonly answered: boolean;
  readonly resumed: boolean;
  readonly connects: string[];
}

/**
 * One keyed engine on one provider, run as a run runs it (the real `invocation`, `runEnv`
 * and `sandboxEnv`, `makeHome` for an API-key run, its own pool user), its egress door a
 * loopback capture server at the door's real path answering each model request with a
 * minimal valid reply in its sealed API's wire format, under `strace` for its connects, with
 * a fake sign-in seeded in every one of its store's `secret_paths`. Two turns: a new
 * conversation, then its resume (its home made again from the store, as the next run's is),
 * that one compacting first where the record says how (`cli.compaction`). Each turn must exit
 * 0 with its final text, the first name its session where the record resumes by one, the
 * second resume it, a compaction be seen; and every request of both is judged
 * (`routeVerdict`).
 */
async function probeRoute(rec: SourceRecord, provider: string,
                          resolvers: readonly string[]): Promise<Record<string, unknown>> {
  const cli = rec.cli!;
  const egress = sealedScope(provider, rec.id);
  const requests: Captured[] = [];
  const server = createHttpServer((request: IncomingMessage, response) => {
    const chunks: Buffer[] = [];
    let size = 0;
    request.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size <= 4 * 1024 * 1024) {
        chunks.push(chunk);
      }
    });
    request.on("end", () => {
      const captured: Captured = {
        method: request.method ?? "", url: request.url ?? "",
        headers: Object.fromEntries(Object.entries(request.headers).map(([name, value]) =>
          [name, Array.isArray(value) ? value.join(", ") : value ?? ""])),
        body: Buffer.concat(chunks).toString("utf8") };
      requests.push(captured);
      // The MCP door answers none (404: no server, so no client starts an OAuth discovery
      // of it); a model request gets its API's minimal valid reply.
      const answer = captured.url.split("?")[0]!.endsWith("/mcp")
        ? { status: 404, type: "application/json", body: "{}" } : reply(egress.api, captured);
      response.writeHead(answer.status, { "content-type": answer.type });
      response.end(answer.body);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  const origin = `http://127.0.0.1:${port}`;
  const local = (value: string) => value.replaceAll(EGRESS_ORIGIN, origin);
  const run = `run_${hex(16)}`;
  const secret = hex(32);
  const connection = `c${hex(12)}`;
  const marker = `ta_fake_signin_${hex(16)}`;
  // The first turn's own word, which a resumed turn's requests carry only if it loaded the
  // first turn's history (a CLI that silently starts afresh fails).
  const nonce = `probe${hex(8)}`;
  const engine = runEnv(sources, rec.id, "api_key");
  const keyed = sandboxEnv([{ connection, provider, egress }], () => hex(16),
                           { ...engine, run, secret, connection });
  const placeholder = Object.values(keyed).find((value) => value.startsWith("ta_ph_"))!;
  const names = cli.api_key_env![provider]!;
  const inPath = engine.secret_header === undefined ? secret : undefined;
  const prefix = new URL(egressBaseUrl(connection, names.base_url !== null
    && names.base === "connection" ? null : egress, run, inPath)).pathname;
  const env = Object.fromEntries(Object.entries({ ...engine.env, ...keyed }).map(
    ([name, value]) => [name, local(value)]));
  const first: StartDoc = {
    run, agent: "route-probe", conversation: `cv_${hex(16)}`, engine: rec.id,
    credential: "api_key", model: null, session_id: null,
    prompt: `Remember the word ${nonce}. Reply with the word OK.`, unattended: false,
    authority: "owner",
    lease: { lease_id: hex(16), epoch: 1 },
    config: { generation: 1, env, inherit: engine.inherit },
    urls: { mcp: `${origin}/r/${run}/${secret}/mcp`, mcp_bare: `${origin}/r/${run}/mcp`,
            events: `${origin}/r/${run}/events`, signin: `${origin}/signin` } };
  // A fake sign-in in each of the store's secret paths: if any of it reaches a request, a
  // key run's home carried the sign-in.
  const store = `${STORE}/${rec.id}`;
  // What the store held before the probe: only what the probe adds (its fake sign-in, its
  // turns' sessions) goes at its end.
  const had = new Set(existsSync(store) ? safeWalk(store) : []);
  const seeded: string[] = [];
  for (const rel of keptPaths(rec).secrets) {
    const concrete = rel.split("/").map((part) => part === "*" ? "probe" : part).join("/");
    const path = join(store, concrete.endsWith("/") ? `${concrete}probe.json` : concrete);
    if (!existsSync(path)) {
      safeDirs(TOP, dirname(path), 0o700);
      safeWrite(store, path, new TextEncoder().encode(JSON.stringify({
        access_token: marker, refresh_token: marker, id_token: marker,
        tokens: { access_token: marker, refresh_token: marker },
        claudeAiOauth: { accessToken: marker, refreshToken: marker } })), 0o600);
      seeded.push(path);
    }
  }
  const name = `route-${rec.id}-${process.pid}`;
  const system = `${ROOT}${SYSTEM}/${name}`;
  const agentDir = `${ROOT}${AGENTS_DIR}/${first.agent}`;
  const conversationDir = cli.engine.resume === "continue_in_cwd"
    ? `${CONVERSATIONS}/${rec.id}/${first.conversation}` : undefined;
  const turns: ProbeTurn[] = [];

  /** One turn, as one run runs: its own user and home, then its home kept and dropped. */
  const turn = async (start: StartDoc, resumed: boolean,
                      extra: readonly string[]): Promise<ProbeTurn> => {
    const as = take(ROOT, STATE, name);
    const trace = `${STATE}/route-${name}.trace`;
    let home: string | null = null;
    let exit = 1;
    let session: string | null = null;
    let answered = false;
    let text = "";
    let stderr = "";
    try {
      home = await makeHome(name, rec, false);
      const carried = safeWalk(home).filter((rel) => isKept(keptPaths(rec).secrets, rel));
      if (carried.length > 0) {
        throw new Error(`an API-key run's home carries the sign-in's ${carried.join(", ")}`);
      }
      remove(system);
      mkdirSync(system, { mode: 0o750 });
      chownSync(system, 0, as.gid);
      giveTree(agentDir, as);
      if (conversationDir !== undefined) {
        safeDirs(TOP, conversationDir, 0o700);
        giveTree(conversationDir, as);
      }
      const call = invocation(rec, start, process.env, {
        continued: resumed, root: ROOT, home, system,
        ...(conversationDir === undefined ? {} : { conversationDir }) });
      if (call.resumed !== resumed) {
        throw new Error(resumed ? "the second turn does not resume the first"
          : "the first turn resumes nothing");
      }
      for (const [path, text] of call.files) {
        const inSystem = path.startsWith(`${system}/`);
        safeWrite(inSystem ? system : home, path, new TextEncoder().encode(text),
                  inSystem ? 0o640 : 0o644, undefined, inSystem ? 0o750 : 0o755);
      }
      giveTree(home, as);
      configOwned(rec, home, system, call.files.keys(), as);
      const argv = [call.argv[0]!, ...extra, ...call.argv.slice(1)];
      const runHome = home;
      exit = await new Promise<number>((resolve) => {
        const child = spawn("strace", ["-f", "-qq", "-e", "trace=connect", "-o", trace, "--",
                                       ...asUser(as, [shared()], argv)], {
          cwd: call.cwd, env: { ...call.env, TMPDIR: join(runHome, "tmp") }, detached: true,
          stdio: [call.stdin === null ? "ignore" : "pipe", "pipe", "pipe"] });
        child.stderr!.on("data", (data: Buffer) => {
          stderr = (stderr + data.toString("utf8")).slice(-600);
        });
        if (call.stdin !== null) {
          child.stdin!.on("error", () => undefined);
          child.stdin!.end(call.stdin);
        }
        createInterface({ input: child.stdout! }).on("line", (line) => {
          if (line.trim() === "") {
            return;
          }
          const read = readEvent(cli.engine.events, lineEvent(line));
          session = read.session_id ?? session;
          if (read.text !== null) {
            // Its final text, as the run reads it: deltas accumulated where the record says.
            text = read.text.accumulate ? text + read.text.value : read.text.value;
            answered ||= text.includes("OK");
          }
        });
        const timer = setTimeout(() => {
          try {
            process.kill(-child.pid!, "SIGKILL");
          } catch {
            // gone already
          }
        }, ROUTE_PROBE_MS);
        child.on("close", (code) => {
          clearTimeout(timer);
          resolve(code ?? 1);
        });
        child.on("error", () => {
          clearTimeout(timer);
          resolve(127);
        });
      });
      killAll(as.uid);
      await keepHome(home, rec);
      return { stderr, exit, session_id: session, answered, resumed: call.resumed,
               connects: outsideConnects(existsSync(trace) ? readFileSync(trace, "utf8") : "",
                                         resolvers) };
    } finally {
      killAll(as.uid);
      if (home !== null) {
        dropHome(home);
      }
      remove(trace);
      give(STATE, as);
    }
  };

  let reason: string | null = null;
  let compacted: boolean | null = null;
  let history: boolean | null = null;
  try {
    if (spawnSync("strace", ["-V"], { encoding: "utf8" }).status !== 0) {
      throw new Error("strace is not installed in the probe's sandbox");
    }
    safeDirs(TOP, `${ROOT}${SYSTEM}`, 0o755);
    remove(agentDir);
    safeDirs(TOP, agentDir, 0o755);
    writeFileSync(`${agentDir}/agent.json`, JSON.stringify({ engine: rec.id, scopes: [] }));
    writeFileSync(`${agentDir}/AGENTS.md`, "# Route probe\n\nReply with the word OK.\n");
    const one = await turn(first, false, []);
    turns.push(one);
    if (one.exit !== 0 || !one.answered) {
      throw new Error(`the first turn did not complete (exit ${one.exit}${one.answered ? ""
        : ", no final text"}): ${one.stderr.trim().slice(-300)}`);
    }
    if (cli.engine.resume === "session_id" && one.session_id === null) {
      throw new Error("the first turn named no session to resume");
    }
    const before = requests.length;
    const two = await turn({ ...first, session_id: one.session_id,
                             prompt: "Reply with the word OK once more." }, true,
                           cli.compaction?.argv ?? []);
    turns.push(two);
    if (two.exit !== 0 || !two.answered) {
      throw new Error(`the resumed turn did not complete (exit ${two.exit}${two.answered ? ""
        : ", no final text"}): ${two.stderr.trim().slice(-300)}`);
    }
    history = requests.slice(before).some((request) => request.body.includes(nonce));
    if (!history) {
      throw new Error("the resumed turn's requests carry none of the first turn's history");
    }
    if (cli.compaction !== undefined) {
      compacted = requests.slice(before).some((request) =>
        request.body.includes(cli.compaction!.marker));
      if (!compacted) {
        throw new Error("the resumed turn made no compaction request");
      }
    }
    reason = routeVerdict(requests, turns.flatMap((each) => each.connects), {
      prefix, placeholder, secret, marker, resolvers,
      mcp: [`/r/${run}/${secret}/mcp`, `/r/${run}/mcp`],
      secretInHeader: engine.secret_header !== undefined });
  } catch (error) {
    reason = String(error instanceof Error ? error.message : error);
    // What the CLI did is judged too: a leak or an outside connect is named first.
    reason = routeVerdict(requests, turns.flatMap((each) => each.connects), {
      prefix, placeholder, secret, marker, resolvers,
      mcp: [`/r/${run}/${secret}/mcp`, `/r/${run}/mcp`],
      secretInHeader: engine.secret_header !== undefined }) ?? reason;
  } finally {
    remove(system);
    remove(agentDir);
    if (conversationDir !== undefined) {
      remove(conversationDir);
    }
    for (const rel of existsSync(store) ? safeWalk(store) : []) {
      if (!had.has(rel)) {
        remove(join(store, rel));  // the probe's own sessions and its fake sign-in
      }
    }
    for (const path of seeded) {
      remove(path);
    }
    server.close();
  }
  return { engine: rec.id, provider, ok: reason === null, reason,
           turns: turns.map((each) => ({ exit: each.exit, answered: each.answered,
                                         resumed: each.resumed })),
           compacted, history,
           requests: requests.map((request) => `${request.method} ${request.url.split("?")[0]}`),
           connects: [...new Set(turns.flatMap((each) => each.connects))] };
}

/**
 * The route probe (`route-probe <resolver>...`, at every template build, on a sandbox whose
 * network denies all): every record that runs on an API key, on every provider it keys, run
 * as a run runs it (`probeRoute`), one JSON line each; any failure fails the command.
 */
async function routeProbe(resolvers: readonly string[]): Promise<number> {
  let failed = false;
  for (const rec of RECORDS.values()) {
    const cli = rec.cli!;
    if (rec.unwired !== undefined || !cli.credential.includes("api_key")) {
      continue;
    }
    for (const provider of Object.keys(cli.api_key_env ?? {}).sort()) {
      const result = await probeRoute(rec, provider, resolvers);
      failed ||= result["ok"] !== true;
      process.stdout.write(`${JSON.stringify(result)}\n`);
    }
  }
  return failed ? NO : OK;
}

// -- main -----------------------------------------------------------------------------

export async function main(argv: readonly string[]): Promise<number> {
  const [command, ...args] = argv;
  switch (command) {
    case "prepare":
      return prepare(args);
    case "login-launch":
      return loginLaunch(args[0] ?? "");
    case "login-run":
      return loginRun(args[0] ?? "");
    case "login-input":
      return loginInput(args[0] ?? "");
    case "login-drop":
      return loginDrop(args[0] ?? "");
    case "login-cancel":
      return loginCancel(args[0] ?? "");
    case "login-probe":
      return loginProbe(args[0] ?? "", args[1] ?? null);
    case "route-probe":
      return routeProbe(args);
    case "matcher-probe":
      // The sync-ignore automaton's step, timed here (the template build's standing check).
      process.stdout.write(`${JSON.stringify({ step_ns: stepNanos() })}\n`);
      return OK;
    case "signin-check":
      return await signedIn(record(args[0] ?? "")) ? OK : NO;
    case "launch":
      return launch(args[0] ?? "");
    case "run":
      return runCommand(args[0] ?? "", args[1] === "--recover");
    case "cancel":
      return cancel(args[0] ?? "");
    case "status":
      return status(args[0] ?? "");
    case "state":
      return stateCommand();
    case "adopt":
      return adopt();
    case "quiet-wait":
      return quietWait(Number(args[0]));
    case "renew-wait":
      return renewWait(Number(args[0]));
    case "renewed":
      await signalled("SIGUSR2", "renewed-ack", Date.now() + COMMAND_MS);
      return OK;
    case "recover":
      return recover();
    case "sync":
      return syncCommand();
    default:
      process.stderr.write(`ta-runner: no command ${String(command)}\n`);
      return 2;
  }
}

if (process.argv[1] !== undefined && /runner\.(?:mjs|ts|js)$/.test(process.argv[1])
    && process.env["TA_RUNNER_IMPORTED"] === undefined) {
  main(process.argv.slice(2)).then((code) => process.exit(code), (error: unknown) => {
    process.stderr.write(`ta-runner: ${String(error)}\n`);
    process.exit(1);
  });
}
