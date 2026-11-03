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
import { MANIFEST_CEILING, manifestDay, spread } from "../src/backup";
import { EXPORT_CHANGED, EXPORT_LIFE_S, exported, NOT_EXPORTED } from "../src/export";
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
    const [{ clock }] = await sqlIn(o.stub, "SELECT clock FROM owner_backup_state") as
      [{ clock: number }];
    expect(lines[0]).toMatchObject({ t: "header", format: "tinyassets-export", version: 1,
                                     schema: MIGRATIONS.length, owner: o.name,
                                     export_id: exportId, clock });
    const chunkBytes = await fetched(o, lines, exportId);
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
    const whole = await exportAll(o);
    expect(whole.cursors.length).toBeGreaterThan(0);
    // From each page's cursor, the rest of the manifest, exactly.
    const pages: Line[][] = [];
    for (let next: string | null = null; ;) {
      const page = await o.stub.ownerExport(o.session(), next === null ? { cursor: null }
        : { cursor: next, export_id: whole.exportId });
      pages.push(page.lines.map((line) => JSON.parse(line) as Line));
      if (page.next === null) {
        break;
      }
      next = page.next;
    }
    for (const [at, cursor] of whole.cursors.entries()) {
      const rest = await exportAll(o, cursor, whole.exportId);
      expect(rest.lines).toEqual(pages.slice(at + 1).flat());
    }
    // A later page names its open export; an unknown one is refused.
    expect((await failure(() => o.stub.ownerExport(o.session(), {
      cursor: whole.cursors[0]!, export_id: "0".repeat(32) }))).message)
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

/** A manifest finished long ago, so tonight's is due now. */
const LAST_YEARS = "INSERT INTO owner_backups (day, state, cursor, parts, part_list, bytes, " +
  "started_at, finished_at, clock) VALUES ('20200101', 'done', NULL, 0, '', 0, 1, 1, 0)";

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
      .toEqual(["export_id", "opened_at", "ends_at", "clock"]);
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

  it("is one snapshot: a later page, once the owner changed, is refused (review M1)",
     async () => {
    const o = await owner();
    for (let i = 0; i < 220; i++) {
      await write(o, `${W}/inline-${i}.bin`, filled(INLINE_BYTES, i));
    }
    const first = await o.stub.ownerExport(o.session(), { cursor: null });
    expect(first.next).not.toBeNull();
    // A renewal of the session, and another export opened and ended, change nothing it reads.
    await o.stub.sessionExtend(o.session());
    const other = await o.stub.ownerExport(o.session(), { cursor: null });
    await o.stub.exportEnd(o.bind(), { export_id: other.export_id });
    const second = await o.stub.ownerExport(o.session(), { cursor: first.next,
                                                            export_id: first.export_id });
    // A change between pages: the next page is refused, and a new export reads it whole.
    await write(o, `${W}/late.bin`, filled(10, 1));
    expect((await failure(() => o.stub.ownerExport(o.session(), {
      cursor: second.next ?? first.next, export_id: first.export_id }))).message)
      .toBe(EXPORT_CHANGED);
    const again = await exportAll(o);
    expect(again.lines.some((line) => line["table"] === "file_entries"
      && (line["row"] as { name: string }).name === "late.bin")).toBe(true);
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
    expect(lines.at(-1)).toEqual({ t: "end" });
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

  it("begins again, pinned anew, when the owner changes between its bursts (review M1)",
     async () => {
    const o = await owner();
    await write(o, `${W}/first.bin`, filled(INLINE_BYTES + 10, 8));
    await sqlIn(o.stub, LAST_YEARS);
    // A manifest under way, pinned to a clock the owner has since moved past.
    const day = manifestDay(Date.now() / 1000);
    await sqlIn(o.stub, "INSERT INTO owner_backups (day, state, cursor, parts, part_list, " +
                        "bytes, started_at, clock) VALUES (?, 'writing', NULL, 0, '', 0, ?, 0)",
                day, Date.now() / 1000);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });
    await drainDue(o.stub);
    const [row] = await sqlIn(o.stub, "SELECT state, clock FROM owner_backups WHERE day = ?",
                              day);
    const [{ clock }] = await sqlIn(o.stub, "SELECT clock FROM owner_backup_state") as
      [{ clock: number }];
    // Begun again at the clock as it stood, and finished: the stream names the change.
    expect(row).toEqual({ state: "done", clock });
    expect(clock).toBeGreaterThan(0);
    const [space] = await sqlIn(o.stub, "SELECT ns FROM file_space");
    const root = (await bucket().getManifestRoot(space!["ns"] as string, day))!;
    const text = Buffer.concat(await Promise.all(root.parts.map((part, at) =>
      bucket().getManifestPart(space!["ns"] as string, root.key, day, at, part.sha256))))
      .toString("utf8");
    expect(JSON.parse(text.split("\n")[0]!)).toMatchObject({ t: "header", clock });
    expect(text).toContain('"name":"d"');
  });

  it("past its ceiling, is given up once and the owner told, never retried in a loop " +
     "(review L7)", async () => {
    const o = await owner();
    await write(o, `${W}/k.bin`, filled(INLINE_BYTES + 10, 9));
    await sqlIn(o.stub, LAST_YEARS);
    // A manifest under way with every part but none used already.
    const day = manifestDay(Date.now() / 1000);
    await sqlIn(o.stub, "INSERT INTO owner_backups (day, state, cursor, parts, part_list, " +
                        "bytes, started_at, clock) VALUES (?, 'writing', NULL, ?, '', 0, ?, 0)",
                day, MANIFEST_PARTS, Date.now() / 1000);
    await o.stub.fileMkdir(o.bind(), { path: `${W}/d`, expected_etag: null });  // its job set
    // Pinned to the clock as it stands, nothing changed since.
    await sqlIn(o.stub, "UPDATE owner_backups SET clock = (SELECT clock FROM " +
                        "owner_backup_state) WHERE day = ?", day);
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
