/**
 * The runner's on-disk format and its rollback rule (`state-format.ts`): what this runner
 * writes is exactly its format's record (`state-format.json`), and it reads every format it
 * says it reads, as recorded. The runner one deploy back passed the same reading test against
 * the same record, so it reads what this one writes while the format is the same; and a
 * change of format must bump `STATE_FORMAT`, record its schema, and keep reading the one
 * before (expand, then contract).
 */
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { afterEach, describe, expect, it } from "vitest";

import { checkFormat, READS, STATE_FORMAT } from "./state-format";
import { openMirror } from "./sync";

interface Format {
  /** The sync mirror's schema, as SQLite keeps it (`sqlite_master`, by name). */
  readonly mirror: readonly { type: string; name: string; sql: string | null }[];
}

const RECORD = JSON.parse(readFileSync(join(import.meta.dirname, "state-format.json"), "utf8")) as
  Record<string, Format>;

const dirs: string[] = [];
function temp(): string {
  const dir = mkdtempSync(join(tmpdir(), "ta-format-"));
  dirs.push(dir);
  return dir;
}
afterEach(() => {
  for (const dir of dirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function schema(path: string): Format["mirror"] {
  const db = new DatabaseSync(path);
  try {
    return db.prepare("SELECT type, name, sql FROM sqlite_master ORDER BY name").all()
      .map((row) => ({ type: String(row["type"]), name: String(row["name"]),
                       sql: row["sql"] === null ? null : String(row["sql"]) }));
  } finally {
    db.close();
  }
}

describe("the runner's on-disk format", () => {
  it("is expand-then-contract: it reads its own and at most the one before", () => {
    expect(READS).toContain(STATE_FORMAT);
    expect(READS.every((format) => format === STATE_FORMAT || format === STATE_FORMAT - 1))
      .toBe(true);
    for (const format of READS) {
      expect(RECORD[String(format)], `format ${format} has no record`).toBeDefined();
    }
  });

  it("is written exactly as its format's record says", () => {
    const path = join(temp(), "mirror.db");
    const mirror = openMirror(path);
    mirror.put("/workspace/a.md", { version: 3, size: 5, mtime_ms: 1, sha256: "0".repeat(64) });
    mirror.setCursor(7);
    mirror.close();
    expect(schema(path)).toEqual(RECORD[String(STATE_FORMAT)]!.mirror);
  });

  it("reads every format it reads, as each was recorded", () => {
    for (const format of READS) {
      const path = join(temp(), "mirror.db");
      const db = new DatabaseSync(path);
      for (const entry of RECORD[String(format)]!.mirror) {
        if (entry.sql !== null) {
          db.exec(entry.sql);
        }
      }
      db.exec("INSERT INTO meta VALUES (1, 7); INSERT INTO files VALUES " +
              `('/workspace/a.md', 3, 5, 1, '${"0".repeat(64)}')`);
      db.close();
      const mirror = openMirror(path);
      expect(mirror.cursor()).toBe(7);
      expect(mirror.get("/workspace/a.md")).toEqual({ version: 3, size: 5, mtime_ms: 1,
                                                      sha256: "0".repeat(64) });
      mirror.close();
    }
  });

  it("is marked on a new sandbox, and kept with its caches while it stands", () => {
    const state = temp();
    const cache = join(state, "sessions-seen.json");
    checkFormat(state, [cache]);
    expect(readFileSync(join(state, "format"), "utf8")).toBe(`${STATE_FORMAT}\n`);
    writeFileSync(cache, "{}");
    checkFormat(state, [cache]);
    expect(existsSync(cache)).toBe(true);
  });

  it("discards only the caches when it moves on from the format before", () => {
    const state = temp();
    const cache = join(state, "sessions-seen.json");
    const kept = join(state, "generations");
    writeFileSync(join(state, "format"), `${STATE_FORMAT - 1}\n`);
    writeFileSync(cache, "{}");
    writeFileSync(kept, "3");
    if (READS.includes(STATE_FORMAT - 1)) {
      checkFormat(state, [cache]);
      expect(existsSync(cache)).toBe(false);
      expect(readFileSync(kept, "utf8")).toBe("3");
      expect(readFileSync(join(state, "format"), "utf8")).toBe(`${STATE_FORMAT}\n`);
    } else {
      expect(() => checkFormat(state, [cache])).toThrow(/does not read|reads/);
      expect(existsSync(cache)).toBe(true);
    }
  });

  it("refuses loudly a format newer than it reads, and changes nothing", () => {
    const state = temp();
    const cache = join(state, "sessions-seen.json");
    writeFileSync(join(state, "format"), `${STATE_FORMAT + 1}\n`);
    writeFileSync(cache, "{}");
    expect(() => checkFormat(state, [cache])).toThrow(/format 2|format/);
    expect(existsSync(cache)).toBe(true);
    expect(readFileSync(join(state, "format"), "utf8")).toBe(`${STATE_FORMAT + 1}\n`);
  });
});
