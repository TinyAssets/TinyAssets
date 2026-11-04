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
  readonly grant: (slot: Slot) => void;
  readonly refuse: () => void;
}

/**
 * A pool of `capacity` units, waits of at most `waitMs`, and `reserve` units only one-unit
 * holds take. At most `waiters` larger holds and `smallWaiters` one-unit holds wait at once,
 * each class its own cap, so larger holds waiting never get a small read refused (each its
 * Worker's own figure; four times the capacity unless it names one).
 *
 * **The one admission rule** (`#fits`): a call takes free units only where no call it must
 * not pass is waiting for them.
 * - A larger hold never takes the reserve.
 * - While a larger hold waits, a one-unit hold takes only the reserve, so the units one-unit
 *   holds took past it come back to the larger one as they are released (review M-b).
 * Two clauses of this same rule are to come, with the calls that grow what they hold:
 * - wait-die: a call that holds units may wait to grow only if it is the oldest holder, and
 *   while it waits a call holding nothing takes nothing past the reserve;
 * - an owner's share: an owner holding half the pool or more takes no more while another
 *   owner waits.
 * Each class's waiters are drained in order, the larger first, as the rule admits them.
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
  /** One-unit holds waiting, and larger ones: each first come first served. */
  readonly #small: Waiter[] = [];
  readonly #large: Waiter[] = [];
  /** Units held, by one-unit holds, and by larger ones. */
  #smallHeld = 0;
  #largeHeld = 0;
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

  /**
   * Whether `units` fit now. A larger hold leaves the reserve free; a one-unit hold takes
   * any free unit, but only the reserve while a larger hold waits.
   */
  #fits(units: number): boolean {
    if (this.#held + units > this.capacity) {
      return false;
    }
    if (units > 1) {
      return this.#largeHeld + units <= this.capacity - this.reserve;
    }
    return this.#large.length === 0 || this.#smallHeld + 1 <= this.reserve;
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
    if (queue.length >= (units === 1 ? this.#smallWaiters : this.#waiters)) {
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
    if (units === 1) {
      this.#smallHeld += 1;
    } else {
      this.#largeHeld += units;
    }
    this.#peak = Math.max(this.#peak, this.#held);
    let held = true;
    return {
      units,
      release: () => {
        if (held) {
          held = false;
          if (units === 1) {
            this.#smallHeld -= 1;
          } else {
            this.#largeHeld -= units;
          }
          this.#drain();
        }
      },
    };
  }

  /** The waiting granted in order: the larger head first, then one-unit holds. */
  #drain(): void {
    for (let moved = true; moved;) {
      moved = false;
      for (const queue of [this.#large, this.#small]) {
        while (queue.length > 0 && this.#fits(queue[0]!.units)) {
          const next = queue.shift()!;
          next.grant(this.#take(next.units));
          moved = true;
        }
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
