/**
 * The owner object's doors as the doors Worker calls them
 * (`docs/decisions/untangle-2026-11-wave-1.md` A1; `list_doors.md` R1): the one interface the
 * doors type the owner's stub by (`DurableObjectStub<OwnerDoors>`), which `OwnerDO` implements
 * (`owner/src/owner-do.ts`), so the doors import no owner source.
 *
 * It names only the doors the doors call, each a row of the door table (`doors.json`, read by
 * `doors.ts`; the services guard, `contract/guards/services.test.ts`, holds every name here
 * to a row, its arity, and its request to the row's fields): the binding first, then the
 * request, which the object's runner checks field by field:
 * - where the row lists the fields, exactly them (`Asked`: `DoorRequests`, which
 *   `requests.ts` writes from the table), or, where the doors hand a parsed body on as it
 *   came, that body (`Handed`, `JsonBody`), so `tsc` holds every request the doors build,
 *   and the object's handler, to the row's fields; where the doors build the request, also
 *   the value each field takes (`Asked<door, { … }>`), the owner's own request type's, since
 *   the row names most fields alone;
 * - where its module reads it whole (`loose`), the type its module takes, spelled once here
 *   (`AdmissionRequest`, `TierRequest`, the connections', the inbound bindings', the egress
 *   door's).
 *
 * An answer is what the doors read of it, or `object` where they pass it on as it came (as
 * JSON, or as an RPC result). A tool's door is called by its row's name alone
 * (`doors/src/tools.ts` `callDoor`), so it needs no entry here.
 *
 * Beside them are the answers the egress door reads field by field (`EgressOpened`,
 * `EgressReserved`, `EgressRefusal`) and the billed event the E2B hook routes
 * (`BilledEvent`).
 */
import type { Binding, InboundAnswer } from "./binding.ts";
import type { TierName } from "./control.ts";
import type { ProfileName } from "./doors.ts";
import type { Outcome } from "./egress.ts";
import type { CheckPart } from "./import-message.ts";
import type { Metering, MeterOutcome } from "./gateway.ts";
import type { DoorRequests } from "./requests.ts";
import type { EgressCode } from "./refusal.ts";
import type { Events } from "./wake-records.ts";

/** One execution E2B billed, from its signed lifecycle event (`doors/src/e2b-hooks.ts`). */
export interface BilledEvent {
  /** The event's id, and the execution's and sandbox's, E2B's own. */
  readonly id: string;
  readonly execution_id: string;
  readonly sandbox_id: string;
  /** The event's own time, ms. */
  readonly at: number;
  readonly authority: string;
  readonly kind: "paused" | "killed";
  readonly started_at: number;
  readonly execution_ms: number;
  readonly vcpu: number;
  readonly memory_mb: number;
}

/** A reserved model call: its metering. */
export interface EgressReserved {
  readonly ok: true;
  readonly metering: Metering;
}

/** An admitted call: what the door forwards with, and the object's end signal. */
export interface EgressOpened {
  readonly ok: true;
  readonly reservation: string;
  readonly connection: string;
  readonly blob: string;
  readonly version: number;
  readonly generation: number;
  /**
   * Whether it is a model call, its charge then reserved once the gateway parsed its body
   * (`reserve`); false for a free call (a read), which the gateway sends unmetered.
   */
  readonly metered: boolean;
  /** Closed (or errored) when the object ends the stream; the door watches it. */
  readonly ended: ReadableStream<Uint8Array>;
}

export interface EgressRefusal {
  readonly ok: false;
  readonly code: EgressCode;
  /** The refusal's receipt row; null past the minute's refusal receipts. */
  readonly receipt: string | null;
}

/**
 * A request body the doors parsed (`doors/src/kit.ts`, `jsonObject`, its one maker) and
 * hand a door as it came, or with fields of their own decoded in: the object's runner checks
 * its fields against the door's row as it does any request's. Only the doors that take one
 * name it, beside their fields (`Handed`), so a request the doors build is still held to its
 * fields. Its mark is a type's alone (no body holds it), named so no field could be it.
 */
export type JsonBody =
  Readonly<Record<string, unknown>> & { readonly "parsed by jsonObject": true };

/**
 * A door's request, by its row's fields (`requests.ts`, written from the table), and the
 * types of their values (`Values`), which the row's names alone do not give.
 */
type Asked<Door extends keyof DoorRequests, Values extends object = object> =
  DoorRequests[Door] & Values;
/** A door's request by its row's fields and their values, or a parsed body handed on. */
type Handed<Door extends keyof DoorRequests, Values extends object = object> =
  (DoorRequests[Door] & Values) | JsonBody;

/**
 * Why the platform suspends an owner (`admission.ts`): the reasons the import's admit carries
 * from the old stack too (`doors/src/import/`, `import/survey.ts`).
 */
export const SUSPENSIONS: readonly string[] = Object.freeze(["abuse", "billing"]);

/**
 * The platform's change of the owner's admission (`admission.ts`): its admit at sign-up, or
 * a suspension's reason (`SUSPENSIONS`) and whether it holds.
 */
export type AdmissionRequest =
  | { readonly admit: true }
  | { readonly reason: string; readonly suspended: boolean };

/** The platform's setting of the owner's tier (`admission.ts`; the import's admit step). */
export interface TierRequest {
  readonly tier: TierName;
}

/**
 * The owner's state as the platform reads it (`ownerState`, the operator's `owner.state`):
 * names, codes and counts alone, never its content, a credential or a binding. An owner
 * being erased, or erased, has none: the door refuses it `erasing` or `owner_erased`.
 */
export interface OwnerStateAnswer {
  /** Its admission (`admission.ts`): `pending`, `admitted` or `suspended`, and why, sorted. */
  readonly state: string;
  readonly reasons: readonly string[];
  readonly tier: TierName;
  /** The step of the restore that holds it (`owner/src/restore.ts`), or null when none does. */
  readonly restore: string | null;
  /** How many of its runs are live. */
  readonly runs: number;
  /** The bytes of its content, against its tier's storage. */
  readonly stored_bytes: number;
}

/**
 * A rotation's sweep of one owner under the files keyring (`dataKeyRewrap`, the operator's
 * `owner.rewrap`): whether its object's wrapped data key changed, the manifest roots found,
 * how many it could not bring under `kid` (a root a pass wrote meanwhile, or each generation
 * a restore that holds the owner reads), and the kid its data key is under, read from the
 * key's own header (null with no key). The platform asks again until `left` is 0.
 */
export interface RewrapAnswer {
  readonly rewrapped: boolean;
  readonly roots: number;
  readonly left: number;
  readonly kid: string | null;
}

/**
 * A rotation's sweep of one owner under the gateway keyring (`gatewayReseal`, the operator's
 * `owner.reseal`): its connection and doorbell subscription blobs resealed, how many are not
 * under `kid` (one a refresh or a revoke held, or a replaced doorbell secret still signing),
 * and the gateway's active kid as it answered (null with no blob). Asked again until `left`
 * is 0.
 */
export interface ResealAnswer {
  readonly resealed: number;
  readonly left: number;
  readonly kid: string | null;
}

/**
 * One blob sealed under the gateway's ring, as the owner's sweep left it: the active kid the
 * gateway answered for it (null: it refused the blob, naming none), the kid it is under now
 * (null: it lost its swap, went, or was refused), and whether this sweep resealed it.
 */
export interface Swept {
  readonly active: string | null;
  readonly under: string | null;
  readonly resealed: boolean;
}

/**
 * The sweep's answer from its blobs: those it resealed, and those not under the active kid
 * the gateway answered last (`left`), so a ring changed mid-sweep leaves each blob sealed
 * before the change left too.
 */
export function resealAnswer(swept: readonly Swept[]): ResealAnswer {
  const kid = swept.findLast((blob) => blob.active !== null)?.active ?? null;
  return { resealed: swept.filter((blob) => blob.resealed).length,
           left: swept.filter((blob) => blob.under === null || blob.under !== kid).length, kid };
}

/** A restore's generation (`owner/src/restore.ts`): its namespace (32 hex) and id (16 hex). */
export interface RestoreBeginRequest {
  readonly namespace: string;
  readonly gen: string;
}

/**
 * How far a restore is (`owner/src/restore.ts`): its step; a failed check's words; and once
 * done, the rows and chunks it holds, the API keys the doors delete from the directory, and
 * the owner's admission as it stood at E.
 */
export type RestoreAnswer =
  | { readonly step: "pinned" | "wiped" | "index" | "log" | "apply" | "check" | "chunks"
                     | "names" | "sweep" | "clear";
      readonly done: false }
  | { readonly step: "failed"; readonly done: false; readonly failed: string }
  | { readonly step: "done"; readonly done: true; readonly gen: string;
      readonly rows: number; readonly chunks: number; readonly keys: readonly string[];
      readonly state: string; readonly reasons: readonly string[] };

/** What a restore could read for an owner (`restoreSource`): as its object holds it. */
export interface RestoreSource {
  readonly namespace: string | null;
  /** Each finished generation, and whether a restore can load it (`backup.ts` `restorableAt`). */
  readonly generations: readonly { readonly gen: string; readonly finished_at: number;
                                   readonly restorable: boolean }[];
  readonly state: string;
  /**
   * Whether a restore may begin, or go on, in this object as it stands: pending and empty,
   * wiped for it, or begun. Else a pin and a wipe come first (a live owner, a failed restore).
   */
  readonly begins: boolean;
  /**
   * The restore's record, from its pin: its step, its generation, the pin's own, and when the
   * hold began (seconds).
   */
  readonly restore: { readonly step: string; readonly gen: string;
                      readonly fresh: string | null; readonly since: number } | null;
}

/** The generation a restore pins on a live owner (`restorePin`): one of its finished ones. */
export interface RestorePinRequest {
  readonly gen: string;
}

/** What confirms a wipe (`restoreWipe`): `wipe-<owner>-<gen>`, as the pin's answer names it. */
export interface RestoreWipeRequest {
  readonly confirm: string;
}

/**
 * The words that confirm the platform's erasure of `owner` (`erasureBegin`, the operator's
 * `owner.erase`): the owner named a second time, after the one the signed message names, so
 * an erasure is never begun by words typed for another owner. The object takes these words
 * exactly, and the kit names them before it asks (`deploy.ts`, mode `erase`).
 */
export const eraseWords = (owner: string): string => `erase-${owner}`;

/** What confirms an erasure: the owner's typed words, or the platform's (`eraseWords`). */
export interface EraseRequest {
  readonly confirm: string;
}

/**
 * A restore's hold on its owner, from the pin through the wipe (`owner/src/restore.ts`): the
 * record's step, the generation pinned and its namespace, the generation the pin wrote of the
 * owner's present state (`fresh`: null before it is begun, or for an object that held no
 * owner), whether the wipe may go on (that generation finished), and the words that confirm
 * the wipe while one is due (`pinned` once written, or `failed`), else null.
 */
export interface RestoreHold {
  readonly step: string;
  readonly namespace: string;
  readonly gen: string;
  readonly fresh: string | null;
  readonly written: boolean;
  readonly confirm: string | null;
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

/** A sign-in's runner's post: the sign-in, its frames, and its end. */
export interface SigninEventsRequest {
  readonly id: string;
  readonly frames: unknown;
  readonly end?: unknown;
}

/** A sandbox's call through the egress door, as its path and request name it. */
export interface EgressRequest {
  /** The run the call's path names (`/r/<run>/c/<connection>/...`). */
  readonly run: string;
  readonly connection: string;
  readonly method: string;
  /** The path (and query) after `/c/<connection>`, as the provider's API names it. */
  readonly path: string;
  /** The request body's declared length, or null. */
  readonly bytes_hint: number | null;
}

/**
 * An admitted model call's reserve, as the gateway's one parse of its body read it: the
 * model the body (or the path) names, and its tier option's value, or null when absent.
 */
export interface ReserveRequest {
  readonly reservation: string;
  readonly model: string;
  readonly tier: string | null;
}

/** A sync op: what the sandbox's scan found, against the version it last synced. */
export type SyncOp =
  | { readonly op: "write"; readonly path: string; readonly base: number | null;
      readonly mode?: number; readonly inline?: Uint8Array; readonly intent_id?: string }
  | { readonly op: "mkdir" | "remove"; readonly path: string; readonly base: number | null };

/** A streaming call's beat, which keeps its reservation open. */
export interface BeatRequest {
  readonly reservation: string;
}

/**
 * A call's one end as the door saw it: how it ended, the provider's status, the bytes it
 * streamed, the gateway's meter, and the reseal the gateway's header carried, when it did.
 * A type, not an interface, so it is also a JSON object to the object's settle.
 */
export type SettleSent = {
  readonly reservation: string;
  readonly outcome: Outcome;
  readonly status: number | null;
  readonly bytes: number;
  readonly spend: MeterOutcome;
  readonly resealed?: unknown;
};

/** The owner's object, as the doors reach it (`binder.ts` alone looks one up by name). */
export interface OwnerDoors extends Rpc.DurableObjectBranded {
  // The two doors that take no binding: the sandbox line's ring, and E2B's billed events.
  sandboxRung(): Promise<{ readonly waiting: number }>;
  sandboxBilled(event: BilledEvent): Promise<{ readonly counted: boolean }>;

  // Sign-in and sign-up (`authorize.ts`), the owner's session (`app.ts`), erasure (`erase.ts`).
  admissionSet(binding: Binding, request: AdmissionRequest): Promise<{ readonly state: string }>;
  tierSet(binding: Binding, request: TierRequest): Promise<{ readonly tier: TierName }>;
  // The owner's state and a key rotation's sweeps, the operator channel's (`operator.ts`).
  ownerState(binding: Binding): Promise<OwnerStateAnswer>;
  dataKeyRewrap(binding: Binding): Promise<RewrapAnswer>;
  gatewayReseal(binding: Binding): Promise<ResealAnswer>;
  // A restore, the operator channel's (`operator.ts`; `owner/src/restore.ts`).
  restoreSource(binding: Binding): Promise<RestoreSource>;
  restoreBegin(binding: Binding, request: Asked<"restoreBegin", RestoreBeginRequest>):
    Promise<RestoreAnswer>;
  restoreStep(binding: Binding): Promise<RestoreAnswer>;
  restorePin(binding: Binding, request: Asked<"restorePin", RestorePinRequest>):
    Promise<RestoreHold>;
  restoreWipe(binding: Binding, request: Asked<"restoreWipe", RestoreWipeRequest>):
    Promise<RestoreHold>;
  restoreRelease(binding: Binding): Promise<{ readonly released: true }>;
  sessionOpen(binding: Binding): Promise<{ readonly session: string }>;
  sessionExtend(binding: Binding): Promise<object>;
  sessionClose(binding: Binding): Promise<{ readonly closed: number }>;
  socketsEnd(binding: Binding): Promise<object>;
  socketTicket(binding: Binding, request: Handed<"socketTicket">): Promise<object>;
  ownerErase(binding: Binding, request: Asked<"ownerErase", EraseRequest>):
    Promise<{ readonly erasing: true }>;
  erasureBegin(binding: Binding, request: Asked<"erasureBegin", EraseRequest>):
    Promise<{ readonly erasing: true }>;
  erasureOutside(binding: Binding):
    Promise<{ readonly keys: readonly string[]; readonly routes: readonly string[] } | null>;
  erasureOutsideDone(binding: Binding): Promise<object>;

  // An MCP caller's tools (`mcp.ts`) and its MCP Events (`events.ts`).
  toolProfile(binding: Binding):
    Promise<{ readonly profile: ProfileName; readonly tools: readonly string[] }>;
  wakeEvents(binding: Binding, commandCenter?: string):
    Promise<{ readonly events: readonly Events[] }>;
  wakeSubscribe(binding: Binding, request: Asked<"wakeSubscribe", {
    readonly subscription_id: string; readonly name: string;
    readonly arguments: { readonly command_center_id: string; readonly board?: boolean;
                          readonly rooms?: readonly string[] };
    readonly blob: string; readonly kid: string; readonly version: number;
    readonly expires_at: number | null }>): Promise<object>;
  wakeUnsubscribe(binding: Binding,
                  request: Asked<"wakeUnsubscribe", { readonly subscription_id: string }>):
    Promise<object>;

  // API keys and outside clients (`api-keys.ts`, `revoke.ts`, `app.ts`).
  keyMint(binding: Binding, request: Asked<"keyMint", {
    readonly key_id: string; readonly name: string; readonly scopes: unknown;
    /** As the caller sent it: the object checks it (`request_id`, `digest`). */
    readonly proposal?: unknown }>):
    Promise<{ readonly name: string; readonly created_at: number;
              readonly scopes: readonly object[] }>;
  keyList(binding: Binding): Promise<{ readonly keys: readonly { readonly key_id: string }[] }>;
  grantRevoke(binding: Binding, request: Asked<"grantRevoke", { readonly client: string }>):
    Promise<{ readonly client: string; readonly revoked: boolean; readonly generation: number;
              readonly revision: number }>;
  grantForget(binding: Binding, request: Asked<"grantForget", { readonly client: string }>):
    Promise<{ readonly forgotten: boolean }>;
  grantSet(binding: Binding, request: Handed<"grantSet">): Promise<object>;
  grantList(binding: Binding): Promise<object>;
  registerAgent(binding: Binding, request: Handed<"registerAgent">): Promise<object>;

  // Inbound events and their bindings (`inbound.ts`).
  inboundAccept(binding: Binding, request: Asked<"inboundAccept", {
    readonly event_id: string; readonly event_at: number; readonly body: Uint8Array;
    readonly headers: Readonly<Record<string, string>> }>): Promise<InboundAnswer>;
  inboundCreate(binding: Binding, request: InboundCreateRequest | JsonBody):
    Promise<InboundRoute>;
  inboundSet(binding: Binding, request: InboundSetRequest | JsonBody): Promise<InboundRoute>;
  inboundRevoke(binding: Binding, request: BindingRequest | JsonBody):
    Promise<{ readonly route: string | null }>;
  inboundList(binding: Binding): Promise<object>;

  // Model connections (`connect.ts`).
  connectionPrepare(binding: Binding, request: ConnectionPrepareRequest):
    Promise<{ readonly connection: string; readonly version: number;
              readonly flow?: { readonly state: string; readonly code_challenge: string;
                                readonly expires_at: number } }>;
  connectionCommit(binding: Binding, request: ConnectionCommitRequest): Promise<object>;
  connectionRevoke(binding: Binding, request: ConnectionRequest): Promise<object>;
  connectionList(binding: Binding): Promise<object>;
  flowFinish(binding: Binding, request: FlowFinishRequest):
    Promise<{ readonly connection: string; readonly version: number;
              readonly verifier: string; readonly redirect_uri: string;
              readonly source: string }>;

  // The owner surface (`app.ts`): its own routes, and each door a route names.
  tierRead(binding: Binding): Promise<{ readonly tier: TierName }>;
  fileUsage(binding: Binding): Promise<object>;
  fileRead(binding: Binding,
           request: Handed<"fileRead", { readonly path: string; readonly etag?: string }>):
    Promise<{ readonly entry: object; readonly inline?: Uint8Array }>;
  fileWrite(binding: Binding, request: Asked<"fileWrite", {
    readonly path: string; readonly expected_etag: string | null;
    readonly content: string | Uint8Array; readonly mode?: number }>): Promise<object>;
  ownerExport(binding: Binding, request: Handed<"ownerExport", {
    readonly cursor?: string | null; readonly export_id?: string }>):
    Promise<{ readonly export_id: string; readonly lines: readonly string[];
              readonly next: string | null; readonly pending: boolean }>;
  exportChunks(binding: Binding, request: Asked<"exportChunks">): Promise<object>;
  exportEnd(binding: Binding, request: { readonly export_id: string }): Promise<object>;
  storageUsage(binding: Binding): Promise<object>;
  agentDoorbell(binding: Binding, request: Asked<"agentDoorbell", {
    readonly agent_id: string; readonly kind: string; readonly subscription_id: string;
    readonly board: boolean; readonly rooms: readonly string[]; readonly blob: string;
    readonly kid: string; readonly version: number }>): Promise<object>;
  agentDoorbellEnd(binding: Binding, request: Asked<"agentDoorbellEnd">): Promise<object>;
  policyWakeLimits(binding: Binding, request: Asked<"policyWakeLimits">): Promise<object>;
  noticeList(binding: Binding): Promise<object>;
  policyRead(binding: Binding): Promise<object>;
  policyWrite(binding: Binding, request: Asked<"policyWrite">): Promise<object>;
  fileUploadBegin(binding: Binding, request: Asked<"fileUploadBegin">): Promise<object>;
  fileUploadCommit(binding: Binding, request: Asked<"fileUploadCommit">): Promise<object>;
  fileUploadAbort(binding: Binding, request: Asked<"fileUploadAbort">): Promise<object>;
  agentSubmit(binding: Binding, request: Asked<"agentSubmit">): Promise<object>;
  agentRun(binding: Binding, request: Asked<"agentRun">): Promise<object>;
  agentStatus(binding: Binding, request: Asked<"agentStatus">): Promise<object>;
  agentCancel(binding: Binding, request: Asked<"agentCancel">): Promise<object>;
  runList(binding: Binding, request: Asked<"runList">): Promise<object>;
  signinStart(binding: Binding, request: Asked<"signinStart">): Promise<object>;
  signinCode(binding: Binding, request: Asked<"signinCode">): Promise<object>;
  signinCancel(binding: Binding, request: Asked<"signinCancel">): Promise<object>;
  signinList(binding: Binding): Promise<object>;

  // An upload's bytes (`file-bytes.ts`).
  fileUploadTarget(binding: Binding,
                   request: Asked<"fileUploadTarget", { readonly intent_id: string }>):
    Promise<{ readonly namespace: string; readonly key: string;
              readonly chunks: readonly { readonly sha256: string; readonly size: number }[];
              readonly pending: readonly string[] }>;
  fileUploadStored(binding: Binding, request: Asked<"fileUploadStored", {
    readonly intent_id: string; readonly chunks: readonly string[] }>): Promise<object>;

  // The cutover's import consumer (`import.ts`), at its import binding (`bindImport`).
  importNotice(binding: Binding, request: Asked<"importNotice", { readonly detail: string }>):
    Promise<object>;
  importRows(binding: Binding, request: Asked<"importRows", { readonly rows: readonly unknown[] }>):
    Promise<object>;
  importFiles(binding: Binding,
              request: Asked<"importFiles", { readonly entries: readonly unknown[] }>):
    Promise<object>;
  importFileBegin(binding: Binding, request: Asked<"importFileBegin", {
    readonly path: string; readonly size: number; readonly mode: 0 | 1;
    readonly content_id: string; readonly chunks: readonly string[] }>):
    Promise<{ readonly skipped: true }
            | { readonly skipped: false; readonly intent_id: string; readonly namespace: string;
                readonly key: string;
                readonly chunks: readonly { readonly sha256: string; readonly size: number }[];
                readonly pending: readonly string[] }>;
  importFileStored(binding: Binding, request: Asked<"importFileStored", {
    readonly intent_id: string; readonly chunks: readonly string[] }>): Promise<object>;
  importFileCommit(binding: Binding, request: Asked<"importFileCommit", {
    readonly intent_id: string; readonly path: string; readonly mode: 0 | 1;
    readonly content_id: string; readonly mtime_ms: number }>): Promise<object>;
  importCheck(binding: Binding, request: Asked<"importCheck", {
    readonly part: CheckPart; readonly page: number }>): Promise<{ readonly more: boolean }>;
  importDiscard(binding: Binding): Promise<{ readonly namespace: string | null }>;

  // The egress host (`egress.ts`): a model call, a run's events, a sign-in's, and sync.
  egressOpen(binding: Binding, request: EgressRequest): Promise<EgressOpened | EgressRefusal>;
  egressReserve(binding: Binding, request: ReserveRequest):
    Promise<EgressReserved | EgressRefusal>;
  egressBeat(binding: Binding, request: BeatRequest): Promise<object>;
  egressSettle(binding: Binding, request: SettleSent): Promise<object>;
  runEvents(binding: Binding, request: Handed<"runEvents">): Promise<object>;
  signinEvents(binding: Binding, request: SigninEventsRequest): Promise<object>;
  signinReport(binding: Binding, request: Handed<"signinReport">): Promise<object>;
  fileChanges(binding: Binding, request: Handed<"fileChanges">): Promise<object>;
  fileList(binding: Binding, request: Handed<"fileList">): Promise<object>;
  fileReadChunks(binding: Binding, request: Handed<"fileReadChunks">):
    Promise<{ readonly chunks: readonly Uint8Array[] }>;
  fileSyncCommit(binding: Binding, request: Handed<"fileSyncCommit", {
    readonly lease_id: string; readonly epoch: number; readonly ops: readonly SyncOp[] }>):
    Promise<object>;
  fileSyncUploadBegin(binding: Binding, request: Handed<"fileSyncUploadBegin">):
    Promise<object>;
  fileSyncUploadChunks(binding: Binding, request: Handed<"fileSyncUploadChunks", {
    readonly lease_id: string; readonly epoch: number; readonly intent_id: string;
    readonly chunks: readonly { readonly sha256: string; readonly bytes: Uint8Array }[] }>):
    Promise<object>;
  fileSyncUploadAbort(binding: Binding, request: Handed<"fileSyncUploadAbort">):
    Promise<object>;
}

/** An inbound binding as the doors keep its route: its id, revision and route token. */
export interface InboundRoute {
  readonly binding_id: string;
  readonly revision: number;
  readonly route: string;
}
