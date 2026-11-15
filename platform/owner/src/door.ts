/**
 * The runner's types (`owner-do.ts`, `#call`): a door's call once its row's steps held
 * (`Call`), the door a `units` handler checks in each unit it makes (`Door`), what a call is
 * bound to (`Account`, `Bound`, `Readable`), and each handler's shape by its row (`HandlerOf`,
 * `DoorsOf`, from the generated `door-map.ts`). A handler module declares what its handlers
 * reach as its `<Module>DoorNeeds`: its own store, the other modules' methods it calls, and
 * the object's units and reads it calls (`DoorUnits`, `ObjectReads`). `tsc` holds the parts
 * the object hands every handler to each module's at its handler map (`HANDLERS`), since a
 * handler's first parameter is contravariant. No state flows through this module.
 */
import type { RunCapability } from "../../contract/src/capabilities";
import type { Binding, Origin } from "../../contract/src/binding";
import type { Grant, Stamped } from "./authority";
import type { ControlGate } from "./control-gate";
import type { DoorRows } from "./door-map";
import type { OwnerEnv } from "./env";
import type { SandboxLease } from "./files";
import type { OwnerStorage, WriteGate } from "./gate";
import type { InboundRow } from "./inbound";

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
 * What a sync door's caller may read: its lease, the readable set's own name (null for the
 * owner's sandbox, `*` for all of the owner's files), and the test of a path.
 */
export interface Readable {
  readonly lease: string;
  readonly scope: string | null;
  readonly test: (path: string) => boolean;
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
  /** The row's capability held at `place`, the place its handler names. */
  held(place: unknown): void;
  /**
   * `capability` held where the call's account acts, as the runner's scope step holds it: as
   * `agent` (the `main` agent by default), in `center` (by default, where a scoped client's
   * one enabled agent is registered).
   */
  scope(capability: RunCapability, center?: unknown, agent?: string): void;
  /**
   * Where the call's scoped client acts: the command center its one enabled agent is
   * registered in, refused when it is not one; null for a full grant's or the owner's. Read
   * when asked, so only a call that needs it is refused.
   */
  universeOf(): string | null;
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

/** What the stores are built over: the object's write gate, its storage view and its units. */
export type Units = Pick<WriteGate, "storage" | "write" | "quiet">;

/** The object's units of its write gate, as a handler calls them (`gate.ts`). */
export interface DoorUnits {
  /** A synchronous body as one unit of the write gate. */
  door<T>(act: () => T): Promise<T>;
  /** A unit that marks nothing changed (`gate.ts`). */
  quiet<T>(act: () => T): Promise<T>;
  /** A unit, then the one schedule synced from the rows it changed. */
  synced<T>(act: () => T): Promise<T>;
  /** A changing file door's unit, then the schedule synced whatever came of it. */
  filing<T>(act: () => T): Promise<T>;
  /** The one schedule synced from the rows units changed. */
  sync(): Promise<void>;
}

/** What only the object reads or decides, as a handler calls it. */
export interface ObjectReads {
  readonly env: OwnerEnv;
  readonly storage: OwnerStorage;
  /** The owner this object is named by; refused when it has none. */
  owner(): string;
  /** The platform's gates as this object reads them (`control-gate.ts`). */
  control(): ControlGate;
  /** Whether the owner is erased or being erased: its doors with no binding do nothing. */
  gone(): boolean;
  /**
   * The cutover's discard of the owner (`import.ts`'s `importDiscard`): every R2 object and
   * the whole database deleted, no tombstone; the namespace it deleted.
   */
  discard(): Promise<string | null>;
  /**
   * The instance reset once what it wrote is durable, as a crash resets it, so the next call
   * starts a fresh one: a restore's wipe (`restore.ts`), which nothing the instance holds in
   * memory may outlive. The call that asks it ends with the reset.
   */
  reset(): Promise<never>;
  /** The account `binding` binds to at `door`'s row, as the runner binds it there. */
  accountAt(door: string, binding: Binding): Account;
  /** `door` called by its row and handler, as any call of it is (code mode). */
  invoke(door: string, binding: Binding, request: unknown): Promise<unknown>;
}

/** A door of the table, by name (`door-map.ts`, written from it). */
export type DoorName = keyof DoorRows;
/** A module that holds doors' handlers, as the rows name it. */
export type Module = DoorRows[DoorName]["module"];
/** Who a door takes (its row's `caller`): what its call's account step proved. */
export type CallerOf<D extends DoorName> = DoorRows[D]["caller"];
/** How a door's handler runs in the write gate (its row's `gate`). */
export type GateOf<D extends DoorName> = DoorRows[D]["gate"];
/** The doors whose rows name module `M`. */
type DoorsIn<M extends Module> =
  { [D in DoorName]: DoorRows[D]["module"] extends M ? D : never }[DoorName];
/**
 * A door's handler, handed `N` (its first parameter: what it may reach) and, by its row's
 * gate, the door that checks its call in each unit it makes, or the call checked in one unit
 * of that gate. Its request is its own (`never` here, so any request type fits), held to the
 * row's fields by the runner.
 */
export type HandlerOf<D extends DoorName, N> = GateOf<D> extends "units"
  ? (o: N, door: Door<never>) => Promise<unknown> : (o: N, call: Call<never>) => unknown;
/** The handlers of module `M`'s doors, each handed `N`: one per row that names `M`. */
export type DoorsOf<M extends Module, N> = { readonly [D in DoorsIn<M>]: HandlerOf<D, N> };
