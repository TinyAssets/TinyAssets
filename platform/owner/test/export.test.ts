/**
 * The owner export and the nightly manifest (slice P; `export.ts`, `backup.ts`): one stream
 * of everything the owner holds, resumable, the owner's alone; the same stream without the
 * chunks' bytes written to R2 each night by the owner's own schedule, and gone with the
 * owner's key at erasure.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { manifestDay, spread } from "../src/backup";
import { exported, NOT_EXPORTED } from "../src/export";
import type FilesService from "../../files/src/index";
import { CHUNK_BYTES, INLINE_BYTES } from "../src/files";
import type { Binding, OwnerDO } from "../src/owner-do";
import { MIGRATIONS } from "../src/schema";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { drainDue, failure, laneAt, sqlIn } from "./support";
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
    ({ owner: name, caller: name, origin,
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

/** Every page from `cursor` to the end: their lines, and each page's next cursor. */
async function exportAll(o: Owner, cursor: string | null = null):
    Promise<{ lines: Line[]; cursors: string[] }> {
  const lines: Line[] = [];
  const cursors: string[] = [];
  for (let next: string | null = cursor; ;) {
    const page = await o.stub.ownerExport(o.session(), { cursor: next });
    lines.push(...page.lines.map((line) => JSON.parse(line) as Line));
    if (page.next === null) {
      return { lines, cursors };
    }
    cursors.push(page.next);
    next = page.next;
  }
}

/** A file's bytes as the stream holds them: its content's inline bytes, or its chunks'. */
function bytesOf(lines: Line[], path: string): Uint8Array {
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
  return Buffer.concat(chunks.map((chunk) => Buffer.from(lines.find((line) =>
    line.t === "chunk" && line["sha256"] === chunk["sha256"])!["data"] as string, "base64")));
}

describe("the owner export", () => {
  it("streams every exported table's rows and every chunk's bytes, which round-trip", async () => {
    const o = await owner();
    const small = filled(100, 1);
    const big = filled(INLINE_BYTES + 5000, 2);
    await write(o, `${W}/small.txt`, small);
    await write(o, `${W}/big.bin`, big);
    await o.stub.collectionWrite(o.bind(), { collection: "board", row_id: "t1", body: "hello",
                                             expected_etag: "", private: false });
    const { lines } = await exportAll(o);
    expect(lines[0]).toMatchObject({ t: "header", format: "tinyassets-export", version: 1,
                                     schema: MIGRATIONS.length, owner: o.name });
    expect(lines.at(-1)).toEqual({ t: "end" });
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
    expect(bytesOf(lines, `${W}/small.txt`)).toEqual(Buffer.from(small));
    expect(bytesOf(lines, `${W}/big.bin`)).toEqual(Buffer.from(big));
    expect(lines.some((line) => line["table"] === "collection_rows"
      && (line["row"] as { body: string }).body === "hello")).toBe(true);
    // Nothing kept out appears: no table, no namespace, no data key, no session hash.
    const [space] = await sqlIn(o.stub, "SELECT ns, data_key FROM file_space");
    const text = JSON.stringify(lines);
    for (const secret of [space!["ns"], space!["data_key"]] as string[]) {
      expect(text).not.toContain(secret);
    }
    const [session] = await sqlIn(o.stub, "SELECT session_hash FROM owner_sessions");
    expect(text).not.toContain(session!["session_hash"] as string);
    expect(lines.filter((line) => line.t === "row" && Object.hasOwn(NOT_EXPORTED,
                                                                    line["table"] as string)))
      .toEqual([]);
  });

  it("is resumable from any page's cursor, and refuses a cursor it never handed out",
     async () => {
    const o = await owner();
    // Three chunks of 4 MiB: no one page carries them all (8 MiB of chunks a page).
    for (const seed of [3, 4, 5]) {
      await write(o, `${W}/large-${seed}.bin`, filled(CHUNK_BYTES, seed));
    }
    const whole = await exportAll(o);
    expect(whole.cursors.length).toBeGreaterThan(0);
    const chunkLines = whole.lines.filter((line) => line.t === "chunk");
    expect(chunkLines).toHaveLength(3);
    // From each page's cursor, the rest of the stream, exactly.
    const pages: Line[][] = [];
    for (let next: string | null = null; ;) {
      const page = await o.stub.ownerExport(o.session(), { cursor: next });
      pages.push(page.lines.map((line) => JSON.parse(line) as Line));
      if (page.next === null) {
        break;
      }
      next = page.next;
    }
    for (const [at, cursor] of whole.cursors.entries()) {
      const rest = await exportAll(o, cursor);
      expect(rest.lines).toEqual(pages.slice(at + 1).flat());
    }
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
      .message).toBe("an export request is at most its cursor");
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
    await sqlIn(o.stub, "INSERT INTO owner_backups (day, state, cursor, parts, bytes, " +
                        "started_at, finished_at) VALUES ('20200101', 'done', NULL, 0, 0, 1, 1)");
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });  // re-derives jobs
    await drainDue(o.stub);
    const day = manifestDay(Date.now() / 1000);
    expect(await sqlIn(o.stub, "SELECT day, state FROM owner_backups"))
      .toEqual([{ day, state: "done" }]);
    expect(await laneAt(o.stub, "manifest")).toBe(tomorrow + spread(o.name));
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
    expect(lines.at(-1)).toEqual({ t: "end" });
    // The same stream as the export's, the chunks' bytes left in the bucket.
    const chunks = lines.filter((line) => line.t === "chunk");
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).not.toHaveProperty("data");
    expect(lines.some((line) => line["table"] === "file_entries"
      && (line["row"] as { name: string }).name === "kept.bin")).toBe(true);
  });

  it("is deleted with every copy of the owner's key when the owner is erased", async () => {
    const o = await owner();
    await write(o, `${W}/gone.bin`, filled(INLINE_BYTES + 10, 6));
    await sqlIn(o.stub, "INSERT INTO owner_backups (day, state, cursor, parts, bytes, " +
                        "started_at, finished_at) VALUES ('20200101', 'done', NULL, 0, 0, 1, 1)");
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
