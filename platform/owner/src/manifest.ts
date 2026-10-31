/**
 * What the owner's file manifest takes in its object, in bytes: the one measure the
 * manifest quota (owner policy's `max_manifest_bytes`) and each outside client's share of
 * it are held in (plan §6; `files.ts`, migration 10).
 *
 * Every manifest row is charged what SQLite lays it out in, in its 4,096-byte pages: each
 * b-tree record the row makes (the row itself, and each index entry it adds), its text
 * counted in bytes, plus a fixed part for its header, its numbers and its cell. A record
 * takes its share of a page shared by as many such records as fit; past the most a page
 * keeps in place (1,002 bytes in an index or a `WITHOUT ROWID` table, 4,061 in a rowid
 * table), 489 bytes stay in place and the rest takes whole overflow pages of 4,092 bytes.
 * So a 4,096-byte path, which a stored path may be, is charged its three copies' overflow
 * pages (about 14 KiB a new file), and a short one what its rows take; a test measures the
 * object's own database against the charge. The rows charged, by triggers only: each file
 * entry, history row, content (its inline bytes and its chunk list, whose length the
 * content's size gives), chunk, temporary directory, share row and scope reference
 * (`MANIFEST_TABLES`).
 *
 * The owner-wide total counts each stored row once: the real storage. Each scope (an
 * outside client, all clients together, the owner's own writers) is charged its own
 * rows, and once for each content or chunk its own rows reference, deduplicated within
 * the scope and never across scopes, so no scope keeps bytes alive at another's cost. A
 * chunk no scope references any more is garbage until it is deleted, charged to the scope
 * that let it go last.
 *
 * This module imports nothing, so the owner-bound guard reads the same charge the schema
 * keeps.
 */

/** A chunk's most bytes: the files Worker's `CHUNK_BYTES` (a test ties the two). */
export const CHUNK_BYTES = 4 * 1024 * 1024;
/** SQLite's page in the object's database. */
export const PAGE_BYTES = 4096;
/**
 * The room a page is counted as having for its cells: three quarters of its 4,088, since
 * a b-tree's pages are seldom full (a page split leaves two half full); at least one
 * record a page, whatever its size.
 */
export const PAGE_ROOM = 3066;
/** A cell's own bytes past its payload: its pointer and its size. */
const CELL = 4;
/** The most of a record an index (or `WITHOUT ROWID`) page keeps in place. */
export const INDEX_KEPT = 1002;
/** The most of a record a rowid table's page keeps in place. */
export const TABLE_KEPT = 4061;
/** What stays in place of a record that spills, and what one overflow page holds. */
const SPILL_KEPT = 489;
const OVERFLOW = 4092;
/** A record's header and numbers, at most, past its text. */
export const RECORD_FIXED = 48;

/** The bytes a record of `payload` bytes takes, in a b-tree that keeps `kept` in place. */
export function recordBytes(payload: number, kept: number): number {
  const local = payload <= kept ? payload : SPILL_KEPT;
  return Math.floor(PAGE_BYTES / Math.max(1, Math.floor(PAGE_ROOM / (local + CELL))))
    + PAGE_BYTES * Math.ceil((payload - local) / OVERFLOW);
}

/** `recordBytes` in SQL, of an integer expression of text bytes (`RECORD_FIXED` added). */
export function record(text: string, kept: number): string {
  const payload = `(${text} + ${RECORD_FIXED})`;
  const local = `(CASE WHEN ${payload} <= ${kept} THEN ${payload} ELSE ${SPILL_KEPT} END)`;
  return `(${PAGE_BYTES} / max(1, ${PAGE_ROOM} / (${local} + ${CELL})) + ${PAGE_BYTES} * ` +
    `((${payload} - ${local} + ${OVERFLOW - 1}) / ${OVERFLOW}))`;
}

/** A text column's bytes. */
const bytes = (column: string): string => `length(CAST(${column} AS BLOB))`;

/** A content id's, or a chunk hash's, 64 hex characters. */
const HASH = 64;

/** A file entry: one record, keyed by its parent and name, its content id always counted. */
export const ENTRY_BYTES = (row: string): string =>
  record(`${bytes(`${row}.parent`)} + ${bytes(`${row}.name`)} + ${bytes(`${row}.writer`)} + ` +
         `${bytes(`${row}.kind`)} + ${HASH}`, INDEX_KEPT);

/**
 * A history row: its own record (its op and kind at their longest, its content id always
 * counted, so a redaction leaves its charge), its path's index entry, and its entries in
 * the prune-time and released indexes, both always counted.
 */
export const HISTORY_BYTES = (row: string): string =>
  `(${record(`${bytes(`${row}.path`)} + ${bytes(`${row}.writer`)} + ${HASH} + 11 + 4`,
             TABLE_KEPT)} + ${record(`${bytes(`${row}.path`)} + 9`, INDEX_KEPT)} + ` +
  `2 * ${record("17", INDEX_KEPT)})`;

/** The rows of a content's chunk list, from its size: none for inline content. */
const CHUNK_LIST = (row: string): string =>
  `(CASE WHEN ${row}.inline IS NULL THEN (${row}.size + ${CHUNK_BYTES - 1}) / ${CHUNK_BYTES} ` +
  "ELSE 0 END)";

/** A content's own record, its inline bytes in it. */
export const CONTENT_RECORD_BYTES = (row: string): string =>
  record(`${HASH} + coalesce(length(${row}.inline), 0)`, INDEX_KEPT);
/** One row of a content's chunk list: its content id, index and chunk hash. */
export const CHUNK_LIST_ROW_BYTES = (): string => record(`${HASH} + ${HASH} + 9`, INDEX_KEPT);

/** A content: its record, and each row of its chunk list. */
export const CONTENT_BYTES = (row: string): string =>
  `(${CONTENT_RECORD_BYTES(row)} + ${CHUNK_LIST(row)} * ${CHUNK_LIST_ROW_BYTES()})`;

/**
 * A chunk: its record (its state at its longest, and the scope that last let it go, which
 * its garbage is charged to, at a client's 71 bytes) and its entry in the due index. The
 * same for every chunk, so a change of holder moves no charge.
 */
export const CHUNK_ROW_BYTES = (): string =>
  `(${record(`${HASH} + 9 + 71`, INDEX_KEPT)} + ${record(`${HASH} + 8`, INDEX_KEPT)})`;

/** A temporary directory: its record and its entry in the expiry index. */
export const TEMP_BYTES = (row: string): string =>
  `(${record(`${bytes(`${row}.name`)} + ${bytes(`${row}.writer`)}`, INDEX_KEPT)} + ${
    record(`${bytes(`${row}.name`)} + 8`, INDEX_KEPT)})`;

/** A scope's reference count on one content or chunk: its scope, the id and the count. */
export const SCOPE_REF_BYTES = (row: string): string =>
  record(`${bytes(`${row}.scope`)} + ${HASH} + 9`, INDEX_KEPT);

/** An outside client's writer in the manifest, `client:` and its 64 hex characters. */
export const CLIENT_WRITER = "client:*";
/**
 * A share row's own charge: `recordBytes` of its `client:<id>` key (71 bytes), five
 * counters (9 bytes each at most) and `RECORD_FIXED` (a test ties the two).
 */
export const SHARE_BYTES = 227;
/** The share row every outside client's together is counted in. */
export const CLIENTS = "*";
/** The scope of the owner's own writers (the owner, its agent, the sync lease, the system). */
export const OWNER_SCOPE = "owner";

/**
 * What the database may grow by for a charge of one byte, at worst: the charge counts each
 * record's page at `PAGE_ROOM` of room, and SQLite rebalances a b-tree page only once less
 * than a third of it is used (`balance` runs when its free bytes pass two thirds of the
 * usable 4,088), so a page thinned by deletes may stay a third full, holding a third of
 * what a page is charged for. The owner-bound guard counts the manifest at its quota times
 * this; a test measures a database filled, thinned and filled again against it.
 */
export const PAGE_USABLE = 4088;
export const WORST_FILL = PAGE_ROOM / (PAGE_USABLE / 3);

/**
 * The tables the manifest charge covers, each by the triggers that charge its rows when
 * they are made and take the charge back when they go; a content's chunk list is charged
 * with its content (`file_content_chunks`, by the content's triggers).
 */
export const MANIFEST_TABLES: Readonly<Record<string, readonly [string, string]>> =
  Object.freeze({
    file_entries: Object.freeze(["file_entries_added", "file_entries_removed"] as const),
    file_history: Object.freeze(["file_history_added", "file_history_removed"] as const),
    file_contents: Object.freeze(["file_contents_added", "file_contents_removed"] as const),
    file_content_chunks: Object.freeze(["file_contents_added", "file_contents_removed"] as const),
    file_chunks: Object.freeze(["file_chunks_added", "file_chunks_removed"] as const),
    file_temps: Object.freeze(["file_temps_added", "file_temps_removed"] as const),
    file_shares: Object.freeze(["file_shares_charged", "file_shares_credited"] as const),
    file_scope_contents: Object.freeze(["file_scope_contents_added",
                                        "file_scope_contents_removed"] as const),
    file_scope_chunks: Object.freeze(["file_scope_chunks_added",
                                      "file_scope_chunks_removed"] as const),
  });

/**
 * Every index on a manifest table, each charged in its rows' bytes above: a new one is
 * charged nowhere, so the owner-bound guard and the charge test refuse it until it is.
 */
export const MANIFEST_INDEXES: readonly string[] = Object.freeze([
  "file_chunks_due", "file_history_due", "file_history_paths", "file_history_released",
  "file_temps_due",
]);
