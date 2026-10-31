/**
 * The owner object's one schedule, on the Agents SDK Lifecycle job queue (ADR-032, tasks 3.6).
 *
 * Lifecycle (`agents/lifecycle`, agents 0.31.0) owns the Durable Object's queue of durable
 * jobs and its single physical alarm, and the thread host (`thread-host.ts`) keeps a wake
 * job per agent thread on the same queue. So no source here sets, reads or clears the
 * alarm (the alarm guard): every queue change re-arms it from the queue, and
 * `OwnerDO.alarm` only hands the runtime's alarm to Lifecycle, which drives the due jobs
 * and re-arms.
 *
 * Each kind of timed work is a lane: its own durable rows, which stay the truth, and one
 * Lifecycle job, `owner-schedule/<lane>`, that says only when to look again. Job ids are
 * one key space across Lifecycle's whole job table, shared with every capability, so ours
 * carry the schedule's prefix.
 * - `sync()` points each lane's job at the lane's next work, never in the past, cancels it
 *   when there is none, and leaves a job already in place alone: each push and each alarm
 *   set is a billed row written. Every door that changes a lane's rows calls it, and the
 *   object calls it on wake, so a deploy re-derives the jobs from the rows as stored. A
 *   job held later than its work (the lane's own backoff, or the memory-limit breaker's)
 *   is kept unless the rows now have work earlier than the job was set for.
 * - A run is one bounded pass (each lane takes a batch). It ends by setting its own job
 *   from the rows as they then stand, so more due work wakes the object again at once,
 *   and a door that changed the rows while it ran is never lost to a stale result: a
 *   same-id push made while the job dispatches supersedes the drive result (agents
 *   `job-queue.ts`, `applyOutcome`).
 * - A pass contains every outside call's failure in its own rows: a failing row is backed
 *   off in its row (a wake's or work row's due time, a chunk's retry time), so it neither
 *   loops the alarm nor rethrows. Lifecycle reads a rethrown transport error ("network
 *   connection lost") as a platform failure and rethrows the whole alarm, which would
 *   starve every later job on the object, every thread's wake among them. A pass throws only for
 *   a bug; then, after Lifecycle's in-process retries, its lane backs off `LANE_RETRY_S`.
 * - Passes are inline in Lifecycle's drive loop, which awaits each job in turn, so a pass
 *   is short: it hands long work off (a turn to pi's durable submit, `scheduled.ts`) and
 *   returns. Its job is single-flight, so an overlapping alarm (the deadman wake, a
 *   platform re-fire) skips it while it runs, for up to `LANE_HUNG_S`. Every pass fits
 *   that by wall clock, not by a batch size: it takes no new row once `LANE_PASS_S` has
 *   passed, and each outside call a row makes is bounded by `LANE_HUNG_S - LANE_PASS_S`
 *   (the work hand-off's `WORK_HANDOFF_MS`, each doorbell send's and revoke's
 *   `WAKE_SEND_MS`, which the lane holds itself, a chunk delete's remaining budget, never
 *   begun with less than `DELETE_FLOOR_MS` left), so a pass ends before an overlapping alarm
 *   may run it again. A pass writes its rows through the object's write gate a unit at a
 *   time (`gate.ts`), and so does each job set here: Lifecycle's job row is pushed inside
 *   a unit, so it never lands in a pi commit that could roll it back.
 * - Each lane fences its own work in its rows (a wake's claim lease and may-have-sent
 *   mark, a chunk's `deleting` state, a work row's claim), so a run Lifecycle repeats
 *   takes nothing the first took, and unknown work is never replayed.
 *
 * Lifecycle's memory-limit breaker is the object's, not a lane's: it counts consecutive
 * memory-limit resets across every capability's jobs. A strike in a lane backs off that
 * lane's job and every pending `recoveryLoop` job (the threads' wakes), and a sealing
 * strike deletes the executing job and purges those (agents `job-driver.ts`); a strike in a
 * thread's wake does the same to it. Our lanes' jobs are not `recoveryLoop`: they
 * re-derive from their rows, so a purged lane comes back at the next door or wake.
 *
 * Each alarm invocation writes the physical alarm four times: the startup fallback, the
 * deadman pre-arm before driving, a lane's own set when its pass ends, and Lifecycle's
 * final re-arm. Lifecycle's own storage is a bounded set: one job-table row per lane and
 * per working or sleeping thread, a wake-failure count per failing thread and a
 * memory-limit strike counter in the key-value store. It
 * counts toward the object's storage, never toward an owner bound, which count only the
 * object's own tables. Job ids are always given, so Lifecycle never draws one from its
 * random pool.
 */
import {
  type LifecycleJob, LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";

import type { Write } from "./gate";

/** The schedule's Lifecycle capability id, and the prefix of its job ids. */
export const SCHEDULE = "owner-schedule";
/** How long a lane whose pass failed for a bug waits before it runs again. */
export const LANE_RETRY_S = 300;
/** How long a lane's pass may run before an overlapping alarm treats it as hung. */
export const LANE_HUNG_S = 60;
/** How long a pass takes new rows; a row's own outside call then ends within the rest. */
export const LANE_PASS_S = 45;

/**
 * `run`'s result if it settles within `ms`, else "late": how a pass bounds a row's outside
 * call. `run` is never cancelled, so a late caller keeps the row fenced (a mark, a state).
 */
export async function inTime<T>(run: Promise<T>, ms: number): Promise<T | "late"> {
  if (ms === Infinity) {
    return run;  // unbounded: no timer, whatever a runtime makes of an Infinity delay
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<"late">((resolve) => {
    timer = setTimeout(() => resolve("late"), Math.max(0, ms));
  });
  try {
    return await Promise.race([run, late]);
  } finally {
    clearTimeout(timer);
  }
}

/** A lane's Lifecycle job id. */
export function laneJob(name: string): string {
  return `${SCHEDULE}/${name}`;
}

/** One kind of timed work, over its own rows. Times are in seconds. */
export interface Lane {
  /** When the lane next has work it can do, never before `now`; null for none. */
  next(now: number): number | null;
  /**
   * One short, bounded pass over what is due at `now`, taking no new row once the wall
   * clock (`Date.now()`, ms) reaches `until`. Each outside call's failure is kept in its
   * own row, backed off there; a throw is a bug, and fails the whole pass. Each of its
   * writes is a unit of the object's write gate.
   */
  run(now: number, until: number): Promise<unknown>;
}

/** The lane's work time, in ms, that a job was set for: its payload's, else its own time. */
function setFor(job: LifecycleJob): number {
  const payload = job.payload;
  const due = typeof payload === "object" && payload !== null
    ? (payload as { due?: unknown }).due : undefined;
  return typeof due === "number" && Number.isFinite(due) ? due : job.time;
}

export class Schedule extends LifecycleCapability {
  readonly #lanes: ReadonlyMap<string, Lane>;
  readonly #clock: () => number;
  /** The object's write gate, which every job set goes through. */
  readonly #write: Write;
  readonly #passMs: number;
  /** Whether every lane is halted: the owner is being erased (`erasure.ts`). A read. */
  readonly #halted: () => boolean;

  constructor(lanes: Readonly<Record<string, Lane>>, clock: () => number, write: Write,
              passMs: number = LANE_PASS_S * 1000, halted: () => boolean = () => false) {
    super(SCHEDULE);
    this.#lanes = new Map(Object.entries(lanes));
    this.#clock = clock;
    this.#write = write;
    this.#passMs = passMs;
    this.#halted = halted;
  }

  /** Point every lane's job at its next work, or cancel it; nothing while halted. */
  async sync(): Promise<void> {
    if (this.#halted()) {
      return;
    }
    for (const name of this.#lanes.keys()) {
      await this.#set(name, undefined);
    }
  }

  /**
   * Set lane `name`'s job from its rows: due at its next work, or cancelled. `hold` is a
   * run's own word: `undefined` for a door, which keeps a job in place or held later
   * (unless the rows now have earlier work); null for a pass that ended, which always
   * writes (its drive result would otherwise complete and delete the job) and clears any
   * hold; a time for a pass that failed, held until then. The rows are read and the job
   * row written in one unit of the write gate; Lifecycle re-arms the alarm after it.
   */
  async #set(name: string, hold: number | null | undefined): Promise<void> {
    const jobs = this.lifecycle.jobs;
    const id = laneJob(name);
    const [rearmed] = await this.#write((): [Promise<unknown>] | [] => {
      const job = jobs.get(id);
      const next = this.#lanes.get(name)!.next(this.#clock());
      if (next === null) {
        return job === undefined ? [] : [jobs.cancel(id)];
      }
      const due = Math.ceil(next * 1000);
      const time = hold === null || hold === undefined ? due
        : Math.max(due, Math.ceil(hold * 1000));
      if (hold === undefined && job !== undefined) {
        const was = setFor(job);
        if ((job.time === time && was === due) || (job.time > due && due >= was)) {
          return [];  // in place, or held later with no earlier work
        }
      }
      // Lifecycle writes the job row before it returns; the re-arm follows the unit.
      return [jobs.push({ id, fn: name, time, payload: { due }, singleflight: true,
                          hungTimeoutSeconds: LANE_HUNG_S })];
    });
    await rearmed;
  }

  /**
   * A lane's job is due: one bounded pass, then its job set from the rows as they stand.
   * While halted, no lane runs, and its job completes.
   */
  async onJob({ job }: LifecycleJobContext): Promise<LifecycleJobOutcome> {
    const lane = this.#lanes.get(job.fn);
    if (lane === undefined || job.id !== laneJob(job.fn) || this.#halted()) {
      return undefined;  // no lane of this code's, or none runs: done
    }
    await lane.run(this.#clock(), Date.now() + this.#passMs);
    await this.#set(job.fn, null);
    return undefined;  // superseded by the set above
  }

  /** A pass that failed through Lifecycle's retries: the lane waits `LANE_RETRY_S`. */
  async onJobError({ job }: LifecycleJobContext, _error: unknown): Promise<LifecycleJobOutcome> {
    if (!this.#lanes.has(job.fn) || job.id !== laneJob(job.fn)) {
      return undefined;
    }
    console.error(`the owner's ${job.fn} pass failed; it runs again later`);  // no ids or paths
    await this.#set(job.fn, this.#clock() + LANE_RETRY_S);
    return undefined;
  }
}
