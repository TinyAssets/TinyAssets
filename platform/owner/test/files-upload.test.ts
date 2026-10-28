/**
 * Uploads, chunk reference counts and garbage collection, and history retention (plan
 * §6): an intent records its chunks before any byte is sent, so nothing in the bucket
 * lacks a row and garbage collection never lists it.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import {
  CHUNK_BYTES, type ChunkStore, chunkedId, type Entry, GC_GRACE_S, HOUSEKEEPING_SLACK_S,
  INLINE_BYTES, INTENT_TTL_S, sha256Hex,
} from "../src/files";
import { MIGRATIONS } from "../src/schema";
import { rig, type Rig } from "./ledger-support";
import { alarmOf, drainDue, failure, fileCounters, inFiles, laneAt, sqlIn, text } from "./support";

const W = "/workspace";

function entryOf(answer: { entry: Entry } | { conflict: Entry | null }): Entry {
  if (!("entry" in answer)) {
    throw new Error("expected a commit");
  }
  return answer.entry;
}

function filled(size: number, seed: number): Uint8Array {
  const bytes = new Uint8Array(size);
  for (let i = 0; i < size; i++) {
    bytes[i] = (i * 7 + seed) % 256;
  }
  return bytes;
}

const ref = (bytes: Uint8Array) => ({ sha256: sha256Hex(bytes), size: bytes.byteLength });

async function upload(r: Rig, path: string, pieces: Uint8Array[],
                      expected: string | null = null) {
  const begun = await r.stub.fileUploadBegin(r.bind(null), { chunks: pieces.map(ref) });
  const missing = pieces.filter((piece) => begun.missing.includes(sha256Hex(piece)));
  if (missing.length > 0) {
    await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
      chunks: missing.map((piece) => ({ sha256: sha256Hex(piece), bytes: piece })) });
  }
  const size = pieces.reduce((sum, piece) => sum + piece.byteLength, 0);
  return { begun, committed: await r.stub.fileUploadCommit(r.bind(null), {
    intent_id: begun.intent_id, path, expected_etag: expected, size,
    chunks: pieces.map((piece) => sha256Hex(piece)) }) };
}

const chunk = (r: Rig, sha256: string) =>
  sqlIn(r.stub, "SELECT state, stored, refs, holds, gc_after FROM file_chunks WHERE sha256 = ?",
        sha256).then((rows) => rows[0]);
const ns = async (r: Rig) => (await sqlIn(r.stub, "SELECT ns FROM file_space"))[0]!["ns"] as string;
const inBucket = async (r: Rig, sha256: string) => {
  try {
    await env.FILES.getChunks(await ns(r), [{ sha256 }]);
    return true;
  } catch {
    return false;
  }
};

describe("the schema's file ceilings", () => {
  it("are the manifest's own: the inline bound, the chunk size and the grace", () => {
    const schema = MIGRATIONS.flat().join(" ");
    expect(schema).toContain(`CHECK ((inline IS NOT NULL) = (size <= ${INLINE_BYTES}))`);
    expect(schema).toContain(`CHECK (size BETWEEN 1 AND ${CHUNK_BYTES})`);
    expect(schema).toContain(`unixepoch('subsec') + ${GC_GRACE_S}`);
  });
});

describe("an upload", () => {
  it("names the chunks to send, then commits them as one file", async () => {
    const r = await rig();
    const pieces = [filled(CHUNK_BYTES, 1), filled(10, 2)];
    const { begun, committed } = await upload(r, `${W}/two.bin`, pieces);
    expect(begun.missing).toEqual(pieces.map((piece) => sha256Hex(piece)));
    expect(begun.expires_at).toBeGreaterThan(Date.now() / 1000 + INTENT_TTL_S - 60);
    expect(entryOf(committed)).toMatchObject({ size: CHUNK_BYTES + 10, kind: "file" });
    const read = await r.stub.fileRead(r.bind(null), { path: `${W}/two.bin` });
    expect(read.chunks).toEqual(pieces.map(ref));
    expect((await r.stub.fileReadChunks(r.bind(null), { path: `${W}/two.bin`,
      version: read.entry.version, first: 1, count: 1 })).chunks).toEqual([pieces[1]]);
    expect(await sqlIn(r.stub, "SELECT content_id FROM file_contents"))
      .toEqual([{ content_id: chunkedId(CHUNK_BYTES + 10, pieces.map((p) => sha256Hex(p))) }]);
    // The same chunks again send nothing, and dedupe into the same content.
    const again = await upload(r, `${W}/copy.bin`, pieces);
    expect(again.begun.missing).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_contents")).toEqual([{ n: 1 }]);
    expect(await chunk(r, sha256Hex(pieces[1]!))).toMatchObject({ state: "live", refs: 1,
                                                                   holds: 0 });
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it.each([
    ["a chunk the intent does not hold", (p: Uint8Array[]) => ({ chunks: [sha256Hex(filled(9, 9)),
      sha256Hex(p[1]!)] }), "a commit names its intent's chunks, one for each 4 MiB of the file"],
    ["too few chunks", (p: Uint8Array[]) => ({ chunks: [sha256Hex(p[0]!)] }),
      "a commit names its intent's chunks, one for each 4 MiB of the file"],
    ["chunks out of order", (p: Uint8Array[]) => ({ chunks: [sha256Hex(p[1]!), sha256Hex(p[0]!)] }),
      "the chunks do not make up the file's size"],
    ["a size the chunks do not make", () => ({ size: CHUNK_BYTES + 11 }),
      "the chunks do not make up the file's size"],
    ["a file small enough to be inline", () => ({ size: INLINE_BYTES, chunks: [] }),
      "size is a whole number from 4097 to 9007199254740991"],
  ])("refuses a commit naming %s, and changes nothing", async (_what, change, message) => {
    const r = await rig();
    const pieces = [filled(CHUNK_BYTES, 1), filled(10, 2)];
    const begun = await r.stub.fileUploadBegin(r.bind(null), { chunks: pieces.map(ref) });
    await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
      chunks: pieces.map((piece) => ({ sha256: sha256Hex(piece), bytes: piece })) });
    const request = { intent_id: begun.intent_id, path: `${W}/x`, expected_etag: null,
                      size: CHUNK_BYTES + 10, chunks: pieces.map((p) => sha256Hex(p)),
                      ...change(pieces) };
    expect((await failure(() => r.stub.fileUploadCommit(r.bind(null), request))).message)
      .toBe(message);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_entries")).toEqual([{ n: 0 }]);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_intents")).toEqual([{ n: 1 }]);
  });

  it("refuses a commit after its intent was committed, abandoned or expired", async () => {
    const r = await rig();
    const pieces = [filled(INLINE_BYTES + 1, 3)];
    const { begun } = await upload(r, `${W}/a`, pieces);
    const commit = (intent: string) => r.stub.fileUploadCommit(r.bind(null), {
      intent_id: intent, path: `${W}/b`, expected_etag: null, size: INLINE_BYTES + 1,
      chunks: [sha256Hex(pieces[0]!)] });
    const gone = "no such upload: it was committed, abandoned or has expired";
    expect((await failure(() => commit(begun.intent_id))).message).toBe(gone);
    const abandoned = await r.stub.fileUploadBegin(r.bind(null), { chunks: pieces.map(ref) });
    expect(await r.stub.fileUploadAbort(r.bind(null), { intent_id: abandoned.intent_id }))
      .toEqual({ aborted: true });
    expect(await r.stub.fileUploadAbort(r.bind(null), { intent_id: abandoned.intent_id }))
      .toEqual({ aborted: false });
    expect((await failure(() => commit(abandoned.intent_id))).message).toBe(gone);
    const expired = await r.stub.fileUploadBegin(r.bind(null), { chunks: pieces.map(ref) });
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    expect((await failure(() => commit(expired.intent_id))).message).toBe(gone);
    expect((await failure(() => r.stub.fileUploadChunks(r.bind(null), {
      intent_id: expired.intent_id, chunks: [] }))).message).toBe(gone);
    expect((await failure(() => commit("x"))).message)
      .toBe("an intent id is 32 lowercase hex characters");
  });

  it("checks each chunk's bytes: its declared size, its intent, and R2's hash check", async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 4);
    const begun = await r.stub.fileUploadBegin(r.bind(null), { chunks: [ref(piece)] });
    const send = (chunks: { sha256: string; bytes: Uint8Array }[]) =>
      r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id, chunks });
    expect((await failure(() => send([{ sha256: sha256Hex(piece), bytes: piece.subarray(1) }])))
      .message).toBe("a chunk's bytes are the size its upload declared");
    expect((await failure(() => send([{ sha256: "e".repeat(64), bytes: piece }]))).message)
      .toBe("an upload sends only its intent's chunks");
    const forged = piece.slice();
    forged[0] = forged[0]! ^ 1;
    expect((await failure(() => send([{ sha256: sha256Hex(piece), bytes: forged }]))).message)
      .toBe("the chunk's bytes do not match its sha256");
    expect(await send([{ sha256: sha256Hex(piece), bytes: piece }])).toEqual({ created: 1 });
  });

  it.each([
    ["no chunks", []],
    ["a bad hash", [{ sha256: "X".repeat(64), size: 1 }]],
    ["an empty chunk", [{ sha256: "a".repeat(64), size: 0 }]],
    ["a chunk past 4 MiB", [{ sha256: "a".repeat(64), size: CHUNK_BYTES + 1 }]],
    ["one hash with two sizes", [{ sha256: "a".repeat(64), size: 1 },
                                 { sha256: "a".repeat(64), size: 2 }]],
  ])("refuses a begin with %s", async (_what, chunks) => {
    const r = await rig();
    expect((await failure(() => r.stub.fileUploadBegin(r.bind(null), { chunks }))).name)
      .toBe("Invalid");
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_chunks")).toEqual([{ n: 0 }]);
  });
});

describe("a chunk", () => {
  it("whose content is gone becomes garbage, revives when wanted, and goes after grace",
     async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 5);
    const sha = sha256Hex(piece);
    const first = entryOf((await upload(r, `${W}/a`, [piece])).committed);
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null, content: text("x") });
    // The old version still holds it, until its content is redacted.
    expect(await chunk(r, sha)).toMatchObject({ state: "live", refs: 1 });
    await r.stub.fileRedact(r.bind(null), { path: `${W}/a`, version: first.version });
    const garbage = await chunk(r, sha);
    expect(garbage).toMatchObject({ state: "garbage", stored: 1, refs: 0, holds: 0 });
    expect(garbage!["gc_after"] as number).toBeGreaterThan(Date.now() / 1000 + GC_GRACE_S - 60);
    // A stored garbage chunk is not sent again: a new upload revives it.
    const revived = await upload(r, `${W}/b`, [piece]);
    expect(revived.begun.missing).toEqual([]);
    expect(await chunk(r, sha)).toMatchObject({ state: "live", refs: 1, gc_after: null });
    await r.stub.fileRemove(r.bind(null), { path: `${W}/b`, expected_etag: null, recursive: false });
    await inFiles(r.stub, { retention_days: 0 }, async (files) => {
      await files.collect(Date.now() / 1000);  // the delete's history is not due yet
    });
    await sqlIn(r.stub, "UPDATE file_history SET prune_after = 1 WHERE prune_after IS NOT NULL");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await chunk(r, sha)).toMatchObject({ state: "garbage" });
    expect(await inBucket(r, sha)).toBe(true);  // inside its grace
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1 WHERE sha256 = ?", sha);
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await chunk(r, sha)).toBeUndefined();
    expect(await inBucket(r, sha)).toBe(false);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("from an upload that never committed is garbage that is sent again, never revived",
     async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 6);
    const sha = sha256Hex(piece);
    const begun = await r.stub.fileUploadBegin(r.bind(null), { chunks: [ref(piece)] });
    expect(await chunk(r, sha)).toMatchObject({ state: "uploading", stored: 0, holds: 1 });
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await sqlIn(r.stub, "SELECT * FROM file_intents")).toEqual([]);
    expect(await chunk(r, sha)).toMatchObject({ state: "garbage", stored: 0, holds: 0 });
    const again = await r.stub.fileUploadBegin(r.bind(null), { chunks: [ref(piece)] });
    expect(again.missing).toEqual([sha]);
    expect(again.intent_id).not.toBe(begun.intent_id);
    expect(await chunk(r, sha)).toMatchObject({ state: "uploading", holds: 1, gc_after: null });
  });

  it("being deleted is never held or referenced; its delete is tried again when it fails",
     async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 7);
    const sha = sha256Hex(piece);
    await r.stub.fileUploadBegin(r.bind(null), { chunks: [ref(piece)] });
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
    const failing: ChunkStore = {
      putChunks: (space, chunks) => env.FILES.putChunks(space, chunks),
      getChunks: (space, reads) => env.FILES.getChunks(space, reads),
      deleteChunks: async () => { throw new Error("FILES is down"); },
    };
    expect((await failure(() => inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000),
                                        failing))).message).toBe("FILES is down");
    const deleting = await chunk(r, sha);
    expect(deleting).toMatchObject({ state: "deleting" });
    expect(deleting!["gc_after"] as number).toBeGreaterThan(Date.now() / 1000);
    expect((await failure(() => r.stub.fileUploadBegin(r.bind(null), { chunks: [ref(piece)] })))
      .message).toBe("a chunk of this upload is being deleted; begin again");
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await chunk(r, sha)).toBeUndefined();
    expect((await r.stub.fileUploadBegin(r.bind(null), { chunks: [ref(piece)] })).missing)
      .toEqual([sha]);
  });

  it("is deleted by the object's one alarm, which a change arms", async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 8);
    const sha = sha256Hex(piece);
    const first = entryOf((await upload(r, `${W}/a`, [piece])).committed);
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null, content: text("x") });
    await r.stub.fileRedact(r.bind(null), { path: `${W}/a`, version: first.version });
    const due = (await chunk(r, sha))!["gc_after"] as number;
    const armed = await laneAt(r.stub, "files");
    expect(armed).not.toBeNull();
    expect(armed!).toBeLessThanOrEqual(Math.ceil((due + HOUSEKEEPING_SLACK_S) * 1000) / 1000);
    expect(await alarmOf(r.stub)).toBeLessThanOrEqual(armed!);  // Lifecycle armed it
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
    // Any change re-derives the lane's job from the rows: due now, so Lifecycle runs it.
    await r.stub.fileMkdir(r.bind(null), { path: `${W}/d`, expected_etag: null });
    await drainDue(r.stub);
    expect(await chunk(r, sha)).toBeUndefined();
    expect(await inBucket(r, sha)).toBe(false);
  });

  it("is never deleted while content refers to it, whatever its age", async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 9);
    await upload(r, `${W}/a`, [piece]);
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000 + 400 * 86_400));
    expect(await chunk(r, sha256Hex(piece))).toMatchObject({ state: "live", refs: 1 });
    expect(await inBucket(r, sha256Hex(piece))).toBe(true);
  });
});

describe("history retention", () => {
  it("keeps each live path's newest versions and every version younger than its days",
     async () => {
    const r = await rig();
    for (let i = 0; i < 12; i++) {
      await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null,
                                             content: text(`v${i}`) });
    }
    const rows = await sqlIn(r.stub, "SELECT version, at, prune_after FROM file_history " +
                                       "WHERE path = ? ORDER BY version", `${W}/a`);
    expect(rows.slice(2).every((row) => row["prune_after"] === null)).toBe(true);
    for (const row of rows.slice(0, 2)) {
      expect(row["prune_after"]).toBe((row["at"] as number) + 30 * 86_400);
    }
    // Due rows go at the alarm; the newest ten stay whatever their age.
    await sqlIn(r.stub, "UPDATE file_history SET prune_after = 1 WHERE prune_after IS NOT NULL");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect((await r.stub.fileHistory(r.bind(null), { path: `${W}/a`, limit: 100 })).changes
      .map((c) => c.version)).toEqual(rows.slice(2).map((row) => row["version"]).reverse());
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("keeps a deleted path's history for its days, and a re-created path's newest again",
     async () => {
    const r = await rig();
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null, content: text("1") });
    await r.stub.fileRemove(r.bind(null), { path: `${W}/a`, expected_etag: null, recursive: false });
    const due = await sqlIn(r.stub, "SELECT at, prune_after FROM file_history WHERE path = ?",
                            `${W}/a`);
    expect(due.every((row) => row["prune_after"] === (row["at"] as number) + 30 * 86_400))
      .toBe(true);
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null, content: text("2") });
    expect(await sqlIn(r.stub, "SELECT prune_after FROM file_history WHERE path = ?", `${W}/a`))
      .toEqual([{ prune_after: null }, { prune_after: null }, { prune_after: null }]);
  });

  it("follows the owner's policy for how many versions and days it keeps", async () => {
    const r = await rig();
    await inFiles(r.stub, { keep_versions: 2, retention_days: 1 }, async (files) => {
      for (let i = 0; i < 4; i++) {
        await files.write(`${W}/a`, text(`v${i}`), { expected: null, writer: "owner" });
      }
    });
    const rows = await sqlIn(r.stub, "SELECT at, prune_after FROM file_history ORDER BY version");
    expect(rows.map((row) => row["prune_after"] === null)).toEqual([false, false, true, true]);
    expect(rows[0]!["prune_after"]).toBe((rows[0]!["at"] as number) + 86_400);
  });

  it("restores a kept version as a new one, and never redacts the current one", async () => {
    const r = await rig();
    const one = entryOf(await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null,
                                                               content: text("one") }));
    const two = entryOf(await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null,
                                                               content: text("two") }));
    expect(await r.stub.fileRestore(r.bind(null), { path: `${W}/a`, version: one.version,
                                                    expected_etag: one.etag }))
      .toEqual({ conflict: two });
    const restored = entryOf(await r.stub.fileRestore(r.bind(null), {
      path: `${W}/a`, version: one.version, expected_etag: two.etag }));
    expect(restored.version).toBeGreaterThan(two.version);
    expect((await r.stub.fileRead(r.bind(null), { path: `${W}/a` })).inline).toEqual(text("one"));
    expect((await failure(() => r.stub.fileRedact(r.bind(null), { path: `${W}/a`,
                                                                  version: restored.version })))
      .message).toBe("the current version is never redacted; write a new one first");
    expect(await r.stub.fileRedact(r.bind(null), { path: `${W}/a`, version: two.version }))
      .toEqual({ redacted: two.version });
    expect((await failure(() => r.stub.fileRestore(r.bind(null), { path: `${W}/a`,
      version: two.version, expected_etag: null }))).message)
      .toBe("that version holds no file content");
    expect((await failure(() => r.stub.fileRedact(r.bind(null), { path: `${W}/a`,
                                                                  version: two.version })))
      .message).toBe("that version holds no content");
    expect((await failure(() => r.stub.fileRestore(r.bind(null), { path: `${W}/b`,
      version: one.version, expected_etag: null }))).message).toBe("not_found: no such version");
    await expect(sqlIn(r.stub, "UPDATE file_history SET op = 'write'")).rejects
      .toThrow(/never changed/);
    await expect(sqlIn(r.stub, "UPDATE file_history SET content_id = 'x'")).rejects
      .toThrow(/never changed/);
  });
});
