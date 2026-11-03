/**
 * The streams being read (slice P review N1): an owner export, and the nightly manifest,
 * each read across many units while the owner keeps changing, and each ending as exactly
 * the owner at one moment E, with no restart. The method is pre-copy with a dirty-key
 * journal, as live migration (Clark et al., NSDI 2005), MongoDB's initial sync and a WAL
 * roll-forward use:
 *
 * 1. **The journal.** While a stream is open (`owner_streams`, at most one export's and the
 *    manifest's), a trigger on every exported table (`schema.ts` `STREAMED`) writes each key
 *    a change touched to the stream's journal (`stream_dirty`); with none open, a trigger
 *    costs one probe. A key taken by a page and changed again is set untaken again.
 * 2. **The base pass.** The tables' pages and the chunks (`export.ts` `readPage`), with no
 *    check of anything: a fuzzy copy.
 * 3. **The rounds.** Each page takes the journal's untaken keys, a page's bytes at a time,
 *    and re-emits each as its row now stands or as gone (`keyedLines`); what changes
 *    meanwhile is journaled again for a later page.
 * 4. **The last round** is the page that takes every untaken key in its one unit: nothing
 *    else runs in the object during a synchronous unit, so the stream then ends
 *    (`endLine`), at that moment E. Read last line per key wins, it is the owner at E.
 *
 * A page names how many of the rounds' pages were read before it (its cursor); a page asked
 * again is served again (its keys put back), so a client or a pass that lost one resumes.
 *
 * **Bounded.** A stream journals at most `DIRTY_BYTES_MAX` bytes over its life (each new key
 * its bytes and `DIRTY_ROW_BYTES`; the trigger stops past it), and reads at most
 * `ROUNDS_MAX` pages of rounds: an owner that changes faster than a stream can follow fails
 * it (`STREAM_UNSETTLED`) instead of looping. An export not read for `STREAM_IDLE_S` is
 * closed. Neither ever refuses or slows a write of the owner's.
 */
import { Invalid, Refused } from "./errors";
import {
  chunkLine, type Cursor, EXPORT_PAGE_BYTES, endLine, type Header, keyedLines, type Page,
  readPage, roundsSection,
} from "./export";
import type { OwnerStorage } from "./gate";

/** A stream's kind: the owner's export, or the nightly manifest. */
export type StreamKind = "export" | "manifest";
/** The most bytes one stream journals over its life: past it, it fails. */
export const DIRTY_BYTES_MAX = 2 * 1024 * 1024;
/** A journal row's own bytes past its table's name and key, as the journal counts it. */
export const DIRTY_ROW_BYTES = 96;
/** The most pages of rounds one stream reads: past it, it fails. */
export const ROUNDS_MAX = 1024;
/** How long an export may go unread before its stream is closed. */
export const STREAM_IDLE_S = 600;
/** Journal keys a page takes at a time. */
const TAKE_BATCH = 256;

/** What a page answers when its stream is gone: closed, idle, or another export began. */
export const STREAM_CLOSED = "this export is not open: begin it again";
/** What a page answers when the owner changed faster than the stream could follow. */
export const STREAM_UNSETTLED = "stream_unsettled: the owner changed more than this stream " +
  "could follow while it was read; begin it again";

export class Streams {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** In a unit: a stream of `kind` opened as `id` at `now` (s), one of its kind before it closed. */
  open(kind: StreamKind, id: string, now: number): void {
    this.#sql.exec("DELETE FROM owner_streams WHERE kind = ?", kind);
    this.#sql.exec("INSERT INTO owner_streams (kind, stream_id, opened_at, read_at) " +
                     "VALUES (?, ?, ?, ?)", kind, id, now, now);
  }

  /** Whether the stream of `kind` named `id` is open. A read. */
  isOpen(kind: StreamKind, id: string): boolean {
    return this.#sql.exec("SELECT 1 FROM owner_streams WHERE kind = ? AND stream_id = ?", kind,
                          id).toArray().length > 0;
  }

  /** In a unit: the stream of `kind` closed (named `id`, or any), its journal with it. */
  close(kind: StreamKind, id?: string): void {
    this.#sql.exec("DELETE FROM owner_streams WHERE kind = ? AND (? IS NULL OR stream_id = ?)",
                   kind, id ?? null, id ?? null);
  }

  /** In a unit: an export's stream unread for `STREAM_IDLE_S` closed. */
  sweep(now: number): void {
    this.#sql.exec("DELETE FROM owner_streams WHERE kind = 'export' AND read_at < ?",
                   now - STREAM_IDLE_S);
  }

  /** Whether an export's stream is open but idle past `STREAM_IDLE_S`. A read. */
  idle(now: number): boolean {
    return this.#sql.exec("SELECT 1 FROM owner_streams WHERE kind = 'export' AND read_at < ?",
                          now - STREAM_IDLE_S).toArray().length > 0;
  }

  /**
   * In a unit: one page of the open stream of `kind` named `id`, from `cursor`: a base page,
   * or a page of rounds, which ends the stream's lines (`next` null) once it took the whole
   * journal. Refused when the stream is not open, or once it can no longer follow the owner
   * (`STREAM_UNSETTLED`). The caller closes the stream once it has kept what it read.
   */
  page(kind: StreamKind, id: string, cursor: Cursor, header: Header, now: number): Page {
    const stream = this.#sql.exec<{ journaled: number }>(
      "SELECT journaled FROM owner_streams WHERE kind = ? AND stream_id = ?", kind, id)
      .toArray()[0];
    if (stream === undefined) {
      throw new Invalid(STREAM_CLOSED);
    }
    if (stream.journaled >= DIRTY_BYTES_MAX) {
      throw new Refused(STREAM_UNSETTLED);
    }
    this.#sql.exec("UPDATE owner_streams SET read_at = ? WHERE kind = ?", now, kind);
    if (cursor.section !== roundsSection()) {
      return readPage(this.#sql, cursor, header, Number.MAX_SAFE_INTEGER);
    }
    const read = cursor.after![0] as number;
    if (read >= ROUNDS_MAX) {
      throw new Refused(STREAM_UNSETTLED);
    }
    // The pages before this one were kept: their keys go. One taken since is put back.
    this.#sql.exec("DELETE FROM stream_dirty WHERE kind = ? AND taken BETWEEN 1 AND ?", kind,
                   read);
    this.#sql.exec("UPDATE stream_dirty SET taken = 0 WHERE kind = ? AND taken > ?", kind, read);
    const lines: string[] = [];
    const chunks: { sha256: string; size: number }[] = [];
    let bytes = 0;
    while (bytes < EXPORT_PAGE_BYTES) {
      const keys = this.#sql.exec<{ tbl: string; key: string }>(
        "SELECT tbl, key FROM stream_dirty WHERE kind = ? AND taken = 0 ORDER BY tbl, key " +
          "LIMIT ?", kind, TAKE_BATCH).toArray();
      if (keys.length === 0) {
        break;
      }
      for (const { tbl, key } of keys) {
        this.#sql.exec("UPDATE stream_dirty SET taken = ? WHERE kind = ? AND tbl = ? AND " +
                         "key = ?", read + 1, kind, tbl, key);
      }
      for (const table of [...new Set(keys.map((key) => key.tbl))]) {
        const out = keyedLines(this.#sql, table, keys.filter((key) => key.tbl === table)
          .map((key) => key.key));
        lines.push(...out.lines);
        chunks.push(...out.chunks);
        bytes += out.bytes;
      }
    }
    const left = this.#sql.exec("SELECT 1 FROM stream_dirty WHERE kind = ? AND taken = 0 LIMIT 1",
                                kind).toArray().length > 0;
    if (left) {
      return { lines, chunks, next: { section: roundsSection(), after: [read + 1] } };
    }
    // The whole journal taken in this one unit: the stream is the owner as it stands now.
    return { lines: [...lines, ...chunks.map((chunk) => chunkLine(chunk)), endLine(Date.now())],
             chunks: [], next: null };
  }
}
