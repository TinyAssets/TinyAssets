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
 * size class, up to `SLOT_WAIT_MS`, with at most `waiters` waiting. Past either the call is
 * refused `busy`, told and retryable, before it changes anything. A one-unit hold (a window)
 * never waits behind a larger one: larger holds leave `reserve` units free, which only
 * one-unit holds take, and they queue apart. So a co-tenant's small read waits only on other
 * small reads, never behind edits.
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
 * How long a call waits for units before it is refused `busy`: the queue's own term. A
 * waiting tool call is answered within a sixth of the MCP SDK's default request timeout
 * (`DEFAULT_REQUEST_TIMEOUT_MSEC`, 60 s), so the refusal is told and retried well inside
 * what the client waits, the rest left to the call's own work. A waiter is served sooner
 * whenever the holds ahead of it turn over faster: the leg measures a step's hold.
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
  readonly grant: (slot: Slot) => void;
  readonly refuse: () => void;
}

/**
 * A pool of `capacity` units, waits of at most `waitMs`, at most `waiters` waiting (its
 * Worker's own figure; four times the capacity unless it names one), and `reserve` units
 * only one-unit holds take.
 */
export class SlotPool {
  readonly capacity: number;
  readonly reserve: number;
  readonly #waitMs: number;
  readonly #waiters: number;
  /** One-unit holds waiting, and larger ones: each first come first served. */
  readonly #small: Waiter[] = [];
  readonly #large: Waiter[] = [];
  #held = 0;
  #peak = 0;

  constructor(capacity: number, waitMs: number = SLOT_WAIT_MS, waiters: number = 4 * capacity,
              reserve = 0) {
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
  }

  /** Whether `units` fit now: a larger hold leaves the reserve free. */
  #fits(units: number): boolean {
    return this.#held + units <= (units === 1 ? this.capacity : this.capacity - this.reserve);
  }

  /** `units`, now or once free (first come first served in its class), or refused `busy`. */
  acquire(units: number): Promise<Slot> {
    const most = this.capacity - this.reserve;
    if (!Number.isSafeInteger(units) || units < 1 || (units > 1 && units > most)) {
      throw new Error(`a call takes 1 to ${Math.max(1, most)} units, not ${units}`);
    }
    const queue = units === 1 ? this.#small : this.#large;
    if (queue.length === 0 && this.#fits(units)) {
      return Promise.resolve(this.#take(units));
    }
    if (this.#small.length + this.#large.length >= this.#waiters) {
      return Promise.reject(new SlotBusy());
    }
    return new Promise<Slot>((resolve, reject) => {
      const waiter: Waiter = {
        units,
        grant: (slot) => {
          clearTimeout(timer);
          resolve(slot);
        },
        refuse: () => reject(new SlotBusy()),
      };
      const timer = setTimeout(() => {
        const at = queue.indexOf(waiter);
        if (at >= 0) {
          queue.splice(at, 1);
          waiter.refuse();
          this.#drain();
        }
      }, this.#waitMs);
      queue.push(waiter);
    });
  }

  #take(units: number): Slot {
    this.#held += units;
    this.#peak = Math.max(this.#peak, this.#held);
    let held = true;
    return {
      units,
      release: () => {
        if (held) {
          held = false;
          this.#held -= units;
          this.#drain();
        }
      },
    };
  }

  #drain(): void {
    for (const queue of [this.#small, this.#large]) {
      while (queue.length > 0 && this.#fits(queue[0]!.units)) {
        const next = queue.shift()!;
        next.grant(this.#take(next.units));
      }
    }
  }

  state(): PoolState {
    return { capacity: this.capacity, held: this.#held,
             waiting: this.#small.length + this.#large.length, peak: this.#peak };
  }

  /** A test's: the most held at once counted again from now. */
  resetPeak(): void {
    this.#peak = this.#held;
  }
}

/** Run `work` holding `units` of `pool`, released whatever it does. */
export async function slotted<T>(pool: SlotPool, units: number, work: () => Promise<T>):
    Promise<T> {
  const slot = await pool.acquire(units);
  try {
    return await work();
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
 * `waiters`; every object and request in the isolate shares it. Asked for another, it is a
 * bug: one Worker, one figure.
 */
export function isolatePool(capacity: number, reserve = 0, waiters = 4 * capacity): SlotPool {
  if (ISOLATE.pool === null) {
    ISOLATE.pool = new SlotPool(capacity, SLOT_WAIT_MS, waiters, reserve);
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
