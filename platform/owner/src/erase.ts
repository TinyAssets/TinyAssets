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
 * (`WriteGate.wipe`), its job row with it, and writes the tombstone into it in the same
 * synchronous turn (`ErasureParts.wipe`: the erased admission row, `admission.ts`), and
 * again once the wipe resolves. That leaves a window: the store probe's Q8 lost a write
 * made in `deleteAll`'s turn when workerd killed the turn, so a crash there can leave the
 * wiped database with no tombstone, and a later `getByName` makes the owner afresh,
 * admitted. Closing it is slice 10's (a record outside the object, or an explicit
 * platform admit), and the erasure door waits for it (`tasks.md`). A thread store wipes
 * itself in `thread-store.ts`; no other source disables Lifecycle's alarm or wipes a
 * database (the alarm guard).
 */
import {
  LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";

import { ERASE_RETRY_MS, erasureStep, markErasing, storesDestroyed } from "./erasure";
import type { OwnerStorage, Write } from "./gate";
import type { Thread, Threads } from "./threads";

/** The erasure's Lifecycle capability id, and its job's function. */
export const ERASURE = "owner-erasure";
const ERASE_FN = "erase";
const ERASE_JOB = `${ERASURE}/${ERASE_FN}`;
/** Threads one round of the erasure drops at once. */
const ERASE_BATCH = 64;
/** The longest the erasure waits on one thread's drop before it goes on to the next. */
const ERASE_DROP_MS = 15_000;

/** `run`'s outcome within `ms`: whether it finished. */
async function finished(run: Promise<void>, ms: number): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<false>((resolve) => {
    timer = setTimeout(() => resolve(false), ms);
  });
  try {
    return await Promise.race([run.then(() => true, () => false), late]);
  } finally {
    clearTimeout(timer);
  }
}

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
  /** The object's whole database deleted, and the tombstone left (`WriteGate.wipe`). */
  readonly wipe: () => Promise<void>;
  /** How long a refused door waits after the last attempt before it resumes the erasure. */
  readonly retryMs: number;
}

export class Erasure extends LifecycleCapability {
  readonly #parts: ErasureParts;
  #running: Promise<void> | undefined;
  #erased = false;
  /** When the last attempt began (ms), so a refused door resumes at most once a `retryMs`. */
  #lastStart = 0;

  constructor(parts: ErasureParts) {
    super(ERASURE);
    this.#parts = parts;
  }

  /** Whether this instance wiped its owner: it answers nothing more. */
  get erased(): boolean {
    return this.#erased;
  }

  /**
   * Erase the owner, in `erasure.ts`'s order, from the step it recorded; resolves once its
   * database is wiped. One in flight is joined. A failed attempt rejects; its job tries
   * again `ERASE_RETRY_MS` later, and so does the object's next start or refused door.
   */
  erase(): Promise<void> {
    if (this.#running === undefined) {
      this.#lastStart = Date.now();
      this.#running = this.#erase().finally(() => {
        this.#running = undefined;
      });
    }
    return this.#running;
  }

  /**
   * An erasure the mark records, resumed off the caller's line (the object's start; a door
   * the mark refused, inside its unit, `fromDoor`): never twice at once, and from a door at
   * most once a `retryMs` after the last attempt began, so a polling caller drives no
   * retries back to back; a failure waits for the next.
   */
  resume(fromDoor = false): void {
    if (this.#erased || this.#running !== undefined
        || (fromDoor && Date.now() - this.#lastStart < this.#parts.retryMs)) {
      return;
    }
    queueMicrotask(() => {
      this.erase().catch(() => console.error("the owner's erasure failed; it resumes later"));
    });
  }

  async #erase(): Promise<void> {
    if (this.#erased) {
      return;
    }
    const { storage, write, clock, threads, drop, lifecycle, wipe } = this.#parts;
    if (erasureStep(storage) < 2) {
      // 1. The mark, every thread dropping, and the job that resumes this after a crash
      // (pushed again on a resume, which also sets the alarm again).
      const jobs = this.lifecycle.jobs;
      const [rearmed] = await write((): [Promise<unknown>] => {
        markErasing(storage, clock());
        threads.dropAll();
        return [jobs.push({ id: ERASE_JOB, fn: ERASE_FN, time: Date.now() + ERASE_RETRY_MS })];
      });
      await rearmed;
      this.#parts.ended();
      // 2. Every store a row names destroyed, each row forgotten once its store is; one that
      // fails or hangs is passed, and the step fails once every other was tried.
      let failed = 0;
      for (let after = ""; ;) {
        const rows = threads.remaining(ERASE_BATCH, after);
        if (rows.length === 0) {
          break;
        }
        after = rows.at(-1)!.thread_id;
        const done = await Promise.all(rows.map((row) => finished(drop(row), ERASE_DROP_MS)));
        failed += done.filter((ok) => !ok).length;
      }
      if (failed > 0) {
        throw new Error("some of the owner's thread stores could not be destroyed yet");
      }
      await write(() => storesDestroyed(storage));
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
