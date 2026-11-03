/**
 * The owner export and the nightly manifest (slice P; `export.ts`, `backup.ts`): one stream
 * of everything the owner holds, resumable, the owner's alone; the same stream without the
 * chunks' bytes written to R2 each night by the owner's own schedule, and gone with the
 * owner's key at erasure.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { mint } from "../../files/src/datakey";
import { MANIFEST_PARTS } from "../../files/src/keys";
import { PLATFORM_CALLER, PLATFORM_ONLY } from "../src/admission";
import {
  Backups, MANIFEST_CEILING, MANIFEST_CONTINUE_S, MANIFEST_UNSETTLED, manifestDay, spread,
} from "../src/backup";
import { WriteGate } from "../src/gate";
import { DIRTY_BYTES_MAX, Streams, STREAM_UNSETTLED } from "../src/streams";
import { EXPORT_LIFE_S, exported, NOT_EXPORTED } from "../src/export";
import type FilesService from "../../files/src/index";
import { CHUNK_BYTES, CHUNK_GONE, GC_GRACE_S, INLINE_BYTES } from "../src/files";
import type { Binding, OwnerDO } from "../src/owner-do";
import { MIGRATIONS } from "../src/schema";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { drainDue, failure, laneAt, sqlIn, TIERS } from "./support";
import type { FauxOwnerDO } from "./worker";

type Line = Record<string, unknown> & { t: string };

/** Tables the schema does not make: Lifecycle's, the runtime's and SQLite's own. */
const runtimeTable = (name: string) => name === "schema_version" || name.startsWith("sqlite_")
  || name.startsWith("_cf_") || name.startsWith("cf_agents_");

/** The files Worker's manifest reads, which only a recovery and these tests make. */
const bucket = () => env.FILES as unknown as Pick<FilesService, "getManifestRoot" |
                                                                  "getManifestPart">;

/** An owner of its own on the class whose erasure is in reach, signed in, with a session. */
async function owner() {
  const name = `owner-${crypto.randomUUID()}`;
  const stub: DurableObjectStub<FauxOwnerDO> = env.AGENT.getByName(name);
  const at = signedIn();
  const bind = (origin: typeof MUSE | null = null, extra: Partial<Binding> = {}): Binding =>
    ({ owner: name, caller: name, origin, tiers: TIERS,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }), ...extra });
  const { session } = await stub.sessionOpen(bind(null, { authenticated_at: at }));
  return { name, stub, bind, session: () => bind(null, { session }) };
}
type Owner = Awaited<ReturnType<typeof owner>>;

const W = "/workspace";
const filled = (size: number, seed: number) =>
  Uint8Array.from({ length: size }, (_, i) => (i * 31 + seed) % 251);

async function write(o: Owner, path: string, content: Uint8Array): Promise<void> {
  await o.stub.fileWrite(o.bind(), { path, expected_etag: null, content });
}

/** Every page from `cursor` to the end: their lines, each page's next cursor, the export. */
async function exportAll(o: Owner, cursor: string | null = null, exportId?: string):
    Promise<{ lines: Line[]; cursors: string[]; exportId: string }> {
  const lines: Line[] = [];
  const cursors: string[] = [];
  let id = exportId;
  for (let next: string | null = cursor; ;) {
    const page = await o.stub.ownerExport(o.session(), next === null ? { cursor: null }
      : { cursor: next, export_id: id! });
    id = page.export_id;
    lines.push(...page.lines.map((line) => JSON.parse(line) as Line));
    if (page.next === null) {
      return { lines, cursors, exportId: id };
    }
    cursors.push(page.next);
    next = page.next;
  }
}

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

/** A file's bytes as the export holds them: its content's inline bytes, or its chunks'. */
function bytesOf(lines: Line[], path: string, chunkBytes: Map<string, Buffer>): Uint8Array {
  const rows = (table: string) => lines.filter((line) => line.t === "row"
    && line["table"] === table).map((line) => line["row"] as Record<string, unknown>);
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
                                          policy: { ...rest, hooks: { codemode: false } } });
}

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
    const { lines, exportId } = await exportAll(o);
    expect(lines[0]).toMatchObject({ t: "header", format: "tinyassets-export", version: 1,
                                     schema: MIGRATIONS.length, owner: o.name,
                                     export_id: exportId });
    const chunkBytes = await fetched(o, lines, exportId);
    expect(lines.at(-1)).toMatchObject({ t: "end" });
    expect(lines.at(-1)!["at"]).toBeGreaterThan(0);
    expect(lines.filter((line) => line.t === "header" || line.t === "end")).toHaveLength(2);
    // Each exported table's rows are exactly its rows, columns as listed, in key order.
    const tables = exported();
    for (const [table, { columns }] of Object.entries(tables)) {
      const stored = await sqlIn(o.stub, `SELECT ${columns.join(", ")} FROM ${table} ` +
                                         `ORDER BY ${tables[table]!.key.join(", ")}`);
      const streamed = lines.filter((line) => line.t === "row" && line["table"] === table)
        .map((line) => line["row"]);
      expect(streamed.map((row) => JSON.stringify(row))).toEqual(stored.map((row) =>
        JSON.stringify(Object.fromEntries(columns.map((column) => {
          const value = row[column];
          return [column, value instanceof ArrayBuffer
            ? { $base64: Buffer.from(value).toString("base64") } : value];
        })))));
    }
    // The files come back byte for byte, and the collection row as written.
    expect(bytesOf(lines, `${W}/small.txt`, chunkBytes)).toEqual(Buffer.from(small));
    expect(bytesOf(lines, `${W}/big.bin`, chunkBytes)).toEqual(Buffer.from(big));
    expect(lines.some((line) => line["table"] === "collection_rows"
      && (line["row"] as { body: string }).body === "hello")).toBe(true);
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

  it("is resumable from any page's cursor, and refuses a cursor it never handed out",
     async () => {
    const o = await owner();
    // Small files kept inline, about 5.5 KB of JSON each: past one page's 1 MiB of rows.
    for (let i = 0; i < 220; i++) {
      await write(o, `${W}/inline-${i}.bin`, filled(INLINE_BYTES, i));
    }
    // Each page asked again from its cursor is the same page, the rounds' among them.
    const first = await o.stub.ownerExport(o.session(), { cursor: null });
    const cursors: string[] = [];
    let pages = 1;
    for (let next = first.next; next !== null; pages++) {
      cursors.push(next);
      const page = await o.stub.ownerExport(o.session(), { cursor: next,
                                                           export_id: first.export_id });
      if (page.next !== null) {
        expect(await o.stub.ownerExport(o.session(), { cursor: next,
                                                       export_id: first.export_id }))
          .toEqual(page);
      }
      next = page.next;
    }
    expect(pages).toBeGreaterThan(1);
    // A later page names its open export; an unknown one, or one ended, is refused.
    expect((await failure(() => o.stub.ownerExport(o.session(), {
      cursor: cursors[0]!, export_id: "0".repeat(32) }))).message)
      .toBe("this export is not open: begin it again");
    expect((await failure(() => o.stub.ownerExport(o.session(), {
      cursor: cursors[0]!, export_id: first.export_id }))).message)
      .toBe("this export is not open: begin it again");
    for (const bad of ["x", "Zm9v", Buffer.from("[1,999,null]").toString("base64url"),
                       Buffer.from("[2,0,null]").toString("base64url"), "a".repeat(20_000)]) {
      expect((await failure(() => o.stub.ownerExport(o.session(), { cursor: bad }))).message)
        .toBe("an export cursor is one a page handed out");
    }
  });

  it("is the owner's alone, in its session: no outside client, no call without the session",
     async () => {
    const o = await owner();
    await o.stub.outsideAgents(o.bind(MUSE));
    expect((await failure(() => o.stub.ownerExport(o.bind(MUSE), {}))).message)
      .toBe("only the owner may export everything it holds");
    expect((await failure(() => o.stub.ownerExport(o.bind(), {}))).name).toBe("Refused");
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
});

describe("the streams' journal", () => {
  it("journals every exported table's changed keys, by the key the export reads, and no " +
     "other table's (review N1)", async () => {
    const o = await owner();
    const triggers = await sqlIn(o.stub, "SELECT name, tbl_name, sql FROM sqlite_master " +
                                         "WHERE type = 'trigger' AND name GLOB '*_streamed_*'");
    const tables = exported();
    expect([...new Set(triggers.map((row) => row["tbl_name"] as string))].sort())
      .toEqual(Object.keys(tables).sort());
    for (const [table, { key }] of Object.entries(tables)) {
      const of = (event: string) => triggers.find((row) =>
        row["name"] === `${table}_streamed_${event}`)!["sql"] as string;
      const keyed = (row: string) => `json_array(${key.map((column) => `${row}.${column}`)
        .join(", ")})`;
      expect(of("insert")).toContain(keyed("NEW"));
      expect(of("delete")).toContain(keyed("OLD"));
      expect(of("update")).toContain(keyed("OLD"));
      expect(of("update")).toContain(keyed("NEW"));
    }
    // With no stream open, a write journals nothing.
    await write(o, `${W}/quiet.txt`, filled(10, 1));
    expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM stream_dirty")).toEqual([{ n: 0 }]);
  });
});

/** A manifest finished long ago, so tonight's is due now. */
const LAST_YEARS = "INSERT INTO owner_backups (day, state, cursor, parts, part_list, bytes, " +
  "started_at, finished_at) VALUES ('20200101', 'done', NULL, 0, '', 0, 1, 1)";

/** Rows as a reader keeps them: each table's rows by key, the last line for a key winning. */
type Rows = Record<string, Record<string, string>>;

/** A stream's lines replayed, last line per (table, key) winning, `gone` taking one away. */
function replay(lines: Line[]): Rows {
  const tables = exported();
  const out: Rows = Object.fromEntries(Object.keys(tables).map((table) => [table, {}]));
  for (const line of lines) {
    const table = line["table"] as string;
    if (line.t === "row") {
      const row = line["row"] as Record<string, unknown>;
      out[table]![JSON.stringify(tables[table]!.key.map((column) => row[column]))] =
        JSON.stringify(row);
    } else if (line.t === "gone") {
      delete out[table]![JSON.stringify(line["key"])];
    }
  }
  return out;
}

/** The owner's exported rows as they stand, keyed as `replay` keys them. */
function standing(sql: Pick<SqlStorage, "exec">): Rows {
  const tables = exported();
  return Object.fromEntries(Object.entries(tables).map(([table, { columns, key }]) => [
    table, Object.fromEntries(sql.exec<Record<string, SqlStorageValue>>(
      `SELECT ${[...new Set([...columns, ...key])].join(", ")} FROM ${table}`).toArray()
      .map((row) => [JSON.stringify(key.map((column) => row[column])), JSON.stringify(
        Object.fromEntries(columns.map((column) => {
          const value = row[column];
          return [column, value instanceof ArrayBuffer
            ? { $base64: Buffer.from(value).toString("base64") } : value];
        })))]))]));
}

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

describe("an open export", () => {
  it("holds no chunk: garbage written after it opened goes on its own grace (review H1)",
     async () => {
    const o = await owner();
    // An export open on an owner with nothing in R2: its pages name no chunk.
    const first = await o.stub.ownerExport(o.session(), { cursor: null });
    expect(first.lines.some((line) => line.includes('"t":"chunk"'))).toBe(false);
    // A file written and deleted after it opened: garbage, in no owner's storage number.
    const sha = await writeThenDelete(o, `${W}/after.bin`, 3);
    expect(await stateOf(o, sha)).toEqual([{ state: "garbage" }]);
    expect((await o.stub.storageUsage(o.bind())).used_bytes).toBe(0);
    // Its grace passed (30 days): the sweep takes it, the export open or not, and opening
    // export after export renews nothing.
    for (let i = 0; i < 3; i++) {
      await o.stub.ownerExport(o.session(), { cursor: null });
    }
    await sqlIn(o.stub, "UPDATE file_chunks SET gc_after = 1 WHERE sha256 = ?", sha);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/x`, expected_etag: null });
    await drainDue(o.stub);
    expect(await stateOf(o, sha)).toEqual([]);
    // Nothing in the export's row could hold one.
    expect((await sqlIn(o.stub, "PRAGMA table_info(owner_exports)")).map((row) => row["name"]))
      .toEqual(["export_id", "opened_at", "ends_at"]);
  });

  it("lives under the garbage grace, so every chunk its pages name is still there",
     async () => {
    expect(EXPORT_LIFE_S).toBeLessThan(GC_GRACE_S);
    const o = await owner();
    const big = filled(INLINE_BYTES + 10, 7);
    await write(o, `${W}/named.bin`, big);
    const { lines, exportId } = await exportAll(o);
    const sha = lines.find((line) => line.t === "chunk")!["sha256"] as string;
    const [{ ends_at: endsAt }] = await sqlIn(o.stub, "SELECT ends_at FROM owner_exports") as
      [{ ends_at: number }];
    // The file goes after its page was read: its chunk is garbage, due only past the export.
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

  it("ends as the owner at one moment though the owner writes between every page " +
     "(review N1)", async () => {
    const o = await owner();
    for (let i = 0; i < 440; i++) {
      await write(o, `${W}/inline-${i}.bin`, filled(INLINE_BYTES, i));
    }
    // A writer between every page: a board write (one a second, as an agent's), a frame
    // batch (twenty rows at once), a file overwritten and one deleted.
    const lines: Line[] = [];
    let pages = 0;
    let exportId = "";
    for (let next: string | null = null; ; pages++) {
      const page = await o.stub.ownerExport(o.session(), next === null ? { cursor: null }
        : { cursor: next, export_id: exportId });
      exportId = page.export_id;
      lines.push(...page.lines.map((line) => JSON.parse(line) as Line));
      if (page.next === null) {
        break;
      }
      next = page.next;
      expect(pages).toBeLessThan(40);
      await o.stub.collectionWrite(o.bind(), { collection: "board", row_id: `p${pages}`,
                                               body: `page ${pages}`, expected_etag: "",
                                               private: false });
      await sqlIn(o.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
                          "WHERE i < 20) INSERT INTO owner_notices (at, type, detail) " +
                          "SELECT ?, 'frame', 'batch ' || i FROM n", pages);
      await write(o, `${W}/inline-${pages}.bin`, filled(INLINE_BYTES, 1000 + pages));
      await o.stub.fileRemove(o.bind(), { path: `${W}/inline-${439 - pages}.bin`,
                                          expected_etag: null, recursive: false });
    }
    expect(pages).toBeGreaterThan(1);
    // Rounds re-emitted what changed, and a delete as gone.
    expect(lines.some((line) => line.t === "gone")).toBe(true);
    expect(lines.at(-1)).toMatchObject({ t: "end" });
    // Replayed, the stream is exactly the owner as it stands: nothing wrote after its end.
    const now = await runInDurableObject(o.stub, (_instance: OwnerDO, state) =>
      standing(state.storage.sql));
    expect(replay(lines)).toEqual(now);
    // Its journal went with it.
    expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM stream_dirty")).toEqual([{ n: 0 }]);
    expect(await sqlIn(o.stub, "SELECT COUNT(*) AS n FROM owner_streams")).toEqual([{ n: 0 }]);
  });

  it("serves a page asked again, and fails, never loops, once the owner outruns it",
     async () => {
    const o = await owner();
    for (let i = 0; i < 220; i++) {
      await write(o, `${W}/inline-${i}.bin`, filled(INLINE_BYTES, i));
    }
    const first = await o.stub.ownerExport(o.session(), { cursor: null });
    const again = await o.stub.ownerExport(o.session(), { cursor: first.next,
                                                           export_id: first.export_id });
    expect(await o.stub.ownerExport(o.session(), { cursor: first.next,
                                                   export_id: first.export_id }))
      .toEqual(again);
    // The owner changed past what one stream may journal: the export fails, loudly.
    await sqlIn(o.stub, "UPDATE owner_streams SET journaled = ?", DIRTY_BYTES_MAX);
    expect((await failure(() => o.stub.ownerExport(o.session(), {
      cursor: again.next, export_id: first.export_id }))).message).toBe(STREAM_UNSETTLED);
    // A new export replaces its stream; the old one's pages are no longer served.
    const fresh = await exportAll(o);
    expect(fresh.lines.at(-1)).toMatchObject({ t: "end" });
    expect((await failure(() => o.stub.ownerExport(o.session(), {
      cursor: again.next, export_id: first.export_id }))).message)
      .toBe("this export is not open: begin it again");
  });
});

describe("the nightly manifest", () => {
  it("is written by the owner's own schedule, under its key, and the older one let go",
     async () => {
    const o = await owner();
    await write(o, `${W}/kept.bin`, filled(INLINE_BYTES + 10, 5));
    const now = Date.now() / 1000;
    const tomorrow = Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(),
                              new Date().getUTCDate() + 1) / 1000;
    // Due the next night, at the owner's own hour of it: one job, no poll.
    expect(await laneAt(o.stub, "manifest")).toBe(tomorrow + spread(o.name));
    expect(await laneAt(o.stub, "manifest")).toBeGreaterThan(now);
    // Last night's was written long ago: tonight's is due now.
    await sqlIn(o.stub, LAST_YEARS);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });  // re-derives jobs
    await drainDue(o.stub);
    const day = manifestDay(Date.now() / 1000);
    expect(await sqlIn(o.stub, "SELECT day, state FROM owner_backups"))
      .toEqual([{ day, state: "done" }]);
    // Nothing changed since it began: no job, and the idle owner's object is never woken.
    expect(await laneAt(o.stub, "manifest")).toBeNull();
    // The root names its parts and carries the wrapped key; the parts read under it.
    const [space] = await sqlIn(o.stub, "SELECT ns, data_key FROM file_space");
    const [ns, key] = [space!["ns"] as string, space!["data_key"] as string];
    const root = (await bucket().getManifestRoot(ns, day))!;
    expect(root).toMatchObject({ format: "tinyassets-manifest", day, key });
    const parts: Uint8Array[] = await Promise.all(root.parts.map((part, at) =>
      bucket().getManifestPart(ns, root.key, day, at, part.sha256)));
    const lines: Line[] = Buffer.concat(parts).toString("utf8").trimEnd().split("\n")
      .map((line: string) => JSON.parse(line) as Line);
    expect(lines[0]).toMatchObject({ t: "header", owner: o.name });
    expect(lines.at(-1)).toMatchObject({ t: "end" });
    // The same stream as the export's, the chunks' bytes left in the bucket.
    const chunks = lines.filter((line) => line.t === "chunk");
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).not.toHaveProperty("data");
    expect(lines.some((line) => line["table"] === "file_entries"
      && (line["row"] as { name: string }).name === "kept.bin")).toBe(true);
    // A read changes nothing: still no job.
    await o.stub.fileStat(o.bind(), { path: `${W}/kept.bin` });
    expect(await sqlIn(o.stub, "SELECT changed FROM owner_backup_state")).toEqual([{ changed: 0 }]);
    expect(await laneAt(o.stub, "manifest")).toBeNull();
    // A change sets it due again, the next night.
    await o.stub.fileMkdir(o.bind(), { path: `${W}/later`, expected_etag: null });
    expect(await sqlIn(o.stub, "SELECT changed FROM owner_backup_state")).toEqual([{ changed: 1 }]);
    for (let i = 0; i < 50 && await laneAt(o.stub, "manifest") === null; i++) {
      await new Promise((resolve) => setTimeout(resolve, 10));  // the mark's own sync
    }
    expect(await laneAt(o.stub, "manifest")).toBe(tomorrow + spread(o.name));
  });

  it("ends as the owner at one moment though the owner writes during every R2 put " +
     "(review N1)", async () => {
    const o = await owner();
    await sqlIn(o.stub, LAST_YEARS);
    // About 11 MB of stream: more than one burst, so R2 puts fall inside it.
    await sqlIn(o.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
      "WHERE i < 10000) INSERT INTO owner_notices (at, type, detail) SELECT 0, 'x', " +
      "printf('%.1000c', 'a') FROM n");
    const outcome = await runInDurableObject(o.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      const streams = new Streams(gate.storage);
      const parts = new Map<number, Uint8Array>();
      const before: Rows[] = [];
      let roots = 0;
      let writes = 0;
      const store = {
        // An agent's write lands while each part is on its way to R2.
        async putManifestPart(_ns: string, _key: string, _day: string, part: number,
                              bytes: Uint8Array) {
          before.push(standing(state.storage.sql));
          await gate.write(() => {
            writes++;
            gate.storage.sql.exec("INSERT INTO owner_notices (at, type, detail) VALUES " +
                                  "(1, 'agent', 'write')");
            gate.storage.sql.exec("UPDATE owner_notices SET detail = 'changed' WHERE seq = ?",
                                  writes);
            gate.storage.sql.exec("DELETE FROM owner_notices WHERE seq = ?", 5000 + writes);
          });
          parts.set(part, bytes.slice());
          return { sha256: "a".repeat(64), size: bytes.byteLength };
        },
        async putManifestRoot() { roots++; },
        async deleteManifest() {},
      };
      const backups = new Backups({
        storage: gate.storage, write: gate.quiet, store: store as never, streams,
        space: async () => ({ ns: "0".repeat(32), key: "v1.k.x.y" }), owner: () => o.name,
        serves: () => true, schema: MIGRATIONS.length, halted: () => false, notice: () => {},
      });
      await backups.run(Date.now() / 1000, Date.now() + 60_000);
      const text = Buffer.concat([...parts.keys()].sort((a, b) => a - b)
        .map((part) => parts.get(part)!)).toString("utf8");
      return { roots, writes, before, text,
               rows: state.storage.sql.exec("SELECT state FROM owner_backups").toArray(),
               streams: state.storage.sql.exec("SELECT COUNT(*) AS n FROM owner_streams")
                 .one().n };
    });
    // Finished in one pass, never begun again, though every put saw a write.
    expect(outcome.roots).toBe(1);
    expect(outcome.writes).toBeGreaterThan(2);
    expect(outcome.rows).toEqual([{ state: "done" }]);
    expect(outcome.streams).toBe(0);
    const lines = outcome.text.trimEnd().split("\n").map((line: string) => JSON.parse(line) as Line);
    expect(lines[0]).toMatchObject({ t: "header" });
    expect(lines.at(-1)).toMatchObject({ t: "end" });
    // Replayed, it is the owner at its end: as it stood when the last burst's puts began,
    // before the first write after the stream ended.
    const replayed = replay(lines);
    expect(outcome.before.slice(-2).some((rows) =>
      JSON.stringify(rows) === JSON.stringify(replayed))).toBe(true);
    expect(replayed["owner_notices"]!["[1]"]).toContain("changed");
  });

  it("goes on later, never at once, when a pass ends before it does; given up, never " +
     "looped, when the owner outruns it", async () => {
    const o = await owner();
    await write(o, `${W}/a.bin`, filled(INLINE_BYTES + 10, 12));
    await sqlIn(o.stub, LAST_YEARS);
    await sqlIn(o.stub, "UPDATE owner_backup_state SET changed = 1");
    const due = await runInDurableObject(o.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      const streams = new Streams(gate.storage);
      const backups = new Backups({
        storage: gate.storage, write: gate.quiet, store: env.FILES, streams,
        space: async () => ({ ns: "0".repeat(32), key: "v1.k.x.y" }), owner: () => o.name,
        serves: () => true, schema: MIGRATIONS.length, halted: () => false, notice: () => {},
      });
      const now = Date.now() / 1000;
      await backups.run(now, Date.now());  // a pass with no time left: begun, not finished
      return { next: backups.next(now)!, now };
    });
    expect(due.next).toBeGreaterThanOrEqual(due.now + MANIFEST_CONTINUE_S - 5);
    // The owner outran the stream: the manifest is given up, the owner told, no job left.
    await sqlIn(o.stub, "UPDATE owner_streams SET journaled = ?", DIRTY_BYTES_MAX);
    await sqlIn(o.stub, "UPDATE owner_backups SET retry_at = NULL WHERE state = 'writing'");
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });  // its job set
    await sqlIn(o.stub, "UPDATE owner_backup_state SET changed = 0");
    await drainDue(o.stub);
    expect(await sqlIn(o.stub, "SELECT state FROM owner_backups WHERE day != '20200101'"))
      .toEqual([]);
    const told = await o.stub.noticeList(o.bind());
    expect(told.notices.filter((notice) => notice.type === MANIFEST_UNSETTLED)).toHaveLength(1);
    expect(await laneAt(o.stub, "manifest")).toBeNull();
  });

  it("past its ceiling, is given up once and the owner told, never retried in a loop " +
     "(review L7)", async () => {
    const o = await owner();
    await write(o, `${W}/k.bin`, filled(INLINE_BYTES + 10, 9));
    await sqlIn(o.stub, LAST_YEARS);
    // A manifest under way with every part but none used already.
    const day = manifestDay(Date.now() / 1000);
    await sqlIn(o.stub, "INSERT INTO owner_backups (day, state, cursor, parts, part_list, " +
                        "bytes, started_at) VALUES (?, 'writing', NULL, ?, '', 0, ?)",
                day, MANIFEST_PARTS, Date.now() / 1000);
    await sqlIn(o.stub, "INSERT INTO owner_streams (kind, stream_id, opened_at, read_at) " +
                        "VALUES ('manifest', 'manifest', 0, 0)");
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });  // its job set
    await sqlIn(o.stub, "UPDATE owner_backup_state SET changed = 0");
    await drainDue(o.stub);
    expect(await sqlIn(o.stub, "SELECT day FROM owner_backups WHERE day = ?", day)).toEqual([]);
    const told = await o.stub.noticeList(o.bind());
    expect(told.notices.filter((notice) => notice.type === MANIFEST_CEILING)).toHaveLength(1);
    expect(await sqlIn(o.stub, "SELECT told FROM owner_backup_state")).toEqual([{ told: 1 }]);
    // No change since: no job, so no retry every few minutes.
    expect(await laneAt(o.stub, "manifest")).toBeNull();
  });

  it("is wrapped again, with the object's key, by a rotation's sweep, an idle owner's too " +
     "(review M4)", async () => {
    const o = await owner();
    // The owner's key was minted before a rotation, under the files ring's older kid.
    const ring = JSON.parse(env.FILES_TEST_RING) as { keys: Record<string, string> };
    const older = JSON.stringify({ active: "k1", keys: { k1: ring.keys["k1"] } });
    const ns = crypto.randomUUID().replaceAll("-", "");
    await sqlIn(o.stub, "UPDATE file_space SET ns = ?, data_key = ?", ns, await mint(older, ns));
    const body = filled(INLINE_BYTES + 10, 11);
    await write(o, `${W}/kept.bin`, body);
    await sqlIn(o.stub, LAST_YEARS);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });
    await drainDue(o.stub);
    const day = manifestDay(Date.now() / 1000);
    // A pass under the older kid's key writes its root under the active kid all the same.
    expect((await bucket().getManifestRoot(ns, day))!.key.startsWith("v1.k2.")).toBe(true);
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
    const { lines, exportId } = await exportAll(o);
    expect(bytesOf(lines, `${W}/kept.bin`, await fetched(o, lines, exportId)))
      .toEqual(Buffer.from(body));
  });

  it("is deleted with every copy of the owner's key when the owner is erased", async () => {
    const o = await owner();
    await write(o, `${W}/gone.bin`, filled(INLINE_BYTES + 10, 6));
    await sqlIn(o.stub, LAST_YEARS);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });
    await drainDue(o.stub);
    const day = manifestDay(Date.now() / 1000);
    const [space] = await sqlIn(o.stub, "SELECT ns, data_key FROM file_space");
    const ns = space!["ns"] as string;
    expect(await bucket().getManifestRoot(ns, day)).not.toBeNull();
    await runInDurableObject(o.stub, (instance: OwnerDO) =>
      (instance as unknown as FauxOwnerDO).erasure.erase());
    // No manifest, and no object row, holds the owner's wrapped key any more: a chunk the
    // bucket still keeps is unreadable (the files Worker's SSE-C tests hold that).
    expect(await bucket().getManifestRoot(ns, day)).toBeNull();
    expect(await sqlIn(o.stub, "SELECT name FROM sqlite_master WHERE name = 'file_space'"))
      .toEqual([]);
  });
});
