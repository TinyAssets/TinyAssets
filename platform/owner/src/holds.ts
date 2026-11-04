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

/**
 * The owner Worker's resting isolate, in workerd, TO BE REPLACED by production's
 * `memoryUsageBytes`: its bundle loaded with no bindings, one request served, after a swept
 * collection, heap plus backing stores, 5.47 MiB (`memory/baselines.mjs`, 2026-11-04; the
 * doors' 7.28 MiB, the files Worker's 0.73), rounded up.
 */
export const OWNER_BASELINE_BYTES = 8 * 1024 * 1024;

/**
 * What no unit models: garbage not yet collected and the runtime's own buffers, sized from
 * the measured gap, TO BE REPLACED by production's: with no forced collection, chunks made
 * and dropped level off at 78.9 MiB of workerd's RSS (rpcretain_research.md §2), whose own
 * resting RSS is 55.7 MiB (this leg's run), so about 23 MiB, rounded up.
 */
export const UNMODELED_BYTES = 24 * 1024 * 1024;

/** The isolate's pool, at the owner Worker's capacity. */
export function ownerSlots(): SlotPool {
  return isolatePool(OWNER_CHUNK_SLOTS);
}
