/**
 * Generations (slice P; `backup_shape_research.md` (d)): the owner export and the nightly
 * manifest are one artifact, a generation. Each is a fuzzy base copy of every exported row
 * (`export.ts` `readPage`) and the change log captured while the base was read
 * (`changelog.ts`), shipped to R2 as it goes, and consistent at its end E: read last line
 * per key wins, it is the owner at E however the owner wrote meanwhile. Nothing has to catch
 * up with the present, so a generation always settles; nothing is deleted before R2 holds
 * it and every object is immutable once recorded, so a crash anywhere repeats only what was
 * not yet acknowledged. It is the `manifest` lane of the object's one schedule
 * (`schedule.ts`): no platform cron looks at every owner.
 *
 * - **Only on change.** Every write unit of the object but a quiet one (this lane's own, an
 *   export's bookkeeping, a session's renewal: `gate.ts`) marks the owner changed (`mark`);
 *   a generation begins by clearing it. With one finished and no change since, the lane has
 *   no job: an idle owner's object is never woken for it.
 * - **When.** Nightly, from its day's start in UTC plus the owner's own offset in the night
 *   (`MANIFEST_SPREAD_S`), the first the night after the object first serves its owner; or
 *   at once, when an export asks and none is running or reusable. One a pass did not finish
 *   goes on `MANIFEST_CONTINUE_S` later, never at once.
 * - **What.** Its base parts (`o/<ns>/m/<gen>/<n>`), whole lines cut at the chunk size, and
 *   its log segments (`o/<ns>/m/<gen>/l/<a>-<b>`), each under the owner's data key; its
 *   root (`o/<ns>/m/<gen>`), written last, names them, the log's span and the wrapped key,
 *   so a recovery needs only the files Worker's keyring and the bucket.
 * - **E.** The unit that reads the base's last page fixes E, the log's last seq then; the
 *   unit that records that page's part, after R2 holds it, keeps E and turns capture off. A
 *   crash between the two reads the last pages again and fixes E anew, capture still on;
 *   what was logged past E meanwhile is shipped as the tail (no segment spans E), so a later
 *   E finds it, and the root never names it.
 * - **Given up, never looped.** Past `MANIFEST_PARTS` objects, or when the unshipped log
 *   passed `LOG_BYTES` (an R2 outage), its objects are deleted, the owner is told once, and
 *   the lane waits for the owner's next change. The owner's writes always land.
 * - **Kept.** The newest finished generation, the one being written, each an open export
 *   pins (`owner_exports`, `EXPORT_LIFE_S`), and the one a restore holds (`pinned`, from its
 *   pin to its end, `restore.ts`); and, while the newest's log is past `LOG_BYTES`
 *   (which a restore refuses to stage, `restore.ts`), the latest older one a restore can load
 *   (`fallback`, `restorableAt`): its log within, begun within `RESTORABLE_LIFE_S`. That is
 *   the one list, `#retained`. Every other is deleted from R2, root first. No garbage a kept
 *   one names is collected early (`protectsSince`), and plain collection reaches none a
 *   restorable superseded one names. With none a restore can load, the owner is told
 *   (`MANIFEST_UNRESTORABLE`) and `restore.source` names none restorable.
 * - **Erasure.** The generations carry the owner's wrapped key, so the erasure's prefix
 *   delete takes them with the chunks, and the mark has already halted this lane.
 *
 * Nothing here limits the owner: a generation is the platform's recovery copy and the
 * owner's export, bounded by the object it copies (`limits-audit.md`).
 */
import { randomHex } from "../../contract/src/ids";
import { createHash } from "node:crypto";

import {
  CHUNK_BYTES, GC_GRACE_S, LOG_BYTES, MANIFEST_PARTS, SLOT_WAIT_MS,
} from "../../contract/src/limits";
import type { ChangeLog } from "./changelog";
import {
  chunkLine, type Cursor, decodeCursor, encodeCursor, encodeLines, endLine, EXPORT_PAGE_BYTES,
  type Lines,
  readPage,
} from "./export";
import type { ChunkStore, ManifestRoot } from "../../contract/src/files";
import type { ChunkSpace } from "./files";
import type { OwnerStorage, Write } from "./gate";
import { BURST_GATHER_BYTES, BURST_UNITS, ownerSlots, SHIP_UNITS } from "./holds";
import { isSlotBusy, type Slot, slotBusy, type SlotPool } from "../../contract/src/slots";

/** The hours of the night owners' manifests spread over, from the day's start in UTC. */
const MANIFEST_SPREAD_S = 4 * 3600;
/**
 * How long after a generation last finished or began the generations kept protect the
 * garbage they name from early collection (`protectsSince`, `files.ts`): two nights. An
 * owner that changes has a new one each night (one `DAY_S` after the last began, at the same
 * offset), and one more night covers a generation that failed once and ran the next night;
 * the longest generation, the worst stream (22.2 GB) at the reviewer's lowest read rate
 * (10 MB/s) and the lane's duty cycle (45 s a pass, 5 s between), is under 42 minutes,
 * inside it. Past it no generation has finished or begun, so none kept is a recovery point
 * to keep garbage for, and early collection holds the owner's figure again.
 */
export const MANIFEST_PROTECT_S = 2 * 86_400;
/** When a pass whose R2 call failed tries again. */
const MANIFEST_RETRY_S = 300;
/** When a generation a pass did not finish goes on: never at once. */
export const MANIFEST_CONTINUE_S = 5;
/**
 * The stream's bytes one burst gathers, with no outside call, before it puts them as parts:
 * two parts' worth, counted as the parts hold them (each line's UTF-8 and its newline), so
 * a burst holds at most this and its last page (`export.test.ts`, "a generation at
 * production sizes", holds a burst of three-byte characters to it), once as its pages'
 * buffers (one a page, `encodeLines`, never an array a line) and once as parts: within
 * `BURST_UNITS` of the isolate's pool, which it holds from its first page to its last part's
 * put (`holds.ts`; the memory leg's "a generation's burst" holds it there).
 */
const GATHER_BYTES = BURST_GATHER_BYTES;
/**
 * When a pass whose burst or log ship found the pool full goes on: the turnover of a full queue
 * (`SLOT_WAIT_MS`), as the archive's lane's (`ARCHIVE_BUSY_RETRY_S`). Not a failure.
 */
export const MANIFEST_BUSY_RETRY_S = SLOT_WAIT_MS / 1000;
/**
 * A generation's sizes: a base page's bytes, a part's (`CHUNK_BYTES`, the most the files
 * Worker takes), a burst's. Every lane cuts at these, a test's too, its fixtures sized from
 * them: one burst's cut within a third of the CPU limit, and linear
 * (`guards/backup-cut.test.ts`); an owner of several bursts finished and read back exactly,
 * its row under a row's 2 MB (`export.test.ts`, "a generation at production sizes").
 */
export const GENERATION_SIZES = Object.freeze({
  page: EXPORT_PAGE_BYTES, part: CHUNK_BYTES, gather: GATHER_BYTES });
/** The notice an owner is given, once, when its generation passes `MANIFEST_PARTS`. */
export const MANIFEST_CEILING = "manifest_ceiling";
/** The notice an owner is given, once, when its unshipped log passed `LOG_BYTES`. */
export const MANIFEST_LOG_OVERFLOW = "manifest_log_overflow";
/**
 * The notice an owner is given, once, when a migration ran while a generation was under way
 * (review L5-2): its base and log are of the old schema, so it is given up, never mixed, and
 * a fresh one is begun under the new.
 */
const MANIFEST_SCHEMA = "manifest_schema";
/**
 * The notice an owner is given, once, when one line of its stream (one row, as JSON) is
 * longer than any part may be (`CHUNK_BYTES`): the files Worker takes no such part, so the
 * generation is given up, loudly, and the lane waits for the owner's next change.
 * Unreachable by the schema's bounds (the widest row image, `IMAGE_MAX`, is under a
 * chunk), kept so a later wider column fails loudly.
 */
export const MANIFEST_LINE = "manifest_line";
/**
 * The notice an owner is given, once until one can be restored again, when no generation can
 * be (`restorableAt`): each since the last that could was written while more than
 * `LOG_BYTES` changed, and that one passed `RESTORABLE_LIFE_S`.
 */
export const MANIFEST_UNRESTORABLE = "manifest_unrestorable";
/** A generation's recorded cursor once its base is read: only its log is left. */
const ENDED = "ended";
const DAY_S = 86_400;
/**
 * How long a superseded generation stands as one a restore can load, from its start: a day
 * inside `GC_GRACE_S`. Every chunk it names was live when it began, so plain collection
 * reaches none before it is `GC_GRACE_S` old, and garbage is never kept longer: deleted data
 * leaves within the 30 days the owner is promised. Past it the generation is not offered,
 * pinned or kept as the fallback, and the lane, woken at that time, prunes it; the day is the
 * lane's nightly cadence, so even a missed wake prunes it before then. No later generation
 * can be made restorable instead: one is begun each night after a change, and the fallback
 * stands only while every one since was written while more than `LOG_BYTES` changed.
 */
export const RESTORABLE_LIFE_S = GC_GRACE_S - DAY_S;

/** The day `at` (seconds) falls in, UTC, as `YYYYMMDD`. */
export function manifestDay(at: number): string {
  return new Date(at * 1000).toISOString().slice(0, 10).replaceAll("-", "");
}

/** The start of `day` (`YYYYMMDD`), UTC, in seconds. */
function dayStart(day: string): number {
  return Date.UTC(Number(day.slice(0, 4)), Number(day.slice(4, 6)) - 1,
                  Number(day.slice(6, 8))) / 1000;
}

/**
 * `units` of `pool` now, or refused `busy`: inside the alarm a lane never waits on the pool,
 * since the object's other lanes would wait with it (`owner-do.ts`'s `now`). A call that
 * does not fit at once leaves its queue at the next turn of the event loop.
 */
async function unitsNow(pool: SlotPool, units: number): Promise<Slot> {
  const turn = new AbortController();
  const timer = setTimeout(() => turn.abort(slotBusy()), 0);
  try {
    return await pool.acquire(units, "", turn.signal);
  } finally {
    clearTimeout(timer);
  }
}

/** The owner's own offset into the night, in seconds. */
export function spread(owner: string): number {
  return createHash("sha256").update(owner).digest().readUInt32BE(0) % MANIFEST_SPREAD_S;
}

/**
 * A burst's lines (its pages' `Lines`, in order) cut into parts of whole lines, each at most
 * `part` bytes, so an export streams them as they are. A line longer than `part` is a part
 * of its own, longer than `part`: past `CHUNK_BYTES` the lane gives such a generation up
 * (`MANIFEST_LINE`), since the files Worker takes no such part. Linear: each line's end
 * is read once and each byte copied once, as slices of its page's buffer, never the part
 * so far again (`guards/backup-cut.test.ts` holds it, and its bytes to the cut it
 * replaced).
 */
export function cutParts(pages: readonly Lines[], part: number): Uint8Array[] {
  const parts: Uint8Array[] = [];
  /** The part so far: slices of its pages' buffers, and their bytes. */
  let runs: Uint8Array[] = [];
  let held = 0;
  const close = () => {
    const out = new Uint8Array(held);
    let at = 0;
    for (const run of runs) {
      out.set(run, at);
      at += run.byteLength;
    }
    parts.push(out);
    runs = [];
    held = 0;
  };
  for (const { bytes, ends } of pages) {
    // This page's run in the part so far starts at `from`; its next line at `start`.
    let from = 0;
    let start = 0;
    for (const end of ends) {
      const sofar = held + start - from;
      if (sofar > 0 && sofar + end - start > part) {
        if (start > from) {
          runs.push(bytes.subarray(from, start));
          held += start - from;
        }
        close();
        from = start;
      }
      start = end;
    }
    if (start > from) {
      runs.push(bytes.subarray(from, start));
      held += start - from;
    }
  }
  if (held > 0) {
    close();
  }
  return parts;
}

/** One generation's row. */
type GenerationRow = {
  readonly gen: string;
  readonly day: string;
  readonly state: "writing" | "done";
  readonly cursor: string | null;
  readonly parts: number;
  readonly part_list: string;
  readonly segments: number;
  readonly segment_list: string;
  readonly bytes: number;
  readonly s: number;
  readonly schema: number;
  readonly e: number | null;
  readonly ended_at: number | null;
  readonly started_at: number;
  readonly finished_at: number | null;
  readonly retry_at: number | null;
  /** A finished generation's log through E, in bytes: its segments' sizes. */
  readonly log_bytes: number;
  /** Its place in the order generations were begun or adopted (`NEXT_SEQ`). */
  readonly seq: number;
};

/**
 * A generation's place, after every one kept: generations are ordered by when they were begun
 * or adopted, never by a clock. A clock that reads behind the last generation's start (another
 * machine's, one set back) would sort the new one as older, and its own finish would prune it
 * as superseded, the pinned one kept as the newest.
 */
const NEXT_SEQ = "(SELECT coalesce(max(seq), 0) + 1 FROM owner_backups)";

/** A generation's object an export reads at an index: a part, a segment, its end, or none yet. */
type GenerationObject =
  | { readonly kind: "part"; readonly part: number; readonly sha256: string }
  | { readonly kind: "segment"; readonly a: number; readonly b: number;
      readonly sha256: string }
  | { readonly kind: "end"; readonly line: string }
  | { readonly kind: "pending" };

/** The parts a part list names, in order. */
function partsOf(list: string): { sha256: string; size: number }[] {
  return list === "" ? [] : list.split(",").map((entry) => {
    const [sha256, size] = entry.split(":");
    return { sha256: sha256!, size: Number(size) };
  });
}

/** The segments a segment list names, in order. */
function segmentsOf(list: string): { a: number; b: number; sha256: string; size: number }[] {
  return list === "" ? [] : list.split(",").map((entry) => {
    const [span, sha256, size] = entry.split(":");
    const [a, b] = span!.split("-");
    return { a: Number(a), b: Number(b), sha256: sha256!, size: Number(size) };
  });
}

/**
 * Whether finished generation `row` of `done` can be restored at `now`: its log within
 * `LOG_BYTES`, which a restore stages (`restore.ts`), and, once superseded, begun within
 * `RESTORABLE_LIFE_S`, so plain collection has reached no chunk it names.
 */
function restorableAt(row: GenerationRow, done: readonly GenerationRow[], now: number): boolean {
  return row.log_bytes <= LOG_BYTES
    && (row.gen === done.at(-1)?.gen || row.started_at > now - RESTORABLE_LIFE_S);
}

/**
 * The finished generation kept behind the newest while the newest cannot be restored: the
 * latest older one that can, so one a restore can load stands while one does; null while the
 * newest can be.
 */
function fallback(done: readonly GenerationRow[], now: number): GenerationRow | null {
  const newest = done.at(-1);
  if (newest === undefined || restorableAt(newest, done, now)) {
    return null;
  }
  return done.slice(0, -1).reverse().find((row) => restorableAt(row, done, now)) ?? null;
}

/** What the manifest lane runs on. */
interface BackupParts {
  readonly storage: OwnerStorage;
  /** The write gate's quiet units: this lane's own writes mark nothing changed. */
  readonly write: Write;
  readonly store: ChunkStore;
  /** The change log a generation captures (`changelog.ts`). */
  readonly log: ChangeLog;
  /** The owner's namespace and wrapped key, made on first use (`Files.space`). */
  readonly space: () => Promise<ChunkSpace>;
  readonly owner: () => string;
  /** Whether the object serves its owner yet (its first door ran): none before, nothing to keep. */
  readonly serves: () => boolean;
  readonly schema: number;
  /** Whether the owner is being erased: a pass under way stops before its next put. */
  readonly halted: () => boolean;
  /** The generation a restore holds (`restore.ts`), which no prune deletes; null for none. */
  readonly pinned: () => string | null;
  /** In a unit: the owner told something its work reports (`notices.ts`). */
  readonly notice: (type: string, detail: string) => void;
}

export class Backups {
  readonly #parts: BackupParts;
  /** The pass under way, which a second asked meanwhile joins (`run`). */
  #pass: Promise<void> | null = null;

  constructor(parts: BackupParts) {
    this.#parts = parts;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#parts.storage.sql;
  }

  /** One of this lane's units: quiet, so it marks nothing changed. */
  #write<T>(unit: () => T): Promise<T> {
    return this.#parts.write(unit);
  }

  /**
   * In a unit that wrote (the write gate's `onChange`, never a quiet unit): the owner changed
   * since the generation began. Whether this was the first change since, which sets the job.
   */
  mark(sql: Pick<SqlStorage, "exec">): boolean {
    return sql.exec(
      "UPDATE owner_backup_state SET changed = 1 WHERE changed = 0 RETURNING changed")
      .toArray().length > 0;
  }

  #changed(): boolean {
    return this.#sql.exec<{ changed: number }>("SELECT changed FROM owner_backup_state").one()
      .changed === 1;
  }

  #rows(): GenerationRow[] {
    return this.#sql.exec<GenerationRow>(
      "SELECT * FROM owner_backups ORDER BY seq, started_at").toArray();
  }

  /** Until when (s) an open export pins generation `gen` from deletion, or null. A read. */
  #pinned(gen: string, now: number): number | null {
    return this.#sql.exec<{ until: number | null }>(
      "SELECT MAX(ends_at) AS until FROM owner_exports WHERE gen = ? AND ends_at > ?", gen, now)
      .one().until;
  }

  /**
   * The superseded generations a prune may delete once no export pins them: no restore's, no
   * fallback. A read.
   */
  #prunable(done: readonly GenerationRow[], now: number): GenerationRow[] {
    const kept = [fallback(done, now)?.gen, this.#parts.pinned()];
    return done.slice(0, -1).filter((row) => !kept.includes(row.gen));
  }

  /** The prunable generations no export pins at `now`: the prune's to delete. A read. */
  #superseded(done: readonly GenerationRow[], now: number): GenerationRow[] {
    return this.#prunable(done, now).filter((row) => this.#pinned(row.gen, now) === null);
  }

  /** Whether finished generation `gen` can be restored at `now` (`restorableAt`). A read. */
  restorable(gen: string, now: number): boolean {
    const done = this.#rows().filter((row) => row.state === "done");
    const row = done.find((each) => each.gen === gen);
    return row !== undefined && restorableAt(row, done, now);
  }

  /**
   * In a quiet unit: the owner told once, and the log written, when generations are finished
   * but none can be restored at `now`; told again only once one can be (`#finish`).
   */
  #tellUnrestorable(now: number): void {
    const done = this.#rows().filter((row) => row.state === "done");
    if (done.length === 0 || done.some((row) => restorableAt(row, done, now))
        || this.#sql.exec("UPDATE owner_backup_state SET told = 1 WHERE told = 0 RETURNING told")
          .toArray().length === 0) {
      return;
    }
    console.error("no generation of this owner can be restored");
    this.#parts.notice(MANIFEST_UNRESTORABLE, "No recovery copy of your data can be restored " +
      `right now: more than ${LOG_BYTES / 2 ** 20} MB of your data changed while each recent ` +
      "copy was taken, more than a restore takes, and no older copy is recent enough to keep. " +
      "Your data is intact; export it to keep a copy. A new copy is taken each night your " +
      "data changes.");
  }

  /**
   * The generations kept at `now`, oldest first: the one being written, the newest finished,
   * each an open export pins, the one a restore holds and the fallback. A read.
   */
  #retained(rows: readonly GenerationRow[], now: number): GenerationRow[] {
    const gone = this.#superseded(rows.filter((row) => row.state === "done"), now)
      .map((row) => row.gen);
    return rows.filter((row) => !gone.includes(row.gen));
  }

  /**
   * When (s) the oldest generation kept at `now` began, while one finished or began within
   * `MANIFEST_PROTECT_S`; else null. A kept generation names only chunks live when it began or
   * since, so garbage let go after this is never collected early (`files.ts`). A read.
   */
  protectsSince(now: number): number | null {
    const rows = this.#rows();
    if (Math.max(...rows.map((row) => row.finished_at ?? row.started_at))
          < now - MANIFEST_PROTECT_S) {
      return null;
    }
    return Math.min(...this.#retained(rows, now).map((row) => row.started_at));
  }

  /** The finished generations, oldest first. A read. */
  manifests(): { gen: string; day: string; parts: number; segments: number;
                 started_at: number; finished_at: number; log_bytes: number }[] {
    return this.#rows().filter((row) => row.state === "done").map((row) => ({
      gen: row.gen, day: row.day, parts: row.parts, segments: row.segments,
      started_at: row.started_at, finished_at: row.finished_at!, log_bytes: row.log_bytes }));
  }

  /** When the lane next has work, in seconds, never before `now`; null before any owner. */
  next(now: number): number | null {
    if (!this.#parts.serves()) {
      return null;
    }
    const rows = this.#rows();
    const retry = (at: number | null) => Math.max(now, at ?? now);
    const writing = rows.find((row) => row.state === "writing");
    if (writing !== undefined) {
      return retry(writing.retry_at);
    }
    const done = rows.filter((row) => row.state === "done");
    // A superseded generation goes once no export pins it, but a restore's and the fallback,
    // which goes once it passes RESTORABLE_LIFE_S.
    const older = this.#prunable(done, now).map((row) => this.#pinned(row.gen, now) ?? now);
    const kept = fallback(done, now);
    const nightly = done.length > 0 && !this.#changed() ? null
      : Math.max(now, dayStart(done.at(-1)?.day ?? manifestDay(now)) + DAY_S
                 + spread(this.#parts.owner()));
    const times = [...older.map(retry), nightly,
                   kept === null ? null : retry(kept.started_at + RESTORABLE_LIFE_S)]
      .filter((at): at is number => at !== null);
    return times.length === 0 ? null : Math.min(...times);
  }

  /**
   * In a quiet unit: the generation an export at `now` reads: the one running; else the
   * newest finished, while nothing changed since it began; else a new one begun now.
   */
  forExport(now: number): string {
    const rows = this.#rows();
    const writing = rows.find((row) => row.state === "writing");
    if (writing !== undefined) {
      return writing.gen;
    }
    const newest = rows.filter((row) => row.state === "done").at(-1);
    return newest !== undefined && !this.#changed() ? newest.gen : this.#open(now);
  }

  /**
   * The object of generation `gen` at `index` as it now stands: a base part once recorded,
   * a log segment once the base is read and the segment recorded, then its end once it is
   * done; `pending` until then. For a finished generation its root's lists (`root`). A read.
   */
  object(gen: string, index: number,
         root: { parts: readonly { sha256: string }[];
                 segments: readonly { a: number; b: number; sha256: string }[] } | null):
      GenerationObject | null {
    const row = this.#rows().find((each) => each.gen === gen);
    if (row === undefined) {
      return null;
    }
    const parts = row.state === "done" ? root?.parts ?? [] : partsOf(row.part_list);
    const segments = (row.state === "done" ? root?.segments ?? [] : segmentsOf(row.segment_list))
      .filter((segment) => row.e === null || segment.b <= row.e);
    if (index < parts.length) {
      return { kind: "part", part: index, sha256: parts[index]!.sha256 };
    }
    if (row.cursor !== ENDED) {
      return { kind: "pending" };
    }
    const at = index - parts.length;
    if (at < segments.length) {
      const { a, b, sha256 } = segments[at]!;
      return { kind: "segment", a, b, sha256 };
    }
    return row.state === "done" && at === segments.length
      ? { kind: "end", line: endLine(row.ended_at! * 1000, row.e!) }
      : row.state === "done" ? null : { kind: "pending" };
  }

  /**
   * In a unit: the generation a restore read this object from (`restore.ts`), recorded as its
   * newest finished one, as its root lists it: kept, read by an export, and superseded by the
   * next one as any is.
   */
  adopt(root: ManifestRoot): void {
    const at = root.at / 1000;
    this.#sql.exec(
      "INSERT INTO owner_backups (gen, day, state, cursor, parts, part_list, segments, " +
        "segment_list, bytes, s, schema, e, ended_at, started_at, finished_at, log_bytes, seq) " +
        `VALUES (?, ?, 'done', ?, ?, '', ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ${NEXT_SEQ})`, root.gen,
      manifestDay(at), ENDED, root.parts.length, root.segments.length,
      [...root.parts, ...root.segments].reduce((sum, object) => sum + object.size, 0), root.s,
      this.#parts.schema, root.e, at, at, at,
      root.segments.reduce((sum, segment) => sum + segment.size, 0));
  }

  /** Whether generation `gen` is finished (its root holds its lists). A read. */
  finished(gen: string): boolean {
    return this.#rows().some((row) => row.gen === gen && row.state === "done");
  }

  /** Whether generation `gen` is kept: being written or finished, not given up. A read. */
  kept(gen: string): boolean {
    return this.#rows().some((row) => row.gen === gen);
  }

  /** The generation being written, or null. A read. */
  writing(): string | null {
    return this.#rows().find((row) => row.state === "writing")?.gen ?? null;
  }

  /**
   * One pass: superseded generations no export pins deleted, then the one due begun or
   * carried on, until the wall clock reaches `until` (ms). One it did not finish goes on
   * `MANIFEST_CONTINUE_S` later; an R2 call that fails backs the lane off in its row
   * (`MANIFEST_RETRY_S`); the pass itself completes. One at a time: a pass asked while one runs
   * (the lane's, or a restore's pin carrying its fresh generation) joins it.
   */
  run(now: number, until: number): Promise<void> {
    this.#pass ??= this.#run(now, until).finally(() => {
      this.#pass = null;
    });
    return this.#pass;
  }

  async #run(now: number, until: number): Promise<void> {
    try {
      if (!await this.#prune(until)) {
        return;
      }
      const due = this.next(now);
      let writing = this.#rows().find((row) => row.state === "writing");
      if (writing === undefined && due !== null && now >= due && (
            this.#rows().filter((row) => row.state === "done").length === 0
            || this.#changed())) {
        const gen = await this.#write(() => this.#open(now));
        writing = this.#rows().find((row) => row.gen === gen);
      }
      if (writing !== undefined && !await this.#carry(writing, until)) {
        await this.#write(() => this.#sql.exec(
          "UPDATE owner_backups SET retry_at = ? WHERE gen = ? AND state = 'writing'",
          Date.now() / 1000 + MANIFEST_CONTINUE_S, writing.gen));
      }
    } catch (error) {
      if (isSlotBusy(error)) {
        // A burst or a segment's ship found the pool full: nothing of it was read or put; it
        // goes on once units turn over, never counted a failure.
        await this.#write(() => this.#sql.exec(
          "UPDATE owner_backups SET retry_at = ? WHERE state = 'writing'",
          Date.now() / 1000 + MANIFEST_BUSY_RETRY_S));
        return;
      }
      // An R2 call that failed, kept in the rows: the lane comes back after its retry.
      console.error("the generation's pass failed; it is tried again later");
      await this.#write(() => this.#sql.exec(
        "UPDATE owner_backups SET retry_at = ? WHERE state = 'writing' OR gen = " +
          "(SELECT gen FROM owner_backups ORDER BY seq, started_at LIMIT 1)",
        Date.now() / 1000 + MANIFEST_RETRY_S));
    } finally {
      // After what the pass did, so a give-up's own notice, the more telling one, comes first.
      if (!this.#parts.halted()) {
        await this.#write(() => this.#tellUnrestorable(Date.now() / 1000));
      }
    }
  }

  /** Every superseded generation no export pins deleted, root first; whether none is left. */
  async #prune(until: number): Promise<boolean> {
    const done = this.#rows().filter((row) => row.state === "done");
    for (const old of this.#superseded(done, Date.now() / 1000)) {
      if (Date.now() >= until || this.#parts.halted()) {
        return false;
      }
      const { ns } = await this.#parts.space();
      const root = await this.#parts.store.getManifestRoot(ns, old.gen);
      await this.#parts.store.deleteManifest(ns, old.gen, old.parts, root?.segments ?? []);
      await this.#write(() => this.#sql.exec("DELETE FROM owner_backups WHERE gen = ?",
                                             old.gen));
    }
    return true;
  }

  /**
   * In a quiet unit: a new generation begun at `now`, whatever the owner's change since the
   * last; its id. A restore's pin's (`restore.ts`), which needs one begun after its hold.
   */
  begin(now: number): string {
    return this.#open(now);
  }

  /** In a unit: a new generation begun at `now`, its capture on; its id. */
  #open(now: number): string {
    const gen = randomHex(8);
    this.#sql.exec("UPDATE owner_backup_state SET changed = 0");
    const s = this.#parts.log.begin(gen);
    this.#sql.exec(
      "INSERT INTO owner_backups (gen, day, state, cursor, parts, part_list, bytes, s, " +
        `schema, started_at, seq) VALUES (?, ?, 'writing', NULL, 0, '', 0, ?, ?, ?, ${NEXT_SEQ})`,
      gen,
      manifestDay(now), s, this.#parts.schema, now);
    return gen;
  }

  /**
   * The generation of `gen` given up (past `MANIFEST_PARTS`, its log past `LOG_BYTES`, a
   * migration while it ran, or its capture lost): its objects deleted, its row and its log
   * gone, and the owner told once (`why`, unless null) until one a restore can load is
   * written again (`#finish`). The lane waits for a change, but after a migration, which
   * begins a fresh one at once.
   */
  async #abandon(ns: string, row: GenerationRow, why: string | null): Promise<void> {
    console.error(`a generation was given up (${why ?? "its capture was lost"})`);
    const now = this.#rows().find((each) => each.gen === row.gen) ?? row;
    await this.#parts.store.deleteManifest(ns, row.gen, now.parts,
                                           segmentsOf(now.segment_list));
    await this.#write(() => this.#parts.storage.transactionSync(() => {
      this.#sql.exec("DELETE FROM owner_backups WHERE gen = ?", row.gen);
      if (this.#parts.log.state().gen === row.gen) {
        this.#parts.log.stop();
      }
      if (why !== null && this.#sql.exec(
            "UPDATE owner_backup_state SET told = 1 WHERE told = 0 RETURNING told")
            .toArray().length > 0) {
        this.#parts.notice(why, why === MANIFEST_CEILING
          ? `The nightly recovery copy of your data passed its ${MANIFEST_PARTS} parts and ` +
            "was not written. Your data is intact; export it to keep a copy."
          : why === MANIFEST_LINE
          ? "The nightly recovery copy of your data was not written: one of your records is " +
            "too large for one part of it. Your data is intact; it is tried again after your " +
            "next change."
          : why === MANIFEST_SCHEMA
          ? "The recovery copy of your data under way was begun again: an update changed how " +
            "your data is stored while it ran. Your data is intact."
          : "The nightly recovery copy of your data could not be written: storage was " +
            "unavailable for too long while it ran. Your data is intact; it is tried again " +
            "after your next change.");
      }
    }));
  }

  /**
   * The generation finished: its tail (segments past E) deleted, its root put naming the
   * segments through E, its row done (its lists in the root), its log gone.
   */
  async #finish(ns: string, key: string, gen: string): Promise<void> {
    const row = this.#rows().find((each) => each.gen === gen)!;
    const segments = segmentsOf(row.segment_list);
    const tail = segments.filter((segment) => segment.b > row.e!);
    if (tail.length > 0) {
      await this.#parts.store.deleteManifest(ns, gen, 0, tail);  // no root is put yet
    }
    const logged = segments.filter((segment) => segment.b <= row.e!);
    await this.#parts.store.putManifestRoot(ns, key, gen, partsOf(row.part_list), logged,
                                            { s: row.s, e: row.e! });
    const logBytes = logged.reduce((sum, segment) => sum + segment.size, 0);
    await this.#write(() => this.#parts.storage.transactionSync(() => {
      this.#sql.exec("UPDATE owner_backups SET state = 'done', finished_at = ?, part_list = " +
                       "'', segment_list = '', log_bytes = ? WHERE gen = ?", Date.now() / 1000,
                     logBytes, gen);
      if (logBytes <= LOG_BYTES) {
        this.#sql.exec("UPDATE owner_backup_state SET told = 0");
      }
      this.#tellUnrestorable(Date.now() / 1000);
      this.#parts.log.stop();
    }));
  }

  /**
   * Ship the generation's log through `through`, a segment at a time, until it is or the pass
   * ends: each pending segment recorded, put, then acknowledged with its list's entry. No
   * segment spans `cut` (E, once fixed): those past it are the tail, which only a base read
   * again after a crash needs, and which the root never names. Whether every seq through
   * `through` is shipped; null once past `MANIFEST_PARTS`. Each segment built and put holding
   * `SHIP_UNITS` of the pool, now or refused `busy` (the lane goes on after
   * `MANIFEST_BUSY_RETRY_S`); never inside a burst, which holds its parts meanwhile.
   */
  async #ship(ns: string, key: string, row: GenerationRow, through: number, until: number,
              cut: number | null = null): Promise<boolean | null> {
    const log = this.#parts.log;
    while (log.state().shipped < through) {
      if (Date.now() >= until || this.#parts.halted()) {
        return false;
      }
      const at = this.#rows().find((each) => each.gen === row.gen)!;
      if (at.parts + at.segments + 1 > MANIFEST_PARTS) {
        return null;
      }
      if (!await this.#shipOne(ns, key, row, through, cut)) {
        return true;  // nothing left to ship through `through`
      }
    }
    return true;
  }

  /**
   * One segment of the log shipped (`#ship`), in a frame of its own, so its bytes are let go
   * before the next is built (rvburst X2: held in `#ship`'s frame, segment N's bytes stood
   * beside segment N+1's build): its units taken now or refused `busy` (before `pending`
   * sizes it, which writes each candidate line once), then sized, built, put and
   * acknowledged with its list's entry. Whether one was shipped (false: none was pending).
   */
  async #shipOne(ns: string, key: string, row: GenerationRow, through: number,
                 cut: number | null): Promise<boolean> {
    const log = this.#parts.log;
    const shipped = log.state().shipped;
    const slot = await unitsNow(ownerSlots(), SHIP_UNITS);
    try {
      const span = await this.#write(() => log.pending(
        cut !== null && shipped < cut ? Math.min(through, cut) : through));
      if (span === null) {
        return false;
      }
      const bytes = await this.#segment(span.a, span.b, SHIP_UNITS);
      const put = await this.#parts.store.putManifestSegment(ns, key, row.gen, span.a, span.b,
                                                             bytes);
      const entry = `${span.a}-${span.b}:${put.sha256}:${put.size}`;
      await this.#write(() => this.#parts.storage.transactionSync(() => {
        this.#sql.exec(
          "UPDATE owner_backups SET segments = segments + 1, bytes = bytes + ?, " +
            "segment_list = segment_list || CASE WHEN segment_list = '' THEN '' ELSE ',' " +
            "END || ? WHERE gen = ?", put.size, entry, row.gen);
        log.acknowledge(span.b);
      }));
      return true;
    } finally {
      slot.release();
    }
  }

  /**
   * A log segment's bytes (`ChangeLog.segment`: one buffer, never its lines held), the memory
   * leg sampling it at its widest row's encode and built (`units`, the hold it is built in).
   */
  async #segment(a: number, b: number, units: number): Promise<Uint8Array> {
    const pool = ownerSlots();
    const observe = pool.observe;
    const bytes = await this.#parts.log.segment(a, b, observe === null ? undefined
      : () => observe(units));
    if (observe !== null) {
      await observe(units);
    }
    return bytes;
  }

  /**
   * One base page from `cursor`, read in a unit of its own (the unit that reads the last
   * fixes E, the log's last seq then) and encoded outside it (`encodeLines`), in a frame of
   * its own so its text is let go once encoded; the memory leg samples it encoded, its text
   * and its bytes both in hand (rvburst W1).
   */
  async #page(at: GenerationRow, cursor: string | null):
      Promise<{ lines: Lines[]; next: Cursor | null; last: number | null; at: number }> {
    const log = this.#parts.log;
    const page = await this.#write(() => {
      const read = readPage(this.#sql, decodeCursor(cursor),
                            { schema: this.#parts.schema, owner: this.#parts.owner(),
                              at: Date.now(), gen: at.gen, s: at.s },
                            Number.MAX_SAFE_INTEGER, GENERATION_SIZES.page);
      return { read, last: read.next === null ? log.state().last : null, at: Date.now() };
    });
    const encoded = encodeLines(page.read.lines);
    const pool = ownerSlots();
    if (pool.observe !== null) {
      await pool.observe(BURST_UNITS);
      if (page.read.lines.length === 0 && encoded.bytes.byteLength > 0) {
        throw new Error("a page's bytes without its lines");  // its text held to here
      }
    }
    return { lines: [encoded, encodeLines(page.read.chunks.map(chunkLine))],
             next: page.read.next, last: page.last, at: page.at };
  }

  /**
   * The generation of `row` carried on until it finishes or the pass does: base bursts, each
   * followed by shipping the log so far. Whether it ended (finished, given up, or halted), so
   * a pass that did not finish backs off.
   */
  async #carry(row: GenerationRow, until: number): Promise<boolean> {
    const { ns, key } = await this.#parts.space();
    const log = this.#parts.log;
    if (row.schema !== this.#parts.schema) {
      // A migration ran while it was under way: its base and log are the old schema's, so it
      // is given up, loudly, and a fresh one begun under the new (review L5-2).
      await this.#abandon(ns, row, MANIFEST_SCHEMA);
      const gen = await this.#write(() => this.#open(Date.now() / 1000));
      return this.#carry(this.#rows().find((each) => each.gen === gen)!, until);
    }
    if (log.state().gen !== row.gen) {
      await this.#abandon(ns, row, null);  // a generation whose capture is not its own
      return true;
    }
    while (Date.now() < until) {
      if (this.#parts.halted()) {
        return true;
      }
      if (log.state().overflow === 1) {
        await this.#abandon(ns, row, MANIFEST_LOG_OVERFLOW);
        return true;
      }
      const at = this.#rows().find((each) => each.gen === row.gen)!;
      if (at.cursor === ENDED) {
        const shipped = await this.#ship(ns, key, at, at.e!, until);
        if (shipped === null) {
          await this.#abandon(ns, at, MANIFEST_CEILING);
          return true;
        }
        if (!shipped) {
          return false;
        }
        if (this.#parts.halted()) {
          return true;  // the root carries the key: none is written once the erasure began
        }
        await this.#finish(ns, key, at.gen);
        await this.#prune(until);
        return true;
      }
      // The log so far first: what a pass that ended mid-burst, and the wait for the next,
      // left unshipped never waits on another burst too.
      const first = await this.#ship(ns, key, at, log.state().last, until);
      if (first === null) {
        await this.#abandon(ns, at, MANIFEST_CEILING);
        return true;
      }
      if (!first) {
        return false;
      }
      if (this.#parts.halted()) {
        return true;  // an erasure begun while the log shipped: no burst begins
      }
      // A burst of whole base pages, its parts put and recorded (`#burst`), in a frame of
      // its own, so nothing of it is live once its log ships (rvburst X2).
      const burst = await this.#burst(ns, key, at);
      if (burst === "ended") {
        return true;
      }
      const e = burst.e;
      // And the log written while the burst's parts were put and recorded, before the next
      // burst, each segment under its own hold (cut at E once fixed).
      if (await this.#ship(ns, key, at, log.state().last, until, e) === null) {
        await this.#abandon(ns, at, MANIFEST_CEILING);
        return true;
      }
    }
    return false;
  }

  /**
   * One burst of generation `at`: whole base pages, each read in a unit of its own, with no
   * outside call between them (the unit that reads the last fixes E, the log's last seq
   * then), cut into parts, put, and recorded. Each page's lines encoded once, into one
   * buffer, as its parts will hold them: the burst bounded by their bytes. Its units of the
   * pool taken first, now or refused `busy` (nothing read yet: the lane goes on after
   * `MANIFEST_BUSY_RETRY_S`), and held to its parts' record, its pages and parts in hand
   * throughout. A frame of its own, so all it held is let go before the log ships. E if
   * the base is read, or "ended" (given up or halted).
   */
  async #burst(ns: string, key: string, at: GenerationRow):
      Promise<{ e: number | null } | "ended"> {
    const log = this.#parts.log;
    let e: number | null = null;
    const pool = ownerSlots();
    const slot = await unitsNow(pool, BURST_UNITS);
    try {
      const lines: Lines[] = [];
      let size = 0;
      let next: string | null = at.cursor;
      let endedAt = 0;
      while (size < GENERATION_SIZES.gather) {
        const page = await this.#page(at, next);
        for (const each of page.lines) {
          lines.push(each);
          size += each.bytes.byteLength;
        }
        if (page.next === null) {
          e = page.last;
          endedAt = page.at;
          break;
        }
        next = encodeCursor(page.next);
      }
      // Parts of whole lines, at most a chunk each, so an export streams them as they are.
      const parts = cutParts(lines, GENERATION_SIZES.part);
      // The pages let go once cut: the parts hold their bytes from here on (the memory
      // leg samples the cut, the pages and parts both in hand).
      if (pool.observe !== null) {
        await pool.observe(BURST_UNITS);
      }
      lines.length = 0;
      if (parts.some((bytes) => bytes.byteLength > CHUNK_BYTES)) {
        await this.#abandon(ns, at, MANIFEST_LINE);
        return "ended";
      }
      if (at.parts + at.segments + parts.length > MANIFEST_PARTS) {
        await this.#abandon(ns, at, MANIFEST_CEILING);
        return "ended";
      }
      const written: { sha256: string; size: number }[] = [];
      for (const bytes of parts) {
        if (this.#parts.halted()) {
          return "ended";
        }
        // No segment is built between them: the log a burst's puts leave is shipped once
        // its parts are recorded and let go, under its own hold (rvburst W1), so a burst
        // never holds its parts and a segment's build at once.
        written.push(await this.#parts.store.putManifestPart(ns, key, at.gen,
                                                             at.parts + written.length, bytes));
      }
      const added = written.map((put) => `${put.sha256}:${put.size}`).join(",");
      await this.#write(() => this.#parts.storage.transactionSync(() => {
        this.#sql.exec(
          "UPDATE owner_backups SET cursor = ?, parts = ?, bytes = bytes + ?, retry_at = NULL, " +
            "part_list = part_list || CASE WHEN part_list = '' OR ? = '' THEN '' ELSE ',' END " +
            "|| ? WHERE gen = ? AND parts = ?",
          e === null ? next : ENDED, at.parts + written.length,
          written.reduce((sum, put) => sum + put.size, 0), added, added, at.gen, at.parts);
        if (e !== null) {
          // The base's last page is held: E stands, and capture stops.
          this.#sql.exec("UPDATE owner_backups SET e = ?, ended_at = ? WHERE gen = ?", e,
                         endedAt / 1000, at.gen);
          log.end(e);
        }
      }));
      parts.length = 0;  // recorded: let go before the log ships
      if (pool.observe !== null) {
        await pool.observe(BURST_UNITS);
      }
    } finally {
      slot.release();
    }
    return { e };
  }
}
