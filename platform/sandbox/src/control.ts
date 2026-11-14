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
 *    its two secrets, the sync token and its own (`/var/lib/ta/lease.json`, in the root-only
 *    state directory), never the egress token, which the network's transform carries;
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
 * asked again, once; its mismatch (25) is the alert `sandbox_runner_mismatch`, logged and
 * thrown as the contract's refusal of that code, and nothing launches. A runner speaking a
 * protocol this Worker does not (`SPEAKS`) is refused the same way
 * (`sandbox_runner_protocol`).
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
 * measured request time, never past the soonest edge), so no run dies at the ceiling. Each
 * look ends by giving the line back every slot this object owes it (a renewal's, its sandbox
 * running again or gone), so one the line did not take is asked again until it lands.
 */
import { sha256Hex } from "../../contract/src/digest";
import { AUTHORITY, HEX32, RUN_ID, SIGNIN_ID } from "../../contract/src/ids";
import { DurableObject } from "cloudflare:workers";

import { readControl, type SandboxCeilings } from "../../contract/src/control";
import { bundledSources, engineHosts } from "../../sources/src/catalog";
import { LEASE_TTL_S } from "../../contract/src/limits";
import {
  allowList, doorHeaders, EGRESS_HOST, egressTokenOf, generationOf, SANDBOX_HEADER,
  withEgressToken,
} from "../../contract/src/egress";
import { migrate } from "../../contract/src/migrate";
import { Refusal, refusalOf } from "../../contract/src/refusal";
import { SEALED_ROUTE } from "../../contract/src/routing-mac";
import {
  METER_BATCH, type MeterEvent, type NetworkApplied, type NetworkDocument, type NetworkReapply,
  type SandboxSignin, type SandboxStart, type Started,
} from "../../contract/src/sandbox";

import { install } from "../e2b/install";
import type { Exec, Handle } from "../e2b/transport";
import { type AnswerFields, ANSWERS, COMMAND, COMMAND_MS, EXIT, inboxCode, inboxDoc,
         type JobKind, JOBS, LEASE_FILE, type LeaseFile, LOADER, type QuietAnswer, SPEAKS,
         type SigninDoc, type StartDoc, type StateAnswer,
         type WorkerCommand } from "../runner/protocol";

import { type E2b, noRoom, runtimeE2b, type SandboxInfo } from "./e2b";
import { runnerBytes, runnerDigest } from "./runner-bytes";
import { CONTROL_MIGRATIONS } from "./schema";
import type { SandboxEnv } from "./env";
import { SLOTS, type SandboxSlots } from "./slots";

/** A runner command answered 0 or 3, and its output: any other exit is a fault, thrown. */
function answered(command: WorkerCommand, done: Exec): { code: number; out: string } {
  if (done.exitCode !== EXIT.ok && done.exitCode !== EXIT.no) {
    throw new Error(`ta-runner ${command} failed (${done.exitCode}): ${done.stderr}`);
  }
  return { code: done.exitCode, out: done.stdout };
}

/** Measured: the longest pause, 705 ms (`e2b-resume-measurement.md`, 40 cycles). */
const PAUSE_MAX_MS = 705;
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
const RENEW_EDGE_MS = RENEWING_MS + PAUSE_MAX_MS;
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
function backoff(tries: number): number {
  return Math.min(RETRY_BASE_MS * 2 ** Math.max(0, tries - 1), RETRY_MAX_MS);
}

/**
 * Cloudflare's wall-clock limit on one alarm handler, 15 minutes: one look for a quiet
 * moment ends inside it with room for the renewals after, and the next alarm looks on.
 */
const ALARM_WALL_MS = 15 * 60_000;

/** The control record's sandbox part, as one read gave it (`#control`). */
interface SandboxPart {
  /** Its numbers: null when it holds none, and nothing new starts. */
  readonly ceilings: SandboxCeilings | null;
  /** Its scratch retention, in days: null when it names none. */
  readonly retention: number | null;
  /** Whether the retention is the record's own, read now (a start keeps it as it hands it). */
  readonly fresh: boolean;
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
  /**
   * A re-apply's network kept while its sandbox is paused, applied as it resumes: the
   * owner's plan as it came, with no egress token (`network`), or null.
   */
  network_due: string | null;
}

/** A running sandbox's handle, and the runner's digest its commands name. */
interface Box extends Handle {
  readonly runner: string;
}

const LEASE = HEX32;

function json(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value));
}

/**
 * A sandbox's network as E2B's info shows it (E2B's answer, which no contract types): E2B's
 * whole document, or it fails loudly.
 */
function liveNetwork(found: SandboxInfo): NetworkDocument {
  const n = found.network as Record<string, unknown> | null;
  if (n === null || typeof n !== "object" || !Array.isArray(n["allowOut"])
      || !Array.isArray(n["denyOut"]) || !Array.isArray(n["dnsServers"])
      || typeof n["rules"] !== "object" || n["rules"] === null) {
    throw new Error("a sandbox network is E2B's whole document");
  }
  return { allowOut: n["allowOut"] as string[], denyOut: n["denyOut"] as string[],
           dnsServers: n["dnsServers"] as string[],
           rules: n["rules"] as NetworkDocument["rules"] };
}

/**
 * The network widened to `hosts` (a signed-in engine's vendor hosts): unchanged when it
 * already lets every name out (`*`) or lists them all; the egress door stays listed last.
 */
function widened(network: NetworkDocument, hosts: readonly string[]): NetworkDocument | null {
  if (network.allowOut.includes("*") || hosts.every((host) => network.allowOut.includes(host))) {
    return null;
  }
  return { ...network, allowOut: allowList([...network.allowOut, ...hosts]) };
}

/**
 * Whether `network` was made under a newer egress generation than `plan`: a re-apply that
 * reaches the Worker after a later one (separate calls, so not ordered) never replaces it.
 * Every network the owner makes carries its generation, so one with none fails loudly.
 */
function newer(network: unknown, plan: unknown): boolean {
  const live = generationOf(network);
  const next = generationOf(plan);
  if (live === null || next === null) {
    throw new Error("a lease's network carries the egress generation it was made under");
  }
  return live > next;
}

/**
 * A re-apply's plan (`NetworkReapply`), held to its shape: its one transform on the door
 * setting the generation header, and no egress token.
 */
function reapplyNetwork(plan: NetworkDocument): NetworkDocument {
  const headers = doorHeaders(plan);
  if (Object.keys(plan.rules).length !== 1 || plan.rules[EGRESS_HOST]?.length !== 1
      || headers === null || generationOf(plan) === null || SANDBOX_HEADER in headers) {
    throw new Error("a re-apply's network sets the door's generation header, and no token");
  }
  return plan;
}

/** The vendor's own hosts of an engine run on its sign-in; none on a key. */
function vendorHosts(start: SandboxStart): string[] {
  return start.credential !== "vendor_login_in_sandbox" ? [] : hostsOf(start.engine, null);
}

/** An engine's vendor hosts, and its region's (`engineHosts`), by the engine's id. */
function hostsOf(engine: string, region: string | null): string[] {
  const record = bundledSources().get(engine);
  if (record?.cli === undefined) {
    throw new Error(`engine ${engine} has no CLI record`);
  }
  return engineHosts(record, region);
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
 * The start's document as the runner reads it (the protocol's own, field by field): the
 * start but the lease's secrets, its network and its sealed route, with the control
 * record's scratch retention (`scratch_retention_days`, null when it names none).
 */
function startDocument(start: SandboxStart, retentionDays: number | null): StartDoc {
  const { config, lease, urls } = start;
  return {
    run: start.run, agent: start.agent, conversation: start.conversation, engine: start.engine,
    credential: start.credential, model: start.model, session_id: start.session_id,
    prompt: start.prompt, unattended: start.unattended, authority: start.authority,
    lease: { lease_id: lease.lease_id, epoch: lease.epoch },
    config: { generation: config.generation, egress: config.egress, env: config.env,
              inherit: config.inherit },
    urls: { mcp: urls.mcp, mcp_bare: urls.mcp_bare, events: urls.events, signin: urls.signin },
    scratch_retention_days: retentionDays,
  };
}

/** A sign-in's document as the runner reads it: the sign-in but its lease and its secrets. */
function signinDocument(signin: SandboxSignin): SigninDoc {
  return { id: signin.id, engine: signin.engine, region: signin.region, mode: signin.mode,
           authority: signin.authority,
           urls: { events: signin.urls.events, signin: signin.urls.signin } };
}

/**
 * The lease file a start or sign-in that opened its lease hands the runner (null for one
 * that joins it): the lease and the runner's two tokens alone. The egress token stays in
 * E2B's transform, outside.
 */
function leaseFile(request: SandboxStart | SandboxSignin): LeaseFile | null {
  const { tokens } = request;
  return tokens === null ? null
    : { lease: { lease_id: request.lease.lease_id, epoch: request.lease.epoch },
        authority: request.authority,
        tokens: { sync_token: tokens.sync_token, runner_token: tokens.runner_token } };
}

/** What a start or a sign-in hands its job's launch (`#launch`). */
interface Launch {
  readonly kind: JobKind;
  readonly id: string;
  readonly request: SandboxStart | SandboxSignin;
  /** The vendor hosts its network is widened by. */
  readonly hosts: () => readonly string[];
  /** Its document for the runner. */
  readonly document: StartDoc | SigninDoc;
  /** What it does in its sandbox before its document goes in: a refusal, or null to go on. */
  readonly ready?: (handle: Box) => Promise<Started | null>;
}

/** A runner's answer: its JSON, held to the fields the protocol says it has. */
function answerOf<T>(out: string, fields: AnswerFields<T>): T {
  const parsed = JSON.parse(out) as Record<string, unknown>;
  for (const [field, type] of Object.entries(fields)) {
    if (typeof parsed[field] !== type) {
      throw new Error("the runner's state is out of shape");
    }
  }
  return Object.fromEntries(Object.keys(fields).map((field) => [field, parsed[field]])) as T;
}

/**
 * The state `box`'s runner answered (`out`), from a runner speaking a protocol this Worker
 * speaks: one that does not is refused (`sandbox_runner_protocol`), and nothing runs on it.
 */
function stateOf(box: Box, out: string): StateAnswer {
  const protocol = (JSON.parse(out) as Partial<Record<keyof StateAnswer, unknown>>).protocol;
  if (typeof protocol !== "number" || !SPEAKS.includes(protocol)) {
    console.error(`sandbox_runner_protocol: the runner ${box.runner} in sandbox ${
      box.sandboxID} speaks protocol ${String(protocol)}; this Worker speaks ${
      SPEAKS.join(" and ")}`);
    throw new Refusal("sandbox_runner_protocol",
                      "the sandbox's runner speaks a protocol this Worker does not; nothing " +
                      "runs on it");
  }
  return answerOf<StateAnswer>(out, ANSWERS[COMMAND.state]);
}

/**
 * The metadata naming an owner's sandboxes, so an erasure finds strays the rows lost: an
 * opaque name, the first 32 hex of a SHA-256.
 */
function ownerTag(owner: string): string {
  return sha256Hex(`ta_owner\n${owner}`).slice(0, 32);
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
  readonly #digest: string;

  constructor(ctx: DurableObjectState, env: SandboxEnv) {
    super(ctx, env);
    this.#sql = ctx.storage.sql;
    migrate(ctx.storage, CONTROL_MIGRATIONS);
    this.#e2b = () => runtimeE2b(env.E2B_API_KEY);
    this.#digest = runnerDigest();
  }

  /**
   * The control record's sandbox part (`contract/src/control.ts`), read once for a start, a
   * sign-in or a look: as read, its numbers kept; or, when the record is missing, malformed
   * or unreadable, the part last kept, and said so loudly, never a silent none (none ever
   * kept: nothing starts, and no run deletes scratch by age). The numbers are kept through a
   * read that holds none, so the sandboxes already running are renewed on them (`#look`):
   * only new starts stop. Only a start keeps the retention, as it hands it to the runner
   * (`fresh`), so the fallback is the retention the last run was given.
   */
  async #control(): Promise<SandboxPart> {
    const read = await readControl(this.env.CONTROL);
    if (read.kind === "read") {
      const { sandbox: ceilings, scratch_retention_days: retention } = read.control;
      if (ceilings !== null) {
        this.#sql.exec("INSERT INTO known (only, runtime_s, concurrency, window_s) VALUES " +
                         "(1, ?, ?, ?) ON CONFLICT (only) DO UPDATE SET runtime_s = " +
                         "excluded.runtime_s, concurrency = excluded.concurrency, window_s = " +
                         "excluded.window_s", ceilings.runtime_ceiling_s,
                       ceilings.concurrency_ceiling, ceilings.renewal_window_s);
      }
      return { ceilings, retention, fresh: true };
    }
    const kept = { ceilings: this.#known(), retention: this.#sql.exec<{ days: number | null }>(
      "SELECT days FROM known_retention").toArray()[0]?.days ?? null, fresh: false };
    console.error(`the control record is ${read.kind}: the sandbox runs on the part last read ` +
                  `(numbers ${kept.ceilings === null ? "none" : "kept"}, scratch retention ${
                    kept.retention === null ? "none" : `${kept.retention} days`})`);
    return kept;
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

  /**
   * A slot this object holds in the line (a renewal's, `reserve`) owed back, its sandbox
   * running again or gone. It stays owed until the line takes it (`#giveBack`): one the line
   * did not take is asked again by the look, armed at once while any is owed, never lost.
   */
  #owe(sandboxId: string): void {
    this.#sql.exec("INSERT INTO owed (sandbox_id) VALUES (?) ON CONFLICT DO NOTHING", sandboxId);
  }

  /**
   * `sandboxId`'s slot held in the line (`reserve`) across a pause and resume. A give-back
   * still owed for it (one the line refused) is dropped before the hold, so no give-back lets
   * the hold go: the sandbox's slot is owed again only once it runs again or is gone.
   */
  async #hold(sandboxId: string): Promise<void> {
    this.#sql.exec("DELETE FROM owed WHERE sandbox_id = ?", sandboxId);
    await this.#slots().reserve(sandboxId);
  }

  /** Every slot owed given back to the line, each kept owed until it is: the first refusal thrown. */
  async #giveBack(): Promise<void> {
    const slots = this.#slots();
    for (const { sandbox_id } of this.#sql.exec<{ sandbox_id: string }>(
      "SELECT sandbox_id FROM owed").toArray()) {
      await slots.unreserve(sandbox_id);
      this.#sql.exec("DELETE FROM owed WHERE sandbox_id = ?", sandbox_id);
    }
  }

  /** A sandbox of the platform's paused or ended: the line's first is rung (logged on fault). */
  async #freed(): Promise<void> {
    try {
      await this.#slots().freed();
    } catch (error) {
      console.error("the sandbox line could not be rung; its own back-off rings it", error);
    }
  }

  /** The sandbox numbers last kept (`#control`), or null when none were ever read. */
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

  /** The vendor hosts of every job launched in the authority's sandbox under `leaseId`. */
  #launchedHosts(authority: string, leaseId: string | null): string[] {
    const hosts = new Set<string>();
    for (const { hosts: each } of this.#sql.exec<{ hosts: string }>(
      "SELECT hosts FROM launched WHERE authority = ? AND lease_id = ?", authority, leaseId)
      .toArray()) {
      for (const host of JSON.parse(each) as string[]) {
        hosts.add(host);
      }
    }
    return [...hosts];
  }

  /**
   * The re-apply kept for the authority's paused sandbox, made whole for `found` as it is
   * now: the live egress token in the door's transform, the launched jobs' vendor hosts let
   * out; or null when none is kept (or its sandbox carries no token to keep).
   */
  #due(authority: string, found: SandboxInfo): NetworkDocument | null {
    const row = this.#row(authority);
    const token = egressTokenOf(found.network);
    if (row?.network_due == null || token === null) {
      return null;
    }
    const whole = withEgressToken(reapplyNetwork(JSON.parse(row.network_due) as NetworkDocument),
                                  token);
    return widened(whole, this.#launchedHosts(authority, row.lease_id)) ?? whole;
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
   * A runner command, by the digest the sandbox runs, whatever it exits with. The loader
   * finding no runner of this Worker's digest has it installed and is asked again, once;
   * bytes that are not their digest are refused (`sandbox_runner_mismatch`). Another
   * digest absent is its exit (`EXIT.absent`), for the caller that knows that runner may be
   * gone; every other caller asks `#run`.
   */
  async #exec(box: Box, argv: readonly [WorkerCommand, ...string[]], timeoutMs = COMMAND_MS):
      Promise<Exec> {
    let done = await this.#e2b().exec(box, [LOADER, box.runner, ...argv], { timeoutMs });
    if (done.exitCode === EXIT.absent && box.runner === this.#digest) {
      await this.#install(box);
      done = await this.#e2b().exec(box, [LOADER, box.runner, ...argv], { timeoutMs });
    }
    if (done.exitCode === EXIT.mismatch) {
      console.error(`sandbox_runner_mismatch: the runner ${box.runner} in sandbox ${
        box.sandboxID} is not its digest; nothing runs on it`);
      throw new Refusal("sandbox_runner_mismatch",
                        "the sandbox's runner is not its digest; nothing runs on it");
    }
    return done;
  }

  /** A runner command (`#exec`) answered 0 or 3; any other exit is thrown. */
  async #run(box: Box, argv: readonly [WorkerCommand, ...string[]], timeoutMs = COMMAND_MS):
      Promise<{ code: number; out: string }> {
    return answered(argv[0], await this.#exec(box, argv, timeoutMs));
  }

  /** This Worker's runner uploaded into the sandbox and installed by the loader. */
  async #install(handle: Handle): Promise<void> {
    await install(this.#e2b(), handle, runnerBytes(), this.#digest, COMMAND_MS);
  }

  /**
   * The sandbox on this Worker's runner, when it runs another and nothing lives in it (no
   * run, no sign-in): installed, `adopt`ed (the disk held to its format; nothing deleted)
   * and committed to its row, its caller then running the adopted runner's `prepare`, which
   * drops the other runners' bytes. Otherwise as it is: a live run's runner stays. A
   * failure on the way keeps the runner it has (logged loudly, asked again at the next
   * start): an adoption is never what fails a start. Only a refusal (the runner's, by its
   * code) is thrown. Whether it adopted.
   */
  async #adopt(authority: string, box: Box): Promise<{ box: Box; adopted: boolean }> {
    const digest = this.#digest;
    if (box.runner === digest) {
      return { box, adopted: false };
    }
    try {
      const asked = await this.#exec(box, [COMMAND.state]);
      // Its own runner gone from the disk: nothing can live on it.
      if (asked.exitCode !== EXIT.absent
          && stateOf(box, answered(COMMAND.state, asked).out).live > 0) {
        return { box, adopted: false };
      }
      const adopted = { ...box, runner: digest };
      await this.#install(adopted);
      if ((await this.#run(adopted, [COMMAND.adopt])).code !== EXIT.ok) {
        return { box, adopted: false };  // something began living since the look
      }
      this.#sql.exec("UPDATE sandboxes SET runner = ? WHERE authority = ?", digest, authority);
      return { box: adopted, adopted: true };
    } catch (error) {
      if (refusalOf(error) !== null) {
        throw error;
      }
      console.error(`sandbox_runner_adopt_failed: sandbox ${box.sandboxID} keeps its runner ${
        box.runner}, and this Worker's ${digest} is tried again at its next start`, error);
      return { box, adopted: false };
    }
  }

  /** A sandbox just running: its resolver pinned, `/proc` hidden, its lockdown re-checked. */
  async #prepare(box: Box, network: NetworkDocument): Promise<void> {
    await this.#run(box, [COMMAND.prepare, ...network.dnsServers]);
  }

  /**
   * A paused sandbox resumed (memory restored, or rebooted when E2B kept its files only):
   * prepared again, and its runs told (`renewed`) or restarted from disk (`recover`). Null
   * when E2B has it no more. E2B's refusal for room is thrown (`noRoom`). A start's resume
   * (`adopt`) of a sandbox resumed with its memory and no renewal pending adopts this
   * Worker's runner first when nothing lives, so the one `prepare` is the adopted runner's;
   * a rebooted one's runs restart under the runner that launched them.
   */
  async #resume(row: Row, found: SandboxInfo, network: NetworkDocument | null,
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
    // A start's own network, else a re-apply kept while it was paused (`network`).
    const applied = network ?? this.#due(row.authority, found);
    if (applied !== null) {
      await e2b.network(resumed.sandboxID, applied);
      this.#sql.exec("UPDATE sandboxes SET network_due = NULL WHERE authority = ?",
                     row.authority);
    }
    if (adopt && found.pauseMode !== "filesystem" && row.renewing === 0) {
      handle = (await this.#adopt(row.authority, handle)).box;
    }
    await this.#prepare(handle, applied ?? liveNetwork(found));
    if (found.pauseMode === "filesystem") {
      await this.#run(handle, [COMMAND.recover]);  // rebooted: its runs restart from disk
    } else if (row.renewing === 1) {
      await this.#run(handle, [COMMAND.renewed]);
    }
    const after = await e2b.info(resumed.sandboxID);
    if (row.paused === 1 || this.#row(row.authority)?.paused === 1) {
      this.#log(row.authority, "resume");
    }
    this.#sql.exec("UPDATE sandboxes SET renewing = 0, paused = 0, retry_at = NULL, tries = 0, " +
                     "end_at = ? WHERE authority = ?",
                   after === null ? Date.now() : Date.parse(after.endAt), row.authority);
    // A renewal's slot, held across its pause, given back to the line.
    this.#owe(resumed.sandboxID);
    await this.#giveBack();
    return handle;
  }

  /**
   * The authority's sandbox, running: as it is, or resumed, or created with `network`
   * (only a start that opened the lease has one); or `waiting` when one more running
   * sandbox has no room or it is not the owner's turn; or `lost` when a joining run finds
   * none.
   */
  async #ensure(owner: string, authority: string, network: NetworkDocument | null,
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
        await this.#prepare(box, network ?? liveNetwork(found!));
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
        // An opaque name, so E2B's own list shows whose it is to us and to no one else.
        metadata: { ta_owner: ownerTag(owner),
                    // Sealed, so only the doors open it: the route of E2B's lifecycle events.
                    ta_route: route },
      });
      const info = await e2b.info(created.sandboxID);
      const digest = this.#digest;
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
        || typeof start.route !== "string" || !SEALED_ROUTE.test(start.route)
        || !httpsDoors(start.urls)) {
      throw new Error("a sandbox start names its run, its authority, its lease and its " +
                      "https doors on the egress host");
    }
    const { ceilings, retention, fresh } = await this.#control();
    if (ceilings === null) {
      return { started: false, code: "sandbox_unconfigured" };
    }
    return this.#launch(owner, ceilings, {
      kind: "run", id: start.run, request: start, hosts: () => vendorHosts(start),
      document: startDocument(start, retention),
      ready: async (handle) => {
        if (start.credential === "vendor_login_in_sandbox") {
          const signedIn = await this.#run(handle, [COMMAND.signinCheck, start.engine]);
          if (signedIn.code === EXIT.no) {
            return { started: false, code: "signin_required" };
          }
        }
        if (fresh) {
          this.#sql.exec("INSERT INTO known_retention (only, days) VALUES (1, ?) ON CONFLICT " +
                           "(only) DO UPDATE SET days = excluded.days", retention);
        }
        return null;
      },
    });
  }

  /**
   * The vendor's own sign-in started in the authority's sandbox (slice SIGNIN), or its
   * sign-out: the sandbox found, resumed or made as a run's start finds it (its turn in the
   * line, `sandbox_waiting` past the ceiling), its network let out to the engine's vendor
   * hosts (and the chosen region's), and the runner's `login-launch` started with the
   * sign-in's document (no secret); the sign-in holds the lease as a launched run does.
   */
  async signin(owner: string, signin: SandboxSignin): Promise<Started> {
    if (!SIGNIN_ID.test(signin.id) || !AUTHORITY.test(signin.authority)
        || !LEASE.test(signin.lease.lease_id) || (signin.tokens === null) !== (signin.network === null)
        || typeof signin.route !== "string" || !SEALED_ROUTE.test(signin.route)
        || !httpsDoors(signin.urls) || (signin.mode !== "in" && signin.mode !== "out")) {
      throw new Error("a sign-in names its id, its authority, its lease, its route and its " +
                      "https doors on the egress host");
    }
    const hosts = hostsOf(signin.engine, signin.region);
    const { ceilings } = await this.#control();
    if (ceilings === null) {
      return { started: false, code: "sandbox_unconfigured" };
    }
    return this.#launch(owner, ceilings, { kind: "signin", id: signin.id, request: signin,
                                           hosts: () => hosts,
                                           document: signinDocument(signin) });
  }

  /**
   * A job launched in its authority's sandbox, a run's or a sign-in's, on the serial chain:
   * the sandbox found, resumed or made (`#ensure`), its network widened by the job's vendor
   * hosts; a start that opened the lease hands the runner its secrets (`LEASE_FILE`); then
   * what the job does first (`ready`), its document into the inbox, and the runner's launch
   * of it, detached, which holds the lease until it is released.
   */
  async #launch(owner: string, ceilings: SandboxCeilings, job: Launch): Promise<Started> {
    const { request } = job;
    return this.#serial(async () => {
      const e2b = this.#e2b();
      const network = request.network === null ? null : request.network.network;
      const hosts = job.hosts();
      // The opener's network replaces the live one whole: it keeps the vendor hosts of every
      // job launched under this lease before it (a joining job's start that ran first).
      const kept = network === null ? []
        : [...hosts, ...this.#launchedHosts(request.authority, request.lease.lease_id)];
      const first = network === null ? null : widened(network, kept) ?? network;
      const got = await this.#ensure(owner, request.authority, first, ceilings, request.route);
      if (got === "waiting") {
        return { started: false, code: "sandbox_waiting" };
      }
      if (got === "lost") {
        return { started: false, code: "sandbox_lost" };
      }
      const { handle } = got;
      const lease = leaseFile(request);
      if (lease !== null) {
        await e2b.upload(handle, LEASE_FILE, json(lease));
        // The lease's own network is on it now: a re-apply kept for an earlier one is moot.
        this.#sql.exec("UPDATE sandboxes SET lease_id = ?, releasing = 0, network_due = NULL " +
                         "WHERE authority = ?", request.lease.lease_id, request.authority);
      } else if (hosts.length > 0) {
        // A joining job on another engine's sign-in: its vendor's hosts added to the live
        // network (read back whole, its transform with it, and replaced whole).
        const live = await e2b.info(handle.sandboxID);
        const more = live === null ? null : widened(liveNetwork(live), hosts);
        if (more !== null) {
          await e2b.network(handle.sandboxID, more);
        }
      }
      const refused = job.ready === undefined ? null : await job.ready(handle);
      if (refused !== null) {
        return refused;
      }
      await e2b.upload(handle, inboxDoc(job.id), json(job.document));
      const launched = await this.#run(handle, [JOBS[job.kind].launch, job.id]);
      if (launched.code !== EXIT.ok) {
        throw new Error(`the runner did not launch ${job.kind === "run" ? "run" : "sign-in"} ${
          job.id}`);
      }
      this.#sql.exec("INSERT OR REPLACE INTO launched (run, authority, lease_id, hosts) " +
                       "VALUES (?, ?, ?, ?)", job.id, request.authority,
                     request.lease.lease_id, JSON.stringify(hosts));
      this.#sql.exec("UPDATE sandboxes SET releasing = 0, idle_since = NULL WHERE " +
                       "authority = ?", request.authority);
      await this.#arm();
      return { started: true };
    });
  }

  /** A row's sandbox as E2B has it now (null: gone), and its handle (null: not running). */
  async #find(row: Row | null): Promise<{ found: SandboxInfo | null; handle: Box | null }> {
    const found = row === null ? null : await this.#e2b().info(row.sandbox_id);
    return { found, handle: found === null ? null : handleOf(found, row!) };
  }

  /** The authority's running sandbox, or null (none, or paused). */
  async #running(authority: string): Promise<Box | null> {
    return (await this.#find(this.#row(authority))).handle;
  }

  /**
   * A live lease's network replaced whole under a newer egress generation (the owner's
   * `runs` lane, after an egress policy write), on the serial chain: the lease's sandbox as
   * E2B has it now, the plan made whole with that sandbox's own egress token (read back from
   * its live transform; the owner holds only its hash) and widened by the vendor hosts of
   * every job launched in it under the lease, so a signed-in CLI keeps its vendor. A running
   * sandbox takes it at once (`applied`); a paused one keeps it on its row, applied as it
   * resumes (`at_resume`); a lease this Worker has no sandbox of answers `lost`.
   */
  async network(request: NetworkReapply): Promise<NetworkApplied> {
    if (typeof request !== "object" || request === null || !AUTHORITY.test(request.authority)
        || !LEASE.test(request.lease?.lease_id) || !Number.isSafeInteger(request.lease.epoch)) {
      throw new Error("a re-apply names its authority and its lease");
    }
    const plan = reapplyNetwork(request.network.network);
    return this.#serial(async () => {
      const e2b = this.#e2b();
      const row = this.#row(request.authority);
      if (row === null || row.lease_id !== request.lease.lease_id) {
        return { state: "lost" };
      }
      const found = await e2b.info(row.sandbox_id);
      const token = found === null ? null : egressTokenOf(found.network);
      if (found === null || token === null) {
        return { state: "lost" };
      }
      if (newer(found.network, plan)) {
        return { state: "applied" };  // a later re-apply is on it already
      }
      if (handleOf(found, row) === null) {
        if (row.network_due === null || !newer(JSON.parse(row.network_due), plan)) {
          this.#sql.exec("UPDATE sandboxes SET network_due = ? WHERE authority = ?",
                         JSON.stringify(plan), row.authority);
        }
        return { state: "at_resume" };
      }
      const whole = withEgressToken(plan, token);
      await e2b.network(found.sandboxID,
                        widened(whole, this.#launchedHosts(row.authority, row.lease_id)) ?? whole);
      this.#sql.exec("UPDATE sandboxes SET network_due = NULL WHERE authority = ?", row.authority);
      return { state: "applied" };
    });
  }

  /**
   * A pasted code to the live sign-in's CLI: left in the runner's root-only inbox and handed
   * over at once (`login-input`, which reads and deletes it whatever follows); whether the
   * sign-in was live to take it. A hand-over that failed drops it (`login-drop`), and a pause
   * between the two leaves it to the runner's `prepare` at the resume, which deletes every
   * code left in the inbox before anything else reads it: a code never rests on disk.
   */
  async signinInput(request: { authority: string; id: string; code: string }):
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
      await this.#e2b().upload(handle, inboxCode(request.id),
                               new TextEncoder().encode(request.code));
      try {
        return { delivered: (await this.#run(handle, [COMMAND.loginInput, request.id])).code
          === EXIT.ok };
      } catch (error) {
        await this.#run(handle, [COMMAND.loginDrop, request.id]).catch(() => undefined);
        throw error;
      }
    });
  }

  /** A live sign-in stopped (`login-cancel`). */
  async signinCancel(request: { authority: string; id: string }):
      Promise<{ cancelled: boolean }> {
    if (!SIGNIN_ID.test(request.id) || !AUTHORITY.test(request.authority)) {
      throw new Error("a sign-in's cancel names its sign-in");
    }
    return this.#serial(async () => {
      const handle = await this.#running(request.authority);
      return { cancelled: handle !== null
        && (await this.#run(handle, [JOBS.signin.cancel, request.id])).code === EXIT.ok };
    });
  }

  /** The sandbox a run was launched in, as E2B has it now (`#find`). */
  async #launchedIn(run: string): Promise<{ found: SandboxInfo | null; handle: Box | null }> {
    const at = this.#sql.exec<{ authority: string }>(
      "SELECT authority FROM launched WHERE run = ?", run).toArray()[0];
    return this.#find(at === undefined ? null : this.#row(at.authority));
  }

  async cancel(request: { run: string; lease_id: string }):
      Promise<{ cancelled: boolean }> {
    if (!RUN_ID.test(request.run)) {
      throw new Error("a cancel names its run");
    }
    return this.#serial(async () => {
      const { handle } = await this.#launchedIn(request.run);
      if (handle === null) {
        return { cancelled: false };
      }
      // SIGTERM to the CLI, then a kill after its grace; the runner then syncs and ends.
      return { cancelled: (await this.#run(handle, [JOBS.run.cancel, request.run])).code
        === EXIT.ok };
    });
  }

  async status(request: { run: string; lease_id: string }):
      Promise<{ state: "running" | "exited" | "gone" }> {
    if (!RUN_ID.test(request.run)) {
      throw new Error("a status names its run");
    }
    return this.#serial(async () => {
      const { found, handle } = await this.#launchedIn(request.run);
      if (found === null) {
        return { state: "gone" };
      }
      if (handle === null) {
        return { state: "exited" };
      }
      return { state: (await this.#run(handle, [COMMAND.status, request.run])).code === EXIT.ok
        ? "running" : "exited" };
    });
  }

  /**
   * The lease is over: its sandbox paused unless a run started in it since. A pause that
   * does not land now (E2B busy, or unreachable) is kept asked (`releasing`) and the look
   * takes it again, backing off: an idle sandbox is reaped, never left to its ceiling.
   */
  async release(request: { lease_id: string }): Promise<{ paused: boolean }> {
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
      this.#owe(row.sandbox_id);  // a renewal's slot it held: the line's again, at the look
    }
    this.#sql.exec("DELETE FROM sandboxes WHERE authority = ?", authority);
    this.#sql.exec("DELETE FROM launched WHERE authority = ?", authority);
  }

  /** The runner's state (`stateOf`). */
  async #runnerState(handle: Box): Promise<StateAnswer> {
    return stateOf(handle, (await this.#run(handle, [COMMAND.state])).out);
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
   * pause did not land); and at once while a slot is owed the line. A paused, idle sandbox
   * needs nothing. None, no alarm.
   */
  async #arm(at = Number.POSITIVE_INFINITY): Promise<void> {
    const ceilings = this.#known();
    let soonest = this.#sql.exec("SELECT 1 FROM owed LIMIT 1").toArray().length > 0
      ? Math.min(at, Date.now()) : at;
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
      // What is owed is given back whatever the look found: no slot waits on another sandbox.
      next = await this.#look(began).finally(() => this.#giveBack());
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
    const ceilings = (await this.#control()).ceilings ?? this.#known();
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
      const { found, handle } = await this.#find(row);
      if (handle === null || Date.now() < Date.parse(found!.endAt) - windowMs) {
        return;
      }
      this.#ended(found!);
      const until = Math.min(Date.parse(found!.endAt) - RENEW_EDGE_MS, lookEnd);
      const asked = await this.#exec(handle, [COMMAND.quietWait, String(Math.floor(until))],
                                     Math.max(0, until - Date.now()) + COMMAND_MS);
      // A start on the chain adopted another runner meanwhile, and its `prepare` dropped
      // this one's bytes: no quiet moment read here, and nothing failed.
      if (asked.exitCode === EXIT.absent && this.#row(row.authority)?.runner !== handle.runner) {
        return;
      }
      const { out } = answered(COMMAND.quietWait, asked);
      quiet.set(row.authority, answerOf<QuietAnswer>(out, ANSWERS[COMMAND.quietWait]).quiet);
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
      await this.#hold(found.sandboxID);
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
    await this.#run(handle, [COMMAND.renewWait, String(Date.now())]);
    await this.#hold(handle.sandboxID);
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
   * The meter's log after `after` (slice P; the owner's object reads it): at most
   * `METER_BATCH` entries and the cursor past them. What the cursor passed is dropped: the
   * owner's object read it.
   */
  async meter(request: { after: number }):
      Promise<{ events: MeterEvent[]; next: number }> {
    const after = request.after;
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
      for (const id of await e2b.list("ta_owner", ownerTag(owner))) {
        ids.add(id);
      }
      for (const id of ids) {
        await e2b.kill(id);
        await this.#slots().unreserve(id);
      }
      await this.#giveBack();
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
