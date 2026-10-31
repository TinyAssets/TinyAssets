/**
 * The owner's erasure as a Lifecycle capability (`erasure.ts` holds the order and the
 * mark): one durable job, `owner-erasure/erase`, pushed in the marking unit, so a crash at
 * any step is resumed by the object's own alarm, which Lifecycle drives as it drives every
 * job. While the mark stands, the schedule's lanes run nothing and every thread's wake job
 * finds its thread dropping and completes, so the erasure's job is the only work the
 * alarm does.
 *
 * Its last step is the one place the owner's own database is wiped whole: Lifecycle's
 * alarm is disabled for good first (`disableAlarms`, which also clears the alarm), so
 * Lifecycle writes nothing after the wipe, then the write gate deletes the whole database
 * (`WriteGate.wipe`), its job row with it. A thread store wipes itself in
 * `thread-store.ts`; no other source disables Lifecycle's alarm or wipes a database (the
 * alarm guard).
 */
import {
  LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";

import { ERASE_RETRY_MS, markErasing } from "./erasure";
import type { OwnerStorage, Write } from "./gate";
import type { Thread, Threads } from "./threads";

/** The erasure's Lifecycle capability id, and its job's function. */
export const ERASURE = "owner-erasure";
const ERASE_FN = "erase";
const ERASE_JOB = `${ERASURE}/${ERASE_FN}`;
/** Threads one round of the erasure drops at once. */
const ERASE_BATCH = 64;

/** What the erasure runs on: the object's gate, its threads and their drop, and its wipe. */
export interface ErasureParts {
  readonly storage: OwnerStorage;
  readonly write: Write;
  readonly clock: () => number;
  readonly threads: Pick<Threads, "dropAll" | "remaining">;
  /** The agent's ending of every wait and view (`OwnerAgent.erasing`). */
  readonly ended: () => void;
  /** The one drop path, with no receipt (`OwnerAgent.drop`). */
  readonly drop: (thread: Thread) => Promise<void>;
  /** The object's Lifecycle, whose alarm the last step disables for good. */
  readonly lifecycle: { disableAlarms(): Promise<void> };
  /** The object's whole database deleted (`WriteGate.wipe`). */
  readonly wipe: () => Promise<void>;
}

export class Erasure extends LifecycleCapability {
  readonly #parts: ErasureParts;
  #running: Promise<void> | undefined;
  #erased = false;

  constructor(parts: ErasureParts) {
    super(ERASURE);
    this.#parts = parts;
  }

  /** Whether this instance wiped its owner: it answers nothing more. */
  get erased(): boolean {
    return this.#erased;
  }

  /**
   * Erase the owner, in `erasure.ts`'s order; resolves once its database is wiped. One in
   * flight is joined. A failed attempt rejects, and its job tries again `ERASE_RETRY_MS`
   * later; so does a crash.
   */
  erase(): Promise<void> {
    this.#running ??= this.#erase().finally(() => {
      this.#running = undefined;
    });
    return this.#running;
  }

  async #erase(): Promise<void> {
    if (this.#erased) {
      return;
    }
    const { storage, write, clock, threads, drop, lifecycle, wipe } = this.#parts;
    const jobs = this.lifecycle.jobs;
    // 1. The mark, every thread dropping, and the job that resumes this after a crash.
    const [rearmed] = await write((): [Promise<unknown>] => {
      markErasing(storage, clock());
      threads.dropAll();
      return [jobs.push({ id: ERASE_JOB, fn: ERASE_FN, time: Date.now() + ERASE_RETRY_MS })];
    });
    await rearmed;
    this.#parts.ended();
    // 2. Every store a row names destroyed, each row forgotten once its store is.
    for (;;) {
      const rows = threads.remaining(ERASE_BATCH);
      if (rows.length === 0) {
        break;
      }
      await Promise.all(rows.map((row) => drop(row)));
    }
    // 3. The owner wiped whole, Lifecycle's alarm disabled first so nothing is written after.
    await lifecycle.disableAlarms();
    await wipe();
    this.#erased = true;
  }

  /** The erasure's job is due: an erasure a crash cut short, resumed. */
  async onJob({ job }: LifecycleJobContext): Promise<LifecycleJobOutcome> {
    if (job.id !== ERASE_JOB) {
      return undefined;
    }
    await this.erase();
    return undefined;
  }

  /** An attempt that failed through Lifecycle's retries is tried again later. */
  onJobError({ job }: LifecycleJobContext, _error: unknown): LifecycleJobOutcome {
    if (job.id !== ERASE_JOB) {
      return undefined;
    }
    console.error("the owner's erasure failed; it is tried again later");  // no ids
    return { rescheduleAt: Date.now() + ERASE_RETRY_MS };
  }
}
