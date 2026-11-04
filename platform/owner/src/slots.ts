/**
 * Memory bounded by structure (ADR-037, memshape_research.md §2): an isolate's one pool of
 * chunk slots.
 *
 * Large data moves only as streams or 4 MiB chunks, never whole. A path that holds chunks
 * (at most two in hand, and the files plane's copy of what it sends or reads: three
 * `UNIT_BYTES`, `CHUNK_SLOT_BYTES`) takes one slot while it holds them, and only then: when
 * the bytes are in hand, never across a wait on another service (review M2). A pool counts
 * slots, never bytes: what a slot holds is the code's own construction, so nothing is
 * estimated at run time. Its capacity is its Worker's own, a parameter (the owner object's
 * `OWNER_CHUNK_SLOTS`, the gateway's); the counts test holds each capacity times its slot's
 * cost, with the Worker's baseline and what no unit models, within the isolate's 128 MB.
 *
 * **Waiting.** A slot that is not free now is waited for, first come first served, up to
 * `SLOT_WAIT_MS`; at most `waiters` wait at once. Past either, the call is refused `busy`,
 * told and retryable, before it changes anything. So contention queues briefly instead of
 * refusing ordinary load, and an overload is loud, never a lost write.
 *
 * **Release** holds whatever happens: `slotted` releases in `finally`, and a slot released
 * twice is released once.
 *
 * **Retained data** (the relay's tails) is one exact budget an isolate (`SharedBudget`),
 * split across the objects holding any, the largest dropping its oldest past it.
 *
 * It imports nothing, so any Worker uses it (the owner object, the gateway). Its one
 * module-scope holder (`isolatePool`, `isolateTails`) is the platform's pinned exemption from
 * the no-module-state rule (`module-state.test.ts`): it holds a count of slots in use, the
 * calls waiting for one, and which objects' tails share the budget, never an owner's data.
 */

/** A slot's unit: one 4 MiB chunk, the files plane's own. */
export const UNIT_BYTES = 4 * 1024 * 1024;
/** What one slot holds at most: two chunks in hand, and the files plane's copy of one. */
export const CHUNK_SLOT_BYTES = 3 * UNIT_BYTES;
/** How long a call waits for a slot before it is refused `busy`. */
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

/** Slots taken, held until `release` (a second release is nothing). */
export interface Slot {
  readonly units: number;
  release(): void;
}

/** A pool's state, a test's view: its capacity, the slots in use, the calls waiting. */
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

/** A pool of `capacity` slots, waits of at most `waitMs`, at most `waiters` waiting. */
export class SlotPool {
  readonly capacity: number;
  readonly #waitMs: number;
  readonly #waiters: number;
  readonly #queue: Waiter[] = [];
  #held = 0;
  #peak = 0;

  constructor(capacity: number, waitMs: number = SLOT_WAIT_MS, waiters: number = 4 * capacity) {
    if (!Number.isSafeInteger(capacity) || capacity < 1) {
      throw new Error("a pool holds at least one slot");
    }
    this.capacity = capacity;
    this.#waitMs = waitMs;
    this.#waiters = waiters;
  }

  /** `units` slots, now or once free (first come first served), or refused `busy`. */
  acquire(units: number): Promise<Slot> {
    if (!Number.isSafeInteger(units) || units < 1 || units > this.capacity) {
      throw new Error(`a call takes 1 to ${this.capacity} slots, not ${units}`);
    }
    if (this.#queue.length === 0 && this.#held + units <= this.capacity) {
      return Promise.resolve(this.#take(units));
    }
    if (this.#queue.length >= this.#waiters) {
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
        const at = this.#queue.indexOf(waiter);
        if (at >= 0) {
          this.#queue.splice(at, 1);
          waiter.refuse();
          this.#drain();  // a large waiter gone may free the head for smaller ones
        }
      }, this.#waitMs);
      this.#queue.push(waiter);
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
    while (this.#queue.length > 0 && this.#held + this.#queue[0]!.units <= this.capacity) {
      const next = this.#queue.shift()!;
      next.grant(this.#take(next.units));
    }
  }

  state(): PoolState {
    return { capacity: this.capacity, held: this.#held, waiting: this.#queue.length,
             peak: this.#peak };
  }

  /** A test's: the most held at once counted again from now. */
  resetPeak(): void {
    this.#peak = this.#held;
  }
}

/** Run `work` holding `units` of `pool`'s slots, released whatever it does. */
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
 * The isolate's one pool, made on first use with its Worker's `capacity`; every object and
 * request in the isolate shares it. Asked for another capacity, it is a bug: one Worker, one
 * figure.
 */
export function isolatePool(capacity: number): SlotPool {
  if (ISOLATE.pool === null) {
    ISOLATE.pool = new SlotPool(capacity);
  } else if (ISOLATE.pool.capacity !== capacity) {
    throw new Error(`the isolate's pool holds ${ISOLATE.pool.capacity} slots, not ${capacity}`);
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
