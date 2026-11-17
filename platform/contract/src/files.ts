/**
 * The files Worker's RPC contract (`tinyassets-files`, `docs/decisions/untangle-2026-11-wave-1.md`
 * A1): its three entrypoints, as their callers bind them, and the values that cross them. The files
 * Worker implements each against it (`files/src/index.ts`), and the owner's object and the doors
 * bind it from here, so no caller copies a files type.
 *
 * - `ChunkStore`: the default entrypoint, as the owner's object binds it (`FILES`). `key` is
 *   always that owner's wrapped data key, which only the files Worker can open.
 * - `ChunkUpload`: `FilesUpload`, the upload door's, which puts chunks and reads nothing.
 * - `ChunkLocate`: `FilesLocate`, the operator channel's, which answers a namespace's owner and
 *   nothing else of its objects.
 *
 * Every chunk is at most `CHUNK_BYTES`, and one call moves at most `BATCH_BYTES` (`limits.ts`).
 *
 * Every object of one owner is under its namespace's prefix (`objectPrefix`), which the files
 * Worker's keys (`files/src/keys.ts`) and the importer's wipe (`import/window.ts`) both build on.
 */
import { keyed, whole } from "./checks.ts";
import { BATCH_BYTES, CHUNK_BYTES, MANIFEST_PARTS } from "./limits.ts";
import { HEX32, HEX64 } from "./ids.ts";
import { Refusal } from "./refusal.ts";

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

/** The namespace whose prefix the bucket key `key` is under (`o/<ns>/…`), or null. */
export function namespaceOf(key: string): string | null {
  const [o, ns, ...rest] = key.split("/");
  return o === "o" && HEX32.test(ns ?? "") && rest.length > 0 ? ns! : null;
}

/**
 * Where the bucket marks a namespace deleted on purpose (an erasure's, or the cutover's
 * discard's): `e/<ns>`, outside every namespace's prefix, so no prefix delete, sweep or
 * listing of a namespace reaches it. The files Worker makes the mark at an erasure's start and
 * before its prefix delete, and nothing deletes it, so the nightly copy carries it off the account, where a copy back
 * reads it after the account is gone (`docs/decisions/untangle-2026-11/r8-account-loss.md`).
 * A namespace is random, so a mark holds no person's identifier.
 */
export const MARKS = "e/";

/** What a mark holds: what it is, and nothing of the namespace's owner or its time. */
export const MARK_BODY = '{"kind":"tinyassets-erased-namespace"}';

/** The namespace the bucket key `key` marks deleted (`e/<ns>`), or null. */
export function markedNamespace(key: string): string | null {
  const ns = key.startsWith(MARKS) ? key.slice(MARKS.length) : "";
  return HEX32.test(ns) ? ns : null;
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

/**
 * The most reads one `getChunks` or `checkChunks` takes: each holds a whole chunk, and a call
 * `BATCH_BYTES`.
 */
export const GET_READS = BATCH_BYTES / CHUNK_BYTES;

/**
 * What `checkChunks` finds of one chunk under its owner's key: `stored`, opening to bytes that
 * are its hash; `missing` from the bucket; or `broken`, there but not opening under the key or
 * not its hash. It is an answer: what the check throws is a refusal, a lost answer or a bug.
 */
export type ChunkCheck = "stored" | "missing" | "broken";

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
  if (!keyed(value, ["sha256", "size"]) || typeof value["sha256"] !== "string"
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
  if (!keyed(value, ["a", "b", "sha256", "size"]) || !segmentSpans(value["a"], value["b"])) {
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
  if (!keyed(value, ["at", "e", "parts", "s", "segments"]) || !Array.isArray(value["parts"])
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
  /** Chunks read as `getChunks` reads them whole, each answered, in order, by what it found. */
  checkChunks(ns: string, key: string, sha256s: string[]): Promise<ChunkCheck[]>;
  /** Chunks deleted (at most 1,000), by hash under the owner's key. */
  deleteChunks(ns: string, key: string, sha256s: string[]): Promise<{ deleted: number }>;
  /** The wrapped key wrapped again under the ring's active kid (a rotation's sweep). */
  rewrapKey(ns: string, key: string): Promise<{ key: string }>;
  /**
   * A generation root's wrapped key wrapped again in place, and the key it now carries; `key`
   * null when the root changed meanwhile (a pass wrote it, under the active kid), left as it
   * is; null when there is no root.
   */
  rewrapManifestRoot(ns: string, gen: string): Promise<{ key: string | null } | null>;
  /**
   * The namespace's mark (`MARKS`) made, once, and kept: the erasure's, at its start, so the
   * nightly copy carries it off the account before the delete is due.
   */
  markNamespace(ns: string): Promise<void>;
  /**
   * Every object under the namespace's prefix deleted, its mark (`markNamespace`) made first
   * and kept: the erasure's and the discard's alone.
   */
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
  /** A generation's log segments deleted: a finished one's tail past E. */
  deleteSegments(ns: string, gen: string, segments: readonly { a: number; b: number }[]):
    Promise<void>;
  /**
   * One page of the namespace's generations from `cursor` (null: the first), every object of
   * one `keep` does not name deleted, roots first; where the next page starts, or null.
   */
  sweepManifests(ns: string, keep: string[], cursor: string | null):
    Promise<{ deleted: number; cursor: string | null }>;
  /** The names of chunks (at most 1,000) in the bucket, by hash under the owner's key. */
  chunkNames(ns: string, key: string, sha256s: string[]): Promise<string[]>;
  /** One page of the namespace's chunk names after `after` (null: the first); whether more. */
  listChunks(ns: string, after: string | null): Promise<{ names: string[]; more: boolean }>;
  /** Chunks deleted (at most 1,000) by their names: a restore's sweep's alone. */
  deleteListed(ns: string, names: string[]): Promise<{ deleted: number }>;
}

/** `FilesUpload`, as the upload door binds it: chunks put, and nothing read. */
export interface ChunkUpload {
  putChunks(ns: string, key: string, chunks: ChunkPut[]): Promise<{ created: number }>;
}

/**
 * What `locate` answers of a namespace: the owner its newest finished generation names (part 0's
 * export header, `export-header.ts`), and that generation. Every generation of a namespace names
 * its one owner.
 */
export interface LocatedOwner {
  readonly owner: string;
  readonly gen: string;
}

/**
 * `FilesLocate`, as the doors bind it for the operator channel (`restore.locate`): a namespace's
 * owner, null when the bucket holds no root of it, and nothing else read.
 */
export interface ChunkLocate {
  locate(ns: string): Promise<LocatedOwner | null>;
}
