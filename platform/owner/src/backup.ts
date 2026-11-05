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
 * - **Kept.** The newest finished generation, the one being written, and each an open export
 *   pins (`owner_exports`, `EXPORT_LIFE_S`); every other is deleted from R2, root first.
 * - **Erasure.** The generations carry the owner's wrapped key, so the erasure's prefix
 *   delete takes them with the chunks, and the mark has already halted this lane.
 *
 * Nothing here limits the owner: a generation is the platform's recovery copy and the
 * owner's export, bounded by the object it copies (`limits-audit.md`).
 */
import { createHash, randomBytes } from "node:crypto";

import { MANIFEST_PARTS } from "../../files/src/keys";
import type { ChangeLog } from "./changelog";
import {
  chunkLine, decodeCursor, encodeCursor, encodeLines, endLine, EXPORT_PAGE_BYTES, type Lines,
  readPage,
} from "./export";
import { CHUNK_BYTES, type ChunkStore, type ChunkSpace } from "./files";
import type { OwnerStorage, Write } from "./gate";
import { BURST_UNITS, ownerSlots } from "./holds";
import { type Slot, SLOT_WAIT_MS, SlotBusy, type SlotPool } from "./slots";

/** The manifest lane's name in the schedule. */
export const MANIFEST_LANE = "manifest";
/** The hours of the night owners' manifests spread over, from the day's start in UTC. */
export const MANIFEST_SPREAD_S = 4 * 3600;
/**
 * How long a finished generation protects the garbage it names from early collection
 * (`files.ts`): two nights. An owner that changes has a new one each night (one `DAY_S`
 * after the last began, at the same offset), and one more night covers a generation that
 * failed once and ran the next night; the longest generation, the worst stream (22.2 GB) at
 * the reviewer's lowest read rate (10 MB/s) and the lane's duty cycle (45 s a pass, 5 s
 * between), is under 42 minutes, inside it. Past it the generation is no recovery point to
 * keep garbage for, and early collection holds the owner's figure again.
 */
export const MANIFEST_PROTECT_S = 2 * 86_400;
/** When a pass whose R2 call failed tries again. */
export const MANIFEST_RETRY_S = 300;
/** When a generation a pass did not finish goes on: never at once. */
export const MANIFEST_CONTINUE_S = 5;
/** A part's entry in its generation's list: its hash, a colon, its size, a comma. */
export const PART_ENTRY = 64 + 1 + 7 + 1;
/** A segment's entry: its span (two seqs and a dash), a colon, its hash, size and comma. */
export const SEGMENT_ENTRY = 16 + 1 + 16 + 1 + 64 + 1 + 7 + 1;
/**
 * The stream's bytes one burst gathers, with no outside call, before it puts them as parts:
 * two parts' worth, counted as the parts hold them (each line's UTF-8 and its newline), so
 * a burst holds at most this and its last page (`export.test.ts`, "a generation at
 * production sizes", holds a burst of three-byte characters to it), once as its pages'
 * buffers (one a page, `encodeLines`, never an array a line) and once as parts: within
 * `BURST_UNITS` of the isolate's pool, which it holds from its first page to its last part's
 * put (`holds.ts`; the memory leg's "a generation's burst" holds it there).
 */
const GATHER_BYTES = 2 * CHUNK_BYTES;
/**
 * When a pass whose burst found the pool full goes on: the turnover of a full queue
 * (`SLOT_WAIT_MS`), as the archive's lane's (`ARCHIVE_BUSY_RETRY_S`). Not a failure.
 */
export const MANIFEST_BUSY_RETRY_S = SLOT_WAIT_MS / 1000;
/**
 * A generation's sizes: a base page's rows, a part's bytes, a burst's. The production
 * figures are these; a test passes smaller ones (`BackupParts.sizes`) to cut a small
 * fixture into many pages, parts and bursts, the bound logic the same at any size. What
 * only these figures show is held at them: one burst's cut within a tenth of the CPU limit,
 * and linear (`guards/backup-cut.test.ts`); an owner of several bursts finished and read
 * back exactly, its row under a row's 2 MB (`export.test.ts`, "a generation at production
 * sizes").
 */
export interface GenerationSizes {
  readonly page: number;
  readonly part: number;
  readonly gather: number;
  /**
   * The most bytes any part may hold: `CHUNK_BYTES`, what the files Worker takes (a test
   * shrinks it to see a line past it). A part over `part` but within it, a line wider than
   * a test's small parts, is put as it is.
   */
  readonly most?: number;
}
export const GENERATION_SIZES: GenerationSizes = Object.freeze({
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
export const MANIFEST_SCHEMA = "manifest_schema";
/**
 * The notice an owner is given, once, when one line of its stream (one row, as JSON) is
 * longer than any part may be (`CHUNK_BYTES`): the files Worker takes no such part, so the
 * generation is given up, loudly, and the lane waits for the owner's next change.
 * Unreachable by the schema's bounds (the widest row image, `IMAGE_MAX`, is under a
 * chunk), kept so a later wider column fails loudly.
 */
export const MANIFEST_LINE = "manifest_line";
/** A generation's recorded cursor once its base is read: only its log is left. */
const ENDED = "ended";
const DAY_S = 86_400;

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
  const timer = setTimeout(() => turn.abort(new SlotBusy()), 0);
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
export type GenerationRow = {
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
};

/** A generation's object an export reads at an index: a part, a segment, its end, or none yet. */
export type GenerationObject =
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

/** What the manifest lane runs on. */
export interface BackupParts {
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
  /** In a unit: the owner told something its work reports (`notices.ts`). */
  readonly notice: (type: string, detail: string) => void;
  /** Its sizes, `GENERATION_SIZES` unless a test shrinks them. */
  readonly sizes?: GenerationSizes;
  /** The pool its bursts take their units of: the isolate's (`ownerSlots`) unless a test's. */
  readonly pool?: SlotPool;
}

export class Backups {
  readonly #parts: BackupParts;

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
    return this.#sql.exec<GenerationRow>("SELECT * FROM owner_backups ORDER BY started_at")
      .toArray();
  }

  /** Until when (s) an open export pins generation `gen` from deletion, or null. A read. */
  #pinned(gen: string, now: number): number | null {
    return this.#sql.exec<{ until: number | null }>(
      "SELECT MAX(ends_at) AS until FROM owner_exports WHERE gen = ? AND ends_at > ?", gen, now)
      .one().until;
  }

  /** The finished generations, oldest first. A read. */
  manifests(): { gen: string; day: string; parts: number; segments: number;
                 started_at: number; finished_at: number }[] {
    return this.#rows().filter((row) => row.state === "done").map((row) => ({
      gen: row.gen, day: row.day, parts: row.parts, segments: row.segments,
      started_at: row.started_at, finished_at: row.finished_at! }));
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
    // A superseded generation goes once no export pins it.
    const older = done.slice(0, -1).map((row) => this.#pinned(row.gen, now) ?? now);
    const prune = older.length === 0 ? null : retry(Math.min(...older));
    const nightly = done.length > 0 && !this.#changed() ? null
      : Math.max(now, dayStart(done.at(-1)?.day ?? manifestDay(now)) + DAY_S
                 + spread(this.#parts.owner()));
    return prune === null ? nightly : nightly === null ? prune : Math.min(prune, nightly);
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

  /** Whether generation `gen` is finished (its root holds its lists). A read. */
  finished(gen: string): boolean {
    return this.#rows().some((row) => row.gen === gen && row.state === "done");
  }

  /**
   * One pass: superseded generations no export pins deleted, then the one due begun or
   * carried on, until the wall clock reaches `until` (ms). One it did not finish goes on
   * `MANIFEST_CONTINUE_S` later; an R2 call that fails backs the lane off in its row
   * (`MANIFEST_RETRY_S`); the pass itself completes.
   */
  async run(now: number, until: number): Promise<void> {
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
      if (error instanceof SlotBusy) {
        // A burst found the pool full: nothing was read or put; it goes on once units turn
        // over, never counted a failure.
        await this.#write(() => this.#sql.exec(
          "UPDATE owner_backups SET retry_at = ? WHERE state = 'writing'",
          Date.now() / 1000 + MANIFEST_BUSY_RETRY_S));
        return;
      }
      // An R2 call that failed, kept in the rows: the lane comes back after its retry.
      console.error("the generation's pass failed; it is tried again later");
      await this.#write(() => this.#sql.exec(
        "UPDATE owner_backups SET retry_at = ? WHERE state = 'writing' OR gen = " +
          "(SELECT gen FROM owner_backups ORDER BY started_at LIMIT 1)",
        Date.now() / 1000 + MANIFEST_RETRY_S));
    }
  }

  /** Every superseded generation no export pins deleted, root first; whether none is left. */
  async #prune(until: number): Promise<boolean> {
    const done = this.#rows().filter((row) => row.state === "done");
    const now = Date.now() / 1000;
    for (const old of done.slice(0, -1)) {
      if (this.#pinned(old.gen, now) !== null) {
        continue;
      }
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

  /** In a unit: a new generation begun at `now`, its capture on; its id. */
  #open(now: number): string {
    const gen = randomBytes(8).toString("hex");
    this.#sql.exec("UPDATE owner_backup_state SET changed = 0");
    const s = this.#parts.log.begin(gen);
    this.#sql.exec(
      "INSERT INTO owner_backups (gen, day, state, cursor, parts, part_list, bytes, s, " +
        "schema, started_at) VALUES (?, ?, 'writing', NULL, 0, '', 0, ?, ?, ?)", gen,
      manifestDay(now), s, this.#parts.schema, now);
    return gen;
  }

  /**
   * The generation of `gen` given up (past `MANIFEST_PARTS`, its log past `LOG_BYTES`, a
   * migration while it ran, or its capture lost): its objects deleted, its row and its log
   * gone, and the owner told once (`why`, unless null) until one is written again. The lane
   * waits for a change, but after a migration, which begins a fresh one at once.
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
    await this.#parts.store.putManifestRoot(ns, key, gen, partsOf(row.part_list),
                                            segments.filter((segment) => segment.b <= row.e!),
                                            { s: row.s, e: row.e! });
    await this.#write(() => this.#parts.storage.transactionSync(() => {
      this.#sql.exec("UPDATE owner_backups SET state = 'done', finished_at = ?, part_list = " +
                       "'', segment_list = '' WHERE gen = ?", Date.now() / 1000, gen);
      this.#sql.exec("UPDATE owner_backup_state SET told = 0");
      this.#parts.log.stop();
    }));
  }

  /**
   * Ship the generation's log through `through`, a segment at a time, until it is or the pass
   * ends: each pending segment recorded, put, then acknowledged with its list's entry. No
   * segment spans `cut` (E, once fixed): those past it are the tail, which only a base read
   * again after a crash needs, and which the root never names. Whether every seq through
   * `through` is shipped; null once past `MANIFEST_PARTS`.
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
      const shipped = log.state().shipped;
      const span = await this.#write(() => log.pending(
        cut !== null && shipped < cut ? Math.min(through, cut) : through));
      if (span === null) {
        return true;
      }
      const put = await this.#parts.store.putManifestSegment(ns, key, row.gen, span.a, span.b,
                                                             log.segment(span.a, span.b));
      const entry = `${span.a}-${span.b}:${put.sha256}:${put.size}`;
      await this.#write(() => this.#parts.storage.transactionSync(() => {
        this.#sql.exec(
          "UPDATE owner_backups SET segments = segments + 1, bytes = bytes + ?, segment_list = " +
            "segment_list || CASE WHEN segment_list = '' THEN '' ELSE ',' END || ? WHERE gen = ?",
          put.size, entry, row.gen);
        log.acknowledge(span.b);
      }));
    }
    return true;
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
      // A burst of whole base pages, each read in a unit of its own, with no outside call
      // between them; the unit that reads the last fixes E, the log's last seq then. Each
      // page's lines encoded once, into one buffer, as its parts will hold them: the burst
      // bounded by their bytes. Its units of the pool taken first, now or refused `busy`
      // (nothing read yet: the lane goes on after `MANIFEST_BUSY_RETRY_S`), and held to its
      // parts' record, its pages and parts in hand throughout.
      let e: number | null = null;
      const pool = this.#parts.pool ?? ownerSlots();
      const slot = await unitsNow(pool, BURST_UNITS);
      try {
        const lines: Lines[] = [];
        let size = 0;
        let next: string | null = at.cursor;
        let endedAt = 0;
        const sizes = this.#parts.sizes ?? GENERATION_SIZES;
        while (size < sizes.gather) {
          const cursor = next;
          const page = await this.#write(() => {
            const read = readPage(this.#sql, decodeCursor(cursor),
                                  { schema: this.#parts.schema, owner: this.#parts.owner(),
                                    at: Date.now(), gen: at.gen, s: at.s },
                                  Number.MAX_SAFE_INTEGER, sizes.page);
            return { read, last: read.next === null ? log.state().last : null, at: Date.now() };
          });
          for (const each of [page.read.lines, encodeLines(page.read.chunks.map(chunkLine))]) {
            lines.push(each);
            size += each.bytes.byteLength;
          }
          if (page.read.next === null) {
            e = page.last;
            endedAt = page.at;
            break;
          }
          next = encodeCursor(page.read.next);
        }
        // Parts of whole lines, at most a chunk each, so an export streams them as they are.
        const parts = cutParts(lines, sizes.part);
        if (parts.some((bytes) => bytes.byteLength > (sizes.most ?? CHUNK_BYTES))) {
          await this.#abandon(ns, at, MANIFEST_LINE);
          return true;
        }
        if (at.parts + at.segments + parts.length > MANIFEST_PARTS) {
          await this.#abandon(ns, at, MANIFEST_CEILING);
          return true;
        }
        const written: { sha256: string; size: number }[] = [];
        for (const bytes of parts) {
          if (this.#parts.halted()) {
            return true;
          }
          written.push(await this.#parts.store.putManifestPart(ns, key, at.gen,
                                                               at.parts + written.length, bytes));
          // The log so far, shipped after each part (cut at E once fixed), so what an owner
          // writes during one outside call is all the unshipped log ever waits on.
          if (await this.#ship(ns, key, at, log.state().last, until, e) === null) {
            await this.#abandon(ns, at, MANIFEST_CEILING);
            return true;
          }
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
        if (pool.observe !== null) {
          await pool.observe(BURST_UNITS);
        }
      } finally {
        slot.release();
      }
      // And the log written while the burst's parts were recorded, before the next burst.
      if (await this.#ship(ns, key, at, log.state().last, until, e) === null) {
        await this.#abandon(ns, at, MANIFEST_CEILING);
        return true;
      }
    }
    return false;
  }
}
