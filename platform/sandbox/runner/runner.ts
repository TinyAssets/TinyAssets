/**
 * The in-VM runner (`ta-runner`; ADR-034 decision 4.3, `engines-replan.md` §2.1 steps 4-7),
 * baked into the versioned template (`template/build.py`) and run as root by the sandbox
 * Worker through envd. Each command is one subcommand; a run's own runner is detached
 * (`launch`) and lives as long as its CLI:
 *
 * - `prepare <dns...>`: the sandbox just started or resumed: `/etc/resolv.conf` pinned to
 *   the network's resolvers (E2B opens the firewall only), `/proc` remounted `hidepid=2`,
 *   so no engine's user sees another's processes or environment.
 * - `signin-check <engine>`: the record's `status_argv` as the engine's own user: 0 signed
 *   in, 3 not.
 * - `launch <run>`: the start document the Worker put in the root-only inbox taken, and the
 *   run's runner started detached; 0 once it runs.
 * - `run <run>` (the detached runner): a first empty post (the run's first heartbeat,
 *   before anything slow), the sandbox's sign-ins reported, the owner's files synced in,
 *   the engine's sessions restored, its projection, the refusal of project env files, the
 *   CLI as its own user from the record, its events framed and posted (a batch at least
 *   every `HEARTBEAT_MS`), the files synced out, then the end.
 * - `cancel <run>`: SIGTERM to the CLI, a kill after `CANCEL_GRACE_MS`; 3 when not live.
 * - `status <run>`: 0 while its runner runs, else 3.
 * - `state`: the live runs and whether every CLI is quiet (`{live, quiet}`).
 * - `renewing` / `renewed`: around a renewal's pause, each live run's runner posts a
 *   `ta.renewing` frame and holds its posts, then a `ta.renewed` frame and reconnects.
 * - `recover`: after a resume that rebooted the sandbox (E2B kept its files only), each run
 *   that was live is started again from its session (`resume_argv`).
 *
 * The runner's secret (`x-ta-runner`) and the sync token are in its root-only state
 * directory; each CLI runs as its own unprivileged user, `HOME` its engine home, its
 * environment the run's allow-list alone, and its umask 002 so the workspace it shares
 * with the other engines' users (group `ta`) stays shared.
 */
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { chmodSync, chownSync, closeSync, cpSync, existsSync, lstatSync, mkdirSync, openSync,
         readdirSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { createInterface } from "node:readline";

import { bundledSources, RUN_BASICS, type SourceRecord } from "../../doors/src/sources";
import { readEvent } from "../../owner/src/run-events";

import { AGENTS_DIR, engineHome, engineUser, invocation, secretPaths, sessionPaths,
         type StartDoc, WORKSPACE } from "./engine";
import { lineEvent, Outbox } from "./frames";
import { heldSecret, projectEnvFile, projectEnvRefusal, secretAtoms } from "./guards";
import { readMirror, SyncClient, SyncFenced, syncIn, syncOut, walk, writeMirror } from "./sync";

/** Every path the runner touches is under this root: `/` in the sandbox, a temp dir in tests. */
const ROOT = process.env["TA_RUNNER_ROOT"] ?? "";
const STATE = `${ROOT}/var/lib/ta`;
const LOCAL_WORKSPACE = `${ROOT}${WORKSPACE}`;
/** Where the engines' sessions live among the owner's files, by authority and engine. */
const SESSIONS = `${WORKSPACE}/.sessions`;

export const HEARTBEAT_MS = 20_000;
const FLUSH_MS = 250;
export const CANCEL_GRACE_MS = 10_000;
/** A CLI that fails this soon after a renewal is resumed from its session, once. */
const RENEW_RESUME_MS = 120_000;
const STATUS_MS = 20_000;
const OK = 0;
const NO = 3;
/** The input a run resumed after a renewal or a reboot is continued with. */
export const CONTINUE_PROMPT = "The sandbox paused to renew its runtime and your last step " +
  "was interrupted. Continue the task where you left off.";

const sources = bundledSources();

function record(engine: string): SourceRecord {
  const found = sources.get(engine);
  if (found?.cli === undefined) {
    throw new Error(`engine ${engine} has no CLI record`);
  }
  return found;
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

function writeJson(path: string, value: unknown, mode = 0o600): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  writeFileSync(`${path}.tmp`, JSON.stringify(value), { mode });
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
  last_at: number;
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

/** An engine's user and group ids, from the template's own `/etc/passwd`. */
function identity(engine: string): { uid: number; gid: number } {
  const name = engineUser(engine);
  for (const line of readFileSync(`${ROOT}/etc/passwd`, "utf8").split("\n")) {
    const [user, , uid, gid] = line.split(":");
    if (user === name) {
      return { uid: Number(uid), gid: Number(gid) };
    }
  }
  throw new Error(`the template has no user ${name}`);
}

/** The group the engines' users share the workspace through. */
function sharedGroup(): number | null {
  for (const line of readFileSync(`${ROOT}/etc/group`, "utf8").split("\n")) {
    const [group, , gid] = line.split(":");
    if (group === "ta") {
      return Number(gid);
    }
  }
  return null;
}

/** The engine's environment for its own commands (its status check): the record's own. */
function engineEnv(rec: SourceRecord): Record<string, string> {
  const cli = rec.cli!;
  const home = `${ROOT}${engineHome(rec.id)}`;
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
    env[name] = fill(value);
  }
  env["HOME"] = home;
  return env;
}

/** Whether the engine is signed in inside this sandbox: its record's `status_argv`. */
export function signedIn(rec: SourceRecord): boolean {
  const login = rec.cli?.login as { status_argv?: string[];
                                    status_equals?: Record<string, unknown> } | undefined;
  if (login?.status_argv === undefined) {
    return false;
  }
  const bin = `${ROOT}${binOf(rec)}`;
  const home = `${ROOT}${engineHome(rec.id)}`;
  const argv = login.status_argv.map((arg) => arg.replaceAll("{bin}", bin)
    .replaceAll("{home}", home));
  const { uid, gid } = identity(rec.id);
  if (!existsSync(home)) {
    mkdirSync(home, { recursive: true, mode: 0o700 });
    chownSync(home, uid, gid);
  }
  const done = spawnSync(argv[0]!, argv.slice(1), {
    env: engineEnv(rec), uid, gid, cwd: home, timeout: STATUS_MS, encoding: "utf8" });
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
}

function binOf(rec: SourceRecord): string {
  const install = rec.cli!.install;
  return install.manager === "npm"
    ? `/opt/ta/engines/${rec.id}/node_modules/.bin/${install.bin}`
    : `/opt/ta/engines/${rec.id}/bin/${install.bin}`;
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
  // Once a boot (the marker on tmpfs): the modes are on disk and outlive a pause, and the
  // scan of the whole root takes seconds (probed: about 7 s).
  if (!existsSync(LOCKED)) {
    lockDown();
    writeFileSync(LOCKED, "");
  }
  return OK;
}

const LOCKED = `${ROOT}/run/ta-locked-down`;

/**
 * The places every user may write, outside a user's own: none but the sticky scratch
 * directories. E2B's base image leaves root's own programs world-writable at boot (probed:
 * `/usr/bin/envd`, `/usr/local/bin`, systemd's units), so an engine's user could replace
 * what root runs; `prepare` takes every user's write away from them at each start and
 * resume, and fails loudly if any is left.
 */
const SCRATCH = Object.freeze(["/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp", "/home",
                               "/workspace"]);
function lockDown(): void {
  const prune = SCRATCH.flatMap((path, i) => [...(i === 0 ? [] : ["-o"]), "-path", path]);
  const find = (extra: readonly string[]) => spawnSync("find", [
    "/", "-xdev", "(", ...prune, ")", "-prune", "-o", "!", "-type", "l", "-perm", "-0002",
    "!", "-perm", "-1000", ...extra], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const fixed = find(["-exec", "chmod", "o-w", "{}", "+"]);
  const left = find(["-print"]);
  if (fixed.status !== 0 || left.status !== 0 || left.stdout.trim() !== "") {
    throw new Error(`world-writable paths remain: ${left.stdout.slice(0, 300)}`);
  }
}

// -- launch, cancel, status, state ----------------------------------------------------

function launch(run: string): number {
  const dir = runDir(run);
  const inbox = `${STATE}/inbox/${run}.json`;
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  renameSync(inbox, `${dir}/start.json`);
  chmodSync(`${dir}/start.json`, 0o600);
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
                                                            last_kind: null, last_at: 0,
                                                            cli_pid: null }),
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
 * Whether a CLI is between turns, as its events show: its last event a tool call, a result
 * or a usage report (its model call answered), or none for `QUIET_MS`.
 */
const QUIET_MS = 30_000;
function quiet(state: RunState): boolean {
  return state.last_kind === "tool_call" || state.last_kind === "result"
    || state.last_kind === "usage" || Date.now() - state.last_at >= QUIET_MS;
}

function stateCommand(): number {
  const live = liveRuns();
  process.stdout.write(JSON.stringify({ live: live.length,
                                        quiet: live.every(({ state }) => quiet(state)) }));
  return OK;
}

async function around(signal: "SIGUSR1" | "SIGUSR2", ack: string): Promise<number> {
  const live = liveRuns();
  for (const { run, state } of live) {
    rmSync(`${runDir(run)}/${ack}`, { force: true });
    process.kill(state.pid, signal);
  }
  const until = Date.now() + 15_000;
  while (Date.now() < until && !live.every(({ run }) => existsSync(`${runDir(run)}/${ack}`))) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return OK;
}

function recover(): number {
  const dir = `${STATE}/runs`;
  for (const run of existsSync(dir) ? readdirSync(dir).filter((each) => RUN.test(each)) : []) {
    const state = readState(run);
    if (state !== null && !state.ended && !alive(state.pid)) {
      detach(run, ["--recover"]);
    }
  }
  return OK;
}

// -- a run ----------------------------------------------------------------------------

interface LeaseFile {
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly authority: string;
  readonly tokens: { readonly runner_token: string; readonly sync_token: string };
}

/** The run's posts to its event door: one in flight, held during a renewal's pause. */
class Poster {
  holding = false;
  #last = 0;
  #busy = false;
  #refused: string | null = null;

  constructor(readonly url: string, readonly token: string, readonly outbox: Outbox,
              readonly onRefused: (why: string) => void) {}

  get refused(): string | null {
    return this.#refused;
  }

  /**
   * One post of the next frames, with `end` when they are the last ones: `taken`, `retry`
   * (the network, or the object's `events_busy` after its `Retry-After`), or `refused`
   * (the object ended the run: cancelled, revoked, settled, its lease closed).
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
      await new Promise((resolve) => setTimeout(resolve, 1000));
      return "retry";
    }
    const text = await response.text();
    if (response.status === 200) {
      this.outbox.taken(frames);
      this.#last = Date.now();
      return "taken";
    }
    if (response.status === 503 || response.status === 429) {
      const after = Number(response.headers.get("retry-after") ?? "1");
      await new Promise((resolve) => setTimeout(
        resolve, Math.min(30, Math.max(1, Number.isFinite(after) ? after : 1)) * 1000));
      return "retry";
    }
    if (response.status === 401 || response.status === 403 || response.status === 404) {
      this.#refused = text.slice(0, 200);
      this.onRefused(this.#refused);
      return "refused";
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
    return "retry";
  }

  async #once(end: { exit_code: number } | null): Promise<"taken" | "retry" | "refused"> {
    this.#busy = true;
    try {
      return await this.#post(end);
    } finally {
      this.#busy = false;
    }
  }

  /** The post in flight finished, and every frame that waits posted (or a try's bound). */
  async drain(): Promise<void> {
    for (let tries = 0; this.#busy && tries < 300; tries += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    for (let tries = 0; this.outbox.pending > 0 && this.#refused === null && tries < 20;
         tries += 1) {
      await this.#once(null);
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

/** Copy a tree's regular files, keeping their modes; links skipped. */
function copyTree(from: string, to: string, uid: number, gid: number): void {
  for (const rel of walk(from)) {
    const target = join(to, rel);
    mkdirSync(dirname(target), { recursive: true, mode: 0o700 });
    cpSync(join(from, rel), target, { preserveTimestamps: true });
    chownSync(target, uid, gid);
  }
}

/** A tree's owner set, for the engine's own user. */
function own(path: string, uid: number, gid: number): void {
  if (!existsSync(path)) {
    return;
  }
  chownSync(path, uid, gid);
  if (lstatSync(path).isDirectory()) {
    for (const entry of readdirSync(path)) {
      own(join(path, entry), uid, gid);
    }
  }
}

/** Every secret's atoms, from every record's `secret_paths` that exists in this sandbox. */
function atoms(): string[] {
  const found: string[] = [];
  const records = sources.ids().map((id) => sources.get(id)!).filter((rec) => rec.cli);
  for (const path of secretPaths(records)) {
    const local = `${ROOT}${path}`;
    if (!existsSync(local)) {
      continue;
    }
    const files = lstatSync(local).isDirectory() ? walk(local).map((rel) => join(local, rel))
      : [local];
    for (const file of files) {
      found.push(...secretAtoms(readFileSync(file, "utf8")));
    }
  }
  return found;
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
                            last_kind: null, last_at: Date.now(), ended: false,
                            renewing: false };
  const save = () => writeJson(`${dir}/state.json`, state);
  const outbox = new Outbox(state.seq);
  const cli_: { child: ChildProcess | null; kill: ReturnType<typeof setTimeout> | null;
                stopped: boolean } = { child: null, kill: null, stopped: false };
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
  let renewedAt = 0;
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
      writeFileSync(`${dir}/renewing-ack`, "");
    })();
  });
  process.on("SIGUSR2", () => {
    poster.holding = false;
    state.renewing = false;
    renewedAt = Date.now();
    outbox.push({ type: "ta.renewed" });
    save();
    writeFileSync(`${dir}/renewed-ack`, "");
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
  try {
    if (recovering) {
      outbox.push({ type: "ta.recovered" });
    }
    // The sandbox's sign-ins, for every engine that signs in with the vendor's own login.
    await reportSignins(start, lease);
    const client = new SyncClient(new URL(start.urls.events).origin, lease.tokens.sync_token);
    const gid = sharedGroup();
    const place = { local: LOCAL_WORKSPACE, gid, client: start.authority !== "owner" };
    const mirrorPath = `${STATE}/mirror.json`;
    const mirror = readMirror(mirrorPath);
    await syncIn(client, place, mirror);
    writeMirror(mirrorPath, mirror);
    const { uid, gid: engineGid } = identity(rec.id);
    const home = `${ROOT}${engineHome(rec.id)}`;
    mkdirSync(home, { recursive: true, mode: 0o700 });
    chownSync(home, uid, engineGid);
    // The engine's sessions restored from the owner's files: a cold create continues.
    const stored = `${ROOT}${SESSIONS}/${start.authority}/${rec.id}`;
    if (existsSync(stored)) {
      for (const rel of walk(stored)) {
        const target = join(home, rel);
        if (!existsSync(target)) {
          mkdirSync(dirname(target), { recursive: true, mode: 0o700 });
          cpSync(join(stored, rel), target, { preserveTimestamps: true });
          own(target, uid, engineGid);
        }
      }
      own(home, uid, engineGid);
    }
    const agentDir = `${ROOT}${AGENTS_DIR}/${start.agent}`;
    if (!existsSync(`${agentDir}/agent.json`)) {
      throw new Error(`the agent ${start.agent} has no definition in the sandbox`);
    }
    project(rec, agentDir);
    // The agent's own files never set what the run's environment does not.
    const forbid = cli.per_credential?.[start.credential]?.forbid_env ?? [];
    const scanned = new Map<string, string>();
    for (const rel of walk(agentDir)) {
      if (projectEnvFile(rel) !== null) {
        scanned.set(rel, readFileSync(join(agentDir, rel), "utf8"));
      }
    }
    const refusal = projectEnvRefusal(scanned, forbid);
    if (refusal !== null) {
      outbox.push({ type: "ta.refused", reason: refusal });
      throw new RunRefused(refusal);
    }
    const continuedDir = cli.engine.resume === "continue_in_cwd"
      ? `${home}/conversations/${start.conversation}` : null;
    const continued = continuedDir !== null && existsSync(continuedDir);
    let call = invocation(rec, start, process.env, {
      continued, root: ROOT, ...(recovering ? { prompt: CONTINUE_PROMPT, resume: true } : {}) });
    if (recovering && !call.resumed) {
      throw new RunRefused("recover_unresumable: the run's session is unknown, so the " +
                           "rebooted sandbox cannot continue it");
    }
    if (continuedDir !== null) {
      conversationDir(continuedDir, agentDir, uid, engineGid);
    }
    for (const [path, text] of call.files) {
      mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
      writeFileSync(path, text, { mode: 0o600 });
    }
    own(home, uid, engineGid);
    configOwned(rec, home, call.files.keys());
    const events = cli.engine.events;
    for (let attempt = 0; ; attempt += 1) {
      exitCode = await new Promise<number>((resolve, reject) => {
        process.umask(0o002);
        if (cli_.stopped) {
          resolve(128 + 15);
          return;
        }
        const child = spawn(call.argv[0]!, call.argv.slice(1), {
          cwd: call.cwd, env: call.env, uid, gid: engineGid, detached: true,
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
          stderr = (stderr + data.toString("utf8")).slice(-4096);
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
          state.last_at = Date.now();
          state.seq = outbox.seq;
          save();
        });
        child.on("close", (code, signal) => {
          if (cli_.kill !== null) {
            clearTimeout(cli_.kill);
            cli_.kill = null;
          }
          if (code !== 0 && stderr !== "") {
            outbox.push({ type: "ta.stderr", text: stderr.slice(-2048) });
          }
          resolve(code ?? (signal === null ? 1 : 128 + 15));
        });
      });
      // A CLI that failed just after a renewal (its model call cut by the pause) is resumed
      // from its session, once; a cancel is never resumed.
      if (exitCode === 0 || attempt > 0 || cli_.stopped || existsSync(`${dir}/cancel`)
          || renewedAt === 0 || Date.now() - renewedAt > RENEW_RESUME_MS) {
        break;
      }
      const resumed = { ...start, session_id: state.session_id };
      call = invocation(rec, resumed, process.env, { continued: true, root: ROOT,
                                                      prompt: CONTINUE_PROMPT });
      if (!call.resumed) {
        break;
      }
      outbox.push({ type: "ta.resumed", after: "renewal" });
    }
    // The engine's sessions into the owner's files, then every change out; no secret's
    // bytes in either.
    const secrets = atoms();
    const sessions = `${ROOT}${SESSIONS}/${start.authority}/${rec.id}`;
    for (const path of sessionPaths(rec)) {
      const local = `${ROOT}${path}`;
      if (!existsSync(local)) {
        continue;
      }
      const isDir = lstatSync(local).isDirectory();
      for (const file of isDir ? walk(local).map((rel) => join(local, rel)) : [local]) {
        const rel = relative(home, file);
        if (heldSecret(readFileSync(file, "latin1"), secrets) !== null) {
          outbox.push({ type: "ta.sync_refused", path: `{home}/${rel}`,
                        reason: "holds a secret's bytes" });
          continue;
        }
        const target = join(sessions, rel);
        mkdirSync(dirname(target), { recursive: true, mode: 0o2770 });
        cpSync(file, target, { preserveTimestamps: true });
        chmodSync(target, 0o640);
        if (gid !== null) {
          chownSync(target, 0, gid);
        }
      }
    }
    const report = await syncOut(client, place, mirror, lease.lease, secrets);
    writeMirror(mirrorPath, mirror);
    for (const refused of report.refused) {
      outbox.push({ type: "ta.sync_refused", ...refused });
    }
    if (report.conflicts.length > 0) {
      outbox.push({ type: "ta.sync_conflicts", paths: report.conflicts.slice(0, 64) });
    }
  } catch (error) {
    if (error instanceof SyncFenced) {
      outbox.push({ type: "ta.sync_fenced" });
    } else if (!(error instanceof RunRefused)) {
      outbox.push({ type: "ta.runner_error", message: String(error).slice(0, 1024) });
    }
    exitCode = exitCode === 0 ? 1 : exitCode;
  } finally {
    clearInterval(pump);
    await poster.finish(exitCode);
    state.ended = true;
    state.seq = outbox.seq;
    save();
  }
  return OK;
}

class RunRefused extends Error {
  override readonly name = "RunRefused";
}

/** Whether a path is there, a dangling link included (`existsSync` follows links). */
function present(path: string): boolean {
  try {
    lstatSync(path);
    return true;
  } catch {
    return false;
  }
}

/**
 * The record's projection (§2.1 step 4.3): each of its skills paths, in the agent's
 * directory, a link to the agent's `.agents/skills`; never a file that would shadow its
 * `AGENTS.md`. Links are never synced.
 */
function project(rec: SourceRecord, agentDir: string): void {
  for (const path of rec.cli!.skills_paths) {
    const target = join(agentDir, path);
    if (path === ".agents/skills" || present(target)) {
      continue;
    }
    mkdirSync(dirname(target), { recursive: true });
    symlinkSync(relative(dirname(target), join(agentDir, ".agents/skills")), target);
  }
}

/**
 * The run's config files (its MCP config, its system settings, its models file: the record's
 * templates) are root's, readable and never writable by the CLI, so an agent cannot rewrite
 * its own MCP door or trust; and a directory that holds nothing but them is root's too
 * (Gemini CLI skips a system settings file whose directory its user owns, probed). A
 * directory the CLI keeps state in (an ancestor of its home variables, sessions or secrets)
 * stays its own.
 */
function configOwned(rec: SourceRecord, home: string, files: Iterable<string>): void {
  const cli = rec.cli!;
  const kept = [...Object.values(cli.home_env), ...cli.session_paths, ...cli.secret_paths]
    .map((path) => path.replaceAll("{home}", home).replace(/\/+$/, ""));
  for (const file of files) {
    chownSync(file, 0, 0);
    chmodSync(file, 0o644);
    for (let dir = dirname(file); dir.startsWith(`${home}/`); dir = dirname(dir)) {
      if (kept.some((path) => path === dir || path.startsWith(`${dir}/`))) {
        break;
      }
      chownSync(dir, 0, 0);
      chmodSync(dir, 0o755);
    }
  }
}

/** A conversation's own cwd (`continue_in_cwd`): links to the agent's files. */
function conversationDir(dir: string, agentDir: string, uid: number, gid: number): void {
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    for (const entry of readdirSync(agentDir)) {
      symlinkSync(join(agentDir, entry), join(dir, entry));
    }
  }
  own(dir, uid, gid);
}

async function reportSignins(start: StartDoc, lease: LeaseFile): Promise<void> {
  const signins = sources.ids().map((id) => sources.get(id)!).filter((rec) =>
    rec.cli !== undefined && rec.unwired === undefined
    && rec.cli.credential.includes("vendor_login_in_sandbox")
    && (rec.cli.login as { status_argv?: unknown } | undefined)?.status_argv !== undefined)
    .map((rec) => ({ engine: rec.id, signed_in: safeSignedIn(rec) }));
  try {
    await fetch(start.urls.signin, {
      method: "POST", body: JSON.stringify({ signins }),
      headers: { "content-type": "application/json", "x-ta-runner": lease.tokens.runner_token } });
  } catch {
    // a report that did not land is made again at the next start
  }
}

function safeSignedIn(rec: SourceRecord): boolean {
  try {
    return signedIn(rec);
  } catch {
    return false;
  }
}

// -- main -----------------------------------------------------------------------------

export async function main(argv: readonly string[]): Promise<number> {
  const [command, ...args] = argv;
  switch (command) {
    case "prepare":
      return prepare(args);
    case "signin-check":
      return signedIn(record(args[0] ?? "")) ? OK : NO;
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
    case "renewing":
      return around("SIGUSR1", "renewing-ack");
    case "renewed":
      return around("SIGUSR2", "renewed-ack");
    case "recover":
      return recover();
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

