/**
 * The change log (slice P, `changelog.ts`): while a generation runs, every exported row's
 * change writes its image, exactly as the export reads the row, in the writing unit's own
 * transaction; nothing with capture off; past `LOG_BYTES` (or an image past `IMAGE_MAX`) the
 * write lands and the generation fails with one notice.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { MANIFEST_LOG_OVERFLOW } from "../src/backup";
import { ChangeLog, IMAGE_MAX, LOG_BYTES, LOG_WIDE } from "../src/changelog";
import { exported } from "../src/export";
import { INLINE_BYTES } from "../src/files";
import { WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { LOGGED } from "../src/schema";
import { digest, generate, type Line, owner, replay, standing } from "./generation-support";
import { filesOn, sqlIn } from "./support";

const W = "/workspace";
const text = (body: string) => new TextEncoder().encode(body);

/**
 * The segment build `ChangeLog.segment` replaced (review rvburst V1), verbatim but for its
 * rows: every kept line held as a string, then joined and encoded. The reference for its
 * bytes.
 */
function replacedSegment(rows: readonly { seq: number; line: string }[]): Uint8Array {
  const tables = exported();
  const kept = new Map<string, string>();
  for (const row of rows) {
    const line = JSON.parse(row.line) as { t: string; table: string;
                                           row?: Record<string, unknown>; key?: unknown[] };
    const shape = tables[line.table]!;
    const key = line.t === "row" ? shape.key.map((column) => line.row![column]) : line.key!;
    if (line.t === "row" && line.table === "file_contents"
        && typeof line.row!["inline"] === "string") {
      line.row!["inline"] = {
        $base64: Buffer.from(line.row!["inline"] as string, "hex").toString("base64") };
    }
    const id = JSON.stringify([line.table, key]);
    kept.delete(id);
    kept.set(id, JSON.stringify({ ...line, seq: row.seq }));
  }
  return new TextEncoder().encode([...kept.values()].map((line) => `${line}\n`).join(""));
}

describe("a log segment's build", () => {
  it("makes byte for byte the segment the build it replaced made, every line's last per key, " +
     "of any characters", async () => {
    const o = await owner();
    const made = await runInDurableObject(o.stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      const chars = ["a", "Z", " ", "\"", "\\", "\n", "\u0001", "é", "€", "語", "😀", "\ud800"];
      let seed = 7;
      const next = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32;
      const textOf = (n: number) => Array.from({ length: n },
                                                () => chars[Math.floor(next() * chars.length)])
        .join("");
      // Rows, gone lines and inline contents, the same few keys again and again.
      for (let n = 0; n < 400; n++) {
        const pick = next();
        const line = pick < 0.6
          ? { t: "row", table: "owner_notices",
              row: { seq: Math.floor(next() * 40), at: n, type: "x",
                     detail: textOf(Math.floor(next() * 300)) } }
          : pick < 0.8 ? { t: "gone", table: "owner_notices", key: [Math.floor(next() * 40)] }
          : { t: "row", table: "file_contents",
              row: { content_id: `c${Math.floor(next() * 5)}`, size: n,
                     inline: Buffer.from(textOf(20)).toString("hex"), refs: 1 } };
        sql.exec("INSERT INTO change_log (line) VALUES (?)", JSON.stringify(line));
      }
      const log = new ChangeLog(state.storage);
      const rows = sql.exec<{ seq: number; line: string }>(
        "SELECT seq, line FROM change_log ORDER BY seq").toArray();
      const first = rows[0]!.seq - 1;
      return [[first, first + 400], [first + 17, first + 233], [first + 399, first + 400]]
        .map(([a, b]) => {
          const built = log.segment(a!, b!);
          const reference = replacedSegment(rows.filter((row) => row.seq > a! && row.seq <= b!));
          return Buffer.compare(Buffer.from(built), Buffer.from(reference));
        });
    });
    expect(made).toEqual([0, 0, 0]);
  });
});

describe("the change log", () => {
  it("has one trigger an exported table and event, its image every exported column and " +
     "its key", async () => {
    const o = await owner();
    const triggers = await sqlIn(o.stub, "SELECT name, tbl_name, sql FROM sqlite_master " +
                                         "WHERE type = 'trigger' AND name GLOB '*_logged_*'");
    const tables = exported();
    expect([...new Set(triggers.map((row) => row["tbl_name"] as string))].sort())
      .toEqual(Object.keys(tables).sort());
    expect(triggers).toHaveLength(3 * Object.keys(tables).length);
    for (const [table, { columns, key, omitted }] of Object.entries(tables)) {
      const of = (event: string) => triggers.find((row) =>
        row["name"] === `${table}_logged_${event}`)!["sql"] as string;
      for (const column of columns) {
        expect(of("insert")).toContain(`'${column}', `);
      }
      for (const column of Object.keys(omitted ?? {})) {
        expect(of("insert")).not.toContain(`'${column}', `);
      }
      const keyed = (row: string) => `json_array(${key.map((column) => `${row}.${column}`)
        .join(", ")})`;
      expect(of("delete")).toContain(keyed("OLD"));
      expect(of("update")).toContain(keyed("OLD"));
      expect(of("update")).toContain("NEW.");
    }
  });

  it("writes each change's image as the export reads the row: a key moved, a BLOB, a delete",
     async () => {
    const o = await owner();
    const out = await runInDurableObject(o.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      const log = new ChangeLog(gate.storage);
      const files = filesOn(gate.storage, gate.write);
      await gate.write(() => log.begin("0123456789abcdef"));
      await files.write(`${W}/a.bin`, Uint8Array.from([0, 1, 2, 250, 251]),
                        { expected: null, writer: "owner" });
      await files.write(`${W}/b.txt`, text("second"), { expected: null, writer: "owner" });
      await gate.write(() => files.rename(`${W}/b.txt`, `${W}/c.txt`,
                                          { expected: null, replace: false, writer: "owner" }));
      await gate.write(() => files.remove(`${W}/a.bin`, { expected: null, writer: "owner" }));
      await gate.write(() => gate.storage.sql.exec(
        "UPDATE owner_notices SET detail = 'x' WHERE 0"));  // touches nothing, logs nothing
      const { last, s } = log.state();
      const lines = new TextDecoder().decode(log.segment(s - 1, last)).trimEnd().split("\n")
        .map((line) => JSON.parse(line) as Line);
      return { lines, now: standing(state.storage.sql) };
    });
    // Every image is the row as it stands now, or gone with it: the export's own form.
    const replayed = replay(out.lines);
    for (const [table, rows] of Object.entries(replayed)) {
      for (const [key, row] of Object.entries(rows)) {
        expect([table, key, JSON.parse(row)]).toEqual([table, key,
                                                      JSON.parse(out.now[table]![key]!)]);
      }
    }
    expect(out.lines.some((line) => line.t === "gone" && line["table"] === "file_entries"
      && (line["key"] as string[])[1] === "b.txt")).toBe(true);
    expect(out.lines.some((line) => line.t === "row" && line["table"] === "file_contents"
      && typeof (line["row"] as { inline: unknown }).inline === "object")).toBe(true);
    expect(out.lines.every((line) => typeof line["seq"] === "number")).toBe(true);
  });

  it("writes nothing while no generation captures", async () => {
    const o = await owner();
    await o.stub.fileWrite(o.bind(), { path: `${W}/quiet.txt`, expected_etag: null,
                                       content: text("quiet") });
    expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM change_log")).toEqual([{ n: 0 }]);
  });

  it("past LOG_BYTES lands the write and fails the generation with one notice", async () => {
    for (const how of ["full"] as const) {
      const o = await owner();
      await sqlIn(o.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
        "WHERE i < 200) INSERT INTO owner_notices (at, type, detail) SELECT 0, 'x', " +
        "printf('%.1000c', 'a') FROM n");
      const made = await generate(o.stub, {
        duringCall: async ({ gate, n }) => {
          if (n !== 1) {
            return;
          }
          // As R2 refused puts long enough.
          await gate.write(() => {
            gate.storage.sql.exec("UPDATE log_capture SET bytes = ?", LOG_BYTES);
            gate.storage.sql.exec("INSERT INTO owner_notices (at, type, detail) VALUES " +
                                  "(1, 'agent', 'landed')");
          });
        },
      });
      expect([how, made.notices]).toEqual([how, [MANIFEST_LOG_OVERFLOW]]);
      // The write that passed it landed.
      expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM owner_notices WHERE type = " +
                                 "'agent'")).toEqual([{ n: 1 }]);
      expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM owner_backups WHERE state = " +
                                 "'writing'")).toEqual([{ n: 0 }]);
      expect(await sqlIn(o.stub, "SELECT capturing, gen FROM log_capture"))
        .toEqual([{ capturing: 0, gen: null }]);
    }
  }, 120_000);

  it("logs a connection's widest row whole: its token refresh fails no generation (review " +
     "L5-1)", async () => {
    const o = await owner();
    await sqlIn(o.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
      "WHERE i < 200) INSERT INTO owner_notices (at, type, detail) SELECT 0, 'x', " +
      "printf('%.1000c', 'a') FROM n");
    // Its models at their ceiling in bytes, every one a character JSON escapes, and its
    // egress scope at its own.
    const models = '"'.repeat(655_360);
    await sqlIn(o.stub, "INSERT INTO connections (id, source, kind, display_name, provider, " +
      "models, status, version, created_at, revocable, egress) VALUES (?, 'src', 'oauth2', " +
      "'wide', 'p', ?, 'active', 1, 0, 1, ?)", "c".repeat(25), models, "\\".repeat(16_384));
    let refreshed = 0;
    const made = await generate(o.stub, {
      duringCall: async ({ gate }) => {
        // A refresh's claim, then its new token's expiry, as `connections.ts` writes them.
        await gate.write(() => gate.storage.sql.exec(
          "UPDATE connections SET lease_id = ?, lease_until = ?, expires_at = ? WHERE id = ?",
          `lease-${++refreshed}`, refreshed, 1_000 + refreshed, "c".repeat(25)));
      },
    });
    expect(refreshed).toBeGreaterThan(1);
    expect(made.notices).toEqual([]);
    expect(digest(replay(made.lines))).toBe(made.atE);
    const images = made.lines.filter((line) => line.t === "row" && line.table === "connections");
    expect(images.length).toBeGreaterThan(1);
    expect(made.lines.some((line) => (line as { t: string }).t === "wide")).toBe(false);
  }, 120_000);

  it("marks an image past IMAGE_MAX, the backstop: the write lands and the generation fails",
     async () => {
    const o = await owner();
    await runInDurableObject(o.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      const sql = gate.storage.sql;
      const log = new ChangeLog(gate.storage);
      await gate.write(() => {
        // A table no exported one is (the guard holds every exported row's image under it).
        sql.exec("CREATE TABLE wide_probe (k TEXT PRIMARY KEY, v TEXT)");
        sql.exec(LOGGED("wide_probe", "k v", "k", "INSERT"));
        log.begin("0123456789abcdef");
      });
      await gate.write(() => sql.exec("INSERT INTO wide_probe (k, v) VALUES ('w', ?)",
                                      "a".repeat(IMAGE_MAX)));
      expect(sql.exec("SELECT COUNT(*) AS n FROM wide_probe").one()).toEqual({ n: 1 });
      expect(sql.exec("SELECT line FROM change_log").toArray()).toEqual([{ line: LOG_WIDE }]);
      expect(sql.exec("SELECT capturing, overflow FROM log_capture").one())
        .toEqual({ capturing: 0, overflow: 1 });
    });
    expect(LOG_WIDE).toBe('{"t":"wide"}');
  });

  it("keeps small files' bytes in their image under the BLOB's size bound", () => {
    expect(INLINE_BYTES * 2).toBeLessThan(IMAGE_MAX);
  });
});
