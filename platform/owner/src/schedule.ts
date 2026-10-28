/**
 * The owner object's one schedule, on the Agents SDK Lifecycle job queue (ADR-032, tasks 3.6).
 *
 * Lifecycle (`agents/lifecycle`, agents 0.31.0) owns the Durable Object's queue of durable
 * jobs and its single physical alarm, and PiHarness, once installed, keeps its own wake job
 * on the same queue. So no source here sets, reads or clears the alarm (the alarm guard):
 * every queue change re-arms it from the queue, and `OwnerDO.alarm` only hands the
 * runtime's alarm to Lifecycle, which drives the due jobs and re-arms.
 *
 * Each kind of timed work is a lane: its own durable rows, which stay the truth, and one
 * Lifecycle job, named after the lane, that says only when to look again.
 * - `sync()` points each lane's job at the lane's next work, never in the past, cancels it
 *   when there is none, and leaves a job already there alone: each push and each alarm set
 *   is a billed row written. Every door that changes a lane's rows calls it, and the object
 *   calls it on wake, so a deploy re-derives the jobs from the rows as stored.
 * - A run is bounded (each lane takes a batch). It ends by setting its own job from the rows
 *   as they then stand, so more due work wakes the object again at once, and a door that
 *   changed the rows while it ran is never lost to a stale result: a same-id push made while
 *   the job dispatches supersedes the drive result (agents `job-queue.ts`, `applyOutcome`).
 * - A run that still fails after Lifecycle's in-process retries backs its lane off
 *   `LANE_RETRY_S`, kept in the job's payload so a door's `sync()` honours it too; never a
 *   tight loop. A platform failure (a superseded isolate, a memory-limit reset) keeps the job
 *   for a fresh invocation, and the alarm's memory-limit breaker is Lifecycle's.
 * - Lifecycle's retries, its deadman wake and overlapping alarms can run a lane twice. Each
 *   lane fences its own work in its rows (a wake's claim lease and may-have-sent mark, a
 *   chunk's `deleting` state, a stamped item's claim), so a second run takes nothing the
 *   first took, and unknown work is never replayed.
 *
 * Lifecycle's own storage is a fixed set: its `cf_agents_jobs` table holds one row per lane
 * here (and PiHarness's one), and it keeps a memory-limit strike counter in the key-value
 * store. It counts toward the object's storage, never toward an owner bound: every bound
 * counts only this object's own tables. Its job ids are always given, so Lifecycle never
 * draws one from its random pool.
 */
import {
  type LifecycleJob, LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";

/** The schedule's Lifecycle capability id, which scopes its job ids. */
export const SCHEDULE = "owner-schedule";
/** How long a lane whose run failed waits before it runs again. */
export const LANE_RETRY_S = 300;

/** One kind of timed work, over its own rows. Times are in seconds. */
export interface Lane {
  /** When the lane next has work it can do, never before `now`; null for none. */
  next(now: number): number | null;
  /** One bounded pass over what is due at `now`. A throw fails the whole pass. */
  run(now: number): Promise<unknown>;
}

/** The backoff a lane's job carries in its payload, or null. */
function heldUntil(job: LifecycleJob | undefined): number | null {
  const payload = job?.payload;
  if (typeof payload !== "object" || payload === null) {
    return null;
  }
  const until = (payload as { not_before?: unknown }).not_before;
  return typeof until === "number" && Number.isFinite(until) ? until : null;
}

export class Schedule extends LifecycleCapability {
  readonly #lanes: ReadonlyMap<string, Lane>;
  readonly #clock: () => number;

  constructor(lanes: Readonly<Record<string, Lane>>, clock: () => number) {
    super(SCHEDULE);
    this.#lanes = new Map(Object.entries(lanes));
    this.#clock = clock;
  }

  /** Point every lane's job at its next work, or cancel it. */
  async sync(): Promise<void> {
    for (const name of this.#lanes.keys()) {
      await this.#set(name, false);
    }
  }

  /**
   * Set lane `name`'s job from its rows: due at its next work, held to the backoff `held`
   * (the job's own while it has not passed, unless given), or cancelled. `force` writes
   * even a job already there, as a run must, since its drive result would otherwise
   * complete and delete the job.
   */
  async #set(name: string, force: boolean, held?: number | null): Promise<void> {
    const jobs = this.lifecycle.jobs;
    const job = jobs.get(name);
    const now = this.#clock();
    const kept = heldUntil(job);
    const until = held !== undefined ? held : kept !== null && kept > now ? kept : null;
    const next = this.#lanes.get(name)!.next(now);
    if (next === null) {
      if (job !== undefined) {
        await jobs.cancel(name);
      }
      return;
    }
    const time = Math.ceil(Math.max(next, until ?? next) * 1000);
    if (!force && job !== undefined && job.time === time && kept === until) {
      return;
    }
    await jobs.push({ id: name, fn: name, time,
                      ...(until === null ? {} : { payload: { not_before: until } }) });
  }

  /** A lane's job is due: one bounded pass, then its job set from the rows as they stand. */
  async onJob({ job }: LifecycleJobContext): Promise<LifecycleJobOutcome> {
    const lane = this.#lanes.get(job.fn);
    if (lane === undefined || job.id !== job.fn) {
      return undefined;  // no lane of this code's: done
    }
    await lane.run(this.#clock());
    await this.#set(job.fn, true, null);
    return undefined;  // superseded by the set above
  }

  /** A pass that failed through Lifecycle's retries: the lane waits `LANE_RETRY_S`. */
  async onJobError({ job }: LifecycleJobContext, _error: unknown): Promise<LifecycleJobOutcome> {
    if (!this.#lanes.has(job.fn) || job.id !== job.fn) {
      return undefined;
    }
    console.error(`the owner's ${job.fn} pass failed; it runs again later`);  // no ids or paths
    await this.#set(job.fn, true, this.#clock() + LANE_RETRY_S);
    return undefined;
  }
}
