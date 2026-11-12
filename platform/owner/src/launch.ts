/**
 * Runs launched, delivered and given their sandbox (ADR-034 decision 4; `decisions.md` A2): the
 * object's half of a run, around `runs.ts`'s rows and decisions. One `Launcher` an object, which
 * serves Runs its one narrow interface (`RunHost`) and holds:
 *
 * - **a launch** (`launch`): the agent's `agent.json` read whole (`readAgent`), its run admitted
 *   in one unit against the file still at the etag it was read at, then started through the
 *   sandbox Worker (`SANDBOX`), and what came of it recorded (`running`, `failed` with the
 *   sandbox's code, `unknown` when its answer never came, or `waiting` for a sandbox to free);
 *   a door's bounded wait for its run to settle (`settledWithin`), woken on this instance;
 * - **the `runs` lane's pass** (`pass`): runs whose runner was lost settled, waiting runs
 *   started on their turn, mail no run took delivered as its agent's run (`#deliver`), and due
 *   schedules claimed once and run;
 * - **a mail's reply** (`answer`): a run's result posted as its agent's reply to that mail, in
 *   the settle's unit, its body placed in R2 first when long (`beginReply`, `withReply`);
 * - **the sandbox's leases** (`openLease`, `sandboxLeases`): an authority's lease opened with its
 *   tokens and network, that network marked on it at the generation it was made under, given
 *   back, and paused through the sandbox Worker once no run holds it (`leaseFreed`); one
 *   run's whole environment (`#runConfig`).
 *
 * It reaches the owner's other modules only through the object's state it is handed
 * (`OwnerState`), each read or write in the unit it is called in.
 */
import { HEX64, randomHex } from "../../contract/src/ids";
import { sha256Hex } from "../../contract/src/digest";
import { utf8Bytes, utf8Decoder } from "../../contract/src/text";
import { LEASE_LIVED, mintToken, sealRoute } from "../../contract/src/routing-mac";
import { runUrls, sandboxEnv } from "../../contract/src/egress";
import { PLACE_BYTES } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";
import { bundledSources } from "../../sources/src/catalog";
import { runEnv } from "../../sources/src/run-env";
import { CREDENTIAL_KINDS, SourceRecordError } from "../../sources/src/schema";

import { AGENT_UNDEFINED, type AgentFile, agentFile, agentFilePath } from "./agent-file";
import type { Stamped, StandingGrant } from "./authority";
import type { Placed, Placing } from "./bodies";
import { OWNER_AUTHORITY, type Lease, type SandboxLease } from "./files";
import { type Delivery, type MailAuthority, mailInput } from "./ledger";
import { nativeId } from "../../contract/src/binding";
import { clock } from "./clock";
import { type EgressPolicy, networkDocument } from "./network";
import type { OwnerState } from "./owner-do";
import {
  type Ask, type LiveLease, type LiveRun, type MailAnswer, mailConversation, NO_SCHEDULE,
  type OpenedTokens, type RunHost, type RunReceipt, Runs, scheduleAuthority, SIGNIN_REQUIRED,
  type SpendCaps, type UsableSource,
} from "./runs";
import { SANDBOX_ABSENT, SANDBOX_WAITING, started } from "./sandbox";
import { inTime, LANE_HUNG_S, LANE_PASS_S } from "./schedule";
import { switchedOn } from "./signins";
import { tierNumbers } from "./tier";
import type { TierName } from "../../contract/src/control";
import type { CredentialKind, NetworkPlan } from "../../contract/src/sandbox";

/**
 * How long a live lease's re-apply waits before the lane sends it again, after it was sent
 * with no answer yet, failed, or was kept for its paused sandbox: long enough that a lane
 * pass does not loop on a sandbox Worker that keeps failing, short enough that a model call
 * refused for a stale network is answered within a minute of the Worker taking it again.
 */
export const NETWORK_RETRY_S = 30;
/** A re-apply's call to the sandbox Worker, bounded inside a lane pass (`schedule.ts`). */
const NETWORK_CALL_MS = (LANE_HUNG_S - LANE_PASS_S) * 1000;

/** The agent's file changed between its read and the admission: the caller tries again. */
export const AGENT_CHANGED = "agent_changed: agent.json changed as the run was admitted";
/**
 * The bytes a mail's run's input takes in the waiting inputs' queue: none, since its row
 * keeps only the mail's reference (ADR-035); the queue's rows bound it.
 */
export const MAIL_INPUT_BYTES = 0;

/**
 * One engine run's environment's inputs: the run itself (its base URLs carry it), the CLI
 * record (`engine`), the credential it runs on, and an API-key run's connection. Its
 * environment carries that credential alone (`sandboxEnv`): a run on the vendor's own
 * sign-in gets no key at all.
 */
export interface SandboxRun {
  readonly run: string;
  /** The run's own secret (64 hex), which its URLs carry: only its CLI holds them. */
  readonly secret: string;
  readonly engine: string;
  readonly credential: CredentialKind;
  /** An API-key run's connection; absent on the vendor's own sign-in. */
  readonly connection?: string;
}

/** What the sandbox slice sets up a sandbox from (`network.ts`). */
export interface SandboxConfig {
  readonly generation: number;
  readonly egress: EgressPolicy;
  /**
   * The run's whole environment, its allow-list (`runEnv`, `sandboxEnv`): `HOME` and the
   * record's own variables, each a template the runner fills (`{home}`, `{agent}`), and on
   * an API key its one connection's key variables. The runner starts from an empty
   * environment and sets exactly these, keeping only `inherit` of the sandbox's own.
   */
  readonly env: Readonly<Record<string, string>>;
  /** The process basics the run keeps from the sandbox template's environment. */
  readonly inherit: readonly string[];
}

/**
 * The sandbox leases, one an authority (the owner's own work's, each outside client's),
 * owner-internal (G4): no door reaches them. A run's admission opens its authority's, or
 * joins the live one (`runs.ts`): there is no other way to open one, so no path skips
 * admission. What is left here is what slice S's sandbox Worker reports and the tests take
 * through `Internals.sandbox`. Each call is one unit of the write gate, and refused unless
 * the owner is admitted for its class, as a door's binding check refuses.
 */
export interface SandboxLeases {
  /** Renew it (the runner's heartbeat; each event batch renews it too). */
  renew(request: { lease_id: string; epoch: number }): Promise<Lease>;
  /**
   * Give it back: both tokens refused from this unit on, its streams ended, and every run
   * still live under it settled `unknown` (its runner is gone with it).
   */
  close(request: { lease_id: string; epoch: number }): Promise<{ released: boolean }>;
  /** The owner's egress policy, its generation, and `run`'s environment. */
  config(run: SandboxRun): Promise<SandboxConfig>;
}

/** What the launcher reaches of the object: its stores, its gate, and its own reads. */
export type LaunchState = Pick<OwnerState, "env" | "storage" | "owner" | "files" | "connections"
  | "grants" | "policy" | "usage" | "egress" | "relay" | "ledger" | "notices" | "signins"
  | "bodies" | "starter" | "door" | "sync" | "rereading" | "control" | "admitted" | "unadmitted"
  | "tier">;

export class Launcher implements RunHost {
  readonly #o: LaunchState;
  /** The owner's runs: admission, grants, conversations, schedules (`runs.ts`). */
  readonly runs: Runs;
  /** Who waits on a run's settle, on this instance only: a door's bounded wait. */
  readonly #waiters = new Map<string, Set<() => void>>();
  /**
   * A long reply's body placed before an events unit (`runEvents`), set only for that
   * synchronous unit, for its settle to post by reference (`answer`).
   */
  #replyPlaced: Placed | null = null;
  /** The leases whose re-apply this instance has out with the sandbox Worker (`reapply`). */
  readonly #reapplying = new Set<string>();

  constructor(o: LaunchState) {
    this.#o = o;
    this.runs = new Runs(o.storage, this);
  }

  // -- RUNHOST: what Runs reaches past its rows -----------------------------------------

  unadmitted(): Refusal | null {
    return this.#o.unadmitted();
  }

  tier(): TierName {
    return this.#o.tier();
  }

  sandboxBound(): boolean {
    return this.#o.env.SANDBOX !== undefined;
  }

  reachable(): readonly UsableSource[] {
    const reachable = new Set(this.#o.connections.egressible().map((entry) => entry.connection));
    return this.#o.connections.usable().filter((entry) => reachable.has(entry.connection));
  }

  standing(stamp: Stamped): StandingGrant {
    return this.#o.grants.standing(stamp.client, stamp.generation, stamp.authenticated_at);
  }

  caps(): SpendCaps {
    return this.#o.policy.models();
  }

  spent(period: string, source: string | null): number {
    return this.#o.usage.spent(period, source);
  }

  lease(now: number, authority: string): LiveLease | null {
    return this.#o.files.sandboxLease(now, authority);
  }

  leases(now: number): readonly LiveLease[] {
    return this.#o.files.liveLeases(now);
  }

  releaseLease(leaseId: string, epoch: number): void {
    // A lease a sign-in holds stays: its sandbox is in use with no run (slice SIGNIN).
    if (!this.#o.signins.holds(leaseId) && this.#o.files.leaseRelease(leaseId, epoch).released) {
      this.#o.egress.end("lease", (live) => live.lease === leaseId);
      // Once the unit is over, its sandbox paused: no run lives in it (slice S).
      queueMicrotask(() => void this.#sandboxRelease(leaseId));
    }
  }

  renewLease(leaseId: string, epoch: number): void {
    this.#o.files.leaseRenew(leaseId, epoch);
  }

  leasesLapsed(now: number): void {
    // Each lapsed lease's sandbox paused, as a given-back one's is (slice S).
    for (const leaseId of this.#o.files.leasesLapsed(now)) {
      queueMicrotask(() => void this.#sandboxRelease(leaseId));
    }
  }

  ended(run: string): void {
    this.#o.egress.end("run", (live) => live.run === run);
    this.#o.relay.settled(run);
    this.#wake(run);
  }

  /**
   * In the settle's unit, before the run settles: a mail's run's result posted as its
   * agent's reply to that mail, in its thread, under the run's authority (its run id the
   * post's own id, so it is posted once), when the mail is one a result answers: a person's
   * or an outside client's, never another agent's (`Ledger.answer`), so a reply never wakes
   * an agent and starts no run (no `converse` to check). A reply the ledger does not post,
   * for any reason (the other side gone, its mailbox full, a broken invariant), leaves the
   * owner a notice, and the settle stands.
   */
  answer(answer: MailAnswer): void {
    const authority: MailAuthority | null = answer.stamp === null || answer.origin === null
      ? null : { client: answer.stamp.client, family: answer.origin.family,
                 generation: answer.stamp.generation,
                 authenticated_at: answer.stamp.authenticated_at,
                 universe: answer.stamp.universe };
    // Past the placement threshold its body was placed before this unit (`runEvents`).
    this.#reply(answer, authority,
                utf8Bytes(answer.result) > PLACE_BYTES ? this.#replyPlaced : null);
  }

  mailSettled(mail: string, agent: string): void {
    this.#o.ledger.settled(mail, agent);
  }

  notice(type: string, detail: string): void {
    this.#o.notices.add(type, detail, clock());
  }

  /** From a unit: each run's cancel sent to the sandbox once the unit is over. */
  stop(grants: readonly LiveRun[]): void {
    for (const grant of grants) {
      queueMicrotask(() => void this.cancel(grant));
    }
  }

  // -- A LAUNCH --------------------------------------------------------------------------

  /**
   * A new owner's starter seed, awaited before a door reads an agent: its first `converse`
   * finds `main` however soon it comes (review L3), the lane's pass joined if it runs.
   */
  async seeded(): Promise<void> {
    if (this.#o.starter.version() === 0) {
      await this.#o.starter.seeded(clock());
      await this.#o.sync();
    }
  }

  /** An agent's `agent.json`, read whole, and the etag it was read at. */
  async readAgent(agent: string): Promise<{ file: AgentFile; etag: string }> {
    const path = agentFilePath(agent);
    const content = await this.#o.door(() => {
      const entry = this.#o.files.stat(path);
      if (entry === null || entry.kind !== "file") {
        throw new Refusal("agent_undefined", AGENT_UNDEFINED);
      }
      return this.#o.files.open(path);
    });
    let bytes = content.inline;
    if (bytes === undefined) {
      const parts = await this.#o.files.fetch((content.chunks ?? []).map(({ sha256 }) =>
        ({ sha256 })));
      bytes = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
      let at = 0;
      for (const part of parts) {
        bytes.set(part, at);
        at += part.byteLength;
      }
    }
    let text: string;
    try {
      text = utf8Decoder("strip").decode(bytes);
    } catch {
      throw new Refusal("agent_file_invalid", `${path} is not UTF-8 text`);
    }
    return { file: agentFile(text), etag: content.entry.etag };
  }

  /**
   * Admit a run in one unit (`ask`, which binds the call again, and the agent's file still
   * at the etag it was read at), then start it through the sandbox and record what came of
   * it: `running`, `failed` with the sandbox's code, or `unknown` when its answer never came
   * (the run is then cancelled too, in case it did start). Its id: a run that waits for
   * its conversation's live run is written `waiting` and starts from the lane, its turn
   * come (`waiting`, the run the lane starts).
   */
  async launch(read: { file: AgentFile; etag: string }, ask: () => Omit<Ask, "file">,
               waiting: string | null = null, taken: () => void = () => {}): Promise<string> {
    const { admitted, agent, unattended } = await this.#o.door(() => {
      const made = ask();
      if (this.#o.files.stat(agentFilePath(made.agent))?.etag !== read.etag) {
        throw new Refusal("agent_changed", AGENT_CHANGED);
      }
      const done = { agent: made.agent, unattended: made.unattended,
                     admitted: this.runs.admit({ ...made, file: read.file }, clock(), (chosen) =>
                       this.#runConfig({ run: chosen.run, secret: chosen.secret,
                                         engine: chosen.engine, credential: chosen.credential,
                                         ...(chosen.connection === null ? {}
                                           : { connection: chosen.connection }) }), waiting) };
      taken();  // the input it was asked on is its own now (a mail's, taken)
      return done;
    });
    if (admitted.existing) {
      return admitted.run;
    }
    const sandbox = this.#o.env.SANDBOX;
    let answer: { started: true } | { started: false; code: string } | null = null;
    try {
      if (sandbox === undefined) {
        throw new Error(SANDBOX_ABSENT);  // admission refused this already
      }
      answer = started(await sandbox.start(this.#o.owner(), {
        run: admitted.run, agent, conversation: admitted.conversation,
        engine: admitted.engine, credential: admitted.credential, model: admitted.model,
        session_id: admitted.session_id, prompt: admitted.prompt, unattended,
        authority: admitted.authority, lease: admitted.lease, tokens: admitted.tokens,
        network: admitted.network, config: admitted.config,
        route: sealRoute(this.#o.env.ROUTING_MAC_KEY, { owner: this.#o.owner(),
                                                        authority: admitted.authority }),
        urls: runUrls(admitted.run, admitted.secret) }));
    } catch {
      console.error("a run's start had no answer; it is settled unknown");
      answer = null;
    }
    await this.#o.door(() => {
      if (answer === null) {
        this.runs.settle(admitted.run, "unknown", "start_lost", null, clock());
      } else if (answer.started) {
        this.runs.started(admitted.run, clock());
      } else if (answer.code === SANDBOX_WAITING) {
        // E2B runs as many sandboxes as its plan allows: the run waits, never refused, and
        // the lane starts it again when the platform's line rings the owner (`sandboxRung`).
        // The founder's alert reads E2B's own count (owed: `sandbox-probe.md`); this line is
        // the Worker log's record of it.
        if (this.runs.waitForSandbox(admitted.run, admitted.prompt)) {
          console.warn(`${SANDBOX_WAITING}: a run waits for a sandbox to free`);
        }
      } else {
        this.runs.settle(admitted.run, "failed", answer.code, null, clock());
        if (answer.code === SIGNIN_REQUIRED) {
          // No login inside that sandbox: the owner signs in there, never copies one.
          this.#o.notices.add(SIGNIN_REQUIRED, `${agent}: sign in to ${admitted.engine} inside ${
            admitted.authority === OWNER_AUTHORITY ? "your own" : `${admitted.authority}'s`
          } sandbox, through the vendor's own flow`, clock());
        }
      }
    });
    if (answer === null) {
      await this.cancel({ run: admitted.run, lease_id: admitted.lease.lease_id });
    }
    await this.#o.sync();
    return admitted.run;
  }

  /** The run as it stands once settled, or once `waitMs` passed; the caller's own. */
  async settledWithin(run: string, waitMs: number, client: string | null): Promise<RunReceipt> {
    const live = await this.#o.door(() => this.runs.receipt(run, client).settled_at === null);
    if (live && waitMs > 0) {
      await new Promise<void>((resolve) => {
        const waiting = this.#waiters.get(run) ?? new Set<() => void>();
        const done = () => {
          clearTimeout(timer);
          waiting.delete(done);
          if (waiting.size === 0 && this.#waiters.get(run) === waiting) {
            this.#waiters.delete(run);
          }
          resolve();
        };
        const timer = setTimeout(done, waitMs);
        waiting.add(done);
        this.#waiters.set(run, waiting);
      });
    }
    return this.#o.door(() => this.runs.receipt(run, client));
  }

  /** A run settled: whoever waits on it on this instance goes on. */
  #wake(run: string): void {
    for (const done of [...(this.#waiters.get(run) ?? [])]) {
      queueMicrotask(done);
    }
  }

  /** Stop a run's CLI through the sandbox; a failure is logged, the run settles regardless. */
  async cancel(run: Pick<LiveRun, "run" | "lease_id">): Promise<void> {
    const sandbox = this.#o.env.SANDBOX;
    if (sandbox === undefined) {
      console.error(`${SANDBOX_ABSENT}: run ${run.run}'s CLI could not be stopped`);
      return;
    }
    try {
      await sandbox.cancel(this.#o.owner(), { run: run.run, lease_id: run.lease_id });
    } catch {
      console.error("a run's cancel did not reach the sandbox");
    }
  }

  /**
   * A lease that ended, its sandbox paused through the sandbox Worker (zero idle tail). A
   * release that does not reach it is logged: the sandbox Worker reaps a sandbox idle for a
   * lease's TTL on its own (by then a lease no run renews has lapsed), and keeps asking E2B
   * for a pause that did not land.
   */
  async #sandboxRelease(leaseId: string): Promise<void> {
    const sandbox = this.#o.env.SANDBOX;
    if (sandbox === undefined) {
      return;  // no sandbox bound, so no lease was opened under one
    }
    try {
      await sandbox.release(this.#o.owner(), { lease_id: leaseId });
    } catch {
      console.error("a lease's release did not reach the sandbox; its idle reap pauses it");
    }
  }

  // -- A MAIL'S REPLY ----------------------------------------------------------------------

  /**
   * In a unit: a long reply's upload begun as its writer's (the run's client's, or the
   * owner's), or null when its body stays in its row or its writer's files refuse it; that
   * settle then leaves the owner a notice (`answer`).
   */
  beginReply(answer: MailAnswer): Placing | null {
    try {
      return this.#o.bodies.begin(answer.result,
                                  answer.stamp === null ? "owner" : `client:${answer.stamp.client}`);
    } catch (error) {
      if (!(error instanceof Refusal) || error.transient) {
        throw error;  // a transient refusal fails the batch, posted again: the reply is kept
      }
      return null;
    }
  }

  /** In a unit: `act` with a long reply's placed body set for its settle to post (`answer`). */
  withReply<T>(placed: Placed | null, act: () => T): T {
    this.#replyPlaced = placed;
    try {
      return act();
    } finally {
      this.#replyPlaced = null;
    }
  }

  /**
   * A mail's reply posted in this unit, or a scheduled run's result in the owner's room with
   * its agent (`dm:<agent's id>`, `dm:main`'s for main; the run's id its post's own, so it is
   * posted once), its body
   * placed or not; a refusal a notice.
   */
  #reply(answer: MailAnswer, authority: MailAuthority | null, placed: Placed | null): void {
    try {
      // The reply is the owner's agents' content: held to the tier's storage too.
      this.#o.files.held("owner", () => {
        if (answer.mail === null) {
          this.#o.ledger.post({ native: answer.agent }, `dm:${nativeId(answer.agent)}`,
                              answer.run, answer.result, null, clock(), authority, placed,
                              authority === null ? "owner" : `client:${authority.client}`);
        } else {
          this.#o.ledger.answer({ native: answer.agent }, answer.mail, answer.run,
                                answer.result, clock(), authority, placed);
        }
      });
    } catch (error) {
      if (error instanceof Refusal && error.transient) {
        // For the moment only (the hot mail ceiling, `storage_busy`): the settle's unit
        // fails with it, so the run stays live and its runner posts the batch again after
        // its Retry-After; the reply is posted then, never dropped.
        throw error;
      }
      if (!(error instanceof Refusal)) {
        console.error("a mail's reply failed in the ledger; the run's settle stands");
      }
      this.#o.notices.add("mail_reply_refused", `${answer.agent}: ${
        error instanceof Error ? error.message : "the ledger failed"}`, clock());
    }
  }

  // -- THE RUNS LANE -----------------------------------------------------------------------

  /**
   * The `runs` lane's next time: mail no run has taken while admitted, else the soonest of
   * Runs' own and a live lease's re-apply coming due (`reapply`).
   */
  next(now: number): number | null {
    const admitted = this.#o.unadmitted() === null;
    if (admitted && this.#o.ledger.mailDue(this.runs.queueRoom(MAIL_INPUT_BYTES))) {
      return now;
    }
    const times = [this.runs.next(now, admitted),
                   ...(admitted && this.sandboxBound()
                     ? this.#behind(now).map((each) => each.due) : [])]
      .filter((time): time is number => time !== null);
    return times.length === 0 ? null : Math.min(...times);
  }

  /**
   * The switched-on sign-ins, read by the lane itself from the control record the object
   * binds (`CONTROL`), and kept (review N1): so a source switched off stops powering the
   * lane's runs (a schedule's, a mail's, a waiting one's) at its next pass, though the owner
   * makes no door call (`ControlGate.signins`, fail-closed).
   */
  async #laneSignins(): Promise<void> {
    const on = await this.#o.control().signins();
    await this.#o.door(() => switchedOn(this.#o.storage, on, clock()));
  }

  /**
   * The `runs` lane's pass: runs whose runner was lost settled `unknown`; then each waiting
   * run whose turn came started, oldest first, through the same admission as a door's (one
   * its turn refuses settles `failed`, the refusal in the owner's notices); then each mail
   * to one of the owner's agents that no run took, oldest first, starting that agent's run
   * (`#deliver`); then each due schedule claimed, once, and run the same way; a refusal kept
   * on its row and in the owner's notices, never thrown.
   */
  async run(_now: number, until: number): Promise<void> {
    await this.#laneSignins();
    await this.#o.door(() => this.runs.lost(clock()));
    await this.reapply(false);
    while (clock() < until) {
      const waiting = await this.#o.door(() => {
        const next = this.#o.unadmitted() !== null ? null : this.runs.nextWaiting();
        return next === null ? null : { run: next.id, ask: this.runs.waitingAsk(next.id)! };
      });
      if (waiting === null) {
        break;
      }
      try {
        const read = await this.readAgent(waiting.ask.agent);
        const prompt = waiting.ask.mail === null ? waiting.ask.prompt
          : await this.#mailPrompt(waiting.ask.mail.message, waiting.ask.agent);
        await this.launch(read, () => ({
          ...waiting.ask, prompt, tiers: tierNumbers(this.#o.storage, undefined, clock()) }),
                          waiting.run);
      } catch (error) {
        if (!(error instanceof Refusal) || error.transient) {
          throw error;  // a transient refusal fails the pass, and the run waits on
        }
        if (error.code === "conversation_busy" || error.code === "tier_runs") {
          break;  // not its turn after all: it keeps waiting, and the lane comes back
        }
        const reason = error.message;
        await this.#o.door(() => {
          if (this.runs.refuseWaiting(waiting.run, error, clock())) {
            this.#o.notices.add("run_refused", `${waiting.ask.agent}: ${reason}`, clock());
          }
        });
      }
    }
    while (clock() < until) {
      // A placed body (ADR-035) is read from R2 between the lane's units, as a door's.
      const next = (bodies: ReadonlyMap<string, string>) => {
        if (this.#o.unadmitted() !== null) {
          return null;
        }
        if (this.runs.queueRoom(MAIL_INPUT_BYTES)) {
          this.#o.ledger.release();  // the inputs' queue has room for any held mail again
        }
        return this.#o.ledger.nextMail(bodies);
      };
      const mail = await this.#o.rereading(() => {
        const found = next(new Map());
        return found?.content_id == null ? [] : [found.content_id];
      }, next, (found) => found?.content_id != null, (located) => this.#o.bodies.fetch(located));
      if (mail === null) {
        break;
      }
      await this.#deliver(mail);
    }
    // A due schedule is claimed in its run's admission unit, once the run is admitted, so
    // it fires once: a run admitted claims it; a terminal refusal claims it with the refusal
    // kept; a transient refusal claims nothing, and it stays due for the lane's next pass.
    // (A unit keeps what it wrote before a throw, `gate.ts`: so the claim is the admission's
    // last write, never its first.)
    while (clock() < until) {
      const row = await this.#o.door(() => this.#o.unadmitted() !== null
        ? null : this.runs.due(clock()));
      if (row === null) {
        return;
      }
      let gone = false;
      try {
        const read = await this.readAgent(row.agent);
        const run = await this.launch(read, () => {
          if (!this.runs.stands(row)) {
            gone = true;
            throw new Refusal("no_schedule", NO_SCHEDULE);
          }
          return {
            initiator: "schedule", ...scheduleAuthority(row), agent: row.agent,
            conversation: null, request: null,
            tiers: tierNumbers(this.#o.storage, undefined, clock()), unattended: true,
            prompt: row.input, mail: null };
        }, null, () => this.runs.claim(row, clock()));
        await this.#o.door(() => this.runs.ran(row.id, run, null));
      } catch (error) {
        if (!(error instanceof Refusal) || error.transient) {
          throw error;  // a transient refusal fails the pass: unclaimed, the schedule stays due
        }
        if (gone) {
          continue;  // cancelled, or claimed, since it was read: nothing of it fires now
        }
        const reason = error.message;
        await this.#o.door(() => {
          if (this.runs.claim(row, clock())) {
            this.runs.ran(row.id, null, reason);
            this.#o.notices.add("run_refused", `${row.agent}: ${reason}`, clock());
          }
        });
      }
    }
  }

  /**
   * A waiting mail run's input, read again from its mail as it starts: the row kept only the
   * mail's reference, and a placed body (ADR-035) is read from R2 between the lane's units.
   * A mail no longer held, or whose body is not its own, refuses the run.
   */
  async #mailPrompt(messageId: string, agent: string): Promise<string> {
    const mail = await this.#o.rereading(() => {
      const found = this.#o.ledger.mailFor(messageId, nativeId(agent));
      return found?.content_id == null ? [] : [found.content_id];
    }, (bodies: ReadonlyMap<string, string>) =>
      this.#o.ledger.mailFor(messageId, nativeId(agent), bodies),
    (found) => found?.content_id != null, (located) => this.#o.bodies.fetch(located));
    if (mail === null) {
      throw new Refusal("mail_missing",
                        "mail_missing: the mail this run answers is no longer held");
    }
    if (mail.body === null) {
      throw new Refusal(mail.integrity === "tampered" ? "mail_tampered" : "mail_missing",
                        `mail_${mail.integrity}: the mail's body is ${mail.integrity}`);
    }
    return mailInput(mail, mail.body);
  }

  /**
   * One mail to one of the owner's agents started as that agent's run, attended only when a
   * person sent it: its input the mail's envelope and body (`mailInput`), all the run needs
   * to answer by a deliberate post; its conversation its thread's under the mail's authority
   * (made with
   * the run when the thread has none), its authority the mail's (a client's stamp at its
   * generation, acting as this agent: held to the intersection of the agent's scopes and
   * the client's grant there, and refused once that grant moved), its retry key the mail and
   * agent. The mail is taken in the admission's own unit, waiting runs and runs past the
   * tier included. A run that must wait while the inputs' queue is full leaves the mail
   * held, until the queue has room; any other refusal fails the mail, with a notice.
   */
  async #deliver(mail: Delivery): Promise<void> {
    const authority = mail.authority;
    const origin = authority === null ? null
      : { client: authority.client, family: authority.family };
    const stamp: Stamped | null = authority === null ? null
      : { client: authority.client, generation: authority.generation,
          authenticated_at: authority.authenticated_at, universe: authority.universe,
          agent: mail.agent };
    try {
      if (mail.body === null) {
        throw new Refusal(mail.integrity === "tampered" ? "mail_tampered" : "mail_missing",
                          `mail_${mail.integrity}: the mail's body is ${mail.integrity}`);
      }
      const prompt = mailInput(mail, mail.body);
      const read = await this.readAgent(mail.agent);
      const conversation = mailConversation(mail.agent, mail.thread, origin?.client ?? null);
      await this.launch(read, () => ({
        initiator: "mail", stamp, origin, agent: mail.agent,
        conversation: this.runs.kept(conversation, mail.agent, origin?.client ?? null)
          ? conversation : null,
        request: sha256Hex(`mail\0${mail.message_id}\0${mail.agent_id}`),
        tiers: tierNumbers(this.#o.storage, undefined, clock()), unattended: !mail.attended,
        prompt, mail: { message: mail.message_id, conversation } }), null,
                        () => this.#o.ledger.taken(mail.message_id, mail.agent_id, clock()));
    } catch (error) {
      if (!(error instanceof Refusal) || error.transient) {
        throw error;  // a transient refusal fails the pass, and the mail is delivered again
      }
      if (error.code === "agent_changed") {
        return;  // read again on the next turn of the pass
      }
      const reason = error.message;
      await this.#o.door(() => {
        if (error.code === "run_inputs_full") {
          this.#o.ledger.hold(mail.message_id, mail.agent_id, clock());
          return;
        }
        // Its fixed code, as a protocol shows one; a quiet one's own words.
        this.#o.ledger.undeliverable(mail.message_id, mail.agent_id,
                                     error.named ? error.code : reason, clock());
        this.#o.notices.add("mail_refused", `${mail.agent}: ${reason}`, clock());
      });
    }
  }

  // -- THE SANDBOX'S LEASES ----------------------------------------------------------------

  /** In a unit: a lease no run and no sign-in holds now given back, as a settle gives it. */
  leaseFreed(leaseId: string, epoch: number): void {
    if (this.runs.liveRuns().some((run) => run.lease_id === leaseId)
        || this.#o.signins.holds(leaseId)) {
      return;
    }
    if (this.#o.files.leaseRelease(leaseId, epoch).released) {
      this.#o.egress.end("lease", (live) => live.lease === leaseId);
      queueMicrotask(() => void this.#sandboxRelease(leaseId));
    }
  }

  /**
   * In a unit, as a run's admission finds no live lease of its authority: that sandbox's
   * lease taken, its egress and sync tokens minted (they live while the lease is renewed,
   * by its heartbeat, with no deadline of their own, `LEASE_LIVED`, so no run meets a fixed
   * one) and the runner's own secret, and the owner's network made with the egress token in
   * its transform. A lease that lapsed is replaced: its streams and tokens end with it.
   *
   * The lease's network is marked in this same unit, at the generation this network is made
   * under, never a later one: the unit that dispatches the start (a run's admission, a
   * sign-in's first unit). That start applies it before anything in the sandbox can call
   * out with this lease's egress token, which only that network's transform carries: E2B
   * takes the network with the create, or replaces it on a running or resumed sandbox, before
   * the runner launches anything (`sandbox/src/control.ts`, `#ensure` and `#launch`). So a
   * call bearing the token was made through the network, and a run's first model call is
   * admitted however soon it comes, before its start's answer is recorded. A policy write
   * while the start is out leaves the newer generation stale (`egress_network_stale`), and a
   * start that fails gives the lease back, so a mark on it admits nothing. In `ask` mode the
   * owner gets a refusal receipt for each network made (`network.ts`).
   */
  openLease(now: number, authority: string):
      { lease: Lease; tokens: OpenedTokens; network: NetworkPlan } {
    const egress = mintToken(this.#o.env.ROUTING_MAC_KEY, "egress", this.#o.owner(), LEASE_LIVED);
    const sync = mintToken(this.#o.env.ROUTING_MAC_KEY, "sync", this.#o.owner(), LEASE_LIVED);
    const runner = randomHex(32);
    const lease = this.#o.files.leaseAcquire(authority, "sandbox", {
      egress: egress.hash, sync: sync.hash, runner: sha256Hex(runner) });
    const live = new Set(this.#o.files.liveLeases(now).map((each) => each.lease_id));
    this.#o.egress.end("lease", (stream) => !live.has(stream.lease));
    const { document } = this.#o.policy.read();
    const network = networkDocument(document.egress, this.#o.policy.egressGeneration(),
                                    egress.token);
    this.#o.files.leaseNetwork(lease.lease_id, network.generation);
    if (network.receipt) {
      this.#o.policy.refusal("sandbox_network", { mode: network.mode,
                                                  generation: network.generation },
                             clock(), "egress.ask");
    }
    return { lease, tokens: { sync_token: sync.token, runner_token: runner }, network };
  }

  /**
   * In a unit: the live leases whose network is behind the current egress generation (an
   * egress policy write since their start), each with its re-apply's due time: at once
   * when none was tried, else `NETWORK_RETRY_S` after the last was sent or failed. Not one
   * this instance has out now: its answer comes first.
   */
  #behind(now: number): { lease: SandboxLease; due: number }[] {
    const generation = this.#o.policy.egressGeneration();
    return this.#o.files.liveLeases(now)
      .filter((lease) => lease.network_generation !== generation
              && !this.#reapplying.has(lease.lease_id))
      .map((lease) => ({ lease, due: lease.network_tried_at === null ? now
        : Math.max(now, lease.network_tried_at + NETWORK_RETRY_S) }));
  }

  /**
   * Each live lease whose network is behind the current egress generation, made again
   * under it and re-applied on its sandbox (the sandbox Worker's `network`, which keeps the
   * live egress token and the launched jobs' vendor hosts): the leases due (`#behind`), or
   * on a `kick` (a policy write's, once its unit committed) every one not already out with
   * this generation. Its send is a unit (`networkSent`): the lease's calls are admitted
   * under the new generation from there on, since only that network's transform sets it.
   * Its answer is another (`networkAnswered`): `applied` confirms it; `at_resume` keeps it
   * out for its paused sandbox; `lost`, a throw or no answer within `NETWORK_CALL_MS`
   * leaves it to the lane's next try (a late answer is recorded when it comes). In `ask`
   * mode each re-apply is a refusal receipt, as each lease's first network is.
   */
  async reapply(kick: boolean): Promise<void> {
    const sandbox = this.#o.env.SANDBOX;
    if (sandbox === undefined) {
      return;  // no sandbox bound, so no lease was opened under one
    }
    const sent = await this.#o.door(() => {
      if (this.#o.unadmitted() !== null) {
        return [];
      }
      const now = clock();
      const generation = this.#o.policy.egressGeneration();
      const due = this.#behind(now).filter(({ lease, due: at }) =>
        kick ? lease.network_pending !== generation : at <= now);
      if (due.length === 0) {
        return [];
      }
      const network = networkDocument(this.#o.policy.read().document.egress, generation, null);
      const out: { lease: SandboxLease; network: NetworkPlan }[] = [];
      for (const { lease } of due) {
        if (this.#o.files.networkSent(lease.lease_id, lease.epoch, generation, now)) {
          this.#reapplying.add(lease.lease_id);
          out.push({ lease, network });
          if (network.receipt) {
            this.#o.policy.refusal("sandbox_network", { mode: network.mode, generation },
                                   clock(), "egress.ask");
          }
        }
      }
      return out;
    });
    await Promise.all(sent.map(async ({ lease, network }) => {
      let answer: "applied" | "at_resume" | "failed" | "late" = "failed";
      try {
        const got = await inTime(sandbox.network(this.#o.owner(), {
          authority: lease.authority, lease: { lease_id: lease.lease_id, epoch: lease.epoch },
          network }), NETWORK_CALL_MS);
        answer = got === "late" ? "late" : got.state === "lost" ? "failed" : got.state;
      } catch {
        console.error("a lease's network re-apply did not reach the sandbox; the lane tries again");
      }
      try {
        if (answer !== "late") {  // out still: its send's retry clock stands
          const recorded = answer;
          await this.#o.door(() => this.#o.files.networkAnswered(
            lease.lease_id, lease.epoch, network.generation, recorded, clock()));
        }
      } finally {
        this.#reapplying.delete(lease.lease_id);
      }
    }));
  }

  /**
   * One run's whole environment (slice E's `runEnv`, G4's `sandboxEnv`), from its record,
   * its credential and an API-key run's connection, with the owner's egress policy and its
   * generation: refused (`invalid`) when the record does not run on that credential.
   */
  #runConfig(run: SandboxRun): SandboxConfig {
    if (typeof run !== "object" || run === null || typeof run.engine !== "string"
        || typeof run.run !== "string" || !CREDENTIAL_KINDS.includes(run.credential)
        || (run.credential === "api_key") !== (typeof run.connection === "string")) {
      throw new Refusal("invalid",
                        "a sandbox run names itself, its engine, its credential and, on an " +
                        "API key, its connection");
    }
    let engine;
    try {
      engine = runEnv(bundledSources(), run.engine, run.credential);
    } catch (error) {
      throw error instanceof SourceRecordError ? new Refusal("run_config_invalid", error.message) : error;
    }
    if (typeof run.secret !== "string" || !HEX64.test(run.secret)) {
      throw new Refusal("invalid", "a sandbox run carries its own secret, 64 hex");
    }
    const keyed = sandboxEnv(this.#o.connections.egressible(),
                             () => randomHex(16),
                             { ...engine, run: run.run, secret: run.secret,
                               connection: run.connection ?? null });
    // The record's own variables are never shaped like a key's, so the two never meet.
    const shared = Object.keys(keyed).find((name) => Object.hasOwn(engine.env, name));
    if (shared !== undefined) {
      throw new Error(`a run's record and its key both set ${shared}`);
    }
    const { document } = this.#o.policy.read();
    return { generation: this.#o.policy.egressGeneration(), egress: document.egress,
             env: Object.freeze({ ...engine.env, ...keyed }), inherit: engine.inherit };
  }

  /** The sandbox's lease API, which the sandbox slice reaches in the object (`SandboxLeases`). */
  sandboxLeases(): SandboxLeases {
    const leases: SandboxLeases = {
      renew: (request) => this.#o.door(() => {
        this.#o.admitted("work");
        return this.#o.files.leaseRenew(request.lease_id, request.epoch);
      }),
      close: (request) => this.#o.door(() => {
        this.#o.admitted("reduce");
        const { released } = this.#o.files.leaseRelease(request.lease_id, request.epoch);
        if (released) {
          this.#o.egress.end("lease", (live) => live.lease === request.lease_id);
        }
        // Its runs go with it: their runner is gone, so each is settled `unknown`.
        for (const run of this.runs.liveRuns()) {
          if (run.lease_id === request.lease_id && run.epoch === request.epoch) {
            this.runs.settle(run.run, "unknown", "lease_closed", null, clock());
          }
        }
        return { released };
      }),
      config: (run) => this.#o.door(() => {
        this.#o.admitted("read");
        return this.#runConfig(run);
      }),
    };
    return Object.freeze(leases);
  }
}
