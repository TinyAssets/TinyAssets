/**
 * The nightly manifest (slice P): each night the owner's own object writes the owner's
 * export stream (`export.ts`), without the chunks' bytes, to R2, for recovery. It is the
 * `manifest` lane of the object's one schedule (`schedule.ts`): no platform cron looks at
 * every owner; each object's Lifecycle job wakes it once a night.
 *
 * - **The change clock.** Every write unit of the object but a quiet one (this lane's own,
 *   an export's bookkeeping, a session's renewal: `gate.ts`) moves the owner's change clock
 *   and marks the owner changed (`mark`, the write gate's `onChange`); a manifest begins by
 *   clearing the mark. With a finished manifest and no change since, the lane has no job: an
 *   idle owner's object is never woken for it.
 * - **One snapshot.** A manifest is pinned to the clock as it began. A pass reads pages in
 *   bursts with no outside call between them (each page its own unit, and nothing else runs
 *   in the object between units that await nothing), so no change lands inside a burst;
 *   each burst first checks the clock, and once it moved the manifest begins again from its
 *   first page, the parts it put deleted. A stream that ends is the owner at one moment.
 * - **When.** A manifest is due from its day's start in UTC plus the owner's own offset in
 *   the night (`MANIFEST_SPREAD_S`, from a hash of the owner id), so owners' writes spread
 *   over hours rather than meet at midnight. The first is due the night after the object
 *   first serves its owner (its first door); an object that never did keeps no job.
 * - **What.** The parts (`o/<ns>/m/<day>/<n>`) are the stream's bytes in order, cut at the
 *   chunk size, each under the owner's data key (SSE-C); the root (`o/<ns>/m/<day>`), written
 *   last, names each part's hash and size and carries the owner's wrapped key, so a recovery
 *   needs only the files Worker's keyring and the bucket (`files/src/index.ts`).
 * - **Resumable.** A pass reads whole pages, puts the parts they make, then in one unit
 *   records those parts (`owner_backups.part_list`, each `<sha256>:<size>`, comma-joined)
 *   and the next page's cursor.
 *   A crash or the pass's end between the two only writes those parts again.
 * - **Its ceiling.** `MANIFEST_PARTS` parts, past the worst stream the object can hold
 *   (the owner-bound guard measures it, JSON's escaping included). A stream past it is
 *   never retried in a loop: its parts are deleted, the owner is told once (a notice,
 *   `manifest_ceiling`), and the lane waits for the owner's next change.
 * - **Kept.** The newest finished manifest, and the one being written: once one finishes,
 *   every older one is deleted from R2, root first, and then its rows. A new one begins
 *   only once that is done, so the object never holds more than two manifests' rows.
 * - **Erasure.** The manifests carry the owner's wrapped key, so the erasure's prefix delete
 *   takes them with the chunks (`FILES.deleteNamespace`, `erase.ts`), and the mark has
 *   already halted this lane: a pass under way stops before its next put. Then nothing holds
 *   the owner's key.
 *
 * Nothing here limits the owner: a manifest is the platform's recovery copy, bounded by the
 * object it copies (`limits-audit.md`).
 */
import { createHash } from "node:crypto";

import { MANIFEST_PARTS } from "../../files/src/keys";
import { chunkLine, decodeCursor, encodeCursor, END_LINE, type Page, readPage } from "./export";
import { CHUNK_BYTES, type ChunkStore, type ChunkSpace } from "./files";
import type { OwnerStorage, Write } from "./gate";

/** The manifest lane's name in the schedule. */
export const MANIFEST_LANE = "manifest";
/** The hours of the night owners' manifests spread over, from the day's start in UTC. */
export const MANIFEST_SPREAD_S = 4 * 3600;
/** When a pass whose R2 call failed tries again. */
export const MANIFEST_RETRY_S = 300;
/** A part's entry in its manifest's list: its hash, a colon, its size, a comma. */
export const PART_ENTRY = 64 + 1 + 7 + 1;
/**
 * The stream's bytes one burst gathers, with no outside call, before it puts them as parts:
 * two parts' worth, about 24 MiB of the isolate's 128 MB at its peak (the text as UTF-16,
 * then its bytes).
 */
const GATHER_BYTES = 2 * CHUNK_BYTES;
/** The notice an owner is given, once, when its manifest passes `MANIFEST_PARTS`. */
export const MANIFEST_CEILING = "manifest_ceiling";

/** The owner's change clock as it stands. A read. */
export function changeClock(sql: Pick<SqlStorage, "exec">): number {
  return sql.exec<{ clock: number }>("SELECT clock FROM owner_backup_state").one().clock;
}
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

/** The owner's own offset into the night, in seconds. */
export function spread(owner: string): number {
  return createHash("sha256").update(owner).digest().readUInt32BE(0) % MANIFEST_SPREAD_S;
}

type BackupRow = {
  day: string;
  state: "writing" | "done";
  cursor: string | null;
  parts: number;
  part_list: string;
  bytes: number;
  started_at: number;
  finished_at: number | null;
  retry_at: number | null;
  clock: number;
};

/** What the manifest lane runs on. */
export interface BackupParts {
  readonly storage: OwnerStorage;
  /** The write gate's quiet units: this lane's own writes move no change clock. */
  readonly write: Write;
  readonly store: ChunkStore;
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
}

export class Backups {
  readonly #parts: BackupParts;

  constructor(parts: BackupParts) {
    this.#parts = parts;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#parts.storage.sql;
  }

  /** One of this lane's units: quiet, so it moves no change clock. */
  #write<T>(unit: () => T): Promise<T> {
    return this.#parts.write(unit);
  }

  /**
   * In a unit that wrote (the write gate's `onChange`, never a quiet unit): the owner's
   * change clock moved, and the owner changed since the manifest. Whether this was the first
   * change since, which sets the lane's job again.
   */
  mark(sql: Pick<SqlStorage, "exec">): boolean {
    sql.exec("UPDATE owner_backup_state SET clock = clock + 1");
    return sql.exec(
      "UPDATE owner_backup_state SET changed = 1 WHERE changed = 0 RETURNING changed")
      .toArray().length > 0;
  }

  #changed(): boolean {
    return this.#sql.exec<{ changed: number }>("SELECT changed FROM owner_backup_state").one()
      .changed === 1;
  }

  #rows(): BackupRow[] {
    return this.#sql.exec<BackupRow>("SELECT * FROM owner_backups ORDER BY day").toArray();
  }

  /** The finished manifests, oldest first. A read. */
  manifests(): { day: string; parts: number; bytes: number; finished_at: number }[] {
    return this.#rows().filter((row) => row.state === "done").map((row) => ({
      day: row.day, parts: row.parts, bytes: row.bytes, finished_at: row.finished_at! }));
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
    if (done.length > 1) {
      return retry(done[0]!.retry_at);
    }
    if (done.length === 1 && !this.#changed()) {
      return null;  // nothing changed since the last manifest: no job, no wake
    }
    const after = done.length === 0 ? manifestDay(now) : done[0]!.day;
    return Math.max(now, dayStart(after) + DAY_S + spread(this.#parts.owner()));
  }

  /**
   * One pass: older manifests deleted, then the one due begun or carried on, a few parts at
   * a time until the wall clock reaches `until` (ms). An R2 call that fails backs the lane
   * off in its row (`retry_at`); the pass itself completes.
   */
  async run(now: number, until: number): Promise<void> {
    try {
      if (!await this.#prune(until)) {
        return;
      }
      const due = this.next(now);
      const writing = this.#rows().find((row) => row.state === "writing")
        ?? (due !== null && now >= due ? await this.#begin(now) : undefined);
      if (writing !== undefined) {
        await this.#carry(writing, until);
      }
    } catch {
      // An R2 call that failed, kept in the rows: the lane comes back after its retry.
      console.error("the nightly manifest's pass failed; it is tried again later");
      await this.#write(() => this.#sql.exec(
        "UPDATE owner_backups SET retry_at = ? WHERE state = 'writing' OR day = " +
          "(SELECT MIN(day) FROM owner_backups)", Date.now() / 1000 + MANIFEST_RETRY_S));
    }
  }

  /** Every finished manifest but the newest deleted, root first; whether none is left over. */
  async #prune(until: number): Promise<boolean> {
    const done = this.#rows().filter((row) => row.state === "done");
    for (const old of done.slice(0, -1)) {
      if (Date.now() >= until || this.#parts.halted()) {
        return false;
      }
      const { ns } = await this.#parts.space();
      await this.#parts.store.deleteManifest(ns, old.day, old.parts);
      await this.#write(() => this.#sql.exec("DELETE FROM owner_backups WHERE day = ?",
                                                    old.day));
    }
    return true;
  }

  async #begin(now: number): Promise<BackupRow> {
    const day = manifestDay(now);
    await this.#write(() => this.#parts.storage.transactionSync(() => {
      // What changes from here on is the next manifest's.
      this.#sql.exec("UPDATE owner_backup_state SET changed = 0");
      this.#sql.exec(
        "INSERT INTO owner_backups (day, state, cursor, parts, part_list, bytes, started_at, " +
          "clock) VALUES (?, 'writing', NULL, 0, '', 0, ?, ?) ON CONFLICT (day) DO NOTHING",
        day, now, changeClock(this.#sql));
    }));
    return this.#rows().find((row) => row.day === day)!;
  }

  /**
   * The manifest of `day` begun again from its first page, pinned to the clock as it stands:
   * the owner changed since it began. The parts it put are deleted first.
   */
  async #restart(ns: string, day: string, parts: number): Promise<void> {
    await this.#parts.store.deleteManifest(ns, day, parts);
    await this.#write(() => this.#parts.storage.transactionSync(() => {
      this.#sql.exec("UPDATE owner_backup_state SET changed = 0");
      this.#sql.exec("UPDATE owner_backups SET cursor = NULL, parts = 0, part_list = '', " +
                       "bytes = 0, retry_at = NULL, clock = ? WHERE day = ?",
                     changeClock(this.#sql), day);
    }));
  }

  /**
   * The manifest of `day` given up past `MANIFEST_PARTS`: its parts deleted, its row gone,
   * and the owner told once until a manifest is written again. The lane waits for a change.
   */
  async #abandon(ns: string, day: string, parts: number): Promise<void> {
    console.error("the nightly manifest passed its ceiling; the owner is told");
    await this.#parts.store.deleteManifest(ns, day, parts);
    await this.#write(() => this.#parts.storage.transactionSync(() => {
      this.#sql.exec("DELETE FROM owner_backups WHERE day = ?", day);
      if (this.#sql.exec("UPDATE owner_backup_state SET told = 1 WHERE told = 0 RETURNING told")
            .toArray().length > 0) {
        this.#parts.notice(MANIFEST_CEILING, `The nightly recovery copy of your data passed ` +
          `its ${MANIFEST_PARTS} parts and was not written. Your data is intact; export it ` +
          "to keep a copy.");
      }
    }));
  }

  /** The manifest of `row` carried on from its cursor until it ends or the pass does. */
  async #carry(row: BackupRow, until: number): Promise<void> {
    if (row.state !== "writing") {
      return;  // today's is done already
    }
    const { ns, key } = await this.#parts.space();
    let cursor = row.cursor;
    let part = row.parts;
    let clock = row.clock;
    while (Date.now() < until && !this.#parts.halted()) {
      // Whole pages, gathered to a burst's size, each read in a unit of its own, with no
      // outside call between them: no other unit runs in the object meanwhile.
      const lines: string[] = [];
      let size = 0;
      let next: string | null = cursor;
      let ended = false;
      let moved = false;
      while (size < GATHER_BYTES) {
        const at = next;
        const page = await this.#write((): Page | null => changeClock(this.#sql) !== clock ? null
          : readPage(this.#sql, decodeCursor(at),
                     { schema: this.#parts.schema, owner: this.#parts.owner(), at: Date.now(),
                       clock }, Number.MAX_SAFE_INTEGER));
        if (page === null) {
          moved = true;
          break;
        }
        const text = [...page.lines, ...page.chunks.map((chunk) => chunkLine(chunk)),
                      ...(page.next === null ? [END_LINE] : [])];
        for (const line of text) {
          lines.push(line);
          size += line.length;
        }
        if (page.next === null) {
          ended = true;
          break;
        }
        next = encodeCursor(page.next);
      }
      if (moved) {
        // The owner changed since this manifest began: it begins again, pinned anew.
        await this.#restart(ns, row.day, part);
        cursor = null;
        part = 0;
        clock = changeClock(this.#sql);
        continue;
      }
      const bytes = new TextEncoder().encode(lines.map((line) => `${line}\n`).join(""));
      if (part + Math.ceil(bytes.byteLength / CHUNK_BYTES) > MANIFEST_PARTS) {
        await this.#abandon(ns, row.day, part);
        return;
      }
      const written: { sha256: string; size: number }[] = [];
      for (let at = 0; at < bytes.byteLength; at += CHUNK_BYTES) {
        if (this.#parts.halted()) {
          return;
        }
        written.push(await this.#parts.store.putManifestPart(
          ns, key, row.day, part + written.length, bytes.subarray(at, at + CHUNK_BYTES)));
      }
      const added = written.map((put) => `${put.sha256}:${put.size}`).join(",");
      await this.#write(() => this.#sql.exec(
        "UPDATE owner_backups SET cursor = ?, parts = ?, bytes = bytes + ?, retry_at = NULL, " +
          "part_list = part_list || CASE WHEN part_list = '' OR ? = '' THEN '' ELSE ',' END " +
          "|| ? WHERE day = ? AND parts = ?",
        ended ? null : next, part + written.length, bytes.byteLength, added, added, row.day,
        part));
      part += written.length;
      cursor = next;
      if (ended) {
        const list = this.#sql.exec<{ part_list: string }>(
          "SELECT part_list FROM owner_backups WHERE day = ?", row.day).one().part_list;
        const parts = list.split(",").map((entry) => {
          const [sha256, size] = entry.split(":");
          return { sha256: sha256!, size: Number(size) };
        });
        if (this.#parts.halted()) {
          return;  // the root carries the key: none is written once the erasure began
        }
        await this.#parts.store.putManifestRoot(ns, key, row.day, parts);
        await this.#write(() => this.#parts.storage.transactionSync(() => {
          this.#sql.exec("UPDATE owner_backups SET state = 'done', finished_at = ? WHERE day = ?",
                         Date.now() / 1000, row.day);
          this.#sql.exec("UPDATE owner_backup_state SET told = 0");
        }));
        await this.#prune(until);
        return;
      }
    }
  }
}
