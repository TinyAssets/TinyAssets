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
/** Or once it holds this many records, however small: half the hot mail's ceiling. */
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
 * (`planSegment`, `makeChunk`), so what it holds is `MOVE_HOLD_BYTES` whatever its size;
 * this bounds each pass's reads, hashing and calls. `pack`'s largest piece (twice the least
 * and a record) and the one straggler always taken fit it but for their lines.
 */
export const MOVE_BYTES = 32 * 1024 * 1024;
/**
 * What one archive move holds in the isolate, whatever its records (review N5, the memory
 * research's §2 A): the output chunk being made, the source chunk it reads and the one
 * before it, the service binding's copy of the chunk it sends, a group of hot records' rows
 * and lines, the plan (each record's place, about 200 bytes a record) and the trailer's
 * text when its chunk is made. The grant a move takes from the isolate-wide guard; measured
 * in workerd at the worst mixes (`limits-audit.md`).
 */
export const MOVE_HOLD_BYTES = 32 * 1024 * 1024;
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

/**
 * A segment planned from its records' lengths alone, before any of its bytes is read: each
 * record's offset, the trailer's place and size, and the chunks it is cut into. Its bytes
 * are the format above, exactly; each chunk is made from its sources when it is wanted
 * (`makeChunk`), so a segment is never held.
 */
export interface Plan<T extends Planned> {
  readonly stream: string;
  readonly records: readonly T[];
  readonly offsets: readonly number[];
  readonly first: number;
  readonly last: number;
  readonly trailer_offset: number;
  readonly trailer_bytes: number;
  readonly size: number;
  readonly chunks: number;
}

/** A plan's trailer line (with its newline) and its 16-digit suffix, made when wanted. */
function trailerOf(stream: string, records: readonly Planned[], offsets: readonly number[],
                   at: number): Uint8Array {
  const keys: Placement[] = [];
  const chains: Record<string, Placement[]> = {};
  for (const [n, record] of records.entries()) {
    const placement: Placement = [record.key, offsets[n]!, record.length];
    keys.push(placement);
    if (record.chain !== null) {
      (chains[record.chain] ??= []).push(placement);
    }
  }
  const trailer: Trailer = { stream, first: keys[0]![0], last: keys.at(-1)![0],
                             records: keys.length, keys, chains };
  return new TextEncoder().encode(`${JSON.stringify({ t: "segment", ...trailer })}\n${
    String(at).padStart(SUFFIX_BYTES, "0")}`);
}

/** A stream's records, in key order, planned as one segment. */
export function planSegment<T extends Planned>(stream: string, records: readonly T[]): Plan<T> {
  if (records.length === 0) {
    throw new Error("a segment holds at least one record");
  }
  const offsets: number[] = [];
  let at = 0;
  for (const [n, record] of records.entries()) {
    if (n > 0 && record.key <= records[n - 1]!.key) {
      throw new Error("a segment's records are in key order, each key once");
    }
    offsets.push(at);
    at += record.length;
  }
  const tail = trailerOf(stream, records, offsets, at).byteLength;
  return { stream, records, offsets, first: records[0]!.key, last: records.at(-1)!.key,
           trailer_offset: at, trailer_bytes: tail - SUFFIX_BYTES, size: at + tail,
           chunks: Math.ceil((at + tail) / CHUNK_BYTES) };
}

/** Records `makeChunk` asks its sources for at once. */
const SOURCE_GROUP = 512;

/**
 * Chunk `n` of a plan's segment, made from just the source ranges it covers: each record's
 * sources (`sources`, asked a group of records at a time: held lines, or ranges of stored
 * contents, read a whole 4 MiB chunk at a time through `read`), and the trailer when it
 * reaches it. A record whose sources are not the length it was planned at fails loudly.
 */
export async function makeChunk<T extends Planned>(
    plan: Plan<T>, n: number,
    sources: (records: readonly T[]) => Promise<readonly (readonly Source[])[]>,
    read: (content: Content, index: number) => Promise<Uint8Array>): Promise<Uint8Array> {
  const from = n * CHUNK_BYTES;
  const until = Math.min(from + CHUNK_BYTES, plan.size);
  const out = new Uint8Array(until - from);
  // The records it covers: the last whose offset is at or before `from`, on to `until`.
  let lo = 0;
  let hi = plan.records.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (plan.offsets[mid]! <= from) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }
  let end = lo;
  while (end < plan.records.length && plan.offsets[end]! < until) {
    end++;
  }
  for (let group = lo; group < end && plan.offsets[group]! < plan.trailer_offset;
       group += SOURCE_GROUP) {
    const records = plan.records.slice(group, Math.min(end, group + SOURCE_GROUP));
    const each = await sources(records);
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
            const lo = Math.max(a, base);
            const hi = Math.min(b, base + chunk.byteLength);
            out.set(chunk.subarray(lo - base, hi - base), lo - from);
          }
        }
        at += length;
      }
      if (at !== plan.offsets[group + k]! + records[k]!.length) {
        throw new Error("an archive move's record is not the length its plan placed");
      }
    }
  }
  if (until > plan.trailer_offset) {
    const tail = trailerOf(plan.stream, plan.records, plan.offsets, plan.trailer_offset);
    const a = Math.max(from, plan.trailer_offset);
    out.set(tail.subarray(a - plan.trailer_offset, until - plan.trailer_offset), a - from);
  }
  return out;
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
    parts.push(chunk);
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
    { sha256: string; offset: number; length: number }[] {
  const reads: { sha256: string; offset: number; length: number }[] = [];
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
    reads.push({ sha256, offset: within, length: take });
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
