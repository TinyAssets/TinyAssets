/**
 * The owner's erasure as a Lifecycle capability (`erasure.ts` holds the order and the
 * mark): one durable job, `owner-erasure/erase`, pushed in the marking unit, so a crash at
 * any step is resumed by the object's own alarm, which Lifecycle drives as it drives every
 * job. While the mark stands, the schedule's lanes run nothing, so the erasure's job is
 * the only work the alarm does.
 *
 * Its last step is the one place the owner's own database is wiped whole: Lifecycle's
 * alarm is disabled for good first (`disableAlarms`, which also clears the alarm), so
 * Lifecycle writes nothing after the wipe, then the write gate deletes the whole database
 * (`WriteGate.wipe`), its job row with it, and writes the tombstone into it in the same
 * synchronous turn (`ErasureParts.wipe`: the erased admission row, `admission.ts`), and
 * again once the wipe resolves. That leaves a window: a crash after the wipe is durable
 * and before the tombstone is leaves the wiped database with no tombstone, and a later
 * `getByName` makes the owner afresh, admitted (workerd shows it). The facet probe's Q8
 * (run 41247617223, `facet-probe.md`) found the order right with no crash, but did not
 * measure the window: production's `ctx.abort()` rolled back everything since the last
 * durable point, even an awaited `deleteAll`. The window stands until slice 10 closes it
 * (a record outside the object, or an explicit platform admit), and the erasure door
 * waits for it (`tasks.md`). No other source disables Lifecycle's alarm or wipes a
 * database (the alarm guard).
 */
import {
  LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";

import {
  ERASE_RETRY_MS, erasureStartedAt, erasureStep, markErasing, runsEnded,
} from "./erasure";
import type { OwnerStorage, Write } from "./gate";

/** The erasure's Lifecycle capability id, and its job's function. */
export const ERASURE = "owner-erasure";
const ERASE_FN = "erase";
const ERASE_JOB = `${ERASURE}/${ERASE_FN}`;

/** What the erasure runs on: the object's gate, the work it ends, and its wipe. */
interface ErasureParts {
  readonly storage: OwnerStorage;
  readonly write: Write;
  readonly clock: () => number;
  /** The ending of every run in flight, in a unit (`Egress.drop`: its streams). */
  readonly ended: () => void;
  /** Every sandbox of the owner killed and the sandbox Worker's state of them deleted. */
  readonly sandboxes: () => Promise<void>;
  /** The object's Lifecycle, whose alarm the last step disables for good. */
  readonly lifecycle: { disableAlarms(): Promise<void> };
  /**
   * Every object the owner holds in R2 deleted, by its namespace's prefix
   * (`Files.eraseNamespace`): its files' chunks and its placed bodies' (ADR-035), once every
   * put this instance began has landed (`Files.settled`; under the mark no new one begins).
   * Repeatable; it changes no row. Resolves with the namespace, for `blobsAgain`.
   */
  readonly blobs: () => Promise<string | null>;
  /** The same deletion again after the wipe, for a put another instance began (`ns`). */
  readonly blobsAgain: (ns: string | null) => Promise<void>;
  /**
   * How long after the mark the R2 step waits, so a put an earlier instance of the object
   * began before the mark has landed by then (`PUT_GRACE_MS`).
   */
  readonly putGraceMs: number;
  /**
   * The outside record (`erasure.ts` step 2): whether the doors confirmed it, and, until
   * they do, the erasure queue's message sent again. A missing queue fails loudly.
   */
  readonly outside: () => Promise<boolean>;
  /** The object's whole database deleted, and the tombstone left (`WriteGate.wipe`). */
  readonly wipe: () => Promise<void>;
  /**
   * The cutover's discard's wipe (`discard`): the whole database deleted, and nothing left, no
   * tombstone (`WriteGate.wipe` with none).
   */
  readonly bare: () => Promise<void>;
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
   * Begin the owner's erasure: the mark and its job, durable before this resolves; the rest
   * goes on off the caller's line (the job resumes it after any failure or restart), so
   * nothing of it depends on the request that began it.
   */
  async begin(): Promise<void> {
    const { storage, write, clock } = this.#parts;
    const jobs = this.lifecycle.jobs;
    const [rearmed] = await write((): [Promise<unknown>] => {
      markErasing(storage, clock());
      return [jobs.push({ id: ERASE_JOB, fn: ERASE_FN, time: Date.now() + ERASE_RETRY_MS })];
    });
    await rearmed;
    this.resume();
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
    const { storage, write, clock, ended, sandboxes, lifecycle, blobs, blobsAgain, wipe,
            outside } = this.#parts;
    if (erasureStep(storage) < 2) {
      // 1. The mark, and the job that resumes this after a crash (pushed again on a resume,
      // which also sets the alarm again).
      const jobs = this.lifecycle.jobs;
      const [rearmed] = await write((): [Promise<unknown>] => {
        markErasing(storage, clock());
        return [jobs.push({ id: ERASE_JOB, fn: ERASE_FN, time: Date.now() + ERASE_RETRY_MS })];
      });
      await rearmed;
      // 2. The outside record: the doors told (again, should this be a resume).
      await outside();
      // 3. Every run ended: the owner's sandboxes killed first, paused ones included (a
      // failure rejects, and the erasure is tried again), then its streams and sockets.
      await sandboxes();
      await write(() => {
        ended();
        runsEnded(storage);
      });
    }
    // 3. Every R2 copy deleted by the namespace's prefix, once a put any instance began
    // before the mark has had its time to land (a failure rejects, and the retry deletes
    // again); then the owner wiped whole, Lifecycle's alarm disabled first so nothing is
    // written after; then the prefix deleted once more.
    const due = erasureStartedAt(storage) * 1000 + this.#parts.putGraceMs;
    if (due > Date.now()) {
      // Not yet: the erasure's job wakes it when the grace is up (a day), held in storage,
      // so no in-memory wait carries it and a restart loses nothing.
      const jobs = this.lifecycle.jobs;
      const [rearmed] = await write((): [Promise<unknown>] =>
        [jobs.push({ id: ERASE_JOB, fn: ERASE_FN, time: due })]);
      await rearmed;
      return;
    }
    // The wipe waits for the doors' confirmation of the outside record: they read the names
    // it deletes from this database. Until then the message goes again at each attempt.
    if (!await outside()) {
      const jobs = this.lifecycle.jobs;
      const [rearmed] = await write((): [Promise<unknown>] =>
        [jobs.push({ id: ERASE_JOB, fn: ERASE_FN, time: Date.now() + this.#parts.retryMs })]);
      await rearmed;
      return;
    }
    const ns = await blobs();
    await lifecycle.disableAlarms();
    await wipe();
    this.#erased = true;
    await blobsAgain(ns);
  }

  /**
   * The cutover's discard of an owner the import put here (`import.ts`, `importDiscard`): this
   * erasure's own R2 step (every object under the namespace's prefix, once every put this
   * instance began has landed) and its wipe, Lifecycle's alarm disabled first, then the prefix
   * deleted once more. It is not an erasure: no mark, no outside record (no erased record in
   * the directory, no issuer's deletion), no grace and no tombstone, so the name is no one's
   * and its next object starts afresh, `pending`. `wiping` is told the namespace just before
   * the wipe. Repeatable until the wipe; the namespace it deleted.
   */
  async discard(wiping: (ns: string | null) => void): Promise<string | null> {
    const { blobs, blobsAgain, lifecycle, bare } = this.#parts;
    const ns = await blobs();
    await lifecycle.disableAlarms();
    wiping(ns);
    await bare();
    await blobsAgain(ns);
    return ns;
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
