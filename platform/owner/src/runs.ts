/**
 * Runs in the owner's object (ADR-034 decision 4; `engines-replan.md` §2): the unit of work
 * is a run, one engine invocation, a provider's CLI in the owner's sandbox. This module
 * keeps the runs' rows (migration 12) and decides on them; the object (`owner-do.ts`) reads
 * the agent's file, calls the sandbox and relays the events around its units.
 *
 * **Admit, in one gate unit** (`admit`), each refusal a fixed code:
 * 1. the owner is admitted for work (`owner_suspended`, `owner_erased`), and a sandbox is
 *    bound (`SANDBOX_ABSENT`), before anything is written;
 * 2. the agent is enabled (`agent.json`, `AGENT_DISABLED`);
 * 3. the initiator's stamp stands: the owner's own, or a client's grant at the generation it
 *    was stamped with, rechecked (`Grants.standing`; `AUTHORITY_CHANGED`);
 * 4. a retry of the same request is the same run (its `request` key), never a second one;
 * 5. a conversation continued is the caller's own and of this agent (`NO_CONVERSATION`),
 *    with no live run in it, since one engine session runs one invocation at a time
 *    (`CONVERSATION_BUSY`, a technical ceiling);
 * 6. the tier's simultaneous runs hold (`TIER_RUNS`), from the numbers the binder handed
 *    and the owner's own tier (`tier.ts`);
 * 7. the source is chosen (`choose`): `agent.json`'s engine, or `auto`'s strongest connected
 *    source that has an engine (Direction 4), and for an unattended run (a schedule's, the
 *    mail's) only a credential its record allows unattended (ADR-033 decision 3,
 *    `UNATTENDED_REFUSED`);
 * 8. the owner's own limits hold (`OWNER_LIMIT`): an API-key run's source and the owner's
 *    day and month spend under their caps (the policy's, off by default). A signed-in run
 *    spends on the vendor's plan, which TinyAssets never sees; the vendor's limits hold.
 * Then, in the same unit: the conversation made or touched, the run written `queued`, the
 * sandbox's lease opened, or joined while one is live (the sandbox's runs share it), and the
 * run grant minted: the lease, the stamp and origin, the scopes (the agent's, or for a
 * client's run their intersection with what that client's grant holds where it acts) and
 * the run's own connection (none on the vendor's own sign-in). So simultaneous runs each
 * carry their own connection, and the egress door holds each call to its own run's
 * (`egress.ts`).
 *
 * **Settle** (`settle`), once, in a unit: `succeeded` from the runner's end with exit 0 and
 * no error event, `failed` otherwise (a cancel's is `cancelled`), `unknown` when the runner
 * was lost (its lease lapsed, or a start whose answer never came). Unknown is never
 * replayed. The run grant goes with it, and the lease when no live run holds it any more.
 * There is no platform deadline: a run's only time bound is the sandbox's own continuous
 * runtime ceiling, as its lease's tokens carry it (`RUN_DEADLINE_S`).
 *
 * **The `runs` lane** (`lane`): a schedule's due time, or a live run's lease expiry while
 * runs are live, never a poll: with no row due the lane has no job, and a due row whose
 * work is absent (a run on the owner's mail with none unacked) starts nothing.
 */
import { randomBytes } from "node:crypto";

import type { CredentialKind, Sources } from "../../doors/src/sources";

import { AGENT_DISABLED, type AgentFile, AUTO, RUN_CAPABILITIES, type RunCapability }
  from "./agent-file";
import { AUTHORITY_CHANGED, type StandingGrant, type Stamped } from "./authority";
import { type Candidate, ranked } from "./auto";
import { nextOccurrence, parseCron } from "./cron";
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import { periods } from "./money";
import {
  CONVERSATION_ID, CONVERSATIONS_KEPT, ERROR_BYTES, INPUT_BYTES, RESULT_BYTES, RUN_ID,
  RUNS_KEPT, SCHEDULE_ID,
} from "./run-rows";
import { type Frame, FRAME_VERSION, readEvent } from "./run-events";
import type { NetworkPlan } from "./network";
import { type RunConfig, SANDBOX_ABSENT } from "./sandbox";
import { TIER_RUNS, type TierName, type Tiers } from "./tier";
import { utf8Bytes } from "./text";

export const NO_CONVERSATION = "no_conversation: no such conversation of the caller's";
export const CONVERSATION_BUSY = "conversation_busy: a run in this conversation is still live";
export const UNATTENDED_REFUSED = "unattended_refused: no source of this agent may run " +
  "unattended on its record";
export const NO_SOURCE = "no_source: no connected source can run this agent's engine";
export const OWNER_LIMIT = "owner_limit";
export const NO_RUN = "no_run: no such run of the caller's";
export const RUN_ENDED = "run_ended: the run is no longer live";
export const NO_SCHEDULE = "no_schedule: no such schedule of the caller's";

export type RunState = "queued" | "running" | "succeeded" | "failed" | "unknown";
export type Initiator = "owner" | "client" | "schedule" | "mail";

/** A connected source as the choice reads it: `Connections.usable`'s shape. */
export interface UsableSource {
  readonly connection: string;
  readonly source: string;
  readonly provider: string;
  readonly display_name: string;
  readonly created_at: number;
  readonly models: readonly Candidate["model"][];
}

/** The owner's own spending caps, in µ$, as the model policy leaves them. */
export interface SpendCaps {
  readonly daily_cost: number | null;
  readonly monthly_cost: number | null;
  readonly sources: { readonly [source: string]: { readonly daily_cost?: number | null;
                                                   readonly monthly_cost?: number | null } };
}

/** The sandbox's live lease, as the runs read it (`Files.sandboxLease`). */
export interface LiveLease {
  readonly lease_id: string;
  readonly epoch: number;
  readonly expires_at: number;
}

/** A lease the admission opened: its two tokens, for the start that configures the sandbox. */
export interface OpenedTokens {
  readonly egress_token: string;
  readonly sync_token: string;
}

export interface RunDeps {
  /** The owner's `require_work` refusal, or null while it is admitted. */
  readonly unadmitted: () => string | null;
  readonly sandboxBound: () => boolean;
  readonly sources: () => Sources;
  /** The owner's active connections and their models. */
  readonly usable: () => readonly UsableSource[];
  /** The connections the sandbox may reach (a stored egress scope). */
  readonly egressible: () => readonly { readonly connection: string }[];
  /** A client's grant, standing at its stamp's generation; refused otherwise. */
  readonly standing: (stamp: Stamped) => StandingGrant;
  readonly tier: () => TierName;
  readonly caps: () => SpendCaps;
  readonly spent: (period: string, source: string | null) => number;
  readonly lease: {
    live(now: number): LiveLease | null;
    /** In the unit: a new lease, its tokens minted; refused while one is held. */
    open(now: number): { readonly lease: LiveLease; readonly tokens: OpenedTokens;
                         readonly network: NetworkPlan };
    release(leaseId: string, epoch: number): boolean;
    renew(leaseId: string, epoch: number): void;
  };
  /** In the unit, once a run settled: its streams ended (`egress.ts`). */
  readonly ended: (run: string) => void;
  /** In the unit, once the lease was given back: every stream under it ended. */
  readonly leaseEnded: (leaseId: string) => void;
}

/** What an admission asks for. */
export interface Ask {
  readonly initiator: Initiator;
  /** A client's stamp (null for the owner's own work), and that client's origin. */
  readonly stamp: Stamped | null;
  readonly origin: { readonly client: string; readonly family: string } | null;
  readonly agent: string;
  readonly file: AgentFile;
  /** The conversation continued, or null for a new one. */
  readonly conversation: string | null;
  /** The retry key (a hash of the client and its send id), or null. */
  readonly request: string | null;
  readonly tiers: Tiers;
  readonly unattended: boolean;
}

/** An admitted run, as the object starts it. */
export interface Admitted {
  readonly run: string;
  readonly conversation: string;
  readonly existing: false;
  readonly engine: string;
  readonly credential: CredentialKind;
  readonly connection: string | null;
  readonly model: string | null;
  readonly session_id: string | null;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly tokens: OpenedTokens | null;
  /** The owner's network, with the egress token in its transform, when this run opened it. */
  readonly network: NetworkPlan | null;
  readonly scopes: readonly RunCapability[];
  /** The run's whole environment, made before anything was written. */
  readonly config: RunConfig;
}

/** The source a run was given, and its id: what its environment is made from. */
export interface Chosen {
  readonly run: string;
  readonly engine: string;
  readonly credential: CredentialKind;
  readonly connection: string | null;
}

/** A request admitted before: its run, and nothing new written. */
export interface Retried {
  readonly existing: true;
  readonly run: string;
  readonly conversation: string;
}

/** A run's receipt, as the status doors show it. */
export interface RunReceipt {
  readonly run_id: string;
  readonly conversation_id: string;
  readonly agent: string;
  readonly engine: string;
  readonly credential: CredentialKind;
  readonly model: string | null;
  readonly state: RunState;
  readonly initiator: Initiator;
  readonly code: string | null;
  readonly result: string | null;
  readonly result_cut: boolean;
  readonly error: string | null;
  readonly exit_code: number | null;
  readonly requests: number;
  readonly tokens: number;
  readonly cost: number;
  readonly created_at: number;
  readonly started_at: number | null;
  readonly settled_at: number | null;
}

/** A run grant, as the run-scoped door and egress read it. */
export interface RunGrant {
  readonly run: string;
  readonly lease_id: string;
  readonly epoch: number;
  readonly stamp: Stamped | null;
  readonly origin: { readonly client: string; readonly family: string } | null;
  readonly scopes: readonly RunCapability[];
  readonly connection: string | null;
}

/** A frame as the runner posts it: its number, its time and the CLI's raw event. */
export interface PostedFrame {
  readonly seq: number;
  readonly at: number;
  readonly event: unknown;
}

export interface RunSchedule {
  readonly schedule_id: string;
  readonly agent: string;
  readonly trigger: "at" | "cron" | "mail";
  readonly cron: string | null;
  readonly if_mail: boolean;
  readonly next_at: number | null;
  readonly last_run: string | null;
  readonly last_error: string | null;
}

type RunRow = {
  id: string; agent: string; conversation: string; source: string; credential: CredentialKind;
  model: string | null; state: RunState; initiator: Initiator; client: string | null;
  stamp: string | null; request: string | null; unattended: number; cancel_requested: number;
  requests: number; tokens: number; cost: number; last_seq: number; result: string | null;
  result_cut: number; error: string | null; code: string | null; exit_code: number | null;
  created_at: number; started_at: number | null; settled_at: number | null;
};

type GrantRow = {
  run: string; lease_id: string; epoch: number; stamp: string | null; origin: string | null;
  scopes: string; connection: string | null;
};

type ScheduleRow = {
  id: string; agent: string; input: string; trigger: RunSchedule["trigger"]; cron: string | null;
  if_mail: number; next_at: number | null; client: string | null; stamp: string | null;
  origin: string | null; last_run: string | null; last_error: string | null;
};

const LIVE = "state IN ('queued', 'running')";

function id(prefix: string): string {
  return `${prefix}${randomBytes(16).toString("hex")}`;
}

/** `text` cut to at most `bytes` UTF-8 bytes on a character boundary, and whether it was. */
export function cut(text: string, bytes: number): { text: string; cut: boolean } {
  const encoded = new TextEncoder().encode(text);
  if (encoded.byteLength <= bytes) {
    return { text, cut: false };
  }
  let end = bytes;
  while (end > 0 && (encoded[end]! & 0xc0) === 0x80) {
    end--;  // never inside a character
  }
  return { text: new TextDecoder().decode(encoded.subarray(0, end)), cut: true };
}

function receipt(row: RunRow): RunReceipt {
  return {
    run_id: row.id, conversation_id: row.conversation, agent: row.agent, engine: row.source,
    credential: row.credential, model: row.model, state: row.state, initiator: row.initiator,
    code: row.code, result: row.result, result_cut: row.result_cut === 1, error: row.error,
    exit_code: row.exit_code, requests: row.requests, tokens: row.tokens, cost: row.cost,
    created_at: row.created_at, started_at: row.started_at, settled_at: row.settled_at,
  };
}

function grantOf(row: GrantRow): RunGrant {
  return {
    run: row.run, lease_id: row.lease_id, epoch: row.epoch,
    stamp: row.stamp === null ? null : JSON.parse(row.stamp) as Stamped,
    origin: row.origin === null ? null : JSON.parse(row.origin) as RunGrant["origin"],
    scopes: JSON.parse(row.scopes) as RunCapability[], connection: row.connection,
  };
}

function scheduleOf(row: ScheduleRow): RunSchedule {
  return { schedule_id: row.id, agent: row.agent, trigger: row.trigger, cron: row.cron,
           if_mail: row.if_mail === 1, next_at: row.next_at, last_run: row.last_run,
           last_error: row.last_error };
}

export class Runs {
  readonly #storage: OwnerStorage;
  readonly #deps: RunDeps;

  constructor(storage: OwnerStorage, deps: RunDeps) {
    this.#storage = storage;
    this.#deps = deps;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #run(runId: string): RunRow | undefined {
    return this.#sql.exec<RunRow>("SELECT * FROM runs WHERE id = ?", runId).toArray()[0];
  }

  /** How many runs are live now. A read. */
  live(): number {
    return this.#sql.exec<{ n: number }>(`SELECT COUNT(*) AS n FROM runs WHERE ${LIVE}`).one().n;
  }

  // -- ADMISSION ---------------------------------------------------------------------

  /** In a unit: the run admitted, written, its lease opened or joined and its grant minted. */
  admit(ask: Ask, now: number, prepare: (chosen: Chosen) => RunConfig): Admitted | Retried {
    const unadmitted = this.#deps.unadmitted();
    if (unadmitted !== null) {
      throw new Refused(unadmitted);
    }
    if (!this.#deps.sandboxBound()) {
      throw new Refused(SANDBOX_ABSENT);
    }
    if (!ask.file.enabled) {
      throw new Refused(AGENT_DISABLED);
    }
    const held = this.#held(ask);
    if (ask.request !== null) {
      const same = this.#sql.exec<RunRow>("SELECT * FROM runs WHERE request = ?", ask.request)
        .toArray()[0];
      if (same !== undefined) {
        if (same.client !== (ask.origin?.client ?? null) || same.agent !== ask.agent) {
          throw new Refused("conflict: this request id named another run");
        }
        return { existing: true, run: same.id, conversation: same.conversation };
      }
    }
    // A run whose runner was lost (its lease gone) holds no slot: it is settled `unknown`
    // first, so the tier counts only the runs that still run.
    this.lost(now);
    const conversation = ask.conversation === null ? null : this.#conversation(ask);
    const tier = this.#deps.tier();
    if (this.live() >= ask.tiers[tier].runs) {
      throw new Refused(`${TIER_RUNS} (${tier}: ${ask.tiers[tier].runs})`);
    }
    const chosen = this.choose(ask.file, ask.unattended);
    if (chosen.credential === "api_key") {
      this.#limits(chosen.source!, now);
    }
    const runId = id("run_");
    // The run's environment, before anything is written: a run its record refuses writes
    // nothing.
    const config = prepare({ run: runId, engine: chosen.engine, credential: chosen.credential,
                             connection: chosen.connection });
    // Written from here: nothing above refused.
    this.#prune();
    let conversationId: string;
    let session: string | null = null;
    if (conversation === null) {
      conversationId = this.#newConversation(ask, chosen.engine, now);
    } else {
      conversationId = conversation.id;
      // Another engine cannot resume this one's session: it starts a new one.
      session = conversation.source === chosen.engine ? conversation.session_id : null;
      this.#sql.exec("UPDATE conversations SET source = ?, session_id = ?, last_at = ? " +
                       "WHERE id = ?", chosen.engine, session, now, conversationId);
    }
    this.#sql.exec("UPDATE conversations SET last_run = ? WHERE id = ?", runId, conversationId);
    this.#sql.exec(
      "INSERT INTO runs (id, agent, conversation, source, credential, model, state, initiator, " +
        "client, stamp, request, unattended, created_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
      runId, ask.agent, conversationId, chosen.engine, chosen.credential, chosen.model,
      ask.initiator, ask.origin?.client ?? null,
      ask.stamp === null ? null : JSON.stringify(ask.stamp), ask.request,
      ask.unattended ? 1 : 0, now);
    const live = this.#deps.lease.live(now);
    const opened = live === null ? this.#deps.lease.open(now) : null;
    const lease = opened?.lease ?? live!;
    const scopes = ask.file.scopes.filter((scope) => held(scope));
    this.#sql.exec(
      "INSERT INTO run_grants (run, lease_id, epoch, stamp, origin, scopes, connection, " +
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      runId, lease.lease_id, lease.epoch,
      ask.stamp === null ? null : JSON.stringify(ask.stamp),
      ask.origin === null ? null : JSON.stringify(ask.origin), JSON.stringify(scopes),
      chosen.connection, now);
    return { run: runId, conversation: conversationId, existing: false, engine: chosen.engine,
             credential: chosen.credential, connection: chosen.connection, model: chosen.model,
             session_id: session, lease: { lease_id: lease.lease_id, epoch: lease.epoch },
             tokens: opened?.tokens ?? null, network: opened?.network ?? null, scopes, config };
  }

  /** A run's requests, tokens and cost so far, as egress caps it. A read. */
  usage(runId: string): { requests: number; tokens: number; cost: number } {
    return this.#sql.exec<{ requests: number; tokens: number; cost: number }>(
      "SELECT requests, tokens, cost FROM runs WHERE id = ?", runId).toArray()[0]
      ?? { requests: 0, tokens: 0, cost: 0 };
  }

  /**
   * What the initiator holds, as a test of each capability: everything for the owner's own
   * work; for a client's, what its grant, standing at its stamp's generation, holds where
   * the run acts (a full grant every capability a run can hold).
   */
  #held(ask: Ask): (capability: RunCapability) => boolean {
    if (ask.stamp === null) {
      return () => true;
    }
    const stamp = ask.stamp;
    let grant: StandingGrant;
    try {
      grant = this.#deps.standing(stamp);
    } catch (error) {
      if (error instanceof Refused) {
        throw new Refused(AUTHORITY_CHANGED);
      }
      throw error;
    }
    if (grant.mode === "full") {
      return () => true;
    }
    return (capability) => stamp.universe !== null
      && grant.holds({ universe: stamp.universe, agent: stamp.agent, capability });
  }

  /** The conversation `ask` continues: the caller's own, of this agent, with no live run. */
  #conversation(ask: Ask): { id: string; source: string; session_id: string | null } {
    const found = typeof ask.conversation === "string" && CONVERSATION_ID.test(ask.conversation)
      ? this.#sql.exec<{ id: string; agent: string; source: string; client: string | null;
                         session_id: string | null }>(
        "SELECT id, agent, source, client, session_id FROM conversations WHERE id = ?",
        ask.conversation).toArray()[0] : undefined;
    if (found === undefined || found.agent !== ask.agent
        || found.client !== (ask.origin?.client ?? null)) {
      throw new Refused(NO_CONVERSATION);
    }
    if (this.#sql.exec(`SELECT 1 FROM runs WHERE conversation = ? AND ${LIVE}`, found.id)
          .toArray().length > 0) {
      throw new Refused(CONVERSATION_BUSY);
    }
    return found;
  }

  /** A new conversation, the least recently run idle one forgotten first at the bound. */
  #newConversation(ask: Ask, engine: string, now: number): string {
    const count = this.#sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM conversations")
      .one().n;
    if (count >= CONVERSATIONS_KEPT) {
      this.#sql.exec("DELETE FROM conversations WHERE id IN (SELECT c.id FROM conversations c " +
                       "WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.conversation = c.id " +
                       `AND r.${LIVE}) ORDER BY c.last_at, c.id LIMIT ?)`,
                     count - CONVERSATIONS_KEPT + 1);
    }
    const conversation = id("cv_");
    this.#sql.exec("INSERT INTO conversations (id, agent, source, client, created_at, last_at) " +
                     "VALUES (?, ?, ?, ?, ?, ?)", conversation, ask.agent, engine,
                   ask.origin?.client ?? null, now, now);
    return conversation;
  }

  /**
   * The engine, credential, connection and model a run of `file` runs on (ADR-034 decision
   * 4.1): its named engine, on its named credential (else its record's credentials in
   * order, a key's run on the named connection, else the strongest connected one whose
   * source lists the engine); or for `auto`, the strongest connected source that lists an
   * engine. An unattended run takes only a credential its record allows unattended.
   */
  choose(file: AgentFile, unattended: boolean):
      { engine: string; credential: CredentialKind; connection: string | null;
        source: string | null; model: string | null } {
    const sources = this.#deps.sources();
    const reachable = new Set(this.#deps.egressible().map((entry) => entry.connection));
    const candidates = ranked(this.#deps.usable().filter((entry) =>
      reachable.has(entry.connection)));
    let unattendedOnly = false;
    const allows = (engine: string, credential: CredentialKind): boolean => {
      const cli = sources.get(engine)?.cli;
      if (cli === undefined || !cli.credential.includes(credential)) {
        return false;
      }
      if (unattended && cli.unattended[credential]?.allowed !== true) {
        unattendedOnly = true;
        return false;
      }
      return true;
    };
    // The connection the file names (the run's environment then refuses an engine that runs
    // no key of its provider), else the strongest whose source lists the engine.
    const keyFor = (engine: string): Candidate | undefined => candidates.find((candidate) =>
      file.connection !== null ? candidate.connection === file.connection
        : (sources.get(candidate.source)?.engines ?? []).includes(engine));
    if (file.engine !== AUTO) {
      const record = sources.get(file.engine);
      if (record?.cli === undefined || record.unwired !== undefined) {
        throw new Refused(`${NO_SOURCE} (${file.engine} is no served engine)`);
      }
      const credentials: readonly CredentialKind[] = file.credential !== null
        ? [file.credential] : file.connection !== null ? ["api_key"] : record.cli.credential;
      for (const credential of credentials) {
        if (!allows(file.engine, credential)) {
          continue;
        }
        if (credential === "vendor_login_in_sandbox") {
          return { engine: file.engine, credential, connection: null, source: null,
                   model: file.model };
        }
        const key = keyFor(file.engine);
        if (key !== undefined) {
          return { engine: file.engine, credential, connection: key.connection,
                   source: key.source, model: file.model };
        }
      }
    } else {
      for (const candidate of candidates) {
        if (file.connection !== null && candidate.connection !== file.connection) {
          continue;
        }
        const engine = (sources.get(candidate.source)?.engines ?? [])
          .find((name) => allows(name, "api_key"));
        if (engine !== undefined) {
          return { engine, credential: "api_key", connection: candidate.connection,
                   source: candidate.source, model: file.model ?? candidate.model.id };
        }
      }
    }
    throw new Refused(unattendedOnly ? UNATTENDED_REFUSED : NO_SOURCE);
  }

  /** The owner's own spending caps, for an API-key run on `source`: off unless set. */
  #limits(source: string, now: number): void {
    const caps = this.#deps.caps();
    const { day, month } = periods(now);
    const own = caps.sources[source];
    const checks: [string, number | null | undefined, string, string | null][] = [
      ["daily_cost", caps.daily_cost, `d:${day}`, null],
      ["monthly_cost", caps.monthly_cost, `m:${month}`, null],
      ["source_daily_cost", own?.daily_cost, `d:${day}`, source],
      ["source_monthly_cost", own?.monthly_cost, `m:${month}`, source],
    ];
    for (const [rule, cap, period, by] of checks) {
      if (cap !== null && cap !== undefined && this.#deps.spent(period, by) >= cap) {
        throw new Refused(`${OWNER_LIMIT}: ${rule} is spent`);
      }
    }
  }

  /** Settled runs past the newest `RUNS_KEPT`. */
  #prune(): void {
    this.#sql.exec(`DELETE FROM runs WHERE NOT ${LIVE} AND seq <= ` +
                     "(SELECT MAX(seq) FROM runs) - ?", RUNS_KEPT);
  }

  // -- A RUN'S LIFE ------------------------------------------------------------------

  /** In a unit: the sandbox took the run's start. */
  started(runId: string, now: number): void {
    this.#sql.exec("UPDATE runs SET state = 'running', started_at = ? WHERE id = ? " +
                     "AND state = 'queued'", now, runId);
  }

  /** The live run's grant, or null. A read. */
  grant(runId: string): RunGrant | null {
    const row = this.#sql.exec<GrantRow>(
      "SELECT g.* FROM run_grants g JOIN runs r ON r.id = g.run WHERE g.run = ? " +
        `AND r.${LIVE}`, runId).toArray()[0];
    return row === undefined ? null : grantOf(row);
  }

  /** The live run's grant under the live lease `leaseId`, or null: a call from that run. */
  grantUnder(runId: unknown, leaseId: string, epoch: number): RunGrant | null {
    if (typeof runId !== "string" || !RUN_ID.test(runId)) {
      return null;
    }
    const grant = this.grant(runId);
    return grant !== null && grant.lease_id === leaseId && grant.epoch === epoch ? grant : null;
  }

  /**
   * In a unit: the run settled, once: its state, why (`code`) and the runner's exit; its
   * grant revoked; its streams ended; and the lease given back when no live run holds it.
   * False for a run not live.
   */
  settle(runId: string, state: "succeeded" | "failed" | "unknown", code: string | null,
         exitCode: number | null, now: number): boolean {
    const grant = this.#sql.exec<GrantRow>("SELECT * FROM run_grants WHERE run = ?", runId)
      .toArray()[0];
    const settled = this.#sql.exec(
      "UPDATE runs SET state = ?, code = ?, exit_code = ?, settled_at = ? WHERE id = ? " +
        `AND ${LIVE} RETURNING conversation`, state, code, exitCode, now, runId).toArray();
    if (settled.length === 0) {
      return false;
    }
    this.#sql.exec("DELETE FROM run_grants WHERE run = ?", runId);
    this.#sql.exec("UPDATE conversations SET last_at = ? WHERE id = ?", now,
                   settled[0]!["conversation"] as string);
    this.#deps.ended(runId);
    if (grant !== undefined && this.#sql.exec(
      "SELECT 1 FROM run_grants WHERE lease_id = ? AND epoch = ?", grant.lease_id, grant.epoch)
      .toArray().length === 0) {
      if (this.#deps.lease.release(grant.lease_id, grant.epoch)) {
        this.#deps.leaseEnded(grant.lease_id);
      }
    }
    return true;
  }

  /**
   * In a unit: the runner's frames for a live run under the live lease, renewing it (each
   * batch is the runner's heartbeat); each new frame (`seq` past the last) read by the run's
   * record, its session id, result and error kept; the frames to relay, and the settle the
   * runner's `end` brings.
   */
  events(runId: string, lease: LiveLease, frames: readonly PostedFrame[],
         end: { readonly exit_code: number | null } | null, now: number):
      { conversation: string; frames: Frame[]; settled: RunState | null } {
    const grant = this.grantUnder(runId, lease.lease_id, lease.epoch);
    const row = grant === null ? undefined : this.#run(runId);
    if (row === undefined) {
      throw new Refused(RUN_ENDED);
    }
    this.#deps.lease.renew(lease.lease_id, lease.epoch);
    const events = this.#deps.sources().get(row.source)?.cli?.engine.events;
    if (events === undefined) {
      throw new Error(`run ${runId}'s engine ${row.source} has no record`);
    }
    let last = row.last_seq;
    let result = row.result;
    let resultCut = row.result_cut === 1;
    let error = row.error;
    let session: string | null = null;
    const relayed: Frame[] = [];
    for (const frame of frames) {
      if (frame.seq <= last) {
        continue;  // a retried batch's frames, relayed already
      }
      last = frame.seq;
      const read = readEvent(events, frame.event);
      if (read.session_id !== null) {
        session = read.session_id;
      }
      if (read.text !== null) {
        const joined = read.text.accumulate && result !== null ? result + read.text.value
          : read.text.value;
        const kept = cut(joined, RESULT_BYTES);
        result = kept.text;
        resultCut = kept.cut || (read.text.accumulate && resultCut);
      }
      if (read.error !== null && error === null) {
        error = cut(read.error, ERROR_BYTES).text;
      }
      relayed.push(Object.freeze({ v: FRAME_VERSION, run: runId, seq: frame.seq, at: frame.at,
                                   kind: read.kind, event: frame.event }));
    }
    this.#sql.exec("UPDATE runs SET last_seq = ?, result = ?, result_cut = ?, error = ?, " +
                     "state = CASE WHEN state = 'queued' THEN 'running' ELSE state END, " +
                     "started_at = coalesce(started_at, ?) WHERE id = ?",
                   last, result, resultCut ? 1 : 0, error, now, runId);
    if (session !== null) {
      this.#sql.exec("UPDATE conversations SET session_id = ? WHERE id = ?", session,
                     row.conversation);
    }
    let settled: RunState | null = null;
    if (end !== null) {
      const failed = end.exit_code !== 0 || error !== null || row.cancel_requested === 1;
      settled = failed ? "failed" : "succeeded";
      this.settle(runId, settled, row.cancel_requested === 1 ? "cancelled"
        : end.exit_code !== 0 ? "exit" : error !== null ? "engine_error" : null,
                  end.exit_code, now);
    }
    return { conversation: row.conversation, frames: relayed, settled };
  }

  /** In a unit: a live run marked cancelled; its grant and lease, for the sandbox's cancel. */
  cancel(runId: string): RunGrant | null {
    const grant = this.grant(runId);
    if (grant !== null) {
      this.#sql.exec("UPDATE runs SET cancel_requested = 1 WHERE id = ?", runId);
    }
    return grant;
  }

  /**
   * In a unit: the client's live runs ended (a grant change or revoke ends them, ADR-034
   * decision 6): each marked cancelled and its grant revoked at once, so no call of theirs
   * passes from this unit on; the ones to cancel in the sandbox.
   */
  endClient(client: string): RunGrant[] {
    const ended = this.#sql.exec<{ id: string }>(
      `SELECT id FROM runs WHERE client = ? AND ${LIVE}`, client).toArray()
      .map(({ id: run }) => this.cancel(run)).filter((grant) => grant !== null);
    for (const grant of ended) {
      this.#sql.exec("DELETE FROM run_grants WHERE run = ?", grant.run);
      this.#deps.ended(grant.run);
    }
    this.#sql.exec("DELETE FROM run_schedules WHERE client = ?", client);
    return ended;
  }

  /** Every live run's grant. A read. */
  liveGrants(): RunGrant[] {
    return this.#sql.exec<GrantRow>(
      `SELECT g.* FROM run_grants g JOIN runs r ON r.id = g.run WHERE r.${LIVE}`).toArray()
      .map(grantOf);
  }

  /** In a unit: every live run marked cancelled; the ones to cancel in the sandbox. */
  endAll(): RunGrant[] {
    return this.#sql.exec<{ id: string }>(`SELECT id FROM runs WHERE ${LIVE}`).toArray()
      .map(({ id: run }) => this.cancel(run)).filter((grant) => grant !== null);
  }

  /** The caller's run's receipt, or refused: a client sees its own runs only. A read. */
  receipt(runId: unknown, client: string | null): RunReceipt {
    const row = typeof runId === "string" && RUN_ID.test(runId) ? this.#run(runId) : undefined;
    if (row === undefined || (client !== null && row.client !== client)) {
      throw new Refused(NO_RUN);
    }
    return receipt(row);
  }

  /** The conversation's live runs, oldest first. A read. */
  liveIn(conversation: string): string[] {
    return this.#sql.exec<{ id: string }>(
      `SELECT id FROM runs WHERE conversation = ? AND ${LIVE} ORDER BY seq`, conversation)
      .toArray().map((row) => row.id);
  }

  /** Whether the conversation is the caller's own. A read. */
  owns(conversation: unknown, client: string | null): boolean {
    if (typeof conversation !== "string" || !CONVERSATION_ID.test(conversation)) {
      return false;
    }
    const row = this.#sql.exec<{ client: string | null }>(
      "SELECT client FROM conversations WHERE id = ?", conversation).toArray()[0];
    return row !== undefined && row.client === client;
  }

  /**
   * In a unit: the live runs whose lease is gone (the runner lost: no heartbeat within the
   * lease's life) settled `unknown`, never replayed.
   */
  lost(now: number): string[] {
    const lease = this.#deps.lease.live(now);
    const lost = this.#sql.exec<{ run: string }>(
      "SELECT g.run FROM run_grants g JOIN runs r ON r.id = g.run WHERE r." + LIVE +
        " AND NOT (g.lease_id = ? AND g.epoch = ?)", lease?.lease_id ?? "", lease?.epoch ?? -1)
      .toArray().map((row) => row.run);
    for (const run of lost) {
      this.settle(run, "unknown", "lost", null, now);
    }
    return lost;
  }

  // -- THE RUNS LANE: schedules ---------------------------------------------------------

  /** In a unit: a schedule of `agent`'s input: at a time, on a cron, or on the owner's mail. */
  schedule(request: { agent: string; input: string; at: number | null; cron: string | null;
                      mail: boolean; if_mail: boolean; stamp: Stamped | null;
                      origin: { client: string; family: string } | null }, now: number):
      RunSchedule {
    if (utf8Bytes(request.input) > INPUT_BYTES) {
      throw new Invalid(`a scheduled input is at most ${INPUT_BYTES} bytes`);
    }
    const kinds = [request.at !== null, request.cron !== null, request.mail]
      .filter(Boolean).length;
    if (kinds !== 1) {
      throw new Invalid("a schedule is one of at, cron or mail");
    }
    let next: number | null;
    let trigger: RunSchedule["trigger"];
    if (request.cron !== null) {
      next = nextOccurrence(parseCron(request.cron), now);
      trigger = "cron";
    } else if (request.at !== null) {
      if (!Number.isFinite(request.at) || request.at <= now) {
        throw new Invalid("at is a time still ahead, in seconds since the epoch");
      }
      next = request.at;
      trigger = "at";
    } else {
      next = null;
      trigger = "mail";
    }
    const scheduleId = id("sch_");
    this.#sql.exec(
      "INSERT INTO run_schedules (id, agent, input, trigger, cron, if_mail, next_at, client, " +
        "stamp, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      scheduleId, request.agent, request.input, trigger, request.cron,
      request.if_mail ? 1 : 0, next, request.origin?.client ?? null,
      request.stamp === null ? null : JSON.stringify(request.stamp),
      request.origin === null ? null : JSON.stringify(request.origin), now);
    return this.scheduleView(scheduleId, request.origin?.client ?? null);
  }

  /** The caller's schedule, or refused. A read. */
  scheduleView(scheduleId: unknown, client: string | null): RunSchedule {
    const row = typeof scheduleId === "string" && SCHEDULE_ID.test(scheduleId)
      ? this.#sql.exec<ScheduleRow>("SELECT * FROM run_schedules WHERE id = ?", scheduleId)
        .toArray()[0] : undefined;
    if (row === undefined || (client !== null && row.client !== client)) {
      throw new Refused(NO_SCHEDULE);
    }
    return scheduleOf(row);
  }

  /** In a unit: the caller's schedule removed. */
  unschedule(scheduleId: unknown, client: string | null): { schedule_id: string } {
    const found = this.scheduleView(scheduleId, client);
    this.#sql.exec("DELETE FROM run_schedules WHERE id = ?", found.schedule_id);
    return { schedule_id: found.schedule_id };
  }

  /**
   * In a unit, the doorbell on change: mail reached the owner, so each run-on-mail schedule
   * is due now (one already due stays as it is).
   */
  mailArrived(now: number): void {
    this.#sql.exec("UPDATE run_schedules SET next_at = ? WHERE trigger = 'mail' " +
                     "AND next_at IS NULL", now);
  }

  /**
   * When the lane next has work: the earliest due schedule, only while the owner is
   * admitted (`require_work`: a suspended owner's schedules wait), and while runs are live
   * the lease's expiry (a lost runner is settled then). Null for none. A read.
   */
  next(now: number, admitted: boolean): number | null {
    const due = !admitted ? null : this.#sql.exec<{ at: number | null }>(
      "SELECT MIN(next_at) AS at FROM run_schedules WHERE next_at IS NOT NULL").one().at;
    const times = due === null ? [] : [Math.max(now, due)];
    if (this.live() > 0) {
      const lease = this.#deps.lease.live(now);
      times.push(lease === null ? now : Math.max(now, lease.expires_at));
    }
    return times.length === 0 ? null : Math.min(...times);
  }

  /**
   * In a unit: the next due schedule claimed: its next time moved on (a cron's next
   * occurrence; an `at` and a mail run's cleared), so it fires once; and whether it has work
   * (a run on mail, or one that asks for mail, only with the owner's mail unacked). Null
   * when none is due.
   */
  claim(now: number, unacked: () => number):
      { schedule: ScheduleRow; work: boolean } | null {
    const row = this.#sql.exec<ScheduleRow>(
      "SELECT * FROM run_schedules WHERE next_at IS NOT NULL AND next_at <= ? " +
        "ORDER BY next_at, id LIMIT 1", now).toArray()[0];
    if (row === undefined) {
      return null;
    }
    if (row.trigger === "at") {
      this.#sql.exec("DELETE FROM run_schedules WHERE id = ?", row.id);
    } else {
      const next = row.trigger === "cron" ? nextOccurrence(parseCron(row.cron), now) : null;
      this.#sql.exec("UPDATE run_schedules SET next_at = ? WHERE id = ?", next, row.id);
    }
    const work = !(row.trigger === "mail" || row.if_mail === 1) || unacked() > 0;
    return { schedule: row, work };
  }

  /** In a unit: what came of a schedule's run: the run, or why it was refused. */
  ran(scheduleId: string, run: string | null, error: string | null): void {
    this.#sql.exec("UPDATE run_schedules SET last_run = coalesce(?, last_run), last_error = ? " +
                     "WHERE id = ?", run, error === null ? null : error.slice(0, 256), scheduleId);
  }
}

/** A schedule row's stamp and origin, as the lane admits its run. */
export function scheduleAuthority(row: ScheduleRow): {
  stamp: Stamped | null; origin: { client: string; family: string } | null } {
  return { stamp: row.stamp === null ? null : JSON.parse(row.stamp) as Stamped,
           origin: row.origin === null ? null : JSON.parse(row.origin) as {
             client: string; family: string } };
}

export type { ScheduleRow };
export { RUN_CAPABILITIES };
