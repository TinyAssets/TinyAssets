/**
 * Content ids (plan §6): what the files plane names a file's bytes by. They are
 * deterministic, so the owner's object, its starter and the cutover's importer
 * (`platform/import/`) all give the same bytes the same id, and the importer's expected
 * manifest is the platform's own.
 *
 * Content of at most `INLINE_BYTES` is kept inline in the owner's object; larger content is
 * cut into `CHUNK_BYTES` chunks, each named by its SHA-256, and the content by its size and
 * ordered chunks.
 */
import { sha256Hex } from "./digest";
import { CHUNK_BYTES, INLINE_BYTES } from "./limits";

/** The content id of small content kept inline: its kind, size and bytes. */
export function inlineId(bytes: Uint8Array): string {
  return sha256Hex(Buffer.concat([Buffer.from(`ta-inline\n${bytes.byteLength}\n`), bytes]));
}

/**
 * The content id of chunked content: its kind, size and ordered chunks. A one-chunk file
 * is a list of one, so no kind of its own; the kind keeps it apart from a chunk's own
 * hash and from inline content.
 */
export function chunkedId(size: number, shas: readonly string[]): string {
  return sha256Hex(`ta-chunks\n${size}\n${shas.join("\n")}`);
}

/** The chunk sizes a file of `size` bytes is cut into. */
export function chunkPlan(size: number): number[] {
  const sizes: number[] = [];
  for (let at = 0; at < size; at += CHUNK_BYTES) {
    sizes.push(Math.min(CHUNK_BYTES, size - at));
  }
  return sizes;
}

/** Bytes cut into 4 MiB chunks, each with its hash. */
export function cut(bytes: Uint8Array): { sha256: string; size: number; bytes: Uint8Array }[] {
  return chunkPlan(bytes.byteLength).map((size, index) => {
    const piece = bytes.subarray(index * CHUNK_BYTES, index * CHUNK_BYTES + size);
    return { sha256: sha256Hex(piece), size, bytes: piece };
  });
}

/** The content id the files plane gives these bytes. */
export function contentIdOf(bytes: Uint8Array): string {
  return bytes.byteLength <= INLINE_BYTES ? inlineId(bytes)
    : chunkedId(bytes.byteLength, cut(bytes).map((chunk) => chunk.sha256));
}
