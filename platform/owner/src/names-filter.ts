/**
 * A restore's filter of chunk names (`restore.ts`, its `names` and `sweep` steps;
 * `docs/decisions/untangle-2026-11/r-restore-chunks.md`): a Bloom filter of the bucket name of
 * every chunk row, so the sweep deletes only a listed chunk the filter says no row names.
 *
 * A filter answers no false negative: every name added reads as held, so no chunk a row names
 * is ever deleted. A false positive keeps a chunk no row names, which then stays until the
 * owner's erasure: a leak of at most about `FILTER_FALSE` of the unnamed chunks, and only
 * after a restore.
 *
 * It is sized once, for the most chunk rows the file index holds (`CHUNK_ROWS_MAX`), so one
 * filter serves every owner and is one row of the owner's object, under SQLite's 2 MB value.
 * A name is an HMAC under the owner's data key (`files/src/datakey.ts` `chunkName`), so its
 * bytes are uniformly random: each of the filter's `FILTER_HASHES` bit indices is one 32-bit
 * slice of the name, taken modulo the filter's bits, and no second hash is needed.
 */
import { CHUNK_ROWS_MAX } from "./manifest";

/** The share of absent names the filter may read as held, at `CHUNK_ROWS_MAX` names. */
export const FILTER_FALSE = 0.01;
/** Its bits: the least for `CHUNK_ROWS_MAX` names at `FILTER_FALSE`, in whole bytes. */
export const FILTER_BITS = (): number => Math.ceil(CHUNK_ROWS_MAX() * Math.log(1 / FILTER_FALSE)
  / Math.LN2 ** 2 / 8) * 8;
/** Its bytes, as its row holds them. */
export const FILTER_BYTES = (): number => FILTER_BITS() / 8;
/** The bits each name sets: the best count for that many bits a name. */
export const FILTER_HASHES = (): number => Math.round(FILTER_BITS() / CHUNK_ROWS_MAX() * Math.LN2);

/** Hex characters of a name each bit index is read from: 32 bits, of a name's 64. */
export const SLICE = 8;

/** `name`'s bit indices, in a filter of `bits` bits, `hashes` a name. */
function indices(name: string, bits: number, hashes: number): number[] {
  if (!/^[0-9a-f]{64}$/.test(name)) {
    throw new Error("a chunk name is 64 lowercase hex characters");
  }
  return Array.from({ length: hashes }, (_, at) =>
    Number.parseInt(name.slice(at * SLICE, (at + 1) * SLICE), 16) % bits);
}

/** An empty filter. */
export function emptyFilter(): Uint8Array {
  return new Uint8Array(FILTER_BYTES());
}

/** `names` added to `filter`, in place: adding a name twice changes nothing. */
export function addNames(filter: Uint8Array, names: readonly string[]): void {
  const [bits, hashes] = [FILTER_BITS(), FILTER_HASHES()];
  for (const name of names) {
    for (const bit of indices(name, bits, hashes)) {
      filter[bit >> 3] = filter[bit >> 3]! | (1 << (bit & 7));
    }
  }
}

/** Whether `filter` may hold `name`: false only for a name never added. */
export function mayHold(filter: Uint8Array, name: string): boolean {
  return indices(name, FILTER_BITS(), FILTER_HASHES())
    .every((bit) => (filter[bit >> 3]! & (1 << (bit & 7))) !== 0);
}

/** `bytes` as a filter, or a loud failure for any other size, which would lose its bits. */
export function filterOf(bytes: ArrayBuffer): Uint8Array {
  if (bytes.byteLength !== FILTER_BYTES()) {
    throw new Error(`a restore's names filter is ${bytes.byteLength} bytes, not ` +
                    `${FILTER_BYTES()}`);
  }
  return new Uint8Array(bytes);
}
