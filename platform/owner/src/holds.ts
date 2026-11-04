/**
 * The owner object's holds of large data (ADR-037, memshape_research.md §2): its isolate's
 * one pool of chunk slots (`slots.ts`), shared by every object the isolate runs.
 *
 * `OWNER_CHUNK_SLOTS` is the pool's capacity: each slot holds at most `CHUNK_SLOT_BYTES`
 * (two chunks in hand and the files plane's copy of one, 12 MiB). The counts test
 * (`guards/memory-counts.test.ts`) holds the capacity times that cost, with the relay's tail
 * pool (`TAIL_POOL_BYTES`, `relay.ts`), the Worker's baseline and what no unit models, within
 * the isolate's 128 MB. Paths that take a slot while they hold chunks: a chunk-wise
 * `file_edit`, a chunked `file_append`, and a read's body windows.
 */
import { isolatePool, type SlotPool } from "./slots";

export const OWNER_CHUNK_SLOTS = 4;

/** The isolate's pool, at the owner Worker's capacity. */
export function ownerSlots(): SlotPool {
  return isolatePool(OWNER_CHUNK_SLOTS);
}
