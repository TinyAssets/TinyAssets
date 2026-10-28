/**
 * The one place an R2 key is built (ADR-032, plan §6): `o/<ns>/c/<sha256>`.
 *
 * `ns` is an owner's random 128-bit namespace, held only in that owner's object; `sha256`
 * is a chunk's content hash. Both are lowercase hex, checked here, so no path, owner id
 * or other caller text ever reaches a key. A path never becomes a key because R2
 * NFC-normalizes keys, and uploads are verbatim (Fact 9): two names a sandbox can create
 * would meet in one object. The r2-keys guard keeps every other source from building one.
 */
/** A request that is malformed: a wrong field, type or size, or bytes that miss their hash. */
export class Invalid extends Error {
  override readonly name = "Invalid";
}

/** A chunk's most bytes; the owner's chunker cuts files at exactly this size. */
export const CHUNK_BYTES = 4 * 1024 * 1024;
/** The most bytes one put or get call moves: six whole chunks, under RPC's 32 MiB. */
export const BATCH_BYTES = 6 * CHUNK_BYTES;
/** R2's most keys in one delete. */
export const DELETE_BATCH = 1000;

const NS = /^[0-9a-f]{32}$/;
const SHA256 = /^[0-9a-f]{64}$/;

/** An owner namespace: 32 lowercase hex characters. */
export function namespace(ns: unknown): string {
  if (typeof ns !== "string" || !NS.test(ns)) {
    throw new Invalid("a namespace is 32 lowercase hex characters");
  }
  return ns;
}

/** A chunk's SHA-256: 64 lowercase hex characters. */
export function chunkHash(sha256: unknown): string {
  if (typeof sha256 !== "string" || !SHA256.test(sha256)) {
    throw new Invalid("a chunk hash is 64 lowercase hex characters");
  }
  return sha256;
}

/** The R2 key of one owner's chunk. */
export function chunkKey(ns: unknown, sha256: unknown): string {
  return `o/${namespace(ns)}/c/${chunkHash(sha256)}`;
}
