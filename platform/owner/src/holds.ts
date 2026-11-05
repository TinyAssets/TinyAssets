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
import { isolatePool, type SlotPool, UNIT_BYTES } from "./slots";

/**
 * The pool's capacity, in units (48 MiB): three edits' making steps (`EDIT_MAKE_UNITS`) at
 * once beside the reserve, or a whole body's read (`bodyUnits`, two chunks) beside a making
 * step. The counts test holds it with the relay's terms and the baseline
 * within the isolate; production's `memoryUsageBytes` resets it in one line.
 */
export const OWNER_POOL_UNITS = 12;

/**
 * Units only one-unit holds take (`SlotPool`'s reserve): a body window, a co-tenant's
 * small read, never waits behind an edit's steps (review M1).
 */
export const OWNER_RESERVE_UNITS = 1;

/**
 * How many larger holds may wait for units at once (`SlotPool`'s waiters), by the memory a
 * waiter holds: no units, but its own request's arguments, which no unit counts yet (owed
 * with PR 3b's request weights, as every RPC argument is), as large as the largest body a
 * door takes: `/api`'s 16 MiB (`API_BODY_MAX`, four units; `/mcp`'s `MCP_BODY_MAX` is one).
 * Three keep what the waiting calls hold to the pool's own size again (the counts test
 * holds the figure to that derivation). One more is refused `busy` at once.
 */
export const OWNER_WAITERS = 3;

/**
 * How many one-unit holds (windows) may wait at once, apart from the larger ones, so a
 * co-tenant's small read is never refused for edits waiting. A one-unit hold is a read's:
 * its arguments are ids, cursors and limits, whose fields the door checked before it asked
 * for a unit. So the cap is the queue's turnover: the reserve serves them one at a time,
 * and at the leg's measured window time 64 turn over well within `SLOT_WAIT_MS` (the leg
 * fails past it).
 */
export const OWNER_SMALL_WAITERS = 64;

/**
 * A chunk-wise edit's reading step (its scan, its hashes): the source chunk in hand and the
 * call's copy of it as it arrives (the leg reads about 4.07 MiB).
 */
export const EDIT_READ_UNITS = 2;

/**
 * A chunk-wise edit's making step: the chunk made, the source chunk in hand as it is copied
 * (each let go as its pieces are taken, `edit.ts`'s `letGo`, so none outlives its part:
 * review M-a read 12.07 MiB with two source chunks still alive), and the put's copy of what
 * it sends.
 */
export const EDIT_MAKE_UNITS = 3;

/**
 * A chunked `file_append`'s one step: the file's last chunk, the appended text (at most a
 * chunk), the two joined (two chunks), and slack for the write's own (measured 16.02 MiB at
 * its worst, a last chunk a KiB short of whole and a whole chunk of text).
 */
export const APPEND_UNITS = 5;

/**
 * What an archive move's or rewrite's records hold, held for the move's life (its bytes are
 * in hand throughout: `moveTableUnits`): an aggregate bound, a move's records together at
 * most this many bytes each, not each record's own. Derived from the structure the code
 * builds, each array at twice what it holds (they grow by doubling): its table (`MoveTable`:
 * 53 bytes in typed arrays, 106 doubled; its chain's name, a message id of at most 36 bytes
 * in the table's pool, 72 doubled), its plan's typed arrays (`planSegment`, 42) and the
 * move's lists of its keys and indices (the pick's seqs, its order, its pieces: 36): 256. A
 * record with a placed body costs more on its own (its content's entry, about 81 bytes, 162
 * doubled), but it is at least `PLACE_BYTES`, so `MOVE_BYTES` bounds such records to about
 * 16,000 a move: the worst mix (a piece of the least records and placed stragglers) is about
 * 11.4 MiB, within the units at `MOVE_RECORDS`. Measured in one run of the workerd memory
 * leg (2026-11-04 17:30 UTC, `limits-audit.md` names it): a move's records built by its own
 * code at `MOVE_RECORDS` (no real mail reaches it) 12.98 MiB, 182 bytes a record; the merge
 * of the most records real mail reaches (32,568) 4.80 MiB at its passes' ends, 155; a tail
 * seal of 8,489 small messages (its arrays near the doubling's worst, at 16,384) 1.68 MiB,
 * 208.
 */
export const MOVE_RECORD_BYTES = 256;

/**
 * The units a move's records hold at `records` records (at least one; at `MOVE_RECORDS`, the
 * most a move holds, five), taken before its records arrive and held to the move's end; each
 * pass takes `MOVE_PASS_UNITS` beside them (a lease grown and shrunk back, `SlotPool.lease`).
 */
export function moveTableUnits(records: number): number {
  return Math.max(1, Math.ceil(records * MOVE_RECORD_BYTES / UNIT_BYTES));
}

/**
 * One pass of an archive move (a scan, the hashing pass, the sending pass): the source chunk
 * in hand (`MoveReader`, detached as the next arrives and as the pass ends), the chunk made
 * from it (detached once hashed or sent), and the put's copy of what it sends, as an edit's
 * making step (`EDIT_MAKE_UNITS`). The same run reads at most 8.07 MiB of a pass of two 8 MiB
 * bodies beside a one-unit table, the same at 4 MiB (O(1) in their size).
 */
export const MOVE_PASS_UNITS = 3;

/**
 * A generation's burst (`backup.ts`, the manifest lane): its pages' buffers and line ends (at
 * most `GATHER_BYTES` and its last page), then its parts beside them, and the part its call
 * is sending: twice two parts and a page, and a part, rounded up to whole units. Taken now
 * or not at all, from its first page to its last part's put (inside the alarm a lane never
 * waits on the pool), so every object's burst is inside the pool. The leg reads 16.46 MiB at
 * a put, a burst of the smallest lines, and holds it within these at every checkpoint.
 */
export const BURST_UNITS = 6;

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
  return isolatePool(OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, OWNER_WAITERS,
                     OWNER_SMALL_WAITERS);
}
