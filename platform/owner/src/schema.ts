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
 * entry at the end.
 */

export type Migration = readonly string[];

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
    "CREATE INDEX collection_writers ON collection_rows (collection, writer)",
    "CREATE INDEX collection_creators ON collection_rows (collection, creator)",
    // The high-water seq survives a clear, so a reader's cursor never goes back.
    "CREATE TABLE collection_seqs (collection TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
    "CREATE TABLE collection_history (" +
      "collection TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "op TEXT NOT NULL, body_json TEXT NOT NULL, at REAL NOT NULL, writer TEXT NOT NULL, " +
      "PRIMARY KEY (collection, key, revision))",
    "CREATE INDEX collection_history_writers ON collection_history (collection, writer)",
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
