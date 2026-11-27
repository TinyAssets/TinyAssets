/**
 * The owner's migrations held to their lock, `owner/migrations.json` (`ci/kit/locks.ts`,
 * which says what a lock holds and when a migration is pinned).
 */
import { createHash } from "node:crypto";
import { DatabaseSync } from "node:sqlite";

import { describe, expect, it } from "vitest";

import { exported, NOT_EXPORTED } from "../src/export";
import type { Migration } from "../../contract/src/migrate";
import { FORWARD, MIGRATIONS, unforwardable } from "../src/schema";
import { holdLocks } from "../../ci/kit/locks";
import { workerSources } from "../../ci/kit/sources";
import { LOCKS } from "./locks";

describe("append-only migrations", () => {
  it("hold every migration to its pin", () => {
    expect(holdLocks(LOCKS)).toEqual([]);
  });
});

/**
 * Each place a `REPLACE` could meet an exported table (review L5-2): SQLite's REPLACE
 * conflict resolution deletes the row it replaces with no delete trigger (recursive triggers
 * are off), so the change log would miss that row's `gone` and a replay would bring it back.
 * A statement in a source (`INSERT OR REPLACE INTO`, `REPLACE INTO`, `UPDATE OR REPLACE`),
 * its string pieces joined, or a table whose own constraint resolves `ON CONFLICT REPLACE`.
 */
export function replaceProblems(sources: ReadonlyMap<string, string>,
                                migrations: readonly Migration[],
                                tables: ReadonlySet<string>): string[] {
  const problems: string[] = [];
  // Its target: anything up to a space or a parenthesis. Only a plain literal name of a
  // table not exported passes (review L6-2): a template, a quoted name or a schema prefix
  // could name an exported one, so it fails too.
  const statement = /\b(?:INSERT\s+OR\s+REPLACE\s+INTO|REPLACE\s+INTO|UPDATE\s+OR\s+REPLACE)\s+([^\s(]+)/gi;
  const passes = (target: string) => /^[a-z_][a-z0-9_]*$/.test(target) && !tables.has(target);
  for (const [path, source] of sources) {
    const joined = source.replace(/["'`]\s*\+\s*["'`]/g, "");
    for (const found of joined.matchAll(statement)) {
      if (!passes(found[1]!)) {
        problems.push(`${path}: a REPLACE against ${found[1]}, an exported table or no plain name`);
      }
    }
  }
  for (const sql of migrations.flat()) {
    const table = /^\s*CREATE\s+TABLE\s+([A-Za-z_]+)/i.exec(sql)?.[1];
    if (table !== undefined && tables.has(table) && /ON\s+CONFLICT\s+REPLACE/i.test(sql)) {
      problems.push(`the exported ${table} resolves a conflict by REPLACE`);
    }
    for (const found of sql.matchAll(statement)) {
      if (!passes(found[1]!)) {
        problems.push(`a migration REPLACEs into ${found[1]}, an exported table or no plain name`);
      }
    }
  }
  return problems;
}

describe("no REPLACE against an exported table (review L5-2)", () => {
  const tables = new Set(Object.keys(exported()));

  it("holds for every source and migration", () => {
    expect(replaceProblems(workerSources(), MIGRATIONS, tables)).toEqual([]);
  });

  it("sees each way one would come", () => {
    const at = (source: string) => replaceProblems(new Map([["x.ts", source]]), [], tables);
    expect(at('sql.exec("INSERT OR REPLACE INTO file_entries (parent) VALUES (?)")')).toHaveLength(1);
    expect(at('sql.exec("REPLACE INTO runs (seq) VALUES (1)")')).toHaveLength(1);
    expect(at('sql.exec("UPDATE OR REPLACE outside_clients SET name = ?")')).toHaveLength(1);
    expect(at('sql.exec("INSERT OR " +\n  "REPLACE INTO connections (id) VALUES (?)")')).toHaveLength(1);
    expect(replaceProblems(new Map(), [["CREATE TABLE owner_notices (seq INTEGER PRIMARY KEY " +
      "ON CONFLICT REPLACE)"]], tables)).toHaveLength(1);
    // A target it cannot read as a plain name fails, whatever it names (review L6-2).
    expect(at("sql.exec(`INSERT OR REPLACE INTO ${table} (k) VALUES (?)`)")).toHaveLength(1);
    expect(at('sql.exec("INSERT OR REPLACE INTO \\"schema_digests\\" (k) VALUES (?)")'))
      .toHaveLength(1);
    expect(at('sql.exec("REPLACE INTO main.schema_digests (k) VALUES (?)")')).toHaveLength(1);
    expect(at('sql.exec("UPDATE OR REPLACE [runs] SET k = 1")')).toHaveLength(1);
    expect(replaceProblems(new Map(), [["INSERT OR REPLACE INTO main.runs (seq) VALUES (1)"]],
                           tables)).toHaveLength(1);
    // A table not exported may: the applied migrations' digests are the start's own check.
    expect(at('sql.exec("INSERT OR REPLACE INTO schema_digests (version) VALUES (?)")'))
      .toEqual([]);
  });
});

/**
 * The export's shape (`exported`: each table's columns, key and omitted ones) and the schema
 * it is of. A shape is one schema's: changed with no migration, two generations of one schema
 * would read two ways. Pinned again, with its count, at each new migration.
 */
const EXPORT_SHAPE = { schema: 35, sha256:
  "c33e3a62020f259faf5a52aeadf081843c5e60fd6af8f5c55e69c8d9b9ae9449" };

/**
 * Prepare, without executing, every kind of row change. Updating all writable columns
 * includes UPDATE OF triggers. SQLite compiles the nested trigger programs too.
 * OpenWrite names the b-tree in P2 and the DELETE truncate optimization (Clear) in P1.
 * An index's root belongs to its table, including the primary index of a WITHOUT ROWID table.
 */
function triggerWrites(db: DatabaseSync, table: string): Set<string> {
  const roots = new Map(db.prepare("SELECT rootpage, tbl_name FROM sqlite_schema WHERE rootpage > 0")
    .all().map((row) => [Number(row.rootpage), String(row.tbl_name).toLowerCase()]));
  const quote = (name: string) => '"' + name.replaceAll('"', '""') + '"';
  const name = quote(table);
  const columns = db.prepare("SELECT name FROM pragma_table_xinfo(?) WHERE hidden = 0")
    .all(table).map((column) => quote(String(column.name)));
  const changes = [
    "INSERT INTO " + name + " DEFAULT VALUES",
    "UPDATE " + name + " SET " + columns.map((column) => column + " = " + column).join(", "),
    "DELETE FROM " + name,
  ];
  const targets = new Set<string>();
  for (const change of changes) {
    const touched = new Set<string>();
    for (const instruction of db.prepare("EXPLAIN " + change).iterate()) {
      if (instruction.opcode === "OpenWrite" || instruction.opcode === "Clear") {
        const root = instruction.opcode === "OpenWrite" ? instruction.p2 : instruction.p1;
        const target = roots.get(Number(root));
        if (target === undefined) throw new Error("unknown SQLite write root in " + change);
        touched.add(target);
      }
    }
    if (!touched.has(table)) throw new Error("SQLite did not identify the write to " + table);
    for (const target of touched) targets.add(target);
  }
  return targets;
}

/**
 * Prepare changes against the catalog immediately before and after each migration statement.
 * Dropping a trigger can remove its only route to an exported table. Adding a column named
 * by an existing UPDATE OF trigger can make a previously unreachable write possible.
 */
function migrationObjects(migrations: readonly Migration[], forward: readonly (string | null)[]) {
  const before = new DatabaseSync(":memory:");
  const after = new DatabaseSync(":memory:");
  const exportedTables = new Set(Object.keys(exported()));
  const tables = [...exportedTables, ...Object.keys(NOT_EXPORTED)];
  const catalog = (db: DatabaseSync) => new Map(db.prepare(
    "SELECT type, name, tbl_name, sql FROM sqlite_schema " +
    "WHERE type IN ('trigger', 'index') AND sql IS NOT NULL",
  ).all().map((row) => [`${row.type}:${row.name}`, {
    table: String(row.tbl_name).toLowerCase(), sql: String(row.sql),
  }] as const));
  try {
    const removedTables: Set<string>[] = [];
    const problems: string[] = [];
    migrations.forEach((migration, index) => {
      const removed = new Set<string>();
      removedTables.push(removed);
      const reached = new Set<string>();
      for (const sql of migration) {
        if (forward[index] !== null) {
          before.exec(sql);
          after.exec(sql);
          continue;
        }
        // Let SQLite split the statements, including trigger bodies and trailing comments.
        let remaining = sql + "\n;SELECT 1";
        for (;;) {
          const statement = after.prepare(remaining).sourceSQL;
          remaining = remaining.slice(statement.length);
          if (remaining.length === 0) break;
          const old = catalog(before);
          after.exec(statement);
          const current = catalog(after);
          const named = new Set(tables.filter((table) => new RegExp(`\\b${table}\\b`, "i").test(statement)));
          for (const [key, object] of old) {
            if (current.get(key)?.sql !== object.sql) {
              removed.add(object.table);
              named.add(object.table);
            }
          }
          for (const table of named) {
            reached.add(table);
            for (const db of [before, after]) {
              if (db.prepare("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name=? COLLATE NOCASE")
                .get(table)) {
                for (const target of triggerWrites(db, table)) reached.add(target);
              }
            }
          }
          before.exec(statement);
        }
      }
      for (const table of reached) {
        if (exportedTables.has(table)) problems.push(`migration ${index + 1} names or reaches ${table}`);
      }
    });
    return { removedTables, problems };
  } finally {
    before.close();
    after.close();
  }
}

function forwardChanges(migrations: readonly Migration[], forward: readonly (string | null)[]): string[] {
  return migrationObjects(migrations, forward).problems;
}

describe("every migration says how a generation from before it is brought forward", () => {
  it("one entry a migration, null only where it names or reaches no exported table", () => {
    expect(FORWARD).toHaveLength(MIGRATIONS.length);
    expect(forwardChanges(MIGRATIONS, FORWARD)).toEqual([]);
  });

  it("identifies both direct and indirect changes when only a trigger name is dropped", () => {
    const migrations = [MIGRATIONS[0]!, [
      'DROP TRIGGER main."object_owner_logged_insert"',
      'DROP TRIGGER "file_scope_chunks_removed"',
    ]];
    const changed = forwardChanges(migrations, [FORWARD[0]!, null]);
    expect(changed).toContain("migration 2 names or reaches object_owner");
    expect(changed).toContain("migration 2 names or reaches file_chunks");
  });

  it("checks SQLite table names independently of spelling in a predicate or a write", () => {
    const before = MIGRATIONS[0]!;
    const version = (sql: string) =>
      forwardChanges([before, [sql]], [FORWARD[0]!, null]);
    expect(version('CREATE TRIGGER owner_size BEFORE INSERT ON "Object_Owner" ' +
      "WHEN length(NEW.owner) > 4 BEGIN SELECT RAISE(ABORT, 'size'); END"))
      .toContain("migration 2 names or reaches object_owner");
    expect(version('update "ObJeCt_OwNeR" set owner = upper(owner)'))
      .toContain("migration 2 names or reaches object_owner");
    const changesOwner = 'create trigger indirect after update on "Log_Capture" begin ' +
      'update [Object_Owner] set owner = upper(owner); end';
    expect(forwardChanges([[...before, changesOwner], ["update LOG_CAPTURE set capturing = 0"]],
      [FORWARD[0]!, null])).toContain("migration 2 names or reaches object_owner");
    expect(forwardChanges([[...before, changesOwner], ["drop trigger indirect"]],
      [FORWARD[0]!, null])).toContain("migration 2 names or reaches object_owner");
  });

  it("follows a write with a conflict policy from an internal table to an exported one", () => {
    const before = MIGRATIONS[0]!;
    const migration = ["UPDATE log_capture SET capturing = 0"];
    const changes = (body: string) => forwardChanges([
      [...before, "CREATE TRIGGER on_capture AFTER UPDATE OF capturing ON log_capture BEGIN " +
        body + "; END"], migration,
    ], [FORWARD[0]!, null]);
    expect(changes("SELECT owner FROM object_owner")).toEqual([]);
    expect(changes("UPDATE log_capture SET gen = gen")).toEqual([]);
    expect(changes("UPDATE OR IGNORE object_owner SET owner = upper(owner)"))
      .toContain("migration 2 names or reaches object_owner");
  });

  it("follows a nested insert and delete through a dropped trigger", () => {
    const indirect = "CREATE TRIGGER capture_removed AFTER DELETE ON log_capture BEGIN " +
      "INSERT INTO ledger_marker VALUES (1); END";
    const marker = "CREATE TABLE ledger_marker (value INTEGER)";
    const clear = "CREATE TRIGGER marker_added AFTER INSERT ON ledger_marker BEGIN " +
      "DELETE FROM file_entries; END";
    const before = [...MIGRATIONS[0]!, marker, indirect, clear];
    expect(forwardChanges([before, ["DROP TRIGGER capture_removed"]], [FORWARD[0]!, null]))
      .toContain("migration 2 names or reaches file_entries");
  });

  it("sees an installed UPDATE OF trigger when a later migration adds its column", () => {
    const later = "CREATE TRIGGER future_capture AFTER UPDATE OF future ON log_capture " +
      "BEGIN UPDATE object_owner SET owner = upper(owner); END";
    const migrations = [[...MIGRATIONS[0]!, later], ["ALTER TABLE log_capture ADD COLUMN future INTEGER"]];
    expect(forwardChanges(migrations, [FORWARD[0]!, null]))
      .toContain("migration 2 names or reaches object_owner");
  });

  it("identifies the exported table of a removed request constraint or read index", () => {
    const drop = ['DROP INDEX main."runs_by_request"', 'DROP INDEX collection_changes'];
    const changed = forwardChanges([...MIGRATIONS, drop], [...FORWARD, null]);
    expect(changed).toContain(`migration ${MIGRATIONS.length + 1} names or reaches runs`);
    expect(changed).toContain(`migration ${MIGRATIONS.length + 1} names or reaches collection_rows`);
  });

  it("finds the table of a dropped trigger even when the same migration reinstalls it", () => {
    const create = MIGRATIONS[0]!.find((sql) =>
      sql.startsWith("CREATE TRIGGER object_owner_logged_insert "))!;
    const drop = 'DROP TRIGGER "object_owner_logged_insert"';
    const migrations = [MIGRATIONS[0]!, [drop + "; " + create + "; -- restored before commit"]];
    expect([...migrationObjects(migrations, [FORWARD[0]!, null]).removedTables[1]!]).toEqual(["object_owner"]);
  });

  it("brings a generation forward from schema 31 on (migration 31 keys each private row " +
     "by its writer), and none before it or newer than the code", () => {
    const schemas = Array.from({ length: MIGRATIONS.length + 1 }, (_, at) => at + 1);
    expect(schemas.filter((schema) => unforwardable(schema) === null))
      .toEqual(schemas.filter((schema) => schema >= 31 && schema <= MIGRATIONS.length));
  });

  it("holds the export's shape to the schema it is of", () => {
    expect({ schema: MIGRATIONS.length, sha256: createHash("sha256")
      .update(JSON.stringify(exported())).digest("hex") }).toEqual(EXPORT_SHAPE);
  });
});
