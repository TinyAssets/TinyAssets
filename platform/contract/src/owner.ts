/**
 * The owner object's doors as the doors Worker calls them
 * (`docs/decisions/untangle-2026-11-wave-1.md` A1; `list_doors.md` R1): `OwnerDoors`, the one
 * interface the doors type the owner's stub by (`DurableObjectStub<OwnerDoors>`), which
 * `OwnerDO` implements (`owner/src/owner-do.ts`), so the doors import no owner source.
 * `npm run generate` writes it from the door table (`owner-doors.ts`, `contract/requests.ts`):
 * each door its row's, the binding first (but on the two unbound doors), then its request,
 * which the object's runner checks field by field. What the table cannot say is here, by door
 * (`DoorTypes`):
 * - where the row lists the fields, exactly them, each of its schema's type (`Asked`:
 *   `DoorRequests`, which `requests.ts` writes from the table), or, where the doors hand a
 *   parsed body on as it came, that body (`Handed`, `JsonBody`), so `tsc` holds every
 *   request the doors build, and the object's handler, to the row's fields and types; where
 *   the doors build a field narrower than a JSON type says (a list's items, an object's
 *   keys), that value (`values`);
 * - where its module reads it whole (`loose`), the parameters its module takes, spelled once
 *   here (`AdmissionRequest`, `TierRequest`, the connections', the inbound bindings', the
 *   egress door's);
 * - where the row declares the answer the doors' code and their app read (`DoorAnswers`, its
 *   fields and their JSON types, which the deploy kit holds across serving versions,
 *   `deploy/handed.ts`, and their compiler finds them read, `doors/guards/answer-reads.test.ts`),
 *   what the doors' code reads of it narrower than those (`answer`: a
 *   literal, a union's arms, a list's items, an object's fields); where it declares none, an
 *   object they pass on as it came (as JSON, or as an RPC result).
 *
 * The answers the doors relay to the sandbox are the rows' too, the fields only its runner
 * reads marked `unread` (`RelayedAnswer`, each runner protocol's record of them,
 * `sandbox/runner/protocol.json` `relayed`). Beside them are answers as the owner's handlers
 * answer them whole, which other readers past the doors read by their own types: the egress
 * door's (`EgressOpened`, `EgressReserved`, `EgressRefusal`) and the deploy kit's through the
 * operator door (`OwnerStateAnswer`, the rotations' and the restore's); and the billed event
 * the E2B hook routes (`BilledEvent`).
 */
import type { ServedSkill } from "./agent-definition.ts";
import type { Binding } from "./binding.ts";
import type { TierName } from "./control.ts";
import type { ProfileName } from "./doors.ts";
import type { Outcome } from "./egress.ts";
import type { CheckPart } from "./import-message.ts";
import type { Metering, MeterOutcome } from "./gateway.ts";
import type { DoorAnswers, DoorRequests, UnreadAnswers } from "./requests.ts";
import type { EgressCode, RefusalCode } from "./refusal.ts";
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
 * A door's request, by its row's fields and their types (`requests.ts`, written from the
 * table), and the values the doors build narrower than those (`Values`).
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
export type RestoreBeginRequest = DoorRequests["restoreBegin"];

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

/**
 * Another namespace of the owner held by its object's own (`restoreHold`): the namespace that
 * holds it, and the one held.
 */
export interface RestoreHeld {
  readonly namespace: string;
  readonly held: string;
}

/** A page of what the owner's object names in the bucket (`restoreNames`), from the `from`th. */
export type RestoreNamesRequest = DoorRequests["restoreNames"];

/**
 * What a copy back from the off-account copy may write into the owner's namespace
 * (`restoreNames`): the bucket name of each chunk a page of its chunk rows holds, the
 * generations it holds, and where the next page begins (null: none is left).
 */
export interface RestoreNames {
  readonly chunks: readonly string[];
  readonly generations: readonly string[];
  readonly next: number | null;
}

/**
 * The generation a restore pins on a live owner (`restorePin`), and its namespace: one of its
 * own finished ones, or one of another namespace whose generation names this owner (one its
 * own holds, `r8-lost-object.md`).
 */
export type RestorePinRequest = DoorRequests["restorePin"];

/** What confirms a wipe (`restoreWipe`): `wipe-<owner>-<gen>`, as the pin's answer names it. */
export type RestoreWipeRequest = DoorRequests["restoreWipe"];

/**
 * The words that confirm the platform's erasure of `owner` (`erasureBegin`, the operator's
 * `owner.erase`): the owner named a second time, after the one the signed message names, so
 * an erasure is never begun by words typed for another owner. The object takes these words
 * exactly, and the kit names them before it asks (`deploy.ts`, mode `erase`).
 */
export const eraseWords = (owner: string): string => `erase-${owner}`;

/** The words the owner types to erase its own account (`ownerErase`), taken exactly. */
export const ERASE_CONFIRM = "erase my account";

/** What confirms an erasure: the owner's typed words (`ERASE_CONFIRM`), or the platform's. */
export type EraseRequest = DoorRequests["erasureBegin"];

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
   * Where the credential may be forwarded (the setup check's `egress`: its sealed base URLs
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

/**
 * What the door table cannot say of a door the doors call, by door: the values the doors build
 * into its row's fields narrower than its types (`values`), that they hand a parsed body on
 * (`handed`), a loose door's parameters after its binding (`loose`), and the fields of its
 * answer that their code reads narrower than its row's types (`answer`: a literal, a
 * union's arms, a list's items, an object's fields), held to the owner's own by `OwnerDO
 * implements OwnerDoors`. `tsc` refuses an entry for no door of the table, or one its row
 * leaves no room for: `values` or `handed` but on a field list, a field the row does not list,
 * `loose` but on a loose door, `answer` but on a row that declares one, or a field of it the
 * row's answer does not read (`owner-doors.ts`). A door with no entry takes its row's fields,
 * each of its type, and answers its row's answer, or an object the doors pass on.
 */
export interface DoorTypes {
  readonly sandboxBilled: { readonly loose: [event: BilledEvent] };
  readonly restoreStep: {
    readonly answer:
      { readonly done: false } | { readonly done: true; readonly keys: readonly string[] };
  };
  readonly admissionSet: { readonly loose: [request: AdmissionRequest] };
  readonly tierSet: { readonly loose: [request: TierRequest] };
  readonly socketTicket: { readonly handed: true };
  readonly ownerErase: { readonly answer: { readonly erasing: true } };
  readonly erasureOutside: {
    readonly answer: { readonly keys: readonly string[]; readonly routes: readonly string[] };
  };
  readonly toolProfile: {
    readonly answer: { readonly profile: ProfileName; readonly tools: readonly string[];
                       readonly withheld: readonly string[] };
  };
  readonly definitionRead: { readonly answer: { readonly skills: readonly ServedSkill[] } };
  readonly wakeEvents: {
    readonly loose: [commandCenter?: string];
    readonly answer: { readonly events: readonly Events[] };
  };
  readonly wakeSubscribe: {
    readonly values: {
      readonly arguments: { readonly command_center_id: string; readonly board?: boolean;
                            readonly rooms?: readonly string[] } };
  };
  readonly keyList: { readonly answer: { readonly keys: readonly { readonly key_id: string }[] } };
  readonly grantSet: { readonly handed: true };
  readonly registerAgent: { readonly handed: true };
  readonly inboundAccept: {
    readonly values: { readonly headers: Readonly<Record<string, string>> };
    readonly answer: { readonly state: "admitted" | "no_op" }
      | { readonly state: "refused"; readonly error: RefusalCode };
  };
  readonly inboundCreate: { readonly loose: [request: InboundCreateRequest | JsonBody] };
  readonly inboundSet: { readonly loose: [request: InboundSetRequest | JsonBody] };
  readonly inboundRevoke: { readonly loose: [request: BindingRequest | JsonBody] };
  readonly connectionPrepare: {
    readonly loose: [request: ConnectionPrepareRequest];
    readonly answer: { readonly flow?: { readonly state: string; readonly code_challenge: string;
                                         readonly expires_at: number } };
  };
  readonly connectionCommit: { readonly loose: [request: ConnectionCommitRequest] };
  readonly connectionRevoke: { readonly loose: [request: ConnectionRequest] };
  readonly flowFinish: { readonly loose: [request: FlowFinishRequest] };
  readonly fileRead: { readonly handed: true };
  readonly ownerExport: { readonly handed: true };
  readonly exportEnd: { readonly loose: [request: { readonly export_id: string }] };
  readonly agentDoorbell: { readonly values: { readonly rooms: readonly string[] } };
  readonly fileUploadTarget: {
    readonly answer: {
      readonly chunks: readonly { readonly sha256: string; readonly size: number }[];
      readonly pending: readonly string[] };
  };
  readonly fileUploadStored: { readonly values: { readonly chunks: readonly string[] } };
  readonly importFileBegin: {
    readonly values: { readonly mode: 0 | 1; readonly chunks: readonly string[] };
    readonly answer: { readonly skipped: true } | {
      readonly skipped: false; readonly intent_id: string; readonly namespace: string;
      readonly key: string;
      readonly chunks: readonly { readonly sha256: string; readonly size: number }[];
      readonly pending: readonly string[] };
  };
  readonly importFileStored: { readonly values: { readonly chunks: readonly string[] } };
  readonly importFileCommit: { readonly values: { readonly mode: 0 | 1 } };
  readonly importCheck: { readonly values: { readonly part: CheckPart } };
  readonly egressOpen: {
    readonly loose: [request: EgressRequest];
    readonly answer: {
      readonly ok: true; readonly reservation: string; readonly blob: string;
      readonly version: number; readonly metered: boolean;
      readonly ended: ReadableStream<Uint8Array> } | EgressRefusal;
  };
  readonly egressReserve: {
    readonly loose: [request: ReserveRequest];
    readonly answer: { readonly ok: true; readonly metering: Metering } | EgressRefusal;
  };
  readonly egressBeat: { readonly loose: [request: BeatRequest] };
  readonly egressSettle: { readonly loose: [request: SettleSent] };
  readonly runEvents: { readonly handed: true };
  readonly runDecide: { readonly handed: true };
  readonly signinEvents: { readonly loose: [request: SigninEventsRequest] };
  readonly signinReport: { readonly handed: true };
  readonly fileChanges: { readonly handed: true };
  readonly fileList: { readonly handed: true };
  readonly fileReadChunks: {
    readonly handed: true;
    readonly answer: { readonly chunks: readonly Uint8Array[] };
  };
  readonly fileSyncCommit: {
    readonly handed: true;
    readonly values: { readonly ops: readonly SyncOp[] };
  };
  readonly fileSyncUploadBegin: { readonly handed: true };
  readonly fileSyncUploadChunks: {
    readonly handed: true;
    readonly values: {
      readonly chunks: readonly { readonly sha256: string; readonly bytes: Uint8Array }[] };
  };
  readonly fileSyncUploadAbort: { readonly handed: true };
}

/** A door's entry in `DoorTypes`, or none. */
type TypesOf<D extends string> = D extends keyof DoorTypes ? DoorTypes[D] : Record<never, never>;
/** The values the doors build into a door's fields, or none named. */
type ValuesOf<D extends string> =
  TypesOf<D> extends { readonly values: infer V extends object } ? V : object;
/** A door's request by its row's fields: as the doors build it, or a parsed body handed on. */
export type DoorRequest<D extends keyof DoorRequests> =
  TypesOf<D> extends { readonly handed: true } ? Handed<D, ValuesOf<D>> : Asked<D, ValuesOf<D>>;
/** A loose door's parameters after its binding: its module's own, else one request it reads. */
export type DoorLoose<D extends string> =
  TypesOf<D> extends { readonly loose: infer P extends readonly unknown[] } ? P
    : [request: unknown];
/**
 * `answer`'s fields, each `narrow`'s where it names one, arm by arm of either (and null where
 * `answer` is).
 */
type Narrowed<Answer, Narrow> = Answer extends object
  ? Narrow extends object ? Omit<Answer, keyof Narrow> & Narrow : never : Answer;
/**
 * What the doors read of a door's answer: its row's (`DoorAnswers`, but its
 * `unread` fields), each field `DoorTypes` `answer` narrows its narrowing (`owner-doors.ts`
 * holds each to its row's type); an object they pass on where the row declares none.
 */
export type DoorAnswer<D extends string> = D extends keyof DoorAnswers
  ? TypesOf<D> extends { readonly answer: infer A } ? Narrowed<DoorAnswers[D], A> : DoorAnswers[D]
  : object;
/**
 * What the owner answers of a door: what the doors read (`DoorAnswer`), and the fields its row
 * marks `unread`, which the doors' version before or a spoken runner protocol may still read
 * (`owner/src/owner-do.ts`).
 */
export type OwnerAnswer<D extends string> = DoorAnswer<D>
  & (D extends keyof UnreadAnswers ? UnreadAnswers[D] | null : unknown);
/** A value as JSON carries it: its bytes as base64. */
type AsJson<T> = T extends Uint8Array ? string
  : T extends readonly (infer Item)[] ? readonly AsJson<Item>[]
    : T extends object ? keyof T extends never ? T : { readonly [K in keyof T]: AsJson<T[K]> }
      : T;
/**
 * A door's answer as the doors relay it to the sandbox, which its runner reads
 * (`sandbox/runner/sync.ts`, `decide.ts`): what the owner answers (`OwnerAnswer`), whole, as
 * JSON (`doors/src/egress.ts`: the sync doors by `SYNC_DOORS`, and the decide door, which
 * hands an `http` hook's `hook` as the hook's own words, an arm the runner never asks for).
 */
export type RelayedAnswer<D extends string> = AsJson<OwnerAnswer<D>>;
