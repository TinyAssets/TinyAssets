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
 * {@link WakeSender} (`Doorbell`, slice W: each vendor's wake from its `wake-kinds.json`
 * record, after the kill switch in `CONTROL`), to the callback an agent's client stores
 * at `wakeSubscribe` or the owner at `agentDoorbell`; file housekeeping (expired uploads and temporary directories,
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
 * A main agent spends few calls (slice M): `inboxDigest` and `boardDigest` answer what
 * changed for it since its cursor in one read, held to its grant; `codeRun` is code mode at
 * the MCP door, one script calling the caller's other tools (`codemode.ts`), refused unless
 * the owner's policy turns `hooks.codemode` on and the caller's profile holds `code`; and
 * `toolProfile` answers the doors which tools a caller is served (`doors/profiles.json`:
 * its grant's profile, or a run's).
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
  type ProfileName, profileNames, refusal, takesRequest, type Tool, toolsNamed,
} from "../../doors/src/tools";
import {
  bundledSources, CREDENTIAL_KINDS, type CredentialKind, runEnv, SourceRecordError,
} from "../../doors/src/sources";

import {
  admission, type Admission, doorRefusal, type DoorClass, OWNER_ERASED, OWNER_SUSPENDED,
  ownerTier, PLATFORM_CALLER, PLATFORM_ONLY, PLATFORM_RESERVED, setAdmission, setTier,
  tombstoned, workRefusal, writeTombstone,
} from "./admission";
import { AGENT_UNDEFINED, type AgentFile, agentFile, agentFilePath } from "./agent-file";
import { agentId, AUTHORITY_CHANGED, refusalCode, type Stamped } from "./authority";
import { type CloseKind, OWNERS_SOCKET, Relay, type SocketState } from "./relay";
import { type Ask, CONVERSATION_BUSY, SIGNIN_REQUIRED, type LiveRun, type MailAnswer,
         mailConversation,
         type PostedFrame, QUEUE_FULL, type RunReceipt, Runs, RUN_ENDED, type RunSchedule,
         scheduleAuthority, secretHash } from "./runs";
import { ENGINE_SIGNINS, SIGNIN_LIST_CHARS, SIGNIN_MODELS_CHARS } from "./run-rows";
import { EVENT_FRAMES_MAX, EVENTS_RACED, eventsBounded, FRAME_UNITS_MAX, PostGate } from "./run-events";
import { SANDBOX_ABSENT, SANDBOX_WAITING, type SandboxService, started } from "./sandbox";
import { TICKET_REFUSED } from "./socket-shape";
import { Tickets } from "./tickets";
import { type ApiKey, keyId, keyName, Keys } from "./keys";
import {
  bindingId, created as inboundCreated, Inbound, type InboundBinding, type InboundRow,
  ownerProof, PROOF_INVALID,
} from "./inbound";
import { proves } from "./inbound-proof";
import {
  keepTiers, keptTiers, PLATFORM_TIER_ONLY, TIER_RUNS, type TierName, tierNumbers,
} from "./tier";
import { Bodies, type Located, PLACE_BYTES, type Placed, placedAs, type Placing } from "./bodies";
import {
  type Changed, type Changes, checkBody, Collections, Conflict, type History,
  type HistoryEntry, type Page, type Row,
} from "./collections";
import {
  CONNECTION_UNAVAILABLE, type ConnectionView, Connections, CREDENTIAL_EXPIRED,
  type GatewayService, type ModelInfo, type Prepared,
} from "./connections";
import { Invalid, Refused } from "./errors";
import {
  type Change as FileChange, CHUNK_BYTES, CHUNK_DELETING, type ChunkRef, type ChunkStore,
  type Content, type Entry, FileConflict, FileFault, Files, type Lease, OWNER_AUTHORITY,
  type SandboxLease, type SyncOp,
  type SyncOutcome, type Usage,
} from "./files";
import { type OwnerStorage, type Write, WriteGate } from "./gate";
import { type Capability, type Client, type Grant, Grants, holds, type Scope } from "./grants";
import {
  CODEMODE_TIMEOUT_MS, type CodemodeBounds, codemodeSandbox, type HooksService,
} from "./codemode";
import {
  type Actor, type Agent, type ChainMail, checkMessage, clock, type Delivery, Ledger,
  type MailAuthority, type MailPage, RUN_NO_MAILBOX,
  apiKeyOrigin, MAIL_HEADER_BYTES, mailInput, type NativeAgent, nativeId, type Origin,
  outsideOrigin, type Posted, registrant, type Source,
} from "./ledger";
import { type Notice, Notices } from "./notices";
import { Erasure } from "./erase";
import {
  ERASE_RETRY_MS, ERASING, erasing, markOutside, outsideDone, PUT_GRACE_MS, wiped,
} from "./erasure";
import {
  type Change, customTools, fileLimits, hookSettings, modelSettings, Policy,
  type PolicyDocument, type Projection, type Receipt,
} from "./policy";
import { type Card, Registrations } from "./registrations";
import { type Lane, Schedule } from "./schedule";
import { AGENTS_DIR, AGENTS_OWNER_ONLY, OWNER_TREES, ownersOnly, storedPath, within }
  from "./paths";
import { migrate } from "./schema";
import { Sessions } from "./sessions";
import { CONTROL_FRESH_MS, ControlGate, type Gate } from "./control-gate";
import { codePoints, principal, utf8Bytes } from "./text";
import { priced, Usage as ModelUsage, type UsageReceipt } from "./usage";
import {
  Egress, type EgressOpened, type EgressReceipt, type EgressRefusal, egressRequest,
  type SandboxBinding, sandboxBinding, settleRequest,
} from "./egress";
import {
  type EgressPolicy, networkDocument, type NetworkPlan, runUrls, sandboxEnv,
} from "./network";
import {
  CLOSED, Doorbell, type RingAnswer, runWakes, WAKE_RETRY_S, WAKE_SEND_MS, type WakeSender,
  WAKES_PER_ALARM,
} from "./wakes";
import { eventKind, type Events, follows, kindFor, wakeKinds } from "./wake-kinds";

/**
 * Whose object this is, who is asking and through what: set by the binder from a
 * verified credential, the one binding every door takes. `caller` is the owner's own
 * account, or, at the collection doors alone, another owner visiting (origin null).
 * `origin` is the verified outside client the owner's account acts through (an outside
 * agent), or null. The object takes every sender and reader from here, never from a
 * request field.
 */
/** An inbound event's binding at any door but `inboundAccept`, and the reverse. */
export const INBOUND_ONLY = "an inbound event's route reaches the inbound door alone";
export const INBOUND_DOOR = "the inbound door takes an inbound event's route alone";

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
  /**
   * An inbound event's route, as the binder read it from the directory (`h:` or `i:`): the
   * owner's binding it names, at the revision the entry carries. Only `inboundAccept` takes
   * it; every other door refuses it.
   */
  readonly inbound?: { readonly binding: string; readonly revision: number };
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

/** A launch of an agent: now, at a time, or on a cron (`agentRun`). */
export interface LaunchRequest {
  readonly agent: string;
  readonly input: string;
  readonly at?: number;
  readonly cron?: string;
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
  /** The owner's hourly cap on the agent's wakes, or null for none (the default). */
  readonly max_wakes_per_hour: number | null;
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
  /** The tool profile (`directory` or `custom`); omitted, the grant keeps its own. */
  readonly profile?: string;
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

/** A mail chain the owner reads, from one of its messages (`mailChain`). */
export interface ChainRequest {
  readonly message_id: string;
  readonly limit: number;
  readonly after?: number;
}

export interface AckRequest {
  readonly through: number;
}

/** What changed for a main agent: its unread mail, and its rooms' rows past `after`. */
export interface InboxDigestRequest {
  readonly limit: number;
  readonly after: number;
  readonly max_bytes?: number;
  readonly head_chars?: number;
}

/** The board's rows changed past `after`. */
export interface BoardDigestRequest {
  readonly limit: number;
  readonly after: number;
  readonly head_chars?: number;
}

/** A row as a digest shows it, the head of its body and how long the body is; or its deletion. */
export type RowHead =
  | (Omit<Extract<Changed, { deleted: false }>, "body">
     & { readonly head: string; readonly body_chars: number })
  | Extract<Changed, { deleted: true }>;

/** Rows changed past a digest's cursor, and the next cursor; `reset` as `Changes`'. */
export interface Heads {
  readonly cursor: number;
  readonly rows: RowHead[];
  readonly reset?: true;
}

/** One code mode script (`codeRun`). */
export interface CodeRequest {
  readonly script: string;
}

/**
 * A script's end, as pi-codemode reports it: its value or its error, its output, how many
 * tool calls ended each way, and which calls of tools that change something landed
 * (`landed`, in order, a run of the same tool and outcome as one entry with its count), so
 * an agent knows what a stopped script did. An answer past `CODE_ANSWER_MAX` is refused
 * `answer_too_large`, its value and output dropped, its calls kept.
 */
export type CodeResult =
  | { readonly ok: true; readonly value: JsonValue; readonly output: CodeOutput[];
      readonly calls: CodeCalls; readonly landed: Landed[] }
  | { readonly ok: false; readonly error: { readonly kind: string; readonly message: string };
      readonly output: CodeOutput[]; readonly calls: CodeCalls; readonly landed: Landed[] };

/** A run of one tool's calls with one outcome, in a script's order. */
export interface Landed {
  readonly name: string;
  readonly status: "ok" | "error" | "cancelled";
  readonly count: number;
}

/** A script's value: JSON, as pi-codemode hands it back (an object or array, untyped). */
export type JsonValue = null | boolean | number | string | object;
/** One item of a script's output: its text, or an image. */
export type CodeOutput = { readonly type: "text"; readonly text: string; readonly console?: true }
  | { readonly type: "image"; readonly data: string; readonly mimeType: string };
/** A script's tool calls, counted by how each ended. */
export interface CodeCalls {
  readonly ok: number;
  readonly error: number;
  readonly cancelled: number;
}

/**
 * An agent's client's MCP Events subscription, as the MCP door hands it once it verified
 * the callback and the gateway sealed it: the id the door derived (from the caller, URL,
 * event and arguments; never the client's), the event, the arguments (its command center,
 * and the board or rooms it follows), the sealed blob, and when it lapses (seconds, or null).
 */
export interface SubscribeRequest {
  readonly subscription_id: string;
  readonly name: string;
  readonly arguments: { readonly command_center_id: string; readonly board?: boolean;
                        readonly rooms?: readonly string[] };
  readonly blob: string;
  readonly kid: string;
  readonly version: number;
  readonly expires_at: number | null;
}

/** The caller that holds a subscription ending it: the id the door derived. */
export interface UnsubscribeRequest {
  readonly subscription_id: string;
}

/**
 * The owner setting an agent's doorbell: the kind it pasted a token for, the id the door
 * derived, what it follows, and the blob the gateway sealed the token and URL into.
 */
export interface AgentDoorbellRequest {
  readonly agent_id: string;
  readonly kind: string;
  readonly subscription_id: string;
  readonly board: boolean;
  readonly rooms: readonly string[];
  readonly blob: string;
  readonly kid: string;
  readonly version: number;
}

/** A key's mint (`keyMint`): its id, name and scopes, and an agent's held request. */
export interface KeyMintRequest {
  readonly key_id: string;
  readonly name: string;
  readonly scopes: unknown;
  readonly proposal?: { readonly request_id: string; readonly digest: string };
}

/** An erasure door's refusal when no erasure is under way. */
export const NO_ERASURE = "no erasure of this owner is under way";

/** What the owner types to erase its account. */
export const ERASE_CONFIRM = "erase my account";

function erasureConfirmed(request: unknown): void {
  const r = policyFields(request, ["confirm"]);
  if (r["confirm"] !== ERASE_CONFIRM) {
    throw new Invalid(`an erasure is confirmed by ${JSON.stringify(ERASE_CONFIRM)}`);
  }
}

export interface EraseRequest {
  readonly confirm: string;
}

export interface InboundCreateRequest {
  readonly kind: "capability" | "shared";
  readonly agent: string;
  readonly route: string;
  readonly profile?: unknown;
  readonly secret?: string;
}

export interface InboundSetRequest {
  readonly binding_id: string;
  readonly expected_revision: number;
  readonly enabled?: boolean;
  readonly agent?: string;
}

export interface BindingRequest {
  readonly binding_id: string;
}

/** An inbound event as the binder hands it: its id and time, its raw bytes, its headers. */
export interface InboundEvent {
  readonly event_id: string;
  readonly event_at: number;
  readonly body: Uint8Array;
  readonly headers: Readonly<Record<string, string>>;
}

export type InboundAnswer =
  | { readonly state: "admitted"; readonly receipt: string; readonly deduped: boolean }
  | { readonly state: "refused"; readonly error: string; readonly retry?: true }
  | { readonly state: "no_op"; readonly error: string };

export interface UploadStoredRequest {
  readonly intent_id: string;
  readonly chunks: readonly string[];
}

/**
 * The platform's change of the owner's admission (`admission.ts`): its admit at sign-up, or
 * a suspension's reason and whether it holds.
 */
export type AdmissionRequest =
  | { readonly admit: true }
  | { readonly reason: string; readonly suspended: boolean };

/**
 * The object's environment: four service bindings, `FILES`, to the files Worker, which
 * binds no Durable Object and no service, `GATEWAY`, to the model gateway, which binds
 * nothing, `HOOKS`, to the hooks Worker, which binds only its own class, and `SANDBOX`, to
 * the sandbox Worker (slice S), which binds only its own class and the directory and
 * reaches nothing but E2B (the config guard checks every config); and no Durable Object
 * namespace.
 */
export interface OwnerEnv {
  readonly FILES: ChunkStore;
  readonly GATEWAY: GatewayService;
  readonly HOOKS: HooksService;
  /**
   * The platform's control record alone (`c:control`, the doors' too), read by code mode's
   * calls for the gates the door read (`control-gate.ts`); never written here.
   */
  readonly CONTROL: KVNamespace;
  /**
   * The routing key the sandbox tokens are MAC'd under (`doors/src/routing-mac.ts`), a
   * Worker secret the doors Worker holds too; a lease is never opened without it.
   */
  readonly ROUTING_MAC_KEY: string;
  /**
   * The sandbox Worker (`sandbox.ts`, `platform/sandbox`). Absent (a config without it, a
   * test's), every run is refused (`SANDBOX_ABSENT`) and none reaches `running`.
   */
  readonly SANDBOX?: SandboxService;
  /**
   * The erasure queue's producer (slice L): `{owner}`, which the doors consume to make the
   * erasure's outside record (`doors/src/erase.ts`). Absent, an erasure fails loudly.
   */
  readonly ERASURES?: { send(message: { owner: string }): Promise<void> };
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

/** The sync agent's upload, begun as its lease's writer (`fileSyncUploadBegin`). */
export interface SyncUploadBeginRequest extends LeaseRequest {
  readonly size: number;
  readonly chunks: string[];
}

/** The sync agent's chunk bytes for its own upload (`fileSyncUploadChunks`). */
export interface SyncUploadChunksRequest extends LeaseRequest {
  readonly intent_id: string;
  readonly chunks: { readonly sha256: string; readonly bytes: Uint8Array }[];
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

/** A history read: a row's key, and the revision to read before for older entries. */
export interface HistoryRequest extends KeyRequest {
  readonly before_revision?: number;
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
  /**
   * A run's call: its grant's run, scopes and agent, every door held to the scopes and the
   * ledger acting as the agent; null otherwise.
   */
  readonly run: { readonly id: string; readonly scopes: readonly string[];
                  readonly agent: string; readonly stamp: Stamped | null } | null;
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

/** The board (`collections.ts`), and every room's prefix: `room:<app>:<room>`. */
const BOARD = "board";
const ROOM_PREFIX = "room:";
/** Code mode's tool (`doors/src/tools.ts`), and its refusals. */
const CODE = "code";
export const CODE_MODE_OFF = "code_mode_off: the owner's policy has code mode off";
export const CODE_NOT_SERVED = "tool_not_served: not in this caller's tool profile";
const CODE_TIMED_OUT = "the script is past its time";
/**
 * The most a script's answer may take as V8 serializes it (`serializedBytes`): Workers RPC's
 * 32 MiB of serialized arguments or return value (its own error: "Serialized RPC arguments
 * or return values are limited to 32MiB"), less 1 MiB for the RPC's own envelope.
 */
export const CODE_ANSWER_MAX = 31 * 1024 * 1024;
export const CODE_ANSWER_TOO_LARGE = "answer_too_large: the script's value and output are " +
  "past the 31 MiB an answer across the RPC carries; return less, or write it with a tool";
/** A string V8 keeps one byte a unit: every unit Latin-1. */
const ONE_BYTE = /^[\u0000-\u00ff]*$/;
/** A value's per-item framing in V8's serialization, at most: a tag and a length or a number. */
const FRAME_BYTES = 10;

/**
 * At least what V8's serializer takes for a JSON value: a string one byte a unit when every
 * unit is Latin-1, else two bytes a unit (the whole string, however few units are wide), and
 * each item's framing; an object's keys as strings.
 */
export function serializedBytes(value: unknown): number {
  if (typeof value === "string") {
    return FRAME_BYTES + (ONE_BYTE.test(value) ? 1 : 2) * value.length;
  }
  if (Array.isArray(value)) {
    return value.reduce<number>((bytes, item) => bytes + serializedBytes(item), FRAME_BYTES);
  }
  if (typeof value === "object" && value !== null) {
    return Object.entries(value).reduce<number>(
      (bytes, [key, item]) => bytes + serializedBytes(key) + serializedBytes(item), FRAME_BYTES);
  }
  return FRAME_BYTES;
}
/** A digest's head of each body, in characters, unless the caller names another. */
const HEAD_CHARS = 512;

/** A digest's `head_chars`: a whole number of characters, `HEAD_CHARS` when omitted. */
function headChars(value: unknown): number {
  if (value === undefined) {
    return HEAD_CHARS;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Invalid("head_chars is a whole number of characters");
  }
  return value;
}

/** Changed rows with their placed bodies (ADR-035) read from R2 set in them. */
function filledChanges(changes: Changes, bodies: ReadonlyMap<string, string>): Changes {
  return { ...changes, rows: changes.rows.map((row) => row.deleted ? row
    : filled(row as Extract<Changed, { deleted: false }> & { content_id?: string }, bodies)) };
}

/** Changed rows as a digest shows them: each body cut to its first `chars` characters. */
function heads(changes: Changes, chars: number): Heads {
  return {
    ...(changes.reset === true ? { reset: true } : {}),
    cursor: changes.cursor,
    rows: changes.rows.map((row): RowHead => {
      if (row.deleted) {
        return row;
      }
      const { body, ...rest } = row;
      return { ...rest, head: [...body].slice(0, chars).join(""), body_chars: codePoints(body) };
    }),
  };
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
function contentsOf(rows: readonly object[]): string[] {
  return rows.flatMap((row) => {
    const id = (row as { readonly content_id?: string }).content_id;
    return id === undefined ? [] : [id];
  });
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

/**
 * What a writing door's answer shows of placed bodies (ADR-035): the content ids it names,
 * located in its unit, and the answer with them read in.
 */
interface Shown<T> {
  readonly contents: (value: T) => readonly string[];
  readonly fill: (value: T, bodies: ReadonlyMap<string, string>) => T;
}

/** A conflict's current row, read back whole, as `get` and `pull` show it. */
function conflictShown<T extends object>(): Shown<T> {
  return {
    contents: (value) => "conflict" in value && value.conflict !== null
      ? contentsOf([value.conflict as Row]) : [],
    fill: (value, bodies) => "conflict" in value && value.conflict !== null
      ? { ...value, conflict: filled(value.conflict as Row, bodies) } : value,
  };
}

/**
 * Whose files scope a door's rows count in (ADR-035): the owner's, an outside client's
 * (`client:<id>`), or a visiting owner's own, `client:` and its principal's hash, so a
 * visitor is held to the owner's `client_share` as a client is.
 */
function filerOf(account: Account): string {
  if (account.origin !== null) {
    return `client:${account.origin.client}`;
  }
  return account.caller === account.owner ? "owner"
    : `client:${createHash("sha256").update(`visitor\n${account.caller}`, "utf8").digest("hex")}`;
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
/**
 * The bytes a mail's run's input takes in the waiting inputs' queue: none, since its row
 * keeps only the mail's reference (ADR-035); the queue's rows bound it.
 */
const MAIL_INPUT_BYTES = 0;
/** How many times a placed body begins again past a chunk the sweep is deleting. */
const PLACE_TRIES = 5;
/** What only the owner does with a mail chain: read it (`mailChain`). */
const CHAINS = "read its agents' mail chains";
/** An agent's file, whose agent is a ledger identity while it exists (slice ID). */
const AGENT_FILE = /^\/workspace\/agents\/([^/]+)\/agent\.json$/;

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
  /** The doorbell (`wakes.ts`), or null for none: read at each use, never kept. */
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
             // The kill switch closed: nothing claimed, and the job held a retry out.
             run: async (_now, until) => {
               const done = await deps.runWakes(deps.ledger, deps.sender(), deps.clock,
                                                deps.write, WAKES_PER_ALARM, WAKE_SEND_MS, until);
               return done === CLOSED ? { hold: deps.clock() + WAKE_RETRY_S } : done;
             } },
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
  /** The erasure's wait for a put begun before its mark (`PUT_GRACE_MS`), for the tests. */
  readonly putGraceMs?: number;
  /** The sandbox's lease API, which the sandbox slice reaches in the object (`SandboxLeases`). */
  readonly sandbox?: (internal: SandboxLeases) => void;
  /** Code mode's bounds, smaller in a test than production's (`codemode.ts`). */
  readonly codemode?: CodemodeBounds;
  /** How long a script's read of the control record serves its calls (`control-gate.ts`). */
  readonly controlFreshMs?: number;
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
  /** The owner's API keys (`keys.ts`): the key store. */
  readonly #keys: Keys;
  /** The owner's inbound bindings (`inbound.ts`). */
  readonly #inbound: Inbound;
  /** The runners' events posts held at once (`EVENTS_IN_FLIGHT`). */
  readonly #posts = new PostGate();
  /**
   * A long reply's body placed before an events unit (`runEvents`), set only for that
   * synchronous unit, for its settle to post by reference (`#answer`).
   */
  #replyPlaced: Placed | null = null;
  /** Who waits on a run's settle, on this instance only: a door's bounded wait. */
  readonly #waiters = new Map<string, Set<() => void>>();
  /** Code mode's bounds: production's, unless a test passed smaller. */
  readonly #codemode: CodemodeBounds;
  /** How long a script's read of the control record serves its calls. */
  readonly #controlFreshMs: number;
  /** The owner this object is named by (`ctx.id.name`), refused when it has none. */
  readonly #ownerName: () => string;
  /** The sandbox's lease, owner-internal: the runs slice calls it in process. */
  readonly #sandbox: SandboxLeases;
  readonly #schedule: Schedule;
  readonly #lifecycle: Lifecycle<OwnerEnv>;
  /**
   * What rings outside agents' doorbells (`wakes.ts`), called in process by the `wakes`
   * lane: every wake kind's request from its record, after the kill switch (`CONTROL`).
   */
  readonly #sender: WakeSender;
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
    // Retention kept in R2 makes room first at the byte quota: the oldest placed history,
    // then the oldest acked mail with a placed body (ADR-035).
    this.#files = new Files(storage, env.FILES, () => fileLimits(
      this.#policy.read().document,
      keptTiers(storage)?.[ownerTier(storage)].storage_bytes ?? null), write,
      (filer) => this.#collections.reclaim(filer) || this.#ledger.reclaim(filer),
      // Each agent's ledger identity follows its `agent.json`, in the file change's own unit.
      (path, present, now) => {
        const agent = AGENT_FILE.exec(path)?.[1];
        if (agent !== undefined) {
          if (present) {
            this.#ledger.appear(agent, now);
          } else {
            this.#ledger.retire(agent, now);
          }
        }
      });
    this.#bodies = new Bodies(this.#files, storage, write);
    // An outside writer's share of the collections is the owner's own `client_share`.
    this.#collections = new Collections(storage, undefined, this.#bodies.adopt,
                                        () => fileLimits(this.#policy.read().document, null));
    this.#grants = new Grants(storage, this.#policy);
    this.#sessions = new Sessions(storage);
    this.#ledger = new Ledger(storage, this.#grants, undefined, admitted,
                              () => this.#collections.clock(), this.#bodies.adopt);
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
    this.#sender = new Doorbell({
      ledger: this.#ledger, live: (client) => this.#grants.live(client), write, clock,
      reads: (client, center) => this.#grants.reads(client, { universe: center,
                                                               agent: MAIN_AGENT,
                                                               capability: "read_graph" }),
      touched: (rooms, after) => this.#collections.touched(rooms, after),
      notice: (type, detail) => write(() => this.#notices.add(type, detail, clock())),
      // The kill switch: the control record, read once a pass, as the doors read it.
      gate: async () => await this.#control().refusal("outside", owner()) === null,
      // The secret is the gateway's alone: it opens the blob and makes the send.
      ring: (subscription, version, blob, previous, request): Promise<RingAnswer> =>
        env.GATEWAY.ringDoorbell(owner(), subscription, version, blob, previous, request),
    });
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
    this.#keys = new Keys(storage);
    this.#inbound = new Inbound(storage);
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
        lapsed: (now) => {
          // Each lapsed lease's sandbox paused, as a given-back one's is (slice S).
          for (const leaseId of this.#files.leasesLapsed(now)) {
            queueMicrotask(() => void this.#sandboxRelease(leaseId));
          }
        },
      },
      stop: (runs) => this.#cancelInSandbox(runs),
      answer: (answer) => this.#answer(answer),
      mailSettled: (mail, agent) => this.#ledger.settled(mail, agent),
      ended: (run) => {
        this.#egress.end("run", (live) => live.run === run);
        this.#relay.settled(run);
        this.#wake(run);
      },
      leaseEnded: (leaseId) => {
        this.#egress.end("lease", (live) => live.lease === leaseId);
        // Once the unit is over, its sandbox paused: no run lives in it (slice S).
        queueMicrotask(() => void this.#sandboxRelease(leaseId));
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
      // Mail no run has taken is the lane's work now, while the owner is admitted; held mail
      // only once the inputs' queue has room for it.
      runs: { next: (now) => admitted() && this.#ledger.mailDue(
                this.#runs.queueRoom(MAIL_INPUT_BYTES)) ? now : this.#runs.next(now, admitted()),
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
      // Every sandbox of the owner killed, paused ones included, and the sandbox Worker's
      // state of them deleted (slice S review H-6).
      // A config with no sandbox bound fails the erasure loudly, never skips it.
      sandboxes: async () => {
        if (this.env.SANDBOX === undefined) {
          throw new Error("the erasure cannot reach the sandbox Worker: SANDBOX is not bound");
        }
        await this.env.SANDBOX.erase(this.#ownerName());
      },
      // The outside record: the doors told through the erasure queue until they confirm.
      outside: async () => {
        if (await this.#door(() => outsideDone(storage))) {
          return true;
        }
        if (this.env.ERASURES === undefined) {
          throw new Error("the erasure cannot reach the doors: ERASURES is not bound");
        }
        await this.env.ERASURES.send({ owner: this.#ownerName() });
        return false;
      },
      // Every R2 copy of the owner's: files and placed bodies alike are the files plane's
      // chunks (ADR-035), deleted by the namespace's prefix once every put this instance
      // began has landed (no new one begins under the mark), and again after the wipe.
      blobs: async () => {
        const ns = this.#files.storedNamespace();
        await this.#files.settled();
        if (ns !== null) {
          await this.#files.eraseNamespace(ns);
        }
        return ns;
      },
      blobsAgain: async (ns) => {
        if (ns !== null) {
          await this.#files.eraseNamespace(ns);
        }
      },
      putGraceMs: internals.putGraceMs ?? PUT_GRACE_MS,
      wipe: () => this.#gate.wipe(writeTombstone),
      retryMs: internals.eraseRetryMs ?? ERASE_RETRY_MS,
    });
    internals.erasure?.(this.#erasure);
    this.#sandbox = this.#sandboxLeases();
    internals.sandbox?.(this.#sandbox);
    this.#codemode = internals.codemode ?? {};
    this.#controlFreshMs = internals.controlFreshMs ?? CONTROL_FRESH_MS;
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
    const grant = this.#grants.admit(origin.client, source.kind, signedIn, clock());
    if (source.kind === "api_key") {
      this.#keys.used(source.ref, Math.floor(clock()));  // the key store's last use, coarse
    }
    return { owner, caller, origin, grant, run: null };
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
             run: { id: grant.run, scopes: grant.scopes, agent: grant.agent,
                    stamp: grant.stamp } };
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
      { client: string; mode: string; scopes: Scope[]; profile: string; generation: number;
        revision: number }> {
    return this.#synced(() => this.#widening(binding, "work", "outside_set", request,
                                             "change an outside client's grant",
                                             (_account, id) => {
      const r = policyFields(request, ["client", "source", "mode", "scopes",
                                       "expected_revision", "name"], ["profile"]);
      return this.#storage.transactionSync(() => {
        const { moved, ...set } = this.#grants.set(id, r as unknown as GrantRequest, clock());
        if (moved) {
          this.#endClient(set.client);
        }
        if (set.mode === "revoked") {
          this.#keys.revoked(set.client, Math.floor(clock()));
          this.#evictRevoked();
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
        this.#keys.revoked(revoked.client, Math.floor(clock()));
        this.#evictRevoked();
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
      return this.#storage.transactionSync(() => {
        const forgot = this.#grants.forgetKey(r["client"], clock());
        if (forgot.forgotten) {
          this.#keys.forgotten(forgot.client);
        }
        return forgot;
      });
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
        return this.#storage.transactionSync(() => {
          const forgot = this.#grants.forgetRevoked(id, r["clients"], clock());
          for (const client of forgot.forgotten) {
            this.#keys.forgotten(client);
          }
          return forgot;
        });
      });
    });
  }

  // -- THE KEY STORE: the owner's API keys (slice L; `keys.ts`) ------------------------

  /**
   * Mint an API key's record and its grant, in the owner's session, in one unit: the live
   * keys counted there (`MAX_KEYS`, strict), the grant set (source `api_key`, scoped,
   * revision 0), an agent's held key request applied with it when one is named
   * (`proposal`), and the key's row. The binder then writes the key's directory entry and
   * shows its secret once (`doors/src/api-keys.ts`); the object never sees the secret.
   */
  keyMint(binding: Binding, request: KeyMintRequest): Promise<ApiKey & { scopes: Scope[];
      revision: number }> {
    return this.#synced(() => this.#widening(binding, "work", "key_mint", request,
                                             "mint an API key", (_account, id) => {
      const r = policyFields(request, ["key_id", "name", "scopes"], ["proposal"]);
      const key = keyId(r["key_id"]);
      const name = keyName(r["name"]);
      const now = Math.floor(clock());  // whole seconds: the directory entry's mint time
      return this.#storage.transactionSync(() => {
        const row = this.#keys.add(key, apiKeyOrigin(key).client, name, now);
        const set = this.#grants.set(id, {
          client: apiKeyOrigin(key).client, source: "api_key", mode: "scoped",
          scopes: r["scopes"] as Scope[], expected_revision: 0, name,
        } as unknown as GrantRequest, now);
        if (r["proposal"] !== undefined) {
          this.#policy.applyKeyRequest(r["proposal"], set.scopes, now);
        }
        return { ...row, scopes: set.scopes, revision: set.revision };
      });
    }));
  }

  /** The owner's API keys, live and revoked: what its key list shows, and its erasure ends. */
  keyList(binding: Binding): Promise<{ keys: ApiKey[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its API keys");
      return { keys: this.#keys.list() };
    });
  }

  // -- ERASURE: the owner's own erasure (slice L; `erase.ts`, `erasure.ts`) ------------

  /**
   * The owner erases its account, in its live session, confirmed by its typed words: the
   * object's mark first, durable before this answers (every door refuses from then on),
   * then the erasure's own job drives every other step (`erasure.ts`): the outside record
   * through the doors, the sandboxes, the R2 copies and the wipe, each retried until done,
   * none of them resting on this request finishing.
   */
  async ownerErase(binding: Binding, request: EraseRequest): Promise<{ erasing: true }> {
    const confirmed = await this.#door(() => this.#widening(binding, "reduce", "owner_erase",
                                                            request, "erase its account", () => {
      erasureConfirmed(request);
      return true;
    }));
    if (!confirmed) {
      throw new Error("an erasure's widening returned without confirming it");
    }
    await this.#erasure.begin();
    return { erasing: true };
  }

  /**
   * The erasure's outside step, as the doors take it from the erasure queue: what the
   * directory holds for the owner (its keys' ids, its inbound routes), read only while the
   * owner is marked erasing, and only by the platform's reserved caller; null once the
   * owner is wiped (its outside record confirmed before the wipe).
   */
  erasureOutside(binding: Binding): Promise<{ keys: string[]; routes: string[] } | null> {
    return this.#door(() => {
      if ((this.#tombstone || this.#erasure.erased) && binding?.caller === PLATFORM_CALLER) {
        return null;
      }
      const { caller } = this.#bind(binding, "admission", "erasure");
      this.#platformErasure(binding, caller);
      return { keys: this.#keys.ids(), routes: this.#inbound.routes() };
    });
  }

  /** The doors' confirmation that the outside record is done: the wipe may go on. */
  erasureOutsideDone(binding: Binding): Promise<{ confirmed: true }> {
    return this.#door(() => {
      if ((this.#tombstone || this.#erasure.erased) && binding?.caller === PLATFORM_CALLER) {
        return { confirmed: true } as const;
      }
      const { caller } = this.#bind(binding, "admission", "erasure");
      this.#platformErasure(binding, caller);
      markOutside(this.#storage);
      this.#erasure.resume();
      return { confirmed: true };
    });
  }

  /** The platform's reserved caller, at an erasure under way, and no one else. */
  #platformErasure(binding: Binding, caller: string): void {
    if (caller !== PLATFORM_CALLER || !("origin" in binding) || binding.origin !== null
        || binding.session !== undefined || binding.source !== undefined) {
      throw new Refused(PLATFORM_ONLY);
    }
    if (!erasing(this.#storage)) {
      throw new Refused(NO_ERASURE);
    }
  }

  // -- INBOUND: the owner's event sources (slice L; `inbound.ts`) ----------------------

  /** The owner's inbound bindings, never a secret. */
  inboundList(binding: Binding): Promise<{ bindings: InboundBinding[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its inbound bindings");
      return { bindings: this.#inbound.list() };
    });
  }

  /**
   * A new inbound binding, in the owner's session: a capability URL the binder minted (its
   * route the token's hash), with an owner-held proof if the owner adds one; or a shared
   * app's installation, which only its install flow (owed) may name.
   */
  inboundCreate(binding: Binding, request: InboundCreateRequest): Promise<InboundBinding> {
    return this.#door(() => this.#widening(binding, "work", "inbound_create", request,
                                           "add an inbound binding", () =>
      this.#inbound.create(inboundCreated(request), clock())));
  }

  /** The owner's change of a binding, in its session, against its revision. */
  inboundSet(binding: Binding, request: InboundSetRequest): Promise<InboundBinding> {
    return this.#door(() => this.#widening(binding, "work", "inbound_set", request,
                                           "change an inbound binding", () =>
      this.#inbound.set(request, clock())));
  }

  /** A binding withdrawn, a narrowing the owner's account makes without a session. */
  inboundRevoke(binding: Binding, request: BindingRequest):
      Promise<{ binding_id: string; route: string | null }> {
    return this.#door(() => {
      this.#owner(binding, "reduce", "withdraw an inbound binding");
      return this.#inbound.revoke(request);
    });
  }

  /**
   * One inbound event, as the binder proved and routed it (`doors/src/inbound.ts`): the
   * binding its route names at that route's revision, enabled; an owner-held proof checked
   * on the raw bytes; the event posted as the owner's mail to the binding's agent, keyed by
   * the binding, its revision and the event's id, so a retry is the same mail. A shared
   * app's event refused for a full mailbox counts toward its breaker; once tripped, its
   * events are a no-op until the owner changes the binding.
   */
  inboundAccept(binding: Binding, request: InboundEvent): Promise<InboundAnswer> {
    return this.#placing((): { body: unknown; writer: string } | { done: InboundAnswer } => {
      const { owner } = this.#bind(binding, "work", "inbound");
      const now = clock();
      const event = this.#inboundEvent(binding, owner, request, now);
      if ("done" in event) {
        return event;
      }
      try {
        const resend = this.#ledger.prepare(null, event.room, event.cmid, event.envelope, null);
        if (resend !== null) {
          return { done: { state: "admitted", receipt: resend.message_id, deduped: true } };
        }
      } catch (error) {
        return { done: this.#inboundRefused(event.row, error, now) };
      }
      return { body: event.envelope, writer: "owner" };
    }, (placed) => {
      const { owner } = this.#bind(binding, "work", "inbound");
      const now = clock();
      const event = this.#inboundEvent(binding, owner, request, now);
      if ("done" in event) {
        return event.done;
      }
      this.#keepTiers(binding);
      try {
        const posted = this.#ledger.post(null, event.room, event.cmid, event.envelope, null,
                                         now, null, placed, "owner");
        this.#inbound.succeeded(event.row);
        return { state: "admitted", receipt: posted.message_id, deduped: false };
      } catch (error) {
        return this.#inboundRefused(event.row, error, now);
      }
    }, { synced: true });
  }

  /**
   * An inbound event checked in its unit: the binding at the route's revision, enabled, an
   * owner-held proof on the raw bytes, and its mail's room, key and envelope; or the no-op a
   * tripped breaker answers.
   */
  #inboundEvent(binding: Binding, owner: string, request: InboundEvent, now: number):
      { done: InboundAnswer }
      | { row: InboundRow; room: string; cmid: string; envelope: string } {
    if (binding.origin !== null || binding.caller !== owner || binding.session !== undefined
        || binding.source !== undefined) {
      throw new Invalid("invalid inbound binding");
    }
    const route = binding.inbound!;
    const row = this.#inbound.current(bindingId(route.binding), route.revision);
    const r = policyFields(request, ["event_id", "event_at", "body", "headers"]);
    const eventId = r["event_id"];
    const eventAt = r["event_at"];
    const body = r["body"];
    const headers = r["headers"];
    if (typeof eventId !== "string" || !eventId.trim() || codePoints(eventId) > 256
        || typeof eventAt !== "number" || !Number.isSafeInteger(eventAt) || eventAt < 0
        || eventAt > 999_999_999_999 || !(body instanceof Uint8Array)
        || typeof headers !== "object" || headers === null || Array.isArray(headers)) {
      throw new Invalid("an inbound event is its id, its time, its bytes and its headers");
    }
    if (row.kind === "shared" && row.tripped === 1) {
      return { done: { state: "no_op", error: "breaker_tripped" } };
    }
    const proof = ownerProof(row);
    if (proof !== null && !proves(proof.profile, headers as Record<string, string>, body,
                                  proof.secret, Math.floor(now))) {
      throw new Refused(PROOF_INVALID);
    }
    // An HMAC with no timestamp signs the body alone, so a captured body could come again
    // under a new event id: such an event is keyed by its bytes, a replay always deduped.
    const unstamped = proof?.profile.method === "hmac-sha256"
      && proof.profile.timestamp_header === undefined;
    const identity = unstamped
      ? `body:${createHash("sha256").update(body).digest("hex")}` : `id:${eventId}`;
    const key = createHash("sha256")
      .update(`tinyassets-inbound\0${row.binding_id}\0${row.revision}\0${identity}`, "utf8")
      .digest("hex");
    const envelope = JSON.stringify({ inbound: {
      binding: row.binding_id, event_id: eventId, event_at: eventAt,
      raw_base64: Buffer.from(body).toString("base64") } });
    return { row, room: `dm:${nativeId(row.agent)}`, cmid: `inb_${key}`, envelope };
  }

  /**
   * An inbound event the agent's mail refused: a full mailbox counts toward a shared app's
   * breaker (and its trip is the owner's notice); the rest are the event's own refusals.
   */
  #inboundRefused(row: InboundRow, error: unknown, now: number): InboundAnswer {
    if (error instanceof Refused && /mailbox is full|mail is full/.test(error.message)) {
      if (row.kind === "shared" && this.#inbound.failed(row, Math.floor(now))) {
        this.#notices.add("inbound", `An inbound app's events to ${row.agent} were paused: ` +
                          "its mailbox was full. Change the binding to resume.", now);
      }
      return { state: "refused", error: "mailbox_full", retry: true };
    }
    if (error instanceof Refused && error.message === "conflict") {
      return { state: "refused", error: "event_conflict" };
    }
    if (error instanceof Invalid && /message body/.test(error.message)) {
      return { state: "refused", error: "too_large" };
    }
    if (error instanceof Refused) {
      return { state: "refused", error: "agent_unavailable" };
    }
    throw error;
  }

  // -- THE FILE DOOR'S BYTES: the binder streams chunks to FILES (slice L; tasks 6.1) ----

  /**
   * An upload's target for the binder, which streams the file's bytes to `FILES` itself:
   * the owner's chunk namespace, the intent's chunk plan and the chunks still to send. The
   * binder is the trust root, already reaching every owner's object, so handing it this
   * owner's namespace weakens no isolation.
   */
  async fileUploadTarget(binding: Binding, request: IntentRequest):
      Promise<{ namespace: string; chunks: { sha256: string; size: number }[];
                pending: string[] }> {
    const plan = await this.#door(() => {
      this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["intent_id"]);
      return this.#files.uploadPlan(r["intent_id"]);
    });
    return { namespace: await this.#files.namespace(), ...plan };
  }

  /**
   * The binder's word that `FILES` accepted these of the intent's chunks (each put verified
   * its hash): recorded as `fileUploadChunks` records its own puts.
   */
  fileUploadStored(binding: Binding, request: UploadStoredRequest): Promise<{ stored: number }> {
    return this.#door(() => {
      this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["intent_id", "chunks"]);
      return this.#files.stored(r["intent_id"], r["chunks"]);
    });
  }

  /** In the revoke's transaction: revoked rows past their bound forgotten, keys with them. */
  #evictRevoked(): void {
    for (const client of this.#grants.evictRevoked(clock())) {
      this.#keys.forgotten(client);
    }
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

  /**
   * The owner sees every outside agent; an outside caller only its own identity's. Beside
   * them, the owner's own agents, each a ledger identity while its `agent.json` exists:
   * every one for the owner; for anyone else, the live ones its grant covers (a scoped
   * grant, or a run's under one, only those it holds `converse` on where it acts: the ones
   * it may mail).
   */
  outsideAgents(binding: Binding): Promise<{ agents: Agent[]; owner_agents: NativeAgent[] }> {
    return this.#door(() => {
      const account = this.#account(binding, "read");
      const own = account.origin === null && account.run === null;
      let natives = this.#ledger.natives(own);
      const grant = account.grant;
      if (!own && grant !== null && grant.mode === "scoped") {
        // Where it acts: a run's stamp's universe, or the command center of a client's one
        // enabled agent; none (no agent registered yet) covers none of them.
        const mine = account.origin === null ? [] : this.#ledger.agents(account.origin)
          .filter((agent) => agent.disabled_reason === null);
        const universe = account.run !== null ? account.run.stamp?.universe ?? null
          : mine.length === 1 ? mine[0]!.command_center_id : null;
        natives = natives.filter((native) => universe !== null && holds(grant, {
          universe, agent: native.agent, capability: "converse" }));
      }
      return { agents: this.#ledger.agents(account.origin), owner_agents: natives };
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

  /**
   * Only the owner itself, in its session, sets an agent's doorbell where its wake kind's
   * record has the owner hand it over (`callback: owner`: Claude's `/fire`, Grok's webhook
   * routine), for the agent's own kind: the blob the gateway sealed the pasted token and
   * URL into (`sealDoorbell`, from the owner surface), under the id the door derived, and
   * what it follows. A repeat refreshes it. The object never sees the token; a refusal's
   * receipt holds only its request's digest.
   */
  agentDoorbell(binding: Binding, request: AgentDoorbellRequest):
      Promise<{ agent_id: string; subscription_id: string }> {
    return this.#door(() => this.#widening(binding, "work", "OUTSIDE_AGENTS.doorbell", request,
                                           "set its agents' doorbells", () => {
      const r = ledgerFields(request, ["agent_id", "kind", "subscription_id", "board", "rooms",
                                       "blob", "kid", "version"]);
      const stored = this.#ledger.subscribe(null, {
        subscription_id: r["subscription_id"] as string, agent_id: r["agent_id"] as string,
        kind: r["kind"] as string, board: r["board"] as boolean,
        rooms: r["rooms"] as readonly string[], blob: r["blob"] as string,
        kid: r["kid"] as string, version: r["version"] as number, expires_at: null,
      });
      return { agent_id: stored.agent_id, subscription_id: r["subscription_id"] as string };
    }));
  }

  /** Only the owner itself, in its session, ends a doorbell it pasted, by its id. */
  agentDoorbellEnd(binding: Binding, request: UnsubscribeRequest):
      Promise<{ unsubscribed: boolean }> {
    return this.#door(() => this.#widening(binding, "reduce", "OUTSIDE_AGENTS.doorbell_end",
                                           request, "end its agents' doorbells", () => {
      const r = ledgerFields(request, ["subscription_id"]);
      return { unsubscribed: this.#ledger.unsubscribe(null, r["subscription_id"]) };
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

  /**
   * A post in a pair's room (`ledger.ts`), as the owner, an outside client's agent, or a
   * run's own agent (never the owner or client that started it). Its authority is the
   * caller's: none for the owner's own work, a client's grant at its generation, or the
   * run's stamp. Mail to one of the owner's own agents starts that agent's run (the `runs`
   * lane, which this door's unit rings), so its caller holds `converse` there too, as a
   * `converse` call would.
   */
  roomPost(binding: Binding, request: PostRequest): Promise<Posted> {
    return this.#placing((): { body: unknown; writer: string } | { done: Posted } => {
      const account = this.#account(binding, "work");
      this.#scope(account, "write_graph");
      const { r, actor } = this.#poster(account, binding, request);
      // The room, the sender, the parent, the caps and a resend, before any byte is placed.
      const resend = this.#ledger.prepare(actor, r["room_id"], r["client_message_id"],
                                          r["body"], r["parent_id"]);
      return resend !== null ? { done: resend } : { body: r["body"], writer: filerOf(account) };
    }, (placed) => {
      const account = this.#account(binding, "work");
      this.#scope(account, "write_graph");
      const { r, actor, authority } = this.#poster(account, binding, request);
      return this.#ledger.post(actor, r["room_id"], r["client_message_id"], r["body"],
                               r["parent_id"], clock(), authority, placed, filerOf(account));
    }, { synced: true });
  }

  /**
   * A post's caller checked, in each of its units: the first places nothing for a post the
   * checks refuse, and the second commits only what they still allow.
   */
  #poster(account: Account, binding: Binding, request: PostRequest) {
    const r = ledgerFields(request, ["room_id", "client_message_id", "body", "parent_id"]);
    const actor = this.#actor(account);
    const authority = this.#mailAuthority(binding, account);
    const to = this.#ledger.recipient(actor, r["room_id"]);
    if (to.native !== null) {
      this.#mailsAgent(account, authority, to.native);
      this.#keepTiers(binding);
    }
    return { r, actor, authority };
  }

  /** Who a caller is in the ledger: a run, its agent; else the owner or the outside client. */
  #actor(account: Account): Actor {
    return account.run !== null ? { native: account.run.agent } : account.origin;
  }

  /**
   * The authority a caller's mail goes under: none for the owner's own work (the owner's,
   * or an owner-started run's), else the client's grant at its generation, as the binding
   * verified it or the run's stamp holds it, and where a scoped grant acts.
   */
  #mailAuthority(binding: Binding, account: Account): MailAuthority | null {
    if (account.run !== null) {
      const stamp = account.run.stamp;
      return stamp === null || account.origin === null ? null
        : { client: stamp.client, family: account.origin.family, generation: stamp.generation,
            authenticated_at: stamp.authenticated_at, universe: stamp.universe };
    }
    if (account.origin === null || account.grant === null) {
      return null;
    }
    return { client: account.origin.client, family: account.origin.family,
             generation: account.grant.generation,
             authenticated_at: binding.authenticated_at ?? null,
             universe: this.#universe(account) };
  }

  /**
   * That a caller may start `agent`'s run by mail: a run holds `converse` among its scopes;
   * a client's scoped grant (a run's stamp's too) holds `converse` for that agent where it
   * acts. The owner's own work may.
   */
  #mailsAgent(account: Account, authority: MailAuthority | null, agent: string): void {
    if (account.run !== null) {
      this.#scope(account, "converse");
    }
    if (authority === null || account.grant?.mode !== "scoped") {
      return;
    }
    if (authority.universe === null
        || !holds(account.grant, { universe: authority.universe, agent,
                                   capability: "converse" })) {
      throw new Refused("outside client capability not granted");
    }
  }

  /**
   * The tier numbers a door's binding handed, kept for the runs the lane starts (a mail's);
   * none handed (`null`) forgets the kept ones, and that run is refused then, not this call.
   */
  #keepTiers(binding: Binding): void {
    if (binding.tiers === undefined) {
      return;
    }
    try {
      tierNumbers(this.#storage, binding.tiers, clock());
    } catch (error) {
      if (!(error instanceof Refused)) {
        throw error;
      }
    }
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
    if (set.state === "suspended") {
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
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      return this.#ledger.placedNext(account.origin, r["limit"]);
    }, (bodies) => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes"]);
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      return this.#ledger.read(account.origin, r["limit"], r["max_bytes"] ?? null,
                               r["pad_bytes"] ?? 0, clock(), bodies);
    }, (page) => page.messages.length === 0 && page.more);
  }

  mailboxAck(binding: Binding, request: AckRequest): Promise<{ reader: string; through: number }> {
    return this.#door(() => {
      const account = this.#account(binding, "reduce");
      const r = ledgerFields(request, ["through"]);
      this.#noMailbox(account);
      this.#scope(account, "write_graph");
      return this.#ledger.ack(account.origin, r["through"]);
    });
  }

  // -- DIGESTS and CODE MODE: a main agent's few calls (slice M) -------------------

  /**
   * What changed for this agent since its cursor, in one read: its unacknowledged mail
   * (`mailboxRead`'s page, from its own acknowledged cursor) and the owner's rooms' rows
   * (`room:<app>:<room>`) written past `after`, an owner-wide cursor, each as its head.
   * Held to its grant as the mailbox and the collections are: `read_graph`, and a private
   * row only to its writer. Its mail part is a mailbox read, so a run is refused it
   * (`run_no_mailbox`, slice ID), as at `mailboxRead`; a run reads the board's digest.
   */
  async inboxDigest(binding: Binding, request: InboxDigestRequest):
      Promise<{ mail: MailPage; rooms: Heads }> {
    // Its placed bodies (ADR-035), mail and rows, are read from R2 between its units, as a
    // mailbox read's; the read's unit marks only the mail it returns.
    const { mail, rooms, bodies, head } = await this.#rereading(() => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit", "after"], ["max_bytes", "head_chars"]);
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      return [...this.#ledger.placedNext(account.origin, r["limit"]),
              ...contentsOf(this.#collections.changed(account.caller, { prefix: ROOM_PREFIX },
                                                      r["after"], r["limit"]).rows)];
    }, (bodies) => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit", "after"], ["max_bytes", "head_chars"]);
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      const changes = this.#collections.changed(account.caller, { prefix: ROOM_PREFIX },
                                                r["after"], r["limit"]);
      return {
        mail: this.#ledger.read(account.origin, r["limit"], r["max_bytes"] ?? null, 0, clock(),
                                bodies),
        rooms: changes, bodies, head: headChars(r["head_chars"]),
      };
    }, ({ mail: page, rooms: changes, bodies }) => (page.messages.length === 0 && page.more)
      || contentsOf(changes.rows).some((id) => !bodies.has(id)));
    return { mail, rooms: heads(filledChanges(rooms, bodies), head) };
  }

  /** The board's rows written past `after`, each as its head, held as `collectionPull` is. */
  async boardDigest(binding: Binding, request: BoardDigestRequest): Promise<Heads> {
    const { changes, head } = await this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["limit", "after"], ["head_chars"]);
      const changes = this.#collections.changed(caller, { collection: BOARD }, r["after"],
                                                r["limit"]);
      return { value: { changes, head: headChars(r["head_chars"]) },
               contents: contentsOf(changes.rows) };
    }, (value, bodies) => ({ ...value, changes: filledChanges(value.changes, bodies) }));
    return heads(changes, head);
  }

  /**
   * The tools this caller is served (the doors list and call no others): a run's, the `run`
   * profile; an outside client's, its grant's (`custom`: the owner's policy's choice); the
   * owner's own, `custom`. `code` only while the owner's policy turns code mode on.
   */
  toolProfile(binding: Binding): Promise<{ profile: ProfileName; tools: readonly string[] }> {
    return this.#door(() => this.#profile(this.#account(binding, "read")));
  }

  #profile(account: Account): { profile: ProfileName; tools: readonly string[] } {
    const { document } = this.#policy.read();
    const profile: ProfileName = account.run !== null ? "run"
      : account.grant?.profile ?? "custom";
    const names = profile === "custom" ? customTools(document) : profileNames(profile);
    return { profile, tools: hookSettings(document).codemode ? names
                                                             : names.filter((n) => n !== CODE) };
  }

  /**
   * Code mode at the MCP door: one script calling the caller's profile's other tools as
   * `tools.<name>(args)`, its VM in the hooks Worker (`codemode.ts`), its wall time
   * `CODEMODE_TIMEOUT_MS`. Refused unless the owner's policy has `hooks.codemode` on (its
   * default) and the caller's profile holds `code`. Each call is the tool's own door with
   * the caller's binding, admitted again (grant, run, admission), held to the caller's
   * profile and the switch as they stand then, and to the platform's gates the door read
   * (the control record's kill switch for an outside client, its maintenance gate for a
   * run or the owner: `control-gate.ts`), so a revoke, a narrowed profile, the switch
   * turned off or outside access switched off refuses the script's next call. A refusal
   * reaches the script as an error with its reason; anything else as `internal error`,
   * logged here in full.
   */
  async codeRun(binding: Binding, request: CodeRequest): Promise<CodeResult> {
    const { owner, script, tools, gate } = await this.#door(() => {
      const account = this.#account(binding, "work");
      const r = fields(request, ["script"], [], "invalid code request");
      if (typeof r["script"] !== "string" || r["script"].trim() === "") {
        throw new Invalid("a script is text");
      }
      if (!hookSettings(this.#policy.read().document).codemode) {
        throw new Refused(CODE_MODE_OFF);
      }
      const { tools: names } = this.#profile(account);
      if (!names.includes(CODE)) {
        throw new Refused(CODE_NOT_SERVED);
      }
      // The gate its door read: every `/mcp` and `/api` binding carries the client's origin,
      // the owner's own MCP clients' too, so they take the outside gate; a run (the egress
      // door) and the owner's app (no origin) the maintenance gate.
      const gate: Gate = account.origin !== null && account.run === null ? "outside" : "owner";
      return { owner: account.owner, script: r["script"],
               tools: toolsNamed(names.filter((name) => name !== CODE)), gate };
    });
    const control = this.#control();
    const closed = await control.refusal(gate, owner);
    if (closed !== null) {
      throw new Refused(closed);
    }
    // The wall bound, held here too: pi-codemode's own deadline is a timer, which a script
    // calling tools back to back starved in workerd, so each call checks the clock, which
    // moves with every call's I/O, and ends the execution past it.
    const timeoutMs = this.#codemode.timeoutMs ?? CODEMODE_TIMEOUT_MS;
    const deadline = Date.now() + timeoutMs;
    const late = new AbortController();
    const sandbox = codemodeSandbox({
      ...this.#codemode,
      service: () => this.env.HOOKS,
      owner: () => owner,
      tools: tools.map((tool) => ({ name: tool.name, description: tool.description,
                                    inputSchema: tool.input,
                                    execute: (args: unknown) => {
                                      if (Date.now() >= deadline) {
                                        late.abort();
                                        throw new Error(CODE_TIMED_OUT);
                                      }
                                      return this.#scripted(binding, tool, args,
                                                            () => control.refusal(gate, owner));
                                    } })),
    });
    try {
      const result = await sandbox.execute(script, { signal: late.signal });
      const calls = { ok: 0, error: 0, cancelled: 0 };
      const landed: Landed[] = [];
      const changes = new Set(tools.filter((tool) => !tool.readOnlyHint)
        .map((tool) => tool.name));
      for (const call of result.calls) {
        calls[call.status] += 1;
        const last = landed.at(-1);
        if (!changes.has(call.name)) {
          continue;
        }
        if (last?.name === call.name && last.status === call.status) {
          landed[landed.length - 1] = { ...last, count: last.count + 1 };
        } else {
          landed.push({ name: call.name, status: call.status, count: 1 });
        }
      }
      const error = late.signal.aborted && !result.ok
        ? { kind: "timeout", message: `Execution timed out after ${timeoutMs} ms` }
        : result.ok ? null : { kind: result.error.kind, message: result.error.message };
      const answer: CodeResult = error === null
        ? { ok: true, value: (result as { value: JsonValue }).value, output: result.output,
            calls, landed }
        : { ok: false, error, output: result.output, calls, landed };
      // The answer crosses an RPC to the doors: past what one carries it is refused whole,
      // its calls kept, so the caller learns what landed.
      return serializedBytes(answer) <= CODE_ANSWER_MAX ? answer
        : { ok: false, error: { kind: "answer_too_large", message: CODE_ANSWER_TOO_LARGE },
            output: [], calls, landed };
    } finally {
      await sandbox.close();
    }
  }

  /** One tool call of a script: rechecked as `codeRun` says, then the tool's own door. */
  async #scripted(binding: Binding, tool: Tool, args: unknown,
                  gate: () => Promise<string | null>): Promise<unknown> {
    try {
      const closed = await gate();
      if (closed !== null) {
        throw new Refused(closed);
      }
      await this.#door(() => {
        const { tools } = this.#profile(this.#account(binding, "read"));
        if (!hookSettings(this.#policy.read().document).codemode) {
          throw new Refused(CODE_MODE_OFF);
        }
        if (!tools.includes(CODE) || !tools.includes(tool.name)) {
          throw new Refused(CODE_NOT_SERVED);
        }
      });
      const door = this[tool.door] as unknown as (...args: unknown[]) => Promise<unknown>;
      return await Reflect.apply(door, this, takesRequest(tool) ? [binding, args] : [binding]);
    } catch (error) {
      const refused = refusal(error);
      if (refused === null) {
        console.error(`code mode's ${tool.name} call failed`, error);
        throw new Error("internal error");
      }
      throw new Error(refused.message);
    }
  }

  /**
   * A run reads and acks no mailbox: its mail is its input, and its agent's mailbox holds
   * every authority's mail, which no one run's authority covers. A run sees its own thread,
   * under its own authority, as its conversation.
   */
  #noMailbox(account: Account): void {
    if (account.run !== null) {
      throw new Refused(RUN_NO_MAILBOX);
    }
  }

  /**
   * The owner's view of a mail chain (agents' mail to each other included), from any of its
   * messages, such as the mail a run's receipt names: oldest first, after `after`.
   */
  async mailChain(binding: Binding, request: ChainRequest):
      Promise<{ root: string; messages: ChainMail[]; more: boolean }> {
    // Its placed bodies (ADR-035) are read from R2 between its units, as a mailbox read's.
    const { root, messages, more } = await this.#rereading(
      () => this.#chain(this.#owner(binding, "read", CHAINS), request, new Map()).placed,
      (bodies) => this.#chain(this.#owner(binding, "read", CHAINS), request, bodies),
      (page) => page.placed.length > 0);
    return { root, messages, more };
  }

  /** A chain page as the owner reads it, handed the placed bodies read so far. */
  #chain(_owner: Account, request: ChainRequest, bodies: ReadonlyMap<string, string>) {
    const r = ledgerFields(request, ["message_id", "limit"], ["after"]);
    return this.#ledger.chain(r["message_id"], r["after"] ?? 0, r["limit"], bodies);
  }

  /**
   * The events this caller's client may subscribe (MCP Events `events/list`): each
   * subscribing wake kind's, to an outside client (never a run) whose grant reads somewhere.
   * Asked for a command center (`events/subscribe`, before the door sends its callback a
   * byte), only its one enabled subscribing agent's there, where it holds `read_graph`; a
   * caller with none is refused.
   */
  wakeEvents(binding: Binding, commandCenter?: string): Promise<{ events: Events[] }> {
    return this.#door(() => {
      const account = this.#account(binding, "read");
      const grant = account.grant;
      if (account.origin === null || account.run !== null || grant === null) {
        if (commandCenter !== undefined) {
          throw new Refused("only an agent's own client subscribes its doorbell");
        }
        return { events: [] };
      }
      if (commandCenter === undefined) {
        const reads = grant.mode === "full"
          || grant.scopes.some((scope) => scope.capability === "read_graph");
        return { events: reads ? wakeKinds().flatMap((kind) => kind.push?.events ?? []) : [] };
      }
      if (typeof commandCenter !== "string") {
        throw new Invalid("invalid subscription arguments");
      }
      this.#scope(account, "read_graph", commandCenter);
      const agent = this.#ledger.subscriber(account.origin, commandCenter);
      if (agent === undefined) {
        throw new Refused("not this caller's subscribing agent");
      }
      return { events: [kindFor(agent.delivery_method)!.push!.events!] };
    });
  }

  /**
   * An agent's own client subscribes (MCP Events `events/subscribe`), once the MCP door
   * verified its callback and the gateway sealed it: for its one enabled agent in the command
   * center its arguments name, where a scoped client needs `read_graph`. A run is refused.
   * The subscription is stored under the id the door derived, its lifetime as granted. The
   * answer never holds the callback.
   */
  wakeSubscribe(binding: Binding, request: SubscribeRequest):
      Promise<{ subscription_id: string; agent_id: string; expires_at: number | null }> {
    return this.#synced(() => {
      const account = this.#account(binding, "work");
      const r = ledgerFields(request, ["subscription_id", "name", "arguments", "blob", "kid",
                                       "version", "expires_at"]);
      if (account.origin === null || account.run !== null) {
        throw new Refused("only an agent's own client subscribes its doorbell");
      }
      if (typeof r["name"] !== "string" || eventKind(r["name"]) === null) {
        throw new Invalid("unknown event");
      }
      const args = r["arguments"] as Record<string, unknown> | null;
      const follow = follows(args, ["command_center_id"]);
      const center = args?.["command_center_id"];
      if (typeof center !== "string") {
        throw new Invalid("invalid subscription arguments");
      }
      this.#scope(account, "read_graph", center);
      const stored = this.#ledger.subscribe(account.origin, {
        subscription_id: r["subscription_id"] as string, command_center_id: center,
        board: follow.board, rooms: follow.rooms, blob: r["blob"] as string,
        kid: r["kid"] as string, version: r["version"] as number,
        expires_at: r["expires_at"] as number | null,
      });
      return { subscription_id: r["subscription_id"] as string, ...stored };
    });
  }

  /**
   * The caller that holds a subscription ends it (MCP Events `events/unsubscribe`), by the
   * id the door derived; repeating it is harmless. A run is refused.
   */
  wakeUnsubscribe(binding: Binding, request: UnsubscribeRequest):
      Promise<{ unsubscribed: boolean }> {
    return this.#door(() => {
      const account = this.#account(binding, "reduce");
      const r = ledgerFields(request, ["subscription_id"]);
      if (account.origin === null || account.run !== null) {
        throw new Refused("only the client that holds a subscription ends it");
      }
      return { unsubscribed: this.#ledger.unsubscribe(account.origin, r["subscription_id"]) };
    });
  }

  /** The platform's gates as this object reads them (`control-gate.ts`), from `CONTROL`. */
  #control(): ControlGate {
    return new ControlGate(this.env.CONTROL, this.#controlFreshMs);
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
        sandbox: "none" | "egress" | "sync" | "run" | "inbound" | "erasure" = "none"):
      { owner: string; caller: string } {
    if (this.#tombstone || this.#erasure.erased) {
      throw new Refused(OWNER_ERASED);
    }
    const inbound = typeof binding === "object" && binding !== null && "inbound" in binding;
    const inboundDoor = sandbox === "inbound";
    // The erasure's own platform doors are the one way in while the owner is marked, and
    // only for the platform's reserved caller.
    const erasureDoor = sandbox === "erasure" && door === "admission"
      && typeof binding === "object" && binding !== null && binding.caller === PLATFORM_CALLER;
    if (inboundDoor || sandbox === "erasure") {
      sandbox = "none";
    }
    if (erasing(this.#storage) && !erasureDoor) {
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
    // The object's own name first: an object for another owner, or for none, says so
    // whatever its admission.
    const named = this.ctx.id.name;
    if (named === undefined) {
      throw new Refused("this object is not named by an owner");
    }
    if (named !== owner) {
      throw new Refused("this object belongs to another owner");
    }
    if (door !== "admission") {
      const refusal = doorRefusal(admission(this.#storage), door);
      if (refusal !== null) {
        throw new Refused(refusal);
      }
    }
    // An inbound event's binding reaches `inboundAccept` alone, and that door nothing else.
    if (inbound !== inboundDoor) {
      throw new Refused(inbound ? INBOUND_ONLY : INBOUND_DOOR);
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
    const sql = this.#storage.sql;
    const stored = sql.exec<{ owner: string }>("SELECT owner FROM object_owner").toArray()[0];
    if (stored === undefined && sandbox !== "none") {
      // Only an object that minted a lease's tokens has a sandbox: a never-seen owner gets
      // no rows from one.
      throw new Refused(NO_SANDBOX);
    }
    // The owner is stored at its first door past the platform's admit, never by the admit.
    if (stored === undefined && door !== "admission") {
      sql.exec("INSERT INTO object_owner (only, owner) VALUES (1, ?)", owner);
    } else if (stored !== undefined && stored.owner !== owner) {
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
    // The call came from inside the lease's own sandbox: E2B's transform set that lease's
    // egress token beside the sync token (review M-5).
    const inside = sandbox.kind === "sync"
      ? this.#files.leaseByToken("egress", sandbox.egress, clock()) : null;
    if (lease === null || inside?.lease_id !== lease.lease_id
        || (request !== null && (request.lease_id !== lease.lease_id
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
  async #placing<T>(prepare: () => { body: unknown; writer: string } | { done: T },
                    act: (placed: Placed | null) => T,
                    options: { synced?: boolean; shown?: Shown<T> } = {}): Promise<T> {
    const shown = options.shown;
    const located = (value: T) => ({ value, located: this.#bodies.locate(
      shown === undefined ? [] : shown.contents(value)) });
    // A body whose chunk the sweep is deleting this moment begins again once the delete
    // lands (review rvlim L5), a few times, then the refusal stands.
    let first: { done: { value: T; located: Located } } | { placing: Placing } | undefined;
    for (let tries = 1; first === undefined; tries++) {
      try {
        first = await this.#door(():
            { done: { value: T; located: Located } } | { placing: Placing } => {
          const prepared = prepare();
          if ("done" in prepared) {
            return { done: located(prepared.done) };
          }
          const placing = this.#bodies.begin(prepared.body as string, prepared.writer);
          return placing === null ? { done: located(act(null)) } : { placing };
        });
      } catch (error) {
        if (!(error instanceof Refused) || error.message !== CHUNK_DELETING
            || tries >= PLACE_TRIES) {
          throw error;
        }
        await new Promise((resolve) => setTimeout(resolve, 100 * tries));
      }
    }
    let done: { value: T; located: Located };
    if ("done" in first) {
      done = first.done;
    } else {
      const placed = await this.#bodies.send(first.placing);
      try {
        done = await this.#door(() => located(act(placed)));
      } finally {
        await this.#bodies.release(placed);
      }
    }
    if (options.synced === true) {
      await this.#schedule.sync();
    }
    // A conflict's current row, placed, is read back whole, as a read door reads it.
    return shown === undefined || done.located.size === 0 ? done.value
      : shown.fill(done.value, await this.#bodies.fetch(done.located));
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
                    fill: (value: T, bodies: ReadonlyMap<string, string>) => T,
                    synced = false): Promise<T> {
    const { value, located } = await this.#door(() => {
      const read = unit();
      return { value: read.value, located: this.#bodies.locate(read.contents) };
    });
    if (synced) {
      await this.#schedule.sync();  // a delete that rang an agent's doorbell (`#ring`)
    }
    return fill(value, located.size === 0 ? new Map() : await this.#bodies.fetch(located));
  }

  /**
   * Ring the agents that follow `collection` for a change past `after` (slice W): only for
   * the owner's own act (an outside client's write, or a run's, rings no agent, so no two
   * agents wake each other without end, as slice ID's mail rings only the agent it
   * addresses), only for the board or a room, only for a change anyone may see, and only
   * agents with a live subscription following it whose grant reads their command center.
   * Each wake says what changed and the cursor before it, so its digest is just the change.
   */
  #ring(account: Account, collection: string, after: number): void {
    const change = collection === BOARD ? "board"
      : collection.startsWith(ROOM_PREFIX) ? "rooms" : null;
    if (account.origin !== null || account.run !== null || change === null
        || !this.#collections.touched([collection], after)) {
      return;
    }
    const agents = this.#ledger.followers(collection, change === "board", clock())
      .filter((agent) => this.#grants.reads(agent.client, {
        universe: agent.command_center_id, agent: MAIN_AGENT, capability: "read_graph" }));
    if (agents.length) {
      this.#ledger.changed(change, after, agents.map((agent) => agent.agent_id));
    }
  }

  /** A row written by compare-and-swap; the owner's own board or room write rings (`#ring`). */
  collectionWrite(binding: Binding, request: WriteRequest): Promise<{ row: Row } | Conflicted> {
    return this.#placing(() => {
      const account = this.#collector(binding, "work", "write_graph");
      const r = fields(request, ["collection", "row_id", "body", "expected_etag", "private"],
                       ["history_op"]);
      return { body: checkBody(r["body"]), writer: filerOf(account) };
    }, (placed) => {
      const account = this.#collector(binding, "work", "write_graph");
      const r = fields(request, ["collection", "row_id", "body", "expected_etag", "private"],
                       ["history_op"]);
      const collection = r["collection"] as string;
      const before = this.#collections.clock();
      const written = conflicted(() => ({
        row: this.#collections.write(account.owner, account.caller, collection,
                                     r["row_id"] as string, r["body"], r["expected_etag"],
                                     r["private"], "history_op" in r ? r["history_op"] : "write",
                                     placed, filerOf(account)),
      }));
      this.#ring(account, collection, before);
      return written;
    }, { shown: conflictShown(), synced: true });
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

  collectionHistory(binding: Binding, request: HistoryRequest): Promise<History> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["collection", "row_id"], ["before_revision"]);
      const kept = this.#collections.history(caller, r["collection"] as string,
                                             r["row_id"] as string, r["before_revision"] ?? null);
      return { value: kept, contents: contentsOf(kept.entries) };
    }, (kept, bodies) => ({ ...kept, entries: kept.entries.map((entry) => filled(entry, bodies)) }));
  }

  collectionDelete(binding: Binding, request: DeleteRequest):
      Promise<{ deleted: number } | Conflicted> {
    return this.#reading(() => {
      const account = this.#collector(binding, "reduce", "write_graph");
      this.#ownerOnly(account.owner, account.caller, "delete");
      const r = fields(request, ["collection", "row_id", "expected_etag"]);
      const before = this.#collections.clock();
      const value = conflicted(() => this.#collections.delete(
        account.caller, r["collection"] as string, r["row_id"] as string, r["expected_etag"],
      ));
      this.#ring(account, r["collection"] as string, before);
      return { value, contents: conflictShown<typeof value>().contents(value) };
    }, conflictShown<{ deleted: number } | Conflicted>().fill, true);
  }

  collectionClear(binding: Binding, request: ClearRequest): Promise<{ deleted: number }> {
    return this.#synced(() => {
      const account = this.#collector(binding, "reduce", "write_graph");
      this.#ownerOnly(account.owner, account.caller, "clear");
      const r = fields(request, ["collection", "writer"]);
      const before = this.#collections.clock();
      const cleared = this.#collections.clear(account.caller, r["collection"] as string,
                                              r["writer"]);
      this.#ring(account, r["collection"] as string, before);
      return cleared;
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
   * A change to the owner's agent definitions (`OWNER_TREES`: its agents' files, skills and
   * `agent.json`). The founder's rule (2026-11-03): every agent of the owner has full power by
   * default to do what any other of its agents can, so an agent (an outside client's call, or
   * a run) under a full grant, the default, changes them; one under a grant the owner narrowed
   * (`scoped`) is refused. The owner's own app is a human's path, so its bearer token alone
   * changes nothing there: it is a widening, taken only in the owner's live session and
   * receipted when refused, against a leaked app token.
   */
  #ownersTree(binding: Binding, account: Account, action: string, request: unknown,
              ...paths: unknown[]): void {
    if (!paths.some((path) => ownersOnly(storedPath(path)))) {
      return;
    }
    if (account.run !== null) {
      const stamp = account.run.stamp;
      if (stamp !== null && !this.#grants.full(stamp.client)) {
        throw new Refused(AGENTS_OWNER_ONLY);
      }
      return;
    }
    if (account.origin !== null) {
      if (account.grant?.mode !== "full") {
        throw new Refused(AGENTS_OWNER_ONLY);
      }
      return;
    }
    try {
      this.#sessions.live(binding.session, clock());
    } catch (error) {
      try {
        this.#policy.refusal(action, { request, origin: null }, clock());
      } catch {
        console.error("owner-session refusal receipt unavailable");  // refuse regardless
      }
      throw error;
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

  /**
   * A read door's caller: a file door's (`read_files`, every path it reaches visible), or the
   * sync agent's by its sync token (slice S), held to the same readable set its change feed
   * is filtered by (`#syncer`), so no content door shows a path the feed would not.
   */
  #reader(binding: Binding, door: "read", capability: "read_files"):
      (path: string) => boolean {
    if (binding.sandbox === undefined) {
      this.#filer(binding, door, capability);
      return () => true;
    }
    return this.#syncer(binding, door, null).test;
  }

  /** A path the reader may not see is refused as one that does not exist. */
  #visible(visible: (path: string) => boolean, path: unknown): void {
    if (typeof path === "string" && !visible(path)) {
      throw new FileFault("not_found", "no such file");
    }
  }

  fileList(binding: Binding, request: FileListRequest):
      Promise<{ entries: Entry[]; next: string | null }> {
    return this.#door(() => {
      const visible = this.#reader(binding, "read", "read_files");
      const r = fileFields(request, ["dir", "limit"], ["after"]);
      const listed = this.#files.list(r["dir"], r["after"] ?? null, r["limit"]);
      return { ...listed, entries: listed.entries.filter((entry) => visible(entry.path)) };
    });
  }

  /**
   * A file's entry and content: its bytes when small, else its chunk list for
   * `fileReadChunks`. With `etag` still current, only the entry (`not_modified`).
   */
  fileRead(binding: Binding, request: FileReadRequest):
      Promise<{ entry: Entry; not_modified?: true; inline?: Uint8Array; chunks?: ChunkRef[] }> {
    return this.#door(() => {
      const visible = this.#reader(binding, "read", "read_files");
      const r = fileFields(request, ["path"], ["etag"]);
      this.#visible(visible, r["path"]);
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
      const visible = this.#reader(binding, "read", "read_files");
      const r = fileFields(request, ["path", "version", "first", "count"]);
      this.#visible(visible, r["path"]);
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
      this.#ownersTree(binding, account, "FILES.write", request, r["path"]);
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
      this.#ownersTree(binding, account, "FILES.write", { path: r["path"] }, r["path"]);
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
      this.#ownersTree(binding, account, "FILES.mkdir", request, r["path"]);
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
      this.#ownersTree(binding, account, "FILES.remove", request, r["path"]);
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
      this.#ownersTree(binding, account, "FILES.rename", request, r["from"], r["to"]);
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
      this.#ownersTree(binding, account, "FILES.restore", request, r["path"]);
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
               prompt: asked.prompt, mail: null };
    });
    return this.#settledWithin(run, asked.waitMs, asked.client);
  }

  /**
   * A launch of one of the owner's agents on an input (`run_graph`), in a fresh conversation
   * of its own: now, or as a schedule of the `runs` lane, at a time (`at`) or at each time a
   * cron names (`cron`). A schedule's run is unattended: its source must allow that
   * (ADR-033 decision 3). An agent answers mail by being mailed itself (`roomPost`).
   */
  async agentRun(binding: Binding, request: LaunchRequest): Promise<RunView | RunSchedule> {
    const asked = await this.#synced(() => {
      const account = this.#account(binding, "work");
      const r = runFields(request, ["agent", "input"], ["at", "cron"]);
      const agent = agentId(r["agent"]);
      const universe = this.#runDoor(account, "run_graph", agent);
      const input = runText(r["input"], "an input");
      const client = account.origin?.client ?? null;
      const at = r["at"] ?? null;
      const cron = r["cron"] ?? null;
      if (at !== null && (typeof at !== "number" || !Number.isFinite(at))) {
        throw new Invalid("at is a time in seconds since the epoch");
      }
      if (cron !== null && typeof cron !== "string") {
        throw new Invalid("cron is a cron expression");
      }
      if (at === null && cron === null) {
        return { now: true as const, agent, input, client };
      }
      if (binding.tiers !== undefined) {
        tierNumbers(this.#storage, binding.tiers, clock());  // kept for the lane's runs
      }
      const { stamp, origin } = this.#initiated(binding, account, universe, agent);
      return { now: false as const, schedule: this.#runs.schedule({
        agent, input, at: at as number | null, cron: cron as string | null, stamp, origin },
                                                                  clock()) };
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
               prompt: asked.input, mail: null };
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
    // A reply this batch's settle would post, past `PLACE_BYTES`, is placed in R2 first, in
    // the gate's own first unit (ADR-035): the settle's unit then posts it by reference, so a
    // crash between leaves only an orphan upload for the sweep, never a lost reply.
    const placing = await this.#door(() => {
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
        const reply = this.#runs.answering(binding.run as string, lease, r.frames, r.end);
        return reply === null || utf8Bytes(reply.result) <= PLACE_BYTES ? null
          : { placing: this.#beginReply(reply) };
      } catch (error) {
        this.#posts.leave();
        throw error;
      }
    });
    let placed: Placed | null = null;
    try {
      placed = placing?.placing == null ? null : await this.#bodies.send(placing.placing);
      const done = await this.#door(() => {
        this.#bind(binding, "reduce", "run");
        const lease = this.#runner(binding);
        if (lease === null) {
          throw new Refused(RUN_ENDED);
        }
        const r = eventsRequest(request);
        // Another batch landed between the units (a runner's retry): the reply this settle
        // would post is not the one placed, so the batch is posted again, never dropped. A
        // reply whose writer's files refused it (no placing) settles with a notice instead.
        const reply = this.#runs.answering(binding.run as string, lease, r.frames, r.end);
        if (reply !== null && utf8Bytes(reply.result) > PLACE_BYTES
            && !placedAs(placed, reply.result)
            && !(placing !== null && placing.placing === null)) {
          throw new Refused(EVENTS_RACED);
        }
        this.#replyPlaced = placed;
        try {
          return this.#runs.events(binding.run as string, lease, r.frames, r.end, clock());
        } finally {
          this.#replyPlaced = null;
        }
      });
      this.#relay.relay(done.conversation, done.frames);
      if (done.settled !== null) {
        this.#relay.settled(binding.run as string);
        await this.#schedule.sync();
      }
      return { accepted: done.frames.length, settled: done.settled };
    } finally {
      await this.#bodies.release(placed);
      this.#posts.leave();
    }
  }

  /**
   * In a unit: a long reply's upload begun as its writer's (the run's client's, or the
   * owner's), or null when its body stays in its row or its writer's files refuse it; that
   * settle then leaves the owner a notice (`#answer`).
   */
  #beginReply(answer: MailAnswer): Placing | null {
    try {
      return this.#bodies.begin(answer.result,
                                answer.stamp === null ? "owner" : `client:${answer.stamp.client}`);
    } catch (error) {
      if (!(error instanceof Refused || error instanceof Invalid)) {
        throw error;
      }
      return null;
    }
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
  #answer(answer: MailAnswer): void {
    const authority: MailAuthority | null = answer.stamp === null || answer.origin === null
      ? null : { client: answer.stamp.client, family: answer.origin.family,
                 generation: answer.stamp.generation,
                 authenticated_at: answer.stamp.authenticated_at,
                 universe: answer.stamp.universe };
    // Past the placement threshold its body was placed before this unit (`runEvents`).
    this.#reply(answer, authority,
                utf8Bytes(answer.result) > PLACE_BYTES ? this.#replyPlaced : null);
  }

  /** A mail's reply posted in this unit, its body placed or not; a refusal a notice. */
  #reply(answer: MailAnswer, authority: MailAuthority | null, placed: Placed | null): void {
    try {
      this.#ledger.answer({ native: answer.agent }, answer.mail, answer.run, answer.result,
                          clock(), authority, placed);
    } catch (error) {
      if (!(error instanceof Refused || error instanceof Invalid)) {
        console.error("a mail's reply failed in the ledger; the run's settle stands");
      }
      this.#notices.add("mail_reply_refused", `${answer.agent}: ${
        error instanceof Error ? error.message : "the ledger failed"}`, clock());
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
                waiting: string | null = null, taken: () => void = () => {}): Promise<string> {
    const { admitted, agent, unattended } = await this.#door(() => {
      const made = ask();
      if (this.#files.stat(agentFilePath(made.agent))?.etag !== read.etag) {
        throw new Refused(AGENT_CHANGED);
      }
      const done = { agent: made.agent, unattended: made.unattended,
                     admitted: this.#runs.admit({ ...made, file: read.file }, clock(), (chosen) =>
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
      } else if (answer.code === SANDBOX_WAITING) {
        // E2B runs as many sandboxes as its plan allows: the run waits, never refused, and
        // the lane starts it again when the platform's line rings the owner (`sandboxRung`).
        // The founder's alert reads E2B's own count (owed: `sandbox-probe.md`); this line is
        // the Worker log's record of it.
        if (this.#runs.waitForSandbox(admitted.run, admitted.prompt)) {
          console.warn(`${SANDBOX_WAITING}: a run waits for a sandbox to free`);
        }
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

  /**
   * A lease that ended, its sandbox paused through the sandbox Worker (zero idle tail). A
   * release that does not reach it is logged: the sandbox Worker reaps a sandbox idle for a
   * lease's TTL on its own (by then a lease no run renews has lapsed), and keeps asking E2B
   * for a pause that did not land.
   */
  async #sandboxRelease(leaseId: string): Promise<void> {
    const sandbox = this.env.SANDBOX;
    if (sandbox === undefined) {
      return;  // no sandbox bound, so no lease was opened under one
    }
    try {
      await sandbox.release(this.#ownerName(), { lease_id: leaseId });
    } catch {
      console.error("a lease's release did not reach the sandbox; its idle reap pauses it");
    }
  }

  /**
   * The platform's line rang this owner (slice S; `sandbox/src/slots.ts`, through the doors
   * Worker's ring consumer): a sandbox freed, so each run waiting for one starts again
   * through its whole admission, oldest first. It takes no binding: it carries nothing,
   * reads nothing and grants nothing, and only the doors' consumer, which binds `OWNER`,
   * reaches it. With no run waiting the owner leaves the line, so the next owner is rung.
   * An erased or erasing owner does nothing (its erasure takes it out of the line).
   */
  async sandboxRung(): Promise<{ waiting: number }> {
    if (this.#tombstone || this.#erasure.erased || erasing(this.#storage)) {
      return { waiting: 0 };
    }
    const waiting = await this.#door(() => this.#runs.sandboxRung());
    if (waiting === 0) {
      await this.env.SANDBOX?.unwait(this.#ownerName());
    }
    await this.#schedule.sync();
    return { waiting };
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
   * its turn refuses settles `failed`, the refusal in the owner's notices); then each mail
   * to one of the owner's agents that no run took, oldest first, starting that agent's run
   * (`#deliver`); then each due schedule claimed, once, and run the same way; a refusal kept
   * on its row and in the owner's notices, never thrown.
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
        const prompt = waiting.ask.mail === null ? waiting.ask.prompt
          : await this.#mailPrompt(waiting.ask.mail.message, waiting.ask.agent);
        await this.#launch(read, () => ({
          ...waiting.ask, prompt, tiers: tierNumbers(this.#storage, undefined, clock()) }),
                           waiting.run);
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
      // A placed body (ADR-035) is read from R2 between the lane's units, as a door's.
      const next = (bodies: ReadonlyMap<string, string>) => {
        if (workRefusal(admission(this.#storage)) !== null) {
          return null;
        }
        if (this.#runs.queueRoom(MAIL_INPUT_BYTES)) {
          this.#ledger.release();  // the inputs' queue has room for any held mail again
        }
        return this.#ledger.nextMail(bodies);
      };
      const mail = await this.#rereading(() => {
        const found = next(new Map());
        return found?.content_id == null ? [] : [found.content_id];
      }, next, (found) => found?.content_id != null);
      if (mail === null) {
        break;
      }
      await this.#deliver(mail);
    }
    while (clock() < until) {
      const row = await this.#door(() => workRefusal(admission(this.#storage)) !== null
        ? null : this.#runs.claim(clock()));
      if (row === null) {
        return;
      }
      try {
        const read = await this.#readAgent(row.agent);
        const run = await this.#launch(read, () => ({
          initiator: "schedule", ...scheduleAuthority(row), agent: row.agent,
          conversation: null, request: null,
          tiers: tierNumbers(this.#storage, undefined, clock()), unattended: true,
          prompt: row.input, mail: null }));
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

  /**
   * A waiting mail run's input, read again from its mail as it starts: the row kept only the
   * mail's reference, and a placed body (ADR-035) is read from R2 between the lane's units.
   * A mail no longer held, or whose body is not its own, refuses the run.
   */
  async #mailPrompt(messageId: string, agent: string): Promise<string> {
    const mail = await this.#rereading(() => {
      const found = this.#ledger.mailFor(messageId, nativeId(agent));
      return found?.content_id == null ? [] : [found.content_id];
    }, (bodies) => this.#ledger.mailFor(messageId, nativeId(agent), bodies),
    (found) => found?.content_id != null);
    if (mail === null) {
      throw new Refused("mail_missing: the mail this run answers is no longer held");
    }
    if (mail.body === null) {
      throw new Refused(`mail_${mail.integrity}: the mail's body is ${mail.integrity}`);
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
        throw new Refused(`mail_${mail.integrity}: the mail's body is ${mail.integrity}`);
      }
      const prompt = mailInput(mail, mail.body);
      const read = await this.#readAgent(mail.agent);
      const conversation = mailConversation(mail.agent, mail.thread, origin?.client ?? null);
      await this.#launch(read, () => ({
        initiator: "mail", stamp, origin, agent: mail.agent,
        conversation: this.#runs.kept(conversation, mail.agent, origin?.client ?? null)
          ? conversation : null,
        request: createHash("sha256").update(`mail\0${mail.message_id}\0${mail.agent_id}`,
                                             "utf8").digest("hex"),
        tiers: tierNumbers(this.#storage, undefined, clock()), unattended: !mail.attended,
        prompt, mail: { message: mail.message_id, conversation } }), null,
                         () => this.#ledger.taken(mail.message_id, mail.agent_id, clock()));
    } catch (error) {
      if (!(error instanceof Refused || error instanceof Invalid)) {
        throw error;
      }
      const reason = error.message;
      if (reason === AGENT_CHANGED) {
        return;  // read again on the next turn of the pass
      }
      await this.#door(() => {
        if (reason === QUEUE_FULL) {
          this.#ledger.hold(mail.message_id, mail.agent_id, clock());
          return;
        }
        this.#ledger.undeliverable(mail.message_id, mail.agent_id, refusalCode(reason),
                                   clock());
        this.#notices.add("mail_refused", `${mail.agent}: ${reason}`, clock());
      });
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

  /**
   * The owner's own tier, for its app (`read`): its admission row's, which only the platform
   * sets. The numbers are the control record's, which the doors read and show beside it.
   */
  tierRead(binding: Binding): Promise<{ tier: TierName }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its tier");
      return { tier: ownerTier(this.#storage) };
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
  /**
   * A streaming call's beat from its door (`EGRESS_BEAT_S`): the open reservation its egress
   * token opened is still its door's, so it is not settled lost.
   */
  egressBeat(binding: Binding, request: { reservation: string }): Promise<{ beat: boolean }> {
    return this.#door(() => {
      this.#bind(binding, "reduce", "egress");
      return { beat: this.#egress.beat(sandboxBinding(binding.sandbox), request?.reservation,
                                       clock()) };
    });
  }

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
                                      ops: r["ops"] },
                                    (authority) => this.#grants.full(authority));
    });
  }

  /**
   * The sync agent's upload of a file past one inline write (`INLINE_BYTES`), through the
   * files plane's own upload (slice S review M-6): begun as the lease's own writer, so its
   * sync commit (`intent_id`) takes it, fenced by the live lease.
   */
  fileSyncUploadBegin(binding: Binding, request: SyncUploadBeginRequest):
      Promise<{ intent_id: string; missing: string[]; expires_at: number }> {
    return this.#filing(() => {
      this.#syncer(binding, "work", request);
      const r = fileFields(request, ["lease_id", "epoch", "size", "chunks"]);
      return this.#files.begin(r["size"], r["chunks"],
                               this.#files.syncWriter(r["lease_id"], r["epoch"]));
    });
  }

  /** The sync agent's chunk bytes for its own upload, at most two chunks a call. */
  async fileSyncUploadChunks(binding: Binding, request: SyncUploadChunksRequest):
      Promise<{ created: number }> {
    const r = await this.#door(() => {
      this.#syncer(binding, "work", request);
      const fields = fileFields(request, ["lease_id", "epoch", "intent_id", "chunks"]);
      if (this.#files.intentWriter(fields["intent_id"])
          !== this.#files.syncWriter(fields["lease_id"], fields["epoch"])) {
        throw new Refused(SYNC_FENCED);
      }
      return fields;
    });
    return this.#files.upload(r["intent_id"], r["chunks"]);
  }
}
