/**
 * The one place an R2 key is built (ADR-032, plan §6): a chunk's `o/<ns>/c/<name>`, and a
 * generation's (the owner export and the nightly manifest, `owner/src/backup.ts`) root
 * `o/<ns>/m/<gen>`, its base parts `o/<ns>/m/<gen>/<part>` and its change-log segments
 * `o/<ns>/m/<gen>/l/<a>-<b>`.
 *
 * `ns` is an owner's random 128-bit namespace, held only in that owner's object; a chunk's
 * `name` is the HMAC of its content hash under the owner's data key (`datakey.ts`
 * `chunkName`), so the bucket's keys confirm no known file. Both are lowercase hex, checked
 * here, so no path, owner id or other caller text ever reaches a key. A path never becomes a key because R2
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
/** A generation's id: 16 lowercase hex characters, the owner's object makes it. */
const GEN = /^[0-9a-f]{16}$/;
/**
 * The most objects one generation has, its base parts and its log segments together: 4 MiB
 * each, 64 GiB. Past the worst case of the stream the owner's object can hold, escaping
 * included (`owner/guards/owner-bound.test.ts`, "the nightly manifest's stream"), and its
 * lists (`PART_ENTRY` each) fit a SQLite row's 2 MB. A generation past it is never retried:
 * the owner is told (`owner/src/backup.ts`).
 */
export const MANIFEST_PARTS = 16_384;

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

/** The R2 key of one owner's chunk, by its name (64 lowercase hex, as a hash is). */
export function chunkKey(ns: unknown, name: unknown): string {
  return `o/${namespace(ns)}/c/${chunkHash(name)}`;
}

/** A generation's id: 16 lowercase hex characters. */
export function generation(gen: unknown): string {
  if (typeof gen !== "string" || !GEN.test(gen)) {
    throw new Invalid("a manifest's generation is 16 lowercase hex characters");
  }
  return gen;
}

/** A log segment's span: whole seqs, `a` before `b`. */
export function segmentSpan(a: unknown, b: unknown): [number, number] {
  if (typeof a !== "number" || typeof b !== "number" || !Number.isSafeInteger(a)
      || !Number.isSafeInteger(b) || a < 0 || b <= a) {
    throw new Invalid("a manifest segment spans whole seqs, its first before its last");
  }
  return [a, b];
}

/** A manifest part's number: 0 to `MANIFEST_PARTS - 1`. */
export function manifestPart(part: unknown): number {
  if (typeof part !== "number" || !Number.isSafeInteger(part) || part < 0
      || part >= MANIFEST_PARTS) {
    throw new Invalid(`a manifest part is 0 to ${MANIFEST_PARTS - 1}`);
  }
  return part;
}

/** The R2 key of one owner's generation root (`part` absent) or one of its base parts. */
export function manifestKey(ns: unknown, gen: unknown, part?: unknown): string {
  const root = `o/${namespace(ns)}/m/${generation(gen)}`;
  return part === undefined ? root : `${root}/${manifestPart(part)}`;
}

/** Where in a generation an object is: a base part's number, or a log segment's span. */
export type ManifestAt = number | { readonly a: number; readonly b: number };

/** The R2 key of a generation's object at `at`: a base part, or a log segment. */
export function manifestObjectKey(ns: unknown, gen: unknown, at: ManifestAt): string {
  return typeof at === "number" ? manifestKey(ns, gen, at) : segmentKey(ns, gen, at.a, at.b);
}

/** The R2 key of one of a generation's log segments: the seqs after `a` through `b`. */
export function segmentKey(ns: unknown, gen: unknown, a: unknown, b: unknown): string {
  const [from, to] = segmentSpan(a, b);
  return `o/${namespace(ns)}/m/${generation(gen)}/l/${from}-${to}`;
}

/** The prefix every chunk key of one owner's namespace starts with: the erasure's list. */
export function chunkPrefix(ns: unknown): string {
  return `o/${namespace(ns)}/c/`;
}

/** The chunk name a key listed under `chunkPrefix(ns)` holds, or a loud failure. */
export function listedChunk(ns: unknown, key: unknown): string {
  const prefix = chunkPrefix(ns);
  if (typeof key !== "string" || !key.startsWith(prefix)) {
    throw new Error("a listed key is not under its namespace's prefix");
  }
  return chunkHash(key.slice(prefix.length));
}

/** The prefix every manifest key of one owner's namespace starts with: the erasure's list. */
export function manifestPrefix(ns: unknown): string {
  return `o/${namespace(ns)}/m/`;
}

/** The manifest key a key listed under `manifestPrefix(ns)` is, rebuilt, or a loud failure. */
export function listedManifest(ns: unknown, key: unknown): string {
  const prefix = manifestPrefix(ns);
  if (typeof key !== "string" || !key.startsWith(prefix)) {
    throw new Error("a listed key is not under its namespace's prefix");
  }
  const [gen, part, ...rest] = key.slice(prefix.length).split("/");
  if (part === "l" && rest.length === 1) {
    const span = /^(0|[1-9][0-9]*)-([1-9][0-9]*)$/.exec(rest[0]!);
    if (span === null) {
      throw new Error("a listed manifest key is out of shape");
    }
    return segmentKey(ns, gen, Number(span[1]), Number(span[2]));
  }
  if (rest.length > 0 || (part !== undefined && !/^(0|[1-9][0-9]*)$/.test(part))) {
    throw new Error("a listed manifest key is out of shape");
  }
  return manifestKey(ns, gen, part === undefined ? undefined : Number(part));
}
