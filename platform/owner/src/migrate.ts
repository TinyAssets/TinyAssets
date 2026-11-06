/**
 * How a Durable Object's schema migrates on wake, for every object that keeps tables (the
 * owner's, `schema.ts`; the sandbox Worker's two, `sandbox/src/schema.ts`). Pure: it imports
 * nothing of any object, so a Worker takes it without the owner's storage modules.
 *
 * `schema_version` holds one row: how many of the object's migrations it has applied. The
 * rest are applied in order, each in its own transaction with its version bump, so a crash
 * mid-way leaves a whole number of migrations applied. A stored version past the code's
 * list stops the object loudly: code older than its data never runs against it.
 *
 * A migration is append-only from its Worker's first deploy: a new shape is a new entry at
 * the end, held by the Worker's migration lock and its migrations guard.
 */
import { createHash } from "node:crypto";

export type Migration = readonly string[];

/** What `migrate` needs of an object's storage. */
export interface MigrationStorage {
  readonly sql: Pick<SqlStorage, "exec">;
  transactionSync<T>(closure: () => T): T;
}

/** A migration's digest: SHA-256 over its statements as JSON, as the code evaluates them. */
export function migrationDigest(migration: Migration): string {
  return createHash("sha256").update(JSON.stringify(migration)).digest("hex");
}

/** What a start answers when a migration it applied before reads differently now. */
export const MIGRATION_CHANGED = "an applied migration's text changed since it ran; refusing to run";
/** What a start answers when a migration it applied has no digest to check it against. */
export const MIGRATION_UNPINNED = "an applied migration has no digest; refusing to run";

/**
 * Apply every migration past the stored version; return the version now stored. Each
 * applied migration's digest is kept beside the version (`schema_digests`), and every start
 * checks the migrations it already applied against them first, as Flyway's `validate`
 * does: one whose text changed since it ran (a hotfix branch, a revert, a squash after the
 * first deploy) stops the object loudly before anything runs, whatever the repo's guard
 * missed; so does one whose digest row is gone (Fact 8: the check never passes by default).
 */
export function migrate(storage: MigrationStorage, migrations: readonly Migration[]): number {
  const sql = storage.sql;
  sql.exec(
    "CREATE TABLE IF NOT EXISTS schema_version " +
      "(only INTEGER PRIMARY KEY CHECK (only = 1), version INTEGER NOT NULL)",
  );
  sql.exec("CREATE TABLE IF NOT EXISTS schema_digests (version INTEGER PRIMARY KEY, " +
             "sha256 TEXT NOT NULL CHECK (length(sha256) = 64))");
  const stored = sql.exec<{ version: number }>("SELECT version FROM schema_version").toArray();
  const current = stored[0]?.version ?? 0;
  if (current > migrations.length) {
    throw new Error(
      `schema version ${current} is newer than this code's ${migrations.length}; refusing to run`,
    );
  }
  const applied = new Map(sql.exec<{ version: number; sha256: string }>(
    "SELECT version, sha256 FROM schema_digests").toArray()
    .map((row) => [row.version, row.sha256]));
  for (let version = 1; version <= current; version++) {
    const held = applied.get(version);
    if (held === undefined) {
      throw new Error(`${MIGRATION_UNPINNED} (migration ${version})`);
    }
    if (held !== migrationDigest(migrations[version - 1]!)) {
      throw new Error(`${MIGRATION_CHANGED} (migration ${version})`);
    }
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
      sql.exec("INSERT OR REPLACE INTO schema_digests (version, sha256) VALUES (?, ?)", version,
               migrationDigest(statements));
    });
  }
  return migrations.length;
}
