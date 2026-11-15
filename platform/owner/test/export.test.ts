/**
 * The owner export and the nightly manifest, one artifact (slice P; `export.ts`, `backup.ts`,
 * `changelog.ts`): a generation, a fuzzy base copy and the change log captured while it was
 * read, consistent at its end E however the owner writes meanwhile; resumable from any
 * cursor, the owner's alone; written each night by the owner's own schedule, and gone with
 * the owner's key at erasure.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, vi } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { rewrap } from "../../files/src/datakey";
import { manifestKey } from "../../files/src/keys";
import {
  CHUNK_BYTES, GC_GRACE_S, INLINE_BYTES, LOG_BYTES, MANIFEST_PARTS, PART_ENTRY, SEGMENT_ENTRY,
} from "../../contract/src/limits";
import { PLATFORM_ONLY } from "../src/admission";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import {
  GENERATION_SIZES, MANIFEST_BUSY_RETRY_S, MANIFEST_CEILING, MANIFEST_CONTINUE_S, spread,
} from "../src/backup";
import {
  BURST_UNITS, OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, ownerSlots, SHIP_UNITS,
} from "../src/holds";
import {
  decodeCursor, encodeLines, EXPORT_LIFE_S, EXPORT_PAGE_BYTES, exported, NOT_EXPORTED,
  readPage,
} from "../src/export";
import type FilesService from "../../files/src/index";
import type { ChunkStore } from "../../contract/src/files";
import { CHUNK_GONE, type Files } from "../src/files";
import type { WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { MIGRATIONS } from "../src/schema";
import {
  type At, BURST, digest, exportFrom, generate, type Line, objectsOf, type Owner, owner, PAGE,
  replay, standing,
} from "./generation-support";
import { MUSE } from "./ledger-support";
import { erasedWhole } from "./erasure-support";
import { clock } from "../src/clock";
import { markErasing } from "../src/erasure";
import { drainDue, failure, laneAt, starterSeed, storesOn, through } from "./support";
import { carried } from "../src/starter-package";

afterEach(() => {
  vi.useRealTimers();
});

/** Tables the schema does not make: Lifecycle's, the runtime's and SQLite's own. */
const runtimeTable = (name: string) => name === "schema_version" || name.startsWith("sqlite_")
  || name.startsWith("_cf_") || name.startsWith("cf_agents_");

/** The files Worker's manifest reads, which only a recovery and these tests make. */
const bucket = () => env.FILES as unknown as Pick<FilesService, "getManifestRoot" |
                                                                  "getManifestPart">;

const W = "/workspace";
/** What every owner's seed holds before a test writes (`starterSeed`). */
const SEED = starterSeed();
const filled = (size: number, seed: number) =>
  Uint8Array.from({ length: size }, (_, i) => (i * 31 + seed) % 251);

async function write(o: Owner, path: string, content: Uint8Array): Promise<void> {
  await o.stub.fileWrite(o.bind(), { path, expected_etag: null, content });
}

/** `count` notices of a thousand characters each: a base of about `count` KB. */
const notices = (o: Owner, count: number) => sqlIn(o.stub,
  "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) INSERT INTO " +
  "owner_notices (at, type, detail) SELECT 0, 'x', printf('%.1000c', 'a') FROM n", count);

/** A distinct small source file, as a repository holds. */
const source = (i: number) =>
  new TextEncoder().encode(`export const v${i} = "${"y".repeat(900)}";`);

/** The owner's rows' digest as they stand now. */
const now = (o: Owner) => runInDurableObject(o.stub, (_instance: OwnerDO, state) =>
  digest(standing(state.storage.sql)));

/** Each chunk line's bytes, fetched by hash from the open export, one call a chunk. */
async function fetched(o: Owner, lines: Line[], exportId: string): Promise<Map<string, Buffer>> {
  const out = new Map<string, Buffer>();
  for (const line of lines.filter((each) => each.t === "chunk")) {
    const { chunks } = await o.stub.exportChunks(o.session(), {
      export_id: exportId, sha256s: [line["sha256"] as string] });
    out.set(chunks[0]!.sha256, Buffer.from(chunks[0]!.data, "base64"));
  }
  return out;
}

/** A file's bytes as the export holds them, read last line per key. */
function bytesOf(lines: Line[], path: string, chunkBytes: Map<string, Buffer>): Uint8Array {
  const rows = (table: string) => Object.values(replay(lines)[table]!)
    .map((row) => JSON.parse(row) as Record<string, unknown>);
  const name = path.slice(path.lastIndexOf("/") + 1);
  const entry = rows("file_entries").find((row) => row["name"] === name)!;
  const content = rows("file_contents").find((row) => row["content_id"] === entry["content_id"])!;
  if (content["inline"] !== null) {
    return Buffer.from((content["inline"] as { $base64: string }).$base64, "base64");
  }
  const chunks = rows("file_content_chunks")
    .filter((row) => row["content_id"] === entry["content_id"])
    .sort((a, b) => (a["idx"] as number) - (b["idx"] as number));
  return Buffer.concat(chunks.map((chunk) => chunkBytes.get(chunk["sha256"] as string)!));
}

/** The owner changes its policy in its session: a receipt naming that session is kept. */
async function policyInSession(o: Owner): Promise<void> {
  const { generation, policy } = await o.stub.policyRead(o.bind());
  const { tools: _tools, ...rest } = policy as typeof policy & { tools?: unknown };
  await o.stub.policyWrite(o.session(), { expected_generation: generation,
                                          policy: { ...rest, codemode: { on: false } } });
}

/** A writer at every outside call: `count` small files, a notice changed, one gone, one new. */
const busy = (count: number) => async ({ files, gate, n }: { files: Files; gate: WriteGate;
                                                             n: number }) => {
  for (let i = 0; i < count; i++) {
    await files.write(`${W}/repo/src/file-${n}-${i}.ts`, source(n * 1000 + i),
                      { expected: null, writer: "owner" });
  }
  await gate.write(() => {
    gate.storage.sql.exec("UPDATE owner_notices SET detail = 'changed ' || ? WHERE seq = ?", n, n);
    gate.storage.sql.exec("DELETE FROM owner_notices WHERE seq = ?", 1000 + n);
    gate.storage.sql.exec("INSERT INTO owner_notices (at, type, detail) VALUES (?, 'agent', " +
                          "'new')", n);
  });
};

describe("the owner export", () => {
  it("lists every exported table's rows and every chunk, whose bytes round-trip", async () => {
    const o = await owner();
    // A policy written in the owner's session first: its receipt names that session.
    await policyInSession(o);
    expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM policy_receipts WHERE session_id " +
                               "IS NOT NULL")).toEqual([{ n: 1 }]);
    const small = filled(100, 1);
    const big = filled(INLINE_BYTES + 5000, 2);
    await write(o, `${W}/small.txt`, small);
    await write(o, `${W}/big.bin`, big);
    await o.stub.collectionWrite(o.bind(), { collection: "board", row_id: "t1", body: "hello",
                                             expected_etag: "", private: false });
    const { lines, exportId } = await exportFrom(o, null);
    expect(lines[0]).toMatchObject({ t: "header", format: "tinyassets-export", version: 3,
                                     schema: MIGRATIONS.length, owner: o.name });
    expect(lines[0]!["gen"]).toMatch(/^[0-9a-f]{16}$/);
    expect(lines.at(-1)).toMatchObject({ t: "end" });
    expect(lines.at(-1)!["at"]).toBeGreaterThan(0);
    expect(lines.filter((line) => line.t === "header" || line.t === "end")).toHaveLength(2);
    // Read last line per key, it is exactly the owner's rows, columns as listed.
    expect(digest(replay(lines))).toBe(await now(o));
    const chunkBytes = await fetched(o, lines, exportId);
    expect(bytesOf(lines, `${W}/small.txt`, chunkBytes)).toEqual(Buffer.from(small));
    expect(bytesOf(lines, `${W}/big.bin`, chunkBytes)).toEqual(Buffer.from(big));
    // Nothing kept out appears: no table, no namespace, no data key, no session hash (the
    // policy receipt's own included).
    const [space] = await sqlIn(o.stub, "SELECT ns, data_key FROM file_space");
    const text = JSON.stringify(lines);
    for (const secret of [space!["ns"], space!["data_key"]] as string[]) {
      expect(text).not.toContain(secret);
    }
    const [session] = await sqlIn(o.stub, "SELECT session_hash FROM owner_sessions");
    expect(text).not.toContain(session!["session_hash"] as string);
    expect(lines.some((line) => line["table"] === "policy_receipts")).toBe(true);
    expect(lines.filter((line) => line["table"] === "policy_receipts")
      .every((line) => !("session_id" in (line["row"] as object)))).toBe(true);
    expect(lines.filter((line) => line.t === "row" && Object.hasOwn(NOT_EXPORTED,
                                                                    line["table"] as string)))
      .toEqual([]);
  });

  it("resumes from every cursor it ever handed out, to the same owner at E (review R4-2)",
     async () => {
    const o = await owner();
    await notices(o, BURST + 3 * PAGE);  // past one burst, so writes land between its pages
    // A generation with a writer at every outside call: base parts and log segments.
    const made = await generate(o.stub, { duringCall: busy(2) });
    expect(made.lines.some((line) => line["seq"] !== undefined)).toBe(true);
    // An export now reads it (nothing changed since it began, by the owner's doors).
    const whole = await exportFrom(o, null);
    expect(whole.lines[0]!["gen"]).toBe(made.gen);
    expect(whole.cursors.length).toBeGreaterThan(2);
    expect(digest(replay(whole.lines))).toBe(made.atE);
    // From each cursor, the rest of the stream, exactly, however many times it is asked.
    const objects: Line[][] = [];
    for (let next: string | null = null; ;) {
      const page = await o.stub.ownerExport(o.session(), next === null ? { cursor: null }
        : { cursor: next, export_id: whole.exportId });
      objects.push(page.lines.map((line) => JSON.parse(line) as Line));
      if (page.next === null) {
        break;
      }
      next = page.next;
    }
    for (const [at, cursor] of whole.cursors.entries()) {
      const rest = await exportFrom(o, cursor, whole.exportId);
      expect(rest.lines).toEqual(objects.slice(at + 1).flat());
    }
    // A cursor names its export's generation; an unknown export, or a bad cursor, is refused.
    expect((await failure(() => o.stub.ownerExport(o.session(), {
      cursor: whole.cursors[0]!, export_id: "0".repeat(32) }))).message)
      .toBe("this export is not open: begin it again");
    for (const bad of ["x", "Zm9v", Buffer.from("[2,\"zz\",0]").toString("base64url"),
                       Buffer.from(`[1,"${made.gen}",0]`).toString("base64url"),
                       Buffer.from(`[2,"${"0".repeat(16)}",0]`).toString("base64url")]) {
      expect((await failure(() => o.stub.ownerExport(o.session(), {
        cursor: bad, export_id: whole.exportId }))).message)
        .toBe("an export cursor is one a page handed out");
    }
  });

  it("is the owner's alone, in its session: no outside client, no call without the session",
     async () => {
    const o = await owner();
    await o.stub.outsideAgents(o.bind(MUSE));
    expect((await failure(() => o.stub.ownerExport(o.bind(MUSE), {}))).message)
      .toBe("only the owner may export everything it holds");
    expect((await failure(() => o.stub.ownerExport(o.bind(), {}))).code).toBe("refused");
    expect((await failure(() => o.stub.ownerExport(o.session(), { cursor: null, x: 1 } as never)))
      .message).toBe("an export request is at most its cursor and its export_id");
  });

  it("holds every table of the schema to exported or kept out, and every column decided",
     async () => {
    const o = await owner();
    const tables = (await sqlIn(o.stub, "SELECT name FROM sqlite_master WHERE type = 'table'"))
      .map((row) => row["name"] as string).filter((name) => !runtimeTable(name)).sort();
    const listed = [...Object.keys(exported()), ...Object.keys(NOT_EXPORTED)].sort();
    expect(tables).toEqual(listed);
    for (const [table, { columns, omitted }] of Object.entries(exported())) {
      const held = (await sqlIn(o.stub, `PRAGMA table_info(${table})`))
        .map((row) => row["name"] as string).sort();
      expect([...columns, ...Object.keys(omitted ?? {})].sort()).toEqual(held);
    }
  });

  it("reads one generation from two exports at once, one joining it while it runs",
     async () => {
    const o = await owner();
    await notices(o, 2000);
    // A generation begun and not yet carried on (its first pass had no time), due now.
    await generate(o.stub, { onePass: true });
    await sqlIn(o.stub, "UPDATE owner_backups SET retry_at = NULL");
    const [a, b] = await Promise.all([exportFrom(o, null), exportFrom(o, null)]);
    expect(a.exportId).not.toBe(b.exportId);
    expect(a.lines[0]!["gen"]).toBe(b.lines[0]!["gen"]);
    expect(a.lines).toEqual(b.lines);
    expect(await sqlIn(o.stub, "SELECT COUNT(DISTINCT gen) AS n FROM owner_exports"))
      .toEqual([{ n: 1 }]);
  });
});

describe("a generation", () => {
  it("ends as the owner at E though the owner writes at every outside call and after " +
     "every unit (review R4-3)", async () => {
    const o = await owner();
    await notices(o, BURST + 3 * PAGE);
    let after = 0;
    const made = await generate(o.stub, {
      duringCall: busy(10),
      afterUnit: async ({ gate }) => {
        after++;
        await gate.write(() => gate.storage.sql.exec(
          "UPDATE owner_notices SET detail = 'after ' || ? WHERE seq = ?", after, 100 + after));
      },
    });
    expect(made.notices).toEqual([]);
    expect(made.lines.at(0)).toMatchObject({ t: "header" });
    expect(after).toBeGreaterThan(5);
    expect(made.atE).toBeDefined();
    expect(digest(replay(made.lines))).toBe(made.atE);
  });

  it("carries the starter's receipts as they change while it runs (slice STARTER)",
     async () => {
    // The seed's own receipts, one a file, and its state at the carried version: a pass of
    // a later version changes them while the generation runs.
    const o = await owner();
    await notices(o, 4 * PAGE);
    const [edited, dropped] = SEED.files.map((file) => file.path);
    const later = carried().version + 1;
    let after = 0;
    const made = await generate(o.stub, {
      afterUnit: async ({ gate }) => {
        after++;
        await gate.write(() => {
          const sql = gate.storage.sql;
          if (after === 1) {
            sql.exec("UPDATE starter_receipts SET state = 'edited', offered = ? WHERE path = ?",
                     "b".repeat(64), edited!);
          } else if (after === 2) {
            sql.exec("DELETE FROM starter_receipts WHERE path = ?", dropped!);
          } else if (after === 3) {
            sql.exec("UPDATE starter_state SET version = ?", later);
          }
        });
      },
    });
    expect(made.notices).toEqual([]);
    expect(after).toBeGreaterThan(3);
    const rows = replay(made.lines);
    expect(digest(rows)).toBe(made.atE);
    expect(Object.keys(rows["starter_receipts"]!)).toHaveLength(SEED.files.length - 1);
    expect(Object.values(rows["starter_state"]!).map((row) => JSON.parse(row)["version"]))
      .toEqual([later]);
  });

  it("settles though a 5,000-file sync runs through its pass (review R4-3)", { tags: ["large"] },
     async () => {
    const o = await owner();
    await notices(o, 3 * BURST);  // three bursts: tens of units and calls
    let written = 0;
    // The sync's files land at every outside call and after every unit, as they come.
    const sync = async (at: At) => {
      const count = Math.min(125, 5000 - written);
      written += count;
      await busy(count)(at);
    };
    const made = await generate(o.stub, { duringCall: sync, afterUnit: sync });
    expect({ written, notices: made.notices, calls: made.calls })
      .toMatchObject({ written: 5000, notices: [] });
    expect(made.atE).toBeDefined();
    expect(digest(replay(made.lines))).toBe(made.atE);
  });

  it("repeats only what was not acknowledged, wherever a pass is cut: before or after " +
     "each outside call (review R4-1; crash points)", { tags: ["large"] }, async () => {
    const plain = await owner();
    await notices(plain, BURST + PAGE);  // two bursts: segments before E and after
    const { calls, lines } = await generate(plain.stub, { duringCall: busy(3) });
    expect(calls).toBeGreaterThan(3);
    // Its root names log segments through E: crash points fall between the base's bursts.
    expect(lines.some((line) => line["seq"] !== undefined)).toBe(true);
    for (let call = 1; call <= calls; call++) {
      for (const when of ["before", "after"] as const) {
        const o = await owner();
        await notices(o, BURST + PAGE);
        const made = await generate(o.stub, { duringCall: busy(3), failAt: { call, when } });
        expect([call, when, made.notices]).toEqual([call, when, []]);
        expect([call, when, digest(replay(made.lines))]).toEqual([call, when, made.atE]);
      }
    }
  });

  it("gives up one a migration ran under, loudly, and begins a fresh one under the new " +
     "schema: never two schemas in one (review L5-2)", async () => {
    const o = await owner();
    await notices(o, 200);
    const before = await generate(o.stub, { onePass: true });
    const [begun] = await sqlIn(o.stub, "SELECT gen, schema FROM owner_backups");
    expect(begun).toEqual({ gen: before.gen, schema: MIGRATIONS.length });
    // Begun under the code before the last migration, as a deploy that adds one finds it.
    await sqlIn(o.stub, "UPDATE owner_backups SET schema = ? WHERE gen = ?",
                MIGRATIONS.length - 1, before.gen);
    const after = await generate(o.stub);
    expect(after.notices).toEqual(["manifest_schema"]);
    expect(after.gen).not.toBe(before.gen);
    expect(await sqlIn(o.stub, "SELECT state, schema FROM owner_backups"))
      .toEqual([{ state: "done", schema: MIGRATIONS.length }]);
    // Its lines are one schema's, and replayed they are the owner at E.
    expect(after.lines[0]).toMatchObject({ t: "header", schema: MIGRATIONS.length });
    expect(digest(replay(after.lines))).toBe(after.atE);
  });

  it("takes its burst's units of the pool now or yields: with the pool full it reads and " +
     "puts nothing and goes on later; with room, each part's put inside its units, each " +
     "segment's inside its own, never a burst's (burstlease, rvburst W1)", async () => {
    const pool = ownerSlots();
    const o = await owner();
    await notices(o, BURST + PAGE);  // two bursts, the first of two parts
    // Another object's holds: past what leaves a burst its units beside the reserve.
    const other = await pool.acquire(OWNER_POOL_UNITS - OWNER_RESERVE_UNITS - BURST_UNITS + 1);
    const held = await generate(o.stub);
    expect({ calls: held.calls, notices: held.notices, lines: held.lines.length })
      .toEqual({ calls: 0, notices: [], lines: 0 });
    const [row] = await sqlIn(o.stub, "SELECT state, cursor, parts, retry_at FROM owner_backups");
    expect(row).toMatchObject({ state: "writing", cursor: null, parts: 0 });
    expect(row!["retry_at"] as number)
      .toBeGreaterThan(Date.now() / 1000 + MANIFEST_BUSY_RETRY_S - 2);
    expect(pool.state().held).toBe(other.units);  // nothing of the burst's left held
    other.release();
    pool.resetPeak();
    // With room, the owner writing at every call: each part's put is made inside the
    // burst's units, each segment's inside its own ship hold (before or after a burst, at
    // the end: never between a burst's parts), the root's in none; all go back.
    const seen: Record<string, number[]> = { part: [], segment: [], root: [] };
    const made = await generate(o.stub, { duringCall: async (at) => {
      seen[at.kind]!.push(pool.state().held);
      await busy(2)(at);
    } });
    expect(made.notices).toEqual([]);
    expect(digest(replay(made.lines))).toBe(made.atE);
    expect(seen["part"]!.length).toBeGreaterThan(2);
    expect(new Set(seen["part"])).toEqual(new Set([BURST_UNITS]));
    expect(new Set(seen["segment"])).toEqual(new Set([SHIP_UNITS]));
    expect(seen["root"]).toEqual([0]);
    expect(pool.state()).toMatchObject({ held: 0, peak: BURST_UNITS });
  });

  it("ships a log segment outside a burst inside its own units, now or not at all: with the " +
     "pool full it puts nothing and goes on later (rvburst 1)", async () => {
    const pool = ownerSlots();
    const o = await owner();
    await notices(o, 200);
    // Begun, its capture on, and the owner's writes logged before any burst.
    await generate(o.stub, { onePass: true });
    await sqlIn(o.stub, "UPDATE owner_notices SET detail = 'changed' WHERE seq <= 50");
    const [{ logged }] = await sqlIn(o.stub, "SELECT COUNT(*) AS logged FROM change_log") as
      [{ logged: number }];
    expect(logged).toBeGreaterThan(0);
    // Another object's holds: past what leaves the ship its units beside the reserve.
    const other = await pool.acquire(OWNER_POOL_UNITS - OWNER_RESERVE_UNITS - SHIP_UNITS + 1);
    const held = await generate(o.stub);
    expect({ calls: held.calls, notices: held.notices }).toEqual({ calls: 0, notices: [] });
    const [row] = await sqlIn(o.stub, "SELECT state, segments, retry_at FROM owner_backups");
    expect(row).toMatchObject({ state: "writing", segments: 0 });
    expect(row!["retry_at"] as number)
      .toBeGreaterThan(Date.now() / 1000 + MANIFEST_BUSY_RETRY_S - 2);
    expect(await sqlIn(o.stub, "SELECT COUNT(*) AS logged FROM change_log"))
      .toEqual([{ logged }]);
    expect(pool.state().held).toBe(other.units);
    other.release();
    const made = await generate(o.stub);
    expect(made.notices).toEqual([]);
    expect(digest(replay(made.lines))).toBe(made.atE);
    expect(pool.state().held).toBe(0);
  });

  it("begins no burst once the owner's erasure begins while the log ships: no part put, " +
     "the pool never past the ship's units (rvburst V2)", async () => {
    const pool = ownerSlots();
    const o = await owner();
    await notices(o, 200);
    await generate(o.stub, { onePass: true });
    await sqlIn(o.stub, "UPDATE owner_notices SET detail = 'changed' WHERE seq <= 50");
    pool.resetPeak();
    const kinds: string[] = [];
    await generate(o.stub, { duringCall: async ({ gate, kind }) => {
      kinds.push(kind);
      if (kind === "segment") {
        // The erasure begins during the first ship's put.
        await gate.write(() => markErasing(gate.storage, clock()));
      }
    } });
    expect(kinds).toEqual(["segment"]);
    expect(pool.state()).toMatchObject({ held: 0, peak: SHIP_UNITS });
  });

  it("goes on later, never at once, when a pass ends before it does", async () => {
    const o = await owner();
    await notices(o, 100);
    await generate(o.stub, { onePass: true });
    const [row] = await sqlIn(o.stub, "SELECT state, retry_at FROM owner_backups");
    expect(row!["state"]).toBe("writing");
    expect(row!["retry_at"] as number)
      .toBeGreaterThan(Date.now() / 1000 + MANIFEST_CONTINUE_S - 2);
  });
});

describe("a generation at production sizes (rs_cap §2.1)", () => {
  /** A row's most bytes (Durable Objects' SQLite limits), as `owner-bound.test.ts` costs it. */
  const ROW_MAX = 2_000_000;
  /** A fixture line's most bytes: a notice row's at 1,000 three-byte characters, and newline. */
  const WIDEST = 3_300;
  /** Notices of 1,000 characters, every other one of three-byte characters (`€`). */
  const notices3 = (o: Owner, count: number) => sqlIn(o.stub,
    "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) " +
    "INSERT INTO owner_notices (at, type, detail) SELECT i, 'x', CASE WHEN i % 2 = 0 THEN " +
    "printf('%.1000c', 'a') ELSE replace(printf('%.1000c', 'a'), 'a', '€') END FROM n", count);

  it("reads each page of three-byte characters within its bytes, its lines whole",
     { tags: ["large"] }, async () => {
    const o = await owner();
    await notices3(o, 2_000);
    const pages = await runInDurableObject(o.stub, (_instance: OwnerDO, state) => {
      const seen: { bytes: number; rows: number }[] = [];
      for (let cursor = decodeCursor(null); ;) {
        const page = readPage(state.storage.sql, cursor, { schema: MIGRATIONS.length,
                                                           owner: "owner", at: 0, gen: "g", s: 1 },
                              Number.MAX_SAFE_INTEGER, EXPORT_PAGE_BYTES);
        // Encoded as the burst encodes it: one buffer, each line whole at its end.
        const { bytes, ends } = encodeLines(page.lines);
        const text = Array.from(ends, (end, n) => new TextDecoder().decode(
          bytes.subarray(n === 0 ? 0 : ends[n - 1]!, end)));
        expect(ends.at(-1)).toBe(bytes.byteLength);
        expect(text.every((line) => line.endsWith("\n") && JSON.parse(line) !== null)).toBe(true);
        seen.push({ bytes: bytes.byteLength,
                    rows: text.filter((line) => line.includes('"table":"owner_notices"')).length });
        if (page.next === null) {
          return seen;
        }
        cursor = page.next;
      }
    });
    console.log(`pages of ${pages.map((page) => page.bytes).join(", ")} bytes`);
    // Counted in UTF-16, each page of these would hold about twice its bytes.
    for (const page of pages) {
      expect(page.bytes).toBeLessThan(EXPORT_PAGE_BYTES + WIDEST);
    }
    expect(pages.length).toBeGreaterThan(3);
    expect(pages.reduce((sum, page) => sum + page.rows, 0)).toBe(2_000);
  });

  it("finishes an owner of several bursts, read back exactly, each burst bounded by its " +
     "bytes and its row under a row's 2 MB", { tags: ["large"] }, async () => {
    const o = await owner();
    // 14,000 notices of 1,000 characters, every other one of three-byte characters: about
    // 29 MB of base, so that each burst counts its UTF-8, not its UTF-16 (a third of it).
    await notices3(o, 14_000);
    const bursts: number[] = [];
    let entries = 0;
    const widest = { part: 0, segment: 0 };
    let rowMost = 0;
    const made = await generate(o.stub, {
      // After every unit: a burst is recorded in one, its parts' entries appended at once.
      afterUnit: async ({ gate }) => {
        const row = gate.storage.sql.exec<{ part_list: string; segment_list: string;
                                            bytes: number }>(
          "SELECT part_list, segment_list, length(gen) + length(day) + length(state) + " +
            "coalesce(length(cursor), 0) + length(part_list) + length(segment_list) + 10 * 8 " +
            "AS bytes FROM owner_backups WHERE state = 'writing'").toArray()[0];
        if (row === undefined) {
          return;
        }
        rowMost = Math.max(rowMost, row.bytes);
        for (const entry of row.part_list.split(",")) {
          widest.part = Math.max(widest.part, entry.length + 1);
        }
        for (const entry of row.segment_list.split(",")) {
          widest.segment = Math.max(widest.segment, entry.length + 1);
        }
        const listed = row.part_list === "" ? [] : row.part_list.split(",");
        if (listed.length > entries) {
          const sizes = listed.slice(entries).map((entry) => Number(entry.split(":")[1]));
          expect(sizes.every((size) => size <= CHUNK_BYTES)).toBe(true);
          bursts.push(sizes.reduce((sum, size) => sum + size, 0));
          entries = listed.length;
        }
      },
    });
    expect(made.notices).toEqual([]);
    expect(made.atE).toBeDefined();
    // Every part split into lines that each parse (the harness parses them): whole lines.
    expect(made.lines.filter((line) => line.t === "row" && line["table"] === "owner_notices"))
      .toHaveLength(14_000);
    expect(digest(replay(made.lines))).toBe(made.atE);
    // Several bursts, each under the gather and its last page, every one counted in bytes
    // (a page at most its bytes and the line that reached them). Counted in UTF-16, a burst
    // of these would be over half again as large.
    console.log(`bursts of ${bursts.map((bytes) => (bytes / 2 ** 20).toFixed(1)).join(", ")} ` +
                `MiB; the row at most ${rowMost} bytes, its widest entries ` +
                `${JSON.stringify(widest)}`);
    expect(bursts.length).toBeGreaterThanOrEqual(3);
    for (const bytes of bursts) {
      expect(bytes).toBeLessThan(GENERATION_SIZES.gather + GENERATION_SIZES.page + WIDEST);
    }
    // The row: under a row's 2 MB as it ran, and its lists' entries, at production's sizes
    // (a part's seven digits), within what the schema and the guard cost them at.
    expect(rowMost).toBeLessThan(ROW_MAX);
    expect(widest.part).toBeLessThanOrEqual(PART_ENTRY);
    expect(widest.segment).toBeLessThanOrEqual(SEGMENT_ENTRY);
    expect(MANIFEST_PARTS * Math.max(widest.part, widest.segment)).toBeLessThan(ROW_MAX);
  });
});

/** A generation finished long ago, so tonight's is due now. */
const LAST_YEARS = "INSERT INTO owner_backups (gen, day, state, cursor, parts, part_list, " +
  "bytes, s, schema, started_at, finished_at) VALUES ('ffffffffffffffff', '20200101', 'done', " +
  "NULL, 0, '', 0, 1, 1, 1, 1)";

/** A file written, then removed with its history: its chunk is garbage, in its grace. */
async function writeThenDelete(o: Owner, path: string, seed: number): Promise<string> {
  await write(o, path, filled(INLINE_BYTES + 1000, seed));
  const rows = await sqlIn(o.stub, "SELECT c.sha256 FROM file_entries e JOIN " +
    "file_content_chunks c ON c.content_id = e.content_id WHERE e.name = ?",
                           path.slice(path.lastIndexOf("/") + 1));
  await o.stub.fileRemove(o.bind(), { path, expected_etag: null, recursive: false });
  await o.stub.fileHistoryPurge(o.bind(), { path });
  return rows[0]!["sha256"] as string;
}

const stateOf = (o: Owner, sha: string) =>
  sqlIn(o.stub, "SELECT state FROM file_chunks WHERE sha256 = ?", sha);

/** The owner's namespace. */
const nsOf = async (o: Owner) =>
  (await sqlIn(o.stub, "SELECT ns FROM file_space"))[0]!["ns"] as string;

describe("an open export", () => {
  it("holds no chunk: garbage written after it opened goes on its own grace (review H1)",
     async () => {
    const o = await owner();
    await exportFrom(o, null);
    // A file written and deleted after it opened: garbage, in no owner's storage number.
    const sha = await writeThenDelete(o, `${W}/after.bin`, 3);
    expect(await stateOf(o, sha)).toEqual([{ state: "garbage" }]);
    expect((await o.stub.storageUsage(o.bind())).used_bytes).toBe(SEED.bytes);
    // Its grace passed (30 days): the sweep takes it, the export open or not.
    await sqlIn(o.stub, "UPDATE file_chunks SET gc_after = 1 WHERE sha256 = ?", sha);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/x`, expected_etag: null });
    await drainDue(o.stub);
    expect(await stateOf(o, sha)).toEqual([]);
  });

  it("lives under the garbage grace, so every chunk it names is still there", async () => {
    expect(EXPORT_LIFE_S).toBeLessThan(GC_GRACE_S);
    const o = await owner();
    const big = filled(INLINE_BYTES + 10, 7);
    await write(o, `${W}/named.bin`, big);
    const { lines, exportId } = await exportFrom(o, null);
    const sha = lines.find((line) => line.t === "chunk")!["sha256"] as string;
    const [{ ends_at: endsAt }] = await sqlIn(o.stub, "SELECT ends_at FROM owner_exports") as
      [{ ends_at: number }];
    // The file goes after the export read it: its chunk is garbage, due only past the export.
    await o.stub.fileRemove(o.bind(), { path: `${W}/named.bin`, expected_etag: null,
                                        recursive: false });
    await o.stub.fileHistoryPurge(o.bind(), { path: `${W}/named.bin` });
    const [{ gc_after: due }] = await sqlIn(o.stub, "SELECT gc_after FROM file_chunks WHERE " +
                                                    "sha256 = ?", sha) as [{ gc_after: number }];
    expect(due).toBeGreaterThan(endsAt);
    expect((await fetched(o, lines, exportId)).get(sha)).toEqual(Buffer.from(big));
    // A hash the owner never stored, or no longer stores, is refused by name.
    expect((await failure(() => o.stub.exportChunks(o.session(), {
      export_id: exportId, sha256s: ["e".repeat(64)] }))).message)
      .toBe(`not_found: ${CHUNK_GONE}`);
    await sqlIn(o.stub, "UPDATE file_chunks SET gc_after = 1 WHERE sha256 = ?", sha);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/y`, expected_etag: null });
    await drainDue(o.stub);
    expect((await failure(() => o.stub.exportChunks(o.session(), {
      export_id: exportId, sha256s: [sha] }))).message).toBe(`not_found: ${CHUNK_GONE}`);
    // Ended, its id names nothing.
    expect(await o.stub.exportEnd(o.bind(), { export_id: exportId })).toEqual({ ended: true });
    expect((await failure(() => o.stub.exportChunks(o.session(), {
      export_id: exportId, sha256s: [sha] }))).message)
      .toBe("this export is not open: begin it again");
  });

  it("pins its generation through the nightly replacement until it ends", async () => {
    const o = await owner();
    await write(o, `${W}/one.txt`, filled(10, 1));
    const old = await exportFrom(o, null);
    const gen = old.lines[0]!["gen"] as string;
    // The owner changes; the next generation replaces it, but the export pins it.
    await write(o, `${W}/two.txt`, filled(10, 2));
    const made = await generate(o.stub);
    expect(made.gen).not.toBe(gen);
    const ns = await nsOf(o);
    expect(await bucket().getManifestRoot(ns, gen)).not.toBeNull();
    const again = await exportFrom(o, old.cursors[0]!, old.exportId);
    expect(again.lines).toEqual(old.lines.slice(old.lines.length - again.lines.length));
    // Its life over, the old generation goes at the lane's next look.
    await sqlIn(o.stub, "UPDATE owner_exports SET ends_at = 1 WHERE export_id = ?",
                old.exportId);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });
    await drainDue(o.stub);
    expect(await bucket().getManifestRoot(ns, gen)).toBeNull();
    expect(await bucket().getManifestRoot(ns, made.gen)).not.toBeNull();
  });
});

describe("the nightly manifest", () => {
  it("is written by the owner's own schedule, under its key, and the older one let go",
     async () => {
    const o = await owner();
    await write(o, `${W}/kept.bin`, filled(INLINE_BYTES + 10, 5));
    const at = Date.now() / 1000;
    const tomorrow = Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(),
                              new Date().getUTCDate() + 1) / 1000;
    // Due the next night, at the owner's own hour of it: one job, no poll.
    expect(await laneAt(o.stub, "manifest")).toBe(tomorrow + spread(o.name));
    expect(await laneAt(o.stub, "manifest")).toBeGreaterThan(at);
    // Last night's was written long ago: tonight's is due now.
    await sqlIn(o.stub, LAST_YEARS);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });  // re-derives jobs
    await drainDue(o.stub);
    const rows = await sqlIn(o.stub, "SELECT gen, state FROM owner_backups");
    expect(rows).toHaveLength(1);
    expect(rows[0]!["state"]).toBe("done");
    const gen = rows[0]!["gen"] as string;
    // Nothing changed since it began: no job, and the idle owner's object is never woken.
    expect(await laneAt(o.stub, "manifest")).toBeNull();
    // The root names its parts and carries the wrapped key; the parts read under it.
    const [space] = await sqlIn(o.stub, "SELECT ns, data_key FROM file_space");
    const [ns, key] = [space!["ns"] as string, space!["data_key"] as string];
    const root = (await bucket().getManifestRoot(ns, gen))!;
    expect(root).toMatchObject({ format: "tinyassets-manifest", gen, key });
    const parts: Uint8Array[] = await Promise.all(root.parts.map((part, index) =>
      bucket().getManifestPart(ns, root.key, gen, index, part.sha256)));
    const lines: Line[] = Buffer.concat(parts).toString("utf8").trimEnd().split("\n")
      .map((line: string) => JSON.parse(line) as Line);
    expect(lines[0]).toMatchObject({ t: "header", owner: o.name, gen });
    // The same stream as the export's, the chunks' bytes left in the bucket: the seed's,
    // and this test's one.
    const chunks = SEED.mine(lines.filter((line) => line.t === "chunk"),
                             (line) => line["sha256"] as string);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).not.toHaveProperty("data");
    expect(lines.some((line) => line["table"] === "file_entries"
      && (line["row"] as { name: string }).name === "kept.bin")).toBe(true);
    // A read changes nothing: still no job.
    await o.stub.fileStat(o.bind(), { path: `${W}/kept.bin` });
    expect(await sqlIn(o.stub, "SELECT changed_at FROM owner_backups"))
      .toEqual([{ changed_at: null }]);
    expect(await laneAt(o.stub, "manifest")).toBeNull();
    // A change sets it due again, the next night.
    await o.stub.fileMkdir(o.bind(), { path: `${W}/later`, expected_etag: null });
    expect((await sqlIn(o.stub, "SELECT changed_at FROM owner_backups"))[0]!["changed_at"])
      .not.toBeNull();
    for (let i = 0; i < 50 && await laneAt(o.stub, "manifest") === null; i++) {
      await new Promise((resolve) => setTimeout(resolve, 10));  // the mark's own sync
    }
    expect(await laneAt(o.stub, "manifest")).toBe(tomorrow + spread(o.name));
  });

  it("past its ceiling, is given up once and the owner told, never retried in a loop " +
     "(review L7)", async () => {
    const o = await owner();
    await write(o, `${W}/k.bin`, filled(INLINE_BYTES + 10, 9));
    await sqlIn(o.stub, LAST_YEARS);
    // A generation under way with every part but none used already.
    const gen = "abcdefabcdefabcd";
    await sqlIn(o.stub, "INSERT INTO owner_backups (gen, day, state, cursor, parts, " +
                        "part_list, bytes, s, schema, started_at) VALUES (?, '20261103', " +
                        "'writing', NULL, ?, '', 0, 1, ?, ?)", gen, MANIFEST_PARTS,
                MIGRATIONS.length, Date.now() / 1000);
    await sqlIn(o.stub, "UPDATE log_capture SET gen = ?, capturing = 1", gen);
    await sqlIn(o.stub, "UPDATE owner_backup_state SET began_at = ?", Date.now() / 1000);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });  // its job set
    await drainDue(o.stub);
    expect(await sqlIn(o.stub, "SELECT gen FROM owner_backups WHERE gen = ?", gen)).toEqual([]);
    const told = await o.stub.noticeList(o.bind());
    expect(told.notices.filter((notice) => notice.type === MANIFEST_CEILING)).toHaveLength(1);
    expect(await sqlIn(o.stub, "SELECT told FROM owner_backup_state")).toEqual([{ told: 1 }]);
    expect(await sqlIn(o.stub, "SELECT capturing, gen FROM log_capture"))
      .toEqual([{ capturing: 0, gen: null }]);
    // The newest does not hold the owner's change, so it is tried again: the next night after
    // the one given up began, never every few minutes.
    expect(await laneAt(o.stub, "manifest"))
      .toBeGreaterThanOrEqual((Math.floor(Date.now() / 86_400_000) + 1) * 86_400);
  });

  it("is wrapped again, with the object's key, by a rotation's sweep, an idle owner's too " +
     "(review M4)", async () => {
    const o = await owner();
    // The owner's key, which its seed's files are under, as a wrap before a rotation left
    // it: under the files ring's older kid.
    const ring = JSON.parse(env.FILES_TEST_RING) as { keys: Record<string, string> };
    const older = JSON.stringify({ active: "k1", keys: ring.keys });
    const [held] = await sqlIn(o.stub, "SELECT ns, data_key FROM file_space");
    const ns = held!["ns"] as string;
    await sqlIn(o.stub, "UPDATE file_space SET data_key = ?",
                await rewrap(older, ns, held!["data_key"]));
    const body = filled(INLINE_BYTES + 10, 11);
    await write(o, `${W}/kept.bin`, body);
    await sqlIn(o.stub, LAST_YEARS);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });
    await drainDue(o.stub);
    const [{ gen }] = await sqlIn(o.stub, "SELECT gen FROM owner_backups") as [{ gen: string }];
    // A pass under the older kid's key writes its root under the active kid all the same.
    expect((await bucket().getManifestRoot(ns, gen))!.key.startsWith("v1.k2.")).toBe(true);
    // Idle since: no lane runs for it. The platform's sweep, and only the platform's, wraps
    // the object's own copy again; the root is found and kept under the active kid.
    expect(await laneAt(o.stub, "manifest")).toBeNull();
    expect((await failure(() => o.stub.dataKeyRewrap(o.bind()))).message).toBe(PLATFORM_ONLY);
    const platform: Binding = { owner: o.name, caller: PLATFORM_CALLER, origin: null };
    expect(await o.stub.dataKeyRewrap(platform)).toEqual({ rewrapped: true, roots: 1 });
    const [space] = await sqlIn(o.stub, "SELECT data_key FROM file_space");
    expect((space!["data_key"] as string).startsWith("v1.k2.")).toBe(true);
    expect(await o.stub.dataKeyRewrap(platform)).toEqual({ rewrapped: false, roots: 1 });
    // The same data key: what was put under the older wrap reads under the new one.
    const { lines, exportId } = await exportFrom(o, null);
    expect(bytesOf(lines, `${W}/kept.bin`, await fetched(o, lines, exportId)))
      .toEqual(Buffer.from(body));
  });

  it("is deleted with every copy of the owner's key when the owner is erased", async () => {
    const o = await owner();
    await write(o, `${W}/gone.bin`, filled(INLINE_BYTES + 10, 6));
    await sqlIn(o.stub, LAST_YEARS);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });
    await drainDue(o.stub);
    const [{ gen }] = await sqlIn(o.stub, "SELECT gen FROM owner_backups") as [{ gen: string }];
    const ns = await nsOf(o);
    expect(await bucket().getManifestRoot(ns, gen)).not.toBeNull();
    await erasedWhole(o.stub, o.name, o.session());
    // No manifest, and no object row, holds the owner's wrapped key any more: a chunk the
    // bucket still keeps is unreadable (the files Worker's sealed-object tests hold that).
    expect(await bucket().getManifestRoot(ns, gen)).toBeNull();
    expect(await sqlIn(o.stub, "SELECT name FROM sqlite_master WHERE name = 'file_space'"))
      .toEqual([]);
  });
});

/** The owner's namespace and wrapped key. */
const spaceOf = async (o: Owner) => {
  const [row] = await sqlIn(o.stub, "SELECT ns, data_key FROM file_space");
  return { ns: row!["ns"] as string, key: row!["data_key"] as string };
};

/** A generation put as a pass puts one, a part and its root, that no row of the object names. */
async function unnamed(ns: string, key: string, gen: string): Promise<void> {
  const part = await env.FILES.putManifestPart(ns, key, gen, 0, new TextEncoder().encode("left"));
  await env.FILES.putManifestRoot(ns, key, gen, [part], [], { s: 1, e: 0 });
}

const sweepState = (o: Owner) => sqlIn(o.stub,
                                       "SELECT sweep, sweep_retry FROM owner_backup_state");

describe("the sweep of generations no row names", () => {
  it("deletes, in the next pass, what a generation given up left in R2: a part that landed " +
     "with its answer lost", async () => {
    const o = await owner();
    await write(o, `${W}/a.bin`, filled(INLINE_BYTES + 10, 21));
    const kept = await generate(o.stub);
    const { ns } = await spaceOf(o);
    await write(o, `${W}/b.bin`, filled(INLINE_BYTES + 10, 22));
    let lost: string | null = null;
    let left: string[] | null = null;
    const given = await generate(o.stub, {
      failAt: { call: 1, when: "after" },
      duringCall: async ({ gate, n }) => {
        if (n === 1) {
          // Its first put lands and its answer is lost, nothing of it recorded; the next pass
          // gives it up, its log having overflowed meanwhile, as in an R2 outage.
          lost = gate.storage.sql.exec<{ gen: string }>(
            "SELECT gen FROM owner_backups WHERE state = 'writing'").one().gen;
          await gate.quiet(() => gate.storage.sql.exec("UPDATE log_capture SET overflow = 1"));
        }
      },
      afterUnit: async ({ gate }) => {
        if (lost !== null && left === null && gate.storage.sql.exec(
              "SELECT 1 FROM owner_backups WHERE gen = ?", lost).toArray().length === 0) {
          left = await objectsOf(ns, lost);  // as the give-up left it
        }
      } });
    expect(given.notices).toEqual(["manifest_log_overflow"]);
    expect(left).toEqual([manifestKey(ns, lost!, 0)]);
    expect(await objectsOf(ns, lost!)).toEqual([]);
    expect(await objectsOf(ns, kept.gen)).toContain(manifestKey(ns, kept.gen));
    expect(await sweepState(o)).toEqual([{ sweep: 0, sweep_retry: null }]);
  });

  it("keeps every object of a generation the one list names: the one a pass is writing, its " +
     "put landed unrecorded, an open export's, the fallback and the newest", async () => {
    const o = await owner();
    await write(o, `${W}/a.bin`, filled(INLINE_BYTES + 10, 31));
    const exported = await generate(o.stub);
    await exportFrom(o, null);  // open: it pins the newest, unchanged since it began
    await write(o, `${W}/b.bin`, filled(INLINE_BYTES + 10, 32));
    const fallback = await generate(o.stub);
    await write(o, `${W}/c.bin`, filled(INLINE_BYTES + 10, 33));
    // The newest's log as a busy pass's would be, recorded as its finish records it, before the
    // same pass prunes: the one before it is then kept, as the fallback.
    const newest = await generate(o.stub, { afterUnit: async ({ gate }) => {
      await gate.write(() => gate.storage.sql.exec(
        "UPDATE owner_backups SET log_bytes = ? WHERE state = 'done' AND gen NOT IN (?, ?)",
        LOG_BYTES + 1, exported.gen, fallback.gen));
    } });
    const { ns, key } = await spaceOf(o);
    const orphan = "0123012301230123";
    await write(o, `${W}/d.bin`, filled(INLINE_BYTES + 10, 34));
    let writing: string | null = null;
    let seen: Map<string, string[]> | null = null;
    await generate(o.stub, {
      // The pass's first put lands with its answer lost: the pass ends, its part unrecorded.
      failAt: { call: 1, when: "after" },
      afterUnit: async ({ gate }) => {
        // Once that pass backed off: a generation no row names in R2, and a sweep owed.
        const row = gate.storage.sql.exec<{ gen: string; retry_at: number | null }>(
          "SELECT gen, retry_at FROM owner_backups WHERE state = 'writing'").toArray()[0];
        if (writing === null && row !== undefined && row.retry_at !== null) {
          writing = row.gen;
          await unnamed(ns, key, orphan);
          await gate.write(() => gate.storage.sql.exec(
            "UPDATE owner_backup_state SET sweep = sweep + 1"));
        }
      },
      // The next pass sweeps before it puts again: what R2 holds then.
      duringCall: async ({ n }) => {
        if (n === 2) {
          seen = new Map(await Promise.all([writing!, exported.gen, fallback.gen, newest.gen,
                                            orphan].map(async (gen) =>
            [gen, await objectsOf(ns, gen)] as const)));
        }
      } });
    expect(seen!.get(orphan)).toEqual([]);
    expect(seen!.get(writing!)).toEqual([manifestKey(ns, writing!, 0)]);
    for (const gen of [exported.gen, fallback.gen, newest.gen]) {
      expect([gen, seen!.get(gen)]).toEqual([gen, expect.arrayContaining([manifestKey(ns, gen)])]);
    }
    expect(await sweepState(o)).toEqual([{ sweep: 0, sweep_retry: null }]);
  });

  it("stays owed when its answer is lost after its deletes landed, backing off in its row, " +
     "and is made again from the start when another is owed while it runs", async () => {
    const o = await owner();
    await write(o, `${W}/a.bin`, filled(INLINE_BYTES + 10, 41));
    const kept = await generate(o.stub);
    const { ns, key } = await spaceOf(o);
    const [first, second] = ["1".repeat(16), "2".repeat(16)];
    await unnamed(ns, key, first);
    await sqlIn(o.stub, "UPDATE owner_backup_state SET sweep = 1");
    /** One pass of the lane over the object's own storage, its store as `store` makes it. */
    const pass = (store: (state: DurableObjectState) => ChunkStore) =>
      runInDurableObject(o.stub, async (_instance: OwnerDO, state) => {
        const { backups } = storesOn(state, store(state));
        await backups.run(clock(), Date.now() + 60_000);
        return { next: backups.next(clock()), now: clock(), ...state.storage.sql.exec<{
          sweep: number; sweep_retry: number | null }>(
          "SELECT sweep, sweep_retry FROM owner_backup_state").one() };
      });
    const lostAnswer = await pass(() => through({
      sweepManifests: async (...args) => {
        await env.FILES.sweepManifests(...args);
        throw new Error("the answer was lost");
      } }));
    expect(await objectsOf(ns, first)).toEqual([]);
    expect(lostAnswer.sweep).toBe(1);
    expect(lostAnswer.sweep_retry).toBeGreaterThan(lostAnswer.now);
    expect(lostAnswer.next).toBe(lostAnswer.sweep_retry);
    // At its retry, on the fake clock: another owed while it runs (a prune between pages) is
    // not settled by it.
    await unnamed(ns, key, second);
    vi.useFakeTimers({ toFake: ["Date"], now: lostAnswer.sweep_retry! * 1000 });
    const meanwhile = await pass((state) => through({
      sweepManifests: (...args) => {
        state.storage.sql.exec("UPDATE owner_backup_state SET sweep = sweep + 1");
        return env.FILES.sweepManifests(...args);
      } }));
    expect(await objectsOf(ns, second)).toEqual([]);
    expect([meanwhile.sweep, meanwhile.next]).toEqual([2, meanwhile.now]);
    const settled = await pass(() => env.FILES);
    expect([settled.sweep, settled.sweep_retry]).toEqual([0, null]);
    expect(await objectsOf(ns, kept.gen)).toContain(manifestKey(ns, kept.gen));
  });
});
