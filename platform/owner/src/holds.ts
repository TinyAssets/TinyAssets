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
import { CHUNK_LINE_BYTES, EXPORT_PAGE_BYTES, EXPORT_READ_BATCH } from "./export";
import { CHUNK_BYTES, IMAGE_MAX } from "../../contract/src/limits";
import {
  type Isolate, isolatePool, isolateTails, type SharedBudget, type SlotPool, UNIT_BYTES,
} from "../../contract/src/slots";

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
 * A log segment's one buffer at most (`ChangeLog.segment`): exactly its bytes as written,
 * allocated once and never grown, never past a part (`CHUNK_BYTES`): `pending` sizes it by
 * its lines as written, so it always fits one. It is returned as an exact copy (a view would
 * carry the whole part over RPC: rvburst X1), beside the buffer once its rows are let go,
 * so twice it at most, within `SHIP_BYTES`.
 */
const SEGMENT_BUFFER_BYTES = CHUNK_BYTES;

/**
 * One row's text while a segment encodes it, at its worst: the widest row image the log takes
 * (`IMAGE_MAX` of UTF-8, so at most that many characters), held three times at once (its
 * stored text, its parsed value, its JSON written again), each a two-byte string in V8 once
 * one character is past U+00FF (review rvburst V1).
 */
const SEGMENT_ROW_BYTES = 3 * 2 * IMAGE_MAX;

/** A segment's build at its most: its buffer and one row's text. */
export const SHIP_BYTES = SEGMENT_BUFFER_BYTES + SEGMENT_ROW_BYTES;

/**
 * One log segment shipped outside a burst (`backup.ts` `#ship`: before a burst, after its
 * parts' record, and the log's last at the generation's end), its build at its most
 * (`SHIP_BYTES`) in whole units. Taken now or not at all around each segment's sizing,
 * build, put and acknowledgement; no segment is built inside a burst. The memory leg
 * holds every checkpoint within them, sampling the build at its widest row's encode ("a log
 * segment shipped outside a burst": a full segment with a row at `IMAGE_MAX`, one character
 * past U+00FF, among 1,000-character lines of the same mix, its put over RPC).
 */
export const SHIP_UNITS = (SHIP_BYTES + UNIT_BYTES - 1
  - ((SHIP_BYTES + UNIT_BYTES - 1) % UNIT_BYTES)) / UNIT_BYTES;  // rounded up, by arithmetic

/** A burst's gathered bytes before its last page (`backup.ts`'s `GATHER_BYTES`): two parts. */
export const BURST_GATHER_BYTES = 2 * CHUNK_BYTES;

/**
 * A burst's bytes at most: its gather, then its last page (`EXPORT_PAGE_BYTES` of rows and
 * the widest line that reached it, an image at most `IMAGE_MAX`, and a page's chunk lines,
 * `EXPORT_READ_BATCH` of `CHUNK_LINE_BYTES`), held once as its pages and again as its parts.
 */
const BURST_BYTES = BURST_GATHER_BYTES + EXPORT_PAGE_BYTES + IMAGE_MAX
  + EXPORT_READ_BATCH * CHUNK_LINE_BYTES;

/**
 * One base page's build at its most (`readPage`, then `encodeLines` outside its unit): its
 * lines as text, two bytes a character at worst (`EXPORT_PAGE_BYTES` of rows and the widest
 * line that reached it, `IMAGE_MAX`), and then its one buffer beside them, encoded once and
 * exactly (`EXPORT_PAGE_BYTES`, `IMAGE_MAX` and its chunk lines); or, as a row is read, its
 * values (`IMAGE_MAX`, two bytes a character) beside the text so far. Its rows come a row at
 * a time from the cursor, never a batch held whole (rvburst W1).
 */
const PAGE_BUILD_BYTES = 2 * (EXPORT_PAGE_BYTES + IMAGE_MAX)
  + (EXPORT_PAGE_BYTES + IMAGE_MAX + EXPORT_READ_BATCH * CHUNK_LINE_BYTES) + 2 * IMAGE_MAX;

/** A burst at its cut: its pages and its parts. */
export const BURST_CUT_BYTES = 2 * BURST_BYTES;
/** A burst at its last page's build: the pages before it (under its gather) and the build. */
export const BURST_PAGE_BYTES = BURST_GATHER_BYTES + PAGE_BUILD_BYTES;
/**
 * A burst at the more of its two peaks (`BURST_UNITS`), by arithmetic (a module binds no call
 * or conditional): exact for whole bytes this size (`memory-counts.test.ts` holds it to the
 * greater). It builds no log segment: its log ships once its parts are recorded and let go,
 * under its own hold (`SHIP_UNITS`).
 */
export const BURST_PEAK_BYTES = (BURST_CUT_BYTES + BURST_PAGE_BYTES
  + ((BURST_CUT_BYTES - BURST_PAGE_BYTES) ** 2) ** 0.5) / 2;

/**
 * A generation's burst (`backup.ts`, the manifest lane), in whole units, at the more of its
 * two peaks: at its cut, its pages and its parts (`BURST_CUT_BYTES`); at its last page's
 * build, the pages before it and the build (`BURST_PAGE_BYTES`). It builds no log segment
 * while it holds its parts. Taken now or not at all, from its first page to its parts'
 * record (inside the alarm a lane never waits on the pool), so every object's burst is
 * inside the pool. The memory leg holds every checkpoint within them (rows at `IMAGE_MAX`,
 * one character past U+00FF, on its first burst's pages, the last of them crossing its
 * gather, each page sampled encoded; then a burst of the smallest lines, an owner's write
 * during a part's put leaving a full segment of those rows to ship once its parts are let
 * go).
 */
export const BURST_UNITS = (BURST_PEAK_BYTES + UNIT_BYTES - 1
  - ((BURST_PEAK_BYTES + UNIT_BYTES - 1) % UNIT_BYTES)) / UNIT_BYTES;  // rounded up

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

/**
 * The owner isolate's one pool and one budget of the relay's tails, held at module scope:
 * one of the platform's pinned exemptions from the no-module-state rule
 * (`guards/module-state.test.ts`), since the pool is the isolate's, shared by every object
 * and request in it. It holds a count of units in use, the calls waiting and which objects'
 * tails share the budget, never an owner's data.
 */
const ISOLATE: Isolate = { pool: null, tails: null };

/** The isolate's pool, at the owner Worker's capacity and reserve. */
export function ownerSlots(): SlotPool {
  return isolatePool(ISOLATE, OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, OWNER_WAITERS,
                     OWNER_SMALL_WAITERS);
}

/** The isolate's one budget of the relay's tails, of `units`, made on first use. */
export function ownerTails(units: number): SharedBudget {
  return isolateTails(ISOLATE, units);
}
