/**
 * The change log of a generation (slice P; `backup_shape_research.md` (d), the base-backup-
 * plus-WAL method at row level): while a generation's base is read, every change to an
 * exported row writes the row's image, as an export line, into `change_log`, in the writing
 * unit's own transaction (one trigger an exported table and event, `schema.ts` `LOGGED`), so
 * no change is lost and none is torn. The generation ships the log to R2 as it goes, a
 * segment at a time (`o/<ns>/m/<gen>/l/<a>-<b>`): the pending segment is recorded first, its
 * lines read, put, and only then acknowledged and deleted, so a crash between any two steps
 * repeats the same segment with the same bytes.
 *
 * Replayed after the base, the last line per (table, key) wins, and the result is the owner
 * at E, the last seq when the base's last page was read: a key with no image since S was
 * unchanged since S, so its base line is its value at E; any other's last image is.
 *
 * Capture is on only while a generation runs (always-on capture is the founder's cost dial,
 * tasks.md). The one bound is `LOG_BYTES` of unshipped log in the object: past it capture
 * stops and the generation fails with one notice; the write that passed it lands.
 */
import { Buffer } from "node:buffer";

import { exported } from "./export";
import { CHUNK_BYTES } from "./files";
import type { OwnerStorage } from "./gate";

/**
 * The unshipped change log the object holds at most, in bytes: a quarter of the room under
 * the owner database's half once every other table is at its worst, an equal share with
 * each slice still to land (`limits-audit.test.ts` derives it: the largest whole MiB within
 * that share). The room is there so a generation's held changes survive an R2 outage; it
 * binds only while R2 refuses puts, or when one unit changes more than it.
 */
export const LOG_BYTES = 68 * 1024 * 1024;
/** A log row's own bytes past its line, as the log counts it: its seq and record. */
export const LOG_ROW_BYTES = 64;
/**
 * The widest row image the log takes, under a Durable Object row's 2 MB: derived from the
 * widest real row (review L5-1), every exported table's at its ceiling escaped at JSON's
 * worst, a connection's models list at its 640 KiB among them, rounded up to a quarter MiB
 * (the owner-bound guard holds it equal). So no change, a token refresh's included, ever
 * writes `LOG_WIDE`; that mark stays the backstop should a later column outgrow it before
 * the guard catches it, failing the generation, never the write.
 */
export const IMAGE_MAX = 1536 * 1024;
/** What the log holds in place of an image past `IMAGE_MAX`. */
export const LOG_WIDE = '{"t":"wide"}';
/** What one log line adds to a segment past its stored text: its seq. */
const SEQ_BYTES = 32;

/** The capture's one row. */
export type Capture = {
  readonly gen: string | null;
  readonly s: number;
  readonly last: number;
  readonly e: number | null;
  readonly capturing: number;
  readonly bytes: number;
  readonly overflow: number;
  readonly shipped: number;
  readonly pending_a: number | null;
  readonly pending_b: number | null;
};

export class ChangeLog {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** The capture as it stands. A read. */
  state(): Capture {
    return this.#sql.exec<Capture>("SELECT gen, s, last, e, capturing, bytes, overflow, " +
                                    "shipped, pending_a, pending_b FROM log_capture").one();
  }

  /** In a unit: capture on for generation `gen`, from the next seq; what is left goes. */
  begin(gen: string): number {
    this.#sql.exec("DELETE FROM change_log");
    const { last } = this.state();
    this.#sql.exec("UPDATE log_capture SET gen = ?, s = ?, e = NULL, capturing = 1, " +
                     "bytes = 0, overflow = 0, shipped = ?, pending_a = NULL, pending_b = NULL", gen,
                   last + 1, last);
    return last + 1;
  }

  /** In a unit: the generation's end at seq `e`, and capture off. */
  end(e: number): void {
    this.#sql.exec("UPDATE log_capture SET e = ?, capturing = 0", e);
  }

  /** In a unit: the generation over (finished or given up): capture off, the log gone. */
  stop(): void {
    this.#sql.exec("UPDATE log_capture SET gen = NULL, e = NULL, capturing = 0, " +
                     "pending_a = NULL, pending_b = NULL");
    this.#sql.exec("DELETE FROM change_log");
  }

  /**
   * In a unit: the segment to ship next, at most `through` (the log's end once known): the
   * one pending, else the unshipped seqs after `shipped`, whole lines to a part's size at
   * most, recorded pending. Null when nothing is left to ship.
   */
  pending(through: number): { a: number; b: number } | null {
    const at = this.state();
    if (at.pending_a !== null && at.pending_b !== null) {
      return { a: at.pending_a, b: at.pending_b };
    }
    let b = at.shipped;
    let size = 0;
    for (const row of this.#sql.exec<{ seq: number; n: number }>(
           "SELECT seq, length(CAST(line AS BLOB)) AS n FROM change_log WHERE seq > ? AND " +
             "seq <= ? ORDER BY seq LIMIT 4096", at.shipped, through).toArray()) {
      if (size > 0 && size + row.n + SEQ_BYTES > CHUNK_BYTES) {
        break;
      }
      size += row.n + SEQ_BYTES;
      b = row.seq;
    }
    if (b === at.shipped) {
      return null;
    }
    this.#sql.exec("UPDATE log_capture SET pending_a = ?, pending_b = ?", at.shipped, b);
    return { a: at.shipped, b };
  }

  /**
   * The segment's bytes: the log's lines after `a` through `b`, each with its seq, the last
   * line per (table, key) kept, in the order of those last seqs, a BLOB's hex turned base64
   * as the base writes it, each followed by its newline. Deterministic for the same rows, so
   * a segment put again is the same bytes. Built without holding its lines (review rvburst
   * V1: a mostly-ASCII line with one character past U+00FF is a two-byte string in V8, twice
   * its UTF-8): a first pass keeps each key's last seq and counts the stored bytes, and a
   * second, in seq order, encodes only the kept rows straight into one buffer of at most
   * those bytes (`pending` holds them to a part, or one wider line), one row's text at a
   * time. A read.
   */
  segment(a: number, b: number): Uint8Array {
    const tables = exported();
    const rows = () => this.#sql.exec<{ seq: number; line: string }>(
      "SELECT seq, line FROM change_log WHERE seq > ? AND seq <= ? ORDER BY seq", a, b);
    const last = new Map<string, number>();
    let most = 0;
    for (const row of rows()) {
      const line = parsed(row.line);
      const shape = tables[line.table]!;
      const key = line.t === "row" ? shape.key.map((column) => line.row![column]) : line.key!;
      last.set(JSON.stringify([line.table, key]), row.seq);
      most += Buffer.byteLength(row.line, "utf8") + SEQ_BYTES;
    }
    const kept = new Set(last.values());
    last.clear();
    const encoder = new TextEncoder();
    let out = new Uint8Array(most);
    let at = 0;
    for (const row of rows()) {
      if (!kept.has(row.seq)) {
        continue;
      }
      const line = parsed(row.line);
      if (line.t === "row" && line.table === "file_contents"
          && typeof line.row!["inline"] === "string") {
        line.row!["inline"] = {
          $base64: Buffer.from(line.row!["inline"] as string, "hex").toString("base64") };
      }
      const text = JSON.stringify({ ...line, seq: row.seq });
      const need = Buffer.byteLength(text, "utf8") + 1;
      if (at + need > out.byteLength) {
        // A line its JSON writes wider than the log stored it: grown, never cut.
        const wider = new Uint8Array(Math.max(at + need, 2 * out.byteLength));
        wider.set(out.subarray(0, at));
        out = wider;
      }
      at += encoder.encodeInto(text, out.subarray(at)).written;
      out[at++] = 0x0a;
    }
    return out.subarray(0, at);
  }

  /** In a unit: the segment through `b` acknowledged: shipped, and its lines gone. */
  acknowledge(b: number): void {
    this.#sql.exec("UPDATE log_capture SET shipped = ?, pending_a = NULL, pending_b = NULL", b);
    this.#sql.exec("DELETE FROM change_log WHERE seq <= ?", b);
  }
}

/** A change log line, parsed: a row or a gone line, else the generation fails loudly. */
function parsed(text: string): { t: string; table: string; row?: Record<string, unknown>;
                                 key?: unknown[] } {
  const line = JSON.parse(text) as { t: string; table: string; row?: Record<string, unknown>;
                                     key?: unknown[] };
  if (line.t !== "row" && line.t !== "gone") {
    throw new Error("a change log line too wide to keep: the generation fails");
  }
  return line;
}
