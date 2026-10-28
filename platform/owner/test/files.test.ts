/**
 * The owner's file manifest at its doors (plan §6): compare-and-swap writes, versions
 * that never repeat, verbatim bytes and names, the inline boundary, and every file door
 * admitted through the binding.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { describe, expect, it } from "vitest";

import { CHUNK_BYTES, type Entry, INLINE_BYTES, sha256Hex } from "../src/files";
import type { Binding, OwnerDO } from "../src/owner-do";
import { FILE_DEFAULTS } from "../src/policy";
import { apiKeyOrigin } from "../src/ledger";
import { MUSE, rig, type Rig } from "./ledger-support";
import { failure, fileCounters, inFiles, laneAt, ownerObject, sqlIn, text } from "./support";

const W = "/workspace";

function entryOf(answer: { entry: Entry } | { conflict: Entry | null }): Entry {
  if (!("entry" in answer)) {
    throw new Error(`expected a commit, got a conflict with ${JSON.stringify(answer.conflict)}`);
  }
  return answer.entry;
}

async function write(r: Rig, path: string, content: Uint8Array | string,
                     expected: string | null = null, binding: Binding = r.bind(null)) {
  return r.stub.fileWrite(binding, { path, expected_etag: expected,
                                     content: typeof content === "string" ? text(content) : content });
}

/** A file's whole bytes through the read doors: inline, or its chunks by version. */
async function readAll(r: Rig, path: string): Promise<Uint8Array> {
  const read = await r.stub.fileRead(r.bind(null), { path });
  if (read.inline !== undefined) {
    return read.inline;
  }
  const pieces: Uint8Array[] = [];
  for (let first = 0; first < read.chunks!.length; first += 6) {
    pieces.push(...(await r.stub.fileReadChunks(r.bind(null), {
      path, version: read.entry.version, first,
      count: Math.min(6, read.chunks!.length - first) })).chunks);
  }
  const out = new Uint8Array(pieces.reduce((sum, piece) => sum + piece.byteLength, 0));
  let at = 0;
  for (const piece of pieces) {
    out.set(piece, at);
    at += piece.byteLength;
  }
  return out;
}

describe("a file write", () => {
  it("creates only with an empty etag and swaps only on the current one", async () => {
    const r = await rig();
    const made = entryOf(await write(r, `${W}/a.txt`, "one", ""));
    expect(made).toMatchObject({ path: `${W}/a.txt`, kind: "file", size: 3, mode: 0,
                                 writer: "owner" });
    // A second create, and a swap on a stale etag, are answered with the entry as it stands.
    expect(await write(r, `${W}/a.txt`, "two", "")).toEqual({ conflict: made });
    expect(await write(r, `${W}/a.txt`, "two", "f".repeat(64))).toEqual({ conflict: made });
    const next = entryOf(await write(r, `${W}/a.txt`, "two", made.etag));
    expect(await write(r, `${W}/a.txt`, "three", made.etag)).toEqual({ conflict: next });
    expect(new TextDecoder().decode(await readAll(r, `${W}/a.txt`))).toBe("two");
    // An etag for a missing file is a conflict carrying null.
    expect(await write(r, `${W}/b.txt`, "x", made.etag)).toEqual({ conflict: null });
  });

  it("writes unconditionally with a null etag", async () => {
    const r = await rig();
    await write(r, `${W}/a.txt`, "one");
    const second = entryOf(await write(r, `${W}/a.txt`, "two"));
    expect(second.size).toBe(3);
    expect(new TextDecoder().decode(await readAll(r, `${W}/a.txt`))).toBe("two");
  });

  it("stamps every version with a new seq that never repeats across a delete", async () => {
    const r = await rig();
    const versions: number[] = [];
    versions.push(entryOf(await write(r, `${W}/a.txt`, "one")).version);
    versions.push(entryOf(await write(r, `${W}/a.txt`, "one")).version);
    const { etag } = (await r.stub.fileStat(r.bind(null), { path: `${W}/a.txt` })).entry!;
    expect(await r.stub.fileRemove(r.bind(null), { path: `${W}/a.txt`, expected_etag: etag,
                                                   recursive: false })).toEqual({ removed: 1 });
    versions.push(entryOf(await write(r, `${W}/a.txt`, "one", "")).version);
    expect(versions).toEqual([...versions].sort((a, b) => a - b));
    expect(new Set(versions).size).toBe(3);
    const history = await r.stub.fileHistory(r.bind(null), { path: `${W}/a.txt`, limit: 10 });
    expect(history.changes.map((c) => [c.op, c.kind])).toEqual(
      [["write", "file"], ["delete", null], ["write", "file"], ["write", "file"]]);
    expect(new Set(history.changes.map((c) => c.version)).size).toBe(4);
  });

  it("keeps the seq rising in the store itself", async () => {
    const r = await rig();
    await write(r, `${W}/a.txt`, "one");
    for (const seq of [0, 1]) {
      await expect(sqlIn(r.stub, "UPDATE file_space SET seq = ?", seq)).rejects
        .toThrow(/a file version never repeats/);
    }
  });

  it("changes the etag on every commit, the same content included", async () => {
    const r = await rig();
    const etags = new Set<string>();
    for (let i = 0; i < 3; i++) {
      etags.add(entryOf(await write(r, `${W}/same.txt`, "same")).etag);
    }
    expect(etags.size).toBe(3);
  });

  it("keeps content verbatim: every byte value, invalid UTF-8, a BOM and CRLF", async () => {
    const r = await rig();
    const all = Uint8Array.from({ length: 256 }, (_, i) => i);
    const odd = Uint8Array.from([0xef, 0xbb, 0xbf, 0x61, 0x0d, 0x0a, 0xc3, 0x28, 0xff, 0x0a]);
    const large = new Uint8Array(3 * INLINE_BYTES + 7);
    for (let i = 0; i < large.length; i++) {
      large[i] = (i * 31) % 256;
    }
    for (const [name, body] of [["all.bin", all], ["odd.txt", odd], ["large.bin", large]] as const) {
      await write(r, `${W}/${name}`, body);
      expect(await readAll(r, `${W}/${name}`)).toEqual(body);
    }
  });

  it("keeps 4,096 bytes inline and puts 4,097 in a chunk the files Worker holds", async () => {
    const r = await rig();
    const small = new Uint8Array(INLINE_BYTES).fill(1);
    const big = new Uint8Array(INLINE_BYTES + 1).fill(1);
    await write(r, `${W}/small`, small);
    await write(r, `${W}/big`, big);
    const small_ = await r.stub.fileRead(r.bind(null), { path: `${W}/small` });
    const big_ = await r.stub.fileRead(r.bind(null), { path: `${W}/big` });
    expect(small_.inline).toEqual(small);
    expect(small_.chunks).toBeUndefined();
    expect(big_.inline).toBeUndefined();
    expect(big_.chunks).toEqual([{ sha256: sha256Hex(big), size: INLINE_BYTES + 1 }]);
    const [space] = await sqlIn(r.stub, "SELECT ns, inline_bytes, chunk_bytes FROM file_space");
    expect(space).toMatchObject({ inline_bytes: INLINE_BYTES, chunk_bytes: INLINE_BYTES + 1 });
    expect(await env.FILES.getChunks(space!["ns"] as string, [{ sha256: sha256Hex(big) }]))
      .toEqual([big]);
    expect(await sqlIn(r.stub, "SELECT sha256, state, stored, refs, holds FROM file_chunks"))
      .toEqual([{ sha256: sha256Hex(big), state: "live", stored: 1, refs: 1, holds: 0 }]);
  });

  it("keeps each owner's chunks under a random namespace of its own, made once", async () => {
    const one = await rig();
    const other = await rig();
    for (const r of [one, other]) {
      await write(r, `${W}/big`, new Uint8Array(INLINE_BYTES + 1).fill(1));
      await write(r, `${W}/big2`, new Uint8Array(INLINE_BYTES + 1).fill(2));
    }
    const [a] = await sqlIn(one.stub, "SELECT ns FROM file_space");
    const [b] = await sqlIn(other.stub, "SELECT ns FROM file_space");
    expect(a!["ns"]).toMatch(/^[0-9a-f]{32}$/);
    expect(a!["ns"]).not.toBe(b!["ns"]);
    expect(await inFiles(one.stub, {}, (files) => files.ns())).toBe(a!["ns"]);
  });

  it("answers a read with only the entry while its etag is current", async () => {
    const r = await rig();
    const made = entryOf(await write(r, `${W}/a.txt`, "one"));
    expect(await r.stub.fileRead(r.bind(null), { path: `${W}/a.txt`, etag: made.etag }))
      .toEqual({ entry: made, not_modified: true });
    expect((await r.stub.fileRead(r.bind(null), { path: `${W}/a.txt`, etag: "old" })).inline)
      .toEqual(text("one"));
  });

  it("keeps a version's chunks readable by version after an overwrite", async () => {
    const r = await rig();
    const first = new Uint8Array(INLINE_BYTES + 1).fill(7);
    const v1 = entryOf(await write(r, `${W}/v.bin`, first)).version;
    await write(r, `${W}/v.bin`, new Uint8Array(INLINE_BYTES + 1).fill(8));
    expect((await r.stub.fileReadChunks(r.bind(null), { path: `${W}/v.bin`, version: v1,
                                                         first: 0, count: 1 })).chunks)
      .toEqual([first]);
    for (const bad of [{ first: 1, count: 1 }, { first: 0, count: 0 }, { first: 0, count: 3 }]) {
      expect((await failure(() => r.stub.fileReadChunks(r.bind(null), {
        path: `${W}/v.bin`, version: v1, ...bad }))).message)
        .toBe("first and count name 1 or 2 of the version's chunks");
    }
  });

  it("reads at most two chunks a door call, of a file with more", async () => {
    const r = await rig();
    const bytes = new Uint8Array(2 * CHUNK_BYTES + 1).fill(4);
    const { version } = await inFiles(r.stub, {}, (files) => files.write(
      `${W}/three.bin`, bytes, { expected: null, writer: "owner" }));
    const read = (first: number, count: number) => r.stub.fileReadChunks(
      r.bind(null), { path: `${W}/three.bin`, version, first, count });
    expect((await read(1, 2)).chunks.map((chunk) => chunk.byteLength)).toEqual([CHUNK_BYTES, 1]);
    expect((await failure(() => read(0, 3))).message)
      .toBe("first and count name 1 or 2 of the version's chunks");
  });

  it("serves door reads and writes that interleave at their FILES calls", async () => {
    const r = await rig();
    const version = entryOf(await write(r, `${W}/one.bin`,
                                        new Uint8Array(INLINE_BYTES + 1).fill(6))).version;
    const read = () => r.stub.fileReadChunks(r.bind(null), { path: `${W}/one.bin`, version,
                                                             first: 0, count: 1 });
    // Each door's account is a unit of its own, and its FILES calls run between units, so
    // doors at once never wait on each other's calls.
    const together = await Promise.all([
      ...Array.from({ length: 8 }, read),
      ...Array.from({ length: 8 }, (_, i) => write(r, `${W}/w${i}.bin`,
                                                   new Uint8Array(INLINE_BYTES + 1).fill(i))),
    ]);
    expect(together.slice(0, 8).every((answer) =>
      "chunks" in answer && answer.chunks[0]!.byteLength === INLINE_BYTES + 1)).toBe(true);
    expect(together.slice(8).map((answer) => entryOf(answer as never).size))
      .toEqual(Array(8).fill(INLINE_BYTES + 1));
  });

  it("keeps the mode it had when an overwrite names none", async () => {
    const r = await rig();
    await r.stub.fileWrite(r.bind(null), { path: `${W}/run.sh`, expected_etag: null,
                                           content: text("#!/bin/sh"), mode: 1 });
    expect(entryOf(await write(r, `${W}/run.sh`, "#!/bin/sh\n")).mode).toBe(1);
    expect((await failure(() => r.stub.fileWrite(r.bind(null), {
      path: `${W}/run.sh`, expected_etag: null, content: text("x"), mode: 2 }))).message)
      .toBe("mode is 0 or 1");
  });

  it("refuses a file over a directory, and a directory under a file", async () => {
    const r = await rig();
    await r.stub.fileMkdir(r.bind(null), { path: `${W}/d`, expected_etag: null });
    await write(r, `${W}/f`, "x");
    expect((await failure(() => write(r, `${W}/d`, "x"))).message)
      .toBe("is_directory: is a directory");
    expect((await failure(() => write(r, `${W}/f/inner`, "x"))).message)
      .toBe("not_directory: a parent is not a directory");
    expect((await failure(() => write(r, W, "x"))).message)
      .toBe("is_directory: a root is a directory");
  });

  it("refuses content that is not bytes, or past one chunk at the write door", async () => {
    const r = await rig();
    for (const content of ["text", [1, 2], new Uint8Array(4 * 1024 * 1024 + 1)]) {
      expect((await failure(() => r.stub.fileWrite(r.bind(null), {
        path: `${W}/x`, expected_etag: null, content: content as Uint8Array }))).message)
        .toBe("a written file is bytes, at most 4 MiB; larger ones are uploaded");
    }
  });
});

describe("a file path", () => {
  it("is kept verbatim: composed and decomposed names are two files", async () => {
    const r = await rig();
    const composed = `${W}/café.txt`;
    const decomposed = `${W}/café.txt`;
    await write(r, composed, "composed");
    await write(r, decomposed, "decomposed");
    expect(new TextDecoder().decode(await readAll(r, composed))).toBe("composed");
    expect(new TextDecoder().decode(await readAll(r, decomposed))).toBe("decomposed");
    expect((await r.stub.fileList(r.bind(null), { dir: W, limit: 10 })).entries
      .map((e) => e.path)).toEqual([decomposed, composed]);
  });

  it.each([
    ["a relative path", "workspace/a"],
    ["a dot component", `${W}/./a`],
    ["a dot-dot component", `${W}/../etc/passwd`],
    ["an empty component", `${W}//a`],
    ["a trailing slash", `${W}/a/`],
    ["a NUL", `${W}/a\u0000b`],
    ["a lone surrogate", `${W}/a\ud800`],
    ["a name past 255 bytes", `${W}/${"é".repeat(128)}`],
    ["a path past 4096 bytes", `${W}/${"a/".repeat(2046)}a`],
    ["a path outside the roots", "/etc/passwd"],
    ["the temporary area's parent", "/.ta"],
    ["a prefix of a root", "/workspaces/a"],
    ["not text", 7],
  ])("refuses %s", async (_what, path) => {
    const r = await rig();
    const error = await failure(() => r.stub.fileStat(r.bind(null), { path: path as string }));
    expect(error.name).toBe("Invalid");
    expect(error.message).toMatch(/^a path /);
  });

  it("takes a name of exactly 255 bytes", async () => {
    const r = await rig();
    expect(entryOf(await write(r, `${W}/${"a".repeat(255)}`, "x")).size).toBe(1);
  });
});

/** Every file door, with a request that passes its fields. */
function doors(r: Rig): Record<string, (binding: Binding) => Promise<unknown>> {
  const s = r.stub;
  const path = `${W}/a.txt`;
  return {
    fileStat: (b) => s.fileStat(b, { path }),
    fileList: (b) => s.fileList(b, { dir: W, limit: 1 }),
    fileRead: (b) => s.fileRead(b, { path }),
    fileReadChunks: (b) => s.fileReadChunks(b, { path, version: 1, first: 0, count: 1 }),
    fileHistory: (b) => s.fileHistory(b, { path, limit: 1 }),
    fileChanges: (b) => s.fileChanges(b, { after: 0, limit: 1 }),
    fileUsage: (b) => s.fileUsage(b),
    fileWrite: (b) => s.fileWrite(b, { path, expected_etag: null, content: text("x") }),
    fileUploadBegin: (b) => s.fileUploadBegin(b, { size: 5000, chunks: ["a".repeat(64)] }),
    fileUploadChunks: (b) => s.fileUploadChunks(b, { intent_id: "0".repeat(32), chunks: [] }),
    fileUploadCommit: (b) => s.fileUploadCommit(b, { intent_id: "0".repeat(32), path,
      expected_etag: null }),
    fileUploadAbort: (b) => s.fileUploadAbort(b, { intent_id: "0".repeat(32) }),
    fileMkdir: (b) => s.fileMkdir(b, { path: `${W}/d`, expected_etag: null }),
    fileRemove: (b) => s.fileRemove(b, { path, expected_etag: null, recursive: false }),
    fileRename: (b) => s.fileRename(b, { from: path, to: `${W}/b.txt`, expected_etag: null,
                                          replace: false }),
    fileRestore: (b) => s.fileRestore(b, { path, version: 1, expected_etag: null }),
    fileRedact: (b) => s.fileRedact(b, { path, version: 1 }),
    fileHistoryPurge: (b) => s.fileHistoryPurge(b, { path }),
    fileLeaseAcquire: (b) => s.fileLeaseAcquire(b, { holder: "sandbox" }),
    fileLeaseRenew: (b) => s.fileLeaseRenew(b, { lease_id: "0".repeat(32), epoch: 1 }),
    fileLeaseRelease: (b) => s.fileLeaseRelease(b, { lease_id: "0".repeat(32), epoch: 1 }),
    fileSyncCommit: (b) => s.fileSyncCommit(b, { lease_id: "0".repeat(32), epoch: 1,
      ops: [{ op: "mkdir", path: `${W}/s`, base: null }] }),
  };
}

const READS = ["fileStat", "fileList", "fileRead", "fileReadChunks", "fileHistory",
               "fileChanges", "fileUsage"];
const OWNER_ONLY = ["fileRedact", "fileHistoryPurge", "fileLeaseAcquire", "fileLeaseRenew", "fileLeaseRelease",
                    "fileSyncCommit"];
const ADMISSION = /belongs to another owner|not named|own callers only|not granted|only the owner|never carries|not one enabled agent|no grant|revoked/;

async function outcome(call: () => Promise<unknown>): Promise<string> {
  return call().then(() => "served", (error: Error) => error.message);
}

describe("every file door", () => {
  it("is admitted through the binding: another owner, a visitor and a nameless object refused",
     async () => {
    const r = await rig();
    const unnamed = env.OWNER.get(env.OWNER.newUniqueId());
    for (const [name, call] of Object.entries(doors(r))) {
      expect([name, await outcome(() => call({ owner: "someone-else", caller: "someone-else",
                                               origin: null }))])
        .toEqual([name, "this object belongs to another owner"]);
      expect([name, await outcome(() => call({ owner: r.owner, caller: "visitor", origin: null }))])
        .toEqual([name, "the account answers its owner's own callers only"]);
      const other = doors({ ...r, stub: unnamed } as Rig)[name]!;
      expect([name, await outcome(() => other({ owner: r.owner, caller: r.owner, origin: null }))])
        .toEqual([name, "this object is not named by an owner"]);
    }
    expect(await fileCounters(r.stub).then(([kept]) => kept))
      .toMatchObject({ entries: 0, history: 0, chunk_bytes: 0 });
  });

  it("holds a scoped outside client to read_files or write_files, and the owner's own doors",
     async () => {
    const r = await rig();
    await r.register(MUSE);
    const scope = async (capability: string) => {
      const [row] = (await r.stub.grantList(r.bind(null))).clients;
      await r.stub.grantSet(r.session(null), {
        client: MUSE.client, source: "mcp", mode: "scoped",
        scopes: [{ universe: "alpha", agent: "main", capability }],
        expected_generation: row!.generation, name: "" });
    };
    for (const granted of ["read_files", "write_files"]) {
      await scope(granted);
      for (const [name, call] of Object.entries(doors(r))) {
        const answer = await outcome(() => call(r.bind(MUSE)));
        if (OWNER_ONLY.includes(name)) {
          expect([name, answer]).toEqual([name, expect.stringMatching(/^only the owner may /)]);
        } else if (READS.includes(name) === (granted === "read_files")) {
          expect([name, ADMISSION.test(answer)]).toEqual([name, false]);
        } else {
          expect([name, answer]).toEqual([name, "outside client capability not granted"]);
        }
      }
    }
  });

  it("refuses an outside client carrying the owner's session, and an ungranted API key",
     async () => {
    const r = await rig();
    const key = apiKeyOrigin("0123456789abcdef");
    for (const [name, call] of Object.entries(doors(r))) {
      expect([name, await outcome(() => call(r.session(MUSE)))])
        .toEqual([name, "an outside client's call never carries the owner's session"]);
      expect([name, await outcome(() => call(r.bind(key, {
        source: { kind: "api_key", ref: "0123456789abcdef" } })))])
        .toEqual([name, "outside client has no grant"]);
    }
  });

  it("writes an outside client's changes as that client's", async () => {
    const r = await rig();
    await r.register(MUSE);
    expect(entryOf(await write(r, `${W}/a.txt`, "x", null, r.bind(MUSE))).writer)
      .toBe(`client:${MUSE.client}`);
  });

  it("checks a writer's authority in the unit that writes, not only where its door began",
     async () => {
    const r = await rig();
    await r.register(MUSE);
    const [row] = (await r.stub.grantList(r.bind(null))).clients;
    const answer = await runInDurableObject(r.stub, async (instance: OwnerDO, state) => {
      // A pi commit holds the queue: the write's door queues its check, then the owner's
      // narrowing of the client's grant queues behind it, before the write itself.
      let release!: () => void;
      const held = new Promise<void>((resolve) => { release = resolve; });
      const commit = new DurableObjectSqliteDatabase(state.storage).transaction(() => held);
      const writing = instance.fileWrite(r.bind(MUSE), {
        path: `${W}/a.txt`, expected_etag: null, content: text("x") })
        .then(() => "written", (error: Error) => error.message);
      const narrowing = instance.grantSet(r.session(null), {
        client: MUSE.client, source: "mcp", mode: "scoped",
        scopes: [{ universe: "alpha", agent: "main", capability: "read_files" }],
        expected_generation: row!.generation, name: "" });
      release();
      await Promise.all([commit, narrowing]);
      return writing;
    });
    expect(answer).toBe("outside client capability not granted");
    expect(await sqlIn(r.stub, "SELECT name FROM file_entries WHERE name = 'a.txt'"))
      .toEqual([]);
  });

  it("sets the files lane's job from the rows after a whole write, as every file door does",
     async () => {
    const r = await rig();
    // An open upload: the lane's job waits for its expiry, which the rows then bring due.
    await r.stub.fileUploadBegin(r.bind(null), { size: INLINE_BYTES + 1,
                                                 chunks: [sha256Hex(text("x"))] });
    expect(await laneAt(r.stub, "files")).toBeGreaterThan(Date.now() / 1000 + 60);
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    await write(r, `${W}/b`, "1");
    expect(await laneAt(r.stub, "files")).toBeLessThanOrEqual(Date.now() / 1000);
  });

  it("refuses a request with a field it does not take", async () => {
    const r = await rig();
    for (const request of [{ path: `${W}/a`, owner: "x" }, {}, null, [`${W}/a`]]) {
      expect((await failure(() => r.stub.fileStat(r.bind(null), request as never))).message)
        .toBe("invalid file request");
    }
  });
});

describe("the manifest's queries", () => {
  it("read by key, never by scanning a table", async () => {
    const { stub, as, name } = ownerObject();
    await stub.fileMkdir(as(name), { path: `${W}/d/e`, expected_etag: null });
    const plans = await runInDurableObject(stub, (_i: OwnerDO, state) => {
      const plan = (query: string, ...params: SqlStorageValue[]) => state.storage.sql
        .exec<{ detail: string }>(`EXPLAIN QUERY PLAN ${query}`, ...params).toArray()
        .map((row) => row.detail).join("; ");
      return [
        plan("SELECT * FROM file_entries WHERE parent = ? AND name = ?", W, "d"),
        plan("SELECT * FROM file_entries WHERE parent = ? AND name > ? ORDER BY name LIMIT ?",
             W, "", 10),
        plan("SELECT * FROM file_entries WHERE parent >= ? AND parent < ?", `${W}/d/`, `${W}/d0`),
        plan("SELECT version FROM file_history WHERE path = ? ORDER BY version DESC LIMIT ?",
             `${W}/d`, 10),
        plan("SELECT version, path FROM file_history WHERE version > ? AND version <= ? " +
               "ORDER BY version LIMIT ?", 0, 10, 10),
        plan("SELECT MIN(prune_after) FROM file_history WHERE prune_after IS NOT NULL"),
        plan("SELECT version, path FROM file_history WHERE released = 1 " +
               "ORDER BY prune_after, version LIMIT ?", 10),
        plan("SELECT MIN(gc_after) FROM file_chunks WHERE gc_after IS NOT NULL"),
        plan("SELECT MIN(expires_at) FROM file_intents"),
        plan("SELECT MIN(expires_at) FROM file_temps"),
      ];
    });
    for (const plan of plans) {
      expect(plan).not.toMatch(/SCAN (?!.*USING)|TEMP B-TREE/);
    }
    expect(plans[6]).toMatch(/USING INDEX file_history_released/);
  });

  it("keep every counter equal to a recount", async () => {
    const r = await rig();
    await write(r, `${W}/a/b.txt`, "x");
    await write(r, `${W}/a/c.bin`, new Uint8Array(INLINE_BYTES * 2).fill(3));
    await write(r, `${W}/a/c.bin`, new Uint8Array(INLINE_BYTES * 2).fill(4));
    await r.stub.fileRename(r.bind(null), { from: `${W}/a`, to: `${W}/z`, expected_etag: null,
                                            replace: false });
    await r.stub.fileRemove(r.bind(null), { path: `${W}/z/b.txt`, expected_etag: null,
                                            recursive: false });
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
    await inFiles(r.stub, { keep_versions: 1, retention_days: 0 }, async (files) => {
      await files.write(`${W}/z/c.bin`, new Uint8Array(INLINE_BYTES * 2).fill(5),
                        { expected: null, writer: "t" });
      await files.collect(Date.now() / 1000 + 10);
    });
    const [after, recount] = await fileCounters(r.stub);
    expect(after).toEqual(recount);
    expect(FILE_DEFAULTS.keep_versions).toBe(10);
  });
});
