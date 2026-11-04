/**
 * The owner object's holds of large data (ADR-037): its isolate's one pool of units
 * (`slots.ts`, a unit `UNIT_BYTES`, one chunk), shared by every object the isolate runs, and
 * what each path takes of it while its bytes are in hand.
 *
 * Each cost is the most its step holds in the owner isolate, as the workerd memory leg
 * measures it on the real path at its worst (`memory/leg.mjs`: distinct content, so every
 * chunk is remade, at sizes past the path's steady state); the leg holds each path's peak
 * within it. The counts test (`guards/memory-counts.test.ts`) holds the pool, with the
 * isolate's other live holds and its resting baseline, within the isolate's 128 MB.
 */
import { isolatePool, type SlotPool } from "./slots";

/**
 * The pool's capacity, in units (48 MiB): three edits' making steps at once
 * (`EDIT_MAKE_UNITS`) beside the reserve, or a whole body's read (`bodyUnits`, two chunks)
 * beside one. The counts test holds it, the waiting calls' own units, the relay's terms and
 * the baseline within the isolate; production's `memoryUsageBytes` resets it in one line.
 */
export const OWNER_POOL_UNITS = 12;

/**
 * Units only one-unit holds take (`SlotPool`'s reserve): a body window, a co-tenant's
 * small read, never waits behind an edit's steps (review M1).
 */
export const OWNER_RESERVE_UNITS = 1;

/**
 * How many calls may wait for units at once (`SlotPool`'s waiters): as many as the pool's
 * units. A waiting call holds nothing a unit counts, but it holds its own request, its
 * arguments, which no unit counts yet (owed with PR 3b's request weights, as every RPC
 * argument is); at most a unit each by the MCP door (`MCP_BODY_MAX`, 4 MiB), so the calls
 * waiting hold at most the pool's own size again. One more is refused `busy` at once.
 */
export const OWNER_WAITERS = OWNER_POOL_UNITS;

/** A chunk-wise edit's reading step (its scan, its hashes): one source chunk in hand. */
export const EDIT_READ_UNITS = 1;

/**
 * A chunk-wise edit's making step: the chunk made, the source chunk it is copied from, and
 * the put's copy of what it sends (review H2 measured 12.12 MiB at the worst edit with a
 * source chunk cached across steps; with none, the leg reads 8.06 MiB).
 */
export const EDIT_MAKE_UNITS = 3;

/**
 * A chunked `file_append`'s one step: the file's last chunk, the appended text (at most a
 * chunk), the two joined (two chunks), and slack for the write's own (measured 16.02 MiB at
 * its worst, a last chunk a KiB short of whole and a whole chunk of text).
 */
export const APPEND_UNITS = 5;

/** Body windows (`Bodies.windows`): each at most a page, read by verified ranges. */
export const WINDOW_UNITS = 1;

/**
 * A whole body's read (`Bodies.fetch`): per chunk, its bytes, its text (a one-byte string, or
 * UTF-16 for wider text: never more bytes than its UTF-8) and slack (measured 8.01 MiB for a
 * two-chunk body, the text taken after its parts are let go).
 */
export function bodyUnits(chunks: number): number {
  return 3 * chunks;
}

/**
 * The owner Worker's resting isolate, in workerd: an owner object constructed, its SQLite
 * open and migrated, one request served, after a swept collection, heap plus backing
 * stores. The leg measures it each run and holds it within this figure; production's
 * `memoryUsageBytes` replaces it.
 */
export const OWNER_BASELINE_BYTES = 8 * 1024 * 1024;

/** The isolate's pool, at the owner Worker's capacity and reserve. */
export function ownerSlots(): SlotPool {
  return isolatePool(OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, OWNER_WAITERS);
}
