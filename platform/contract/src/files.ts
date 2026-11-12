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
 */

/** One chunk to store: its hash and its bytes. */
export interface ChunkPut {
  readonly sha256: string;
  readonly bytes: Uint8Array;
}

/**
 * One chunk to read: whole, or `length` bytes from `offset`; with `verify`, a range read
 * from the whole chunk, its hash rechecked there, and only the range answered (review L7).
 */
export interface ChunkGet {
  readonly sha256: string;
  readonly offset?: number;
  readonly length?: number;
  readonly verify?: boolean;
}

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
 * A generation's root, as a recovery reads it (`getManifestRoot`, its list opened): its base
 * parts and its log segments in order, the log's span `s` (its first seq) to `e` (its end,
 * the moment the generation is the owner as it stood), and when it was written.
 */
export interface ManifestRoot {
  readonly format: "tinyassets-manifest";
  readonly version: 2;
  readonly gen: string;
  /** The owner's wrapped data key, which the parts and segments are under. */
  readonly key: string;
  readonly parts: readonly ManifestObject[];
  readonly segments: readonly ManifestSegment[];
  readonly s: number;
  readonly e: number;
  readonly at: number;
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
