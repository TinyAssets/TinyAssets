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

/** SQLite owns the table names for triggers and indexes, including drops that name only the object. */
function migrationObjects(migrations: readonly Migration[], forward: readonly (string | null)[]) {
  const db = new DatabaseSync(":memory:");
  try {
    const read = db.prepare("SELECT type, name, tbl_name, sql FROM sqlite_schema " +
                            "WHERE type IN ('trigger', 'index') AND sql IS NOT NULL");
    const triggers = new Map<string, string>();
    const objects = () => new Map(read.all().map((row) => {
      const table = String(row.tbl_name).toLowerCase();
      const sql = String(row.sql);
      if (row.type === "trigger") {
        triggers.set(sql, table);
      }
      return [`${row.type}:${row.name}`, { table, sql }] as const;
    }));
    const removedTables = migrations.map((migration, index) => {
      const removed = new Set<string>();
      if (forward[index] !== null) {
        for (const sql of migration) {
          db.exec(sql);
        }
        return removed;
      }
      let before = objects();
      for (const sql of migration) {
        // SQLite splits the statements, including a trigger body with internal semicolons.
        // A trailing SELECT gives whitespace and comments a final harmless statement.
        let remaining = sql + "\n;SELECT 1";
        for (;;) {
          const statement = db.prepare(remaining).sourceSQL;
          remaining = remaining.slice(statement.length);
          if (remaining.length === 0) {
            break;
          }
          db.exec(statement);
          const after = objects();
          for (const [name, object] of before) {
            if (after.get(name)?.sql !== object.sql) {
              removed.add(object.table);
            }
          }
          before = after;
        }
      }
      return removed;
    });
    return { removedTables, triggers };
  } finally {
    db.close();
  }
}

function forwardChanges(migrations: readonly Migration[], forward: readonly (string | null)[]): string[] {
  const exportedTables = new Set(Object.keys(exported()));
  const tables = [...exportedTables, ...Object.keys(NOT_EXPORTED)];
  // Each table's triggers' writes, from every trigger a migration made: a write to a table
  // reaches what they write, as `file_scope_chunks_removed` reaches `file_chunks`.
  const { removedTables, triggers } = migrationObjects(migrations, forward);
  const writes = new Map<string, string[]>();
  for (const [trigger, table] of triggers) {
    // The body can quote a table name and use any case for keywords or identifiers.
    const body = trigger.slice(trigger.search(/\bbegin\b/i));
    const targets = body.matchAll(/\b(?:update|into|delete\s+from)\s+["`\[]?(\w+)/gi);
    const names = Array.from(targets, (write) => write[1]!.toLowerCase());
    writes.set(table, [...writes.get(table) ?? [], ...names]);
  }
  const reached = (from: string[]): Set<string> => {
    const seen = new Set(from);
    for (const table of seen) {
      (writes.get(table) ?? []).forEach((write) => seen.add(write));
    }
    return seen;
  };
  return migrations.flatMap((migration, at) => forward[at] !== null ? []
    : [...reached(tables.filter((table) => removedTables[at]!.has(table) || migration.some((sql) =>
        new RegExp(`\\b${table}\\b`, "i").test(sql))))].filter((table) => exportedTables.has(table))
      .map((table) => `migration ${at + 1} names or reaches ${table}`));
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
