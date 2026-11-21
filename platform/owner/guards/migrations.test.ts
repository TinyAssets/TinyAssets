/**
 * The owner's migrations held to their lock, `owner/migrations.json` (`ci/kit/locks.ts`,
 * which says what a lock holds and how a deploy pins it).
 */
import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { exported, NOT_EXPORTED } from "../src/export";
import type { Migration } from "../../contract/src/migrate";
import { FORWARD, MIGRATIONS, unforwardable } from "../src/schema";
import { holdLocks } from "../../ci/kit/locks";
import { workerSources } from "../../ci/kit/sources";
import { LOCKS } from "./locks";

describe("append-only migrations", () => {
  it("hold every deployed migration to its pinned text", () => {
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
const EXPORT_SHAPE = { schema: 18, sha256:
  "15f28c74a9c665073bd86deed1f6f4556e0cb3df9f5937298e24bb47074d97bf" };

describe("every migration says how a generation from before it is brought forward", () => {
  it("one entry a migration, null only where its text names no exported table, nor reaches " +
     "one through a trigger", () => {
    expect(FORWARD).toHaveLength(MIGRATIONS.length);
    const exportedTables = new Set(Object.keys(exported()));
    const tables = [...exportedTables, ...Object.keys(NOT_EXPORTED)];
    // Each table's triggers' writes, from every trigger a migration made: a write to a table
    // reaches what they write, as `file_scope_chunks_removed` reaches `file_chunks`.
    const writes = new Map<string, string[]>();
    for (const sql of MIGRATIONS.flat()) {
      const on = /^CREATE TRIGGER \w+ [^;]*? ON (\w+)/.exec(sql)?.[1];
      if (on !== undefined) {
        writes.set(on, [...writes.get(on) ?? [], ...[...sql.slice(sql.indexOf(" BEGIN "))
          .matchAll(/\b(?:UPDATE|INTO|DELETE FROM)\s+(\w+)/g)].map((write) => write[1]!)]);
      }
    }
    const reached = (from: string[]): Set<string> => {
      const seen = new Set(from);
      for (const table of seen) {
        (writes.get(table) ?? []).forEach((write) => seen.add(write));
      }
      return seen;
    };
    const named = MIGRATIONS.flatMap((migration, at) => FORWARD[at] !== null ? []
      : [...reached(tables.filter((table) => migration.some((sql) =>
          new RegExp(`\\b${table}\\b`).test(sql))))].filter((table) => exportedTables.has(table))
        .map((table) => `migration ${at + 1} names or reaches ${table}`));
    expect(named).toEqual([]);
  });

  it("brings a generation forward from schema 9 on, and none before it or newer than the code",
     () => {
    const schemas = Array.from({ length: MIGRATIONS.length + 1 }, (_, at) => at + 1);
    expect(schemas.filter((schema) => unforwardable(schema) === null))
      .toEqual(schemas.filter((schema) => schema >= 9 && schema <= MIGRATIONS.length));
  });

  it("holds the export's shape to the schema it is of", () => {
    expect({ schema: MIGRATIONS.length, sha256: createHash("sha256")
      .update(JSON.stringify(exported())).digest("hex") }).toEqual(EXPORT_SHAPE);
  });
});
