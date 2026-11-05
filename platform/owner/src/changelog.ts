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
   * most as the segment writes them (`writtenLine`: JSON may write a line wider than the
   * log stored it, and a segment past a part could never be put: rvburst W2), recorded
   * pending. Null when nothing is left to ship.
   */
  pending(through: number): { a: number; b: number } | null {
    const at = this.state();
    // A span pinned before `pending` sized as written (rvburst X3) whose segment is past a
    // part as written was never put (`segment` refuses it, and the files Worker would), so
    // it is sized again from `shipped`; any other pinned span stands, as a put of it may
    // have landed.
    if (at.pending_a !== null && at.pending_b !== null
        && this.#writtenBytes(at.pending_a, at.pending_b) <= CHUNK_BYTES) {
      return { a: at.pending_a, b: at.pending_b };
    }
    let b = at.shipped;
    let size = 0;
    for (const row of this.#sql.exec<{ seq: number; line: string }>(
           "SELECT seq, line FROM change_log WHERE seq > ? AND seq <= ? ORDER BY seq " +
             "LIMIT 4096", at.shipped, through)) {
      // Every line as written, kept or not: the segment keeps at most these.
      const n = Buffer.byteLength(writtenLine(row.seq, row.line), "utf8") + 1;
      if (size > 0 && size + n > CHUNK_BYTES) {
        break;
      }
      size += n;
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
   * a segment put again is the same bytes. Built without holding its lines (rvburst V1, W1):
   * SQLite finds each key's last seq (`keyOf`), and each kept row's line is then read once,
   * alone by its seq, written and encoded straight into one buffer of a part, allocated once
   * and never grown (`pending` sizes the span by its lines as written, so they fit it), one
   * row's text at a time (`holds.ts`'s `SHIP_BYTES` derives the most this holds). `sample`,
   * the memory leg's, is awaited at the widest kept row's encode, its stored text and its
   * JSON in hand. A read.
   */
  async segment(a: number, b: number, sample?: () => Promise<void>): Promise<Uint8Array> {
    // Each key's last seq, and its stored bytes, found by SQLite (its key columns read by
    // `json_extract`): no line read into the isolate for it (rvburst W1).
    const kept = this.#kept(a, b);
    const wide = this.#sql.exec<{ n: number }>(
      "SELECT COUNT(*) AS n FROM change_log WHERE seq > ? AND seq <= ? AND " +
        "json_extract(line, '$.t') NOT IN ('row', 'gone')", a, b).one().n;
    if (wide > 0) {
      throw new Error("a change log line too wide to keep: the generation fails");
    }
    let widest = { seq: -1, n: -1 };
    for (const each of kept) {
      widest = each.n > widest.n ? each : widest;
    }
    // One buffer of a part, allocated once and never grown: `pending` sizes the span by its
    // lines as written, so the kept rows fit it; each kept row's line is read once, alone
    // by its seq, written and encoded straight in.
    const encoder = new TextEncoder();
    const out = new Uint8Array(CHUNK_BYTES);
    let at = 0;
    for (const { seq } of kept) {
      const stored = this.#sql.exec<{ line: string }>(
        "SELECT line FROM change_log WHERE seq = ?", seq).one().line;
      const text = writtenLine(seq, stored);
      const size = Buffer.byteLength(text, "utf8") + 1;
      if (at + size > out.byteLength) {
        throw new Error(`a log segment is past a part (${CHUNK_BYTES}) as written`);
      }
      at += encoder.encodeInto(text, out.subarray(at)).written;
      out[at++] = 0x0a;
      if (sample !== undefined && seq === widest.seq) {
        await sample();
        if (text.length === 0 || stored === "") {
          throw new Error("a sampled log line is empty");  // each held to here
        }
      }
    }
    // Its own bytes, exactly: a view of the part's buffer would carry all of it over RPC
    // (workerd serializes a view's whole backing buffer: rvburst X1). The rows' texts are let
    // go by now, so the copy sits beside the buffer alone, within `SHIP_BYTES`.
    return out.slice(0, at);
  }

  /** Each key's last seq after `a` through `b`, and its stored bytes (`keyOf`). A read. */
  #kept(a: number, b: number): { seq: number; n: number }[] {
    return this.#sql.exec<{ seq: number; n: number }>(
      "SELECT max(seq) AS seq, n FROM (SELECT seq, length(CAST(line AS BLOB)) AS n, " +
        `json_extract(line, '$.table') AS tbl, ${keyOf()} AS k FROM change_log WHERE seq > ? ` +
        "AND seq <= ?) GROUP BY tbl, k ORDER BY seq", a, b).toArray();
  }

  /** The bytes the segment after `a` through `b` takes as written, each kept line once. */
  #writtenBytes(a: number, b: number): number {
    let bytes = 0;
    for (const { seq } of this.#kept(a, b)) {
      const stored = this.#sql.exec<{ line: string }>(
        "SELECT line FROM change_log WHERE seq = ?", seq).one().line;
      bytes += Buffer.byteLength(writtenLine(seq, stored), "utf8") + 1;
    }
    return bytes;
  }

  /** In a unit: the segment through `b` acknowledged: shipped, and its lines gone. */
  acknowledge(b: number): void {
    this.#sql.exec("UPDATE log_capture SET shipped = ?, pending_a = NULL, pending_b = NULL", b);
    this.#sql.exec("DELETE FROM change_log WHERE seq <= ?", b);
  }
}

/**
 * A log line as a segment writes it: its seq added, a BLOB's hex turned base64 as the base
 * writes it.
 */
function writtenLine(seq: number, stored: string): string {
  const line = parsed(stored);
  if (line.t === "row" && line.table === "file_contents"
      && typeof line.row!["inline"] === "string") {
    line.row!["inline"] = {
      $base64: Buffer.from(line.row!["inline"] as string, "hex").toString("base64") };
  }
  return JSON.stringify({ ...line, seq });
}

/**
 * A log line's key as SQLite reads it (`json_extract`), canonical JSON: a gone line's own,
 * else its table's key columns from its row, as the triggers write both.
 */
function keyOf(): string {
  const tables = Object.entries(exported()).map(([name, { key }]) =>
    `WHEN '${name}' THEN json_array(${key.map((column) =>
      `json_extract(line, '$.row.${column}')`).join(", ")})`);
  return "CASE WHEN json_extract(line, '$.t') = 'gone' THEN json(json_extract(line, '$.key')) " +
    `ELSE CASE json_extract(line, '$.table') ${tables.join(" ")} END END`;
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
