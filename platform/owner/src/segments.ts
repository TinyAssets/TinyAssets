/**
 * Archive segments (slice STORE item 2, phase 1; `history_research.md` §2.2): where an
 * append-only stream's old records go once they no longer change, so the owner's object
 * keeps one row a segment, never one an item.
 *
 * A segment is one content of the files plane (ADR-035's placement: `Files.begin`, its
 * chunks to `FILES`, `Files.adopt` in the unit that names it), so it is under the owner's
 * data key (SSE-C), its bytes count in the one storage number (`chunk_bytes`), and the GC
 * grace, the generation's protection and the erasure's prefix delete cover it as any content.
 * Its bytes (review N3: a body is never escaped, so a record's size is its bytes):
 * 1. a record's rows, each one line in the export's own grammar (`export.ts`):
 *    `{"t":"row","table":"<name>","row":{<column>:<value>,...}}`, records in key order; a
 *    row that carries a body (a message's) holds `"body":null` and its line adds
 *    `"raw":"body","raw_bytes":<n>`, and the line's newline is followed by exactly `n` bytes
 *    (the body's own UTF-8, copied in unescaped, so the segment names no other content and a
 *    record is its body's size and its lines') and a newline;
 * 2. the trailer, one line: `{"t":"segment","stream":"mail","first":<key>,"last":<key>,
 *    "records":<n>,"keys":[[<key>,<offset>,<length>],...],"chains":{"<root>":[[<key>,
 *    <offset>,<length>],...]}}`, each record's byte range by its key, and for mail each
 *    chain's records by its root;
 * 3. 16 ASCII digits: the trailer's offset, so the segment reads alone.
 *
 * The object keeps the segment's fence (`archive_segments`: its key range, its trailer's
 * range, its records), so a read by key is the fence, one ranged read of the trailer, one of
 * the record. A segment is sealed at `SEGMENT_MIN_BYTES` of records or `SEGMENT_MIN_RECORDS`
 * records, and no record is smaller than `MIN_RECORD_BYTES` (the tests hold the smallest),
 * so every segment but a lone one holds at least `SEGMENT_LEAST_BYTES` of counted storage
 * (a rewrite left small takes in its neighbours by key; `pack` cuts only where both sides
 * hold at least it), and the fence's rows are at most the storage over
 * that, plus one: no count, the one number bounds them.
 */
import { Buffer } from "node:buffer";
import { createHash, createHmac, randomBytes } from "node:crypto";

import { CHUNK_BYTES } from "./manifest";

/**
 * A segment is sealed once its records' own bytes reach this (never their trailer shares,
 * review N4): two chunks. Not one: a fence row costs the
 * owner-bound guard about 2.5 KB at its worst fill, so 0.5 TB of tier in 4 MiB segments
 * would pass STORE's quarter of the room under the half (`limits-audit.test.ts`).
 */
export const SEGMENT_MIN_BYTES = 8 * 1024 * 1024;
/**
 * Or once it holds this many records, however small: half the hot mail's ceiling. Never
 * first at these figures: the least mail's record (868 bytes) reaches `SEGMENT_MIN_BYTES`
 * at under 10,000 records (`guards/move-cpu.test.ts`), so production seals by bytes.
 */
export const SEGMENT_MIN_RECORDS = 25_000;
/**
 * The fewest bytes a record takes: a mail's three rows at their narrowest (the tests build
 * the smallest message the ledger takes and hold its bytes to it).
 */
export const MIN_RECORD_BYTES = 336;
/**
 * The least a sealed segment holds: `SEGMENT_MIN_BYTES`, as either seal reaches it
 * (`SEGMENT_MIN_RECORDS` times `MIN_RECORD_BYTES` is past it; the tests hold the two).
 */
export const SEGMENT_LEAST_BYTES = 8 * 1024 * 1024;
/**
 * The fence's rows at most: the owner-bound guard's term for `archive_segments`, a lone
 * small segment among them. A technical ceiling on what a tier may offer
 * (`TIER_STORAGE_MAX`), never on what a user stores.
 */
export const ARCHIVE_SEGMENTS_MAX = 59_606;
/**
 * The most storage a tier record may give (`tier.ts`): the fence's rows, each a sealed
 * segment's least bytes, about 0.5 TB. A larger tier needs larger segments (compaction)
 * first: a redesign, recorded in `limits-audit.md`.
 */
export const TIER_STORAGE_MAX = (ARCHIVE_SEGMENTS_MAX - 1) * SEGMENT_LEAST_BYTES;
/**
 * The most records' bytes one merge rewrites in a pass: its segment and its stragglers (it
 * takes stragglers while the two fit, and always one). Work, not memory: a move is streamed
 * (`planSegment`, `makeChunk`), so what it holds is its records' units and a pass's
 * (`holds.ts`) whatever its size; this bounds each pass's reads, hashing and calls. `pack`'s
 * largest piece (twice the least and a record) and the one straggler always taken fit it but
 * for their lines.
 */
export const MOVE_BYTES = 32 * 1024 * 1024;
/**
 * The most records one move holds (review N7), the bound its units are derived at
 * (`holds.ts`'s `moveTableUnits`):
 * `pack`'s largest piece of records at `MIN_RECORD_BYTES` (twice the least of them, and the
 * record past it: 49,933) and the 25,000 stragglers a pick takes at most. A merge's `into`
 * is one piece; a rewrite pass takes in a neighbour only while what it keeps and the
 * neighbour's records fit it (`#archiveRewrite`). `MoveTable` refuses past it, loudly.
 */
export const MOVE_RECORDS = 74_933;
/**
 * A record's share of its segment's trailer and suffix, counted with its lines in the one
 * number (a message's `line_bytes`): its key's and its chain's places (three numbers each,
 * at most 52 bytes), its chain root's name (40), and its share of the trailer's head and the
 * suffix (about 170 a segment), so a move frees at least the bytes it places (review M1).
 */
export const RECORD_TRAILER_BYTES = 320;

/**
 * Records in key order cut into the pieces one rewrite or merge places, each at least
 * `least` (review N2): one piece while they hold at most twice `least`; past it, a cut after
 * each run of records that reaches `least` while what is left still holds at least `least`,
 * so no piece is ever smaller (a record as large as a body never leaves a small one beside
 * it: no cut is made where it would). With no such cut, one piece: at most twice `least`
 * and the largest record. Each record's bytes are `size`'s.
 */
export function pack<T>(records: readonly T[], least: number, size: (record: T) => number):
    T[][] {
  const sizes = records.map(size);
  const total = sizes.reduce((sum, size) => sum + size, 0);
  if (total <= 2 * least) {
    return [records.slice()];
  }
  const pieces: T[][] = [];
  let piece: T[] = [];
  let bytes = 0;
  let left = total;
  for (const [n, record] of records.entries()) {
    piece.push(record);
    bytes += sizes[n]!;
    left -= sizes[n]!;
    if (bytes >= least && left >= least) {
      pieces.push(piece);
      piece = [];
      bytes = 0;
    }
  }
  if (piece.length > 0) {
    pieces.push(piece);
  }
  return pieces;
}

/** The 16 digits after the trailer that give its offset. */
const SUFFIX_BYTES = 16;

/**
 * A record's row: its table, its columns, and the raw bytes of its one raw column (`body`)
 * when it carries one (as the views they came in, never joined), the column then null.
 */
export type SegmentRow = readonly [table: string, row: Readonly<Record<string, unknown>>,
                                   raw?: readonly Uint8Array[]];

/** A record as a stream hands it to a segment: its key, its chain, its rows. */
export interface SegmentRecord {
  readonly key: number;
  readonly chain: string | null;
  /** The record's rows, written in this order. */
  readonly rows: readonly SegmentRow[];
  /**
   * Its bytes as a segment already holds them (its `rows` then empty): a record kept as it
   * was. A move keeps such records as ranges of their segment instead (`Source`), never
   * read whole.
   */
  readonly held?: readonly Uint8Array[];
}

/** One record's place in its segment. */
export type Placement = readonly [key: number, offset: number, length: number];

/** A segment's trailer, as its last line holds it. */
export interface Trailer {
  readonly stream: string;
  readonly first: number;
  readonly last: number;
  readonly records: number;
  readonly keys: readonly Placement[];
  readonly chains: Readonly<Record<string, readonly Placement[]>>;
}

/** A row as its export line (`export.ts`'s grammar), with its newline; a raw one's marked. */
export function rowLine(table: string, row: Readonly<Record<string, unknown>>,
                        rawBytes?: number): string {
  return `${JSON.stringify(rawBytes === undefined ? { t: "row", table, row }
    : { t: "row", table, row, raw: "body", raw_bytes: rawBytes })}\n`;
}

/** Byte arrays' total length. */
function lengthOf(parts: readonly Uint8Array[]): number {
  return parts.reduce((sum, part) => sum + part.byteLength, 0);
}

/**
 * A record's bytes as parts: a held record's as it holds them; else each row's line, and a
 * raw row's bytes and their newline.
 */
export function recordParts(record: SegmentRecord): Uint8Array[] {
  if (record.held !== undefined) {
    return [...record.held];
  }
  const encoder = new TextEncoder();
  const parts: Uint8Array[] = [];
  for (const [table, row, raw] of record.rows) {
    parts.push(encoder.encode(rowLine(table, row, raw === undefined ? undefined : lengthOf(raw))));
    if (raw !== undefined) {
      parts.push(...raw, Uint8Array.of(0x0a));
    }
  }
  return parts;
}

/** A record's bytes in its segment. */
export function recordBytes(record: SegmentRecord): number {
  return lengthOf(recordParts(record));
}

/** The UTF-8 bytes of `text`. */
export function bytesOf(text: string): number {
  return new TextEncoder().encode(text).byteLength;
}

/** A stored content a segment's bytes are read from: its chunks by hash, and its size. */
export interface Content {
  readonly id: string;
  readonly chunks: readonly string[];
  readonly size: number;
}

/** A run of a segment's bytes: bytes held (a line), or a range of a stored content. */
export type Source = Uint8Array
  | { readonly content: Content; readonly offset: number; readonly length: number };

/** A record as a plan places it: its key, its chain, and its bytes' length. */
export interface Planned {
  readonly key: number;
  readonly chain: string | null;
  readonly length: number;
}

/** The records a plan places, in key order, by their index (`MoveTable`'s, or a list's). */
export interface PlanRecords {
  readonly count: number;
  key(n: number): number;
  chain(n: number): string | null;
  length(n: number): number;
}

/** A list of records as `PlanRecords` (a test's, or a few). */
export function listed(records: readonly Planned[]): PlanRecords {
  return { count: records.length, key: (n) => records[n]!.key,
           chain: (n) => records[n]!.chain, length: (n) => records[n]!.length };
}

/**
 * A segment planned from its records' lengths alone, before any of its bytes is read: each
 * record's offset, its chains grouped as the trailer lists them, the trailer's place and
 * size, and the chunks it is cut into, all in typed arrays (review N6: a plan is about 40
 * bytes a record beside its records, measured). Its bytes are the format above, exactly; each chunk
 * is made from its sources when it is wanted (`makeChunk`), the trailer's text only in the
 * chunks it reaches, so a segment is never held.
 */
export interface Plan {
  readonly stream: string;
  readonly records: PlanRecords;
  readonly offsets: Float64Array;
  /** The records with a chain, grouped by chain in the order each first appears. */
  readonly chained: Uint32Array;
  /** Where each chain's group starts in `chained`, and its end after the last. */
  readonly groups: Uint32Array;
  readonly first: number;
  readonly last: number;
  readonly trailer_offset: number;
  readonly trailer_bytes: number;
  readonly size: number;
  readonly chunks: number;
}

/**
 * A plan's trailer line (with its newline) and its 16-digit suffix, as small texts in order
 * (`emit`): exactly `JSON.stringify({ t: "segment", stream, first, last, records, keys,
 * chains })`, never built whole.
 */
function trailerText(plan: Omit<Plan, "trailer_bytes" | "size" | "chunks">,
                     emit: (text: string) => void): void {
  const { records, offsets } = plan;
  const place = (n: number) => `[${records.key(n)},${offsets[n]},${records.length(n)}]`;
  emit(`{"t":"segment","stream":${JSON.stringify(plan.stream)},"first":${plan.first},` +
       `"last":${plan.last},"records":${records.count},"keys":[`);
  for (let n = 0; n < records.count; n++) {
    emit(n === 0 ? place(n) : `,${place(n)}`);
  }
  emit('],"chains":{');
  for (let g = 0; g + 1 < plan.groups.length; g++) {
    const start = plan.groups[g]!;
    emit(`${g === 0 ? "" : ","}${JSON.stringify(records.chain(plan.chained[start]!))}:[`);
    for (let k = start; k < plan.groups[g + 1]!; k++) {
      emit(k === start ? place(plan.chained[k]!) : `,${place(plan.chained[k]!)}`);
    }
    emit("]");
  }
  emit("}}\n");
  emit(String(plan.trailer_offset).padStart(SUFFIX_BYTES, "0"));
}

/** A stream's records, in key order, planned as one segment. */
export function planSegment(stream: string, records: PlanRecords): Plan {
  if (records.count === 0) {
    throw new Error("a segment holds at least one record");
  }
  const offsets = new Float64Array(records.count);
  let at = 0;
  for (let n = 0; n < records.count; n++) {
    if (n > 0 && records.key(n) <= records.key(n - 1)) {
      throw new Error("a segment's records are in key order, each key once");
    }
    offsets[n] = at;
    at += records.length(n);
  }
  // The chains' groups, in the order each chain first appears (a plain object's order for
  // keys that are no array index, as a chain root never is), each in key order.
  const groupOf = new Int32Array(records.count).fill(-1);
  const seen = new Map<string, number>();
  for (let n = 0; n < records.count; n++) {
    const chain = records.chain(n);
    if (chain !== null) {
      let group = seen.get(chain);
      if (group === undefined) {
        group = seen.size;
        seen.set(chain, group);
      }
      groupOf[n] = group;
    }
  }
  const groups = new Uint32Array(seen.size + 1);
  seen.clear();
  for (const group of groupOf) {
    if (group >= 0) {
      groups[group + 1]!++;
    }
  }
  for (let g = 1; g < groups.length; g++) {
    groups[g]! += groups[g - 1]!;
  }
  const chained = new Uint32Array(groups[groups.length - 1]!);
  const fill = groups.slice(0, -1);
  for (let n = 0; n < records.count; n++) {
    if (groupOf[n]! >= 0) {
      chained[fill[groupOf[n]!]!++] = n;
    }
  }
  const head = { stream, records, offsets, chained, groups, first: records.key(0),
                 last: records.key(records.count - 1), trailer_offset: at };
  let tail = 0;
  trailerText(head, (text) => {
    tail += Buffer.byteLength(text);
  });
  return { ...head, trailer_bytes: tail - SUFFIX_BYTES, size: at + tail,
           chunks: Math.ceil((at + tail) / CHUNK_BYTES) };
}

/** Records `makeChunk` asks its sources for at once. */
const SOURCE_GROUP = 512;

/**
 * Chunk `n` of a plan's segment, made from just the source ranges it covers: each record's
 * sources (`sources`, asked for a group of records at a time by their indices: held lines,
 * or ranges of stored contents, read a whole 4 MiB chunk at a time through `read`), and the
 * trailer's texts that reach it. A record whose sources are not the length it was planned
 * at fails loudly.
 */
export async function makeChunk(
    plan: Plan, n: number,
    sources: (from: number, to: number) => Promise<readonly (readonly Source[])[]>,
    read: (content: Content, index: number) => Promise<Uint8Array>): Promise<Uint8Array> {
  const from = n * CHUNK_BYTES;
  const until = Math.min(from + CHUNK_BYTES, plan.size);
  const out = new Uint8Array(until - from);
  const count = plan.records.count;
  // The records it covers: the last whose offset is at or before `from`, on to `until`.
  let lo = 0;
  let hi = count - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (plan.offsets[mid]! <= from) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }
  let end = lo;
  while (end < count && plan.offsets[end]! < until) {
    end++;
  }
  for (let group = lo; group < end && from < plan.trailer_offset; group += SOURCE_GROUP) {
    const to = Math.min(end, group + SOURCE_GROUP);
    const each = await sources(group, to);
    for (const [k, runs] of each.entries()) {
      let at = plan.offsets[group + k]!;
      for (const run of runs) {
        const length = run instanceof Uint8Array ? run.byteLength : run.length;
        const a = Math.max(from, at);
        const b = Math.min(until, at + length);
        if (a < b && run instanceof Uint8Array) {
          out.set(run.subarray(a - at, b - at), a - from);
        } else if (a < b && !(run instanceof Uint8Array)) {
          // The stored chunks the overlap spans, each read whole (so `FILES` rechecks it):
          // content byte `x` is segment byte `x + shift`.
          const shift = at - run.offset;
          for (let c = Math.floor((a - shift) / CHUNK_BYTES); c * CHUNK_BYTES < b - shift; c++) {
            const chunk = await read(run.content, c);
            const base = c * CHUNK_BYTES + shift;
            const lo2 = Math.max(a, base);
            const hi2 = Math.min(b, base + chunk.byteLength);
            out.set(chunk.subarray(lo2 - base, hi2 - base), lo2 - from);
          }
        }
        at += length;
      }
      if (at !== plan.offsets[group + k]! + plan.records.length(group + k)) {
        throw new Error("an archive move's record is not the length its plan placed");
      }
    }
  }
  if (until > plan.trailer_offset) {
    const encoder = new TextEncoder();
    let at = plan.trailer_offset;
    trailerText(plan, (text) => {
      const length = Buffer.byteLength(text);
      if (at + length > from && at < until) {
        const bytes = encoder.encode(text);
        const a = Math.max(from, at);
        const b = Math.min(until, at + length);
        out.set(bytes.subarray(a - at, b - at), a - from);
      }
      at += length;
    });
  }
  return out;
}

/** The chunks a `MoveTable` names a content by: 32 bytes of hash each. */
const HASH_BYTES = 32;

/** A typed array grown to hold at least `need` entries (doubling). */
function grown<T extends Float64Array | Uint32Array | Int32Array | Uint8Array>(
    array: T, need: number, make: (size: number) => T): T {
  if (need <= array.length) {
    return array;
  }
  const next = make(Math.max(need, array.length * 2));
  next.set(array);
  return next;
}

/**
 * A move's records, packed (review N6): each record's key, length, time, room (by index into
 * the few rooms it names), chain (its name's UTF-8 in one pool of bytes: never a string a
 * record, which may hold the row or line it came from), where its bytes are (kept: a stored
 * content and an offset in it; hot: inline, or a placed body's content) in typed arrays,
 * about 150 bytes a record (measured in workerd), never an object a record; and the contents
 * they name, each its size and its chunks' hashes as bytes, with the hash its bytes must have
 * when given.
 */
export class MoveTable implements PlanRecords {
  #count = 0;
  #keys = new Float64Array(64);
  #lengths = new Float64Array(64);
  #ats = new Float64Array(64);
  #offsets = new Float64Array(64);
  #rooms = new Uint32Array(64);
  #refs = new Int32Array(64);
  #kinds = new Uint8Array(64);
  #chainAt = new Float64Array(64);
  #chainBytes = new Int32Array(64);
  #chainPool = new Uint8Array(4096);
  #chainUsed = 0;
  readonly #roomNames: string[] = [];
  readonly #roomIds = new Map<string, number>();
  #contents = 0;
  #sizes = new Float64Array(8);
  #firsts = new Uint32Array(8);
  #counts = new Uint32Array(8);
  #expected = new Uint8Array(8);
  #hashes = new Uint8Array(8 * HASH_BYTES);
  #hashCount = 0;
  #expects = new Uint8Array(8 * HASH_BYTES);

  /** A record kept in a segment (`kind` "kept") or a hot message's ("inline", "placed"). */
  static readonly KEPT = 0;
  static readonly INLINE = 1;
  static readonly PLACED = 2;

  get count(): number {
    return this.#count;
  }

  /** A record appended; its index. `ref` names its content (kept, placed), else -1. */
  push(key: number, chain: string | null, room: string, at: number, length: number,
       kind: number, ref: number, offset: number): number {
    if (this.#count >= MOVE_RECORDS) {
      // Never a silent cut: the move fails, is counted, and the owner is told at three.
      throw new Error(`an archive move's records are past MOVE_RECORDS (${MOVE_RECORDS}), ` +
                      "the bound its memory is derived at");
    }
    const n = this.#count++;
    const need = this.#count;
    this.#keys = grown(this.#keys, need, (size) => new Float64Array(size));
    this.#lengths = grown(this.#lengths, need, (size) => new Float64Array(size));
    this.#ats = grown(this.#ats, need, (size) => new Float64Array(size));
    this.#offsets = grown(this.#offsets, need, (size) => new Float64Array(size));
    this.#rooms = grown(this.#rooms, need, (size) => new Uint32Array(size));
    this.#refs = grown(this.#refs, need, (size) => new Int32Array(size));
    this.#kinds = grown(this.#kinds, need, (size) => new Uint8Array(size));
    this.#chainAt = grown(this.#chainAt, need, (size) => new Float64Array(size));
    this.#chainBytes = grown(this.#chainBytes, need, (size) => new Int32Array(size));
    let roomId = this.#roomIds.get(room);
    if (roomId === undefined) {
      // A copy of its own, so the few rooms held never hold what they were read from.
      const own = Buffer.from(room, "utf8").toString("utf8");
      roomId = this.#roomNames.push(own) - 1;
      this.#roomIds.set(own, roomId);
    }
    if (chain === null) {
      this.#chainBytes[n] = -1;
    } else {
      this.#chainPool = grown(this.#chainPool, this.#chainUsed + 3 * chain.length,
                              (size) => new Uint8Array(size));
      const { written } = new TextEncoder().encodeInto(
        chain, this.#chainPool.subarray(this.#chainUsed));
      this.#chainAt[n] = this.#chainUsed;
      this.#chainBytes[n] = written;
      this.#chainUsed += written;
    }
    this.#keys[n] = key;
    this.#lengths[n] = length;
    this.#ats[n] = at;
    this.#offsets[n] = offset;
    this.#rooms[n] = roomId;
    this.#refs[n] = ref;
    this.#kinds[n] = kind;
    return n;
  }

  /** A content named, by its size and its chunks' hashes (hex), and the hash it must have. */
  addContent(size: number, chunks: readonly string[], expected: string | null): number {
    const ref = this.#contents++;
    this.#sizes = grown(this.#sizes, this.#contents, (n) => new Float64Array(n));
    this.#firsts = grown(this.#firsts, this.#contents, (n) => new Uint32Array(n));
    this.#counts = grown(this.#counts, this.#contents, (n) => new Uint32Array(n));
    this.#expected = grown(this.#expected, this.#contents, (n) => new Uint8Array(n));
    this.#expects = grown(this.#expects, this.#contents * HASH_BYTES, (n) => new Uint8Array(n));
    this.#hashes = grown(this.#hashes, (this.#hashCount + chunks.length) * HASH_BYTES,
                         (n) => new Uint8Array(n));
    this.#sizes[ref] = size;
    this.#firsts[ref] = this.#hashCount;
    this.#counts[ref] = chunks.length;
    for (const chunk of chunks) {
      this.#hashes.set(Buffer.from(chunk, "hex"), this.#hashCount++ * HASH_BYTES);
    }
    if (expected !== null) {
      this.#expected[ref] = 1;
      this.#expects.set(Buffer.from(expected, "hex"), ref * HASH_BYTES);
    }
    return ref;
  }

  key(n: number): number {
    return this.#keys[n]!;
  }

  length(n: number): number {
    return this.#lengths[n]!;
  }

  chain(n: number): string | null {
    const bytes = this.#chainBytes[n]!;
    return bytes < 0 ? null : new TextDecoder().decode(
      this.#chainPool.subarray(this.#chainAt[n]!, this.#chainAt[n]! + bytes));
  }

  room(n: number): string {
    return this.#roomNames[this.#rooms[n]!]!;
  }

  at(n: number): number {
    return this.#ats[n]!;
  }

  kind(n: number): number {
    return this.#kinds[n]!;
  }

  /** A kept record's offset in its content. */
  offset(n: number): number {
    return this.#offsets[n]!;
  }

  /** The content a record names (kept, placed), made when asked. */
  content(n: number): Content {
    return this.contentOf(this.#refs[n]!);
  }

  /** A content by its ref, made when asked: its id is its ref. */
  contentOf(ref: number): Content {
    const first = this.#firsts[ref]!;
    const chunks = Array.from({ length: this.#counts[ref]! }, (_, c) =>
      Buffer.from(this.#hashes.subarray((first + c) * HASH_BYTES, (first + c + 1) * HASH_BYTES))
        .toString("hex"));
    return { id: String(ref), chunks, size: this.#sizes[ref]! };
  }

  /** The contents named, and the hash each must have, if given (hex), by ref. */
  get contents(): number {
    return this.#contents;
  }

  expected(ref: number): string | null {
    return this.#expected[ref] === 1 ? Buffer.from(this.#expects.subarray(
      ref * HASH_BYTES, (ref + 1) * HASH_BYTES)).toString("hex") : null;
  }

  /** The records at `indices` (in key order), as a plan's records. */
  piece(indices: Uint32Array): PlanRecords & { readonly indices: Uint32Array } {
    return { count: indices.length, indices, key: (n) => this.#keys[indices[n]!]!,
             chain: (n) => this.chain(indices[n]!), length: (n) => this.#lengths[indices[n]!]! };
  }
}

/**
 * The line a stored content holds at `offset`, with its newline: read a whole chunk at a
 * time through `read` until its end. The line of a record's first row, for its room, its
 * chain and its time, so a kept record is never read past it.
 */
export async function lineAt(content: Content, offset: number, most: number,
                             read: (content: Content, index: number) => Promise<Uint8Array>):
    Promise<Uint8Array> {
  const parts: Uint8Array[] = [];
  let at = offset;
  while (at < Math.min(content.size, offset + most)) {
    const c = Math.floor(at / CHUNK_BYTES);
    const chunk = (await read(content, c)).subarray(at - c * CHUNK_BYTES);
    const end = chunk.indexOf(0x0a);
    if (end >= 0) {
      parts.push(chunk.subarray(0, end + 1));
      return joined(parts);
    }
    // A copy of the line's start: the chunk it is cut from is let go as the next is read.
    parts.push(chunk.slice());
    at += chunk.byteLength;
  }
  throw new Error("a segment's record line has no end");
}

/** Byte arrays joined into one. */
export function joined(parts: readonly Uint8Array[]): Uint8Array {
  const whole = new Uint8Array(lengthOf(parts));
  let at = 0;
  for (const part of parts) {
    whole.set(part, at);
    at += part.byteLength;
  }
  return whole;
}

/** A trailer's line, parsed and checked. */
export function parseTrailer(text: string): Trailer {
  const parsed = JSON.parse(text) as Record<string, unknown>;
  if (parsed["t"] !== "segment" || typeof parsed["stream"] !== "string"
      || !Array.isArray(parsed["keys"]) || typeof parsed["chains"] !== "object") {
    throw new Error("a segment's trailer is not one");
  }
  return parsed as unknown as Trailer;
}

/** A parsed record's row: as `SegmentRow`, a raw row's bytes one view. */
export type ParsedRow = [table: string, row: Record<string, unknown>, raw?: [Uint8Array]];

/**
 * A record's bytes, parsed: each row by its table, a raw row with its raw bytes as a view of
 * `bytes` (never decoded nor copied here).
 */
export function parseRecord(bytes: Uint8Array): ParsedRow[] {
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  const rows: ParsedRow[] = [];
  let at = 0;
  while (at < bytes.byteLength) {
    const end = bytes.indexOf(0x0a, at);
    if (end < 0) {
      throw new Error("a segment's record line has no end");
    }
    const parsed = JSON.parse(decoder.decode(bytes.subarray(at, end))) as {
      t?: unknown; table?: unknown; row?: unknown; raw?: unknown; raw_bytes?: unknown };
    if (parsed.t !== "row" || typeof parsed.table !== "string" || typeof parsed.row !== "object"
        || parsed.row === null) {
      throw new Error("a segment's record is not export rows");
    }
    at = end + 1;
    if (parsed.raw === undefined) {
      rows.push([parsed.table, parsed.row as Record<string, unknown>]);
      continue;
    }
    const length = parsed.raw_bytes;
    if (parsed.raw !== "body" || typeof length !== "number" || !Number.isSafeInteger(length)
        || length < 0 || at + length >= bytes.byteLength || bytes[at + length] !== 0x0a) {
      throw new Error("a segment's raw body is not as its line says");
    }
    rows.push([parsed.table, parsed.row as Record<string, unknown>,
               [bytes.subarray(at, at + length)]]);
    at += length + 1;
  }
  return rows;
}

/**
 * Export version 3's reading rule for a record (`export.ts`): its rows as the owner held
 * them, a raw row's bytes decoded (UTF-8, as written) back into its `body` column.
 */
export function recordRows(bytes: Uint8Array): [string, Record<string, unknown>][] {
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  return parseRecord(bytes).map(([table, row, raw]) =>
    [table, raw === undefined ? row : { ...row, body: decoder.decode(raw[0]) }]);
}

/** A byte range of a segment's content, as reads of its chunks (each `CHUNK_BYTES`). */
export function chunkReads(chunks: readonly string[], offset: number, length: number):
    { sha256: string; offset: number; length: number; verify: true }[] {
  const reads: { sha256: string; offset: number; length: number; verify: true }[] = [];
  let at = offset;
  const end = offset + length;
  while (at < end) {
    const index = Math.floor(at / CHUNK_BYTES);
    const within = at - index * CHUNK_BYTES;
    const take = Math.min(end - at, CHUNK_BYTES - within);
    const sha256 = chunks[index];
    if (sha256 === undefined) {
      throw new Error("a segment's range runs past its content");
    }
    reads.push({ sha256, offset: within, length: take, verify: true });
    at += take;
  }
  return reads;
}

/**
 * The digest the two units of a move compare: the batch's rows as the object holds them,
 * hashed one message's rows at a time as they are read (review N3: never the batch's JSON
 * whole, which escapes an inline body of control characters six times over; review N5:
 * never the batch held at once).
 */
export class ImageDigest {
  readonly #hash = createHash("sha256");

  add(image: unknown): void {
    this.#hash.update(JSON.stringify(image)).update("\n");
  }

  digest(): string {
    return this.#hash.digest("hex");
  }
}

/**
 * A members bloom of 64 bytes (three bits a member): which agents' rooms a segment holds,
 * so a purge rewrites only the segments that may hold its agent's mail. A false positive
 * costs one trailer read; there are no false negatives.
 */
export const BLOOM_BYTES = 64;

function bloomBits(member: string): number[] {
  const hash = createHash("sha256").update(member).digest();
  return [0, 2, 4].map((at) => hash.readUInt16BE(at) % (BLOOM_BYTES * 8));
}

/** A bloom of `members`. */
export function bloomOf(members: Iterable<string>): Uint8Array {
  const bloom = new Uint8Array(BLOOM_BYTES);
  for (const member of members) {
    for (const bit of bloomBits(member)) {
      bloom[bit >> 3]! |= 1 << (bit & 7);
    }
  }
  return bloom;
}

/** Whether `bloom` may hold `member`. */
export function bloomHas(bloom: Uint8Array, member: string): boolean {
  return bloomBits(member).every((bit) => (bloom[bit >> 3]! & (1 << (bit & 7))) !== 0);
}

/** A pair's room's members (`dm:<a>`, the owner and `a`; `dm:<a>+<b>`). */
export function roomMembers(room: string): string[] {
  return room.startsWith("dm:") ? room.slice(3).split("+") : [];
}

// -- Ids that carry their order key ----------------------------------------------------------

/** A fresh key for `orderedId`: 32 random bytes, kept in the object. */
export function idKey(): Uint8Array {
  return new Uint8Array(randomBytes(32));
}

function round(key: Uint8Array, index: number, half: Buffer): Buffer {
  return createHmac("sha256", key).update(Buffer.from([index])).update(half).digest()
    .subarray(0, 8);
}

function xor(a: Buffer, b: Buffer): Buffer {
  const out = Buffer.alloc(a.length);
  for (let i = 0; i < a.length; i++) {
    out[i] = a[i]! ^ b[i]!;
  }
  return out;
}

/**
 * An id for `key` (a mail's seq): 32 hex, a keyed permutation (a four-round Feistel network
 * over HMAC-SHA256) of the key and eight random bytes. Opaque to anyone without the object's
 * key, so it says nothing of the seq or the volume, and the object reads the seq back.
 */
export function orderedId(idKeyBytes: Uint8Array, key: number): string {
  let left = Buffer.alloc(8);
  left.writeBigUInt64BE(BigInt(key));
  let right = Buffer.from(randomBytes(8));
  for (let i = 0; i < 4; i++) {
    [left, right] = [right, xor(left, round(idKeyBytes, i, right))];
  }
  return Buffer.concat([left, right]).toString("hex");
}

/** The key an `orderedId` carries, or null for text that is not one (any 32 hex decodes). */
export function idOrder(idKeyBytes: Uint8Array, id: string): number | null {
  if (!/^[0-9a-f]{32}$/.test(id)) {
    return null;
  }
  const whole = Buffer.from(id, "hex");
  let left = whole.subarray(0, 8);
  let right = whole.subarray(8);
  for (let i = 3; i >= 0; i--) {
    [right, left] = [left, xor(right, round(idKeyBytes, i, left))];
  }
  const key = left.readBigUInt64BE();
  return key <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(key) : null;
}
