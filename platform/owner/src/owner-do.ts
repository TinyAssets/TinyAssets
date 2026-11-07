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
import { HEX64, randomHex, SIGNIN_ID } from "../../contract/src/ids";
import { codePoints, principal, utf8Bytes, utf8Decoder } from "../../contract/src/text";
import { sha256Hex } from "../../contract/src/digest";
import { type EditRefusal, type Edited, letGo, planEdit } from "./edit";
import {
  APPEND_UNITS, EDIT_MAKE_UNITS, EDIT_READ_UNITS, MOVE_PASS_UNITS, moveTableUnits, ownerSlots,
} from "./holds";
import {
  isSlotBusy, type Lease as PoolLease, slotBusy, type SlotPool, slotted,
} from "../../contract/src/slots";
import {
  BATCH_BYTES, CHUNK_BYTES, ENGINE_SIGNINS, GC_GRACE_S, INLINE_BYTES, PLACE_BYTES,
  SIGNIN_LIST_CHARS, SIGNIN_MODELS_CHARS, SLOT_WAIT_MS,
} from "../../contract/src/limits";
import { createHash } from "node:crypto";

import { Lifecycle } from "agents/lifecycle";
import { DurableObject } from "cloudflare:workers";

import type { Vendor } from "../../doors/src/apps";
import { APP_RESOURCE } from "../../doors/src/config";
import { LEASE_LIVED, mintToken, sealRoute } from "../../contract/src/routing-mac";
import {
  type ProfileName, profileNames, takesRequest, type Tool, toolsNamed,
} from "../../doors/src/tools";
import { bundledSources } from "../../sources/src/catalog";
import { runEnv } from "../../sources/src/run-env";
import { CREDENTIAL_KINDS, type CredentialKind, SourceRecordError } from "../../sources/src/schema";

import {
  admission, type Admission, doorRefusal, type DoorClass, OWNER_ERASED,
  ownerTier, PLATFORM_CALLER, PLATFORM_ONLY, PLATFORM_RESERVED, setAdmission, setTier,
  tombstoned, workRefusal, writeTombstone,
} from "./admission";
import { AGENT_UNDEFINED, type AgentFile, agentFile, agentFilePath } from "./agent-file";
import { agentId, AUTHORITY_CHANGED, type Stamped } from "./authority";
import { type CloseKind, OWNERS_SOCKET, Relay, type SocketState } from "./relay";
import { type Ask, SIGNIN_REQUIRED, type LiveRun, type MailAnswer,
         mailConversation, NO_SCHEDULE,
         type PostedFrame, type RunReceipt, Runs, RUN_ENDED, type RunSchedule,
         scheduleAuthority, } from "./runs";
import {
  EVENT_FRAMES_MAX, EVENTS_RACED, eventsBounded, FRAME_UNITS_MAX, FRAME_VERSION, PostGate,
} from "../../contract/src/run-events";
import {
  SANDBOX_ABSENT, SANDBOX_WAITING, type SandboxService, type Started, started,
} from "./sandbox";
import { TICKET_REFUSED } from "../../contract/src/socket-shape";
import { Tickets } from "./tickets";
import { type ApiKey, keyId, keyName, Keys } from "./keys";
import {
  bindingId, created as inboundCreated, Inbound, type InboundBinding, type InboundRow,
  ownerProof, PROOF_INVALID,
} from "./inbound";
import { proves } from "../../contract/src/inbound-proof";
import {
  keepTiers, keptTiers, PLATFORM_TIER_ONLY, type TierName, tierNumbers,
} from "./tier";
import {
  Bodies, bodyOffset, inlineWindow, type Located, type Placed, placedAs,
  type Placing, type Window, WINDOWS_BYTES,
} from "./bodies";
import { fitted, jsonBytes } from "../../contract/src/encoded";
import {
  bodied, type Cut, fitList, fitPage, PAGE_BYTES, RESULT_TOO_LARGE, whole as fits,
} from "./pages";
import {
  type Changed, type Changes, checkBody, Collections, Conflict, type History,
  type HistoryEntry, type Page, type Row,
} from "./collections";
import {
  Connections, type ConnectionView,
  type GatewayService, type Prepared,
} from "./connections";
import type { ModelRecord } from "../../contract/src/model-record";
import { ERASING, FileFault, Refusal } from "../../contract/src/refusal";
import {
  type Change as FileChange, type ChunkRef, type ChunkStore, type Content,
  type Entry, FileConflict, Files, type Lease, MAX_OPEN_INTENTS,
  OWNER_AUTHORITY, type SandboxLease, type SyncOp, type SyncOutcome, type Usage, WHOLE_BYTES,
} from "./files";
import { type OwnerStorage, type Write, WriteGate } from "./gate";
import { type Capability, type Client, type ConnectedApp, type Grant, Grants, holds, type Scope }
  from "./grants";
import {
  CODEMODE_TIMEOUT_MS, type CodemodeBounds, codemodeSandbox, type HooksService,
} from "./codemode";
import {
  type Actor, type Agent, type ArchivedMail, archivedHead, archivedMail, archivedRecipients,
  keptRecord, type MailParts,
  recordHead,
  type ChainMail,
  checkMessage, clock,
  type Delivery, type Fence, Ledger, type LedgerLimits, STORAGE_BUSY,
  type MailAuthority, type MailPage, RUN_NO_MAILBOX,
  apiKeyOrigin, MAIL_HEADER_BYTES, mailInput, type NativeAgent, nativeId, type Origin,
  outsideOrigin, type Posted, registrant, type Source,
} from "./ledger";
import { type Notice, Notices } from "./notices";
import {
  chunkReads, type Content as Stored, joined, lineAt, makeChunk, MOVE_BYTES,
  MOVE_RECORDS, MoveTable, pack, parseTrailer, planSegment, roomMembers, SEGMENT_LEAST_BYTES,
  type Source as Run,
} from "./segments";
import { Erasure } from "./erase";
import { ERASE_RETRY_MS, erasing, markOutside, outsideDone, PUT_GRACE_MS, wiped } from "./erasure";
import {
  type Change, customTools, fileLimits, hookSettings, modelSettings, Policy,
  type PolicyDocument, type Projection, type Receipt,
} from "./policy";
import { type Card, Registrations } from "./registrations";
import { type Lane, Schedule } from "./schedule";
import { AGENTS_OWNER_ONLY, ownersOnly } from "./paths";
import {
  ABILITY_TREES, AGENTS_DIR, MAX_NAME_BYTES, nameOf, parentOf, storedPath, within,
} from "../../contract/src/paths";
import { migrate } from "./migrate";
import { MIGRATIONS } from "./schema";
import { decodeExportCursor, encodeExportCursor } from "./export";
import { Backups, MANIFEST_PROTECT_S } from "./backup";
import { OpenExports } from "./open-exports";
import { ChangeLog } from "./changelog";
import { type BilledEvent, billedEvent, METER_BATCH, meterRead, SandboxTime } from "./sandbox-time";
import { Sessions } from "./sessions";
import {
  frontmatter, READ_DEFAULT_BYTES, SEARCH_QUERY_BYTES, SEARCH_SCAN, searchPage, textOf, titleOf,
  windowEnd, words,
} from "./file-search";
import { carried, type Receipt as StarterReceipt, Starter, type StarterPackage }
  from "./starter";
import { CONTROL_FRESH_MS, ControlGate, type Gate } from "./control-gate";
import { storageFull, storageFullRefusal } from "./storage-full";
import {
  AUTOMATON_STATES, DEFAULT_IGNORES, LIST_BYTES, ruleLines, SYNC_IGNORE, unappliedLines,
} from "../../contract/src/sync-ignore";
import { priced, Usage as ModelUsage, type UsageReceipt } from "./usage";
import {
  Egress, type EgressOpened, type EgressReceipt, type EgressRefusal, type EgressReserved,
  egressRequest, reserveRequest,
  type SandboxBinding, sandboxBinding, settleRequest,
} from "./egress";
import { EGRESS_ORIGIN, runUrls, sandboxEnv } from "../../contract/src/egress";
import { type EgressPolicy, networkDocument, type NetworkPlan } from "./network";
import {
  pastedCode, SIGNIN_OFF, signinAuthority, type SigninMode, Signins, type SigninView, signinView,
  switchedOn,
} from "./signins";
import {
  CLOSED, Doorbell, type RingAnswer, runWakes, WAKE_RETRY_S, WAKE_SEND_MS, type WakeSender,
  WAKES_PER_ALARM,
} from "./wakes";
import { eventKind, type Events, kindFor, wakeKinds } from "../../contract/src/wake-records";
import { follows } from "../../contract/src/wake-kinds";

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
   * The served records whose vendor sign-in the platform's control record switches on, as
   * the binder read it for this call (`signins.ts`, `switches`): never a request field. The
   * object keeps the last list it was handed for the runs its own lane starts.
   */
  readonly signins?: unknown;
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
  /**
   * Which of `apps.json`'s vendors an MCP client is, as its sign-in knew it (the grant's
   * props, `doors/src/apps.ts`): recorded on its grant's row, never a request field.
   */
  readonly vendor?: Vendor | null;
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

/** The owner's ask to sign in to an engine inside a sandbox (`signinStart`). */
export interface SigninStartRequest {
  readonly engine: string;
  /** `owner` (the default), or one client's id: that client's own sandbox. */
  readonly authority?: string;
  /** The owner's choice among the record's `cli.regions`, when it has any. */
  readonly region?: string;
  readonly mode?: SigninMode;
}

export interface SigninRequestById {
  readonly id: string;
}

export interface SigninCodeRequest extends SigninRequestById {
  readonly code: string;
}

/** A sign-in's runner's post: its frames, and its end. */
export interface SigninEventsRequest extends SigninRequestById {
  readonly frames: unknown;
  readonly end?: unknown;
}

export interface RunListRequest {
  readonly before?: number;
  readonly limit?: number;
}

/** A past run in the caller's list: its receipt without its result (its own door's). */
export type RunListed = Omit<RunReceipt, "result"> & { readonly seq: number };

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

/** An agents list's page: after the cursor a `next` an answer gave (`outsideAgents`). */
export interface AgentsRequest {
  readonly after?: string;
}

export interface ReadRequest {
  readonly limit: number;
  readonly max_bytes?: number;
  readonly pad_bytes?: number;
  /** The first message's body from this byte: a `body_next_offset` an answer gave. */
  readonly body_offset?: number;
  /** With `body_offset`, the message it reads on: refused when the first is another. */
  readonly message_id?: string;
}

/** A mail chain the owner reads, from one of its messages (`mailChain`). */
export interface ChainRequest {
  readonly message_id: string;
  readonly limit: number;
  readonly after?: number;
  readonly body_offset?: number;
  readonly body_message_id?: string;
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

/**
 * A row as a digest shows it, the head of its body and the body's UTF-8 bytes (`body_bytes`,
 * as a cut body's); or its deletion.
 */
export type RowHead =
  | (Omit<Extract<Changed, { deleted: false }>, "body">
     & { readonly head: string; readonly body_bytes: number })
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
 * an agent knows what a stopped script did. Its output past what one tool answer holds
 * (`PAGE_BYTES`, `pages.ts`) is cut, said so (`output_cut`); a value past it alone is
 * refused `answer_too_large`, its calls kept (`fittedCode`).
 */
export type CodeResult =
  | { readonly ok: true; readonly value: JsonValue; readonly output: CodeOutput[];
      readonly calls: CodeCalls; readonly landed: Landed[]; readonly output_cut?: OutputCut }
  | { readonly ok: false; readonly error: { readonly kind: string; readonly message: string };
      readonly output: CodeOutput[]; readonly calls: CodeCalls; readonly landed: Landed[];
      readonly output_cut?: OutputCut };

/**
 * A script's output cut to fit one answer (review M6), said so: how many items it had, and
 * how many the answer keeps whole (the next one, a text, cut at a character after them).
 */
export interface OutputCut {
  readonly items: number;
  readonly kept: number;
}

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
    throw new Refusal("invalid", `an erasure is confirmed by ${JSON.stringify(ERASE_CONFIRM)}`);
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
   * The routing key the sandbox tokens are MAC'd under (`contract/src/routing-mac.ts`), a
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
  /** At most this many characters of each small file's text (`file-search.ts`). */
  readonly head_chars?: number;
}

/** A window of a file's text (`file_read`): from a byte offset, at most `max_bytes`. */
export interface FileTextRequest {
  readonly path: string;
  readonly offset?: number;
  readonly max_bytes?: number;
}

/** A search of the owner's files (`search`), from a cursor a search answered. */
export interface FileSearchRequest {
  readonly query: string;
  readonly cursor?: string;
}

/** A file by the id a search answered (`fetch`): its stored path. */
export interface FileFetchRequest {
  readonly id: string;
}

/** A search's hit, in the shape ChatGPT's deep research and company knowledge read. */
export interface SearchResult {
  readonly id: string;
  readonly title: string;
  readonly url: string;
}

/** What `fetch` says of a file beside its text. */
export interface FetchMetadata {
  /** Its path, its id's; left out when its fields were cut to fit one answer (`fields_cut`). */
  readonly path?: string;
  /** Whether its frontmatter's fields were cut to fit one answer (review L5). */
  readonly fields_cut?: true;
  readonly size: number;
  readonly version: number;
  /** When it last changed, ISO 8601. */
  readonly modified: string;
  readonly writer: string;
  /** Its etag, which `file_edit` and `file_write` take. */
  readonly etag: string;
  readonly type?: string;
  readonly description?: string;
  readonly tags?: readonly string[];
  /** Whether the file is not UTF-8 text: its text is then empty. */
  readonly binary?: true;
  /** Whether the text stops short of the file's end: `file_read` reads on from `next_offset`. */
  readonly truncated: boolean;
  readonly next_offset?: number;
}

/** The owner app's page of the folder holding `path`: where a citation of it points. */
export function fileUrl(path: string): string {
  return `${APP_RESOURCE}#data/${encodeURIComponent(parentOf(path))}`;
}

/** A non-negative whole number of a request, or its default. */
function whole(value: unknown, what: string, fallback: number, high: number): number {
  if (value === undefined) {
    return fallback;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > high) {
    throw new Refusal("invalid", `${what} is a whole number from 0 to ${high}`);
  }
  return value;
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
  /** The file's bytes, or its text, written as UTF-8 (`file_write`, slice BRAIN). */
  readonly content: Uint8Array | string;
  readonly mode?: number;
}

/** Text added at the end of a file, which is made when absent (`file_append`). */
export interface FileAppendRequest {
  readonly path: string;
  readonly content: string;
}

/** One exact occurrence of `old_text` in a file's text replaced, at its etag (`file_edit`). */
export interface FileEditRequest {
  readonly path: string;
  readonly old_text: string;
  readonly new_text: string;
  readonly expected_etag: string;
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

/** The sync agent's own upload given up (`fileSyncUploadAbort`). */
export interface SyncUploadAbortRequest extends LeaseRequest {
  readonly intent_id: string;
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

/**
 * A row read: its key; with `body_offset`, its body from that byte (a `body_next_offset` an
 * answer gave), and with `etag`, refused when the row changed since that answer.
 */
export interface GetRequest extends KeyRequest {
  readonly body_offset?: number;
  readonly etag?: string;
}

/**
 * A history read: a row's key, and the revision to read before for older entries; with
 * `body_offset`, the page's newest entry's body from that byte.
 */
export interface HistoryRequest extends KeyRequest {
  readonly before_revision?: number;
  readonly body_offset?: number;
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
export const SYNC_FENCED = "sync_fenced: the sync lease was lost; this sync is fenced";

/**
 * The request's fields are exactly `required`, plus any of `optional`, and none is
 * `undefined`: a field sent is a value, as the Python door refused `None`.
 */
function fields(request: unknown, required: readonly string[],
                optional: readonly string[] = [],
                refusal = "invalid collection request"): Record<string, unknown> {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Refusal("invalid", refusal);
  }
  const entries = Object.entries(request);
  const keys = entries.map(([key]) => key);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))
      || entries.some(([, value]) => value === undefined)) {
    throw new Refusal("invalid", refusal);
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
 * A script's answer fitted to one tool answer (`pages.ts`, review M6): its value and calls
 * whole, its output's items while they fit and the next text cut at a character, said so
 * (`output_cut`). A value that alone passes one answer is refused `answer_too_large`, its
 * calls and what landed kept, so nothing is lost silently.
 */
function fittedCode(answer: CodeResult): CodeResult {
  if (jsonBytes(answer) <= PAGE_BYTES) {
    return answer;
  }
  const cut: OutputCut = { items: answer.output.length, kept: answer.output.length };
  const bare = { ...answer, output: [], output_cut: cut };
  if (jsonBytes(bare) > PAGE_BYTES) {
    return { ok: false, error: { kind: "answer_too_large", message: CODE_ANSWER_TOO_LARGE },
             output: [], calls: answer.calls, landed: answer.landed };
  }
  const output: CodeOutput[] = [];
  let used = jsonBytes(bare);
  for (const item of answer.output) {
    const bytes = jsonBytes(item) + (output.length > 0 ? 1 : 0);
    if (used + bytes > PAGE_BYTES) {
      const meta = jsonBytes({ ...item, text: "" }) + (output.length > 0 ? 1 : 0);
      if (item.type === "text" && used + meta + 8 <= PAGE_BYTES) {
        output.push({ ...item, text: fitted(item.text, PAGE_BYTES - used - meta + 2).text });
      }
      break;
    }
    used += bytes;
    output.push(item);
  }
  const kept = output.length > 0 && output.at(-1) !== answer.output[output.length - 1]
    ? output.length - 1 : output.length;
  return { ...answer, output, output_cut: { items: answer.output.length, kept } };
}

/** A script's value past what one tool answer holds (`pages.ts`), refused whole. */
export const CODE_ANSWER_TOO_LARGE = "answer_too_large: the script's value is past the " +
  `${PAGE_BYTES} characters one answer holds; return less, or write it with a tool and read ` +
  "it back";
/** A digest's head of each body, in characters, unless the caller names another. */
const HEAD_CHARS = 512;

/** A digest's `head_chars`: a whole number of characters, `HEAD_CHARS` when omitted. */
function headChars(value: unknown): number {
  if (value === undefined) {
    return HEAD_CHARS;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Refusal("invalid", "head_chars is a whole number of characters");
  }
  return value;
}

/**
 * What a page's unit names to read from R2: the placed bodies' content ids, and, by content
 * id, the most bytes of each window (one answer's when omitted) and the byte it starts at
 * (0 when omitted: the page's first item's `body_offset`).
 */
interface Wants {
  readonly contents: readonly string[];
  readonly maxOf?: (contentId: string) => number;
  readonly offsetOf?: (contentId: string) => number;
}

/** What each of an inbox digest's two parts, its mail and its rooms, fits: half an answer. */
const INBOX_PART = (PAGE_BYTES - 32) / 2;

/** A head's window of a placed body: at most four bytes a character, within one answer. */
function headWindow(chars: number): number {
  return Math.min(4 * chars, PAGE_BYTES);
}

/**
 * Changed rows as a digest shows them, fitted to one answer (`pages.ts`): each body cut to
 * its first `chars` characters, read from its row or its placed body's window; the rows in
 * order while they fit, at least one, the first's head cut further when it does not; the
 * page ends before a placed body not read, and when rows are left the cursor is the last
 * row's change.
 */
function heads(changes: Changes, chars: number, windows: ReadonlyMap<string, Window>,
               bytes: number = PAGE_BYTES): Heads {
  const rows: { row: RowHead }[] = [];
  for (const row of changes.rows) {
    if (row.deleted) {
      rows.push({ row });
      continue;
    }
    const { body, content_id: contentId, ...rest } =
      row as Extract<Changed, { deleted: false }> & { content_id?: string };
    const window = contentId === undefined ? inlineWindow(body, 0) : windows.get(contentId);
    if (window === undefined) {
      break;
    }
    const head = [...window.text.slice(0, 2 * chars)].slice(0, chars).join("");
    rows.push({ row: { ...rest, head, body_bytes: window.size } });
  }
  const reset = changes.reset === true ? { reset: true as const } : {};
  const fixed = jsonBytes({ ...reset, cursor: changes.cursor, rows: [] });
  const kept = rows.length === 0 ? [] : fitPage(fixed, rows, ({ row }, room, first) => {
    const fit = fits(row, room);
    if (fit !== null || !first || row.deleted) {
      return fit;
    }
    const meta = jsonBytes({ ...row, head: "" });
    if (room - meta + 2 < 2) {
      throw new Refusal("result_too_large", RESULT_TOO_LARGE);
    }
    return fits({ ...row, head: fitted(row.head, room - meta + 2).text }, room);
  }, bytes);
  if (kept.length === 0 && changes.rows.length > 0) {
    throw new Error("a digest's first row is always read");
  }
  return { ...reset,
           cursor: kept.length === changes.rows.length ? changes.cursor : kept.at(-1)!.change,
           rows: kept };
}

/**
 * A row or entry, its body taken from its row or its placed body's window (`body_offset`
 * from `offset`), as a page fits it; null for a placed body not read.
 */
function windowed<T extends { readonly body: string; readonly content_id?: string }>(
    row: T, windows: ReadonlyMap<string, Window>, offset = 0):
    { item: Omit<T, "content_id">; window: Window } | null {
  const { content_id: contentId, ...item } = row;
  const window = contentId === undefined ? inlineWindow(row.body, offset)
    : windows.get(contentId);
  return window === undefined ? null : { item, window };
}

/** Rows or entries with their windows, in order, up to the first placed body not read. */
function windowedAll<T extends { readonly body: string; readonly content_id?: string }>(
    rows: readonly T[], windows: ReadonlyMap<string, Window>, offset = 0):
    { item: Omit<T, "content_id">; window: Window }[] {
  const out: { item: Omit<T, "content_id">; window: Window }[] = [];
  for (const [index, row] of rows.entries()) {
    const read = windowed(row, windows, index === 0 ? offset : 0);
    if (read === null) {
      break;
    }
    out.push(read);
  }
  return out;
}

/**
 * One row as an answer shows it, fitted (`pages.ts`), beside the answer's other fields
 * (`fixed` bytes).
 */
function fittedRow<T extends { readonly body: string }>(
    read: { item: T; window: Window } | null, fixed: number): T & Partial<Cut> {
  if (read === null) {
    throw new Error("a placed body its row names was not read");
  }
  return bodied(read.item, read.window, PAGE_BYTES - fixed, true)!.item;
}

/**
 * A search's `next_cursor`: the stored path it resumes after, as base64url (review L5), an
 * opaque token at most 5,462 characters with nothing to escape, so a path of escaped
 * characters never makes an answer too wide to go on.
 */
function pathCursor(path: string): string {
  return Buffer.from(path, "utf8").toString("base64url");
}

function cursorPath(cursor: unknown): string {
  if (typeof cursor !== "string" || !/^[A-Za-z0-9_-]{1,5462}$/.test(cursor)) {
    throw new Refusal("invalid", "a cursor is a next_cursor a search answered");
  }
  return Buffer.from(cursor, "base64url").toString("utf8");
}

/** The agents list's three lists, in the order a page reads them. */
const AGENT_LISTS = Object.freeze(["agents", "owner_agents", "apps"] as const);

/** Each list's stable key: an agent's id, an owner agent's id, an app's name. */
function agentKey(list: (typeof AGENT_LISTS)[number], item: Agent | NativeAgent | ConnectedApp):
    string {
  return list === "apps" ? (item as ConnectedApp).name : (item as Agent | NativeAgent).agent_id;
}

/**
 * The agents a caller sees, fitted to one answer after the cursor `after` (`outsideAgents`,
 * review M3 and its verification): the three lists read as one sequence, each by its stable
 * key, so an agent added or removed between pages is neither skipped nor answered twice;
 * `next`, the last one answered's `<list>:<key>`, when some are left.
 */
function agentsPage(all: { agents: Agent[]; owner_agents: NativeAgent[]; apps: ConnectedApp[] },
                    after: string | null):
    { agents: Agent[]; owner_agents: NativeAgent[]; apps: ConnectedApp[]; next?: string } {
  const at = after === null ? null : { list: AGENT_LISTS.indexOf(
    after.slice(0, after.indexOf(":")) as (typeof AGENT_LISTS)[number]),
                                       key: after.slice(after.indexOf(":") + 1) };
  if (at !== null && at.list < 0) {
    throw new Refusal("invalid", "after is a next an agents list answered");
  }
  const items = AGENT_LISTS.flatMap((list, index) => [...all[list]]
    .map((item) => ({ list, item, key: agentKey(list, item) }))
    .sort((x, y) => (x.key < y.key ? -1 : x.key > y.key ? 1 : 0))
    .filter((entry) => at === null || index > at.list || (index === at.list && entry.key > at.key)));
  const empty = { agents: [], owner_agents: [], apps: [] };
  if (items.length === 0) {
    return empty;
  }
  const kept = fitPage(jsonBytes({ ...empty, next: `owner_agents:${"\u0001".repeat(512)}` }),
                       items, (entry, room) => fits(entry.item, room));
  const page: { agents: Agent[]; owner_agents: NativeAgent[]; apps: ConnectedApp[] } =
    { agents: [], owner_agents: [], apps: [] };
  for (const [index, entry] of kept.entries()) {
    (page[items[index]!.list] as unknown[]).push(entry);
  }
  const last = items[kept.length - 1]!;
  return kept.length === items.length ? page : { ...page, next: `${last.list}:${last.key}` };
}

/** A run as an answer shows it: its result cut further to fit one, `result_cut` then. */
function fittedRun<T extends { readonly result: string | null; readonly result_cut: boolean }>(
    view: T): T {
  if (view.result === null || jsonBytes(view) <= PAGE_BYTES) {
    return view;
  }
  const meta = jsonBytes({ ...view, result: "", result_cut: true });
  if (meta + 2 > PAGE_BYTES) {
    throw new Refusal("result_too_large", RESULT_TOO_LARGE);
  }
  return { ...view, result: fitted(view.result, PAGE_BYTES - meta + 2).text, result_cut: true };
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

/** Files an append never changes: text put after their end breaks what they mean. */
const STRUCTURED = /\.(json|jsonc|json5|toml|ya?ml)$/i;
/**
 * The tries a large append makes before it is refused `file_busy` (a small one is one unit
 * and never retries). A try is lost only when another write to the same file commits while
 * this one's chunks are out at `FILES`. Another chunked write holds one of the owner's open
 * uploads while its chunks are out, and the owner has at most `MAX_OPEN_INTENTS` open, this
 * one's among them, so those in flight when it starts can beat it at most
 * `MAX_OPEN_INTENTS - 1` times before every one has landed: `MAX_OPEN_INTENTS` tries outlast
 * them. A small whole write holds no upload; one landing in each of that many windows in a
 * row is a flood, refused loudly rather than retried without end. Measured (review rvbr):
 * 80 and then 300 concurrent appends to one small file all landed, none refused.
 */
export const APPEND_TRIES = MAX_OPEN_INTENTS;
/**
 * An outside agent's or a run's remove, rename or restore with no etag: it never removes,
 * moves or overwrites an entry it did not read (review R2-L3, as `UNREAD_WRITE`).
 */
export const UNREAD_CHANGE = "expected_etag is the entry's current etag, or \"\" where there " +
  "is none: read it first (file_stat, fetch or file_read)";

/** A change by an outside client or a run names the etag it read; the owner's app need not. */
function heldToRead(account: Account, asked: unknown): void {
  if ((account.origin !== null || account.run !== null) && typeof asked !== "string") {
    throw new Refusal("invalid", UNREAD_CHANGE);
  }
}

/** An outside agent's or a run's whole write with no etag: it never replaces a file unread. */
export const UNREAD_WRITE = "expected_etag is the file's current etag, or \"\" to create it " +
  "only when absent: read the file first (fetch, file_read or file_stat)";

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

/**
 * A read of the archive that the lane's moves and rewrites changed under each of its
 * `READ_TRIES`: retried soon, never an internal error (reviews N9 and N10).
 */
function archiveMoving(): Refusal {
  const retry = 1;
  return new Refusal(STORAGE_BUSY, `${STORAGE_BUSY}: the archive moved this mail under each ` +
    `of ${READ_TRIES} reads; try again in ${retry} second. ` +
    JSON.stringify({ retry_after_s: retry }), { retryAfterS: retry });
}

/** A post's parent, hot at its gate, moved to the archive before its unit (`roomPost`). */
class ParentMoved extends Error {
  override readonly name = "ParentMoved";
}

/** The placed contents a read's rows name (ADR-035). */
function contentsOf(rows: readonly object[]): string[] {
  return rows.flatMap((row) => {
    const id = (row as { readonly content_id?: string }).content_id;
    return id === undefined ? [] : [id];
  });
}

/**
 * What a writing door's answer shows of placed bodies (ADR-035): the content ids it names,
 * located in its unit, and the answer with their windows read in.
 */
interface Shown<T> {
  readonly contents: (value: T) => readonly string[];
  readonly fill: (value: T, windows: ReadonlyMap<string, Window>) => T;
}

/**
 * The most bytes one `file_edit` edits (ADR-037's ruling 2): no longer `WHOLE_BYTES`, since a
 * chunk-wise edit's memory does not depend on the file, but the time one call takes, its
 * tightest bound. The workerd memory leg times the worst edit (the first chunk's, longer, so
 * every chunk is read, remade and put) on the real path, with the files Worker's own hashing
 * left out: about 17 ms a chunk of wall time, at least the CPU it spent (2026-11-04, its log
 * in `limits-audit.md`); a request has 30 s of CPU, and two thirds are left for a slower
 * host, so 500 chunks (about 9 s). The leg fails if the measured time passes 30 s at this
 * figure. Its subrequests, about five a chunk, are 2,500 at most. Production's wall time a
 * chunk (R2's round trips) is owed live; an edit commits whole or not at all.
 */
export const EDIT_BYTES_MAX = 500 * CHUNK_BYTES;

/**
 * An edit's output small enough to keep inline (at most `INLINE_BYTES`, under one chunk):
 * its one chunk made, in a making step (`hold`).
 */
function inlineOf(plan: Edited, hold: <T>(work: () => Promise<T>) => Promise<T>):
    Promise<Uint8Array> {
  if (plan.sizes.length !== 1) {
    throw new Error("an inline edit's output is one chunk");
  }
  return hold(() => plan.make(0));
}

/** A written row as its write answers it: everything but its body, and the body's bytes. */
export type WrittenRow = Omit<Row, "body" | "content_id"> & { readonly body_bytes: number };

function writtenRow(row: Row): WrittenRow {
  const { body, content_id: _placed, ...rest } = row;
  return { ...rest, body_bytes: utf8Bytes(body) };
}

/** A conflict's current row, read back as `get` shows it: fitted to one answer. */
function conflictShown<T extends object>(): Shown<T> {
  return {
    contents: (value) => "conflict" in value && value.conflict !== null
      ? contentsOf([value.conflict as Row]) : [],
    fill: (value, windows) => "conflict" in value && value.conflict !== null
      ? { ...value, conflict: fittedRow(windowed(value.conflict as Row, windows),
                                        jsonBytes({ ...value, conflict: null }) - 4) } : value,
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
    : `client:${sha256Hex(`visitor\n${account.caller}`)}`;
}

/** Who a file change is by, in its history: the owner, or the outside client it came through. */
function fileWriter(account: Account): string {
  return account.origin === null ? "owner" : `client:${account.origin.client}`;
}

function flag(value: unknown, what: string): boolean {
  if (typeof value !== "boolean") {
    throw new Refusal("invalid", `${what} is true or false`);
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
/**
 * What a sync commit's write of the sync-ignore list says of the files it holds back, as its
 * sync agent counted them (`sync.ts`): the kept files its lines name, and those with changes.
 */
function heldBackOf(ops: unknown): { kept: number; changed: number } | null {
  const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0
    ? value as number : null;
  for (const op of Array.isArray(ops) ? ops as unknown[] : []) {
    const fields = (typeof op === "object" && op !== null ? op : {}) as Record<string, unknown>;
    const held = fields["held_back"] as Record<string, unknown> | undefined;
    if (fields["path"] === SYNC_IGNORE && fields["op"] === "write" && typeof held === "object"
        && held !== null) {
      const kept = count(held["kept"]);
      const changed = count(held["changed"]);
      if (kept !== null && changed !== null && changed <= kept) {
        return { kept, changed };
      }
    }
  }
  return null;
}

/**
 * A run's own MCP door never converses: work between agents is mail (ADR-034 decision 11).
 * It may launch, schedule, read and cancel runs (the founder, 2026-11-03: every agent of an
 * owner may do what any other of the owner's agents may), held to its grant.
 */
export const RUN_STARTS_NONE = "run_starts_none: a run talks to an agent by mail, never " +
  "converse";
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
    throw new Refusal("invalid", `${what} is well-formed text`);
  }
  return value;
}

function waitOf(value: unknown): number {
  if (value === undefined) {
    return 0;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0
      || value > RUN_WAIT_MS) {
    throw new Refusal("invalid", `wait_ms is 0 to ${RUN_WAIT_MS}`);
  }
  return value;
}

const SEND_ID = /^[A-Za-z0-9_-]{1,128}$/;

/** A request's retry key: the caller and its send id, hashed, so a retry is the same run. */
function requestKey(client: string | null, sendId: unknown): string {
  if (typeof sendId !== "string" || !SEND_ID.test(sendId)) {
    throw new Refusal("invalid", "client_send_id is 1 to 128 letters, digits, - and _");
  }
  return sha256Hex(`${client ?? "owner"}\0${sendId}`);
}

/** The engine, its reports in one batch at most `ENGINE_SIGNINS`, each engine once. */
function signinRequest(request: unknown):
    { engine: string; signed_in: boolean; models: string[] }[] {
  const r = fields(request, ["signins"], [], "invalid sign-in report");
  const signins = r["signins"];
  if (!Array.isArray(signins) || signins.length === 0 || signins.length > ENGINE_SIGNINS) {
    throw new Refusal("invalid", `a sign-in report names 1 to ${ENGINE_SIGNINS} engines`);
  }
  const sources = bundledSources();
  const seen = new Set<string>();
  return signins.map((entry) => {
    const f = fields(entry, ["engine", "signed_in"], ["models"], "invalid sign-in report");
    const engine = f["engine"];
    const cli = typeof engine === "string" ? sources.get(engine)?.cli : undefined;
    if (typeof engine !== "string" || cli === undefined || seen.has(engine)
        || !cli.credential.includes("vendor_login_in_sandbox")) {
      throw new Refusal("invalid", "a sign-in report names engines that sign in, each once");
    }
    seen.add(engine);
    if (typeof f["signed_in"] !== "boolean") {
      throw new Refusal("invalid", "signed_in is true or false");
    }
    const models = f["models"] ?? [];
    if (!Array.isArray(models) || models.some((model) => typeof model !== "string"
                                               || !/^[!-~]+$/.test(model))
        || JSON.stringify(models).length > SIGNIN_MODELS_CHARS) {
      throw new Refusal("invalid",
                        `models are model ids, at most ${SIGNIN_MODELS_CHARS} characters ` +
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
    throw new Refusal("invalid", "invalid run events");
  }
  if (!eventsBounded(r)) {
    throw new Refusal("invalid", `a post is at most ${EVENT_FRAMES_MAX} frames, each at most ` +
                      `${FRAME_UNITS_MAX} units of JSON`);
  }
  let last = 0;
  for (const frame of frames) {
    const f = fields(frame, ["seq", "at", "event"], [], "invalid run events");
    if (typeof f["seq"] !== "number" || !Number.isSafeInteger(f["seq"]) || f["seq"] <= last
        || typeof f["at"] !== "number" || !Number.isFinite(f["at"])) {
      throw new Refusal("invalid", "a frame's seq rises from 1, and its at is a time");
    }
    last = f["seq"];
  }
  let end: { exit_code: number | null } | null = null;
  if (r["end"] !== undefined) {
    const e = fields(r["end"], ["exit_code"], [], "invalid run events");
    const code = e["exit_code"];
    if (code !== null && (typeof code !== "number" || !Number.isSafeInteger(code))) {
      throw new Refusal("invalid", "an end's exit_code is a whole number, or null");
    }
    end = { exit_code: code as number | null };
  }
  return { frames: frames as PostedFrame[], end };
}

/** An archived record read whole in one read when no larger (a chain page's own share). */
const ARCHIVED_WHOLE_BYTES = 64 * 1024;
/** The bytes first read of a larger record for its first two lines (more when they pass it). */
const ARCHIVED_HEAD_BYTES = 16 * 1024;

/** The `archive` lane's failed passes in a row at which the owner is told, once. */
const ARCHIVE_FAILS_TOLD = 3;
/** How long a failed `archive` pass holds its lane. */
const ARCHIVE_RETRY_S = 60;
/**
 * How long a pass refused `busy` holds its lane (review round 11, Lows A and B): the pool's
 * own wait (`SLOT_WAIT_MS`), the term a full queue of larger holds turns over within (the
 * memory leg checks it), so the units held when it was refused have been given back.
 */
const ARCHIVE_BUSY_RETRY_S = SLOT_WAIT_MS / 1000;
/**
 * What the owner is told when a busy pool keeps the archive from moving while the hot set
 * stands at its ceiling: once a blocked spell (since the last segment the lane placed).
 */
const ARCHIVE_BUSY_TOLD = "New mail is held at its ceiling because this server is too busy " +
  `to move older mail out just now; nothing is lost, and the archive tries again every ${
    ARCHIVE_BUSY_RETRY_S} seconds.`;

/**
 * An archive move's reads of stored contents (review N5): each chunk read whole through
 * `FILES` (which rechecks its hash), the last one kept for the next range in it; and each
 * content the move's table gives a hash for (a placed body, its message's) checked as its
 * chunks are first read, in order, in the move's first pass (`bodiesChecked`), one hash at a
 * time, so a move of many placed bodies holds none but the one being read.
 */
class MoveReader {
  readonly #files: Files;
  readonly #table: MoveTable;
  /** Each content's next chunk to hash; its count once checked. */
  readonly #next: Uint32Array;
  #hashing: { ref: number; hash: ReturnType<typeof createHash> } | null = null;
  #checking = true;
  #last: { sha256: string; bytes: Uint8Array } | null = null;

  constructor(files: Files, table: MoveTable) {
    this.#files = files;
    this.#table = table;
    this.#next = new Uint32Array(Math.max(1, table.contents));
  }

  readonly read = async (content: Stored, index: number): Promise<Uint8Array> => {
    const sha256 = content.chunks[index];
    if (sha256 === undefined) {
      throw new Error("a move's range runs past its content");
    }
    let bytes = this.#last?.sha256 === sha256 ? this.#last.bytes : undefined;
    if (bytes === undefined) {
      // The chunk kept is let go before the next arrives: its pieces are taken (`makeChunk`
      // and `lineAt` copy what they keep), so it never outlives its use (`letGo`).
      this.letGo();
      bytes = (await this.#files.fetch([{ sha256 }]))[0];
      if (bytes === undefined) {
        throw new Error("a move's chunk was not read");
      }
      this.#last = { sha256, bytes };
    }
    const ref = Number(content.id);
    if (this.#checking && this.#table.expected(ref) !== null && this.#next[ref] === index) {
      if (this.#hashing?.ref !== ref) {
        this.#hashing = { ref, hash: createHash("sha256") };
      }
      this.#hashing.hash.update(bytes);
      this.#next[ref]!++;
      if (index + 1 === content.chunks.length) {
        if (this.#hashing.hash.digest("hex") !== this.#table.expected(ref)) {
          throw new Error("an archived body is not the body its message holds");
        }
        this.#hashing = null;
      }
    }
    return bytes;
  };

  /** The chunk kept for the next range let go, its buffer detached (`edit.ts`'s `letGo`). */
  letGo(): void {
    if (this.#last !== null) {
      letGo(this.#last.bytes);
      this.#last = null;
    }
  }

  /** After the first pass: every content with a hash read whole and checked, or loudly not. */
  bodiesChecked(): void {
    for (let ref = 0; ref < this.#table.contents; ref++) {
      if (this.#table.expected(ref) !== null
          && this.#next[ref] !== this.#table.contentOf(ref).chunks.length) {
        throw new Error("an archived body is not the body its message holds");
      }
    }
    this.#checking = false;
  }
}

/**
 * `lease` grown to `total` units now, or refused `busy` (everything it holds given back):
 * inside the alarm a pass never waits on the pool, since the object's other lanes would wait
 * with it (round 11, Low B). A call that does not fit at once leaves its queue at the next
 * turn of the event loop; the lane holds and tries again (`ARCHIVE_BUSY_RETRY_S`).
 */
async function now(lease: PoolLease, total: number): Promise<void> {
  const turn = new AbortController();
  const timer = setTimeout(() => turn.abort(slotBusy()), 0);
  try {
    await lease.grow(total, turn.signal);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * An archive move's or rewrite's holds on the isolate's pool (ADR-037), one lease in the
 * larger class (`SlotPool.lease`, the pool's one admission rule), each growth taken now or
 * refused `busy` (`now`: inside the alarm it never waits). Its table's units
 * (`moveTableUnits`), taken before its records arrive and held to its end, since the table's
 * bytes are in hand throughout; grown as a rewrite takes in a neighbour; and each pass's
 * `MOVE_PASS_UNITS` beside them, grown before the pass's first chunk arrives and given back
 * (`Lease.shrink`) once its last is gone: its waits each bring or send its bytes (a chunk
 * read, a chunk put, a unit reading hot records' lines), never a begin or a commit. Refused
 * `busy`, the pass ends with nothing changed: the lane holds and tries again
 * (`ARCHIVE_BUSY_RETRY_S`), not counted as a failure (`#archivePass`).
 */
class MoveHold {
  readonly #pool: SlotPool;
  readonly #lease: PoolLease;
  /** The units its records hold; a pass's are grown past them and given back. */
  #table: number;

  private constructor(pool: SlotPool, lease: PoolLease, table: number) {
    this.#pool = pool;
    this.#lease = lease;
    this.#table = table;
  }

  /** The units for a table of `records` records: a lease in the larger class. */
  static async take(pool: SlotPool, records: number): Promise<MoveHold> {
    const lease = pool.lease("", true);
    const table = moveTableUnits(records);
    await now(lease, table);
    return new MoveHold(pool, lease, table);
  }

  /** The units held for the table. */
  get units(): number {
    return this.#table;
  }

  /** The table grown to hold `records` records: its units grown first. */
  async table(records: number): Promise<void> {
    const table = moveTableUnits(records);
    if (table > this.#table) {
      await now(this.#lease, table);
      this.#table = table;
    }
  }

  /** One pass, holding its own units beside the table's; `letGo` drops what it kept. */
  async pass<T>(act: () => Promise<T>, letGo: () => void): Promise<T> {
    await now(this.#lease, this.#table + MOVE_PASS_UNITS);
    try {
      const done = await act();
      letGo();
      if (this.#pool.observe !== null) {
        await this.#pool.observe(this.#lease.units);
      }
      return done;
    } finally {
      letGo();
      this.#lease.shrink(this.#table);
    }
  }

  release(): void {
    this.#lease.release();
  }
}

/** The owner's notice that the archive keeps failing: its last error as it said it, if any. */
function archiveFailing(said: string | null): string {
  return `Older mail could not be moved out (${said ?? "the last tries stopped before they " +
    "could say why"}), so new mail may wait at the hot ceiling; the archive tries again ` +
    "every minute.";
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
  /** The `manifest` lane: the nightly manifest (`backup.ts`). */
  readonly manifest: {
    next(now: number): number | null;
    run(now: number, until: number): Promise<void>;
  };
  /** The `sandbox` lane: the sandbox Worker's log read into the meter (`sandbox-time.ts`). */
  readonly sandbox: {
    next(now: number): number | null;
    run(now: number, until: number): Promise<void>;
  };
  /** The `archive` lane: one move or one rewrite a pass (`segments.ts`). */
  readonly archive: {
    next(now: number): number | null;
    run(now: number, until: number): Promise<unknown>;
  };
  /** The starter package (`starter.ts`): seeded, then kept current, as its rows say. */
  readonly starter: {
    next(now: number): number | null;
    run(now: number): Promise<void>;
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
    Readonly<Record<"wakes" | "files" | "connections" | "runs" | "manifest" | "sandbox"
                    | "archive" | "starter", Lane>> {
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
    // The nightly manifest: once a night, at the owner's own hour; never a poll.
    manifest: { next: (now) => deps.manifest.next(now),
                run: (now, until) => deps.manifest.run(now, until) },
    // Sandbox time: a settled run not yet metered, or an open span's next look.
    sandbox: { next: (now) => deps.sandbox.next(now),
               run: (now, until) => deps.sandbox.run(now, until) },
    // The archive (slice STORE item 2): acked mail moved to segments from half the hot
    // ceiling, and a deleted agent's mail rewritten out of them; never a poll.
    archive: { next: (now) => deps.archive.next(now),
               run: (now, until) => deps.archive.run(now, until) },
    // The starter package: an admitted owner's seed, then each newer version once.
    starter: { next: (now) => deps.starter.next(now), run: (now) => deps.starter.run(now) },
  };
}

/** An archive move as a test sees it between its passes: its kind, what it replaces, holds. */
export interface ArchiveMoveSeen {
  readonly stream: "mail" | "rewrite";
  readonly replaced: number;
  readonly records: number;
}

/** Where a test may hold a chain read (`Internals.chainStep`). */
export type ChainStep = "fenced" | "hot";

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
  /**
   * The ledger's ceilings, smaller in a test than production's (`LEDGER_LIMITS`), so the
   * `archive` lane's moves are seen with a handful of messages.
   */
  readonly ledgerLimits?: LedgerLimits;
  /**
   * Run between an archive move's two passes, with what the move replaces and holds, so a
   * test can make its sources differ there and see the second pass refused by the chunk's
   * hash (review N5), or see each move's bound (review N7).
   */
  readonly archiveBetweenPasses?: (storage: OwnerStorage, move: ArchiveMoveSeen) => void;
  /**
   * Awaited by a chain read at two steps: `fenced`, just after the unit that took its
   * fences, and `hot`, between its archived reads and the unit that reads its hot rows; so a
   * test can replace its segments or move its mail there and see the read hold (reviews N9
   * and N10).
   */
  readonly chainStep?: (step: ChainStep) => Promise<void>;
  /**
   * The starter package the object seeds and keeps current (`starter.ts`): the one this code
   * carries when omitted, as production builds it; another for a test of updates; null for
   * none, the tests of everything else, which start from an empty workspace.
   */
  readonly starter?: StarterPackage | null;
}

/** A model's cost record with its input at its dearest rate: what a token can cost at most. */
function dearest(model: ModelRecord): ModelRecord {
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
export function egressPrice(models: readonly ModelRecord[], input: number, output: number):
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
  /** The nightly manifest (`backup.ts`). */
  readonly #backups: Backups;
  /** The exports open, each named for a week (`open-exports.ts`). */
  readonly #exports: OpenExports;
  /** The change log a generation captures (`changelog.ts`). */
  readonly #log: ChangeLog;
  /** Sandbox time, per run and per day (`sandbox-time.ts`). */
  readonly #sandboxTime: SandboxTime;
  /** Where large bodies live (ADR-035): placed in R2 through the files plane. */
  readonly #bodies: Bodies;
  readonly #registrations: Registrations;
  readonly #notices: Notices;
  /** The starter package: the default agent's files, seeded and kept current (`starter.ts`). */
  readonly #starter: Starter;
  readonly #connections: Connections;
  readonly #usage: ModelUsage;
  /** Sandbox egress: each request admitted at open and settled once it ends (`egress.ts`). */
  readonly #egress: Egress;
  /** The owner's runs: admission, grants, conversations, schedules (`runs.ts`). */
  readonly #runs: Runs;
  /** The held-back count a sync commit in progress carries for the sync-ignore list. */
  #heldBack: { kept: number; changed: number } | null = null;
  /** The owner app's sockets and the runs' live tails (`relay.ts`), a Lifecycle capability. */
  readonly #relay: Relay;
  /** The vendor's own sign-ins in progress inside a sandbox (`signins.ts`). */
  readonly #signins: Signins;
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
  /** A test's step between an archive move's passes (`Internals.archiveBetweenPasses`). */
  readonly #betweenPasses: ((storage: OwnerStorage, move: ArchiveMoveSeen) => void) | null;
  /** A test's steps in a chain read (`Internals.chainStep`). */
  readonly #chainStep: ((step: ChainStep) => Promise<void>) | null;
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
      // The one wall's refusal names the owner's tier and the next one up (`storage_full`).
      (stored, needed, held, limit) => storageFullRefusal(storageFull(
        stored, needed, held, limit, ownerTier(storage), keptTiers(storage))),
      // Mail is never let go at the figure (ADR-036): the owner deletes what it chooses.
      (filer) => this.#collections.reclaim(filer),
      // Each agent's ledger identity follows its `agent.json`, in the file change's own unit.
      (path, present, now, change) => {
        const agent = AGENT_FILE.exec(path)?.[1];
        if (agent !== undefined) {
          if (present) {
            this.#ledger.appear(agent, now);
          } else {
            this.#ledger.retire(agent, now);
          }
        }
        // Every agent may edit the sync's ignore list (the founder's every-agent-full-power
        // rule), but never unseen: each change is a receipt and one notice for the owner.
        if (path === SYNC_IGNORE) {
          this.#syncIgnoreChanged(present, now, change);
        }
      },
      // Garbage the newest finished generation names is never collected early, while it is
      // a recovery point (finished within MANIFEST_PROTECT_S).
      () => {
        const newest = this.#backups.manifests().at(-1);
        return newest === undefined || newest.finished_at < clock() - MANIFEST_PROTECT_S
          ? null : newest.started_at;
      });
    this.#exports = new OpenExports(storage);
    this.#log = new ChangeLog(storage);
    this.#bodies = new Bodies(this.#files, storage, write);
    // An outside writer's share of the collections is the owner's own `client_share`.
    this.#collections = new Collections(storage, undefined, this.#bodies.adopt,
                                        () => fileLimits(this.#policy.read().document, null));
    this.#grants = new Grants(storage, this.#policy);
    this.#sessions = new Sessions(storage);
    this.#ledger = new Ledger(storage, this.#grants, internals.ledgerLimits, admitted,
                              () => this.#collections.clock(), this.#bodies.adopt);
    this.#backups = new Backups({
      storage, write: this.#gate.quiet, store: env.FILES, space: () => this.#files.space(),
      log: this.#log,
      owner: () => this.ctx.id.name ?? "", schema: MIGRATIONS.length,
      serves: () => storage.sql.exec("SELECT 1 FROM object_owner").toArray().length > 0,
      halted: () => wiped(storage.sql) || erasing(storage),
      notice: (type, detail) => this.#notices.add(type, detail, clock()),
    });
    this.#registrations = new Registrations(storage, this.#grants);
    this.#notices = new Notices(storage);
    this.#starter = new Starter({
      storage, files: this.#files, write, admitted, clock,
      // The files' byte quota is the tier's storage: until a door hands the numbers, no write
      // lands, so a seed waits for them rather than failing (the first door's call keeps them).
      ready: () => keptTiers(storage) !== null,
      notice: (type, detail) => this.#notices.add(type, detail, clock()),
    }, internals.starter === undefined ? carried() : internals.starter);
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
          if (error instanceof Refusal && (error.code === "connection_unavailable"
                                           || error.code === "credential_expired")) {
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
      price: (models, input, output) => egressPrice(models as readonly ModelRecord[], input,
                                                    output),
      pruned: (now) => this.#usage.spendPruned(now),
    });
    this.#usage = new ModelUsage(storage, () => {
      const { generation, document } = this.#policy.read();
      return { generation, settings: modelSettings(document) };
    });
    this.#tickets = new Tickets(storage);
    this.#signins = new Signins(storage);
    this.#keys = new Keys(storage);
    this.#inbound = new Inbound(storage);
    this.#runs = new Runs(storage, {
      unadmitted: () => workRefusal(admission(storage)),
      sandboxBound: () => env.SANDBOX !== undefined,
      sources: () => bundledSources(),
      // Each source's vendor sign-in behind its own switch, as the binder last handed them.
      signinOn: (engine) => switchedOn(storage, undefined, clock()).has(engine),
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
          if (error instanceof Refusal && !error.invalid) {
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
        // A lease a sign-in holds stays: its sandbox is in use with no run (slice SIGNIN).
        release: (leaseId, epoch) => !this.#signins.holds(leaseId)
          && this.#files.leaseRelease(leaseId, epoch).released,
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
      notice: (type, detail) => this.#notices.add(type, detail, clock()),
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
    this.#sandboxTime = new SandboxTime(storage, this.#runs.meter());
    this.#relay = new Relay({
      admit: (binding, conversation, ticket) =>
        this.#door(() => this.#socketAdmit(binding, conversation, ticket)),
      check: (state) => this.#door(() => this.#socketCheck(state)),
      // A sign-in's socket gets its own tail on attach, as a conversation's gets its runs'.
      live: (conversation) => this.#door(() => this.#signinOwned(conversation) ? [conversation]
        : this.#runs.liveIn(conversation)),
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
      manifest: { next: (now) => this.#backups.next(now),
                  run: (now, until) => this.#backups.run(now, until) },
      sandbox: { next: (now) => this.#sandboxTime.next(now),
                 run: (now, until) => this.#meterPass(until) },
      archive: { next: (now) => this.#ledger.archiveDue()
                   || this.#ledger.purgedAgents().length > 0
                   || this.#ledger.archiveCompactPending() ? now : null,
                 run: () => this.#archivePass() },
      starter: this.#starter, sender: () => this.#sender, admitted, clock, write, runWakes,
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
    this.#betweenPasses = internals.archiveBetweenPasses ?? null;
    this.#chainStep = internals.chainStep ?? null;
    this.#controlFreshMs = internals.controlFreshMs ?? CONTROL_FRESH_MS;
    this.#lifecycle = lifecycle.use(this.#schedule).use(this.#erasure).use(this.#relay)
      .use(this.#relay.capability);
    void ctx.blockConcurrencyWhile(async () => {
      if (this.#tombstone) {
        return;  // an erased owner: nothing is made here again
      }
      await write(() => migrate(storage, MIGRATIONS));
      // From here on, a unit that writes (not a quiet one) marks the owner changed for the
      // nightly manifest.
      this.#gate.onChange((sql) => {
        if (this.#backups.mark(sql)) {
          // The first change since the last manifest: its job set again, once the unit is done.
          queueMicrotask(() => {
            this.#schedule.sync().catch(() => console.error("the schedule's sync failed"));
          });
        }
      });
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

  /**
   * A door's synchronous body as a quiet unit: it writes only what no export reads (an
   * export's own row, a session's renewal), so it marks nothing changed (`gate.ts`).
   */
  #quiet<T>(act: () => T): Promise<T> {
    return this.#gate.quiet(act);
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
    const unread = keptTiers(this.#storage) === null;
    keepTiers(this.#storage, binding.tiers, clock());
    // The switched-on sign-ins, likewise: kept for the lane's runs (written only when changed).
    switchedOn(this.#storage, binding.signins, clock());
    if (unread && keptTiers(this.#storage) !== null && this.#starter.version() === 0) {
      // The first numbers a new owner's door hands: its seed, which waited for them, is due.
      queueMicrotask(() => void this.#schedule.sync());
    }
    if (!("origin" in binding)) {
      throw new Refusal("invalid", "invalid outside origin");
    }
    const origin = outsideOrigin(binding.origin);
    if (caller !== owner && !(serves.visitors === true && origin === null)) {
      throw new Refusal("refused", "the account answers its owner's own callers only");
    }
    const signedIn = binding.authenticated_at ?? null;
    if (signedIn !== null && (typeof signedIn !== "number" || !Number.isSafeInteger(signedIn)
                              || signedIn < 0)) {
      throw new Refusal("invalid", "authenticated_at is a time in whole seconds");
    }
    if (origin === null) {
      return { owner, caller, origin, grant: null, run: null };
    }
    // The owner's cookie rides only the owner's own browser requests (tasks 2); an
    // outside client's call carrying it is a binder bug or a replay, refused before any
    // grant is recorded. Only an API key's registration carries both: the binder checked
    // the owner holds the key, and the object re-derives its origin from the key id.
    if (binding.session !== undefined && serves.registering !== true) {
      throw new Refusal("refused", OUTSIDE_SESSION);
    }
    const source = registrant(binding.source, origin);
    if (binding.session !== undefined && source.kind !== "api_key") {
      throw new Refusal("refused", MCP_REGISTERS_BY_CARD);
    }
    const vendor = binding.vendor ?? null;
    if (vendor !== null && (source.kind !== "mcp" || typeof vendor !== "object"
        || typeof vendor.id !== "string" || typeof vendor.verified !== "boolean")) {
      throw new Refusal("invalid", "invalid outside client vendor");
    }
    const grant = this.#grants.admit(origin.client, source.kind, signedIn, clock(), vendor);
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
      throw new Refusal("run_ended", RUN_ENDED);
    }
    let held: Grant | null = null;
    if (grant.stamp !== null) {
      try {
        held = this.#grants.standing(grant.stamp.client, grant.stamp.generation,
                                     grant.stamp.authenticated_at);
      } catch (error) {
        if (error instanceof Refusal && !error.invalid) {
          this.#cancelInSandbox(this.#runs.endClient(grant.stamp.client, clock()));
          throw new Refusal("authority_changed", AUTHORITY_CHANGED);
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
      throw new Refusal("refused", `only the owner may ${action}`);
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
        throw new Refusal("refused", `only the owner may ${owner}`);
      }
      return act(account, this.#sessions.live(binding.session, clock()));
    } catch (error) {
      if (error instanceof Refusal) {
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
        throw new Refusal("refused", "outside client capability not granted");
      }
      return;
    }
    if (account.grant?.mode !== "scoped") {
      return;
    }
    const universe = center === undefined ? this.#universe(account) : center;
    if (typeof universe !== "string"
        || !holds(account.grant, { universe, agent, capability })) {
      throw new Refusal("refused", "outside client capability not granted");
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
      throw new Refusal("refused", "this outside identity is not one enabled agent");
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
    // Quiet: a renewal changes nothing an export reads, so an export open across it stands.
    return this.#quiet(() => {
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

  // -- SANDBOX TIME: the sandbox Worker's log, metered (`sandbox-time.ts`) -----------

  /**
   * The `sandbox` lane's pass: the sandbox Worker's log read after the cursor, a batch at a
   * time until the pass's end, each applied in a unit. Without a sandox bound there is no
   * log, and runs that never started are metered at none. A read that fails backs the lane
   * off in its row; the pass completes.
   */
  async #meterPass(until: number): Promise<void> {
    const sandbox = this.env.SANDBOX;
    const owner = this.ctx.id.name;
    do {
      const { after } = this.#sandboxTime.cursor();
      let read: { events: ReturnType<typeof meterRead>["events"]; next: number };
      const readAt = Date.now();
      try {
        read = sandbox === undefined || owner === undefined ? { events: [], next: after }
          : meterRead(await sandbox.meter(owner, { after }), after);
      } catch {
        console.error("the sandbox meter's read failed; it is tried again later");
        await this.#gate.write(() => this.#sandboxTime.failed(clock()));
        return;
      }
      await this.#gate.write(() => this.#storage.transactionSync(
        () => this.#sandboxTime.apply(read.events, read.next, readAt)));
      if (read.events.length < METER_BATCH) {
        return;
      }
    } while (Date.now() < until);
  }

  /**
   * One execution E2B billed, from its signed lifecycle event, which the doors verified and
   * routed here by its sealed route (`doors/src/e2b-hooks.ts`). It carries no binding, as the
   * line's ring does not (`sandboxRung`): it grants nothing, and only adds to the meter, once
   * an execution, inside the window its event's time allows. An erased or erasing owner
   * counts nothing.
   */
  async sandboxBilled(event: BilledEvent): Promise<{ counted: boolean }> {
    if (this.#tombstone || this.#erasure.erased || erasing(this.#storage)) {
      return { counted: false };
    }
    const checked = billedEvent(event);
    return this.#door(() => ({ counted: this.#sandboxTime.billed(checked, Date.now()) }));
  }

  /** The owner's sandbox seconds a day, newest first: the founder's cost view. */
  sandboxDays(binding: Binding, request: { days?: number }): Promise<{
      days: { day: string; seconds: number; billed_seconds: number; vcpu_seconds: number;
              gb_seconds: number; unverified_executions: number }[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its sandbox time");
      const days = request?.days ?? 31;
      if (typeof days !== "number" || !Number.isSafeInteger(days) || days < 1 || days > 400) {
        throw new Refusal("invalid", "days is 1 to 400");
      }
      return { days: this.#sandboxTime.days(days) };
    });
  }

  // -- STORAGE: the tier's one storage number, used and left -------------------

  /**
   * What the owner stores against its one storage number (founder, 2026-11-03): every byte
   * of user content wherever it lives (`used_bytes`: chunks in R2, small files and inline
   * bodies in the object), the bound (`limit_bytes`: the tier's storage, or the owner's own
   * lower setting; null until the binder has handed the tier numbers), and where it is:
   * mail (`messages`), collection rows and their history (`collections`), and files, the
   * rest. Platform bookkeeping (indexes, receipts, counters) is not user content.
   */
  storageUsage(binding: Binding): Promise<{
      used_bytes: number; limit_bytes: number | null;
      breakdown: { files: number; messages: number; collections: number };
      deleted_content: { kept_bytes: number; kept_up_to_days: number } }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its storage");
      const usage = this.#files.usage();
      const used = usage.stored;
      // Each kind's bodies, inline or placed (a placed content counted once); files are the
      // rest, so the parts always sum to what is used.
      const messages = Math.min(used, this.#ledger.storedBytes());
      const collections = Math.min(used - messages, this.#collections.storedBytes());
      // Deleted files kept for recovery: up to 30 days, within the same figure (the oldest go
      // first past it), so a lower setting shortens recovery; counted in nothing used.
      return { used_bytes: used, limit_bytes: usage.limits.max_bytes,
               breakdown: { files: used - messages - collections, messages, collections },
               deleted_content: { kept_bytes: this.#files.garbageBytes(),
                                  kept_up_to_days: GC_GRACE_S / 86_400 } };
    });
  }

  // -- OWNER_EXPORT: everything the owner holds (`export.ts`) --------------------

  /**
   * One object of the owner's export from `cursor` (null: the start, which opens the export
   * on a generation, `backup.ts`: the one running, the newest finished while nothing changed
   * since, or a new one, begun at once): its lines, and the next object's cursor, or null
   * after its end line. Every object is immutable once R2 holds it, so any cursor resumes;
   * one not yet written answers `pending`, its cursor the same, to ask again. The owner's
   * alone, in its live session: no outside client, no run, and no request without the
   * session cookie reaches it. A `read` door, so a billing suspension still lets the owner
   * take its data; its writes (the export's own rows) are quiet: they mark nothing changed.
   */
  async ownerExport(binding: Binding,
                    request: { cursor?: string | null; export_id?: string }):
      Promise<{ export_id: string; lines: string[]; next: string | null; pending: boolean }> {
    const asked = await this.#quiet(() => {
      this.#owner(binding, "read", "export everything it holds");
      this.#sessions.live(binding.session, clock());
      if (typeof request !== "object" || request === null || Array.isArray(request)
          || Object.keys(request).some((key) => key !== "cursor" && key !== "export_id")) {
        throw new Refusal("invalid", "an export request is at most its cursor and its export_id");
      }
      const now = clock();
      const first = request.cursor === null || request.cursor === undefined;
      const open = first ? this.#exports.open(now, this.#backups.forExport(now))
        : this.#exports.named(request.export_id, now);
      const at = first ? { gen: open.gen, index: 0 } : decodeExportCursor(request.cursor);
      if (at.gen !== open.gen) {
        throw new Refusal("invalid", "an export cursor is one a page handed out");
      }
      return { open, index: at.index, first };
    });
    const { open, index } = asked;
    if (asked.first) {
      await this.#schedule.sync();  // a generation begun or joined goes on at once
    }
    const { ns, key } = await this.#files.space();
    const root = this.#backups.finished(open.gen)
      ? await this.env.FILES.getManifestRoot(ns, open.gen) : null;
    const object = this.#backups.object(open.gen, index, root);
    const answer = (lines: string[], next: string | null, pending = false) =>
      ({ export_id: open.export_id, lines, next, pending });
    if (object === null) {
      throw new Refusal("invalid", "this export is not open: begin it again");
    }
    if (object.kind === "pending") {
      return answer([], encodeExportCursor(open.gen, index), true);
    }
    if (object.kind === "end") {
      return answer([object.line], null);
    }
    const bytes = object.kind === "part"
      ? await this.env.FILES.getManifestPart(ns, root?.key ?? key, open.gen, object.part,
                                             object.sha256)
      : await this.env.FILES.getManifestSegment(ns, root?.key ?? key, open.gen, object.a,
                                                object.b, object.sha256);
    return answer(new TextDecoder().decode(bytes).trimEnd().split("\n"),
                  encodeExportCursor(open.gen, index + 1));
  }

  /**
   * Chunks an open export's pages named, by hash: their bytes, at most two chunks a call
   * (`BATCH_BYTES`), each checked against its hash by the files Worker. Any chunk the owner
   * still stores is served (live, or garbage in its grace), however the owner changed since;
   * one gone from the bucket is refused (`CHUNK_GONE`). The owner's alone.
   */
  async exportChunks(binding: Binding, request: { export_id: string; sha256s: string[] }):
      Promise<{ chunks: { sha256: string; data: string }[] }> {
    const wanted = await this.#door(() => {
      this.#owner(binding, "read", "export everything it holds");
      this.#sessions.live(binding.session, clock());
      if (typeof request !== "object" || request === null
          || Object.keys(request).sort().join() !== "export_id,sha256s"
          || !Array.isArray(request.sha256s) || request.sha256s.length < 1
          || request.sha256s.length > 2) {
        throw new Refusal("invalid", "an export's chunks are its export_id and one or two sha256s");
      }
      this.#exports.named(request.export_id, clock());
      return this.#files.storedChunks(request.sha256s);
    });
    const bytes = await this.#files.fetch(wanted.map((sha256) => ({ sha256 })));
    return { chunks: wanted.map((sha256, at) => ({
      sha256, data: Buffer.from(bytes[at]!).toString("base64") })) };
  }

  /** An export ended: its id names it no longer. */
  exportEnd(binding: Binding, request: { export_id: string }): Promise<{ ended: boolean }> {
    return this.#quiet(() => {
      this.#owner(binding, "reduce", "end its export");
      return { ended: this.#exports.end(request?.export_id) };
    });
  }

  /**
   * A key rotation's sweep for this owner (`files/src/datakey.ts`, tasks.md): its wrapped
   * data key, and every nightly manifest root it keeps, wrapped again under the files
   * keyring's active kid; an idle owner's too, since nothing else rewrites its root. The
   * platform's alone (`PLATFORM_CALLER`), as `admissionSet` is.
   */
  async dataKeyRewrap(binding: Binding): Promise<{ rewrapped: boolean; roots: number }> {
    const days = await this.#door(() => {
      const { caller } = this.#bind(binding, "admission");
      if (caller !== PLATFORM_CALLER || !("origin" in binding) || binding.origin !== null
          || binding.session !== undefined || binding.source !== undefined) {
        throw new Refusal("refused", PLATFORM_ONLY);
      }
      return this.#backups.manifests().map((manifest) => manifest.gen);
    });
    return this.#files.rewrap(days);
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
        throw new Refusal("refused", "no pending proposal for this card");
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
      throw new Refusal("refused", PLATFORM_ONLY);
    }
    if (!erasing(this.#storage)) {
      throw new Refusal("refused", NO_ERASURE);
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
      throw new Refusal("invalid", "invalid inbound binding");
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
      throw new Refusal("invalid",
                        "an inbound event is its id, its time, its bytes and its headers");
    }
    if (row.kind === "shared" && row.tripped === 1) {
      return { done: { state: "no_op", error: "breaker_tripped" } };
    }
    const proof = ownerProof(row);
    if (proof !== null && !proves(proof.profile, headers as Record<string, string>, body,
                                  proof.secret, Math.floor(now))) {
      throw new Refusal("inbound_proof_invalid", PROOF_INVALID);
    }
    // An HMAC with no timestamp signs the body alone, so a captured body could come again
    // under a new event id: such an event is keyed by its bytes, a replay always deduped.
    const unstamped = proof?.profile.method === "hmac-sha256"
      && proof.profile.timestamp_header === undefined;
    const identity = unstamped
      ? `body:${sha256Hex(body)}` : `id:${eventId}`;
    const key = sha256Hex(`tinyassets-inbound\0${row.binding_id}\0${row.revision}\0${identity}`);
    const envelope = JSON.stringify({ inbound: {
      binding: row.binding_id, event_id: eventId, event_at: eventAt,
      raw_base64: Buffer.from(body).toString("base64") } });
    return { row, room: `dm:${nativeId(row.agent)}`, cmid: `inb_${key}`, envelope };
  }

  /**
   * An inbound event the agent's mail refused: too much unread mail (`ledger_full`) counts
   * toward a shared app's breaker (and its trip is the owner's notice); the hot ceiling while
   * older mail moves out (`storage_busy`) is retried and counts nothing; the rest are the
   * event's own refusals.
   */
  #inboundRefused(row: InboundRow, error: unknown, now: number): InboundAnswer {
    const refused = error instanceof Refusal ? error.code : null;
    if (refused === "storage_busy") {
      return { state: "refused", error: "mailbox_full", retry: true };
    }
    if (error instanceof Refusal && error.transient) {
      throw error;  // for the moment only (a full pool): never the event's own refusal
    }
    if (refused === "ledger_full") {
      if (row.kind === "shared" && this.#inbound.failed(row, Math.floor(now))) {
        this.#notices.add("inbound", `An inbound app's events to ${row.agent} were paused: ` +
                          "its mailbox was full. Change the binding to resume.", now);
      }
      return { state: "refused", error: "mailbox_full", retry: true };
    }
    if (refused === "post_conflict") {
      return { state: "refused", error: "event_conflict" };
    }
    if (refused === "message_body") {
      return { state: "refused", error: "too_large" };
    }
    if (error instanceof Refusal && !error.invalid) {
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
      Promise<{ namespace: string; key: string; chunks: { sha256: string; size: number }[];
                pending: string[] }> {
    const plan = await this.#door(() => {
      this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["intent_id"]);
      return this.#files.uploadPlan(r["intent_id"]);
    });
    // The namespace and its wrapped data key, which only `FILES` opens (slice P, M4).
    const { ns, key } = await this.#files.space();
    return { namespace: ns, key, ...plan };
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
   * it may mail). One answer holds a page (`pages.ts`, review M3): its agents, the owner's
   * agents and the apps, in that order, each by its stable key, while they fit; when some
   * are left, `next` is where the next page starts, for `after` on the next read.
   */
  async outsideAgents(binding: Binding, request: AgentsRequest = {}):
      Promise<{ agents: Agent[]; owner_agents: NativeAgent[]; apps: ConnectedApp[];
                next?: string }> {
    await this.#seededFor(() => {
      this.#account(binding, "read");
    });
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
      // The owner's connected AI apps, to every caller, a narrowed grant's and a run's too:
      // names and dates, no data (autoconnect_research.md §3.2), so any agent can offer an
      // app that is not connected yet.
      const r = fields(request ?? {}, [], ["after"]);
      const after = r["after"] === undefined ? null : r["after"];
      if (after !== null && typeof after !== "string") {
        throw new Refusal("invalid", "after is a next an agents list answered");
      }
      return agentsPage({ agents: this.#ledger.agents(account.origin), owner_agents: natives,
                          apps: this.#grants.apps() }, after);
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
        throw new Refusal("refused", "only an outside client files its own agent's registration");
      }
      if (registrant(binding.source, account.origin).kind !== "mcp") {
        throw new Refusal("refused",
                          "an API key's agent registers from the owner's browser with the key");
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
        throw new Refusal("refused", "no pending registration for this card");
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
  async roomPost(binding: Binding, request: PostRequest): Promise<Posted> {
    // A parent archived (acked, moved to a segment) is read from R2 before the units: its
    // seq, from its id, in the gate; then its record, outside. One the lane moved after the
    // gate found it hot is read so in a post again (review N10's Low), never refused as no
    // parent: archived mail never returns hot, so a second post finds it archived.
    const gated = await this.#door(() => {
      this.#account(binding, "work");
      return this.#archivedParent(request);
    });
    for (let tries = 0; ; tries++) {
      const parent = tries === 0 ? gated : await this.#door(() => {
        this.#account(binding, "work");
        return this.#archivedParent(request);
      });
      const archived = parent === null ? null : await this.#archived(parent.seq, parent.id);
      // In each unit: the parent the gate found hot is hot still.
      const kept = () => {
        if (archived === null && this.#archivedParent(request) !== null) {
          throw new ParentMoved();
        }
      };
      try {
        return await this.#busyRings(() => this.#placing((): { body: unknown; writer: string }
                                                            | { done: Posted } => {
          const account = this.#account(binding, "work");
          this.#scope(account, "write_graph");
          const { r, actor } = this.#poster(account, binding, request);
          kept();
          // The room, the sender, the parent, the caps and a resend, before any byte is placed.
          const resend = this.#ledger.prepare(actor, r["room_id"], r["client_message_id"],
                                              r["body"], r["parent_id"], archived);
          return resend !== null ? { done: resend }
            : { body: r["body"], writer: filerOf(account) };
        }, (placed) => {
          const account = this.#account(binding, "work");
          this.#scope(account, "write_graph");
          const { r, actor, authority } = this.#poster(account, binding, request);
          kept();
          return this.#ledger.post(actor, r["room_id"], r["client_message_id"], r["body"],
                                   r["parent_id"], clock(), authority, placed,
                                   filerOf(account), archived);
        }, { synced: true }), utf8Bytes(String(request?.body ?? "")));
      } catch (error) {
        if (!(error instanceof ParentMoved) || tries + 1 >= READ_TRIES) {
          throw error instanceof ParentMoved ? archiveMoving() : error;
        }
      }
    }
  }

  /**
   * A post held at the hot ceiling (`storage_busy`) rings the schedule, so the lane moves;
   * when the archive cannot move for the storage figure, it is the one wall instead
   * (`storage_full`, what it needs its body's bytes), never a busy that cannot clear.
   */
  async #busyRings<T>(act: () => Promise<T>, needed: number): Promise<T> {
    try {
      return await act();
    } catch (error) {
      if (error instanceof Refusal && error.code === "storage_busy") {
        const full = await this.#door(() => this.#ledger.archiveBlocked()
          ? this.#files.fullRefusal(needed) : null);
        if (full !== null) {
          throw full;
        }
        await this.#schedule.sync();
      }
      throw error;
    }
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
      throw new Refusal("refused", "outside client capability not granted");
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
      if (!(error instanceof Refusal && !error.invalid)) {
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

  /**
   * The starter package's receipts (`starter.ts`): each path it wrote, the version, and
   * whether it stands as installed, edited, deleted or proposed; with the version the owner's
   * files were last reconciled to and the version this code carries.
   */
  starterReceipts(binding: Binding):
      Promise<{ version: number; carried: number; receipts: StarterReceipt[] }> {
    return this.#door(() => {
      this.#owner(binding, "read", "read its starter files' receipts");
      return { version: this.#starter.version(), carried: this.#starter.carried(),
               receipts: this.#starter.receipts() };
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
        throw new Refusal("refused", PLATFORM_ONLY);
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
    return this.#rereadingPage(() => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes", "body_offset",
                                                  "message_id"]);
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      const offset = bodyOffset(r["body_offset"]);
      const next = this.#ledger.placedNext(account.origin, r["limit"]);
      return { contents: next.contents,
               offsetOf: (contentId) => contentId === next.first ? offset : 0 };
    }, (windows) => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit"], ["max_bytes", "pad_bytes", "body_offset",
                                                  "message_id"]);
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      return this.#ledger.read(account.origin, r["limit"], r["max_bytes"] ?? null,
                               r["pad_bytes"] ?? 0, clock(), windows,
                               bodyOffset(r["body_offset"]), PAGE_BYTES, r["message_id"] ?? null);
    }, (page) => page.messages.length === 0 && page.more);
  }

  mailboxAck(binding: Binding, request: AckRequest): Promise<{ reader: string; through: number }> {
    return this.#synced(() => {
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
    // mailbox read's; the read's unit marks only the mail it returns. One answer holds
    // both: each part fits half of it (`INBOX_PART`, `pages.ts`), so neither starves.
    const { mail, rooms, windows, chars } = await this.#rereadingPage(() => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit", "after"], ["max_bytes", "head_chars"]);
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      const chars = headChars(r["head_chars"]);
      const mail = new Set(this.#ledger.placedNext(account.origin, r["limit"], INBOX_PART)
        .contents);
      return { contents: [...mail, ...contentsOf(this.#collections.changed(
        account.caller, { prefix: ROOM_PREFIX }, r["after"], r["limit"]).rows)],
               maxOf: (contentId) => mail.has(contentId) ? INBOX_PART : headWindow(chars) };
    }, (windows) => {
      const account = this.#account(binding, "read");
      const r = ledgerFields(request, ["limit", "after"], ["max_bytes", "head_chars"]);
      this.#noMailbox(account);
      this.#scope(account, "read_graph");
      const changes = this.#collections.changed(account.caller, { prefix: ROOM_PREFIX },
                                                r["after"], r["limit"]);
      return {
        mail: this.#ledger.read(account.origin, r["limit"], r["max_bytes"] ?? null, 0, clock(),
                                windows, 0, INBOX_PART),
        rooms: changes, windows, chars: headChars(r["head_chars"]),
      };
    }, ({ mail: page }) => page.messages.length === 0 && page.more);
    return { mail, rooms: heads(rooms, chars, windows, INBOX_PART) };
  }

  /** The board's rows written past `after`, each as its head, held as `collectionPull` is. */
  boardDigest(binding: Binding, request: BoardDigestRequest): Promise<Heads> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["limit", "after"], ["head_chars"]);
      const chars = headChars(r["head_chars"]);
      const changes = this.#collections.changed(caller, { collection: BOARD }, r["after"],
                                                r["limit"]);
      return { value: { changes, chars }, contents: contentsOf(changes.rows),
               maxOf: () => headWindow(chars) };
    }, ({ changes, chars }, windows) => heads(changes, chars, windows));
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
        throw new Refusal("invalid", "a script is text");
      }
      if (!hookSettings(this.#policy.read().document).codemode) {
        throw new Refusal("code_mode_off", CODE_MODE_OFF);
      }
      const { tools: names } = this.#profile(account);
      if (!names.includes(CODE)) {
        throw new Refusal("tool_not_served", CODE_NOT_SERVED);
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
      throw new Refusal("refused", closed);
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
      return fittedCode(answer);
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
        throw new Refusal("refused", closed);
      }
      await this.#door(() => {
        const { tools } = this.#profile(this.#account(binding, "read"));
        if (!hookSettings(this.#policy.read().document).codemode) {
          throw new Refusal("code_mode_off", CODE_MODE_OFF);
        }
        if (!tools.includes(CODE) || !tools.includes(tool.name)) {
          throw new Refusal("tool_not_served", CODE_NOT_SERVED);
        }
      });
      const door = this[tool.door] as unknown as (...args: unknown[]) => Promise<unknown>;
      return await Reflect.apply(door, this, takesRequest(tool) ? [binding, args] : [binding]);
    } catch (error) {
      if (!(error instanceof Refusal)) {
        console.error(`code mode's ${tool.name} call failed`, error);
        throw new Error("internal error");
      }
      throw new Error(error.message);
    }
  }

  /**
   * A run reads and acks no mailbox: its mail is its input, and its agent's mailbox holds
   * every authority's mail, which no one run's authority covers. A run sees its own thread,
   * under its own authority, as its conversation.
   */
  #noMailbox(account: Account): void {
    if (account.run !== null) {
      throw new Refusal("run_no_mailbox", RUN_NO_MAILBOX);
    }
  }

  /**
   * The owner's view of a mail chain (agents' mail to each other included), from any of its
   * messages, such as the mail a run's receipt names: oldest first, after `after`.
   */
  async mailChain(binding: Binding, request: ChainRequest):
      Promise<{ root: string; messages: ChainMail[]; more: boolean }> {
    const r = await this.#door(() => {
      this.#owner(binding, "read", CHAINS);
      return ledgerFields(request, ["message_id", "limit"], ["after", "body_offset",
                                                             "body_message_id"]);
    });
    const after = r["after"] ?? 0;
    if (typeof after !== "number" || !Number.isSafeInteger(after) || after < 0) {
      throw new Refusal("invalid", "a chain read's after is a seq");
    }
    const offset = bodyOffset(r["body_offset"]);
    // Its root: the hot message's, or the archived one's, read from its segment.
    const start = await this.#door(() => this.#ledger.chainStart(r["message_id"]));
    let root: string;
    let rootSeq: number;
    let room: string;
    if ("archived" in start) {
      const found = await this.#archived(start.archived, r["message_id"] as string);
      if (found === null) {
        throw new Refusal("refused", "no such message held");
      }
      root = found.message.chain_root;
      room = found.message.room_id;
      rootSeq = await this.#door(() => this.#ledger.seqOf(root)) ?? 0;
    } else {
      ({ root, root_seq: rootSeq, room } = start);
    }
    const most = typeof r["limit"] === "number" ? r["limit"] : 1;
    for (let tries = 0; tries < READ_TRIES; tries++) {
      const read = await this.#chainPage(binding, r, { root, rootSeq, room }, after, offset,
                                         most);
      if (read !== null) {
        return { root, messages: read.messages, more: read.more };
      }
    }
    throw archiveMoving();
  }

  /**
   * One read of a chain's page (`mailChain`), or null when the archive changed under it.
   * Its archived messages past `after`, as a page reads placed bodies (`pages.ts`, PR 1):
   * the next segments' chain maps, then each record by range, its body only as the window
   * a page may show (from `body_offset` when it is the page's first message), the windows
   * stopping at `WINDOWS_BYTES` past the first; the first left unread is the frontier, so
   * the page stops below it and the next goes on from there. Each fence's content and chunks
   * are located in the fences' own unit, with the archive's mark (review N10); a segment
   * replaced after it (a merge, a compaction, a purge's rewrite) is read from its chunks in
   * their grace, and a read that fails all the same (a chunk collected early, past the figure)
   * is a changed archive, read again, when the mark moved (`#segmentsRead`). Its hot rows are
   * read in a later unit, which first checks that no segment made since may hold the chain's
   * room past `after`: a move between them took mail out of the hot rows into a segment the
   * read never saw (review N9), so the read is null, and `mailChain` reads again from the
   * fences. Another room's moves never count. The page stays the one bounded page
   * `ledger.chain` fits; only the reads are repeated, at most `READ_TRIES`.
   */
  async #chainPage(binding: Binding, r: Record<string, unknown>,
                   chain: { root: string; rootSeq: number; room: string }, after: number,
                   offset: number, most: number):
      Promise<{ messages: ChainMail[]; more: boolean } | null> {
    const { root, rootSeq, room } = chain;
    const { fenced, firstHot, seen, located } = await this.#door(() => {
      const fences = this.#ledger.chainFences(rootSeq, after);
      return { fenced: fences, firstHot: this.#ledger.chainNext(root, after),
               seen: this.#ledger.archiveMark(),
               located: this.#bodies.locate(fences.fences.map((fence) => fence.content_id)) };
    });
    await this.#chainStep?.("fenced");
    let frontier = fenced.frontier;
    const mail: ArchivedMail[] = [];
    const read = await this.#segmentsRead(seen.mark, async () => {
      let held = 0;
      read: for (const fence of fenced.fences) {
        const chunks = located.get(fence.content_id)!.chunks;
        const trailer = await this.#trailer(fence, chunks);
        for (const [key, at, length] of trailer.chains[root] ?? []) {
          if (key <= after) {
            continue;
          }
          if (mail.length >= most || (mail.length > 0 && held >= WINDOWS_BYTES)) {
            frontier = key;
            break read;
          }
          const first = mail.length === 0 && (firstHot === null || key < firstHot);
          const found = await this.#archivedRecord(chunks, at, length, first ? offset : 0);
          held += found.body === null ? 0 : found.body.end - found.body.offset;
          mail.push(found);
        }
      }
      return true;
    });
    if (read === null) {
      return null;
    }
    await this.#chainStep?.("hot");
    // Its hot messages' placed bodies (ADR-035) are read from R2 between its units, and the
    // page, hot and archived together, fits one answer.
    const archived = { root, mail, frontier };
    const page = (windows: ReadonlyMap<string, Window>) => this.#ledger.chain(
      r["message_id"], after, r["limit"], windows, offset, r["body_message_id"] ?? null,
      archived);
    return this.#rereadingPage(() => {
      this.#owner(binding, "read", CHAINS);
      const read = page(new Map());
      return { contents: read.placed,
               offsetOf: (contentId) => contentId === read.first ? offset : 0 };
    }, (windows) => {
      this.#owner(binding, "read", CHAINS);
      return this.#ledger.movedSince(seen.newest, room, after) ? null : page(windows);
    }, (read) => read !== null && read.messages.length === 0 && read.placed.length > 0);
  }

  // -- THE ARCHIVE (slice STORE item 2, `segments.ts`) --------------------------------------

  /**
   * Segments' reads from R2, outside any unit, after the unit that took their fences, their
   * located chunks and the archive's `mark`: the reads' value, or null when they failed
   * because the archive changed since (the mark moved), for the caller to read again. A
   * segment replaced since (a merge, a compaction, a purge's rewrite) is let go as garbage
   * in its grace (`GC_GRACE_S`, 30 days), so its chunks still read for far longer than one
   * read, and their hashes still check; but past the owner's figure the oldest garbage is
   * collected early (`files.ts`), and a just-replaced segment's chunks, the newest, could
   * still go, so a failed read is retried when the mark moved. With the mark unchanged, the
   * failure is no change of the archive's, and stands, loudly.
   */
  async #segmentsRead<T>(mark: string, read: () => Promise<T>): Promise<T | null> {
    try {
      return await read();
    } catch (error) {
      if (await this.#door(() => this.#ledger.archiveMark().mark !== mark)) {
        return null;
      }
      throw error;
    }
  }

  /**
   * A byte range of a segment's content, read from R2 outside any unit by the verified
   * ranges it spans (`FILES` reads each whole chunk, rechecks its hash and answers the range).
   */
  async #segmentBytes(chunks: readonly string[], offset: number, length: number):
      Promise<Uint8Array> {
    return joined(await this.#files.fetch(chunkReads(chunks, offset, length)));
  }

  /** A segment's trailer, read alone by its fence's range. */
  async #trailer(fence: Fence, chunks: readonly string[]):
      Promise<ReturnType<typeof parseTrailer>> {
    return parseTrailer(utf8Decoder("keep").decode(
      await this.#segmentBytes(chunks, fence.trailer_offset, fence.trailer_bytes)));
  }

  /**
   * An archived record read by range, never its body whole (PR 1): a small one in one read;
   * a larger one its head (its message's row and its body's), its recipients' lines, and
   * its body's window from `offset` (at most `PAGE_BYTES`, cut back to a character), or no
   * body at all (`offset` null: a reply's parent needs only its row).
   */
  async #archivedRecord(chunks: readonly string[], at: number, length: number,
                        offset: number | null): Promise<ArchivedMail> {
    if (length <= ARCHIVED_WHOLE_BYTES) {
      return archivedMail(await this.#segmentBytes(chunks, at, length), offset);
    }
    let head = null;
    for (let read = ARCHIVED_HEAD_BYTES; head === null; read *= 4) {
      head = archivedHead(await this.#segmentBytes(chunks, at, Math.min(length, read)));
      if (head === null && read >= length) {
        throw new Error("an archived mail record lacks its message or its body");
      }
    }
    const bodyAt = at + head.bodyAt;
    const tail = bodyAt + head.bodyBytes + 1;
    const recipients = archivedRecipients(await this.#segmentBytes(chunks, tail,
                                                                   at + length - tail));
    return { message: head.message, recipients, body: offset === null ? null
      : await this.#bodies.windowAt(chunks, bodyAt, head.bodyBytes, offset, PAGE_BYTES) };
  }

  /**
   * An archived message by the seq its id carries, from its segment; null when none. Its
   * fences' contents are located with them and the archive's mark, and a read that failed
   * because the archive changed is read again (`#segmentsRead`, review N10), at most
   * `READ_TRIES`, then refused `storage_busy`.
   */
  async #archived(seq: number, messageId: string): Promise<ArchivedMail | null> {
    for (let tries = 0; tries < READ_TRIES; tries++) {
      const { fences, seen, located } = await this.#door(() => {
        const fences = this.#ledger.archiveFences(seq);
        return { fences, seen: this.#ledger.archiveMark(),
                 located: this.#bodies.locate(fences.map((fence) => fence.content_id)) };
      });
      const found = await this.#segmentsRead(seen.mark, async () => {
        for (const fence of fences) {
          const chunks = located.get(fence.content_id)!.chunks;
          const placement = (await this.#trailer(fence, chunks)).keys
            .find(([key]) => key === seq);
          if (placement !== undefined) {
            return { mail: await this.#archivedRecord(chunks, placement[1], placement[2], null) };
          }
        }
        return { mail: null };
      });
      if (found === null) {
        continue;
      }
      if (found.mail === null) {
        return null;
      }
      const mail = found.mail;
      const purged = await this.#door(() => this.#ledger.purgedAgents());
      return mail.message.message_id === messageId
        && !roomMembers(mail.message.room_id).some((member) => purged.includes(member))
        ? mail : null;
    }
    throw archiveMoving();
  }

  /** In a unit: a post's parent's seq, when it is archived (not hot, and the object's). */
  #archivedParent(request: PostRequest): { id: string; seq: number } | null {
    const parentId = (request as { parent_id?: unknown } | null)?.parent_id;
    if (typeof parentId !== "string") {
      return null;
    }
    try {
      const start = this.#ledger.chainStart(parentId);
      return "archived" in start ? { id: parentId, seq: start.archived } : null;
    } catch {
      return null;  // not one the object gave: the post refuses it as no parent
    }
  }

  /**
   * One pass of the `archive` lane: a deleted agent's mail rewritten out of a segment, or
   * else one move of acked mail (a straggler's merge, or the tail's new segment). A move
   * that would leave the storage figure higher than it found it is refused for the figure
   * (`archive_full`): the archive is then blocked, and a post at the hot ceiling is the one
   * wall, `storage_full`, until the owner frees space (review M1). A failure outside (the
   * storage service down) holds the lane a while. Each pass is counted in storage as it
   * begins and cleared as it ends (review N3), so a pass killed with its isolate (for memory,
   * say) still counts: at `ARCHIVE_FAILS_TOLD` in a row the owner is told once, with the last
   * error as it said it, or that the passes stopped before they could say.
   */
  async #archivePass(): Promise<unknown> {
    let begun: { fails: number; error: string | null } | null = null;
    try {
      // Blocked and still full: nothing to try until the owner frees space.
      begun = await this.#door(() => {
        const { stored, figure } = this.#files.storedNow();
        return this.#ledger.archiveBlocked() && figure !== null && stored >= figure ? null
          : this.#ledger.archiveBegin();
      });
      if (begun === null) {
        return { hold: clock() + ARCHIVE_RETRY_S };
      }
      if (begun.fails === ARCHIVE_FAILS_TOLD && begun.error === null) {
        // The third failed pass never reached its catch: it went with its isolate.
        await this.#door(() => this.#notices.add("storage_busy_long", archiveFailing(null),
                                                 clock()));
      }
      // A purge's rewrite first, so a deleted agent's mail leaves R2 promptly; the order is
      // not needed for correctness (a merge into a segment that still holds it keeps it,
      // and its bloom names the agent, so the purge rewrites that segment in turn). A
      // rewrite that fails never holds the move behind it (review N7): the move runs, and
      // the rewrite's failure is the pass's, counted and told at three.
      // Each holds its own units of the isolate's pool (`MoveHold`), released as it ends.
      let rewrote = false;
      let failed: unknown = null;
      try {
        rewrote = await this.#archiveRewrite();
      } catch (error) {
        if (error instanceof Refusal && error.code === "archive_changed") {
          rewrote = true;
        } else {
          failed = error;
        }
      }
      // The move runs whenever mail is due (the hot set past half), a rewrite or not, so a
      // long purge never holds new mail at `storage_busy` (review N8); the rewrite's table
      // and units are let go before it, so the pass holds one move's at a time.
      if (!rewrote || await this.#door(() => this.#ledger.archiveDue())) {
        await this.#archiveMove();
      }
      if (failed !== null) {
        throw failed;
      }
      await this.#door(() => this.#ledger.archiveEnded());
      return undefined;
    } catch (error) {
      if (error instanceof Refusal && error.code === "archive_changed") {
        await this.#door(() => this.#ledger.archiveEnded());
        return undefined;  // the batch moved under it: the next pass picks again
      }
      if (error instanceof Refusal && (error.code === "archive_full"
                                       || error.code === "storage_full")) {
        const first = await this.#door(() => {
          const was = this.#ledger.archiveBlocked();
          this.#ledger.blockArchive();
          this.#ledger.archiveEnded();  // its own cause, told in its own words
          return !was;
        });
        if (first) {
          await this.#door(() => this.#notices.add("storage_busy_long",
            "Older mail could not be moved out because your storage is full, so new mail is " +
              "refused until you free some space or upgrade.", clock()));
        }
        return { hold: clock() + ARCHIVE_RETRY_S };
      }
      if (isSlotBusy(error)) {
        // The isolate's pool full (`busy`, `MoveHold`): contention, not storage (round 11,
        // Low A). Not a failed pass, never logged: the lane tries again once the pool's own
        // wait has turned its queue over. The owner is told only when it puts storage at risk,
        // the hot set at its ceiling (the next post refused `storage_busy`) with the moves
        // blocked: once a blocked spell, since the last segment the lane placed.
        await this.#door(() => {
          if (begun !== null) {
            this.#ledger.archiveBusy(begun);
          }
          if (this.#ledger.atHotCeiling()) {
            const since = this.#ledger.lastPlaced();
            if (!this.#notices.list().some((notice) => notice.type === "storage_busy_long"
                && notice.detail === ARCHIVE_BUSY_TOLD && notice.at >= since)) {
              this.#notices.add("storage_busy_long", ARCHIVE_BUSY_TOLD, clock());
            }
          }
        });
        return { hold: clock() + ARCHIVE_BUSY_RETRY_S };
      }
      console.error("the archive lane's pass failed", error);
      if (begun !== null) {
        // What failed, as it said it, never a cause it did not give (review N1).
        const said = (error instanceof Error ? error.message.split("\n")[0]! : String(error))
          .slice(0, 200);
        const fails = begun.fails + 1;
        await this.#door(() => {
          this.#ledger.archiveFailed(said);
          if (fails === ARCHIVE_FAILS_TOLD) {
            this.#notices.add("storage_busy_long", archiveFailing(said), clock());
          }
        });
      }
      return { hold: clock() + ARCHIVE_RETRY_S };
    }
  }

  /**
   * One move: a straggler's merge (acked mail whose keys fall in a segment, rewritten into
   * it, so no two segments overlap) or the tail's new segment, in the move's two units.
   * Planned from lengths alone, then streamed (`#placePieces`): nothing is read whole.
   */
  async #archiveMove(): Promise<void> {
    const pick = await this.#door(() => this.#ledger.archivePick());
    if (pick === null) {
      return;
    }
    // Its table's units before its records arrive: the batch's, and its segment's.
    const hold = await MoveHold.take(ownerSlots(), pick.seqs.length + (pick.into?.records ?? 0));
    try {
      const table = new MoveTable();
      const batch = await this.#door(() => this.#ledger.archiveBatch(pick.seqs, table,
        (contentId, bytes, sha256) => table.addContent(
          bytes, this.#bodies.locate([contentId]).get(contentId)!.chunks, sha256)));
      if (batch === null) {
        return;
      }
      const reader = new MoveReader(this.#files, table);
      if (pick.into !== null) {
        const into = pick.into;
        await hold.pass(() => this.#scanSegment(into, table, reader), () => reader.letGo());
      }
      const all = Array.from({ length: table.count }, (_, n) => n);
      await this.#placePieces(table, all, pick.into === null ? [] : [pick.into], pick.seqs,
                              batch.digest, "mail", reader, hold);
    } finally {
      hold.release();
    }
  }

  /**
   * One rewrite pass: the next segment a deleted agent's mail may be in, without that mail;
   * one left under `SEGMENT_LEAST_BYTES` takes in its neighbours by key (never by id), so
   * ranges stay disjoint. Each segment is scanned with the purge's filter: only what is kept
   * enters the move's table, the rest streams past (review N7).
   *
   * Bounded, compacting across passes: a pass takes in a neighbour only while what it keeps
   * and the neighbour's records stay within `MOVE_RECORDS` and the neighbours it scans
   * within `MOVE_BYTES` (a merge's work), and stops there. A pass stopped so, still under the
   * least, places what it kept as one small segment: the next pass, finding a segment under
   * the least that is not the only one (`purgeNext`, derived from the fences alone, so a
   * restore keeps it), takes it back as its target and goes on taking in its neighbours,
   * and so on until it holds the least or stands alone. Each pass leaves the archive whole and disjoint, only less compacted
   * until the last; the first neighbour always fits (a small segment's records are under
   * the least's worth and a piece's at most 49,933), so every pass makes progress. False
   * when no rewrite waits.
   */
  async #archiveRewrite(): Promise<boolean> {
    const target = await this.#door(() => this.#ledger.purgeNext());
    if (target === null) {
      return false;
    }
    const purged = new Set(await this.#door(() => this.#ledger.purgedAgents()));
    const keep = (room: string) => !roomMembers(room).some((member) => purged.has(member));
    // Its table's units before its records arrive, grown as it takes in each neighbour.
    const hold = await MoveHold.take(ownerSlots(), target.fence.records);
    try {
      return await this.#rewriteHeld(target, keep, hold);
    } finally {
      hold.release();
    }
  }

  /** `#archiveRewrite` holding its table's units (`hold`, for the target's records). */
  async #rewriteHeld(target: { fence: Fence; compact: boolean },
                     keep: (room: string) => boolean, hold: MoveHold): Promise<boolean> {
    const table = new MoveTable();
    const scan = (fence: Fence) => {
      const reader = new MoveReader(this.#files, table);
      return hold.pass(() => this.#scanSegment(fence, table, reader, keep),
                       () => reader.letGo());
    };
    const first = await scan(target.fence);
    if (first.kept === first.all && !target.compact) {
      await this.#door(() => this.#ledger.purgeSkip(target.fence.segment));
      return true;
    }
    const replaced = [target.fence];
    let size = 0;
    for (let n = 0; n < table.count; n++) {
      size += table.length(n);
    }
    let scanned = 0;
    // Left small, it takes in its neighbours by key (a neighbour a purge also thinned may
    // not be enough alone), within the pass's bound, until it holds the least or none is
    // left: never by id.
    while (size < SEGMENT_LEAST_BYTES) {
      const span = { ...target.fence,
                     first_key: Math.min(...replaced.map((fence) => fence.first_key)),
                     last_key: Math.max(...replaced.map((fence) => fence.last_key)) };
      const beside = await this.#door(() => this.#ledger.fenceBeside(span));
      if (beside === null) {
        break;
      }
      if (table.count + beside.records > MOVE_RECORDS || scanned + beside.bytes > MOVE_BYTES) {
        break;  // the pass's bound: the next pass goes on from what this one places
      }
      replaced.push(beside);
      scanned += beside.bytes;
      // What it keeps so far and all the neighbour's: within `MOVE_RECORDS` (checked above).
      await hold.table(table.count + beside.records);
      const from = table.count;
      await scan(beside);
      for (let n = from; n < table.count; n++) {
        size += table.length(n);
      }
    }
    if (target.compact && replaced.length === 1) {
      return false;  // a small segment with no neighbour to take in stands alone, as it may
    }
    const all = Array.from({ length: table.count }, (_, n) => n);
    await this.#placePieces(table, all, replaced, [], "0".repeat(64), "rewrite",
                            new MoveReader(this.#files, table), hold);
    return true;
  }

  /**
   * A segment's records appended to a move's table as ranges of its content, never held
   * (review N5): its trailer read, then each record's first line alone (its room, its chain,
   * its time), the chunks read whole one at a time, in order. Only the records `keep` takes
   * (by room) enter the table (review N7: a purge's dropped mail streams past). Its records
   * read, and those kept.
   */
  async #scanSegment(fence: Fence, table: MoveTable, reader: MoveReader | null,
                     keep: (room: string) => boolean = () => true):
      Promise<{ all: number; kept: number }> {
    const located = await this.#door(() => this.#bodies.locate([fence.content_id]));
    const ref = table.addContent(fence.bytes, located.get(fence.content_id)!.chunks, null);
    const content = table.contentOf(ref);
    const read = (reader ?? new MoveReader(this.#files, table)).read;
    const trailer = parseTrailer(utf8Decoder("keep")
      .decode(await lineAt(content, fence.trailer_offset, fence.trailer_bytes, read)));
    let kept = 0;
    for (const [key, offset, length] of trailer.keys) {
      const head = recordHead(await lineAt(content, offset, length, read));
      if (keep(head.room)) {
        keptRecord(table, key, offset, length, ref, head);
        kept++;
      }
    }
    return { all: trailer.keys.length, kept };
  }

  /**
   * A move's records (`indices` into its table) placed as one or two segments (`pack`),
   * replacing `replaced` and the hot batch `seqs` in its second unit (ADR-035's order: the
   * bytes to R2 first, held by uploads that may pass the storage figure, then named in one
   * unit, which may not leave the stored figure above both the figure and where the move
   * found it: `archive_full`). Streamed (review N5, the memory research's §2 A): each piece
   * planned from its records' lengths; pass 1 makes each chunk from the source ranges it
   * covers, hashes it and drops it (and checks each placed body against its message's hash),
   * for `Files.begin`; pass 2 makes each again and sends it, one a call. The sources do not
   * change between the passes (contents are named by their hashes; the batch's rows are
   * checked at the commit by their digest), and a chunk that did differ is refused by R2's
   * check of its hash: loudly.
   */
  async #placePieces(table: MoveTable, indices: readonly number[], replaced: readonly Fence[],
                     seqs: readonly number[], digest: string, stream: "mail" | "rewrite",
                     reader: MoveReader, hold: MoveHold): Promise<void> {
    const ordered = [...indices].sort((a, b) => table.key(a) - table.key(b));
    const plans = ordered.length === 0 ? []
      : pack(ordered, SEGMENT_LEAST_BYTES, (n) => table.length(n)).map((part) => {
        const piece = table.piece(Uint32Array.from(part));
        return { plan: planSegment("mail", piece), indices: piece.indices };
      });
    const hashes: string[][] = [];
    await hold.pass(async () => {
      for (const { plan, indices: at } of plans) {
        const sources = (from: number, to: number) =>
          this.#moveSources(table, at.subarray(from, to));
        const each: string[] = [];
        for (let n = 0; n < plan.chunks; n++) {
          const made = await makeChunk(plan, n, sources, reader.read);
          each.push(sha256Hex(made));
          letGo(made);
        }
        hashes.push(each);
      }
    }, () => reader.letGo());
    reader.bodiesChecked();
    if (this.#betweenPasses !== null) {
      const between = this.#betweenPasses;
      await this.#door(() => between(this.#storage, { stream, replaced: replaced.length,
                                                     records: table.count }));
    }
    const pieces = plans.map(({ plan, indices: at }) => {
      const members = new Set<string>();
      let firstAt = Number.MAX_SAFE_INTEGER;
      let lastAt = 0;
      for (const n of at) {
        for (const member of roomMembers(table.room(n))) {
          members.add(member);
        }
        firstAt = Math.min(firstAt, table.at(n));
        lastAt = Math.max(lastAt, table.at(n));
      }
      return { plan, at, fence: {
        first_key: plan.first, last_key: plan.last, first_at: firstAt, last_at: lastAt,
        records: plan.records.count, bytes: plan.size,
        trailer_offset: plan.trailer_offset, trailer_bytes: plan.trailer_bytes,
        members: Ledger.members(members) } };
    });
    const begun = await this.#door(() => {
      const before = this.#files.storedNow().stored;
      const started = pieces.map((piece, n) => this.#files.begin(
        piece.plan.size, hashes[n]!, "owner", [], true, null, true));
      this.#ledger.moveBegun(stream, started[0]?.intent_id ?? "0".repeat(32), digest, clock(),
                             before);
      return { started, before };
    });
    try {
      await hold.pass(async () => {
        for (const [n, piece] of pieces.entries()) {
          const sources = (from: number, to: number) =>
            this.#moveSources(table, piece.at.subarray(from, to));
          const wanted = new Set(begun.started[n]!.missing);
          for (const [k, sha256] of hashes[n]!.entries()) {
            if (wanted.delete(sha256)) {
              const made = await makeChunk(piece.plan, k, sources, reader.read);
              await this.#files.upload(begun.started[n]!.intent_id, [{ sha256, bytes: made }]);
              letGo(made);
            }
          }
        }
      }, () => reader.letGo());
      await this.#door(() => {
        const named: { content_id: string; fence: (typeof pieces)[number]["fence"] }[] = [];
        const adopt = (n: number): void => {
          if (n === pieces.length) {
            this.#ledger.archiveCommit(seqs, digest, replaced, named, clock());
            const { stored, figure } = this.#files.storedNow();
            if (figure !== null && stored > Math.max(figure, begun.before)) {
              throw new Refusal("archive_full",
                                "archive_full: the move would leave the storage figure higher " +
                                "than it found it");
            }
            this.#ledger.moveEnded(stream);
            return;
          }
          this.#files.adopt(begun.started[n]!.intent_id, "owner", (contentId) => {
            named.push({ content_id: contentId, fence: pieces[n]!.fence });
            adopt(n + 1);
          });
        };
        adopt(0);
      });
    } finally {
      await this.#door(() => {
        for (const started of begun.started) {
          this.#files.abort(started.intent_id);
        }
        this.#ledger.moveEnded(stream, begun.started[0]?.intent_id ?? "0".repeat(32));
      });
    }
  }

  /**
   * The sources of a group of a move's records (`at`, their indices in its table), in their
   * order: a kept one's range of its segment; a hot one's lines, read again in a unit,
   * around its body (inline, or its placed content's whole range).
   */
  async #moveSources(table: MoveTable, at: Uint32Array): Promise<(readonly Run[])[]> {
    const hot: number[] = [];
    for (const n of at) {
      if (table.kind(n) !== MoveTable.KEPT) {
        hot.push(table.key(n));
      }
    }
    const parts = hot.length === 0 ? new Map<number, MailParts>()
      : await this.#door(() => this.#ledger.archiveParts(hot));
    return Array.from(at, (n): Run[] => {
      if (table.kind(n) === MoveTable.KEPT) {
        return [{ content: table.content(n), offset: table.offset(n), length: table.length(n) }];
      }
      const own = parts.get(table.key(n))!;
      if (table.kind(n) === MoveTable.INLINE) {
        return [own.head, own.inline!, own.tail];
      }
      const content = table.content(n);
      return [own.head, { content, offset: 0, length: content.size }, own.tail];
    });
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
          throw new Refusal("refused", "only an agent's own client subscribes its doorbell");
        }
        return { events: [] };
      }
      if (commandCenter === undefined) {
        const reads = grant.mode === "full"
          || grant.scopes.some((scope) => scope.capability === "read_graph");
        return { events: reads ? wakeKinds().flatMap((kind) => kind.push?.events ?? []) : [] };
      }
      if (typeof commandCenter !== "string") {
        throw new Refusal("invalid", "invalid subscription arguments");
      }
      this.#scope(account, "read_graph", commandCenter);
      const agent = this.#ledger.subscriber(account.origin, commandCenter);
      if (agent === undefined) {
        throw new Refusal("refused", "not this caller's subscribing agent");
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
        throw new Refusal("refused", "only an agent's own client subscribes its doorbell");
      }
      if (typeof r["name"] !== "string" || eventKind(r["name"]) === null) {
        throw new Refusal("invalid", "unknown event");
      }
      const args = r["arguments"] as Record<string, unknown> | null;
      const follow = follows(args, ["command_center_id"]);
      const center = args?.["command_center_id"];
      if (typeof center !== "string") {
        throw new Refusal("invalid", "invalid subscription arguments");
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
        throw new Refusal("refused", "only the client that holds a subscription ends it");
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
      throw new Refusal("owner_erased", OWNER_ERASED);
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
      throw new Refusal("erasing", ERASING);
    }
    if (typeof binding !== "object" || binding === null) {
      throw new Refusal("invalid", "invalid owner principal");
    }
    const owner = principal(binding.owner);
    const caller = principal(binding.caller);
    if (owner === PLATFORM_CALLER || (door !== "admission" && caller === PLATFORM_CALLER)) {
      throw new Refusal("refused", PLATFORM_RESERVED);
    }
    // The object's own name first: an object for another owner, or for none, says so
    // whatever its admission.
    const named = this.ctx.id.name;
    if (named === undefined) {
      throw new Refusal("refused", "this object is not named by an owner");
    }
    if (named !== owner) {
      throw new Refusal("refused", "this object belongs to another owner");
    }
    if (door !== "admission") {
      const refusal = doorRefusal(admission(this.#storage), door);
      if (refusal !== null) {
        throw refusal;
      }
    }
    // An inbound event's binding reaches `inboundAccept` alone, and that door nothing else.
    if (inbound !== inboundDoor) {
      throw new Refusal("refused", inbound ? INBOUND_ONLY : INBOUND_DOOR);
    }
    // A sandbox token reaches its own doors and nothing else: the egress token the egress
    // doors, the sync token the sync doors; they take nothing else.
    // A run's call is the egress token's, with its run: the run doors (`#runAccount`) alone.
    const presented = binding.sandbox === undefined ? "none"
      : (binding.sandbox as { kind?: unknown }).kind;
    const expected = sandbox === "run" ? "egress" : sandbox;
    if (expected !== presented || (sandbox === "run") !== ("run" in binding)) {
      throw new Refusal("refused", sandbox === "none" ? SANDBOX_DOORS : SANDBOX_ONLY);
    }
    if (sandbox !== "none") {
      // The binder's sandbox binding in its exact shape (`bindSandbox`, `bindRun`), checked
      // before anything is written: a stranger's request writes nothing.
      const keys = sandbox === "run" ? "caller,origin,owner,run,sandbox"
        : "caller,origin,owner,sandbox";
      if (Object.keys(binding).sort().join() !== keys || binding.origin !== null
          || caller !== owner) {
        throw new Refusal("invalid", "invalid sandbox binding");
      }
      sandboxBinding(binding.sandbox);
      if (sandbox === "run" && sandboxBinding(binding.sandbox).kind !== "egress") {
        throw new Refusal("invalid", "invalid sandbox binding");
      }
    }
    const sql = this.#storage.sql;
    const stored = sql.exec<{ owner: string }>("SELECT owner FROM object_owner").toArray()[0];
    if (stored === undefined && sandbox !== "none") {
      // Only an object that minted a lease's tokens has a sandbox: a never-seen owner gets
      // no rows from one.
      throw new Refusal("refused", NO_SANDBOX);
    }
    // The owner is stored at its first door past the platform's admit, never by the admit.
    if (stored === undefined && door !== "admission") {
      sql.exec("INSERT INTO object_owner (only, owner) VALUES (1, ?)", owner);
    } else if (stored !== undefined && stored.owner !== owner) {
      throw new Refusal("refused", "this object belongs to another owner");
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
      throw new Refusal("sync_fenced", SYNC_FENCED);
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
          throw new Refusal("sync_fenced", SYNC_FENCED);
        }
        grant = this.#grants.standing(stamp.client, stamp.generation, stamp.authenticated_at);
      } catch (error) {
        if (error instanceof Refusal && !error.invalid) {
          throw new Refusal("sync_fenced", SYNC_FENCED);
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
        throw new Refusal("refused", "outside client capability not granted");
      }
      const agents = this.#runs.liveAgents(lease.authority)
        .map((agent) => `${AGENTS_DIR}/${agent}`);
      return { lease: lease.lease_id, scope: agents.join("\n"),
               test: (path) => path === AGENTS_DIR || agents.some((dir) => within(path, dir))
                 || ABILITY_TREES.some((tree) => within(path, tree)) };
    }
  }

  #ownerOnly(owner: string, caller: string, action: string): void {
    if (caller !== owner) {
      throw new Refusal("refused", `only the owner may ${action} collection rows`);
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
    // Whose files scope the write's bodies count in: the tier's one storage number holds
    // them, inline or placed, retention making room first (`Files.held`).
    let writer = "owner";
    for (let tries = 1; first === undefined; tries++) {
      try {
        first = await this.#door(():
            { done: { value: T; located: Located } } | { placing: Placing } => {
          const prepared = prepare();
          if ("done" in prepared) {
            return { done: located(prepared.done) };
          }
          writer = prepared.writer;
          const placing = this.#bodies.begin(prepared.body as string, prepared.writer);
          return placing === null ? { done: located(this.#files.held(writer, () => act(null))) }
            : { placing };
        });
      } catch (error) {
        if (!(error instanceof Refusal) || error.code !== "chunk_deleting"
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
        done = await this.#door(() => located(this.#files.held(writer, () => act(placed))));
      } finally {
        await this.#bodies.release(placed);
      }
    }
    if (options.synced === true) {
      await this.#schedule.sync();
    }
    // A conflict's current row, placed, is read back as a read door reads it.
    return shown === undefined || done.located.size === 0 ? done.value
      : shown.fill(done.value, await this.#bodies.windows(done.located, () => PAGE_BYTES));
  }

  /**
   * A read door whose unit marks what it returns (a mailbox read, ADR-035): the placed
   * bodies it would return located in a unit of their own (`locate`, its caller checked),
   * read from R2 (`fetch`: windows for a page, or whole for a run's input), then its unit (`read`, the caller checked again) handed them; it reads again, a
   * few times at most, while `again` says mail between put an unread placed body first.
   */
  async #rereading<T, B>(locate: () => readonly string[], read: (bodies: B) => T,
                         again: (value: T) => boolean,
                         fetch: (located: Located) => Promise<B>): Promise<T> {
    for (let tries = 0; ; tries++) {
      const located = await this.#door(() => this.#bodies.locate(locate()));
      const bodies = await fetch(located);
      const value = await this.#door(() => read(bodies));
      if (!again(value) || tries >= READ_TRIES) {
        return value;
      }
    }
  }

  /**
   * `#rereading` for a page: its bodies' windows (`Wants`: each at most `maxOf` bytes, from
   * `offsetOf`).
   */
  #rereadingPage<T>(locate: () => Wants, read: (windows: ReadonlyMap<string, Window>) => T,
                    again: (value: T) => boolean): Promise<T> {
    let wants: Wants = { contents: [] };
    return this.#rereading(() => {
      wants = locate();
      return wants.contents;
    }, read, again, (located) => this.#bodies.windows(located, wants.maxOf ?? (() => PAGE_BYTES),
                                                      wants.offsetOf));
  }

  /**
   * A read door's unit, then the windows of the placed bodies its rows name read from R2
   * (ADR-035), and handed to `fill`, which fits the page to
   * one answer: `unit` returns its result and the content ids it names, located in the same
   * unit, so each is the content its row named.
   */
  async #reading<T, U = T>(unit: () => { value: T } & Wants,
                           fill: (value: T, windows: ReadonlyMap<string, Window>) => U,
                           synced = false): Promise<U> {
    const { value, located, wants } = await this.#door(() => {
      const read = unit();
      return { value: read.value, located: this.#bodies.locate(read.contents), wants: read };
    });
    if (synced) {
      await this.#schedule.sync();  // a delete that rang an agent's doorbell (`#ring`)
    }
    return fill(value, located.size === 0 ? new Map()
      : await this.#bodies.windows(located, wants.maxOf ?? (() => PAGE_BYTES), wants.offsetOf));
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

  /**
   * A row written by compare-and-swap; the owner's own board or room write rings (`#ring`).
   * Answered without its body (review H3): its etag, revision, seq and `body_bytes`, so a
   * body past one answer is never echoed back.
   */
  collectionWrite(binding: Binding, request: WriteRequest):
      Promise<{ row: WrittenRow } | Conflicted> {
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
        row: writtenRow(this.#collections.write(account.owner, account.caller, collection,
                                                r["row_id"] as string, r["body"],
                                                r["expected_etag"], r["private"],
                                                "history_op" in r ? r["history_op"] : "write",
                                                placed, filerOf(account))),
      }));
      this.#ring(account, collection, before);
      return written;
    }, { shown: conflictShown(), synced: true });
  }

  /**
   * A collection's rows past `after`, fitted to one answer (`pages.ts`): when rows are left
   * the cursor is the last one's seq, and a first row cut to fit is read on with
   * `collection_get`'s `body_offset`.
   */
  collectionPull(binding: Binding, request: PullRequest): Promise<Page> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["collection", "after", "limit"]);
      const page = this.#collections.pull(caller, r["collection"] as string, r["after"],
                                          r["limit"]);
      return { value: page, contents: contentsOf(page.rows) };
    }, (page, windows) => {
      const rows = page.rows.length === 0 ? [] : fitPage(
        jsonBytes({ cursor: page.cursor, rows: [] }), windowedAll(page.rows, windows),
        ({ item, window }, room, first) => bodied(item, window, room, first));
      return { cursor: rows.length === page.rows.length ? page.cursor : rows.at(-1)!.seq,
               rows: rows as Row[] };
    });
  }

  /**
   * One row, fitted to one answer: with `body_offset`, its body from that byte, and with
   * `etag`, refused when the row is no longer the one that answer read.
   */
  collectionGet(binding: Binding, request: GetRequest): Promise<{ row: Row | null }> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["collection", "row_id"], ["body_offset", "etag"]);
      const offset = bodyOffset(r["body_offset"]);
      // A read on names the revision it reads (review L3), so two are never spliced.
      if (offset > 0 && r["etag"] === undefined) {
        throw new Refusal("invalid", "body_offset reads on a row's body: name its etag");
      }
      const got = this.#collections.get(caller, r["collection"] as string, r["row_id"] as string);
      if (r["etag"] !== undefined && got.row?.etag !== r["etag"]) {
        throw new Refusal("row_changed",
                          "row_changed: the row is no longer the one at that etag: read it " +
                          "again from its start");
      }
      return { value: { got, offset }, contents: contentsOf(got.row === null ? [] : [got.row]),
               offsetOf: () => offset };
    }, ({ got, offset }, windows) => ({ row: got.row === null ? null
      : fittedRow(windowed(got.row, windows, offset), jsonBytes({ row: null }) - 4) as Row }));
  }

  /**
   * A row's kept history, fitted to one answer, newest first: when entries are left `more`
   * and `before_revision` name the older ones; `body_offset` reads the page's newest
   * entry's body from that byte.
   */
  collectionHistory(binding: Binding, request: HistoryRequest): Promise<History> {
    return this.#reading(() => {
      const { caller } = this.#collector(binding, "read", "read_graph");
      const r = fields(request, ["collection", "row_id"], ["before_revision", "body_offset"]);
      const offset = bodyOffset(r["body_offset"]);
      const kept = this.#collections.history(caller, r["collection"] as string,
                                             r["row_id"] as string, r["before_revision"] ?? null);
      const newest = kept.entries.at(-1)?.content_id;
      return { value: { kept, offset }, contents: contentsOf(kept.entries),
               offsetOf: (contentId) => contentId === newest ? offset : 0 };
    }, ({ kept, offset }, windows) => {
      const fitted = kept.entries.length === 0 ? [] : fitPage(
        jsonBytes({ entries: [], more: true, before_revision: kept.before_revision }),
        windowedAll([...kept.entries].reverse(), windows, offset),
        ({ item, window }, room, first) => bodied(item, window, room, first));
      const entries = (fitted as HistoryEntry[]).reverse();
      return entries.length === kept.entries.length ? { ...kept, entries }
        : { entries, more: true, before_revision: entries[0]!.revision };
    });
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

  /**
   * A change to the sync's ignore list, in its own unit: a receipt and one notice naming
   * who changed it (the owner, an agent's run, or an outside client), and, when a line it
   * adds stops files the owner already keeps from syncing their changes, how many and which.
   */
  #syncIgnoreChanged(present: boolean, now: number,
                     change: { writer: string; content_id: string | null; size: number }): void {
    const outcome = present ? "changed" : "removed";
    const who = this.#writerNamed(change.writer);
    this.#policy.receipt("write", { rule: "sync_ignore", outcome, decision: who }, now);
    let held = "";
    if (present && change.content_id !== null) {
      // Whoever wrote it, its new lines are read (a set's work, the list at most an inline
      // file), never matched against the owner's files here.
      const lines = (bytes: Uint8Array | null) => bytes === null ? null
        : ruleLines(new TextDecoder().decode(bytes).split("\n"));
      const after = lines(this.#files.inlineOf(change.content_id));
      const before = this.#files.contentBefore(SYNC_IGNORE, this.#files.lastVersion(SYNC_IGNORE));
      const was = new Set(before === null ? [] : lines(this.#files.inlineOf(before.content_id))
        ?? []);
      const defaults = new Set(DEFAULT_IGNORES);
      if (after === null || after.some((line) => !was.has(line) && !defaults.has(line))) {
        held = " Its new lines may hold back files you keep from syncing their changes; " +
          "each sandbox sync reports the files they hold back.";
      }
      // Lines past a ceiling are not applied: said here, never in silence.
      const raw = this.#files.inlineOf(change.content_id);
      const past = raw === null ? [] : unappliedLines(new TextDecoder().decode(raw).split("\n"));
      const states = past.filter((each) => each.reason === "states");
      if (states.length > 0) {
        held += ` ${states.length} line${states.length === 1 ? " is" : "s are"} past the ${
          AUTOMATON_STATES.toLocaleString("en")} automaton states the lines with *, ? or [ ] ` +
          `may take together, so not applied: the files they name go on syncing (${
            states.slice(0, 3).map((each) => each.line).join(", ")}${states.length > 3
            ? ", …" : ""}).`;
      }
      if (change.size > LIST_BYTES) {
        held += ` The list past its ${LIST_BYTES.toLocaleString("en")} bytes is not applied: ` +
          "the files those lines name go on syncing.";
      }
      // The owner's own sandbox, whose mirror is the owner's whole tree, adds what its sync
      // agent counted (`sync.ts`); an outside client's sees only its part, so its count is
      // never given, and no count ever takes the sentence away.
      const synced = this.#heldBack;
      if (synced !== null && /^lease:[0-9]+$/.test(change.writer) && synced.kept > 0) {
        held += ` In the sync that wrote it, its lines hold back ${synced.kept} file${
          synced.kept === 1 ? "" : "s"} you keep (${synced.changed} with changes in the ` +
          "sandbox): your files keep the version they have, and each sync reports them.";
      }
    }
    this.#notices.add("sync_ignore_changed", `${SYNC_IGNORE} was ${outcome} by ${who}: what ` +
                        `the sandbox keeps out of your files follows it.${held}`, now);
  }

  /**
   * Who a file change's writer is, in words the owner reads: the owner; the agents whose
   * runs hold the owner's sandbox lease it was synced under; or an outside client by name.
   */
  #writerNamed(writer: string): string {
    const epoch = /^lease:([0-9]+)$/.exec(writer)?.[1];
    if (epoch !== undefined) {
      // The agents named at most three, so the receipt stays its size.
      const agents = this.#runs.agentsAt(Number(epoch), 4);
      return agents.length === 0 ? "a run in your sandbox"
        : `a run of your agent${agents.length === 1 ? "" : "s"} ${agents.slice(0, 3).join(", ")}${
          agents.length > 3 ? " and others" : ""}`;
    }
    const client = /^client:(.+)$/.exec(writer)?.[1];
    if (client !== undefined) {
      const name = this.#grants.name(client);
      return `the outside client ${name !== "" ? `"${name}"` : client.slice(0, 12)}`;
    }
    return writer === "owner" ? "you" : writer;
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
   * A change to the owner's agent definitions (`OWNER_TREES`: its agents' files, skills, goals and
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
        throw new Refusal("refused", AGENTS_OWNER_ONLY);
      }
      return;
    }
    if (account.origin !== null) {
      if (account.grant?.mode !== "full") {
        throw new Refusal("refused", AGENTS_OWNER_ONLY);
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
    // A run's own call carries its egress token as its sandbox: a file door's caller too.
    if (binding.sandbox === undefined || binding.run !== undefined) {
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

  /**
   * A directory's entries by name; with `head_chars`, each small file's (inline, so no
   * bucket is read) first characters of its text, enough for its frontmatter.
   */
  async fileList(binding: Binding, request: FileListRequest):
      Promise<{ entries: (Entry & { head?: string })[]; next: string | null }> {
    await this.#seededFor(() => {
      this.#reader(binding, "read", "read_files");
    });
    return this.#door(() => {
      const visible = this.#reader(binding, "read", "read_files");
      const r = fileFields(request, ["dir", "limit"], ["after", "head_chars"]);
      const listed = this.#files.list(r["dir"], r["after"] ?? null, r["limit"]);
      const entries = listed.entries.filter((entry) => visible(entry.path));
      const chars = r["head_chars"] === undefined ? null
        : whole(r["head_chars"], "head_chars", 0, Number.MAX_SAFE_INTEGER);
      // One answer holds the page (`pages.ts`): when entries are left, `next` is the last
      // one's name, so the next page starts after it.
      const shown = chars === null ? entries : entries.map((entry) => {
        if (entry.kind !== "file" || entry.size > INLINE_BYTES) {
          return entry;
        }
        const inline = this.#files.open(entry.path).inline;
        const text = inline === undefined ? null : textOf(inline);
        return text === null ? entry : { ...entry, head: [...text].slice(0, chars).join("") };
      });
      return fitList({ ...listed, entries: shown }, shown,
                     { entries: [], next: String.fromCharCode(1).repeat(MAX_NAME_BYTES) },
                     (kept) => ({ entries: kept, next: nameOf(kept.at(-1)!.path) }));
    });
  }

  /**
   * A window of a file's text (`file_read`): its UTF-8 bytes from `offset`, at most
   * `max_bytes` (`READ_DEFAULT_BYTES` when omitted; at most one chunk, the files plane's own
   * unit, a technical ceiling), cut back to a character boundary, and where the next window
   * starts (null at the end). A file that is not UTF-8 text is refused as such.
   */
  async fileText(binding: Binding, request: FileTextRequest):
      Promise<{ entry: Entry; offset: number; text: string; next_offset: number | null }> {
    await this.#seededFor(() => {
      this.#filer(binding, "read", "read_files");
    });
    const asked = await this.#door(() => {
      this.#filer(binding, "read", "read_files");
      const r = fileFields(request, ["path"], ["offset", "max_bytes"]);
      const content = this.#files.open(r["path"]);
      const offset = whole(r["offset"], "offset", 0, content.entry.size);
      const max = whole(r["max_bytes"], "max_bytes", READ_DEFAULT_BYTES, CHUNK_BYTES);
      if (max < 1) {
        throw new Refusal("invalid", "max_bytes is at least 1");
      }
      return { content, offset, max };
    });
    const { content, offset, max } = asked;
    // One answer holds the window (`pages.ts`): its text cut to what the entry leaves.
    const room = PAGE_BYTES - jsonBytes({ entry: content.entry, offset, text: "",
                                          next_offset: content.entry.size }) + 2;
    if (room < 8) {
      throw new Refusal("result_too_large", RESULT_TOO_LARGE);
    }
    const read = await this.#window(content, offset, Math.min(max, room));
    if (read.text === null) {
      throw new Refusal("invalid", `${content.entry.path} is not UTF-8 text`);
    }
    const part = fitted(read.text, room);
    const end = offset + part.bytes;
    return { entry: content.entry, offset, text: part.text,
             next_offset: end < content.entry.size ? end : null };
  }

  /**
   * The text of `content` from `offset`, at most `max` bytes cut back to a character: its
   * inline bytes, or the chunks the window spans (at most two reads).
   */
  async #window(content: Content, offset: number, max: number):
      Promise<{ text: string | null; next: number | null }> {
    const size = content.entry.size;
    const want = Math.min(size, offset + max + 4);  // a character's tail past the window
    let bytes: Uint8Array;
    if (content.inline !== undefined) {
      bytes = content.inline.subarray(offset, want);
    } else {
      const reads: { sha256: string; offset: number; length: number; verify: true }[] = [];
      let at = 0;
      for (const chunk of content.chunks ?? []) {
        const from = Math.max(offset, at);
        const to = Math.min(want, at + chunk.size);
        if (from < to) {
          // Verified (review L7): `FILES` rechecks the whole chunk and answers the range.
          reads.push({ sha256: chunk.sha256, offset: from - at, length: to - from,
                       verify: true });
        }
        at += chunk.size;
      }
      const parts = await this.#files.fetch(reads);
      bytes = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
      let put = 0;
      for (const part of parts) {
        bytes.set(part, put);
        put += part.byteLength;
      }
    }
    const end = windowEnd(bytes, 0, max);
    if (end === null) {
      throw new Refusal("invalid", "offset is inside a character: start at a window's next_offset");
    }
    return { text: textOf(bytes.subarray(0, end)),
             next: offset + end < size ? offset + end : null };
  }

  /**
   * A search of the owner's files (`search`; `file-search.ts`): the query's words, whole,
   * against each file's path, and each small file's frontmatter, text and index lines, a
   * page of the manifest at a time from `cursor` (`SEARCH_SCAN` entries, `SEARCH_ANSWER_BYTES`
   * of results); every match of the page, best first, and `next_cursor` right after the last
   * one answered or the last entry read, null at the end; a query of at most
   * `SEARCH_QUERY_BYTES`.
   */
  async fileSearch(binding: Binding, request: FileSearchRequest):
      Promise<{ results: SearchResult[]; next_cursor: string | null; note?: string }> {
    await this.#seededFor(() => {
      this.#filer(binding, "read", "read_files");
    });
    return this.#door(() => {
      this.#filer(binding, "read", "read_files");
      const r = fileFields(request, ["query"], ["cursor"]);
      const query = r["query"];
      if (typeof query !== "string" || utf8Bytes(query) > SEARCH_QUERY_BYTES) {
        throw new Refusal("invalid", `a query is text of at most ${SEARCH_QUERY_BYTES} bytes`);
      }
      const terms = new Set(words(query));
      if (terms.size === 0) {
        throw new Refusal("invalid", "a query has at least one word");
      }
      const cursor = r["cursor"] === undefined ? null : storedPath(cursorPath(r["cursor"]));
      const scanned = this.#files.scan(cursor, SEARCH_SCAN);
      const page = searchPage(scanned.entries, scanned.next !== null, terms, fileUrl);
      const answer = page.resume === null ? { results: [...page.results], next_cursor: null }
        : { results: [...page.results], next_cursor: pathCursor(page.resume),
            note: "more of the files are still to search: search again with next_cursor" };
      // Only paths of thousands of escaped characters pass one answer: told, never cut.
      if (jsonBytes(answer) > PAGE_BYTES) {
        throw new Refusal("result_too_large", RESULT_TOO_LARGE);
      }
      return answer;
    });
  }

  /**
   * A file by a search's id, its stored path (`fetch`): its title, its text (at most one
   * chunk's, the files plane's own unit; `file_read` reads past it), the app's url for a
   * citation, and its metadata.
   */
  async fileFetch(binding: Binding, request: FileFetchRequest):
      Promise<{ id: string; title: string; text: string; url: string;
                metadata: FetchMetadata }> {
    await this.#seededFor(() => {
      this.#filer(binding, "read", "read_files");
    });
    const content = await this.#door(() => {
      this.#filer(binding, "read", "read_files");
      const r = fileFields(request, ["id"]);
      return this.#files.open(r["id"]);
    });
    // One answer holds it (`pages.ts`): its text cut to what its other fields leave, and
    // `truncated` with `next_offset` for file_read to go on from.
    const read = await this.#window(content, 0, PAGE_BYTES);
    // A file search found that is not text (a scan, an image) is answered with its metadata
    // and no text, never refused: every id a search answers is fetchable (review L4).
    const found = frontmatter(read.text ?? "");
    const path = content.entry.path;
    // A frontmatter too wide for one answer beside its path is cut, said so (review L5):
    // every id a search answers is fetchable, whatever its fields hold.
    let front = found;
    let cut = false;
    const answer = (text: string, next: number | null) => ({
      id: path, title: titleOf(path, front), text, url: fileUrl(path),
      metadata: { ...(cut ? { fields_cut: true as const } : { path }),
                  size: content.entry.size, version: content.entry.version,
                  modified: new Date(content.entry.mtime_ms).toISOString(),
                  writer: content.entry.writer, etag: content.entry.etag,
                  ...(front.type === undefined ? {} : { type: front.type }),
                  ...(front.description === undefined ? {} : { description: front.description }),
                  ...(front.tags.length === 0 ? {} : { tags: front.tags }),
                  ...(read.text === null ? { binary: true as const } : {}),
                  truncated: read.text !== null && next !== null,
                  ...(read.text === null || next === null ? {} : { next_offset: next }) },
    });
    let room = PAGE_BYTES - jsonBytes(answer("", content.entry.size)) + 2;
    if (room < 8) {
      // Its metadata's path is its id's; its frontmatter's fields, a few KiB each at most.
      cut = true;
      const { title, description, type } = found;
      front = { ...(title === undefined ? {} : { title: fitted(title, 1024).text }),
                ...(description === undefined ? {}
                  : { description: fitted(description, 4096).text }),
                ...(type === undefined ? {} : { type: fitted(type, 256).text }),
                tags: found.tags.filter((_, index) => index < 16)
                  .map((tag) => fitted(tag, 256).text) };
      room = PAGE_BYTES - jsonBytes(answer("", content.entry.size)) + 2;
      if (room < 8) {
        throw new Refusal("result_too_large", RESULT_TOO_LARGE);
      }
    }
    const part = fitted(read.text ?? "", room);
    return answer(part.text, read.text === null || part.bytes >= content.entry.size ? null
      : part.bytes);
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
        throw new Refusal("invalid", "first and count name 1 or 2 of the version's chunks");
      }
      return chunks.slice(first, first + many).map(({ sha256 }) => ({ sha256 }));
    });
    return { chunks: await this.#files.fetch(reads) };
  }

  /**
   * Write a whole file of at most one chunk (4 MiB); larger ones go by upload. Its content is
   * bytes, or text written as UTF-8 (`file_write`, an outside agent's whole replacement). An
   * outside client or a run writes at the etag it read, or `""` to create a file only when it
   * is absent, never with no etag at all (review H1): a replacement never lands over a file
   * the agent did not read. The owner's own app may still write with none.
   */
  async fileWrite(binding: Binding, request: FileWriteRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    await this.#seededFor(() => {
      this.#filer(binding, "work", "write_files");
    });
    const { account, r, content } = await this.#door(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["path", "expected_etag", "content"], ["mode"]);
      this.#ownersTree(binding, account, "FILES.write", request, r["path"]);
      const asked = r["expected_etag"];
      if ((account.origin !== null || account.run !== null) && typeof asked !== "string") {
        throw new Refusal("invalid", UNREAD_WRITE);
      }
      const given = r["content"];
      const content = typeof given === "string" ? new TextEncoder().encode(given) : given;
      if (!(content instanceof Uint8Array) || content.byteLength > CHUNK_BYTES) {
        throw new Refusal("invalid",
                          "a written file is bytes or text, at most 4 MiB; larger ones are " +
                          "uploaded");
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
   * Text added at the end of a file (`file_append`, slice BRAIN: an outside agent's everyday
   * write-back), the file made when absent. It never changes a byte already there, so it is
   * additive (OpenAI's `destructiveHint: false`): the file is read, the text put after it, and
   * the whole written at the etag it was read at, so two appends never lose each other; one
   * that meets another's write reads the file again and puts its text after that. A file of
   * whole chunks keeps them as they are (`Files.write`'s `prefix`), so only its last chunk
   * and the text are written again, at any size. The text is at most one chunk (4 MiB).
   * A structured file (`STRUCTURED`: JSON, TOML, YAML) is refused: text put after its end
   * breaks what it means however whole its bytes stay, so it is edited (the founder,
   * 2026-11-04). A file that stays small (at most `INLINE_BYTES` with the text) is read and
   * written in one unit (`Files.writeSmall`), so its append never meets another write and
   * never tries twice. A larger one is written around its chunks' `FILES` calls, so another
   * write to it can land between its read and its commit: it then reads again, checking the
   * caller's authority and the agent trees each try, at most `APPEND_TRIES` times, past which
   * it is refused `file_busy`.
   */
  async fileAppend(binding: Binding, request: FileAppendRequest): Promise<{ entry: Entry }> {
    await this.#seededFor(() => {
      this.#filer(binding, "work", "write_files");
    });
    const { account, path, added } = await this.#door(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["path", "content"]);
      this.#ownersTree(binding, account, "FILES.append", request, r["path"]);
      if (STRUCTURED.test(storedPath(r["path"]))) {
        throw new Refusal("invalid",
                          "a structured file (JSON, TOML or YAML) is never appended to: read " +
                          "it and change it with file_edit");
      }
      const content = r["content"];
      if (typeof content !== "string" || content === "") {
        throw new Refusal("invalid", "appended content is text of at least one character");
      }
      const added = new TextEncoder().encode(content);
      if (added.byteLength > CHUNK_BYTES) {
        throw new Refusal("invalid", "appended text is at most 4 MiB at once");
      }
      return { account, path: storedPath(r["path"]), added };
    });
    const authorize = () => {
      this.#ownersTree(binding, this.#filer(binding, "work", "write_files"), "FILES.append",
                       request, path);
    };
    try {
      for (let tries = 0; ; tries += 1) {
        if (tries === APPEND_TRIES) {
          throw new Refusal("file_busy",
                            `file_busy: ${path} changed under each of ${APPEND_TRIES} tries: ` +
                            "append again");
        }
        const read = await this.#door((): { done: Entry } | { now: Content | null } => {
          authorize();
          const entry = this.#files.stat(path);
          if (entry !== null && entry.kind !== "file") {
            throw new Refusal("invalid", `${path} is a directory`);
          }
          const content = entry === null ? null : this.#files.open(path);
          const before = content === null ? new Uint8Array(0) : content.inline;
          if (before !== undefined && before.byteLength + added.byteLength <= INLINE_BYTES) {
            // Small: read and written in this one unit, so nothing lands between.
            const whole = new Uint8Array(before.byteLength + added.byteLength);
            whole.set(before, 0);
            whole.set(added, before.byteLength);
            return { done: this.#files.writeSmall(path, whole, {
              expected: entry?.etag ?? "", mode: entry?.mode, writer: fileWriter(account) }) };
          }
          return { now: content };
        });
        if ("done" in read) {
          return { entry: read.done };
        }
        const now = read.now;
        // The file's last chunk and the text, joined and written, in one step of
        // `APPEND_UNITS` (`holds.ts`, ADR-037): what it holds is the same whatever the
        // file's size.
        const wrote = await slotted(ownerSlots(), APPEND_UNITS, async () => {
          let bytes = added;
          let prefix: ChunkRef[] = [];
          if (now !== null) {
            let tail: Uint8Array;
            if (now.inline !== undefined) {
              tail = now.inline;
            } else {
              const chunks = now.chunks ?? [];
              const whole = chunks.length > 0 && chunks.at(-1)!.size === CHUNK_BYTES
                ? chunks.length : chunks.length - 1;
              prefix = chunks.slice(0, whole);
              const rest = chunks.slice(whole);
              tail = rest.length === 0 ? new Uint8Array(0)
                : (await this.#files.fetch(rest.map(({ sha256 }) => ({ sha256 }))))[0]!;
            }
            bytes = new Uint8Array(tail.byteLength + added.byteLength);
            bytes.set(tail, 0);
            bytes.set(added, tail.byteLength);
          }
          return fileConflictedAfter(async () => ({
            entry: await this.#files.write(path, bytes, {
              expected: now?.entry.etag ?? "",
              ...(now === null ? {} : { mode: now.entry.mode }), prefix,
              writer: fileWriter(account), authorize }),
          }));
        });
        if (!("conflict" in wrote)) {
          return wrote;
        }
        // Another write landed between the read and this one: read it again.
      }
    } finally {
      await this.#schedule.sync();
    }
  }

  /**
   * One exact occurrence of `old_text` in a file's text replaced by `new_text`, at the etag
   * the caller read (`file_edit`, slice BRAIN: a correction, as a stale directive rewritten in
   * place). Text that occurs nowhere, or more than once, is refused with how many times, so
   * the caller gives more of the text around it. A chunked file is edited chunk by chunk
   * (`edit.ts`, ADR-037), each chunk read or made in a step of the isolate's units of its
   * own, at most `EDIT_BYTES_MAX`; a small one, whole.
   */
  async fileEdit(binding: Binding, request: FileEditRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    await this.#seededFor(() => {
      this.#filer(binding, "work", "write_files");
    });
    const asked = await this.#door(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["path", "old_text", "new_text", "expected_etag"]);
      this.#ownersTree(binding, account, "FILES.edit", request, r["path"]);
      const [old, replacement, etag] = [r["old_text"], r["new_text"], r["expected_etag"]];
      if (typeof old !== "string" || old === "" || typeof replacement !== "string") {
        throw new Refusal("invalid",
                          "old_text is text of at least one character, and new_text is text");
      }
      if (typeof etag !== "string" || etag === "") {
        throw new Refusal("invalid",
                          "expected_etag is the file's etag, as file_stat or fetch answers it");
      }
      const path = storedPath(r["path"]);
      const entry = this.#files.stat(path);
      if (entry === null || entry.kind !== "file") {
        throw new FileFault("not_found", "no such file");
      }
      if (entry.etag !== etag) {
        return { conflict: entry };
      }
      if (entry.size > EDIT_BYTES_MAX) {
        throw new Refusal("invalid",
                          `file_edit edits a file of at most ${EDIT_BYTES_MAX} bytes, the ` +
                          "reads and time one call holds; write a larger one by upload");
      }
      return { account, path, old, replacement, content: this.#files.open(path) };
    });
    if (asked.conflict !== undefined) {
      return { conflict: asked.conflict };
    }
    const { account, path, old, replacement, content } = asked;
    const authorize = () => this.#filer(binding, "work", "write_files");
    const refused = (refusal: EditRefusal): never => {
      throw new Refusal("invalid", refusal === "ill_formed"
        ? "old_text and new_text are well-formed text"
        : refusal === "not_utf8" ? `${path} is not UTF-8 text`
        : refusal === "absent" ? "old_text is not in the file: read it again and give its " +
          "exact text"
        : `old_text occurs ${refusal.times} times: give more of the text around the one to ` +
          "change");
    };
    try {
      if (content.inline !== undefined) {
        // A small file, kept inline: edited whole, its bytes at most `INLINE_BYTES`.
        let text: string;
        try {
          text = utf8Decoder("keep").decode(content.inline);
        } catch {
          return refused("not_utf8");
        }
        const first = text.indexOf(old);
        if (first < 0) {
          return refused("absent");
        }
        let times = 0;
        for (let at = first; at >= 0; at = text.indexOf(old, at + 1)) {
          times += 1;
        }
        if (times > 1) {
          return refused({ times });
        }
        const edited = new TextEncoder().encode(
          text.slice(0, first) + replacement + text.slice(first + old.length));
        return await fileConflictedAfter(async () => ({
          entry: await this.#files.write(path, edited, {
            expected: content.entry.etag, mode: content.entry.mode, writer: fileWriter(account),
            authorize }),
        }));
      }
      // A chunked file, edited chunk by chunk (`edit.ts`, ADR-037): each chunk read in a
      // reading step, each chunk made and put in a making step, the units taken as its
      // bytes come and released as they go, never across the begin, the commit or the
      // other chunks. Its memory does not depend on its size.
      const chunks = content.chunks ?? [];
      const pool = ownerSlots();
      const make = <T>(work: () => Promise<T>) => slotted(pool, EDIT_MAKE_UNITS, work);
      const plan = await planEdit({
        size: content.entry.size, chunks,
        read: async (index) => (await this.#files.fetch([{ sha256: chunks[index]!.sha256 }]))[0]!,
      }, old, replacement, CHUNK_BYTES, (work) => slotted(pool, EDIT_READ_UNITS, work));
      if ("refused" in plan) {
        return refused(plan.refused);
      }
      return await fileConflictedAfter(async () => ({
        entry: plan.size <= INLINE_BYTES
          ? await this.#files.write(path, await inlineOf(plan, make), {
            expected: content.entry.etag, mode: content.entry.mode,
            writer: fileWriter(account), authorize })
          : await this.#files.writeChunks(path, plan.size, plan.hashes, plan.make, {
            expected: content.entry.etag, mode: content.entry.mode,
            writer: fileWriter(account), readable: chunks.map(({ sha256 }) => sha256),
            authorize, hold: make }),
      }));
    } finally {
      await this.#schedule.sync();
    }
  }

  /**
   * A new owner's starter seed, awaited before a door touches the files or lists the agents,
   * after the caller's authority is checked in a unit (`check`): an outside agent's first
   * `fetch` of `/workspace/USER.md` or `agents_list` finds the seeded profile and `main`
   * however soon after sign-up it comes, and its first write lands beside the seeded files,
   * never before them (autoconnect_research.md §2.1).
   */
  async #seededFor(check: () => void): Promise<void> {
    await this.#door(check);
    await this.#seeded();
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
      heldToRead(account, r["expected_etag"]);
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
      heldToRead(account, r["expected_etag"]);
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
      // One answer holds the page (`pages.ts`): when versions are left, `next` the last's.
      const kept = this.#files.history(r["path"], r["before"] ?? null, r["limit"]);
      return fitList(kept, kept.changes, { changes: [], next: Number.MAX_SAFE_INTEGER },
                     (changes) => ({ changes, next: changes.at(-1)!.version }));
    });
  }

  fileRestore(binding: Binding, request: RestoreRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#filing(() => {
      const account = this.#filer(binding, "work", "write_files");
      const r = fileFields(request, ["path", "version", "expected_etag"]);
      this.#ownersTree(binding, account, "FILES.restore", request, r["path"]);
      heldToRead(account, r["expected_etag"]);
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
        readable.lease, sha256Hex(readable.scope)
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
  // own MCP door never converses (`RUN_STARTS_NONE`): work between agents is mail. It may
  // launch and schedule its owner's agents, and read and cancel runs and schedules, as its
  // initiator: an owner-started or scheduled run as the owner, a client's run as that client,
  // held to that client's grant; whatever it launches runs unattended.

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
    await this.#seeded();
    const read = await this.#readAgent(asked.agent);
    const run = await this.#launch(read, () => {
      const account = this.#account(binding, "work");
      const universe = this.#runDoor(account, "converse", asked.agent);
      return { ...this.#initiated(binding, account, universe, asked.agent),
               agent: asked.agent, conversation: asked.conversation, request: asked.request,
               tiers: tierNumbers(this.#storage, binding.tiers, clock()), unattended: false,
               prompt: asked.prompt, mail: null };
    });
    return fittedRun(await this.#settledWithin(run, asked.waitMs, asked.client));
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
        throw new Refusal("invalid", "at is a time in seconds since the epoch");
      }
      if (cron !== null && typeof cron !== "string") {
        throw new Refusal("invalid", "cron is a cron expression");
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
    await this.#seeded();
    const read = await this.#readAgent(asked.agent);
    const run = await this.#launch(read, () => {
      const account = this.#account(binding, "work");
      const universe = this.#runDoor(account, "run_graph", asked.agent);
      // A run's own launch has no person at it: unattended, as a schedule's run is.
      return { ...this.#initiated(binding, account, universe, asked.agent),
               agent: asked.agent, conversation: null, request: null,
               tiers: tierNumbers(this.#storage, binding.tiers, clock()),
               unattended: account.run !== null, prompt: asked.input, mail: null };
    });
    return fittedRun(await this.#settledWithin(run, 0, asked.client));
  }

  /** A run of the caller's, from its receipt; or a schedule of the caller's. */
  agentStatus(binding: Binding, request: RunRequest): Promise<RunView | RunSchedule> {
    return this.#door(() => {
      const account = this.#account(binding, "read");
      this.#runDoor(account, null, null);
      const client = account.origin?.client ?? null;
      const r = runFields(request, [], ["run_id", "schedule_id"]);
      if (("run_id" in r) === ("schedule_id" in r)) {
        throw new Refusal("invalid", "a status names a run_id or a schedule_id");
      }
      return "run_id" in r ? fittedRun(this.#runs.receipt(r["run_id"], client))
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
        throw new Refusal("invalid", "a cancel names a run_id or a schedule_id");
      }
      if ("schedule_id" in r) {
        return { schedule: this.#runs.unschedule(r["schedule_id"], client).schedule_id };
      }
      const run = this.#runs.receipt(r["run_id"], client).run_id;
      if (this.env.SANDBOX === undefined && this.#runs.grant(run) !== null) {
        throw new Refusal("sandbox_absent",
                          SANDBOX_ABSENT);  // no CLI could be stopped: refused, loudly
      }
      return { run, client, live: this.#runs.cancel(run, clock()) };
    });
    if ("schedule" in found) {
      return { schedule_id: found.schedule, cancelled: true };
    }
    if (found.live !== null) {
      await this.#sandboxCancel(found.live);
    }
    return this.#door(() => fittedRun(this.#runs.receipt(found.run, found.client)));
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
          throw new Refusal("run_ended", RUN_ENDED);
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
          throw new Refusal("run_ended", RUN_ENDED);
        }
        const r = eventsRequest(request);
        // Another batch landed between the units (a runner's retry): the reply this settle
        // would post is not the one placed, so the batch is posted again, never dropped. A
        // reply whose writer's files refused it (no placing) settles with a notice instead.
        const reply = this.#runs.answering(binding.run as string, lease, r.frames, r.end);
        if (reply !== null && utf8Bytes(reply.result) > PLACE_BYTES
            && !placedAs(placed, reply.result)
            && !(placing !== null && placing.placing === null)) {
          throw new Refusal("events_raced", EVENTS_RACED);
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
      if (!(error instanceof Refusal) || error.transient) {
        throw error;  // a transient refusal fails the batch, posted again: the reply is kept
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

  /**
   * A mail's reply posted in this unit, or a scheduled run's result in the owner's room with
   * its agent (`dm:<agent's id>`, `dm:main`'s for main; the run's id its post's own, so it is
   * posted once), its body
   * placed or not; a refusal a notice.
   */
  #reply(answer: MailAnswer, authority: MailAuthority | null, placed: Placed | null): void {
    try {
      // The reply is the owner's agents' content: held to the tier's storage too.
      this.#files.held("owner", () => {
        if (answer.mail === null) {
          this.#ledger.post({ native: answer.agent }, `dm:${nativeId(answer.agent)}`,
                            answer.run, answer.result, null, clock(), authority, placed,
                            authority === null ? "owner" : `client:${authority.client}`);
        } else {
          this.#ledger.answer({ native: answer.agent }, answer.mail, answer.run,
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
      this.#notices.add("mail_reply_refused", `${answer.agent}: ${
        error instanceof Error ? error.message : "the ledger failed"}`, clock());
    }
  }


  // -- THE VENDOR'S OWN SIGN-IN, inside a sandbox (slice SIGNIN; `signins.ts`) -----------

  /** Whether `id` is a live sign-in's: an app socket may watch it (the owner's own). A read. */
  #signinOwned(id: unknown): boolean {
    if (typeof id !== "string" || !SIGNIN_ID.test(id)) {
      return false;
    }
    try {
      this.#signins.get(id);
      return true;
    } catch {
      return false;
    }
  }

  /** In a unit: a lease no run and no sign-in holds now given back, as a settle gives it. */
  #leaseFreed(leaseId: string, epoch: number): void {
    if (this.#runs.liveRuns().some((run) => run.lease_id === leaseId)
        || this.#signins.holds(leaseId)) {
      return;
    }
    if (this.#files.leaseRelease(leaseId, epoch).released) {
      this.#egress.end("lease", (live) => live.lease === leaseId);
      queueMicrotask(() => void this.#sandboxRelease(leaseId));
    }
  }

  /**
   * Begin the vendor's own sign-in to `engine` (`mode: "in"`), or its sign-out (`"out"`),
   * inside the owner's own sandbox or one client's (`authority`, that client's id: a login is
   * never copied between sandboxes). The owner's own door, on its own account: the object
   * opens that sandbox's lease with no run (or joins the live one), and the sandbox Worker
   * starts the record's `cli.login` there (`SandboxService.signin`). The answer is the
   * sign-in's id, which the owner's app watches on its socket (`ta.signin` frames: the
   * vendor's URL to open in the owner's own browser, a code to type there, whether it takes
   * a pasted code), or the code the Worker answered (`sandbox_waiting`, ...).
   */
  async signinStart(binding: Binding, request: SigninStartRequest):
      Promise<SigninView | { started: false; code: string }> {
    const begun = await this.#door(() => {
      this.#owner(binding, "work", "sign in inside a sandbox");
      if (this.env.SANDBOX === undefined) {
        throw new Refusal("sandbox_absent", SANDBOX_ABSENT);
      }
      const r = runFields(request, ["engine"], ["authority", "region", "mode"]);
      const rec = typeof r["engine"] === "string" ? bundledSources().get(r["engine"]) : undefined;
      const cli = rec?.cli;
      if (rec === undefined || cli === undefined || rec.unwired !== undefined
          || cli.login === undefined || !cli.credential.includes("vendor_login_in_sandbox")) {
        throw new Refusal("invalid",
                          "a sign-in names an engine that signs in with the vendor's own login");
      }
      const mode = (r["mode"] ?? "in") as SigninMode;
      if (mode !== "in" && mode !== "out") {
        throw new Refusal("invalid", "a sign-in's mode is in or out");
      }
      // A sign-in sits behind its source's switch; a sign-out never does, so a login is
      // always removable (review H1).
      if (mode === "in" && !switchedOn(this.#storage, binding.signins, clock()).has(rec.id)) {
        throw new Refusal("signin_off", SIGNIN_OFF);
      }
      const regions = cli.regions === undefined ? [] : Object.keys(cli.regions);
      const region = r["region"] ?? null;
      if (mode === "in" ? (regions.length === 0 ? region !== null
                                                : !regions.includes(region as string))
          : region !== null) {
        throw new Refusal("invalid", regions.length === 0 ? "this sign-in takes no region"
          : `this sign-in takes the owner's choice of region: ${regions.join(", ")}`);
      }
      const authority = signinAuthority(r["authority"]);
      if (authority !== OWNER_AUTHORITY && !this.#grants.live(authority)) {
        throw new Refusal("refused",
                          "no such client: a client's own sandbox is signed in for a client " +
                          "the owner has connected");
      }
      const now = clock();
      this.#signins.lost(now, new Set(this.#files.liveLeases(now).map((each) => each.lease_id)));
      const live = this.#files.sandboxLease(now, authority);
      const opened = live === null ? this.#openLease(this.#ownerName, now, authority) : null;
      const lease = live ?? opened!.lease;
      const row = this.#signins.begin(authority, rec.id, mode, region as string | null,
                                      { lease_id: lease.lease_id, epoch: lease.epoch }, now);
      return { row, opened, region: region as string | null,
               lease: { lease_id: lease.lease_id, epoch: lease.epoch } };
    });
    let answer: Started | null = null;
    try {
      answer = started(await this.env.SANDBOX!.signin(this.#ownerName(), {
        id: begun.row.id, engine: begun.row.engine, region: begun.region, mode: begun.row.mode,
        authority: begun.row.authority, lease: begun.lease,
        tokens: begun.opened?.tokens ?? null, network: begun.opened?.network ?? null,
        route: sealRoute(this.env.ROUTING_MAC_KEY, { owner: this.#ownerName(),
                                                     authority: begun.row.authority }),
        urls: { events: `${EGRESS_ORIGIN}/signin/${begun.row.id}/events`,
                signin: runUrls(`run_${"0".repeat(32)}`, "0".repeat(64)).signin } }));
    } catch {
      console.error("a sign-in's start had no answer");
      answer = null;
    }
    if (answer?.started === true) {
      return signinView(begun.row);
    }
    await this.#door(() => {
      this.#signins.end(begun.row.id);
      this.#leaseFreed(begun.lease.lease_id, begun.lease.epoch);
    });
    return { started: false, code: answer === null ? "start_lost" : answer.code };
  }

  /**
   * The runner's frames of a sign-in (`ta.signin`: its state, the vendor's URL and code),
   * on the egress host by the egress token and the runner's secret of the live lease that
   * holds it: the latest prompt kept, the lease renewed (its beat), each frame relayed to the
   * owner's app sockets on the sign-in's id; its end ends it, and the lease goes once nothing
   * holds it.
   */
  async signinEvents(binding: Binding, request: SigninEventsRequest):
      Promise<{ accepted: number; ended: boolean }> {
    const { row, ended, stopped, events, frames } = await this.#door(() => {
      this.#bind(binding, "reduce", "egress");
      const lease = this.#runner(binding);
      if (lease === null) {
        throw new Refusal("run_ended", RUN_ENDED);
      }
      const r = eventsRequest({ frames: request.frames, ...(request.end === undefined ? {}
        : { end: request.end }) });
      const now = clock();
      const done = this.#signins.frames(request.id, lease, r.frames.map((frame) => frame.event),
                                        r.end !== null, now);
      this.#files.leaseRenew(lease.lease_id, lease.epoch);
      if (done.ended) {
        this.#leaseFreed(lease.lease_id, lease.epoch);
      }
      return { ...done, frames: r.frames };
    });
    // Only what the trusted side kept: each frame's own, checked event.
    const last = frames.at(-1);
    this.#relay.relay(row.id, events.map((event, i) => ({
      v: FRAME_VERSION, run: row.id, seq: (last?.seq ?? 0) - events.length + i + 1,
      at: last?.at ?? clock(), kind: "event" as const, event })));
    if (ended) {
      this.#relay.settled(row.id);  // its tail goes with it
    }
    if (stopped) {
      // Its CLI stopped: what it showed is not the vendor's page, or it is past its deadline.
      await this.env.SANDBOX?.signinCancel(this.#ownerName(), { authority: row.authority,
                                                                id: row.id })
        .catch(() => console.error("a refused sign-in's stop did not reach the sandbox"));
    }
    return { accepted: frames.length, ended };
  }

  /**
   * The code the vendor's page showed the owner, pasted back: to the sign-in's CLI's stdin
   * (`SandboxService.signinInput`). The owner's own door.
   */
  async signinCode(binding: Binding, request: SigninCodeRequest):
      Promise<{ delivered: boolean }> {
    const { row, code } = await this.#door(() => {
      this.#owner(binding, "work", "sign in inside a sandbox");
      const r = runFields(request, ["id", "code"]);
      const code = pastedCode(r["code"]);
      const row = this.#signins.wantsCode(r["id"], clock());
      if (!switchedOn(this.#storage, binding.signins, clock()).has(row.engine)) {
        throw new Refusal("signin_off", SIGNIN_OFF);
      }
      return { row, code };
    });
    const sandbox = this.env.SANDBOX;
    if (sandbox === undefined) {
      throw new Refusal("sandbox_absent", SANDBOX_ABSENT);
    }
    return sandbox.signinInput(this.#ownerName(), { authority: row.authority, id: row.id,
                                                    code });
  }

  /** A sign-in stopped: its CLI's run ends `cancelled`. The owner's own door. */
  async signinCancel(binding: Binding, request: SigninRequestById):
      Promise<{ cancelled: boolean }> {
    const row = await this.#door(() => {
      this.#owner(binding, "reduce", "stop a sign-in");
      return this.#signins.get(runFields(request, ["id"])["id"]);
    });
    const sandbox = this.env.SANDBOX;
    if (sandbox === undefined) {
      throw new Refusal("sandbox_absent", SANDBOX_ABSENT);
    }
    return sandbox.signinCancel(this.#ownerName(), { authority: row.authority, id: row.id });
  }

  /**
   * The sign-ins in progress, and each engine's presence in the owner's own sandbox as its
   * runner last reported it (never a credential). The owner's own door.
   */
  signinList(binding: Binding): Promise<{ signins: SigninView[];
                                           engines: ReturnType<Runs["signins"]> }> {
    return this.#door(() => {
      this.#owner(binding, "read", "see its sign-ins");
      const now = clock();
      this.#signins.lost(now, new Set(this.#files.liveLeases(now).map((each) => each.lease_id)));
      return { signins: this.#signins.list().map(signinView), engines: this.#runs.signins() };
    });
  }

  /**
   * The caller's past runs, newest first, a page at a time (`before`, the last page's
   * `next`): the owner's every run, an outside client's its own alone, as their receipts show
   * them (the result is the run's own door's, `agentStatus`).
   */
  runList(binding: Binding, request: RunListRequest):
      Promise<{ runs: RunListed[]; next: number | null }> {
    return this.#door(() => {
      const account = this.#account(binding, "read");
      this.#runDoor(account, null, null);
      const r = runFields(request, [], ["before", "limit"]);
      return this.#runs.list(account.origin?.client ?? null, r["before"], r["limit"]);
    });
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
        throw new Refusal("refused", "egress_lease_closed");
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
          throw new Refusal("invalid",
                            `a sandbox's signed-in engines are at most ${SIGNIN_LIST_CHARS} ` +
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
   * In a unit: a run door's caller held: a run's own call never converses, and reads or
   * cancels only holding `run_graph`; for a door that starts work, `need` held where it acts (a scoped client's agent; a run's grant, and
   * a client's run's stamped grant as well, for the agent it launches); its universe.
   */
  #runDoor(account: Account, need: "converse" | "run_graph" | null, agent: string | null):
      string | null {
    if (account.run !== null && need === "converse") {
      throw new Refusal("run_starts_none", RUN_STARTS_NONE);
    }
    if (account.run !== null && need === null) {
      // A run reads and cancels runs and schedules only as its agent may make them: an
      // agent the owner narrowed is held to it (review H1).
      this.#scope(account, "run_graph");
    }
    if (need === null || agent === null) {
      return null;
    }
    if (account.run === null) {
      const universe = this.#universe(account);
      this.#scope(account, need, universe, agent);
      return universe;
    }
    this.#scope(account, need);
    const universe = account.run.stamp?.universe ?? null;
    if (account.grant?.mode === "scoped"
        && (universe === null || !holds(account.grant, { universe, agent, capability: need }))) {
      throw new Refusal("refused", "outside client capability not granted");
    }
    return universe;
  }

  /**
   * Who starts a run: the owner, or a client, with its stamp and origin. A run's own launch
   * is its initiator's: the owner's for an owner-started or scheduled run, else the stamp
   * its client's run holds, for the agent it launches.
   */
  #initiated(binding: Binding, account: Account, universe: string | null, agent: string):
      { initiator: "owner" | "client"; stamp: Stamped | null; origin: Origin | null } {
    if (account.run !== null) {
      const stamp = account.run.stamp;
      return stamp === null || account.origin === null
        ? { initiator: "owner", stamp: null, origin: null }
        : { initiator: "client", origin: account.origin, stamp: { ...stamp, universe, agent } };
    }
    if (account.origin === null || account.grant === null) {
      return { initiator: "owner", stamp: null, origin: null };
    }
    return { initiator: "client", origin: account.origin,
             stamp: { client: account.origin.client, generation: account.grant.generation,
                      authenticated_at: binding.authenticated_at ?? null, universe, agent } };
  }

  /**
   * A new owner's starter seed, awaited before a door reads an agent: its first `converse`
   * finds `main` however soon it comes (review L3), the lane's pass joined if it runs.
   */
  async #seeded(): Promise<void> {
    if (this.#starter.version() === 0) {
      await this.#starter.seeded(clock());
      await this.#schedule.sync();
    }
  }

  /** An agent's `agent.json`, read whole, and the etag it was read at. */
  async #readAgent(agent: string): Promise<{ file: AgentFile; etag: string }> {
    const path = agentFilePath(agent);
    const content = await this.#door(() => {
      const entry = this.#files.stat(path);
      if (entry === null || entry.kind !== "file") {
        throw new Refusal("agent_undefined", AGENT_UNDEFINED);
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
  async #launch(read: { file: AgentFile; etag: string }, ask: () => Omit<Ask, "file">,
                waiting: string | null = null, taken: () => void = () => {}): Promise<string> {
    const { admitted, agent, unattended } = await this.#door(() => {
      const made = ask();
      if (this.#files.stat(agentFilePath(made.agent))?.etag !== read.etag) {
        throw new Refusal("agent_changed", AGENT_CHANGED);
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
        route: sealRoute(this.env.ROUTING_MAC_KEY, { owner: this.#ownerName(),
                                                     authority: admitted.authority }),
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
   * The switched-on sign-ins, read by the lane itself from the control record the object
   * binds (`CONTROL`), and kept (review N1): so a source switched off stops powering the
   * lane's runs (a schedule's, a mail's, a waiting one's) at its next pass, though the owner
   * makes no door call (`ControlGate.signins`, fail-closed).
   */
  async #laneSignins(): Promise<void> {
    const on = await this.#control().signins();
    await this.#door(() => switchedOn(this.#storage, on, clock()));
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
    await this.#laneSignins();
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
        if (!(error instanceof Refusal) || error.transient) {
          throw error;  // a transient refusal fails the pass, and the run waits on
        }
        if (error.code === "conversation_busy" || error.code === "tier_runs") {
          break;  // not its turn after all: it keeps waiting, and the lane comes back
        }
        const reason = error.message;
        await this.#door(() => {
          if (this.#runs.refuseWaiting(waiting.run, error, clock())) {
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
      }, next, (found) => found?.content_id != null, (located) => this.#bodies.fetch(located));
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
      const row = await this.#door(() => workRefusal(admission(this.#storage)) !== null
        ? null : this.#runs.due(clock()));
      if (row === null) {
        return;
      }
      let gone = false;
      try {
        const read = await this.#readAgent(row.agent);
        const run = await this.#launch(read, () => {
          if (!this.#runs.stands(row)) {
            gone = true;
            throw new Refusal("no_schedule", NO_SCHEDULE);
          }
          return {
            initiator: "schedule", ...scheduleAuthority(row), agent: row.agent,
            conversation: null, request: null,
            tiers: tierNumbers(this.#storage, undefined, clock()), unattended: true,
            prompt: row.input, mail: null };
        }, null, () => this.#runs.claim(row, clock()));
        await this.#door(() => this.#runs.ran(row.id, run, null));
      } catch (error) {
        if (!(error instanceof Refusal) || error.transient) {
          throw error;  // a transient refusal fails the pass: unclaimed, the schedule stays due
        }
        if (gone) {
          continue;  // cancelled, or claimed, since it was read: nothing of it fires now
        }
        const reason = error.message;
        await this.#door(() => {
          if (this.#runs.claim(row, clock())) {
            this.#runs.ran(row.id, null, reason);
            this.#notices.add("run_refused", `${row.agent}: ${reason}`, clock());
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
    const mail = await this.#rereading(() => {
      const found = this.#ledger.mailFor(messageId, nativeId(agent));
      return found?.content_id == null ? [] : [found.content_id];
    }, (bodies: ReadonlyMap<string, string>) =>
      this.#ledger.mailFor(messageId, nativeId(agent), bodies),
    (found) => found?.content_id != null, (located) => this.#bodies.fetch(located));
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
      const read = await this.#readAgent(mail.agent);
      const conversation = mailConversation(mail.agent, mail.thread, origin?.client ?? null);
      await this.#launch(read, () => ({
        initiator: "mail", stamp, origin, agent: mail.agent,
        conversation: this.#runs.kept(conversation, mail.agent, origin?.client ?? null)
          ? conversation : null,
        request: sha256Hex(`mail\0${mail.message_id}\0${mail.agent_id}`),
        tiers: tierNumbers(this.#storage, undefined, clock()), unattended: !mail.attended,
        prompt, mail: { message: mail.message_id, conversation } }), null,
                         () => this.#ledger.taken(mail.message_id, mail.agent_id, clock()));
    } catch (error) {
      if (!(error instanceof Refusal) || error.transient) {
        throw error;  // a transient refusal fails the pass, and the mail is delivered again
      }
      if (error.code === "agent_changed") {
        return;  // read again on the next turn of the pass
      }
      const reason = error.message;
      await this.#door(() => {
        if (error.code === "run_inputs_full") {
          this.#ledger.hold(mail.message_id, mail.agent_id, clock());
          return;
        }
        // Its fixed code, as a protocol shows one; a quiet one's own words.
        this.#ledger.undeliverable(mail.message_id, mail.agent_id,
                                   error.named ? error.code : reason, clock());
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
      if (!this.#runs.owns(conversation, null) && !this.#signinOwned(conversation)) {
        throw new Refusal("ticket_refused", TICKET_REFUSED);
      }
      const grant = binding.grant;
      const tokenExpires = binding.expires_at;
      if (typeof grant !== "string" || typeof tokenExpires !== "number") {
        throw new Refusal("invalid", "a socket ticket is bound to its token's grant and expiry");
      }
      const now = clock();
      const expires = Tickets.expiry(now, tokenExpires);
      if (expires <= now) {
        throw new Refusal("ticket_refused", TICKET_REFUSED);
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
        throw new Refusal("invalid", "a grant id");
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
      throw new Refusal("refused", OWNERS_SOCKET);
    }
    if ((binding as Binding).session !== undefined) {
      throw new Refusal("invalid", "an app socket carries no session");
    }
    const ticket = this.#tickets.consume(hash, conversation, clock());
    if (!this.#runs.owns(conversation, null) && !this.#signinOwned(conversation)) {
      throw new Refusal("ticket_refused", TICKET_REFUSED);
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
      if (!(error instanceof Refusal)) {
        throw error;
      }
      return error.code === "owner_erased" || error.code === "erasing" ? "erased"
        : error.code === "owner_suspended" ? "suspended" : "signedOut";
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
        throw new Refusal("refused", PLATFORM_TIER_ONLY);
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
      throw new Refusal("owner_erased", OWNER_ERASED);
    }
    if (erasing(this.#storage)) {
      throw new Refusal("erasing", ERASING);
    }
    const refusal = doorRefusal(admission(this.#storage), door);
    if (refusal !== null) {
      throw refusal;
    }
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
    const keyed = sandboxEnv(this.#connections.egressible(),
                             () => randomHex(16),
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
    const runner = randomHex(32);
    const lease = this.#files.leaseAcquire(authority, "sandbox", {
      egress: egress.hash, sync: sync.hash, runner: sha256Hex(runner) });
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
          throw new Refusal("egress_policy_stale",
                            "egress_policy_stale: the network was made under another egress " +
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
   * An admitted model call's reserve (`Egress.reserve`), which the gateway asks for through
   * the egress door once its one parse of the body read the model and tier: its metering, or
   * its refusal's fixed code and receipt. Held to the egress binding that opened the call.
   */
  egressReserve(binding: Binding, request: unknown): Promise<EgressReserved | EgressRefusal> {
    return this.#door(() => {
      this.#bind(binding, "work", "egress");
      return this.#egress.reserve(sandboxBinding(binding.sandbox), reserveRequest(request),
                                  clock());
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
          if (!(error instanceof Refusal && error.invalid)) {
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
      this.#heldBack = heldBackOf(r["ops"]);
      try {
        return this.#files.syncCommit({ lease_id: r["lease_id"], epoch: r["epoch"],
                                        ops: r["ops"] },
                                      (authority) => this.#grants.full(authority));
      } finally {
        this.#heldBack = null;
      }
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
      return this.#files.syncBegin(r["lease_id"], r["epoch"], r["size"], r["chunks"]);
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
        throw new Refusal("sync_fenced", SYNC_FENCED);
      }
      return fields;
    });
    return this.#files.upload(r["intent_id"], r["chunks"]);
  }

  /**
   * The sync agent's own upload given up (its file changed mid-sync, or was refused), let go
   * now rather than holding the owner's storage until it expires; fenced by the live lease,
   * and only the lease's own upload. One already gone is no error.
   */
  fileSyncUploadAbort(binding: Binding, request: SyncUploadAbortRequest):
      Promise<{ aborted: boolean }> {
    return this.#filing(() => {
      this.#syncer(binding, "work", request);
      const r = fileFields(request, ["lease_id", "epoch", "intent_id"]);
      const writer = this.#files.syncWriter(r["lease_id"], r["epoch"]);
      let owner: string | null;
      try {
        owner = this.#files.intentWriter(r["intent_id"]);
      } catch (error) {
        if (!(error instanceof Refusal && !error.invalid)) {
          throw error;  // a malformed id: invalid
        }
        owner = null;  // gone already: expired, committed or let go
      }
      if (owner !== null && owner !== writer) {
        throw new Refusal("sync_fenced", SYNC_FENCED);
      }
      return owner === null ? { aborted: false } : this.#files.abort(r["intent_id"]);
    });
  }
}
