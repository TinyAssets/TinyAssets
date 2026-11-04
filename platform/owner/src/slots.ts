/**
 * Memory bounded by structure (ADR-037): an isolate's one pool of units.
 *
 * Large data moves only as streams or 4 MiB chunks, never whole. A path takes units for
 * what it holds while it holds it, and only then: a step's units are taken before the bytes
 * it brings arrive and released once they are gone, never across a step that holds nothing
 * of them (review M1). A hold is weighted by what it holds, in units of `UNIT_BYTES`, the
 * files plane's chunk: a body window takes one, a chunk-wise edit's making step its declared
 * cost (`holds.ts`), each measured by the workerd leg. A pool counts units, never bytes, so
 * nothing is estimated at run time. Its capacity is its Worker's own, a parameter (the owner
 * object's `OWNER_POOL_UNITS`, the gateway's); the counts test holds it, with every other live
 * hold, within the isolate's 128 MB.
 *
 * **Waiting.** Units not free now are waited for, first come first served within a hold's
 * size class, up to `SLOT_WAIT_MS`, each class with its own cap of waiters. Past either the
 * call is refused `busy`, told and retryable, before it changes anything. A one-unit hold (a
 * window) never waits behind a larger one: larger holds leave `reserve` units free, which
 * only one-unit holds take, and the classes queue and are capped apart. So a co-tenant's
 * small read waits, and is refused, only on other small reads, never on edits. And a larger
 * hold is never starved by small ones: while it waits, they take only the reserve.
 *
 * **Release** holds whatever happens: `slotted` releases in `finally`, and units released
 * twice are released once.
 *
 * **Retained data** (the relay's tails) is one exact budget an isolate (`SharedBudget`),
 * split across the objects holding any, the largest dropping its oldest past it.
 *
 * It imports nothing, so any Worker uses it (the owner object, the gateway). Its one
 * module-scope holder (`isolatePool`, `isolateTails`) is one of the platform's two pinned
 * exemptions from the no-module-state rule (`module-state.test.ts`, the gateway's interposer
 * the other): it holds a count of units in use, the calls waiting, and which objects' tails
 * share the budget, never an owner's data.
 */

/** A unit: one 4 MiB chunk, the files plane's own. */
export const UNIT_BYTES = 4 * 1024 * 1024;
/**
 * How long a call waits for units before it is refused `busy`: the queue's own term. Neither
 * Claude's nor ChatGPT's connectors document how long they wait for a tool call (users
 * report about 60 s to 5 minutes, 2026-11-04); the MCP SDK's default request timeout is 60 s
 * (`DEFAULT_REQUEST_TIMEOUT_MSEC`), the shortest a client is known to keep. A waiting call
 * is answered within a sixth of it, so the refusal is told and retried while the client
 * still waits, the rest left to the call's own work. The leg checks that a full queue turns
 * over well within it at the measured step.
 */
export const SLOT_WAIT_MS = 10_000;
/** The refusal: retryable, never a loss (the doors answer it `503` with `Retry-After`). */
export const BUSY = "busy: this server is holding as much as it may at once; try again shortly";

/** The refusal's error: named as every told refusal is, so the doors answer it as one. */
export class SlotBusy extends Error {
  override readonly name = "Refused";
  constructor() {
    super(BUSY);
  }
}

/** Units taken, held until `release` (a second release is nothing). */
export interface Slot {
  readonly units: number;
  release(): void;
}

/** A pool's state, a test's view: its capacity, the units in use, the calls waiting. */
export interface PoolState {
  readonly capacity: number;
  readonly held: number;
  readonly waiting: number;
  readonly peak: number;
}

interface Waiter {
  readonly units: number;
  readonly owner: string;
  readonly grant: (slot: Slot) => void;
  readonly refuse: (reason?: unknown) => void;
}

/**
 * Waiters of one size class, fair across owners: each owner's calls first come first served,
 * the owners with waiters taking turns (round-robin), the first in turn order served next and
 * then sent to the back. Calls that name no owner are one owner's.
 */
class Turns {
  /** Each owner's waiters, in turn order. */
  readonly #queues = new Map<string, Waiter[]>();
  length = 0;

  push(waiter: Waiter): void {
    const queue = this.#queues.get(waiter.owner);
    if (queue === undefined) {
      this.#queues.set(waiter.owner, [waiter]);
    } else {
      queue.push(waiter);
    }
    this.length += 1;
  }

  /** Each owner's oldest waiter, in turn order. */
  *heads(): Generator<Waiter> {
    for (const queue of this.#queues.values()) {
      yield queue[0]!;
    }
  }

  /** `waiter`, an owner's oldest, taken: its owner's turn is spent, to the back. */
  take(waiter: Waiter): void {
    const queue = this.#queues.get(waiter.owner)!;
    queue.shift();
    this.#queues.delete(waiter.owner);
    if (queue.length > 0) {
      this.#queues.set(waiter.owner, queue);
    }
    this.length -= 1;
  }

  remove(waiter: Waiter): boolean {
    const queue = this.#queues.get(waiter.owner);
    const at = queue?.indexOf(waiter) ?? -1;
    if (queue === undefined || at < 0) {
      return false;
    }
    queue.splice(at, 1);
    if (queue.length === 0) {
      this.#queues.delete(waiter.owner);
    }
    this.length -= 1;
    return true;
  }

  /** How many of `owner`'s calls wait here. */
  count(owner: string): number {
    return this.#queues.get(owner)?.length ?? 0;
  }

  /**
   * The full class's room for a newcomer of `owner`: the newest waiter of the owner waiting
   * most is refused `busy`, unless that is `owner`'s own share already. So the waiters are
   * shared without a fixed cap an owner.
   */
  makeRoom(owner: string): boolean {
    let most: Waiter[] | null = null;
    for (const queue of this.#queues.values()) {
      if (most === null || queue.length > most.length) {
        most = queue;
      }
    }
    if (most === null || most.length <= this.count(owner) + 1) {
      return false;
    }
    const evicted = most.at(-1)!;
    this.remove(evicted);
    evicted.refuse();
    return true;
  }
}

/**
 * Units a call holds and grows as its need grows (`SlotPool.lease`; the gateway's `Hold`). A
 * lease that holds units and cannot grow at once waits only if it is the pool's oldest holder
 * (wait-die); any other gives back everything it holds and is refused `busy`, retryable and
 * before anything it guards has begun. Only the oldest holder ever waits while it holds, so
 * the waits form no cycle, and while it waits no other call takes past the reserve, so it is
 * never starved.
 */
export interface Lease {
  readonly units: number;
  /** At least `total` units held, or `busy` (its units let go), or `signal`'s reason. */
  grow(total: number, signal?: AbortSignal): Promise<void>;
  /**
   * Its newest units let go, down to `total` (never below): a step's units, given back as
   * the step ends, the lease still holding what it held before (the archive's move: its
   * records' units held, each pass's grown and given back, `owner-do.ts`'s `MoveHold`).
   */
  shrink(total: number): void;
  release(): void;
}

/**
 * Who asks for units, as `#fits` reads it: the owner, whether the hold is a larger one (more
 * than one unit in all), and the lease that grows, if any.
 */
interface Asker {
  readonly owner: string;
  readonly large: boolean;
  readonly lease: LeaseOf | null;
}

/**
 * A pool of `capacity` units, waits of at most `waitMs`, and `reserve` units only one-unit
 * holds take. At most `waiters` larger holds and `smallWaiters` one-unit holds wait at once,
 * each class its own cap, so larger holds waiting never get a small read refused (each its
 * Worker's own figure; four times the capacity unless it names one). A call may name its
 * owner (the gateway's calls do; the owner object's isolate is one owner's): within each
 * class the owners waiting take turns (`Turns`), and a full class makes room by refusing the
 * newest waiter of the owner waiting most.
 *
 * **The one admission rule** (`#fits`): a call takes free units only where no call it must
 * not pass is waiting for them.
 * - A larger hold never takes the reserve.
 * - While a larger hold waits, a one-unit hold takes only the reserve, so the units one-unit
 *   holds took past it come back to the larger one as they are released (review M-b).
 * - While the oldest holder waits to grow (wait-die: only it may wait while it holds), no
 *   other call takes past the reserve: a younger holder gives way, and a call holding nothing
 *   takes only the reserve, as for a larger hold waiting (`rveg_report.md` V-M1).
 * - While another owner waits, an owner holding half the pool or more takes no more until
 *   that waiter is served: a share under contention, not a cap; alone, an owner may use it
 *   all (V-H2).
 * Each class's waiters are drained as the rule admits them, the oldest holder's growth first,
 * then the larger, in turns by owner.
 */
export class SlotPool {
  readonly capacity: number;
  readonly reserve: number;
  /**
   * The memory leg's (`memory/leg.mjs`), null elsewhere: awaited at the end of every hold,
   * before its units are released, with the units it held, so a sample sees each step's
   * live set at its end against what it holds.
   */
  observe: ((units: number) => Promise<void>) | null = null;
  readonly #waitMs: number;
  readonly #waiters: number;
  readonly #smallWaiters: number;
  /** One-unit holds waiting, and larger ones: each in turns by owner. */
  readonly #small = new Turns();
  readonly #large = new Turns();
  /** The oldest holder's growth, waiting: passed by no other call. */
  #oldest: { asker: Asker; units: number; waiter: Waiter } | null = null;
  /** The leases holding units, by age: the first is the oldest holder. */
  readonly #holders = new Set<LeaseOf>();
  /** Units held, by one-unit holds, and by larger ones; and by each owner. */
  #smallHeld = 0;
  #largeHeld = 0;
  readonly #heldBy = new Map<string, number>();
  #peak = 0;

  constructor(capacity: number, waitMs: number = SLOT_WAIT_MS, waiters: number = 4 * capacity,
              reserve = 0, smallWaiters: number = waiters) {
    if (!Number.isSafeInteger(capacity) || capacity < 1) {
      throw new Error("a pool holds at least one unit");
    }
    if (!Number.isSafeInteger(reserve) || reserve < 0 || reserve >= capacity) {
      throw new Error("a pool's reserve leaves larger holds at least one unit");
    }
    this.capacity = capacity;
    this.reserve = reserve;
    this.#waitMs = waitMs;
    this.#waiters = waiters;
    this.#smallWaiters = smallWaiters;
  }

  get #held(): number {
    return this.#smallHeld + this.#largeHeld;
  }

  /** The most units one larger hold may take: it leaves the reserve free. */
  get most(): number {
    return Math.max(1, this.capacity - this.reserve);
  }

  /** Whether another owner than `owner` waits for units. */
  #othersWait(owner: string): boolean {
    const mine = this.#small.count(owner) + this.#large.count(owner)
      + (this.#oldest?.asker.owner === owner ? 1 : 0);
    return this.#small.length + this.#large.length + (this.#oldest === null ? 0 : 1) > mine;
  }

  /** The admission rule (see the class): whether `units` for `asker` fit now. */
  #fits(units: number, asker: Asker): boolean {
    if (this.#held + units > this.capacity) {
      return false;
    }
    // The owner's share: half the pool or more, while another owner waits, takes no more.
    if ((this.#heldBy.get(asker.owner) ?? 0) * 2 >= this.capacity
        && this.#othersWait(asker.owner)) {
      return false;
    }
    // While the oldest holder waits to grow (within its own share), no other call passes it
    // but into the reserve.
    const oldest = this.#oldest;
    const passed = oldest !== null && oldest.asker !== asker
      && ((this.#heldBy.get(oldest.asker.owner) ?? 0) * 2 < this.capacity
          || !this.#othersWait(oldest.asker.owner));
    if (asker.large) {
      return !passed && this.#largeHeld + units <= this.capacity - this.reserve;
    }
    return (this.#large.length === 0 && !passed) || this.#smallHeld + 1 <= this.reserve;
  }

  /**
   * `units` for `owner`, now or once free (its turn in its class, its own calls first come
   * first served), or refused `busy`; `signal`, aborted, takes the call out of its queue at
   * once.
   */
  acquire(units: number, owner = "", signal?: AbortSignal, large = units > 1): Promise<Slot> {
    const most = this.capacity - this.reserve;
    if (!Number.isSafeInteger(units) || units < 1 || (units > 1 && units > most)) {
      throw new Error(`a call takes 1 to ${Math.max(1, most)} units, not ${units}`);
    }
    return this.#ask(units, { owner, large, lease: null }, signal);
  }

  #ask(units: number, asker: Asker, signal?: AbortSignal): Promise<Slot> {
    if (signal?.aborted === true) {
      return Promise.reject(signal.reason);
    }
    const queue = asker.large ? this.#large : this.#small;
    if (queue.length === 0 && this.#fits(units, asker)) {
      return Promise.resolve(this.#take(units, asker));
    }
    if (queue.length >= (asker.large ? this.#waiters : this.#smallWaiters)
        && !queue.makeRoom(asker.owner)) {
      return Promise.reject(new SlotBusy());
    }
    return new Promise<Slot>((resolve, reject) => {
      const done = () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", stop);
      };
      const waiter: Waiter = {
        units,
        owner: asker.owner,
        grant: (slot) => {
          done();
          resolve(slot);
        },
        refuse: (reason?: unknown) => {
          done();
          reject(reason ?? new SlotBusy());
        },
      };
      const leave = (reason?: unknown) => {
        if (queue.remove(waiter)) {
          waiter.refuse(reason);
          this.#drain();
        }
      };
      const stop = () => leave(signal!.reason);
      const timer = setTimeout(() => leave(), this.#waitMs);
      signal?.addEventListener("abort", stop, { once: true });
      queue.push(waiter);
    });
  }

  /**
   * A lease of units for `owner`, empty: see `Lease`. `large` holds even one unit in the
   * larger class, never the reserve: a lease that will grow past one (the archive's move).
   */
  lease(owner = "", large = false): Lease {
    return new LeaseOf(this, owner, large);
  }

  /** A holding lease's growth by `units` to `total`: now, the oldest holder's wait, or busy. */
  grow(lease: LeaseOf, units: number, total: number, signal?: AbortSignal): Promise<Slot> {
    if (signal?.aborted === true) {
      return Promise.reject(signal.reason);
    }
    const asker: Asker = { owner: lease.owner, large: total > 1 || lease.large, lease };
    if (this.#fits(units, asker)) {
      return Promise.resolve(this.#take(units, asker));
    }
    // Wait-die: only the oldest holder waits while it holds; any other gives way.
    if (this.#holders.values().next().value !== lease || this.#oldest !== null) {
      return Promise.reject(new SlotBusy());
    }
    return new Promise<Slot>((resolve, reject) => {
      const done = () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", stop);
        if (this.#oldest?.waiter === waiter) {
          this.#oldest = null;
        }
      };
      const waiter: Waiter = {
        units,
        owner: lease.owner,
        grant: (slot) => {
          done();
          resolve(slot);
        },
        refuse: (reason?: unknown) => {
          done();
          reject(reason ?? new SlotBusy());
          this.#drain();
        },
      };
      const stop = () => waiter.refuse(signal!.reason);
      const timer = setTimeout(() => waiter.refuse(), this.#waitMs);
      signal?.addEventListener("abort", stop, { once: true });
      this.#oldest = { asker, units, waiter };
    });
  }

  /** A lease that now holds units joins the holders, the youngest; one let go leaves. */
  holding(lease: LeaseOf, held: boolean): void {
    if (held) {
      this.#holders.add(lease);
      return;
    }
    this.#holders.delete(lease);
    if (this.#oldest?.asker.lease === lease) {
      this.#oldest.waiter.refuse();
    }
  }

  #take(units: number, asker: Asker): Slot {
    const large = asker.large;
    if (large) {
      this.#largeHeld += units;
    } else {
      this.#smallHeld += units;
    }
    const owner = asker.owner;
    this.#heldBy.set(owner, (this.#heldBy.get(owner) ?? 0) + units);
    this.#peak = Math.max(this.#peak, this.#held);
    let held = true;
    return {
      units,
      release: () => {
        if (held) {
          held = false;
          if (large) {
            this.#largeHeld -= units;
          } else {
            this.#smallHeld -= units;
          }
          const left = this.#heldBy.get(owner)! - units;
          if (left === 0) {
            this.#heldBy.delete(owner);
          } else {
            this.#heldBy.set(owner, left);
          }
          this.#drain();
        }
      },
    };
  }

  /**
   * The waiting granted as the rule admits them, until it admits no more: the oldest holder's
   * growth first, then the larger class, then one-unit holds, each in turns by owner (an
   * owner the rule holds back is passed over; an owner's oldest call that does not fit waits
   * for it, so a large call is never starved by smaller ones behind it).
   */
  #drain(): void {
    for (let moved = true; moved;) {
      moved = false;
      const oldest = this.#oldest;
      if (oldest !== null && this.#fits(oldest.units, oldest.asker)) {
        oldest.waiter.grant(this.#take(oldest.units, oldest.asker));
        moved = true;
      }
      for (const [queue, large] of [[this.#large, true], [this.#small, false]] as const) {
        for (const head of [...queue.heads()]) {
          const asker: Asker = { owner: head.owner, large, lease: null };
          if (this.#fits(head.units, asker)) {
            queue.take(head);
            head.grant(this.#take(head.units, asker));
            moved = true;
          } else if ((this.#heldBy.get(head.owner) ?? 0) * 2 < this.capacity
                     || !this.#othersWait(head.owner)) {
            break;  // within its share and not fitting: it waits for its units
          }
        }
      }
    }
  }

  state(): PoolState {
    return { capacity: this.capacity, held: this.#held,
             waiting: this.#small.length + this.#large.length + (this.#oldest === null ? 0 : 1),
             peak: this.#peak };
  }

  /** A test's: the most held at once counted again from now. */
  resetPeak(): void {
    this.#peak = this.#held;
  }
}

/** `SlotPool.lease`'s lease. */
class LeaseOf implements Lease {
  readonly owner: string;
  readonly large: boolean;
  readonly #pool: SlotPool;
  readonly #slots: Slot[] = [];
  #units = 0;
  #released = false;

  constructor(pool: SlotPool, owner: string, large: boolean) {
    this.#pool = pool;
    this.owner = owner;
    this.large = large;
  }

  get units(): number {
    return this.#units;
  }

  async grow(total: number, signal?: AbortSignal): Promise<void> {
    if (!Number.isSafeInteger(total) || total < 1 || (total > 1 && total > this.#pool.most)) {
      throw new Error(`a lease holds 1 to ${this.#pool.most} units, not ${total}`);
    }
    while (!this.#released && this.#units < total) {
      const need = total - this.#units;
      let slot: Slot;
      try {
        slot = this.#units === 0
          ? await this.#pool.acquire(need, this.owner, signal, need > 1 || this.large)
          : await this.#pool.grow(this, need, total, signal);
      } catch (error) {
        if (error instanceof SlotBusy) {
          this.release();  // die: everything it holds goes back
        }
        throw error;
      }
      if (this.#released) {
        slot.release();
        return;
      }
      this.#slots.push(slot);
      if (this.#units === 0) {
        this.#pool.holding(this, true);
      }
      this.#units += slot.units;
    }
  }

  shrink(total: number): void {
    while (this.#slots.length > 0 && this.#units - this.#slots.at(-1)!.units >= total) {
      const slot = this.#slots.pop()!;
      this.#units -= slot.units;
      slot.release();
    }
    if (this.#units === 0) {
      this.#pool.holding(this, false);
    }
  }

  release(): void {
    this.#released = true;
    this.#pool.holding(this, false);
    for (const slot of this.#slots.splice(0)) {
      slot.release();
    }
    this.#units = 0;
  }
}

/** Run `work` holding `units` of `pool`, released whatever it does. */
export async function slotted<T>(pool: SlotPool, units: number, work: () => Promise<T>):
    Promise<T> {
  const slot = await pool.acquire(units);
  try {
    const done = await work();
    if (pool.observe !== null) {
      await pool.observe(units);
    }
    return done;
  } finally {
    slot.release();
  }
}

/**
 * One of a shared budget's members (a relay's tails): what it holds, in the budget's unit,
 * and its oldest unit dropped, answering how much that freed (0 when it holds nothing).
 */
export interface Sharer {
  held(): number;
  drop(): number;
}

/**
 * An exact budget of retained data the isolate's objects share (the relay's tails, M5):
 * each member counts what it holds in the budget's own unit, and past the budget the member
 * holding the most drops its oldest unit, then the next, until the total fits. A member that
 * is gone (an evicted object's instance, collected) leaves on its own: members are held
 * weakly.
 */
export class SharedBudget {
  readonly budget: number;
  readonly #members = new Set<WeakRef<Sharer>>();
  readonly #refs = new WeakMap<Sharer, WeakRef<Sharer>>();

  constructor(budget: number) {
    this.budget = budget;
  }

  join(member: Sharer): void {
    if (!this.#refs.has(member)) {
      const ref = new WeakRef(member);
      this.#refs.set(member, ref);
      this.#members.add(ref);
    }
  }

  leave(member: Sharer): void {
    const ref = this.#refs.get(member);
    if (ref !== undefined) {
      this.#members.delete(ref);
      this.#refs.delete(member);
    }
  }

  #live(): Sharer[] {
    const live: Sharer[] = [];
    for (const ref of this.#members) {
      const member = ref.deref();
      if (member === undefined) {
        this.#members.delete(ref);
      } else {
        live.push(member);
      }
    }
    return live;
  }

  /** What every member holds together. */
  total(): number {
    return this.#live().reduce((sum, member) => sum + member.held(), 0);
  }

  /** A member grew: the largest drop their oldest until the total fits the budget. */
  trim(): void {
    for (let total = this.total(); total > this.budget;) {
      const largest = this.#live().reduce<Sharer | null>((most, member) =>
        most === null || member.held() > most.held() ? member : most, null);
      const freed = largest === null ? 0 : largest.drop();
      if (freed === 0) {
        return;
      }
      total -= freed;
    }
  }
}

const ISOLATE: { pool: SlotPool | null; tails: SharedBudget | null } = { pool: null,
                                                                          tails: null };

/**
 * The isolate's one pool, made on first use with its Worker's `capacity`, `reserve` and
 * waiters' caps; every object and request in the isolate shares it. Asked for another, it is
 * a bug: one Worker, one figure.
 */
export function isolatePool(capacity: number, reserve = 0, waiters = 4 * capacity,
                            smallWaiters = waiters): SlotPool {
  if (ISOLATE.pool === null) {
    ISOLATE.pool = new SlotPool(capacity, SLOT_WAIT_MS, waiters, reserve, smallWaiters);
  } else if (ISOLATE.pool.capacity !== capacity || ISOLATE.pool.reserve !== reserve) {
    throw new Error(`the isolate's pool holds ${ISOLATE.pool.capacity} units ` +
                    `(${ISOLATE.pool.reserve} reserved), not ${capacity} (${reserve})`);
  }
  return ISOLATE.pool;
}

/** The isolate's one budget of the relay's tails, of `budget` units, made on first use. */
export function isolateTails(budget: number): SharedBudget {
  if (ISOLATE.tails === null) {
    ISOLATE.tails = new SharedBudget(budget);
  } else if (ISOLATE.tails.budget !== budget) {
    throw new Error(`the isolate's tails hold ${ISOLATE.tails.budget} units, not ${budget}`);
  }
  return ISOLATE.tails;
}
