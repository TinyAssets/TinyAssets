/**
 * A file edited chunk by chunk (`file_edit`, memshape_research.md §4, ADR-037): one exact
 * occurrence of `old` replaced by `new` without the file ever being held whole, so its
 * memory does not depend on the file's size.
 *
 * 1. **Scan** the source a chunk at a time: count the matches of `old`'s UTF-8 bytes (a
 *    match across a chunk's end found through a carry of `|old| - 1` bytes, so none is
 *    counted twice), and check the UTF-8 as it goes. UTF-8 synchronizes on itself, so byte
 *    matches of well-formed text are exactly the text's (`String.indexOf`'s, overlapping ones
 *    counted as it counts them). Ill-formed `old` or `new` (a lone surrogate) is refused.
 * 2. **Hash** each output chunk from the source pieces it spans, never building it: the
 *    chunks before the change keep their hashes, and when `|new| = |old|` the chunks past it
 *    too, unread.
 * 3. **Make** each chunk the files plane lacks, one at a time, from at most two source
 *    chunks (`make`). Sources are content-addressed, so it is the bytes hashed; one that
 *    differs fails loudly (Fact 8).
 *
 * **Steps.** Each pass reads one source chunk at a time, in a step of its own (`step`, the
 * caller's units: `EDIT_READ_UNITS`, `holds.ts`), its bytes dropped as the step ends, so
 * nothing chunk-sized is held between steps: only the scan's carry (under `|old|` bytes) and
 * the hashes' states. `make` builds one output chunk from the source chunks it spans, read
 * one at a time; the caller holds its making step's units around it and the put that sends
 * it (`Files.writeChunks`). Each source chunk read is the edit's own and is let go as soon
 * as its pieces are taken (`letGo`), so a step holds the chunk in hand and no more. Its cost
 * is reads: about four a changed chunk.
 */
import { utf8Decoder } from "../../contract/src/text";
import { sha256Hex } from "../../contract/src/digest";
import { createHash } from "node:crypto";

/** The source: its chunks in order, each read whole and verified (`read`). */
export interface Source {
  readonly size: number;
  readonly chunks: readonly { readonly sha256: string; readonly size: number }[];
  readonly read: (index: number) => Promise<Uint8Array>;
}

/** An edit's refusal: why, as today's whole-file editor refuses it. */
export type EditRefusal = "ill_formed" | "not_utf8" | "absent" | { readonly times: number };

/** A step of an edit's reading: `work` run while the caller holds its units. */
export type Step = <T>(work: () => Promise<T>) => Promise<T>;

const direct: Step = (work) => work();

/** The edited file: its size and chunk hashes, and each chunk made on demand. */
export interface Edited {
  readonly size: number;
  readonly hashes: readonly string[];
  readonly sizes: readonly number[];
  readonly make: (index: number) => Promise<Uint8Array>;
}

const SCAN_SLICE = 64 * 1024;

function chunkPlan(size: number, chunk: number): number[] {
  const sizes: number[] = [];
  for (let at = 0; at < size; at += chunk) {
    sizes.push(Math.min(chunk, size - at));
  }
  return sizes;
}

function indexOfBytes(hay: Uint8Array, needle: Uint8Array, from: number): number {
  return Buffer.from(hay.buffer, hay.byteOffset, hay.byteLength)
    .indexOf(Buffer.from(needle.buffer, needle.byteOffset, needle.byteLength), from);
}

/**
 * A source chunk's bytes let go as its step ends: the edit owns what `read` answers, so its
 * buffer is detached (`transfer(0)`), and no reference the collector cannot yet rule out
 * (review M-a: one dead chunk now and then read live one collection longer) keeps them.
 */
export function letGo(bytes: Uint8Array): void {
  const buffer = bytes.buffer as ArrayBuffer & { transfer?: (length: number) => ArrayBuffer };
  if (bytes.byteOffset === 0 && bytes.byteLength === buffer.byteLength
      && typeof buffer.transfer === "function") {
    buffer.transfer(0);
  }
}

/**
 * Plan the edit of `source` (chunks of `chunk` bytes, the last shorter), or its refusal, each
 * source chunk read in a step of its own (`step`).
 */
export async function planEdit(source: Source, old: string, replacement: string,
                               chunk: number, step: Step = direct):
    Promise<Edited | { refused: EditRefusal }> {
  if (!old.isWellFormed() || !replacement.isWellFormed()) {
    return { refused: "ill_formed" };
  }
  const oldBytes = new TextEncoder().encode(old);
  const newBytes = new TextEncoder().encode(replacement);
  const k = oldBytes.byteLength;
  // Pass 1: scan.
  const decoder = utf8Decoder("keep");
  let carry = new Uint8Array(0);
  let carryAt = 0;
  let times = 0;
  let first = -1;
  let offset = 0;
  for (let index = 0; index < source.chunks.length; index++) {
    // One step: the chunk read, scanned and dropped; only the carry goes on.
    const refused = await step(async (): Promise<EditRefusal | null> => {
      const bytes = await source.read(index);
      try {
        for (let at = 0; at < bytes.byteLength; at += SCAN_SLICE) {
          decoder.decode(bytes.subarray(at, Math.min(at + SCAN_SLICE, bytes.byteLength)),
                         { stream: true });
        }
      } catch {
        return "not_utf8";
      }
      if (carry.byteLength > 0) {
        // Matches that begin in the carry end in this chunk: the carry and this chunk's head.
        const head = bytes.subarray(0, Math.min(k - 1, bytes.byteLength));
        const seam = new Uint8Array(carry.byteLength + head.byteLength);
        seam.set(carry, 0);
        seam.set(head, carry.byteLength);
        for (let at = indexOfBytes(seam, oldBytes, 0); at >= 0 && at < carry.byteLength;
             at = indexOfBytes(seam, oldBytes, at + 1)) {
          times += 1;
          first = first < 0 ? carryAt + at : first;
        }
      }
      for (let at = indexOfBytes(bytes, oldBytes, 0); at >= 0;
           at = indexOfBytes(bytes, oldBytes, at + 1)) {
        times += 1;
        first = first < 0 ? offset + at : first;
      }
      const keep = Math.max(0, k - 1);
      if (keep === 0) {
        carry = new Uint8Array(0);
      } else if (bytes.byteLength >= keep) {
        carry = bytes.slice(bytes.byteLength - keep);
      } else {
        const joined = new Uint8Array(carry.byteLength + bytes.byteLength);
        joined.set(carry, 0);
        joined.set(bytes, carry.byteLength);
        carry = joined.slice(Math.max(0, joined.byteLength - keep));
      }
      offset += bytes.byteLength;
      carryAt = offset - carry.byteLength;
      letGo(bytes);
      return null;
    });
    if (refused !== null) {
      return { refused };
    }
  }
  try {
    decoder.decode();
  } catch {
    return { refused: "not_utf8" };
  }
  if (first < 0) {
    return { refused: "absent" };
  }
  if (times > 1) {
    return { refused: { times } };
  }
  // The edit in output space: out = src[0, m) + new + src[m + k, size).
  const m = first;
  const size = source.size - k + newBytes.byteLength;
  const sizes = chunkPlan(size, chunk);
  const changedFrom = Math.floor(m / chunk);
  const changedTo = newBytes.byteLength === k ? Math.floor((m + Math.max(k, 1) - 1) / chunk)
    : sizes.length - 1;
  const starts: number[] = [];
  for (let at = 0, index = 0; index < source.chunks.length; index++) {
    starts.push(at);
    at += source.chunks[index]!.size;
  }
  // A source reader by byte range: each chunk it spans read once, in `gate`'s step, and
  // dropped once its pieces are sunk; nothing is cached across steps.
  const sourceRange = async (from: number, to: number, sink: (piece: Uint8Array) => void,
                             gate: Step) => {
    while (from < to) {
      const index = Math.floor(from / chunk);
      from = await gate(async () => {
        const bytes = await source.read(index);
        const lo = from - starts[index]!;
        const hi = Math.min(to - starts[index]!, bytes.byteLength);
        sink(bytes.subarray(lo, hi));
        letGo(bytes);
        return starts[index]! + hi;
      });
    }
  };
  const outRange = async (from: number, to: number, sink: (piece: Uint8Array) => void,
                          gate: Step) => {
    if (from < m) {
      await sourceRange(from, Math.min(to, m), sink, gate);
    }
    const newStart = Math.max(from, m);
    const newEnd = Math.min(to, m + newBytes.byteLength);
    if (newStart < newEnd) {
      sink(newBytes.subarray(newStart - m, newEnd - m));
    }
    const tail = Math.max(from, m + newBytes.byteLength);
    if (tail < to) {
      await sourceRange(tail - newBytes.byteLength + k, to - newBytes.byteLength + k, sink,
                        gate);
    }
  };
  // Pass 2: the changed chunks' hashes, the output streamed once from the first changed
  // chunk to the last, each source chunk read once, cut into its chunks as it goes.
  const hashes = source.chunks.slice(0, changedFrom).map((each) => each.sha256);
  const from = changedFrom * chunk;
  const to = sizes.slice(0, changedTo + 1).reduce((sum, each) => sum + each, 0);
  let hash = createHash("sha256");
  let left = sizes[changedFrom] ?? 0;
  await outRange(from, Math.max(from, to), (piece) => {
    for (let at = 0; at < piece.byteLength;) {
      const take = Math.min(left, piece.byteLength - at);
      hash.update(piece.subarray(at, at + take));
      at += take;
      left -= take;
      if (left === 0) {
        hashes.push(hash.digest("hex"));
        hash = createHash("sha256");
        left = sizes[hashes.length] ?? 0;
      }
    }
  }, step);
  if (hashes.length !== Math.max(changedFrom, changedTo + 1)) {
    throw new Error("an edit's hashes did not cover its changed chunks");
  }
  for (let index = changedTo + 1; index < sizes.length; index++) {
    hashes.push(source.chunks[index]!.sha256);
  }
  return {
    size, hashes, sizes,
    make: async (index) => {
      const out = new Uint8Array(sizes[index]!);
      let written = 0;
      // Inside the caller's making step, which holds this chunk and its sources' units.
      await outRange(index * chunk, index * chunk + sizes[index]!, (piece) => {
        out.set(piece, written);
        written += piece.byteLength;
      }, direct);
      if (sha256Hex(out) !== hashes[index]) {
        throw new Error("a file's source changed between an edit's passes");
      }
      return out;
    },
  };
}
