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
 * Worker, which itself binds no Durable Object and no service, `GATEWAY`, the model
 * gateway, which binds nothing at all, and `HOOKS`, the hooks Worker, which binds only its
 * own class (`codemode.ts`: code mode's VMs run there, never here); it binds no Durable
 * Object namespace; the runtime hands it no `ctx.exports` (`disable_ctx_exports`); and
 * its source never names `exports` or imports `env` (the config and self-reach guards). The owner's file bytes reach the bucket only
 * through `FILES`, under the owner's namespace, which only this object holds
 * (`files.ts`). The module keeps no mutable state at top level (the module-state
 * guard), since objects of one class can share an isolate.
 *
 * Every public method is an RPC any binding can call, so the class has no public method
 * but its doors (`RPC_METHODS` in the tests pins the list). Its timed work is the one
 * schedule's (`schedule.ts`): lanes on the Agents SDK Lifecycle job queue, which owns the
 * object's single physical alarm, beside the erasure's job. The object is a plain Durable
 * Object that holds a Lifecycle, never the SDK's `Agent`, and installs none of Lifecycle's
 * runtime handlers: `alarm` hands the runtime's alarm to it, and the owner app's sockets
 * (`relay.ts`) are the SDK's `WebSockets` capability on the same Lifecycle, reached by the
 * object's `fetch`, which accepts only the binder's upgrade (a Workers RPC cannot carry a
 * socket), and by the three `webSocket*` reserved methods, each handed to Lifecycle's
 * method of the same name. No agent loop runs here: agents run on unmodified engines
 * (ADR-034), in the owner's sandbox or on their vendor's platform, and no package's code
 * runs in this object.
 * Claiming and settling wakes is the `wakes` lane's own, in process, with the doorbell's
 * {@link WakeSender}; file housekeeping (expired uploads and temporary directories,
 * history prunes, garbage chunk deletes) is the `files` lane's. No door reaches either.
 *
 * The owner's model connections (`connections.ts`) hold only blobs the gateway sealed;
 * their doors are the owner's, in its live session. The sandbox's model calls reach a
 * connection only through the egress door, admitted and charged here (`egress.ts`) against
 * the owner's model policy; the `connections` lane sweeps expired flows and asks providers
 * to revoke what the owner revoked.
 *
 * Every owner write goes through the object's write gate (`gate.ts`) as one synchronous
 * unit: each door's synchronous body is one unit (`#door`), a door that then crosses to
 * `FILES` or the gateway writes its later steps through units of their own, and each lane
 * writes a row at a time. The modules see the storage only through the gate's view, which
 * refuses a write outside a unit. Reads are direct.
 *
 * The run doors (`agentSubmit`, `agentRun`, `agentStatus`, `agentCancel`; slice R) start,
 * read and cancel runs (`runs.ts`): an engine's run in the owner's sandbox, admitted here in
 * one unit against the owner's admission, the agent, the tier's simultaneous runs, the
 * owner's own limits and the initiator's stamp, its source chosen, its row, grant and lease
 * written, then started through the `SANDBOX` interface (`sandbox.ts`), absent until slice
 * S binds it, so until then every run is refused loudly. A run's engine calls back on the
 * egress host: its MCP door, every door held to its run grant's scopes (a run binding,
 * `#runAccount`), and its event door (`runEvents`), whose frames the object reads by the
 * engine's record and relays to the owner's app sockets subscribed to that conversation.
 *
 * Erasing the owner (`erasure.ts`, `erase.ts`) is an internal path, which no door reaches
 * until the erasure door (slice 10): once it marks the owner, every door refuses
 * (`ERASING`, in the binding check every door's first unit runs) and no lane runs; it ends
 * every run, then wipes the owner whole, leaving only the erased admission row
 * (`admission.ts`). An object that finds that tombstone as it
 * starts migrates nothing and starts no Lifecycle, and every door refuses it
 * (`OWNER_ERASED`), so a stale `getByName` never makes owner state again.
 *
 * The owner's admission (`admission.ts`) is checked in the same binding check: a suspended
 * owner's every door refuses, and so do the lanes with `require_work` semantics (the
 * `wakes` lane's claim). Only the platform, as the reserved `PLATFORM_CALLER`, sets it, at
 * `admissionSet`, the one door that caller reaches.
 */
import { createHash, randomBytes } from "node:crypto";

import { Lifecycle } from "agents/lifecycle";
import { DurableObject } from "cloudflare:workers";

import { LEASE_LIVED, mintToken } from "../../doors/src/routing-mac";
import {
  bundledSources, CREDENTIAL_KINDS, type CredentialKind, runEnv, SourceRecordError,
} from "../../doors/src/sources";

import {
  admission, type Admission, doorRefusal, type DoorClass, OWNER_ERASED, OWNER_SUSPENDED,
  ownerTier, PLATFORM_CALLER, PLATFORM_ONLY, PLATFORM_RESERVED, setAdmission, setTier,
  tombstoned, workRefusal, writeTombstone,
} from "./admission";
import { AGENT_UNDEFINED, type AgentFile, agentFile, agentFilePath } from "./agent-file";
import { agentId, AUTHORITY_CHANGED, type Stamped } from "./authority";
import { type CloseKind, OWNERS_SOCKET, Relay, type SocketState } from "./relay";
import { type Ask, CONVERSATION_BUSY, SIGNIN_REQUIRED, type LiveRun, type PostedFrame, type RunReceipt, Runs,
         RUN_ENDED, type RunSchedule, scheduleAuthority, secretHash } from "./runs";
import { ENGINE_SIGNINS, SIGNIN_LIST_CHARS, SIGNIN_MODELS_CHARS } from "./run-rows";
import { EVENT_FRAMES_MAX, eventsBounded, FRAME_UNITS_MAX, PostGate } from "./run-events";
import { SANDBOX_ABSENT, type SandboxService, started } from "./sandbox";
import { TICKET_REFUSED } from "./socket-shape";
import { Tickets } from "./tickets";
import {
  keepTiers, keptTiers, PLATFORM_TIER_ONLY, TIER_RUNS, type TierName, tierNumbers,
} from "./tier";
import { Bodies, type Placed, type Placing } from "./bodies";
import {
  checkBody, Collections, Conflict, type HistoryEntry, type Page, type Row,
} from "./collections";
import {
  CONNECTION_UNAVAILABLE, type ConnectionView, Connections, CREDENTIAL_EXPIRED,
  type GatewayService, type ModelInfo, type Prepared,
} from "./connections";
import { Invalid, Refused } from "./errors";
import {
  type Change as FileChange, CHUNK_BYTES, type ChunkRef, type ChunkStore, type Content,
  type Entry, FileConflict, Files, type Lease, OWNER_AUTHORITY, type SandboxLease, type SyncOp,
  type SyncOutcome, type Usage,
} from "./files";
import { type OwnerStorage, type Write, WriteGate } from "./gate";
import { type Capability, type Client, type Grant, Grants, holds, type Scope } from "./grants";
import type { HooksService } from "./codemode";
import {
  type Agent, checkMessage, clock, Ledger, type MailPage, OWNER, type Origin, outsideOrigin,
  type Posted, registrant, type Source,
} from "./ledger";
import { type Notice, Notices } from "./notices";
import { Erasure } from "./erase";
import { ERASE_RETRY_MS, ERASING, erasing, wiped } from "./erasure";
import {
  type Change, fileLimits, modelSettings, Policy, type PolicyDocument, type Projection,
  type Receipt,
} from "./policy";
import { type Card, Registrations } from "./registrations";
import { type Lane, Schedule } from "./schedule";
import { AGENTS_DIR, AGENTS_OWNER_ONLY, OWNER_TREES, ownersOnly, storedPath, within }
  from "./paths";
import { migrate } from "./schema";
import { Sessions } from "./sessions";
import { principal } from "./text";
import { priced, Usage as ModelUsage, type UsageReceipt } from "./usage";
import {
  Egress, type EgressOpened, type EgressReceipt, type EgressRefusal, egressRequest,
  type SandboxBinding, sandboxBinding, settleRequest,
} from "./egress";
import {
  type EgressPolicy, networkDocument, type NetworkPlan, runUrls, sandboxEnv,
} from "./network";
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
  /**
   * The sandbox, as the egress door read its token (`egress.ts`): which token it is (the
   * egress token or the sync token), the SHA-256 of its secret and, for the egress token,
   * the egress generation its
   * network's transform set. Only the egress doors take it; every other door refuses it.
   */
  readonly sandbox?: SandboxBinding;
  /**
   * A run's call on the egress host (its MCP door, its event door): the run its path named,
   * beside the egress token's `sandbox`. The object resolves the live lease, the run under
   * it and its grant, and holds every door to that grant (`#runAccount`).
   */
  readonly run?: string;
  /**
   * The tier numbers of the platform's control record (`tier.ts`), as the binder read them
   * for a run door: never a request field, and never the owner's tier, which is the
   * object's own row.
   */
  readonly tiers?: unknown;
  /**
   * The first-party grant the owner's token was issued under, and that token's expiry
   * (whole seconds), from the provider's own unwrap: a socket ticket's binding, and a
   * sign-out's (`bindOwnerGrant`, `bindSignOut`).
   */
  readonly grant?: string;
  readonly expires_at?: number;
}

export type { Origin, Source, WakeSender };

/** A message to an agent: a run in a conversation, new or continued (`agentSubmit`). */
export interface SubmitRequest {
  readonly message: string;
  readonly client_send_id: string;
  readonly agent_id?: string;
  readonly conversation_id?: string;
  readonly wait_ms?: number;
}

/** A launch of an agent: now, at a time, on a cron, or on the owner's mail (`agentRun`). */
export interface LaunchRequest {
  readonly agent: string;
  readonly input: string;
  readonly at?: number;
  readonly cron?: string;
  readonly on_mail?: boolean;
  readonly if_mail?: boolean;
}

/** A run or a schedule, by its id, exactly one of the two (`agentStatus`, `agentCancel`). */
export interface RunRequest {
  readonly run_id?: string;
  readonly schedule_id?: string;
}

/** What a run door answers: the run as it stands. */
export type RunView = RunReceipt;

/** The runner's report (`runEvents`): its new frames, and its end once the CLI exited. */
export interface EventsRequest {
  readonly frames: PostedFrame[];
  readonly end?: { readonly exit_code: number | null };
}

export interface ConversationRequest {
  readonly conversation_id: string;
}

/**
 * The runner's report of the sandbox's engines' sign-ins (`signinReport`): for each engine
 * that signs in with the vendor's own login, whether its record's `status_argv` succeeded,
 * and the models it read the engine offers. Presence only: never a credential.
 */
export interface SigninRequest {
  readonly signins: readonly { readonly engine: string; readonly signed_in: boolean;
                               readonly models?: readonly string[] }[];
}

export interface TierRequest {
  readonly tier: TierName;
}

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
  readonly expected_revision: number;
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

/** The platform's change of the owner's admission (`admission.ts`). */
export interface AdmissionRequest {
  readonly reason: string;
  readonly suspended: boolean;
}

/**
 * The object's environment: three service bindings, `FILES`, to the files Worker, which
 * binds no Durable Object and no service, `GATEWAY`, to the model gateway, which binds
 * nothing, and `HOOKS`, to the hooks Worker, which binds only its own class (the config
 * guard checks every config), and no Durable Object namespace. `SANDBOX`, the sandbox
 * Worker, is typed here and bound by no config until slice S.
 */
export interface OwnerEnv {
  readonly FILES: ChunkStore;
  readonly GATEWAY: GatewayService;
  readonly HOOKS: HooksService;
  /**
   * The routing key the sandbox tokens are MAC'd under (`doors/src/routing-mac.ts`), a
   * Worker secret the doors Worker holds too; a lease is never opened without it.
   */
  readonly ROUTING_MAC_KEY: string;
  /**
   * The sandbox Worker (`sandbox.ts`), slice S's: unbound until then, so every run is
   * refused (`SANDBOX_ABSENT`) and none reaches `running`.
   */
  readonly SANDBOX?: SandboxService;
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
  /**
   * Where the credential may be forwarded (the setup check's `egress`: its sealed base URL
   * and endpoints); every commit but a failed one carries it.
   */
  readonly egress?: unknown;
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

export interface NetworkAppliedRequest extends LeaseRequest {
  readonly generation: number;
}

/** A lease as the binder keeps the directory by it: with its token's hash. */
export type HeldLease = Lease & { readonly token: string };

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
  /** A run's call: its grant's run and scopes, every door held to them; null otherwise. */
  readonly run: { readonly id: string; readonly scopes: readonly string[] } | null;
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
/** A sandbox token's binding at any door but its own, and any other binding at those. */
export const SANDBOX_DOORS = "a sandbox's token reaches its own doors only";
export const SANDBOX_ONLY = "a sandbox door takes its own sandbox token only";
/** The refusal of a sandbox binding for an owner whose object never opened a lease. */
export const NO_SANDBOX = "no sandbox lease was opened for this owner";
/**
 * What a sync door's caller may read: its lease, the readable set's own name (null for the
 * owner's sandbox, `*` for all of the owner's files), and the test of a path.
 */
interface Readable {
  readonly lease: string;
  readonly scope: string | null;
  readonly test: (path: string) => boolean;
}

/** A sync token that is not the live lease's, or names another lease or epoch. */
export const SYNC_FENCED = "the sync lease was lost; this sync is fenced";

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

/**
 * How often a mailbox read reads its placed bodies again when mail that came between its
 * read of them and its unit put an unread placed body first (ADR-035).
 */
const READ_TRIES = 3;

/** The placed contents a read's rows name (ADR-035). */
function contentsOf(rows: readonly { readonly content_id?: string }[]): string[] {
  return rows.flatMap((row) => row.content_id === undefined ? [] : [row.content_id]);
}

/** A row or entry with its placed body read in, as a caller sees it. */
function filled<T extends { readonly body: string; readonly content_id?: string }>(
    row: T, bodies: ReadonlyMap<string, string>): T {
  if (row.content_id === undefined) {
    return row;
  }
  const { content_id: contentId, ...rest } = row;
  const body = bodies.get(contentId);
  if (body === undefined) {
    throw new Error("a placed body its row names was not read");
  }
  return { ...rest, body } as T;
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

/** The agent a scoped client acts as when its call names none. */
export const MAIN_AGENT = "main";

/** A run's own MCP door reaches no run door: work between agents is mail (ADR-034 d11). */
export const RUN_STARTS_NONE = "run_starts_none: a run starts, reads and cancels no run";
/** The agent's file changed between its read and the admission: the caller tries again. */
export const AGENT_CHANGED = "agent_changed: agent.json changed as the run was admitted";
/**
 * The longest a run door waits for its run to settle before it answers the run as it
 * stands, in ms: a call's own bound (a convention; the run itself has no deadline).
 */
export const RUN_WAIT_MS = 60_000;

function runFields(request: unknown, required: readonly string[],
                   optional: readonly string[] = []): Record<string, unknown> {
  return fields(request, required, optional, "invalid run request");
}

/** A run's input: well-formed text, at least a character; its size is the door's bound. */
function runText(value: unknown, what: string): string {
  if (typeof value !== "string" || value.length === 0 || /\p{Cs}/u.test(value)) {
    throw new Invalid(`${what} is well-formed text`);
  }
  return value;
}

function waitOf(value: unknown): number {
  if (value === undefined) {
    return 0;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0
      || value > RUN_WAIT_MS) {
    throw new Invalid(`wait_ms is 0 to ${RUN_WAIT_MS}`);
  }
  return value;
}

const SEND_ID = /^[A-Za-z0-9_-]{1,128}$/;

/** A request's retry key: the caller and its send id, hashed, so a retry is the same run. */
function requestKey(client: string | null, sendId: unknown): string {
  if (typeof sendId !== "string" || !SEND_ID.test(sendId)) {
    throw new Invalid("client_send_id is 1 to 128 letters, digits, - and _");
  }
  return createHash("sha256").update(`${client ?? "owner"}\0${sendId}`, "utf8").digest("hex");
}

/** The engine, its reports in one batch at most `ENGINE_SIGNINS`, each engine once. */
function signinRequest(request: unknown):
    { engine: string; signed_in: boolean; models: string[] }[] {
  const r = fields(request, ["signins"], [], "invalid sign-in report");
  const signins = r["signins"];
  if (!Array.isArray(signins) || signins.length === 0 || signins.length > ENGINE_SIGNINS) {
    throw new Invalid(`a sign-in report names 1 to ${ENGINE_SIGNINS} engines`);
  }
  const sources = bundledSources();
  const seen = new Set<string>();
  return signins.map((entry) => {
    const f = fields(entry, ["engine", "signed_in"], ["models"], "invalid sign-in report");
    const engine = f["engine"];
    const cli = typeof engine === "string" ? sources.get(engine)?.cli : undefined;
    if (typeof engine !== "string" || cli === undefined || seen.has(engine)
        || !cli.credential.includes("vendor_login_in_sandbox")) {
      throw new Invalid("a sign-in report names engines that sign in, each once");
    }
    seen.add(engine);
    if (typeof f["signed_in"] !== "boolean") {
      throw new Invalid("signed_in is true or false");
    }
    const models = f["models"] ?? [];
    if (!Array.isArray(models) || models.some((model) => typeof model !== "string"
                                               || !/^[!-~]+$/.test(model))
        || JSON.stringify(models).length > SIGNIN_MODELS_CHARS) {
      throw new Invalid(`models are model ids, at most ${SIGNIN_MODELS_CHARS} characters ` +
                        "as JSON");
    }
    return { engine, signed_in: f["signed_in"], models: models as string[] };
  });
}

/** The runner's report, exactly: frames numbered from 1 and rising, and an optional end. */
function eventsRequest(request: unknown): {
  frames: PostedFrame[]; end: { exit_code: number | null } | null } {
  const r = fields(request, ["frames"], ["end"], "invalid run events");
  const frames = r["frames"];
  if (!Array.isArray(frames)) {
    throw new Invalid("invalid run events");
  }
  if (!eventsBounded(r)) {
    throw new Invalid(`a post is at most ${EVENT_FRAMES_MAX} frames, each at most ` +
                      `${FRAME_UNITS_MAX} units of JSON`);
  }
  let last = 0;
  for (const frame of frames) {
    const f = fields(frame, ["seq", "at", "event"], [], "invalid run events");
    if (typeof f["seq"] !== "number" || !Number.isSafeInteger(f["seq"]) || f["seq"] <= last
        || typeof f["at"] !== "number" || !Number.isFinite(f["at"])) {
      throw new Invalid("a frame's seq rises from 1, and its at is a time");
    }
    last = f["seq"];
  }
  let end: { exit_code: number | null } | null = null;
  if (r["end"] !== undefined) {
    const e = fields(r["end"], ["exit_code"], [], "invalid run events");
    const code = e["exit_code"];
    if (code !== null && (typeof code !== "number" || !Number.isSafeInteger(code))) {
      throw new Invalid("an end's exit_code is a whole number, or null");
    }
    end = { exit_code: code as number | null };
  }
  return { frames: frames as PostedFrame[], end };
}

/** What the object's lanes run over: its stores, its in-process callers and the passes. */
export interface LaneDeps {
  readonly ledger: Ledger;
  readonly files: {
    nextAlarm(now: number): number | null;
    collect(now: number, until: number): Promise<void>;
  };
  readonly connections: {
    nextDue(now: number): number | null;
    run(now: number, until: number): Promise<void>;
  };
  /** The `runs` lane: its rows' next time (`Runs.next`), and the object's pass over them. */
  readonly runs: {
    next(now: number): number | null;
    run(now: number, until: number): Promise<void>;
  };
  /** The doorbell, or null until tasks 7 constructs one: read at each use, never kept. */
  readonly sender: () => WakeSender | null;
  /** Whether the owner is admitted (`require_work`): read at each use. */
  readonly admitted: () => boolean;
  readonly clock: () => number;
  /** The object's write gate, which every lane write goes through a row at a time. */
  readonly write: Write;
  readonly runWakes: typeof runWakes;
}

/**
 * The lanes of the object's one schedule (`schedule.ts`). Each lane's pass is handed the
 * pass's wall-clock end (`until`) and passes it on, so no lane can outrun its hung
 * timeout; the passes write through the object's gate. The doorbell and the admission are
 * read when a lane looks or runs, so one the object gains later is never missed.
 * Exported so the wiring is tested with recording passes; `OwnerDO` gives it the real ones.
 */
export function ownerLanes(deps: LaneDeps):
    Readonly<Record<"wakes" | "files" | "connections" | "runs", Lane>> {
  return {
    // Lapsed claims, then due wakes and revokes; open rows count only with a doorbell, and
    // only while the owner is admitted (`require_work`): a suspended owner's wait.
    wakes: { next: (now) => deps.ledger.nextDue(now, deps.sender() !== null
                                                       && deps.admitted()),
             run: (_now, until) => deps.runWakes(deps.ledger, deps.sender(), deps.clock,
                                                 deps.write, WAKES_PER_ALARM, WAKE_SEND_MS,
                                                 until) },
    files: { next: (now) => deps.files.nextAlarm(now),
             run: (now, until) => deps.files.collect(now, until) },
    // Connection housekeeping: expired flows, unclaimed pending rows, provider revokes.
    connections: { next: (now) => deps.connections.nextDue(now),
                   run: (now, until) => deps.connections.run(now, until) },
    // Runs: a schedule due, or a live run's lease expiry; never a poll for work.
    runs: { next: (now) => deps.runs.next(now),
            run: (now, until) => deps.runs.run(now, until) },
  };
}

/** What a test reaches past the doors: the erasure's internal path and its retry interval. */
export interface Internals {
  readonly erasure?: (internal: Erasure) => void;
  readonly eraseRetryMs?: number;
  /** The sandbox's lease API, which the sandbox slice reaches in the object (`SandboxLeases`). */
  readonly sandbox?: (internal: SandboxLeases) => void;
}

/** A model's cost record with its input at its dearest rate: what a token can cost at most. */
function dearest(model: ModelInfo): ModelInfo {
  const top = (rates: { input: number; cacheRead: number; cacheWrite: number }) =>
    Math.max(rates.input, rates.cacheRead, rates.cacheWrite);
  return { ...model, cost: { ...model.cost, input: top(model.cost),
                             ...(model.cost.tiers === undefined ? {}
                               : { tiers: model.cost.tiers.map((tier) =>
                                 ({ ...tier, input: top(tier) })) }) } };
}

/**
 * The most µ$ a sandbox call of `input` and `output` tokens can cost on any of `models`,
 * each model's tokens at most its own context window and output cap, at its dearest rates.
 */
export function egressPrice(models: readonly ModelInfo[], input: number, output: number):
    number {
  return Math.max(...models.map((model) => priced(dearest(model),
                                                  Math.min(input, model.contextWindow),
                                                  Math.min(output, model.maxTokens))));
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
  /** The network applied under egress `generation` (8b's `updateNetwork` returned). */
  networkApplied(request: { lease_id: string; epoch: number; generation: number }):
    Promise<Lease>;
  /** The network document for the current egress policy, with the egress token. */
  network(egressToken: string): Promise<NetworkPlan>;
  /** Whether a live lease's network is older than the egress policy: 8b re-applies it. */
  stale(leaseId: string): Promise<boolean>;
  /** The owner's egress policy, its generation, and `run`'s environment. */
  config(run: SandboxRun): Promise<SandboxConfig>;
}

export class OwnerDO extends DurableObject<OwnerEnv> {
  /** The one write gate: every owner write is one synchronous unit. */
  readonly #gate: WriteGate;
  /** The storage as the modules see it: the gate's view. */
  readonly #storage: OwnerStorage;
  readonly #collections: Collections;
  readonly #policy: Policy;
  readonly #grants: Grants;
  readonly #sessions: Sessions;
  readonly #ledger: Ledger;
  readonly #files: Files;
  /** Where large bodies live (ADR-035): placed in R2 through the files plane. */
  readonly #bodies: Bodies;
  readonly #registrations: Registrations;
  readonly #notices: Notices;
  readonly #connections: Connections;
  readonly #usage: ModelUsage;
  /** Sandbox egress: each request admitted at open and settled once it ends (`egress.ts`). */
  readonly #egress: Egress;
  /** The owner's runs: admission, grants, conversations, schedules (`runs.ts`). */
  readonly #runs: Runs;
  /** The owner app's sockets and the runs' live tails (`relay.ts`), a Lifecycle capability. */
  readonly #relay: Relay;
  readonly #tickets: Tickets;
  /** The runners' events posts held at once (`EVENTS_IN_FLIGHT`). */
  readonly #posts = new PostGate();
  /** Who waits on a run's settle, on this instance only: a door's bounded wait. */
  readonly #waiters = new Map<string, Set<() => void>>();
  /** The owner this object is named by (`ctx.id.name`), refused when it has none. */
  readonly #ownerName: () => string;
  /** The sandbox's lease, owner-internal: the runs slice calls it in process. */
  readonly #sandbox: SandboxLeases;
  readonly #schedule: Schedule;
  readonly #lifecycle: Lifecycle<OwnerEnv>;
  /**
   * What rings outside agents' doorbells, called in process by the `wakes` lane. None
   * until the doorbell slice (tasks 7) constructs one here; until then the lane only
   * recovers lapsed claims, and open wakes and revokes stay pending without a job.
   */
  readonly #sender: WakeSender | null = null;
  /**
   * Whether this object's database is an erased owner's tombstone, as the object found it
   * starting (`admission.ts`): it then migrates nothing, starts no Lifecycle, and every door
   * refuses.
   */
  readonly #tombstone: boolean;
  /**
   * The owner's erasure (`erasure.ts`, `erase.ts`): an internal path, which the erasure
   * door (slice 10) calls; no door reaches it yet.
   */
  readonly #erasure: Erasure;
  /**
   * The runtime constructs it with `ctx` and `env` alone; the workerd tests take the
   * erasure's and the sandbox lease's internal paths (`internals`), which no door reaches.
   */
  constructor(ctx: DurableObjectState, env: OwnerEnv, internals: Internals = {}) {
    super(ctx, env);
    this.#gate = new WriteGate(ctx.storage);
    const storage = this.#gate.storage;
    const write = this.#gate.write;
    this.#storage = storage;
    this.#tombstone = tombstoned(storage.sql);
    const admitted = () => workRefusal(admission(storage)) === null;
    this.#policy = new Policy(storage);
    // The files byte quota is the tier's storage (ADR-034 decision 8), as last handed.
    this.#files = new Files(storage, env.FILES, () => fileLimits(
      this.#policy.read().document,
      keptTiers(storage)?.[ownerTier(storage)].storage_bytes ?? null), write);
    this.#bodies = new Bodies(this.#files, storage, write);
    this.#collections = new Collections(storage, undefined, this.#bodies.adopt);
    this.#grants = new Grants(storage, this.#policy);
    this.#sessions = new Sessions(storage);
    this.#ledger = new Ledger(storage, this.#grants, undefined, admitted, this.#bodies.adopt);
    this.#registrations = new Registrations(storage, this.#grants);
    this.#notices = new Notices(storage);
    const owner = () => {
      const named = ctx.id.name;
      if (named === undefined) {
        throw new Error("this object is not named by an owner");
      }
      return named;
    };
    this.#ownerName = owner;
    this.#connections = new Connections(storage, write, () => env.GATEWAY, owner,
                                        this.#notices, clock);
    this.#egress = new Egress(storage, {
      lease: (token, now) => this.#files.leaseByToken("egress", token, now),
      runConnection: (run, secret, lease) => {
        const grant = this.#runs.grantFor(run, secret, lease);
        return grant === null ? undefined : grant.connection;
      },
      runUsage: (run) => this.#runs.usage(run),
      tierRuns: () => keptTiers(storage)?.[ownerTier(storage)].runs ?? null,
      generation: () => this.#policy.egressGeneration(),
      credential: (connection, nowMs) => {
        try {
          return this.#connections.credential(connection, nowMs);
        } catch (error) {
          if (error instanceof Refused && (error.message === CONNECTION_UNAVAILABLE
                                           || error.message === CREDENTIAL_EXPIRED)) {
            return null;  // not the owner's, not active, or expired: refused at the open
          }
          throw error;
        }
      },
      connection: (connection) => this.#connections.egressOf(connection),
      // A sandbox run's caps are the model policy's per-turn limits, held per run, and its
      // daily, monthly and per-source ceilings: the model policy's caps, no more, no less.
      caps: (source) => {
        const { document } = this.#policy.read();
        const settings = modelSettings(document);
        const own = settings.sources[source];
        return { run_requests: settings.turn_requests, run_tokens: settings.turn_tokens,
                 run_cost: settings.turn_cost, daily_cost: settings.daily_cost,
                 monthly_cost: settings.monthly_cost,
                 source_daily_cost: own?.daily_cost ?? null,
                 source_monthly_cost: own?.monthly_cost ?? null };
      },
      spent: (period, source) => this.#usage.spent(period, source),
      // The call's model is unknown until the gateway reads the body: priced at the most
      // any of the connection's models charges, input at its dearest of uncached,
      // cache-read and cache-write rates, by pi-ai's `calculateCost` (`priced`).
      price: (models, input, output) => egressPrice(models as readonly ModelInfo[], input,
                                                    output),
      pruned: (now) => this.#usage.spendPruned(now),
    });
    this.#usage = new ModelUsage(storage, () => {
      const { generation, document } = this.#policy.read();
      return { generation, settings: modelSettings(document) };
    });
    this.#tickets = new Tickets(storage);
    this.#runs = new Runs(storage, {
      unadmitted: () => workRefusal(admission(storage)),
      sandboxBound: () => env.SANDBOX !== undefined,
      sources: () => bundledSources(),
      usable: () => this.#connections.usable(),
      egressible: () => this.#connections.egressible(),
      standing: (stamp) => {
        const grant = this.#grants.standing(stamp.client, stamp.generation,
                                            stamp.authenticated_at);
        return { mode: grant.mode, holds: (scope) => holds(grant, scope as Scope) };
      },
      tier: () => ownerTier(storage),
      // The lane's: the tier's simultaneous runs, by the numbers the binder last handed.
      capacity: () => {
        try {
          return tierNumbers(storage, undefined, clock())[ownerTier(storage)].runs;
        } catch (error) {
          if (error instanceof Refused) {
            return null;  // none handed yet: no waiting run starts
          }
          throw error;
        }
      },
      caps: () => modelSettings(this.#policy.read().document),
      spent: (period, source) => this.#usage.spent(period, source),
      lease: {
        live: (now, authority) => this.#files.sandboxLease(now, authority),
        all: (now) => this.#files.liveLeases(now),
        open: (now, authority) => this.#openLease(owner, now, authority),
        release: (leaseId, epoch) => this.#files.leaseRelease(leaseId, epoch).released,
        renew: (leaseId, epoch) => {
          this.#files.leaseRenew(leaseId, epoch);
        },
        lapsed: (now) => this.#files.leasesLapsed(now),
      },
      stop: (runs) => this.#cancelInSandbox(runs),
      ended: (run) => {
        this.#egress.end("run", (live) => live.run === run);
        this.#relay.settled(run);
        this.#wake(run);
      },
      leaseEnded: (leaseId) => {
        this.#egress.end("lease", (live) => live.lease === leaseId);
      },
    });
    this.#relay = new Relay({
      admit: (binding, conversation, ticket) =>
        this.#door(() => this.#socketAdmit(binding, conversation, ticket)),
      check: (state) => this.#door(() => this.#socketCheck(state)),
      live: (conversation) => this.#door(() => this.#runs.liveIn(conversation)),
      now: clock,
    });
    // The lanes of the one schedule. There is no global tick; each owner's object
    // schedules only itself.
    this.#schedule = new Schedule(ownerLanes({
      ledger: this.#ledger, files: this.#files, connections: this.#connections,
      runs: { next: (now) => this.#runs.next(now, admitted()),
              run: (now, until) => this.#runsPass(now, until) },
      sender: () => this.#sender, admitted, clock, write, runWakes,
    }), clock, write, undefined, () => wiped(storage.sql) || erasing(storage));
    const lifecycle = new Lifecycle<OwnerEnv>(this);
    this.#erasure = new Erasure({
      storage, write, clock, lifecycle,
      // Every run ended: its streams, its sockets, and its CLI through the sandbox.
      ended: () => {
        this.#egress.drop();
        this.#relay.closeAll("erased");
        this.#cancelInSandbox(this.#runs.liveRuns());
      },
      // Every R2 copy of the owner's: files and placed bodies alike are the files plane's
      // chunks (ADR-035), which the wipe would otherwise leave behind, named nowhere.
      blobs: () => this.#files.eraseChunks(),
      wipe: () => this.#gate.wipe(writeTombstone),
      retryMs: internals.eraseRetryMs ?? ERASE_RETRY_MS,
    });
    internals.erasure?.(this.#erasure);
    this.#sandbox = this.#sandboxLeases();
    internals.sandbox?.(this.#sandbox);
    this.#lifecycle = lifecycle.use(this.#schedule).use(this.#erasure).use(this.#relay)
      .use(this.#relay.capability);
    void ctx.blockConcurrencyWhile(async () => {
      if (this.#tombstone) {
        return;  // an erased owner: nothing is made here again
      }
      await write(() => migrate(storage));
      if (erasing(storage)) {
        // An erasure a crash cut short, from its recorded step: its alarm may be gone.
        this.#erasure.resume();
        return;
      }
      // The schedule as stored, for this code's lanes and sender: a deploy that adds a
      // doorbell gives the wakes held while there was none their job.
      await this.#schedule.sync();
    });
  }

  /**
   * The runtime's alarm, which Lifecycle owns: it starts the Lifecycle, drives the due jobs
   * (each lane's, and the erasure's), then re-arms.
   */
  override async alarm(): Promise<void> {
    await this.#lifecycle.alarm();
  }

  /**
   * The runtime's `fetch`, the object's one non-RPC entry: only the binder's socket upgrade
   * (`relay.ts`), attached and then handed to Lifecycle's `fetch`, whose `WebSockets`
   * accepts it; anything else is refused with its fixed code.
   */
  override async fetch(request: Request): Promise<Response> {
    return this.#relay.serve(request, (upgrade) => this.#lifecycle.fetch(upgrade));
  }

  /** A hibernating socket's frame, handed to Lifecycle (`relay.ts` reads it). */
  override async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    await this.#lifecycle.webSocketMessage(ws, message);
  }

  /** A hibernating socket's close, handed to Lifecycle. */
  override async webSocketClose(ws: WebSocket, code: number, reason: string,
                                wasClean: boolean): Promise<void> {
    await this.#lifecycle.webSocketClose(ws, code, reason, wasClean);
  }

  /** A hibernating socket's error, handed to Lifecycle. */
  override async webSocketError(ws: WebSocket, error: unknown): Promise<void> {
    await this.#lifecycle.webSocketError(ws, error);
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
  #account(binding: Binding, door: DoorClass, serves: Serves = {}): Account {
    if (typeof binding === "object" && binding !== null && "run" in binding) {
      return this.#runAccount(binding, door);
    }
    const { owner, caller } = this.#bind(binding, door);
    // The tier's numbers, as the binder read them from the control record: kept for the
    // files byte quota and the lane's runs (written only when they changed).
    keepTiers(this.#storage, binding.tiers, clock());
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
      return { owner, caller, origin, grant: null, run: null };
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
             grant: this.#grants.admit(origin.client, source.kind, signedIn, clock()),
             run: null };
  }

  /**
   * A run's call on the egress host (its MCP door; ADR-034 decision 6): the egress token's
   * live lease, the run its path named live under it, and that run's grant. Every door it
   * reaches is held to the grant's scopes (`#scope`), as its initiator: the owner's own
   * work acts as the owner (never the owner itself at an owner-only door, `#owner`), a
   * client's as that client, whose stamp is rechecked here at every call; a stamp that no
   * longer stands ends the client's runs, in this unit, and refuses.
   */
  #runAccount(binding: Binding, door: DoorClass): Account {
    const { owner, caller } = this.#bind(binding, door, "run");
    const now = clock();
    const sandbox = sandboxBinding(binding.sandbox);
    const lease = this.#egress.lease(sandbox, now);
    // The run's own secret, as its path carried it: a run id alone authorizes nothing.
    const grant = lease === null || sandbox.kind !== "egress" ? null
      : this.#runs.grantFor(binding.run, sandbox.secret, lease);
    if (grant === null) {
      throw new Refused(RUN_ENDED);
    }
    let held: Grant | null = null;
    if (grant.stamp !== null) {
      try {
        held = this.#grants.standing(grant.stamp.client, grant.stamp.generation,
                                     grant.stamp.authenticated_at);
      } catch (error) {
        if (error instanceof Refused) {
          this.#cancelInSandbox(this.#runs.endClient(grant.stamp.client, clock()));
          throw new Refused(AUTHORITY_CHANGED);
        }
        throw error;
      }
    }
    return { owner, caller, origin: grant.origin, grant: held,
             run: { id: grant.run, scopes: grant.scopes } };
  }

  /** The owner itself, on its own account, never an outside client nor a run. */
  #owner(binding: Binding, door: DoorClass, action: string): Account {
    const account = this.#account(binding, door);
    if (account.origin !== null || account.run !== null) {
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
  #widening<T>(binding: Binding, door: DoorClass, action: string, request: unknown,
               owner: string | null,
               act: (account: Account, sessionId: string) => T, serves: Serves = {}): T {
    const account = this.#account(binding, door, serves);
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
   * `agent` (the `main` agent by default), in `center` (default: the command center its
   * one enabled agent is registered in). `full` and the owner pass.
   */
  #scope(account: Account, capability: Capability, center?: unknown,
         agent: string = MAIN_AGENT): void {
    if (account.run !== null) {
      // A run holds its grant's scopes, which admission already held to its initiator's.
      if (!account.run.scopes.includes(capability)) {
        throw new Refused("outside client capability not granted");
      }
      return;
    }
    if (account.grant?.mode !== "scoped") {
      return;
    }
    const universe = center === undefined ? this.#universe(account) : center;
    if (typeof universe !== "string"
        || !holds(account.grant, { universe, agent, capability })) {
      throw new Refused("outside client capability not granted");
    }
  }

  /**
   * Where a scoped client acts: the command center its one enabled agent is registered in.
   * None for a full grant's or the owner's, whose stamps name no universe.
   */
  #universe(account: Account): string | null {
    if (account.grant?.mode !== "scoped") {
      return null;
    }
    const mine = this.#ledger.agents(account.origin)
      .filter((agent) => agent.disabled_reason === null);
    if (mine.length !== 1) {
      throw new Refused("this outside identity is not one enabled agent");
    }
    return mine[0]!.command_center_id;
  }

  // -- SESSIONS: the owner's protected interactive sessions ---------------------

  /** A verified interactive sign-in opens a session; its cookie is returned once. */
  sessionOpen(binding: Binding): Promise<{ session: string; expires_at: number }> {
    return this.#door(() => {
      this.#owner(binding, "read", "open its session");
      return this.#sessions.open(binding.authenticated_at, clock());
    });
  }

  sessionExtend(binding: Binding): Promise<{ expires_at: number }> {
    return this.#door(() => {
      this.#owner(binding, "read", "renew its session");
      return this.#sessions.extend(binding.session, clock());
    });
  }

  /** One session signed out, and the connection flows it began cancelled with it. */
  sessionClose(binding: Binding): Promise<{ closed: number }> {
    return this.#synced(() => {
      this.#owner(binding, "reduce", "sign out");
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
  async sessionCloseAll(binding: Binding): Promise<{ closed: number }> {
    const closed = await this.#synced(() => {
      this.#owner(binding, "reduce", "sign out");
      this.#connections.cancelFlows(null);
      return this.#sessions.closeAll();
    });
    this.#relay.closeAll("signedOut");  // account revocation: every device's sockets
    return closed;
  }

  // -- OWNER_POLICY ------------------------------------------------------------

  policyRead(binding: Binding): Promise<Projection> {
    return this.#door(() => {
      const { owner } = this.#owner(binding, "read", "read its policy");
      return this.#policy.projection(owner);
    });
  }

  policyReceipts(binding: Binding): Promise<{ receipts: Receipt[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its policy");
      return { receipts: this.#policy.receipts() };
    });
  }

  /**
   * The owner's write, in its session; with `proposal`, a held card's one apply. A new
   * files retention is applied to rows already kept by housekeeping, so the schedule is
   * synced after it (the `files` lane re-applies it). Once it committed, it ends the
   * sandbox's in-flight egress streams; a call opened after it is admitted under the new
   * generation.
   */
  async policyWrite(binding: Binding, request: PolicyWriteRequest): Promise<Projection> {
    const done = await this.#synced(() => this.#widening(binding, "work", "write", request,
                                                         "write its policy", ({ owner }, id) => {
      const r = policyFields(request, ["expected_generation", "policy"], ["proposal"]);
      const written = this.#policy.write(id, r["expected_generation"], r["policy"],
                                         r["proposal"] ?? null, clock());
      return { owner, generation: written.generation, digest: written.digest,
               policy: written.document };
    }));
    await this.#door(() => this.#egress.end("policy"));
    return done;
  }

  /**
   * An agent's proposal, the owner's or an admitted outside client's; never applied. A
   * scoped client proposes only with `propose_policy` granted where its agent is.
   */
  policyPropose(binding: Binding, request: ProposeRequest):
      Promise<{ request_digest: string; generation: number }> {
    return this.#door(() => {
      const account = this.#account(binding, "work");
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
      this.#owner(binding, "read", "read its policy");
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
      return this.#widening(binding, "work", "wake_limits", request, "change its agents' policy",
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
      this.#owner(binding, "read", "read its outside clients");
      return { clients: this.#grants.list() };
    });
  }

  /**
   * The owner's widening, in its session, against the grant's revision. A set that moves
   * the grant's authority (its mode or scopes) ends the client's agents' wakes and held
   * registrations with it, as `grantRevoke` ends them; one that moves only the revision, a
   * rename, ends nothing: every stamp of the client's still stands.
   */
  grantSet(binding: Binding, request: GrantRequest): Promise<
      { client: string; mode: string; scopes: Scope[]; generation: number; revision: number }> {
    return this.#synced(() => this.#widening(binding, "work", "outside_set", request,
                                             "change an outside client's grant",
                                             (_account, id) => {
      const r = policyFields(request, ["client", "source", "mode", "scopes",
                                       "expected_revision", "name"]);
      return this.#storage.transactionSync(() => {
        const { moved, ...set } = this.#grants.set(id, r as unknown as GrantRequest, clock());
        if (moved) {
          this.#endClient(set.client);
        }
        return set;
      });
    }));
  }

  /**
   * A narrowing the owner's account makes without a session, as a key's revoke does: the
   * client's agents' wakes and held registrations end with it, in its unit.
   */
  grantRevoke(binding: Binding, request: ClientRequest):
      Promise<{ client: string; revoked: boolean; generation: number; revision: number }> {
    return this.#synced(() => {
      this.#owner(binding, "reduce", "revoke an outside client's grant");
      const r = policyFields(request, ["client"]);
      return this.#storage.transactionSync(() => {
        const revoked = this.#grants.revoke(r["client"], clock());
        this.#endClient(revoked.client);
        return revoked;
      });
    });
  }

  /**
   * Forget a revoked API key's grant (slice 3's review L1): the binder calls it once the
   * directory has deleted the key (tasks 2). A narrowing, since a key with no row is
   * refused, so the owner's account makes it without a session. Never an MCP client's.
   */
  grantForget(binding: Binding, request: ClientRequest):
      Promise<{ client: string; forgotten: boolean }> {
    return this.#door(() => {
      this.#owner(binding, "reduce", "forget an outside client's grant");
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
      return this.#widening(binding, "reduce", "outside_forget", request,
                            "forget its revoked outside clients", (_account, id) => {
        const r = policyFields(request, ["clients"]);
        return this.#grants.forgetRevoked(id, r["clients"], clock());
      });
    });
  }

  /**
   * A grant change ends what its client set going (P0-3's M3): its agents' open wakes and
   * callbacks and its held registrations, in the caller's transaction.
   */
  #endClient(client: string): void {
    this.#ledger.suppressClient(client, clock());
    this.#registrations.endClient(client);
    // And its runs (ADR-034 decision 6): their grants gone in this unit, their CLIs stopped.
    this.#cancelInSandbox(this.#runs.endClient(client, clock()));
  }

  // -- OUTSIDE_AGENTS ---------------------------------------------------------

  /** The owner sees every agent; an outside caller only its own identity's. */
  outsideAgents(binding: Binding): Promise<{ agents: Agent[] }> {
    return this.#door(() => {
      const { origin } = this.#account(binding, "read");
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
    return this.#synced(() => this.#widening(binding, "work", "OUTSIDE_AGENTS.register", request, null,
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
      const account = this.#account(binding, "work");
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
      this.#owner(binding, "read", "read its agents' registrations");
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
    return this.#synced(() => this.#widening(binding, "reduce", "OUTSIDE_AGENTS.disable", request, null,
                                             ({ origin }) => {
      const r = ledgerFields(request, ["agent_id", "reason"]);
      return this.#ledger.disable(r["agent_id"], r["reason"], origin);
    }));
  }

  /** Only the owner itself, in its session, deletes an agent. */
  deleteAgent(binding: Binding, request: AgentRequest):
      Promise<{ agent_id: string; messages: number }> {
    return this.#synced(() => this.#widening(binding, "reduce", "OUTSIDE_AGENTS.delete", request,
                                             "change its agents' policy", () => {
      const r = ledgerFields(request, ["agent_id"]);
      return this.#ledger.remove(r["agent_id"]);
    }));
  }

  // -- ROOM_POST and MAILBOX_RECEIPT -------------------------------------------

  roomPost(binding: Binding, request: PostRequest): Promise<Posted> {
    return this.#placing(() => {
      const account = this.#account(binding, "work");
      const r = ledgerFields(request, ["room_id", "client_message_id", "body", "parent_id"]);
      this.#scope(account, "write_graph");
      checkMessage(r["body"]);
      return { body: r["body"], writer: fileWriter(account) };
    }, (placed) => {
      const account = this.#account(binding, "work");
      const r = ledgerFields(request, ["room_id", "client_message_id", "body", "parent_id"]);
      this.#scope(account, "write_graph");
      const posted = this.#ledger.post(account.origin, r["room_id"], r["client_message_id"],
                                       r["body"], r["parent_id"], clock(), placed,
                                       fileWriter(account));
      if (posted.recipients.includes(OWNER)) {
        this.#runs.mailArrived(clock());  // the doorbell: the runs on the owner's mail
      }
      return posted;
    }, true);
  }

  // -- MODEL CONNECTIONS: sealed credentials, flows and spend (tasks 4 G2) ------------
  //
  // Each door is the owner's own, in its live session: an outside client's call carries
  // no session and is refused, with a refusal receipt. The binder seals and exchanges with
  // the gateway between them; no plaintext secret reaches the object.

  /** A pending connection for the binder to seal a credential for; for OAuth, its flow. */
  connectionPrepare(binding: Binding, request: ConnectionPrepareRequest): Promise<Prepared> {
    return this.#synced(() => this.#widening(binding, "work", "connection_prepare", request,
                                             "connect a model source", (_account, id) =>
      this.#connections.prepare(id, request, clock())));
  }

  /** The sealed blob the gateway returned, and the models its setup check listed. */
  connectionCommit(binding: Binding, request: ConnectionCommitRequest): Promise<ConnectionView> {
    return this.#synced(() => this.#widening(binding, "work", "connection_commit", request,
                                             "connect a model source",
                                             () => this.#connections.commit(request, clock())));
  }

  /**
   * A connection unusable at once; its credential deleted here, and at its provider. Its
   * sandbox egress streams end once the revoke committed.
   */
  async connectionRevoke(binding: Binding, request: ConnectionRequest):
      Promise<{ connection: string; revoked: boolean; provider_revoke: boolean }> {
    const revoked = await this.#synced(() => this.#widening(binding, "reduce",
                                                            "connection_revoke", request,
                                                            "revoke a model source",
      () => this.#connections.revoke(request, clock())));
    await this.#door(() => this.#egress.end("connection",
                                            (live) => live.connection === revoked.connection));
    return revoked;
  }

  /** Every connection, newest first, never its blob. */
  connectionList(binding: Binding): Promise<{ connections: ConnectionView[] }> {
    return this.#door(() => this.#widening(binding, "read", "connection_list", null,
                                           "read its model sources",
                                           () => ({ connections: this.#connections.list() })));
  }

  /** The callback's state, in the session that began the flow: its verifier, used up. */
  flowFinish(binding: Binding, request: FlowFinishRequest):
      Promise<{ connection: string; version: number; verifier: string; redirect_uri: string;
                source: string }> {
    return this.#synced(() => this.#widening(binding, "work", "flow_finish", { state: "[state]" },
                                             "connect a model source", (_account, id) =>
      this.#connections.flowFinish(id, request, clock())));
  }

  /** The owner's newest model-call receipts: what each call reserved, and what it cost. */
  usageReceipts(binding: Binding): Promise<{ receipts: UsageReceipt[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its model spend");
      return { receipts: this.#usage.receipts(clock()) };
    });
  }

  // -- NOTICES: what the owner's work reported to the owner -------------------------

  /** The owner's notices, newest first (`notices.ts`). */
  noticeList(binding: Binding): Promise<{ notices: Notice[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its notices");
      return { notices: this.#notices.list() };
    });
  }

  // -- ADMISSION: the platform's word on the owner's account -------------------------

  /**
   * The platform suspends the owner for a reason (`billing`, `abuse`), or lifts it
   * (`admission.ts`): taken only from the reserved `PLATFORM_CALLER`, with no outside client
   * and no session, whatever the owner's admission is now; an erased owner is never set
   * again. A suspension then ends the sandbox's in-flight egress streams: the prompt stop,
   * since every lease, egress call and wake claim is refused while it lasts. Lifting one
   * gives the `wakes` lane its job back.
   */
  async admissionSet(binding: Binding, request: AdmissionRequest): Promise<Admission> {
    const set = await this.#synced(() => {
      const { caller } = this.#bind(binding, "admission");
      if (caller !== PLATFORM_CALLER || !("origin" in binding) || binding.origin !== null
          || binding.session !== undefined || binding.source !== undefined) {
        throw new Refused(PLATFORM_ONLY);
      }
      return setAdmission(this.#storage, request);
    });
    if (set.state !== "admitted") {
      // The prompt stop: its streams, and its runs' CLIs (each settles from its runner).
      await this.#door(() => {
        this.#egress.end("admission");
        this.#cancelInSandbox(this.#runs.endAll(clock()));
      });
    }
    if (doorRefusal(set, "read") !== null) {
      this.#relay.closeAll("suspended");  // reads refused: an abuse suspension
    }
    return set;
  }

  /**
   * A mailbox page. Its placed bodies (ADR-035) are read from R2 first, located in a unit
   * of their own; the read's unit then marks only what it returns, and ends the page before
   * a placed body it was not handed (one that arrived between), which a further read of
   * the bodies picks up.
   */
  mailboxRead(binding: Binding, request: ReadRequest): Promise<MailPage> {
    return this.#rereading(() => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes"]);
      this.#scope(account, "read_graph");
      return this.#ledger.placedNext(account.origin, r["limit"]);
    }, (bodies) => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes"]);
      this.#scope(account, "read_graph");
      return this.#ledger.read(account.origin, r["limit"], r["max_bytes"] ?? null,
                               r["pad_bytes"] ?? 0, clock(), bodies);
    }, (page) => page.messages.length === 0 && page.more);
  }

  mailboxAck(binding: Binding, request: AckRequest): Promise<{ reader: string; through: number }> {
    return this.#door(() => {
      const account = this.#account(binding, "reduce");
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
      const account = this.#account(binding, "work");
      const r = ledgerFields(request, ["command_center_id", "agent_id"]);
      this.#scope(account, "read_graph", r["command_center_id"]);
      const generation = this.#ledger.subscriber(account.origin, r["command_center_id"],
                                                 r["agent_id"]);
      return { agent_id: r["agent_id"] as string, generation };
    });
  }

  /**
   * The owner this object serves: the name it was reached by, and every other one
   * refused. The stored owner, fixed at first use, is a second check of the same. An
   * erased owner's tombstone refuses every call (`OWNER_ERASED`), an erasure under way too
   * (`ERASING`), and so does an owner not admitted (`require_work`), unless `admission` is
   * false: the platform's own door, which sets it. The platform's reserved caller reaches
   * no other door, and no owner is named so.
   */
  #bind(binding: Binding, door: DoorClass | "admission",
        sandbox: "none" | "egress" | "sync" | "run" = "none"): { owner: string; caller: string } {
    if (this.#tombstone || this.#erasure.erased) {
      throw new Refused(OWNER_ERASED);
    }
    if (erasing(this.#storage)) {
      this.#erasure.resume(true);
      throw new Refused(ERASING);
    }
    if (typeof binding !== "object" || binding === null) {
      throw new Invalid("invalid owner principal");
    }
    const owner = principal(binding.owner);
    const caller = principal(binding.caller);
    if (owner === PLATFORM_CALLER || (door !== "admission" && caller === PLATFORM_CALLER)) {
      throw new Refused(PLATFORM_RESERVED);
    }
    if (door !== "admission") {
      const refusal = doorRefusal(admission(this.#storage), door);
      if (refusal !== null) {
        throw new Refused(refusal);
      }
    }
    // A sandbox token reaches its own doors and nothing else: the egress token the egress
    // doors, the sync token the sync doors; they take nothing else.
    // A run's call is the egress token's, with its run: the run doors (`#runAccount`) alone.
    const presented = binding.sandbox === undefined ? "none"
      : (binding.sandbox as { kind?: unknown }).kind;
    const expected = sandbox === "run" ? "egress" : sandbox;
    if (expected !== presented || (sandbox === "run") !== ("run" in binding)) {
      throw new Refused(sandbox === "none" ? SANDBOX_DOORS : SANDBOX_ONLY);
    }
    if (sandbox !== "none") {
      // The binder's sandbox binding in its exact shape (`bindSandbox`, `bindRun`), checked
      // before anything is written: a stranger's request writes nothing.
      const keys = sandbox === "run" ? "caller,origin,owner,run,sandbox"
        : "caller,origin,owner,sandbox";
      if (Object.keys(binding).sort().join() !== keys || binding.origin !== null
          || caller !== owner) {
        throw new Invalid("invalid sandbox binding");
      }
      sandboxBinding(binding.sandbox);
      if (sandbox === "run" && sandboxBinding(binding.sandbox).kind !== "egress") {
        throw new Invalid("invalid sandbox binding");
      }
    }
    const named = this.ctx.id.name;
    if (named === undefined) {
      throw new Refused("this object is not named by an owner");
    }
    if (named !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    const sql = this.#storage.sql;
    const stored = sql.exec<{ owner: string }>("SELECT owner FROM object_owner").toArray()[0];
    if (stored === undefined && sandbox !== "none") {
      // Only an object that minted a lease's tokens has a sandbox: a never-seen owner gets
      // no rows from one.
      throw new Refused(NO_SANDBOX);
    }
    if (stored === undefined) {
      sql.exec("INSERT INTO object_owner (only, owner) VALUES (1, ?)", owner);
    } else if (stored.owner !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    return { owner, caller };
  }

  /**
   * A sync door's caller by the sync token: the live lease's own, and when `request` names a
   * lease, that lease at its epoch; else refused, as a lost lease's sync is fenced.
   */
  #syncer(binding: Binding, door: DoorClass, request: { lease_id?: unknown; epoch?: unknown }
                                                      | null): Readable {
    this.#bind(binding, door, "sync");
    const sandbox = sandboxBinding(binding.sandbox);
    const lease = sandbox.kind === "sync"
      ? this.#files.leaseByToken("sync", sandbox.token, clock()) : null;
    if (lease === null || (request !== null && (request.lease_id !== lease.lease_id
                                                || request.epoch !== lease.epoch))) {
      throw new Refused(SYNC_FENCED);
    }
    // A client's sandbox always reads its live runs' agents' own definitions (their
    // `AGENTS.md`, `agent.json` and the abilities' trees: without them the agent cannot run),
    // read-only; the rest of the owner's files only as that client may read them; and writes
    // only what it may write (as itself, never into the owner's trees: `Files.syncCommit`),
    // by its grant as its live run stamped it, standing now.
    if (lease.authority === OWNER_AUTHORITY) {
      return { lease: lease.lease_id, scope: null, test: () => true };
    }
    {
      const stamp = this.#runs.authorityStamp(lease.authority);
      let grant: Grant;
      try {
        if (stamp === null) {
          throw new Refused(SYNC_FENCED);
        }
        grant = this.#grants.standing(stamp.client, stamp.generation, stamp.authenticated_at);
      } catch (error) {
        if (error instanceof Refused) {
          throw new Refused(SYNC_FENCED);
        }
        throw error;
      }
      const capability = door === "read" ? "read_files" : "write_files";
      const held = grant.mode === "full" || (stamp!.universe !== null && holds(grant, {
        universe: stamp!.universe, agent: stamp!.agent, capability }));
      if (held) {
        return { lease: lease.lease_id, scope: "*", test: () => true };
      }
      if (door !== "read") {
        throw new Refused("outside client capability not granted");
      }
      const agents = this.#runs.liveAgents(lease.authority)
        .map((agent) => `${AGENTS_DIR}/${agent}`);
      return { lease: lease.lease_id, scope: agents.join("\n"),
               test: (path) => path === AGENTS_DIR || agents.some((dir) => within(path, dir))
                 || OWNER_TREES.some((tree) => tree !== AGENTS_DIR && within(path, tree)) };
    }
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
  #collector(binding: Binding, door: DoorClass, capability: Capability): Account {
    const account = this.#account(binding, door, { visitors: true });
    this.#scope(account, capability);
    return account;
  }

  /**
   * A write door with a body (ADR-035). Its first unit checks its caller and request
   * (`prepare`, the body and the writer it is placed as); a body its row keeps is written
   * in that same unit (`act`). A body past `PLACE_BYTES` is begun there instead, sent to R2,
   * and written in a second unit, which checks the caller again and adopts it; its upload
   * is released after, whatever the unit did (none once adopted).
   */
  async #placing<T>(prepare: () => { body: unknown; writer: string },
                    act: (placed: Placed | null) => T, synced = false): Promise<T> {
    const first = await this.#door((): { done: T } | { placing: Placing } => {
      const { body, writer } = prepare();
      const placing = this.#bodies.begin(body as string, writer);
      return placing === null ? { done: act(null) } : { placing };
    });
    let done: T;
    if ("done" in first) {
      done = first.done;
    } else {
      const placed = await this.#bodies.send(first.placing);
      try {
        done = await this.#door(() => act(placed));
      } finally {
        await this.#bodies.release(placed);
      }
    }
    if (synced) {
      await this.#schedule.sync();
    }
    return done;
  }

  /**
   * A read door's unit, then the placed bodies its rows name read from R2 and set in them
   * (ADR-035): `unit` returns its result and the content ids it names, located in the
   * same unit, so each is the content its row named.
   */
  /**
   * A read door whose unit marks what it returns (a mailbox read, ADR-035): the placed
   * bodies it would return located in a unit of their own (`locate`, its caller checked),
   * read from R2, then its unit (`read`, the caller checked again) handed them; it reads
   * again, a few times at most, while `again` says mail between put an unread placed body
   * first.
   */
  async #rereading<T>(locate: () => readonly string[],
                      read: (bodies: ReadonlyMap<string, string>) => T,
                      again: (value: T) => boolean): Promise<T> {
    for (let tries = 0; ; tries++) {
      const located = await this.#door(() => this.#bodies.locate(locate()));
      const bodies = await this.#bodies.fetch(located);
      const value = await this.#door(() => read(bodies));
      if (!again(value) || tries >= READ_TRIES) {
        return value;
      }
    }
  }

  async #reading<T>(unit: () => { value: T; contents: readonly string[] },
                    fill: (value: T, bodies: ReadonlyMap<string, string>) => T): Promise<T> {
    const { value, located } = await this.#door(() => {
      const read = unit();
      return { value: read.value, located: this.#bodies.locate(read.contents) };
    });
    return located.size === 0 ? fill(value, new Map()) : fill(value, await this.#bodies.fetch(located));
  }

  collectionWrite(binding: Binding, request: WriteRequest): Promise<{ row: Row } | Conflicted> {
    return this.#placing(() => {
      const account = this.#collector(binding, "work", "write_graph");
      const r = fields(request, ["collection", "row_id", "body", "expected_etag", "private"],
                       ["history_op"]);
      return { body: checkBody(r["body"]), writer: fileWriter(account) };
    }, (placed) => {
      const account = this.#collector(binding, "work", "write_graph");
      const r = fields(request, ["collection", "row_id", "body", "expected_etag", "private"],
                       ["history_op"]);
      return conflicted(() => ({
        row: this.#collections.write(account.owner, account.caller, r["collection"] as string,
                                     r["row_id"] as string, r["body"], r["expected_etag"],
                                     r["private"], "history_op" in r ? r["history_op"] : "write",
                                     placed, fileWriter(account)),
      }));
    });
  }

  collectionPull(binding: Binding, request: PullRequest): Promise<Page> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["collection", "after", "limit"]);
      const page = this.#collections.pull(caller, r["collection"] as string, r["after"],
                                          r["limit"]);
      return { value: page, contents: contentsOf(page.rows) };
    }, (page, bodies) => ({ ...page, rows: page.rows.map((row) => filled(row, bodies)) }));
  }

  collectionGet(binding: Binding, request: KeyRequest): Promise<{ row: Row | null }> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["collection", "row_id"]);
      const got = this.#collections.get(caller, r["collection"] as string, r["row_id"] as string);
      return { value: got, contents: contentsOf(got.row === null ? [] : [got.row]) };
    }, (got, bodies) => ({ row: got.row === null ? null : filled(got.row, bodies) }));
  }

  collectionHistory(binding: Binding, request: KeyRequest): Promise<{ entries: HistoryEntry[] }> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["collection", "row_id"]);
      const kept = this.#collections.history(caller, r["collection"] as string,
                                             r["row_id"] as string);
      return { value: kept, contents: contentsOf(kept.entries) };
    }, (kept, bodies) => ({ entries: kept.entries.map((entry) => filled(entry, bodies)) }));
  }

  collectionDelete(binding: Binding, request: DeleteRequest):
      Promise<{ deleted: number } | Conflicted> {
    return this.#door(() => {
      const { owner, caller } = this.#collector(binding, "reduce", "write_graph");
      this.#ownerOnly(owner, caller, "delete");
      const r = fields(request, ["collection", "row_id", "expected_etag"]);
      return conflicted(() => this.#collections.delete(
        caller, r["collection"] as string, r["row_id"] as string, r["expected_etag"],
      ));
    });
  }

  collectionClear(binding: Binding, request: ClearRequest): Promise<{ deleted: number }> {
    return this.#door(() => {
      const { owner, caller } = this.#collector(binding, "reduce", "write_graph");
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
  #filer(binding: Binding, door: DoorClass, capability: "read_files" | "write_files"):
      Account {
    const account = this.#account(binding, door);
    this.#scope(account, capability);
    return account;
  }

  /**
   * That an outside client's change leaves the owner's agent definitions alone: its agents'
   * files and skills (`OWNER_TREES`).
   */
  #ownersTree(account: Account, ...paths: unknown[]): void {
    if (account.origin !== null && paths.some((path) => ownersOnly(storedPath(path)))) {
      throw new Refused(AGENTS_OWNER_ONLY);
    }
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
      this.#filer(binding, "read", "read_files");
      const r = fileFields(request, ["path"]);
      return { entry: this.#files.stat(r["path"]) };
    });
  }

  fileList(binding: Binding, request: FileListRequest):
      Promise<{ entries: Entry[]; next: string | null }> {
    return this.#door(() => {
      this.#filer(binding, "read", "read_files");
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
      this.#filer(binding, "read", "read_files");
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
      this.#filer(binding, "read", "read_files");
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
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["path", "expected_etag", "content"], ["mode"]);
      this.#ownersTree(account, r["path"]);
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
          authorize: () => this.#filer(binding, "work", "write_files") }),
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
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["size", "chunks"]);
      return this.#files.begin(r["size"], r["chunks"], fileWriter(account));
    });
  }

  /** Send an upload's chunk bytes, at most two chunks a call; R2 checks each hash. */
  async fileUploadChunks(binding: Binding, request: UploadChunksRequest):
      Promise<{ created: number }> {
    const r = await this.#door(() => {
      this.#filer(binding, "work", "write_files");
      return fileFields(request, ["intent_id", "chunks"]);
    });
    // `Files.upload` records each chunk stored, once `FILES` took it, in a unit of its own.
    return this.#files.upload(r["intent_id"], r["chunks"]);
  }

  /** Commit an upload whose chunks are all stored as the file it declared. */
  fileUploadCommit(binding: Binding, request: UploadCommitRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["intent_id", "path", "expected_etag"], ["mode"]);
      this.#ownersTree(account, r["path"]);
      return fileConflicted(() => ({
        entry: this.#files.commit(r["intent_id"], r["path"], {
          expected: r["expected_etag"], mode: r["mode"], writer: fileWriter(account) }),
      }));
    });
  }

  fileUploadAbort(binding: Binding, request: IntentRequest): Promise<{ aborted: boolean }> {
    return this.#filing(() => {
      this.#filer(binding, "reduce", "write_files");
      const r = fileFields(request, ["intent_id"]);
      return this.#files.abort(r["intent_id"]);
    });
  }

  fileMkdir(binding: Binding, request: MkdirRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["path", "expected_etag"]);
      this.#ownersTree(account, r["path"]);
      return fileConflicted(() => ({
        entry: this.#files.mkdir(r["path"], { expected: r["expected_etag"],
                                              writer: fileWriter(account) }),
      }));
    });
  }

  fileRemove(binding: Binding, request: RemoveRequest):
      Promise<{ removed: number } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "reduce", "write_files");
      const r = fileFields(request, ["path", "expected_etag", "recursive"]);
      this.#ownersTree(account, r["path"]);
      return fileConflicted(() => this.#files.remove(r["path"], {
        expected: r["expected_etag"], recursive: flag(r["recursive"], "recursive"),
        writer: fileWriter(account) }));
    });
  }

  fileRename(binding: Binding, request: RenameRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["from", "to", "expected_etag", "replace"]);
      this.#ownersTree(account, r["from"], r["to"]);
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
      this.#filer(binding, "read", "read_files");
      const r = fileFields(request, ["path", "limit"], ["before"]);
      return this.#files.history(r["path"], r["before"] ?? null, r["limit"]);
    });
  }

  fileRestore(binding: Binding, request: RestoreRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["path", "version", "expected_etag"]);
      this.#ownersTree(account, r["path"]);
      return fileConflicted(() => ({
        entry: this.#files.restore(r["path"], r["version"], {
          expected: r["expected_etag"], writer: fileWriter(account) }),
      }));
    });
  }

  /** Drop a kept version's content: the owner's own act, never an outside client's. */
  fileRedact(binding: Binding, request: VersionRequest): Promise<{ redacted: number }> {
    return this.#filing(() => {
      this.#owner(binding, "reduce", "redact its file history");
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
      this.#owner(binding, "reduce", "purge its file history");
      const r = fileFields(request, ["path"]);
      return this.#files.purge(r["path"]);
    });
  }

  fileChanges(binding: Binding, request: ChangesRequest):
      Promise<{ changes: FileChange[]; cursor: number; reset: boolean }> {
    return this.#door(() => {
      let readable: Readable | null = null;
      if (binding.sandbox === undefined) {
        this.#filer(binding, "read", "read_files");
      } else {
        readable = this.#syncer(binding, "read", null);
      }
      const r = fileFields(request, ["after", "limit"]);
      const changes = this.#files.changes(r["after"], r["limit"]);
      if (readable === null) {
        return changes;
      }
      // A client's sandbox whose readable set changed since its last read (a new agent's run,
      // a widened file scope) reads the feed again from its start: `reset`.
      const moved = readable.scope !== null && this.#files.leaseReadable(
        readable.lease, createHash("sha256").update(readable.scope, "utf8").digest("hex")
          .slice(0, 16));
      const test = readable.test;
      return { ...changes, reset: changes.reset || moved,
               changes: changes.changes.filter((change) => test(change.path)) };
    });
  }

  fileUsage(binding: Binding): Promise<Usage> {
    return this.#door(() => {
      return this.#files.usage(fileWriter(this.#filer(binding, "read", "read_files")));
    });
  }

  // -- RUNS: the run doors (slice R, `runs.ts`; ADR-034 decision 4) -------------------------
  //
  // A run door starts, reads or cancels a run. Starting one is two units around the agent's
  // file read: the first checks the call and reads what it asks; then `agent.json` is read
  // (its bytes may cross to `FILES`); then one unit binds the call again and admits the run
  // whole (`#launch`), so nothing between the two can widen what the admission saw. A run's
  // own MCP door reaches none of these (`RUN_STARTS_NONE`): work between agents is mail.

  /**
   * A message to one of the owner's agents (`converse`): a run in a new conversation, or in
   * one of the caller's own (`conversation_id`), resuming its engine's session. A retry with
   * the same `client_send_id` is the same run. Waits up to `wait_ms` for the run to settle,
   * then answers the run as it stands; its status is `agentStatus`'s.
   */
  async agentSubmit(binding: Binding, request: SubmitRequest): Promise<RunView> {
    const asked = await this.#door(() => {
      const account = this.#account(binding, "work");
      const r = runFields(request, ["message", "client_send_id"],
                          ["agent_id", "conversation_id", "wait_ms"]);
      const agent = agentId(r["agent_id"] ?? MAIN_AGENT);
      this.#runDoor(account, "converse", agent);
      const client = account.origin?.client ?? null;
      return { agent, client, prompt: runText(r["message"], "a message"),
               waitMs: waitOf(r["wait_ms"]),
               conversation: (r["conversation_id"] ?? null) as string | null,
               request: requestKey(client, r["client_send_id"]) };
    });
    const read = await this.#readAgent(asked.agent);
    const run = await this.#launch(read, () => {
      const account = this.#account(binding, "work");
      const universe = this.#runDoor(account, "converse", asked.agent);
      return { ...this.#initiated(binding, account, universe, asked.agent),
               agent: asked.agent, conversation: asked.conversation, request: asked.request,
               tiers: tierNumbers(this.#storage, binding.tiers, clock()), unattended: false,
               prompt: asked.prompt };
    });
    return this.#settledWithin(run, asked.waitMs, asked.client);
  }

  /**
   * A launch of one of the owner's agents on an input (`run_graph`), in a fresh conversation
   * of its own: now, or as a schedule of the `runs` lane, at a time (`at`), at each time a
   * cron names (`cron`), or each time mail reaches the owner (`on_mail`); `if_mail` holds a
   * timed one to the owner's mail too (it runs only when there is some unacked). A
   * schedule's run is unattended: its source must allow that (ADR-033 decision 3).
   */
  async agentRun(binding: Binding, request: LaunchRequest): Promise<RunView | RunSchedule> {
    const asked = await this.#synced(() => {
      const account = this.#account(binding, "work");
      const r = runFields(request, ["agent", "input"], ["at", "cron", "on_mail", "if_mail"]);
      const agent = agentId(r["agent"]);
      const universe = this.#runDoor(account, "run_graph", agent);
      const input = runText(r["input"], "an input");
      const client = account.origin?.client ?? null;
      const at = r["at"] ?? null;
      const cron = r["cron"] ?? null;
      const mail = r["on_mail"] === undefined ? false : flag(r["on_mail"], "on_mail");
      const ifMail = r["if_mail"] === undefined ? false : flag(r["if_mail"], "if_mail");
      if (at !== null && (typeof at !== "number" || !Number.isFinite(at))) {
        throw new Invalid("at is a time in seconds since the epoch");
      }
      if (cron !== null && typeof cron !== "string") {
        throw new Invalid("cron is a cron expression");
      }
      if (at === null && cron === null && !mail) {
        if (ifMail) {
          throw new Invalid("if_mail goes with at, cron or on_mail");
        }
        return { now: true as const, agent, input, client };
      }
      if (binding.tiers !== undefined) {
        tierNumbers(this.#storage, binding.tiers, clock());  // kept for the lane's runs
      }
      const { stamp, origin } = this.#initiated(binding, account, universe, agent);
      return { now: false as const, schedule: this.#runs.schedule({
        agent, input, at: at as number | null, cron: cron as string | null, mail,
        if_mail: ifMail, stamp, origin }, clock()) };
    });
    if (!asked.now) {
      return asked.schedule;
    }
    const read = await this.#readAgent(asked.agent);
    const run = await this.#launch(read, () => {
      const account = this.#account(binding, "work");
      const universe = this.#runDoor(account, "run_graph", asked.agent);
      return { ...this.#initiated(binding, account, universe, asked.agent),
               agent: asked.agent, conversation: null, request: null,
               tiers: tierNumbers(this.#storage, binding.tiers, clock()), unattended: false,
               prompt: asked.input };
    });
    return this.#settledWithin(run, 0, asked.client);
  }

  /** A run of the caller's, from its receipt; or a schedule of the caller's. */
  agentStatus(binding: Binding, request: RunRequest): Promise<RunView | RunSchedule> {
    return this.#door(() => {
      const account = this.#account(binding, "read");
      this.#runDoor(account, null, null);
      const client = account.origin?.client ?? null;
      const r = runFields(request, [], ["run_id", "schedule_id"]);
      if (("run_id" in r) === ("schedule_id" in r)) {
        throw new Invalid("a status names a run_id or a schedule_id");
      }
      return "run_id" in r ? this.#runs.receipt(r["run_id"], client)
        : this.#runs.scheduleView(r["schedule_id"], client);
    });
  }

  /**
   * Cancel a run of the caller's: marked, its CLI stopped through the sandbox; it settles
   * `failed` (`cancelled`) from the runner's end, or `unknown` if the runner is lost. Or a
   * schedule of the caller's removed. A settled run cancels nothing.
   */
  async agentCancel(binding: Binding, request: RunRequest):
      Promise<RunView | { schedule_id: string; cancelled: true }> {
    const found = await this.#synced(() => {
      const account = this.#account(binding, "reduce");
      this.#runDoor(account, null, null);
      const client = account.origin?.client ?? null;
      const r = runFields(request, [], ["run_id", "schedule_id"]);
      if (("run_id" in r) === ("schedule_id" in r)) {
        throw new Invalid("a cancel names a run_id or a schedule_id");
      }
      if ("schedule_id" in r) {
        return { schedule: this.#runs.unschedule(r["schedule_id"], client).schedule_id };
      }
      const run = this.#runs.receipt(r["run_id"], client).run_id;
      if (this.env.SANDBOX === undefined && this.#runs.grant(run) !== null) {
        throw new Refused(SANDBOX_ABSENT);  // no CLI could be stopped: refused, loudly
      }
      return { run, client, live: this.#runs.cancel(run, clock()) };
    });
    if ("schedule" in found) {
      return { schedule_id: found.schedule, cancelled: true };
    }
    if (found.live !== null) {
      await this.#sandboxCancel(found.live);
    }
    return this.#door(() => this.#runs.receipt(found.run, found.client));
  }

  /**
   * The runner's report for its run (`EventsRequest`), on the egress host by the egress
   * token and the run its path names: each new frame read by the run's record (its session
   * id, result and error kept), relayed to the app sockets on the run's conversation, and
   * never committed; the batch renews the lease (the runner's heartbeat); the runner's
   * `end` settles the run.
   */
  async runEvents(binding: Binding, request: EventsRequest):
      Promise<{ accepted: number; settled: string | null }> {
    const done = await this.#door(() => {
      // At most `EVENTS_IN_FLIGHT` posts held at once, whatever the tier: one more is
      // refused before anything is written, and its runner posts it again.
      this.#posts.enter();
      try {
        this.#bind(binding, "reduce", "run");
        const lease = this.#runner(binding);
        if (lease === null) {
          throw new Refused(RUN_ENDED);
        }
        const r = eventsRequest(request);
        return this.#runs.events(binding.run as string, lease, r.frames, r.end, clock());
      } catch (error) {
        this.#posts.leave();
        throw error;
      }
    });
    try {
      this.#relay.relay(done.conversation, done.frames);
      if (done.settled !== null) {
        this.#relay.settled(binding.run as string);
        await this.#schedule.sync();
      }
      return { accepted: done.frames.length, settled: done.settled };
    } finally {
      this.#posts.leave();
    }
  }

  /**
   * The runner's report of the engines' sign-ins (`SigninRequest`), on the egress host by
   * the egress token of the live lease: each engine's row (signed in or not, when it was
   * checked, the models it offers), never a credential; `auto` ranks the signed-in ones
   * with the owner's connections. A sign-in that lapsed (signed in, now not) leaves a
   * "sign in again" notice, and `auto` stops picking that engine.
   */
  async signinReport(binding: Binding, request: SigninRequest):
      Promise<{ recorded: number; lapsed: string[] }> {
    return this.#door(() => {
      this.#bind(binding, "work", "egress");
      const lease = this.#runner(binding);
      if (lease === null) {
        throw new Refused("egress_lease_closed");
      }
      const reports = signinRequest(request);
      const lapsed: string[] = [];
      if (lease.authority !== OWNER_AUTHORITY) {
        // An outside client's sandbox's own sign-ins, kept on its lease: a login is never
        // copied between sandboxes, so none of the owner's own reaches it.
        const now = new Set(lease.signins);
        for (const report of reports) {
          if (report.signed_in) {
            now.add(report.engine);
          } else {
            now.delete(report.engine);
          }
        }
        const joined = [...now].sort().join(",");
        if (joined.length > SIGNIN_LIST_CHARS) {
          throw new Invalid(`a sandbox's signed-in engines are at most ${SIGNIN_LIST_CHARS} ` +
                            "characters joined");
        }
        const before = this.#files.leaseSignins(lease.lease_id, [...now]);
        for (const engine of before.filter((each) => !now.has(each))) {
          lapsed.push(engine);
          this.#notices.add("signin_lapsed",
                            `${engine}: sign in again inside ${lease.authority}'s sandbox`, clock());
        }
        return { recorded: reports.length, lapsed };
      }
      for (const report of reports) {
        if (this.#runs.reportSignin(report.engine, report.signed_in, report.models, clock())
              .lapsed) {
          lapsed.push(report.engine);
          this.#notices.add("signin_lapsed", `${report.engine}: sign in again`, clock());
        }
      }
      return { recorded: reports.length, lapsed };
    });
  }

  /**
   * The live lease the egress token names, when the call also presents that lease's runner
   * secret (`x-ta-runner`), which only the runner holds: its events and its sign-in reports.
   * Null otherwise.
   */
  #runner(binding: Binding): SandboxLease | null {
    const sandbox = sandboxBinding(binding.sandbox);
    const lease = this.#egress.lease(sandbox, clock());
    return lease !== null && sandbox.kind === "egress" && sandbox.runner !== undefined
      && sandbox.runner === lease.runner_token ? lease : null;
  }

  /**
   * In a unit: a run door's caller held: never a run's own call (a run starts none), and
   * for a door that starts work, `need` held where it acts (a scoped client's agent); its
   * universe.
   */
  #runDoor(account: Account, need: "converse" | "run_graph" | null, agent: string | null):
      string | null {
    if (account.run !== null) {
      throw new Refused(RUN_STARTS_NONE);
    }
    if (need === null || agent === null) {
      return null;
    }
    const universe = this.#universe(account);
    this.#scope(account, need, universe, agent);
    return universe;
  }

  /** Who starts a run: the owner, or a client, with its stamp and origin. */
  #initiated(binding: Binding, account: Account, universe: string | null, agent: string):
      { initiator: "owner" | "client"; stamp: Stamped | null; origin: Origin | null } {
    if (account.origin === null || account.grant === null) {
      return { initiator: "owner", stamp: null, origin: null };
    }
    return { initiator: "client", origin: account.origin,
             stamp: { client: account.origin.client, generation: account.grant.generation,
                      authenticated_at: binding.authenticated_at ?? null, universe, agent } };
  }

  /** An agent's `agent.json`, read whole, and the etag it was read at. */
  async #readAgent(agent: string): Promise<{ file: AgentFile; etag: string }> {
    const path = agentFilePath(agent);
    const content = await this.#door(() => {
      const entry = this.#files.stat(path);
      if (entry === null || entry.kind !== "file") {
        throw new Refused(AGENT_UNDEFINED);
      }
      return this.#files.open(path);
    });
    let bytes = content.inline;
    if (bytes === undefined) {
      const parts = await this.#files.fetch((content.chunks ?? []).map(({ sha256 }) =>
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
      text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
    } catch {
      throw new Invalid(`${path} is not UTF-8 text`);
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
  async #launch(read: { file: AgentFile; etag: string }, ask: () => Omit<Ask, "file">,
                waiting: string | null = null): Promise<string> {
    const { admitted, agent, unattended } = await this.#door(() => {
      const made = ask();
      if (this.#files.stat(agentFilePath(made.agent))?.etag !== read.etag) {
        throw new Refused(AGENT_CHANGED);
      }
      return { agent: made.agent, unattended: made.unattended,
               admitted: this.#runs.admit({ ...made, file: read.file }, clock(), (chosen) =>
                 this.#runConfig({ run: chosen.run, secret: chosen.secret, engine: chosen.engine,
                                   credential: chosen.credential,
                                   ...(chosen.connection === null ? {}
                                     : { connection: chosen.connection }) }), waiting) };
    });
    if (admitted.existing) {
      return admitted.run;
    }
    const sandbox = this.env.SANDBOX;
    let answer: { started: true } | { started: false; code: string } | null = null;
    try {
      if (sandbox === undefined) {
        throw new Error(SANDBOX_ABSENT);  // admission refused this already
      }
      answer = started(await sandbox.start(this.#ownerName(), {
        run: admitted.run, agent, conversation: admitted.conversation,
        engine: admitted.engine, credential: admitted.credential, model: admitted.model,
        session_id: admitted.session_id, prompt: admitted.prompt, unattended,
        authority: admitted.authority, lease: admitted.lease, tokens: admitted.tokens,
        network: admitted.network, config: admitted.config,
        urls: runUrls(admitted.run, admitted.secret) }));
    } catch {
      console.error("a run's start had no answer; it is settled unknown");
      answer = null;
    }
    await this.#door(() => {
      if (answer === null) {
        this.#runs.settle(admitted.run, "unknown", "start_lost", null, clock());
      } else if (answer.started) {
        this.#runs.started(admitted.run, clock());
      } else {
        this.#runs.settle(admitted.run, "failed", answer.code, null, clock());
        if (answer.code === SIGNIN_REQUIRED) {
          // No login inside that sandbox: the owner signs in there, never copies one.
          this.#notices.add(SIGNIN_REQUIRED, `${agent}: sign in to ${admitted.engine} inside ${
            admitted.authority === OWNER_AUTHORITY ? "your own" : `${admitted.authority}'s`
          } sandbox, through the vendor's own flow`, clock());
        }
      }
    });
    if (answer === null) {
      await this.#sandboxCancel({ run: admitted.run, lease_id: admitted.lease.lease_id });
    }
    await this.#schedule.sync();
    return admitted.run;
  }

  /** The run as it stands once settled, or once `waitMs` passed; the caller's own. */
  async #settledWithin(run: string, waitMs: number, client: string | null): Promise<RunView> {
    const live = await this.#door(() => this.#runs.receipt(run, client).settled_at === null);
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
    return this.#door(() => this.#runs.receipt(run, client));
  }

  /** A run settled: whoever waits on it on this instance goes on. */
  #wake(run: string): void {
    for (const done of [...(this.#waiters.get(run) ?? [])]) {
      queueMicrotask(done);
    }
  }

  /** Stop a run's CLI through the sandbox; a failure is logged, the run settles regardless. */
  async #sandboxCancel(run: Pick<LiveRun, "run" | "lease_id">): Promise<void> {
    const sandbox = this.env.SANDBOX;
    if (sandbox === undefined) {
      console.error(`${SANDBOX_ABSENT}: run ${run.run}'s CLI could not be stopped`);
      return;
    }
    try {
      await sandbox.cancel(this.#ownerName(), { run: run.run, lease_id: run.lease_id });
    } catch {
      console.error("a run's cancel did not reach the sandbox");
    }
  }

  /** From a unit: each run's cancel sent to the sandbox once the unit is over. */
  #cancelInSandbox(grants: readonly LiveRun[]): void {
    for (const grant of grants) {
      queueMicrotask(() => void this.#sandboxCancel(grant));
    }
  }

  /**
   * The `runs` lane's pass: runs whose runner was lost settled `unknown`; then each waiting
   * run whose turn came started, oldest first, through the same admission as a door's (one
   * its turn refuses settles `failed`, the refusal in the owner's notices); then each due
   * schedule claimed, once, and run the same way when it has work; a refusal kept on its
   * row and in the owner's notices, never thrown.
   */
  async #runsPass(_now: number, until: number): Promise<void> {
    await this.#door(() => this.#runs.lost(clock()));
    while (clock() < until) {
      const waiting = await this.#door(() => {
        const next = workRefusal(admission(this.#storage)) !== null ? null
          : this.#runs.nextWaiting();
        return next === null ? null : { run: next.id, ask: this.#runs.waitingAsk(next.id)! };
      });
      if (waiting === null) {
        break;
      }
      try {
        const read = await this.#readAgent(waiting.ask.agent);
        await this.#launch(read, () => ({
          ...waiting.ask, tiers: tierNumbers(this.#storage, undefined, clock()) }), waiting.run);
      } catch (error) {
        if (!(error instanceof Refused || error instanceof Invalid)) {
          throw error;
        }
        const reason = error.message;
        if (reason === CONVERSATION_BUSY || reason.startsWith(TIER_RUNS)) {
          break;  // not its turn after all: it keeps waiting, and the lane comes back
        }
        await this.#door(() => {
          if (this.#runs.refuseWaiting(waiting.run, reason, clock())) {
            this.#notices.add("run_refused", `${waiting.ask.agent}: ${reason}`, clock());
          }
        });
      }
    }
    while (clock() < until) {
      const claimed = await this.#door(() => workRefusal(admission(this.#storage)) !== null
        ? null : this.#runs.claim(clock(), () => this.#ledger.ownerUnacked()));
      if (claimed === null) {
        return;
      }
      if (!claimed.work) {
        continue;  // due, but nothing to do: no sandbox is woken for nothing
      }
      const row = claimed.schedule;
      try {
        const read = await this.#readAgent(row.agent);
        const run = await this.#launch(read, () => ({
          initiator: row.trigger === "mail" ? "mail" : "schedule",
          ...scheduleAuthority(row), agent: row.agent, conversation: null, request: null,
          tiers: tierNumbers(this.#storage, undefined, clock()), unattended: true,
          prompt: row.input }));
        await this.#door(() => this.#runs.ran(row.id, run, null));
      } catch (error) {
        if (!(error instanceof Refused || error instanceof Invalid)) {
          throw error;
        }
        const reason = error.message;
        await this.#door(() => {
          this.#runs.ran(row.id, null, reason);
          this.#notices.add("run_refused", `${row.agent}: ${reason}`, clock());
        });
      }
    }
  }

  // -- SOCKETS: the owner app's live view of its runs (A6, salvaged; `relay.ts`) ----------

  /**
   * A socket ticket for one of the owner's own conversations (`read`): a routing-MAC token
   * of the `socket` kind, single-use, good for `SOCKET_TICKET_S` and never past the token it
   * was asked with, bound to that token's first-party grant (`binding.grant`,
   * `binding.expires_at`, the binder's from the verified token). Only its secret's SHA-256
   * is kept. The ticket, once.
   */
  socketTicket(binding: Binding, request: ConversationRequest):
      Promise<{ ticket: string; conversation_id: string; expires_at: number }> {
    return this.#door(() => {
      this.#owner(binding, "read", "open an app socket");
      const r = runFields(request, ["conversation_id"]);
      const conversation = r["conversation_id"];
      if (!this.#runs.owns(conversation, null)) {
        throw new Refused(TICKET_REFUSED);
      }
      const grant = binding.grant;
      const tokenExpires = binding.expires_at;
      if (typeof grant !== "string" || typeof tokenExpires !== "number") {
        throw new Invalid("a socket ticket is bound to its token's grant and expiry");
      }
      const now = clock();
      const expires = Tickets.expiry(now, tokenExpires);
      if (expires <= now) {
        throw new Refused(TICKET_REFUSED);
      }
      const minted = mintToken(this.env.ROUTING_MAC_KEY, "socket", this.#ownerName(), expires);
      return { ticket: minted.token, conversation_id: conversation as string,
               expires_at: this.#tickets.mint(minted.hash, conversation as string, grant,
                                              tokenExpires, now) };
    });
  }

  /**
   * The first-party grant the binding's token was issued under signed out (`reduce`): its
   * outstanding tickets forgotten and its app sockets closed `signed_out`, every other
   * device's left open. The doors tell it before they revoke the grant.
   */
  async socketsEnd(binding: Binding): Promise<{ grant: string; ended: true }> {
    const grant = await this.#door(() => {
      this.#owner(binding, "reduce", "end its app sockets");
      const held = binding.grant;
      if (typeof held !== "string" || held === "" || held.length > 128) {
        throw new Invalid("a grant id");
      }
      this.#tickets.endGrant(held);
      return held;
    });
    this.#relay.endGrant(grant);
    return { grant, ended: true };
  }

  /**
   * In a unit: a socket's attach, as a `read` door: the binding's account, the owner's own
   * (no outside client and no run has a socket), no session, and its ticket consumed for
   * this conversation of the owner's; refused alike otherwise (`TICKET_REFUSED`).
   */
  #socketAdmit(binding: unknown, conversation: string, hash: string): SocketState {
    const account = this.#account(binding as Binding, "read");
    if (account.origin !== null || account.run !== null) {
      throw new Refused(OWNERS_SOCKET);
    }
    if ((binding as Binding).session !== undefined) {
      throw new Invalid("an app socket carries no session");
    }
    const ticket = this.#tickets.consume(hash, conversation, clock());
    if (!this.#runs.owns(conversation, null)) {
      throw new Refused(TICKET_REFUSED);
    }
    return { v: 1, owner: account.owner, conversation, grant: ticket.grant,
             expires_at: ticket.token_expires_at,
             binding: { owner: account.owner, caller: account.caller, origin: null } };
  }

  /** In a unit: whether a socket's attachment still holds, as at its attach; its close. */
  #socketCheck(state: SocketState): CloseKind | null {
    try {
      this.#account(state.binding as unknown as Binding, "read");
      return state.expires_at <= clock() ? "expired" : null;
    } catch (error) {
      if (!(error instanceof Refused || error instanceof Invalid)) {
        throw error;
      }
      const message = error.message;
      return message === OWNER_ERASED || message === ERASING ? "erased"
        : message.startsWith(OWNER_SUSPENDED) ? "suspended" : "signedOut";
    }
  }

  // -- THE TIER: the platform's word on the owner's tier (`tier.ts`) ---------------------

  /**
   * The platform sets the owner's tier (`free`, `paid`): taken only from the reserved
   * `PLATFORM_CALLER`, as `admissionSet` is. The numbers are the control record's, never
   * this door's.
   */
  tierSet(binding: Binding, request: TierRequest): Promise<{ tier: TierName }> {
    return this.#door(() => {
      const { caller } = this.#bind(binding, "admission");
      if (caller !== PLATFORM_CALLER || !("origin" in binding) || binding.origin !== null
          || binding.session !== undefined || binding.source !== undefined) {
        throw new Refused(PLATFORM_TIER_ONLY);
      }
      return setTier(this.#storage, request);
    });
  }

  // -- THE SANDBOX: its one lease, its network and its egress (tasks 4 G4) -----------
  //
  // The sandbox holds one lease, the file sync lease (`files.ts`): its id is the claim
  // token and its epoch the fencing number, for the sync commits and the egress alike, and
  // it holds the hashes of its two tokens' secrets, the egress token (E2B's transform alone
  // carries it) and the sync token (the in-VM sync agent's). The lease itself is owner-
  // internal (`SandboxLeases`): no door reaches it. The egress doors take the egress
  // token's binding, the sync doors the sync token's, and no other door takes either.

  /** The owner's own admission for `door`, as a binding check reads it, for internal calls. */
  #admitted(door: DoorClass): void {
    if (this.#tombstone || this.#erasure.erased) {
      throw new Refused(OWNER_ERASED);
    }
    if (erasing(this.#storage)) {
      throw new Refused(ERASING);
    }
    const refusal = doorRefusal(admission(this.#storage), door);
    if (refusal !== null) {
      throw new Refused(refusal);
    }
  }

  /**
   * One run's whole environment (slice E's `runEnv`, G4's `sandboxEnv`), from its record,
   * its credential and an API-key run's connection, with the owner's egress policy and its
   * generation: refused (`Invalid`) when the record does not run on that credential.
   */
  #runConfig(run: SandboxRun): SandboxConfig {
    if (typeof run !== "object" || run === null || typeof run.engine !== "string"
        || typeof run.run !== "string" || !CREDENTIAL_KINDS.includes(run.credential)
        || (run.credential === "api_key") !== (typeof run.connection === "string")) {
      throw new Invalid("a sandbox run names itself, its engine, its credential and, on an " +
                        "API key, its connection");
    }
    let engine;
    try {
      engine = runEnv(bundledSources(), run.engine, run.credential);
    } catch (error) {
      throw error instanceof SourceRecordError ? new Invalid(error.message) : error;
    }
    if (typeof run.secret !== "string" || !/^[0-9a-f]{64}$/.test(run.secret)) {
      throw new Invalid("a sandbox run carries its own secret, 64 hex");
    }
    const keyed = sandboxEnv(this.#connections.egressible(),
                             () => randomBytes(16).toString("hex"),
                             { ...engine, run: run.run, secret: run.secret,
                               connection: run.connection ?? null });
    // The record's own variables are never shaped like a key's, so the two never meet.
    const shared = Object.keys(keyed).find((name) => Object.hasOwn(engine.env, name));
    if (shared !== undefined) {
      throw new Error(`a run's record and its key both set ${shared}`);
    }
    const { document } = this.#policy.read();
    return { generation: this.#policy.egressGeneration(), egress: document.egress,
             env: Object.freeze({ ...engine.env, ...keyed }), inherit: engine.inherit };
  }

  /**
   * In a unit, as a run's admission finds no live lease of its authority: that sandbox's
   * lease taken, its egress and sync tokens minted (they live while the lease is renewed,
   * by its heartbeat, with no deadline of their own, `LEASE_LIVED`, so no run meets a fixed
   * one) and the runner's own secret, and the owner's network made with the egress token in
   * its transform. A lease that lapsed is replaced: its streams and tokens end with it.
   */
  #openLease(owner: () => string, now: number, authority: string) {
    const egress = mintToken(this.env.ROUTING_MAC_KEY, "egress", owner(), LEASE_LIVED);
    const sync = mintToken(this.env.ROUTING_MAC_KEY, "sync", owner(), LEASE_LIVED);
    const runner = randomBytes(32).toString("hex");
    const lease = this.#files.leaseAcquire(authority, "sandbox", {
      egress: egress.hash, sync: sync.hash, runner: secretHash(runner) });
    const live = new Set(this.#files.liveLeases(now).map((each) => each.lease_id));
    this.#egress.end("lease", (stream) => !live.has(stream.lease));
    const { document } = this.#policy.read();
    return { lease, tokens: { egress_token: egress.token, sync_token: sync.token,
                              runner_token: runner },
             network: networkDocument(document.egress, this.#policy.egressGeneration(),
                                      egress.token) };
  }

  #sandboxLeases(): SandboxLeases {
    const leases: SandboxLeases = {
      renew: (request) => this.#door(() => {
        this.#admitted("work");
        return this.#files.leaseRenew(request.lease_id, request.epoch);
      }),
      close: (request) => this.#door(() => {
        this.#admitted("reduce");
        const { released } = this.#files.leaseRelease(request.lease_id, request.epoch);
        if (released) {
          this.#egress.end("lease", (live) => live.lease === request.lease_id);
        }
        // Its runs go with it: their runner is gone, so each is settled `unknown`.
        for (const run of this.#runs.liveRuns()) {
          if (run.lease_id === request.lease_id && run.epoch === request.epoch) {
            this.#runs.settle(run.run, "unknown", "lease_closed", null, clock());
          }
        }
        return { released };
      }),
      networkApplied: (request) => this.#door(() => {
        this.#admitted("work");
        const generation = this.#policy.egressGeneration();
        if (request.generation !== generation) {
          throw new Refused("egress_policy_stale: the network was made under another egress " +
                            "generation");
        }
        const lease = this.#files.networkApplied(request.lease_id, request.epoch, generation);
        if (this.#policy.read().document.egress.mode === "ask") {
          this.#policy.refusal("sandbox_network", { mode: "ask", generation }, clock(),
                               "egress.ask");
        }
        return lease;
      }),
      network: (egressToken) => this.#door(() => {
        this.#admitted("read");
        const { document } = this.#policy.read();
        return networkDocument(document.egress, this.#policy.egressGeneration(), egressToken);
      }),
      stale: (leaseId) => this.#door(() => {
        const lease = this.#files.liveLeases(clock()).find((each) => each.lease_id === leaseId);
        return lease !== undefined
          && lease.network_generation !== this.#policy.egressGeneration();
      }),
      config: (run) => this.#door(() => {
        this.#admitted("read");
        return this.#runConfig(run);
      }),
    };
    return Object.freeze(leases);
  }


  /**
   * One sandbox request, admitted at open (`Egress.open`): its blob, version, generation,
   * reservation and end signal, or its refusal's fixed code and receipt. An OAuth
   * credential about to expire is refreshed first, as a model call's is.
   */
  async egressOpen(binding: Binding, request: unknown): Promise<EgressOpened | EgressRefusal> {
    const { r, live } = await this.#door(() => {
      this.#bind(binding, "work", "egress");
      const checked = egressRequest(request);
      const lease = this.#egress.lease(sandboxBinding(binding.sandbox), clock());
      // Only the live lease's own run connection is ever refreshed for it.
      const sandbox = sandboxBinding(binding.sandbox);
      return { r: checked, live: lease !== null && sandbox.kind === "egress"
        && this.#runs.grantFor(checked.run, sandbox.secret, lease)?.connection
          === checked.connection };
    });
    // Only a live lease's call may refresh its connection's OAuth credential.
    if (live) {
      try {
        await this.#connections.fresh(r.connection);
      } catch {
        // A refresh that failed leaves the credential as it was: the open below refuses it
        // if it has expired, and uses it while it has not.
      }
    }
    return this.#door(() => {
      this.#bind(binding, "work", "egress");
      return this.#egress.open(sandboxBinding(binding.sandbox), r, clock());
    });
  }

  /**
   * One reservation's end, as the egress door saw it: its outcome, the provider's status
   * and the bytes it streamed; a blob the gateway resealed meanwhile is kept by the
   * connection's compare-and-swap.
   */
  egressSettle(binding: Binding, request: Record<string, unknown>):
      Promise<{ settled: boolean }> {
    return this.#door(() => {
      this.#bind(binding, "reduce", "egress");
      const { resealed, ...rest } = request ?? {};
      const settled = this.#egress.settle(sandboxBinding(binding.sandbox),
                                          settleRequest(rest), clock());
      if (settled.settled && resealed !== undefined) {
        try {
          this.#connections.resealed(settled.connection, settled.version, resealed, Date.now());
        } catch (error) {
          if (!(error instanceof Invalid)) {
            throw error;
          }
          // A malformed reseal is the gateway's bug: the call still settles, and the
          // gateway reseals again at the next one.
          console.error("a resealed blob the egress door handed back was malformed");
        }
      }
      return { settled: settled.settled };
    });
  }

  /** The owner's newest egress receipts: what the sandbox asked, and how each ended. */
  egressReceipts(binding: Binding): Promise<{ receipts: EgressReceipt[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its sandbox's egress");
      return { receipts: this.#egress.receipts() };
    });
  }

  fileSyncCommit(binding: Binding, request: SyncCommitRequest):
      Promise<{ outcomes: SyncOutcome[] }> {
    return this.#filing(() => {
      if (binding.sandbox === undefined) {
        this.#owner(binding, "work", "hold the file sync lease");
      } else {
        this.#syncer(binding, "work", request);
      }
      const r = fileFields(request, ["lease_id", "epoch", "ops"]);
      return this.#files.syncCommit({ lease_id: r["lease_id"], epoch: r["epoch"],
                                      ops: r["ops"] });
    });
  }
}
