/**
 * Runs launched, delivered and given their sandbox (ADR-034 decision 4;
 * `docs/decisions/untangle-2026-11-wave-1.md` A2): the object's half of a run, around
 * `runs.ts`'s rows and decisions. One `Launcher` an object, which serves Runs its one narrow
 * interface (`RunHost`) and holds:
 *
 * - **a launch** (`launch`): the agent's `agent.json`, `mcp.json` and `hooks.json` read whole
 *   (`readAgent`), with the ranking its `auto` ranks by (the owner's
 *   `/workspace/model-ranking.json`, else the platform's), its run admitted in one unit
 *   against the file still at the etag it was read at, with the agent's own MCP servers the owner's egress lets out (`agentServers`; the
 *   owner told of each left out), each one an owner's connector is sealed for bound to it on
 *   the run's grant and reached through the egress door (`connectorsOf`), and only the
 *   servers' and hooks' commands the owner's rules let its run start (`#ownCommands`), then started
 *   through the sandbox Worker (`SANDBOX`), and what came of it recorded (`running`,
 *   `failed` with the sandbox's code or for an answer out of its shape, `unknown` when its
 *   answer never came, or `waiting` for a sandbox to free);
 *   a door's bounded wait for its run to settle (`settledWithin`), woken on this instance;
 * - **the `runs` lane's pass** (`pass`): runs whose runner was lost settled, waiting runs
 *   started on their turn, mail no run took delivered as its agent's run (`#deliver`), and due
 *   schedules claimed once and run;
 * - **a mail's reply** (`answer`): a run's result posted as its agent's reply to that mail, in
 *   the settle's unit, its body placed in R2 first when long (`beginReply`, `withReply`);
 * - **the sandbox's leases** (`openLease`): an authority's lease opened with its tokens and
 *   network, that network marked on it at the generation it was made under, given back, and
 *   paused through the sandbox Worker once no run holds it (`leaseFreed`); one run's whole
 *   environment (`#runConfig`).
 *
 * It reaches the owner's other modules only through what the object hands it once
 * (`LauncherNeeds`), each read or write in the unit it is called in.
 */
import { randomHex } from "../../contract/src/ids";
import { sha256Hex } from "../../contract/src/digest";
import { utf8Bytes, utf8Decoder } from "../../contract/src/text";
import { LEASE_LIVED, mintToken, sealRoute } from "../../contract/src/routing-mac";
import {
  type Egressible, runUrls, sandboxEnv, withEgressToken,
} from "../../contract/src/egress";
import type { PolicyDocument } from "../../contract/src/policy-document";
import { LEASE_TTL_S, PLACE_BYTES } from "../../contract/src/limits";
import { slotted, type SlotPool } from "../../contract/src/slots";
import { Refusal, refusalOf } from "../../contract/src/refusal";
import { bundledSources } from "../../sources/src/catalog";
import { type Ranking, rankingOf, shippedRanking } from "../../sources/src/ranking";
import { runEnv } from "../../sources/src/run-env";
import { SourceRecordError } from "../../sources/src/schema";

import {
  AGENT_FILES, AGENT_UNDEFINED, type AgentFile, agentFile, type AgentHooks, agentHooks,
  type AgentMcp, agentMcp, agentPath, type McpServer,
} from "../../contract/src/agent-definition";
import { OWNER, scopeOf, type Stamped, type StandingGrant } from "./authority";
import type { Bodies, Placed, Placing } from "./bodies";
import type { DoorUnits, ObjectReads } from "./door";
import type { Egress } from "./egress";
import { slottedNow } from "./gate";
import type { OwnerEnv } from "./env";
import { type Files, type Lease, type SandboxLease } from "./files";
import { ADMITTED_FILE_BYTES, ADMITTED_FILE_UNITS, ownerSlots } from "./holds";
import { type Delivery, type Ledger, type MailAuthority, mailInput } from "./ledger";
import { nativeId, type Origin } from "../../contract/src/binding";
import { RANKING } from "../../contract/src/paths";
import { clock, inTime } from "./clock";
import { agentServers, connectorsOf, networkDocument, throughConnectors } from "./network";
import type { Notices } from "./notices";
import type { Relay } from "./relay";
import {
  type Ask, type LiveLease, type MailAnswer, mailConversation, NO_SCHEDULE,
  type OpenedTokens, type RunHost, type RunReceipt, Runs, scheduleAuthority,
  type SpendCaps,
} from "./runs";
import type { Usable } from "./auto";
import { networkOutcome, startOutcome, vendorHosts } from "./sandbox";
import { type Held, LANE_CALL_MS } from "./schedule";
import type { Starter } from "./starter";
import type { Signins } from "./signins";
import type { TierName, Tiers } from "../../contract/src/control";
import {
  type CredentialKind, type NetworkPlan, type RunConfig, SANDBOX_WAITING, type SandboxStart,
  SIGNIN_REQUIRED,
} from "../../contract/src/sandbox";

/**
 * How long a live lease's re-apply waits before the lane sends it again, after it was sent
 * with no answer yet, failed, or was kept for its paused sandbox: longer than a call's bound
 * (`LANE_CALL_MS`), so it is never sent again while a send is out (a test holds the two
 * apart), and a lane pass does not loop on a sandbox Worker that keeps failing; short enough
 * that a model call refused for a stale network is answered within a minute of the Worker
 * taking it again.
 */
export const NETWORK_RETRY_S = 30;

/**
 * How long a live lease's sandbox may owe a narrowed egress policy (a host the owner dropped
 * still let out by its older network) before its lease is given back and its runs stopped
 * (`Launcher.reapply`, `NETWORK_UNCONFIRMED`): one lease's TTL, the time the platform already
 * lets a sandbox run unconfirmed (a runner unheard that long is settled lost). It holds two
 * whole tries, the write's own and the retry `NETWORK_RETRY_S` on, each answered within
 * `LANE_CALL_MS` (a test holds this), so a re-apply that lands is never cut short
 * (`untangle-2026-11/network-narrow-bound.md`).
 */
export const NARROWED_WITHIN_S = LEASE_TTL_S;

/** A run's settle code, and the owner's notice, when a narrowing was not in force in time. */
export const NETWORK_UNCONFIRMED = "network_unconfirmed";

/**
 * What a run is admitted on (`readAgent`): its `agent.json` at its etag, its servers and its
 * hooks.
 */
export interface AgentRead {
  readonly file: AgentFile;
  readonly etag: string;
  readonly ranking: Ranking;
  readonly mcp: AgentMcp;
  readonly hooks: AgentHooks;
}

/** How a read holds the isolate's pool: a door's waits its turn, a lane's takes it now. */
type PoolHold = <T>(pool: SlotPool, units: number, work: () => Promise<T>) => Promise<T>;

/** `agent`'s definition file `name`, as text: strict UTF-8, or the run is refused. */
function definitionText(agent: string, name: string, bytes: Uint8Array): string {
  try {
    return utf8Decoder("strip").decode(bytes);
  } catch {
    throw new Refusal("agent_file_invalid", `${agentPath(agent, name)} is not UTF-8 text`);
  }
}

/** The agent's file changed between its read and the admission: the caller tries again. */
export const AGENT_CHANGED = "agent_changed: agent.json changed as the run was admitted";
/**
 * The bytes a mail's run's input takes in the waiting inputs' queue: none, since its row
 * keeps only the mail's reference (ADR-035); the queue's rows bound it.
 */
const MAIL_INPUT_BYTES = 0;

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

/** A run as the owner's rules decide its calls (`actions.ts`): its id, agent and conversation. */
interface DecidedRun {
  readonly id: string;
  readonly agent: string;
  readonly conversation: string;
}

/**
 * What the launcher reaches of the object, handed it once by the object that builds it: the
 * files' leases and the bodies a run's mail and reply place; the mail it delivers; the
 * owner's model connections, policy, spend and grants a run is admitted against; the tier
 * and admission; the sandbox's egress, sign-ins and the app sockets a run's end tells; its
 * starter seed; and the object's units and reads; the owner's rules on a run's own commands.
 */
export interface LauncherNeeds extends Pick<DoorUnits, "door" | "sync">,
                                       Pick<ObjectReads, "owner" | "sandbox" | "storage"
                                         | "control"> {
  readonly env: Pick<OwnerEnv, "ROUTING_MAC_KEY">;
  /** The owner's rules on a run's own definition's commands (`actions.ts` `decideCommand`). */
  readonly actions: {
    decideCommand(run: DecidedRun, origin: Origin | null,
                  asked: { readonly door: string; readonly request: unknown }, now: number): void;
  };
  readonly admission: {
    numbers(): Tiers;
    tier(): TierName;
    tiers(): Tiers | null;
    unadmitted(): Refusal | null;
  };
  readonly bodies: Pick<Bodies, "begin" | "fetch" | "rereading">;
  readonly connections: {
    usable(): readonly Usable[];
    egressible(): readonly Egressible[];
  };
  readonly egress: Pick<Egress, "end" | "spent">;
  readonly files: Pick<Files, "held" | "leaseAcquire" | "leaseRelease" | "leaseRenew"
    | "leasesLapsed" | "liveLeases" | "networkAnswered" | "networkSent" | "openFile" | "range"
    | "sandboxLease" | "stat">;
  readonly grants: {
    standing(client: unknown, generation: unknown, authenticatedAt: number | null):
        StandingGrant;
  };
  readonly ledger: Pick<Ledger, "answer" | "hold" | "mailDue" | "mailFor" | "nextMail" | "post"
    | "release" | "roomOf" | "settled" | "taken" | "undeliverable">;
  readonly notices: Pick<Notices, "add" | "once">;
  readonly policy: {
    egressGeneration(): number;
    models(): SpendCaps;
    read(): { readonly document: PolicyDocument };
  };
  readonly relay: Pick<Relay, "settled">;
  readonly signins: Pick<Signins, "end" | "holds" | "list">;
  readonly starter: Pick<Starter, "seeded" | "version">;
}

export class Launcher implements RunHost {
  readonly #o: LauncherNeeds;
  /** The owner's runs: admission, grants, conversations, schedules (`runs.ts`). */
  readonly runs: Runs;
  /** Who waits on a run's settle, on this instance only: a door's bounded wait. */
  readonly #waiters = new Map<string, Set<() => void>>();
  /**
   * A long reply's body placed before an events unit (`runEvents`), set only for that
   * synchronous unit, for its settle to post by reference (`answer`).
   */
  #replyPlaced: Placed | null = null;
  /**
   * The jobs' stops sent to the sandbox and not yet answered (`stopAfter`). A lease's release
   * is sent only once those out when it is due have answered (`#released`), so it reaches the
   * sandbox's serial chain after the stops of its lease's jobs: a release drops its lease's
   * `launched` rows, so a run's cancel after it finds nothing to stop, and it pauses only a
   * sandbox nothing lives in (`sandbox/src/control.ts` `release`).
   */
  readonly #stopping = new Set<Promise<void>>();
  /** The leases whose release is asked and not yet sent: each is sent once. */
  readonly #releasing = new Set<string>();

  constructor(o: LauncherNeeds) {
    this.#o = o;
    this.runs = new Runs(o.storage, this);
  }

  // -- RUNHOST: what Runs reaches past its rows -----------------------------------------

  unadmitted(): Refusal | null {
    return this.#o.admission.unadmitted();
  }

  tier(): TierName {
    return this.#o.admission.tier();
  }

  tiers(): Tiers | null {
    return this.#o.admission.tiers();
  }

  reachable(): readonly Usable[] {
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
    return this.#o.egress.spent(period, source);
  }

  lease(now: number, authority: string): LiveLease | null {
    return this.#o.files.sandboxLease(now, authority);
  }

  leases(now: number): readonly LiveLease[] {
    return this.#o.files.liveLeases(now);
  }

  releaseLease(leaseId: string, epoch: number): void {
    // A lease a sign-in holds stays: its sandbox is in use with no run.
    if (!this.#o.signins.holds(leaseId)) {
      this.#giveBack(leaseId, epoch);
    }
  }

  renewLease(leaseId: string, epoch: number): void {
    this.#o.files.leaseRenew(leaseId, epoch);
  }

  leasesLapsed(now: number): void {
    // Each lapsed lease's sandbox paused, as a given-back one's is.
    for (const leaseId of this.#o.files.leasesLapsed(now)) {
      this.#released(leaseId);
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

  noticeOnce(type: string, detail: string): void {
    this.#o.notices.once(type, detail, clock());
  }

  /** From a unit: each run's cancel sent to the sandbox once the unit is over (`stopAfter`). */
  stop(runs: readonly string[]): void {
    for (const run of runs) {
      void this.stopAfter(() => this.cancel(run));
    }
  }

  /**
   * From a unit: `stop`, a job's stop in the sandbox (a run's cancel, a sign-in's), sent once
   * the unit is over, before the release of any lease given back meanwhile (`#released`).
   * What it answers.
   */
  stopAfter<T>(stop: () => Promise<T>): Promise<T> {
    const stopped = Promise.resolve().then(stop);
    const out = stopped.then(() => undefined, () => undefined);
    this.#stopping.add(out);
    void out.then(() => this.#stopping.delete(out));
    return stopped;
  }

  /**
   * In a unit: a job whose start may have launched after it stopped standing (cancelled,
   * ended or lost while its start was out: `#start`, `signinStart`), stopped (`stopAfter`);
   * then, when nothing holds its lease now, that lease given back and its sandbox released
   * after the stop, however the lease went: one given back while the start was out had its
   * release reach the sandbox first, and the start then resumed it. What the stop answers.
   */
  landed<T>(lease: { readonly lease_id: string; readonly epoch: number },
            stop: () => Promise<T>): { stopped: Promise<T> } {
    const stopped = this.stopAfter(stop);
    if (!this.runs.holds(lease.lease_id) && !this.#o.signins.holds(lease.lease_id)) {
      this.#giveBack(lease.lease_id, lease.epoch);
      this.#released(lease.lease_id);
    }
    return { stopped };
  }

  /**
   * In a unit: the client's runs ended (`Runs.endClient`), their CLIs stopped once the unit
   * is over.
   */
  endClient(client: string, now: number): void {
    this.stop(this.runs.endClient(client, now));
  }

  /** In a unit: every live run cancelled (`Runs.endAll`), its CLI stopped once it is over. */
  endAll(now: number): void {
    this.stop(this.runs.endAll(now));
  }

  // -- A LAUNCH --------------------------------------------------------------------------

  /**
   * A new owner's starter seed, awaited before a door reads an agent: its first `converse`
   * finds `main` however soon it comes, the lane's pass joined if it runs.
   */
  async seeded(): Promise<void> {
    if (this.#o.starter.version() === 0) {
      await this.#o.starter.seeded(clock());
      await this.#o.sync();
    }
  }

  /**
   * What a run of `agent` is admitted on: its `agent.json`, read whole, the etag it was read
   * at, the ranking its `auto` ranks by (the owner's own file, `RANKING`, whole, else the
   * platform's), its `mcp.json` (none, no servers) and its `hooks.json` (none, no hooks). An
   * owner's file that is no ranking
   * refuses the run (`invalid`), never a silent fall back to the platform's; a definition
   * file that does not fit refuses it too (`agent_file_invalid`), never half-read. A door's
   * read waits its turn on the isolate's pool (`slotted`).
   */
  readAgent(agent: string): Promise<AgentRead> {
    return this.#readAgent(agent, slotted);
  }

  /**
   * `readAgent` for the `runs` lane, whose read takes the pool's units now or is refused
   * `busy` (`slottedNow`), which holds the lane (`run`): inside the alarm a lane never waits
   * on the pool, since the object's other lanes would wait with it (`unitsNow`).
   */
  #laneAgent(agent: string): Promise<AgentRead> {
    return this.#readAgent(agent, slottedNow);
  }

  async #readAgent(agent: string, hold: PoolHold): Promise<AgentRead> {
    const read = await this.#whole(agentPath(agent, AGENT_FILES.agent), hold);
    if (read === null) {
      throw new Refusal("agent_undefined", AGENT_UNDEFINED);
    }
    const servers = await this.#whole(agentPath(agent, AGENT_FILES.mcp), hold);
    const hooks = await this.#whole(agentPath(agent, AGENT_FILES.hooks), hold);
    return { file: agentFile(definitionText(agent, AGENT_FILES.agent, read.bytes)),
             etag: read.etag, ranking: await this.#ranking(hold),
             mcp: servers === null ? { servers: {} }
               : agentMcp(definitionText(agent, AGENT_FILES.mcp, servers.bytes)),
             hooks: agentHooks(hooks === null ? '{"hooks": {}}'
               : definitionText(agent, AGENT_FILES.hooks, hooks.bytes)) };
  }

  async #ranking(hold: PoolHold): Promise<Ranking> {
    const read = await this.#whole(RANKING, hold);
    if (read === null) {
      return shippedRanking();
    }
    let value: unknown;
    try {
      value = JSON.parse(utf8Decoder("strip").decode(read.bytes));
    } catch {
      throw new Refusal("invalid", `${RANKING} is not JSON text`);
    }
    return rankingOf(value, RANKING);
  }

  /**
   * The owner's file at `path`, read whole, and its etag; null where nothing is. Anything else
   * there (a folder) refuses the run (`Files.openFile`), never read as no file (Fact 8). A file
   * past an inline one is read holding the pool's units as `hold` takes them.
   */
  async #whole(path: string, hold: PoolHold):
      Promise<{ bytes: Uint8Array; etag: string } | null> {
    const content = await this.#o.door(() => this.#o.files.openFile(path));
    if (content === null) {
      return null;
    }
    if (content.entry.size > ADMITTED_FILE_BYTES) {
      throw new Refusal("invalid", `${path} is past the ${ADMITTED_FILE_BYTES} bytes a run's ` +
                        "admission reads whole");
    }
    const bytes = content.inline ?? await hold(ownerSlots(), ADMITTED_FILE_UNITS, () =>
      this.#o.files.range(content.chunks ?? [], 0, content.entry.size));
    return { bytes, etag: content.entry.etag };
  }

  /**
   * A door's run: admitted (`#admit`), and, when it starts now, started through the sandbox
   * with what came of it recorded (`#start`). Its id: a run that waits for its conversation's
   * live run is written `waiting` and starts from the lane, its turn come.
   */
  async launch(read: AgentRead, ask: () => Omit<Ask, "file" | "ranking" | "connectors">):
      Promise<string> {
    const start = await this.#admit(read, ask, null, () => {});
    if (typeof start === "string") {
      return start;
    }
    await this.#start(start);
    return start.run;
  }

  /**
   * A lane row's run (`waiting`, the waiting run the lane starts; `taken`, the row's input
   * taken in the admission's unit), as a door's, but the pass waits on its start only to
   * `LANE_CALL_MS` (`inTime`), as on every outside call a row makes. A start still out then
   * stays fenced in its row: the run is `queued`, which no pass starts again; its answer is
   * recorded when it comes, against the run as it then stands (`#start`); and one that never
   * comes leaves the run to `Runs.lost`, a lease's TTL after its admission, which settles it
   * `unknown` and stops it. A fault of the start's own after the pass went on is a bug,
   * logged, since no pass is left to fail.
   */
  async #laneLaunch(read: AgentRead, ask: () => Omit<Ask, "file" | "ranking" | "connectors">,
                    waiting: string | null, taken: (run: string) => void): Promise<void> {
    const start = await this.#admit(read, ask, waiting, taken);
    if (typeof start === "string") {
      return;
    }
    const started = this.#start(start);
    if (await inTime(started, LANE_CALL_MS) === "late") {
      started.catch((error: unknown) =>
        console.error("a run's start failed after the lane's pass went on", error));
    }
  }

  /**
   * A run admitted in one unit (`ask`, which binds the call again, and the agent's file still
   * at the etag it was read at), and its start built once, so a second send is the same start;
   * or the run's id alone when it does not start now (a retry's run, or one written waiting).
   */
  async #admit(read: AgentRead, ask: () => Omit<Ask, "file" | "ranking" | "connectors">,
               waiting: string | null, taken: (run: string) => void):
      Promise<string | SandboxStart> {
    const admission = await this.#o.door(() => {
      const made = ask();
      if (this.#o.files.stat(agentPath(made.agent, AGENT_FILES.agent))?.etag !== read.etag) {
        throw new Refusal("agent_changed", AGENT_CHANGED);
      }
      const { file, ranking } = read;
      // The servers under the policy the run's network is made by, in this same unit, each
      // one an owner's connector is sealed for bound to it, reached through the egress door.
      const kept = agentServers(read.mcp, this.#o.policy.read().document.egress);
      const bound = connectorsOf(kept.servers, this.#o.connections.egressible());
      const admitted = this.runs.admit({ ...made, file, ranking,
                                         connectors: [...new Set(Object.values(bound))] },
                                       clock(), (chosen) =>
        this.#runConfig({ run: chosen.run, secret: chosen.secret,
                          engine: chosen.engine, credential: chosen.credential,
                          ...(chosen.connection === null ? {}
                            : { connection: chosen.connection }) }), waiting);
      taken(admitted.run);  // the input it was asked on is its own now (a mail's, taken)
      if (admitted.existing) {
        return admitted.run;
      }
      for (const { name, host, why } of kept.dropped) {
        this.#o.notices.once("mcp_server_dropped", `${made.agent}: its MCP server ${name} (${
          host}) is left out of its runs: ${why}`, clock());
      }
      // Only a run that starts now decides its definition's commands, so a run retried or
      // written waiting uses no yes. A command server is bound to no connector, so one left
      // out changes no grant.
      const own = this.#ownCommands({ id: admitted.run, agent: made.agent,
                                      conversation: admitted.conversation }, made.origin,
                                    kept.servers, read.hooks.hooks);
      return { agent: made.agent, unattended: made.unattended, admitted, hooks: own.hooks,
               servers: throughConnectors(own.servers, bound, admitted.run, admitted.secret) };
    });
    if (typeof admission === "string") {
      return admission;
    }
    const { admitted, agent, unattended, servers, hooks } = admission;
    return {
      run: admitted.run, agent, conversation: admitted.conversation,
      engine: admitted.engine, credential: admitted.credential, model: admitted.model,
      session_id: admitted.session_id, prompt: admitted.prompt, unattended,
      authority: admitted.authority, lease: admitted.lease, tokens: admitted.tokens,
      network: admitted.network, config: admitted.config,
      hosts: vendorHosts(admitted.engine, admitted.credential),
      route: sealRoute(this.#o.env.ROUTING_MAC_KEY, { owner: this.#o.owner(),
                                                      authority: admitted.authority }),
      urls: runUrls(admitted.run, admitted.secret), mcp_servers: servers, hooks };
  }

  /**
   * An admitted run started through the sandbox, and what came of it (`StartOutcome`)
   * recorded against the run as it stands when the answer comes: `running`, `failed` with
   * the sandbox's code or its refusal's (a transient one's once its start, sent again while
   * the run stands, did not start within the bound), `failed` `start_malformed` when its
   * answer came out of the contract's shape, or `unknown` when no answer came. A run stands
   * while it holds its grant (a cancel, a revocation, a settle or a lost runner each take it),
   * and only a run that stands gets a second chance: its start sent again, or its wait for a
   * sandbox; one that no longer stands settles by its start's answer (one cancelled,
   * `cancelled`: `Runs.settle`). Whatever may have launched and no longer stands is stopped in
   * the sandbox, and its sandbox released after once nothing holds its lease (`landed`): a
   * start with no answer it can read, and one that started after its run stopped standing
   * (cancelled while its start was out, or settled lost while it was late), which then
   * settles from its runner's end, or is refused at its first post.
   */
  async #start(start: SandboxStart): Promise<void> {
    const { run } = start;
    const stands = () => this.runs.grant(run) !== null;
    const answer = await startOutcome("a run", () => this.#o.sandbox.start(start),
                                      () => this.#o.door(stands));
    const stopped = await this.#o.door(() => {
      const standing = stands();
      if (answer.started === null) {
        this.runs.settle(run, answer.state, answer.code, null, clock());
      } else if (answer.started) {
        this.runs.started(run, clock());
      } else if (answer.code === SANDBOX_WAITING && standing) {
        // E2B runs as many sandboxes as its plan allows: the run waits, never refused, and
        // the lane starts it again when the platform's line rings the owner (`sandboxRung`).
        // The founder's alert reads E2B's own count (owed: `sandbox-probe.md`); this line is
        // the Worker log's record of it.
        if (this.runs.waitForSandbox(run, start.prompt)) {
          console.warn(`${SANDBOX_WAITING}: a run waits for a sandbox to free`);
        }
      } else {
        this.runs.settle(run, "failed", answer.code, null, clock());
        if (answer.code === SIGNIN_REQUIRED) {
          // No login inside that sandbox: the owner signs in there, never copies one.
          this.#o.notices.add(SIGNIN_REQUIRED, `${start.agent}: sign in to ${start.engine
          } inside ${start.authority === OWNER ? "your own" : `${start.authority}'s`
          } sandbox, through the vendor's own flow`, clock());
        }
      }
      return answer.started === null || (answer.started && !standing)
        ? this.landed(start.lease, () => this.cancel(run)) : null;
    });
    await stopped?.stopped;
    await this.#o.sync();
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
  async cancel(run: string): Promise<void> {
    try {
      await this.#o.sandbox.cancel({ run });
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
    try {
      await this.#o.sandbox.release({ lease_id: leaseId });
    } catch {
      console.error("a lease's release did not reach the sandbox; its idle reap pauses it");
    }
  }

  /**
   * From a unit: the lease's sandbox released (`#sandboxRelease`) once the unit is over and
   * the jobs' stops out then have answered (`#stopping`), once however often it is asked
   * before it is sent.
   */
  #released(leaseId: string): void {
    if (this.#releasing.has(leaseId)) {
      return;
    }
    this.#releasing.add(leaseId);
    queueMicrotask(() => void (async () => {
      await Promise.all(this.#stopping);
      this.#releasing.delete(leaseId);
      await this.#sandboxRelease(leaseId);
    })());
  }

  /** In a unit: the lease given back if it lives, its streams ended and its sandbox released. */
  #giveBack(leaseId: string, epoch: number): void {
    if (this.#o.files.leaseRelease(leaseId, epoch).released) {
      this.#o.egress.end("lease", (live) => live.lease === leaseId);
      this.#released(leaseId);
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
      return this.#o.bodies.begin(answer.result, scopeOf(answer.stamp?.client ?? null));
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
   * its agent (the run's id its post's own, so it is posted once), its body
   * placed or not; a refusal a notice.
   */
  #reply(answer: MailAnswer, authority: MailAuthority | null, placed: Placed | null): void {
    try {
      // The reply is the owner's agents' content: held to the tier's storage too.
      this.#o.files.held(OWNER, () => {
        if (answer.mail === null) {
          this.#o.ledger.post({ native: answer.agent },
                              this.#o.ledger.roomOf(OWNER, nativeId(answer.agent)), answer.run,
                              answer.result, null, clock(), authority, placed,
                              scopeOf(authority?.client ?? null));
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
    const admitted = this.#o.admission.unadmitted() === null;
    if (admitted && this.#o.ledger.mailDue(this.runs.queueRoom(MAIL_INPUT_BYTES))) {
      return now;
    }
    const times = [this.runs.next(now, admitted),
                   ...(admitted ? this.#behind(now).map((each) => each.due) : []),
                   ...this.#narrowed(now).map((each) => each.due)]
      .filter((time): time is number => time !== null);
    return times.length === 0 ? null : Math.min(...times);
  }

  /**
   * The `runs` lane's pass: runs whose runner was lost settled `unknown`; then each waiting
   * run whose turn came started, oldest first, through the same admission as a door's (one
   * its turn refuses settles `failed`, the refusal in the owner's notices); then each mail
   * to one of the owner's agents that no run took, oldest first, starting that agent's run
   * (`#deliver`); then each due schedule claimed, once, and run the same way; a refusal kept
   * on its row and in the owner's notices, never thrown.
   *
   * A refusal for the moment (as it crossed, or the runtime's overload: `refusalOf`) says the
   * same request succeeds after its `Retry-After`, and nothing of the row it met: it comes of
   * what every row shares (the owner's storage catching up, the isolate's pool, a binding
   * busy). So the pass takes no more rows and holds the lane until then (`Held`), the row as
   * it was (the run waits on, the mail is delivered again, the schedule stays due), where
   * `schedule.ts` reads a throw as a bug. Anything else thrown is one.
   */
  async run(_now: number, until: number): Promise<Held | undefined> {
    try {
      await this.#pass(until);
      return undefined;
    } catch (error) {
      const refusal = refusalOf(error);
      if (refusal === null || !refusal.transient) {
        throw error;
      }
      return { hold: clock() + (refusal.retryAfter ?? 0) };
    }
  }

  async #pass(until: number): Promise<void> {
    // The lane reads the control record itself (`CONTROL`), so its kept copy, whose tiers and
    // switched-on sign-ins the lane's runs take, moves though the owner makes no door call: a
    // source switched off stops powering a schedule's, a mail's or a waiting run at this pass.
    await this.#o.control().read();
    await this.#o.door(() => this.runs.lost(clock()));
    await this.reapply();
    while (clock() < until) {
      const waiting = await this.#o.door(() => {
        const next = this.#o.admission.unadmitted() !== null ? null : this.runs.nextWaiting();
        return next === null ? null : { run: next.id, ask: this.runs.waitingAsk(next.id)! };
      });
      if (waiting === null) {
        break;
      }
      try {
        const read = await this.#laneAgent(waiting.ask.agent);
        const prompt = waiting.ask.mail === null ? waiting.ask.prompt
          : await this.#mailPrompt(waiting.ask.mail.message, waiting.ask.agent);
        await this.#laneLaunch(read, () => ({
          ...waiting.ask, prompt, tiers: this.#o.admission.numbers() }),
                               waiting.run, () => {});
      } catch (error) {
        const refusal = refusalOf(error);
        if (refusal === null || refusal.transient) {
          throw error;  // a transient refusal holds the lane, and the run waits on
        }
        if (refusal.code === "conversation_busy" || refusal.code === "tier_runs") {
          break;  // not its turn after all: it keeps waiting, and the lane comes back
        }
        const reason = refusal.message;
        await this.#o.door(() => {
          if (this.runs.refuseWaiting(waiting.run, refusal, clock())) {
            this.#o.notices.add("run_refused", `${waiting.ask.agent}: ${reason}`, clock());
          }
        });
      }
    }
    while (clock() < until) {
      // A placed body (ADR-035) is read from R2 between the lane's units, as a door's.
      const next = (bodies: ReadonlyMap<string, string>) => {
        if (this.#o.admission.unadmitted() !== null) {
          return null;
        }
        if (this.runs.queueRoom(MAIL_INPUT_BYTES)) {
          this.#o.ledger.release();  // the inputs' queue has room for any held mail again
        }
        return this.#o.ledger.nextMail(bodies);
      };
      const mail = await this.#o.bodies.rereading(() => {
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
    // last write, never its first; its record beside it, before the run's sandbox can
    // report a hold, `Runs.#hold`, that a later record would clear.)
    while (clock() < until) {
      const row = await this.#o.door(() => this.#o.admission.unadmitted() !== null
        ? null : this.runs.due(clock()));
      if (row === null) {
        return;
      }
      let gone = false;
      try {
        const read = await this.#laneAgent(row.agent);
        await this.#laneLaunch(read, () => {
          if (!this.runs.stands(row)) {
            gone = true;
            throw new Refusal("no_schedule", NO_SCHEDULE);
          }
          return {
            initiator: "schedule", ...scheduleAuthority(row), agent: row.agent,
            conversation: null, request: null,
            tiers: this.#o.admission.numbers(), unattended: true,
            prompt: row.input, mail: null };
        }, null, (run) => {
          this.runs.claim(row, clock());
          this.runs.ran(row.id, run, null);
        });
      } catch (error) {
        const refusal = refusalOf(error);
        if (refusal === null || refusal.transient) {
          throw error;  // a transient refusal holds the lane: unclaimed, the schedule stays due
        }
        if (gone) {
          continue;  // cancelled, or claimed, since it was read: nothing of it fires now
        }
        const reason = refusal.message;
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
    const mail = await this.#o.bodies.rereading(() => {
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
   * held, until the queue has room; one refused for the moment leaves it as it was (the pass
   * holds the lane, `run`); any other refusal fails the mail, with a notice.
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
      const read = await this.#laneAgent(mail.agent);
      const conversation = mailConversation(mail.agent, mail.thread, origin?.client ?? null);
      await this.#laneLaunch(read, () => ({
        initiator: "mail", stamp, origin, agent: mail.agent,
        conversation: this.runs.kept(conversation, mail.agent, origin?.client ?? null)
          ? conversation : null,
        request: sha256Hex(`mail\0${mail.message_id}\0${mail.agent_id}`),
        tiers: this.#o.admission.numbers(), unattended: !mail.attended,
        prompt, mail: { message: mail.message_id, conversation } }), null,
                             () => this.#o.ledger.taken(mail.message_id, mail.agent_id, clock()));
    } catch (error) {
      const refusal = refusalOf(error);
      if (refusal === null || refusal.transient) {
        throw error;  // a transient refusal holds the lane, and the mail is delivered again
      }
      if (refusal.code === "agent_changed") {
        return;  // read again on the next turn of the pass
      }
      const reason = refusal.message;
      await this.#o.door(() => {
        if (refusal.code === "run_inputs_full") {
          this.#o.ledger.hold(mail.message_id, mail.agent_id, clock());
          return;
        }
        // Its code on the recipient row; its words, a quiet one's own, in the notice.
        this.#o.ledger.undeliverable(mail.message_id, mail.agent_id, refusal.code, clock());
        this.#o.notices.add("mail_refused", `${mail.agent}: ${reason}`, clock());
      });
    }
  }

  // -- THE SANDBOX'S LEASES ----------------------------------------------------------------

  /** In a unit: a lease no run and no sign-in holds now given back, as a settle gives it. */
  leaseFreed(leaseId: string, epoch: number): void {
    if (!this.runs.holds(leaseId) && !this.#o.signins.holds(leaseId)) {
      this.#giveBack(leaseId, epoch);
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
   * start that fails gives the lease back, so a mark on it admits nothing.
   */
  openLease(now: number, authority: string):
      { lease: Lease; tokens: OpenedTokens; network: NetworkPlan } {
    const egress = mintToken(this.#o.env.ROUTING_MAC_KEY, "egress", this.#o.owner(), LEASE_LIVED);
    const sync = mintToken(this.#o.env.ROUTING_MAC_KEY, "sync", this.#o.owner(), LEASE_LIVED);
    const runner = randomHex(32);
    const generation = this.#o.policy.egressGeneration();
    const lease = this.#o.files.leaseAcquire(authority, "sandbox", {
      egress: egress.hash, sync: sync.hash, runner: sha256Hex(runner) }, generation);
    const live = new Set(this.#o.files.liveLeases(now).map((each) => each.lease_id));
    this.#o.egress.end("lease", (stream) => !live.has(stream.lease));
    const plan = this.#networkPlan(generation);
    return { lease, tokens: { sync_token: sync.token, runner_token: runner },
             network: { ...plan, network: withEgressToken(plan.network, egress.token) } };
  }

  /**
   * In a unit: the network for the owner's egress policy at the egress `generation` the unit
   * read, with no egress token (`networkDocument`).
   */
  #networkPlan(generation: number): NetworkPlan {
    return networkDocument(this.#o.policy.read().document.egress, generation);
  }

  /**
   * In a unit: the live leases whose network is behind the current egress generation (an
   * egress policy write since their start), each with its re-apply's due time: at once
   * when none was tried under the current generation, else `NETWORK_RETRY_S` after the
   * last was sent or failed.
   */
  #behind(now: number): { lease: SandboxLease; due: number }[] {
    const generation = this.#o.policy.egressGeneration();
    return this.#o.files.liveLeases(now)
      .filter((lease) => lease.network_generation !== generation)
      .map((lease) => ({ lease, due: lease.network_tried_generation !== generation
        || lease.network_tried_at === null ? now
        : Math.max(now, lease.network_tried_at + NETWORK_RETRY_S) }));
  }

  /**
   * In a unit: each live lease owing a narrowed egress policy, with when it is given back if
   * no re-apply confirms it first (`#unconfirmed`).
   */
  #narrowed(now: number): { lease: SandboxLease; due: number }[] {
    return this.#o.files.liveLeases(now).flatMap((lease) => lease.narrowed_at === null
      ? [] : [{ lease, due: lease.narrowed_at + NARROWED_WITHIN_S }]);
  }

  /**
   * In a unit: each live lease whose sandbox has owed a narrowed egress policy for
   * `NARROWED_WITHIN_S`, no re-apply having confirmed it, given back, so no host the owner
   * dropped stays reachable from it: its sign-ins ended and its runs settled `failed`
   * (`NETWORK_UNCONFIRMED`), so its runner's next post is refused and stops each CLI (which
   * needs neither the sandbox Worker nor E2B, whose fault may be why no re-apply landed);
   * its tokens and streams end with it; each CLI stopped through the Worker as well, and its
   * sandbox paused after those stops (`#released`); and the owner told. Whatever the owner's
   * admission: a stop is never held.
   */
  #unconfirmed(now: number): void {
    const due = this.#narrowed(now).filter(({ due: at }) => at <= now)
      .map(({ lease }) => lease);
    const held = due.length === 0 ? [] : this.#o.signins.list();
    for (const lease of due) {
      const signins = held.filter((row) => row.lease_id === lease.lease_id);
      for (const row of signins) {
        this.#o.signins.end(row.id);
        this.#o.relay.settled(row.id);
      }
      const runs = this.runs.endLease(lease.lease_id, NETWORK_UNCONFIRMED, now);
      this.leaseFreed(lease.lease_id, lease.epoch);
      this.stop(runs);
      for (const row of signins) {
        void this.stopAfter(() => this.#o.sandbox.signinCancel({ authority: row.authority,
                                                                 id: row.id }))
          .catch(() => console.error("a stopped sign-in's cancel did not reach the sandbox"));
      }
      const sandboxOf = lease.authority === OWNER ? "your own sandbox"
        : `client ${lease.authority}'s sandbox`;
      this.#o.notices.add(NETWORK_UNCONFIRMED, `The egress policy you narrowed was not in ` +
        `force on ${sandboxOf} within ${NARROWED_WITHIN_S} s, so it was stopped: ` +
        `${runs.length} run(s) and ${signins.length} sign-in(s) ended. A new run starts it ` +
        "again under the policy in force.", now);
    }
  }

  /**
   * Each live lease past its narrowing's bound given back (`#unconfirmed`); then each whose
   * network is behind the current egress generation, made again under it and re-applied on
   * its sandbox (the sandbox Worker's `network`, which keeps the live egress token and the
   * launched jobs' vendor hosts), each one due (`#behind`): at once after a policy write
   * that moved the egress generation, whose unit wakes this lane (`synced`). Its send is a
   * unit (`networkSent`): the lease's calls are admitted under the new generation from
   * there on, since only that network's transform sets it. Its answer is another
   * (`networkAnswered`, as `networkOutcome` reads it): `applied` confirms it; `at_resume`
   * keeps it out for its paused sandbox; a failure clears it. No answer within
   * `LANE_CALL_MS` records nothing: the send's retry clock stands.
   */
  async reapply(): Promise<void> {
    const { sandbox } = this.#o;
    const sent = await this.#o.door(() => {
      this.#unconfirmed(clock());
      if (this.#o.admission.unadmitted() !== null) {
        return [];
      }
      const now = clock();
      const generation = this.#o.policy.egressGeneration();
      const due = this.#behind(now).filter(({ due: at }) => at <= now);
      const out: { lease: SandboxLease; generation: number; network: NetworkPlan }[] = [];
      for (const { lease } of due) {
        if (this.#o.files.networkSent(lease.lease_id, lease.epoch, generation, now)) {
          out.push({ lease, generation, network: this.#networkPlan(generation) });
        }
      }
      return out;
    });
    await Promise.all(sent.map(async ({ lease, generation, network }) => {
      const answer = await inTime(networkOutcome(() => sandbox.network({
        authority: lease.authority, lease: { lease_id: lease.lease_id, epoch: lease.epoch },
        network })), LANE_CALL_MS);
      if (answer === "late") {
        return;
      }
      await this.#o.door(() => this.#o.files.networkAnswered(
        lease.lease_id, lease.epoch, generation, answer, clock()));
    }));
  }

  /**
   * In the admission's unit: `servers` and `hooks` less each command of them (a server started
   * by its command, a hook) the owner's rules hold for `run` (`decideCommand`, its door the
   * definition's file, its request the entry), the owner told once of each left out, and why.
   * A held one's card is filed and noticed by the decision; its yes resumes the run's
   * conversation, whose next run's admission admits it once.
   */
  #ownCommands(run: DecidedRun, origin: Origin | null,
               servers: Readonly<Record<string, McpServer>>, hooks: AgentHooks["hooks"]):
      { servers: Readonly<Record<string, McpServer>>; hooks: AgentHooks["hooks"] } {
    const now = clock();
    const runs = (door: string, what: string, request: object): boolean => {
      try {
        this.#o.actions.decideCommand(run, origin, { door, request }, now);
        return true;
      } catch (error) {
        const refusal = refusalOf(error);
        if (refusal === null) {
          throw error;
        }
        const card = refusal.facts?.["card_id"];
        this.#o.notices.once("definition_command_dropped", `${run.agent}: ${what} is left ` +
          `out of this run: ${typeof card === "string" ? `it waits for your answer to card ${
            card}` : refusal.message}`, now);
        return false;
      }
    };
    return {
      servers: Object.freeze(Object.fromEntries(Object.entries(servers).filter(([name, server]) =>
        !("command" in server) || runs(AGENT_FILES.mcp, `its MCP server ${name} (${
          server.command})`, { server: name, ...server })))),
      hooks: Object.freeze(Object.fromEntries(Object.entries(hooks).map(([event, list]) =>
        [event, Object.freeze(list.filter((hook, at) => runs(AGENT_FILES.hooks,
          `its ${event} hook ${hook.run[0]!}`, { hook: `${event}[${at}]`, ...hook })))]))) as
        AgentHooks["hooks"],
    };
  }

  /**
   * One run's whole environment, from its record,
   * its credential and an API-key run's connection, with the owner's egress policy and its
   * generation: refused (`invalid`) when the record does not run on that credential.
   */
  #runConfig(run: SandboxRun): RunConfig {
    let engine;
    try {
      engine = runEnv(bundledSources(), run.engine, run.credential);
    } catch (error) {
      throw error instanceof SourceRecordError ? new Refusal("run_config_invalid", error.message) : error;
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
}
