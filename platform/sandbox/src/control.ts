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
 *    refused. Every resume runs `prepare` again (the lockdown re-checked, not assumed), and
 *    its row says whether that ended (`prepared`): a start that finds the sandbox running
 *    unprepared (a fault after its create, resume or adoption) prepares it first. A
 *    start that opened the lease (`tokens`) applies its network whole and hands the runner
 *    its two secrets, the sync token and its own (`/var/lib/ta/lease.json`, in the root-only
 *    state directory), never the egress token, which the network's transform carries. A
 *    sandbox made from another template than the pin (its `ta_template` metadata) is moved to
 *    it first by such a start, when nothing lives in it (`#moved`): what lives in it alone
 *    carried into a new one made from the pin, then the old one killed; a move that cannot
 *    finish leaves the start in the old one, untouched;
 * 3. the network is widened to the vendor hosts the start hands (`hosts`: its record's login
 *    and provider hosts, on the vendor's own sign-in), and a run on that sign-in runs the
 *    record's `status_argv` there first
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
 * thrown as the contract's refusal of that code, and nothing launches. Before a start or a
 * resume sends anything else to a runner not its own, the Worker asks it `state`, which every
 * protocol answers alike: one nothing lives on is adopted where it can be, whatever protocol
 * it speaks; one it keeps (something lives on it, its adoption failed, or a rebooted
 * sandbox's runs restart under it) must speak a protocol this Worker does (`SPEAKS`), or the
 * start is refused the same way (`sandbox_runner_protocol`) and nothing is sent to it.
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
 * timeout) is resumed the same way, on its runner's word: one whose runner has not answered
 * since the sandbox last came running is left paused (`heard`). A sandbox whose release
 * asked a pause that did not land, or that has no live run at its look, is paused (the idle
 * reaper); so is one whose runner has had no live run for a lease's TTL (`LEASE_TTL_S`),
 * looked at once a TTL while it runs: by then the owner's lease, renewed only by its runs'
 * heartbeats, has lapsed, so a release that never reached this Worker leaves no sandbox
 * running to its ceiling. Only a live run is renewed, and a run lives only while the owner's
 * object takes its heartbeat: a job none of whose posts it took for a lease's TTL ends
 * itself (`runner/frames.ts` `Poster`), as the object has settled it lost, so an owner
 * that cannot be reached renews no sandbox past that. The
 * look reads a runner by its `state` alone, so it reaps a sandbox whose runner speaks a
 * protocol this Worker does not as any other; but no renewal can carry on a job living on
 * such a runner, so that sandbox runs to the end of the window it was found in (`kill_at`),
 * where E2B's own timeout pauses it, and the look kills it by its E2B id.
 *
 * Each sandbox is looked at on its own: one whose look fails is logged, and the others are
 * looked at still. The alarm is armed again on every path, a failed look included (backing
 * off from the measured request time, never past the soonest edge), so no run dies at the
 * ceiling. Each look ends by giving the line back every slot this object owes it (a
 * renewal's, its sandbox running again or gone), so one the line did not take is asked again
 * until it lands.
 */
import { sha256Hex } from "../../contract/src/digest";
import { AUTHORITY, HEX32, RUN_ID, SIGNIN_ID } from "../../contract/src/ids";
import { DurableObject } from "cloudflare:workers";

import { readControl, type SandboxCeilings, SqlKeeper } from "../../contract/src/control";
import { LEASE_TTL_S } from "../../contract/src/limits";
import {
  allowList, doorHeaders, EGRESS_HOST, egressTokenOf, generationOf, SANDBOX_HEADER,
  withEgressToken,
} from "../../contract/src/egress";
import { migrate } from "../../contract/src/migrate";
import { Refusal, refusalOf } from "../../contract/src/refusal";
import { ROUTE_METADATA, routeShaped } from "../../contract/src/routing-mac";
import {
  METER_BATCH, type MeterEvent, type NetworkApplied, type NetworkDocument, type NetworkReapply,
  type SandboxSignin, type SandboxStart, type Started,
} from "../../contract/src/sandbox";

import { install } from "../e2b/install";
import type { Exec, Handle } from "../e2b/transport";
import { type AnswerFields, ANSWERS, CARRY_FILE, type CarryAnswer, COMMAND, COMMAND_MS, EXIT,
         inboxCode, inboxDoc, type JobKind, JOBS, LEASE_FILE, type LeaseFile, LOADER,
         type OwnCommand, type QuietAnswer, SPEAKS, type SigninDoc, type StartDoc,
         type StateAnswer, type WorkerCommand } from "../runner/protocol";

import { type E2b, noRoom, runtimeE2b, type SandboxInfo } from "./e2b";
import { runnerBytes, runnerDigest } from "./runner-bytes";
import { CONTROL_MIGRATIONS } from "./schema";
import type { SandboxEnv } from "./env";
import { SLOTS, type SandboxSlots } from "./slots";

/** A runner command answered 0 or 3, and its output: any other exit is a fault, thrown. */
function answered(command: WorkerCommand | OwnCommand, done: Exec):
    { code: number; out: string } {
  if (done.exitCode !== EXIT.ok && done.exitCode !== EXIT.no) {
    throw new Error(`ta-runner ${command} failed (${done.exitCode}): ${done.stderr}`);
  }
  return { code: done.exitCode, out: done.stdout };
}

/**
 * Measured: the longest of 40 pauses with 150 MiB of memory in use, 705 ms
 * (`e2b-resume-measurement.md`). One just after 6 GB were written took 7.0 s
 * (`sandbox-probe.md`): a renewal at the edge that slow lands on E2B's own pause (the edge).
 */
const PAUSE_MAX_MS = 705;
/** Measured: the longest resume call, 895 ms to the first byte (the same 40 cycles). */
const RESUME_MAX_MS = 895;
/**
 * How long a renewal waits for its runs' answers to `renewing` (`renew-wait`'s deadline), so
 * that none has a post in flight at the pause (`runner.ts` `Job`): at most two posts, the one
 * in flight and then its held frames, `ta.renewing` among them, each within the 252 ms that
 * `ta-runner renewing` took with a live run, its frame posted (`sandbox-probe.md`), and the
 * drain's look at the post in flight every 50 ms: 554 ms, 79% of the bound. A run that has
 * not answered by then (its door slow or refusing) is paused regardless: its post is sent
 * again after the resume, and the owner reads only the frames past its last `seq`.
 */
const RENEWING_MS = 700;
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

function backoff(tries: number): number {
  return Math.min(RETRY_BASE_MS * 2 ** Math.max(0, tries - 1), RETRY_MAX_MS);
}

/**
 * Whether E2B has room for one more running sandbox, a start's or a move's: E2B's count is the
 * team's, and advisory, so unknown, the create is tried and E2B's own refusal holds the plan;
 * a renewal's sandbox, paused for a moment, keeps its slot (`reserved`): counted, and with no
 * count to set it against, nothing is tried.
 */
function roomForOne(count: number | null, reserved: number, ceiling: number): boolean {
  return count === null ? reserved === 0 : count + reserved < ceiling;
}

/**
 * The longest a failed template move waits to be tried again, however often it failed: a day.
 * A move fails on a cause in the new image, envd or E2B, none of which mends inside a start, and
 * each try makes a sandbox and kills it; a day bounds how long a sandbox stays on an older image
 * once the cause is mended (`untangle-2026-11/template-move.md`).
 */
const MOVE_RETRY_MAX_MS = 24 * 60 * 60_000;

/** A failed move's wait before it is tried again: a lease's TTL, doubling, at most a day. */
function moveBackoff(tries: number): number {
  return Math.min(LEASE_TTL_S * 1000 * 2 ** Math.max(0, tries - 1), MOVE_RETRY_MAX_MS);
}

/**
 * Cloudflare's wall-clock limit on one alarm handler, 15 minutes: one look for a quiet
 * moment ends inside it with room for the renewals after, and the next alarm looks on.
 */
const ALARM_WALL_MS = 15 * 60_000;

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
  /**
   * When the look kills it (ms): a job lives in it on a runner this Worker does not speak, so
   * no renewal carries it past the window it was found in, whose end this is (`#heard`).
   */
  kill_at: number | null;
  /**
   * 1 once its runner answered (its `state`, or a job's launch) since the sandbox last came
   * running, created or resumed: paused, it is resumed for its lease only on that word.
   */
  heard: number;
  /**
   * 1 once its runner's `prepare` ended since the sandbox last came running or took another
   * runner: a start that finds it running with 0 (a fault after its create, resume or
   * adoption, before `prepare` ended) prepares it before anything launches in it.
   */
  prepared: number;
  /** The sandbox a move made while it is not yet the authority's (`#move`), or null. */
  move_to: string | null;
  /** A move's failures in a row, and when it is tried again (ms; `moveBackoff`). */
  move_tries: number;
  move_after: number | null;
}

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

/** A job's vendor hosts as the owner hands them (`SandboxStart.hosts`): a list of names. */
function hostList(hosts: unknown): boolean {
  return Array.isArray(hosts) && hosts.every((host) => typeof host === "string");
}

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
 * start but the lease's secrets, its network and its sealed route, with the agent's own MCP
 * servers, each by its URL or its command, and its own hooks by event.
 */
function startDocument(start: SandboxStart): StartDoc {
  const { config, lease, urls } = start;
  return {
    run: start.run, agent: start.agent, conversation: start.conversation, engine: start.engine,
    credential: start.credential, model: start.model, session_id: start.session_id,
    prompt: start.prompt, unattended: start.unattended, authority: start.authority,
    lease: { lease_id: lease.lease_id, epoch: lease.epoch },
    config: { generation: config.generation, egress: config.egress, env: config.env,
              inherit: config.inherit },
    urls: { mcp: urls.mcp, mcp_bare: urls.mcp_bare, events: urls.events, signin: urls.signin },
    mcp_servers: Object.fromEntries(Object.entries(start.mcp_servers).map(([name, server]) =>
      [name, "url" in server ? { url: server.url }
        : { command: server.command, args: server.args, env: server.env }])),
    hooks: Object.fromEntries(Object.entries(start.hooks).map(([event, hooks]) =>
      [event, hooks.map((hook) => ({ run: hook.run,
                                     ...(hook.match === undefined ? {} : { match: hook.match }),
                                     ...(hook.timeout_s === undefined ? {}
                                       : { timeout_s: hook.timeout_s }) }))])),
  };
}

/**
 * A sign-in's document as the runner reads it: the sign-in but its lease and its secrets.
 */
function signinDocument(signin: SandboxSignin): SigninDoc {
  return { id: signin.id, engine: signin.engine, mode: signin.mode,
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
 * A runner's answer to `state`, which every protocol answers alike (`state-format.test.ts`),
 * so it is read from any runner, one speaking a protocol this Worker does not included.
 */
function stateOf(asked: Exec): StateAnswer {
  return answerOf<StateAnswer>(answered(COMMAND.state, asked).out, ANSWERS[COMMAND.state]);
}

/**
 * The metadata naming an owner's sandboxes, so an erasure finds strays the rows lost: an
 * opaque name, the first 32 hex of a SHA-256.
 */
function ownerTag(owner: string): string {
  return sha256Hex(`ta_owner\n${owner}`).slice(0, 32);
}

/**
 * The metadata naming the template a sandbox was made from (`E2B_TEMPLATE` then), read back
 * from E2B's own record of it (`info`): a sandbox on another is moved (`#moved`). One made
 * before any named it is on another.
 */
const TEMPLATE_METADATA = "ta_template";

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
  readonly #digest: string;
  /** The control record's last good copy, in this object's own SQLite. */
  readonly #kept: SqlKeeper;

  constructor(ctx: DurableObjectState, env: SandboxEnv) {
    super(ctx, env);
    this.#sql = ctx.storage.sql;
    migrate(ctx.storage, CONTROL_MIGRATIONS);
    this.#kept = new SqlKeeper(ctx.storage.sql);
    this.#e2b = () => runtimeE2b(env.E2B_API_KEY);
    this.#digest = runnerDigest();
  }

  /**
   * The owner this object is named by (`ctx.id.name`, which the runtime sets and keeps for its
   * alarms): the one source of the owner its calls act for, so no method takes one. One reached
   * with no name (`idFromString`, `newUniqueId`) fails before it acts for anyone.
   */
  get #owner(): string {
    const named = this.ctx.id.name;
    if (named === undefined) {
      throw new Error("a sandbox control is not named by an owner");
    }
    return named;
  }

  /**
   * The control record's sandbox part (`contract/src/control.ts`), read once for a start, a
   * sign-in or a look: as read, its numbers kept in `known`; as the copy this object last read
   * well formed (`#kept`) when KV can't be read, `known` left as it is (`FRESH_ONLY`); and
   * none when the record is missing, malformed or unreadable with no copy, so nothing new
   * starts. The numbers in `known` outlast a read that holds none, so the sandboxes already
   * running are renewed on them (`#look`): only new starts stop.
   */
  async #control(): Promise<SandboxCeilings | null> {
    const read = await readControl(this.env.CONTROL, this.#kept);
    if (read.kind !== "read" && read.kind !== "kept") {
      return null;
    }
    const ceilings = read.control.sandbox;
    if (read.kind === "read" && ceilings !== null) {
      this.#sql.exec("INSERT INTO known (only, runtime_s, concurrency, window_s) VALUES " +
                       "(1, ?, ?, ?) ON CONFLICT (only) DO UPDATE SET runtime_s = " +
                       "excluded.runtime_s, concurrency = excluded.concurrency, window_s = " +
                       "excluded.window_s", ceilings.runtime_ceiling_s,
                     ceilings.concurrency_ceiling, ceilings.renewal_window_s);
    }
    return ceilings;
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

  /**
   * The owner's start got its sandbox: out of the line, its next rung (logged on fault). A
   * start that resumed or made a sandbox is never failed for it, since a refusal of the start
   * says nothing began (`refusal.ts`), and the line's own back-off rings the owner, which
   * leaves once no run of its waits (`sandboxRung`).
   */
  async #took(): Promise<void> {
    try {
      await this.#slots().took(this.#owner);
    } catch (error) {
      console.error("the sandbox line did not take a start's leave; its own back-off rings " +
                    "the owner", error);
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
  async #exec(box: Box, argv: readonly [WorkerCommand | OwnCommand, ...string[]],
              timeoutMs = COMMAND_MS): Promise<Exec> {
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

  async #run(box: Box, argv: readonly [WorkerCommand, ...string[]], timeoutMs = COMMAND_MS):
      Promise<{ code: number; out: string }> {
    return answered(argv[0], await this.#exec(box, argv, timeoutMs));
  }

  /**
   * A command only this Worker's own runner takes (`OWN_COMMANDS`, a move's carry): never sent
   * to a runner it keeps, whatever that one speaks.
   */
  async #own(box: Box, argv: readonly [OwnCommand, ...string[]]):
      Promise<{ code: number; out: string }> {
    if (box.runner !== this.#digest) {
      throw new Error(`${argv[0]} goes to this Worker's own runner alone`);
    }
    return answered(argv[0], await this.#exec(box, argv));
  }

  async #install(handle: Handle): Promise<void> {
    await install(this.#e2b(), handle, runnerBytes(), this.#digest, COMMAND_MS);
  }

  /**
   * The sandbox's runner as a start or a resume finds it (`found`, as E2B told it before), made
   * one this Worker speaks to before anything else is sent to it. Its own is as it is. Another
   * is asked `state`, which every protocol answers alike, and what follows is kept (`#heard`):
   * when nothing lives in it (no run, no sign-in) and `adoptable`, this Worker's is installed,
   * `adopt`ed (the disk held to its format; nothing deleted) and committed to its row, its
   * caller then running the adopted runner's `prepare`, which drops the other runners' bytes.
   * A runner kept (a live run's, a rebooted sandbox's, a renewal's, or one whose adoption
   * failed) must speak a protocol this Worker speaks, or it is refused
   * (`sandbox_runner_protocol`); one whose `state` fails is a fault, thrown, never gone on
   * with. An adoption that fails on the way keeps the runner it has (logged loudly, asked
   * again at the next start), so an adoption alone never fails a start. An adoption marks
   * the sandbox unprepared (`#adopt`), so its caller runs the adopted runner's `prepare`.
   */
  async #speaking(authority: string, box: Box, adoptable: boolean, found: SandboxInfo):
      Promise<Box> {
    if (box.runner === this.#digest) {
      return box;
    }
    const asked = await this.#exec(box, [COMMAND.state]);
    // Its own runner gone from the disk: nothing can live on it.
    const state = asked.exitCode === EXIT.absent ? null : stateOf(asked);
    const spoken = state === null || this.#heard(authority, state, found).spoken;
    if (adoptable && (state === null || state.live === 0) && await this.#adopt(authority, box)) {
      return { ...box, runner: this.#digest };
    }
    if (state === null) {
      throw new Error(`the runner ${box.runner} is gone from sandbox ${box.sandboxID}, and ` +
                      "this Worker's is not adopted");
    }
    if (!spoken) {
      console.error(`sandbox_runner_protocol: the runner ${box.runner} in sandbox ${
        box.sandboxID} speaks protocol ${state.protocol}; this Worker speaks ${
        SPEAKS.join(" and ")}`);
      throw new Refusal("sandbox_runner_protocol",
                        "the sandbox's runner speaks a protocol this Worker does not; nothing " +
                        "runs on it");
    }
    return box;
  }

  /**
   * What follows from a runner's `state` (its sandbox `found` as E2B told it), kept on its row:
   * whether this Worker speaks its protocol (`SPEAKS`). One it does not is sent nothing but
   * `state`, so no renewal carries its sandbox on (one pending is dropped), and a job living on
   * it ends with the window it was found in: the look kills the sandbox then (`kill_at`, kept
   * from the first look that saw it, so a resume that moved E2B's timeout since moves it not).
   * Nothing living, or a runner it speaks, and nothing ends.
   */
  #heard(authority: string, state: StateAnswer, found: SandboxInfo):
      { spoken: boolean; killAt: number | null } {
    const spoken = SPEAKS.includes(state.protocol);
    const kept = this.#row(authority)?.kill_at ?? null;
    const killAt = spoken || state.live === 0 ? null : kept ?? Date.parse(found.endAt);
    if (killAt !== null && kept === null) {
      console.error(`sandbox_runner_protocol: ${state.live} job(s) live in sandbox ${
        found.sandboxID} on a runner speaking protocol ${state.protocol}, which no renewal of ${
        ""}this Worker's carries on: it is killed at its window's end, ${found.endAt}`);
    }
    this.#sql.exec("UPDATE sandboxes SET kill_at = ?, heard = 1 WHERE authority = ?", killAt,
                   authority);
    if (!spoken) {
      this.#sql.exec("UPDATE sandboxes SET renewing = 0 WHERE authority = ?", authority);
    }
    return { spoken, killAt };
  }

  /** `box` on this Worker's runner, nothing living in it: whether it adopted. */
  async #adopt(authority: string, box: Box): Promise<boolean> {
    const digest = this.#digest;
    const adopted = { ...box, runner: digest };
    try {
      await this.#install(adopted);
      if ((await this.#run(adopted, [COMMAND.adopt])).code !== EXIT.ok) {
        return false;  // something began living since the look
      }
      this.#sql.exec("UPDATE sandboxes SET runner = ?, prepared = 0 WHERE authority = ?", digest,
                     authority);
      return true;
    } catch (error) {
      if (refusalOf(error) !== null) {
        throw error;
      }
      console.error(`sandbox_runner_adopt_failed: sandbox ${box.sandboxID} keeps its runner ${
        box.runner}, and this Worker's ${digest} is tried again at its next start`, error);
      return false;
    }
  }

  /**
   * A sandbox just running, or on another runner: its resolver pinned, `/proc` hidden, its
   * lockdown re-checked, and its row marked prepared once that ended.
   */
  async #prepare(box: Box, network: NetworkDocument): Promise<void> {
    await this.#run(box, [COMMAND.prepare, ...network.dnsServers]);
    this.#sql.exec("UPDATE sandboxes SET prepared = 1 WHERE sandbox_id = ?", box.sandboxID);
  }

  /**
   * A paused sandbox resumed (memory restored, or rebooted when E2B kept its files only):
   * prepared again, and its runs told (`renewed`) or restarted from disk (`recover`). Null
   * when E2B has it no more. E2B's refusal for room is thrown (`noRoom`). Every resume, a
   * start's or the look's, makes its runner one this Worker speaks to first (`#speaking`): one
   * resumed with its memory and no renewal pending adopts this Worker's when nothing lives, so
   * the one `prepare` is the adopted runner's; a rebooted one's runs restart under the runner
   * that launched them, and a renewal's runs go on under theirs.
   */
  async #resume(row: Row, found: SandboxInfo, network: NetworkDocument | null,
                ceilings: SandboxCeilings): Promise<Box | null> {
    const e2b = this.#e2b();
    this.#ended(found);  // a pause E2B made on its own is logged before the resume
    const resumed = await e2b.connect(found.sandboxID, ceilings.runtime_ceiling_s);
    if (resumed === null) {
      return null;
    }
    // Running from here, so its row says so, its runner unheard until it answers and the
    // sandbox unprepared until its `prepare` ends: a fault or refusal on the way leaves it to
    // the look (armed by `#launch`), never billed unseen as a paused one to its ceiling, or to
    // the next start, which prepares it first. A slot a renewal held for it is owed back from
    // here too, so no fault after keeps it held.
    if (row.paused === 1 || this.#row(row.authority)?.paused === 1) {
      this.#log(row.authority, "resume");
    }
    this.#sql.exec("UPDATE sandboxes SET paused = 0, heard = 0, prepared = 0 WHERE authority = ?",
                   row.authority);
    this.#owe(resumed.sandboxID);
    // Its own runner: the one that wrote its disk and launched its runs.
    let handle: Box = { sandboxID: resumed.sandboxID, envdAccessToken: resumed.envdAccessToken,
                        runner: row.runner };
    const applied = network ?? this.#due(row.authority, found);
    if (applied !== null) {
      await e2b.network(resumed.sandboxID, applied);
      this.#sql.exec("UPDATE sandboxes SET network_due = NULL WHERE authority = ?",
                     row.authority);
    }
    handle = await this.#speaking(row.authority, handle,
                                  found.pauseMode !== "filesystem" && row.renewing === 0, found);
    await this.#prepare(handle, applied ?? liveNetwork(found));
    if (found.pauseMode === "filesystem") {
      await this.#run(handle, [COMMAND.recover]);  // rebooted: its runs restart from disk
    } else if (row.renewing === 1) {
      await this.#run(handle, [COMMAND.renewed]);
    }
    const after = await e2b.info(resumed.sandboxID);
    this.#sql.exec("UPDATE sandboxes SET renewing = 0, retry_at = NULL, tries = 0, end_at = ? " +
                     "WHERE authority = ?",
                   after === null ? Date.now() : Date.parse(after.endAt), row.authority);
    await this.#giveBack();
    return handle;
  }

  /**
   * The authority's sandbox, running: as it is, or resumed, or created with `network`
   * (only a start that opened the lease has one); or `waiting` when one more running
   * sandbox has no room or it is not the owner's turn; or `lost` when a joining run finds
   * none.
   */
  async #ensure(authority: string, network: NetworkDocument | null,
                ceilings: SandboxCeilings, route: string):
      Promise<{ handle: Box } | "waiting" | "lost"> {
    const e2b = this.#e2b();
    // What a move cut short left is settled first; one E2B will not kill yet is the look's.
    await this.#settle().catch((left: unknown) => {
      console.error("sandbox_move_failed: what a move left is killed by the look", left);
    });
    const row = this.#row(authority);
    const found = row === null ? null : await e2b.info(row.sandbox_id);
    const running = found === null ? null : handleOf(found, row!);
    if (running !== null) {
      this.#ended(found!);
      if (network !== null) {
        await e2b.network(running.sandboxID, network);
      }
      const box = await this.#speaking(authority, running, true, found!);
      if (this.#row(authority)!.prepared === 0) {
        // Prepared before anything launches in it: a fault after its create, its resume or
        // an adoption left it unprepared, or the runner it adopted now runs its own
        // `prepare`, the row naming it, which drops the other runners' bytes.
        await this.#prepare(box, network ?? liveNetwork(found!));
      }
      return { handle: network === null ? box
        : await this.#moved(authority, box, found!, network, ceilings, route) };
    }
    if (found === null && network === null) {
      return "lost";  // a joining run's sandbox is gone: nothing configures a new one
    }
    const slots = this.#slots();
    const wait = async () => {
      await slots.wait(this.#owner, ceilings.runtime_ceiling_s);
      return "waiting" as const;
    };
    if (row !== null && row.renewing === 1 && found !== null) {
      // A renewal's own sandbox keeps its slot: resumed without its turn.
      const handle = await this.#resume(row, found, network, ceilings);
      return handle === null ? "lost" : { handle };
    }
    const { turn, reserved } = await slots.turn(this.#owner);
    if (!turn) {
      return wait();
    }
    try {
      if (!roomForOne(await e2b.running(), reserved, ceilings.concurrency_ceiling)) {
        return await wait();
      }
      if (found !== null) {
        const handle = await this.#resume(row!, found, network, ceilings);
        if (handle !== null) {
          await this.#took();
          return { handle: network === null ? handle
            : await this.#moved(authority, handle, found, network, ceilings, route) };
        }
        if (network === null) {
          return "lost";
        }
      }
      const created = await this.#create(network!, ceilings, route);
      // Named before anything else awaits, as a move's is: a fault reading its window after
      // leaves it the authority's, never unnamed. Its window is the create's own timeout until
      // E2B's read says it to the moment.
      this.#replaced(authority, created.sandboxID,
                     Date.now() + ceilings.runtime_ceiling_s * 1000, 0);
      const info = await e2b.info(created.sandboxID);
      if (info !== null) {
        this.#sql.exec("UPDATE sandboxes SET end_at = ? WHERE sandbox_id = ?",
                       Date.parse(info.endAt), created.sandboxID);
      }
      await this.#took();
      // The template holds no runner: this Worker's, installed before its first command.
      const box = { ...created, runner: this.#digest };
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

  /** A new sandbox of the authority's from the pinned template, with `network`. */
  async #create(network: NetworkDocument, ceilings: SandboxCeilings, route: string):
      Promise<Handle> {
    return this.#e2b().create({
      template: this.env.E2B_TEMPLATE, timeoutS: ceilings.runtime_ceiling_s, network,
      // An opaque name, so E2B's own list shows whose it is to us and to no one else.
      metadata: { ta_owner: ownerTag(this.#owner),
                  // Sealed, so only the doors open it: the route of E2B's lifecycle events.
                  [ROUTE_METADATA]: route,
                  [TEMPLATE_METADATA]: this.env.E2B_TEMPLATE },
    });
  }

  /**
   * The authority's sandbox now `sandboxId`, made from the pin: the one before forgotten as the
   * look forgets one gone (its time logged, its runs, its window, its kill and its held slot are
   * none of the new one's), its row made, and its start logged.
   */
  #replaced(authority: string, sandboxId: string, endAt: number, prepared: 0 | 1): void {
    this.#forget(authority);
    this.#sql.exec("INSERT INTO sandboxes (authority, sandbox_id, lease_id, end_at, runner, " +
                     "prepared) VALUES (?, ?, NULL, ?, ?, ?)", authority, sandboxId, endAt,
                   this.#digest, prepared);
    this.#log(authority, "start");
  }

  /**
   * The authority's sandbox (`box`, running on this Worker's runner, as E2B told of it before:
   * `found`) moved to the pinned template when it is on another and nothing lives in it
   * (`untangle-2026-11/template-move.md`, `#move`): the new one, or `box` as it was. A move is
   * never a start's refusal: one that fails at any step is logged (`sandbox_move_failed`), what
   * it made is killed, the archive it left in `box` dropped, and the start goes on in `box`; it
   * is tried again past a back-off (`moveBackoff`), so a cause that does not mend costs no start
   * a sandbox made and killed.
   */
  async #moved(authority: string, box: Box, found: SandboxInfo, network: NetworkDocument,
               ceilings: SandboxCeilings, route: string): Promise<Box> {
    const from = found.metadata[TEMPLATE_METADATA] ?? null;
    const row = this.#row(authority)!;
    if (from === this.env.E2B_TEMPLATE || box.runner !== this.#digest
        || (row.move_after !== null && Date.now() < row.move_after)) {
      return box;
    }
    try {
      return await this.#move(authority, box, from, network, ceilings, route) ?? box;
    } catch (error) {
      const tries = row.move_tries + 1;
      const after = Date.now() + moveBackoff(tries);
      this.#sql.exec("UPDATE sandboxes SET move_tries = ?, move_after = ? WHERE authority = ?",
                     tries, after, authority);
      console.error(`sandbox_move_failed: sandbox ${box.sandboxID} stays on ${String(from)}; ` +
                    `its move is tried again from ${new Date(after).toISOString()}`, error);
      await this.#own(box, [COMMAND.carryDrop]).catch((dropped: unknown) => {
        console.error(`sandbox_move_failed: sandbox ${box.sandboxID}'s archive was not dropped; ` +
                      "its next prepare drops it", dropped);
      });
      await this.#settle().catch((left: unknown) => {
        console.error("sandbox_move_failed: the sandbox the move made is killed by the look", left);
      });
      return box;
    }
  }

  /**
   * One move, each step's state kept before the next, so a fault or the object's end at any
   * point leaves nothing unnamed and no credential with no row: the room for one more sandbox
   * by the line's own rule (`roomForOne`); `box`'s runner packs what lives in it alone
   * (`carry-out`; refused while anything lives); a new sandbox made from the pin with the
   * opening start's `network`, named on the row (`move_to`) before anything else awaits, so
   * while it holds nothing of the owner's; its runner installed and prepared; the carry copied
   * across (`copy`, one stream through envd) and put in place (`carry-in`). From there the new
   * sandbox is the owner's: in one transaction the row names it and `box` is marked to be killed
   * (`retiring`), and only then killed (`#settle`), so a kill whose reply is lost, or never
   * sent, leaves `box` to the look, never the new one. Null when it moves nothing now: something
   * lives, or no room (each tried again at the next idle start).
   */
  async #move(authority: string, box: Box, from: string | null, network: NetworkDocument,
              ceilings: SandboxCeilings, route: string): Promise<Box | null> {
    const e2b = this.#e2b();
    const { reserved } = await this.#slots().turn(this.#owner);
    if (!roomForOne(await e2b.running(), reserved, ceilings.concurrency_ceiling)) {
      return null;
    }
    const out = await this.#own(box, [COMMAND.carryOut, authority]);
    if (out.code === EXIT.no) {
      return null;
    }
    const carry = answerOf<CarryAnswer>(out.out, ANSWERS[COMMAND.carryOut]);
    const created = await this.#create(network, ceilings, route);
    this.#sql.exec("UPDATE sandboxes SET move_to = ? WHERE authority = ?", created.sandboxID,
                   authority);
    const made: Box = { ...created, runner: this.#digest };
    await this.#install(made);
    await this.#run(made, [COMMAND.prepare, ...network.dnsServers]);
    await e2b.copy(box, made, CARRY_FILE, carry.bytes);
    if ((await this.#own(made, [COMMAND.carryIn, carry.sha256])).code !== EXIT.ok) {
      throw new Error("the new sandbox's runner did not take the carry");
    }
    const info = await e2b.info(made.sandboxID);
    if (info === null) {
      throw new Error(`the new sandbox ${made.sandboxID} is gone`);
    }
    this.ctx.storage.transactionSync(() => {
      this.#replaced(authority, made.sandboxID, Date.parse(info.endAt), 1);
      this.#sql.exec("INSERT INTO retiring (sandbox_id) VALUES (?) ON CONFLICT DO NOTHING",
                     box.sandboxID);
    });
    console.log(`sandbox_moved: sandbox ${box.sandboxID} on ${String(from)} is now ${
      made.sandboxID} on ${this.env.E2B_TEMPLATE}`);
    await this.#settle().catch((left: unknown) => {
      console.error(`sandbox_move_failed: sandbox ${box.sandboxID}, which a move left, is ` +
                    "killed by the look", left);
    });
    return made;
  }

  /**
   * What moves left, settled on the chain: a sandbox a move made that is not yet the
   * authority's (`move_to`: the move went no further, or this object ended under it) killed by
   * its id, and each sandbox a move left (`retiring`) killed; each kept until E2B's kill
   * answers, so the look asks again (`#arm`). Neither holds a run; the line is rung for each.
   */
  async #settle(): Promise<void> {
    const e2b = this.#e2b();
    let killed = false;
    for (const { authority, move_to: made } of this.#sql.exec<{ authority: string;
                                                                move_to: string }>(
      "SELECT authority, move_to FROM sandboxes WHERE move_to IS NOT NULL").toArray()) {
      await e2b.kill(made);
      this.#sql.exec("UPDATE sandboxes SET move_to = NULL WHERE authority = ?", authority);
      killed = true;
    }
    for (const { sandbox_id } of this.#sql.exec<{ sandbox_id: string }>(
      "SELECT sandbox_id FROM retiring").toArray()) {
      await e2b.kill(sandbox_id);
      this.#sql.exec("DELETE FROM retiring WHERE sandbox_id = ?", sandbox_id);
      killed = true;
    }
    if (killed) {
      await this.#freed();
    }
  }

  async start(start: SandboxStart): Promise<Started> {
    return this.#launch(async () => {
      if (!RUN_ID.test(start.run) || !AUTHORITY.test(start.authority)
          || typeof start.model !== "string" || start.model === ""
          || !LEASE.test(start.lease.lease_id)
          || (start.tokens === null) !== (start.network === null)
          || !routeShaped(start.route)
          || !httpsDoors(start.urls) || !hostList(start.hosts)) {
        throw new Error("a sandbox start names its run, its model, its authority, its lease, " +
                        "its vendor hosts and its https doors on the egress host");
      }
      const ceilings = await this.#control();
      if (ceilings === null) {
        return { started: false, code: "sandbox_unconfigured" };
      }
      return { ceilings, job: {
        kind: "run", id: start.run, request: start, document: startDocument(start),
        ready: async (handle) => {
          if (start.credential === "vendor_login_in_sandbox") {
            const signedIn = await this.#run(handle, [COMMAND.signinCheck, start.engine]);
            if (signedIn.code === EXIT.no) {
              return { started: false, code: "signin_required" };
            }
          }
          return null;
        },
      } };
    });
  }

  /**
   * The vendor's own sign-in started in the authority's sandbox, or its
   * sign-out: the sandbox found, resumed or made as a run's start finds it (its turn in the
   * line, `sandbox_waiting` past the ceiling), its network let out to the vendor hosts it
   * hands (the engine's), and the runner's `login-launch` started
   * with the sign-in's document (no secret); the sign-in holds the lease as a launched run
   * does.
   */
  async signin(signin: SandboxSignin): Promise<Started> {
    return this.#launch(async () => {
      if (!SIGNIN_ID.test(signin.id) || !AUTHORITY.test(signin.authority)
          || !LEASE.test(signin.lease.lease_id)
          || (signin.tokens === null) !== (signin.network === null)
          || !routeShaped(signin.route)
          || !httpsDoors(signin.urls) || (signin.mode !== "in" && signin.mode !== "out")
          || !hostList(signin.hosts)) {
        throw new Error("a sign-in names its id, its authority, its lease, its route, its " +
                        "vendor hosts and its https doors on the egress host");
      }
      const ceilings = await this.#control();
      if (ceilings === null) {
        return { started: false, code: "sandbox_unconfigured" };
      }
      return { ceilings, job: { kind: "signin", id: signin.id, request: signin,
                                document: signinDocument(signin) } };
    });
  }

  /**
   * A job launched in its authority's sandbox, a run's or a sign-in's: its request checked and
   * the control record read (`made`: the job and the ceilings, or a start code), then, on the
   * serial chain, the sandbox found, resumed or made (`#ensure`), its network widened by the
   * job's vendor hosts; a start that opened the lease hands the runner its secrets
   * (`LEASE_FILE`); then what the job does first (`ready`), its document into the inbox, and
   * the runner's launch of it, detached, which holds the lease until it is released; the look
   * armed on every path.
   *
   * Until the launch command is sent, nothing of the job runs: a fault there that is not a
   * refusal already (the runner's two alerts, the runtime's overload) is logged with its cause
   * and refused `sandbox_start_failed`, so the owner fails the job, and stops nothing. From
   * the launch command on, the job may run, so a fault stays a plain error: whether it
   * launched is unknown, and the owner stops it in case.
   */
  async #launch(made: () => Promise<{ ceilings: SandboxCeilings; job: Launch } | Started>):
      Promise<Started> {
    let launching = false;
    try {
      const got = await made();
      if ("started" in got) {
        return got;
      }
      const { ceilings, job } = got;
      const { request } = job;
      const launch = async (): Promise<Started> => {
        const e2b = this.#e2b();
        const network = request.network === null ? null : request.network.network;
        const { hosts } = request;
        // The opener's network replaces the live one whole: it keeps the vendor hosts of every
        // job launched under this lease before it (a joining job's start that ran first).
        const kept = network === null ? []
          : [...hosts, ...this.#launchedHosts(request.authority, request.lease.lease_id)];
        const first = network === null ? null : widened(network, kept) ?? network;
        const ensured = await this.#ensure(request.authority, first, ceilings, request.route);
        if (ensured === "waiting") {
          return { started: false, code: "sandbox_waiting" };
        }
        if (ensured === "lost") {
          return { started: false, code: "sandbox_lost" };
        }
        const { handle } = ensured;
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
        launching = true;
        const launched = await this.#run(handle, [JOBS[job.kind].launch, job.id]);
        if (launched.code !== EXIT.ok) {
          throw new Error(`the runner did not launch ${job.kind === "run" ? "run" : "sign-in"} ${
            job.id}`);
        }
        this.#sql.exec("INSERT OR REPLACE INTO launched (run, authority, lease_id, hosts) " +
                         "VALUES (?, ?, ?, ?)", job.id, request.authority,
                       request.lease.lease_id, JSON.stringify(hosts));
        this.#sql.exec("UPDATE sandboxes SET releasing = 0, idle_since = NULL, heard = 1 " +
                         "WHERE authority = ?", request.authority);
        return { started: true };
      };
      // Whatever it came to, the look is armed: a sandbox a start resumed or made and no job
      // launched in (its lease, if any, never written to its row) is reaped idle, not left
      // running to its ceiling; a slot owed the line is given back.
      return await this.#serial(() => launch().finally(() => this.#arm()));
    } catch (error) {
      if (launching || refusalOf(error) !== null) {
        throw error;
      }
      console.error("sandbox_start_failed: a job's start failed before its launch, so " +
                    "nothing launched", error);
      throw new Refusal("sandbox_start_failed",
                        "the sandbox failed before the job launched; nothing launched");
    }
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

  /**
   * A run's CLI stopped in the sandbox it was launched in (`launched`), whichever lease that
   * was: the run's id names its one job, and the owner's object, which alone calls, settles
   * the run whatever this answers.
   */
  async cancel(request: { run: string }): Promise<{ cancelled: boolean }> {
    if (!RUN_ID.test(request.run)) {
      throw new Error("a cancel names its run");
    }
    return this.#serial(async () => {
      const at = this.#sql.exec<{ authority: string }>(
        "SELECT authority FROM launched WHERE run = ?", request.run).toArray()[0];
      const handle = at === undefined ? null : await this.#running(at.authority);
      if (handle === null) {
        return { cancelled: false };
      }
      // SIGTERM to the CLI, then a kill after its grace; the runner then syncs and ends.
      return { cancelled: (await this.#run(handle, [JOBS.run.cancel, request.run])).code
        === EXIT.ok };
    });
  }

  /**
   * The lease is over: its sandbox paused once nothing lives in it, unless a job launched in
   * it since (`#launch`, which ends the ask). A pause that cannot land now (a job of the
   * lease still ending: stopped, or its posts refused; E2B busy, or unreachable) is kept
   * asked (`releasing`) and the look takes it again, backing off: an idle sandbox is reaped,
   * never left to its ceiling.
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
   * A released sandbox paused once its runner has no live job. Whether it is paused now;
   * `releasing` stays while the pause has not landed: a job still live is one of the released
   * lease ending (the owner gives a lease back only once none of its jobs stands), and a job
   * launched since ends the ask.
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
    if ((await this.#runnerState(row.authority, handle, found)).live > 0) {
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

  /**
   * A running sandbox's runner asked `state`, whatever protocol it speaks, and what follows
   * kept (`#heard`): when a job living on it ends, or null when nothing ends it.
   */
  async #runnerState(authority: string, handle: Box, found: SandboxInfo):
      Promise<StateAnswer & { killAt: number | null }> {
    const state = stateOf(await this.#exec(handle, [COMMAND.state]));
    return { ...state, killAt: this.#heard(authority, state, found).killAt };
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
   * or its kill (`kill_at`) where no renewal can carry it on; a resume (a paused sandbox a
   * lease runs in, or a renewing one), a reap (a release whose pause did not land), a kill
   * (a paused one `kill_at` names); and at once while a slot is owed the line. A paused, idle
   * sandbox needs nothing. None, no alarm.
   */
  async #arm(at = Number.POSITIVE_INFINITY): Promise<void> {
    const ceilings = this.#known();
    let soonest = this.#sql.exec("SELECT 1 FROM owed UNION ALL SELECT 1 FROM retiring UNION " +
                                   "ALL SELECT 1 FROM sandboxes WHERE move_to IS NOT NULL LIMIT 1")
      .toArray().length > 0 ? Math.min(at, Date.now()) : at;
    for (const row of this.#rows()) {
      if (row.renewing === 1 || row.releasing === 1
          || (row.paused === 1 && (row.kill_at !== null || this.#leased(row.authority)))) {
        // Asked at once the first time, then at its back-off: never at alarm speed.
        soonest = Math.min(soonest, row.retry_at ?? Date.now());
      } else if (row.paused === 1) {
        continue;
      } else if (ceilings !== null) {
        soonest = Math.min(soonest, row.kill_at ?? row.end_at - ceilings.renewal_window_s * 1000,
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
   * Each sandbox looked at, each on its own. First, outside the chain, each running one in
   * its renewal window waits for a quiet moment (at most to its edge, and inside this
   * alarm's wall clock); then, on the chain, each is renewed, resumed, reaped, killed or left.
   * Armed again on every path.
   */
  override async alarm(): Promise<void> {
    const began = Date.now();
    let next = Number.POSITIVE_INFINITY;
    let failed: number;
    try {
      // What is owed is given back whatever the look found: no slot waits on another sandbox.
      ({ next, failed } = await this.#look(began).finally(() => this.#giveBack()));
    } catch (error) {
      console.error("a sandbox look failed; it is tried again, backing off", error);
      failed = 1;
    }
    if (failed === 0) {
      this.#sql.exec("UPDATE known SET failures = 0");
      await this.#arm(next);
      return;
    }
    // E2B unreachable, or answering out of shape, for the look or for one sandbox's: asked
    // again, backing off from the measured request time, and sooner only for what the
    // sandboxes looked at need and for an edge still ahead (one already past is E2B's own
    // timeout's to pause; the next look resumes it). Never at alarm speed.
    this.#sql.exec("UPDATE known SET failures = failures + 1");
    const failures = this.#sql.exec<{ failures: number }>("SELECT failures FROM known")
      .toArray()[0]?.failures ?? 1;
    const now = Date.now();
    const ahead = this.#rows().map((row) => row.end_at - RENEW_EDGE_MS)
      .filter((edge) => edge > now);
    await this.ctx.storage.setAlarm(Math.max(now, Math.min(next, now + backoff(failures),
                                                           ...ahead)));
  }

  /**
   * The look: when it next needs to look, and at how many sandboxes it failed. Each is looked
   * at on its own: one whose look fails (E2B or its runner faulting, its runner refused) is
   * logged and looked at again at the next look, backing off, and the others are looked at
   * still, so no sandbox is left running for another's fault.
   */
  async #look(began: number): Promise<{ next: number; failed: number }> {
    const ceilings = await this.#control() ?? this.#known();
    if (ceilings === null) {
      throw new Error("no sandbox numbers were ever read, yet a sandbox is kept");
    }
    const windowMs = ceilings.renewal_window_s * 1000;
    const rows = this.#rows();
    const e2b = this.#e2b();
    const lookEnd = began + ALARM_WALL_MS - rows.length * RENEWAL_MS;
    const quiet = new Map<string, boolean>();
    const failed = new Set<string>();
    const settle = async (row: Row, look: () => Promise<void>): Promise<void> => {
      try {
        await look();
      } catch (error) {
        console.error(`the look at sandbox ${row.sandbox_id} failed; it is looked at again, ${
          ""}backing off`, error);
        failed.add(row.authority);
      }
    };
    await Promise.all(rows.map((row) => settle(row, async () => {
      if (row.renewing === 1 || row.releasing === 1 || row.paused === 1) {
        return;
      }
      const { found, handle } = await this.#find(row);
      if (handle === null || Date.now() < Date.parse(found!.endAt) - windowMs) {
        return;
      }
      this.#ended(found!);
      // A start on the chain adopted another runner meanwhile, and its `prepare` dropped
      // this one's bytes: no answer read here, and nothing failed.
      const adopted = (asked: Exec) => asked.exitCode === EXIT.absent
        && this.#row(row.authority)?.runner !== handle.runner;
      if (handle.runner !== this.#digest) {
        // Nothing but `state` goes to a runner until it says it speaks a protocol this Worker
        // does; one that does not is renewed never, so waits for no quiet moment.
        const asked = await this.#exec(handle, [COMMAND.state]);
        if (adopted(asked) || !SPEAKS.includes(stateOf(asked).protocol)) {
          return;
        }
      }
      const until = Math.min(Date.parse(found!.endAt) - RENEW_EDGE_MS, lookEnd);
      const asked = await this.#exec(handle, [COMMAND.quietWait, String(Math.floor(until))],
                                     Math.max(0, until - Date.now()) + COMMAND_MS);
      if (adopted(asked)) {
        return;
      }
      const { out } = answered(COMMAND.quietWait, asked);
      quiet.set(row.authority, answerOf<QuietAnswer>(out, ANSWERS[COMMAND.quietWait]).quiet);
    })));
    return this.#serial(async () => {
      let next = Number.POSITIVE_INFINITY;
      let left = 0;
      try {
        await this.#settle();
      } catch (error) {
        console.error("what a move left is not killed yet; it is asked again, backing off", error);
        left = 1;
      }
      for (const row of this.#rows().filter((each) => !failed.has(each.authority))) {
        await settle(row, async () => {
          next = Math.min(next, await this.#lookAt(row, quiet.get(row.authority) ?? false,
                                                   ceilings));
        });
      }
      return { next, failed: failed.size + left };
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
      if (row.kill_at !== null) {
        return this.#kill(row);  // E2B's own timeout paused it at its window's end
      }
      if (row.heard === 0 && (row.renewing === 1 || this.#leased(row.authority))) {
        // Its runner has not answered since it last came running (its `state` failing at
        // every look): nothing says a run lives in it, so no look brings it back for one.
        console.error(`sandbox_runner_unheard: sandbox ${row.sandbox_id} is left paused: a ${
          ""}lease ran in it, but its runner has not answered since it last ran`);
        this.#sql.exec("DELETE FROM launched WHERE authority = ?", row.authority);
        this.#sql.exec("UPDATE sandboxes SET renewing = 0, releasing = 0 WHERE authority = ?",
                       row.authority);
        return Number.POSITIVE_INFINITY;
      }
      if (row.renewing === 0 && !this.#leased(row.authority)) {
        this.#sql.exec("UPDATE sandboxes SET releasing = 0 WHERE authority = ?", row.authority);
        return Number.POSITIVE_INFINITY;
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
    const state = await this.#runnerState(row.authority, handle, found);
    if (state.killAt !== null) {
      // A job lives on a runner this Worker does not speak: it runs, looked at as one before
      // its window is, to the end of the window it was found in, and no further.
      return Date.now() < state.killAt
        ? Math.min(state.killAt, await this.#idle(row, found, state.live)) : this.#kill(row);
    }
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
   * A sandbox whose window `kill_at` ended: a job lived in it on a runner this Worker does not
   * speak, so no renewal could carry it on, and its window is over (past, or E2B's own timeout
   * paused it at its end). Killed by its E2B id: kept paused, it would hold that job, which a
   * resume restores and every start refuses (`sandbox_runner_protocol`), at each start's cost.
   * Its runs went with their window: the owner settles each `unknown` once its heartbeat lapsed.
   */
  async #kill(row: Row): Promise<number> {
    console.error(`sandbox_runner_protocol: sandbox ${row.sandbox_id} is killed: a job lived in ${
      ""}it on a runner this Worker does not speak, and its window ended`);
    await this.#e2b().kill(row.sandbox_id);
    this.#forget(row.authority);
    await this.#freed();
    return Number.POSITIVE_INFINITY;
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
   * One renewal: each live run, one launched since the quiet look included, told at once to
   * post `renewing` and hold its posts, its answer waited for to `RENEWING_MS` on, or to the
   * longest pause before `endAt` when that is sooner, so a look late at the edge never waits
   * its pause past `endAt` (`renew-wait`); the row is marked renewing, so a look that fails
   * after the pause resumes it; its slot is held in the line across the pause and the resume
   * (`reserve`), so the
   * first owner waiting cannot create into the moment it frees; the sandbox is paused with
   * its memory and resumed at once.
   */
  async #renew(row: Row, handle: Box, endAt: number, ceilings: SandboxCeilings):
      Promise<void> {
    const e2b = this.#e2b();
    await this.#run(handle, [COMMAND.renewWait,
                             String(Math.min(Date.now() + RENEWING_MS, endAt - PAUSE_MAX_MS))]);
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

  /** The owner waits for a sandbox no more: out of the platform's line, whose next is rung. */
  async unwait(): Promise<void> {
    await this.#slots().leave(this.#owner);
  }

  /**
   * The meter's log after `after` (the owner's object reads it): at most
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
  async erase(): Promise<{ killed: number }> {
    return this.#serial(async () => {
      const e2b = this.#e2b();
      // Each row's, each a move made or left, and any E2B lists under the owner's tag.
      const ids = new Set(this.#sql.exec<{ id: string }>(
        "SELECT sandbox_id AS id FROM sandboxes UNION SELECT move_to FROM sandboxes WHERE " +
          "move_to IS NOT NULL UNION SELECT sandbox_id FROM retiring").toArray()
        .map((each) => each.id));
      for (const id of await e2b.list("ta_owner", ownerTag(this.#owner))) {
        ids.add(id);
      }
      for (const id of ids) {
        await e2b.kill(id);
        await this.#slots().unreserve(id);
      }
      await this.#giveBack();
      await this.#slots().leave(this.#owner);
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
