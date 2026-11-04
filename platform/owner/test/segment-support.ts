/**
 * The archive segment's format, encoded whole by a reference encoder (round 3's, as the
 * records' parts joined with the trailer), so the streamed move (`planSegment`, `makeChunk`)
 * is held to the same bytes; and the helpers that stream a plan in a test.
 */
import { createHash } from "node:crypto";

import { CHUNK_BYTES } from "../src/manifest";
import {
  type Content, joined, makeChunk, type Placement, type Plan, recordParts, type SegmentRecord,
  type Source, type Trailer,
} from "../src/segments";

/** A stream's records encoded whole, as the format says (`segments.ts`'s header). */
export function referenceSegment(stream: string, records: readonly SegmentRecord[]):
    { bytes: Uint8Array; trailer: Trailer; trailer_offset: number; trailer_bytes: number } {
  const parts: Uint8Array[] = [];
  const keys: Placement[] = [];
  const chains: Record<string, Placement[]> = {};
  let offset = 0;
  for (const record of records) {
    const own = recordParts(record);
    const length = own.reduce((sum, part) => sum + part.byteLength, 0);
    const placement: Placement = [record.key, offset, length];
    keys.push(placement);
    if (record.chain !== null) {
      (chains[record.chain] ??= []).push(placement);
    }
    parts.push(...own);
    offset += length;
  }
  const trailer: Trailer = { stream, first: keys[0]![0], last: keys.at(-1)![0],
                             records: keys.length, keys, chains };
  const line = new TextEncoder().encode(`${JSON.stringify({ t: "segment", ...trailer })}\n`);
  parts.push(line, new TextEncoder().encode(String(offset).padStart(16, "0")));
  return { bytes: joined(parts), trailer, trailer_offset: offset,
           trailer_bytes: line.byteLength };
}

/** A content of `bytes`, cut into 4 MiB chunks named by their hashes, and its reader. */
export function storedContent(id: string, bytes: Uint8Array):
    { content: Content; chunks: Map<string, Uint8Array> } {
  const chunks = new Map<string, Uint8Array>();
  const names: string[] = [];
  for (let at = 0; at < bytes.byteLength; at += CHUNK_BYTES) {
    const chunk = bytes.slice(at, at + CHUNK_BYTES);
    const name = createHash("sha256").update(chunk).digest("hex");
    chunks.set(name, chunk);
    names.push(name);
  }
  return { content: { id, chunks: names, size: bytes.byteLength }, chunks };
}

/** A plan's whole bytes, made a chunk at a time as a move makes them. */
export async function streamed(
    plan: Plan, sources: (from: number, to: number) => Promise<readonly (readonly Source[])[]>,
    read: (content: Content, index: number) => Promise<Uint8Array>): Promise<Uint8Array> {
  const parts: Uint8Array[] = [];
  for (let n = 0; n < plan.chunks; n++) {
    parts.push(await makeChunk(plan, n, sources, read));
  }
  return joined(parts);
}
