/**
 * The owner object's schema, migrated on wake.
 *
 * `schema_version` holds one row: how many of `MIGRATIONS` this object has applied.
 * The constructor applies the rest in order inside `blockConcurrencyWhile`, each in
 * its own transaction with its version bump, so a crash mid-way leaves a whole number
 * of migrations applied. A stored version past this code's list stops the object
 * loudly: code older than its data never runs against it.
 *
 * A migration is append-only. Once deployed it never changes; a new shape is a new
 * entry at the end. Nothing is deployed yet, so migration 1 is still the first shape.
 *
 * The `CHECK` on a row body is the store's fixed ceiling of 65,536 characters. The
 * primitive's own bound is `LIMITS.maxRow`, which may be lower but never higher (a
 * test ties the two); raising the ceiling is a new migration.
 */

export type Migration = readonly string[];

/** Add a row's or history entry's counts, times `sign`, to each scope it falls in. */
const ADD_USAGE =
  "INSERT INTO collection_usage (scope, collection, who, rows, row_bytes, history_bytes) " +
  "VALUES ";
const ON_USAGE =
  " ON CONFLICT (scope, collection, who) DO UPDATE SET rows = rows + excluded.rows, " +
  "row_bytes = row_bytes + excluded.row_bytes, " +
  "history_bytes = history_bytes + excluded.history_bytes;";
const ROW_BYTES = (row: string) => `length(CAST(${row}.body AS BLOB))`;
const ROW_USAGE = (row: string, sign: string) =>
  ADD_USAGE +
  `('owner', '', '', ${sign}1, ${sign}${ROW_BYTES(row)}, 0), ` +
  `('collection', ${row}.collection, '', ${sign}1, ${sign}${ROW_BYTES(row)}, 0), ` +
  `('writer', ${row}.collection, ${row}.writer, ${sign}1, ${sign}${ROW_BYTES(row)}, 0), ` +
  `('creator', ${row}.collection, ${row}.creator, ${sign}1, ${sign}${ROW_BYTES(row)}, 0)` +
  ON_USAGE;
const HISTORY_BYTES = (entry: string) => `length(CAST(${entry}.body_json AS BLOB))`;
const HISTORY_USAGE = (entry: string, sign: string) =>
  ADD_USAGE +
  `('owner', '', '', 0, 0, ${sign}${HISTORY_BYTES(entry)}), ` +
  `('collection', ${entry}.collection, '', 0, 0, ${sign}${HISTORY_BYTES(entry)}), ` +
  `('writer', ${entry}.collection, ${entry}.writer, 0, 0, ${sign}${HISTORY_BYTES(entry)})` +
  ON_USAGE;

export const MIGRATIONS: readonly Migration[] = Object.freeze([
  // 1: the owner this object serves, and the one revision primitive (S34, ADR-032).
  Object.freeze([
    "CREATE TABLE object_owner (only INTEGER PRIMARY KEY CHECK (only = 1), owner TEXT NOT NULL)",
    "CREATE TABLE collection_rows (" +
      "collection TEXT NOT NULL, row_id TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "seq INTEGER NOT NULL, etag TEXT NOT NULL, writer TEXT NOT NULL, " +
      "private INTEGER NOT NULL CHECK (private IN (0, 1)), " +
      "body TEXT NOT NULL CHECK (length(body) <= 65536), updated_at REAL NOT NULL, " +
      // Who created the row; a takeover never changes it (the row share counts it).
      "creator TEXT NOT NULL, " +
      "PRIMARY KEY (collection, row_id), UNIQUE (collection, seq))",
    // A clear by writer matches either.
    "CREATE INDEX collection_writers ON collection_rows (collection, writer)",
    "CREATE INDEX collection_creators ON collection_rows (collection, creator)",
    // The high-water seq survives a clear, so a reader's cursor never goes back.
    "CREATE TABLE collection_seqs (collection TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
    "CREATE TABLE collection_history (" +
      "collection TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "op TEXT NOT NULL, body_json TEXT NOT NULL, at REAL NOT NULL, writer TEXT NOT NULL, " +
      "PRIMARY KEY (collection, key, revision))",
    // The bounds' counters: rows and payload bytes in the whole object (the owner's),
    // per collection, and per writer and per creator in a collection. Only the
    // triggers below write them, inside the statement that changes a row or a history
    // entry, so every write, delete, clear and history prune moves them in its own
    // transaction, and a bound check reads one row by key. A history entry counts
    // against its writer; `creator` counts rows alone. A row that falls to zero goes.
    "CREATE TABLE collection_usage (" +
      "scope TEXT NOT NULL CHECK (scope IN ('owner', 'collection', 'writer', 'creator')), " +
      "collection TEXT NOT NULL, who TEXT NOT NULL, rows INTEGER NOT NULL, " +
      "row_bytes INTEGER NOT NULL, history_bytes INTEGER NOT NULL, " +
      "PRIMARY KEY (scope, collection, who))",
    `CREATE TRIGGER collection_rows_added AFTER INSERT ON collection_rows BEGIN ${
      ROW_USAGE("NEW", "")} END`,
    `CREATE TRIGGER collection_rows_changed AFTER UPDATE ON collection_rows BEGIN ${
      ROW_USAGE("OLD", "-")} ${ROW_USAGE("NEW", "")} END`,
    `CREATE TRIGGER collection_rows_removed AFTER DELETE ON collection_rows BEGIN ${
      ROW_USAGE("OLD", "-")} END`,
    // An erase entry has no body and counts nothing.
    `CREATE TRIGGER collection_history_added AFTER INSERT ON collection_history ` +
      `WHEN ${HISTORY_BYTES("NEW")} > 0 BEGIN ${HISTORY_USAGE("NEW", "")} END`,
    `CREATE TRIGGER collection_history_removed AFTER DELETE ON collection_history ` +
      `WHEN ${HISTORY_BYTES("OLD")} > 0 BEGIN ${HISTORY_USAGE("OLD", "-")} END`,
    "CREATE TRIGGER collection_history_kept BEFORE UPDATE ON collection_history " +
      "BEGIN SELECT RAISE(ABORT, 'a history entry is never changed'); END",
    "CREATE TRIGGER collection_usage_emptied AFTER UPDATE ON collection_usage " +
      "WHEN NEW.rows = 0 AND NEW.row_bytes = 0 AND NEW.history_bytes = 0 BEGIN " +
      "DELETE FROM collection_usage WHERE scope = NEW.scope AND collection = NEW.collection " +
      "AND who = NEW.who; END",
  ]),
]);

/** Apply every migration past the stored version; return the version now stored. */
export function migrate(
  storage: DurableObjectStorage,
  migrations: readonly Migration[] = MIGRATIONS,
): number {
  const sql = storage.sql;
  sql.exec(
    "CREATE TABLE IF NOT EXISTS schema_version " +
      "(only INTEGER PRIMARY KEY CHECK (only = 1), version INTEGER NOT NULL)",
  );
  const stored = sql.exec<{ version: number }>("SELECT version FROM schema_version").toArray();
  const current = stored[0]?.version ?? 0;
  if (current > migrations.length) {
    throw new Error(
      `schema version ${current} is newer than this code's ${migrations.length}; refusing to run`,
    );
  }
  for (let version = current + 1; version <= migrations.length; version++) {
    const statements = migrations[version - 1] ?? [];
    storage.transactionSync(() => {
      for (const statement of statements) {
        sql.exec(statement);
      }
      sql.exec(
        "INSERT INTO schema_version (only, version) VALUES (1, ?) " +
          "ON CONFLICT (only) DO UPDATE SET version = excluded.version",
        version,
      );
    });
  }
  return migrations.length;
}
