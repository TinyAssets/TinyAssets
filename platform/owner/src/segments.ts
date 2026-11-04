/**
 * Archive segments (slice STORE item 2, phase 1; `history_research.md` §2.2): where an
 * append-only stream's old records go once they no longer change, so the owner's object
 * keeps one row a segment, never one an item.
 *
 * A segment is one content of the files plane (ADR-035's placement: `Files.begin`, its
 * chunks to `FILES`, `Files.adopt` in the unit that names it), so it is under the owner's
 * data key (SSE-C), its bytes count in the one storage number (`chunk_bytes`), and the GC
 * grace, the generation's protection and the erasure's prefix delete cover it as any content.
 * Its bytes, all UTF-8:
 * 1. one line a record's row, **in the export's own grammar** (`export.ts`):
 *    `{"t":"row","table":"<name>","row":{<column>:<value>,...}}`, a record's lines together,
 *    records in key order; a placed body is copied in, so the segment names no other content;
 * 2. the trailer, one line: `{"t":"segment","stream":"mail","first":<key>,"last":<key>,
 *    "records":<n>,"keys":[[<key>,<offset>,<length>],...],"chains":{"<root>":[[<key>,
 *    <offset>,<length>],...]}}`, each record's byte range by its key, and for mail each
 *    chain's records by its root;
 * 3. 16 ASCII digits: the trailer's offset, so the segment reads alone.
 *
 * The object keeps the segment's fence (`archive_segments`: its key range, its trailer's
 * range, its records), so a read by key is the fence, one ranged read of the trailer, one of
 * the record. A segment is sealed at `SEGMENT_MIN_BYTES` of lines or `SEGMENT_MIN_RECORDS`
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
 * A segment is sealed once its lines reach this: two chunks. Not one: a fence row costs the
 * owner-bound guard about 2.5 KB at its worst fill, so 0.5 TB of tier in 4 MiB segments
 * would pass STORE's quarter of the room under the half (`limits-audit.test.ts`).
 */
export const SEGMENT_MIN_BYTES = 8 * 1024 * 1024;
/** Or once it holds this many records, however small: half the hot mail's ceiling. */
export const SEGMENT_MIN_RECORDS = 25_000;
/**
 * The fewest bytes a record's lines take: a mail's three rows at their narrowest (the tests
 * build the smallest message the ledger takes and hold its lines to it).
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
 * and the largest record, which the moves' byte budget bounds.
 */
export function pack<T extends SegmentRecord>(records: readonly T[], least: number): T[][] {
  const sizes = records.map((record) => bytesOf(recordText(record)));
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

/** A record as a stream hands it to a segment: its key, its chain, its rows' lines. */
export interface SegmentRecord {
  readonly key: number;
  readonly chain: string | null;
  /** The record's rows, each `[table, row]`, written as export lines in this order. */
  readonly rows: readonly (readonly [string, Readonly<Record<string, unknown>>])[];
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

/** A segment encoded: its bytes, and its fence. */
export interface Encoded {
  readonly bytes: Uint8Array;
  readonly trailer: Trailer;
  readonly trailer_offset: number;
  readonly trailer_bytes: number;
}

/** A row as its export line (`export.ts`'s grammar), with its newline. */
export function rowLine(table: string, row: Readonly<Record<string, unknown>>): string {
  return `${JSON.stringify({ t: "row", table, row })}\n`;
}

/** A record's lines, as a segment holds them. */
export function recordText(record: SegmentRecord): string {
  return record.rows.map(([table, row]) => rowLine(table, row)).join("");
}

/** The UTF-8 bytes of `text`. */
export function bytesOf(text: string): number {
  return new TextEncoder().encode(text).byteLength;
}

/** A stream's records, in key order, encoded as one segment. */
export function encodeSegment(stream: string, records: readonly SegmentRecord[]): Encoded {
  if (records.length === 0) {
    throw new Error("a segment holds at least one record");
  }
  const parts: string[] = [];
  const keys: Placement[] = [];
  const chains: Record<string, Placement[]> = {};
  let offset = 0;
  for (const record of records) {
    const text = recordText(record);
    const length = bytesOf(text);
    const placement: Placement = [record.key, offset, length];
    keys.push(placement);
    if (record.chain !== null) {
      (chains[record.chain] ??= []).push(placement);
    }
    parts.push(text);
    offset += length;
  }
  for (let i = 1; i < keys.length; i++) {
    if (keys[i]![0] <= keys[i - 1]![0]) {
      throw new Error("a segment's records are in key order, each key once");
    }
  }
  const trailer: Trailer = { stream, first: keys[0]![0], last: keys.at(-1)![0],
                             records: keys.length, keys, chains };
  const line = `${JSON.stringify({ t: "segment", ...trailer })}\n`;
  const suffix = String(offset).padStart(SUFFIX_BYTES, "0");
  const bytes = new TextEncoder().encode(parts.join("") + line + suffix);
  return { bytes, trailer, trailer_offset: offset, trailer_bytes: bytesOf(line) };
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

/** A record's lines, parsed: each row by its table, in order. */
export function parseRecord(text: string): [string, Record<string, unknown>][] {
  return text.split("\n").filter((line) => line !== "").map((line) => {
    const parsed = JSON.parse(line) as { t?: unknown; table?: unknown; row?: unknown };
    if (parsed.t !== "row" || typeof parsed.table !== "string" || typeof parsed.row !== "object"
        || parsed.row === null) {
      throw new Error("a segment's record is not export rows");
    }
    return [parsed.table, parsed.row as Record<string, unknown>];
  });
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

/** The digest the two units of a move compare: the batch's rows as the object holds them. */
export function imageDigest(images: readonly unknown[]): string {
  return createHash("sha256").update(JSON.stringify(images)).digest("hex");
}

/** A segment's bytes cut into chunks with their hashes, as `FILES` names them. */
export function segmentChunks(bytes: Uint8Array): { sha256: string; bytes: Uint8Array }[] {
  const chunks: { sha256: string; bytes: Uint8Array }[] = [];
  for (let at = 0; at < bytes.byteLength; at += CHUNK_BYTES) {
    const piece = bytes.subarray(at, Math.min(at + CHUNK_BYTES, bytes.byteLength));
    chunks.push({ sha256: createHash("sha256").update(piece).digest("hex"), bytes: piece });
  }
  return chunks;
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
