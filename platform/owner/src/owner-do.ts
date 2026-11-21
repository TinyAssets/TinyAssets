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
 * session cookie (`session`). Each binding is one of the contract's kinds
 * (`contract/src/binding.ts`), of a kind its door's caller takes (`door-steps.ts`
 * `CALLER_BINDINGS`), parsed by that kind's parser before anything reads it, and a handler
 * reads it as parsed (`Call.binding`). What it decides from them, it decides itself, from its
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
 * (`files.ts`). It reaches the sandbox Worker, which picks an owner's sandboxes by the name a
 * call carries, only bound to its own name (`boundSandbox`, from `ctx.id`; the sandbox-bound
 * guard), so no call names another owner. The module keeps no mutable state at top level
 * (the module-state guard), since objects of one class can share an isolate.
 *
 * Every public method is an RPC any binding can call, so the class has no public method
 * but its doors: the door table (`contract/doors.json`, and its steps, `owner/doors.json`)
 * defines them on its prototype when the class loads, one a row, none a name RPC reserves.
 * The object is their router and holds their state: each door's method hands its row's
 * runner (`#call`: the bind, the account, the scope and the fields, `door.ts`) the handler in
 * the module its steps name (`<module>Doors.<door>`), which the runner hands the object's
 * parts (`Parts`), as what the module declares its doors reach (`<Module>DoorNeeds`), where
 * it declares it. Its runs are launched and delivered by `launch.ts`. Its timed work is the
 * one schedule's (`schedule.ts`): lanes on the Agents SDK Lifecycle job queue, which owns the
 * object's single physical alarm, beside the erasure's job. The object is a plain Durable
 * Object that holds a Lifecycle, never the SDK's `Agent`, and installs none of Lifecycle's
 * runtime handlers: `alarm` hands the runtime's alarm to it, and the owner app's sockets
 * (`relay.ts`) are the SDK's `WebSockets` capability on the same Lifecycle, reached by the
 * object's `fetch`, which accepts only the binder's upgrade (a Workers RPC cannot carry a
 * socket), and by the three `webSocket*` reserved methods, each handed to Lifecycle's
 * method of the same name. No agent loop runs here: agents run on unmodified engines
 * (ADR-034), in the owner's sandbox or on their vendor's platform, and no package's code
 * runs in this object.
 *
 * Claiming and settling wakes is the `wakes` lane's own, in process, with the doorbell's
 * {@link WakeSender} (`Doorbell`: each vendor's wake from its `wake-kinds.json` record, after
 * the kill switch in `CONTROL`), to the callback an agent's client stores at
 * `wakeSubscribe` or the owner at `agentDoorbell`; file housekeeping (expired uploads and
 * temporary directories, history prunes, garbage chunk deletes) is the `files` lane's. No door
 * reaches either.
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
 * The run doors (`agentSubmit`, `agentRun`, `agentStatus`, `agentCancel`) start,
 * read and cancel runs (`runs.ts`): an engine's run in the owner's sandbox, admitted here in
 * one unit against the owner's admission, the agent, the tier's simultaneous runs, the
 * owner's own limits and the initiator's stamp, its source chosen, its row, grant and lease
 * written, then started through the `SANDBOX` service (`sandbox.ts`). A run's engine
 * calls back on the egress host: its MCP door, every door held to its run grant's scopes
 * (a run binding, `#runAccount`), and its event door (`runEvents`), whose frames the object
 * reads by the engine's record and relays to the owner's app sockets subscribed to that
 * conversation.
 *
 * A main agent spends few calls: `inboxDigest` and `boardDigest` answer what
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
 * (`admission.ts`). An object that finds that tombstone as it starts migrates nothing and starts no
 * Lifecycle, and every door refuses it (`OWNER_ERASED`), so a stale `getByName` never makes owner
 * state again.
 *
 * The owner's admission (`admission.ts`) is checked in the same binding check: a suspended
 * owner's every door refuses, and so do the lanes with `require_work` semantics (the
 * `wakes` lane's claim). Only the platform, as the reserved `PLATFORM_CALLER`, sets it, at
 * `admissionSet`, the one door that caller reaches but its state, tier, erasure, key and
 * restore doors, and, with an import binding while the owner is `pending`, the cutover's
 * import doors (`import.ts`).
 */
import { rehearsalRun } from "../../contract/src/import-message";

import { Lifecycle } from "agents/lifecycle";
import { DurableObject } from "cloudflare:workers";

import { fields, takesRequest } from "../../contract/src/doors";
import type { DoorMethods } from "./door-map";
import {
  type Binds, CALLER_BINDINGS, type CallerKind, type OwnerRow, ownerRow, ownerRows, type TakenBy,
} from "./door-steps";
import {
  type Account, type Bound, type Call, type Door, type DoorsOf, type DoorUnits, type ImportLine,
  type LiveSession, type Module, namedOwner, type ObjectReads, type OwnerSelf, type PlatformLine,
  type Readable, type Units,
} from "./door";
import { type Lease, mintProof } from "./proof-mint";
import type { RunCapability } from "../../contract/src/capabilities";

import {
  admissionDoors, DISCARDED, type DoorClass, doorRefusal, IMPORT_DOORS_ONLY,
  IMPORT_PENDING_ONLY, IMPORT_PLATFORM_ONLY, OWNER_ERASED, OwnerAdmission, PLATFORM_ONLY,
  PLATFORM_RESERVED, PLATFORM_TIER_ONLY, tombstoned, writeTombstone,
} from "./admission";
import { AUTHORITY_CHANGED, type Grant, MAIN_AGENT, may, NOT_GRANTED, OWNER } from "./authority";
import { Actions, actionsDoors, decidedActor } from "./actions";
import type { Condition } from "../../contract/src/acts";
import { agentId } from "../../contract/src/text";
import { type CloseKind, OWNERS_SOCKET, Relay, relayDoors, type SocketState } from "./relay";
import { agentsAt, RUN_ENDED, Runs, runsDoors } from "./runs";
import { PostGate } from "../../contract/src/run-events";
import { TICKET_REFUSED } from "../../contract/src/socket-shape";
import { Tickets } from "./tickets";
import { Keys, keysDoors } from "./keys";
import { bindingId, Inbound, inboundDoors } from "./inbound";
import { Bodies } from "./bodies";
import { Collections, collectionsDoors } from "./collections";
import { Connections, connectionsDoors } from "./connections";
import type { RingAnswer } from "../../contract/src/gateway";
import { ERASING, Refusal } from "../../contract/src/refusal";
import {
  type FileReaction, Files, filesDoors, type SandboxLease, type SyncCommitRequest, SYNC_FENCED,
  syncIgnoreReaction,
} from "./files";
import { type OwnerStorage, WriteGate } from "./gate";
import { Grants, grantsDoors } from "./grants";
import { codemodeDoors } from "./codemode";
import {
  actorOf, Ledger, ledgerDoors, registrant, RUN_NO_MAILBOX,
} from "./ledger";
import { clock } from "./clock";
import type { OwnerEnv } from "./env";
import {
  type Binding, BINDING_PARSERS, type BindingKind, bindingKind, type BindingOf, type EgressBinding,
  PLATFORM_CALLER, type RunBinding, type SyncBinding,
} from "../../contract/src/binding";
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
import { Egress, egressDoors } from "./egress";
import { exportDoors } from "./export";
import { boundSandbox } from "./sandbox";
import { SandboxTime, sandboxTimeDoors } from "./sandbox-time";
import type { OwnerDoors } from "../../contract/src/owner-doors";
import { pinned, refuseRestoring, restoreDoors, restoreNamespace, restoring } from "./restore";
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
import { ControlGate, objectKeeper } from "./control-gate";
import { storageFull, storageFullRefusal } from "./storage-full";
import { Doorbell, type WakeSender } from "./wakes";

/** Why a restore's wipe resets the instance (`ObjectReads.reset`). */
const RESET = "the object was wiped for a restore; its next call starts it afresh";
/** An inbound event's binding at any door but `inboundAccept`, and the reverse. */
export const INBOUND_ONLY = "an inbound event's route reaches the inbound door alone";
export const INBOUND_DOOR = "the inbound door takes an inbound event's route alone";

/**
 * Who else an account door serves. `visitors`: another owner may call, as itself (the
 * collection doors). `registering`: an outside origin may come with the owner's session
 * (the owner's browser registering an agent for that client).
 */
interface Serves {
  readonly visitors?: boolean;
  readonly registering?: boolean;
}

/** A bound binding the bind step held: its kind, and the binding as that kind's parser read it. */
type Held = { [K in TakenBy<Binds>]: { readonly kind: K; readonly binding: BindingOf<K> } }[
  TakenBy<Binds>];
/** A binding a door of caller `K` holds: of a kind `K` takes. */
type HeldBy<K extends Binds> = Extract<Held, { kind: TakenBy<K> }>;
/** What the bind step reads of a door's row: its class, and its act at a reserved door. */
type Taking = Pick<OwnerRow, "class" | "action">;
/** A socket's attach and check, as a `read` door of the owner's account (`#socketAdmit`). */
const SOCKET: Taking = Object.freeze({ class: "read", action: null });

/** The kinds whose binding is a sandbox's token: a lease's, and so only an opened one's. */
const TOKENS: readonly BindingKind[] = Object.freeze(["egress", "sync", "run"]);

/**
 * The refusal of a binding of kind `presented` at a door of a caller that takes `takes`, but
 * not it: an import's, the platform's and an inbound route's binding each reach their own
 * doors alone, and those doors take no other; a sandbox door (one that takes the egress or
 * the sync token, or a run alone) takes no other; then the platform's doors take only the
 * platform, and the owner's account no sandbox token.
 */
function notTaken(presented: BindingKind, takes: readonly BindingKind[], tier: boolean): Refusal {
  if (presented === "none") {
    return new Refusal("invalid", "invalid owner principal");
  }
  if (presented === "import" || takes.includes("import")) {
    return new Refusal("refused",
                       presented === "import" ? IMPORT_DOORS_ONLY : IMPORT_PLATFORM_ONLY);
  }
  if (presented === "reserved") {
    return new Refusal("refused", PLATFORM_RESERVED);
  }
  if (presented === "inbound" || takes.includes("inbound")) {
    return new Refusal("refused", presented === "inbound" ? INBOUND_ONLY : INBOUND_DOOR);
  }
  if (takes.some((kind) => kind === "egress" || kind === "sync"
                 || (kind === "run" && !takes.includes("account")))) {
    return new Refusal("refused", SANDBOX_ONLY);
  }
  if (takes.includes("reserved") && !TOKENS.includes(presented)) {
    return new Refusal("refused", tier ? PLATFORM_TIER_ONLY : PLATFORM_ONLY);
  }
  return new Refusal("refused", SANDBOX_DOORS);
}

/**
 * `binding` held as a door of `caller` takes it: of a kind the caller takes
 * (`CALLER_BINDINGS`), else refused (`notTaken`), and parsed by that kind's parser.
 */
function taken<K extends Binds>(binding: unknown, caller: K, row: Taking): HeldBy<K> {
  const kind = bindingKind(binding);
  const takes: readonly BindingKind[] = CALLER_BINDINGS[caller];
  if (!takes.includes(kind)) {
    throw notTaken(kind, takes, row.action === "tier");
  }
  return { kind, binding: (BINDING_PARSERS[kind] as (value: unknown) => unknown)(binding) } as
    HeldBy<K>;
}

/**
 * What each caller's bind proves once it held (`#bound`): the owner itself on its own account
 * (`#ownerOf`), the platform's reserved line, and its import line, while the owner is pending.
 * Only the runner makes them, so only those rows' calls carry them (`Call`), and only their
 * handlers can make the widenings that take them. A session row's proof is its live session,
 * made of the row `Sessions.live` found (`#within`). Each is minted per call (`mintProof`).
 */
function provedBy(caller: string, lease: Lease): object {
  switch (caller) {
    case "owner": return { self: mintProof({ proved: "owner" }, lease) as OwnerSelf };
    case "reserved": return { platform: mintProof({ proved: "reserved" }, lease) as PlatformLine };
    case "import": return { importing: mintProof({ proved: "import" }, lease) as ImportLine };
    default: return {};
  }
}

/** A call bound by `binding` to `account`, with nothing else its steps found. */
function bound(binding: Binding | undefined, account: Account): Bound {
  return { binding, account, readable: null, lease: null, route: null, erased: false };
}

/** The account of a call with none of its own (a sandbox's, the platform's, or none). */
function unaccounted(owner: string, caller: string): Account {
  return { owner, caller, origin: null, grant: null, run: null };
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
 * owner's admission and tier, policy, notices, files, placed bodies, collections, grants,
 * sessions, keys, held registrations, ledger, nightly manifest (`backup.ts`, which alone holds
 * the change log), archive (`archive.ts`) and the action rules' held calls (`actions.ts`). The
 * object builds its own with this, once; a test builds the same over an object's own storage
 * to reach a store directly. A file change tells the owner's
 * other modules through these stores (`FileReaction`), so every builder's tells them.
 */
export function ownerStores(units: Units, env: OwnerEnv, id: DurableObjectId) {
  const { storage, write } = units;
  const admission = new OwnerAdmission(storage);
  const policy = new Policy(storage);
  const notices = new Notices(storage);
  const log = new ChangeLog(storage);
  // The files byte bound is the tier's storage (ADR-034 decision 8), as last handed.
  // Nothing is let go to make room at it (ADR-036): the owner deletes what it chooses.
  const files: Files = new Files(storage, env.FILES, () => fileLimits(
    policy.read().document,
    admission.tiers()?.[admission.tier()].storage_bytes ?? null), write,
    // The one wall's refusal names the owner's tier and the next one up (`storage_full`).
    (stored, needed, held, limit) => storageFullRefusal(storageFull(
      stored, needed, held, limit, admission.tier(), admission.tiers())),
    // What follows a file change: each agent's ledger identity, the sync's ignore list's notice.
    () => reactions,
    // Garbage a generation kept names is never collected early.
    () => backups.protectsSince(clock()));
  const bodies = new Bodies(files, storage, write);
  // An outside writer's share of the collections is the owner's own `client_share`.
  const collections = new Collections(storage, bodies.adopt,
                                      () => fileLimits(policy.read().document, null));
  const grants = new Grants(storage, policy);
  const ledger = new Ledger(storage, grants, () => admission.admitted(),
                            () => collections.clock(), bodies.adopt,
                            (needed) => files.fullRefusal(needed));
  const backups = new Backups({
    storage, write: units.quiet, store: env.FILES, space: () => files.space(), log,
    owner: () => namedOwner(id),
    serves: () => storage.sql.exec("SELECT 1 FROM object_owner").toArray().length > 0,
    halted: () => wiped(storage.sql) || erasing(storage),
    pinned: () => pinned(storage),
    notice: (type, detail) => notices.add(type, detail, clock()),
  });
  const archive = new Archive({ door: write, ledger, files, bodies, notices });
  const reactions: readonly FileReaction[] = Object.freeze([
    ledger.agentFiles,
    syncIgnoreReaction({ policy, notices, grants, files,
                         agentsAt: (epoch, most) => agentsAt(storage, epoch, most) }),
  ]);
  return { admission, policy, notices, files, bodies, collections, grants,
           sessions: new Sessions(storage), keys: new Keys(storage),
           registrations: new Registrations(storage, grants), ledger, backups, archive,
           actions: new Actions(storage, () => policy.read(), notices) };
}

/** The object's stores (`ownerStores`). */
export type Stores = ReturnType<typeof ownerStores>;

/** The modules the object builds beside its stores (`ownerStores`), each once. */
interface Built {
  readonly openExports: OpenExports;
  readonly sandboxTime: SandboxTime;
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
  readonly inbound: Inbound;
  readonly erasure: Erasure;
  /** The runners' events posts held at once (`EVENTS_IN_FLIGHT`). */
  readonly posts: PostGate;
}

/**
 * The object's parts, as its runner hands every handler them (`#parts`): its stores, the
 * modules it builds, its gate's units and its reads (`door.ts`), one plain frozen record made
 * once the object is built. No module names this type: each declares what its handlers reach
 * (`<Module>DoorNeeds`), and `HANDLERS` holds the parts to every one.
 */
type Parts = Readonly<Stores & Built & DoorUnits & ObjectReads>;

/**
 * Each module's door handlers (`<module>Doors`), by the module its rows name: `tsc` holds
 * that every module a row names is here, and that each handler of its rows is, typed by its
 * gate and handed the object's parts, which hold what each module's `DoorNeeds` declares
 * (a handler's first parameter is contravariant); the class's loop finds each row's own
 * (`handlerOf`).
 */
const HANDLERS = Object.freeze({
  actions: actionsDoors, admission: admissionDoors, codemode: codemodeDoors,
  collections: collectionsDoors, connections: connectionsDoors, egress: egressDoors,
  erasure: erasureDoors,
  export: exportDoors, files: filesDoors, grants: grantsDoors, import: importDoors,
  inbound: inboundDoors, keys: keysDoors, ledger: ledgerDoors, notices: noticesDoors,
  policy: policyDoors, registrations: registrationsDoors, relay: relayDoors,
  restore: restoreDoors, runs: runsDoors, "sandbox-time": sandboxTimeDoors,
  sessions: sessionsDoors, signins: signinsDoors, starter: starterDoors, usage: usageDoors,
}) satisfies { readonly [M in Module]: DoorsOf<M, Parts> };

/** A handler as the runner calls it, whatever its door: what its row's gate hands it. */
type Handled = (o: Parts, call: never) => unknown;

/** The handlers by module, as the runner reads them by a row's names. */
function modules(): Readonly<Record<string, Readonly<Record<string, Handled>>>> {
  return HANDLERS;
}

/** `row`'s handler, in the module its row names; none is a broken table (`tsc` refuses it). */
function handlerOf(row: OwnerRow): Handled {
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
  /** The owner's admission and tier, and the tier numbers kept (`admission.ts`). */
  readonly #admission: OwnerAdmission;
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
  /** The owner's action rules decided, and the calls they hold (`actions.ts`). */
  readonly #actions: Actions;
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
  /** The object's parts, as the runner hands its handlers them (`Parts`). */
  readonly #parts: Parts;
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
    this.#tombstone = tombstoned(storage.sql);
    const stores = ownerStores(this.#gate, env, ctx.id);
    this.#admission = stores.admission;
    const admitted = () => this.#admission.admitted();
    this.#policy = stores.policy;
    this.#notices = stores.notices;
    this.#files = stores.files;
    this.#bodies = stores.bodies;
    this.#collections = stores.collections;
    this.#grants = stores.grants;
    this.#ledger = stores.ledger;
    this.#backups = stores.backups;
    this.#archive = stores.archive;
    this.#sessions = stores.sessions;
    this.#keys = stores.keys;
    this.#registrations = stores.registrations;
    this.#actions = stores.actions;
    this.#exports = new OpenExports(storage);
    this.#starter = new Starter({
      storage, files: this.#files, write, admitted,
      // The files' byte quota is the tier's storage: until a door hands the numbers, no write
      // lands, so a seed waits for them rather than failing (the first door's call keeps them).
      ready: () => this.#admission.tiers() !== null,
      notice: (type, detail) => this.#notices.add(type, detail, clock()),
    }, carried());
    const owner = () => namedOwner(ctx.id);
    this.#ownerName = owner;
    const sandbox = boundSandbox(env.SANDBOX, ctx.id);
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
      tierRuns: () => this.#admission.tiers()?.[this.#admission.tier()].runs ?? null,
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
    this.#inbound = new Inbound(storage);
    this.#relay = new Relay({
      admit: (binding, conversation, ticket) =>
        this.#door(() => this.#socketAdmit(binding, conversation, ticket)),
      check: (state) => this.#door(() => this.#socketCheck(state)),
      // A sign-in's socket gets its own tail on attach, as a conversation's gets its runs'.
      live: (conversation) => this.#door(() => this.#signins.owned(conversation) ? [conversation]
        : this.#runs.liveIn(conversation)),
    });
    this.#launcher = new Launcher({
      env, storage, owner: () => this.#ownerName(), sandbox, control: () => this.#control(),
      door: (act) => this.#door(act), sync: () => this.#schedule.sync(),
      admission: this.#admission, bodies: this.#bodies, connections: this.#connections,
      egress: this.#egress, files: this.#files, grants: this.#grants, ledger: this.#ledger,
      notices: this.#notices, policy: this.#policy, relay: this.#relay, signins: this.#signins,
      starter: this.#starter, usage: this.#usage,
    });
    this.#runs = this.#launcher.runs;
    this.#sandboxTime = new SandboxTime(storage, this.#runs.meter(), {
      read: (after) => sandbox.meter({ after }),
      write,
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
      // The namespace's mark, at the erasure's start: the copy back's record of it (no put
      // begins under the owner's mark, so the namespace it reads is the one the delete takes).
      // A wipe for a restore empties the object, and its record names the namespace then.
      mark: async () => {
        const ns = this.#files.storedNamespace() ?? restoreNamespace(storage);
        if (ns !== null) {
          await this.#files.markNamespace(ns);
        }
      },
      // Every sandbox of the owner killed, paused ones included, and the sandbox Worker's
      // state of them deleted.
      sandboxes: async () => {
        await sandbox.erase();
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
        const ns = this.#files.storedNamespace() ?? restoreNamespace(storage);
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
    this.#parts = Object.freeze({
      ...stores, openExports: this.#exports, sandboxTime: this.#sandboxTime,
      starter: this.#starter, connections: this.#connections, doorbell: this.#sender,
      usage: this.#usage, egress: this.#egress, runs: this.#runs, launcher: this.#launcher,
      relay: this.#relay, signins: this.#signins, tickets: this.#tickets,
      inbound: this.#inbound, erasure: this.#erasure, posts: this.#posts,
      env, storage, owner: () => this.#ownerName(), sandbox,
      door: <T>(act: () => T) => this.#door(act),
      quiet: <T>(act: () => T) => this.#quiet(act),
      synced: <T>(act: () => T) => this.#synced(act),
      filing: <T>(act: () => T) => this.#filing(act),
      sync: () => this.#schedule.sync(),
      control: () => this.#control(),
      gone: () => this.#tombstone || this.#erasure.erased || erasing(storage),
      discard: () => this.#discard(),
      reset: async () => {
        // Durable first: a reset takes back what is not yet (`facet-probe.md`, Q8).
        await this.#gate.durable();
        this.ctx.abort(RESET);
        throw new Error(RESET);
      },
      accountAt: (door: string, binding: Binding) =>
        this.#within(ownerRow(door), binding, undefined, { live: false },
                     ({ account }) => account),
      invoke: (door: string, binding: Binding, request: unknown) => {
        const row = ownerRow(door);
        return this.#leased(row, binding, request, handlerOf(row));
      },
    });
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
   * verified outside client, which is admitted here against its grant before any
   * door acts. A refusal here leaves no receipt: the call never reached the account.
   */
  #account(held: HeldBy<"account">, serves: Serves = {}): Account {
    if (held.kind === "run") {
      return this.#runAccount(held.binding);
    }
    const binding = held.binding;
    const { owner, caller, origin } = binding;
    // The switched-on sign-ins, as the binder read them from the control record: kept for
    // the lane's runs (written only when they changed).
    switchedOn(this.#storage, binding.signins, clock());
    if (caller !== owner && !(serves.visitors === true && origin === null)) {
      throw new Refusal("refused", "the account answers its owner's own callers only");
    }
    const signedIn = binding.authenticated_at ?? null;
    if (origin === null) {
      return { owner, caller, origin, grant: null, run: null };
    }
    // The owner's cookie rides only the owner's own browser requests; an
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
    if (vendor !== null && source.kind !== "mcp") {
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
  #runAccount(binding: RunBinding): Account {
    const { owner, caller, sandbox } = binding;
    const lease = this.#egress.lease(sandbox, clock());
    // The run's own secret, as its path carried it: a run id alone authorizes nothing.
    const grant = lease === null ? null : this.#runs.grantFor(binding.run, sandbox.secret, lease);
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
          this.#launcher.endClient(grant.stamp.client, clock());
          throw new Refusal("authority_changed", AUTHORITY_CHANGED);
        }
        throw error;
      }
    }
    return { owner, caller, origin: grant.origin, grant: held,
             run: { id: grant.run, scopes: grant.scopes, agent: grant.agent,
                    stamp: grant.stamp, conversation: grant.conversation } };
  }

  /** The owner itself, on its own account, never an outside client nor a run. */
  #ownerOf(held: HeldBy<"owner">, action: string): Account {
    const account = this.#account(held);
    if (account.origin !== null || account.run !== null) {
      throw new Refusal("refused", `only the owner may ${action}`);
    }
    return account;
  }

  // -- THE RUNNER: every door's bind, account, scope and fields, from its row ------------
  //
  // Each door is a row of the contract's table (`contract/doors.json`), its steps the
  // owner's (`owner/doors.json`, `door-steps.ts`), and its handler in the module its steps
  // name (`HANDLERS`). The class defines each row's method on its prototype at module load
  // (`DoorMethods`, written from the two), and each hands its row and handler to the one
  // runner; the steps are these, from the row alone.

  static {
    // Both halves whole (`ownerRows`), as a hand method once held its own row at each call:
    // a door a name RPC reserves (`rpcReserved`), and a door of either half with no match in
    // the other, among what it refuses.
    const rows = ownerRows();
    // Every handler is a row's, of the module it is in: a handler no row names is refused.
    const named = new Map(rows.map((row) => [row.door, row.handler]));
    for (const [module, handlers] of Object.entries(modules())) {
      const stray = Object.keys(handlers).filter((door) => named.get(door) !== module);
      if (stray.length > 0) {
        throw new Error(`${module}: handlers of no row of it (${stray.join(", ")})`);
      }
    }
    for (const row of rows) {
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
            return this.#leased(row, undefined, request, handler);
          }
          : function (this: OwnerDO, binding: unknown, request: unknown) {
            return this.#leased(row, binding, request, handler);
          },
      });
    }
  }

  /**
   * A door's call: its handler handed the object's parts and, by its row's gate, the call
   * checked in one unit of that gate, or the door, which its handler checks in each unit it
   * makes. A row that takes no request is handed none, whatever came.
   */
  #leased(row: OwnerRow, binding: unknown, request: unknown, handler: Handled): Promise<unknown> {
    // Each proof the call's steps mint is live while the call runs, and none once it settled.
    const lease: Lease = { live: true };
    const end = () => {
      lease.live = false;
    };
    try {
      return this.#call(row, binding, request, handler, lease).finally(end);
    } catch (error) {
      end();
      throw error;
    }
  }

  /**
   * The door's call itself, its proofs live for `lease` (`#leased`). The decide step first: an
   * agent's call of a row's act is held to the owner's rules on that act in a unit of its own
   * before the handler (`#decide`), so a call is decided once, however many units its handler
   * makes. Only a rule on the act costs the unit. A session door's caller is the owner alone,
   * and the sync's commit lands a run's files, whose hold is R5's later hold-on-sync
   * (`tasks.md`; `r5-decide.md` item 3).
   */
  #call(row: OwnerRow, binding: unknown, request: unknown, handler: Handled,
        lease: Lease): Promise<unknown> {
    // The request's type is its handler's, held to the row's fields by the runner's fields
    // step at run time, as `tsc` holds the handler to its row at `HANDLERS`.
    const asked = (takesRequest(row) ? request : undefined) as never;
    const act = row.act;
    if (act === undefined || (row.caller !== "account" && row.caller !== "visitors")
        || !this.#actions.ruled(act.name)) {
      return this.#handled(row, binding, asked, handler, lease);
    }
    return this.#door(() => this.#decide(row, act, binding, asked, lease))
      .then(() => this.#handled(row, binding, asked, handler, lease));
  }

  /**
   * The decide step: every step of the row but the handler, then, for an agent's call that is
   * the row's act (its condition held), the owner's rules on it (`actions.ts`): it returns
   * when the call may go on, and throws when a rule never lets it or holds it for the owner.
   */
  #decide(row: OwnerRow, act: NonNullable<OwnerRow["act"]>, binding: unknown, request: unknown,
          lease: Lease): void {
    const call = this.#checked(row, request, this.#bound(row, binding, request),
                               provedBy(row.caller, lease));
    const actor = decidedActor(call.account);
    if (actor !== null && (act.when === undefined || this.#when(act.when, call))) {
      this.#actions.decide(act.name, actor, { door: row.door, request }, clock());
    }
  }

  /** Whether `call` is its row's act under `condition` (`acts.json` `conditions`). */
  #when(condition: Condition, call: Call<unknown, CallerKind>): boolean {
    switch (condition) {
      case "outside_member":
        return this.#ledger.recipient(actorOf(call.account), call.r["room_id"]).outside;
      case "public_row":
        return call.r["private"] === false;
    }
  }

  /** The door's handler on its call, by its row's gate. */
  #handled(row: OwnerRow, binding: unknown, asked: never, handler: Handled,
           lease: Lease): Promise<unknown> {
    if (row.gate === "units") {
      const door: Door<never> = {
        request: asked,
        check: () => this.#within(row, binding, asked, lease, (call) => call),
        within: (inner) => this.#within(row, binding, asked, lease, inner),
        bound: () => this.#bound(row, binding, asked).account,
        authorized: () => {
          const { binding: given, account } = this.#bound(row, binding, asked);
          this.#guard(row, given, account);
          this.#scoped(row, account, null);
        },
      };
      if (row.seeded !== true) {
        return Promise.resolve(handler(this.#parts, door as never));
      }
      // A new owner's starter seed, awaited before a door touches the files or lists the
      // agents, after the caller's authority is checked in a unit: an outside agent's first
      // `fetch` of `/workspace/USER.md` or `agents_list` finds the seeded profile and `main`
      // however soon after sign-up it comes, and its first write lands beside the seeded
      // files, never before them.
      return this.#door(() => door.authorized()).then(() => this.#launcher.seeded())
        .then(() => handler(this.#parts, door as never));
    }
    const unit = () => this.#within(row, binding, asked, lease,
                                    (call) => handler(this.#parts, call as never));
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
   * session, checked against the object's own session row, and, when the row
   * names what only the owner itself may do in it, never an outside client's call. A
   * refusal after its account is bound leaves a refusal receipt of what was asked (the
   * row's stand-in for it, where it names one), written once the refused change rolled back.
   */
  #within<R, T>(row: OwnerRow, binding: unknown, request: R, lease: Lease,
                act: (call: Call<R, CallerKind>) => T): T {
    if (row.caller !== "session") {
      return act(this.#checked(row, request, this.#bound(row, binding, request),
                               provedBy(row.caller, lease)));
    }
    const held = this.#bind(binding, row.caller, row);
    const account = this.#account(held, { registering: row.registering === true });
    try {
      // An outside client holds no owner session, so what only the owner may do is
      // refused as that before the session is looked for.
      if (typeof row.action === "string" && account.origin !== null) {
        throw new Refusal("refused", `only the owner may ${row.action}`);
      }
      const given: Binding = held.binding;
      const hash = this.#sessions.live(given.session, clock());
      const session = mintProof({ proved: "session", hash }, lease) as LiveSession;
      return act(this.#checked(row, request, bound(held.binding, account), { session }));
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

  /** The bind and account steps of the row's caller kind (`door-steps.ts`). */
  #bound(row: OwnerRow, binding: unknown, request: unknown): Bound {
    const door = row.class as DoorClass;
    const capability = row.capability!;
    switch (row.caller) {
      case "account":
      case "session": {
        const held = this.#bind(binding, row.caller, row);
        return bound(held.binding, this.#account(held));
      }
      case "visitors": {
        const held = this.#bind(binding, row.caller, row);
        return bound(held.binding, this.#account(held, { visitors: true }));
      }
      case "owner": {
        const held = this.#bind(binding, row.caller, row);
        const account = this.#ownerOf(held, row.action!);
        if (row.live === true) {
          const given: Binding = held.binding;
          this.#sessions.live(given.session, clock());
        }
        return bound(held.binding, account);
      }
      case "lease": {
        const held = this.#bind(binding, row.caller, row);
        return held.kind === "sync"
          ? this.#syncer(held.binding, door, capability, request as SyncCommitRequest)
          : bound(held.binding, this.#ownerOf(held, row.action!));
      }
      case "sync":
        return this.#syncer(this.#bind(binding, row.caller, row).binding, door, capability,
                            request as { lease_id?: unknown; epoch?: unknown });
      case "reader":
      case "changes": {
        // A run's own call carries its egress token as its sandbox: a reader's caller too.
        const held = this.#bind(binding, row.caller, row);
        if (held.kind === "sync") {
          return this.#syncer(held.binding, door, capability, null);
        }
        const account = this.#account(held);
        this.#scope(account, capability);
        return bound(held.binding, account);
      }
      case "egress": {
        const { binding: given } = this.#bind(binding, row.caller, row);
        return bound(given, unaccounted(given.owner, given.caller));
      }
      case "runner":
      case "run": {
        const { binding: given } = this.#bind(binding, row.caller, row);
        const lease = this.#runner(given);
        if (lease === null) {
          throw row.closed === "egress_lease_closed"
            ? new Refusal("refused", "egress_lease_closed") : new Refusal("run_ended", RUN_ENDED);
        }
        return { ...bound(given, unaccounted(given.owner, given.caller)), lease };
      }
      case "inbound": {
        const { binding: given } = this.#bind(binding, row.caller, row);
        const route = given.inbound;
        return { ...bound(given, unaccounted(given.owner, given.owner)),
                 route: this.#inbound.current(bindingId(route.binding), route.revision) };
      }
      case "reserved":
      case "import": {
        const { binding: given } = this.#bind(binding, row.caller, row);
        return bound(given, unaccounted(given.owner, given.caller));
      }
      case "erasure": {
        // An owner wiped once its outside record was confirmed: its platform doors answer so.
        if ((this.#tombstone || this.#erasure.erased) && bindingKind(binding) === "reserved") {
          const { binding: given } = taken(binding, row.caller, row);
          return { ...bound(given, unaccounted("", PLATFORM_CALLER)), erased: true };
        }
        const { binding: given } = this.#bind(binding, row.caller, row);
        if (!erasing(this.#storage)) {
          throw new Refusal("refused", NO_ERASURE);
        }
        return bound(given, unaccounted(given.owner, given.caller));
      }
      case "discard": {
        // The import's discard: a pending owner, or a rehearsal's in any state; an instance
        // that discarded already answers it again (its database is gone, and so its state).
        const { binding: given } = this.#discarded !== null ? taken(binding, row.caller, row)
          : this.#bind(binding, row.caller, row);
        return bound(given, unaccounted(given.owner, given.caller));
      }
      case "none":
        return bound(BINDING_PARSERS.none(binding), unaccounted("", ""));
    }
  }

  /** The guard, scope and fields steps of a bound call, in the row's order; its proof kept. */
  #checked<R>(row: OwnerRow, request: R, held: Bound, proof: object): Call<R, CallerKind> {
    const { binding, account } = held;
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
    return { ...held, ...proof, request, r, universe: place.universe, agent: place.agent,
             held: (where) => this.#scope(account, row.capability!, where),
             scope: (capability, center, agent) => this.#scope(account, capability, center, agent),
             universeOf: () => this.#universe(account) };
  }

  /** A row's guard: a check of its caller after the account (`door-steps.ts`). */
  #guard(row: OwnerRow, binding: Binding | undefined, account: Account): void {
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
        if (registrant(binding?.source, account.origin).kind !== "mcp") {
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
  #scoped(row: OwnerRow, account: Account, r: Record<string, unknown> | null):
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
  #fields(row: OwnerRow, request: unknown, account: Account): Record<string, unknown> {
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
   * signed into (`rehearsalName`), so it has no source connected and no run. From then on this
   * instance answers every door `busy` (`DISCARDED`) and a discard again with the namespace it
   * deleted; the runtime's next instance of the name is a fresh, `pending` object. Repeatable; the
   * namespace, read before the wipe.
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
    return new ControlGate(this.env.CONTROL,
                           objectKeeper(this.#storage, (unit) => this.#quiet(unit)));
  }

  /**
   * The bind step of a call at a door of `caller` (`row`): its binding held as a kind its
   * caller takes (`taken`), and the owner it names the one this object serves: the name it was
   * reached by, and every other one refused. The stored owner, fixed at first use, is a second
   * check of the same. An erased owner's tombstone refuses every call (`OWNER_ERASED`), an
   * erasure under way too (`ERASING`) but at its own doors, and so does an owner not admitted
   * (`require_work`), but at the platform's own doors (class `admission`), which set it. The
   * platform's reserved caller reaches no other door, and no owner is named so.
   */
  #bind<K extends Binds>(binding: unknown, caller: K, row: Taking): HeldBy<K> {
    if (this.#tombstone || this.#erasure.erased) {
      throw new Refusal("owner_erased", OWNER_ERASED);
    }
    if (this.#discarded !== null) {
      throw new Refusal("busy", DISCARDED);
    }
    // The erasure's own platform doors are the one way in while the owner is marked, and
    // only for the platform's reserved caller.
    if (erasing(this.#storage) && !(caller === "erasure" && bindingKind(binding) === "reserved")) {
      this.#erasure.resume(true);
      throw new Refusal("erasing", ERASING);
    }
    const held = taken(binding, caller, row);
    const read: Held = held;
    const given: Binding = read.binding;
    const owner = given.owner;
    if (owner === PLATFORM_CALLER) {
      throw new Refusal("refused", PLATFORM_RESERVED);
    }
    // The object's own name first: an object for another owner, or for none, says so
    // whatever its admission.
    if (this.#ownerName() !== owner) {
      throw new Refusal("refused", "this object belongs to another owner");
    }
    const door = row.class;
    if (door !== "admission") {
      // From a restore's pin to its end, only the restore's doors (the platform's) take the
      // owner: not its own, not a run's, not the import's (whose discard would delete the
      // generation the restore reads).
      refuseRestoring(this.#storage);
    }
    if (door === "import") {
      // Only an owner the platform has not admitted yet is imported into: never a live one.
      // The discard takes a rehearsal's name in any state too: no issuer's subject is one.
      if (this.#admission.row().state !== "pending"
          && !(caller === "discard" && rehearsalRun(owner) !== null)) {
        throw new Refusal("refused", IMPORT_PENDING_ONLY);
      }
    } else if (door !== "admission") {
      const refusal = doorRefusal(this.#admission.row(), door as DoorClass);
      if (refusal !== null) {
        throw refusal;
      }
    }
    const sql = this.#storage.sql;
    const stored = sql.exec<{ owner: string }>("SELECT owner FROM object_owner").toArray()[0];
    if (stored === undefined && TOKENS.includes(read.kind)) {
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
    // quota and every run, written only when they changed.
    const unread = this.#admission.tiers() === null;
    this.#admission.keep(given.tiers, clock());
    if (unread && this.#admission.tiers() !== null && this.#starter.version() === 0) {
      // The first numbers a new owner's door hands: its seed, which waited for them, is due.
      queueMicrotask(() => void this.#schedule.sync());
    }
    return held;
  }

  /**
   * A sync door's caller by the sync token: the live lease's own, and when `request` names a
   * lease, that lease at its epoch; else refused, as a lost lease's sync is fenced. What it
   * reads and writes is held to `capability` as the lease's authority holds it.
   */
  #syncer(binding: SyncBinding, door: DoorClass, capability: RunCapability,
          request: { lease_id?: unknown; epoch?: unknown } | null): Bound {
    const readable = (held: Readable): Bound =>
      ({ ...bound(binding, unaccounted(binding.owner, binding.caller)), readable: held });
    const { sandbox } = binding;
    const lease = this.#files.leaseByToken("sync", sandbox.token, clock());
    // The call came from inside the lease's own sandbox: E2B's transform set that lease's
    // egress token beside the sync token.
    const inside = this.#files.leaseByToken("egress", sandbox.egress, clock());
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
  #runner(binding: EgressBinding | RunBinding): SandboxLease | null {
    const { sandbox } = binding;
    const lease = this.#egress.lease(sandbox, clock());
    return lease !== null && sandbox.runner !== undefined
      && sandbox.runner === lease.runner_token ? lease : null;
  }

  /**
   * In a unit: a run door's caller held: a run's own call never converses, and reads or
   * cancels only holding `run_graph`; for a door that starts work, `need` held where it acts (a
   * scoped client's agent; a run's grant, and a client's run's stamped grant as well, for the agent
   * it launches); its universe.
   */
  #runDoor(account: Account, need: RunNeed, agent: string | null): string | null {
    if (account.run !== null && need === "converse") {
      throw new Refusal("run_starts_none", RUN_STARTS_NONE);
    }
    if (account.run !== null && need === null) {
      // A run reads and cancels runs and schedules only as its agent may make them: an
      // agent the owner narrowed is held to it.
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

  // -- SOCKETS: the owner app's live view of its runs (`relay.ts`) -------------------------

  /**
   * In a unit: a socket's attach, as a `read` door: the binding's account, the owner's own
   * (no outside client and no run has a socket), no session, and its ticket consumed for
   * this conversation of the owner's; refused alike otherwise (`TICKET_REFUSED`).
   */
  #socketAdmit(binding: unknown, conversation: string, hash: string): SocketState {
    const held = this.#bind(binding, "account", SOCKET);
    const account = this.#account(held);
    if (account.origin !== null || account.run !== null) {
      throw new Refusal("refused", OWNERS_SOCKET);
    }
    const given: Binding = held.binding;
    if (given.session !== undefined) {
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
      this.#account(this.#bind(state.binding, "account", SOCKET));
      return state.expires_at <= clock() ? "expired" : null;
    } catch (error) {
      if (!(error instanceof Refusal)) {
        throw error;
      }
      return error.code === "owner_erased" || error.code === "erasing" ? "erased"
        : error.code === "owner_suspended" ? "suspended" : "signedOut";
    }
  }
}
