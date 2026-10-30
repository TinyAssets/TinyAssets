/**
 * One owner's trusted state, in that owner's own SQLite-backed Durable Object (ADR-032).
 *
 * Only an edge Worker (the binder) turns a verified credential into an owner and a
 * caller, and it reaches this object by `getByName(owner)`. Every RPC method takes that
 * {@link Binding} as its first, typed argument; nothing in a request body names the
 * owner or the caller. The object serves only the owner it is named by (`ctx.id.name`),
 * refuses when it has no name, and also stores that owner on first use and refuses any
 * other, so a binder bug that reaches the wrong object fails loudly instead of acting
 * there.
 *
 * What the binder verified, the object takes as typed binding fields: the outside
 * client a call comes through (`origin`, `source`, `authenticated_at`) and the owner's
 * session cookie (`session`). What it decides from them, it decides itself, from its
 * own rows: every outside call, at every door, is admitted against that client's grant
 * and held to its scopes (`grants.ts`: `read_graph`, `write_graph`, `propose_policy`,
 * `read_files`, `write_files`), and every widening checks the owner's live session row
 * (`sessions.ts`), a refused one leaving a refusal receipt in the policy receipts
 * (`policy.ts`). An outside client's call never carries the owner's session, with one
 * pinned exception: the owner's browser registering an agent for an API key it holds
 * (`registerAgent`), whose origin the object re-derives from the key id. An MCP client's
 * agent registers through a card the client files on its own verified call
 * (`registerPropose`, `registrations.ts`), which the owner applies in session by its id.
 * Every other door refuses the pair.
 *
 * Its code cannot reach another owner's object: the Worker binds only `FILES`, the files
 * Worker, which itself binds no Durable Object and no service, and `GATEWAY`, the model
 * gateway, which binds nothing at all; the runtime hands it no
 * `ctx.exports` (`disable_ctx_exports`); and its source never names `exports` or imports
 * `env` (the config and self-reach guards). The owner's file bytes reach the bucket only
 * through `FILES`, under the owner's namespace, which only this object holds
 * (`files.ts`). The module keeps no mutable state at top level (the module-state
 * guard), since objects of one class can share an isolate.
 *
 * Every public method is an RPC any binding can call, so the class has no public method
 * but its doors (`RPC_METHODS` in the tests pins the list). Its timed work is the one
 * schedule's (`schedule.ts`): lanes on the Agents SDK Lifecycle job queue, which owns the
 * object's single physical alarm, beside PiHarness's wake job (`agent.ts`: pi-durable in
 * the object). The object is a plain Durable Object that holds a Lifecycle, never the
 * SDK's `Agent`, and installs none of Lifecycle's runtime handlers: `alarm` alone hands
 * the runtime's alarm to it, so no `fetch` or WebSocket handler joins the surface.
 * Claiming and settling wakes is the `wakes` lane's own, in process, with the doorbell's
 * {@link WakeSender}; file housekeeping (expired uploads and temporary directories,
 * history prunes, garbage chunk deletes) is the `files` lane's. No door reaches either.
 *
 * The owner's model connections (`connections.ts`) hold only blobs the gateway sealed;
 * their doors are the owner's, in its live session. The agent's one model provider
 * (`gateway-provider.ts`) reserves each call against the owner's model policy (`usage.ts`)
 * and calls `GATEWAY` with the sealed blob; the `connections` lane sweeps expired flows
 * and asks providers to revoke what the owner revoked.
 *
 * Every owner write goes through the object's write gate (`gate.ts`), the SQLite queue
 * pi's commits use, so none lands inside a pi transaction: each door's synchronous body is
 * one unit (`#door`), a door that then crosses to `FILES` writes its later steps through
 * units of their own, and each lane writes a row at a time. The modules see the storage
 * only through the gate's view, which refuses a write outside a unit. Reads are direct.
 *
 * The agent's threads run under the authority they were made under (`authority.ts`,
 * `threads.ts`): an outside client's every tool operation and model call is held to its
 * grant as it stands, and a grant change (`grantSet`, `grantRevoke`) or a policy write
 * ends the agent work it covers once its own unit committed (`agent.ts`). The `work` lane
 * hands due turns to pi through the object's `workRunner`. The agent's `ta` (`ta.ts`)
 * reaches the object's policy, a proposal, its scheduled work and its file usage only
 * through the narrow `Platform` the object hands it, each held to the conversation's
 * authority as the matching door holds its caller.
 */
import type { HarnessSettings } from "@earendil-works/pi-durable";
import { Lifecycle } from "agents/lifecycle";
import { DurableObject } from "cloudflare:workers";

import { type AgentParts, gatewayAgent, type OwnerAgent, type UsageView } from "./agent";
import { Authority, AUTHORITY_RULE } from "./authority";
import { Collections, Conflict, type HistoryEntry, type Page, type Row } from "./collections";
import { type ConnectionView, Connections, type GatewayService, type Prepared } from "./connections";
import { Invalid, Refused } from "./errors";
import {
  type Change as FileChange, CHUNK_BYTES, type ChunkRef, type ChunkStore, type Content,
  type Entry,
  FileConflict, Files, type Lease, type SyncOp, type SyncOutcome, type Usage,
} from "./files";
import { type OwnerStorage, type Write, WriteGate } from "./gate";
import { type Capability, type Client, type Grant, Grants, holds, type Scope } from "./grants";
import {
  type Agent, clock, Ledger, type MailPage, type Origin, outsideOrigin, type Posted,
  registrant, type Source,
} from "./ledger";
import { type Notice, Notices } from "./notices";
import {
  agentPolicy, type Change, fileSettings, modelSettings, Policy, type PolicyDocument,
  type Projection, type Receipt,
} from "./policy";
import { type Card, Registrations } from "./registrations";
import { GatewayProvider } from "./gateway-provider";
import { LiveTee } from "./live-tee";
import { type Lane, Schedule } from "./schedule";
import {
  runWork, Scheduled, WORK_HANDOFF_MS, WORK_PER_PASS, type WorkRunner, workTurn,
} from "./scheduled";
import { migrate } from "./schema";
import { Sessions } from "./sessions";
import { threadAuthority, type ThreadReader, Threads } from "./threads";
import { principal } from "./text";
import { Usage as ModelUsage, type UsageReceipt } from "./usage";
import { runWakes, WAKE_SEND_MS, type WakeSender, WAKES_PER_ALARM } from "./wakes";

/**
 * Whose object this is, who is asking and through what: set by the binder from a
 * verified credential, the one binding every door takes. `caller` is the owner's own
 * account, or, at the collection doors alone, another owner visiting (origin null).
 * `origin` is the verified outside client the owner's account acts through (an outside
 * agent), or null. The object takes every sender and reader from here, never from a
 * request field.
 */
export interface Binding {
  readonly owner: string;
  readonly caller: string;
  readonly origin: Origin | null;
  /** How the binder verified `origin`: an MCP client or an API key. Every outside call. */
  readonly source?: Source;
  /**
   * When the credential was signed in, in whole seconds: an outside client's, which a
   * revoke fences, or the owner's interactive sign-in, which opens a session. Every MCP
   * call carries it: the grant's original interactive sign-in, which a token refresh never
   * moves. A revoke, and once any MCP revoke is forgotten the owner-wide fence, refuses an
   * MCP call without it.
   */
  readonly authenticated_at?: number;
  /** The owner's protected-session cookie, as the browser presented it. */
  readonly session?: string;
}

export type { Origin, Source, WakeSender };

export interface RegisterRequest {
  readonly command_center_id: string;
  readonly display_name: string;
  readonly delivery_method: string;
  readonly recipe_id: string | null;
}

export interface DisableRequest {
  readonly agent_id: string;
  readonly reason: string;
}

export interface AgentRequest {
  readonly agent_id: string;
}

export interface WakeLimitsRequest {
  readonly agent_id: string;
  readonly max_wakes_per_hour: number;
  readonly batch_window_s: number;
  readonly expected_generation: number;
}

export interface PolicyWriteRequest {
  readonly expected_generation: number;
  readonly policy: PolicyDocument;
  readonly proposal?: { readonly request_id: string; readonly digest: string };
}

export interface ProposeRequest {
  readonly change: Change;
  readonly request_id?: string;
}

export interface CardRequest {
  readonly request_id: string;
}

/** The owner's apply of a held registration: the card and the digest it was shown. */
export interface CardApply {
  readonly card_id: string;
  readonly digest: string;
}

export interface RegistrationCardRequest {
  readonly card_id: string;
}

export interface GrantRequest {
  readonly client: string;
  readonly source: string;
  readonly mode: string;
  readonly scopes: Scope[];
  readonly expected_generation: number;
  readonly name: string;
}

export interface ClientRequest {
  readonly client: string;
}

export interface ForgetRequest {
  readonly clients: string[];
}

export interface PostRequest {
  readonly room_id: string;
  readonly client_message_id: string;
  readonly body: string;
  readonly parent_id: string | null;
}

export interface ReadRequest {
  readonly limit: number;
  readonly max_bytes?: number;
  readonly pad_bytes?: number;
}

export interface AckRequest {
  readonly through: number;
}

export interface SubscriberRequest {
  readonly command_center_id: string;
  readonly agent_id: string;
}

/**
 * The object's environment: two service bindings, `FILES`, to the files Worker, which
 * binds no Durable Object and no service, and `GATEWAY`, to the model gateway, which binds
 * nothing (the config guard checks every config), and never a binding to its own
 * namespace.
 */
export interface OwnerEnv {
  readonly FILES: ChunkStore;
  readonly GATEWAY: GatewayService;
}

/**
 * A connection to prepare: its source record's id, kind, display name and pi-ai provider,
 * whether its provider can be asked to revoke what it issues, and, for OAuth, the redirect
 * URI its flow (begun in the same unit) returns to.
 */
export interface ConnectionPrepareRequest {
  readonly source: string;
  readonly kind: "api_key" | "oauth2";
  readonly display_name: string;
  readonly provider: string;
  readonly revocable: boolean;
  readonly redirect_uri?: string;
}

/** A prepared connection's sealed blob, its kid, its models and, for OAuth, its expiry. */
export interface ConnectionCommitRequest {
  readonly connection: string;
  readonly version: number;
  readonly blob: string;
  readonly kid: string;
  readonly models: unknown[];
  readonly expires_at?: number | null;
  /** The setup check's code, when it failed after the provider issued the credential. */
  readonly failed?: string;
}

export interface ConnectionRequest {
  readonly connection: string;
}

export interface FlowFinishRequest {
  readonly state: string;
}

export interface FileRequest {
  readonly path: string;
}

export interface FileListRequest {
  readonly dir: string;
  readonly after?: string;
  readonly limit: number;
}

export interface FileReadRequest {
  readonly path: string;
  readonly etag?: string;
}

export interface FileChunksRequest {
  readonly path: string;
  readonly version: number;
  readonly first: number;
  readonly count: number;
}

export interface FileWriteRequest {
  readonly path: string;
  readonly expected_etag: string | null;
  readonly content: Uint8Array;
  readonly mode?: number;
}

export interface UploadBeginRequest {
  /** The file's size; `chunks` are its 64-hex hashes, one for each 4 MiB. */
  readonly size: number;
  readonly chunks: string[];
}

export interface UploadChunksRequest {
  readonly intent_id: string;
  readonly chunks: { sha256: string; bytes: Uint8Array }[];
}

export interface UploadCommitRequest {
  readonly intent_id: string;
  readonly path: string;
  readonly expected_etag: string | null;
  readonly mode?: number;
}

export interface IntentRequest {
  readonly intent_id: string;
}

export interface MkdirRequest {
  readonly path: string;
  readonly expected_etag: string | null;
}

export interface RemoveRequest {
  readonly path: string;
  readonly expected_etag: string | null;
  readonly recursive: boolean;
}

export interface RenameRequest {
  readonly from: string;
  readonly to: string;
  readonly expected_etag: string | null;
  readonly replace: boolean;
}

export interface FileHistoryRequest {
  readonly path: string;
  readonly before?: number;
  readonly limit: number;
}

export interface VersionRequest {
  readonly path: string;
  readonly version: number;
}

export interface RestoreRequest extends VersionRequest {
  readonly expected_etag: string | null;
}

export interface ChangesRequest {
  readonly after: number;
  readonly limit: number;
}

export interface LeaseRequest {
  readonly lease_id: string;
  readonly epoch: number;
}

export interface SyncCommitRequest extends LeaseRequest {
  readonly ops: SyncOp[];
}

/** A lost file compare-and-swap: answered with the entry as it stands. */
export interface FileConflicted {
  readonly conflict: Entry | null;
}

export interface WriteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly body: string;
  readonly expected_etag: string;
  readonly private: boolean;
  readonly history_op?: string;
}

export interface PullRequest {
  readonly collection: string;
  readonly after: number;
  readonly limit: number;
}

export interface KeyRequest {
  readonly collection: string;
  readonly row_id: string;
}

export interface DeleteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly expected_etag: string;
}

export interface ClearRequest {
  readonly collection: string;
  readonly writer: string | null;
}

/** A lost compare-and-swap is an answer carrying the row as it stands, not a refusal. */
export interface Conflicted {
  readonly conflict: Row | null;
}

/** What a call is bound to: its owner and caller, its outside client and that client's grant. */
interface Account {
  readonly owner: string;
  readonly caller: string;
  readonly origin: Origin | null;
  readonly grant: Grant | null;
}

/**
 * Who else an account door serves. `visitors`: another owner may call, as itself (the
 * collection doors). `registering`: an outside origin may come with the owner's session
 * (the owner's browser registering an agent for that client).
 */
interface Serves {
  readonly visitors?: boolean;
  readonly registering?: boolean;
}

export const OUTSIDE_SESSION = "an outside client's call never carries the owner's session";
export const MCP_REGISTERS_BY_CARD = "an MCP client's agent registers through the card it files";

/**
 * The request's fields are exactly `required`, plus any of `optional`, and none is
 * `undefined`: a field sent is a value, as the Python door refused `None`.
 */
function fields(request: unknown, required: readonly string[],
                optional: readonly string[] = [],
                refusal = "invalid collection request"): Record<string, unknown> {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Invalid(refusal);
  }
  const entries = Object.entries(request);
  const keys = entries.map(([key]) => key);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))
      || entries.some(([, value]) => value === undefined)) {
    throw new Invalid(refusal);
  }
  return request as Record<string, unknown>;
}

/** A ledger request's fields: exactly these, never a sender, reader or owner. */
function ledgerFields(request: unknown, required: readonly string[],
                      optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid ledger request");
}

function policyFields(request: unknown, required: readonly string[],
                      optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid policy request");
}

function conflicted<T>(act: () => T): T | Conflicted {
  try {
    return act();
  } catch (error) {
    if (error instanceof Conflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

function fileFields(request: unknown, required: readonly string[],
                    optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid file request");
}

function fileConflicted<T>(act: () => T): T | FileConflicted {
  try {
    return act();
  } catch (error) {
    if (error instanceof FileConflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

async function fileConflictedAfter<T>(act: () => Promise<T>): Promise<T | FileConflicted> {
  try {
    return await act();
  } catch (error) {
    if (error instanceof FileConflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

/** Who a file change is by, in its history: the owner, or the outside client it came through. */
function fileWriter(account: Account): string {
  return account.origin === null ? "owner" : `client:${account.origin.client}`;
}

function flag(value: unknown, what: string): boolean {
  if (typeof value !== "boolean") {
    throw new Invalid(`${what} is true or false`);
  }
  return value;
}

/** What the object's lanes run over: its stores, its in-process callers and the passes. */
export interface LaneDeps {
  readonly ledger: Ledger;
  readonly files: {
    nextAlarm(now: number): number | null;
    collect(now: number, until: number): Promise<void>;
  };
  readonly work: Scheduled;
  readonly connections: {
    nextDue(now: number): number | null;
    run(now: number, until: number): Promise<void>;
  };
  /** The doorbell, or null until tasks 7 constructs one: read at each use, never kept. */
  readonly sender: () => WakeSender | null;
  /** The scheduled-turn runner (null only in a test's lanes): read at each use. */
  readonly runner: () => WorkRunner | null;
  readonly clock: () => number;
  /** The object's write gate, which every lane write goes through a row at a time. */
  readonly write: Write;
  readonly runWakes: typeof runWakes;
  readonly runWork: typeof runWork;
}

/**
 * The lanes of the object's one schedule (`schedule.ts`). Each lane's pass is handed the
 * pass's wall-clock end (`until`) and passes it on, so no lane can outrun its hung
 * timeout; the passes write through the object's gate. The doorbell and runner are read
 * when a lane looks or runs, so one the object gains later is never missed.
 * Exported so the wiring is tested with recording passes; `OwnerDO` gives it the real ones.
 */
export function ownerLanes(deps: LaneDeps):
    Readonly<Record<"wakes" | "files" | "work" | "connections", Lane>> {
  return {
    // Lapsed claims, then due wakes and revokes; open rows count only with a doorbell.
    wakes: { next: (now) => deps.ledger.nextDue(now, deps.sender() !== null),
             run: (_now, until) => deps.runWakes(deps.ledger, deps.sender(), deps.clock,
                                                 deps.write, WAKES_PER_ALARM, WAKE_SEND_MS,
                                                 until) },
    files: { next: (now) => deps.files.nextAlarm(now),
             run: (now, until) => deps.files.collect(now, until) },
    // Stamped scheduled work: lapsed claims, then, with a runner, due work rechecked
    // against the authority it was made under.
    work: { next: (now) => deps.work.nextDue(now, deps.runner() !== null),
            run: (_now, until) => deps.runWork(deps.work, deps.runner(), deps.clock,
                                               deps.write, WORK_PER_PASS, WORK_HANDOFF_MS,
                                               until) },
    // Connection housekeeping: expired flows, unclaimed pending rows, provider revokes.
    connections: { next: (now) => deps.connections.nextDue(now),
                   run: (now, until) => deps.connections.run(now, until) },
  };
}

/**
 * The `work` lane's runner (agent research §3): a due turn handed to pi in the thread its
 * work named when it was made, since pi dedupes a submission per conversation. It marks
 * the row before any effect (a lapsed claim's mark throws, and nothing is submitted),
 * then submits with the row's `work_id` as the operation id, queued behind a busy run, and
 * returns; pi runs the turn, and its receipts are found by `work_id`. A second hand-off of
 * the same work is pi's duplicate (`accepted: false`): handed all the same, never run
 * twice. A thread that is gone, or whose stamp is not the work's or no longer stands,
 * fails the work before its mark.
 */
export function workRunner(threads: ThreadReader,
                           harness: Pick<OwnerAgent["harness"], "submit">,
                           authority: Pick<Authority, "refusal">): WorkRunner {
  return {
    handOff: async (target, mark) => {
      const turn = workTurn(target.payload);
      const thread = threads.get(turn.thread);
      if (thread === null) {
        return { state: "failed", reason: "no_thread" };
      }
      if (thread.client !== (target.stamp?.client ?? null)
          || thread.generation !== (target.stamp?.generation ?? null)
          || authority.refusal(threadAuthority(thread), []) !== undefined) {
        return { state: "failed", reason: "thread_authority" };
      }
      await mark();
      const receipt = await harness.submit(turn.input, {
        session: String(thread.conversation_id), operationId: target.work_id,
        whenBusy: "followUp",
        entry: { kind: "ta.work", data: { work_id: target.work_id,
                                          client: target.stamp?.client ?? null } },
      });
      return receipt.accepted ? { state: "handed" } : { state: "handed", reason: "duplicate" };
    },
  };
}

/** Builds the object's agent from its parts. */
export type AgentFactory = (parts: AgentParts) => OwnerAgent;

export class OwnerDO extends DurableObject<OwnerEnv> {
  /** The one write gate: every owner write is a unit of the queue pi's commits use. */
  readonly #gate: WriteGate;
  /** The storage as the modules see it: the gate's view. */
  readonly #storage: OwnerStorage;
  readonly #collections: Collections;
  readonly #policy: Policy;
  readonly #grants: Grants;
  readonly #sessions: Sessions;
  readonly #ledger: Ledger;
  readonly #files: Files;
  readonly #work: Scheduled;
  readonly #threads: Threads;
  readonly #registrations: Registrations;
  readonly #notices: Notices;
  readonly #connections: Connections;
  readonly #usage: ModelUsage;
  /** Live model events for the owner's views, in memory only (`live-tee.ts`). */
  readonly #tee = new LiveTee();
  /** The agent's settings from owner policy, kept for the policy generation they are of. */
  #agentSettings: { generation: number; settings: HarnessSettings } | null = null;
  readonly #schedule: Schedule;
  /** pi-durable on PiHarness, a capability of the one Lifecycle (`agent.ts`). */
  readonly #agent: OwnerAgent;
  readonly #lifecycle: Lifecycle<OwnerEnv>;
  /**
   * What rings outside agents' doorbells, called in process by the `wakes` lane. None
   * until the doorbell slice (tasks 7) constructs one here; until then the lane only
   * recovers lapsed claims, and open wakes and revokes stay pending without a job.
   */
  readonly #sender: WakeSender | null = null;
  /** What hands due scheduled turns to pi, called in process by the `work` lane. */
  readonly #runner: WorkRunner;

  /**
   * The runtime constructs it with `ctx` and `env` alone, so the agent is the production
   * one, on the gateway provider; the workerd tests hand a factory that opens it on pi-ai's
   * faux models.
   */
  constructor(ctx: DurableObjectState, env: OwnerEnv, agent: AgentFactory = gatewayAgent) {
    super(ctx, env);
    this.#gate = new WriteGate(ctx.storage);
    const storage = this.#gate.storage;
    const write = this.#gate.write;
    this.#storage = storage;
    this.#collections = new Collections(storage);
    this.#policy = new Policy(storage);
    this.#grants = new Grants(storage, this.#policy);
    this.#sessions = new Sessions(storage);
    this.#ledger = new Ledger(storage, this.#grants);
    this.#files = new Files(storage, env.FILES,
                            () => fileSettings(this.#policy.read().document), write);
    this.#threads = new Threads(storage);
    this.#work = new Scheduled(storage, this.#grants, this.#threads);
    this.#registrations = new Registrations(storage, this.#grants);
    this.#notices = new Notices(storage);
    const owner = () => {
      const named = ctx.id.name;
      if (named === undefined) {
        throw new Error("this object is not named by an owner");
      }
      return named;
    };
    this.#connections = new Connections(storage, write, () => env.GATEWAY, owner,
                                        this.#notices, clock);
    this.#usage = new ModelUsage(storage, () => {
      const { generation, document } = this.#policy.read();
      return { generation, settings: modelSettings(document) };
    });
    // The lanes of the one schedule. There is no global tick; each owner's object
    // schedules only itself.
    this.#schedule = new Schedule(ownerLanes({
      ledger: this.#ledger, files: this.#files, work: this.#work,
      connections: this.#connections,
      sender: () => this.#sender, runner: () => this.#runner, clock, write, runWakes, runWork,
    }), clock, write);
    const provider = new GatewayProvider({
      connections: this.#connections, usage: this.#usage, write, gateway: () => env.GATEWAY,
      owner, tee: this.#tee, clock,
      authority: (session) => this.#agent.modelAuthority(session),
    });
    const authority = new Authority({
      grants: { standing: (client, generation, at) => {
        const grant = this.#grants.standing(client, generation, at);
        return { mode: grant.mode, holds: (scope) => holds(grant, scope) };
      } },
      receipt: (request) => write(() => this.#policy.refusal("tool_call", request, clock(),
                                                             AUTHORITY_RULE)),
    });
    this.#agent = agent({ files: this.#files, notices: this.#notices, write,
                          rearm: () => this.#schedule.sync(),
                          policy: () => this.#agentPolicy(), provider, tee: this.#tee,
                          authority, threads: this.#threads, platform: {
                            policy: () => this.#policy.projection(owner()),
                            propose: (change, requestId, proposer) =>
                              this.#policy.propose(change, requestId, proposer, clock()),
                            work: (client, limit) => this.#work.of(client, limit),
                            fileUsage: () => this.#files.usage(),
                          } });
    this.#runner = workRunner(this.#threads, this.#agent.harness, authority);
    this.#lifecycle = new Lifecycle<OwnerEnv>(this).use(this.#schedule).use(this.#agent.harness);
    void ctx.blockConcurrencyWhile(async () => {
      await write(() => migrate(storage));
      // The schedule as stored, for this code's lanes and sender: a deploy that adds a
      // doorbell gives the wakes held while there was none their job.
      await this.#schedule.sync();
    });
  }

  /**
   * The runtime's alarm, which Lifecycle owns: it starts the Lifecycle (pi's open among
   * its capabilities'), drives the due jobs, then re-arms.
   */
  override async alarm(): Promise<void> {
    await this.#lifecycle.alarm();
  }

  /**
   * The part of owner policy that is pi's settings: pi reads its settings at every use
   * (some on every commit), so the document is read again only when its generation moved.
   */
  #agentPolicy(): HarnessSettings {
    const generation = this.#policy.generation();
    if (this.#agentSettings?.generation !== generation) {
      this.#agentSettings = { generation,
                              settings: agentPolicy(this.#policy.read().document) };
    }
    return this.#agentSettings.settings;
  }

  /** A door's synchronous body, as one unit of the write gate. */
  #door<T>(act: () => T): Promise<T> {
    return this.#gate.write(act);
  }

  /** A door's unit, then the one schedule synced from the rows it changed. */
  async #synced<T>(act: () => T): Promise<T> {
    const done = await this.#door(act);
    await this.#schedule.sync();
    return done;
  }

  /**
   * An account call's owner, origin and grant. The account is its owner's own: the
   * caller must be the owner's account, acting itself (`origin` null) or through a
   * verified outside client, which is admitted here against its grant (P0-3) before any
   * door acts. A refusal here leaves no receipt: the call never reached the account.
   */
  #account(binding: Binding, serves: Serves = {}): Account {
    const { owner, caller } = this.#bind(binding);
    if (!("origin" in binding)) {
      throw new Invalid("invalid outside origin");
    }
    const origin = outsideOrigin(binding.origin);
    if (caller !== owner && !(serves.visitors === true && origin === null)) {
      throw new Refused("the account answers its owner's own callers only");
    }
    const signedIn = binding.authenticated_at ?? null;
    if (signedIn !== null && (typeof signedIn !== "number" || !Number.isSafeInteger(signedIn)
                              || signedIn < 0)) {
      throw new Invalid("authenticated_at is a time in whole seconds");
    }
    if (origin === null) {
      return { owner, caller, origin, grant: null };
    }
    // The owner's cookie rides only the owner's own browser requests (tasks 2); an
    // outside client's call carrying it is a binder bug or a replay, refused before any
    // grant is recorded. Only an API key's registration carries both: the binder checked
    // the owner holds the key, and the object re-derives its origin from the key id.
    if (binding.session !== undefined && serves.registering !== true) {
      throw new Refused(OUTSIDE_SESSION);
    }
    const source = registrant(binding.source, origin);
    if (binding.session !== undefined && source.kind !== "api_key") {
      throw new Refused(MCP_REGISTERS_BY_CARD);
    }
    return { owner, caller, origin,
             grant: this.#grants.admit(origin.client, source.kind, signedIn, clock()) };
  }

  /** The owner itself, on its own account, never an outside client. */
  #owner(binding: Binding, action: string): Account {
    const account = this.#account(binding);
    if (account.origin !== null) {
      throw new Refused(`only the owner may ${action}`);
    }
    return account;
  }

  /**
   * A widening: the owner's live session, checked against the object's own session row
   * (S30, N10). `owner` names what only the owner itself, never an outside client, may
   * do in it. A refusal after the account is bound leaves a refusal receipt of what was
   * asked, written once the refused change has rolled back.
   */
  #widening<T>(binding: Binding, action: string, request: unknown, owner: string | null,
               act: (account: Account, sessionId: string) => T, serves: Serves = {}): T {
    const account = this.#account(binding, serves);
    try {
      // An outside client holds no owner session, so what only the owner may do is
      // refused as that before the session is looked for.
      if (owner !== null && account.origin !== null) {
        throw new Refused(`only the owner may ${owner}`);
      }
      return act(account, this.#sessions.live(binding.session, clock()));
    } catch (error) {
      if (error instanceof Refused || error instanceof Invalid) {
        try {
          this.#policy.refusal(action, { request, origin: account.origin }, clock());
        } catch {
          console.error("owner-session refusal receipt unavailable");  // refuse regardless
        }
      }
      throw error;
    }
  }

  /**
   * A scoped outside client reaches its own mailbox only with `capability` granted, as
   * the `main` agent, in `center` (default: the command center its one enabled agent is
   * registered in). `full` and the owner pass.
   */
  #scope(account: Account, capability: Capability, center?: unknown): void {
    if (account.grant?.mode !== "scoped") {
      return;
    }
    let universe = center;
    if (universe === undefined) {
      const mine = this.#ledger.agents(account.origin)
        .filter((agent) => agent.disabled_reason === null);
      if (mine.length !== 1) {
        throw new Refused("this outside identity is not one enabled agent");
      }
      universe = mine[0]!.command_center_id;
    }
    if (typeof universe !== "string"
        || !holds(account.grant, { universe, agent: "main", capability })) {
      throw new Refused("outside client capability not granted");
    }
  }

  // -- SESSIONS: the owner's protected interactive sessions ---------------------

  /** A verified interactive sign-in opens a session; its cookie is returned once. */
  sessionOpen(binding: Binding): Promise<{ session: string; expires_at: number }> {
    return this.#door(() => {
      this.#owner(binding, "open its session");
      return this.#sessions.open(binding.authenticated_at, clock());
    });
  }

  sessionExtend(binding: Binding): Promise<{ expires_at: number }> {
    return this.#door(() => {
      this.#owner(binding, "renew its session");
      return this.#sessions.extend(binding.session, clock());
    });
  }

  /** One session signed out, and the connection flows it began cancelled with it. */
  sessionClose(binding: Binding): Promise<{ closed: number }> {
    return this.#synced(() => {
      this.#owner(binding, "sign out");
      const id = this.#sessions.id(binding.session);
      const closed = this.#sessions.close(binding.session);
      if (id !== null) {
        this.#connections.cancelFlows(id);
      }
      return closed;
    });
  }

  /**
   * Every session signed out: account revocation and deletion (`revoke_owner`), every open
   * connection flow cancelled in the same unit, so a late callback finds none.
   */
  sessionCloseAll(binding: Binding): Promise<{ closed: number }> {
    return this.#synced(() => {
      this.#owner(binding, "sign out");
      this.#connections.cancelFlows(null);
      return this.#sessions.closeAll();
    });
  }

  // -- OWNER_POLICY ------------------------------------------------------------

  policyRead(binding: Binding): Promise<Projection> {
    return this.#door(() => {
      const { owner } = this.#owner(binding, "read its policy");
      return this.#policy.projection(owner);
    });
  }

  policyReceipts(binding: Binding): Promise<{ receipts: Receipt[] }> {
    return this.#door(() => {
      this.#owner(binding, "read its policy");
      return { receipts: this.#policy.receipts() };
    });
  }

  /**
   * The owner's write, in its session; with `proposal`, a held card's one apply. A new
   * files retention is applied to rows already kept by housekeeping, so the schedule is
   * synced after it (the `files` lane re-applies it). Once it committed, it ends the
   * agent's in-flight work, its live views and its pending waits (`OwnerAgent.endAll`):
   * it opens pi to do so, and waits up to `END_WAIT_MS` for the work to end. Should that
   * fail, the door rejects, though the write is committed; the work it missed is held to
   * the new policy anyway, and its model calls to the new generation.
   */
  async policyWrite(binding: Binding, request: PolicyWriteRequest): Promise<Projection> {
    const done = await this.#synced(() => this.#widening(binding, "write", request,
                                                         "write its policy", ({ owner }, id) => {
      const r = policyFields(request, ["expected_generation", "policy"], ["proposal"]);
      const written = this.#policy.write(id, r["expected_generation"], r["policy"],
                                         r["proposal"] ?? null, clock());
      return { owner, generation: written.generation, digest: written.digest,
               policy: written.document };
    }));
    await this.#agent.endAll();
    return done;
  }

  /**
   * An agent's proposal, the owner's or an admitted outside client's; never applied. A
   * scoped client proposes only with `propose_policy` granted where its agent is.
   */
  policyPropose(binding: Binding, request: ProposeRequest):
      Promise<{ request_digest: string; generation: number }> {
    return this.#door(() => {
      const account = this.#account(binding);
      const r = policyFields(request, ["change"], ["request_id"]);
      this.#scope(account, "propose_policy");
      return this.#policy.propose(r["change"], r["request_id"] ?? null,
                                  account.origin?.client ?? "owner", clock());
    });
  }

  /** The pending card the owner is shown, and the digest its apply must name. */
  policyProposal(binding: Binding, request: CardRequest):
      Promise<{ change: Change; digest: string }> {
    return this.#door(() => {
      this.#owner(binding, "read its policy");
      const r = policyFields(request, ["request_id"]);
      const held = this.#policy.held(r["request_id"], clock());
      if (held === null) {
        throw new Refused("no pending proposal for this card");
      }
      return held;
    });
  }

  /**
   * Owner policy (W3): an agent's hourly wake cap and batch window, kept on its registry
   * row where the ledger reads them, and changed like any policy write: in the owner's
   * session, against the policy generation, receipted.
   */
  policyWakeLimits(binding: Binding, request: WakeLimitsRequest):
      Promise<{ agent: Agent; generation: number }> {
    return this.#door(() => {
      return this.#widening(binding, "wake_limits", request, "change its agents' policy",
                            (_account, id) => {
        const r = policyFields(request, ["agent_id", "max_wakes_per_hour", "batch_window_s",
                                         "expected_generation"]);
        return this.#storage.transactionSync(() => {
          const agent = this.#ledger.setWakeLimits(r["agent_id"], r["max_wakes_per_hour"],
                                                   r["batch_window_s"]);
          const limits = { agent_id: agent.agent_id, max_wakes_per_hour: agent.max_wakes_per_hour,
                           batch_window_s: agent.batch_window_s };
          const generation = this.#policy.changed(id, r["expected_generation"], "wake_limits",
                                                  limits, agent.agent_id, clock());
          return { agent, generation };
        });
      });
    });
  }

  // -- OUTSIDE_CLIENTS: grants (P0-3) ---------------------------------------------

  grantList(binding: Binding): Promise<{ clients: Client[] }> {
    return this.#door(() => {
      this.#owner(binding, "read its outside clients");
      return { clients: this.#grants.list() };
    });
  }

  /**
   * The owner's widening, in its session; the client's agents' wakes end with it, and once
   * it committed, the client's agent work (`OwnerAgent.endClient`), as `grantRevoke`
   * ends it.
   */
  async grantSet(binding: Binding, request: GrantRequest):
      Promise<{ client: string; mode: string; scopes: Scope[]; generation: number }> {
    const changed = await this.#synced(() => this.#widening(binding, "outside_set", request,
                                                            "change an outside client's grant",
                                                            (_account, id) => {
      const r = policyFields(request, ["client", "source", "mode", "scopes",
                                       "expected_generation", "name"]);
      return this.#storage.transactionSync(() => {
        const set = this.#grants.set(id, r as unknown as GrantRequest, clock());
        return { ...set, threads: this.#endClient(set.client) };
      });
    }));
    const { threads, ...answer } = changed;
    await this.#agent.endClient(answer.client, threads);
    return answer;
  }

  /**
   * A narrowing the owner's account makes without a session, as a key's revoke does; the
   * client's agent work ends once it committed: the door opens pi when the client had
   * threads, and waits up to `END_WAIT_MS` for that work to end. Should that fail, the
   * door rejects, though the revoke is committed; what it missed is refused anyway, at
   * every tool operation and model call (`authority.ts`).
   */
  async grantRevoke(binding: Binding, request: ClientRequest):
      Promise<{ client: string; revoked: boolean; generation: number }> {
    const result = await this.#synced(() => {
      this.#owner(binding, "revoke an outside client's grant");
      const r = policyFields(request, ["client"]);
      return this.#storage.transactionSync(() => {
        const revoked = this.#grants.revoke(r["client"], clock());
        return { ...revoked, threads: this.#endClient(revoked.client) };
      });
    });
    const { threads, ...answer } = result;
    await this.#agent.endClient(answer.client, threads);
    return answer;
  }

  /**
   * Forget a revoked API key's grant (slice 3's review L1): the binder calls it once the
   * directory has deleted the key (tasks 2). A narrowing, since a key with no row is
   * refused, so the owner's account makes it without a session. Never an MCP client's.
   */
  grantForget(binding: Binding, request: ClientRequest):
      Promise<{ client: string; forgotten: boolean }> {
    return this.#door(() => {
      this.#owner(binding, "forget an outside client's grant");
      const r = policyFields(request, ["client"]);
      return this.#grants.forgetKey(r["client"], clock());
    });
  }

  /**
   * The owner, in its session, forgets revoked clients to make room under its bound: a
   * widening, so it is the only act that lifts an MCP revoke, and even then a forgotten
   * MCP client comes back only from a sign-in after the revoke (`grants.ts`).
   */
  grantForgetRevoked(binding: Binding, request: ForgetRequest):
      Promise<{ forgotten: string[]; mcp_fence: number }> {
    return this.#door(() => {
      return this.#widening(binding, "outside_forget", request,
                            "forget its revoked outside clients", (_account, id) => {
        const r = policyFields(request, ["clients"]);
        return this.#grants.forgetRevoked(id, r["clients"], clock());
      });
    });
  }

  /**
   * A grant change ends what its client set going (P0-3's M3): its agents' open wakes and
   * callbacks, its pending scheduled work and its held registrations, in the caller's
   * transaction, and its threads, whose conversations it answers: their live views close,
   * and its agent work ends, after that commits (`OwnerAgent.endClient`).
   */
  #endClient(client: string): number[] {
    this.#ledger.suppressClient(client, clock());
    this.#work.endClient(client, clock());
    this.#registrations.endClient(client);
    return this.#threads.endClient(client);
  }

  // -- OUTSIDE_AGENTS ---------------------------------------------------------

  /** The owner sees every agent; an outside caller only its own identity's. */
  outsideAgents(binding: Binding): Promise<{ agents: Agent[] }> {
    return this.#door(() => {
      const { origin } = this.#account(binding);
      return { agents: this.#ledger.agents(origin) };
    });
  }

  /**
   * Registering an agent needs the owner's protected session (S30), in one of two forms.
   * The owner itself applies an MCP client's held registration (`CardApply`): the card
   * the client filed on its own verified call, which captured its origin. Or the owner's
   * browser registers an agent for an API key it holds, naming the key's identity
   * (`origin`, re-derived from the key id): the one door an outside origin reaches
   * together with the owner's session.
   */
  registerAgent(binding: Binding, request: RegisterRequest | CardApply): Promise<Agent> {
    return this.#synced(() => this.#widening(binding, "OUTSIDE_AGENTS.register", request, null,
                                             ({ origin }) => {
      if (origin === null) {
        const r = ledgerFields(request, ["card_id", "digest"]);
        return this.#storage.transactionSync(() => {
          const card = this.#registrations.take(r["card_id"], r["digest"], clock());
          return this.#ledger.register(card.origin, card.source,
                                       card.request as unknown as RegisterRequest);
        });
      }
      const r = ledgerFields(request, ["command_center_id", "display_name", "delivery_method",
                                       "recipe_id"]);
      return this.#ledger.register(origin, binding.source, r as unknown as RegisterRequest);
    }, { registering: true }));
  }

  /**
   * An MCP client files the registration of its agent on its own verified call, so the
   * object captures the origin the binder verified; the owner applies it in session
   * (`registerAgent`). Never applied here. Filing again replaces the identity's card.
   */
  registerPropose(binding: Binding, request: RegisterRequest):
      Promise<{ card_id: string; digest: string; expires_at: number }> {
    return this.#door(() => {
      const account = this.#account(binding);
      if (account.origin === null || account.grant === null) {
        throw new Refused("only an outside client files its own agent's registration");
      }
      if (registrant(binding.source, account.origin).kind !== "mcp") {
        throw new Refused("an API key's agent registers from the owner's browser with the key");
      }
      const r = ledgerFields(request, ["command_center_id", "display_name", "delivery_method",
                                       "recipe_id"]);
      return this.#registrations.file(account.origin, (binding.source as Source).ref, {
        client: account.origin.client, generation: account.grant.generation,
        authenticated_at: binding.authenticated_at ?? null }, r, clock());
    });
  }

  /** The pending registration card the owner is shown, and the digest its apply names. */
  registerProposal(binding: Binding, request: RegistrationCardRequest): Promise<Card> {
    return this.#door(() => {
      this.#owner(binding, "read its agents' registrations");
      const r = ledgerFields(request, ["card_id"]);
      const card = this.#registrations.card(r["card_id"], clock());
      if (card === null) {
        throw new Refused("no pending registration for this card");
      }
      return card;
    });
  }

  /**
   * The owner disables any agent in its session. An outside client cannot reach it: it
   * holds no owner session, and its call never carries one.
   */
  disableAgent(binding: Binding, request: DisableRequest): Promise<Agent> {
    return this.#synced(() => this.#widening(binding, "OUTSIDE_AGENTS.disable", request, null,
                                             ({ origin }) => {
      const r = ledgerFields(request, ["agent_id", "reason"]);
      return this.#ledger.disable(r["agent_id"], r["reason"], origin);
    }));
  }

  /** Only the owner itself, in its session, deletes an agent. */
  deleteAgent(binding: Binding, request: AgentRequest):
      Promise<{ agent_id: string; messages: number }> {
    return this.#synced(() => this.#widening(binding, "OUTSIDE_AGENTS.delete", request,
                                             "change its agents' policy", () => {
      const r = ledgerFields(request, ["agent_id"]);
      return this.#ledger.remove(r["agent_id"]);
    }));
  }

  // -- ROOM_POST and MAILBOX_RECEIPT -------------------------------------------

  roomPost(binding: Binding, request: PostRequest): Promise<Posted> {
    return this.#synced(() => {
      const account = this.#account(binding);
      const r = ledgerFields(request, ["room_id", "client_message_id", "body", "parent_id"]);
      this.#scope(account, "write_graph");
      return this.#ledger.post(account.origin, r["room_id"], r["client_message_id"],
                               r["body"], r["parent_id"]);
    });
  }

  // -- MODEL CONNECTIONS: sealed credentials, flows and spend (tasks 4 G2) ------------
  //
  // Each door is the owner's own, in its live session: an outside client's call carries
  // no session and is refused, with a refusal receipt. The binder seals and exchanges with
  // the gateway between them; no plaintext secret reaches the object.

  /** A pending connection for the binder to seal a credential for; for OAuth, its flow. */
  connectionPrepare(binding: Binding, request: ConnectionPrepareRequest): Promise<Prepared> {
    return this.#synced(() => this.#widening(binding, "connection_prepare", request,
                                             "connect a model source", (_account, id) =>
      this.#connections.prepare(id, request, clock())));
  }

  /** The sealed blob the gateway returned, and the models its setup check listed. */
  connectionCommit(binding: Binding, request: ConnectionCommitRequest): Promise<ConnectionView> {
    return this.#synced(() => this.#widening(binding, "connection_commit", request,
                                             "connect a model source",
                                             () => this.#connections.commit(request, clock())));
  }

  /** A connection unusable at once; its credential deleted here, and at its provider. */
  connectionRevoke(binding: Binding, request: ConnectionRequest):
      Promise<{ connection: string; revoked: boolean; provider_revoke: boolean }> {
    return this.#synced(() => this.#widening(binding, "connection_revoke", request,
                                             "revoke a model source",
                                             () => this.#connections.revoke(request, clock())));
  }

  /** Every connection, newest first, never its blob. */
  connectionList(binding: Binding): Promise<{ connections: ConnectionView[] }> {
    return this.#door(() => this.#widening(binding, "connection_list", null,
                                           "read its model sources",
                                           () => ({ connections: this.#connections.list() })));
  }

  /** The callback's state, in the session that began the flow: its verifier, used up. */
  flowFinish(binding: Binding, request: FlowFinishRequest):
      Promise<{ connection: string; version: number; verifier: string; redirect_uri: string;
                source: string }> {
    return this.#synced(() => this.#widening(binding, "flow_finish", { state: "[state]" },
                                             "connect a model source", (_account, id) =>
      this.#connections.flowFinish(id, request, clock())));
  }

  /**
   * The owner's model usage as pi records it: pi's `pi.usage` summed over its
   * conversations, and the metrics pi and pi-ai recorded through the telemetry adapter
   * (`OwnerAgent.usage`), for G5's measurement. The owner's alone; no cap reads it.
   */
  async usageMetrics(binding: Binding): Promise<UsageView & { owner: string }> {
    const { owner } = await this.#door(() => this.#owner(binding, "read its model usage"));
    return { owner, ...await this.#agent.usage() };
  }

  /** The owner's newest model-call receipts: what each call reserved, and what it cost. */
  usageReceipts(binding: Binding): Promise<{ receipts: UsageReceipt[] }> {
    return this.#door(() => {
      this.#owner(binding, "read its model spend");
      return { receipts: this.#usage.receipts(clock()) };
    });
  }

  // -- NOTICES: what the agent's runtime reported to the owner ----------------------

  /** The owner's notices, newest first (`notices.ts`). */
  noticeList(binding: Binding): Promise<{ notices: Notice[] }> {
    return this.#door(() => {
      this.#owner(binding, "read its notices");
      return { notices: this.#notices.list() };
    });
  }

  mailboxRead(binding: Binding, request: ReadRequest): Promise<MailPage> {
    return this.#door(() => {
      const account = this.#account(binding);
      const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes"]);
      this.#scope(account, "read_graph");
      return this.#ledger.read(account.origin, r["limit"], r["max_bytes"] ?? null,
                               r["pad_bytes"] ?? 0);
    });
  }

  mailboxAck(binding: Binding, request: AckRequest): Promise<{ reader: string; through: number }> {
    return this.#door(() => {
      const account = this.#account(binding);
      const r = ledgerFields(request, ["through"]);
      this.#scope(account, "write_graph");
      return this.#ledger.ack(account.origin, r["through"]);
    });
  }

  /**
   * W4: a `mailbox.message` subscribe must name the caller's own ringing agent, and a
   * scoped client needs `read_graph` in that command center. The answer's `generation`
   * tags the callback the subscribe door stores, so a revoke made before it never
   * erases it.
   */
  mailboxSubscriber(binding: Binding, request: SubscriberRequest):
      Promise<{ agent_id: string; generation: number }> {
    return this.#door(() => {
      const account = this.#account(binding);
      const r = ledgerFields(request, ["command_center_id", "agent_id"]);
      this.#scope(account, "read_graph", r["command_center_id"]);
      const generation = this.#ledger.subscriber(account.origin, r["command_center_id"],
                                                 r["agent_id"]);
      return { agent_id: r["agent_id"] as string, generation };
    });
  }

  /**
   * The owner this object serves: the name it was reached by, and every other one
   * refused. The stored owner, fixed at first use, is a second check of the same.
   */
  #bind(binding: Binding): { owner: string; caller: string } {
    if (typeof binding !== "object" || binding === null) {
      throw new Invalid("invalid owner principal");
    }
    const owner = principal(binding.owner);
    const caller = principal(binding.caller);
    const named = this.ctx.id.name;
    if (named === undefined) {
      throw new Refused("this object is not named by an owner");
    }
    if (named !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    const sql = this.#storage.sql;
    const stored = sql.exec<{ owner: string }>("SELECT owner FROM object_owner").toArray()[0];
    if (stored === undefined) {
      sql.exec("INSERT INTO object_owner (only, owner) VALUES (1, ?)", owner);
    } else if (stored.owner !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    return { owner, caller };
  }

  #ownerOnly(owner: string, caller: string, action: string): void {
    if (caller !== owner) {
      throw new Refused(`only the owner may ${action} collection rows`);
    }
  }

  /**
   * A collection door's caller: the owner, a visiting owner, or an outside client the
   * owner's account acts through, admitted against its grant and held to `capability`.
   */
  #collector(binding: Binding, capability: Capability): { owner: string; caller: string } {
    const account = this.#account(binding, { visitors: true });
    this.#scope(account, capability);
    return account;
  }

  collectionWrite(binding: Binding, request: WriteRequest): Promise<{ row: Row } | Conflicted> {
    return this.#door(() => {
      const { owner, caller } = this.#collector(binding, "write_graph");
      const r = fields(request, ["collection", "row_id", "body", "expected_etag", "private"],
                       ["history_op"]);
      return conflicted(() => ({
        row: this.#collections.write(owner, caller, r["collection"] as string,
                                     r["row_id"] as string, r["body"], r["expected_etag"],
                                     r["private"], "history_op" in r ? r["history_op"] : "write"),
      }));
    });
  }

  collectionPull(binding: Binding, request: PullRequest): Promise<Page> {
    return this.#door(() => {
      const { caller } = this.#collector(binding, "read_graph");
      const r = fields(request, ["collection", "after", "limit"]);
      return this.#collections.pull(caller, r["collection"] as string, r["after"], r["limit"]);
    });
  }

  collectionGet(binding: Binding, request: KeyRequest): Promise<{ row: Row | null }> {
    return this.#door(() => {
      const { caller } = this.#collector(binding, "read_graph");
      const r = fields(request, ["collection", "row_id"]);
      return this.#collections.get(caller, r["collection"] as string, r["row_id"] as string);
    });
  }

  collectionHistory(binding: Binding, request: KeyRequest): Promise<{ entries: HistoryEntry[] }> {
    return this.#door(() => {
      const { caller } = this.#collector(binding, "read_graph");
      const r = fields(request, ["collection", "row_id"]);
      return this.#collections.history(caller, r["collection"] as string, r["row_id"] as string);
    });
  }

  collectionDelete(binding: Binding, request: DeleteRequest):
      Promise<{ deleted: number } | Conflicted> {
    return this.#door(() => {
      const { owner, caller } = this.#collector(binding, "write_graph");
      this.#ownerOnly(owner, caller, "delete");
      const r = fields(request, ["collection", "row_id", "expected_etag"]);
      return conflicted(() => this.#collections.delete(
        caller, r["collection"] as string, r["row_id"] as string, r["expected_etag"],
      ));
    });
  }

  collectionClear(binding: Binding, request: ClearRequest): Promise<{ deleted: number }> {
    return this.#door(() => {
      const { owner, caller } = this.#collector(binding, "write_graph");
      this.#ownerOnly(owner, caller, "clear");
      const r = fields(request, ["collection", "writer"]);
      return this.#collections.clear(caller, r["collection"] as string, r["writer"]);
    });
  }

  // -- FILES: the owner's file tree (plan §6) --------------------------------------
  //
  // These doors move at most two chunks a call. The HTTP and MCP doors (tasks 2) stream
  // larger files from the binder to `FILES` itself. Another RPC may run in the object
  // whenever one awaits `FILES`, so a door that crosses to it writes its account first,
  // in a unit, and its later steps through units of their own (`Files`).

  /**
   * A file door's account: the owner's own, or an outside client admitted against its
   * grant and held to `read_files` or `write_files`. No visiting owner reaches another's
   * files.
   */
  #filer(binding: Binding, capability: "read_files" | "write_files"): Account {
    const account = this.#account(binding);
    this.#scope(account, capability);
    return account;
  }

  /** A changing file door: its unit, then the one schedule synced whatever came of it. */
  async #filing<T>(act: () => T): Promise<T> {
    try {
      return await this.#door(act);
    } finally {
      await this.#schedule.sync();
    }
  }

  fileStat(binding: Binding, request: FileRequest): Promise<{ entry: Entry | null }> {
    return this.#door(() => {
      this.#filer(binding, "read_files");
      const r = fileFields(request, ["path"]);
      return { entry: this.#files.stat(r["path"]) };
    });
  }

  fileList(binding: Binding, request: FileListRequest):
      Promise<{ entries: Entry[]; next: string | null }> {
    return this.#door(() => {
      this.#filer(binding, "read_files");
      const r = fileFields(request, ["dir", "limit"], ["after"]);
      return this.#files.list(r["dir"], r["after"] ?? null, r["limit"]);
    });
  }

  /**
   * A file's entry and content: its bytes when small, else its chunk list for
   * `fileReadChunks`. With `etag` still current, only the entry (`not_modified`).
   */
  fileRead(binding: Binding, request: FileReadRequest):
      Promise<{ entry: Entry; not_modified?: true; inline?: Uint8Array; chunks?: ChunkRef[] }> {
    return this.#door(() => {
      this.#filer(binding, "read_files");
      const r = fileFields(request, ["path"], ["etag"]);
      const content: Content = this.#files.open(r["path"]);
      if (r["etag"] !== undefined && r["etag"] === content.entry.etag) {
        return { entry: content.entry, not_modified: true };
      }
      const { content_id: _id, ...shown } = content;
      return shown;
    });
  }

  /** One or two of a kept version's chunks, from `first`, so a read pins its version. */
  async fileReadChunks(binding: Binding, request: FileChunksRequest):
      Promise<{ chunks: Uint8Array[] }> {
    const reads = await this.#door(() => {
      this.#filer(binding, "read_files");
      const r = fileFields(request, ["path", "version", "first", "count"]);
      const { chunks = [] } = this.#files.openVersion(r["path"], r["version"]);
      const first = r["first"];
      const many = r["count"];
      if (typeof first !== "number" || typeof many !== "number" || !Number.isSafeInteger(first)
          || !Number.isSafeInteger(many) || first < 0 || many < 1 || many > 2
          || first + many > chunks.length) {
        throw new Invalid("first and count name 1 or 2 of the version's chunks");
      }
      return chunks.slice(first, first + many).map(({ sha256 }) => ({ sha256 }));
    });
    return { chunks: await this.#files.fetch(reads) };
  }

  /** Write a whole file of at most one chunk (4 MiB); larger ones go by upload. */
  async fileWrite(binding: Binding, request: FileWriteRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    const { account, r, content } = await this.#door(() => {
      const account = this.#filer(binding, "write_files");
      const r = fileFields(request, ["path", "expected_etag", "content"], ["mode"]);
      const content = r["content"];
      if (!(content instanceof Uint8Array) || content.byteLength > CHUNK_BYTES) {
        throw new Invalid("a written file is bytes, at most 4 MiB; larger ones are uploaded");
      }
      return { account, r, content };
    });
    try {
      // `Files.write` writes each step around its `FILES` call as a unit of its own.
      return await fileConflictedAfter(async () => ({
        entry: await this.#files.write(r["path"], content, {
          expected: r["expected_etag"], mode: r["mode"], writer: fileWriter(account),
          authorize: () => this.#filer(binding, "write_files") }),
      }));
    } finally {
      await this.#schedule.sync();
    }
  }

  /**
   * Begin an upload of one file: its size and chunk hashes, and back the ones whose bytes
   * to send.
   */
  fileUploadBegin(binding: Binding, request: UploadBeginRequest):
      Promise<{ intent_id: string; missing: string[]; expires_at: number }> {
    return this.#filing(() => {
      this.#filer(binding, "write_files");
      const r = fileFields(request, ["size", "chunks"]);
      return this.#files.begin(r["size"], r["chunks"]);
    });
  }

  /** Send an upload's chunk bytes, at most two chunks a call; R2 checks each hash. */
  async fileUploadChunks(binding: Binding, request: UploadChunksRequest):
      Promise<{ created: number }> {
    const r = await this.#door(() => {
      this.#filer(binding, "write_files");
      return fileFields(request, ["intent_id", "chunks"]);
    });
    // `Files.upload` records each chunk stored, once `FILES` took it, in a unit of its own.
    return this.#files.upload(r["intent_id"], r["chunks"]);
  }

  /** Commit an upload whose chunks are all stored as the file it declared. */
  fileUploadCommit(binding: Binding, request: UploadCommitRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "write_files");
      const r = fileFields(request, ["intent_id", "path", "expected_etag"], ["mode"]);
      return fileConflicted(() => ({
        entry: this.#files.commit(r["intent_id"], r["path"], {
          expected: r["expected_etag"], mode: r["mode"], writer: fileWriter(account) }),
      }));
    });
  }

  fileUploadAbort(binding: Binding, request: IntentRequest): Promise<{ aborted: boolean }> {
    return this.#filing(() => {
      this.#filer(binding, "write_files");
      const r = fileFields(request, ["intent_id"]);
      return this.#files.abort(r["intent_id"]);
    });
  }

  fileMkdir(binding: Binding, request: MkdirRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "write_files");
      const r = fileFields(request, ["path", "expected_etag"]);
      return fileConflicted(() => ({
        entry: this.#files.mkdir(r["path"], { expected: r["expected_etag"],
                                              writer: fileWriter(account) }),
      }));
    });
  }

  fileRemove(binding: Binding, request: RemoveRequest):
      Promise<{ removed: number } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "write_files");
      const r = fileFields(request, ["path", "expected_etag", "recursive"]);
      return fileConflicted(() => this.#files.remove(r["path"], {
        expected: r["expected_etag"], recursive: flag(r["recursive"], "recursive"),
        writer: fileWriter(account) }));
    });
  }

  fileRename(binding: Binding, request: RenameRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "write_files");
      const r = fileFields(request, ["from", "to", "expected_etag", "replace"]);
      return fileConflicted(() => ({
        entry: this.#files.rename(r["from"], r["to"], {
          expected: r["expected_etag"], replace: flag(r["replace"], "replace"),
          writer: fileWriter(account) }),
      }));
    });
  }

  fileHistory(binding: Binding, request: FileHistoryRequest):
      Promise<{ changes: FileChange[]; next: number | null }> {
    return this.#door(() => {
      this.#filer(binding, "read_files");
      const r = fileFields(request, ["path", "limit"], ["before"]);
      return this.#files.history(r["path"], r["before"] ?? null, r["limit"]);
    });
  }

  fileRestore(binding: Binding, request: RestoreRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "write_files");
      const r = fileFields(request, ["path", "version", "expected_etag"]);
      return fileConflicted(() => ({
        entry: this.#files.restore(r["path"], r["version"], {
          expected: r["expected_etag"], writer: fileWriter(account) }),
      }));
    });
  }

  /** Drop a kept version's content: the owner's own act, never an outside client's. */
  fileRedact(binding: Binding, request: VersionRequest): Promise<{ redacted: number }> {
    return this.#filing(() => {
      this.#owner(binding, "redact its file history");
      const r = fileFields(request, ["path", "version"]);
      return this.#files.redact(r["path"], r["version"]);
    });
  }

  /**
   * Drop a path's kept versions but its current one: the owner's own act, never an
   * outside client's, and never refused for a quota.
   */
  fileHistoryPurge(binding: Binding, request: FileRequest): Promise<{ purged: number }> {
    return this.#filing(() => {
      this.#owner(binding, "purge its file history");
      const r = fileFields(request, ["path"]);
      return this.#files.purge(r["path"]);
    });
  }

  fileChanges(binding: Binding, request: ChangesRequest):
      Promise<{ changes: FileChange[]; cursor: number; reset: boolean }> {
    return this.#door(() => {
      this.#filer(binding, "read_files");
      const r = fileFields(request, ["after", "limit"]);
      return this.#files.changes(r["after"], r["limit"]);
    });
  }

  fileUsage(binding: Binding): Promise<Usage> {
    return this.#door(() => {
      this.#filer(binding, "read_files");
      return this.#files.usage();
    });
  }

  // The sync lease is the owner's own: the sandbox's sync agent reaches it through the
  // binder with a lease token (tasks 2), as the owner, never as an outside client.

  fileLeaseAcquire(binding: Binding, request: { holder: string }): Promise<Lease> {
    return this.#door(() => {
      this.#owner(binding, "hold the file sync lease");
      const r = fileFields(request, ["holder"]);
      return this.#files.leaseAcquire(r["holder"]);
    });
  }

  fileLeaseRenew(binding: Binding, request: LeaseRequest): Promise<Lease> {
    return this.#door(() => {
      this.#owner(binding, "hold the file sync lease");
      const r = fileFields(request, ["lease_id", "epoch"]);
      return this.#files.leaseRenew(r["lease_id"], r["epoch"]);
    });
  }

  fileLeaseRelease(binding: Binding, request: LeaseRequest): Promise<{ released: boolean }> {
    return this.#door(() => {
      this.#owner(binding, "hold the file sync lease");
      const r = fileFields(request, ["lease_id", "epoch"]);
      return this.#files.leaseRelease(r["lease_id"], r["epoch"]);
    });
  }

  fileSyncCommit(binding: Binding, request: SyncCommitRequest):
      Promise<{ outcomes: SyncOutcome[] }> {
    return this.#filing(() => {
      this.#owner(binding, "hold the file sync lease");
      const r = fileFields(request, ["lease_id", "epoch", "ops"]);
      return this.#files.syncCommit({ lease_id: r["lease_id"], epoch: r["epoch"],
                                      ops: r["ops"] });
    });
  }
}
