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
 * but its doors: the door table (`contract/doors.json`) defines them on its prototype when
 * the class loads, one a row, none a name RPC reserves. The object is their router and holds
 * their state: each door's method hands its row's runner (`#call`: the bind, the account, the
 * scope and the fields, `door.ts`) the handler in the module its row names
 * (`<module>Doors.<door>`), which the runner hands the object's state (`OwnerState`), as what
 * the module declares its doors reach (`<Module>DoorNeeds`), where it declares it. Its
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
 * Erasing the owner (`erasure.ts`, `erase.ts`; the owner's door `ownerErase`, or the
 * platform's `erasureBegin`): once it marks the owner, every door refuses
 * (`ERASING`, in the binding check every door's first unit runs) and no lane runs; it ends
 * every run, then wipes the owner whole, leaving only the erased admission row
 * (`admission.ts`). An object that finds that tombstone as it
 * starts migrates nothing and starts no Lifecycle, and every door refuses it
 * (`OWNER_ERASED`), so a stale `getByName` never makes owner state again.
 *
 * The owner's admission (`admission.ts`) is checked in the same binding check: a suspended
 * owner's every door refuses, and so do the lanes with `require_work` semantics (the
 * `wakes` lane's claim). Only the platform, as the reserved `PLATFORM_CALLER`, sets it, at
 * `admissionSet`, the one door that caller reaches but its state, tier, erasure, key and
 * restore doors, and, with an import binding while the owner is `pending`, the cutover's
 * import doors (`import.ts`).
 */
import { keyed } from "../../contract/src/checks";
import { HEX64 } from "../../contract/src/ids";
import { rehearsalRun } from "../../contract/src/import-message";
import { principal } from "../../contract/src/text";

import { Lifecycle } from "agents/lifecycle";
import { DurableObject } from "cloudflare:workers";

import {
  doorProblems, doorRow, type DoorRow, doorTable, fields, takesRequest,
} from "../../contract/src/doors";
import type { DoorMethods } from "./door-map";
import type {
  Account, Bound, Call, Door, DoorsOf, DoorUnits, Module, ObjectReads, Readable, Units,
} from "./door";
import type { RunCapability } from "../../contract/src/capabilities";

import {
  admission, type Admission, admissionDoors, DISCARDED, type DoorClass, doorRefusal,
  IMPORT_DOORS_ONLY, IMPORT_PENDING_ONLY, IMPORT_PLATFORM_ONLY, isAdmitted, OWNER_ERASED,
  ownerTier, PLATFORM_ONLY, PLATFORM_RESERVED, setTier, tombstoned, workRefusal, writeTombstone,
} from "./admission";
import {
  agentId, AUTHORITY_CHANGED, type Grant, MAIN_AGENT, may, NOT_GRANTED, OWNER, type Scope,
} from "./authority";
import { type CloseKind, OWNERS_SOCKET, Relay, relayDoors, type SocketState } from "./relay";
import { agentsAt, RUN_ENDED, Runs, runsDoors } from "./runs";
import { PostGate } from "../../contract/src/run-events";
import { TICKET_REFUSED } from "../../contract/src/socket-shape";
import { Tickets } from "./tickets";
import { Keys, keysDoors } from "./keys";
import { bindingId, Inbound, inboundDoors } from "./inbound";
import { keepTiers, keptTiers, PLATFORM_TIER_ONLY, tierDoors } from "./tier";
import type { TierName } from "../../contract/src/control";
import { Bodies } from "./bodies";
import { Collections, collectionsDoors } from "./collections";
import { Connections, connectionsDoors } from "./connections";
import type { RingAnswer } from "../../contract/src/gateway";
import { ERASING, Refusal } from "../../contract/src/refusal";
import {
  fileChanged, Files, filesDoors, type SandboxLease, type SyncCommitRequest, SYNC_FENCED,
} from "./files";
import { type OwnerStorage, WriteGate } from "./gate";
import { Grants, grantsDoors } from "./grants";
import { codemodeDoors } from "./codemode";
import {
  Ledger, ledgerDoors, outsideOrigin, registrant, RUN_NO_MAILBOX,
} from "./ledger";
import { clock } from "./clock";
import type { OwnerEnv } from "./env";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { Notices, noticesDoors } from "./notices";
import { importDoors } from "./import";
import { Erasure } from "./erase";
import {
  erasing, erasureDoors, NO_ERASURE, outsideDone, wiped,
} from "./erasure";
import { fileLimits, modelSettings, Policy, policyDoors } from "./policy";
import { Registrations, registrationsDoors } from "./registrations";
import { Schedule } from "./schedule";
import { ABILITY_TREES, AGENTS_DIR, within } from "../../contract/src/paths";
import { Launcher } from "./launch";
import { Egress, egressDoors, sandboxBinding } from "./egress";
import { exportDoors } from "./export";
import { SandboxTime, sandboxTimeDoors } from "./sandbox-time";
import type { OwnerDoors } from "../../contract/src/owner";
import { pinned, refuseRestoring, restoreDoors, restoring } from "./restore";
import { Sessions, sessionsDoors } from "./sessions";
import { Signins, signinsDoors, switchedOn } from "./signins";
import { Starter, starterDoors } from "./starter";
import { carried } from "./starter-package";
import { priced, Usage as ModelUsage, usageDoors } from "./usage";
import { migrate } from "../../contract/src/migrate";
import { Archive } from "./archive";
import { MIGRATIONS } from "./schema";
import { Backups } from "./backup";
import { OpenExports } from "./open-exports";
import { ChangeLog } from "./changelog";
import { ControlGate } from "./control-gate";
import { storageFull, storageFullRefusal } from "./storage-full";
import { Doorbell, type WakeSender } from "./wakes";

/** Why a restore's wipe resets the instance (`ObjectReads.reset`). */
const RESET = "the object was wiped for a restore; its next call starts it afresh";
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
 * The import's binding exactly as the binder makes it (`bindImport`): the platform's reserved
 * caller, no outside client, the bundle's digest and the control record's tiers; else refused.
 */
function importBinding(binding: Binding): void {
  if (!keyed(binding, ["owner", "caller", "origin", "import", "tiers"])
      || binding.caller !== PLATFORM_CALLER || binding.origin !== null
      || !keyed(binding.import, ["bundle"])
      || typeof binding.import.bundle !== "string" || !HEX64.test(binding.import.bundle)) {
    throw new Refusal("refused", IMPORT_PLATFORM_ONLY);
  }
}

/** A bound call with no account of its own (a sandbox's, the platform's, or none). */
function bound(owner: string, caller: string): Bound {
  return { account: { owner, caller, origin: null, grant: null, run: null }, readable: null,
           lease: null, route: null, erased: false };
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
 * A run's own MCP door never converses: work between agents is mail (ADR-034 decision 11).
 * It may launch, schedule, read and cancel runs (the founder, 2026-11-03: every agent of an
 * owner may do what any other of the owner's agents may), held to its grant.
 */
export const RUN_STARTS_NONE = "run_starts_none: a run talks to an agent by mail, never " +
  "converse";

/**
 * The object's stores, as it wires them over its gate's units, from its env and its id: the
 * owner's policy, notices, change log, files, placed bodies, collections, grants, ledger,
 * nightly manifest (`backup.ts`) and archive (`archive.ts`). The object builds its own with
 * this, once; a test builds the same over an object's own storage to reach a store directly.
 * A file change tells the owner's other modules through these stores (`fileChanged`), so
 * every builder's tells them.
 */
export function ownerStores(units: Units, env: OwnerEnv, id: DurableObjectId) {
  const { storage, write } = units;
  const policy = new Policy(storage);
  const notices = new Notices(storage);
  const log = new ChangeLog(storage);
  // The files byte bound is the tier's storage (ADR-034 decision 8), as last handed.
  // Nothing is let go to make room at it (ADR-036): the owner deletes what it chooses.
  const files: Files = new Files(storage, env.FILES, () => fileLimits(
    policy.read().document,
    keptTiers(storage)?.[ownerTier(storage)].storage_bytes ?? null), write,
    // The one wall's refusal names the owner's tier and the next one up (`storage_full`).
    (stored, needed, held, limit) => storageFullRefusal(storageFull(
      stored, needed, held, limit, ownerTier(storage), keptTiers(storage))),
    (path, present, now, change) => fileChanged({
      ledger, policy, notices, grants, files,
      agentsAt: (epoch, most) => agentsAt(storage, epoch, most),
    }, path, present, now, change),
    // Garbage a generation kept names is never collected early.
    () => backups.protectsSince(clock()));
  const bodies = new Bodies(files, storage, write);
  // An outside writer's share of the collections is the owner's own `client_share`.
  const collections = new Collections(storage, bodies.adopt,
                                      () => fileLimits(policy.read().document, null));
  const grants = new Grants(storage, policy);
  const ledger = new Ledger(storage, grants, () => isAdmitted(storage),
                            () => collections.clock(), bodies.adopt,
                            (needed) => files.fullRefusal(needed));
  const backups = new Backups({
    storage, write: units.quiet, store: env.FILES, space: () => files.space(), log,
    owner: () => id.name ?? "",
    serves: () => storage.sql.exec("SELECT 1 FROM object_owner").toArray().length > 0,
    halted: () => wiped(storage.sql) || erasing(storage),
    pinned: () => pinned(storage),
    notice: (type, detail) => notices.add(type, detail, clock()),
  });
  const archive = new Archive({ door: write, ledger, files, bodies, notices });
  return { policy, notices, log, files, bodies, collections, grants, ledger, backups, archive };
}

/** The object's stores (`ownerStores`). */
export type Stores = ReturnType<typeof ownerStores>;

/**
 * The object's state, as its modules are handed it: its doors' handlers, each in the module
 * its row names (`contract/doors.json`), and the run launcher (`launch.ts`). Its stores; its
 * gate's units and its reads (`door.ts`); and what only the object reads or decides: the
 * owner's admission and tier (`admission.ts`, which only the object imports). Only the object
 * makes it (`#stateOf`), so a module reaches these through the object's runner. A module that
 * declares its handlers' reach (`<Module>DoorNeeds`) is handed it as that.
 */
export interface OwnerState extends DoorUnits, ObjectReads {
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
  readonly doorbell: Doorbell;
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
  /** The owner's own admission for `door`, as a binding check reads it, for internal calls. */
  admitted(door: DoorClass): void;
  /** The owner's `require_work` refusal, or null while it is admitted. */
  unadmitted(): Refusal | null;
  /** The owner's own tier. */
  tier(): TierName;
  /** The platform's setting of the owner's tier (`admission.ts`). */
  setTier(request: unknown): { tier: TierName };
  /** The owner's admission as its row holds it (`admission.ts`): a read. */
  admission(): Admission;
}

/**
 * Each module's door handlers (`<module>Doors`), by the module its rows name: `tsc` holds
 * that every module a row names is here, and that each handler of its rows is, typed by its
 * gate and handed the object's state, which holds what each module's `DoorNeeds` declares
 * (a handler's first parameter is contravariant); the class's loop finds each row's own
 * (`handlerOf`).
 */
const HANDLERS = Object.freeze({
  admission: admissionDoors, codemode: codemodeDoors, collections: collectionsDoors,
  connections: connectionsDoors, egress: egressDoors, erasure: erasureDoors,
  export: exportDoors, files: filesDoors, grants: grantsDoors, import: importDoors,
  inbound: inboundDoors, keys: keysDoors, ledger: ledgerDoors, notices: noticesDoors,
  policy: policyDoors, registrations: registrationsDoors, relay: relayDoors,
  restore: restoreDoors, runs: runsDoors, "sandbox-time": sandboxTimeDoors,
  sessions: sessionsDoors, signins: signinsDoors, starter: starterDoors, tier: tierDoors,
  usage: usageDoors,
}) satisfies { readonly [M in Module]: DoorsOf<M, OwnerState> };

/** A handler as the runner calls it, whatever its door: what its row's gate hands it. */
type Handled = (o: OwnerState, call: never) => unknown;

/** The handlers by module, as the runner reads them by a row's names. */
function modules(): Readonly<Record<string, Readonly<Record<string, Handled>>>> {
  return HANDLERS;
}

/** `row`'s handler, in the module its row names; none is a broken table (`tsc` refuses it). */
function handlerOf(row: DoorRow): Handled {
  const module = Object.hasOwn(modules(), row.handler) ? modules()[row.handler]! : {};
  if (!Object.hasOwn(module, row.door)) {
    throw new Error(`${row.door}: no handler in ${row.handler}, the module its row names`);
  }
  return module[row.door]!;
}

/** The object's doors, each defined from its row when the class loads (`#call`). */
export interface OwnerDO extends DoorMethods<typeof HANDLERS> {}

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
  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    super(ctx, env);
    this.#gate = new WriteGate(ctx.storage);
    const storage = this.#gate.storage;
    const write = this.#gate.write;
    this.#storage = storage;
    this.#state = this.#stateOf(env, storage);
    this.#tombstone = tombstoned(storage.sql);
    const admitted = () => isAdmitted(storage);
    const stores = ownerStores(this.#gate, env, ctx.id);
    this.#policy = stores.policy;
    this.#notices = stores.notices;
    this.#files = stores.files;
    this.#bodies = stores.bodies;
    this.#collections = stores.collections;
    this.#grants = stores.grants;
    this.#ledger = stores.ledger;
    this.#backups = stores.backups;
    this.#archive = stores.archive;
    this.#exports = new OpenExports(storage);
    this.#sessions = new Sessions(storage);
    this.#registrations = new Registrations(storage, this.#grants);
    this.#starter = new Starter({
      storage, files: this.#files, write, admitted,
      // The files' byte quota is the tier's storage: until a door hands the numbers, no write
      // lands, so a seed waits for them rather than failing (the first door's call keeps them).
      ready: () => keptTiers(storage) !== null,
      notice: (type, detail) => this.#notices.add(type, detail, clock()),
    }, carried());
    const owner = () => {
      const named = ctx.id.name;
      if (named === undefined) {
        throw new Error("this object is not named by an owner");
      }
      return named;
    };
    this.#ownerName = owner;
    this.#sender = new Doorbell({
      ledger: this.#ledger, live: (client) => this.#grants.live(client), write,
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
      reseal: (subscription, version, blob) =>
        env.GATEWAY.resealDoorbell(owner(), subscription, version, blob),
    });
    this.#connections = new Connections(storage, write, env.GATEWAY, owner, this.#notices);
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
      live: (conversation) => this.#door(() => this.#signins.owned(conversation) ? [conversation]
        : this.#runs.liveIn(conversation)),
    });
    // The lanes of the one schedule, each its module's own (`Lane`): its key is its job's
    // durable id. Each pass is handed its wall-clock end (`until`) and passes it on, so no
    // lane outruns its hung timeout; the passes write through the object's gate. There is no
    // global tick; each owner's object schedules only itself.
    this.#schedule = new Schedule({
      wakes: this.#sender, files: this.#files, connections: this.#connections,
      runs: this.#launcher, manifest: this.#backups, sandbox: this.#sandboxTime,
      archive: this.#archive, starter: this.#starter,
    }, write, () => wiped(storage.sql) || erasing(storage) || restoring(storage));
    const lifecycle = new Lifecycle<OwnerEnv>(this);
    this.#erasure = new Erasure({
      storage, write, lifecycle,
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
      wipe: () => this.#gate.wipe(writeTombstone),
      bare: () => this.#gate.wipe(() => undefined),
    });
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
      get doorbell() { return object.#sender; },
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
      door: <T>(act: () => T) => object.#door(act),
      quiet: <T>(act: () => T) => object.#quiet(act),
      synced: <T>(act: () => T) => object.#synced(act),
      filing: <T>(act: () => T) => object.#filing(act),
      sync: () => object.#schedule.sync(),
      control: () => object.#control(),
      admitted: (door: DoorClass) => object.#admitted(door),
      unadmitted: () => workRefusal(admission(storage)),
      tier: () => ownerTier(storage),
      setTier: (request: unknown) => setTier(storage, request),
      gone: () => object.#tombstone || object.#erasure.erased || erasing(storage),
      discard: () => object.#discard(),
      reset: async () => {
        // Durable first: a reset takes back what is not yet (`facet-probe.md`, Q8).
        await object.#gate.durable();
        object.ctx.abort(RESET);
        throw new Error(RESET);
      },
      admission: () => admission(storage),
      accountAt: (door: string, binding: Binding) =>
        object.#within(doorRow(door), binding, undefined, ({ account }) => account),
      invoke: (door: string, binding: Binding, request: unknown) => {
        const row = doorRow(door);
        return object.#call(row, binding, request, handlerOf(row));
      },
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
    // The switched-on sign-ins, as the binder read them from the control record: kept for
    // the lane's runs (written only when they changed).
    switchedOn(this.#storage, binding.signins, clock());
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
  // Each door is a row of the contract's table (`contract/doors.json`) and its handler in
  // the module its row names (`HANDLERS`). The class defines each row's method on its
  // prototype at module load (`DoorMethods`, written from the table), and each hands its row
  // and handler to the one runner; the steps are these, from the row alone.

  static {
    // The table whole, as a hand method once held its own row at each call (`doorRow`): a
    // door a name RPC reserves (`rpcReserved`) among what it refuses.
    const problems = doorProblems(doorTable());
    if (problems.length > 0) {
      throw new Error(`the door table is broken: ${problems.join("; ")}`);
    }
    // Every handler is a row's, of the module it is in: a handler no row names is refused.
    const named = new Map(doorTable().map((row) => [row.door, row.handler]));
    for (const [module, handlers] of Object.entries(modules())) {
      const stray = Object.keys(handlers).filter((door) => named.get(door) !== module);
      if (stray.length > 0) {
        throw new Error(`${module}: handlers of no row of it (${stray.join(", ")})`);
      }
    }
    for (const row of doorTable()) {
      // A name RPC reserves is refused with the table (`rpcReserved`): the runtime would
      // refuse some only at a call. One the class or its chain has (`fetch`, `toString`)
      // never becomes a door either, or the door would hide it.
      if (row.door in this.prototype) {
        throw new Error(`${row.door}: the class's own name, or its chain's, is no door`);
      }
      const handler = handlerOf(row);
      Object.defineProperty(this.prototype, row.door, {
        value: row.caller === "none"
          ? function (this: OwnerDO, request: unknown) {
            return this.#call(row, undefined as unknown as Binding, request, handler);
          }
          : function (this: OwnerDO, binding: Binding, request: unknown) {
            return this.#call(row, binding, request, handler);
          },
      });
    }
  }

  /**
   * A door's call: its handler handed the object's state and, by its row's gate, the call
   * checked in one unit of that gate, or the door, which its handler checks in each unit it
   * makes. A row that takes no request is handed none, whatever came.
   */
  #call(row: DoorRow, binding: Binding, request: unknown, handler: Handled): Promise<unknown> {
    // The request's type is its handler's, held to the row's fields by the runner's fields
    // step at run time, as `tsc` holds the handler to its row at `HANDLERS`.
    const asked = (takesRequest(row) ? request : undefined) as never;
    if (row.gate === "units") {
      const door: Door<never> = {
        binding, request: asked,
        check: () => this.#within(row, binding, asked, (call) => call),
        within: (inner) => this.#within(row, binding, asked, inner),
        bound: () => this.#bound(row, binding, asked).account,
        authorized: () => {
          const { account } = this.#bound(row, binding, asked);
          this.#guard(row, binding, account);
          this.#scoped(row, account, null);
        },
      };
      if (row.seeded !== true) {
        return Promise.resolve(handler(this.#state, door as never));
      }
      // A new owner's starter seed, awaited before a door touches the files or lists the
      // agents, after the caller's authority is checked in a unit: an outside agent's first
      // `fetch` of `/workspace/USER.md` or `agents_list` finds the seeded profile and `main`
      // however soon after sign-up it comes, and its first write lands beside the seeded
      // files, never before them (autoconnect_research.md §2.1).
      return this.#door(() => door.authorized()).then(() => this.#launcher.seeded())
        .then(() => handler(this.#state, door as never));
    }
    const unit = () => this.#within(row, binding, asked,
                                    (call) => handler(this.#state, call as never));
    switch (row.gate) {
      case "door":
        return this.#door(unit);
      case "quiet":
        return this.#quiet(unit);
      case "synced":
        return this.#synced(unit);
      case "filing":
        return this.#filing(unit);
    }
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
        // The platform's import, while the owner is pending (`import.ts`).
        const { owner, caller } = this.#bind(binding, "import");
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
             held: (where) => this.#scope(account, row.capability!, where),
             scope: (capability, center, agent) => this.#scope(account, capability, center, agent),
             universeOf: () => this.#universe(account) };
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
      throw new Refusal("not_granted", NOT_GRANTED);
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

  // -- THE CUTOVER'S IMPORT: the platform's, into a pending owner (`import.ts`) ------------

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

  /** The platform's gates as this object reads them (`control-gate.ts`), from `CONTROL`. */
  #control(): ControlGate {
    return new ControlGate(this.env.CONTROL);
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
    if (door !== "admission") {
      // From a restore's pin to its end, only the restore's doors (the platform's) take the
      // owner: not its own, not a run's, not the import's (whose discard would delete the
      // generation the restore reads).
      refuseRestoring(this.#storage);
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
      const keys = ["owner", "caller", "origin", "sandbox", ...(sandbox === "run" ? ["run"] : [])];
      if (!keyed(binding, keys) || binding.origin !== null || caller !== owner) {
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
    // The tier's numbers, as the binder read them from the control record (an account's,
    // an inbound event's and the import's bindings carry them): kept for the files byte
    // quota and the lane's runs, written only when they changed.
    const unread = keptTiers(this.#storage) === null;
    keepTiers(this.#storage, binding.tiers, clock());
    if (unread && keptTiers(this.#storage) !== null && this.#starter.version() === 0) {
      // The first numbers a new owner's door hands: its seed, which waited for them, is due.
      queueMicrotask(() => void this.#schedule.sync());
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
    if (lease.authority === OWNER) {
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
        throw new Refusal("not_granted", NOT_GRANTED);
      }
      const agents = this.#runs.liveAgents(lease.authority)
        .map((agent) => `${AGENTS_DIR}/${agent}`);
      return readable({ lease: lease.lease_id, scope: agents.join("\n"),
                        test: (path) => path === AGENTS_DIR
                          || agents.some((dir) => within(path, dir))
                          || ABILITY_TREES.some((tree) => within(path, tree)) });
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
      throw new Refusal("not_granted", NOT_GRANTED);
    }
    return universe;
  }

  // -- SOCKETS: the owner app's live view of its runs (A6, salvaged; `relay.ts`) ----------

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
    if (!this.#runs.owns(conversation, null) && !this.#signins.owned(conversation)) {
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

}
