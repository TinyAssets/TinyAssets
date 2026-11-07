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
 * 5. a conversation continued is the caller's own and of this agent (`NO_CONVERSATION`).
 *    One engine session runs one invocation at a time (a technical ceiling), so an input to
 *    a conversation whose run is live, or that has inputs waiting, is written `waiting`
 *    with its input (`run_inputs`; `QUEUE_FULL` past `QUEUE_ROWS` or `QUEUE_BYTES`), and
 *    the lane starts it, in order, once the run before it settles (`nextWaiting`);
 * 6. the tier's simultaneous runs hold (`TIER_RUNS`), from the numbers the binder handed
 *    and the owner's own tier (`tier.ts`);
 * 7. the source is chosen (`choose`): `agent.json`'s engine, or `auto`'s strongest connected
 *    source that has an engine (Direction 4): the owner's connections and its signed-in
 *    engines (`engine_signins`, each as the runner reports its record's `status_argv`)
 *    ranked together, a subscription preferred to a key of the provider it covers, and a
 *    lapsed sign-in refused (`SIGNIN_LAPSED`), never a silent fall back to another source;
 *    for an unattended run (a schedule's, a mail's) only a credential its record allows
 *    unattended (ADR-033 decision 3, `UNATTENDED_REFUSED`);
 * 8. the owner's own limits hold (`OWNER_LIMIT`): an API-key run's source and the owner's
 *    day and month spend under their caps (the policy's, off by default). A signed-in run
 *    spends on the vendor's plan, which TinyAssets never sees; the vendor's limits hold.
 * Then, in the same unit: the conversation made or touched, the run written `queued`, its
 * authority's sandbox lease opened, or joined while one is live (one sandbox an authority:
 * the owner's own work shares the owner's, and each outside client's runs share that
 * client's own, so runs of different authority never share a VM), and the run grant
 * minted: the stamp and origin, the scopes (the agent's, or for a client's run their
 * intersection with what that client's grant holds where it acts), the run's own
 * connection (none on the vendor's own sign-in) and the SHA-256 of the run's own 256-bit
 * secret, which only its CLI's environment holds (its URLs carry it). Every call of a run's
 * (its MCP door, its model calls) presents that secret, so a run id alone authorizes
 * nothing, and the egress door holds each call to its own run's connection (`egress.ts`).
 * The runner's own doors (its events, the sign-in report) take the runner's secret, which
 * no CLI holds.
 *
 * **Settle** (`settle`), once, in a unit: `succeeded` from the runner's end with exit 0 and
 * no error event, `failed` otherwise (a cancel's is `cancelled`; a revoked or changed
 * client grant's `revoked`, at once), `unknown` when the runner was lost (its lease lapsed,
 * its own heartbeat lapsed, or a start whose answer never came). Unknown is never
 * replayed. The run grant goes with it (a cancel revokes it at once), and the lease when no
 * live run holds it any more; every settle prunes the receipts to `RUNS_KEPT`.
 * There is no platform deadline: a run's lease's tokens live while its heartbeat renews it
 * (`LEASE_LIVED`), so its only time bound is the sandbox's own, the vendor's.
 *
 * **Mail** (slice ID): mail to one of the owner's own agents starts that agent's run, through
 * the same admission, its input the mail, its conversation the mail thread's (one a thread,
 * agent and authority, its id that triple's digest: `mailConversation`), its authority the
 * mail's, attended only when a person sent it, and its result the agent's reply in that
 * thread when a person or client sent it (`RunDeps.answer`, before the settle). Such a run
 * never refuses for the tier: past the tier's simultaneous runs it waits, as an input to a
 * busy conversation does. Its mail stays held until it settles (`RunDeps.mailSettled`).
 *
 * **The `runs` lane** (`lane`): a schedule's due time, a waiting run's turn, mail no run has
 * taken, or a live run's lease expiry while runs are live, never a poll: with none the lane
 * has no job.
 */
import { CONVERSATION_ID, randomHex, RUN_ID, SCHEDULE_ID } from "../../contract/src/ids";
import { sha256Hex } from "../../contract/src/digest";

import type { CredentialKind, Sources } from "../../sources/src/schema";
import {
  type EngineEvents, type Frame, FRAME_VERSION, readEvent,
} from "../../contract/src/run-events";

import { AGENT_DISABLED, type AgentFile, AUTO } from "./agent-file";
import type { RunCapability } from "../../contract/src/capabilities";
import { AUTHORITY_CHANGED, type StandingGrant, type Stamped } from "./authority";
import {
  CONVERSATIONS_KEPT, ERROR_BYTES, INPUT_BYTES, LEASE_TTL_S, MAX_LIST, QUEUE_BYTES, QUEUE_ROWS,
  RESULT_BYTES,
} from "../../contract/src/limits";
import { OWNER_AUTHORITY } from "./files";
import { type Candidate, ranked } from "./auto";
import { nextOccurrence, parseCron } from "./cron";
import { Invalid, Refused } from "../../contract/src/errors";
import type { OwnerStorage } from "./gate";
import { periods } from "./money";
import type { NetworkPlan } from "./network";
import { type RunConfig, SANDBOX_ABSENT, SANDBOX_WAITING } from "./sandbox";
import type { RunMeter } from "./sandbox-time";
import { SIGNIN_OFF } from "./signins";
import { TIER_RUNS, type TierName, type Tiers } from "./tier";
import { utf8Bytes } from "../../contract/src/text";

/**
 * Settled runs kept as receipts, the newest first: an older run's status is gone, never
 * its effects (its transcript is the engine's own file in the owner's files). A technical
 * ceiling of the owner's database (`limits-audit.md`).
 */
export const RUNS_KEPT = 2000;

export const NO_CONVERSATION = "no_conversation: no such conversation of the caller's";
export const CONVERSATION_BUSY = "conversation_busy: a run in this conversation is still live";
/** The inputs waiting for their conversations are at their ceiling (`QUEUE_ROWS`, `QUEUE_BYTES`). */
export const QUEUE_FULL = `run_inputs_full: ${QUEUE_ROWS} inputs or ${QUEUE_BYTES} bytes wait ` +
  "already; let a run settle first";
/** An engine's sign-in lapsed: `auto` no longer picks it, and never falls back silently. */
export const SIGNIN_LAPSED = "signin_lapsed: sign in to this engine again";
/**
 * The sandbox's answer to a start on an engine's own sign-in where none is signed in inside
 * it (its record's `status_argv`, slice S): the run settles `failed` with it, and the owner
 * is told to sign in inside that sandbox. A login lives in its sandbox and is never copied
 * to another (ADR-031 decision 2).
 */
export const SIGNIN_REQUIRED = "signin_required";
export const UNATTENDED_REFUSED = "unattended_refused: no source of this agent may run " +
  "unattended on its record";
export const NO_SOURCE = "no_source: no connected source can run this agent's engine";
export const OWNER_LIMIT = "owner_limit";
export const NO_RUN = "no_run: no such run of the caller's";
export const RUN_ENDED = "run_ended: the run is no longer live";
export const NO_SCHEDULE = "no_schedule: no such schedule of the caller's";
/** A scheduled run's whole answer when it has nothing to tell its owner: nothing is posted. */
export const NO_REPLY = "NO_REPLY";

export type RunState = "waiting" | "queued" | "running" | "succeeded" | "failed" | "unknown";
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

/** A sandbox's live lease, as the runs read it (`Files.sandboxLease`). */
export interface LiveLease {
  readonly lease_id: string;
  readonly epoch: number;
  readonly expires_at: number;
  /** The engines its own runner reported signed in inside its sandbox. */
  readonly signins?: readonly string[];
}

/** A live run, and the lease it runs under: what a sandbox cancel names. */
export type LiveRun = {
  readonly run: string;
  readonly lease_id: string;
  readonly epoch: number;
};

/** A lease the admission opened: its two tokens, for the start that configures the sandbox. */
export interface OpenedTokens {
  readonly egress_token: string;
  readonly sync_token: string;
  /** The runner's own secret, for its events and sign-in reports: never a CLI's. */
  readonly runner_token: string;
}

export interface RunDeps {
  /** The owner's `require_work` refusal, or null while it is admitted. */
  readonly unadmitted: () => string | null;
  readonly sandboxBound: () => boolean;
  readonly sources: () => Sources;
  /**
   * Whether `engine`'s vendor sign-in is switched on (the control record, as the binder last
   * handed it): a run never uses a switched-off source's vendor login.
   */
  readonly signinOn: (engine: string) => boolean;
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
    /** `authority`'s live lease (the owner's own work's, or one outside client's). */
    live(now: number, authority: string): LiveLease | null;
    /** Every live lease. */
    all(now: number): readonly LiveLease[];
    /** In the unit: `authority`'s new lease, its tokens minted; refused while one is held. */
    open(now: number, authority: string): { readonly lease: LiveLease;
                                            readonly tokens: OpenedTokens;
                                            readonly network: NetworkPlan };
    release(leaseId: string, epoch: number): boolean;
    renew(leaseId: string, epoch: number): void;
    /** In the unit: every lapsed lease's row gone, once its runs were settled. */
    lapsed(now: number): void;
  };
  /** In the unit, once a run settled: its streams ended (`egress.ts`). */
  readonly ended: (run: string) => void;
  /**
   * In the unit, before a mail's run that succeeded with a result settles: its agent's
   * reply to that mail (`ledger.ts` decides whether the mail is one a result answers).
   */
  readonly answer: (answer: MailAnswer) => void;
  /** In the unit, once a mail's run settled, however: its mail no longer held for it. */
  readonly mailSettled: (mail: string, agent: string) => void;
  /**
   * In the unit: what a run's sandbox said the owner must hear (its sync's files whose names
   * are not UTF-8, `ta.sync_unnamed`), as the owner's notice.
   */
  readonly notice?: (type: string, detail: string) => void;
  /** In the unit, once the lease was given back: every stream under it ended. */
  readonly leaseEnded: (leaseId: string) => void;
  /** In the unit: a run settled lost or revoked, whose CLI the sandbox stops, once over. */
  readonly stop: (runs: readonly LiveRun[]) => void;
  /**
   * The owner's tier's simultaneous runs, as the numbers last handed say, or null when none
   * were: whether a waiting run may start (the lane's).
   */
  readonly capacity: () => number | null;
}

/** An engine's sign-in, as the runner last reported it: presence only, never a credential. */
export interface SignIn {
  readonly engine: string;
  readonly signed_in: boolean;
  /** The models the engine offers on the sign-in, as the runner read them from the CLI. */
  readonly models: readonly string[];
  readonly checked_at: number;
  readonly changed_at: number;
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
  /** The input: kept only while the run waits for its conversation's live run. */
  readonly prompt: string;
  /** The mail it answers, and its thread's conversation id, when mail started it; else null. */
  readonly mail: { readonly message: string; readonly conversation: string } | null;
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
  /** Whose sandbox it runs in. */
  readonly authority: string;
  /** The run's own secret: its URLs carry it, and no other run's CLI holds it. */
  readonly secret: string;
  readonly tokens: OpenedTokens | null;
  /** The owner's network, with the egress token in its transform, when this run opened it. */
  readonly network: NetworkPlan | null;
  readonly scopes: readonly RunCapability[];
  /** The run's whole environment, made before anything was written. */
  readonly config: RunConfig;
  /** The input it starts on (a waiting run's, as it was kept). */
  readonly prompt: string;
}

/** The source a run was given, its id and secret: what its environment is made from. */
export interface Chosen {
  readonly run: string;
  readonly engine: string;
  readonly credential: CredentialKind;
  readonly connection: string | null;
  readonly secret: string;
}

/**
 * A request admitted before (its run, nothing new written), or a run that waits for its
 * conversation's live run (written `waiting`, its input kept): nothing to start now.
 */
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
  /** The mail it answers, when mail started it: the owner reads its chain (`mailChain`). */
  readonly mail: string | null;
  /**
   * How long its sandbox ran between its start and its settle, in seconds, from the
   * sandbox Worker's own log (`sandbox-time.ts`); null until that log is read past its
   * settle. The founder's cost view: it limits nothing.
   */
  readonly sandbox_s: number | null;
}

/** A run grant, as the run-scoped door and egress read it. */
export interface RunGrant {
  readonly run: string;
  /** Its agent: who the run acts as in the ledger. */
  readonly agent: string;
  /** The lease its run runs under (the run's row's). */
  readonly lease_id: string;
  readonly epoch: number;
  readonly stamp: Stamped | null;
  readonly origin: { readonly client: string; readonly family: string } | null;
  readonly scopes: readonly RunCapability[];
  readonly connection: string | null;
}

/**
 * A run's result posted for it: a mail's run's, its agent's reply to that mail; a scheduled
 * run's of the owner's own schedule (`mail` null), its agent's post in the owner's room with
 * it, `dm:<its id>`, since no
 * one is waiting on a scheduled run. Under the run's authority either way.
 */
export interface MailAnswer {
  readonly run: string;
  readonly agent: string;
  readonly mail: string | null;
  readonly result: string;
  readonly stamp: Stamped | null;
  readonly origin: { readonly client: string; readonly family: string } | null;
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
  readonly trigger: "at" | "cron";
  readonly cron: string | null;
  readonly next_at: number | null;
  readonly last_run: string | null;
  readonly last_error: string | null;
}

type RunRow = {
  id: string; agent: string; conversation: string; source: string; credential: CredentialKind;
  model: string | null; state: RunState; initiator: Initiator; client: string | null;
  lease_id: string | null; epoch: number | null; beat_at: number | null;
  stamp: string | null; request: string | null; unattended: number; cancel_requested: number;
  mail: string | null;
  requests: number; tokens: number; cost: number; last_seq: number; result: string | null;
  result_cut: number; error: string | null; code: string | null; exit_code: number | null;
  created_at: number; started_at: number | null; settled_at: number | null;
  sandbox_ms: number | null;
};

type GrantRow = {
  run: string; agent: string; lease_id: string; epoch: number; stamp: string | null;
  origin: string | null; scopes: string; connection: string | null; secret: string;
};

type ScheduleRow = {
  id: string; agent: string; input: string; trigger: RunSchedule["trigger"]; cron: string | null;
  next_at: number | null; client: string | null; stamp: string | null;
  origin: string | null; last_run: string | null; last_error: string | null;
};

const LIVE = "state IN ('queued', 'running')";
/** A live run's grant row, with its agent and the lease its run runs under. */
const GRANTS = "SELECT g.*, r.agent, r.lease_id, r.epoch FROM run_grants g " +
  "JOIN runs r ON r.id = g.run";
/** A run not settled: live, or waiting for its conversation. */
const UNSETTLED = "settled_at IS NULL";
/** The id a subscription takes among the connections it is ranked with. */
const PLAN = "plan:";

/**
 * The provider an engine's plan covers: its record's one API-key provider (Claude Code's
 * `anthropic`), else the engine itself. Data only: no provider is named here.
 */
function providerOf(apiKeyEnv: Readonly<Record<string, unknown>> | undefined, engine: string):
    string {
  const providers = Object.keys(apiKeyEnv ?? {});
  return providers.length === 1 ? providers[0]! : engine;
}

/** A model a plan offers, as the ranking reads it: its id; no catalog price. */
function planModel(model: string): Candidate["model"] {
  return { id: model, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } } as
    Candidate["model"];
}

function id(prefix: string): string {
  return `${prefix}${randomHex(16)}`;
}

/** A secret's SHA-256 hex: all the object keeps of one. */
/** Whose sandbox a run of `ask` runs in: the client it came through, else the owner's. */
function authorityOf(origin: { readonly client: string } | null): string {
  return origin?.client ?? OWNER_AUTHORITY;
}

/**
 * A session id the conversations keep: 1 to 256 printable ASCII characters, no space (the
 * `conversations.session_id` CHECK). The VM is untrusted, so any other the engine names is
 * refused here, never handed to SQLite to fail the runner's whole post.
 */
export const SESSION_ID = /^[!-~]{1,256}$/;
/** The run's error when its engine named a session id outside that shape: a fixed code. */
export const SESSION_REFUSED = "session_id_refused: the engine named a session id outside " +
  "its shape, so this conversation does not resume";

/**
 * A runner's batch read against its run's record: the frames past the last one kept (a
 * retried batch's are relayed already), the result and the error they leave, the engine's
 * session id, and the frames to relay. Pure; `events` writes what it reads, and `answering`
 * reads it ahead of the batch's unit.
 */
function batch(row: RunRow, events: EngineEvents, frames: readonly PostedFrame[]): {
  last: number; result: string | null; resultCut: boolean; error: string | null;
  session: string | null; relayed: Frame[];
} {
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
    if (read.session_id !== null && SESSION_ID.test(read.session_id)) {
      session = read.session_id;
    } else if (read.session_id !== null && error === null) {
      error = SESSION_REFUSED;  // its frames still relayed; the id is never kept
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
    relayed.push(Object.freeze({ v: FRAME_VERSION, run: row.id, seq: frame.seq, at: frame.at,
                                 kind: read.kind, event: frame.event }));
  }
  return { last, result, resultCut, error, session, relayed };
}

/** A path as the sync agent escapes one it cannot name: `/workspace/`, then printable ASCII. */
const UNNAMED_PATH = /^\/workspace\/[\x20-\x7e]{1,4096}$/;

/** The most of a sync's unnamed paths a notice names. */
const UNNAMED_SHOWN = 5;

/**
 * The owner's notice for a sync's files whose names are not UTF-8 (`ta.sync_unnamed`), in
 * the object's own words: only the count and the escaped paths come from the event, never
 * its text (round 8, R8-1); null for any other event, or one not of that shape.
 */
export function unnamedNotice(event: unknown): string | null {
  if (typeof event !== "object" || event === null
      || (event as Record<string, unknown>)["type"] !== "ta.sync_unnamed") {
    return null;
  }
  const { count, paths } = event as { count?: unknown; paths?: unknown };
  if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 1) {
    return null;
  }
  const named = (Array.isArray(paths) ? paths : [])
    .filter((path): path is string => typeof path === "string" && UNNAMED_PATH.test(path))
    .slice(0, Math.min(UNNAMED_SHOWN, count));
  const one = count === 1;
  return `${count} file${one ? "" : "s"} or folder${one ? "" : "s"} in its sandbox ${
    one ? "has a name" : "have names"} that ${one ? "is" : "are"} not UTF-8, so ${
    one ? "it does" : "they do"} not sync to your files${named.length === 0 ? ""
    : `: ${named.map((path) => `"${path}"`).join(", ")}${count > named.length
      ? `, and ${count - named.length} more` : ""}`}. Rename ${one ? "it" : "them"} to sync ${
    one ? "it" : "them"}.`;
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

/** The runs' side of the sandbox meter (`sandbox-time.ts`), over the object's storage. */
export function runMeter(sql: OwnerStorage["sql"]): RunMeter {
  return {
    unmetered: () => sql.exec("SELECT 1 FROM runs WHERE settled_at IS NOT NULL AND " +
                                "sandbox_ms IS NULL LIMIT 1").toArray().length > 0,
    settledBy: (readAt) => sql.exec<{ id: string; client: string | null;
                                      started_at: number | null; settled_at: number }>(
      "SELECT id, client, started_at, settled_at FROM runs WHERE settled_at IS NOT NULL " +
        "AND sandbox_ms IS NULL AND settled_at * 1000 <= ?", readAt).toArray()
      .map((run) => ({ id: run.id, authority: authorityOf(run.client === null ? null
                                                            : { client: run.client }),
                       started_at: run.started_at, settled_at: run.settled_at })),
    metered: (id, ms) => {
      sql.exec("UPDATE runs SET sandbox_ms = ? WHERE id = ? AND sandbox_ms IS NULL", ms, id);
    },
    oldestUnmetered: () => sql.exec<{ at: number | null }>(
      "SELECT MIN(started_at) AS at FROM runs WHERE sandbox_ms IS NULL AND " +
        "started_at IS NOT NULL").one().at,
  };
}

function receipt(row: RunRow): RunReceipt {
  return {
    run_id: row.id, conversation_id: row.conversation, agent: row.agent, engine: row.source,
    credential: row.credential, model: row.model, state: row.state, initiator: row.initiator,
    code: row.code, result: row.result, result_cut: row.result_cut === 1, error: row.error,
    exit_code: row.exit_code, requests: row.requests, tokens: row.tokens, cost: row.cost,
    created_at: row.created_at, started_at: row.started_at, settled_at: row.settled_at,
    mail: row.mail, sandbox_s: row.sandbox_ms === null ? null : row.sandbox_ms / 1000,
  };
}

function grantOf(row: GrantRow): RunGrant {
  return {
    run: row.run, agent: row.agent, lease_id: row.lease_id, epoch: row.epoch,
    stamp: row.stamp === null ? null : JSON.parse(row.stamp) as Stamped,
    origin: row.origin === null ? null : JSON.parse(row.origin) as RunGrant["origin"],
    scopes: JSON.parse(row.scopes) as RunCapability[], connection: row.connection,
  };
}

function scheduleOf(row: ScheduleRow): RunSchedule {
  return { schedule_id: row.id, agent: row.agent, trigger: row.trigger, cron: row.cron,
           next_at: row.next_at, last_run: row.last_run, last_error: row.last_error };
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

  /** The agents of the runs under the lease taken at `epoch`, at most `most`, by name. A read. */
  agentsAt(epoch: number, most: number): string[] {
    return this.#sql.exec<{ agent: string }>(
      "SELECT DISTINCT agent FROM runs WHERE epoch = ? ORDER BY agent LIMIT ?", epoch, most)
      .toArray().map((row) => row.agent);
  }

  #run(runId: string): RunRow | undefined {
    return this.#sql.exec<RunRow>("SELECT * FROM runs WHERE id = ?", runId).toArray()[0];
  }

  /** How many runs are live now. A read. */
  live(): number {
    return this.#sql.exec<{ n: number }>(`SELECT COUNT(*) AS n FROM runs WHERE ${LIVE}`).one().n;
  }

  // -- ADMISSION ---------------------------------------------------------------------

  /**
   * In a unit: the run admitted, written, its lease opened or joined and its grant minted;
   * or, for a conversation whose run is live (or that has runs waiting already), written
   * `waiting` with its input, to start in order as the one before it settles. `waiting`
   * names a waiting run the lane starts now: it is admitted whole again.
   */
  admit(ask: Ask, now: number, prepare: (chosen: Chosen) => RunConfig,
        waiting: string | null = null): Admitted | Retried {
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
    if (waiting === null && ask.request !== null) {
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
    const conversation = ask.conversation === null ? null : this.#conversation(ask, waiting);
    if (conversation?.busy === true) {
      if (waiting !== null) {
        throw new Refused(CONVERSATION_BUSY);  // not its turn yet: it keeps waiting
      }
      return this.#wait(ask, conversation.id, now);
    }
    const tier = this.#deps.tier();
    if (this.live() >= ask.tiers[tier].runs) {
      if (ask.mail !== null && waiting === null) {
        // Mail never refuses for the tier: its run waits its turn, in its thread's
        // conversation (made now when the thread has none), as a busy conversation's input.
        const chosen = conversation === null
          ? this.choose(ask.file, ask.unattended, authorityOf(ask.origin), now) : null;
        return this.#wait(ask, conversation?.id
          ?? this.#newConversation(ask, chosen!.engine, now), now);
      }
      throw new Refused(`${TIER_RUNS} (${tier}: ${ask.tiers[tier].runs})`);
    }
    const chosen = this.choose(ask.file, ask.unattended, authorityOf(ask.origin), now);
    if (chosen.credential === "api_key") {
      this.#limits(chosen.source!, now);
    }
    const runId = waiting ?? id("run_");
    const secret = randomHex(32);
    // The run's environment, before anything is written: a run its record refuses writes
    // nothing.
    const config = prepare({ run: runId, engine: chosen.engine, credential: chosen.credential,
                             connection: chosen.connection, secret });
    // Written from here: nothing above refused.
    const authority = authorityOf(ask.origin);
    const live = this.#deps.lease.live(now, authority);
    const opened = live === null ? this.#deps.lease.open(now, authority) : null;
    const lease = opened?.lease ?? live!;
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
    let prompt = ask.prompt;
    if (waiting !== null) {
      // Its turn: its source as chosen now, its kept input taken.
      this.#sql.exec("UPDATE runs SET state = 'queued', source = ?, credential = ?, model = ?, " +
                       "lease_id = ?, epoch = ?, beat_at = ?, sandbox_wait = 0, code = NULL " +
                       "WHERE id = ? AND state = 'waiting'",
                     chosen.engine, chosen.credential, chosen.model, lease.lease_id, lease.epoch,
                     now, waiting);
      const kept = this.#sql.exec<{ prompt: string }>(
        "DELETE FROM run_inputs WHERE run = ? RETURNING prompt", waiting).one().prompt;
      // A mail's run kept its mail's reference: its input is the mail as read again now.
      prompt = ask.mail === null ? kept : ask.prompt;
    } else {
      this.#sql.exec(
        "INSERT INTO runs (id, agent, conversation, source, credential, model, state, " +
          "initiator, client, stamp, request, unattended, mail, created_at, lease_id, epoch, " +
          "beat_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        runId, ask.agent, conversationId, chosen.engine, chosen.credential, chosen.model,
        ask.initiator, ask.origin?.client ?? null,
        ask.stamp === null ? null : JSON.stringify(ask.stamp), ask.request,
        ask.unattended ? 1 : 0, ask.mail?.message ?? null, now, lease.lease_id, lease.epoch,
        now);
    }
    const scopes = ask.file.scopes.filter((scope) => held(scope));
    this.#sql.exec(
      "INSERT INTO run_grants (run, secret, stamp, origin, scopes, connection, created_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
      runId, sha256Hex(secret), ask.stamp === null ? null : JSON.stringify(ask.stamp),
      ask.origin === null ? null : JSON.stringify(ask.origin), JSON.stringify(scopes),
      chosen.connection, now);
    this.#prune();
    return { run: runId, conversation: conversationId, existing: false, engine: chosen.engine,
             credential: chosen.credential, connection: chosen.connection, model: chosen.model,
             session_id: session, lease: { lease_id: lease.lease_id, epoch: lease.epoch },
             authority, secret, tokens: opened?.tokens ?? null,
             network: opened?.network ?? null, scopes, config, prompt };
  }

  /**
   * In a unit: a run of `ask` written `waiting` in its conversation, behind the live run and
   * any that wait already, its input kept (`run_inputs`, refused past `QUEUE_ROWS` or
   * `QUEUE_BYTES`). Its source is chosen now, so a run no source can serve is refused at
   * once, and chosen again when its turn comes.
   */
  #wait(ask: Ask, conversation: string, now: number): Retried {
    const chosen = this.choose(ask.file, ask.unattended, authorityOf(ask.origin), now);
    const queued = this.#sql.exec<{ n: number; bytes: number }>(
      "SELECT COUNT(*) AS n, coalesce(SUM(bytes), 0) AS bytes FROM run_inputs").one();
    if (queued.n >= QUEUE_ROWS
        || queued.bytes + (ask.mail === null ? utf8Bytes(ask.prompt) : 0) > QUEUE_BYTES) {
      throw new Refused(QUEUE_FULL);
    }
    const runId = id("run_");
    this.#sql.exec(
      "INSERT INTO runs (id, agent, conversation, source, credential, model, state, initiator, " +
        "client, stamp, request, unattended, mail, created_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?, ?, ?)",
      runId, ask.agent, conversation, chosen.engine, chosen.credential, chosen.model,
      ask.initiator, ask.origin?.client ?? null,
      ask.stamp === null ? null : JSON.stringify(ask.stamp), ask.request,
      ask.unattended ? 1 : 0, ask.mail?.message ?? null, now);
    // A mail's run keeps only its mail's reference (its `mail`): its input is read again
    // from the mail, held until the run settles, as it starts (ADR-035).
    const kept = ask.mail === null ? ask.prompt : "";
    this.#sql.exec("INSERT INTO run_inputs (run, prompt, bytes, origin) VALUES (?, ?, ?, ?)",
                   runId, kept, utf8Bytes(kept),
                   ask.origin === null ? null : JSON.stringify(ask.origin));
    this.#sql.exec("UPDATE conversations SET last_at = ? WHERE id = ?", now, conversation);
    this.#prune();
    return { existing: true, run: runId, conversation };
  }

  /**
   * The waiting run whose turn it is, oldest first: the first waiting in a conversation with
   * no live run, while the tier has room, and not one waiting for a sandbox until the
   * platform's line rings the owner (`sandbox_wait`). A read; null for none.
   */
  nextWaiting(): RunRow | null {
    const room = this.#deps.capacity();
    if (room === null || this.live() >= room) {
      return null;
    }
    return this.#sql.exec<RunRow>(
      "SELECT w.* FROM runs w WHERE w.state = 'waiting' AND NOT EXISTS (SELECT 1 FROM runs r " +
        `WHERE r.conversation = w.conversation AND r.${LIVE}) AND NOT EXISTS (SELECT 1 FROM ` +
        "runs e WHERE e.conversation = w.conversation AND e.state = 'waiting' " +
        "AND e.seq < w.seq) AND w.sandbox_wait = 0 ORDER BY w.seq LIMIT 1").toArray()[0]
      ?? null;
  }

  /**
   * In a unit: a run the sandbox had no room to start (`SANDBOX_WAITING`: E2B runs as many
   * sandboxes as its plan allows) put back to `waiting`, never refused: its input kept
   * again, its grant revoked and its lease given back as a settle would (it never ran), and
   * its start tried again once the platform's line rings the owner (`sandboxRung`),
   * admitted whole. The app reads it as
   * waiting to start (`code` `sandbox_waiting`). False when it was no longer queued.
   */
  waitForSandbox(runId: string, prompt: string): boolean {
    const row = this.#sql.exec<{ conversation: string; lease_id: string | null;
                                 epoch: number | null; client: string | null }>(
      "SELECT conversation, lease_id, epoch, client FROM runs WHERE id = ? AND state = 'queued'",
      runId).toArray()[0];
    if (row === undefined) {
      return false;
    }
    const origin = this.#sql.exec<{ origin: string | null }>(
      "SELECT origin FROM run_grants WHERE run = ?", runId).toArray()[0]?.origin ?? null;
    this.#sql.exec("UPDATE runs SET state = 'waiting', lease_id = NULL, epoch = NULL, " +
                     "beat_at = NULL, sandbox_wait = 1, code = ? WHERE id = ?",
                   SANDBOX_WAITING, runId);
    this.#sql.exec("INSERT INTO run_inputs (run, prompt, bytes, origin) VALUES (?, ?, ?, ?)",
                   runId, prompt, utf8Bytes(prompt), origin);
    this.#sql.exec("DELETE FROM run_grants WHERE run = ?", runId);
    if (row.lease_id !== null && this.#sql.exec(
      `SELECT 1 FROM runs WHERE ${LIVE} AND lease_id = ? AND epoch = ?`, row.lease_id,
      row.epoch).toArray().length === 0) {
      if (this.#deps.lease.release(row.lease_id, row.epoch!)) {
        this.#deps.leaseEnded(row.lease_id);
      }
    }
    return true;
  }

  /** How many runs wait for a sandbox now. A read. */
  waitingForSandbox(): number {
    return this.#sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM runs WHERE sandbox_wait = 1").one().n;
  }

  /**
   * In a unit: the platform's line rang the owner (a sandbox freed): every run waiting for
   * a sandbox may start again, oldest first, through its whole admission. How many there
   * were: none, and the owner leaves the line.
   */
  sandboxRung(): number {
    return this.#sql.exec("UPDATE runs SET sandbox_wait = 0 WHERE sandbox_wait = 1 " +
                            "RETURNING id").toArray().length;
  }


  /**
   * A waiting run's admission as it was asked, its input kept: the lane admits it again
   * whole (`admit` with its id) when its turn comes. Null when it no longer waits. A read.
   */
  waitingAsk(runId: string): Omit<Ask, "file" | "tiers"> | null {
    const row = this.#sql.exec<RunRow & { prompt: string; origin: string | null }>(
      "SELECT r.*, i.prompt, i.origin FROM runs r JOIN run_inputs i ON i.run = r.id " +
        "WHERE r.id = ? AND r.state = 'waiting'", runId).toArray()[0];
    if (row === undefined) {
      return null;
    }
    return { initiator: row.initiator, agent: row.agent, conversation: row.conversation,
             request: row.request, unattended: row.unattended === 1, prompt: row.prompt,
             stamp: row.stamp === null ? null : JSON.parse(row.stamp) as Stamped,
             origin: row.origin === null ? null : JSON.parse(row.origin) as Ask["origin"],
             mail: row.mail === null ? null
               : { message: row.mail, conversation: row.conversation } };
  }

  /**
   * Whether the conversation `id` (a mail thread's, `mailConversation`) is kept already, of
   * `agent` under `client`'s authority (none: the owner's own). A read of its key.
   */
  kept(id: string, agent: string, client: string | null): boolean {
    return this.#sql.exec(
      "SELECT 1 FROM conversations WHERE id = ? AND agent = ? AND coalesce(client, '') = ?",
      id, agent, client ?? "").toArray().length === 1;
  }

  /** Whether the inputs' queue has room for one more of `bytes`. A read. */
  queueRoom(bytes: number): boolean {
    const queued = this.#sql.exec<{ n: number; bytes: number }>(
      "SELECT COUNT(*) AS n, coalesce(SUM(bytes), 0) AS bytes FROM run_inputs").one();
    return queued.n < QUEUE_ROWS && queued.bytes + bytes <= QUEUE_BYTES;
  }

  /**
   * In a unit: a waiting run its turn refused (its grant no longer stands, its agent was
   * disabled, no source serves it): settled `failed` with the refusal's code, the refusal as
   * its error. False when it no longer waits.
   */
  refuseWaiting(runId: string, reason: string, now: number): boolean {
    if (this.#sql.exec("SELECT 1 FROM runs WHERE id = ? AND state = 'waiting'", runId)
          .toArray().length === 0) {
      return false;
    }
    this.#sql.exec("UPDATE runs SET error = ? WHERE id = ?", cut(reason, ERROR_BYTES).text,
                   runId);
    return this.settle(runId, "failed", /^[a-z_]{1,64}/.exec(reason)?.[0] ?? "refused", null,
                       now);
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

  /**
   * The conversation `ask` continues: the caller's own, of this agent; busy while a run in
   * it is live, or (for a new input) while any waits, or (for a waiting run) while one older
   * than it waits.
   */
  #conversation(ask: Ask, waiting: string | null):
      { id: string; source: string; session_id: string | null; busy: boolean } {
    const found = typeof ask.conversation === "string" && CONVERSATION_ID.test(ask.conversation)
      ? this.#sql.exec<{ id: string; agent: string; source: string; client: string | null;
                         session_id: string | null }>(
        "SELECT id, agent, source, client, session_id FROM conversations WHERE id = ?",
        ask.conversation).toArray()[0] : undefined;
    if (found === undefined || found.agent !== ask.agent
        || found.client !== (ask.origin?.client ?? null)) {
      throw new Refused(NO_CONVERSATION);
    }
    const busy = this.#sql.exec(
      `SELECT 1 FROM runs WHERE conversation = ? AND (${LIVE} OR (state = 'waiting' AND ` +
        "seq < coalesce((SELECT seq FROM runs WHERE id = ?), 9223372036854775807)))",
      found.id, waiting).toArray().length > 0;
    return { ...found, busy };
  }

  /** A new conversation, the least recently run idle one forgotten first at the bound. */
  #newConversation(ask: Ask, engine: string, now: number): string {
    const count = this.#sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM conversations")
      .one().n;
    if (count >= CONVERSATIONS_KEPT) {
      this.#sql.exec("DELETE FROM conversations WHERE id IN (SELECT c.id FROM conversations c " +
                       "WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.conversation = c.id " +
                       `AND r.${UNSETTLED}) ORDER BY c.last_at, c.id LIMIT ?)`,
                     count - CONVERSATIONS_KEPT + 1);
    }
    const conversation = ask.mail?.conversation ?? id("cv_");
    this.#sql.exec("INSERT INTO conversations (id, agent, source, client, created_at, " +
                     "last_at) VALUES (?, ?, ?, ?, ?, ?)", conversation, ask.agent, engine,
                   ask.origin?.client ?? null, now, now);
    return conversation;
  }

  /**
   * The engine, credential, connection and model a run of `file` runs on (ADR-034 decision
   * 4.1): its named engine, on its named credential (else its record's credentials in
   * order, a key's run on the named connection, else the strongest connected one whose
   * source lists the engine); or for `auto`, the strongest connected source that lists an
   * engine. An unattended run takes only a credential its record allows unattended. The
   * sign-ins read are the run's own sandbox's (`authority`'s): the owner's own sandbox's
   * (`engine_signins`), or an outside client's, as its own runner reported them on its live
   * lease. A login is never copied: a client's `auto` run uses a plan only where that
   * client's sandbox is known signed in itself, else the owner's keys. An agent that names
   * the vendor's sign-in is admitted unless its sandbox is known signed out (the owner's
   * own, `SIGNIN_LAPSED`); the sandbox's start checks it (`SIGNIN_REQUIRED`).
   */
  choose(file: AgentFile, unattended: boolean, authority = OWNER_AUTHORITY, now = 0):
      { engine: string; credential: CredentialKind; connection: string | null;
        source: string | null; model: string | null } {
    const sources = this.#deps.sources();
    const reachable = new Set(this.#deps.egressible().map((entry) => entry.connection));
    const keys = this.#deps.usable().filter((entry) => reachable.has(entry.connection));
    const candidates = ranked(keys);
    // Each engine its sandbox's runner reported a sign-in for: presence alone.
    const client = authority !== OWNER_AUTHORITY;
    const signins = new Map<string, SignIn>(client
      ? (this.#deps.lease.live(now, authority)?.signins ?? []).map((engine) =>
        [engine, { engine, signed_in: true, models: [], checked_at: now, changed_at: 0 }])
      : this.signins().map((signin) => [signin.engine, signin]));
    const lapsed = (engine: string) => new Refused(`${SIGNIN_LAPSED} (${engine})`);
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
          // Never a silent fall back to a key. A switched-off source's login is never used,
          // even where it is signed in (review H1). A sign-in not known signed out is
          // admitted: the sandbox's start checks it in that sandbox (`SIGNIN_REQUIRED`).
          if (!this.#deps.signinOn(file.engine)) {
            throw new Refused(`${SIGNIN_OFF} (${file.engine})`);
          }
          if (!client && signins.get(file.engine)?.signed_in === false) {
            throw lapsed(file.engine);
          }
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
      // The owner's subscriptions (each engine signed in, or lapsed) ranked with its
      // connections, by the same ranking, each by the models the runner read it offers; a
      // subscription covering a key's provider is preferred to the key (the plan is used).
      const plans = new Map<string, string>();  // provider -> engine
      const planned: UsableSource[] = [];
      for (const signin of signins.values()) {
        const record = sources.get(signin.engine);
        // A switched-off source's plan is no candidate (review H1).
        if (record?.cli === undefined || record.unwired !== undefined
            || !record.cli.credential.includes("vendor_login_in_sandbox")
            || !this.#deps.signinOn(signin.engine)) {
          continue;
        }
        const provider = providerOf(record.cli.api_key_env, signin.engine);
        plans.set(provider, signin.engine);
        planned.push({ connection: `${PLAN}${signin.engine}`, source: signin.engine, provider,
                       display_name: signin.engine, created_at: signin.changed_at,
                       models: (signin.models.length === 0 ? [""] : signin.models)
                         .map((model) => planModel(model)) });
      }
      for (const candidate of ranked([...keys, ...planned])) {
        if (file.connection !== null && candidate.connection !== file.connection) {
          continue;
        }
        const plan = candidate.connection.startsWith(PLAN) ? candidate.source
          : file.connection === null ? plans.get(candidate.provider) : undefined;
        if (plan !== undefined && allows(plan, "vendor_login_in_sandbox")) {
          if (signins.get(plan)?.signed_in !== true) {
            throw lapsed(plan);  // stops picking it, and never falls back silently
          }
          const model = candidate.connection.startsWith(PLAN) && candidate.model.id !== ""
            ? candidate.model.id : null;
          return { engine: plan, credential: "vendor_login_in_sandbox", connection: null,
                   source: null, model: file.model ?? model };
        }
        if (candidate.connection.startsWith(PLAN)) {
          continue;  // a plan its record does not allow here (unattended)
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

  // -- SIGN-INS: each engine's, as the runner reports it --------------------------------

  /** Every engine's sign-in as last reported. A read. */
  signins(): SignIn[] {
    return this.#sql.exec<{ engine: string; signed_in: number; models: string;
                            checked_at: number; changed_at: number }>(
      "SELECT * FROM engine_signins ORDER BY engine").toArray()
      .map((row) => ({ engine: row.engine, signed_in: row.signed_in === 1,
                       models: JSON.parse(row.models) as string[], checked_at: row.checked_at,
                       changed_at: row.changed_at }));
  }

  /**
   * In a unit: the runner's report of `engine`'s sign-in (its record's `status_argv`), and
   * the models it read the engine offers; whether it lapsed (was signed in, now is not).
   */
  reportSignin(engine: string, signedIn: boolean, models: readonly string[], now: number):
      { lapsed: boolean } {
    const before = this.#sql.exec<{ signed_in: number }>(
      "SELECT signed_in FROM engine_signins WHERE engine = ?", engine).toArray()[0];
    this.#sql.exec("INSERT INTO engine_signins (engine, signed_in, models, checked_at, " +
                     "changed_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (engine) DO UPDATE SET " +
                     "signed_in = excluded.signed_in, models = excluded.models, " +
                     "checked_at = excluded.checked_at, changed_at = CASE WHEN " +
                     "signed_in = excluded.signed_in THEN changed_at ELSE excluded.changed_at " +
                     "END", engine, signedIn ? 1 : 0, JSON.stringify(models), now, now);
    return { lapsed: before?.signed_in === 1 && !signedIn };
  }

  /**
   * Settled runs past the newest `RUNS_KEPT` by their settle (review round 2), after every
   * write of a run (each admission, each input that waits) and every settle: so the rows are
   * at most `RUNS_KEPT`, the live ones and the waiting ones, however runs are queued,
   * cancelled or settled, and a long run's receipt outlives its settle.
   */
  #prune(): void {
    // The newest `RUNS_KEPT` by when they settled: never the one settling now, nor a live or
    // a waiting one, however long it ran or waited.
    this.#sql.exec("DELETE FROM runs WHERE settled_at IS NOT NULL AND seq NOT IN (SELECT seq " +
                     "FROM runs WHERE settled_at IS NOT NULL ORDER BY settled_at DESC, seq DESC " +
                     "LIMIT ?)", RUNS_KEPT);
  }

  // -- SANDBOX TIME: the meter's side of the runs (`sandbox-time.ts`) -----------------

  /** The runs' side of the sandbox meter: its reads and its one write, here alone. */
  meter(): RunMeter {
    return runMeter(this.#sql);
  }

  // -- A RUN'S LIFE ------------------------------------------------------------------

  /** In a unit: the sandbox took the run's start. */
  started(runId: string, now: number): void {
    this.#sql.exec("UPDATE runs SET state = 'running', started_at = ? WHERE id = ? " +
                     "AND state = 'queued'", now, runId);
  }

  /** The live run's grant, or null (none once it is cancelled or settled). A read. */
  grant(runId: string): RunGrant | null {
    const row = this.#sql.exec<GrantRow>(`${GRANTS} WHERE g.run = ? AND r.${LIVE}`, runId)
      .toArray()[0];
    return row === undefined ? null : grantOf(row);
  }

  /**
   * The grant of a call from the live run `runId` under `lease`, presenting `secret` (the
   * SHA-256 of what its path carried), or null: a run id alone authorizes nothing, and a
   * cancelled run's grant is gone. A read.
   */
  grantFor(runId: unknown, secret: string | undefined, lease: LiveLease): RunGrant | null {
    if (typeof runId !== "string" || !RUN_ID.test(runId) || secret === undefined) {
      return null;
    }
    const row = this.#sql.exec<GrantRow>(`${GRANTS} WHERE g.run = ? AND r.${LIVE}`, runId)
      .toArray()[0];
    return row !== undefined && row.secret === secret && row.lease_id === lease.lease_id
      && row.epoch === lease.epoch ? grantOf(row) : null;
  }

  /**
   * The stamp an outside client's live run in its sandbox holds, or null when none is live:
   * what that sandbox's sync is held to (its grant standing at that stamp). A read.
   */
  authorityStamp(client: string): Stamped | null {
    const row = this.#sql.exec<{ stamp: string }>(
      `SELECT g.stamp FROM run_grants g JOIN runs r ON r.id = g.run WHERE r.${LIVE} ` +
        "AND r.client = ? AND g.stamp IS NOT NULL ORDER BY r.seq LIMIT 1", client).toArray()[0];
    return row === undefined ? null : JSON.parse(row.stamp) as Stamped;
  }

  /** Whether `runId` is live under `lease`: the runner's own report of it. A read. */
  liveUnder(runId: unknown, lease: LiveLease): boolean {
    return typeof runId === "string" && RUN_ID.test(runId) && this.#sql.exec(
      `SELECT 1 FROM runs WHERE id = ? AND ${LIVE} AND lease_id = ? AND epoch = ?`, runId,
      lease.lease_id, lease.epoch).toArray().length === 1;
  }

  /**
   * In a unit: the run settled, once: its state, why (`code`) and the runner's exit; its
   * grant revoked; its streams ended; and the lease given back when no live run holds it.
   * False for a run not live.
   */
  settle(runId: string, state: "succeeded" | "failed" | "unknown", code: string | null,
         exitCode: number | null, now: number): boolean {
    const settled = this.#sql.exec<{ conversation: string; lease_id: string | null;
                                     epoch: number | null; mail: string | null;
                                     agent: string }>(
      "UPDATE runs SET state = ?, code = ?, exit_code = ?, settled_at = ? WHERE id = ? " +
        `AND ${UNSETTLED} RETURNING conversation, lease_id, epoch, mail, agent`, state, code,
      exitCode, now, runId).toArray();
    if (settled.length === 0) {
      return false;
    }
    const lease = settled[0]!;
    if (lease.mail !== null) {
      this.#deps.mailSettled(lease.mail, lease.agent);
    }
    this.#sql.exec("DELETE FROM run_inputs WHERE run = ?", runId);
    this.#sql.exec("DELETE FROM run_grants WHERE run = ?", runId);
    this.#sql.exec("UPDATE conversations SET last_at = ? WHERE id = ?", now,
                   lease.conversation);
    this.#deps.ended(runId);
    if (lease.lease_id !== null && this.#sql.exec(
      `SELECT 1 FROM runs WHERE ${LIVE} AND lease_id = ? AND epoch = ?`, lease.lease_id,
      lease.epoch).toArray().length === 0) {
      if (this.#deps.lease.release(lease.lease_id, lease.epoch!)) {
        this.#deps.leaseEnded(lease.lease_id);
      }
    }
    this.#prune();
    return true;
  }

  /**
   * In a unit: the runner's frames for a live run under the live lease, renewing it (each
   * batch is the runner's heartbeat); each new frame (`seq` past the last) read by the run's
   * record, its session id, result and error kept; the frames to relay, and the settle the
   * runner's `end` brings. A mail's run that succeeded with a result answers it before it
   * settles, while its mail is still held (`RunDeps.answer`).
   */
  /**
   * A read, before an events batch's unit: the reply its settle would post
   * (`RunDeps.answer`), or null, so the door can place a long one in R2 first (ADR-035).
   */
  answering(runId: string, lease: LiveLease, frames: readonly PostedFrame[],
            end: { readonly exit_code: number | null } | null): MailAnswer | null {
    const row = end !== null && this.liveUnder(runId, lease) ? this.#run(runId) : undefined;
    const events = row === undefined ? undefined
      : this.#deps.sources().get(row.source)?.cli?.engine.events;
    if (row === undefined || end === null || events === undefined) {
      return null;
    }
    const { result, error } = batch(row, events, frames);
    return this.#answerOf(row, end.exit_code !== 0 || error !== null
      || row.cancel_requested === 1, result);
  }

  /**
   * The post a settled run's result makes: its reply to the mail it answers, or a scheduled
   * run's to its owner's room with its agent; null for none (a failed run, an empty result).
   */
  #answerOf(row: RunRow, failed: boolean, result: string | null): MailAnswer | null {
    const grant = this.#sql.exec<GrantRow>(`${GRANTS} WHERE g.run = ?`, row.id).toArray()[0];
    if (failed || result === null || result === "" || grant === undefined
        || (row.mail === null && row.initiator !== "schedule")) {
      return null;
    }
    const granted = grantOf(grant);
    // A schedule's result posts only for the owner's own schedule, and not when the run said
    // there is nothing to tell (`NO_REPLY`). A client's schedule's result is that client's,
    // read through its `run_status`, never posted into the owner's rooms (review M1).
    if (row.mail === null && (granted.stamp !== null || result.trim() === NO_REPLY)) {
      return null;
    }
    return { run: row.id, agent: row.agent, mail: row.mail, result, stamp: granted.stamp,
             origin: granted.origin };
  }

  events(runId: string, lease: LiveLease, frames: readonly PostedFrame[],
         end: { readonly exit_code: number | null } | null, now: number):
      { conversation: string; frames: Frame[]; settled: RunState | null } {
    const row = this.liveUnder(runId, lease) ? this.#run(runId) : undefined;
    if (row === undefined) {
      throw new Refused(RUN_ENDED);
    }
    this.#deps.lease.renew(lease.lease_id, lease.epoch);
    const events = this.#deps.sources().get(row.source)?.cli?.engine.events;
    if (events === undefined) {
      throw new Error(`run ${runId}'s engine ${row.source} has no record`);
    }
    const { last, result, resultCut, error, session, relayed } = batch(row, events, frames);
    for (const frame of frames) {
      const notice = frame.seq > row.last_seq ? unnamedNotice(frame.event) : null;
      if (notice !== null) {
        this.#deps.notice?.("sync_unnamed", `${row.agent}: ${notice}`);
      }
    }
    // The batch is this run's own heartbeat, as it renews the lease.
    this.#sql.exec("UPDATE runs SET last_seq = ?, result = ?, result_cut = ?, error = ?, " +
                     "state = CASE WHEN state = 'queued' THEN 'running' ELSE state END, " +
                     "started_at = coalesce(started_at, ?), beat_at = ? WHERE id = ?",
                   last, result, resultCut ? 1 : 0, error, now, now, runId);
    if (session !== null) {
      this.#sql.exec("UPDATE conversations SET session_id = ? WHERE id = ?", session,
                     row.conversation);
    }
    let settled: RunState | null = null;
    if (end !== null) {
      const failed = end.exit_code !== 0 || error !== null || row.cancel_requested === 1;
      settled = failed ? "failed" : "succeeded";
      const answer = this.#answerOf(row, failed, result);
      if (answer !== null) {
        this.#deps.answer(answer);
      }
      this.settle(runId, settled, row.cancel_requested === 1 ? "cancelled"
        : end.exit_code !== 0 ? "exit" : error !== null ? "engine_error" : null,
                  end.exit_code, now);
    }
    return { conversation: row.conversation, frames: relayed, settled };
  }

  /**
   * In a unit: a live run marked cancelled and its grant revoked at once, so its MCP and
   * model calls are refused from this unit on and its streams end; the run and its lease,
   * for the sandbox's cancel. It settles `failed` (`cancelled`) from the runner's end, or
   * `unknown` when its heartbeat lapses. A waiting run is settled `failed` (`cancelled`) at
   * once: it never started.
   */
  cancel(runId: string, now: number): LiveRun | null {
    if (this.#sql.exec("SELECT 1 FROM runs WHERE id = ? AND state = 'waiting'", runId)
          .toArray().length === 1) {
      this.settle(runId, "failed", "cancelled", null, now);
      return null;
    }
    const live = this.#sql.exec<LiveRun>(
      `UPDATE runs SET cancel_requested = 1 WHERE id = ? AND ${LIVE} ` +
        "RETURNING id AS run, lease_id, epoch", runId).toArray()[0];
    if (live === undefined) {
      return null;
    }
    if (this.#sql.exec("DELETE FROM run_grants WHERE run = ? RETURNING run", runId).toArray()
          .length === 1) {
      this.#deps.ended(runId);
    }
    return { run: live.run, lease_id: live.lease_id, epoch: live.epoch };
  }

  /**
   * In a unit: the client's runs ended (a grant change or revoke ends them, ADR-034
   * decision 6): each settled `failed` (`revoked`) at once, live or waiting, so its grant
   * is gone, its tier slot and conversation free and its lease given back with the last;
   * and the client's schedules removed. The live ones, to stop in the sandbox.
   */
  endClient(client: string, now: number): LiveRun[] {
    const ended = this.#sql.exec<{ id: string; lease_id: string | null; epoch: number | null;
                                   state: RunState }>(
      `SELECT id, lease_id, epoch, state FROM runs WHERE client = ? AND ${UNSETTLED}`, client)
      .toArray();
    for (const row of ended) {
      this.settle(row.id, "failed", "revoked", null, now);
    }
    this.#sql.exec("DELETE FROM run_schedules WHERE client = ?", client);
    return ended.filter((row) => row.state !== "waiting")
      .map((row) => ({ run: row.id, lease_id: row.lease_id!, epoch: row.epoch! }));
  }

  /** Every live run and its lease. A read. */
  liveRuns(): LiveRun[] {
    return this.#sql.exec<LiveRun>(
      `SELECT id AS run, lease_id, epoch FROM runs WHERE ${LIVE} ORDER BY seq`).toArray();
  }

  /** In a unit: every live run marked cancelled, its grant revoked; to cancel in the sandbox. */
  endAll(now: number): LiveRun[] {
    return this.#sql.exec<{ id: string }>(`SELECT id FROM runs WHERE ${LIVE}`).toArray()
      .map(({ id: run }) => this.cancel(run, now)).filter((run) => run !== null);
  }

  /** The caller's run's receipt, or refused: a client sees its own runs only. A read. */
  receipt(runId: unknown, client: string | null): RunReceipt {
    const row = typeof runId === "string" && RUN_ID.test(runId) ? this.#run(runId) : undefined;
    if (row === undefined || (client !== null && row.client !== client)) {
      throw new Refused(NO_RUN);
    }
    return receipt(row);
  }

  /**
   * The caller's runs, newest first (by `seq`), `limit` a page (at most `MAX_LIST`, the
   * object's own most a listing pages), from before `before`: the owner's every run
   * (`client` null), an outside client's its own. Each its receipt without its result. A
   * read.
   */
  list(client: string | null, before: unknown, limit: unknown):
      { runs: (Omit<RunReceipt, "result"> & { seq: number })[]; next: number | null } {
    const most = limit === undefined ? MAX_LIST : limit;
    if (typeof most !== "number" || !Number.isSafeInteger(most) || most < 1 || most > MAX_LIST
        || (before !== undefined && (typeof before !== "number" || !Number.isSafeInteger(before)
                                     || before < 1))) {
      throw new Invalid(`a run list's limit is 1 to ${MAX_LIST}, its before a run's seq`);
    }
    const rows = this.#sql.exec<RunRow & { seq: number }>(
      "SELECT * FROM runs WHERE (? IS NULL OR client = ?) AND (? IS NULL OR seq < ?) " +
        "ORDER BY seq DESC LIMIT ?", client, client, before ?? null, before ?? null, most + 1)
      .toArray();
    const page = rows.slice(0, most);
    return {
      runs: page.map((row) => {
        const { result: _result, ...rest } = receipt(row);
        return { ...rest, seq: row.seq };
      }),
      next: rows.length > most ? page.at(-1)!.seq : null,
    };
  }

  /** The agents of an outside client's live runs: what its sandbox always reads. A read. */
  liveAgents(client: string): string[] {
    return this.#sql.exec<{ agent: string }>(
      `SELECT DISTINCT agent FROM runs WHERE client = ? AND ${LIVE} ORDER BY agent`, client)
      .toArray().map((row) => row.agent);
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
   * In a unit: the live runs whose runner was lost settled `unknown`, never replayed: each
   * whose lease is gone (no heartbeat of the sandbox's within the lease's life), and each
   * whose own heartbeat lapsed (no report of its own within `LEASE_TTL_S`, while other runs
   * keep the lease) or whose grant a cancel revoked and whose runner never ended it; then the
   * lapsed leases' rows gone. It reads the runs' own rows, never their grants, so a run
   * whose grant is gone is still found. The ones to stop in the sandbox.
   */
  lost(now: number): LiveRun[] {
    const leases = new Set(this.#deps.lease.all(now)
      .map((lease) => `${lease.lease_id}/${lease.epoch}`));
    const lost = this.#sql.exec<LiveRun & { beat_at: number }>(
      `SELECT id AS run, lease_id, epoch, beat_at FROM runs WHERE ${LIVE} ORDER BY seq`)
      .toArray().filter((row) => !leases.has(`${row.lease_id}/${row.epoch}`)
                       || row.beat_at + LEASE_TTL_S <= now);
    for (const row of lost) {
      this.settle(row.run, "unknown", "lost", null, now);
    }
    this.#deps.lease.lapsed(now);
    const stopped = lost.map(({ run, lease_id, epoch }) => ({ run, lease_id, epoch }));
    if (stopped.length > 0) {
      this.#deps.stop(stopped);
    }
    return stopped;
  }

  // -- THE RUNS LANE: schedules ---------------------------------------------------------

  /** In a unit: a schedule of `agent`'s input: at a time, or on a cron. */
  schedule(request: { agent: string; input: string; at: number | null; cron: string | null;
                      stamp: Stamped | null;
                      origin: { client: string; family: string } | null }, now: number):
      RunSchedule {
    if (utf8Bytes(request.input) > INPUT_BYTES) {
      throw new Invalid(`a scheduled input is at most ${INPUT_BYTES} bytes`);
    }
    if ((request.at === null) === (request.cron === null)) {
      throw new Invalid("a schedule is one of at or cron");
    }
    let next: number | null;
    let trigger: RunSchedule["trigger"];
    if (request.cron !== null) {
      next = nextOccurrence(parseCron(request.cron), now);
      trigger = "cron";
    } else {
      if (!Number.isFinite(request.at) || request.at! <= now) {
        throw new Invalid("at is a time still ahead, in seconds since the epoch");
      }
      next = request.at!;
      trigger = "at";
    }
    const scheduleId = id("sch_");
    this.#sql.exec(
      "INSERT INTO run_schedules (id, agent, input, trigger, cron, next_at, client, " +
        "stamp, origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      scheduleId, request.agent, request.input, trigger, request.cron,
      next, request.origin?.client ?? null,
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
   * When the lane next has work: the earliest due schedule, only while the owner is
   * admitted (`require_work`: a suspended owner's schedules wait), a waiting run's turn, and
   * each live run's lease expiry or own heartbeat's lapse (a lost runner is settled then).
   * Null for none: with no run live and none due, the lane is disarmed. A read.
   */
  next(now: number, admitted: boolean): number | null {
    const due = !admitted ? null : this.#sql.exec<{ at: number | null }>(
      "SELECT MIN(next_at) AS at FROM run_schedules WHERE next_at IS NOT NULL").one().at;
    const times = due === null ? [] : [Math.max(now, due)];
    if (admitted && this.nextWaiting() !== null) {
      times.push(now);  // a waiting run's turn has come
    }
    // A live run is looked at again when its lease or its own heartbeat would lapse.
    const leases = new Map(this.#deps.lease.all(now)
      .map((lease) => [`${lease.lease_id}/${lease.epoch}`, lease.expires_at]));
    for (const row of this.#sql.exec<{ lease_id: string; epoch: number; beat_at: number }>(
      `SELECT lease_id, epoch, beat_at FROM runs WHERE ${LIVE}`).toArray()) {
      const expires = leases.get(`${row.lease_id}/${row.epoch}`) ?? now;
      times.push(Math.max(now, Math.min(expires, row.beat_at + LEASE_TTL_S)));
    }
    return times.length === 0 ? null : Math.min(...times);
  }

  /**
   * In a unit: the next due schedule claimed: its next time moved on (a cron's next
   * occurrence; an `at` removed), so it fires once. Null when none is due.
   */
  claim(now: number): ScheduleRow | null {
    const row = this.#sql.exec<ScheduleRow>(
      "SELECT * FROM run_schedules WHERE next_at IS NOT NULL AND next_at <= ? " +
        "ORDER BY next_at, id LIMIT 1", now).toArray()[0];
    if (row === undefined) {
      return null;
    }
    if (row.trigger === "at") {
      this.#sql.exec("DELETE FROM run_schedules WHERE id = ?", row.id);
    } else {
      this.#sql.exec("UPDATE run_schedules SET next_at = ? WHERE id = ?",
                     nextOccurrence(parseCron(row.cron), now), row.id);
    }
    return row;
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

/**
 * The conversation id of a mail thread to `agent` under `client`'s authority (none: the
 * owner's own): one a thread, agent and authority, its key the triple's digest, so no index
 * finds it.
 */
export function mailConversation(agent: string, thread: string, client: string | null):
    string {
  return "cv_" + sha256Hex(`tinyassets-mail-conversation\0${agent}\0${thread}\0${client ?? ""}`).slice(0, 32);
}
