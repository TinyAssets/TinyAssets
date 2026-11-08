/**
 * The owner object's doors as the doors Worker calls them (`decisions.md` A1; `list_doors.md`
 * R1): the one interface the doors type the owner's stub by (`DurableObjectStub<OwnerDoors>`),
 * which `OwnerDO` implements (`owner/src/owner-do.ts`), so the doors import no owner source.
 *
 * It names only the doors the doors call, each a row of the door table (`doors.json`, read by
 * `doors.ts`; the doors guard holds every name here to a row and its arity): the binding
 * first, and the request, which the row's `args` describe and the object's runner checks
 * field by field, as a JSON object. An answer is what the doors read of it, or `object`
 * where they pass it on as it came (as JSON, or as an RPC result). A tool's door is called by
 * its row's name alone (`doors/src/tools.ts` `callDoor`), so it needs no entry here.
 *
 * Beside them are the answers the egress door reads field by field (`EgressOpened`,
 * `EgressReserved`, `EgressRefusal`) and the billed event the E2B hook routes
 * (`BilledEvent`).
 */
import type { Binding, InboundAnswer } from "./binding";
import type { TierName } from "./control";
import type { ProfileName } from "./doors";
import type { Metering } from "./gateway";
import type { EgressCode } from "./refusal";
import type { Events } from "./wake-records";

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

/** A door's request: a JSON object, its fields the door row's (`args`). */
type DoorRequest = object;

/** The owner's object, as the doors reach it (`binder.ts` alone looks one up by name). */
export interface OwnerDoors extends Rpc.DurableObjectBranded {
  // The two doors that take no binding: the sandbox line's ring, and E2B's billed events.
  sandboxRung(): Promise<{ readonly waiting: number }>;
  sandboxBilled(event: BilledEvent): Promise<{ readonly counted: boolean }>;

  // Sign-in and sign-up (`authorize.ts`), the owner's session (`app.ts`), erasure (`erase.ts`).
  admissionSet(binding: Binding, request: DoorRequest): Promise<{ readonly state: string }>;
  sessionOpen(binding: Binding): Promise<{ readonly session: string }>;
  sessionExtend(binding: Binding): Promise<object>;
  sessionClose(binding: Binding): Promise<{ readonly closed: number }>;
  socketsEnd(binding: Binding): Promise<object>;
  socketTicket(binding: Binding, request: DoorRequest): Promise<object>;
  ownerErase(binding: Binding, request: DoorRequest): Promise<{ readonly erasing: true }>;
  erasureOutside(binding: Binding):
    Promise<{ readonly keys: readonly string[]; readonly routes: readonly string[] } | null>;
  erasureOutsideDone(binding: Binding): Promise<object>;

  // An MCP caller's tools (`mcp.ts`) and its MCP Events (`events.ts`).
  toolProfile(binding: Binding):
    Promise<{ readonly profile: ProfileName; readonly tools: readonly string[] }>;
  wakeEvents(binding: Binding, commandCenter?: string):
    Promise<{ readonly events: readonly Events[] }>;
  wakeSubscribe(binding: Binding, request: DoorRequest): Promise<object>;
  wakeUnsubscribe(binding: Binding, request: DoorRequest): Promise<object>;

  // API keys and outside clients (`api-keys.ts`, `revoke.ts`, `app.ts`).
  keyMint(binding: Binding, request: DoorRequest):
    Promise<{ readonly name: string; readonly created_at: number;
              readonly scopes: readonly object[] }>;
  keyList(binding: Binding): Promise<{ readonly keys: readonly { readonly key_id: string }[] }>;
  grantRevoke(binding: Binding, request: DoorRequest):
    Promise<{ readonly client: string; readonly revoked: boolean; readonly generation: number;
              readonly revision: number }>;
  grantForget(binding: Binding, request: DoorRequest): Promise<{ readonly forgotten: boolean }>;
  grantSet(binding: Binding, request: DoorRequest): Promise<object>;
  grantList(binding: Binding): Promise<object>;
  registerAgent(binding: Binding, request: DoorRequest): Promise<object>;

  // Inbound events and their bindings (`inbound.ts`).
  inboundAccept(binding: Binding, request: DoorRequest): Promise<InboundAnswer>;
  inboundCreate(binding: Binding, request: DoorRequest): Promise<InboundRoute>;
  inboundSet(binding: Binding, request: DoorRequest): Promise<InboundRoute>;
  inboundRevoke(binding: Binding, request: DoorRequest):
    Promise<{ readonly route: string | null }>;
  inboundList(binding: Binding): Promise<object>;

  // Model connections (`connect.ts`).
  connectionPrepare(binding: Binding, request: DoorRequest):
    Promise<{ readonly connection: string; readonly version: number;
              readonly flow?: { readonly state: string; readonly code_challenge: string;
                                readonly expires_at: number } }>;
  connectionCommit(binding: Binding, request: DoorRequest): Promise<object>;
  connectionRevoke(binding: Binding, request: DoorRequest): Promise<object>;
  connectionList(binding: Binding): Promise<object>;
  flowFinish(binding: Binding, request: DoorRequest):
    Promise<{ readonly connection: string; readonly version: number;
              readonly verifier: string; readonly redirect_uri: string;
              readonly source: string }>;

  // The owner surface (`app.ts`): its own routes, and each door a route names.
  tierRead(binding: Binding): Promise<{ readonly tier: TierName }>;
  fileUsage(binding: Binding): Promise<object>;
  fileRead(binding: Binding, request: DoorRequest):
    Promise<{ readonly entry: object; readonly inline?: Uint8Array }>;
  fileWrite(binding: Binding, request: DoorRequest): Promise<object>;
  ownerExport(binding: Binding, request: DoorRequest):
    Promise<{ readonly export_id: string; readonly lines: readonly string[];
              readonly next: string | null; readonly pending: boolean }>;
  exportChunks(binding: Binding, request: DoorRequest): Promise<object>;
  exportEnd(binding: Binding, request: DoorRequest): Promise<object>;
  storageUsage(binding: Binding): Promise<object>;
  agentDoorbell(binding: Binding, request: DoorRequest): Promise<object>;
  agentDoorbellEnd(binding: Binding, request: DoorRequest): Promise<object>;
  policyWakeLimits(binding: Binding, request: DoorRequest): Promise<object>;
  noticeList(binding: Binding): Promise<object>;
  policyRead(binding: Binding): Promise<object>;
  policyWrite(binding: Binding, request: DoorRequest): Promise<object>;
  fileUploadBegin(binding: Binding, request: DoorRequest): Promise<object>;
  fileUploadCommit(binding: Binding, request: DoorRequest): Promise<object>;
  fileUploadAbort(binding: Binding, request: DoorRequest): Promise<object>;
  agentSubmit(binding: Binding, request: DoorRequest): Promise<object>;
  agentRun(binding: Binding, request: DoorRequest): Promise<object>;
  agentStatus(binding: Binding, request: DoorRequest): Promise<object>;
  agentCancel(binding: Binding, request: DoorRequest): Promise<object>;
  runList(binding: Binding, request: DoorRequest): Promise<object>;
  signinStart(binding: Binding, request: DoorRequest): Promise<object>;
  signinCode(binding: Binding, request: DoorRequest): Promise<object>;
  signinCancel(binding: Binding, request: DoorRequest): Promise<object>;
  signinList(binding: Binding): Promise<object>;

  // An upload's bytes (`file-bytes.ts`).
  fileUploadTarget(binding: Binding, request: DoorRequest):
    Promise<{ readonly namespace: string; readonly key: string;
              readonly chunks: readonly { readonly sha256: string; readonly size: number }[];
              readonly pending: readonly string[] }>;
  fileUploadStored(binding: Binding, request: DoorRequest): Promise<object>;

  // The cutover's import consumer (`import.ts`), at its import binding (`bindImport`).
  importNotice(binding: Binding, request: DoorRequest): Promise<object>;
  importRows(binding: Binding, request: DoorRequest): Promise<object>;
  importFiles(binding: Binding, request: DoorRequest): Promise<object>;
  importFileBegin(binding: Binding, request: DoorRequest):
    Promise<{ readonly skipped: true }
            | { readonly skipped: false; readonly intent_id: string; readonly namespace: string;
                readonly key: string;
                readonly chunks: readonly { readonly sha256: string; readonly size: number }[];
                readonly pending: readonly string[] }>;
  importFileStored(binding: Binding, request: DoorRequest): Promise<object>;
  importFileCommit(binding: Binding, request: DoorRequest): Promise<object>;

  // The egress host (`egress.ts`): a model call, a run's events, a sign-in's, and sync.
  egressOpen(binding: Binding, request: DoorRequest): Promise<EgressOpened | EgressRefusal>;
  egressReserve(binding: Binding, request: DoorRequest): Promise<EgressReserved | EgressRefusal>;
  egressBeat(binding: Binding, request: DoorRequest): Promise<object>;
  egressSettle(binding: Binding, request: DoorRequest): Promise<object>;
  runEvents(binding: Binding, request: DoorRequest): Promise<object>;
  signinEvents(binding: Binding, request: DoorRequest): Promise<object>;
  signinReport(binding: Binding, request: DoorRequest): Promise<object>;
  fileChanges(binding: Binding, request: DoorRequest): Promise<object>;
  fileList(binding: Binding, request: DoorRequest): Promise<object>;
  fileReadChunks(binding: Binding, request: DoorRequest):
    Promise<{ readonly chunks: readonly Uint8Array[] }>;
  fileSyncCommit(binding: Binding, request: DoorRequest): Promise<object>;
  fileSyncUploadBegin(binding: Binding, request: DoorRequest): Promise<object>;
  fileSyncUploadChunks(binding: Binding, request: DoorRequest): Promise<object>;
  fileSyncUploadAbort(binding: Binding, request: DoorRequest): Promise<object>;
}

/** An inbound binding as the doors keep its route: its id, revision and route token. */
export interface InboundRoute {
  readonly binding_id: string;
  readonly revision: number;
  readonly route: string;
}
