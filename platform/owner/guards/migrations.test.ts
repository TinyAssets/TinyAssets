/**
 * The owner's migrations held to their lock, `owner/migrations.json` (`ci/kit/locks.ts`,
 * which says what a lock holds and how a deploy pins it).
 */
import { describe, expect, it } from "vitest";

import { exported } from "../src/export";
import type { Migration } from "../../contract/src/migrate";
import { MIGRATIONS } from "../src/schema";
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
