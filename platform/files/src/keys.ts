/**
 * The one place an R2 key is built (ADR-032, plan §6): a chunk's `o/<ns>/c/<sha256>`, and a
 * nightly manifest's root `o/<ns>/m/<day>` and its parts `o/<ns>/m/<day>/<part>`.
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
/** The most bytes one put or get call moves: two whole chunks, well under RPC's 32 MiB. */
export const BATCH_BYTES = 2 * CHUNK_BYTES;
/** R2's most keys in one delete. */
export const DELETE_BATCH = 1000;

const NS = /^[0-9a-f]{32}$/;
const SHA256 = /^[0-9a-f]{64}$/;
/** A manifest's day, `YYYYMMDD` in UTC. */
const DAY = /^20[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])$/;
/** The most parts one manifest has: 4 MiB each, past any owner object's 10 GB. */
export const MANIFEST_PARTS = 4096;

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

/** A manifest's day: `YYYYMMDD`. */
export function manifestDay(day: unknown): string {
  if (typeof day !== "string" || !DAY.test(day)) {
    throw new Invalid("a manifest's day is YYYYMMDD");
  }
  return day;
}

/** A manifest part's number: 0 to `MANIFEST_PARTS - 1`. */
export function manifestPart(part: unknown): number {
  if (typeof part !== "number" || !Number.isSafeInteger(part) || part < 0
      || part >= MANIFEST_PARTS) {
    throw new Invalid(`a manifest part is 0 to ${MANIFEST_PARTS - 1}`);
  }
  return part;
}

/** The R2 key of one owner's manifest root (`part` absent) or one of its parts. */
export function manifestKey(ns: unknown, day: unknown, part?: unknown): string {
  const root = `o/${namespace(ns)}/m/${manifestDay(day)}`;
  return part === undefined ? root : `${root}/${manifestPart(part)}`;
}
