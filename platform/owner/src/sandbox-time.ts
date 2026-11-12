/**
 * Sandbox time, metered per owner (slice P): how long each of the owner's sandboxes ran, and
 * each run in it, for the founder's cost view (E2B bills a running sandbox by the second).
 * It limits nothing: no door, no admission and no run reads it to refuse.
 *
 * - **Truth.** What E2B bills: its signed lifecycle events (paused, killed) carry each
 *   execution's start, its billed milliseconds, vCPUs and memory, which the doors verify and
 *   hand here (`billed`); each execution is counted once by its execution id (a pause and
 *   then a kill of the paused sandbox may both carry it), split across the UTC days it ran,
 *   into `sandbox_days`' billed columns. An event older than `BILLED_WINDOW_S` (by its own
 *   signed time) is not counted, and every execution counted within the window is kept (8
 *   bytes of its id's SHA-256), so a delivery replayed later is refused by its time, and one
 *   replayed sooner by its id; each live sandbox's last execution is kept until its kill
 *   (`sandbox_live`), so a kill of a paused sandbox however long after counts nothing again.
 *   Past `BILLED_KEPT` inside the window an execution is counted all the same, and tallied
 *   `unverified` on its day, which the view shows: it never quietly understates. It covers what the Worker did not cause: E2B's own
 *   timeout pauses and kills, a Worker lost mid-call, time kept warm.
 * - **Estimate, and the fallback.** The sandbox Worker is the one that starts, pauses, resumes and kills a
 *   sandbox (slice S's `SandboxControl`, which serializes them per owner), and it logs each
 *   as it happens, with its time: `start` (created) and `resume` begin a running span,
 *   `pause` and `kill` end it. The owner's object reads that log after its cursor
 *   (`SandboxService.meter`) and applies it here in one unit; the cursor then moves, so the
 *   log is read once (the Worker drops what the cursor passed).
 * - **When.** On the `sandbox` lane of the object's one schedule: as soon as a settled run
 *   has no sandbox time yet, and, while a span stands open, `METER_OPEN_S` after the last
 *   read, so a pause that came after the run's settle is in the day's sum within that.
 * - **Spans.** `sandbox_spans` holds each authority's running spans (one open at most) for
 *   as long as a run not yet metered may overlap them; each span is added to the days it
 *   covers (`sandbox_days`, UTC) as it closes.
 * - **A run's seconds** (`runs.sandbox_ms`, on its receipt as `sandbox_s`): the time its
 *   sandbox was running between the run's start and its settle, set once the log has been
 *   read past the settle. Runs sharing a sandbox each count their overlap; the sandbox's own
 *   seconds count it once.
 *
 * Bounds (`limits-audit.md`): `SPANS_KEPT` spans, the oldest closed one dropped past it
 * (a technical ceiling: years of an always-renewing sandbox); `DAYS_KEPT` days of sums.
 */
import { createHash } from "node:crypto";

import type { OwnerStorage, Write } from "./gate";
import type { Call, Door, OwnerState } from "./owner-do";
import type { BilledEvent } from "../../contract/src/owner";
import { Refusal } from "../../contract/src/refusal";
import { METER_BATCH, type MeterEvent } from "../../contract/src/sandbox";

/**
 * The runs' side of the meter, which only `runs.ts` reads and writes (the policy-boundary
 * guard): the settled runs not yet metered, each run's metered time, and the earliest start
 * a run not yet metered needs spans from.
 */
export interface RunMeter {
  /** Whether a settled run has no sandbox time yet. A read. */
  unmetered(): boolean;
  /** The settled runs not yet metered whose settle is at or before `readAt` (ms). A read. */
  settledBy(readAt: number): { id: string; authority: string; started_at: number | null;
                               settled_at: number }[];
  /** In a unit: a run's sandbox time, in ms. */
  metered(id: string, ms: number): void;
  /** The earliest start (s) of a run not yet metered, or null. A read. */
  oldestUnmetered(): number | null;
}

/** How long after a read an open span is read again. */
const METER_OPEN_S = 900;
/** When a read whose call failed is tried again. */
const METER_RETRY_S = 300;
/** Spans kept: an open one per authority and the closed ones a run not yet metered needs. */
export const SPANS_KEPT = 1000;
/** Days of sandbox seconds kept, past a year of the cost view. */
export const DAYS_KEPT = 400;
/**
 * How old an E2B event may be, by its own signed time, and still be counted: a day, past
 * E2B's three delivery attempts; a later one leaves the Worker's log as that execution's
 * time. The executions counted are kept as long, so a replay inside it is refused by id.
 */
export const BILLED_WINDOW_S = 86_400;
/** How far ahead of the object's clock an event's time may be: clocks drift. */
const BILLED_SKEW_S = 300;
/**
 * The executions kept to check inside the window, one every 0.86 s all day: past it a new
 * one is counted anyway, and tallied `unverified` (the 10 GB: 8 bytes and a time a row).
 */
export const BILLED_KEPT = 100_000;
/**
 * Live sandboxes whose last execution is kept until their kill: one an authority, and an
 * owner has at most 2,001 (itself, and `GRANT_LIMITS`' 1,000 free and 1,000 owner-set
 * clients; a test holds it there). Past it the oldest goes.
 */
export const LIVE_KEPT = 2_001;

/** 8 bytes of an E2B execution id's SHA-256: what is kept to count it once. */
function digest(executionId: string): ArrayBuffer {
  return Uint8Array.from(createHash("sha256").update(executionId).digest().subarray(0, 8))
    .buffer;
}

const KINDS = Object.freeze(["start", "resume", "pause", "kill"]);
const AUTHORITY = /^[A-Za-z0-9._:-]{1,64}$/;

/** A read of the log, checked whole: anything out of shape fails loudly. */
export function meterRead(value: unknown, after: number): { events: MeterEvent[]; next: number } {
  if (typeof value !== "object" || value === null || Array.isArray(value)
      || Object.keys(value).sort().join() !== "events,next") {
    throw new Error("the sandbox's meter answered out of shape");
  }
  const { events, next } = value as { events: unknown; next: unknown };
  if (!Array.isArray(events) || events.length > METER_BATCH || typeof next !== "number"
      || !Number.isSafeInteger(next) || next < after) {
    throw new Error("the sandbox's meter answered out of shape");
  }
  let last = after;
  for (const event of events as Record<string, unknown>[]) {
    if (typeof event !== "object" || event === null
        || Object.keys(event).sort().join() !== "at,authority,kind,seq"
        || typeof event["seq"] !== "number" || !Number.isSafeInteger(event["seq"])
        || event["seq"] <= last || event["seq"] > next
        || typeof event["authority"] !== "string" || !AUTHORITY.test(event["authority"])
        || typeof event["kind"] !== "string" || !KINDS.includes(event["kind"])
        || typeof event["at"] !== "number" || !Number.isSafeInteger(event["at"])
        || event["at"] < 0) {
      throw new Error("the sandbox's meter answered out of shape");
    }
    last = event["seq"];
  }
  return { events: events as MeterEvent[], next };
}

const DAY_MS = 86_400_000;

/** `YYYYMMDD` of the UTC day `at` (ms) falls in. */
function dayOf(at: number): string {
  return new Date(at).toISOString().slice(0, 10).replaceAll("-", "");
}

/** Where the `sandbox` lane reads the sandbox Worker's log, and the gate it writes through. */
interface MeterSource {
  /** The owner's log after `after`, as the sandbox Worker answers. */
  read(after: number): Promise<unknown>;
  readonly write: Write;
}

export class SandboxTime {
  readonly #storage: OwnerStorage;
  readonly #runs: RunMeter;
  readonly #source: MeterSource;

  constructor(storage: OwnerStorage, runs: RunMeter, source: MeterSource) {
    this.#storage = storage;
    this.#runs = runs;
    this.#source = source;
  }

  /**
   * The `sandbox` lane's pass: the sandbox Worker's log read after the cursor, a batch at a
   * time until the pass's end, each applied in a unit. A read that fails backs the lane
   * off in its row; the pass completes.
   */
  async run(_now: number, until: number): Promise<void> {
    do {
      const { after } = this.cursor();
      let read: { events: ReturnType<typeof meterRead>["events"]; next: number };
      const readAt = Date.now();
      try {
        read = meterRead(await this.#source.read(after), after);
      } catch {
        console.error("the sandbox meter's read failed; it is tried again later");
        await this.#source.write(() => this.failed(Date.now() / 1000));
        return;
      }
      await this.#source.write(() => this.#storage.transactionSync(
        () => this.apply(read.events, read.next, readAt)));
      if (read.events.length < METER_BATCH) {
        return;
      }
    } while (Date.now() < until);
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** The log's cursor and when it was last read (ms; 0 never). A read. */
  cursor(): { after: number; read_at: number; retry_at: number | null } {
    return this.#sql.exec<{ after: number; read_at: number; retry_at: number | null }>(
      "SELECT after, read_at, retry_at FROM sandbox_meter").one();
  }

  /** When the lane next has work, in seconds, or null. A read. */
  next(now: number): number | null {
    const { read_at: readAt, retry_at: retryAt } = this.cursor();
    const unmetered = this.#runs.unmetered();
    const open = this.#sql.exec("SELECT 1 FROM sandbox_spans WHERE ended_at IS NULL LIMIT 1")
      .toArray().length > 0;
    const due = unmetered ? now : open ? readAt / 1000 + METER_OPEN_S : null;
    return due === null ? null : Math.max(now, due, retryAt ?? 0);
  }

  /** In a unit: a read failed; the lane tries again `METER_RETRY_S` later. */
  failed(now: number): void {
    this.#sql.exec("UPDATE sandbox_meter SET retry_at = ?", now + METER_RETRY_S);
  }

  /**
   * In a unit: the log read at `readAt` (ms) applied in order, the cursor moved to `next`,
   * then every settled run the log now covers metered, and what no unmetered run needs let go.
   */
  apply(events: readonly MeterEvent[], next: number, readAt: number): void {
    for (const event of events) {
      if (event.kind === "start" || event.kind === "resume") {
        this.#sql.exec("INSERT INTO sandbox_spans (authority, started_at, ended_at) " +
                         "SELECT ?, ?, NULL WHERE NOT EXISTS (SELECT 1 FROM sandbox_spans " +
                         "WHERE authority = ? AND ended_at IS NULL)",
                       event.authority, event.at, event.authority);
      } else {
        const open = this.#sql.exec<{ started_at: number }>(
          "SELECT started_at FROM sandbox_spans WHERE authority = ? AND ended_at IS NULL",
          event.authority).toArray()[0];
        if (open !== undefined) {
          const end = Math.max(open.started_at, event.at);
          this.#sql.exec("UPDATE sandbox_spans SET ended_at = ? WHERE authority = ? AND " +
                           "started_at = ?", end, event.authority, open.started_at);
          this.#addDays(open.started_at, end);
        }
      }
    }
    this.#sql.exec("UPDATE sandbox_meter SET after = ?, read_at = ?, retry_at = NULL",
                   next, readAt);
    this.#meterRuns(readAt);
    this.#letGo();
  }

  /** The span [from, to) (ms) added to each UTC day it covers. */
  #addDays(from: number, to: number): void {
    for (let at = from; at < to;) {
      const end = Math.min(to, Math.floor(at / DAY_MS) * DAY_MS + DAY_MS);
      this.#sql.exec("INSERT INTO sandbox_days (day, ms) VALUES (?, ?) ON CONFLICT (day) " +
                       "DO UPDATE SET ms = ms + excluded.ms", dayOf(at), end - at);
      at = end;
    }
    this.#sql.exec("DELETE FROM sandbox_days WHERE day NOT IN (SELECT day FROM sandbox_days " +
                     "ORDER BY day DESC LIMIT ?)", DAYS_KEPT);
  }

  /** Each settled run whose settle the log covers: its sandbox's running time inside it. */
  #meterRuns(readAt: number): void {
    for (const run of this.#runs.settledBy(readAt)) {
      const ms = run.started_at === null ? 0
        : this.#overlap(run.authority, run.started_at * 1000, run.settled_at * 1000, readAt);
      this.#runs.metered(run.id, ms);
    }
  }

  /** How long `authority`'s sandbox ran within [from, to) (ms), an open span to `readAt`. */
  #overlap(authority: string, from: number, to: number, readAt: number): number {
    const row = this.#sql.exec<{ ms: number | null }>(
      "SELECT SUM(MAX(0, MIN(coalesce(ended_at, ?), ?) - MAX(started_at, ?))) AS ms " +
        "FROM sandbox_spans WHERE authority = ? AND started_at < ? AND " +
        "coalesce(ended_at, ?) > ?", readAt, to, from, authority, to, readAt, from).one();
    return Math.round(row.ms ?? 0);
  }

  /** Closed spans no run left to meter can overlap, and past `SPANS_KEPT` the oldest. */
  #letGo(): void {
    const oldest = this.#runs.oldestUnmetered();
    this.#sql.exec("DELETE FROM sandbox_spans WHERE ended_at IS NOT NULL AND ended_at <= ?",
                   oldest === null ? Number.MAX_SAFE_INTEGER : oldest * 1000);
    this.#sql.exec("DELETE FROM sandbox_spans WHERE ended_at IS NOT NULL AND started_at IN " +
                     "(SELECT started_at FROM sandbox_spans WHERE ended_at IS NOT NULL " +
                     "ORDER BY started_at DESC LIMIT -1 OFFSET ?)", SPANS_KEPT);
  }

  /**
   * In a unit: one execution E2B billed, at `now` (ms), counted once by its execution id,
   * each UTC day it ran given its part of the billed ms, vCPU-ms and MB-ms. Whether it was
   * counted: not when the event is older than the window (or ahead of the clock past the
   * skew), or when its execution was counted already (kept in the window, or its sandbox's
   * last until the kill). Past `BILLED_KEPT` it is counted, and tallied `unverified`.
   */
  billed(event: BilledEvent, now: number): boolean {
    const floor = now - BILLED_WINDOW_S * 1000;
    if (event.at < floor || event.at > now + BILLED_SKEW_S * 1000) {
      return false;
    }
    this.#sql.exec("DELETE FROM sandbox_billed WHERE at < ?", floor);
    const key = digest(event.execution_id);
    const seen = this.#sql.exec("SELECT 1 FROM sandbox_live WHERE sandbox_id = ? AND " +
                                  "digest = ?", event.sandbox_id, key).toArray().length > 0
      || this.#sql.exec("SELECT 1 FROM sandbox_billed WHERE digest = ?", key).toArray()
        .length > 0;
    if (event.kind === "killed") {
      this.#sql.exec("DELETE FROM sandbox_live WHERE sandbox_id = ?", event.sandbox_id);
    } else {
      this.#sql.exec("INSERT INTO sandbox_live (sandbox_id, digest, at) VALUES (?, ?, ?) " +
                       "ON CONFLICT (sandbox_id) DO UPDATE SET digest = excluded.digest, " +
                       "at = excluded.at", event.sandbox_id, key, now);
      this.#sql.exec("DELETE FROM sandbox_live WHERE sandbox_id IN (SELECT sandbox_id FROM " +
                       "sandbox_live ORDER BY at DESC LIMIT -1 OFFSET ?)", LIVE_KEPT);
    }
    if (seen) {
      return false;
    }
    const kept = this.#sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM sandbox_billed")
      .one().n < BILLED_KEPT;
    if (kept) {
      this.#sql.exec("INSERT INTO sandbox_billed (digest, at) VALUES (?, ?)", key, event.at);
    } else {
      console.error("sandbox_billed is full inside its window; counted unverified");
      this.#sql.exec("INSERT INTO sandbox_days (day, ms, unverified) VALUES (?, 0, 1) ON " +
                       "CONFLICT (day) DO UPDATE SET unverified = unverified + 1",
                     dayOf(event.at));
    }
    const end = event.started_at + event.execution_ms;
    for (let at = event.started_at; at < end;) {
      const until = Math.min(end, Math.floor(at / DAY_MS) * DAY_MS + DAY_MS);
      const ms = until - at;
      this.#sql.exec("INSERT INTO sandbox_days (day, ms, billed_ms, vcpu_ms, mb_ms) VALUES " +
                       "(?, 0, ?, ?, ?) ON CONFLICT (day) DO UPDATE SET billed_ms = billed_ms + " +
                       "excluded.billed_ms, vcpu_ms = vcpu_ms + excluded.vcpu_ms, mb_ms = " +
                       "mb_ms + excluded.mb_ms", dayOf(at), ms, ms * event.vcpu,
                     ms * event.memory_mb);
      at = until;
    }
    this.#sql.exec("DELETE FROM sandbox_days WHERE day NOT IN (SELECT day FROM sandbox_days " +
                     "ORDER BY day DESC LIMIT ?)", DAYS_KEPT);
    return true;
  }

  /**
   * The owner's sandbox time by day, newest first: the Worker's estimate (`seconds`), what
   * E2B billed (`billed_seconds`, `vcpu_seconds`, `gb_seconds`), and how many of the day's
   * billed executions were counted with no id kept to check them against
   * (`unverified_executions`). A read.
   */
  days(limit: number): { day: string; seconds: number; billed_seconds: number;
                         vcpu_seconds: number; gb_seconds: number;
                         unverified_executions: number }[] {
    return this.#sql.exec<{ day: string; ms: number; billed_ms: number; vcpu_ms: number;
                            mb_ms: number; unverified: number }>(
      "SELECT day, ms, billed_ms, vcpu_ms, mb_ms, unverified FROM sandbox_days ORDER BY day " +
        "DESC LIMIT ?", limit).toArray()
      .map((row) => ({ day: row.day, seconds: row.ms / 1000, billed_seconds: row.billed_ms / 1000,
                       vcpu_seconds: row.vcpu_ms / 1000, gb_seconds: row.mb_ms / 1024 / 1000,
                       unverified_executions: row.unverified }));
  }
}

/** A billed event as the doors handed it, checked whole: anything else fails loudly. */
function billedEvent(value: unknown): BilledEvent {
  const event = value as Record<string, unknown>;
  const whole = (v: unknown) => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
  const id = (v: unknown) => typeof v === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(v);
  if (typeof value !== "object" || value === null || Object.keys(value).sort().join()
        !== "at,authority,execution_id,execution_ms,id,kind,memory_mb,sandbox_id,started_at,vcpu"
      || !id(event["id"]) || !id(event["execution_id"]) || !id(event["sandbox_id"])
      || !whole(event["at"])
      || typeof event["authority"] !== "string" || !AUTHORITY.test(event["authority"])
      || (event["kind"] !== "paused" && event["kind"] !== "killed")
      || !whole(event["started_at"]) || !whole(event["execution_ms"])
      || !whole(event["vcpu"]) || !whole(event["memory_mb"])) {
    throw new Error("a billed sandbox event is out of shape");
  }
  return event as unknown as BilledEvent;
}

// -- THE DOORS: the `sandbox-time` rows' handlers (`contract/doors.json`) -------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** The `sandbox-time` doors' handlers, by door. */
export const sandboxTimeDoors = Object.freeze({
  async sandboxBilled(o: OwnerState, door: Door<BilledEvent>): Promise<{ counted: boolean }> {
    if (o.gone()) {
      return { counted: false };
    }
    const checked = billedEvent(door.request);
    return o.door(() => ({ counted: o.sandboxTime.billed(checked, Date.now()) }));
  },
  sandboxDays(o: OwnerState, { request }: Call<{ days?: number }>): {
    days: { day: string; seconds: number; billed_seconds: number; vcpu_seconds: number;
            gb_seconds: number; unverified_executions: number }[] } {
    const days = request?.days ?? 31;
    if (typeof days !== "number" || !Number.isSafeInteger(days) || days < 1 || days > 400) {
      throw new Refusal("invalid", "days is 1 to 400");
    }
    return { days: o.sandboxTime.days(days) };
  },
});
