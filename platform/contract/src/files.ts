/**
 * The files Worker's RPC contract (`tinyassets-files`, `docs/decisions/untangle-2026-11-wave-1.md`
 * A1): its two entrypoints, as their callers bind them, and the values that cross them. The files
 * Worker implements each against it (`files/src/index.ts`), and the owner's object and the doors
 * bind it from here, so no caller copies a files type.
 *
 * - `ChunkStore`: the default entrypoint, as the owner's object binds it (`FILES`). `key` is
 *   always that owner's wrapped data key, which only the files Worker can open.
 * - `ChunkUpload`: `FilesUpload`, the upload door's, which puts chunks and reads nothing.
 *
 * Every chunk is at most `CHUNK_BYTES`, and one call moves at most `BATCH_BYTES` (`limits.ts`).
 *
 * Every object of one owner is under its namespace's prefix (`objectPrefix`), which the files
 * Worker's keys (`files/src/keys.ts`) and the importer's wipe (`import/window.ts`) both build on.
 */
import { keyed, whole } from "./checks";
import { BATCH_BYTES, CHUNK_BYTES, MANIFEST_PARTS } from "./limits";
import { HEX32, HEX64 } from "./ids";
import { Refusal } from "./refusal";

/** An owner namespace: 32 lowercase hex characters, refused as `invalid` otherwise. */
export function namespace(ns: unknown): string {
  if (typeof ns !== "string" || !HEX32.test(ns)) {
    throw new Refusal("invalid", "a namespace is 32 lowercase hex characters");
  }
  return ns;
}

/** The prefix every object of one owner's namespace is under in the bucket: `o/<ns>/`. */
export function objectPrefix(ns: unknown): string {
  return `o/${namespace(ns)}/`;
}

/** One chunk to store: its hash and its bytes. */
export interface ChunkPut {
  readonly sha256: string;
  readonly bytes: Uint8Array;
}

/**
 * One chunk to read: whole, or `length` bytes from `offset`. The files Worker reads and opens
 * the whole chunk, rechecks its hash, and answers the range alone (review L7), so each read
 * holds a whole chunk while it is checked.
 */
export interface ChunkGet {
  readonly sha256: string;
  readonly offset?: number;
  readonly length?: number;
}

/** The most reads one `getChunks` takes: each holds a whole chunk, and a call `BATCH_BYTES`. */
export const GET_READS = BATCH_BYTES / CHUNK_BYTES;

/** One object of a generation: its SHA-256 and size. */
export interface ManifestObject {
  readonly sha256: string;
  readonly size: number;
}

/** A log segment of a generation: the seqs after `a` through `b`, its hash and size. */
export interface ManifestSegment extends ManifestObject {
  readonly a: number;
  readonly b: number;
}

/**
 * What a generation's root seals: its base parts and its log segments in order, the log's
 * span `s` (its first seq) to `e` (its end, the moment the generation is the owner as it
 * stood), and when it was written.
 */
export interface ManifestLists {
  readonly parts: readonly ManifestObject[];
  readonly segments: readonly ManifestSegment[];
  readonly s: number;
  readonly e: number;
  readonly at: number;
}

/** A generation's root, as a recovery reads it (`getManifestRoot`, its lists opened). */
export interface ManifestRoot extends ManifestLists {
  readonly format: "tinyassets-manifest";
  readonly version: 2;
  readonly gen: string;
  /** The owner's wrapped data key, which the parts and segments are under. */
  readonly key: string;
}

function manifestObject(value: unknown): ManifestObject | null {
  if (!keyed(value, "sha256,size") || typeof value["sha256"] !== "string"
      || !HEX64.test(value["sha256"]) || !whole(value["size"], 1, CHUNK_BYTES)) {
    return null;
  }
  return Object.freeze({ sha256: value["sha256"], size: value["size"] });
}

/** Whether `a` and `b` are a log segment's span: whole seqs, its first before its last. */
export function segmentSpans(a: unknown, b: unknown): a is number {
  return whole(a, 0) && whole(b, a + 1);
}

function manifestSegment(value: unknown): ManifestSegment | null {
  if (!keyed(value, "a,b,sha256,size") || !segmentSpans(value["a"], value["b"])) {
    return null;
  }
  const object = manifestObject({ sha256: value["sha256"], size: value["size"] });
  return object === null ? null
    : Object.freeze({ a: value["a"], b: value["b"] as number, ...object });
}

/**
 * A root's lists, checked: 1 or more base parts and the segments, `MANIFEST_PARTS` objects at
 * most, each a hash and a size of 1 to `CHUNK_BYTES` (a segment its span of whole seqs, its
 * first before its last), and the log's span; null for anything else. The files Worker holds
 * a root's lists to it as it seals them and as it opens them.
 */
export function manifestLists(value: unknown): ManifestLists | null {
  if (!keyed(value, "at,e,parts,s,segments") || !Array.isArray(value["parts"])
      || !Array.isArray(value["segments"]) || value["parts"].length === 0
      || value["parts"].length + value["segments"].length > MANIFEST_PARTS
      || !whole(value["s"], 1) || !whole(value["e"], value["s"] - 1) || !whole(value["at"], 0)) {
    return null;
  }
  const parts = (value["parts"] as unknown[]).map(manifestObject);
  const segments = (value["segments"] as unknown[]).map(manifestSegment);
  if (parts.includes(null) || segments.includes(null)) {
    return null;
  }
  return Object.freeze({ parts: Object.freeze(parts as ManifestObject[]),
                         segments: Object.freeze(segments as ManifestSegment[]),
                         s: value["s"], e: value["e"], at: value["at"] });
}

/** The default entrypoint, as the owner's object binds it (`FILES`). */
export interface ChunkStore {
  /** A new data key for the namespace `ns`, wrapped: what its owner's object keeps. */
  mintKey(ns: string): Promise<{ key: string }>;
  /** Chunks stored under their hashes, each verified by R2; `created` counts the new ones. */
  putChunks(ns: string, key: string, chunks: ChunkPut[]): Promise<{ created: number }>;
  /** Chunks' bytes, in order: each whole (its hash rechecked) or a range of it. */
  getChunks(ns: string, key: string, reads: ChunkGet[]): Promise<Uint8Array[]>;
  /** Chunks deleted (at most 1,000), by hash under the owner's key. */
  deleteChunks(ns: string, key: string, sha256s: string[]): Promise<{ deleted: number }>;
  /** The wrapped key wrapped again under the ring's active kid (a rotation's sweep). */
  rewrapKey(ns: string, key: string): Promise<{ key: string }>;
  /** A generation root's wrapped key wrapped again in place; null when there is no root. */
  rewrapManifestRoot(ns: string, gen: string): Promise<{ key: string } | null>;
  /** Every object under the namespace's prefix deleted: the erasure's alone. */
  deleteNamespace(ns: string): Promise<{ deleted: number }>;
  putManifestPart(ns: string, key: string, gen: string, part: number, bytes: Uint8Array):
    Promise<{ sha256: string; size: number }>;
  putManifestSegment(ns: string, key: string, gen: string, a: number, b: number,
                     bytes: Uint8Array): Promise<{ sha256: string; size: number }>;
  putManifestRoot(ns: string, key: string, gen: string, parts: readonly ManifestObject[],
                  segments: readonly ManifestSegment[], span: { s: number; e: number }):
    Promise<void>;
  /** A generation's root, its lists opened; null for none. */
  getManifestRoot(ns: string, gen: string): Promise<ManifestRoot | null>;
  getManifestPart(ns: string, key: string, gen: string, part: number, sha256: string):
    Promise<Uint8Array>;
  getManifestSegment(ns: string, key: string, gen: string, a: number, b: number,
                     sha256: string): Promise<Uint8Array>;
  deleteManifest(ns: string, gen: string, parts: number,
                 segments: readonly { a: number; b: number }[]): Promise<void>;
}

/** `FilesUpload`, as the upload door binds it: chunks put, and nothing read. */
export interface ChunkUpload {
  putChunks(ns: string, key: string, chunks: ChunkPut[]): Promise<{ created: number }>;
}
