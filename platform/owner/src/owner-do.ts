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
 * Its code cannot reach another owner's object: no service the Worker binds reaches an
 * object (`wrangler.jsonc`, held by the config guard; code mode's VMs run in `CODEMODE_VM`,
 * never here); it binds no Durable Object namespace; the runtime hands it no
 * `ctx.exports` (`disable_ctx_exports`); and its source never names `exports` or imports
 * `env` (the config and self-reach guards). The owner's file bytes reach the bucket only
 * through `FILES`, under the owner's namespace, which only this object holds
 * (`files.ts`). The module keeps no mutable state at top level (the module-state
 * guard), since objects of one class can share an isolate.
 *
 * Every public method is an RPC any binding can call, so the class has no public method
 * but its doors (the door table, `contract/doors.json`, lists them). The object is their
 * router and holds their state: each door's method hands its row's runner (`#run`, `#units`:
 * the bind, the account, the scope and the fields) the handler in the module its row names
 * (`<module>Doors.<door>`), which the runner hands the object's state (`OwnerState`). Its
 * runs are launched and delivered by `launch.ts`. Its timed work is the one
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
 * written, then started through the `SANDBOX` service (`sandbox.ts`). A run's engine
 * calls back on the egress host: its MCP door, every door held to its run grant's scopes
 * (a run binding, `#runAccount`), and its event door (`runEvents`), whose frames the object
 * reads by the engine's record and relays to the owner's app sockets subscribed to that
 * conversation.
 *
 * A main agent spends few calls (slice M): `inboxDigest` and `boardDigest` answer what
 * changed for it since its cursor in one read, held to its grant; `codeRun` is code mode at
 * the MCP door, one script calling the caller's other tools (`codemode.ts`), refused unless
 * the owner's policy has `codemode.on` and the caller's profile holds `code`; and
 * `toolProfile` answers the doors which tools a caller is served (`contract/profiles.json`:
 * its grant's profile, or a run's).
 *
 * Erasing the owner (`erasure.ts`, `erase.ts`; the door `ownerErase`): once it marks the
 * owner, every door refuses
 * (`ERASING`, in the binding check every door's first unit runs) and no lane runs; it ends
 * every run, then wipes the owner whole, leaving only the erased admission row
 * (`admission.ts`). An object that finds that tombstone as it
 * starts migrates nothing and starts no Lifecycle, and every door refuses it
 * (`OWNER_ERASED`), so a stale `getByName` never makes owner state again.
 *
 * The owner's admission (`admission.ts`) is checked in the same binding check: a suspended
 * owner's every door refuses, and so do the lanes with `require_work` semantics (the
 * `wakes` lane's claim). Only the platform, as the reserved `PLATFORM_CALLER`, sets it, at
 * `admissionSet`, the one door that caller reaches but its tier, erasure and key doors, and,
 * with an import binding while the owner is `pending`, the cutover's import doors (`import.ts`).
 */
import { HEX64 } from "../../contract/src/ids";
import { rehearsalRun } from "../../contract/src/import-message";
import { principal } from "../../contract/src/text";

import { Lifecycle } from "agents/lifecycle";
import { DurableObject } from "cloudflare:workers";

import { doorRow, type DoorRow, fields, type ProfileName } from "../../contract/src/doors";
import type { RunCapability } from "../../contract/src/capabilities";

import {
  admission, type Admission, admissionDoors, DISCARDED, type DoorClass, doorRefusal,
  IMPORT_DOORS_ONLY, IMPORT_PENDING_ONLY, IMPORT_PLATFORM_ONLY, isAdmitted, OWNER_ERASED,
  ownerTier, PLATFORM_ONLY, PLATFORM_RESERVED, setTier, tombstoned, workRefusal, writeTombstone,
} from "./admission";
import {
  agentId, AUTHORITY_CHANGED, MAIN_AGENT, may, NOT_GRANTED, type Stamped,
} from "./authority";
import { type CloseKind, OWNERS_SOCKET, Relay, relayDoors, type SocketState } from "./relay";
import { RUN_ENDED, Runs, type RunSchedule, runsDoors } from "./runs";
import { PostGate } from "../../contract/src/run-events";
import { TICKET_REFUSED } from "../../contract/src/socket-shape";
import { Tickets } from "./tickets";
import { type ApiKey, Keys, keysDoors } from "./keys";
import { bindingId, Inbound, type InboundBinding, inboundDoors, type InboundRow } from "./inbound";
import { keepTiers, keptTiers, PLATFORM_TIER_ONLY, tierDoors } from "./tier";
import type { TierName } from "../../contract/src/control";
import { Bodies, type Located, type Placed, type Placing, type Window } from "./bodies";
import { type Heads, PAGE_BYTES, type Shown, type Wants } from "./pages";
import { Collections, collectionsDoors, type History, type Page, type Row } from "./collections";
import {
  Connections, connectionsDoors, type ConnectionView, type Prepared,
} from "./connections";
import type { RingAnswer } from "../../contract/src/gateway";
import { ERASING, Refusal } from "../../contract/src/refusal";
import {
 type Change as FileChange, type ChunkRef, type Entry, fileChanged, Files,
 filesDoors, OWNER_AUTHORITY, type SandboxLease, SYNC_FENCED, type SyncOutcome, type Usage,
} from "./files";
import { type OwnerStorage, WriteGate } from "./gate";
import {
  type Client, type ConnectedApp, type Grant, Grants, grantsDoors, type Scope,
} from "./grants";
import {
  codemodeDoors, type CodeRequest, type CodeResult,
} from "./codemode";
import {
  type Agent, type ChainMail, Ledger, ledgerDoors, type MailPage,
  type NativeAgent, outsideOrigin, type Posted, READ_TRIES, registrant, RUN_NO_MAILBOX,
} from "./ledger";
import { clock } from "./clock";
import type { OwnerEnv } from "./env";
import {
  type Binding, type InboundAnswer, type Origin, PLATFORM_CALLER,
} from "../../contract/src/binding";
import { type Notice, Notices, noticesDoors } from "./notices";
import {
  type ImportCheckAnswer, type ImportCheckRequest, type Imported, importDoors,
  type ImportFileBeginRequest, type ImportFileCommitRequest, type ImportFilesRequest,
  type ImportFileStoredRequest, type ImportNoticeRequest, type ImportRowsRequest,
  type ImportUpload,
} from "./import";
import { Erasure } from "./erase";
import {
 ERASE_RETRY_MS, erasing, erasureDoors, NO_ERASURE, outsideDone, PUT_GRACE_MS, wiped,
} from "./erasure";
import {
 type Change, fileLimits, modelSettings, Policy, policyDoors, type Projection, type Receipt,
} from "./policy";
import { type Card, Registrations, registrationsDoors } from "./registrations";
import { Schedule } from "./schedule";
import { ABILITY_TREES, AGENTS_DIR, within } from "../../contract/src/paths";
import { Launcher } from "./launch";
import {
 Egress, egressDoors, type EgressReceipt, sandboxBinding,
} from "./egress";
import { exportDoors } from "./export";
import { SandboxTime, sandboxTimeDoors } from "./sandbox-time";
import type {
  AdmissionRequest, BeatRequest, BilledEvent, BindingRequest, ConnectionCommitRequest,
  ConnectionPrepareRequest, ConnectionRequest, EgressOpened, EgressRefusal, EgressReserved,
  FlowFinishRequest, InboundCreateRequest, InboundSetRequest, OwnerDoors, SigninEventsRequest,
  TierRequest,
} from "../../contract/src/owner";
import { Sessions, sessionsDoors } from "./sessions";
import { signinOwned, Signins, signinsDoors, type SigninView, switchedOn } from "./signins";
import { type Receipt as StarterReceipt, Starter, starterDoors } from "./starter";
import { carried, type StarterPackage } from "./starter-package";
import { priced, Usage as ModelUsage, usageDoors, type UsageReceipt } from "./usage";
import { migrate } from "../../contract/src/migrate";
import { Archive, type ArchiveMoveSeen, type ChainStep } from "./archive";
import { MIGRATIONS } from "./schema";
import { Backups, MANIFEST_PROTECT_S } from "./backup";
import { OpenExports } from "./open-exports";
import { ChangeLog } from "./changelog";
import { CONTROL_FRESH_MS, ControlGate } from "./control-gate";
import { storageFull, storageFullRefusal } from "./storage-full";
import { Doorbell, type WakeSender } from "./wakes";
import { type Events } from "../../contract/src/wake-records";
import type {
  BoardDigestRequest, ClearRequest, Conflicted, DeleteRequest, GetRequest, HistoryRequest,
  PullRequest, WriteRequest, WrittenRow,
} from "./collections";
import type {
  AckRequest, AgentDoorbellRequest, AgentRequest, AgentsRequest, CardApply, ChainRequest,
  DisableRequest, InboxDigestRequest, PostRequest, ReadRequest, SubscribeRequest,
  UnsubscribeRequest, WakeLimitsRequest,
} from "./ledger";
import type { InboundEvent } from "./inbound";
import type { CardRequest, PolicyWriteRequest, ProposeRequest } from "./policy";
import type {
  ChangesRequest, FetchMetadata, FileAppendRequest, FileChunksRequest, FileConflicted,
  FileEditRequest, FileFetchRequest, FileHistoryRequest, FileListRequest, FileReadRequest,
  FileRequest, FileSearchRequest, FileTextRequest, FileWriteRequest, IntentRequest, MkdirRequest,
  RemoveRequest, RenameRequest, RestoreRequest, SearchResult, SyncCommitRequest,
  SyncUploadAbortRequest, SyncUploadBeginRequest, SyncUploadChunksRequest, UploadBeginRequest,
  UploadChunksRequest, UploadCommitRequest, UploadStoredRequest, VersionRequest,
} from "./files";
import type { ClientRequest, ForgetRequest } from "./grants";
import type { ConversationRequest } from "./relay";
import type { EraseRequest } from "./erasure";
import type {
  EventsRequest, LaunchRequest, RunListed, RunListRequest, RunRequest, RunView, SubmitRequest,
} from "./runs";
import type { KeyMintRequest } from "./keys";
import type { RegistrationCardRequest } from "./registrations";
import type {
  SigninCodeRequest, SigninRequest, SigninRequestById, SigninStartRequest,
} from "./signins";

/** An inbound event's binding at any door but `inboundAccept`, and the reverse. */
const INBOUND_ONLY = "an inbound event's route reaches the inbound door alone";
const INBOUND_DOOR = "the inbound door takes an inbound event's route alone";

export interface RegisterRequest {
  readonly command_center_id: string;
  readonly display_name: string;
  readonly delivery_method: string;
  readonly recipe_id: string | null;
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

/** What a call is bound to: its owner and caller, its outside client and that client's grant. */
export interface Account {
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

/**
 * A call as its door's row bound it (`contract/doors.json`): its account, and what a sandbox,
 * runner or inbound door's binding names (the lease's readable set, the lease, the route's
 * binding), or that the platform's erasure door found the owner already erased.
 */
export interface Bound {
  readonly account: Account;
  readonly readable: Readable | null;
  readonly lease: SandboxLease | null;
  readonly route: InboundRow | null;
  readonly erased: boolean;
}

/** A bound call with no account of its own (a sandbox's, the platform's, or none). */
/**
 * The import's binding exactly as the binder makes it (`bindImport`): the platform's reserved
 * caller, no outside client, the bundle's digest and the control record's tiers; else refused.
 */
function importBinding(binding: Binding): void {
  if (typeof binding !== "object" || binding === null || binding.caller !== PLATFORM_CALLER
      || binding.origin !== null
      || Object.keys(binding).sort().join() !== "caller,import,origin,owner,tiers"
      || typeof binding.import !== "object" || binding.import === null
      || Object.keys(binding.import).join() !== "bundle"
      || typeof binding.import.bundle !== "string" || !HEX64.test(binding.import.bundle)) {
    throw new Refusal("refused", IMPORT_PLATFORM_ONLY);
  }
}

function bound(owner: string, caller: string): Bound {
  return { account: { owner, caller, origin: null, grant: null, run: null }, readable: null,
           lease: null, route: null, erased: false };
}

/**
 * A door's call once its row's every step held: bound, its request's fields (`r`), a session
 * door's live session, a run door's place (its universe and agent), and, for a door whose
 * handler names the place its capability is held at, that check (`held`).
 */
export interface Call<R = unknown> extends Bound {
  /** The binding the call came with, as the binder handed it. */
  readonly binding: Binding;
  /** The request as the binder handed it (`r` is its fields, as the row held them). */
  readonly request: R;
  readonly r: Record<string, unknown>;
  readonly session: string;
  readonly universe: string | null;
  readonly agent: string;
  held(place: unknown): void;
}

/** A door whose handler makes its own units: its call, checked again in each. */
export interface Door<R = unknown> {
  /** The binding the call came with, and its request, as the binder handed them. */
  readonly binding: Binding;
  readonly request: R;
  /** Every step of the row, in the unit it is called in. */
  check(): Call<R>;
  /** `act` on the checked call; a session door's refusal receipted. */
  within<T>(act: (call: Call<R>) => T): T;
  /** The bind and account steps alone. */
  bound(): Account;
  /** Every step but the fields: the caller's authority, whatever it asks. */
  authorized(): void;
}

/** What a run door needs where its agent acts: to start work, or (null) to read or cancel it. */
type RunNeed = "converse" | "run_graph" | null;

const OUTSIDE_SESSION = "an outside client's call never carries the owner's session";
const MCP_REGISTERS_BY_CARD = "an MCP client's agent registers through the card it files";
/** A sandbox token's binding at any door but its own, and any other binding at those. */
export const SANDBOX_DOORS = "a sandbox's token reaches its own doors only";
export const SANDBOX_ONLY = "a sandbox door takes its own sandbox token only";
/** The refusal of a sandbox binding for an owner whose object never opened a lease. */
export const NO_SANDBOX = "no sandbox lease was opened for this owner";
/**
 * What a sync door's caller may read: its lease, the readable set's own name (null for the
 * owner's sandbox, `*` for all of the owner's files), and the test of a path.
 */
export interface Readable {
  readonly lease: string;
  readonly scope: string | null;
  readonly test: (path: string) => boolean;
}

/** How many times a placed body begins again past a chunk the sweep is deleting. */
const PLACE_TRIES = 5;

/**
 * A run's own MCP door never converses: work between agents is mail (ADR-034 decision 11).
 * It may launch, schedule, read and cancel runs (the founder, 2026-11-03: every agent of an
 * owner may do what any other of the owner's agents may), held to its grant.
 */
export const RUN_STARTS_NONE = "run_starts_none: a run talks to an agent by mail, never " +
  "converse";

/** What a test reaches past the doors: the erasure's internal path and its retry interval. */
export interface Internals {
  readonly erasure?: (internal: Erasure) => void;
  readonly eraseRetryMs?: number;
  /** The erasure's wait for a put begun before its mark (`PUT_GRACE_MS`), for the tests. */
  readonly putGraceMs?: number;
  /** How long a script's read of the control record serves its calls (`control-gate.ts`). */
  readonly controlFreshMs?: number;
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

/**
 * The object's state, as its modules are handed it: its doors' handlers, each in the module
 * its row names (`contract/doors.json`), and the run launcher (`launch.ts`). Its stores; its
 * gate's units; and what only the object reads or decides: the owner's admission and tier
 * (`admission.ts`, which only the object imports), and the scope a caller is held to where
 * it acts, as the runner holds it. Only the object makes it (`#stateOf`), so a module
 * reaches these through the object's runner.
 */
export interface OwnerState {
  readonly env: OwnerEnv;
  readonly storage: OwnerStorage;
  /** The owner this object is named by; refused when it has none. */
  owner(): string;
  readonly collections: Collections;
  readonly policy: Policy;
  readonly grants: Grants;
  readonly sessions: Sessions;
  readonly ledger: Ledger;
  readonly files: Files;
  readonly backups: Backups;
  readonly openExports: OpenExports;
  readonly sandboxTime: SandboxTime;
  readonly bodies: Bodies;
  readonly registrations: Registrations;
  readonly notices: Notices;
  readonly starter: Starter;
  readonly connections: Connections;
  readonly usage: ModelUsage;
  readonly egress: Egress;
  readonly runs: Runs;
  readonly launcher: Launcher;
  readonly relay: Relay;
  readonly signins: Signins;
  readonly tickets: Tickets;
  readonly keys: Keys;
  readonly inbound: Inbound;
  readonly archive: Archive;
  readonly erasure: Erasure;
  /** The runners' events posts held at once (`EVENTS_IN_FLIGHT`). */
  readonly posts: PostGate;
  /** The held-back count a sync commit in progress carries for the sync-ignore list. */
  heldBack: { kept: number; changed: number } | null;
  /** A synchronous body as one unit of the write gate. */
  door<T>(act: () => T): Promise<T>;
  /** A unit that marks nothing changed (`gate.ts`). */
  quiet<T>(act: () => T): Promise<T>;
  /** A unit, then the one schedule synced from the rows it changed. */
  synced<T>(act: () => T): Promise<T>;
  /** A changing file door's unit, then the schedule synced whatever came of it. */
  filing<T>(act: () => T): Promise<T>;
  /** A write door with a body: placed in R2 past `PLACE_BYTES` (`#placing`). */
  placing<T>(prepare: () => { body: unknown; writer: string } | { done: T },
             act: (placed: Placed | null) => T,
             options?: { synced?: boolean; shown?: Shown<T> }): Promise<T>;
  /** A read whose unit marks what it returns, its placed bodies read between (`#rereading`). */
  rereading<T, B>(locate: () => readonly string[], read: (bodies: B) => T,
                  again: (value: T) => boolean,
                  fetch: (located: Located) => Promise<B>): Promise<T>;
  /** `rereading` for a page: its bodies' windows. */
  rereadingPage<T>(locate: () => Wants, read: (windows: ReadonlyMap<string, Window>) => T,
                   again: (value: T) => boolean): Promise<T>;
  /** A read door's unit, then its rows' placed bodies' windows read from R2. */
  reading<T, U = T>(unit: () => { value: T } & Wants,
                    fill: (value: T, windows: ReadonlyMap<string, Window>) => U,
                    synced?: boolean): Promise<U>;
  /** A door's caller checked in a unit, then a new owner's starter seed awaited. */
  seededFor(door: Door): Promise<void>;
  /** The one schedule synced from the rows units changed. */
  sync(): Promise<void>;
  /** The platform's gates as this object reads them (`control-gate.ts`). */
  control(): ControlGate;
  /** The owner's own admission for `door`, as a binding check reads it, for internal calls. */
  admitted(door: DoorClass): void;
  /** The owner's `require_work` refusal, or null while it is admitted. */
  unadmitted(): Refusal | null;
  /** The owner's own tier. */
  tier(): TierName;
  /** The platform's setting of the owner's tier (`admission.ts`). */
  setTier(request: unknown): { tier: TierName };
  /** Whether the owner is erased or being erased: its doors with no binding do nothing. */
  gone(): boolean;
  /**
   * The cutover's discard of the owner (`import.ts`'s `importDiscard`): every R2 object and
   * the whole database deleted, no tombstone; the namespace it deleted.
   */
  discard(): Promise<string | null>;
  /** The owner's admission as its row holds it (`admission.ts`): a read. */
  admission(): Admission;
  /** The tier numbers a door's binding handed, kept for the runs the lane starts. */
  keepTiers(binding: Binding): void;
  /** `capability` held where `account` acts, as the runner's scope step holds it. */
  scope(account: Account, capability: RunCapability, center?: unknown, agent?: string): void;
  /** Where a scoped client acts: the command center its one enabled agent is registered in. */
  universe(account: Account): string | null;
  /** The account `binding` binds to at `door`'s row, as the runner binds it there. */
  accountAt(door: string, binding: Binding): Account;
  /** `door`'s own public method called with `args`, as any call of it is (code mode). */
  invoke(door: string, args: readonly unknown[]): Promise<unknown>;
}

export class OwnerDO extends DurableObject<OwnerEnv> implements OwnerDoors {
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
  /** Runs launched and delivered, and their sandbox's leases (`launch.ts`). */
  readonly #launcher: Launcher;
  /** The object's state, as its modules are handed it (`OwnerState`). */
  readonly #state: OwnerState;
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
  /** How long a script's read of the control record serves its calls. */
  readonly #controlFreshMs: number;
  /** The owner this object is named by (`ctx.id.name`), refused when it has none. */
  readonly #ownerName: () => string;
  readonly #schedule: Schedule;
  readonly #lifecycle: Lifecycle<OwnerEnv>;
  /** The archive: its moves and rewrites, and reads of archived mail (`archive.ts`). */
  readonly #archive: Archive;
  /**
   * What rings outside agents' doorbells (`wakes.ts`), called in process by the `wakes`
   * lane: every wake kind's request from its record, after the kill switch (`CONTROL`).
   */
  readonly #sender: Doorbell;
  /**
   * Whether this object's database is an erased owner's tombstone, as the object found it
   * starting (`admission.ts`): it then migrates nothing, starts no Lifecycle, and every door
   * refuses.
   */
  readonly #tombstone: boolean;
  /** The owner's erasure (`erasure.ts`, `erase.ts`), begun at `ownerErase`. */
  readonly #erasure: Erasure;
  /**
   * The cutover's discard (`#discard`): null until this instance wiped its owner's database
   * for the import, then the namespace it deleted; from then on every door answers `busy`.
   */
  #discarded: { readonly ns: string | null } | null = null;
  /**
   * The runtime constructs it with `ctx` and `env` alone; the workerd tests take the
   * erasure's internal path (`internals`), which no door reaches.
   */
  constructor(ctx: DurableObjectState, env: OwnerEnv, internals: Internals = {}) {
    super(ctx, env);
    this.#gate = new WriteGate(ctx.storage);
    const storage = this.#gate.storage;
    const write = this.#gate.write;
    this.#storage = storage;
    this.#state = this.#stateOf(env, storage);
    this.#tombstone = tombstoned(storage.sql);
    const admitted = () => isAdmitted(storage);
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
      // What a file change says to the owner's other modules, in its own unit.
      (path, present, now, change) => fileChanged(this.#state, path, present, now, change),
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
    this.#collections = new Collections(storage, this.#bodies.adopt,
                                        () => fileLimits(this.#policy.read().document, null));
    this.#grants = new Grants(storage, this.#policy);
    this.#sessions = new Sessions(storage);
    this.#ledger = new Ledger(storage, this.#grants, admitted, () => this.#collections.clock(),
                              this.#bodies.adopt, (needed) => this.#files.fullRefusal(needed));
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
      admitted,
      gate: async () => await this.#control().refusal("outside", owner()) === null,
      // The secret is the gateway's alone: it opens the blob and makes the send.
      ring: (subscription, version, blob, previous, request): Promise<RingAnswer> =>
        env.GATEWAY.ringDoorbell(owner(), subscription, version, blob, previous, request),
    });
    this.#connections = new Connections(storage, write, env.GATEWAY, owner,
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
      cost: priced,
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
    this.#launcher = new Launcher(this.#state);
    this.#runs = this.#launcher.runs;
    this.#sandboxTime = new SandboxTime(storage, this.#runs.meter(), {
      read: (after) => env.SANDBOX.meter(this.#ownerName(), { after }),
      write,
    });
    this.#relay = new Relay({
      admit: (binding, conversation, ticket) =>
        this.#door(() => this.#socketAdmit(binding, conversation, ticket)),
      check: (state) => this.#door(() => this.#socketCheck(state)),
      // A sign-in's socket gets its own tail on attach, as a conversation's gets its runs'.
      live: (conversation) => this.#door(() => signinOwned(this.#state, conversation) ? [conversation]
        : this.#runs.liveIn(conversation)),
      now: clock,
    });
    this.#archive = new Archive({
      storage, door: (act) => this.#door(act), ledger: this.#ledger, files: this.#files,
      bodies: this.#bodies, notices: this.#notices,
      rereadingPage: (locate, read, again) => this.#rereadingPage(locate, read, again),
      betweenPasses: internals.archiveBetweenPasses ?? null,
      chainStep: internals.chainStep ?? null,
    });
    // The lanes of the one schedule, each its module's own (`Lane`): its key is its job's
    // durable id. Each pass is handed its wall-clock end (`until`) and passes it on, so no
    // lane outruns its hung timeout; the passes write through the object's gate. There is no
    // global tick; each owner's object schedules only itself.
    this.#schedule = new Schedule({
      wakes: this.#sender, files: this.#files, connections: this.#connections,
      runs: this.#launcher, manifest: this.#backups, sandbox: this.#sandboxTime,
      archive: this.#archive, starter: this.#starter,
    }, clock, write, () => wiped(storage.sql) || erasing(storage));
    const lifecycle = new Lifecycle<OwnerEnv>(this);
    this.#erasure = new Erasure({
      storage, write, clock, lifecycle,
      // Every run ended: its streams, its sockets, and its CLI through the sandbox.
      ended: () => {
        this.#egress.drop();
        this.#relay.closeAll("erased");
        this.#launcher.stop(this.#runs.liveRuns());
      },
      // Every sandbox of the owner killed, paused ones included, and the sandbox Worker's
      // state of them deleted (slice S review H-6).
      sandboxes: async () => {
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
      bare: () => this.#gate.wipe(() => undefined),
      retryMs: internals.eraseRetryMs ?? ERASE_RETRY_MS,
    });
    internals.erasure?.(this.#erasure);
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
   * The object's state as its modules are handed it: each store read when a module asks
   * (so the state is made before them, and none is read unmade), the gate's units, and the
   * reads only the object makes.
   */
  #stateOf(env: OwnerEnv, storage: OwnerStorage): OwnerState {
    const object = this;
    return Object.seal({
      env, storage,
      owner: () => object.#ownerName(),
      get collections() { return object.#collections; },
      get policy() { return object.#policy; },
      get grants() { return object.#grants; },
      get sessions() { return object.#sessions; },
      get ledger() { return object.#ledger; },
      get files() { return object.#files; },
      get backups() { return object.#backups; },
      get openExports() { return object.#exports; },
      get sandboxTime() { return object.#sandboxTime; },
      get bodies() { return object.#bodies; },
      get registrations() { return object.#registrations; },
      get notices() { return object.#notices; },
      get starter() { return object.#starter; },
      get connections() { return object.#connections; },
      get usage() { return object.#usage; },
      get egress() { return object.#egress; },
      get runs() { return object.#runs; },
      get launcher() { return object.#launcher; },
      get relay() { return object.#relay; },
      get signins() { return object.#signins; },
      get tickets() { return object.#tickets; },
      get keys() { return object.#keys; },
      get inbound() { return object.#inbound; },
      get archive() { return object.#archive; },
      get erasure() { return object.#erasure; },
      get posts() { return object.#posts; },
      heldBack: null,
      door: <T>(act: () => T) => object.#door(act),
      quiet: <T>(act: () => T) => object.#quiet(act),
      synced: <T>(act: () => T) => object.#synced(act),
      filing: <T>(act: () => T) => object.#filing(act),
      placing: <T>(prepare: () => { body: unknown; writer: string } | { done: T },
                   act: (placed: Placed | null) => T,
                   options?: { synced?: boolean; shown?: Shown<T> }) =>
        object.#placing(prepare, act, options),
      rereading: <T, B>(locate: () => readonly string[], read: (bodies: B) => T,
                        again: (value: T) => boolean,
                        fetch: (located: Located) => Promise<B>) =>
        object.#rereading(locate, read, again, fetch),
      rereadingPage: <T>(locate: () => Wants,
                         read: (windows: ReadonlyMap<string, Window>) => T,
                         again: (value: T) => boolean) =>
        object.#rereadingPage(locate, read, again),
      reading: <T, U = T>(unit: () => { value: T } & Wants,
                          fill: (value: T, windows: ReadonlyMap<string, Window>) => U,
                          synced?: boolean) => object.#reading(unit, fill, synced),
      seededFor: (door: Door) => object.#seededFor(door),
      sync: () => object.#schedule.sync(),
      control: () => object.#control(),
      admitted: (door: DoorClass) => object.#admitted(door),
      unadmitted: () => workRefusal(admission(storage)),
      tier: () => ownerTier(storage),
      setTier: (request: unknown) => setTier(storage, request),
      gone: () => object.#tombstone || object.#erasure.erased || erasing(storage),
      discard: () => object.#discard(),
      admission: () => admission(storage),
      keepTiers: (binding: Binding) => keepTiers(storage, binding.tiers, clock()),
      scope: (account: Account, capability: RunCapability, center?: unknown, agent?: string) =>
        object.#scope(account, capability, center, agent),
      universe: (account: Account) => object.#universe(account),
      accountAt: (door: string, binding: Binding) =>
        object.#within(doorRow(door), binding, undefined, ({ account }) => account),
      invoke: (door: string, args: readonly unknown[]) =>
        Reflect.apply((object as unknown as Record<string, unknown>)[door] as
          (...args: unknown[]) => Promise<unknown>, object, args),
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
   * work acts as the owner (never the owner itself at an owner-only door, `#ownerOf`), a
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
          this.#launcher.stop(this.#runs.endClient(grant.stamp.client, clock()));
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
  #ownerOf(binding: Binding, door: DoorClass, action: string): Account {
    const account = this.#account(binding, door);
    if (account.origin !== null || account.run !== null) {
      throw new Refusal("refused", `only the owner may ${action}`);
    }
    return account;
  }

  // -- THE RUNNER: every door's bind, account, scope and fields, from its row ------------
  //
  // Each door is a row of the contract's table (`contract/doors.json`) and its handler: the
  // public method names its row and hands the handler, and the steps are these, from the
  // row alone.

  /** A door whose handler runs in one unit of its row's gate, checked in that unit. */
  #run<R, T>(name: string, binding: Binding, request: R,
             act: (o: OwnerState, call: Call<R>) => T): Promise<T> {
    const row = doorRow(name);
    const unit = () => this.#within(row, binding, request, (call) => act(this.#state, call));
    switch (row.gate) {
      case "door":
        return this.#door(unit);
      case "quiet":
        return this.#quiet(unit);
      case "synced":
        return this.#synced(unit);
      case "filing":
        return this.#filing(unit);
      case "units":
        break;
    }
    throw new Error(`${name} runs in units of its own`);
  }

  /** A door whose handler makes its own units, each checking the call again. */
  #units<R, T>(name: string, binding: Binding, request: R,
               act: (o: OwnerState, door: Door<R>) => Promise<T>): Promise<T> {
    const row = doorRow(name);
    if (row.gate !== "units") {
      throw new Error(`${name} runs in one unit of its gate`);
    }
    return act(this.#state, {
      binding, request,
      check: () => this.#within(row, binding, request, (call) => call),
      within: (inner) => this.#within(row, binding, request, inner),
      bound: () => this.#bound(row, binding, request).account,
      authorized: () => {
        const { account } = this.#bound(row, binding, request);
        this.#guard(row, binding, account);
        this.#scoped(row, account, null);
      },
    });
  }

  /**
   * `act` on the call its row checks. A session door is a widening: the owner's live
   * session, checked against the object's own session row (S30, N10), and, when the row
   * names what only the owner itself may do in it, never an outside client's call. A
   * refusal after its account is bound leaves a refusal receipt of what was asked (the
   * row's stand-in for it, where it names one), written once the refused change rolled back.
   */
  #within<R, T>(row: DoorRow, binding: Binding, request: R, act: (call: Call<R>) => T): T {
    if (row.caller !== "session") {
      return act(this.#checked(row, binding, request, this.#bound(row, binding, request), ""));
    }
    const account = this.#account(binding, row.class as DoorClass,
                                  { registering: row.registering === true });
    try {
      // An outside client holds no owner session, so what only the owner may do is
      // refused as that before the session is looked for.
      if (typeof row.action === "string" && account.origin !== null) {
        throw new Refusal("refused", `only the owner may ${row.action}`);
      }
      const session = this.#sessions.live(binding.session, clock());
      return act(this.#checked(row, binding, request, { ...bound("", ""), account }, session));
    } catch (error) {
      if (error instanceof Refusal) {
        try {
          this.#policy.refusal(row.rule!, { request: "receipt" in row ? row.receipt : request,
                                            origin: account.origin }, clock());
        } catch {
          console.error("owner-session refusal receipt unavailable");  // refuse regardless
        }
      }
      throw error;
    }
  }

  /** The bind and account steps of the row's caller kind (`contract/src/doors.ts`). */
  #bound(row: DoorRow, binding: Binding, request: unknown): Bound {
    const door = row.class as DoorClass;
    const capability = row.capability!;
    switch (row.caller) {
      case "account":
      case "session":
        return { ...bound("", ""), account: this.#account(binding, door) };
      case "visitors":
        return { ...bound("", ""), account: this.#account(binding, door, { visitors: true }) };
      case "owner": {
        const account = this.#ownerOf(binding, door, row.action!);
        if (row.live === true) {
          this.#sessions.live(binding.session, clock());
        }
        return { ...bound("", ""), account };
      }
      case "lease":
        return binding.sandbox === undefined
          ? { ...bound("", ""), account: this.#ownerOf(binding, door, row.action!) }
          : this.#syncer(binding, door, capability, request as SyncCommitRequest);
      case "sync":
        return this.#syncer(binding, door, capability,
                            request as { lease_id?: unknown; epoch?: unknown });
      case "reader":
      case "changes": {
        // A run's own call carries its egress token as its sandbox: a reader's caller too.
        const filer = binding.sandbox === undefined
          || (row.caller === "reader" && binding.run !== undefined);
        if (!filer) {
          return this.#syncer(binding, door, capability, null);
        }
        const account = this.#account(binding, door);
        this.#scope(account, capability);
        return { ...bound("", ""), account };
      }
      case "egress": {
        const { owner, caller } = this.#bind(binding, door, "egress");
        return bound(owner, caller);
      }
      case "runner":
      case "run": {
        const { owner, caller } = this.#bind(binding, door,
                                             row.caller === "run" ? "run" : "egress");
        const lease = this.#runner(binding);
        if (lease === null) {
          throw row.closed === "egress_lease_closed"
            ? new Refusal("refused", "egress_lease_closed") : new Refusal("run_ended", RUN_ENDED);
        }
        return { ...bound(owner, caller), lease };
      }
      case "inbound": {
        const { owner } = this.#bind(binding, door, "inbound");
        if (binding.origin !== null || binding.caller !== owner || binding.session !== undefined
            || binding.source !== undefined) {
          throw new Refusal("invalid", "invalid inbound binding");
        }
        const route = binding.inbound!;
        return { ...bound(owner, owner),
                 route: this.#inbound.current(bindingId(route.binding), route.revision) };
      }
      case "reserved": {
        const { owner, caller } = this.#bind(binding, "admission");
        if (caller !== PLATFORM_CALLER || !("origin" in binding) || binding.origin !== null
            || binding.session !== undefined || binding.source !== undefined) {
          throw new Refusal("refused", row.action === "tier" ? PLATFORM_TIER_ONLY : PLATFORM_ONLY);
        }
        return bound(owner, caller);
      }
      case "erasure": {
        // An owner wiped once its outside record was confirmed: its platform doors answer so.
        if ((this.#tombstone || this.#erasure.erased) && binding?.caller === PLATFORM_CALLER) {
          return { ...bound("", PLATFORM_CALLER), erased: true };
        }
        const { owner, caller } = this.#bind(binding, "admission", "erasure");
        if (caller !== PLATFORM_CALLER || !("origin" in binding) || binding.origin !== null
            || binding.session !== undefined || binding.source !== undefined) {
          throw new Refusal("refused", PLATFORM_ONLY);
        }
        if (!erasing(this.#storage)) {
          throw new Refusal("refused", NO_ERASURE);
        }
        return bound(owner, caller);
      }
      case "import": {
        // The platform's import, while the owner is pending (`import.ts`): the tier numbers it
        // carries kept, as an account door keeps them, for the files plane's quota.
        const { owner, caller } = this.#bind(binding, "import");
        keepTiers(this.#storage, binding.tiers, clock());
        return bound(owner, caller);
      }
      case "discard": {
        // The import's discard: a pending owner, or a rehearsal's in any state; an instance
        // that discarded already answers it again (its database is gone, and so its state).
        if (this.#discarded !== null) {
          importBinding(binding);
          return bound(principal(binding.owner), PLATFORM_CALLER);
        }
        const { owner, caller } = this.#bind(binding, "import", "discard");
        return bound(owner, caller);
      }
      case "none":
        return bound("", "");
    }
  }

  /** The guard, scope and fields steps of a bound call, in the row's order. */
  #checked<R>(row: DoorRow, binding: Binding, request: R, held: Bound, session: string):
      Call<R> {
    const { account } = held;
    let r: Record<string, unknown>;
    let place: { universe: string | null; agent: string };
    // A row that checks its fields first checks them before its guard too (a run's malformed
    // mailbox read is `invalid`, as on main); every other row, its guard, scope, then fields.
    if (row.fieldsFirst === true) {
      r = this.#fields(row, request, account);
      this.#guard(row, binding, account);
      place = this.#scoped(row, account, r);
    } else {
      this.#guard(row, binding, account);
      place = this.#scoped(row, account, null);
      r = this.#fields(row, request, account);
    }
    return { ...held, binding, request, r, session, universe: place.universe, agent: place.agent,
             held: (where) => this.#scope(account, row.capability!, where) };
  }

  /** A row's guard: a check of its caller after the account (`contract/src/doors.ts`). */
  #guard(row: DoorRow, binding: Binding, account: Account): void {
    switch (row.guard) {
      case undefined:
        return;
      case "no_mailbox":
        // A run reads and acks no mailbox: its mail is its input, and its agent's mailbox
        // holds every authority's mail, which no one run's authority covers.
        if (account.run !== null) {
          throw new Refusal("run_no_mailbox", RUN_NO_MAILBOX);
        }
        return;
      case "owner_rows":
        if (account.caller !== account.owner) {
          throw new Refusal("refused", `only the owner may ${row.action} collection rows`);
        }
        return;
      case "own_card":
        if (account.origin === null || account.grant === null) {
          throw new Refusal("refused", "only an outside client files its own agent's registration");
        }
        if (registrant(binding.source, account.origin).kind !== "mcp") {
          throw new Refusal("refused",
                            "an API key's agent registers from the owner's browser with the key");
        }
        return;
    }
  }

  /**
   * The scope step: an account door's capability held where its caller acts (`#scope`), or,
   * at a run door, where the agent its request names acts (`#runDoor`), its universe kept.
   */
  #scoped(row: DoorRow, account: Account, r: Record<string, unknown> | null):
      { universe: string | null; agent: string } {
    const none = { universe: null, agent: MAIN_AGENT };
    if ((row.caller !== "account" && row.caller !== "visitors") || row.scope === "handler") {
      return none;
    }
    if (row.scope === "agent") {
      const agent = r === null || row.agent === undefined ? null
        : agentId(r[row.agent.field] ?? row.agent.default);
      return { universe: this.#runDoor(account, row.capability as RunNeed, agent),
               agent: agent ?? MAIN_AGENT };
    }
    if (row.capability !== null) {
      this.#scope(account, row.capability);
    }
    return none;
  }

  /** The fields step: the request held to its row's arguments; a loose one is its module's. */
  #fields(row: DoorRow, request: unknown, account: Account): Record<string, unknown> {
    const args = row.args;
    if (args === "none" || args === "loose") {
      return {};
    }
    const held = "refused" in args ? args : account.origin === null ? args.owner : args.outside;
    const asked = held.omitted === true && (request === undefined || request === null) ? {}
      : request;
    return fields(asked, Object.keys(held.required), Object.keys(held.optional), held.refused,
                  held.undefinedAllowed === true);
  }

  /**
   * `capability` held as `agent` (the `main` agent by default), in `center` (default: the
   * command center a scoped client's one enabled agent is registered in): a run holds its
   * grant's scopes, which admission already held to its initiator's; a scoped grant its
   * triples there (`authority.may`); a full grant, and the owner, everything.
   */
  #scope(account: Account, capability: RunCapability, center?: unknown,
         agent: string = MAIN_AGENT): void {
    if (account.run === null && account.grant?.mode !== "scoped") {
      return;
    }
    const universe = account.run !== null ? null
      : center === undefined ? this.#universe(account) : center;
    if (!may(account.run !== null ? { run: account.run.scopes } : account.grant,
             capability, account.run !== null ? "anywhere"
               : { universe: typeof universe === "string" ? universe : null, agent })) {
      throw new Refusal("refused", NOT_GRANTED);
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
    return this.#run("sessionOpen", binding, undefined, sessionsDoors.sessionOpen);
  }

  sessionExtend(binding: Binding): Promise<{ expires_at: number }> {
    return this.#run("sessionExtend", binding, undefined, sessionsDoors.sessionExtend);
  }

  /** One session signed out, and the connection flows it began cancelled with it. */
  sessionClose(binding: Binding): Promise<{ closed: number }> {
    return this.#run("sessionClose", binding, undefined, sessionsDoors.sessionClose);
  }

  /**
   * Every session signed out: account revocation and deletion (`revoke_owner`), every open
   * connection flow cancelled in the same unit, so a late callback finds none.
   */
  sessionCloseAll(binding: Binding): Promise<{ closed: number }> {
    return this.#units("sessionCloseAll", binding, undefined, sessionsDoors.sessionCloseAll);
  }

  // -- SANDBOX TIME: the sandbox Worker's log, metered (`sandbox-time.ts`) -----------

  /**
   * One execution E2B billed, from its signed lifecycle event, which the doors verified and
   * routed here by its sealed route (`doors/src/e2b-hooks.ts`). It carries no binding, as the
   * line's ring does not (`sandboxRung`): it grants nothing, and only adds to the meter, once
   * an execution, inside the window its event's time allows. An erased or erasing owner
   * counts nothing.
   */
  async sandboxBilled(event: BilledEvent): Promise<{ counted: boolean }> {
    return this.#units("sandboxBilled", undefined as unknown as Binding, event, sandboxTimeDoors.sandboxBilled);
  }

  /** The owner's sandbox seconds a day, newest first: the founder's cost view. */
  sandboxDays(binding: Binding, request: { days?: number }): Promise<{
      days: { day: string; seconds: number; billed_seconds: number; vcpu_seconds: number;
              gb_seconds: number; unverified_executions: number }[] }> {
    return this.#run("sandboxDays", binding, request, sandboxTimeDoors.sandboxDays);
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
    return this.#run("storageUsage", binding, undefined, filesDoors.storageUsage);
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
    return this.#units("ownerExport", binding, request, exportDoors.ownerExport);
  }

  /**
   * Chunks an open export's pages named, by hash: their bytes, at most two chunks a call
   * (`BATCH_BYTES`), each checked against its hash by the files Worker. Any chunk the owner
   * still stores is served (live, or garbage in its grace), however the owner changed since;
   * one gone from the bucket is refused (`CHUNK_GONE`). The owner's alone.
   */
  async exportChunks(binding: Binding, request: { export_id: string; sha256s: string[] }):
      Promise<{ chunks: { sha256: string; data: string }[] }> {
    return this.#units("exportChunks", binding, request, exportDoors.exportChunks);
  }

  /** An export ended: its id names it no longer. */
  exportEnd(binding: Binding, request: { export_id: string }): Promise<{ ended: boolean }> {
    return this.#run("exportEnd", binding, request, exportDoors.exportEnd);
  }

  /**
   * A key rotation's sweep for this owner (`files/src/datakey.ts`, tasks.md): its wrapped
   * data key, and every nightly manifest root it keeps, wrapped again under the files
   * keyring's active kid; an idle owner's too, since nothing else rewrites its root. The
   * platform's alone (`PLATFORM_CALLER`), as `admissionSet` is.
   */
  async dataKeyRewrap(binding: Binding): Promise<{ rewrapped: boolean; roots: number }> {
    return this.#units("dataKeyRewrap", binding, undefined, filesDoors.dataKeyRewrap);
  }

  /**
   * A gateway key rotation's sweep for this owner (`gateway/src/keyring.ts`): every model
   * credential blob it holds resealed under the gateway keyring's active kid, an idle
   * connection's too, since nothing else reseals it; the platform retires a kid once every
   * owner's sweep left none. The platform's alone, as `dataKeyRewrap` is.
   */
  async gatewayReseal(binding: Binding): Promise<{ resealed: number; left: number }> {
    return this.#units("gatewayReseal", binding, undefined, connectionsDoors.gatewayReseal);
  }

  // -- OWNER_POLICY ------------------------------------------------------------

  policyRead(binding: Binding): Promise<Projection> {
    return this.#run("policyRead", binding, undefined, policyDoors.policyRead);
  }

  policyReceipts(binding: Binding): Promise<{ receipts: Receipt[] }> {
    return this.#run("policyReceipts", binding, undefined, policyDoors.policyReceipts);
  }

  /**
   * The owner's write, in its session; with `proposal`, a held card's one apply. A new
   * files retention is applied to rows already kept by housekeeping, so the schedule is
   * synced after it (the `files` lane re-applies it). Once it committed, it ends the
   * sandbox's in-flight egress streams; a call opened after it is admitted under the new
   * generation.
   */
  async policyWrite(binding: Binding, request: PolicyWriteRequest): Promise<Projection> {
    return this.#units("policyWrite", binding, request, policyDoors.policyWrite);
  }

  /**
   * An agent's proposal, the owner's or an admitted outside client's; never applied. A
   * scoped client proposes only with `propose_policy` granted where its agent is.
   */
  policyPropose(binding: Binding, request: ProposeRequest):
      Promise<{ request_digest: string; generation: number }> {
    return this.#run("policyPropose", binding, request, policyDoors.policyPropose);
  }

  /** The pending card the owner is shown, and the digest its apply must name. */
  policyProposal(binding: Binding, request: CardRequest):
      Promise<{ change: Change; digest: string }> {
    return this.#run("policyProposal", binding, request, policyDoors.policyProposal);
  }

  /**
   * Owner policy (W3): an agent's hourly wake cap and batch window, kept on its registry
   * row where the ledger reads them, and changed like any policy write: in the owner's
   * session, against the policy generation, receipted.
   */
  policyWakeLimits(binding: Binding, request: WakeLimitsRequest):
      Promise<{ agent: Agent; generation: number }> {
    return this.#run("policyWakeLimits", binding, request, ledgerDoors.policyWakeLimits);
  }

  // -- OUTSIDE_CLIENTS: grants (P0-3) ---------------------------------------------

  grantList(binding: Binding): Promise<{ clients: Client[] }> {
    return this.#run("grantList", binding, undefined, grantsDoors.grantList);
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
    return this.#run("grantSet", binding, request, grantsDoors.grantSet);
  }

  /**
   * A narrowing the owner's account makes without a session, as a key's revoke does: the
   * client's agents' wakes and held registrations end with it, in its unit.
   */
  grantRevoke(binding: Binding, request: ClientRequest):
      Promise<{ client: string; revoked: boolean; generation: number; revision: number }> {
    return this.#run("grantRevoke", binding, request, grantsDoors.grantRevoke);
  }

  /**
   * Forget a revoked API key's grant (slice 3's review L1): the binder calls it once the
   * directory has deleted the key (tasks 2). A narrowing, since a key with no row is
   * refused, so the owner's account makes it without a session. Never an MCP client's.
   */
  grantForget(binding: Binding, request: ClientRequest):
      Promise<{ client: string; forgotten: boolean }> {
    return this.#run("grantForget", binding, request, grantsDoors.grantForget);
  }

  /**
   * The owner, in its session, forgets revoked clients to make room under its bound: a
   * widening, so it is the only act that lifts an MCP revoke, and even then a forgotten
   * MCP client comes back only from a sign-in after the revoke (`grants.ts`).
   */
  grantForgetRevoked(binding: Binding, request: ForgetRequest):
      Promise<{ forgotten: string[]; mcp_fence: number }> {
    return this.#run("grantForgetRevoked", binding, request, grantsDoors.grantForgetRevoked);
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
    return this.#run("keyMint", binding, request, keysDoors.keyMint);
  }

  /** The owner's API keys, live and revoked: what its key list shows, and its erasure ends. */
  keyList(binding: Binding): Promise<{ keys: ApiKey[] }> {
    return this.#run("keyList", binding, undefined, keysDoors.keyList);
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
    return this.#units("ownerErase", binding, request, erasureDoors.ownerErase);
  }

  /**
   * The erasure's outside step, as the doors take it from the erasure queue: what the
   * directory holds for the owner (its keys' ids, its inbound routes), read only while the
   * owner is marked erasing, and only by the platform's reserved caller; null once the
   * owner is wiped (its outside record confirmed before the wipe).
   */
  erasureOutside(binding: Binding): Promise<{ keys: string[]; routes: string[] } | null> {
    return this.#run("erasureOutside", binding, undefined, erasureDoors.erasureOutside);
  }

  /** The doors' confirmation that the outside record is done: the wipe may go on. */
  erasureOutsideDone(binding: Binding): Promise<{ confirmed: true }> {
    return this.#run("erasureOutsideDone", binding, undefined, erasureDoors.erasureOutsideDone);
  }

  // -- THE CUTOVER'S IMPORT: the platform's, into a pending owner (`import.ts`) ------------

  /** The owner's old policy, then its collections' rows, each skipped when it is there. */
  importRows(binding: Binding, request: ImportRowsRequest): Promise<Imported> {
    return this.#units("importRows", binding, request, importDoors.importRows);
  }

  /** Folders and inline files of the imported tree, each skipped when it is there. */
  importFiles(binding: Binding, request: ImportFilesRequest): Promise<Imported> {
    return this.#run("importFiles", binding, request, importDoors.importFiles);
  }

  /** A chunked file's upload, begun or resumed, for the doors to put its bytes to `FILES`. */
  importFileBegin(binding: Binding, request: ImportFileBeginRequest): Promise<ImportUpload> {
    return this.#units("importFileBegin", binding, request, importDoors.importFileBegin);
  }

  /** The doors' word that `FILES` took these of an import upload's chunks. */
  importFileStored(binding: Binding, request: ImportFileStoredRequest):
      Promise<{ stored: number }> {
    return this.#run("importFileStored", binding, request, importDoors.importFileStored);
  }

  /** An import upload committed as its path, or skipped when the file is there. */
  importFileCommit(binding: Binding, request: ImportFileCommitRequest): Promise<Imported> {
    return this.#run("importFileCommit", binding, request, importDoors.importFileCommit);
  }

  /** The owner's one notice that its old files are in the imported tree. */
  importNotice(binding: Binding, request: ImportNoticeRequest): Promise<{ added: boolean }> {
    return this.#run("importNotice", binding, request, importDoors.importNotice);
  }

  /** A page of the check: the files, every chunk read back, the rows' digest, or the rest. */
  importCheck(binding: Binding, request: ImportCheckRequest): Promise<ImportCheckAnswer> {
    return this.#units("importCheck", binding, request, importDoors.importCheck);
  }

  /**
   * The import's discard: every R2 object of the owner deleted and its database wiped, with no
   * tombstone, no erased record and no issuer's deletion; a pending owner, or a rehearsal's.
   */
  importDiscard(binding: Binding): Promise<{ discarded: true; namespace: string | null }> {
    return this.#units("importDiscard", binding, undefined, importDoors.importDiscard);
  }

  /**
   * The discard's wipe (`import.ts`, `Erasure.discard`): the erasure's own deletion of every
   * R2 object by the namespace's prefix (its files' and placed bodies' chunks and its
   * generations), Lifecycle's alarm disabled so nothing writes after, then the whole database
   * deleted, and the prefix deleted once more; no tombstone. A rehearsal's owner is never
   * signed into (`rehearsalName`), so it has no source connected and no run. From then on this instance answers every door `busy` (`DISCARDED`) and a
   * discard again with the namespace it deleted; the runtime's next instance of the name is a
   * fresh, `pending` object. Repeatable; the namespace, read before the wipe.
   */
  async #discard(): Promise<string | null> {
    if (this.#discarded !== null) {
      if (this.#discarded.ns !== null) {
        await this.#files.eraseNamespace(this.#discarded.ns);
      }
      return this.#discarded.ns;
    }
    return this.#erasure.discard((ns) => {
      this.#discarded = { ns };
    });
  }

  // -- INBOUND: the owner's event sources (slice L; `inbound.ts`) ----------------------

  /** The owner's inbound bindings, never a secret. */
  inboundList(binding: Binding): Promise<{ bindings: InboundBinding[] }> {
    return this.#run("inboundList", binding, undefined, inboundDoors.inboundList);
  }

  /**
   * A new inbound binding, in the owner's session: a capability URL the binder minted (its
   * route the token's hash), with an owner-held proof if the owner adds one; or a shared
   * app's installation, which only its install flow (owed) may name.
   */
  inboundCreate(binding: Binding, request: InboundCreateRequest): Promise<InboundBinding> {
    return this.#run("inboundCreate", binding, request, inboundDoors.inboundCreate);
  }

  /** The owner's change of a binding, in its session, against its revision. */
  inboundSet(binding: Binding, request: InboundSetRequest): Promise<InboundBinding> {
    return this.#run("inboundSet", binding, request, inboundDoors.inboundSet);
  }

  /** A binding withdrawn, a narrowing the owner's account makes without a session. */
  inboundRevoke(binding: Binding, request: BindingRequest):
      Promise<{ binding_id: string; route: string | null }> {
    return this.#run("inboundRevoke", binding, request, inboundDoors.inboundRevoke);
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
    return this.#units("inboundAccept", binding, request, inboundDoors.inboundAccept);
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
    return this.#units("fileUploadTarget", binding, request, filesDoors.fileUploadTarget);
  }

  /**
   * The binder's word that `FILES` accepted these of the intent's chunks (each put verified
   * its hash): recorded as `fileUploadChunks` records its own puts.
   */
  fileUploadStored(binding: Binding, request: UploadStoredRequest): Promise<{ stored: number }> {
    return this.#run("fileUploadStored", binding, request, filesDoors.fileUploadStored);
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
    return this.#units("outsideAgents", binding, request, ledgerDoors.outsideAgents);
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
    return this.#run("registerAgent", binding, request, ledgerDoors.registerAgent);
  }

  /**
   * An MCP client files the registration of its agent on its own verified call, so the
   * object captures the origin the binder verified; the owner applies it in session
   * (`registerAgent`). Never applied here. Filing again replaces the identity's card.
   */
  registerPropose(binding: Binding, request: RegisterRequest):
      Promise<{ card_id: string; digest: string; expires_at: number }> {
    return this.#run("registerPropose", binding, request, registrationsDoors.registerPropose);
  }

  /** The pending registration card the owner is shown, and the digest its apply names. */
  registerProposal(binding: Binding, request: RegistrationCardRequest): Promise<Card> {
    return this.#run("registerProposal", binding, request, registrationsDoors.registerProposal);
  }

  /**
   * The owner disables any agent in its session. An outside client cannot reach it: it
   * holds no owner session, and its call never carries one.
   */
  disableAgent(binding: Binding, request: DisableRequest): Promise<Agent> {
    return this.#run("disableAgent", binding, request, ledgerDoors.disableAgent);
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
    return this.#run("agentDoorbell", binding, request, ledgerDoors.agentDoorbell);
  }

  /** Only the owner itself, in its session, ends a doorbell it pasted, by its id. */
  agentDoorbellEnd(binding: Binding, request: UnsubscribeRequest):
      Promise<{ unsubscribed: boolean }> {
    return this.#run("agentDoorbellEnd", binding, request, ledgerDoors.agentDoorbellEnd);
  }

  /** Only the owner itself, in its session, deletes an agent. */
  deleteAgent(binding: Binding, request: AgentRequest):
      Promise<{ agent_id: string; messages: number }> {
    return this.#run("deleteAgent", binding, request, ledgerDoors.deleteAgent);
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
    return this.#units("roomPost", binding, request, ledgerDoors.roomPost);
  }

  // -- MODEL CONNECTIONS: sealed credentials, flows and spend (tasks 4 G2) ------------
  //
  // Each door is the owner's own, in its live session: an outside client's call carries
  // no session and is refused, with a refusal receipt. The binder seals and exchanges with
  // the gateway between them; no plaintext secret reaches the object.

  /** A pending connection for the binder to seal a credential for; for OAuth, its flow. */
  connectionPrepare(binding: Binding, request: ConnectionPrepareRequest): Promise<Prepared> {
    return this.#run("connectionPrepare", binding, request, connectionsDoors.connectionPrepare);
  }

  /** The sealed blob the gateway returned, and the models its setup check listed. */
  connectionCommit(binding: Binding, request: ConnectionCommitRequest): Promise<ConnectionView> {
    return this.#run("connectionCommit", binding, request, connectionsDoors.connectionCommit);
  }

  /**
   * A connection unusable at once; its credential deleted here, and at its provider. Its
   * sandbox egress streams end once the revoke committed.
   */
  async connectionRevoke(binding: Binding, request: ConnectionRequest):
      Promise<{ connection: string; revoked: boolean; provider_revoke: boolean }> {
    return this.#units("connectionRevoke", binding, request, connectionsDoors.connectionRevoke);
  }

  /** Every connection, newest first, never its blob. */
  connectionList(binding: Binding): Promise<{ connections: ConnectionView[] }> {
    return this.#run("connectionList", binding, undefined, connectionsDoors.connectionList);
  }

  /** The callback's state, in the session that began the flow: its verifier, used up. */
  flowFinish(binding: Binding, request: FlowFinishRequest):
      Promise<{ connection: string; version: number; verifier: string; redirect_uri: string;
                source: string }> {
    return this.#run("flowFinish", binding, request, connectionsDoors.flowFinish);
  }

  /** The owner's newest model-call receipts: what each call reserved, and what it cost. */
  usageReceipts(binding: Binding): Promise<{ receipts: UsageReceipt[] }> {
    return this.#run("usageReceipts", binding, undefined, usageDoors.usageReceipts);
  }

  // -- NOTICES: what the owner's work reported to the owner -------------------------

  /** The owner's notices, newest first (`notices.ts`). */
  noticeList(binding: Binding): Promise<{ notices: Notice[] }> {
    return this.#run("noticeList", binding, undefined, noticesDoors.noticeList);
  }

  /**
   * The starter package's receipts (`starter.ts`): each path it wrote, the version, and
   * whether it stands as installed, edited, deleted or proposed; with the version the owner's
   * files were last reconciled to and the version this code carries.
   */
  starterReceipts(binding: Binding):
      Promise<{ version: number; carried: number; receipts: StarterReceipt[] }> {
    return this.#run("starterReceipts", binding, undefined, starterDoors.starterReceipts);
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
    return this.#units("admissionSet", binding, request, admissionDoors.admissionSet);
  }

  /**
   * A mailbox page. Its placed bodies (ADR-035) are read from R2 first, located in a unit
   * of their own; the read's unit then marks only what it returns, and ends the page before
   * a placed body it was not handed (one that arrived between), which a further read of
   * the bodies picks up.
   */
  mailboxRead(binding: Binding, request: ReadRequest): Promise<MailPage> {
    return this.#units("mailboxRead", binding, request, ledgerDoors.mailboxRead);
  }

  mailboxAck(binding: Binding, request: AckRequest): Promise<{ reader: string; through: number }> {
    return this.#run("mailboxAck", binding, request, ledgerDoors.mailboxAck);
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
  inboxDigest(binding: Binding, request: InboxDigestRequest):
      Promise<{ mail: MailPage; rooms: Heads }> {
    return this.#units("inboxDigest", binding, request, ledgerDoors.inboxDigest);
  }

  /** The board's rows written past `after`, each as its head, held as `collectionPull` is. */
  boardDigest(binding: Binding, request: BoardDigestRequest): Promise<Heads> {
    return this.#units("boardDigest", binding, request, collectionsDoors.boardDigest);
  }

  /**
   * The tools this caller is served (the doors list and call no others): a run's, the `run`
   * profile; an outside client's, its grant's (`custom`: the owner's policy's choice); the
   * owner's own, `custom`. `code` only while the owner's policy turns code mode on.
   */
  toolProfile(binding: Binding): Promise<{ profile: ProfileName; tools: readonly string[] }> {
    return this.#run("toolProfile", binding, undefined, codemodeDoors.toolProfile);
  }

  /**
   * Code mode at the MCP door: one script calling the caller's profile's other tools as
   * `tools.<name>(args)`, its VM in the codemode Worker (`codemode.ts`), its wall time
   * `CODEMODE_TIMEOUT_MS`. Refused unless the owner's policy has `codemode.on` (its
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
    return this.#units("codeRun", binding, request, codemodeDoors.codeRun);
  }

  /**
   * The owner's view of a mail chain (agents' mail to each other included), from any of its
   * messages, such as the mail a run's receipt names: oldest first, after `after`.
   */
  mailChain(binding: Binding, request: ChainRequest):
      Promise<{ root: string; messages: ChainMail[]; more: boolean }> {
    return this.#units("mailChain", binding, request, ledgerDoors.mailChain);
  }

  /**
   * The events this caller's client may subscribe (MCP Events `events/list`): each
   * subscribing wake kind's, to an outside client (never a run) whose grant reads somewhere.
   * Asked for a command center (`events/subscribe`, before the door sends its callback a
   * byte), only its one enabled subscribing agent's there, where it holds `read_graph`; a
   * caller with none is refused.
   */
  wakeEvents(binding: Binding, commandCenter?: string): Promise<{ events: Events[] }> {
    return this.#run("wakeEvents", binding, commandCenter, ledgerDoors.wakeEvents);
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
    return this.#run("wakeSubscribe", binding, request, ledgerDoors.wakeSubscribe);
  }

  /**
   * The caller that holds a subscription ends it (MCP Events `events/unsubscribe`), by the
   * id the door derived; repeating it is harmless. A run is refused.
   */
  wakeUnsubscribe(binding: Binding, request: UnsubscribeRequest):
      Promise<{ unsubscribed: boolean }> {
    return this.#run("wakeUnsubscribe", binding, request, ledgerDoors.wakeUnsubscribe);
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
  #bind(binding: Binding, door: DoorClass | "admission" | "import",
        sandbox: "none" | "egress" | "sync" | "run" | "inbound" | "erasure" | "discard" =
          "none"): { owner: string; caller: string } {
    if (this.#tombstone || this.#erasure.erased) {
      throw new Refusal("owner_erased", OWNER_ERASED);
    }
    if (this.#discarded !== null) {
      throw new Refusal("busy", DISCARDED);
    }
    const discarding = sandbox === "discard";
    const inbound = typeof binding === "object" && binding !== null && "inbound" in binding;
    // The cutover's import binding reaches the import doors alone, and they take no other.
    const importing = typeof binding === "object" && binding !== null && "import" in binding;
    const inboundDoor = sandbox === "inbound";
    // The erasure's own platform doors are the one way in while the owner is marked, and
    // only for the platform's reserved caller.
    const erasureDoor = sandbox === "erasure" && door === "admission"
      && typeof binding === "object" && binding !== null && binding.caller === PLATFORM_CALLER;
    if (inboundDoor || sandbox === "erasure" || discarding) {
      sandbox = "none";
    }
    if (erasing(this.#storage) && !erasureDoor) {
      this.#erasure.resume(true);
      throw new Refusal("erasing", ERASING);
    }
    if (typeof binding !== "object" || binding === null) {
      throw new Refusal("invalid", "invalid owner principal");
    }
    if (importing !== (door === "import")) {
      throw new Refusal("refused", importing ? IMPORT_DOORS_ONLY : IMPORT_PLATFORM_ONLY);
    }
    const owner = principal(binding.owner);
    const caller = principal(binding.caller);
    if (door === "import") {
      importBinding(binding);
    }
    if (owner === PLATFORM_CALLER
        || (door !== "admission" && door !== "import" && caller === PLATFORM_CALLER)) {
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
    if (door === "import") {
      // Only an owner the platform has not admitted yet is imported into: never a live one.
      // The discard takes a rehearsal's name in any state too: no issuer's subject is one.
      if (admission(this.#storage).state !== "pending"
          && !(discarding && rehearsalRun(owner) !== null)) {
        throw new Refusal("refused", IMPORT_PENDING_ONLY);
      }
    } else if (door !== "admission") {
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
   * lease, that lease at its epoch; else refused, as a lost lease's sync is fenced. What it
   * reads and writes is held to `capability` as the lease's authority holds it.
   */
  #syncer(binding: Binding, door: DoorClass, capability: RunCapability,
          request: { lease_id?: unknown; epoch?: unknown } | null): Bound {
    const { owner, caller } = this.#bind(binding, door, "sync");
    const readable = (held: Readable): Bound => ({ ...bound(owner, caller), readable: held });
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
      return readable({ lease: lease.lease_id, scope: null, test: () => true });
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
      if (may(grant, capability, stamp!)) {
        return readable({ lease: lease.lease_id, scope: "*", test: () => true });
      }
      if (door !== "read") {
        throw new Refusal("refused", NOT_GRANTED);
      }
      const agents = this.#runs.liveAgents(lease.authority)
        .map((agent) => `${AGENTS_DIR}/${agent}`);
      return readable({ lease: lease.lease_id, scope: agents.join("\n"),
                        test: (path) => path === AGENTS_DIR
                          || agents.some((dir) => within(path, dir))
                          || ABILITY_TREES.some((tree) => within(path, tree)) });
    }
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
      await this.#schedule.sync();  // a delete that rang an agent's doorbell (`ring`, `collections.ts`)
    }
    return fill(value, located.size === 0 ? new Map()
      : await this.#bodies.windows(located, wants.maxOf ?? (() => PAGE_BYTES), wants.offsetOf));
  }

  /**
   * A row written by compare-and-swap; the owner's own board or room write rings (`ring`, `collections.ts`).
   * Answered without its body (review H3): its etag, revision, seq and `body_bytes`, so a
   * body past one answer is never echoed back.
   */
  collectionWrite(binding: Binding, request: WriteRequest):
      Promise<{ row: WrittenRow } | Conflicted> {
    return this.#units("collectionWrite", binding, request, collectionsDoors.collectionWrite);
  }

  /**
   * A collection's rows past `after`, fitted to one answer (`pages.ts`): when rows are left
   * the cursor is the last one's seq, and a first row cut to fit is read on with
   * `collection_get`'s `body_offset`.
   */
  collectionPull(binding: Binding, request: PullRequest): Promise<Page> {
    return this.#units("collectionPull", binding, request, collectionsDoors.collectionPull);
  }

  /**
   * One row, fitted to one answer: with `body_offset`, its body from that byte, and with
   * `etag`, refused when the row is no longer the one that answer read.
   */
  collectionGet(binding: Binding, request: GetRequest): Promise<{ row: Row | null }> {
    return this.#units("collectionGet", binding, request, collectionsDoors.collectionGet);
  }

  /**
   * A row's kept history, fitted to one answer, newest first: when entries are left `more`
   * and `before` name the older ones; `body_offset` reads the page's newest
   * entry's body from that byte.
   */
  collectionHistory(binding: Binding, request: HistoryRequest): Promise<History> {
    return this.#units("collectionHistory", binding, request, collectionsDoors.collectionHistory);
  }

  collectionDelete(binding: Binding, request: DeleteRequest):
      Promise<{ deleted: number } | Conflicted> {
    return this.#units("collectionDelete", binding, request, collectionsDoors.collectionDelete);
  }

  collectionClear(binding: Binding, request: ClearRequest): Promise<{ deleted: number }> {
    return this.#run("collectionClear", binding, request, collectionsDoors.collectionClear);
  }

  // -- FILES: the owner's file tree (plan §6) --------------------------------------
  //
  // These doors move at most two chunks a call. The HTTP and MCP doors (tasks 2) stream
  // larger files from the binder to `FILES` itself. Another RPC may run in the object
  // whenever one awaits `FILES`, so a door that crosses to it writes its account first,
  // in a unit, and its later steps through units of their own (`Files`).

  /** A changing file door: its unit, then the one schedule synced whatever came of it. */
  async #filing<T>(act: () => T): Promise<T> {
    try {
      return await this.#door(act);
    } finally {
      await this.#schedule.sync();
    }
  }

  fileStat(binding: Binding, request: FileRequest): Promise<{ entry: Entry | null }> {
    return this.#run("fileStat", binding, request, filesDoors.fileStat);
  }

  /**
   * A directory's entries by name; with `head_chars`, each small file's (inline, so no
   * bucket is read) first characters of its text, enough for its frontmatter.
   */
  async fileList(binding: Binding, request: FileListRequest):
      Promise<{ entries: (Entry & { head?: string })[]; next: string | null }> {
    return this.#units("fileList", binding, request, filesDoors.fileList);
  }

  /**
   * A window of a file's text (`file_read`): its UTF-8 bytes from `offset`, at most
   * `max_bytes` (`READ_DEFAULT_BYTES` when omitted; at most one chunk, the files plane's own
   * unit, a technical ceiling), cut back to a character boundary, and where the next window
   * starts (null at the end). A file that is not UTF-8 text is refused as such.
   */
  async fileText(binding: Binding, request: FileTextRequest):
      Promise<{ entry: Entry; offset: number; text: string; next_offset: number | null }> {
    return this.#units("fileText", binding, request, filesDoors.fileText);
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
    return this.#units("fileSearch", binding, request, filesDoors.fileSearch);
  }

  /**
   * A file by a search's id, its stored path (`fetch`): its title, its text (at most one
   * chunk's, the files plane's own unit; `file_read` reads past it), the app's url for a
   * citation, and its metadata.
   */
  async fileFetch(binding: Binding, request: FileFetchRequest):
      Promise<{ id: string; title: string; text: string; url: string;
                metadata: FetchMetadata }> {
    return this.#units("fileFetch", binding, request, filesDoors.fileFetch);
  }

  /**
   * A file's entry and content: its bytes when small, else its chunk list for
   * `fileReadChunks`. With `etag` still current, only the entry (`not_modified`).
   */
  fileRead(binding: Binding, request: FileReadRequest):
      Promise<{ entry: Entry; not_modified?: true; inline?: Uint8Array; chunks?: ChunkRef[] }> {
    return this.#run("fileRead", binding, request, filesDoors.fileRead);
  }

  /** One or two of a kept version's chunks, from `first`, so a read pins its version. */
  async fileReadChunks(binding: Binding, request: FileChunksRequest):
      Promise<{ chunks: Uint8Array[] }> {
    return this.#units("fileReadChunks", binding, request, filesDoors.fileReadChunks);
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
    return this.#units("fileWrite", binding, request, filesDoors.fileWrite);
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
    return this.#units("fileAppend", binding, request, filesDoors.fileAppend);
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
    return this.#units("fileEdit", binding, request, filesDoors.fileEdit);
  }

  /**
   * A new owner's starter seed, awaited before a door touches the files or lists the agents,
   * after the caller's authority is checked in a unit (`Door.authorized`): an outside agent's first
   * `fetch` of `/workspace/USER.md` or `agents_list` finds the seeded profile and `main`
   * however soon after sign-up it comes, and its first write lands beside the seeded files,
   * never before them (autoconnect_research.md §2.1).
   */
  async #seededFor(door: Door): Promise<void> {
    await this.#door(() => door.authorized());
    await this.#launcher.seeded();
  }

  /**
   * Begin an upload of one file: its size and chunk hashes, and back the ones whose bytes
   * to send.
   */
  fileUploadBegin(binding: Binding, request: UploadBeginRequest):
      Promise<{ intent_id: string; missing: string[]; expires_at: number }> {
    return this.#run("fileUploadBegin", binding, request, filesDoors.fileUploadBegin);
  }

  /** Send an upload's chunk bytes, at most two chunks a call; R2 checks each hash. */
  async fileUploadChunks(binding: Binding, request: UploadChunksRequest):
      Promise<{ created: number }> {
    return this.#units("fileUploadChunks", binding, request, filesDoors.fileUploadChunks);
  }

  /** Commit an upload whose chunks are all stored as the file it declared. */
  fileUploadCommit(binding: Binding, request: UploadCommitRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#run("fileUploadCommit", binding, request, filesDoors.fileUploadCommit);
  }

  fileUploadAbort(binding: Binding, request: IntentRequest): Promise<{ aborted: boolean }> {
    return this.#run("fileUploadAbort", binding, request, filesDoors.fileUploadAbort);
  }

  fileMkdir(binding: Binding, request: MkdirRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#run("fileMkdir", binding, request, filesDoors.fileMkdir);
  }

  fileRemove(binding: Binding, request: RemoveRequest):
      Promise<{ removed: number } | FileConflicted> {
    return this.#run("fileRemove", binding, request, filesDoors.fileRemove);
  }

  fileRename(binding: Binding, request: RenameRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#run("fileRename", binding, request, filesDoors.fileRename);
  }

  fileHistory(binding: Binding, request: FileHistoryRequest):
      Promise<{ changes: FileChange[]; next: number | null }> {
    return this.#run("fileHistory", binding, request, filesDoors.fileHistory);
  }

  fileRestore(binding: Binding, request: RestoreRequest):
      Promise<{ entry: Entry } | FileConflicted> {
    return this.#run("fileRestore", binding, request, filesDoors.fileRestore);
  }

  /** Drop a kept version's content: the owner's own act, never an outside client's. */
  fileRedact(binding: Binding, request: VersionRequest): Promise<{ redacted: number }> {
    return this.#run("fileRedact", binding, request, filesDoors.fileRedact);
  }

  /**
   * Drop a path's kept versions but its current one: the owner's own act, never an
   * outside client's, and never refused for a quota.
   */
  fileHistoryPurge(binding: Binding, request: FileRequest): Promise<{ purged: number }> {
    return this.#run("fileHistoryPurge", binding, request, filesDoors.fileHistoryPurge);
  }

  fileChanges(binding: Binding, request: ChangesRequest):
      Promise<{ changes: FileChange[]; cursor: number; reset: boolean }> {
    return this.#run("fileChanges", binding, request, filesDoors.fileChanges);
  }

  fileUsage(binding: Binding): Promise<Usage> {
    return this.#run("fileUsage", binding, undefined, filesDoors.fileUsage);
  }

  // -- RUNS: the run doors (slice R, `runs.ts`; ADR-034 decision 4) -------------------------
  //
  // A run door starts, reads or cancels a run. Starting one is two units around the agent's
  // file read: the first checks the call and reads what it asks; then `agent.json` is read
  // (its bytes may cross to `FILES`); then one unit binds the call again and admits the run
  // whole (`Launcher.launch`), so nothing between the two can widen what the admission saw. A run's
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
  agentSubmit(binding: Binding, request: SubmitRequest): Promise<RunView> {
    return this.#units("agentSubmit", binding, request, runsDoors.agentSubmit);
  }

  /**
   * A launch of one of the owner's agents on an input (`run_graph`), in a fresh conversation
   * of its own: now, or as a schedule of the `runs` lane, at a time (`at`) or at each time a
   * cron names (`cron`). A schedule's run is unattended: its source must allow that
   * (ADR-033 decision 3). An agent answers mail by being mailed itself (`roomPost`).
   */
  agentRun(binding: Binding, request: LaunchRequest): Promise<RunView | RunSchedule> {
    return this.#units("agentRun", binding, request, runsDoors.agentRun);
  }

  /** A run of the caller's, from its receipt; or a schedule of the caller's. */
  agentStatus(binding: Binding, request: RunRequest): Promise<RunView | RunSchedule> {
    return this.#run("agentStatus", binding, request, runsDoors.agentStatus);
  }

  /**
   * Cancel a run of the caller's: marked, its CLI stopped through the sandbox; it settles
   * `failed` (`cancelled`) from the runner's end, or `unknown` if the runner is lost. Or a
   * schedule of the caller's removed. A settled run cancels nothing.
   */
  async agentCancel(binding: Binding, request: RunRequest):
      Promise<RunView | { schedule_id: string; cancelled: true }> {
    return this.#units("agentCancel", binding, request, runsDoors.agentCancel);
  }

  /**
   * The runner's report for its run (`EventsRequest`), on the egress host by the egress
   * token and the run its path names: each new frame read by the run's record (its session
   * id, result and error kept), relayed to the app sockets on the run's conversation, and
   * never committed; the batch renews the lease (the runner's heartbeat); the runner's
   * `end` settles the run.
   */
  runEvents(binding: Binding, request: EventsRequest):
      Promise<{ accepted: number; settled: string | null }> {
    return this.#units("runEvents", binding, request, runsDoors.runEvents);
  }

  // -- THE VENDOR'S OWN SIGN-IN, inside a sandbox (slice SIGNIN; `signins.ts`) -----------

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
  signinStart(binding: Binding, request: SigninStartRequest):
      Promise<SigninView | { started: false; code: string }> {
    return this.#units("signinStart", binding, request, signinsDoors.signinStart);
  }

  /**
   * The runner's frames of a sign-in (`ta.signin`: its state, the vendor's URL and code),
   * on the egress host by the egress token and the runner's secret of the live lease that
   * holds it: the latest prompt kept, the lease renewed (its beat), each frame relayed to the
   * owner's app sockets on the sign-in's id; its end ends it, and the lease goes once nothing
   * holds it.
   */
  signinEvents(binding: Binding, request: SigninEventsRequest):
      Promise<{ accepted: number; ended: boolean }> {
    return this.#units("signinEvents", binding, request, signinsDoors.signinEvents);
  }

  /**
   * The code the vendor's page showed the owner, pasted back: to the sign-in's CLI's stdin
   * (`SandboxService.signinInput`). The owner's own door.
   */
  signinCode(binding: Binding, request: SigninCodeRequest):
      Promise<{ delivered: boolean }> {
    return this.#units("signinCode", binding, request, signinsDoors.signinCode);
  }

  /** A sign-in stopped: its CLI's run ends `cancelled`. The owner's own door. */
  signinCancel(binding: Binding, request: SigninRequestById):
      Promise<{ cancelled: boolean }> {
    return this.#units("signinCancel", binding, request, signinsDoors.signinCancel);
  }

  /**
   * The sign-ins in progress, and each engine's presence in the owner's own sandbox as its
   * runner last reported it (never a credential). The owner's own door.
   */
  signinList(binding: Binding): Promise<{ signins: SigninView[];
                                           engines: ReturnType<Runs["signins"]> }> {
    return this.#run("signinList", binding, undefined, signinsDoors.signinList);
  }

  /**
   * The caller's past runs, newest first, a page at a time (`before`, the last page's
   * `next`): the owner's every run, an outside client's its own alone, as their receipts show
   * them (the result is the run's own door's, `agentStatus`).
   */
  runList(binding: Binding, request: RunListRequest):
      Promise<{ runs: RunListed[]; next: number | null }> {
    return this.#run("runList", binding, request, runsDoors.runList);
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
    return this.#run("signinReport", binding, request, signinsDoors.signinReport);
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
  #runDoor(account: Account, need: RunNeed, agent: string | null): string | null {
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
    if (account.grant?.mode === "scoped" && !may(account.grant, need, { universe, agent })) {
      throw new Refusal("refused", NOT_GRANTED);
    }
    return universe;
  }

  /**
   * The platform's line rang this owner (slice S; `sandbox/src/slots.ts`, through the doors
   * Worker's ring consumer): a sandbox freed, so each run waiting for one starts again
   * through its whole admission, oldest first. It takes no binding: it carries nothing,
   * reads nothing and grants nothing, and only the doors' consumer, which binds `OWNER`,
   * reaches it. With no run waiting the owner leaves the line, so the next owner is rung.
   * An erased or erasing owner does nothing (its erasure takes it out of the line).
   */
  sandboxRung(): Promise<{ waiting: number }> {
    return this.#units("sandboxRung", undefined as unknown as Binding, undefined, runsDoors.sandboxRung);
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
    return this.#run("socketTicket", binding, request, relayDoors.socketTicket);
  }

  /**
   * The first-party grant the binding's token was issued under signed out (`reduce`): its
   * outstanding tickets forgotten and its app sockets closed `signed_out`, every other
   * device's left open. The doors tell it before they revoke the grant.
   */
  socketsEnd(binding: Binding): Promise<{ grant: string; ended: true }> {
    return this.#units("socketsEnd", binding, undefined, relayDoors.socketsEnd);
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
    if (!this.#runs.owns(conversation, null) && !signinOwned(this.#state, conversation)) {
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
    return this.#run("tierSet", binding, request, tierDoors.tierSet);
  }

  /**
   * The owner's own tier, for its app (`read`): its admission row's, which only the platform
   * sets. The numbers are the control record's, which the doors read and show beside it.
   */
  tierRead(binding: Binding): Promise<{ tier: TierName }> {
    return this.#run("tierRead", binding, undefined, tierDoors.tierRead);
  }

  // -- THE SANDBOX: its one lease, its network and its egress (tasks 4 G4) -----------
  //
  // The sandbox holds one lease, the file sync lease (`files.ts`): its id is the claim
  // token and its epoch the fencing number, for the sync commits and the egress alike, and
  // it holds the hashes of its two tokens' secrets, the egress token (E2B's transform alone
  // carries it) and the sync token (the in-VM sync agent's). The lease itself is owner-
  // internal (`launch.ts`): no door reaches it. The egress doors take the egress
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
   * One sandbox request, admitted at open (`Egress.open`): its blob, version, generation,
   * reservation and end signal, or its refusal's fixed code and receipt. An OAuth
   * credential about to expire is refreshed first, as a model call's is.
   */
  egressOpen(binding: Binding, request: unknown): Promise<EgressOpened | EgressRefusal> {
    return this.#units("egressOpen", binding, request, egressDoors.egressOpen);
  }

  /**
   * An admitted model call's reserve (`Egress.reserve`), which the gateway asks for through
   * the egress door once its one parse of the body read the model and tier: its metering, or
   * its refusal's fixed code and receipt. Held to the egress binding that opened the call.
   */
  egressReserve(binding: Binding, request: unknown): Promise<EgressReserved | EgressRefusal> {
    return this.#run("egressReserve", binding, request, egressDoors.egressReserve);
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
  egressBeat(binding: Binding, request: BeatRequest): Promise<{ beat: boolean }> {
    return this.#run("egressBeat", binding, request, egressDoors.egressBeat);
  }

  egressSettle(binding: Binding, request: Record<string, unknown>):
      Promise<{ settled: boolean }> {
    return this.#run("egressSettle", binding, request, egressDoors.egressSettle);
  }

  /** The owner's newest egress receipts: what the sandbox asked, and how each ended. */
  egressReceipts(binding: Binding): Promise<{ receipts: EgressReceipt[] }> {
    return this.#run("egressReceipts", binding, undefined, egressDoors.egressReceipts);
  }

  fileSyncCommit(binding: Binding, request: SyncCommitRequest):
      Promise<{ outcomes: SyncOutcome[] }> {
    return this.#run("fileSyncCommit", binding, request, filesDoors.fileSyncCommit);
  }

  /**
   * The sync agent's upload of a file past one inline write (`INLINE_BYTES`), through the
   * files plane's own upload (slice S review M-6): begun as the lease's own writer, so its
   * sync commit (`intent_id`) takes it, fenced by the live lease.
   */
  fileSyncUploadBegin(binding: Binding, request: SyncUploadBeginRequest):
      Promise<{ intent_id: string; missing: string[]; expires_at: number }> {
    return this.#run("fileSyncUploadBegin", binding, request, filesDoors.fileSyncUploadBegin);
  }

  /** The sync agent's chunk bytes for its own upload, at most two chunks a call. */
  async fileSyncUploadChunks(binding: Binding, request: SyncUploadChunksRequest):
      Promise<{ created: number }> {
    return this.#units("fileSyncUploadChunks", binding, request, filesDoors.fileSyncUploadChunks);
  }

  /**
   * The sync agent's own upload given up (its file changed mid-sync, or was refused), let go
   * now rather than holding the owner's storage until it expires; fenced by the live lease,
   * and only the lease's own upload. One already gone is no error.
   */
  fileSyncUploadAbort(binding: Binding, request: SyncUploadAbortRequest):
      Promise<{ aborted: boolean }> {
    return this.#run("fileSyncUploadAbort", binding, request, filesDoors.fileSyncUploadAbort);
  }
}
