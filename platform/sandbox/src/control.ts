/**
 * One owner's sandboxes (`SandboxControl`, a Durable Object of the sandbox Worker's own, one
 * per owner by name): what serializes the E2B calls on them, so a start, a cancel, a
 * release and a renewal of one sandbox never interleave (a release's pause can never
 * freeze a run a start just launched), and the clock that renews each before its runtime
 * ceiling. It keeps no credential and no owner content: each authority's E2B sandbox id,
 * the lease that last configured it, when E2B's timeout ends it, and which runs it took. The
 * envd token is read from E2B at each use (`info`), never stored.
 *
 * **A start** (`start`, `SandboxService.start` through the entrypoint):
 * 1. the control record's sandbox numbers are read (the last ones read, when the record
 *    cannot be read now); none ever, and nothing starts (`sandbox_unconfigured`);
 * 2. the authority's sandbox is found running, or resumed (`connect`, memory restored), or
 *    created from the versioned template (`E2B_TEMPLATE`, `secure`) with the lease's network,
 *    its timeout the runtime ceiling and paused on timeout, memory kept. A resume or a
 *    create is one more running sandbox: it waits its turn in the platform's line
 *    (`slots.ts`), and past `sandbox_concurrency_ceiling` (E2B's own team count) or refused
 *    by E2B for room, the run waits (`sandbox_waiting`) until the doorbell rings, never
 *    refused. Every resume runs `prepare` again (the lockdown re-checked, not assumed). A
 *    start that opened the lease (`tokens`) applies its network whole and hands the runner
 *    its secrets (`/var/lib/ta/lease.json`, in the root-only state directory);
 * 3. a run on the vendor's own sign-in widens the network to its record's login and
 *    provider hosts, and runs the record's `status_argv` there first
 *    (`ta-runner signin-check`): no login, and the answer is `signin_required`;
 * 4. the run's start document goes into the state directory and `ta-runner launch` starts
 *    the run's runner, detached; the answer comes at once.
 *
 * **The runner** is this Worker's own (`runner-bytes.ts`): the template bakes only its loader
 * (`template/ta-runner`), and every command names the runner by its SHA-256, which the
 * loader checks before it runs it. A sandbox runs the digest its row holds; a new one is
 * uploaded and installed (`ta-runner install`) at its create, and a sandbox whose row holds
 * another adopts this Worker's at a start or resume that finds nothing living in it
 * (`state`), never under a live run or sign-in, whose children start again from their own
 * bytes: installed, `adopt`ed (the disk held to its format), the row committed, and only
 * then the adopted runner's `prepare` (at a resume, the one `prepare` it runs), which drops
 * the other runners' bytes, so no row ever names bytes that are gone. An adoption that fails
 * on the way (a full disk, envd failing) is logged loudly (`sandbox_runner_adopt_failed`)
 * and the sandbox goes on with the runner it has, asked again at its next start. The
 * loader's absent (24) has the Worker's own digest uploaded and installed and the command
 * asked again, once; its mismatch (25) is the alert `sandbox_runner_mismatch`, thrown, and
 * nothing launches. A runner speaking a protocol this Worker does not (`SPEAKS`) is
 * refused the same way (`sandbox_runner_protocol`).
 *
 * **The look** (the alarm; the founder's rule of 2026-11-03): a sandbox may run
 * `runtime_ceiling_s` at a stretch, and a pause resets that window (probed). The window's
 * end is E2B's own `endAt`, kept on each row so a look that cannot reach E2B still knows
 * when to be back. From `renewal_window_s` before it, the runner is asked to wait for its
 * CLIs to be quiet (`quiet-wait`, outside the serial chain, so starts go on meanwhile); at a
 * quiet moment, or at the edge (`RENEW_EDGE_MS`, measured), the sandbox is renewed: the
 * runner posts `renewing` and holds its posts, the sandbox is paused with its memory and
 * resumed at once, `prepare` re-checks it, and the runner reconnects (`renewed`; `recover`
 * when E2B kept its files only). A renewal's resume never waits its turn: the renewing
 * sandbox keeps its slot, and E2B refusing it is retried, backing off, and logged as an
 * alert. A sandbox paused while a lease runs in it (a renewal cut short, or E2B's own
 * timeout) is resumed the same way. A sandbox whose release asked a pause that did not
 * land, or that has no live run at its look, is paused (the idle reaper); so is one whose
 * runner has had no live run for a lease's TTL (`LEASE_TTL_S`), looked at once a TTL while
 * it runs: by then the owner's lease, renewed only by its runs' heartbeats, has lapsed, so
 * a release that never reached this Worker leaves no sandbox running to its ceiling.
 *
 * The alarm is armed again on every path, a failed look included (backing off from the
 * measured request time, never past the soonest edge), so no run dies at the ceiling.
 */
import { HEX32, RUN_ID, SIGNIN_ID } from "../../contract/src/ids";
import { DurableObject } from "cloudflare:workers";

import { readControl, type SandboxCeilings } from "../../doors/src/directory";
import { bundledSources } from "../../doors/src/sources";
import { LEASE_TTL_S } from "../../contract/src/limits";
import { DNS_SERVERS, EGRESS_HOST } from "../../contract/src/egress";
import type { SandboxSignin, SandboxStart, Started } from "../../owner/src/sandbox";
import { migrate } from "../../owner/src/migrate";
import { METER_BATCH, type MeterEvent } from "../../owner/src/sandbox-time";

import { E2b, type E2bNetwork, type Handle, noRoom, type SandboxInfo } from "./e2b";
import { SPEAKS } from "../runner/protocol";

import { runnerBytes, runnerDigest } from "./runner-bytes";
import { CONTROL_MIGRATIONS } from "./schema";
import { SLOTS, type SandboxSlots } from "./slots";

/** The runner's loader in the template, and its root-only state directory. */
export const RUNNER = "/usr/local/bin/ta-runner";
export const STATE_DIR = "/var/lib/ta";
/** Where a runner's bytes are uploaded for the loader to install (root-only). */
export const INCOMING = `${STATE_DIR}/incoming`;

/** The start's fixed answers, beside `started: true`. */
export const CODES = Object.freeze({
  waiting: "sandbox_waiting",
  signin: "signin_required",
  unconfigured: "sandbox_unconfigured",
  lost: "sandbox_lost",
} as const);

/** The runner's exit codes: done, "no" (not signed in, not live), and anything else a fault. */
const OK = 0;
const NO = 3;
/** The loader's: no runner of that digest in the sandbox; its bytes are not their digest. */
export const ABSENT = 24;
export const MISMATCH = 25;

/** A runner command that did not answer 0 or 3: its exit code kept. */
export class RunnerFailed extends Error {
  override readonly name = "RunnerFailed";
  constructor(readonly exitCode: number, message: string) {
    super(message);
  }
}

/**
 * The sandbox's runner refused for good (the founder's alert): its bytes are not their
 * digest (`sandbox_runner_mismatch`), or it speaks a protocol this Worker does not
 * (`sandbox_runner_protocol`). Nothing runs on it; an adoption never hides it.
 */
export class RunnerRefused extends Error {
  override readonly name = "RunnerRefused";
}

/**
 * A runner command's deadline: the lease's TTL. The owner's object settles a run whose
 * runner it has not heard from in `LEASE_TTL_S`, so a command that takes longer has lost
 * its run regardless.
 */
const COMMAND_MS = LEASE_TTL_S * 1000;
/** Measured: the longest pause, 705 ms (`e2b-resume-measurement.md`, 40 cycles). */
export const PAUSE_MAX_MS = 705;
/** Measured: the longest resume call, 895 ms to the first byte (the same 40 cycles). */
const RESUME_MAX_MS = 895;
/** Measured: `ta-runner renewing` with a live run, 252 ms (`sandbox-probe.md`). */
const RENEWING_MS = 252;
/** Measured: `prepare`'s lockdown, about 7 s once a boot (`sandbox-probe.md`). */
const PREPARE_MS = 7_000;
/**
 * The renewal's edge: the latest it may begin and still pause before E2B's timeout (the
 * runner's `renewing`, then the longest pause). One that lands late anyway is paused by
 * E2B's own timeout with its memory (probed), and the next look resumes it.
 */
export const RENEW_EDGE_MS = RENEWING_MS + PAUSE_MAX_MS;
/** A whole renewal past its quiet moment: renewing, pause, resume, prepare, renewed. */
const RENEWAL_MS = RENEWING_MS + PAUSE_MAX_MS + RESUME_MAX_MS + PREPARE_MS + RENEWING_MS;
/**
 * Measured: the slowest full TLS request to E2B's API from outside, 480 ms
 * (`e2b-resume-measurement.md`): a failed look's first retry, doubling after.
 */
const RETRY_BASE_MS = 480;
/**
 * The longest a retry waits, however often it failed: a lease's TTL. A run whose runner the
 * owner's object has not heard from in `LEASE_TTL_S` is settled lost, so a paused sandbox a
 * lease runs in is asked to resume at least that often while E2B refuses.
 */
const RETRY_MAX_MS = LEASE_TTL_S * 1000;

/** The wait before the `tries`-th retry: doubling from the measured request time. */
export function backoff(tries: number): number {
  return Math.min(RETRY_BASE_MS * 2 ** Math.max(0, tries - 1), RETRY_MAX_MS);
}

/**
 * Cloudflare's wall-clock limit on one alarm handler, 15 minutes: one look for a quiet
 * moment ends inside it with room for the renewals after, and the next alarm looks on.
 */
const ALARM_WALL_MS = 15 * 60_000;

export interface SandboxEnv {
  readonly E2B_API_KEY: string;
  /** The versioned template, `name:tag` (`template/build.py` prints it). */
  readonly E2B_TEMPLATE: string;
  /** The platform's control record's namespace (`c:control`), read for its sandbox numbers. */
  readonly CONTROL: KVNamespace;
  /** The platform's line for one more running sandbox (`slots.ts`). */
  readonly SLOTS: DurableObjectNamespace<SandboxSlots>;
}

interface Row {
  authority: string;
  sandbox_id: string;
  lease_id: string | null;
  /** When E2B's timeout pauses it (ms), as last read. */
  end_at: number;
  /** 1 while a renewal paused it and has not resumed it. */
  renewing: number;
  /** 1 while a release asked its pause and the pause has not landed. */
  releasing: number;
  /** 1 once the platform saw it paused (E2B's own timeout pause is seen at the next look). */
  paused: number;
  /** When its runner was first seen with no live run (ms), while it has none since. */
  idle_since: number | null;
  /** A pending resume or pause E2B did not take: when it is asked again, and how often it was. */
  retry_at: number | null;
  tries: number;
  /** The runner's digest the sandbox runs: this Worker's at its create, or adopted since. */
  runner: string;
}

/** A running sandbox's handle, and the runner's digest its commands name. */
interface Box extends Handle {
  readonly runner: string;
}

const LEASE = HEX32;
const AUTHORITY = /^(?:owner|[0-9a-f]{64})$/;
/** A sealed route (`sealRoute`), as E2B's metadata carries it. */
const ROUTE = /^[A-Za-z0-9_-]{40,2048}$/;

function json(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value));
}

/** E2B's network document from the lease's plan, as the owner's object made it. */
function e2bNetwork(network: unknown): E2bNetwork {
  const n = network as Record<string, unknown> | null;
  if (n === null || typeof n !== "object" || !Array.isArray(n["allowOut"])
      || !Array.isArray(n["denyOut"]) || !Array.isArray(n["dnsServers"])
      || typeof n["rules"] !== "object" || n["rules"] === null) {
    throw new Error("a sandbox network is E2B's whole document");
  }
  return { allowOut: n["allowOut"] as string[], denyOut: n["denyOut"] as string[],
           dnsServers: n["dnsServers"] as string[],
           rules: n["rules"] as Record<string, unknown[]> };
}

/**
 * The network widened to `hosts` (a signed-in engine's vendor hosts): unchanged when it
 * already lets every name out (`*`) or lists them all; the egress door stays listed last.
 */
export function widened(network: E2bNetwork, hosts: readonly string[]): E2bNetwork | null {
  if (network.allowOut.includes("*") || hosts.every((host) => network.allowOut.includes(host))) {
    return null;
  }
  const names = new Set([...network.allowOut, ...hosts]);
  names.delete(EGRESS_HOST);
  return { ...network, allowOut: [...[...names].sort(), EGRESS_HOST] };
}

/** The vendor's own hosts of an engine run on its sign-in; none on a key. */
function vendorHosts(start: SandboxStart): string[] {
  return start.credential !== "vendor_login_in_sandbox" ? [] : engineHosts(start.engine, null);
}

/**
 * An engine's vendor hosts, its sign-in's and its model calls': the record's, and the
 * chosen region's (`cli.regions`) when it has regions.
 */
function engineHosts(engine: string, region: string | null): string[] {
  const cli = bundledSources().get(engine)?.cli;
  if (cli === undefined) {
    throw new Error(`engine ${engine} has no CLI record`);
  }
  const chosen = region === null ? undefined : cli.regions?.[region];
  if (region !== null && chosen === undefined) {
    throw new Error(`engine ${engine} has no region ${region}`);
  }
  return [...new Set([...cli.hosts.login, ...cli.hosts.provider,
                      ...(chosen?.hosts.login ?? []), ...(chosen?.hosts.provider ?? [])])];
}

/** A sign-in's id: `si_` and 32 hex. */

/**
 * The run's doors, each `https` on the egress host: E2B's transform sets the egress token
 * on HTTPS alone (probed), so a plain-HTTP door would reach the object without it.
 */
function httpsDoors(urls: Readonly<Record<string, string>>): boolean {
  return Object.values(urls).every((url) => {
    try {
      const parsed = new URL(url);
      return parsed.protocol === "https:" && parsed.host === EGRESS_HOST && parsed.port === "";
    } catch {
      return false;
    }
  });
}

/**
 * The start's document as the runner reads it: everything but the lease's own secrets, and
 * the control record's scratch retention (`scratch_retention_days`, null when it names none).
 */
function startDocument(start: SandboxStart, retentionDays: number | null): unknown {
  const { tokens: _tokens, network: _network, route: _route, ...rest } = start;
  return { ...rest, scratch_retention_days: retentionDays };
}

/** A hex SHA-256 of `text`, its first 32 characters: an opaque name in E2B's metadata. */
async function opaque(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("")
    .slice(0, 32);
}

/** The metadata naming an owner's sandboxes, so an erasure finds strays the rows lost. */
export async function ownerTag(owner: string): Promise<string> {
  return opaque(`ta_owner\n${owner}`);
}

function handleOf(found: SandboxInfo, row: { runner: string }): Box | null {
  return found.state === "running" && found.envdAccessToken !== null
    ? { sandboxID: found.sandboxID, envdAccessToken: found.envdAccessToken, runner: row.runner }
    : null;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class SandboxControl extends DurableObject<SandboxEnv> {
  readonly #sql: SqlStorage;
  /** The one chain every E2B change of this owner's sandboxes runs on, in order. */
  #chain: Promise<unknown> = Promise.resolve();
  readonly #e2b: () => E2b;
  /** The digest of the runner this Worker ships, computed once an instance. */
  readonly #digest: Promise<string>;

  constructor(ctx: DurableObjectState, env: SandboxEnv) {
    super(ctx, env);
    this.#sql = ctx.storage.sql;
    migrate(ctx.storage, CONTROL_MIGRATIONS);
    this.#e2b = () => new E2b(env.E2B_API_KEY);
    this.#digest = runnerDigest();
  }

  /**
   * The control record's scratch retention for a run's start document: as read now, kept;
   * when the record cannot be read, the last one read, as the sandbox numbers are
   * (`#ceilings`), and said so loudly; never a silent none. With none ever read, the run
   * deletes no scratch by age, and the log says that is why.
   */
  async #retention(): Promise<number | null> {
    const read = await readControl(this.env.CONTROL);
    if (read.kind === "read") {
      const days = read.control.scratch_retention_days;
      this.#sql.exec("INSERT INTO known_retention (only, days) VALUES (1, ?) ON CONFLICT " +
                       "(only) DO UPDATE SET days = excluded.days", days);
      return days;
    }
    const row = this.#sql.exec<{ days: number | null }>("SELECT days FROM known_retention")
      .toArray()[0];
    if (row === undefined) {
      console.error(`the control record is ${read.kind}, and no scratch retention was ever ` +
                    "read: this run deletes no agent's scratch by age");
      return null;
    }
    console.error(`the control record is ${read.kind}: scratch retention kept at the last ` +
                  `read, ${row.days === null ? "none" : `${row.days} days`}`);
    return row.days;
  }

  /** `act`, after every change before it: one at a time, whatever awaits inside. */
  #serial<T>(act: () => Promise<T>): Promise<T> {
    const next = this.#chain.then(act, act);
    this.#chain = next.catch(() => undefined);
    return next;
  }

  #slots(): DurableObjectStub<SandboxSlots> {
    return this.env.SLOTS.getByName(SLOTS);
  }

  /** A sandbox of the platform's paused or ended: the line's first is rung (logged on fault). */
  async #freed(): Promise<void> {
    try {
      await this.#slots().freed();
    } catch (error) {
      console.error("the sandbox line could not be rung; its own back-off rings it", error);
    }
  }

  /**
   * The control record's sandbox numbers: as read now, kept; or, when the record cannot be
   * read or holds none, the last ones read; null when none were ever read.
   */
  async #ceilings(): Promise<SandboxCeilings | null> {
    const read = await readControl(this.env.CONTROL);
    const now = read.kind === "read" ? read.control.sandbox : null;
    if (now !== null) {
      this.#sql.exec("INSERT INTO known (only, runtime_s, concurrency, window_s) VALUES " +
                       "(1, ?, ?, ?) ON CONFLICT (only) DO UPDATE SET runtime_s = " +
                       "excluded.runtime_s, concurrency = excluded.concurrency, window_s = " +
                       "excluded.window_s", now.runtime_ceiling_s, now.concurrency_ceiling,
                     now.renewal_window_s);
      return now;
    }
    if (read.kind === "read") {
      return null;  // the founder took the numbers out: nothing new starts
    }
    return this.#known();
  }

  #known(): SandboxCeilings | null {
    const row = this.#sql.exec<{ runtime_s: number; concurrency: number; window_s: number }>(
      "SELECT runtime_s, concurrency, window_s FROM known").toArray()[0];
    return row === undefined ? null : { runtime_ceiling_s: row.runtime_s,
                                         concurrency_ceiling: row.concurrency,
                                         renewal_window_s: row.window_s };
  }

  #row(authority: string): Row | null {
    return this.#sql.exec<Row & Record<string, SqlStorageValue>>(
      "SELECT * FROM sandboxes WHERE authority = ?", authority).toArray()[0] ?? null;
  }

  #rows(): Row[] {
    return this.#sql.exec<Row & Record<string, SqlStorageValue>>("SELECT * FROM sandboxes")
      .toArray();
  }

  /** Whether a lease runs in the authority's sandbox: a run launched and not released. */
  #leased(authority: string): boolean {
    return this.#sql.exec("SELECT 1 FROM launched WHERE authority = ?", authority).toArray()
      .length > 0;
  }

  /** One more entry of the meter's log, now. */
  #log(authority: string, kind: "start" | "resume" | "pause" | "kill"): void {
    this.#sql.exec("INSERT INTO meter_log (authority, kind, at) VALUES (?, ?, ?)", authority,
                   kind, Date.now());
  }

  /**
   * What E2B told of it now: when its timeout ends it, and whether it is paused. A pause E2B
   * made on its own (its timeout) is logged as it is first seen.
   */
  #ended(found: SandboxInfo): void {
    const row = this.#sql.exec<{ authority: string; paused: number }>(
      "SELECT authority, paused FROM sandboxes WHERE sandbox_id = ?", found.sandboxID)
      .toArray()[0];
    const paused = found.state === "paused" ? 1 : 0;
    if (row !== undefined && row.paused !== paused) {
      this.#log(row.authority, paused === 1 ? "pause" : "resume");
    }
    this.#sql.exec("UPDATE sandboxes SET end_at = ?, paused = ? WHERE sandbox_id = ?",
                   Date.parse(found.endAt), paused, found.sandboxID);
  }

  #paused(authority: string): void {
    if (this.#row(authority)?.paused === 0) {
      this.#log(authority, "pause");
    }
    this.#sql.exec("UPDATE sandboxes SET paused = 1 WHERE authority = ?", authority);
  }

  /**
   * A runner command, by the digest the sandbox runs. The loader finding no runner of this
   * Worker's digest has it installed and is asked again, once; any other digest absent, or
   * bytes that are not their digest, are thrown.
   */
  async #run(box: Box, argv: readonly string[], timeoutMs = COMMAND_MS):
      Promise<{ code: number; out: string }> {
    let done = await this.#e2b().exec(box, [RUNNER, box.runner, ...argv], { timeoutMs });
    if (done.exitCode === ABSENT && box.runner === await this.#digest) {
      await this.#install(box);
      done = await this.#e2b().exec(box, [RUNNER, box.runner, ...argv], { timeoutMs });
    }
    if (done.exitCode === MISMATCH) {
      const message = `sandbox_runner_mismatch: the runner ${box.runner} in sandbox ${
        box.sandboxID} is not its digest; nothing runs on it`;
      console.error(message);
      throw new RunnerRefused(message);
    }
    if (done.exitCode !== OK && done.exitCode !== NO) {
      throw new RunnerFailed(done.exitCode, `ta-runner ${argv[0]} failed (${done.exitCode}): ${
        done.stderr}`);
    }
    return { code: done.exitCode, out: done.stdout };
  }

  /** This Worker's runner uploaded into the sandbox and installed by the loader. */
  async #install(handle: Handle): Promise<void> {
    const digest = await this.#digest;
    const e2b = this.#e2b();
    await e2b.upload(handle, `${INCOMING}/${digest}`, runnerBytes());
    const done = await e2b.exec(handle, [RUNNER, "install", digest], { timeoutMs: COMMAND_MS });
    if (done.exitCode !== OK) {
      throw new Error(`the runner ${digest} was not installed (${done.exitCode}): ${
        done.stderr}`);
    }
  }

  /**
   * The sandbox on this Worker's runner, when it runs another and nothing lives in it (no
   * run, no sign-in): installed, `adopt`ed (the disk held to its format; nothing deleted)
   * and committed to its row, its caller then running the adopted runner's `prepare`, which
   * drops the other runners' bytes. Otherwise as it is: a live run's runner stays. A
   * failure on the way keeps the runner it has (logged loudly, asked again at the next
   * start): an adoption is never what fails a start. Only a refusal (`RunnerRefused`) is
   * thrown. Whether it adopted.
   */
  async #adopt(authority: string, box: Box): Promise<{ box: Box; adopted: boolean }> {
    const digest = await this.#digest;
    if (box.runner === digest) {
      return { box, adopted: false };
    }
    try {
      let live: number;
      try {
        live = (await this.#runnerState(box)).live;
      } catch (error) {
        if (!(error instanceof RunnerFailed) || error.exitCode !== ABSENT) {
          throw error;
        }
        live = 0;  // its own runner gone from the disk: nothing can live on it
      }
      if (live > 0) {
        return { box, adopted: false };
      }
      const adopted = { ...box, runner: digest };
      await this.#install(adopted);
      if ((await this.#run(adopted, ["adopt"])).code !== OK) {
        return { box, adopted: false };  // something began living since the look
      }
      this.#sql.exec("UPDATE sandboxes SET runner = ? WHERE authority = ?", digest, authority);
      return { box: adopted, adopted: true };
    } catch (error) {
      if (error instanceof RunnerRefused) {
        throw error;
      }
      console.error(`sandbox_runner_adopt_failed: sandbox ${box.sandboxID} keeps its runner ${
        box.runner}, and this Worker's ${digest} is tried again at its next start`, error);
      return { box, adopted: false };
    }
  }

  /** A sandbox just running: its resolver pinned, `/proc` hidden, its lockdown re-checked. */
  async #prepare(box: Box, network: E2bNetwork): Promise<void> {
    const servers = network.dnsServers.length > 0 ? network.dnsServers : DNS_SERVERS;
    await this.#run(box, ["prepare", ...servers]);
  }

  /**
   * A paused sandbox resumed (memory restored, or rebooted when E2B kept its files only):
   * prepared again, and its runs told (`renewed`) or restarted from disk (`recover`). Null
   * when E2B has it no more. E2B's refusal for room is thrown (`noRoom`). A start's resume
   * (`adopt`) of a sandbox resumed with its memory and no renewal pending adopts this
   * Worker's runner first when nothing lives, so the one `prepare` is the adopted runner's;
   * a rebooted one's runs restart under the runner that launched them.
   */
  async #resume(row: Row, found: SandboxInfo, network: E2bNetwork | null,
                ceilings: SandboxCeilings, adopt = false): Promise<Box | null> {
    const e2b = this.#e2b();
    this.#ended(found);  // a pause E2B made on its own is logged before the resume
    const resumed = await e2b.connect(found.sandboxID, ceilings.runtime_ceiling_s);
    if (resumed === null) {
      return null;
    }
    // Its own runner: the one that wrote its disk and launched its runs.
    let handle: Box = { sandboxID: resumed.sandboxID, envdAccessToken: resumed.envdAccessToken,
                        runner: row.runner };
    if (network !== null) {
      await e2b.network(resumed.sandboxID, network);
    }
    if (adopt && found.pauseMode !== "filesystem" && row.renewing === 0) {
      handle = (await this.#adopt(row.authority, handle)).box;
    }
    await this.#prepare(handle, network ?? e2bNetwork(found.network));
    if (found.pauseMode === "filesystem") {
      await this.#run(handle, ["recover"]);  // rebooted: its runs restart from disk
    } else if (row.renewing === 1) {
      await this.#run(handle, ["renewed"]);
    }
    const after = await e2b.info(resumed.sandboxID);
    if (row.paused === 1 || this.#row(row.authority)?.paused === 1) {
      this.#log(row.authority, "resume");
    }
    this.#sql.exec("UPDATE sandboxes SET renewing = 0, paused = 0, retry_at = NULL, tries = 0, " +
                     "end_at = ? WHERE authority = ?",
                   after === null ? Date.now() : Date.parse(after.endAt), row.authority);
    // A renewal's slot, held across its pause, given back to the line.
    await this.#slots().unreserve(resumed.sandboxID);
    return handle;
  }

  /**
   * The authority's sandbox, running: as it is, or resumed, or created with `network`
   * (only a start that opened the lease has one); or `waiting` when one more running
   * sandbox has no room or it is not the owner's turn; or `lost` when a joining run finds
   * none.
   */
  async #ensure(owner: string, authority: string, network: E2bNetwork | null,
                ceilings: SandboxCeilings, route: string):
      Promise<{ handle: Box } | "waiting" | "lost"> {
    const e2b = this.#e2b();
    const row = this.#row(authority);
    const found = row === null ? null : await e2b.info(row.sandbox_id);
    const running = found === null ? null : handleOf(found, row!);
    if (running !== null) {
      this.#ended(found!);
      if (network !== null) {
        await e2b.network(running.sandboxID, network);
      }
      const { box, adopted } = await this.#adopt(authority, running);
      if (adopted) {
        // The adopted runner's own `prepare`, the row naming it: its lockdown, and the
        // other runners' bytes dropped.
        await this.#prepare(box, network ?? e2bNetwork(found!.network));
      }
      return { handle: box };
    }
    if (found === null && network === null) {
      return "lost";  // a joining run's sandbox is gone: nothing configures a new one
    }
    const slots = this.#slots();
    const wait = async () => {
      await slots.wait(owner, ceilings.runtime_ceiling_s);
      return "waiting" as const;
    };
    if (row !== null && row.renewing === 1 && found !== null) {
      // A renewal's own sandbox keeps its slot: resumed without its turn.
      const handle = await this.#resume(row, found, network, ceilings);
      return handle === null ? "lost" : { handle };
    }
    const { turn, reserved } = await slots.turn(owner);
    if (!turn) {
      return wait();
    }
    try {
      const count = await e2b.running();
      // E2B's count is the team's, and advisory: unknown, the create is tried and E2B's
      // own refusal holds the plan. A renewal's sandbox, paused for a moment, keeps its slot
      // (`reserved`): counted, and with no count to set it against, nothing is tried.
      if (count === null ? reserved > 0 : count + reserved >= ceilings.concurrency_ceiling) {
        return await wait();
      }
      if (found !== null) {
        const handle = await this.#resume(row!, found, network, ceilings, true);
        if (handle !== null) {
          await slots.took(owner);
          return { handle };
        }
        if (network === null) {
          return "lost";
        }
      }
      const created = await e2b.create({
        template: this.env.E2B_TEMPLATE, timeoutS: ceilings.runtime_ceiling_s,
        network: network!,
        // Opaque names, so E2B's own list shows whose it is to us and to no one else.
        metadata: { ta_sandbox: await opaque(`${owner}\n${authority}`),
                    ta_owner: await ownerTag(owner),
                    // Sealed, so only the doors open it: the route of E2B's lifecycle events.
                    ta_route: route },
      });
      const info = await e2b.info(created.sandboxID);
      const digest = await this.#digest;
      this.#sql.exec("INSERT INTO sandboxes (authority, sandbox_id, lease_id, end_at, runner) " +
                       "VALUES (?, ?, NULL, ?, ?) ON CONFLICT (authority) DO UPDATE SET " +
                       "sandbox_id = excluded.sandbox_id, lease_id = NULL, end_at = " +
                       "excluded.end_at, renewing = 0, releasing = 0, paused = 0, runner = " +
                       "excluded.runner", authority, created.sandboxID,
                     info === null ? Date.now() : Date.parse(info.endAt), digest);
      this.#log(authority, "start");
      await slots.took(owner);
      // The template holds no runner: this Worker's, installed before its first command.
      const box = { ...created, runner: digest };
      await this.#install(box);
      await this.#prepare(box, network!);
      return { handle: box };
    } catch (error) {
      if (noRoom(error)) {
        return wait();
      }
      throw error;
    }
  }

  async start(owner: string, start: SandboxStart): Promise<Started> {
    if (!RUN_ID.test(start.run) || !AUTHORITY.test(start.authority)
        || !LEASE.test(start.lease.lease_id) || (start.tokens === null) !== (start.network === null)
        || typeof start.route !== "string" || !ROUTE.test(start.route)
        || !httpsDoors(start.urls)) {
      throw new Error("a sandbox start names its run, its authority, its lease and its " +
                      "https doors on the egress host");
    }
    const ceilings = await this.#ceilings();
    if (ceilings === null) {
      return { started: false, code: CODES.unconfigured };
    }
    return this.#serial(async () => {
      const e2b = this.#e2b();
      const network = start.network === null ? null : e2bNetwork(start.network.network);
      const hosts = vendorHosts(start);
      const first = network === null ? null : widened(network, hosts) ?? network;
      const got = await this.#ensure(owner, start.authority, first, ceilings, start.route);
      if (got === "waiting") {
        return { started: false, code: CODES.waiting };
      }
      if (got === "lost") {
        return { started: false, code: CODES.lost };
      }
      const { handle } = got;
      if (start.tokens !== null) {
        await e2b.upload(handle, `${STATE_DIR}/lease.json`, json({
          lease: start.lease, authority: start.authority, tokens: start.tokens }));
        this.#sql.exec("UPDATE sandboxes SET lease_id = ?, releasing = 0 WHERE authority = ?",
                       start.lease.lease_id, start.authority);
      } else if (hosts.length > 0) {
        // A joining run on another engine's sign-in: its vendor's hosts added to the live
        // network (read back whole, its transform with it, and replaced whole).
        const live = await e2b.info(handle.sandboxID);
        const more = live === null ? null : widened(e2bNetwork(live.network), hosts);
        if (more !== null) {
          await e2b.network(handle.sandboxID, more);
        }
      }
      if (start.credential === "vendor_login_in_sandbox") {
        const signedIn = await this.#run(handle, ["signin-check", start.engine]);
        if (signedIn.code === NO) {
          return { started: false, code: CODES.signin };
        }
      }
      await e2b.upload(handle, `${STATE_DIR}/inbox/${start.run}.json`, json(startDocument(start,
        await this.#retention())));
      const launched = await this.#run(handle, ["launch", start.run]);
      if (launched.code !== OK) {
        throw new Error(`the runner did not launch run ${start.run}`);
      }
      this.#sql.exec("INSERT OR REPLACE INTO launched (run, authority, lease_id) VALUES (?, ?, ?)",
                     start.run, start.authority, start.lease.lease_id);
      this.#sql.exec("UPDATE sandboxes SET releasing = 0, idle_since = NULL WHERE " +
                       "authority = ?", start.authority);
      await this.#arm();
      return { started: true };
    });
  }

  /**
   * The vendor's own sign-in started in the authority's sandbox (slice SIGNIN_ID), or its
   * sign-out: the sandbox found, resumed or made as a run's start finds it (its turn in the
   * line, `sandbox_waiting` past the ceiling), its network let out to the engine's vendor
   * hosts (and the chosen region's), and the runner's `login-launch` started with the
   * sign-in's document (no secret); the sign-in holds the lease as a launched run does.
   */
  async signin(owner: string, signin: SandboxSignin): Promise<Started> {
    if (!SIGNIN_ID.test(signin.id) || !AUTHORITY.test(signin.authority)
        || !LEASE.test(signin.lease.lease_id) || (signin.tokens === null) !== (signin.network === null)
        || typeof signin.route !== "string" || !ROUTE.test(signin.route)
        || !httpsDoors(signin.urls) || (signin.mode !== "in" && signin.mode !== "out")) {
      throw new Error("a sign-in names its id, its authority, its lease, its route and its " +
                      "https doors on the egress host");
    }
    const hosts = engineHosts(signin.engine, signin.region);
    const ceilings = await this.#ceilings();
    if (ceilings === null) {
      return { started: false, code: CODES.unconfigured };
    }
    return this.#serial(async () => {
      const e2b = this.#e2b();
      const network = signin.network === null ? null : e2bNetwork(signin.network.network);
      const first = network === null ? null : widened(network, hosts) ?? network;
      const got = await this.#ensure(owner, signin.authority, first, ceilings, signin.route);
      if (got === "waiting") {
        return { started: false, code: CODES.waiting };
      }
      if (got === "lost") {
        return { started: false, code: CODES.lost };
      }
      const { handle } = got;
      if (signin.tokens !== null) {
        await e2b.upload(handle, `${STATE_DIR}/lease.json`, json({
          lease: signin.lease, authority: signin.authority, tokens: signin.tokens }));
        this.#sql.exec("UPDATE sandboxes SET lease_id = ?, releasing = 0 WHERE authority = ?",
                       signin.lease.lease_id, signin.authority);
      } else {
        const live = await e2b.info(handle.sandboxID);
        const more = live === null ? null : widened(e2bNetwork(live.network), hosts);
        if (more !== null) {
          await e2b.network(handle.sandboxID, more);
        }
      }
      const { tokens: _tokens, network: _network, lease: _lease, route: _route, ...doc } = signin;
      await e2b.upload(handle, `${STATE_DIR}/inbox/${signin.id}.json`, json(doc));
      const launched = await this.#run(handle, ["login-launch", signin.id]);
      if (launched.code !== OK) {
        throw new Error(`the runner did not launch sign-in ${signin.id}`);
      }
      this.#sql.exec("INSERT OR REPLACE INTO launched (run, authority, lease_id) VALUES (?, ?, ?)",
                     signin.id, signin.authority, signin.lease.lease_id);
      this.#sql.exec("UPDATE sandboxes SET releasing = 0, idle_since = NULL WHERE " +
                       "authority = ?", signin.authority);
      await this.#arm();
      return { started: true };
    });
  }

  /** The authority's running sandbox, or null (none, or paused). */
  async #running(authority: string): Promise<Box | null> {
    const row = this.#row(authority);
    const found = row === null ? null : await this.#e2b().info(row.sandbox_id);
    return found === null ? null : handleOf(found, row!);
  }

  /**
   * A pasted code to the live sign-in's CLI: left in the runner's root-only inbox and handed
   * over at once (`login-input`, which reads and deletes it whatever follows); whether the
   * sign-in was live to take it. A hand-over that failed drops it (`login-drop`), and a pause
   * between the two leaves it to the runner's `prepare` at the resume, which deletes every
   * code left in the inbox before anything else reads it: a code never rests on disk.
   */
  async signinInput(_owner: string, request: { authority: string; id: string; code: string }):
      Promise<{ delivered: boolean }> {
    if (!SIGNIN_ID.test(request.id) || !AUTHORITY.test(request.authority)
        || typeof request.code !== "string" || /[\r\n]/.test(request.code)) {
      throw new Error("a pasted code names its sign-in and is one line");
    }
    return this.#serial(async () => {
      const handle = await this.#running(request.authority);
      if (handle === null) {
        return { delivered: false };
      }
      await this.#e2b().upload(handle, `${STATE_DIR}/inbox/${request.id}.code`,
                               new TextEncoder().encode(request.code));
      try {
        return { delivered: (await this.#run(handle, ["login-input", request.id])).code === OK };
      } catch (error) {
        await this.#run(handle, ["login-drop", request.id]).catch(() => undefined);
        throw error;
      }
    });
  }

  /** A live sign-in stopped (`login-cancel`). */
  async signinCancel(_owner: string, request: { authority: string; id: string }):
      Promise<{ cancelled: boolean }> {
    if (!SIGNIN_ID.test(request.id) || !AUTHORITY.test(request.authority)) {
      throw new Error("a sign-in's cancel names its sign-in");
    }
    return this.#serial(async () => {
      const handle = await this.#running(request.authority);
      return { cancelled: handle !== null
        && (await this.#run(handle, ["login-cancel", request.id])).code === OK };
    });
  }

  /** The running sandbox a run is in, or null (gone, or paused: its runner is not running). */
  async #live(run: string): Promise<Box | null> {
    const at = this.#sql.exec<{ authority: string }>(
      "SELECT authority FROM launched WHERE run = ?", run).toArray()[0];
    const row = at === undefined ? null : this.#row(at.authority);
    if (row === null) {
      return null;
    }
    const found = await this.#e2b().info(row.sandbox_id);
    return found === null ? null : handleOf(found, row);
  }

  async cancel(_owner: string, request: { run: string; lease_id: string }):
      Promise<{ cancelled: boolean }> {
    if (!RUN_ID.test(request.run)) {
      throw new Error("a cancel names its run");
    }
    return this.#serial(async () => {
      const handle = await this.#live(request.run);
      if (handle === null) {
        return { cancelled: false };
      }
      // SIGTERM to the CLI, then a kill after its grace; the runner then syncs and ends.
      return { cancelled: (await this.#run(handle, ["cancel", request.run])).code === OK };
    });
  }

  async status(_owner: string, request: { run: string; lease_id: string }):
      Promise<{ state: "running" | "exited" | "gone" }> {
    if (!RUN_ID.test(request.run)) {
      throw new Error("a status names its run");
    }
    return this.#serial(async () => {
      const at = this.#sql.exec<{ authority: string }>(
        "SELECT authority FROM launched WHERE run = ?", request.run).toArray()[0];
      const row = at === undefined ? null : this.#row(at.authority);
      const found = row === null ? null : await this.#e2b().info(row.sandbox_id);
      if (found === null) {
        return { state: "gone" };
      }
      const handle = await this.#live(request.run);
      if (handle === null) {
        return { state: "exited" };
      }
      return { state: (await this.#run(handle, ["status", request.run])).code === OK
        ? "running" : "exited" };
    });
  }

  /**
   * The lease is over: its sandbox paused unless a run started in it since. A pause that
   * does not land now (E2B busy, or unreachable) is kept asked (`releasing`) and the look
   * takes it again, backing off: an idle sandbox is reaped, never left to its ceiling.
   */
  async release(_owner: string, request: { lease_id: string }): Promise<{ paused: boolean }> {
    if (!LEASE.test(request.lease_id)) {
      throw new Error("a release names its lease");
    }
    return this.#serial(async () => {
      const row = this.#sql.exec<Row & Record<string, SqlStorageValue>>(
        "SELECT * FROM sandboxes WHERE lease_id = ?", request.lease_id).toArray()[0];
      this.#sql.exec("DELETE FROM launched WHERE lease_id = ?", request.lease_id);
      if (row === undefined) {
        return { paused: false };  // another lease configured it since: not this one's
      }
      this.#sql.exec("UPDATE sandboxes SET releasing = 1 WHERE authority = ?", row.authority);
      let paused = false;
      try {
        paused = await this.#reap(row);
      } finally {
        await this.#arm();
      }
      return { paused };
    });
  }

  /**
   * A released sandbox paused, unless its runner has a live run (then it is no longer
   * released). Whether it is paused now; `releasing` stays while the pause has not landed.
   */
  async #reap(row: Row): Promise<boolean> {
    const e2b = this.#e2b();
    const found = await e2b.info(row.sandbox_id);
    if (found === null) {
      this.#forget(row.authority);
      await this.#freed();
      return false;
    }
    this.#ended(found);
    const handle = handleOf(found, row);
    if (handle === null) {
      this.#sql.exec("UPDATE sandboxes SET releasing = 0 WHERE authority = ?", row.authority);
      return found.state === "paused";
    }
    if ((await this.#runnerState(handle)).live > 0) {
      this.#sql.exec("UPDATE sandboxes SET releasing = 0 WHERE authority = ?", row.authority);
      return false;
    }
    if (await e2b.pause(found.sandboxID) === "busy") {
      return false;  // a previous snapshot is still being written: the look asks again
    }
    this.#sql.exec("UPDATE sandboxes SET releasing = 0, retry_at = NULL, tries = 0 WHERE " +
                     "authority = ?", row.authority);
    this.#paused(row.authority);
    await this.#freed();
    return true;
  }

  /** A pending resume or pause asked again later, backing off: when. */
  #later(authority: string): number {
    const row = this.#row(authority);
    const tries = (row?.tries ?? 0) + 1;
    const at = Date.now() + backoff(tries);
    this.#sql.exec("UPDATE sandboxes SET tries = ?, retry_at = ? WHERE authority = ?", tries, at,
                   authority);
    return at;
  }

  #forget(authority: string): void {
    const row = this.#row(authority);
    if (row !== null && row.paused === 0) {
      this.#log(authority, "kill");  // gone while it ran: its time ended here at the latest
    }
    if (row !== null) {
      // A renewal's slot it held is the line's again.
      this.ctx.waitUntil(this.#slots().unreserve(row.sandbox_id).catch(() => undefined));
    }
    this.#sql.exec("DELETE FROM sandboxes WHERE authority = ?", authority);
    this.#sql.exec("DELETE FROM launched WHERE authority = ?", authority);
  }

  /** The runner's state, from a runner speaking a protocol this Worker speaks. */
  async #runnerState(handle: Box): Promise<{ live: number; quiet: boolean }> {
    const out = (await this.#run(handle, ["state"])).out;
    const protocol = (JSON.parse(out) as { protocol?: unknown }).protocol;
    if (typeof protocol !== "number" || !SPEAKS.includes(protocol)) {
      const message = `sandbox_runner_protocol: the runner ${handle.runner} in sandbox ${
        handle.sandboxID} speaks protocol ${String(protocol)}; this Worker speaks ${
        SPEAKS.join(" and ")}`;
      console.error(message);
      throw new RunnerRefused(message);
    }
    return this.#shape(out);
  }

  #shape(out: string): { live: number; quiet: boolean } {
    const parsed = JSON.parse(out) as { live?: unknown; quiet?: unknown };
    if (typeof parsed.live !== "number" || typeof parsed.quiet !== "boolean") {
      throw new Error("the runner's state is out of shape");
    }
    return { live: parsed.live, quiet: parsed.quiet };
  }

  /**
   * Pause it, asking again while E2B is busy (a previous snapshot finishing: the longest
   * measured pause, doubling) until `until`, past which E2B's own timeout pauses it.
   */
  async #pause(id: string, until: number): Promise<"paused" | "busy" | "gone"> {
    for (let wait = PAUSE_MAX_MS; ; wait *= 2) {
      const paused = await this.#e2b().pause(id);
      if (paused !== "busy" || Date.now() + wait > until) {
        return paused;
      }
      await sleep(wait);
    }
  }

  /**
   * The alarm at the soonest thing a row needs: a renewal look (`end_at` less the window),
   * a resume (a paused sandbox a lease runs in, or a renewing one), a reap (a release whose
   * pause did not land). A paused, idle sandbox needs nothing. None, no alarm.
   */
  async #arm(at = Number.POSITIVE_INFINITY): Promise<void> {
    const ceilings = this.#known();
    let soonest = at;
    for (const row of this.#rows()) {
      if (row.renewing === 1 || row.releasing === 1
          || (row.paused === 1 && this.#leased(row.authority))) {
        // Asked at once the first time, then at its back-off: never at alarm speed.
        soonest = Math.min(soonest, row.retry_at ?? Date.now());
      } else if (row.paused === 1) {
        continue;  // paused and idle: nothing to do
      } else if (ceilings !== null) {
        // The renewal window, and the idle look a lease's TTL on.
        soonest = Math.min(soonest, row.end_at - ceilings.renewal_window_s * 1000,
                           (row.idle_since ?? Date.now()) + LEASE_TTL_S * 1000);
      }
    }
    if (Number.isFinite(soonest)) {
      await this.ctx.storage.setAlarm(Math.max(Date.now(), soonest));
    } else {
      await this.ctx.storage.deleteAlarm();
    }
  }

  /**
   * Each sandbox looked at. First, outside the chain, each running one in its renewal
   * window waits for a quiet moment (at most to its edge, and inside this alarm's wall
   * clock); then, on the chain, each is renewed, resumed, reaped or left. Armed again on
   * every path.
   */
  override async alarm(): Promise<void> {
    const began = Date.now();
    let next: number;
    try {
      next = await this.#look(began);
      this.#sql.exec("UPDATE known SET failures = 0");
    } catch (error) {
      // E2B unreachable, or answering out of shape: asked again, backing off from the
      // measured request time, and sooner only for an edge still ahead (one already past is
      // E2B's own timeout's to pause; the next look resumes it). Never at alarm speed.
      console.error("a sandbox look failed; it is tried again, backing off", error);
      this.#sql.exec("UPDATE known SET failures = failures + 1");
      const failures = this.#sql.exec<{ failures: number }>("SELECT failures FROM known")
        .toArray()[0]?.failures ?? 1;
      const now = Date.now();
      const ahead = this.#rows().map((row) => row.end_at - RENEW_EDGE_MS)
        .filter((edge) => edge > now);
      await this.ctx.storage.setAlarm(Math.min(now + backoff(failures), ...ahead));
      return;
    }
    await this.#arm(next);
  }

  async #look(began: number): Promise<number> {
    // Sandboxes already running are renewed on the last numbers read even when the founder
    // took them out of the record: only new starts stop.
    const ceilings = (await this.#ceilings()) ?? this.#known();
    if (ceilings === null) {
      throw new Error("no sandbox numbers were ever read, yet a sandbox is kept");
    }
    const windowMs = ceilings.renewal_window_s * 1000;
    const rows = this.#rows();
    const e2b = this.#e2b();
    // The quiet look, outside the chain: starts and cancels go on meanwhile.
    const lookEnd = began + ALARM_WALL_MS - rows.length * RENEWAL_MS;
    const quiet = new Map<string, boolean>();
    await Promise.all(rows.map(async (row) => {
      if (row.renewing === 1 || row.releasing === 1 || row.paused === 1) {
        return;
      }
      const found = await e2b.info(row.sandbox_id);
      const handle = found === null ? null : handleOf(found, row);
      if (handle === null || Date.now() < Date.parse(found!.endAt) - windowMs) {
        return;
      }
      this.#ended(found!);
      const until = Math.min(Date.parse(found!.endAt) - RENEW_EDGE_MS, lookEnd);
      let out: string;
      try {
        ({ out } = await this.#run(handle, ["quiet-wait", String(Math.floor(until))],
                                   Math.max(0, until - Date.now()) + COMMAND_MS));
      } catch (error) {
        // A start on the chain adopted another runner meanwhile, and its `prepare` dropped
        // this one's bytes: no quiet moment read here, and nothing failed.
        if (error instanceof RunnerFailed && error.exitCode === ABSENT
            && this.#row(row.authority)?.runner !== handle.runner) {
          return;
        }
        throw error;
      }
      quiet.set(row.authority, this.#shape(out).quiet);
    }));
    return this.#serial(async () => {
      let soonest = Number.POSITIVE_INFINITY;
      for (const row of this.#rows()) {
        soonest = Math.min(soonest, await this.#lookAt(row, quiet.get(row.authority) ?? false,
                                                       ceilings));
      }
      return soonest;
    });
  }

  /** One sandbox looked at, on the chain: when it next needs a look. */
  async #lookAt(row: Row, quiet: boolean, ceilings: SandboxCeilings): Promise<number> {
    const e2b = this.#e2b();
    const windowMs = ceilings.renewal_window_s * 1000;
    const found = await e2b.info(row.sandbox_id);
    if (found === null) {
      this.#forget(row.authority);
      await this.#freed();
      return Number.POSITIVE_INFINITY;
    }
    this.#ended(found);
    const handle = handleOf(found, row);
    if (handle === null) {
      if (row.renewing === 0 && !this.#leased(row.authority)) {
        this.#sql.exec("UPDATE sandboxes SET releasing = 0 WHERE authority = ?", row.authority);
        return Number.POSITIVE_INFINITY;  // paused and idle: nothing to do
      }
      // Paused while a lease runs in it (a renewal cut short, or E2B's own timeout): resumed,
      // its slot its own, held in the line until it runs. E2B refusing is retried, backing
      // off, and is the founder's alert.
      await this.#slots().reserve(found.sandboxID);
      try {
        if (await this.#resume(row, found, null, ceilings) === null) {
          this.#forget(row.authority);
          return Number.POSITIVE_INFINITY;
        }
        return this.#row(row.authority)!.end_at - windowMs;
      } catch (error) {
        if (!noRoom(error)) {
          throw error;
        }
        console.error("sandbox_renewal_refused: E2B refused to resume a sandbox a lease runs " +
                      "in; asked again");
        this.#sql.exec("UPDATE sandboxes SET renewing = 1 WHERE authority = ?", row.authority);
        return this.#later(row.authority);
      }
    }
    if (row.releasing === 1) {
      const paused = await this.#reap(row);
      const after = this.#row(row.authority);
      return after?.releasing === 1 ? this.#later(row.authority)
        : paused || after === null ? Number.POSITIVE_INFINITY : after.end_at - windowMs;
    }
    const endAt = Date.parse(found.endAt);
    const state = await this.#runnerState(handle);
    if (Date.now() < endAt - windowMs) {
      const idle = await this.#idle(row, found, state.live);
      return Number.isFinite(idle) ? Math.min(endAt - windowMs, idle) : idle;  // paused
    }
    if (state.live === 0) {
      // No run lives in it, whatever lease the owner has not yet released: idle.
      this.#sql.exec("DELETE FROM launched WHERE authority = ?", row.authority);
      if (await this.#pause(found.sandboxID, endAt) === "paused") {
        this.#paused(row.authority);
        await this.#freed();
        return Number.POSITIVE_INFINITY;
      }
      return this.#later(row.authority);
    }
    if (!quiet && !state.quiet && Date.now() < endAt - RENEW_EDGE_MS) {
      return Date.now();  // the look for a quiet moment goes on in the next alarm
    }
    await this.#renew(row, handle, endAt, ceilings);
    const after = this.#row(row.authority);
    return after === null ? Number.POSITIVE_INFINITY
      : after.renewing === 1 ? this.#later(row.authority) : after.end_at - windowMs;
  }

  /**
   * Before its window, a running sandbox's idle look: a live run clears it; none starts the
   * idle clock, and none for a lease's TTL pauses it. When it next needs a look.
   */
  async #idle(row: Row, found: SandboxInfo, live: number): Promise<number> {
    const ttlMs = LEASE_TTL_S * 1000;
    if (live > 0) {
      this.#sql.exec("UPDATE sandboxes SET idle_since = NULL WHERE authority = ?", row.authority);
      return Date.now() + ttlMs;
    }
    if (row.idle_since === null) {
      this.#sql.exec("UPDATE sandboxes SET idle_since = ? WHERE authority = ?", Date.now(),
                     row.authority);
      return Date.now() + ttlMs;
    }
    if (Date.now() < row.idle_since + ttlMs) {
      return row.idle_since + ttlMs;
    }
    this.#sql.exec("DELETE FROM launched WHERE authority = ?", row.authority);
    if (await this.#pause(found.sandboxID, Date.parse(found.endAt)) !== "paused") {
      return this.#later(row.authority);
    }
    this.#paused(row.authority);
    this.#sql.exec("UPDATE sandboxes SET idle_since = NULL WHERE authority = ?", row.authority);
    await this.#freed();
    return Number.POSITIVE_INFINITY;
  }

  /**
   * One renewal: the runner posts `renewing` and holds its posts (`renew-wait` now: every
   * live run, one launched since the quiet look included); the row is marked renewing, so a
   * look that fails after the pause resumes it; its slot is held in the line across the
   * pause and the resume (`reserve`), so the first owner waiting cannot create into the
   * moment it frees; the sandbox is paused with its memory and resumed at once.
   */
  async #renew(row: Row, handle: Box, endAt: number, ceilings: SandboxCeilings):
      Promise<void> {
    const e2b = this.#e2b();
    await this.#run(handle, ["renew-wait", String(Date.now())]);
    await this.#slots().reserve(handle.sandboxID);
    this.#sql.exec("UPDATE sandboxes SET renewing = 1 WHERE authority = ?", row.authority);
    const paused = await this.#pause(handle.sandboxID, endAt);
    if (paused === "gone") {
      this.#forget(row.authority);
      await this.#freed();
      return;
    }
    if (paused === "busy") {
      return;  // E2B's timeout pauses it with its memory; the next look resumes it
    }
    this.#paused(row.authority);
    const found = await e2b.info(handle.sandboxID);
    if (found === null) {
      this.#forget(row.authority);
      return;
    }
    try {
      await this.#resume({ ...row, renewing: 1 }, found, null, ceilings);
    } catch (error) {
      if (!noRoom(error)) {
        throw error;
      }
      console.error("sandbox_renewal_refused: E2B refused a renewal's resume; asked again");
    }
  }

  /**
   * The meter's log after `after` (slice P, `owner/src/sandbox-time.ts`): at most
   * `METER_BATCH` entries and the cursor past them. What the cursor passed is dropped: the
   * owner's object read it.
   */
  async meter(_owner: string, request: { after: number }):
      Promise<{ events: MeterEvent[]; next: number }> {
    const after = request?.after;
    if (typeof after !== "number" || !Number.isSafeInteger(after) || after < 0) {
      throw new Error("a meter read names its cursor");
    }
    this.#sql.exec("DELETE FROM meter_log WHERE seq <= ?", after);
    const events = this.#sql.exec<MeterEvent & Record<string, SqlStorageValue>>(
      "SELECT seq, authority, kind, at FROM meter_log WHERE seq > ? ORDER BY seq LIMIT ?", after,
      METER_BATCH).toArray().map(({ seq, authority, kind, at }) => ({ seq, authority, kind, at }));
    return { events, next: events.at(-1)?.seq ?? after };
  }

  /**
   * The owner erased: every sandbox of theirs killed, paused ones included, and any E2B
   * still lists under their tag (one a lost row forgot); then this object's storage
   * deleted whole, and the owner out of the platform's line.
   */
  async erase(owner: string): Promise<{ killed: number }> {
    return this.#serial(async () => {
      const e2b = this.#e2b();
      const ids = new Set(this.#rows().map((row) => row.sandbox_id));
      for (const id of await e2b.list("ta_owner", await ownerTag(owner))) {
        ids.add(id);
      }
      for (const id of ids) {
        await e2b.kill(id);
        await this.#slots().unreserve(id);
      }
      await this.#slots().leave(owner);
      await this.ctx.storage.deleteAlarm();
      await this.ctx.storage.deleteAll();
      migrate(this.ctx.storage, CONTROL_MIGRATIONS);
      if (ids.size > 0) {
        await this.#freed();
      }
      return { killed: ids.size };
    });
  }
}
