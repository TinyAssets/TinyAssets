/**
 * The owner object's schema, migrated on wake: each migration written out once, as the
 * literal SQL text its first deploy ran (`migrations.json` pins each one's digest).
 *
 * `schema_version` holds one row: how many of `MIGRATIONS` this object has applied. The
 * constructor applies the rest in order inside `blockConcurrencyWhile`, each in its own
 * transaction with its version bump, so a crash mid-way leaves a whole number of migrations
 * applied. A stored version past this code's list stops the object loudly: code older than
 * its data never runs against it.
 *
 * The text is frozen, so this module imports nothing. The bounds it spells are the bounds
 * their TypeScript owners enforce too, each read from the contract's frozen bounds
 * (`contract/src/limits.ts`; three from their own modules). The frozen-bounds guard holds
 * each place the text spells one, with how many times, and each owner module's name or
 * charge it spells, equal to the constant the code reads (`guards/frozen-bounds.test.ts`),
 * so a constant changed for its own reason fails it instead of silently meaning other SQL.
 * A part the text once took from a helper (a trigger's charge, a capture trigger's image)
 * is one string, split where a trigger's statements end.
 *
 * Where a body lives is the placement rule (ADR-035): a collection row's, a history entry's
 * or a message's body of more than `PLACE_BYTES` UTF-8 bytes is never in its row. It is
 * content of the files plane, in R2, which the row names by its `content_id`; the `CHECK`s
 * hold every inline body within `PLACE_BYTES`, and the triggers of part 14 count each
 * reference on the content, so the files plane's quota, garbage collection and erasure
 * cover the placed bytes.
 */
import type { Migration } from "./migrate";

/**
 * The owner's admission row (`admission.ts`): one row, `admitted`, `suspended` for one or
 * both of its reasons, or `erased`, the tombstone the erasure leaves as its last step. Its
 * reasons are a sorted JSON list, empty unless suspended. The erasure makes the table again
 * in the wiped database, so it is spelled once, here.
 */
export const ADMISSION_TABLE = "CREATE TABLE owner_admission (only INTEGER PRIMARY KEY " +
  "CHECK (only = 1), state TEXT NOT NULL CHECK (state IN ('pending', 'admitted', " +
  "'suspended', 'erased')), reasons TEXT NOT NULL CHECK (reasons IN ('[]', " +
  "'[\"abuse\"]', '[\"billing\"]', '[\"abuse\",\"billing\"]')), " +
  // The owner's tier (ADR-034 decision 8), which only the platform sets (`tierSet`).
  "tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'paid')), " +
  "CHECK ((state = 'suspended') = (reasons != '[]')))";

/**
 * The owner's migrations: one baseline (squashed before the first deploy, slice P), every
 * table, index, trigger and seed row the owner's object starts with, its parts in the order
 * they were written, numbered as the sources cite them ("part 12"), then each migration
 * since, in the order it shipped.
 *
 * Append-only: a deployed migration is never edited. A new shape is a new migration at the
 * end, written as its literal SQL text like these (no constant interpolated, no helper
 * called), and pinned by the deploy (`migrations.json`); the migrations guard holds every
 * pinned one to its digest, and the append-only lock guard holds the lock itself to the base
 * branch's (`guards/migrations-append-only.test.ts`). A new migration that spells a bound adds
 * it to the frozen bounds and its line to the frozen-bounds guard.
 */
export const MIGRATIONS: readonly Migration[] = Object.freeze([
  Object.freeze([
    // Part 1: the owner this object serves, and the one revision primitive (S34, ADR-032).
    "CREATE TABLE object_owner (only INTEGER PRIMARY KEY CHECK (only = 1), owner TEXT NOT NULL)",
    "CREATE TABLE collection_rows (" +
      "collection TEXT NOT NULL, row_id TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "seq INTEGER NOT NULL, etag TEXT NOT NULL, writer TEXT NOT NULL, " +
      "private INTEGER NOT NULL CHECK (private IN (0, 1)), " +
      // The body inline, or '' with its content placed (ADR-035); its UTF-8 bytes either way.
      "body TEXT NOT NULL CHECK (length(CAST(body AS BLOB)) <= 2048), " +
      "updated_at REAL NOT NULL, " +
      // Who created the row; a takeover never changes it (a clear by writer matches it).
      "creator TEXT NOT NULL, " +
      // The owner-wide change of its latest write (`collection_clock`, slice M).
      "change INTEGER NOT NULL, " +
      "body_bytes INTEGER NOT NULL, content_id TEXT, filer TEXT NOT NULL CHECK (filer = 'owner' OR (length(filer) = 71 AND filer GLOB 'client:*')), CHECK (CASE WHEN content_id IS NULL THEN body_bytes = length(CAST(body AS BLOB)) ELSE body = '' AND body_bytes > 2048 AND length(content_id) = 64 END), " +
      "PRIMARY KEY (collection, row_id), UNIQUE (collection, seq))",
    // A clear by writer matches either.
    "CREATE INDEX collection_writers ON collection_rows (collection, writer)",
    "CREATE INDEX collection_creators ON collection_rows (collection, creator)",
    // The high-water seq survives a clear, so a reader's cursor never goes back; a
    // collection left with no row gives its seq to `seq_floor` and goes, so the table holds
    // a row for each collection with rows, and no more.
    "CREATE TABLE collection_seqs (collection TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
    "CREATE TABLE collection_history (" +
      "collection TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "op TEXT NOT NULL, body_json TEXT NOT NULL CHECK (length(CAST(body_json AS BLOB)) <= 2048), at REAL NOT NULL, writer TEXT NOT NULL, " +
      // A delete's or clear's entry (slice M's digests): its owner-wide change, and the
      // erased row's writer when the row was private, so only that writer is told.
      "change INTEGER, private_writer TEXT, " +
      "body_bytes INTEGER NOT NULL, content_id TEXT, filer TEXT NOT NULL CHECK (filer = 'owner' OR (length(filer) = 71 AND filer GLOB 'client:*')), CHECK (CASE WHEN content_id IS NULL THEN body_bytes = length(CAST(body_json AS BLOB)) ELSE body_json = '' AND body_bytes > 2048 AND length(content_id) = 64 END), " +
      "CHECK ((change IS NULL) = (op NOT IN ('delete', 'clear'))), " +
      "CHECK (private_writer IS NULL OR change IS NOT NULL), " +
      "PRIMARY KEY (collection, key, revision))",
    // The collections are bounded in what they take, laid out as SQLite lays them, whatever
    // their names: `rows_charge` is every row with its five index entries, each collection's
    // seq row and each writer's share counter, held to `maxRowsCharge` (and an outside writer
    // to its share of it); `charge` is the history's entries, pruned oldest first past
    // `maxHistoryCharge`. Each is charged by the triggers below, a record at a time as
    // `manifest.ts`'s `record` lays it out. `revision_floor` is the
    // newest revision a prune let go, so a key whose entries are all gone goes on past it and
    // never repeats a revision; `seq_floor` the newest seq of a collection that was emptied,
    // so a collection made again goes on past it and no reader's cursor goes back.
    "CREATE TABLE collection_space (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "charge INTEGER NOT NULL CHECK (charge >= 0), " +
      "rows_charge INTEGER NOT NULL CHECK (rows_charge >= 0), " +
      "revision_floor INTEGER NOT NULL, seq_floor INTEGER NOT NULL, " +
      // The newest change of a delete's or clear's entry a prune let go (slice M's feed): a
      // reader behind it is told to read again (`changed`'s `reset`), never left unaware.
      "erasure_floor INTEGER NOT NULL)",
    "INSERT INTO collection_space VALUES (1, 0, 0, 0, 0, 0)",
    "CREATE INDEX collection_history_by_age ON collection_history (at)",
    "CREATE TRIGGER collection_history_added AFTER INSERT ON collection_history BEGIN " +
      "UPDATE collection_space SET charge = charge + ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + length(CAST(NEW.op AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body_json AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + coalesce(length(CAST(NEW.private_writer AS BLOB)), 0) + 45 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + length(CAST(NEW.op AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body_json AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + coalesce(length(CAST(NEW.private_writer AS BLOB)), 0) + 45 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + length(CAST(NEW.op AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body_json AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + coalesce(length(CAST(NEW.private_writer AS BLOB)), 0) + 45 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + length(CAST(NEW.op AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body_json AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + coalesce(length(CAST(NEW.private_writer AS BLOB)), 0) + 45 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + length(CAST(NEW.op AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body_json AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + coalesce(length(CAST(NEW.private_writer AS BLOB)), 0) + 45 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.key AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092)) + CASE WHEN NEW.change IS NULL THEN 0 ELSE (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092)) END); END",
    "CREATE TRIGGER collection_history_removed AFTER DELETE ON collection_history BEGIN " +
      "UPDATE collection_space SET charge = charge - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + length(CAST(OLD.op AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body_json AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + coalesce(length(CAST(OLD.private_writer AS BLOB)), 0) + 45 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + length(CAST(OLD.op AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body_json AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + coalesce(length(CAST(OLD.private_writer AS BLOB)), 0) + 45 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + length(CAST(OLD.op AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body_json AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + coalesce(length(CAST(OLD.private_writer AS BLOB)), 0) + 45 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + length(CAST(OLD.op AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body_json AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + coalesce(length(CAST(OLD.private_writer AS BLOB)), 0) + 45 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + length(CAST(OLD.op AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body_json AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + coalesce(length(CAST(OLD.private_writer AS BLOB)), 0) + 45 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.key AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092)) + CASE WHEN OLD.change IS NULL THEN 0 ELSE (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092)) END); END",
    "CREATE TRIGGER collection_history_kept BEFORE UPDATE ON collection_history " +
      "BEGIN SELECT RAISE(ABORT, 'a history entry is never changed'); END",
    // Each outside writer's share of the rows' charge, and all outside writers' (`*`); only
    // the triggers write it, in the statement that changes a row, and a row at zero goes.
    "CREATE TABLE collection_usage (who TEXT PRIMARY KEY CHECK (who = '*' OR " +
      "(length(who) = 71 AND who GLOB 'client:*')), " +
      "charge INTEGER NOT NULL CHECK (charge >= 0)) WITHOUT ROWID",
    "CREATE TRIGGER collection_rows_added AFTER INSERT ON collection_rows BEGIN " +
      "UPDATE collection_space SET rows_charge = rows_charge + ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "INSERT INTO collection_usage (who, charge) SELECT who, ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))) FROM (SELECT '*' AS who UNION ALL SELECT NEW.filer) WHERE NEW.filer GLOB 'client:*' ON CONFLICT (who) DO UPDATE SET charge = charge + excluded.charge; END",
    "CREATE TRIGGER collection_rows_changed AFTER UPDATE ON collection_rows BEGIN " +
      "UPDATE collection_space SET rows_charge = rows_charge - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "UPDATE collection_usage SET charge = charge - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))) WHERE OLD.filer GLOB 'client:*' AND who IN ('*', OLD.filer); " +
      "UPDATE collection_space SET rows_charge = rows_charge + ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "INSERT INTO collection_usage (who, charge) SELECT who, ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + length(CAST(NEW.etag AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.body AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + coalesce(length(CAST(NEW.content_id AS BLOB)), 0) + length(CAST(NEW.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + length(CAST(NEW.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))) FROM (SELECT '*' AS who UNION ALL SELECT NEW.filer) WHERE NEW.filer GLOB 'client:*' ON CONFLICT (who) DO UPDATE SET charge = charge + excluded.charge; END",
    "CREATE TRIGGER collection_rows_removed AFTER DELETE ON collection_rows BEGIN " +
      "UPDATE collection_space SET rows_charge = rows_charge - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "UPDATE collection_usage SET charge = charge - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + length(CAST(OLD.etag AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.body AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + coalesce(length(CAST(OLD.content_id AS BLOB)), 0) + length(CAST(OLD.filer AS BLOB)) + 63 + 1 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.row_id AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + 18 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 18 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + length(CAST(OLD.creator AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4)) + 4096 * (((18 + 48) - (CASE WHEN (18 + 48) <= 1002 THEN (18 + 48) ELSE 489 END) + 4091) / 4092))) WHERE OLD.filer GLOB 'client:*' AND who IN ('*', OLD.filer); END",
    "CREATE TRIGGER collection_seqs_added AFTER INSERT ON collection_seqs BEGIN " +
      "UPDATE collection_space SET rows_charge = rows_charge + ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) <= 4061 THEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.collection AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092))); END",
    "CREATE TRIGGER collection_seqs_removed AFTER DELETE ON collection_seqs BEGIN " +
      "UPDATE collection_space SET rows_charge = rows_charge - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) <= 4061 THEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.collection AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.collection AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092))); END",
    "CREATE TRIGGER collection_usage_added AFTER INSERT ON collection_usage BEGIN " +
      "UPDATE collection_space SET rows_charge = rows_charge + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.who AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.who AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.who AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.who AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.who AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)); END",
    "CREATE TRIGGER collection_usage_removed AFTER DELETE ON collection_usage BEGIN " +
      "UPDATE collection_space SET rows_charge = rows_charge - (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.who AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.who AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.who AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.who AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.who AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)); END",
    "CREATE TRIGGER collection_usage_emptied AFTER UPDATE OF charge ON collection_usage " +
      "WHEN NEW.charge = 0 BEGIN DELETE FROM collection_usage WHERE who = NEW.who; END",
    // The owner-wide change order (slice M's digests): every write, delete and clear takes
    // the clock's next change for its row or its erase entry, so one cursor covers many
    // collections. It only ever rises.
    "CREATE TABLE collection_clock (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "change INTEGER NOT NULL CHECK (change >= 0))",
    "INSERT INTO collection_clock VALUES (1, 0)",
    "CREATE TRIGGER collection_clock_rises BEFORE UPDATE ON collection_clock " +
      "WHEN NEW.change < OLD.change OR NEW.only != OLD.only " +
      "BEGIN SELECT RAISE(ABORT, 'the collection clock only ever rises'); END",
    "CREATE TRIGGER collection_clock_kept BEFORE DELETE ON collection_clock " +
      "BEGIN SELECT RAISE(ABORT, 'the collection clock only ever rises'); END",
    // A digest reads only what changed past its cursor.
    "CREATE INDEX collection_changes ON collection_rows (change)",
    "CREATE INDEX collection_erasures ON collection_history (change) WHERE change IS NOT NULL",
    // Part 2: the message ledger (S30, S3a, S3b), its bodies, and the W3 wake limits (ledger.ts).
    // The object is its owner, so no row carries an owner column. Every bound the ledger
    // keeps is a counter that only the triggers below write, inside the statement that
    // changes the rows it counts, and every query a door or a lane runs has an index, so
    // nothing scans the whole store.
    "CREATE TABLE outside_agents (" +
      "agent_id TEXT PRIMARY KEY, command_center_id TEXT NOT NULL, " +
      "outside_client TEXT NOT NULL, outside_family TEXT NOT NULL, " +
      "source TEXT NOT NULL CHECK (source IN ('api_key', 'mcp')), source_ref TEXT NOT NULL, " +
      "display_name TEXT NOT NULL, delivery_method TEXT NOT NULL " +
      "CHECK (delivery_method IN ('mcp_event', 'routine_fire', 'webhook', 'a2a', 'pull')), " +
      "recipe_id TEXT, revision INTEGER NOT NULL, disabled_reason TEXT, " +
      "created_at REAL NOT NULL, " +
      // The owner's own settings (W3): set only by the owner's wake-limits setter, never by
      // a registration, and off by default (`WAKE_DEFAULTS`): no hourly cap, no batch window.
      "max_wakes_per_hour INTEGER " +
      "CHECK (max_wakes_per_hour IS NULL OR max_wakes_per_hour >= 1), " +
      "batch_window_s INTEGER NOT NULL DEFAULT 0 CHECK (batch_window_s >= 0), " +
      // The hourly cap's one counter (slice W, ruling M5): GCRA's theoretical arrival time,
      // in seconds; null until a capped agent is first woken.
      "wake_tat REAL, " +
      // Messages this agent sent that the owner has not acked yet: its share of the owner's
      // mailbox.
      "unacked_to_owner INTEGER NOT NULL DEFAULT 0 CHECK (unacked_to_owner >= 0), " +
      "UNIQUE (command_center_id, outside_client, outside_family))",
    "CREATE INDEX outside_identities ON outside_agents (outside_client, outside_family)",
    // The enabled agents by delivery method: who a board or room change rings (slice W).
    "CREATE INDEX outside_ringing ON outside_agents (delivery_method) " +
      "WHERE disabled_reason IS NULL",
    // A registration's id, universe and client never change: its client's grant row counts
    // it (`outside_clients.agents`, moved only on insert and delete), and its universe is
    // where a scoped stamp acts.
    "CREATE TRIGGER outside_agents_stamped BEFORE UPDATE OF agent_id, command_center_id, " +
      "outside_client ON outside_agents WHEN NEW.agent_id IS NOT OLD.agent_id " +
      "OR NEW.command_center_id IS NOT OLD.command_center_id " +
      "OR NEW.outside_client IS NOT OLD.outside_client BEGIN " +
      "SELECT RAISE(ABORT, 'a registration never changes its id, universe or client'); END",
    // AUTOINCREMENT: a seq is never reused once its message is evicted, so no cursor
    // ever points at a different message.
    "CREATE TABLE ledger_messages (" +
      "seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, " +
      "command_center_id TEXT NOT NULL, room_id TEXT NOT NULL, " +
      "sender_kind TEXT NOT NULL CHECK (sender_kind IN ('owner', 'native', 'outside')), " +
      "sender_id TEXT NOT NULL, client_message_id TEXT NOT NULL, body_sha256 TEXT NOT NULL, " +
      "parent_id TEXT, chain_root TEXT NOT NULL, chain_depth INTEGER NOT NULL, " +
      "created_at REAL NOT NULL, " +
      // The body's size as counted, and how many of its recipient rows still hold it.
      "body_bytes INTEGER NOT NULL, unacked INTEGER NOT NULL DEFAULT 0 CHECK (unacked >= 0), " +
      // The outside client's authority the mail was sent under (its client, family, grant
      // generation and sign-in, as JSON), or none for the owner's own: what bounds the run
      // a mail to one of the owner's own agents starts (`ledger.ts`, migration 13).
      "authority TEXT CHECK (authority IS NULL OR (length(authority) <= 240 " +
      "AND authority NOT GLOB '*[^ -~]*')), " +
      // Whether a person sent it (the owner's own call): a run it starts is attended. A
      // run's mail and a client's are not.
      "attended INTEGER NOT NULL DEFAULT 0 CHECK (attended IN (0, 1)), " +
      // The bytes its rows take as export lines, its body's included (slice STORE item 2):
      // what it takes in an archive segment, and what the one number counts it at.
      "line_bytes INTEGER NOT NULL CHECK (line_bytes >= body_bytes), " +
      "UNIQUE (sender_id, client_message_id))",
    // The messages no recipient holds, oldest first: what eviction takes.
    "CREATE INDEX messages_done ON ledger_messages (seq) WHERE unacked = 0",
    // A recipient row carries its message's seq, so a reader's mail is an index range.
    // `unacked` is 1 until the reader acks past it, or its agent is disabled (released).
    // A message has one recipient (a room is one agent's dm), and the key holds it to
    // one: the recipients are bounded by their own count, the messages'.
    "CREATE TABLE ledger_recipients (" +
      "message_id TEXT NOT NULL PRIMARY KEY, agent_id TEXT NOT NULL, seq INTEGER NOT NULL, " +
      "state TEXT NOT NULL CHECK (state IN ('queued', 'held_cap', 'woken', 'read', " +
      "'replied', 'failed', 'unknown')), changed_at REAL NOT NULL, " +
      "unacked INTEGER NOT NULL DEFAULT 1 CHECK (unacked IN (0, 1)))",
    "CREATE INDEX recipients_by_seq ON ledger_recipients (agent_id, seq)",
    "CREATE INDEX recipients_unacked ON ledger_recipients (agent_id, seq) WHERE unacked = 1",
    "CREATE INDEX recipients_by_state ON ledger_recipients (agent_id, state)",
    "CREATE TABLE ledger_receipts (" +
      "seq INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL, kind TEXT NOT NULL " +
      "CHECK (kind IN ('wake_sent', 'wake_accepted', 'read', 'ack', 'reply', " +
      "'rate_limited', 'failed', 'claim', 'complete')), " +
      "ref TEXT NOT NULL, at REAL NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}')",
    "CREATE INDEX receipts_by_ref ON ledger_receipts (kind, ref, agent_id)",
    "CREATE INDEX receipts_by_agent ON ledger_receipts (agent_id)",
    // Each reader's ack cursor, and the mail it holds unacked: its mailbox's counters.
    "CREATE TABLE ledger_readers (" +
      "reader TEXT PRIMARY KEY, through_seq INTEGER NOT NULL DEFAULT 0, acked_at REAL, " +
      "unacked INTEGER NOT NULL DEFAULT 0 CHECK (unacked >= 0))",
    // The object's one schedule: each outside agent's wake, and each revoke of a
    // suppressed agent's callbacks, which the doorbell consumes on the `wakes` lane.
    "CREATE TABLE ledger_wakes (" +
      "wake_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, " +
      "state TEXT NOT NULL " +
      "CHECK (state IN ('pending', 'sending', 'sent', 'unknown', 'rate_limited', 'failed', " +
      "'suppressed')), due REAL NOT NULL, claim_id TEXT, " +
      "attempts INTEGER NOT NULL DEFAULT 0, " +
      // The durable may-have-sent mark (the Python op journal's): set before a byte leaves.
      "marked INTEGER NOT NULL DEFAULT 0 CHECK (marked IN (0, 1)), through_seq INTEGER, " +
      // What a wake says changed (slice W, `wake-kinds.ts`): bits for mail 1, rooms 2 and
      // board 4, and the owner-wide change cursor its rooms and board changes are after.
      // `created_at` is the first change's own time, the event's timestamp.
      "changes INTEGER NOT NULL CHECK (changes BETWEEN 1 AND 7), " +
      "after INTEGER NOT NULL CHECK (after >= 0), " +
      // Moves each time a change folds in, so each subscription is owed the wake again.
      "fold INTEGER NOT NULL DEFAULT 0 CHECK (fold >= 0), " +
      "receipt_json TEXT, created_at REAL NOT NULL, settled_at REAL)",
    "CREATE UNIQUE INDEX one_open_wake ON ledger_wakes (agent_id) " +
      "WHERE state IN ('pending', 'rate_limited')",
    "CREATE INDEX wakes_by_agent ON ledger_wakes (agent_id)",
    // The open rows by due time (what the claim pass reads), and the claims being sent by
    // lease (what the recover pass reads): each pass its own index.
    "CREATE INDEX wakes_open ON ledger_wakes (due) WHERE state IN ('pending', 'rate_limited')",
    "CREATE INDEX wakes_sending ON ledger_wakes (due) WHERE state = 'sending'",
    "CREATE INDEX wakes_settled ON ledger_wakes (settled_at) WHERE settled_at IS NOT NULL",
    // The doorbell's subscriptions (slice W, MCP Events): each an enabled ringing agent's,
    // stored by its client's verified `events/subscribe` or by the owner's pasted token,
    // keyed by the id the server derives (principal, callback URL, event, arguments). The
    // secret and the URL are only inside `blob`, which the gateway sealed and alone opens;
    // `previous_*` is the secret a refresh replaced, signing beside the new one until
    // `previous_until`. `rung` is the wake (`<wake_id>.<fold>`) it was last sent, marked
    // before the byte and cleared by a 429, and `status` its last answer, both its own, so a
    // retry rings only the subscriptions still owed. Every row goes in the transaction that
    // disables, moves, deletes or revokes its agent, and is only an enabled agent's. At most
    // `SUBSCRIPTIONS_PER_AGENT` an agent (a technical ceiling, each enabled agent's share in
    // the owner-bound guard), the next refused `subscription_limit`.
    "CREATE TABLE wake_callbacks (" +
      "subscription_id TEXT PRIMARY KEY CHECK (length(subscription_id) = 36 " +
      "AND subscription_id GLOB 'sub_*' AND substr(subscription_id, 5) NOT GLOB '*[^0-9a-f]*'), " +
      "agent_id TEXT NOT NULL, " +
      "principal TEXT NOT NULL CHECK (principal = 'owner' OR (length(principal) = 64 " +
      "AND principal NOT GLOB '*[^0-9a-f]*')), " +
      "kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 64 AND kind NOT GLOB '*[^a-z0-9_]*'), " +
      "board INTEGER NOT NULL CHECK (board IN (0, 1)), " +
      "rooms TEXT NOT NULL CHECK (json_valid(rooms) AND length(rooms) <= 1024), " +
      // Sealed and encoded by the gateway (base64url): visible ASCII, a byte a character.
      "blob TEXT NOT NULL CHECK (length(blob) BETWEEN 1 AND 8192 " +
      "AND blob NOT GLOB '*[^!-~]*'), " +
      "kid TEXT NOT NULL CHECK (length(kid) BETWEEN 1 AND 32), " +
      "version INTEGER NOT NULL CHECK (version >= 1), " +
      "previous_blob TEXT CHECK (length(previous_blob) <= 8192 " +
      "AND previous_blob NOT GLOB '*[^!-~]*'), " +
      "previous_version INTEGER, previous_until REAL, " +
      "rung TEXT CHECK (length(rung) BETWEEN 1 AND 64), " +
      "status INTEGER CHECK (status BETWEEN 100 AND 599), " +
      "expires_at REAL, created_at REAL NOT NULL, refreshed_at REAL NOT NULL, " +
      "CHECK ((previous_blob IS NULL) = (previous_version IS NULL) " +
      "AND (previous_blob IS NULL) = (previous_until IS NULL))) WITHOUT ROWID",
    "CREATE INDEX wake_callbacks_by_agent ON wake_callbacks (agent_id)",
    // A refresh (the same id) is an update, never counted again.
    "CREATE TRIGGER wake_callbacks_full BEFORE INSERT ON wake_callbacks " +
      "WHEN NOT EXISTS (SELECT 1 FROM wake_callbacks WHERE subscription_id = " +
      "NEW.subscription_id) AND (SELECT COUNT(*) FROM wake_callbacks INDEXED BY " +
      "wake_callbacks_by_agent WHERE agent_id = NEW.agent_id) >= 16 " +
      "BEGIN SELECT RAISE(ABORT, 'subscription_limit: 16 doorbell " +
      "subscriptions an agent; end one first'); END",
    "CREATE TRIGGER wake_callbacks_enabled BEFORE INSERT ON wake_callbacks " +
      "WHEN NOT EXISTS (SELECT 1 FROM outside_agents WHERE agent_id = NEW.agent_id " +
      "AND disabled_reason IS NULL) BEGIN SELECT RAISE(ABORT, " +
      "'a doorbell subscription is an enabled agent''s'); END",
    // A message's body, inline or placed past `PLACE_BYTES` (ADR-035); its bytes are its
    // message's `body_bytes`.
    "CREATE TABLE message_bodies (" +
      "message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, " +
      "body TEXT NOT NULL CHECK (length(CAST(body AS BLOB)) <= 2048), " +
      "created_at REAL NOT NULL, body_bytes INTEGER NOT NULL CHECK (body_bytes >= 1), " +
      // Its message's lines' bytes past the body (`line_bytes` less `body_bytes`), counted in
      // the one number with the body, in the writer's files scope (slice STORE item 2, D2).
      "content_id TEXT, filer TEXT NOT NULL CHECK (filer = 'owner' OR (length(filer) = 71 AND filer GLOB 'client:*')), meta_bytes INTEGER NOT NULL CHECK (meta_bytes >= 0), " +
      "CHECK (CASE WHEN content_id IS NULL THEN body_bytes = length(CAST(body AS BLOB)) ELSE body = '' AND body_bytes > 2048 AND length(content_id) = 64 END))",
    // The owner-wide counters the ledger's bounds read.
    "CREATE TABLE ledger_usage (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "agents INTEGER NOT NULL, enabled_agents INTEGER NOT NULL, messages INTEGER NOT NULL, " +
      "settled_wakes INTEGER NOT NULL, " +
      // Messages some recipient still holds, the acked ones' lines' bytes (what the archive
      // lane may move), and the last seq given, which a message's id carries.
      "unacked INTEGER NOT NULL DEFAULT 0 CHECK (unacked >= 0), " +
      "acked_bytes INTEGER NOT NULL DEFAULT 0 CHECK (acked_bytes >= 0), " +
      "last_seq INTEGER NOT NULL DEFAULT 0, " +
      // Whether the archive's last move was refused for the storage figure (a post at the
      // hot ceiling is then `storage_full`, never a busy that cannot clear).
      "archive_blocked INTEGER NOT NULL DEFAULT 0 CHECK (archive_blocked IN (0, 1)), " +
      // The archive lane's passes begun and not ended well, and the last one's error (null
      // when it stopped before it could say, as an isolate killed for memory does): counted
      // as a pass begins, so the owner is told after three however they ended (review N3).
      "archive_fails INTEGER NOT NULL DEFAULT 0 CHECK (archive_fails >= 0), " +
      "archive_error TEXT CHECK (archive_error IS NULL OR length(archive_error) <= 200))",
    "INSERT INTO ledger_usage (only, agents, enabled_agents, messages, settled_wakes) " +
      "VALUES (1, 0, 0, 0, 0)",
    "CREATE TRIGGER outside_agents_added AFTER INSERT ON outside_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents + 1, " +
      "enabled_agents = enabled_agents + (NEW.disabled_reason IS NULL); END",
    "CREATE TRIGGER outside_agents_enabled AFTER UPDATE OF disabled_reason ON outside_agents " +
      "WHEN (OLD.disabled_reason IS NULL) != (NEW.disabled_reason IS NULL) BEGIN " +
      "UPDATE ledger_usage SET enabled_agents = enabled_agents + " +
      "CASE WHEN NEW.disabled_reason IS NULL THEN 1 ELSE -1 END; END",
    "CREATE TRIGGER outside_agents_removed AFTER DELETE ON outside_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents - 1, " +
      "enabled_agents = enabled_agents - (OLD.disabled_reason IS NULL); END",
    "CREATE TRIGGER ledger_messages_added AFTER INSERT ON ledger_messages BEGIN " +
      "UPDATE ledger_usage SET messages = messages + 1, unacked = unacked + (NEW.unacked > 0), " +
      "acked_bytes = acked_bytes + CASE WHEN NEW.unacked = 0 THEN NEW.line_bytes ELSE 0 END; " +
      "END",
    "CREATE TRIGGER ledger_messages_removed AFTER DELETE ON ledger_messages BEGIN " +
      "UPDATE ledger_usage SET messages = messages - 1, unacked = unacked - (OLD.unacked > 0), " +
      "acked_bytes = acked_bytes - CASE WHEN OLD.unacked = 0 THEN OLD.line_bytes ELSE 0 END; " +
      "END",
    "CREATE TRIGGER ledger_messages_held AFTER UPDATE OF unacked ON ledger_messages " +
      "WHEN (OLD.unacked = 0) != (NEW.unacked = 0) BEGIN UPDATE ledger_usage SET " +
      "unacked = unacked + CASE WHEN NEW.unacked = 0 THEN -1 ELSE 1 END, " +
      "acked_bytes = acked_bytes + CASE WHEN NEW.unacked = 0 THEN NEW.line_bytes " +
      "ELSE -NEW.line_bytes END; END",
    // A recipient row holds its message until it is acked or released: the message's
    // `unacked` and the reader's mailbox counters move with it. Its bytes are read from
    // its message, so a recipient row goes before its message (a NULL fails loudly).
    "CREATE TRIGGER ledger_recipient_added AFTER INSERT ON ledger_recipients " +
      "WHEN NEW.unacked = 1 BEGIN " +
      "UPDATE ledger_messages SET unacked = unacked + 1 WHERE message_id = NEW.message_id; " +
      "INSERT INTO ledger_readers (reader, unacked) VALUES (NEW.agent_id, 1) " +
      "ON CONFLICT (reader) DO UPDATE SET unacked = unacked + 1; END",
    "CREATE TRIGGER ledger_recipient_held BEFORE UPDATE OF unacked ON ledger_recipients " +
      "WHEN OLD.unacked = 0 AND NEW.unacked = 1 BEGIN " +
      "SELECT RAISE(ABORT, 'an acked recipient never holds its message again'); END",
    "CREATE TRIGGER ledger_recipient_released AFTER UPDATE OF unacked ON ledger_recipients " +
      "WHEN OLD.unacked = 1 AND NEW.unacked = 0 BEGIN " +
      "UPDATE ledger_messages SET unacked = unacked - 1 WHERE message_id = NEW.message_id; " +
      "UPDATE ledger_readers SET unacked = unacked - 1 WHERE reader = NEW.agent_id; END",
    "CREATE TRIGGER ledger_recipient_removed AFTER DELETE ON ledger_recipients " +
      "WHEN OLD.unacked = 1 BEGIN " +
      "UPDATE ledger_readers SET unacked = unacked - 1 WHERE reader = OLD.agent_id; " +
      "UPDATE ledger_messages SET unacked = unacked - 1 WHERE message_id = OLD.message_id; END",
    // An outside agent's message in its own dm room goes to the owner; while any recipient
    // holds it, it counts against its sender's share of the owner's mailbox. Its mail in a
    // room with one of the owner's own agents goes to that agent, and counts in its mailbox.
    "CREATE TRIGGER ledger_outside_held AFTER UPDATE OF unacked ON ledger_messages " +
      "WHEN NEW.sender_kind = 'outside' AND NEW.room_id = 'dm:' || NEW.sender_id " +
      "AND (OLD.unacked = 0) != (NEW.unacked = 0) BEGIN " +
      "UPDATE outside_agents SET " +
      "unacked_to_owner = unacked_to_owner + CASE WHEN NEW.unacked = 0 THEN -1 ELSE 1 END " +
      "WHERE agent_id = NEW.sender_id; END",
    "CREATE TRIGGER ledger_outside_removed AFTER DELETE ON ledger_messages " +
      "WHEN OLD.sender_kind = 'outside' AND OLD.room_id = 'dm:' || OLD.sender_id " +
      "AND OLD.unacked > 0 BEGIN " +
      "UPDATE outside_agents SET unacked_to_owner = unacked_to_owner - 1 " +
      "WHERE agent_id = OLD.sender_id; END",
    "CREATE TRIGGER ledger_wakes_added AFTER INSERT ON ledger_wakes " +
      "WHEN NEW.settled_at IS NOT NULL BEGIN " +
      "UPDATE ledger_usage SET settled_wakes = settled_wakes + 1; END",
    "CREATE TRIGGER ledger_wakes_settled AFTER UPDATE OF settled_at ON ledger_wakes " +
      "WHEN (OLD.settled_at IS NULL) != (NEW.settled_at IS NULL) BEGIN " +
      "UPDATE ledger_usage SET settled_wakes = settled_wakes + " +
      "CASE WHEN NEW.settled_at IS NULL THEN -1 ELSE 1 END; END",
    "CREATE TRIGGER ledger_wakes_removed AFTER DELETE ON ledger_wakes " +
      "WHEN OLD.settled_at IS NOT NULL BEGIN " +
      "UPDATE ledger_usage SET settled_wakes = settled_wakes - 1; END",
    // Part 3: owner policy, outside-client grants and the owner's protected sessions (N10,
    // P0-3; tasks 3.5): policy.ts, grants.ts, sessions.ts. Each bound reads `policy_usage`,
    // which only the triggers below write, and every query has an index.
    // A session row holds the hash of the cookie the object minted, never the cookie.
    "CREATE TABLE owner_sessions (" +
      "session_hash TEXT PRIMARY KEY CHECK (length(session_hash) = 64), " +
      "created_at REAL NOT NULL, expires_at REAL NOT NULL)",
    "CREATE INDEX sessions_by_expiry ON owner_sessions (expires_at)",
    // The owner's one policy document, its digest, and a generation every change bumps.
    "CREATE TABLE owner_policy (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "generation INTEGER NOT NULL CHECK (generation >= 1), digest TEXT NOT NULL, " +
      "document TEXT NOT NULL, " +
      // The egress generation (part 11), moved only by a write that changes the egress table.
      "egress_generation INTEGER NOT NULL DEFAULT 1 CHECK (egress_generation >= 1))",
    "INSERT INTO owner_policy VALUES (1, 1, 'sha256:b8a7d8f3763ebeadaace95e5a634ffa3954f88b9a75061b061e21e65222d151c', '{\"egress\":{\"destinations\":[],\"mode\":\"open\"},\"entries\":[],\"spend\":{\"ask_above\":null,\"daily\":null,\"monthly\":null,\"source_ceilings\":{},\"transaction\":null}}', " +
      "1)",
    // Append-only. Each bucket is kept and listed apart, so neither an agent's proposals
    // nor a stranger's refusals can push the owner's own changes out.
    "CREATE TABLE policy_receipts (seq INTEGER PRIMARY KEY AUTOINCREMENT, " +
      "bucket TEXT NOT NULL CHECK (bucket IN ('owner', 'agent', 'refusal')), " +
      "kind TEXT NOT NULL CHECK (kind IN ('write', 'wake_limits', 'outside_grant', " +
      "'proposal', 'refusal')), at REAL NOT NULL, session_id TEXT, old_digest TEXT, " +
      "new_digest TEXT, request_digest TEXT, generation INTEGER NOT NULL, rule TEXT, " +
      "decision TEXT, outcome TEXT, " +
      "CHECK ((kind = 'proposal') = (bucket = 'agent') " +
      "AND (kind = 'refusal') = (bucket = 'refusal')))",
    "CREATE INDEX policy_receipts_by_bucket ON policy_receipts (bucket, seq)",
    "CREATE TRIGGER policy_receipts_kept BEFORE UPDATE ON policy_receipts " +
      "BEGIN SELECT RAISE(ABORT, 'a policy receipt is never changed'); END",
    // `proposer` is who filed it: `owner`, or the outside client's id, whose pending
    // proposals are held to a share of the set (`proposal_shares`).
    "CREATE TABLE policy_proposals (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, " +
      "document TEXT NOT NULL, at REAL NOT NULL, applied_at REAL, proposer TEXT NOT NULL)",
    "CREATE INDEX proposals_by_age ON policy_proposals (at)",
    "CREATE INDEX proposals_applied ON policy_proposals (applied_at) " +
      "WHERE applied_at IS NOT NULL",
    "CREATE TABLE proposal_shares (proposer TEXT PRIMARY KEY, " +
      "pending INTEGER NOT NULL CHECK (pending >= 0))",
    // One row per verified outside client. A row an agent holds (`agents`, kept by the
    // triggers on `outside_agents` below) or stamped work holds (`held`: open scheduled
    // work and pending agent registrations, kept by their triggers) is never deleted: it
    // is what that work is rechecked against. A row the owner set (`owner_set`: a grant
    // change or a revoke) is kept while the grant stands; a revoked one may go only by an
    // act the module makes (an API key the directory deleted, or the owner's own forget in
    // its session), and an MCP revoke's time then becomes the owner-wide fence
    // (`grant_fences`), so no credential signed in before it is ever admitted again. Any
    // other row is exactly what the client's next verified call would record again
    // (`full`, generation 1, never revoked), so it is free: the oldest free rows make room
    // for new clients.
    "CREATE TABLE outside_clients (" +
      "client TEXT PRIMARY KEY CHECK (length(client) = 64), " +
      "source TEXT NOT NULL CHECK (source IN ('mcp', 'api_key')), " +
      "mode TEXT NOT NULL CHECK (mode IN ('full', 'scoped', 'revoked')), " +
      // A grant's scopes are counted in bytes (`GRANT_LIMITS.maxScopesBytes`, which the
      // owner-bound guard holds equal), not as so many scopes of so many characters each.
      "scopes TEXT NOT NULL CHECK (length(CAST(scopes AS BLOB)) <= 65536), " +
      "generation INTEGER NOT NULL CHECK (generation >= 1), " +
      "revoked_at REAL, name TEXT NOT NULL, first_seen REAL NOT NULL, " +
      "last_seen REAL NOT NULL, " +
      "owner_set INTEGER NOT NULL DEFAULT 0 CHECK (owner_set IN (0, 1)), " +
      "agents INTEGER NOT NULL DEFAULT 0 CHECK (agents >= 0), " +
      "held INTEGER NOT NULL DEFAULT 0 CHECK (held >= 0), " +
      // The tool profile it is served (slice M, `contract/profiles.json`).
      "profile TEXT NOT NULL DEFAULT 'custom' CHECK (profile IN ('directory', 'custom')), " +
      // A grant's revision apart from its authority (part 9): the owner's concurrency token,
      // moved by every set and revoke and named by the owner's set; `generation` moves only
      // when the grant's mode or scopes do, and is what every stamp is checked against, so a
      // rename keeps the client's stamps. A free row is never set, so it stays at 1.
      "revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1), " +
      "CHECK (owner_set = 1 OR (source = 'mcp' AND mode = 'full' AND generation = 1 " +
      "AND revoked_at IS NULL AND scopes = '[]' AND name = '' AND profile = 'custom')))",
    "CREATE INDEX outside_clients_listed ON outside_clients (first_seen, client)",
    "CREATE INDEX outside_clients_free ON outside_clients (last_seen) " +
      "WHERE owner_set = 0 AND agents = 0 AND held = 0",
    "CREATE TRIGGER outside_clients_kept BEFORE DELETE ON outside_clients " +
      "WHEN OLD.agents > 0 OR OLD.held > 0 OR (OLD.owner_set = 1 AND OLD.mode != 'revoked') " +
      "BEGIN SELECT RAISE(ABORT, 'an outside client grant the owner set or an agent holds is " +
      "never deleted while it stands or holds'); END",
    // The latest MCP revoke ever forgotten: an MCP client with no row is admitted only
    // from a sign-in after it. Every path that drops a revoked MCP row moves it, and it
    // only ever rises: lowering it, or dropping its row, is refused.
    "CREATE TABLE grant_fences (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "mcp_through REAL NOT NULL CHECK (mcp_through >= 0))",
    "INSERT INTO grant_fences VALUES (1, 0)",
    "CREATE TRIGGER grant_fences_rise BEFORE UPDATE ON grant_fences " +
      "WHEN NEW.mcp_through < OLD.mcp_through OR NEW.only != OLD.only " +
      "BEGIN SELECT RAISE(ABORT, 'the revoke fence only ever rises'); END",
    "CREATE TRIGGER grant_fences_kept BEFORE DELETE ON grant_fences " +
      "BEGIN SELECT RAISE(ABORT, 'the revoke fence only ever rises'); END",
    "CREATE TRIGGER outside_clients_forgotten AFTER DELETE ON outside_clients " +
      "WHEN OLD.source = 'mcp' AND OLD.revoked_at IS NOT NULL BEGIN " +
      "UPDATE grant_fences SET mcp_through = MAX(mcp_through, OLD.revoked_at); END",
    "CREATE TABLE policy_usage (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "sessions INTEGER NOT NULL, owner_clients INTEGER NOT NULL, " +
      "free_clients INTEGER NOT NULL, " +
      "pending_proposals INTEGER NOT NULL, applied_proposals INTEGER NOT NULL, " +
      "owner_receipts INTEGER NOT NULL, agent_receipts INTEGER NOT NULL, " +
      "refusal_receipts INTEGER NOT NULL, pending_cards INTEGER NOT NULL DEFAULT 0)",
    "INSERT INTO policy_usage (only, sessions, owner_clients, free_clients, " +
      "pending_proposals, applied_proposals, owner_receipts, agent_receipts, " +
      "refusal_receipts) VALUES (1, 0, 0, 0, 0, 0, 0, 0, 0)",
    // An MCP client's held registration (slice 3's review L2): filed through the client's
    // own verified call, so the object captured its origin, and applied by the owner in
    // its session by card id. One pending card per outside identity; it holds its client's
    // grant row and carries the authority it was filed under.
    "CREATE TABLE agent_cards (card_id TEXT PRIMARY KEY CHECK (length(card_id) = 37), " +
      "client TEXT NOT NULL CHECK (length(client) = 64), " +
      "family TEXT NOT NULL CHECK (length(family) = 64), source_ref TEXT NOT NULL, " +
      "generation INTEGER NOT NULL, authenticated_at REAL, " +
      "request TEXT NOT NULL CHECK (length(request) <= 4096), digest TEXT NOT NULL, " +
      "filed_at REAL NOT NULL, UNIQUE (client, family))",
    "CREATE INDEX agent_cards_by_age ON agent_cards (filed_at)",
    "CREATE TRIGGER owner_sessions_added AFTER INSERT ON owner_sessions BEGIN " +
      "UPDATE policy_usage SET sessions = sessions + 1; END",
    "CREATE TRIGGER owner_sessions_removed AFTER DELETE ON owner_sessions BEGIN " +
      "UPDATE policy_usage SET sessions = sessions - 1; END",
    "CREATE TRIGGER outside_clients_added AFTER INSERT ON outside_clients BEGIN " +
      "UPDATE policy_usage SET owner_clients = owner_clients + (NEW.owner_set = 1), free_clients = free_clients + (NEW.owner_set = 0 AND NEW.agents = 0 AND NEW.held = 0); END",
    "CREATE TRIGGER outside_clients_changed AFTER UPDATE OF owner_set, agents, held " +
      "ON outside_clients BEGIN " +
      "UPDATE policy_usage SET owner_clients = owner_clients - (OLD.owner_set = 1), free_clients = free_clients - (OLD.owner_set = 0 AND OLD.agents = 0 AND OLD.held = 0); " +
      "UPDATE policy_usage SET owner_clients = owner_clients + (NEW.owner_set = 1), free_clients = free_clients + (NEW.owner_set = 0 AND NEW.agents = 0 AND NEW.held = 0); END",
    "CREATE TRIGGER outside_clients_removed AFTER DELETE ON outside_clients BEGIN " +
      "UPDATE policy_usage SET owner_clients = owner_clients - (OLD.owner_set = 1), free_clients = free_clients - (OLD.owner_set = 0 AND OLD.agents = 0 AND OLD.held = 0); END",
    // A client recorded after its agents or held cards were (as a direct store change
    // could leave it) still counts them.
    "CREATE TRIGGER outside_clients_held AFTER INSERT ON outside_clients BEGIN " +
      "UPDATE outside_clients SET agents = (SELECT COUNT(*) FROM outside_agents " +
      "WHERE outside_client = NEW.client), held = (SELECT COUNT(*) FROM " +
      "agent_cards WHERE client = NEW.client) WHERE client = NEW.client; END",
    "CREATE TRIGGER outside_agents_hold AFTER INSERT ON outside_agents BEGIN " +
      "UPDATE outside_clients SET agents = agents + 1 WHERE client = NEW.outside_client; END",
    "CREATE TRIGGER outside_agents_release AFTER DELETE ON outside_agents BEGIN " +
      "UPDATE outside_clients SET agents = agents - 1 WHERE client = OLD.outside_client; END",
    "CREATE TRIGGER policy_proposals_added AFTER INSERT ON policy_proposals BEGIN " +
      "UPDATE policy_usage SET pending_proposals = pending_proposals + " +
      "(NEW.applied_at IS NULL), applied_proposals = applied_proposals + " +
      "(NEW.applied_at IS NOT NULL); " +
      "INSERT INTO proposal_shares (proposer, pending) SELECT NEW.proposer, 1 WHERE NEW.applied_at IS NULL ON CONFLICT (proposer) DO UPDATE SET pending = pending + 1; END",
    "CREATE TRIGGER policy_proposals_applied AFTER UPDATE OF applied_at ON policy_proposals " +
      "WHEN (OLD.applied_at IS NULL) != (NEW.applied_at IS NULL) BEGIN " +
      "UPDATE policy_usage SET " +
      "pending_proposals = pending_proposals + CASE WHEN NEW.applied_at IS NULL " +
      "THEN 1 ELSE -1 END, applied_proposals = applied_proposals + " +
      "CASE WHEN NEW.applied_at IS NULL THEN -1 ELSE 1 END; " +
      "UPDATE proposal_shares SET pending = pending - 1 WHERE proposer = OLD.proposer AND OLD.applied_at IS NULL; " +
      "INSERT INTO proposal_shares (proposer, pending) SELECT NEW.proposer, 1 WHERE NEW.applied_at IS NULL ON CONFLICT (proposer) DO UPDATE SET pending = pending + 1; END",
    "CREATE TRIGGER policy_proposals_removed AFTER DELETE ON policy_proposals BEGIN " +
      "UPDATE policy_usage SET pending_proposals = pending_proposals - " +
      "(OLD.applied_at IS NULL), applied_proposals = applied_proposals - " +
      "(OLD.applied_at IS NOT NULL); " +
      "UPDATE proposal_shares SET pending = pending - 1 WHERE proposer = OLD.proposer AND OLD.applied_at IS NULL; END",
    "CREATE TRIGGER agent_cards_added AFTER INSERT ON agent_cards BEGIN " +
      "UPDATE policy_usage SET pending_cards = pending_cards + 1; " +
      "UPDATE outside_clients SET held = held + 1 WHERE client = NEW.client; END",
    "CREATE TRIGGER agent_cards_removed AFTER DELETE ON agent_cards BEGIN " +
      "UPDATE policy_usage SET pending_cards = pending_cards - 1; " +
      "UPDATE outside_clients SET held = held - 1 WHERE client = OLD.client; END",
    "CREATE TRIGGER agent_cards_kept BEFORE UPDATE ON agent_cards " +
      "BEGIN SELECT RAISE(ABORT, 'a held registration never changes'); END",
    "CREATE TRIGGER proposal_shares_emptied AFTER UPDATE ON proposal_shares " +
      "WHEN NEW.pending = 0 BEGIN DELETE FROM proposal_shares WHERE proposer = NEW.proposer; END",
    "CREATE TRIGGER policy_receipts_added AFTER INSERT ON policy_receipts BEGIN " +
      "UPDATE policy_usage SET owner_receipts = owner_receipts + (NEW.bucket = 'owner'), " +
      "agent_receipts = agent_receipts + (NEW.bucket = 'agent'), " +
      "refusal_receipts = refusal_receipts + (NEW.bucket = 'refusal'); END",
    "CREATE TRIGGER policy_receipts_removed AFTER DELETE ON policy_receipts BEGIN " +
      "UPDATE policy_usage SET owner_receipts = owner_receipts - (OLD.bucket = 'owner'), " +
      "agent_receipts = agent_receipts - (OLD.bucket = 'agent'), " +
      "refusal_receipts = refusal_receipts - (OLD.bucket = 'refusal'); END",
    // Part 4: owner files (plan §6; files.ts): the manifest of paths, versions and history,
    // which chunks make which content, chunk reference counts, upload intents, quotas, the
    // change feed and the sync lease. The bytes are chunks in the files Worker's bucket.
  //
    // Every index and trigger write is a billed row written, so there are only the ones a
    // query or a bound needs. Tables keyed by text are WITHOUT ROWID (the key is the table,
    // no second index), and an entry is keyed by (parent, name), which serves a lookup, a
    // directory listing and a subtree's range alike. Measured (`rowsWritten`, triggers and
    // indexes included, under the default retention): a new small file writes 10 rows, and
    // its overwrite 10 while the path keeps fewer than `keep_versions`, 12 until it keeps
    // `keep_recent`, then 17 in the steady state (each change also moves a row between the
    // retention bands and drops the oldest); a new one-chunk file 22, its overwrite 22, 24,
    // then 35. A delete also releases the path's kept rows, two or three rows written for
    // each: 13 for a path of one version, 72 for one of 20 (78 chunked); a path made again
    // takes them back, so its first write is 44 rows after a gone path of 20 (62 chunked).
    // An entry's own content reference costs a row or two of each figure: the new content's
    // reference, and on an overwrite the old one's release.
  //
    // Counters only triggers write: `file_space`'s entries, history rows, chunk bytes
    // (chunks uploading or live), inline bytes, open intents and the chunks they hold,
    // which the bounds read; a content's `refs` (every entry and every kept history row
    // that names it, in any root: one model, so no path, history or none, can drop
    // content an entry still names) and a chunk's `refs` (contents made of it). A content
    // nothing refers to goes, with its chunk list; a chunk no content refers to and no
    // upload holds becomes garbage, deleted by housekeeping after `GC_GRACE_S` (30 days, the
    // object's point-in-time recovery; SQLite's clock: the schedule compares it with the
    // object's, and a 30-day grace does not mind which), or sooner, oldest first, once the
    // garbage passes the tier's storage (`garbage_bytes`, files.ts `run`). So content any entry or kept row names is never garbage. A history
    // row never changes but for its prune time, its `released` mark and a redaction of
    // its content.
    "CREATE TABLE file_space (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      // The owner's chunk namespace, made on first use (files.ts `ns`), and its data key,
      // wrapped: only the files Worker's keyring opens it (`files/src/datakey.ts`).
      "ns TEXT CHECK (ns IS NULL OR (length(ns) = 32 AND ns NOT GLOB '*[^0-9a-f]*')), " +
      "data_key TEXT CHECK (data_key IS NULL OR (ns IS NOT NULL AND length(data_key) <= 117 " +
      "AND data_key GLOB 'v1.*')), " +
      "seq INTEGER NOT NULL, feed_floor INTEGER NOT NULL, " +
      "entries INTEGER NOT NULL CHECK (entries >= 0), " +
      "history INTEGER NOT NULL CHECK (history >= 0), " +
      "chunk_bytes INTEGER NOT NULL CHECK (chunk_bytes >= 0), " +
      "inline_bytes INTEGER NOT NULL CHECK (inline_bytes >= 0), " +
      "intents INTEGER NOT NULL CHECK (intents >= 0), " +
      "intent_chunks INTEGER NOT NULL CHECK (intent_chunks >= 0), " +
      // The sandbox leases' fencing epoch, only rising: each lease (`sandbox_leases`,
      // migration 12, one an authority) takes the next.
      "epoch INTEGER NOT NULL CHECK (epoch >= 0), " +
      // The retention every kept row is marked by, and how far housekeeping has re-marked
      // the rows for a newer one.
      "retained_keep INTEGER NOT NULL, retained_recent INTEGER NOT NULL, " +
      "retained_days INTEGER NOT NULL, retain_cursor TEXT, " +
      // Everything the manifest takes in the object as SQLite stores it (part 7), and the
      // inline bodies of collection rows, their history and mail (part 15).
      "manifest_bytes INTEGER NOT NULL DEFAULT 0 CHECK (manifest_bytes >= 0), " +
      "body_bytes INTEGER NOT NULL DEFAULT 0 CHECK (body_bytes >= 0), " +
      // The bytes of garbage chunks in their grace: counted in no owner's storage, and held to
      // the owner's storage figure by early collection (files.ts `run`).
      "garbage_bytes INTEGER NOT NULL DEFAULT 0 CHECK (garbage_bytes >= 0))",
    // The manifest starts charged its two share rows (part 7).
    "INSERT INTO file_space VALUES (1, NULL, NULL, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10, 20, 30, NULL, 480, 0, 0)",
    "CREATE TRIGGER file_epoch_rises BEFORE UPDATE OF epoch ON file_space " +
      "WHEN NEW.epoch < OLD.epoch BEGIN SELECT RAISE(ABORT, 'the lease epoch only rises'); END",
    "CREATE TRIGGER file_seq_rises BEFORE UPDATE OF seq ON file_space " +
      "WHEN NEW.seq <= OLD.seq BEGIN SELECT RAISE(ABORT, 'a file version never repeats'); END",
    "CREATE TABLE file_entries (parent TEXT NOT NULL, name TEXT NOT NULL, " +
      "kind TEXT NOT NULL CHECK (kind IN ('file', 'dir')), version INTEGER NOT NULL, " +
      "content_id TEXT, size INTEGER NOT NULL CHECK (size >= 0), " +
      "mode INTEGER NOT NULL CHECK (mode IN (0, 1)), mtime_ms INTEGER NOT NULL, " +
      "writer TEXT NOT NULL, CHECK ((kind = 'file') = (content_id IS NOT NULL)), " +
      "PRIMARY KEY (parent, name)) WITHOUT ROWID",
    "CREATE TABLE file_contents (content_id TEXT PRIMARY KEY, size INTEGER NOT NULL, " +
      "inline BLOB, refs INTEGER NOT NULL DEFAULT 0 CHECK (refs >= 0), " +
      // Inline content is 4,096 bytes at most (a file's `INLINE_BYTES`); chunked content
      // is any size (a placed body's past `PLACE_BYTES`, ADR-035).
      "CHECK (inline IS NULL OR size <= 4096), " +
      "CHECK (inline IS NULL OR length(inline) = size)) WITHOUT ROWID",
    "CREATE TABLE file_content_chunks (content_id TEXT NOT NULL, idx INTEGER NOT NULL, " +
      "sha256 TEXT NOT NULL, PRIMARY KEY (content_id, idx)) WITHOUT ROWID",
    // `stored`: the files Worker accepted a put R2 verified, recorded after it resolved,
    // and nothing else; a commit refuses a chunk not stored, and garbage never stored is
    // sent again, never revived.
    "CREATE TABLE file_chunks (sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64), " +
      "size INTEGER NOT NULL CHECK (size BETWEEN 1 AND 4194304), " +
      "state TEXT NOT NULL CHECK (state IN ('uploading', 'live', 'garbage', 'deleting')), " +
      "stored INTEGER NOT NULL CHECK (stored IN (0, 1)), " +
      "refs INTEGER NOT NULL DEFAULT 0 CHECK (refs >= 0), " +
      "holds INTEGER NOT NULL DEFAULT 0 CHECK (holds >= 0), gc_after REAL, " +
      // The scope that let it go last, which holds its garbage (part 7).
      "holder TEXT NOT NULL DEFAULT 'owner', " +
      "CHECK ((gc_after IS NOT NULL) = (state IN ('garbage', 'deleting'))), " +
      "CHECK (state != 'live' OR stored = 1)) WITHOUT ROWID",
    "CREATE INDEX file_chunks_due ON file_chunks (gc_after) WHERE gc_after IS NOT NULL",
    // An upload of one file: its size and its chunks in order (64 hex each, repeats kept).
    "CREATE TABLE file_intents (intent_id TEXT PRIMARY KEY, size INTEGER NOT NULL, " +
      "chunks TEXT NOT NULL CHECK (length(chunks) % 64 = 0), expires_at REAL NOT NULL, " +
      "writer TEXT NOT NULL DEFAULT 'system', " +
      // The chunks the upload's writer has still to send: its proof of holding their bytes.
      "pending TEXT NOT NULL DEFAULT '' CHECK (length(pending) % 64 = 0)) " +
      "WITHOUT ROWID",
    "CREATE INDEX file_intents_due ON file_intents (expires_at)",
    "CREATE TRIGGER file_intents_added AFTER INSERT ON file_intents BEGIN " +
      "UPDATE file_space SET intents = intents + 1, " +
      "intent_chunks = intent_chunks + length(NEW.chunks) / 64; END",
    "CREATE TRIGGER file_intents_removed AFTER DELETE ON file_intents BEGIN " +
      "UPDATE file_space SET intents = intents - 1, " +
      "intent_chunks = intent_chunks - length(OLD.chunks) / 64; END",
    // The version is the rowid: the feed reads a key range.
    "CREATE TABLE file_history (version INTEGER PRIMARY KEY, path TEXT NOT NULL, " +
      "op TEXT NOT NULL CHECK (op IN ('write', 'mkdir', 'delete', 'rename_from', " +
      "'rename_to', 'restore', 'conflict', 'starter')), " +
      "kind TEXT CHECK (kind IS NULL OR kind IN ('file', 'dir')), content_id TEXT, " +
      "size INTEGER NOT NULL, mode INTEGER NOT NULL, writer TEXT NOT NULL, at REAL NOT NULL, " +
      // `released`: the path is gone, so retention has let the row go (it is always due);
      // a change at the history quota prunes these first (files.ts `#fit`).
      "prune_after REAL, released INTEGER NOT NULL DEFAULT 0 CHECK (released IN (0, 1)), " +
      "CHECK (released = 0 OR prune_after IS NOT NULL))",
    "CREATE INDEX file_history_paths ON file_history (path, version)",
    "CREATE INDEX file_history_due ON file_history (prune_after) WHERE prune_after IS NOT NULL",
    "CREATE INDEX file_history_released ON file_history (prune_after) WHERE released = 1",
    "CREATE TABLE file_temps (name TEXT PRIMARY KEY, expires_at REAL NOT NULL, " +
      "writer TEXT NOT NULL DEFAULT 'system') WITHOUT ROWID",
    "CREATE INDEX file_temps_due ON file_temps (expires_at)",
    "CREATE TRIGGER file_history_kept BEFORE UPDATE OF version, path, op, kind, size, mode, " +
      "writer, at ON file_history BEGIN " +
      "SELECT RAISE(ABORT, 'a file history row is never changed'); END",
    "CREATE TRIGGER file_history_redacted BEFORE UPDATE OF content_id ON file_history " +
      "WHEN NEW.content_id IS NOT NULL BEGIN " +
      "SELECT RAISE(ABORT, 'a file history row is never changed'); END",
    "CREATE TRIGGER file_contents_unreferenced AFTER UPDATE OF refs ON file_contents " +
      "WHEN NEW.refs = 0 BEGIN DELETE FROM file_contents WHERE content_id = NEW.content_id; END",
    "CREATE TRIGGER file_content_chunks_added AFTER INSERT ON file_content_chunks BEGIN " +
      "UPDATE file_chunks SET refs = refs + 1 WHERE sha256 = NEW.sha256; END",
    "CREATE TRIGGER file_content_chunks_removed AFTER DELETE ON file_content_chunks BEGIN " +
      "UPDATE file_chunks SET refs = refs - 1 WHERE sha256 = OLD.sha256; END",
    "CREATE TRIGGER file_chunks_moved AFTER UPDATE OF state ON file_chunks " +
      "WHEN (OLD.state IN ('uploading', 'live')) != (NEW.state IN ('uploading', 'live')) BEGIN " +
      "UPDATE file_space SET chunk_bytes = chunk_bytes + NEW.size * (NEW.state IN ('uploading', 'live')); " +
      "UPDATE file_space SET chunk_bytes = chunk_bytes - OLD.size * (OLD.state IN ('uploading', 'live')); END",
    "CREATE TRIGGER file_chunks_released AFTER UPDATE OF refs, holds ON file_chunks " +
      "WHEN NEW.refs = 0 AND NEW.holds = 0 AND NEW.state IN ('uploading', 'live') BEGIN " +
      "UPDATE file_chunks SET state = 'garbage', " +
      "gc_after = unixepoch('subsec') + 2592000 WHERE sha256 = NEW.sha256; END",
    // Part 5: owner notices (notices.ts): what the owner's work reports that no call fails with
    // (a connection's setup check or provider revoke that failed), the newest kept.
    "CREATE TABLE owner_notices (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, " +
      "type TEXT NOT NULL CHECK (length(type) BETWEEN 1 AND 64), " +
      "detail TEXT NOT NULL CHECK (length(detail) <= 1024))",
    // Part 6: model connections and their use (gateway research §4, §7; connections.ts,
    // usage.ts). A connection's credential is a blob sealed by the gateway, which alone
    // holds the key: the object never holds a plaintext secret. Times are the object's
    // clock in seconds, but an OAuth access expiry and the refresh lease, which are epoch
    // ms as the gateway gives them. A reservation's amount is held against the owner's
    // ceilings by a counter only the triggers below write, so no ceiling ever reads a
    // receipt: `model_spend`, each source's day and month. What is reserved or unknown holds
    // its estimate, what settled holds what it used, and what was never sent or was refused
    // holds nothing. Receipts are history only, pruned without touching a counter.
    "CREATE TABLE connections (id TEXT PRIMARY KEY CHECK (length(id) = 25), " +
      "source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64), " +
      "kind TEXT NOT NULL CHECK (kind IN ('api_key', 'oauth2')), " +
      "display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 128), " +
      "provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 64), " +
      // In bytes (review L5-1): its ceiling's reason is records of bytes (`connections.ts`
      // `MODELS_JSON_MAX`), and a row image's width is bytes.
      "models TEXT NOT NULL DEFAULT '[]' CHECK (length(CAST(models AS BLOB)) <= 655360), " +
      "status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'revoked', 'error')), " +
      "version INTEGER NOT NULL CHECK (version >= 1), expires_at INTEGER, " +
      "lease_id TEXT, lease_until INTEGER, created_at REAL NOT NULL, pending_until REAL, " +
      "revoked_at REAL, revoke_due REAL, revoke_tries INTEGER NOT NULL DEFAULT 0, " +
      "revocable INTEGER NOT NULL CHECK (revocable IN (0, 1)), " +
      // Where its credential may be forwarded (part 11): its sealed base URL, endpoints and
      // pi-ai's metered call, no secret.
      "egress TEXT CHECK (egress IS NULL OR length(CAST(egress AS BLOB)) <= 16384), " +
      "CHECK ((status = 'pending') = (pending_until IS NOT NULL)), " +
      "CHECK (revoke_due IS NULL OR status IN ('revoked', 'error'))) WITHOUT ROWID",
    "CREATE INDEX connections_pending ON connections (pending_until) " +
      "WHERE pending_until IS NOT NULL",
    "CREATE INDEX connections_revokes ON connections (revoke_due) WHERE revoke_due IS NOT NULL",
    // One sealed blob per connection, at its current version.
    "CREATE TABLE connection_secrets (connection_id TEXT NOT NULL, " +
      "version INTEGER NOT NULL, kid TEXT NOT NULL CHECK (length(kid) BETWEEN 1 AND 32), " +
      "blob TEXT NOT NULL CHECK (length(blob) <= 65536), " +
      "PRIMARY KEY (connection_id, version)) WITHOUT ROWID",
    // A connection OAuth flow, keyed by the SHA-256 of its state; its PKCE verifier is
    // held only while it is open.
    "CREATE TABLE connection_flows (flow TEXT PRIMARY KEY CHECK (length(flow) = 64), " +
      "connection_id TEXT NOT NULL, session_id TEXT NOT NULL, verifier TEXT, " +
      "redirect_uri TEXT NOT NULL CHECK (length(redirect_uri) <= 2048), " +
      "created_at REAL NOT NULL, expires_at REAL NOT NULL, " +
      "status TEXT NOT NULL CHECK (status IN ('open', 'used', 'cancelled')), " +
      "CHECK ((status = 'open') = (verifier IS NOT NULL))) WITHOUT ROWID",
    "CREATE INDEX connection_flows_by_expiry ON connection_flows (expires_at)",
    "CREATE TABLE model_reservations (seq INTEGER PRIMARY KEY AUTOINCREMENT, " +
      "id TEXT NOT NULL UNIQUE, run TEXT NOT NULL CHECK (length(run) <= 320), " +
      "connection_id TEXT NOT NULL, source TEXT NOT NULL CHECK (length(source) <= 64), " +
      "model TEXT NOT NULL CHECK (length(model) <= 256), " +
      "state TEXT NOT NULL CHECK (state IN ('reserved', 'dispatched', 'succeeded', " +
      "'failed', 'unknown', 'not_sent', 'refused')), rule TEXT, " +
      "max_output_tokens INTEGER NOT NULL, est_tokens INTEGER NOT NULL, " +
      "est_cost INTEGER NOT NULL, tokens INTEGER, cost INTEGER, " +
      "policy_generation INTEGER NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, " +
      "created_at REAL NOT NULL, settled_at REAL)",
    "CREATE INDEX model_reservations_open ON model_reservations (state) " +
      "WHERE state IN ('reserved', 'dispatched')",
    "CREATE TABLE model_spend (period TEXT NOT NULL, source TEXT NOT NULL, " +
      "cost INTEGER NOT NULL, PRIMARY KEY (period, source)) WITHOUT ROWID",
    "CREATE TRIGGER model_reservations_spent AFTER INSERT ON model_reservations " +
      "WHEN (CASE WHEN NEW.state IN ('reserved', 'dispatched', 'unknown') THEN NEW.est_cost WHEN NEW.state IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) != 0 BEGIN " +
      "INSERT INTO model_spend (period, source, cost) VALUES ('d:' || NEW.day, NEW.source, (CASE WHEN NEW.state IN ('reserved', 'dispatched', 'unknown') THEN NEW.est_cost WHEN NEW.state IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END)), ('m:' || NEW.month, NEW.source, (CASE WHEN NEW.state IN ('reserved', 'dispatched', 'unknown') THEN NEW.est_cost WHEN NEW.state IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END)) ON CONFLICT (period, source) DO UPDATE SET cost = cost + excluded.cost; END",
    "CREATE TRIGGER model_reservations_respent AFTER UPDATE OF state, cost " +
      "ON model_reservations WHEN (CASE WHEN NEW.state IN ('reserved', 'dispatched', 'unknown') THEN NEW.est_cost WHEN NEW.state IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) != (CASE WHEN OLD.state IN ('reserved', 'dispatched', 'unknown') THEN OLD.est_cost WHEN OLD.state IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END) BEGIN " +
      "INSERT INTO model_spend (period, source, cost) VALUES ('d:' || NEW.day, NEW.source, (CASE WHEN NEW.state IN ('reserved', 'dispatched', 'unknown') THEN NEW.est_cost WHEN NEW.state IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) - (CASE WHEN OLD.state IN ('reserved', 'dispatched', 'unknown') THEN OLD.est_cost WHEN OLD.state IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END)), ('m:' || NEW.month, NEW.source, (CASE WHEN NEW.state IN ('reserved', 'dispatched', 'unknown') THEN NEW.est_cost WHEN NEW.state IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) - (CASE WHEN OLD.state IN ('reserved', 'dispatched', 'unknown') THEN OLD.est_cost WHEN OLD.state IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END)) ON CONFLICT (period, source) DO UPDATE SET cost = cost + excluded.cost; END",
    "CREATE TRIGGER model_reservations_kept BEFORE UPDATE OF seq, id, run, connection_id, " +
      "source, " +
      "model, est_tokens, est_cost, policy_generation, day, month, created_at " +
      "ON model_reservations BEGIN SELECT RAISE(ABORT, 'a reservation never changes " +
      "what it reserved'); END",
    // Part 7: the files byte quota and each outside client's share of it (files.ts, manifest.ts),
    // and the bounds on the model reservations (usage.ts). Every manifest row is charged what
    // SQLite stores it in, path bytes and overflow pages included, by triggers only, as the
    // other counters are: once to `file_space`'s `manifest_bytes`, the object's real storage;
    // and to each scope its writer counts in (`file_shares`: an outside client's own,
    // `client:<id>`, all clients' together, `*`, and the owner's own writers', `owner`). A
    // scope is charged its own entries, history rows and temporary directories, and once for
    // each content and chunk its own rows (or its open uploads) reference, counted in
    // `file_scope_contents` and `file_scope_chunks` and charged while the count is above zero,
    // deduplicated within the scope and never across scopes; a chunk no scope references is
    // garbage, charged to its `holder`, the scope that let it go last, until it is deleted or
    // revived. A share row is charged its own bytes, and a client's goes once it holds
    // nothing else; all clients' and the owner's rows are never dropped. Every reference is
    // counted before a content or chunk can go, so a credit reads the row it was charged for.
  //
    // The calls open at once and the spend counters are bounded by count (`CALLS_OPEN`,
    // `SPEND_KEPT`).
    "CREATE TABLE file_shares (who TEXT PRIMARY KEY CHECK (who IN ('*', 'owner') OR (length(who) = 71 AND who GLOB 'client:*')), " +
      "entries INTEGER NOT NULL CHECK (entries >= 0), " +
      "history INTEGER NOT NULL CHECK (history >= 0), " +
      "chunk_bytes INTEGER NOT NULL CHECK (chunk_bytes >= 0), " +
      "inline_bytes INTEGER NOT NULL CHECK (inline_bytes >= 0), " +
      "manifest_bytes INTEGER NOT NULL CHECK (manifest_bytes >= 0), " +
      // The inline bodies (collection rows, their history, mail) its writers keep in the
      // object, under `PLACE_BYTES` each: user content, held to the tier's storage too.
      "body_bytes INTEGER NOT NULL DEFAULT 0 CHECK (body_bytes >= 0)) WITHOUT ROWID",
    "CREATE TABLE file_scope_contents (scope TEXT NOT NULL, content_id TEXT NOT NULL, " +
      "refs INTEGER NOT NULL CHECK (refs >= 0), PRIMARY KEY (scope, content_id)) WITHOUT ROWID",
    "CREATE TABLE file_scope_chunks (scope TEXT NOT NULL, sha256 TEXT NOT NULL, " +
      "refs INTEGER NOT NULL CHECK (refs >= 0), PRIMARY KEY (scope, sha256)) WITHOUT ROWID",
    "INSERT INTO file_shares VALUES ('*', 0, 0, 0, 0, 240, 0), ('owner', 0, 0, 0, 0, 240, 0)",
    "CREATE TRIGGER file_shares_charged AFTER INSERT ON file_shares WHEN NEW.who NOT IN ('*', 'owner') BEGIN " +
      "UPDATE file_shares SET manifest_bytes = " +
      "manifest_bytes + 240 WHERE who IN (NEW.who, '*'); " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes + 240; END",
    "CREATE TRIGGER file_shares_emptied AFTER UPDATE ON file_shares WHEN NEW.who NOT IN ('*', 'owner') AND NEW.entries = 0 AND NEW.history = 0 " +
      "AND NEW.chunk_bytes = 0 " +
      "AND NEW.inline_bytes = 0 AND NEW.body_bytes = 0 AND NEW.manifest_bytes = 240 BEGIN " +
      "DELETE FROM file_shares WHERE who = NEW.who; END",
    "CREATE TRIGGER file_shares_credited AFTER DELETE ON file_shares WHEN OLD.who NOT IN ('*', 'owner') BEGIN " +
      "UPDATE file_shares SET manifest_bytes = " +
      "manifest_bytes - 240 WHERE who = '*'; UPDATE file_space SET " +
      "manifest_bytes = manifest_bytes - 240; END",
    "CREATE TRIGGER file_shares_kept BEFORE DELETE ON file_shares WHEN OLD.who IN ('*', 'owner') BEGIN " +
      "SELECT RAISE(ABORT, 'all clients'' and the " +
      "owner''s share rows are never dropped'); END",
    // A scope's reference: charged its own row, and the content's bytes or the chunk's
    // while it holds any; a content's chunks are its scope's too.
    "CREATE TRIGGER file_scope_contents_added AFTER INSERT ON file_scope_contents BEGIN " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)); " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, (SELECT coalesce(length(c.inline), 0) FROM file_contents c WHERE c.content_id = NEW.content_id), (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (SELECT ((4096 / max(1, 3066 / ((CASE WHEN (64 + coalesce(length(c.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(c.inline), 0) + 48) ELSE 489 END) + 4)) + 4096 * (((64 + coalesce(length(c.inline), 0) + 48) - (CASE WHEN (64 + coalesce(length(c.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(c.inline), 0) + 48) ELSE 489 END) + 4091) / 4092)) + (CASE WHEN c.inline IS NULL THEN (c.size + 4194303) / 4194304 ELSE 0 END) * (4096 / max(1, 3066 / ((CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 64 + 9 + 48) - (CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092))) FROM file_contents c WHERE c.content_id = NEW.content_id), 0 FROM (SELECT NEW.scope AS who) WHERE 1 ON CONFLICT (who) DO UPDATE SET inline_bytes = inline_bytes + excluded.inline_bytes, manifest_bytes = manifest_bytes + excluded.manifest_bytes; " +
      "INSERT INTO file_scope_chunks (scope, sha256, refs) SELECT NEW.scope, x.sha256, " +
      "COUNT(*) FROM file_content_chunks x WHERE x.content_id = NEW.content_id " +
      "GROUP BY x.sha256 ON CONFLICT (scope, sha256) DO UPDATE SET refs = refs + excluded.refs; " +
      "END",
    "CREATE TRIGGER file_scope_contents_emptied AFTER UPDATE OF refs ON file_scope_contents " +
      "WHEN NEW.refs = 0 BEGIN DELETE FROM file_scope_contents WHERE scope = NEW.scope " +
      "AND content_id = NEW.content_id; END",
    "CREATE TRIGGER file_scope_contents_removed AFTER DELETE ON file_scope_contents BEGIN " +
      "UPDATE file_scope_chunks SET refs = refs - (SELECT COUNT(*) FROM file_content_chunks x " +
      "WHERE x.content_id = OLD.content_id AND x.sha256 = file_scope_chunks.sha256) " +
      "WHERE scope = OLD.scope AND sha256 IN (SELECT sha256 FROM file_content_chunks " +
      "WHERE content_id = OLD.content_id); " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes - (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)); " +
      "UPDATE file_shares SET inline_bytes = inline_bytes - ((SELECT coalesce(length(c.inline), 0) FROM file_contents c WHERE c.content_id = OLD.content_id)), manifest_bytes = manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)) + (SELECT ((4096 / max(1, 3066 / ((CASE WHEN (64 + coalesce(length(c.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(c.inline), 0) + 48) ELSE 489 END) + 4)) + 4096 * (((64 + coalesce(length(c.inline), 0) + 48) - (CASE WHEN (64 + coalesce(length(c.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(c.inline), 0) + 48) ELSE 489 END) + 4091) / 4092)) + (CASE WHEN c.inline IS NULL THEN (c.size + 4194303) / 4194304 ELSE 0 END) * (4096 / max(1, 3066 / ((CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 64 + 9 + 48) - (CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092))) FROM file_contents c WHERE c.content_id = OLD.content_id)) WHERE who IN (OLD.scope); END",
    "CREATE TRIGGER file_scope_chunks_added AFTER INSERT ON file_scope_chunks BEGIN " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)); " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, (SELECT k.size FROM file_chunks k WHERE k.sha256 = NEW.sha256), 0, (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(NEW.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)) + ((4096 / max(1, 3066 / ((CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 9 + 71 + 48) - (CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 8 + 48) - (CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4091) / 4092))), 0 FROM (SELECT NEW.scope AS who) WHERE 1 ON CONFLICT (who) DO UPDATE SET chunk_bytes = chunk_bytes + excluded.chunk_bytes, manifest_bytes = manifest_bytes + excluded.manifest_bytes; END",
    "CREATE TRIGGER file_scope_chunks_emptied AFTER UPDATE OF refs ON file_scope_chunks " +
      "WHEN NEW.refs = 0 BEGIN DELETE FROM file_scope_chunks WHERE scope = NEW.scope " +
      "AND sha256 = NEW.sha256; END",
    // The scope that lets a chunk go last holds its garbage.
    "CREATE TRIGGER file_scope_chunks_removed AFTER DELETE ON file_scope_chunks BEGIN " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes - (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)); " +
      "UPDATE file_shares SET chunk_bytes = chunk_bytes - ((SELECT k.size FROM file_chunks k WHERE k.sha256 = OLD.sha256)), manifest_bytes = manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) - (CASE WHEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) <= 1002 THEN (length(CAST(OLD.scope AS BLOB)) + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092)) + ((4096 / max(1, 3066 / ((CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 9 + 71 + 48) - (CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 8 + 48) - (CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4091) / 4092)))) WHERE who IN (OLD.scope); " +
      "UPDATE file_chunks SET holder = OLD.scope WHERE sha256 = OLD.sha256 AND OLD.scope != '*'; END",
    // Each scope's references are counted before the content's own, and taken out first.
    "CREATE TRIGGER file_entries_added AFTER INSERT ON file_entries BEGIN " +
      "UPDATE file_space SET entries = entries + 1, manifest_bytes = manifest_bytes + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092)); " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 1, 0, 0, 0, (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092)), 0 FROM (SELECT '*' AS who WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT NEW.writer WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.writer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET entries = entries + excluded.entries, manifest_bytes = manifest_bytes + excluded.manifest_bytes; " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT NEW.writer WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.writer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; END",
    "CREATE TRIGGER file_entries_removed AFTER DELETE ON file_entries BEGIN " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); " +
      "UPDATE file_space SET entries = entries - 1, manifest_bytes = manifest_bytes - (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092)); " +
      "UPDATE file_shares SET entries = entries - (1), manifest_bytes = manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092))) WHERE who IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    // An overwrite moves the entry's reference to its new content and writer.
    "CREATE TRIGGER file_entries_changed AFTER UPDATE OF content_id, writer ON file_entries " +
      "WHEN NEW.content_id IS NOT OLD.content_id OR NEW.writer IS NOT OLD.writer BEGIN " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT NEW.writer WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.writer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    // An overwrite by another writer moves the entry to it.
    "CREATE TRIGGER file_entries_recharged AFTER UPDATE OF parent, name, kind, writer " +
      "ON file_entries WHEN NEW.writer IS NOT OLD.writer OR NEW.parent IS NOT OLD.parent " +
      "OR NEW.name IS NOT OLD.name OR NEW.kind IS NOT OLD.kind BEGIN " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092)) - (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092)); " +
      "UPDATE file_shares SET entries = entries - (1), manifest_bytes = manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(OLD.parent AS BLOB)) + length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + length(CAST(OLD.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092))) WHERE who IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 1, 0, 0, 0, (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) - (CASE WHEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) <= 1002 THEN (length(CAST(NEW.parent AS BLOB)) + length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + length(CAST(NEW.kind AS BLOB)) + 64 + 48) ELSE 489 END) + 4091) / 4092)), 0 FROM (SELECT '*' AS who WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT NEW.writer WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.writer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET entries = entries + excluded.entries, manifest_bytes = manifest_bytes + excluded.manifest_bytes; END",
    "CREATE TRIGGER file_history_added AFTER INSERT ON file_history BEGIN " +
      "UPDATE file_space SET history = history + 1, manifest_bytes = manifest_bytes + ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) - (CASE WHEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.path AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + 2 * (4096 / max(1, 3066 / ((CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4)) + 4096 * (((17 + 48) - (CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 1, 0, 0, ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) - (CASE WHEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(NEW.path AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.path AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(NEW.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + 2 * (4096 / max(1, 3066 / ((CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4)) + 4096 * (((17 + 48) - (CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4091) / 4092))), 0 FROM (SELECT '*' AS who WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT NEW.writer WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.writer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET history = history + excluded.history, manifest_bytes = manifest_bytes + excluded.manifest_bytes; " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT NEW.writer WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.writer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; END",
    "CREATE TRIGGER file_history_removed AFTER DELETE ON file_history BEGIN " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); " +
      "UPDATE file_space SET history = history - 1, manifest_bytes = manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) - (CASE WHEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.path AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + 2 * (4096 / max(1, 3066 / ((CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4)) + 4096 * (((17 + 48) - (CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "UPDATE file_shares SET history = history - (1), manifest_bytes = manifest_bytes - (((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) - (CASE WHEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) <= 4061 THEN (length(CAST(OLD.path AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 64 + 11 + 4 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.path AS BLOB)) + 9 + 48) - (CASE WHEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) <= 1002 THEN (length(CAST(OLD.path AS BLOB)) + 9 + 48) ELSE 489 END) + 4091) / 4092)) + 2 * (4096 / max(1, 3066 / ((CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4)) + 4096 * (((17 + 48) - (CASE WHEN (17 + 48) <= 1002 THEN (17 + 48) ELSE 489 END) + 4091) / 4092)))) WHERE who IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    // A redaction lets the version's content go, in its writer's scopes first.
    "CREATE TRIGGER file_history_released AFTER UPDATE OF content_id ON file_history BEGIN " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_contents_added AFTER INSERT ON file_contents BEGIN " +
      "UPDATE file_space SET inline_bytes = inline_bytes + coalesce(length(NEW.inline), 0), " +
      "manifest_bytes = manifest_bytes + ((4096 / max(1, 3066 / ((CASE WHEN (64 + coalesce(length(NEW.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(NEW.inline), 0) + 48) ELSE 489 END) + 4)) + 4096 * (((64 + coalesce(length(NEW.inline), 0) + 48) - (CASE WHEN (64 + coalesce(length(NEW.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(NEW.inline), 0) + 48) ELSE 489 END) + 4091) / 4092)) + (CASE WHEN NEW.inline IS NULL THEN (NEW.size + 4194303) / 4194304 ELSE 0 END) * (4096 / max(1, 3066 / ((CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 64 + 9 + 48) - (CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092))); END",
    "CREATE TRIGGER file_contents_removed AFTER DELETE ON file_contents BEGIN " +
      "UPDATE file_space SET inline_bytes = inline_bytes - coalesce(length(OLD.inline), 0), " +
      "manifest_bytes = manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (64 + coalesce(length(OLD.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(OLD.inline), 0) + 48) ELSE 489 END) + 4)) + 4096 * (((64 + coalesce(length(OLD.inline), 0) + 48) - (CASE WHEN (64 + coalesce(length(OLD.inline), 0) + 48) <= 1002 THEN (64 + coalesce(length(OLD.inline), 0) + 48) ELSE 489 END) + 4091) / 4092)) + (CASE WHEN OLD.inline IS NULL THEN (OLD.size + 4194303) / 4194304 ELSE 0 END) * (4096 / max(1, 3066 / ((CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 64 + 9 + 48) - (CASE WHEN (64 + 64 + 9 + 48) <= 1002 THEN (64 + 64 + 9 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "DELETE FROM file_content_chunks WHERE content_id = OLD.content_id; END",
    // A chunk list is exactly the chunks its content's size is cut into, as it is charged.
    "CREATE TRIGGER file_content_chunks_planned BEFORE INSERT ON file_content_chunks " +
      "WHEN NEW.idx < 0 OR NOT (NEW.idx < coalesce((SELECT CASE WHEN c.inline IS NULL THEN (c.size + 4194303) / 4194304 ELSE 0 END FROM file_contents c WHERE c.content_id = NEW.content_id), 0)) BEGIN " +
      "SELECT RAISE(ABORT, 'a chunk list holds the chunks its " +
      "content''s size is cut into'); END",
    "CREATE TRIGGER file_chunks_added AFTER INSERT ON file_chunks BEGIN " +
      "UPDATE file_space SET chunk_bytes = chunk_bytes + NEW.size * (NEW.state IN ('uploading', 'live')); " +
      "UPDATE file_space SET garbage_bytes = garbage_bytes + NEW.size * (NEW.state = 'garbage'); " +
      "UPDATE file_space SET manifest_bytes = " +
      "manifest_bytes + ((4096 / max(1, 3066 / ((CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 9 + 71 + 48) - (CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 8 + 48) - (CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4091) / 4092))); END",
    "CREATE TRIGGER file_chunks_removed AFTER DELETE ON file_chunks BEGIN " +
      "UPDATE file_space SET chunk_bytes = chunk_bytes - OLD.size * (OLD.state IN ('uploading', 'live')); " +
      "UPDATE file_space SET garbage_bytes = garbage_bytes - OLD.size * (OLD.state = 'garbage'); " +
      "UPDATE file_space SET manifest_bytes = " +
      "manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 9 + 71 + 48) - (CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 8 + 48) - (CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "UPDATE file_shares SET manifest_bytes = manifest_bytes - (((4096 / max(1, 3066 / ((CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 9 + 71 + 48) - (CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 8 + 48) - (CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4091) / 4092))) * (OLD.state IN ('garbage', 'deleting'))) WHERE who IN (CASE WHEN OLD.holder GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.holder); END",
    // Garbage is its holder's, from the moment no scope holds it until it is deleted or an
    // upload revives it.
    "CREATE TRIGGER file_chunks_discarded AFTER UPDATE OF state ON file_chunks " +
      "WHEN OLD.state IN ('uploading', 'live') AND NEW.state = 'garbage' BEGIN " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, ((4096 / max(1, 3066 / ((CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 9 + 71 + 48) - (CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 8 + 48) - (CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4091) / 4092))), 0 FROM (SELECT '*' AS who WHERE NEW.holder GLOB 'client:*' UNION ALL SELECT NEW.holder WHERE NEW.holder GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.holder NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET manifest_bytes = manifest_bytes + excluded.manifest_bytes; END",
    "CREATE TRIGGER file_chunks_revived AFTER UPDATE OF state ON file_chunks " +
      "WHEN OLD.state = 'garbage' AND NEW.state IN ('uploading', 'live') BEGIN " +
      "UPDATE file_shares SET manifest_bytes = manifest_bytes - (((4096 / max(1, 3066 / ((CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 9 + 71 + 48) - (CASE WHEN (64 + 9 + 71 + 48) <= 1002 THEN (64 + 9 + 71 + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((64 + 8 + 48) - (CASE WHEN (64 + 8 + 48) <= 1002 THEN (64 + 8 + 48) ELSE 489 END) + 4091) / 4092)))) WHERE who IN (CASE WHEN OLD.holder GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.holder); END",
    "CREATE TRIGGER file_chunks_garbage AFTER UPDATE OF state ON file_chunks " +
      "WHEN (OLD.state = 'garbage') != (NEW.state = 'garbage') BEGIN " +
      "UPDATE file_space SET garbage_bytes = garbage_bytes + NEW.size * (NEW.state = 'garbage'); " +
      "UPDATE file_space SET garbage_bytes = garbage_bytes - OLD.size * (OLD.state = 'garbage'); END",
    "CREATE TRIGGER file_chunks_held BEFORE UPDATE OF holder ON file_chunks " +
      "WHEN NEW.holder IS NOT OLD.holder AND OLD.state IN ('garbage', 'deleting') BEGIN " +
      "SELECT RAISE(ABORT, 'a garbage chunk''s holder never changes'); END",
    "CREATE TRIGGER file_temps_added AFTER INSERT ON file_temps BEGIN " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes + ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) - (CASE WHEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.name AS BLOB)) + 8 + 48) - (CASE WHEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) - (CASE WHEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + length(CAST(NEW.writer AS BLOB)) + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(NEW.name AS BLOB)) + 8 + 48) - (CASE WHEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(NEW.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4091) / 4092))), 0 FROM (SELECT '*' AS who WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT NEW.writer WHERE NEW.writer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.writer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET manifest_bytes = manifest_bytes + excluded.manifest_bytes; END",
    "CREATE TRIGGER file_temps_removed AFTER DELETE ON file_temps BEGIN " +
      "UPDATE file_space SET manifest_bytes = manifest_bytes - ((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) - (CASE WHEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.name AS BLOB)) + 8 + 48) - (CASE WHEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4091) / 4092))); " +
      "UPDATE file_shares SET manifest_bytes = manifest_bytes - (((4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) - (CASE WHEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + length(CAST(OLD.writer AS BLOB)) + 48) ELSE 489 END) + 4091) / 4092)) + (4096 / max(1, 3066 / ((CASE WHEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4)) + 4096 * (((length(CAST(OLD.name AS BLOB)) + 8 + 48) - (CASE WHEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) <= 1002 THEN (length(CAST(OLD.name AS BLOB)) + 8 + 48) ELSE 489 END) + 4091) / 4092)))) WHERE who IN (CASE WHEN OLD.writer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.writer); END",
    // The writer each is charged to never changes.
    "CREATE TRIGGER file_temps_writer_kept BEFORE UPDATE OF writer ON file_temps BEGIN " +
      "SELECT RAISE(ABORT, 'a file row''s writer never changes'); END",
    "CREATE TRIGGER file_intents_writer_kept BEFORE UPDATE OF writer ON file_intents BEGIN " +
      "SELECT RAISE(ABORT, 'a file row''s writer never changes'); END",
    // The calls open at once and the spend counters, each bounded by count; an open call is
    // read by its partial index.
    "CREATE TRIGGER model_reservations_open BEFORE INSERT ON model_reservations " +
      "WHEN NEW.state IN ('reserved', 'dispatched') AND (SELECT COUNT(*) FROM " +
      "model_reservations WHERE state IN ('reserved', 'dispatched')) >= 1000 BEGIN " +
      "SELECT RAISE(ABORT, 'model_calls_full: 1000 model calls are open'); END",
    "CREATE TRIGGER model_spend_kept AFTER INSERT ON model_spend " +
      "WHEN (SELECT COUNT(*) FROM model_spend) > 4300 BEGIN " +
      "SELECT RAISE(ABORT, 'model_spend_full: 4300 spend counters are kept'); END",
    // Part 8: the owner's erasure (erasure.ts; S4-S5): the owner being erased is one row
    // (`owner_erasure`, with the step it reached), the mark every door and lane refuses on.
    "CREATE TABLE owner_erasure (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "started_at REAL NOT NULL, step INTEGER NOT NULL DEFAULT 1 CHECK (step IN (1, 2)), " +
      // The erasure's outside record, confirmed by the doors (`erasure.ts` step 2, slice L).
      "outside INTEGER NOT NULL DEFAULT 0 CHECK (outside IN (0, 1)))",
    // Part 10: the owner's admission (tasks 5, F1): `owner_admission` is the owner's one
    // admission row (`admission.ts`): pending when the object is first made, admitted only by
    // the platform's explicit admit at sign-up (slice L), suspended for billing or abuse by
    // the platform, or, as the last step of the owner's erasure, the erased tombstone, alone
    // in the wiped database.
    ADMISSION_TABLE,
    "INSERT INTO owner_admission (only, state, reasons) VALUES (1, 'pending', '[]')",
    // Part 11: the egress door (tasks 4 G4; egress.ts). The owner's policy row keeps the egress
    // generation, moved only by a write that changes the egress table (each sandbox lease,
    // migration 12, keeps the one its network was last applied under).
    // A connection keeps where its credential may be forwarded (`egress`: its sealed base
    // URL, endpoints and pi-ai's metered call, no secret), from its setup check. Each sandbox
    // call the object admits is an open row until the egress door settles it; a refused one
    // is kept as its receipt. A model call's row is opened uncharged and carries its own
    // charge once the gateway's parse reserved it (`charge`, null to held once, on an open row,
    // then held to settled once, as a model reservation's states), which the triggers alone
    // count: into
    // `model_spend`, its source's day and month, the counters the model reservations move
    // too, and (migration 12) into its run's own requests, tokens and cost on its `runs` row.
    // Each row names the run its path named. The newest `EGRESS_LIMITS.receiptsKept`
    // admitted and `refusalsKept` refused rows are kept, and at most `EGRESS_LIMITS.open` are
    // open at once; a path is visible ASCII, as the door sends it.
    "CREATE TABLE egress_reservations (id TEXT PRIMARY KEY CHECK (length(id) = 32), " +
      "lease_id TEXT CHECK (lease_id IS NULL OR length(lease_id) = 32), epoch INTEGER, " +
      // The run the call came from, as its path named it (`/r/<run>/c/...`).
      "run TEXT NOT NULL CHECK (length(run) = 36), " +
      "token TEXT NOT NULL CHECK (length(token) = 64), " +
      "source TEXT CHECK (source IS NULL OR length(source) BETWEEN 1 AND 64), " +
      "charge TEXT CHECK (charge IS NULL OR charge IN ('held', 'succeeded', 'failed', " +
      "'unknown')), rule TEXT CHECK (rule IS NULL OR length(rule) <= 64), " +
      "max_output_tokens INTEGER, est_tokens INTEGER CHECK (est_tokens IS NULL OR " +
      "est_tokens >= 0), est_cost INTEGER CHECK (est_cost IS NULL OR est_cost >= 0), " +
      "tokens INTEGER CHECK (tokens IS NULL OR tokens >= 0), " +
      "cost INTEGER CHECK (cost IS NULL OR cost >= 0), day TEXT, month TEXT, " +
      "served TEXT CHECK (served IS NULL OR (length(served) BETWEEN 1 AND 256 " +
      "AND served NOT GLOB '*[^!-~]*')), " +
      "connection_id TEXT NOT NULL CHECK (length(connection_id) = 25), version INTEGER, " +
      "method TEXT NOT NULL CHECK (length(method) BETWEEN 1 AND 7), " +
      "path TEXT NOT NULL CHECK (length(path) BETWEEN 1 AND 2048 AND " +
      "path NOT GLOB '*[^ -~]*'), " +
      "generation INTEGER NOT NULL, bytes_hint INTEGER CHECK (bytes_hint IS NULL OR " +
      "bytes_hint >= 0), opened_at REAL NOT NULL, " +
      // Its door's last beat while it streams (`EGRESS_BEAT_S`): past three missed, lost.
      "beat_at REAL, " +
      "state TEXT NOT NULL CHECK (state IN ('open', 'settled', 'refused')), " +
      "code TEXT CHECK (code IS NULL OR length(code) <= 64), " +
      "ended TEXT CHECK (ended IS NULL OR ended IN ('policy', 'lease', 'connection', " +
      "'admission', 'erasure', 'run')), " +
      "outcome TEXT CHECK (outcome IS NULL OR outcome IN ('complete', 'aborted', 'failed', " +
      "'deadline', 'ended', 'lost')), " +
      "status INTEGER CHECK (status IS NULL OR status BETWEEN 100 AND 599), " +
      "bytes INTEGER CHECK (bytes IS NULL OR bytes >= 0), settled_at REAL, " +
      "CHECK ((state = 'refused') = (code IS NOT NULL)), " +
      "CHECK ((state = 'settled') = (outcome IS NOT NULL)), " +
      "CHECK ((state = 'open') = (settled_at IS NULL)), " +
      "CHECK ((charge IS NULL) = (est_cost IS NULL) AND (charge IS NULL) = (est_tokens IS NULL) " +
      "AND (charge IS NULL) = (source IS NULL) AND (charge IS NULL) = (day IS NULL) " +
      "AND (charge IS NULL) = (month IS NULL) " +
      "AND (charge IS NULL) = (max_output_tokens IS NULL)), " +
      "CHECK (charge IS NULL OR (state != 'refused' AND lease_id IS NOT NULL " +
      "AND epoch IS NOT NULL)), " +
      "CHECK ((charge IN ('succeeded', 'failed')) = (cost IS NOT NULL)), " +
      "CHECK ((charge IN ('succeeded', 'failed')) = (tokens IS NOT NULL)))",
    "CREATE INDEX egress_reservations_by_state ON egress_reservations (state, opened_at)",
    "CREATE INDEX egress_reservations_by_time ON egress_reservations (opened_at)",
    "CREATE TRIGGER egress_reservations_charged AFTER UPDATE OF charge ON egress_reservations " +
      "WHEN OLD.charge IS NULL AND NEW.charge IS NOT NULL BEGIN " +
      "INSERT INTO model_spend (period, source, cost) VALUES ('d:' || NEW.day, NEW.source, (CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END)), ('m:' || NEW.month, NEW.source, (CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END)) ON CONFLICT (period, source) DO UPDATE SET cost = cost + excluded.cost; END",
    "CREATE TRIGGER egress_reservations_recharged AFTER UPDATE OF charge, tokens, cost " +
      "ON egress_reservations WHEN NEW.charge IS NOT NULL AND OLD.charge IS NOT NULL " +
      "AND (CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) != (CASE WHEN OLD.charge IN ('held', 'unknown') THEN OLD.est_cost WHEN OLD.charge IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END) BEGIN " +
      "INSERT INTO model_spend (period, source, cost) VALUES ('d:' || NEW.day, NEW.source, (CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) - (CASE WHEN OLD.charge IN ('held', 'unknown') THEN OLD.est_cost WHEN OLD.charge IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END)), ('m:' || NEW.month, NEW.source, (CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) - (CASE WHEN OLD.charge IN ('held', 'unknown') THEN OLD.est_cost WHEN OLD.charge IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END)) ON CONFLICT (period, source) DO UPDATE SET cost = cost + excluded.cost; END",
    "CREATE TRIGGER egress_reservations_kept BEFORE UPDATE OF id, lease_id, epoch, run, token, " +
      "connection_id ON egress_reservations BEGIN SELECT RAISE(ABORT, 'a reservation never " +
      "changes what it reserved'); END",
    "CREATE TRIGGER egress_reservations_charge_kept BEFORE UPDATE OF source, max_output_tokens, " +
      "est_tokens, est_cost, day, month ON egress_reservations WHEN OLD.charge IS NOT NULL " +
      "BEGIN SELECT RAISE(ABORT, 'a reservation never changes what it reserved'); END",
    "CREATE TRIGGER egress_reservations_reserved_once BEFORE UPDATE OF charge " +
      "ON egress_reservations WHEN OLD.charge IS NULL AND NEW.charge IS NOT NULL " +
      "AND (NEW.charge != 'held' OR OLD.state != 'open') " +
      "BEGIN SELECT RAISE(ABORT, 'a call is reserved once, held, while it is open'); END",
    "CREATE TRIGGER egress_reservations_settled_once BEFORE UPDATE OF charge " +
      "ON egress_reservations WHEN OLD.charge IS NOT NULL AND OLD.charge != 'held' " +
      "BEGIN SELECT RAISE(ABORT, 'a charge is settled once'); END",
    // Part 12: runs (ADR-034 decision 4; `runs.ts`). A run is one engine invocation: its row
    // (`runs`) is its receipt, kept past its settle, the newest `RUNS_KEPT` settled ones, with
    // its usage counters, which only the egress triggers below move (a call's charge, held to
    // settled, counts to its run as it counts to its source's day and month). At most
    // `RUNS_CEILING` are live (`queued` or `running`) at once, the most any tier record may
    // set; the tier's own number is admission's (`tier.ts`). A live run names the sandbox
    // lease it runs under and its own last heartbeat (`beat_at`), and holds one run grant
    // (`run_grants`): its initiator's stamp and origin, its scopes, its own connection (none
    // on the vendor's own sign-in) and the SHA-256 of its own secret, deleted at its settle or
    // cancel (its authority ends at once; the runner's end still settles it). The sandbox
    // leases (`sandbox_leases`): one sandbox an authority, the owner's own work's or one
    // outside client's, so runs of different authority never share a VM; each lease its
    // fencing epoch, its expiry (its tokens' too), the SHA-256 of its egress, sync and
    // runner tokens, and the egress generation its network was last applied under; at most
    // `RUNS_CEILING`, since a lease is opened only for a live run and a lapsed one goes. A
    // conversation (`conversations`) keeps its engine and that engine's session id, at most
    // `CONVERSATIONS_KEPT` (the least recently run one with no live run goes first). The
    // `runs` lane's rows (`run_schedules`, at most `SCHEDULES_KEPT`): a launch at a time or a
    // cron recurrence, each with its input. The platform's tier
    // numbers as the binder last handed them (`platform_tiers`, one row), and the owner app's
    // socket tickets (`socket_tickets`, at most `SOCKET_TICKETS`, single-use, each kept as
    // its SHA-256 only). A run that waits for its conversation's live run to settle is a
    // `waiting` row (not live: it holds no slot, grant or lease), its input kept beside it
    // (`run_inputs`) until it starts, at most `QUEUE_ROWS` and `QUEUE_BYTES` of input in all.
    // Each engine's sign-in, as the runner last reported it (`engine_signins`, one row an
    // engine, at most `ENGINE_SIGNINS`): signed in or not, when, and the models it offers;
    // never a credential.
    "CREATE TABLE runs (seq INTEGER PRIMARY KEY AUTOINCREMENT, " +
      "id TEXT NOT NULL UNIQUE CHECK (length(id) = 36), " +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128), " +
      "conversation TEXT NOT NULL CHECK (length(conversation) = 35), " +
      "source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64), " +
      "credential TEXT NOT NULL CHECK (credential IN ('vendor_login_in_sandbox', 'api_key')), " +
      "model TEXT CHECK (model IS NULL OR (length(model) BETWEEN 1 AND 256 " +
      "AND model NOT GLOB '*[^!-~]*')), " +
      "state TEXT NOT NULL CHECK (state IN ('waiting', 'queued', 'running', 'succeeded', " +
      "'failed', 'unknown')), " +
      "initiator TEXT NOT NULL CHECK (initiator IN ('owner', 'client', 'schedule', 'mail')), " +
      "client TEXT CHECK (client IS NULL OR length(client) = 64), " +
      "stamp TEXT CHECK (stamp IS NULL OR length(stamp) <= 1024), " +
      "request TEXT CHECK (request IS NULL OR length(request) = 64), " +
      "unattended INTEGER NOT NULL CHECK (unattended IN (0, 1)), " +
      // The mail it answers, when mail to one of the owner's agents started it (slice ID):
      // the mail is held in the ledger until the run settles.
      "mail TEXT CHECK (mail IS NULL OR length(mail) = 36), " +
      "cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)), " +
      "requests INTEGER NOT NULL DEFAULT 0 CHECK (requests >= 0), " +
      "tokens INTEGER NOT NULL DEFAULT 0 CHECK (tokens >= 0), " +
      "cost INTEGER NOT NULL DEFAULT 0 CHECK (cost >= 0), " +
      "last_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_seq >= 0), " +
      "result TEXT CHECK (result IS NULL OR length(CAST(result AS BLOB)) <= 16384), " +
      "result_cut INTEGER NOT NULL DEFAULT 0 CHECK (result_cut IN (0, 1)), " +
      "error TEXT CHECK (error IS NULL OR length(CAST(error AS BLOB)) <= 1024), " +
      "code TEXT CHECK (code IS NULL OR length(code) BETWEEN 1 AND 64), " +
      "exit_code INTEGER, created_at REAL NOT NULL, started_at REAL, settled_at REAL, " +
      // The sandbox lease a live run runs under, and its last heartbeat.
      "lease_id TEXT CHECK (lease_id IS NULL OR length(lease_id) = 32), epoch INTEGER, " +
      "beat_at REAL, " +
      // The time its sandbox ran between its start and its settle (part 15, `sandbox-time.ts`).
      "sandbox_ms INTEGER CHECK (sandbox_ms IS NULL OR sandbox_ms >= 0), " +
      // A run waiting for a sandbox (slice S: E2B ran as many as its plan allows, or another
      // owner waits ahead): not started again until the platform's line rings the owner.
      "sandbox_wait INTEGER NOT NULL DEFAULT 0 CHECK (sandbox_wait IN (0, 1) AND " +
      "(sandbox_wait = 0 OR state = 'waiting')), " +
      "CHECK ((state IN ('waiting', 'queued', 'running')) = (settled_at IS NULL)), " +
      "CHECK (state NOT IN ('queued', 'running') OR (lease_id IS NOT NULL " +
      "AND epoch IS NOT NULL AND beat_at IS NOT NULL)), " +
      // A client's own run names its client; the owner's names none; a schedule's or the
      // mail's names the client that set it, if one did.
      "CHECK (initiator != 'client' OR client IS NOT NULL), " +
      "CHECK (initiator != 'owner' OR client IS NULL))",
    "CREATE UNIQUE INDEX runs_by_request ON runs (request) WHERE request IS NOT NULL",
    "CREATE INDEX runs_live ON runs (state) WHERE state IN ('queued', 'running')",
    "CREATE INDEX runs_by_conversation ON runs (conversation, seq)",
    "CREATE INDEX runs_waiting ON runs (seq) WHERE state = 'waiting'",
    "CREATE INDEX runs_settled ON runs (settled_at, seq) WHERE settled_at IS NOT NULL",
    "CREATE TRIGGER runs_live_full BEFORE INSERT ON runs WHEN (SELECT COUNT(*) FROM runs " +
      "WHERE state IN ('queued', 'running')) >= 1000 BEGIN SELECT RAISE(ABORT, " +
      "'runs_full: 1000 runs are live'); END",
    "CREATE TRIGGER runs_settled_once BEFORE UPDATE OF state ON runs " +
      "WHEN OLD.state NOT IN ('waiting', 'queued', 'running') BEGIN SELECT RAISE(ABORT, " +
      "'a run is settled once'); END",
    "CREATE TRIGGER runs_kept BEFORE UPDATE OF seq, id, agent, conversation, initiator, " +
      "client, stamp, request, unattended, mail, created_at ON runs BEGIN " +
      "SELECT RAISE(ABORT, 'a run never changes what it was admitted as'); END",
    // A waiting run's source is chosen again as it starts; a started one's never moves.
    "CREATE TRIGGER runs_source_kept BEFORE UPDATE OF source, credential ON runs " +
      "WHEN OLD.state != 'waiting' BEGIN SELECT RAISE(ABORT, 'a run never changes what " +
      "it was admitted as'); END",
    "CREATE TABLE run_inputs (" +
      "run TEXT PRIMARY KEY CHECK (length(run) = 36), " +
      // The input, or '' for a mail's run, whose `mail` is its input's reference (ADR-035).
      "prompt TEXT NOT NULL, bytes INTEGER NOT NULL CHECK (bytes >= 0), " +
      // The client's origin, as its run grant will carry it once the run starts.
      "origin TEXT CHECK (origin IS NULL OR (length(origin) <= 256 " +
      "AND origin NOT GLOB '*[^ -~]*')), " +
      "CHECK (bytes = length(CAST(prompt AS BLOB)))) WITHOUT ROWID",
    "CREATE TRIGGER run_inputs_full BEFORE INSERT ON run_inputs WHEN (SELECT COUNT(*) " +
      "FROM run_inputs) >= 256 OR (SELECT coalesce(SUM(bytes), 0) FROM run_inputs) " +
      "+ NEW.bytes > 6291456 BEGIN SELECT RAISE(ABORT, 'run_inputs_full: " +
      "256 inputs or 6291456 bytes wait already; let a run settle first'); END",
    "CREATE TABLE engine_signins (engine TEXT PRIMARY KEY CHECK (length(engine) BETWEEN 1 " +
      "AND 64), signed_in INTEGER NOT NULL CHECK (signed_in IN (0, 1)), " +
      "models TEXT NOT NULL CHECK (length(models) <= 2048 " +
      "AND models NOT GLOB '*[^ -~]*'), checked_at REAL NOT NULL, changed_at REAL NOT NULL) " +
      "WITHOUT ROWID",
    "CREATE TRIGGER engine_signins_full BEFORE INSERT ON engine_signins WHEN (SELECT " +
      "COUNT(*) FROM engine_signins) >= 64 BEGIN SELECT RAISE(ABORT, " +
      "'engine_signins_full: 64 engines are known'); END",
    "CREATE TABLE sandbox_leases (authority TEXT PRIMARY KEY CHECK (authority = 'owner' " +
      "OR length(authority) = 64), " +
      "lease_id TEXT NOT NULL CHECK (length(lease_id) = 32 AND lease_id NOT GLOB " +
      "'*[^0-9a-f]*'), epoch INTEGER NOT NULL CHECK (epoch >= 1), " +
      "holder TEXT NOT NULL CHECK (length(holder) BETWEEN 1 AND 128), " +
      "lease_expires REAL NOT NULL, " +
      "egress_token TEXT NOT NULL CHECK (length(egress_token) = 64 AND egress_token " +
      "NOT GLOB '*[^0-9a-f]*'), " +
      "sync_token TEXT NOT NULL CHECK (length(sync_token) = 64 AND sync_token " +
      "NOT GLOB '*[^0-9a-f]*'), " +
      "runner_token TEXT NOT NULL CHECK (length(runner_token) = 64 AND runner_token " +
      "NOT GLOB '*[^0-9a-f]*'), " +
      "network_generation INTEGER CHECK (network_generation IS NULL OR " +
      "network_generation >= 1), " +
      // The engines signed in inside this sandbox, as its own runner last reported them
      // (a client's sandbox's: a login is never copied between sandboxes; the owner's own
      // sandbox's are `engine_signins`'), comma-joined, presence only.
      "signins TEXT NOT NULL DEFAULT '' CHECK (length(signins) <= 256 " +
      "AND signins NOT GLOB '*[^!-~]*'), " +
      // A client's sandbox's readable set at its sync agent's last read, as a digest: a
      // change sends the reader back to the feed's start.
      "readable TEXT CHECK (readable IS NULL OR (length(readable) = 16 AND readable NOT GLOB " +
      "'*[^0-9a-f]*')), " +
      "CHECK (egress_token != sync_token AND egress_token != " +
      "runner_token AND sync_token != runner_token)) WITHOUT ROWID",
    "CREATE TRIGGER sandbox_leases_full BEFORE INSERT ON sandbox_leases WHEN (SELECT " +
      "COUNT(*) FROM sandbox_leases) >= 1000 BEGIN SELECT RAISE(ABORT, " +
      "'sandbox_leases_full: 1000 sandboxes are leased'); END",
    "CREATE TRIGGER sandbox_leases_fenced BEFORE UPDATE OF authority, lease_id, epoch, " +
      "egress_token, sync_token, runner_token ON sandbox_leases BEGIN " +
      "SELECT RAISE(ABORT, 'a lease never changes what it was taken as'); END",
    "CREATE TABLE run_grants (" +
      "run TEXT PRIMARY KEY CHECK (length(run) = 36), " +
      // The SHA-256 of the run's own secret, which only its CLI's environment holds.
      "secret TEXT NOT NULL CHECK (length(secret) = 64 AND secret NOT GLOB '*[^0-9a-f]*'), " +
      "stamp TEXT CHECK (stamp IS NULL OR length(stamp) <= 1024), " +
      "origin TEXT CHECK (origin IS NULL OR (length(origin) <= 256 " +
      "AND origin NOT GLOB '*[^ -~]*')), " +
      "scopes TEXT NOT NULL CHECK (length(scopes) <= 512), " +
      "connection TEXT CHECK (connection IS NULL OR length(connection) = 25), " +
      "created_at REAL NOT NULL, CHECK ((stamp IS NULL) = (origin IS NULL))) WITHOUT ROWID",
    "CREATE TABLE conversations (" +
      "id TEXT PRIMARY KEY CHECK (length(id) = 35), " +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128), " +
      "source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64), " +
      "client TEXT CHECK (client IS NULL OR length(client) = 64), " +
      "session_id TEXT CHECK (session_id IS NULL OR (length(session_id) BETWEEN 1 AND 256 " +
      "AND session_id NOT GLOB '*[^!-~]*')), " +
      "last_run TEXT CHECK (last_run IS NULL OR length(last_run) = 36), " +
      "created_at REAL NOT NULL, last_at REAL NOT NULL) WITHOUT ROWID",
    "CREATE INDEX conversations_by_age ON conversations (last_at)",
    "CREATE TRIGGER conversations_full BEFORE INSERT ON conversations WHEN (SELECT COUNT(*) " +
      "FROM conversations) >= 10000 BEGIN SELECT RAISE(ABORT, " +
      "'conversations_full: 10000 conversations are kept'); END",
    "CREATE TABLE run_schedules (" +
      "id TEXT PRIMARY KEY CHECK (length(id) = 36), " +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128), " +
      "input TEXT NOT NULL CHECK (length(CAST(input AS BLOB)) BETWEEN 1 AND 16384), " +
      "trigger TEXT NOT NULL CHECK (trigger IN ('at', 'cron')), " +
      "cron TEXT CHECK (cron IS NULL OR length(cron) BETWEEN 1 AND 256), " +
      "next_at REAL, " +
      "client TEXT CHECK (client IS NULL OR length(client) = 64), " +
      "stamp TEXT CHECK (stamp IS NULL OR length(stamp) <= 1024), " +
      "origin TEXT CHECK (origin IS NULL OR (length(origin) <= 256 " +
      "AND origin NOT GLOB '*[^ -~]*')), " +
      "last_run TEXT CHECK (last_run IS NULL OR length(last_run) = 36), " +
      "last_error TEXT CHECK (last_error IS NULL OR length(last_error) BETWEEN 1 AND 256), " +
      "created_at REAL NOT NULL, CHECK ((trigger = 'cron') = (cron IS NOT NULL)), " +
      "CHECK ((client IS NULL) = (stamp IS NULL) AND (stamp IS NULL) = (origin IS NULL))) " +
      "WITHOUT ROWID",
    "CREATE INDEX run_schedules_due ON run_schedules (next_at) WHERE next_at IS NOT NULL",
    "CREATE TRIGGER run_schedules_full BEFORE INSERT ON run_schedules WHEN (SELECT COUNT(*) " +
      "FROM run_schedules) >= 1000 BEGIN SELECT RAISE(ABORT, " +
      "'run_schedules_full: 1000 schedules are kept; cancel one first'); END",
    "CREATE TABLE platform_tiers (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "tiers TEXT NOT NULL CHECK (length(tiers) <= 1024), read_at REAL NOT NULL)",
    "CREATE TABLE socket_tickets (hash TEXT PRIMARY KEY CHECK (length(hash) = 64 " +
      "AND hash NOT GLOB '*[^0-9a-f]*'), " +
      "conversation TEXT NOT NULL CHECK (length(conversation) = 35), " +
      "grant_id TEXT NOT NULL CHECK (length(grant_id) BETWEEN 1 AND 128), " +
      "expires_at REAL NOT NULL, token_expires_at REAL NOT NULL) WITHOUT ROWID",
    "CREATE TRIGGER socket_tickets_full BEFORE INSERT ON socket_tickets " +
      "WHEN (SELECT COUNT(*) FROM socket_tickets) >= 1024 " +
      "BEGIN SELECT RAISE(ABORT, 'socket_tickets_full: as many socket tickets are " +
      "outstanding as may be; use one or wait for one to expire'); END",
    // A sandbox call's charge counts to its run, as it counts to its source's day and month.
    "CREATE TRIGGER egress_reservations_run_charged AFTER UPDATE OF charge " +
      "ON egress_reservations WHEN OLD.charge IS NULL AND NEW.charge IS NOT NULL " +
      "BEGIN UPDATE runs SET requests = requests + 1, tokens = tokens + ((CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_tokens WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.tokens, 0) ELSE 0 END)), cost = cost + ((CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END)) WHERE id = NEW.run; END",
    "CREATE TRIGGER egress_reservations_run_recharged AFTER UPDATE OF charge, tokens, cost " +
      "ON egress_reservations WHEN NEW.charge IS NOT NULL AND OLD.charge IS NOT NULL " +
      "AND ((CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) != (CASE WHEN OLD.charge IN ('held', 'unknown') THEN OLD.est_cost WHEN OLD.charge IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END) OR (CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_tokens WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.tokens, 0) ELSE 0 END) != (CASE WHEN OLD.charge IN ('held', 'unknown') THEN OLD.est_tokens WHEN OLD.charge IN ('succeeded', 'failed') THEN coalesce(OLD.tokens, 0) ELSE 0 END)) BEGIN " +
      "UPDATE runs SET requests = requests + 0, tokens = tokens + ((CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_tokens WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.tokens, 0) ELSE 0 END) - (CASE WHEN OLD.charge IN ('held', 'unknown') THEN OLD.est_tokens WHEN OLD.charge IN ('succeeded', 'failed') THEN coalesce(OLD.tokens, 0) ELSE 0 END)), cost = cost + ((CASE WHEN NEW.charge IN ('held', 'unknown') THEN NEW.est_cost WHEN NEW.charge IN ('succeeded', 'failed') THEN coalesce(NEW.cost, 0) ELSE 0 END) - (CASE WHEN OLD.charge IN ('held', 'unknown') THEN OLD.est_cost WHEN OLD.charge IN ('succeeded', 'failed') THEN coalesce(OLD.cost, 0) ELSE 0 END)) WHERE id = NEW.run; END",
    // Part 13: the owner's own agents as ledger identities (slice ID; ledger.ts). Each
    // `/workspace/agents/<agent>/agent.json` is one, made as the file appears and retired as
    // it goes, in the file change's own unit: a mail recipient and sender beside the outside
    // agents, counted with them in the registry's rows and, while live, its reserved
    // mailboxes (`ledger_usage`). A retired one keeps its row, and its mail stays readable.
    // Mail to one waits `queued` in its recipient row (read by its agent through
    // `recipients_by_state`) until the `runs` lane starts the run that takes it. The identity
    // is keyed by its ledger id, which is its name's digest, so a name finds its row by the
    // key and no second index is kept.
    "CREATE TABLE native_agents (" +
      "agent_id TEXT PRIMARY KEY CHECK (length(agent_id) = 19 " +
      "AND agent_id GLOB 'ta_*' AND substr(agent_id, 4) NOT GLOB '*[^0-9a-f]*'), " +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128 " +
      "AND agent NOT GLOB '*[^A-Za-z0-9._-]*'), " +
      "created_at REAL NOT NULL, retired_at REAL, " +
      // Messages this agent sent the owner that the owner has not acked yet: its share of
      // the owner's mailbox, as an outside agent's.
      "unacked_to_owner INTEGER NOT NULL DEFAULT 0 CHECK (unacked_to_owner >= 0)) " +
      "WITHOUT ROWID",
    // An identity's name and ledger id never change: its mail names the id.
    "CREATE TRIGGER native_agents_kept BEFORE UPDATE OF agent, agent_id ON native_agents " +
      "BEGIN SELECT RAISE(ABORT, 'an agent identity never changes its name or id'); END",
    "CREATE TRIGGER native_agents_added AFTER INSERT ON native_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents + 1, " +
      "enabled_agents = enabled_agents + (NEW.retired_at IS NULL); END",
    "CREATE TRIGGER native_agents_retired AFTER UPDATE OF retired_at ON native_agents " +
      "WHEN (OLD.retired_at IS NULL) != (NEW.retired_at IS NULL) BEGIN " +
      "UPDATE ledger_usage SET enabled_agents = enabled_agents + " +
      "CASE WHEN NEW.retired_at IS NULL THEN 1 ELSE -1 END; END",
    "CREATE TRIGGER native_agents_removed AFTER DELETE ON native_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents - 1, " +
      "enabled_agents = enabled_agents - (OLD.retired_at IS NULL); END",
    // A native agent's message in its own dm room goes to the owner: its share, as above.
    "CREATE TRIGGER ledger_native_held AFTER UPDATE OF unacked ON ledger_messages " +
      "WHEN NEW.sender_kind = 'native' AND NEW.room_id = 'dm:' || NEW.sender_id " +
      "AND (OLD.unacked = 0) != (NEW.unacked = 0) BEGIN " +
      "UPDATE native_agents SET " +
      "unacked_to_owner = unacked_to_owner + CASE WHEN NEW.unacked = 0 THEN -1 ELSE 1 END " +
      "WHERE agent_id = NEW.sender_id; END",
    "CREATE TRIGGER ledger_native_removed AFTER DELETE ON ledger_messages " +
      "WHEN OLD.sender_kind = 'native' AND OLD.room_id = 'dm:' || OLD.sender_id " +
      "AND OLD.unacked > 0 BEGIN " +
      "UPDATE native_agents SET unacked_to_owner = unacked_to_owner - 1 " +
      "WHERE agent_id = OLD.sender_id; END",
    // Part 14: the placement rule (ADR-035). A body past `PLACE_BYTES` is content of the files
    // plane: a collection row, a history entry or a message body that names it holds a
    // reference on it (`file_contents.refs`) and one in its writer's files scope
    // (`file_scope_contents`, by its `filer`), counted here as each row comes and goes, so
    // content nothing names goes, its chunk becomes garbage and housekeeping deletes it from
    // R2, as a file's. Its bytes count in the files byte quota (`chunk_bytes`), the tier's
    // storage, and in its outside writer's share of it (`client_share`, `owner_reserve`).
    "CREATE TRIGGER collection_rows_placed AFTER INSERT ON collection_rows " +
      "WHEN NEW.content_id IS NOT NULL BEGIN " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; END",
    "CREATE TRIGGER collection_rows_unplaced AFTER DELETE ON collection_rows " +
      "WHEN OLD.content_id IS NOT NULL BEGIN " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER collection_history_placed AFTER INSERT ON collection_history " +
      "WHEN NEW.content_id IS NOT NULL BEGIN " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; END",
    "CREATE TRIGGER collection_history_unplaced AFTER DELETE ON collection_history " +
      "WHEN OLD.content_id IS NOT NULL BEGIN " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER message_bodies_placed AFTER INSERT ON message_bodies " +
      "WHEN NEW.content_id IS NOT NULL BEGIN " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; END",
    "CREATE TRIGGER message_bodies_unplaced AFTER DELETE ON message_bodies " +
      "WHEN OLD.content_id IS NOT NULL BEGIN " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER collection_rows_replaced AFTER UPDATE OF content_id, filer ON " +
      "collection_rows WHEN NEW.content_id IS NOT OLD.content_id OR NEW.filer IS NOT " +
      "OLD.filer BEGIN " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; " +
      "UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER message_bodies_kept BEFORE UPDATE ON message_bodies " +
      "BEGIN SELECT RAISE(ABORT, 'a message body is never changed'); END",
    // Part 15: before real data (slice P). The nightly manifest's bookkeeping (backup.ts): at most
    // two manifests, the newest finished and the one being written, each with its parts as
    // R2 verified them (`<sha256>:<size>`, comma-joined, at most MANIFEST_PARTS). The sandbox
    // meter (sandbox-time.ts): the cursor of the sandbox Worker's log, each authority's
    // running spans a run not yet metered may overlap (one open at most), the seconds a UTC
    // day, and each run's own (`runs.sandbox_ms`).
    // A generation (backup.ts): its id, the day it began, its base cursor (`ended` once the
    // base is read), its base parts and log segments as R2 verified them (lists cleared once
    // its root holds them), its log's span `s` to `e`, and when E was.
    "CREATE TABLE owner_backups (gen TEXT PRIMARY KEY CHECK (length(gen) = 16 " +
      "AND gen NOT GLOB '*[^0-9a-f]*'), day TEXT NOT NULL CHECK (length(day) = 8 " +
      "AND day NOT GLOB '*[^0-9]*'), " +
      "state TEXT NOT NULL CHECK (state IN ('writing', 'done')), " +
      "cursor TEXT CHECK (cursor IS NULL OR length(cursor) <= 16384), " +
      "parts INTEGER NOT NULL CHECK (parts BETWEEN 0 AND 16384), " +
      "part_list TEXT NOT NULL CHECK (length(part_list) <= 1196032), " +
      "segments INTEGER NOT NULL DEFAULT 0 CHECK (segments >= 0), " +
      "segment_list TEXT NOT NULL DEFAULT '' CHECK (length(segment_list) <= " +
      "1753088), " +
      "bytes INTEGER NOT NULL CHECK (bytes >= 0), s INTEGER NOT NULL CHECK (s >= 1), " +
      // The schema version it was begun under: a migration while it runs ends it (review L5-2).
      "schema INTEGER NOT NULL CHECK (schema >= 1), " +
      "e INTEGER, ended_at REAL, started_at REAL NOT NULL, " +
      "finished_at REAL, retry_at REAL, " +
      "CHECK (parts + segments <= 16384), " +
      "CHECK ((state = 'done') = (finished_at IS NOT NULL))) WITHOUT ROWID",
    // The exports open (export.ts): each its id, its end (under `GC_GRACE_S`), and the
    // generation it reads, pinned from deletion until then.
    "CREATE TABLE owner_exports (export_id TEXT PRIMARY KEY CHECK (length(export_id) = 32 " +
      "AND export_id NOT GLOB '*[^0-9a-f]*'), opened_at REAL NOT NULL, " +
      "ends_at REAL NOT NULL, gen TEXT NOT NULL CHECK (length(gen) = 16)) WITHOUT ROWID",
    // Whether anything changed since the last manifest began (backup.ts `mark`), and whether
    // the owner was told its manifest was given up (told once, until one is written).
    "CREATE TABLE owner_backup_state (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "changed INTEGER NOT NULL CHECK (changed IN (0, 1)), " +
      "told INTEGER NOT NULL CHECK (told IN (0, 1)))",
    "INSERT INTO owner_backup_state VALUES (1, 1, 0)",
    // The change log (changelog.ts): while a generation runs, each exported row's image as an
    // export line, by seq, until R2 holds it; and the capture's one row: its generation, the
    // log's first seq `s`, its last `last` and its end `e`, whether it captures, its
    // unshipped bytes, whether they passed `LOG_BYTES`, the seq shipped through, and the
    // segment being shipped.
    "CREATE TABLE log_capture (only INTEGER PRIMARY KEY CHECK (only = 1), gen TEXT, " +
      "s INTEGER NOT NULL DEFAULT 1, last INTEGER NOT NULL DEFAULT 0, e INTEGER, " +
      "capturing INTEGER NOT NULL DEFAULT 0 CHECK (capturing IN (0, 1)), " +
      "bytes INTEGER NOT NULL DEFAULT 0 CHECK (bytes >= 0), " +
      "overflow INTEGER NOT NULL DEFAULT 0 CHECK (overflow IN (0, 1)), " +
      "shipped INTEGER NOT NULL DEFAULT 0, pending_a INTEGER, pending_b INTEGER)",
    "INSERT INTO log_capture (only) VALUES (1)",
    "CREATE TABLE change_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, line TEXT NOT NULL)",
    "CREATE TRIGGER change_log_added AFTER INSERT ON change_log BEGIN UPDATE log_capture SET " +
      "bytes = bytes + length(CAST(NEW.line AS BLOB)) + 64, last = NEW.seq; END",
    "CREATE TRIGGER change_log_removed AFTER DELETE ON change_log BEGIN UPDATE log_capture " +
      "SET bytes = bytes - length(CAST(OLD.line AS BLOB)) - 64; END",
    "CREATE TABLE sandbox_meter (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "after INTEGER NOT NULL CHECK (after >= 0), read_at INTEGER NOT NULL, retry_at REAL)",
    "INSERT INTO sandbox_meter VALUES (1, 0, 0, NULL)",
    "CREATE TABLE sandbox_spans (" +
      // The owner's own work's (`owner`) or one outside client's id.
      "authority TEXT NOT NULL CHECK (length(authority) BETWEEN 1 AND 64), " +
      "started_at INTEGER NOT NULL, ended_at INTEGER CHECK (ended_at IS NULL " +
      "OR ended_at >= started_at), PRIMARY KEY (authority, started_at)) WITHOUT ROWID",
    // A UTC day's sandbox time: the sandbox Worker's own log (`ms`, the estimate) and what
    // E2B billed, from its signed lifecycle events (the truth): ms, vCPU-ms and MB-ms.
    "CREATE TABLE sandbox_days (day TEXT PRIMARY KEY CHECK (length(day) = 8 " +
      "AND day NOT GLOB '*[^0-9]*'), ms INTEGER NOT NULL CHECK (ms >= 0), " +
      "billed_ms INTEGER NOT NULL DEFAULT 0 CHECK (billed_ms >= 0), " +
      "vcpu_ms INTEGER NOT NULL DEFAULT 0 CHECK (vcpu_ms >= 0), " +
      "mb_ms INTEGER NOT NULL DEFAULT 0 CHECK (mb_ms >= 0), " +
      // Executions counted with no id kept to check them against (`BILLED_KEPT` passed).
      "unverified INTEGER NOT NULL DEFAULT 0 CHECK (unverified >= 0)) WITHOUT ROWID",
    // E2B's executions already counted, each by 8 bytes of its execution id's SHA-256 (each
    // counted once, whichever of its events came first), and its event's time: kept while an
    // event that old is still taken (`BILLED_WINDOW_S`).
    "CREATE TABLE sandbox_billed (digest BLOB PRIMARY KEY CHECK (length(digest) = 8), " +
      "at INTEGER NOT NULL) WITHOUT ROWID",
    "CREATE INDEX sandbox_billed_by_age ON sandbox_billed (at)",
    // Each live sandbox's last execution counted, kept until its kill (a kill of a paused
    // sandbox may carry the pause's execution, however long after), with its id for
    // reconciling; the oldest dropped past `LIVE_KEPT`.
    "CREATE TABLE sandbox_live (sandbox_id TEXT PRIMARY KEY CHECK (length(sandbox_id) " +
      "BETWEEN 1 AND 128), digest BLOB NOT NULL CHECK (length(digest) = 8), " +
      "at INTEGER NOT NULL) WITHOUT ROWID",
    "CREATE INDEX sandbox_live_by_age ON sandbox_live (at)",
    // Every exported table's three capture triggers (changelog.ts): while a generation runs, a
    // change's row image goes to the change log, so the generation ends as the owner at E.
    // The changelog test holds each to `exported()`'s columns and keys.
    "CREATE TRIGGER object_owner_logged_insert AFTER INSERT ON object_owner WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'object_owner', 'row', json_object('only', NEW.only, 'owner', NEW.owner)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER object_owner_logged_update AFTER UPDATE ON object_owner WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'object_owner', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.only IS NEW.only); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'object_owner', 'row', json_object('only', NEW.only, 'owner', NEW.owner)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER object_owner_logged_delete AFTER DELETE ON object_owner WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'object_owner', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_admission_logged_insert AFTER INSERT ON owner_admission WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_admission', 'row', json_object('only', NEW.only, 'state', NEW.state, 'reasons', NEW.reasons, 'tier', NEW.tier)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_admission_logged_update AFTER UPDATE ON owner_admission WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_admission', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.only IS NEW.only); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_admission', 'row', json_object('only', NEW.only, 'state', NEW.state, 'reasons', NEW.reasons, 'tier', NEW.tier)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_admission_logged_delete AFTER DELETE ON owner_admission WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_admission', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_policy_logged_insert AFTER INSERT ON owner_policy WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_policy', 'row', json_object('only', NEW.only, 'generation', NEW.generation, 'digest', NEW.digest, 'document', NEW.document, 'egress_generation', NEW.egress_generation)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_policy_logged_update AFTER UPDATE ON owner_policy WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_policy', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.only IS NEW.only); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_policy', 'row', json_object('only', NEW.only, 'generation', NEW.generation, 'digest', NEW.digest, 'document', NEW.document, 'egress_generation', NEW.egress_generation)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_policy_logged_delete AFTER DELETE ON owner_policy WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_policy', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER policy_proposals_logged_insert AFTER INSERT ON policy_proposals WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'policy_proposals', 'row', json_object('request_id', NEW.request_id, 'digest', NEW.digest, 'document', NEW.document, 'at', NEW.at, 'applied_at', NEW.applied_at, 'proposer', NEW.proposer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER policy_proposals_logged_update AFTER UPDATE ON policy_proposals WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'policy_proposals', 'key', json_array(OLD.request_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.request_id IS NEW.request_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'policy_proposals', 'row', json_object('request_id', NEW.request_id, 'digest', NEW.digest, 'document', NEW.document, 'at', NEW.at, 'applied_at', NEW.applied_at, 'proposer', NEW.proposer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER policy_proposals_logged_delete AFTER DELETE ON policy_proposals WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'policy_proposals', 'key', json_array(OLD.request_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER policy_receipts_logged_insert AFTER INSERT ON policy_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'policy_receipts', 'row', json_object('seq', NEW.seq, 'bucket', NEW.bucket, 'kind', NEW.kind, 'at', NEW.at, 'old_digest', NEW.old_digest, 'new_digest', NEW.new_digest, 'request_digest', NEW.request_digest, 'generation', NEW.generation, 'rule', NEW.rule, 'decision', NEW.decision, 'outcome', NEW.outcome)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER policy_receipts_logged_update AFTER UPDATE ON policy_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'policy_receipts', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.seq IS NEW.seq); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'policy_receipts', 'row', json_object('seq', NEW.seq, 'bucket', NEW.bucket, 'kind', NEW.kind, 'at', NEW.at, 'old_digest', NEW.old_digest, 'new_digest', NEW.new_digest, 'request_digest', NEW.request_digest, 'generation', NEW.generation, 'rule', NEW.rule, 'decision', NEW.decision, 'outcome', NEW.outcome)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER policy_receipts_logged_delete AFTER DELETE ON policy_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'policy_receipts', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER outside_clients_logged_insert AFTER INSERT ON outside_clients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'outside_clients', 'row', json_object('client', NEW.client, 'source', NEW.source, 'mode', NEW.mode, 'scopes', NEW.scopes, 'generation', NEW.generation, 'revoked_at', NEW.revoked_at, 'name', NEW.name, 'first_seen', NEW.first_seen, 'last_seen', NEW.last_seen, 'owner_set', NEW.owner_set, 'agents', NEW.agents, 'held', NEW.held, 'profile', NEW.profile, 'revision', NEW.revision)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER outside_clients_logged_update AFTER UPDATE ON outside_clients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'outside_clients', 'key', json_array(OLD.client)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.client IS NEW.client); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'outside_clients', 'row', json_object('client', NEW.client, 'source', NEW.source, 'mode', NEW.mode, 'scopes', NEW.scopes, 'generation', NEW.generation, 'revoked_at', NEW.revoked_at, 'name', NEW.name, 'first_seen', NEW.first_seen, 'last_seen', NEW.last_seen, 'owner_set', NEW.owner_set, 'agents', NEW.agents, 'held', NEW.held, 'profile', NEW.profile, 'revision', NEW.revision)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER outside_clients_logged_delete AFTER DELETE ON outside_clients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'outside_clients', 'key', json_array(OLD.client)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER agent_cards_logged_insert AFTER INSERT ON agent_cards WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'agent_cards', 'row', json_object('card_id', NEW.card_id, 'client', NEW.client, 'family', NEW.family, 'source_ref', NEW.source_ref, 'generation', NEW.generation, 'authenticated_at', NEW.authenticated_at, 'request', NEW.request, 'digest', NEW.digest, 'filed_at', NEW.filed_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER agent_cards_logged_update AFTER UPDATE ON agent_cards WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'agent_cards', 'key', json_array(OLD.card_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.card_id IS NEW.card_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'agent_cards', 'row', json_object('card_id', NEW.card_id, 'client', NEW.client, 'family', NEW.family, 'source_ref', NEW.source_ref, 'generation', NEW.generation, 'authenticated_at', NEW.authenticated_at, 'request', NEW.request, 'digest', NEW.digest, 'filed_at', NEW.filed_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER agent_cards_logged_delete AFTER DELETE ON agent_cards WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'agent_cards', 'key', json_array(OLD.card_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER connections_logged_insert AFTER INSERT ON connections WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'connections', 'row', json_object('id', NEW.id, 'source', NEW.source, 'kind', NEW.kind, 'display_name', NEW.display_name, 'provider', NEW.provider, 'models', NEW.models, 'status', NEW.status, 'version', NEW.version, 'expires_at', NEW.expires_at, 'created_at', NEW.created_at, 'pending_until', NEW.pending_until, 'revoked_at', NEW.revoked_at, 'revoke_due', NEW.revoke_due, 'revoke_tries', NEW.revoke_tries, 'revocable', NEW.revocable, 'egress', NEW.egress)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER connections_logged_update AFTER UPDATE ON connections WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'connections', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.id IS NEW.id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'connections', 'row', json_object('id', NEW.id, 'source', NEW.source, 'kind', NEW.kind, 'display_name', NEW.display_name, 'provider', NEW.provider, 'models', NEW.models, 'status', NEW.status, 'version', NEW.version, 'expires_at', NEW.expires_at, 'created_at', NEW.created_at, 'pending_until', NEW.pending_until, 'revoked_at', NEW.revoked_at, 'revoke_due', NEW.revoke_due, 'revoke_tries', NEW.revoke_tries, 'revocable', NEW.revocable, 'egress', NEW.egress)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER connections_logged_delete AFTER DELETE ON connections WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'connections', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER engine_signins_logged_insert AFTER INSERT ON engine_signins WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'engine_signins', 'row', json_object('engine', NEW.engine, 'signed_in', NEW.signed_in, 'models', NEW.models, 'checked_at', NEW.checked_at, 'changed_at', NEW.changed_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER engine_signins_logged_update AFTER UPDATE ON engine_signins WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'engine_signins', 'key', json_array(OLD.engine)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.engine IS NEW.engine); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'engine_signins', 'row', json_object('engine', NEW.engine, 'signed_in', NEW.signed_in, 'models', NEW.models, 'checked_at', NEW.checked_at, 'changed_at', NEW.changed_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER engine_signins_logged_delete AFTER DELETE ON engine_signins WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'engine_signins', 'key', json_array(OLD.engine)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_notices_logged_insert AFTER INSERT ON owner_notices WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_notices', 'row', json_object('seq', NEW.seq, 'at', NEW.at, 'type', NEW.type, 'detail', NEW.detail)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_notices_logged_update AFTER UPDATE ON owner_notices WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_notices', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.seq IS NEW.seq); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_notices', 'row', json_object('seq', NEW.seq, 'at', NEW.at, 'type', NEW.type, 'detail', NEW.detail)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_notices_logged_delete AFTER DELETE ON owner_notices WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_notices', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_entries_logged_insert AFTER INSERT ON file_entries WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_entries', 'row', json_object('parent', NEW.parent, 'name', NEW.name, 'kind', NEW.kind, 'version', NEW.version, 'content_id', NEW.content_id, 'size', NEW.size, 'mode', NEW.mode, 'mtime_ms', NEW.mtime_ms, 'writer', NEW.writer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_entries_logged_update AFTER UPDATE ON file_entries WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_entries', 'key', json_array(OLD.parent, OLD.name)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.parent IS NEW.parent AND OLD.name IS NEW.name); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_entries', 'row', json_object('parent', NEW.parent, 'name', NEW.name, 'kind', NEW.kind, 'version', NEW.version, 'content_id', NEW.content_id, 'size', NEW.size, 'mode', NEW.mode, 'mtime_ms', NEW.mtime_ms, 'writer', NEW.writer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_entries_logged_delete AFTER DELETE ON file_entries WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_entries', 'key', json_array(OLD.parent, OLD.name)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_history_logged_insert AFTER INSERT ON file_history WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_history', 'row', json_object('version', NEW.version, 'path', NEW.path, 'op', NEW.op, 'kind', NEW.kind, 'content_id', NEW.content_id, 'size', NEW.size, 'mode', NEW.mode, 'writer', NEW.writer, 'at', NEW.at, 'prune_after', NEW.prune_after, 'released', NEW.released)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_history_logged_update AFTER UPDATE ON file_history WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_history', 'key', json_array(OLD.version)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.version IS NEW.version); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_history', 'row', json_object('version', NEW.version, 'path', NEW.path, 'op', NEW.op, 'kind', NEW.kind, 'content_id', NEW.content_id, 'size', NEW.size, 'mode', NEW.mode, 'writer', NEW.writer, 'at', NEW.at, 'prune_after', NEW.prune_after, 'released', NEW.released)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_history_logged_delete AFTER DELETE ON file_history WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_history', 'key', json_array(OLD.version)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_temps_logged_insert AFTER INSERT ON file_temps WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_temps', 'row', json_object('name', NEW.name, 'expires_at', NEW.expires_at, 'writer', NEW.writer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_temps_logged_update AFTER UPDATE ON file_temps WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_temps', 'key', json_array(OLD.name)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.name IS NEW.name); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_temps', 'row', json_object('name', NEW.name, 'expires_at', NEW.expires_at, 'writer', NEW.writer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_temps_logged_delete AFTER DELETE ON file_temps WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_temps', 'key', json_array(OLD.name)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_contents_logged_insert AFTER INSERT ON file_contents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_contents', 'row', json_object('content_id', NEW.content_id, 'size', NEW.size, 'inline', CASE WHEN NEW.inline IS NULL THEN NULL ELSE hex(NEW.inline) END, 'refs', NEW.refs)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_contents_logged_update AFTER UPDATE ON file_contents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_contents', 'key', json_array(OLD.content_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.content_id IS NEW.content_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_contents', 'row', json_object('content_id', NEW.content_id, 'size', NEW.size, 'inline', CASE WHEN NEW.inline IS NULL THEN NULL ELSE hex(NEW.inline) END, 'refs', NEW.refs)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_contents_logged_delete AFTER DELETE ON file_contents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_contents', 'key', json_array(OLD.content_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_content_chunks_logged_insert AFTER INSERT ON file_content_chunks WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_content_chunks', 'row', json_object('content_id', NEW.content_id, 'idx', NEW.idx, 'sha256', NEW.sha256)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_content_chunks_logged_update AFTER UPDATE ON file_content_chunks WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_content_chunks', 'key', json_array(OLD.content_id, OLD.idx)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.content_id IS NEW.content_id AND OLD.idx IS NEW.idx); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_content_chunks', 'row', json_object('content_id', NEW.content_id, 'idx', NEW.idx, 'sha256', NEW.sha256)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_content_chunks_logged_delete AFTER DELETE ON file_content_chunks WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_content_chunks', 'key', json_array(OLD.content_id, OLD.idx)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_chunks_logged_insert AFTER INSERT ON file_chunks WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_chunks', 'row', json_object('sha256', NEW.sha256, 'size', NEW.size, 'state', NEW.state, 'stored', NEW.stored, 'refs', NEW.refs, 'holds', NEW.holds, 'gc_after', NEW.gc_after, 'holder', NEW.holder)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_chunks_logged_update AFTER UPDATE ON file_chunks WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_chunks', 'key', json_array(OLD.sha256)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.sha256 IS NEW.sha256); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'file_chunks', 'row', json_object('sha256', NEW.sha256, 'size', NEW.size, 'state', NEW.state, 'stored', NEW.stored, 'refs', NEW.refs, 'holds', NEW.holds, 'gc_after', NEW.gc_after, 'holder', NEW.holder)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER file_chunks_logged_delete AFTER DELETE ON file_chunks WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'file_chunks', 'key', json_array(OLD.sha256)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_rows_logged_insert AFTER INSERT ON collection_rows WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_rows', 'row', json_object('collection', NEW.collection, 'row_id', NEW.row_id, 'revision', NEW.revision, 'seq', NEW.seq, 'etag', NEW.etag, 'writer', NEW.writer, 'private', NEW.private, 'body', NEW.body, 'updated_at', NEW.updated_at, 'creator', NEW.creator, 'change', NEW.change, 'body_bytes', NEW.body_bytes, 'content_id', NEW.content_id, 'filer', NEW.filer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_rows_logged_update AFTER UPDATE ON collection_rows WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_rows', 'key', json_array(OLD.collection, OLD.row_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.collection IS NEW.collection AND OLD.row_id IS NEW.row_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_rows', 'row', json_object('collection', NEW.collection, 'row_id', NEW.row_id, 'revision', NEW.revision, 'seq', NEW.seq, 'etag', NEW.etag, 'writer', NEW.writer, 'private', NEW.private, 'body', NEW.body, 'updated_at', NEW.updated_at, 'creator', NEW.creator, 'change', NEW.change, 'body_bytes', NEW.body_bytes, 'content_id', NEW.content_id, 'filer', NEW.filer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_rows_logged_delete AFTER DELETE ON collection_rows WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_rows', 'key', json_array(OLD.collection, OLD.row_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_history_logged_insert AFTER INSERT ON collection_history WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_history', 'row', json_object('collection', NEW.collection, 'key', NEW.key, 'revision', NEW.revision, 'op', NEW.op, 'body_json', NEW.body_json, 'at', NEW.at, 'writer', NEW.writer, 'change', NEW.change, 'private_writer', NEW.private_writer, 'body_bytes', NEW.body_bytes, 'content_id', NEW.content_id, 'filer', NEW.filer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_history_logged_update AFTER UPDATE ON collection_history WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_history', 'key', json_array(OLD.collection, OLD.key, OLD.revision)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.collection IS NEW.collection AND OLD.key IS NEW.key AND OLD.revision IS NEW.revision); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_history', 'row', json_object('collection', NEW.collection, 'key', NEW.key, 'revision', NEW.revision, 'op', NEW.op, 'body_json', NEW.body_json, 'at', NEW.at, 'writer', NEW.writer, 'change', NEW.change, 'private_writer', NEW.private_writer, 'body_bytes', NEW.body_bytes, 'content_id', NEW.content_id, 'filer', NEW.filer)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_history_logged_delete AFTER DELETE ON collection_history WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_history', 'key', json_array(OLD.collection, OLD.key, OLD.revision)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_seqs_logged_insert AFTER INSERT ON collection_seqs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_seqs', 'row', json_object('collection', NEW.collection, 'seq', NEW.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_seqs_logged_update AFTER UPDATE ON collection_seqs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_seqs', 'key', json_array(OLD.collection)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.collection IS NEW.collection); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_seqs', 'row', json_object('collection', NEW.collection, 'seq', NEW.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_seqs_logged_delete AFTER DELETE ON collection_seqs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_seqs', 'key', json_array(OLD.collection)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_clock_logged_insert AFTER INSERT ON collection_clock WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_clock', 'row', json_object('only', NEW.only, 'change', NEW.change)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_clock_logged_update AFTER UPDATE ON collection_clock WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_clock', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.only IS NEW.only); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'collection_clock', 'row', json_object('only', NEW.only, 'change', NEW.change)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER collection_clock_logged_delete AFTER DELETE ON collection_clock WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'collection_clock', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER outside_agents_logged_insert AFTER INSERT ON outside_agents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'outside_agents', 'row', json_object('agent_id', NEW.agent_id, 'command_center_id', NEW.command_center_id, 'outside_client', NEW.outside_client, 'outside_family', NEW.outside_family, 'source', NEW.source, 'source_ref', NEW.source_ref, 'display_name', NEW.display_name, 'delivery_method', NEW.delivery_method, 'recipe_id', NEW.recipe_id, 'revision', NEW.revision, 'disabled_reason', NEW.disabled_reason, 'created_at', NEW.created_at, 'max_wakes_per_hour', NEW.max_wakes_per_hour, 'batch_window_s', NEW.batch_window_s, 'unacked_to_owner', NEW.unacked_to_owner)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER outside_agents_logged_update AFTER UPDATE ON outside_agents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'outside_agents', 'key', json_array(OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.agent_id IS NEW.agent_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'outside_agents', 'row', json_object('agent_id', NEW.agent_id, 'command_center_id', NEW.command_center_id, 'outside_client', NEW.outside_client, 'outside_family', NEW.outside_family, 'source', NEW.source, 'source_ref', NEW.source_ref, 'display_name', NEW.display_name, 'delivery_method', NEW.delivery_method, 'recipe_id', NEW.recipe_id, 'revision', NEW.revision, 'disabled_reason', NEW.disabled_reason, 'created_at', NEW.created_at, 'max_wakes_per_hour', NEW.max_wakes_per_hour, 'batch_window_s', NEW.batch_window_s, 'unacked_to_owner', NEW.unacked_to_owner)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER outside_agents_logged_delete AFTER DELETE ON outside_agents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'outside_agents', 'key', json_array(OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER native_agents_logged_insert AFTER INSERT ON native_agents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'native_agents', 'row', json_object('agent_id', NEW.agent_id, 'agent', NEW.agent, 'created_at', NEW.created_at, 'retired_at', NEW.retired_at, 'unacked_to_owner', NEW.unacked_to_owner)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER native_agents_logged_update AFTER UPDATE ON native_agents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'native_agents', 'key', json_array(OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.agent_id IS NEW.agent_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'native_agents', 'row', json_object('agent_id', NEW.agent_id, 'agent', NEW.agent, 'created_at', NEW.created_at, 'retired_at', NEW.retired_at, 'unacked_to_owner', NEW.unacked_to_owner)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER native_agents_logged_delete AFTER DELETE ON native_agents WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'native_agents', 'key', json_array(OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_messages_logged_insert AFTER INSERT ON ledger_messages WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_messages', 'row', json_object('seq', NEW.seq, 'message_id', NEW.message_id, 'command_center_id', NEW.command_center_id, 'room_id', NEW.room_id, 'sender_kind', NEW.sender_kind, 'sender_id', NEW.sender_id, 'client_message_id', NEW.client_message_id, 'body_sha256', NEW.body_sha256, 'parent_id', NEW.parent_id, 'chain_root', NEW.chain_root, 'chain_depth', NEW.chain_depth, 'created_at', NEW.created_at, 'body_bytes', NEW.body_bytes, 'unacked', NEW.unacked, 'authority', NEW.authority, 'attended', NEW.attended, 'line_bytes', NEW.line_bytes)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_messages_logged_update AFTER UPDATE ON ledger_messages WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_messages', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.seq IS NEW.seq); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_messages', 'row', json_object('seq', NEW.seq, 'message_id', NEW.message_id, 'command_center_id', NEW.command_center_id, 'room_id', NEW.room_id, 'sender_kind', NEW.sender_kind, 'sender_id', NEW.sender_id, 'client_message_id', NEW.client_message_id, 'body_sha256', NEW.body_sha256, 'parent_id', NEW.parent_id, 'chain_root', NEW.chain_root, 'chain_depth', NEW.chain_depth, 'created_at', NEW.created_at, 'body_bytes', NEW.body_bytes, 'unacked', NEW.unacked, 'authority', NEW.authority, 'attended', NEW.attended, 'line_bytes', NEW.line_bytes)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_messages_logged_delete AFTER DELETE ON ledger_messages WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_messages', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER message_bodies_logged_insert AFTER INSERT ON message_bodies WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'message_bodies', 'row', json_object('message_id', NEW.message_id, 'room_id', NEW.room_id, 'body', NEW.body, 'created_at', NEW.created_at, 'body_bytes', NEW.body_bytes, 'content_id', NEW.content_id, 'filer', NEW.filer, 'meta_bytes', NEW.meta_bytes)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER message_bodies_logged_update AFTER UPDATE ON message_bodies WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'message_bodies', 'key', json_array(OLD.message_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.message_id IS NEW.message_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'message_bodies', 'row', json_object('message_id', NEW.message_id, 'room_id', NEW.room_id, 'body', NEW.body, 'created_at', NEW.created_at, 'body_bytes', NEW.body_bytes, 'content_id', NEW.content_id, 'filer', NEW.filer, 'meta_bytes', NEW.meta_bytes)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER message_bodies_logged_delete AFTER DELETE ON message_bodies WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'message_bodies', 'key', json_array(OLD.message_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_recipients_logged_insert AFTER INSERT ON ledger_recipients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_recipients', 'row', json_object('message_id', NEW.message_id, 'agent_id', NEW.agent_id, 'seq', NEW.seq, 'state', NEW.state, 'changed_at', NEW.changed_at, 'unacked', NEW.unacked)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_recipients_logged_update AFTER UPDATE ON ledger_recipients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_recipients', 'key', json_array(OLD.message_id, OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.message_id IS NEW.message_id AND OLD.agent_id IS NEW.agent_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_recipients', 'row', json_object('message_id', NEW.message_id, 'agent_id', NEW.agent_id, 'seq', NEW.seq, 'state', NEW.state, 'changed_at', NEW.changed_at, 'unacked', NEW.unacked)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_recipients_logged_delete AFTER DELETE ON ledger_recipients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_recipients', 'key', json_array(OLD.message_id, OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_receipts_logged_insert AFTER INSERT ON ledger_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_receipts', 'row', json_object('seq', NEW.seq, 'agent_id', NEW.agent_id, 'kind', NEW.kind, 'ref', NEW.ref, 'at', NEW.at, 'detail_json', NEW.detail_json)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_receipts_logged_update AFTER UPDATE ON ledger_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_receipts', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.seq IS NEW.seq); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_receipts', 'row', json_object('seq', NEW.seq, 'agent_id', NEW.agent_id, 'kind', NEW.kind, 'ref', NEW.ref, 'at', NEW.at, 'detail_json', NEW.detail_json)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_receipts_logged_delete AFTER DELETE ON ledger_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_receipts', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_readers_logged_insert AFTER INSERT ON ledger_readers WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_readers', 'row', json_object('reader', NEW.reader, 'through_seq', NEW.through_seq, 'acked_at', NEW.acked_at, 'unacked', NEW.unacked)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_readers_logged_update AFTER UPDATE ON ledger_readers WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_readers', 'key', json_array(OLD.reader)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.reader IS NEW.reader); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_readers', 'row', json_object('reader', NEW.reader, 'through_seq', NEW.through_seq, 'acked_at', NEW.acked_at, 'unacked', NEW.unacked)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_readers_logged_delete AFTER DELETE ON ledger_readers WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_readers', 'key', json_array(OLD.reader)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER model_spend_logged_insert AFTER INSERT ON model_spend WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'model_spend', 'row', json_object('period', NEW.period, 'source', NEW.source, 'cost', NEW.cost)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER model_spend_logged_update AFTER UPDATE ON model_spend WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'model_spend', 'key', json_array(OLD.period, OLD.source)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.period IS NEW.period AND OLD.source IS NEW.source); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'model_spend', 'row', json_object('period', NEW.period, 'source', NEW.source, 'cost', NEW.cost)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER model_spend_logged_delete AFTER DELETE ON model_spend WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'model_spend', 'key', json_array(OLD.period, OLD.source)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER model_reservations_logged_insert AFTER INSERT ON model_reservations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'model_reservations', 'row', json_object('seq', NEW.seq, 'id', NEW.id, 'run', NEW.run, 'connection_id', NEW.connection_id, 'source', NEW.source, 'model', NEW.model, 'state', NEW.state, 'rule', NEW.rule, 'max_output_tokens', NEW.max_output_tokens, 'est_tokens', NEW.est_tokens, 'est_cost', NEW.est_cost, 'tokens', NEW.tokens, 'cost', NEW.cost, 'policy_generation', NEW.policy_generation, 'day', NEW.day, 'month', NEW.month, 'created_at', NEW.created_at, 'settled_at', NEW.settled_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER model_reservations_logged_update AFTER UPDATE ON model_reservations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'model_reservations', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.seq IS NEW.seq); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'model_reservations', 'row', json_object('seq', NEW.seq, 'id', NEW.id, 'run', NEW.run, 'connection_id', NEW.connection_id, 'source', NEW.source, 'model', NEW.model, 'state', NEW.state, 'rule', NEW.rule, 'max_output_tokens', NEW.max_output_tokens, 'est_tokens', NEW.est_tokens, 'est_cost', NEW.est_cost, 'tokens', NEW.tokens, 'cost', NEW.cost, 'policy_generation', NEW.policy_generation, 'day', NEW.day, 'month', NEW.month, 'created_at', NEW.created_at, 'settled_at', NEW.settled_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER model_reservations_logged_delete AFTER DELETE ON model_reservations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'model_reservations', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER egress_reservations_logged_insert AFTER INSERT ON egress_reservations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'egress_reservations', 'row', json_object('id', NEW.id, 'lease_id', NEW.lease_id, 'epoch', NEW.epoch, 'run', NEW.run, 'source', NEW.source, 'charge', NEW.charge, 'rule', NEW.rule, 'max_output_tokens', NEW.max_output_tokens, 'est_tokens', NEW.est_tokens, 'est_cost', NEW.est_cost, 'tokens', NEW.tokens, 'cost', NEW.cost, 'day', NEW.day, 'month', NEW.month, 'served', NEW.served, 'connection_id', NEW.connection_id, 'version', NEW.version, 'method', NEW.method, 'path', NEW.path, 'generation', NEW.generation, 'bytes_hint', NEW.bytes_hint, 'opened_at', NEW.opened_at, 'beat_at', NEW.beat_at, 'state', NEW.state, 'code', NEW.code, 'ended', NEW.ended, 'outcome', NEW.outcome, 'status', NEW.status, 'bytes', NEW.bytes, 'settled_at', NEW.settled_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER egress_reservations_logged_update AFTER UPDATE ON egress_reservations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'egress_reservations', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.id IS NEW.id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'egress_reservations', 'row', json_object('id', NEW.id, 'lease_id', NEW.lease_id, 'epoch', NEW.epoch, 'run', NEW.run, 'source', NEW.source, 'charge', NEW.charge, 'rule', NEW.rule, 'max_output_tokens', NEW.max_output_tokens, 'est_tokens', NEW.est_tokens, 'est_cost', NEW.est_cost, 'tokens', NEW.tokens, 'cost', NEW.cost, 'day', NEW.day, 'month', NEW.month, 'served', NEW.served, 'connection_id', NEW.connection_id, 'version', NEW.version, 'method', NEW.method, 'path', NEW.path, 'generation', NEW.generation, 'bytes_hint', NEW.bytes_hint, 'opened_at', NEW.opened_at, 'beat_at', NEW.beat_at, 'state', NEW.state, 'code', NEW.code, 'ended', NEW.ended, 'outcome', NEW.outcome, 'status', NEW.status, 'bytes', NEW.bytes, 'settled_at', NEW.settled_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER egress_reservations_logged_delete AFTER DELETE ON egress_reservations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'egress_reservations', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER runs_logged_insert AFTER INSERT ON runs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'runs', 'row', json_object('seq', NEW.seq, 'id', NEW.id, 'agent', NEW.agent, 'conversation', NEW.conversation, 'source', NEW.source, 'credential', NEW.credential, 'model', NEW.model, 'state', NEW.state, 'initiator', NEW.initiator, 'client', NEW.client, 'stamp', NEW.stamp, 'request', NEW.request, 'unattended', NEW.unattended, 'mail', NEW.mail, 'cancel_requested', NEW.cancel_requested, 'requests', NEW.requests, 'tokens', NEW.tokens, 'cost', NEW.cost, 'last_seq', NEW.last_seq, 'result', NEW.result, 'result_cut', NEW.result_cut, 'error', NEW.error, 'code', NEW.code, 'exit_code', NEW.exit_code, 'created_at', NEW.created_at, 'started_at', NEW.started_at, 'settled_at', NEW.settled_at, 'epoch', NEW.epoch, 'beat_at', NEW.beat_at, 'sandbox_ms', NEW.sandbox_ms, 'sandbox_wait', NEW.sandbox_wait)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER runs_logged_update AFTER UPDATE ON runs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'runs', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.seq IS NEW.seq); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'runs', 'row', json_object('seq', NEW.seq, 'id', NEW.id, 'agent', NEW.agent, 'conversation', NEW.conversation, 'source', NEW.source, 'credential', NEW.credential, 'model', NEW.model, 'state', NEW.state, 'initiator', NEW.initiator, 'client', NEW.client, 'stamp', NEW.stamp, 'request', NEW.request, 'unattended', NEW.unattended, 'mail', NEW.mail, 'cancel_requested', NEW.cancel_requested, 'requests', NEW.requests, 'tokens', NEW.tokens, 'cost', NEW.cost, 'last_seq', NEW.last_seq, 'result', NEW.result, 'result_cut', NEW.result_cut, 'error', NEW.error, 'code', NEW.code, 'exit_code', NEW.exit_code, 'created_at', NEW.created_at, 'started_at', NEW.started_at, 'settled_at', NEW.settled_at, 'epoch', NEW.epoch, 'beat_at', NEW.beat_at, 'sandbox_ms', NEW.sandbox_ms, 'sandbox_wait', NEW.sandbox_wait)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER runs_logged_delete AFTER DELETE ON runs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'runs', 'key', json_array(OLD.seq)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER run_inputs_logged_insert AFTER INSERT ON run_inputs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'run_inputs', 'row', json_object('run', NEW.run, 'prompt', NEW.prompt, 'bytes', NEW.bytes, 'origin', NEW.origin)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER run_inputs_logged_update AFTER UPDATE ON run_inputs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'run_inputs', 'key', json_array(OLD.run)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.run IS NEW.run); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'run_inputs', 'row', json_object('run', NEW.run, 'prompt', NEW.prompt, 'bytes', NEW.bytes, 'origin', NEW.origin)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER run_inputs_logged_delete AFTER DELETE ON run_inputs WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'run_inputs', 'key', json_array(OLD.run)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER run_schedules_logged_insert AFTER INSERT ON run_schedules WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'run_schedules', 'row', json_object('id', NEW.id, 'agent', NEW.agent, 'input', NEW.input, 'trigger', NEW.trigger, 'cron', NEW.cron, 'next_at', NEW.next_at, 'client', NEW.client, 'stamp', NEW.stamp, 'origin', NEW.origin, 'last_run', NEW.last_run, 'last_error', NEW.last_error, 'created_at', NEW.created_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER run_schedules_logged_update AFTER UPDATE ON run_schedules WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'run_schedules', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.id IS NEW.id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'run_schedules', 'row', json_object('id', NEW.id, 'agent', NEW.agent, 'input', NEW.input, 'trigger', NEW.trigger, 'cron', NEW.cron, 'next_at', NEW.next_at, 'client', NEW.client, 'stamp', NEW.stamp, 'origin', NEW.origin, 'last_run', NEW.last_run, 'last_error', NEW.last_error, 'created_at', NEW.created_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER run_schedules_logged_delete AFTER DELETE ON run_schedules WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'run_schedules', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER conversations_logged_insert AFTER INSERT ON conversations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'conversations', 'row', json_object('id', NEW.id, 'agent', NEW.agent, 'source', NEW.source, 'client', NEW.client, 'session_id', NEW.session_id, 'last_run', NEW.last_run, 'created_at', NEW.created_at, 'last_at', NEW.last_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER conversations_logged_update AFTER UPDATE ON conversations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'conversations', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.id IS NEW.id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'conversations', 'row', json_object('id', NEW.id, 'agent', NEW.agent, 'source', NEW.source, 'client', NEW.client, 'session_id', NEW.session_id, 'last_run', NEW.last_run, 'created_at', NEW.created_at, 'last_at', NEW.last_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER conversations_logged_delete AFTER DELETE ON conversations WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'conversations', 'key', json_array(OLD.id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    // The one storage number (founder, 2026-11-03): a body kept inline in its row is user
    // content too, counted in `body_bytes`, owner-wide and in its writer's files scope (its
    // `filer`), so the tier's storage holds every byte a user stores wherever it lives.
    "CREATE TRIGGER collection_rows_inline_added AFTER INSERT ON collection_rows WHEN NEW.content_id IS NULL " +
      "AND NEW.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes + NEW.body_bytes; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, 0, NEW.body_bytes FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET body_bytes = body_bytes + excluded.body_bytes; END",
    "CREATE TRIGGER collection_rows_inline_removed AFTER DELETE ON collection_rows WHEN OLD.content_id IS NULL " +
      "AND OLD.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes - OLD.body_bytes; " +
      "UPDATE file_shares SET body_bytes = body_bytes - (OLD.body_bytes) WHERE who IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); END",
    "CREATE TRIGGER collection_rows_inline_came AFTER UPDATE OF body_bytes, content_id, filer ON collection_rows " +
      "WHEN NEW.content_id IS NULL AND NEW.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes + NEW.body_bytes; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, 0, NEW.body_bytes FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET body_bytes = body_bytes + excluded.body_bytes; END",
    "CREATE TRIGGER collection_rows_inline_went AFTER UPDATE OF body_bytes, content_id, filer ON collection_rows " +
      "WHEN OLD.content_id IS NULL AND OLD.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes - OLD.body_bytes; " +
      "UPDATE file_shares SET body_bytes = body_bytes - (OLD.body_bytes) WHERE who IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); END",
    "CREATE TRIGGER collection_history_inline_added AFTER INSERT ON collection_history WHEN NEW.content_id IS NULL " +
      "AND NEW.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes + NEW.body_bytes; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, 0, NEW.body_bytes FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET body_bytes = body_bytes + excluded.body_bytes; END",
    "CREATE TRIGGER collection_history_inline_removed AFTER DELETE ON collection_history WHEN OLD.content_id IS NULL " +
      "AND OLD.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes - OLD.body_bytes; " +
      "UPDATE file_shares SET body_bytes = body_bytes - (OLD.body_bytes) WHERE who IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); END",
    "CREATE TRIGGER collection_history_inline_came AFTER UPDATE OF body_bytes, content_id, filer ON collection_history " +
      "WHEN NEW.content_id IS NULL AND NEW.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes + NEW.body_bytes; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, 0, NEW.body_bytes FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET body_bytes = body_bytes + excluded.body_bytes; END",
    "CREATE TRIGGER collection_history_inline_went AFTER UPDATE OF body_bytes, content_id, filer ON collection_history " +
      "WHEN OLD.content_id IS NULL AND OLD.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes - OLD.body_bytes; " +
      "UPDATE file_shares SET body_bytes = body_bytes - (OLD.body_bytes) WHERE who IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); END",
    "CREATE TRIGGER message_bodies_inline_added AFTER INSERT ON message_bodies WHEN NEW.content_id IS NULL " +
      "AND NEW.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes + NEW.body_bytes; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, 0, NEW.body_bytes FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET body_bytes = body_bytes + excluded.body_bytes; END",
    "CREATE TRIGGER message_bodies_inline_removed AFTER DELETE ON message_bodies WHEN OLD.content_id IS NULL " +
      "AND OLD.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes - OLD.body_bytes; " +
      "UPDATE file_shares SET body_bytes = body_bytes - (OLD.body_bytes) WHERE who IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); END",
    "CREATE TRIGGER message_bodies_inline_came AFTER UPDATE OF body_bytes, content_id, filer ON message_bodies " +
      "WHEN NEW.content_id IS NULL AND NEW.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes + NEW.body_bytes; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, 0, NEW.body_bytes FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET body_bytes = body_bytes + excluded.body_bytes; END",
    "CREATE TRIGGER message_bodies_inline_went AFTER UPDATE OF body_bytes, content_id, filer ON message_bodies " +
      "WHEN OLD.content_id IS NULL AND OLD.body_bytes > 0 BEGIN " +
      "UPDATE file_space SET body_bytes = body_bytes - OLD.body_bytes; " +
      "UPDATE file_shares SET body_bytes = body_bytes - (OLD.body_bytes) WHERE who IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); END",
    // A message's lines past its body (slice STORE item 2, D2): the one number counts mail at
    // its real bytes, hot as archived.
    "CREATE TRIGGER message_bodies_meta_added AFTER INSERT ON message_bodies WHEN NEW.meta_bytes > 0 " +
      "BEGIN UPDATE file_space SET body_bytes = body_bytes + NEW.meta_bytes; " +
      "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, manifest_bytes, body_bytes) SELECT who, 0, 0, 0, 0, 0, NEW.meta_bytes FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE 1 ON CONFLICT (who) DO UPDATE SET body_bytes = body_bytes + excluded.body_bytes; END",
    "CREATE TRIGGER message_bodies_meta_removed AFTER DELETE ON message_bodies WHEN OLD.meta_bytes > 0 " +
      "BEGIN UPDATE file_space SET body_bytes = body_bytes - OLD.meta_bytes; " +
      "UPDATE file_shares SET body_bytes = body_bytes - (OLD.meta_bytes) WHERE who IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); END",
    // Part 16: the doors' records in the owner's object (slice L). `owner_keys`, the key store
    // (`keys.ts`): each API key's id, name, mint time, last use and revoke, its row only while
    // its owner-set grant row stands (its `client`), so the owner's list and its erasure can
    // enumerate them. `inbound_bindings` (`inbound.ts`): each inbound event source, its agent,
    // its revision, the directory route the doors keep for it, an owner-held proof and its
    // secret, and its breaker. Both are exported, so each is logged as the rest are.
    "CREATE TABLE owner_keys (key_id TEXT PRIMARY KEY CHECK (length(key_id) = 16 AND " +
      "key_id NOT GLOB '*[^0-9a-f]*'), client TEXT NOT NULL UNIQUE CHECK " +
      "(length(client) = 64), name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80), " +
      "created_at INTEGER NOT NULL, last_used_at INTEGER, revoked_at INTEGER)",
    "CREATE TABLE inbound_bindings (binding_id TEXT PRIMARY KEY CHECK " +
      "(length(binding_id) = 32), kind TEXT NOT NULL CHECK (kind IN ('capability', " +
      "'shared')), agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 64), " +
      "enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)), revision INTEGER NOT NULL " +
      "CHECK (revision >= 1), route TEXT NOT NULL UNIQUE CHECK (length(route) <= 160), " +
      "profile TEXT CHECK (profile IS NULL OR length(profile) <= 1024), " +
      "secret TEXT CHECK (secret IS NULL OR length(secret) <= 4096), " +
      "failures TEXT NOT NULL DEFAULT '[]' CHECK (length(failures) <= 64), " +
      "tripped INTEGER NOT NULL DEFAULT 0 CHECK (tripped IN (0, 1)), " +
      "created_at INTEGER NOT NULL, changed_at INTEGER NOT NULL)",
    "CREATE TRIGGER owner_keys_logged_insert AFTER INSERT ON owner_keys WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_keys', 'row', json_object('key_id', NEW.key_id, 'client', NEW.client, 'name', NEW.name, 'created_at', NEW.created_at, 'last_used_at', NEW.last_used_at, 'revoked_at', NEW.revoked_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_keys_logged_update AFTER UPDATE ON owner_keys WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_keys', 'key', json_array(OLD.key_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.key_id IS NEW.key_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'owner_keys', 'row', json_object('key_id', NEW.key_id, 'client', NEW.client, 'name', NEW.name, 'created_at', NEW.created_at, 'last_used_at', NEW.last_used_at, 'revoked_at', NEW.revoked_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER owner_keys_logged_delete AFTER DELETE ON owner_keys WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'owner_keys', 'key', json_array(OLD.key_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER inbound_bindings_logged_insert AFTER INSERT ON inbound_bindings WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'inbound_bindings', 'row', json_object('binding_id', NEW.binding_id, 'kind', NEW.kind, 'agent', NEW.agent, 'enabled', NEW.enabled, 'revision', NEW.revision, 'profile', NEW.profile, 'failures', NEW.failures, 'tripped', NEW.tripped, 'created_at', NEW.created_at, 'changed_at', NEW.changed_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER inbound_bindings_logged_update AFTER UPDATE ON inbound_bindings WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'inbound_bindings', 'key', json_array(OLD.binding_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.binding_id IS NEW.binding_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'inbound_bindings', 'row', json_object('binding_id', NEW.binding_id, 'kind', NEW.kind, 'agent', NEW.agent, 'enabled', NEW.enabled, 'revision', NEW.revision, 'profile', NEW.profile, 'failures', NEW.failures, 'tripped', NEW.tripped, 'created_at', NEW.created_at, 'changed_at', NEW.changed_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER inbound_bindings_logged_delete AFTER DELETE ON inbound_bindings WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'inbound_bindings', 'key', json_array(OLD.binding_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    // Part 17: the vendor's own sign-in inside a sandbox (slice SIGNIN, `signins.ts`).
    // `sandbox_signins`: the sign-in in progress, one an authority, holding that sandbox's
    // lease, its latest prompt (the vendor's URL and code, never a token) and its runner's
    // last beat; gone at its end. `platform_signins`: the served records whose vendor sign-in
    // the control record switches on, as the binder or the lane last read them.
    "CREATE TABLE sandbox_signins (authority TEXT PRIMARY KEY CHECK (authority = 'owner' " +
      "OR (length(authority) = 64 AND authority NOT GLOB '*[^0-9a-f]*')), " +
      "id TEXT NOT NULL UNIQUE CHECK (length(id) = 35 AND id GLOB 'si_*'), " +
      "engine TEXT NOT NULL CHECK (length(engine) BETWEEN 1 AND 64), " +
      "mode TEXT NOT NULL CHECK (mode IN ('in', 'out')), " +
      "region TEXT CHECK (region IS NULL OR length(region) BETWEEN 1 AND 64), " +
      "lease_id TEXT NOT NULL CHECK (length(lease_id) = 32), epoch INTEGER NOT NULL, " +
      "state TEXT NOT NULL CHECK (state IN ('starting', 'open', 'code_sent', 'signed_in', " +
      "'signed_out', 'failed', 'cancelled', 'refused', 'expired')), " +
      "url TEXT CHECK (url IS NULL OR length(url) <= 8000), " +
      "code TEXT CHECK (code IS NULL OR length(code) <= 16), " +
      "accepts_code INTEGER NOT NULL DEFAULT 0 CHECK (accepts_code IN (0, 1)), " +
      "started_at REAL NOT NULL, beat_at REAL NOT NULL) WITHOUT ROWID",
    "CREATE TRIGGER sandbox_signins_full BEFORE INSERT ON sandbox_signins WHEN (SELECT " +
      "COUNT(*) FROM sandbox_signins) >= 1000 BEGIN SELECT RAISE(ABORT, " +
      "'sandbox_signins_full: 1000 sign-ins are live'); END",
    "CREATE TABLE platform_signins (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "engines TEXT NOT NULL CHECK (length(engines) <= 256 " +
      "AND engines NOT GLOB '*[^a-z0-9_,-]*'), read_at REAL NOT NULL)",
    // Part 18: the archive (slice STORE item 2, `segments.ts`). `archive_segments`: one row a
    // segment, its fence: the stream, its key range and time, its records, its content (a
    // files-plane content the row names, counted as a placed body is), its trailer's range,
    // and a bloom of the agents its rooms name; a segment never changes, a rewrite makes a
    // new one. `archive_moves`: a stream's move in flight, between its two units.
    // `archive_purges`: an agent deleted while segments may hold its mail, until the lane has
    // rewritten each; reads and the export drop its rooms at once. `ledger_ids`: the key a
    // message's id carries its seq under.
    "CREATE TABLE archive_segments (segment INTEGER PRIMARY KEY AUTOINCREMENT, " +
      "stream TEXT NOT NULL CHECK (stream IN ('mail')), first_key INTEGER NOT NULL, " +
      "last_key INTEGER NOT NULL CHECK (last_key >= first_key), first_at REAL NOT NULL, " +
      "last_at REAL NOT NULL, records INTEGER NOT NULL CHECK (records >= 1), " +
      "bytes INTEGER NOT NULL CHECK (bytes >= 1), " +
      "content_id TEXT NOT NULL CHECK (length(content_id) = 64), " +
      "filer TEXT NOT NULL CHECK (filer = 'owner'), " +
      "trailer_offset INTEGER NOT NULL CHECK (trailer_offset >= 0), " +
      "trailer_bytes INTEGER NOT NULL CHECK (trailer_bytes >= 1), " +
      "members TEXT NOT NULL CHECK (length(members) = 128 AND members NOT GLOB " +
      "'*[^0-9a-f]*'), made_at REAL NOT NULL)",
    "CREATE INDEX archive_segments_by_last ON archive_segments (stream, last_key)",
    // The segments under the least (`SEGMENT_LEAST_BYTES`), found at once: at most one
    // stands at a time, the lone one or the one a bounded rewrite left for the next pass
    // (review N7: compaction across passes, derived from the fences alone).
    "CREATE INDEX archive_segments_small ON archive_segments (bytes) WHERE bytes < 8388608",
    "CREATE TRIGGER archive_segments_placed AFTER INSERT ON archive_segments " +
      "BEGIN UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, NEW.content_id, 1 FROM (SELECT '*' AS who WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT NEW.filer WHERE NEW.filer GLOB 'client:*' UNION ALL SELECT 'owner' WHERE NEW.filer NOT GLOB 'client:*') WHERE NEW.content_id IS NOT NULL ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1; END",
    "CREATE TRIGGER archive_segments_unplaced AFTER DELETE ON archive_segments " +
      "BEGIN UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = OLD.content_id AND scope IN (CASE WHEN OLD.filer GLOB 'client:*' THEN '*' ELSE 'owner' END, OLD.filer); " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER archive_segments_kept BEFORE UPDATE ON archive_segments " +
      "BEGIN SELECT RAISE(ABORT, 'a segment never changes; a rewrite makes a new one'); END",
    // No two segments of a stream overlap in key range: a lookup by key finds at most one,
    // and the chain read's frontier is the next segment's first key (review H1).
    "CREATE TRIGGER archive_segments_disjoint BEFORE INSERT ON archive_segments " +
      "WHEN EXISTS (SELECT 1 FROM archive_segments WHERE stream = NEW.stream AND " +
      "last_key >= NEW.first_key AND first_key <= NEW.last_key) BEGIN SELECT RAISE(ABORT, " +
      "'archive segments never overlap in key range'); END",
    "CREATE TABLE archive_moves (stream TEXT PRIMARY KEY CHECK (stream IN ('mail', " +
      "'rewrite')), intent_id TEXT NOT NULL CHECK (length(intent_id) = 32), " +
      "digest TEXT NOT NULL CHECK (length(digest) = 64), started_at REAL NOT NULL, " +
      // The stored figure before the move, which its second unit may not leave it above.
      "stored_before INTEGER NOT NULL CHECK (stored_before >= 0))",
    "CREATE TABLE archive_purges (agent_id TEXT PRIMARY KEY CHECK (length(agent_id) " +
      "BETWEEN 1 AND 64), at REAL NOT NULL, " +
      "next_segment INTEGER NOT NULL DEFAULT 0 CHECK (next_segment >= 0))",
    "CREATE TABLE ledger_ids (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "id_key TEXT NOT NULL CHECK (length(id_key) = 64 AND id_key NOT GLOB '*[^0-9a-f]*'))",
    "CREATE TRIGGER archive_segments_logged_insert AFTER INSERT ON archive_segments WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'archive_segments', 'row', json_object('segment', NEW.segment, 'stream', NEW.stream, 'first_key', NEW.first_key, 'last_key', NEW.last_key, 'first_at', NEW.first_at, 'last_at', NEW.last_at, 'records', NEW.records, 'bytes', NEW.bytes, 'content_id', NEW.content_id, 'filer', NEW.filer, 'trailer_offset', NEW.trailer_offset, 'trailer_bytes', NEW.trailer_bytes, 'members', NEW.members, 'made_at', NEW.made_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER archive_segments_logged_update AFTER UPDATE ON archive_segments WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'archive_segments', 'key', json_array(OLD.segment)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.segment IS NEW.segment); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'archive_segments', 'row', json_object('segment', NEW.segment, 'stream', NEW.stream, 'first_key', NEW.first_key, 'last_key', NEW.last_key, 'first_at', NEW.first_at, 'last_at', NEW.last_at, 'records', NEW.records, 'bytes', NEW.bytes, 'content_id', NEW.content_id, 'filer', NEW.filer, 'trailer_offset', NEW.trailer_offset, 'trailer_bytes', NEW.trailer_bytes, 'members', NEW.members, 'made_at', NEW.made_at)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER archive_segments_logged_delete AFTER DELETE ON archive_segments WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'archive_segments', 'key', json_array(OLD.segment)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER archive_purges_logged_insert AFTER INSERT ON archive_purges WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'archive_purges', 'row', json_object('agent_id', NEW.agent_id, 'at', NEW.at, 'next_segment', NEW.next_segment)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER archive_purges_logged_update AFTER UPDATE ON archive_purges WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'archive_purges', 'key', json_array(OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.agent_id IS NEW.agent_id); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'archive_purges', 'row', json_object('agent_id', NEW.agent_id, 'at', NEW.at, 'next_segment', NEW.next_segment)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER archive_purges_logged_delete AFTER DELETE ON archive_purges WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'archive_purges', 'key', json_array(OLD.agent_id)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_ids_logged_insert AFTER INSERT ON ledger_ids WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_ids', 'row', json_object('only', NEW.only, 'id_key', NEW.id_key)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_ids_logged_update AFTER UPDATE ON ledger_ids WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_ids', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.only IS NEW.only); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'ledger_ids', 'row', json_object('only', NEW.only, 'id_key', NEW.id_key)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER ledger_ids_logged_delete AFTER DELETE ON ledger_ids WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'ledger_ids', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
  ]),
  // 2, after the baseline: a sandbox sync's own uploads name their lease (`files.ts`
  // `#endUploads`), so a lease that ends lets go of its sync's open uploads alone, never its
  // client's own writes. No index: the open uploads are at most `MAX_OPEN_INTENTS` rows.
  // `file_intents` is kept out of the export (uploads in flight, `NOT_EXPORTED`), so the
  // column needs no capture trigger.
  Object.freeze([
    "ALTER TABLE file_intents ADD COLUMN lease TEXT CHECK (lease IS NULL OR " +
      "(length(lease) = 32 AND lease NOT GLOB '*[^0-9a-f]*'))",
  ]),
  // 3, after STORE's 2: the starter package (starter.ts): a receipt for each path of the
  // carried package (at most `STARTER_FILES_MAX`, its files and retired paths together; any
  // other path loses its receipt at the next pass), and the one row of the version last
  // reconciled whole, its retry and the version whose failure the owner was told of. Both
  // are the owner's own state, exported (`export.ts`) and so captured into a generation as
  // they change: a restored owner keeps its receipts, so its edited files stay its own.
  Object.freeze([
    "CREATE TABLE starter_receipts (path TEXT PRIMARY KEY CHECK (length(CAST(path AS BLOB)) " +
      "<= 4096), version INTEGER NOT NULL CHECK (version >= 0), class TEXT NOT NULL CHECK " +
      "(class IN ('managed', 'personal')), state TEXT NOT NULL CHECK (state IN ('installed', " +
      "'edited', 'deleted', 'proposed')), installed TEXT CHECK (installed IS NULL OR " +
      "length(installed) = 64), offered TEXT CHECK (offered IS NULL OR length(offered) = 64)) " +
      "WITHOUT ROWID",
    "CREATE TABLE starter_state (only INTEGER PRIMARY KEY CHECK (only = 1), version INTEGER " +
      "NOT NULL CHECK (version >= 0), retry_at REAL, noticed INTEGER)",
    "CREATE TRIGGER starter_receipts_logged_insert AFTER INSERT ON starter_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'starter_receipts', 'row', json_object('path', NEW.path, 'version', NEW.version, 'class', NEW.class, 'state', NEW.state, 'installed', NEW.installed, 'offered', NEW.offered)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER starter_receipts_logged_update AFTER UPDATE ON starter_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'starter_receipts', 'key', json_array(OLD.path)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.path IS NEW.path); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'starter_receipts', 'row', json_object('path', NEW.path, 'version', NEW.version, 'class', NEW.class, 'state', NEW.state, 'installed', NEW.installed, 'offered', NEW.offered)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER starter_receipts_logged_delete AFTER DELETE ON starter_receipts WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'starter_receipts', 'key', json_array(OLD.path)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER starter_state_logged_insert AFTER INSERT ON starter_state WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'starter_state', 'row', json_object('only', NEW.only, 'version', NEW.version, 'retry_at', NEW.retry_at, 'noticed', NEW.noticed)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER starter_state_logged_update AFTER UPDATE ON starter_state WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'starter_state', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.only IS NEW.only); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'starter_state', 'row', json_object('only', NEW.only, 'version', NEW.version, 'retry_at', NEW.retry_at, 'noticed', NEW.noticed)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER starter_state_logged_delete AFTER DELETE ON starter_state WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'starter_state', 'key', json_array(OLD.only)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
  ]),
  // 4, after the starter's 3 (slice BRAIN): which of `apps.json`'s vendors an MCP client is,
  // as its sign-in knew it (`doors/src/apps.ts`): an app's id, verified (1) by its CIMD
  // host or named (0) by its self-declared name; null when none matched, and for an API
  // key. `agents_list` answers the owner's connected apps from it. The table is exported,
  // so its capture triggers that write rows are made again with the two columns.
  Object.freeze([
    "ALTER TABLE outside_clients ADD COLUMN vendor TEXT CHECK (vendor IS NULL OR " +
      "(length(vendor) BETWEEN 1 AND 32 AND vendor NOT GLOB '*[^a-z0-9-]*'))",
    "ALTER TABLE outside_clients ADD COLUMN vendor_verified INTEGER NOT NULL DEFAULT 0 " +
      "CHECK (vendor_verified IN (0, 1) AND (vendor IS NOT NULL OR vendor_verified = 0))",
    "DROP TRIGGER outside_clients_logged_insert",
    "DROP TRIGGER outside_clients_logged_update",
    "CREATE TRIGGER outside_clients_logged_insert AFTER INSERT ON outside_clients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'outside_clients', 'row', json_object('client', NEW.client, 'source', NEW.source, 'mode', NEW.mode, 'scopes', NEW.scopes, 'generation', NEW.generation, 'revoked_at', NEW.revoked_at, 'name', NEW.name, 'first_seen', NEW.first_seen, 'last_seen', NEW.last_seen, 'owner_set', NEW.owner_set, 'agents', NEW.agents, 'held', NEW.held, 'profile', NEW.profile, 'revision', NEW.revision, 'vendor', NEW.vendor, 'vendor_verified', NEW.vendor_verified)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
    "CREATE TRIGGER outside_clients_logged_update AFTER UPDATE ON outside_clients WHEN (SELECT capturing FROM log_capture) = 1 BEGIN " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'gone', 'table', 'outside_clients', 'key', json_array(OLD.client)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168 AND NOT (OLD.client IS NEW.client); " +
      "INSERT INTO change_log (line) SELECT CASE WHEN length(CAST(image AS BLOB)) <= 1572864 THEN image ELSE '{\"t\":\"wide\"}' END FROM (SELECT json_object('t', 'row', 'table', 'outside_clients', 'row', json_object('client', NEW.client, 'source', NEW.source, 'mode', NEW.mode, 'scopes', NEW.scopes, 'generation', NEW.generation, 'revoked_at', NEW.revoked_at, 'name', NEW.name, 'first_seen', NEW.first_seen, 'last_seen', NEW.last_seen, 'owner_set', NEW.owner_set, 'agents', NEW.agents, 'held', NEW.held, 'profile', NEW.profile, 'revision', NEW.revision, 'vendor', NEW.vendor, 'vendor_verified', NEW.vendor_verified)) AS image) WHERE (SELECT capturing FROM log_capture) = 1 AND (SELECT bytes FROM log_capture) < 71303168; " +
      "UPDATE log_capture SET capturing = 0, overflow = 1 WHERE capturing = 1 AND (bytes >= 71303168 OR (SELECT line FROM change_log WHERE seq = log_capture.last) = '{\"t\":\"wide\"}'); END",
  ]),
]);
