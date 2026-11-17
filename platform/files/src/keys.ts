/**
 * The one place an R2 key is built (ADR-032, plan §6), under its owner's prefix
 * (`objectPrefix`, `o/<ns>/`, the contract's): a chunk's `o/<ns>/c/<name>`, and a
 * generation's (the owner export and the nightly manifest, `owner/src/backup.ts`) root
 * `o/<ns>/m/<gen>`, its base parts `o/<ns>/m/<gen>/<part>` and its change-log segments
 * `o/<ns>/m/<gen>/l/<a>-<b>`; each namespace it holds, `o/<ns>/h/<held>`; and, outside every
 * prefix, a namespace's erasure mark `e/<ns>`.
 *
 * `ns` is an owner's random 128-bit namespace, held only in that owner's object; a chunk's
 * `name` is the HMAC of its content hash under the owner's data key (`datakey.ts`
 * `chunkName`), so the bucket's keys confirm no known file. Both are lowercase hex, checked
 * here, so no path, owner id or other caller text ever reaches a key. A path never becomes a key because R2
 * NFC-normalizes keys, and uploads are verbatim (Fact 9): two names a sandbox can create
 * would meet in one object. The r2-keys guard keeps every other source from building one.
 */
import { HOLDS, MARKS, namespace, objectPrefix, segmentSpans } from "../../contract/src/files";
import { Refusal } from "../../contract/src/refusal";
import { HEX16, HEX64 } from "../../contract/src/ids";
import { MANIFEST_PARTS } from "../../contract/src/limits";

const SHA256 = HEX64;
/** A generation's id: 16 lowercase hex characters, the owner's object makes it. */
const GEN = HEX16;

/** A chunk's SHA-256: 64 lowercase hex characters. */
export function chunkHash(sha256: unknown): string {
  if (typeof sha256 !== "string" || !SHA256.test(sha256)) {
    throw new Refusal("invalid", "a chunk hash is 64 lowercase hex characters");
  }
  return sha256;
}

/** The R2 key of one owner's chunk, by its name (64 lowercase hex, as a hash is). */
export function chunkKey(ns: unknown, name: unknown): string {
  return `${chunkPrefix(ns)}${chunkHash(name)}`;
}

/** A generation's id: 16 lowercase hex characters. */
export function generation(gen: unknown): string {
  if (typeof gen !== "string" || !GEN.test(gen)) {
    throw new Refusal("invalid", "a manifest's generation is 16 lowercase hex characters");
  }
  return gen;
}

/** A log segment's span: whole seqs, `a` before `b`. */
export function segmentSpan(a: unknown, b: unknown): [number, number] {
  if (!segmentSpans(a, b)) {
    throw new Refusal("invalid", "a manifest segment spans whole seqs, its first before its last");
  }
  return [a, b as number];
}

/** A manifest part's number: 0 to `MANIFEST_PARTS - 1`. */
export function manifestPart(part: unknown): number {
  if (typeof part !== "number" || !Number.isSafeInteger(part) || part < 0
      || part >= MANIFEST_PARTS) {
    throw new Refusal("invalid", `a manifest part is 0 to ${MANIFEST_PARTS - 1}`);
  }
  return part;
}

/** The R2 key of one owner's generation root (`part` absent) or one of its base parts. */
export function manifestKey(ns: unknown, gen: unknown, part?: unknown): string {
  const root = `${manifestPrefix(ns)}${generation(gen)}`;
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
  return `${manifestKey(ns, gen)}/l/${from}-${to}`;
}

/**
 * The R2 key of the mark that `ns` was deleted on purpose (`e/<ns>`, the contract's `MARKS`):
 * outside the namespace's prefix, so its own delete spares it.
 */
export function markKey(ns: unknown): string {
  return `${MARKS}${namespace(ns)}`;
}

/** The R2 key that records `held` as held by `ns` (`o/<ns>/h/<held>`, the contract's `HOLDS`). */
export function holdKey(ns: unknown, held: unknown): string {
  if (namespace(held) === namespace(ns)) {
    throw new Refusal("invalid", "a namespace never holds itself");
  }
  return `${holdPrefix(ns)}${held}`;
}

/** The prefix every hold of one namespace starts with: the erasure's list of them. */
export function holdPrefix(ns: unknown): string {
  return `${objectPrefix(ns)}${HOLDS}`;
}

/** The held namespace a key listed under `holdPrefix(ns)` names, or a loud failure. */
export function listedHold(ns: unknown, key: unknown): string {
  const prefix = holdPrefix(ns);
  if (typeof key !== "string" || !key.startsWith(prefix)) {
    throw new Error("a listed key is not under its namespace's prefix");
  }
  return namespace(key.slice(prefix.length));
}

/** The prefix every chunk key of one owner's namespace starts with: the erasure's list. */
export function chunkPrefix(ns: unknown): string {
  return `${objectPrefix(ns)}c/`;
}

/** The chunk name a key listed under `chunkPrefix(ns)` holds, or a loud failure. */
export function listedChunk(ns: unknown, key: unknown): string {
  const prefix = chunkPrefix(ns);
  if (typeof key !== "string" || !key.startsWith(prefix)) {
    throw new Error("a listed key is not under its namespace's prefix");
  }
  return chunkHash(key.slice(prefix.length));
}

/**
 * The prefix every manifest key of one owner's namespace starts with: the erasure's list, and
 * the sweep's.
 */
export function manifestPrefix(ns: unknown): string {
  return `${objectPrefix(ns)}m/`;
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

/**
 * A key listed under `manifestPrefix(ns)`, rebuilt (`listedManifest`), with the generation it
 * belongs to and whether it is that generation's root; a loud failure for any other key.
 */
export function listedGeneration(ns: unknown, key: unknown):
    { key: string; gen: string; root: boolean } {
  const rebuilt = listedManifest(ns, key);
  const [gen, ...rest] = rebuilt.slice(manifestPrefix(ns).length).split("/");
  return { key: rebuilt, gen: gen!, root: rest.length === 0 };
}
