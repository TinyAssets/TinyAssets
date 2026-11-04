/**
 * The isolate's memory: one budget for everything a request holds, whoever holds it.
 *
 * Workers gives an isolate 128 MB, "per-isolate, not per-invocation", and one isolate may
 * serve many requests at once and host many Durable Objects of one class
 * (developers.cloudflare.com/workers/platform/limits/, durable-objects/reference/in-memory-state/;
 * a workerd probe saw three objects and the entry Worker in one isolate). Past it, in-flight
 * requests complete, later ones go to a new isolate (an owner object's in-memory state gone),
 * and some may be cancelled. No document bounds how many objects or requests share one, so
 * no per-request constant proves the isolate's sum; this module's one budget does. It is
 * the platform's one exemption from the no-module-state rule (`module-state.test.ts`): what
 * it holds is a count of bytes in flight in this isolate, never an owner's data.
 *
 * **The rule.** Anything that would hold more than `SMALL_BYTES` across an await (a page of
 * a read, a placed body's bytes, a request body, an answer) takes a grant here first, for
 * what it will hold at its worst, and releases it when it is done (`granted`), whatever
 * happens. A grant that does not fit is refused at once with `busy`, a told, retryable
 * refusal (the doors answer it `503` with `Retry-After`): nothing waits, nothing is dropped,
 * nothing is changed by the refused call.
 *
 * **The budget.** `GUARD_BYTES` is the isolate less its `BASELINE_BYTES`. Out of it come the
 * reservations (the relay's share, `reserve`, taken once per isolate by the first object
 * that runs the relay) and the grants in flight, and the largest grant in flight is counted
 * twice, so the collector always has room for one grant's garbage beside the live set: a
 * grant `g` is admitted when `held + g + max(g, largest held) + reserved <= GUARD_BYTES`.
 * The ledger (`guards/memory-bound.test.ts`) holds every grant a path declares to fit
 * alone, beside the reservations, so a refusal is only ever the load of the moment.
 *
 * **What it cannot see.** A Workers RPC call's arguments are deserialized before its method
 * runs, and an answer handed to the runtime is the runtime's to send: each is bounded by its
 * sender's grant (a door's answer by `RESULT_CHARS`, a request body by its door's ceiling),
 * held for the synchronous turn that refuses it. The relay's terms are per object, and its
 * reservation is one object's (`memory-bound.test.ts` says what that leaves).
 */
import { Refused } from "./errors";

/** The isolate's memory (Workers limits: 128 MB, the JavaScript heap with WebAssembly's). */
export const ISOLATE_BYTES = 128_000_000;
/**
 * What an isolate holds before any request: its code, its modules' constants, the runtime's
 * own objects. MEASURED IN WORKERD, TO BE REPLACED: the owner's Worker in the vitest
 * runner's isolate, the test framework loaded beside it and one object used, at rest after a
 * full collection, 28.7 MiB (`test/memory-peaks.mjs`, which fails past this figure;
 * `limits-audit.md`), rounded up to 32 MiB. The test framework makes it larger than
 * production's; once deployed, each Worker's own figure is read from the Memory usage chart
 * (per isolate), and this becomes it.
 */
export const BASELINE_BYTES = 33_554_432;
/** What the reservations and the grants in flight share: the isolate less its baseline. */
export const GUARD_BYTES = ISOLATE_BYTES - BASELINE_BYTES;
/**
 * The relay's share (`relay.ts`: the runs' tails, the events posts it holds, the sockets'
 * attachments), reserved by the first owner object an isolate runs: today's eighth of the
 * isolate, which `memory-bound.test.ts` holds its worst case within.
 */
export const RELAY_RESERVED = ISOLATE_BYTES / 8;
/** At most this many bytes may be held across an await without a grant. */
export const SMALL_BYTES = 65_536;
/** The refusal: retryable, never a loss. */
export const BUSY = "busy: this server's memory is in use by other requests; try again shortly";

/** Bytes granted, held until `release` (once; a second release is a bug). */
export interface Grant {
  readonly bytes: number;
  release(): void;
}

/** The budget's state: what is held, reserved, and the most held at once (a test's view). */
export interface Held {
  readonly held: number;
  readonly reserved: number;
  readonly grants: number;
  readonly peak: number;
}

class Guard {
  readonly #grants = new Map<number, number>();
  readonly #reserved = new Map<string, number>();
  #next = 0;
  #held = 0;
  #peak = 0;

  #reservedBytes(): number {
    let bytes = 0;
    for (const share of this.#reserved.values()) {
      bytes += share;
    }
    return bytes;
  }

  #largest(): number {
    let largest = 0;
    for (const bytes of this.#grants.values()) {
      largest = Math.max(largest, bytes);
    }
    return largest;
  }

  /** A static share, once per isolate: the same name again is the same share. */
  reserve(name: string, bytes: number): void {
    const had = this.#reserved.get(name);
    if (had !== undefined) {
      if (had !== bytes) {
        throw new Error(`the ${name} reservation is ${had} bytes, not ${bytes}`);
      }
      return;
    }
    this.#reserved.set(name, bytes);
  }

  grant(bytes: number, what: string): Grant {
    if (!Number.isSafeInteger(bytes) || bytes < 0) {
      throw new Error(`a grant for ${what} is a whole number of bytes`);
    }
    const reserved = this.#reservedBytes();
    if (2 * bytes + reserved > GUARD_BYTES) {
      // No load makes room for it: a path's declared grant the ledger should have refused.
      throw new Error(`${what} asks ${bytes} bytes, more than the isolate's memory holds`);
    }
    if (this.#held + bytes + Math.max(bytes, this.#largest()) + reserved > GUARD_BYTES) {
      throw new Refused(BUSY);
    }
    const id = this.#next++;
    this.#grants.set(id, bytes);
    this.#held += bytes;
    this.#peak = Math.max(this.#peak, this.#held);
    return {
      bytes,
      release: () => {
        if (!this.#grants.delete(id)) {
          throw new Error(`a grant for ${what} released twice`);
        }
        this.#held -= bytes;
      },
    };
  }

  state(): Held {
    return { held: this.#held, reserved: this.#reservedBytes(), grants: this.#grants.size,
             peak: this.#peak };
  }

  /** A test's: the most held at once counted again from what is held now. */
  resetPeak(): void {
    this.#peak = this.#held;
  }
}

const ISOLATE = new Guard();

/** Take `bytes` for `what`, or be refused `busy`; the caller releases it. */
export function grant(bytes: number, what: string): Grant {
  return ISOLATE.grant(bytes, what);
}

/** Run `work` under a grant of `bytes`, released whatever it does. */
export async function granted<T>(bytes: number, what: string, work: () => Promise<T>):
    Promise<T> {
  const held = ISOLATE.grant(bytes, what);
  try {
    return await work();
  } finally {
    held.release();
  }
}

/** Reserve the isolate's static share `name` (idempotent): the relay's. */
export function reserve(name: string, bytes: number): void {
  ISOLATE.reserve(name, bytes);
}

/** The budget as it stands. */
export function memoryHeld(): Held {
  return ISOLATE.state();
}

/** Count the most held at once again from now (tests). */
export function resetMemoryPeak(): void {
  ISOLATE.resetPeak();
}

/** Whether `error` is the guard's refusal (the doors answer it `503`). */
export function isBusy(error: unknown): boolean {
  return error instanceof Error && error.name === "Refused" && error.message.startsWith("busy:");
}
