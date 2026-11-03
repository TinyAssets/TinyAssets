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
import { generate, type Line, owner, replay, standing } from "./generation-support";
import { filesOn, sqlIn } from "./support";

const W = "/workspace";
const text = (body: string) => new TextEncoder().encode(body);

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

  it("past LOG_BYTES, or an image past IMAGE_MAX, lands the write and fails the generation " +
     "with one notice", async () => {
    for (const how of ["full", "wide"] as const) {
      const o = await owner();
      await sqlIn(o.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
        "WHERE i < 9000) INSERT INTO owner_notices (at, type, detail) SELECT 0, 'x', " +
        "printf('%.1000c', 'a') FROM n");
      const made = await generate(o.stub, {
        duringCall: async ({ gate, n }) => {
          if (n !== 1) {
            return;
          }
          // As R2 refused puts long enough, or a row too wide to log.
          await gate.write(() => {
            if (how === "full") {
              gate.storage.sql.exec("UPDATE log_capture SET bytes = ?", LOG_BYTES);
            }
            gate.storage.sql.exec("INSERT INTO owner_notices (at, type, detail) VALUES " +
                                  "(1, 'agent', 'landed')");
            if (how === "wide") {
              // A connection's models list near its ceiling, every character escaped.
              gate.storage.sql.exec("INSERT INTO connections (id, source, kind, display_name, " +
                "provider, models, status, version, created_at, revocable) VALUES (?, 'src', " +
                "'api_key', 'wide', 'p', ?, 'active', 1, 0, 0)", "c".repeat(25),
                                    '"'.repeat(IMAGE_MAX / 2 + 1));
            }
          });
        },
      });
      expect([how, made.notices]).toEqual([how, [MANIFEST_LOG_OVERFLOW]]);
      // The write that passed it landed.
      expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM owner_notices WHERE type = " +
                                 "'agent'")).toEqual([{ n: 1 }]);
      expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM connections"))
        .toEqual([{ n: how === "wide" ? 1 : 0 }]);
      expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM owner_backups WHERE state = " +
                                 "'writing'")).toEqual([{ n: 0 }]);
      expect(await sqlIn(o.stub, "SELECT capturing, gen FROM log_capture"))
        .toEqual([{ capturing: 0, gen: null }]);
      expect(LOG_WIDE).toBe('{"t":"wide"}');
    }
  }, 120_000);

  it("keeps small files' bytes in their image under the BLOB's size bound", () => {
    expect(INLINE_BYTES * 2).toBeLessThan(IMAGE_MAX);
  });
});
