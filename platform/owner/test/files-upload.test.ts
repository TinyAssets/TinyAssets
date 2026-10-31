/**
 * Uploads, chunk reference counts and garbage collection, content identity, and history
 * retention (plan §6): an intent records its chunks before any byte is sent, a chunk is
 * stored only once the files Worker accepted it, so nothing in the bucket lacks a row,
 * nothing deduplicates against bytes the bucket lacks, and garbage collection never lists
 * the bucket.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { describe, expect, it } from "vitest";

import {
  CHUNK_BYTES, type ChunkStore, chunkedId, DELETE_FLOOR_MS, type Entry, GC_GRACE_S,
  HOUSEKEEPING_SLACK_S, INLINE_BYTES, inlineId, INTENT_TTL_S, MAX_HELD_CHUNKS, MAX_INTENT_CHUNKS,
  MAX_OPEN_INTENTS, sha256Hex, WHOLE_BYTES,
} from "../src/files";
import { WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { MIGRATIONS } from "../src/schema";
import { rig, type Rig } from "./ledger-support";
import {
  alarmOf, drainDue, failure, faulty, fileCounters, filesOn, inFiles, laneAt, lostContent, sqlIn,
  STORAGE_FAULT, text,
} from "./support";

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

const sizeOf = (pieces: Uint8Array[]) => pieces.reduce((sum, piece) => sum + piece.byteLength, 0);
const shas = (pieces: Uint8Array[]) => pieces.map((piece) => sha256Hex(piece));
const begin = (r: Rig, pieces: Uint8Array[]) =>
  r.stub.fileUploadBegin(r.bind(null), { size: sizeOf(pieces), chunks: shas(pieces) });

/** Begin, send each missing chunk, commit: what a binder does. */
async function upload(r: Rig, path: string, pieces: Uint8Array[],
                      expected: string | null = null) {
  const begun = await begin(r, pieces);
  for (const piece of pieces.filter((p) => begun.missing.includes(sha256Hex(p)))) {
    await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
      chunks: [{ sha256: sha256Hex(piece), bytes: piece }] });
  }
  return { begun, committed: await r.stub.fileUploadCommit(r.bind(null), {
    intent_id: begun.intent_id, path, expected_etag: expected }) };
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
const read = async (r: Rig, path: string) => {
  const found = await r.stub.fileRead(r.bind(null), { path });
  if (found.inline !== undefined) {
    return found.inline;
  }
  const parts = [];
  for (let first = 0; first < found.chunks!.length; first++) {
    parts.push(...(await r.stub.fileReadChunks(r.bind(null), { path, version: found.entry.version,
                                                              first, count: 1 })).chunks);
  }
  const out = new Uint8Array(sizeOf(parts));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.byteLength;
  }
  return out;
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
  it("declares its file and chunks, sends the missing ones, then commits them as one file",
     async () => {
    const r = await rig();
    const pieces = [filled(CHUNK_BYTES, 1), filled(10, 2)];
    const { begun, committed } = await upload(r, `${W}/two.bin`, pieces);
    expect(begun.missing).toEqual(shas(pieces));
    expect(begun.expires_at).toBeGreaterThan(Date.now() / 1000 + INTENT_TTL_S - 60);
    expect(entryOf(committed)).toMatchObject({ size: CHUNK_BYTES + 10, kind: "file" });
    expect(sha256Hex(await read(r, `${W}/two.bin`))).toBe(sha256Hex(new Uint8Array([
      ...pieces[0]!, ...pieces[1]!])));
    expect(await sqlIn(r.stub, "SELECT content_id FROM file_contents"))
      .toEqual([{ content_id: chunkedId(CHUNK_BYTES + 10, shas(pieces)) }]);
    // The same chunks again send nothing, and dedupe into the same content.
    const again = await upload(r, `${W}/copy.bin`, pieces);
    expect(again.begun.missing).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_contents")).toEqual([{ n: 1 }]);
    expect(await chunk(r, sha256Hex(pieces[1]!))).toMatchObject({ state: "live", stored: 1,
                                                                   refs: 1, holds: 0 });
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("refuses a commit of chunks the files Worker never accepted, and dedupes nothing on it",
     async () => {
    const r = await rig();
    const real = filled(INLINE_BYTES + 1, 3);
    // A client begins the upload and commits with no bytes sent.
    const begun = await begin(r, [real]);
    expect((await failure(() => r.stub.fileUploadCommit(r.bind(null), {
      intent_id: begun.intent_id, path: `${W}/claimed`, expected_etag: null }))).message)
      .toBe("a chunk of this commit was never stored; send its bytes first");
    expect(await chunk(r, sha256Hex(real))).toMatchObject({ state: "uploading", stored: 0 });
    expect(await r.stub.fileStat(r.bind(null), { path: `${W}/claimed` })).toEqual({ entry: null });
    // The owner's own write of the real bytes still sends them, and reads back.
    await r.stub.fileWrite(r.bind(null), { path: `${W}/real`, expected_etag: null, content: real });
    expect(await read(r, `${W}/real`)).toEqual(real);
    expect(await inBucket(r, sha256Hex(real))).toBe(true);
    // Sent now, the abandoned intent's commit lands too.
    expect(entryOf(await r.stub.fileUploadCommit(r.bind(null), {
      intent_id: begun.intent_id, path: `${W}/claimed`, expected_etag: null })).size)
      .toBe(real.length);
  });

  it("marks a chunk stored only once the files Worker accepted its put", async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 4);
    const refusing: ChunkStore = {
      putChunks: async () => { throw new Error("FILES refused the put"); },
      getChunks: (space, reads) => env.FILES.getChunks(space, reads),
      deleteChunks: (space, list) => env.FILES.deleteChunks(space, list),
    };
    expect((await failure(() => inFiles(r.stub, {}, (files) =>
      files.write(`${W}/a`, piece, { expected: null, writer: "owner" }), refusing))).message)
      .toBe("FILES refused the put");
    expect(await chunk(r, sha256Hex(piece))).toMatchObject({ stored: 0, holds: 0 });
    expect(await r.stub.fileStat(r.bind(null), { path: `${W}/a` })).toEqual({ entry: null });
  });

  it("refuses a commit after its intent was committed, abandoned or expired", async () => {
    const r = await rig();
    const pieces = [filled(INLINE_BYTES + 1, 3)];
    const { begun } = await upload(r, `${W}/a`, pieces);
    const commit = (intent: string) => r.stub.fileUploadCommit(r.bind(null), {
      intent_id: intent, path: `${W}/b`, expected_etag: null });
    const gone = "no such upload: it was committed, abandoned or has expired";
    expect((await failure(() => commit(begun.intent_id))).message).toBe(gone);
    const abandoned = await begin(r, pieces);
    expect(await r.stub.fileUploadAbort(r.bind(null), { intent_id: abandoned.intent_id }))
      .toEqual({ aborted: true });
    expect(await r.stub.fileUploadAbort(r.bind(null), { intent_id: abandoned.intent_id }))
      .toEqual({ aborted: false });
    expect((await failure(() => commit(abandoned.intent_id))).message).toBe(gone);
    const expired = await begin(r, pieces);
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
    const begun = await begin(r, [piece]);
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
    expect(await chunk(r, sha256Hex(piece))).toMatchObject({ stored: 0 });
    expect(await send([{ sha256: sha256Hex(piece), bytes: piece }])).toEqual({ created: 1 });
    expect(await chunk(r, sha256Hex(piece))).toMatchObject({ stored: 1 });
  });

  it("is the only way past 16 MiB: a whole write refuses more before holding anything",
     async () => {
    const r = await rig();
    const error = await failure(() => inFiles(r.stub, {}, (files) => files.write(
      `${W}/huge`, new Uint8Array(WHOLE_BYTES + 1), { expected: null, writer: "o" })));
    expect([WHOLE_BYTES, error.message]).toEqual([16 * 1024 * 1024,
      `a whole write moves at most ${WHOLE_BYTES} bytes; upload larger files`]);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_chunks")).toEqual([{ n: 0 }]);
  });

  it("sends at most two chunks a call", async () => {
    const r = await rig();
    const pieces = [filled(CHUNK_BYTES, 1), filled(CHUNK_BYTES, 2), filled(9, 3)];
    const begun = await begin(r, pieces);
    expect((await failure(() => r.stub.fileUploadChunks(r.bind(null), {
      intent_id: begun.intent_id,
      chunks: pieces.map((piece) => ({ sha256: sha256Hex(piece), bytes: piece })) }))).message)
      .toBe(`an upload sends at most ${2 * CHUNK_BYTES} bytes at once`);
  });

  it.each([
    ["a size small enough to be inline", INLINE_BYTES, ["a".repeat(64)]],
    ["too few chunks for its size", CHUNK_BYTES + 1, ["a".repeat(64)]],
    ["too many chunks for its size", 5000, ["a".repeat(64), "b".repeat(64)]],
    ["one-byte chunks", 5000, Array.from({ length: 5000 }, (_, i) => i.toString(16).padStart(64, "0"))],
    ["a bad hash", 5000, ["X".repeat(64)]],
    ["one hash for a whole and a part chunk", CHUNK_BYTES + 1, ["a".repeat(64), "a".repeat(64)]],
    ["no list", 5000, "a".repeat(64)],
  ])("refuses a begin with %s", async (_what, size, chunks) => {
    const r = await rig();
    expect((await failure(() => r.stub.fileUploadBegin(r.bind(null),
                                                        { size, chunks: chunks as string[] })))
      .name).toBe("Invalid");
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_chunks")).toEqual([{ n: 0 }]);
  });

  it("refuses a begin past the file size quota, or past the chunks one intent holds",
     async () => {
    const r = await rig();
    await expect(inFiles(r.stub, { max_file_bytes: CHUNK_BYTES }, (files) =>
      files.begin(CHUNK_BYTES + 1, ["a".repeat(64), "b".repeat(64)], "owner"))).rejects
      .toThrow("the file is larger than the owner's file size quota");
    const too = (MAX_INTENT_CHUNKS + 1) * CHUNK_BYTES;
    await expect(inFiles(r.stub, { max_file_bytes: too }, (files) =>
      files.begin(too, Array.from({ length: MAX_INTENT_CHUNKS + 1 },
                                  (_, i) => i.toString(16).padStart(64, "0")), "owner"))).rejects
      .toThrow(`an upload holds at most ${MAX_INTENT_CHUNKS} chunks`);
  });

  it("bounds the open uploads and the chunks they hold, by counters the schema keeps",
     async () => {
    const r = await rig();
    const reason = "too many uploads are open; commit or abort one first";
    await inFiles(r.stub, {}, (files) => {
      for (let i = 0; i < MAX_OPEN_INTENTS; i++) {
        files.begin(INLINE_BYTES + 1, [i.toString(16).padStart(64, "0")], "owner");
      }
      expect(() => files.begin(INLINE_BYTES + 1, ["f".repeat(64)], "owner")).toThrow(reason);
    });
    expect((await r.stub.fileUsage(r.bind(null))).intents).toBe(MAX_OPEN_INTENTS);
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
    // The held-chunk bound reads its counter: one short of it takes one chunk, not two.
    await inFiles(r.stub, {}, (files) => {
      const two = files.begin(CHUNK_BYTES + 1, ["a".repeat(64), "b".repeat(64)], "owner");
      expect((files.usage()).intent_chunks).toBe(2);
      files.abort(two.intent_id);
    });
    await sqlIn(r.stub, "UPDATE file_space SET intent_chunks = ?", MAX_HELD_CHUNKS - 1);
    await inFiles(r.stub, {}, (files) => {
      expect(() => files.begin(CHUNK_BYTES + 1, ["a".repeat(64), "b".repeat(64)], "owner"))
        .toThrow(reason);
      files.begin(INLINE_BYTES + 1, ["c".repeat(64)], "owner");
    });
  });
});

describe("a content id", () => {
  // The id the manifest once gave a two-chunk file: what a small file could spell.
  const big = [filled(CHUNK_BYTES, 1), filled(100, 2)];
  const oldListPreimage = () => text(`ta-chunks:${CHUNK_BYTES + 100}:${shas(big).join("")}`);

  it.each([["the small file first", true], ["the big file first", false]])(
    "never lets a small file alias a chunked one, %s", async (_order, smallFirst) => {
      const r = await rig();
      const small = oldListPreimage();
      if (smallFirst) {
        await r.stub.fileWrite(r.bind(null), { path: `${W}/small`, expected_etag: null,
                                               content: small });
      }
      await upload(r, `${W}/big`, big);
      if (!smallFirst) {
        await r.stub.fileWrite(r.bind(null), { path: `${W}/small`, expected_etag: null,
                                               content: small });
      }
      expect(await read(r, `${W}/small`)).toEqual(small);
      expect((await read(r, `${W}/big`)).byteLength).toBe(CHUNK_BYTES + 100);
      expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_contents")).toEqual([{ n: 2 }]);
    });

  it("names the kind and the size of what it names", () => {
    const one = filled(INLINE_BYTES + 1, 5);
    expect(new Set([inlineId(text("x")), chunkedId(1, [sha256Hex(text("x"))]),
                    chunkedId(2, [sha256Hex(text("x"))]), sha256Hex(text("x"))]).size).toBe(4);
    expect(chunkedId(one.length, [sha256Hex(one)])).not.toBe(sha256Hex(one));
    expect(chunkedId(10, ["a".repeat(64), "b".repeat(64)]))
      .not.toBe(chunkedId(11, ["a".repeat(64), "b".repeat(64)]));
  });

  it.each([
    ["inline content of another size", (r: Rig) => sqlIn(r.stub,
      "INSERT INTO file_contents (content_id, size, inline) VALUES (?, 2, X'0102')",
      inlineId(text("abc")))],
    ["inline content of other bytes", (r: Rig) => sqlIn(r.stub,
      "INSERT INTO file_contents (content_id, size, inline) VALUES (?, 3, X'010203')",
      inlineId(text("abc")))],
  ])("fails loudly when its id already names %s", async (_what, plant) => {
    const r = await rig();
    await plant(r);
    expect((await failure(() => r.stub.fileWrite(r.bind(null), {
      path: `${W}/a`, expected_etag: null, content: text("abc") }))).message)
      .toBe("a content id names other stored content");
    expect(await r.stub.fileStat(r.bind(null), { path: `${W}/a` })).toEqual({ entry: null });
  });

  it.each([["another chunk list", 0, ["c".repeat(64), "d".repeat(64)]],
           ["its chunks at another size", 1, null]])(
    "fails loudly when a chunked id already names %s", async (_what, extra, planted) => {
      const r = await rig();
      const pieces = [filled(CHUNK_BYTES, 7), filled(5, 8)];
      const id = chunkedId(CHUNK_BYTES + 5, shas(pieces));
      const [first, second] = planted ?? shas(pieces);
      await sqlIn(r.stub, "INSERT INTO file_contents (content_id, size) VALUES (?, ?)", id,
                  CHUNK_BYTES + 5 + extra);
      await sqlIn(r.stub, "INSERT INTO file_content_chunks VALUES (?, 0, ?), (?, 1, ?)",
                  id, first!, id, second!);
      expect((await failure(() => upload(r, `${W}/a`, pieces))).message)
        .toBe("a content id names other stored content");
    });
});

describe("garbage collection", () => {
  it("never collects content that an entry or a kept history row names, in any root",
     async () => {
    const r = await rig();
    const body = (seed: number) => filled(INLINE_BYTES + 10, seed);
    const limits = { keep_versions: 1, keep_recent: 2 };
    const other = await inFiles(r.stub, limits, async (files) => {
      const o = { expected: null, writer: "o" };
      const temp = files.tempDir("t-", "agent");
      const kept = files.tempDir("t-", "agent");
      for (const seed of [1, 2, 3]) {
        await files.write(`${W}/a`, body(seed), o);
      }
      await files.write(`${temp}/t`, body(4), o);
      await files.write(`${temp}/u`, body(5), o);
      await files.write(`${temp}/same-as-a`, body(3), o);
      files.rename(`${temp}/t`, `${W}/from-temp`, { ...o, replace: false });
      files.rename(`${temp}/u`, `${kept}/u`, { ...o, replace: false });
      files.rename(`${W}/a`, `${kept}/a`, { ...o, replace: false });
      await files.write(`${W}/b`, body(6), o);
      files.remove(`${W}/b`, o);
      files.dropTemps([temp], "agent");
      return kept;
    });
    // The rows marked as these limits keep them, every marked row due, then every garbage
    // chunk: three passes collect all there is to collect.
    await inFiles(r.stub, limits, (files) => files.collect(Date.now() / 1000));
    await sqlIn(r.stub, "UPDATE file_history SET prune_after = 1 WHERE prune_after IS NOT NULL");
    await inFiles(r.stub, limits, (files) => files.collect(Date.now() / 1000));
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1 WHERE gc_after IS NOT NULL");
    await inFiles(r.stub, limits, (files) => files.collect(Date.now() / 1000));
    expect(await lostContent(r.stub)).toEqual([]);
    expect(await read(r, `${W}/from-temp`)).toEqual(body(4));
    await inFiles(r.stub, limits, async (files) => {
      for (const [name, seed] of [["u", 5], ["a", 3]] as const) {
        const [chunk] = files.open(`${other}/${name}`).chunks!;
        expect((await files.fetch([{ sha256: chunk!.sha256 }]))[0])
          .toEqual(body(seed));
      }
    });
    // Only what nothing names went: a's older versions and b's bytes.
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_chunks")).toEqual([{ n: 3 }]);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
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
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await chunk(r, sha)).toMatchObject({ state: "live" });  // the delete keeps 30 days
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

  it("from an upload that never sent it is garbage that is sent again, never revived",
     async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 6);
    const sha = sha256Hex(piece);
    const begun = await begin(r, [piece]);
    expect(await chunk(r, sha)).toMatchObject({ state: "uploading", stored: 0, holds: 1 });
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await sqlIn(r.stub, "SELECT * FROM file_intents")).toEqual([]);
    expect(await chunk(r, sha)).toMatchObject({ state: "garbage", stored: 0, holds: 0 });
    const again = await begin(r, [piece]);
    expect(again.missing).toEqual([sha]);
    expect(again.intent_id).not.toBe(begun.intent_id);
    expect(await chunk(r, sha)).toMatchObject({ state: "uploading", holds: 1, gc_after: null });
  });

  it("is recorded stored only once FILES took it, so a put that fails records nothing",
     async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 11);
    const sha = sha256Hex(piece);
    const begun = await begin(r, [piece]);
    const refusing: ChunkStore = {
      putChunks: async () => { throw new Error("FILES is down"); },
      getChunks: (space, reads) => env.FILES.getChunks(space, reads),
      deleteChunks: (space, shas) => env.FILES.deleteChunks(space, shas),
    };
    expect((await failure(() => inFiles(r.stub, {}, (files) =>
      files.upload(begun.intent_id, [{ sha256: sha, bytes: piece }]), refusing))).message)
      .toBe("FILES is down");
    expect(await chunk(r, sha)).toMatchObject({ stored: 0 });
    expect(await inBucket(r, sha)).toBe(false);
    await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
                                                  chunks: [{ sha256: sha, bytes: piece }] });
    expect(await chunk(r, sha)).toMatchObject({ stored: 1 });
    expect(await inBucket(r, sha)).toBe(true);
  });

  it("sent but never committed is garbage that revives without being sent again", async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 9);
    const sha = sha256Hex(piece);
    const begun = await begin(r, [piece]);
    await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
                                                  chunks: [{ sha256: sha, bytes: piece }] });
    await r.stub.fileUploadAbort(r.bind(null), { intent_id: begun.intent_id });
    expect(await chunk(r, sha)).toMatchObject({ state: "garbage", stored: 1 });
    expect((await begin(r, [piece])).missing).toEqual([]);
  });

  it("being deleted is never held; its delete is tried again when it fails", async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 7);
    const sha = sha256Hex(piece);
    await begin(r, [piece]);
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
    const failing: ChunkStore = {
      putChunks: (space, chunks) => env.FILES.putChunks(space, chunks),
      getChunks: (space, reads) => env.FILES.getChunks(space, reads),
      deleteChunks: async () => { throw new Error("FILES is down"); },
    };
    // The failure stays in the chunk's row: the pass itself completes.
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000), failing);
    const deleting = await chunk(r, sha);
    expect(deleting).toMatchObject({ state: "deleting" });
    expect(deleting!["gc_after"] as number).toBeGreaterThan(Date.now() / 1000);
    expect((await failure(() => begin(r, [piece]))).message)
      .toBe("a chunk of this upload is being deleted; begin again");
    // A delete that outlasts its pass's budget is tried again, as a failed one is.
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
    const hung: ChunkStore = { ...failing, deleteChunks: () => new Promise(() => undefined) };
    await inFiles(r.stub, {}, (files) => files.collect(
      Date.now() / 1000, Date.now() + DELETE_FLOOR_MS + 20), hung);
    expect((await chunk(r, sha))!["gc_after"] as number).toBeGreaterThan(Date.now() / 1000);
    // A pass with less than DELETE_FLOOR_MS left, or none, begins no delete: FILES is not
    // asked, and the chunk waits in its row as it was.
    const asked: string[][] = [];
    const recording: ChunkStore = { ...failing, deleteChunks: async (_ns, shas) => {
      asked.push(shas);
      return { deleted: shas.length };
    } };
    // Half a second is too little to begin one (the floor is a second), and so is none.
    expect(DELETE_FLOOR_MS).toBeGreaterThan(500);
    for (const left of [500, -1]) {
      await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
      await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000,
                                                         Date.now() + left), recording);
      expect(await chunk(r, sha)).toMatchObject({ state: "deleting", gc_after: 1 });
    }
    expect(asked).toEqual([]);
    await sqlIn(r.stub, "UPDATE file_chunks SET state = 'garbage', gc_after = 1");
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await chunk(r, sha)).toBeUndefined();
    expect((await begin(r, [piece])).missing).toEqual([sha]);
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

  it("whose content another row still holds keeps its bytes when one version is redacted",
     async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 10);
    const first = entryOf((await upload(r, `${W}/a`, [piece])).committed);
    await upload(r, `${W}/b`, [piece]);
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null, content: text("x") });
    await r.stub.fileRedact(r.bind(null), { path: `${W}/a`, version: first.version });
    expect(await chunk(r, sha256Hex(piece))).toMatchObject({ state: "live", refs: 1 });
    expect(await read(r, `${W}/b`)).toEqual(piece);
  });
});

describe("a chunk between a pass's units", () => {
  /** A stored chunk no content holds, due for its delete now. */
  async function dueGarbage(r: Rig, piece: Uint8Array): Promise<void> {
    const begun = await begin(r, [piece]);
    await r.stub.fileUploadChunks(r.bind(null), {
      intent_id: begun.intent_id, chunks: [{ sha256: sha256Hex(piece), bytes: piece }] });
    await r.stub.fileUploadAbort(r.bind(null), { intent_id: begun.intent_id });
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
  }

  it("held again by an upload while the pass waits on the queue is never deleted under it",
     async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 12);
    const sha = sha256Hex(piece);
    await dueGarbage(r, piece);
    const missing = await runInDurableObject(r.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      const files = filesOn(gate.storage, gate.write);
      // A pi commit holds the queue; the pass queues behind it, an upload's begin after it.
      let release!: () => void;
      const held = new Promise<void>((resolve) => { release = resolve; });
      const commit = new DurableObjectSqliteDatabase(state.storage).transaction(() => held);
      const pass = files.collect(Date.now() / 1000 + 10);
      const again = gate.write(() => files.begin(piece.byteLength, [sha], "owner"));
      release();
      const begun = await again;
      await gate.write(() => files.commit(begun.intent_id, `${W}/kept.bin`,
                                          { expected: null, writer: "owner" }));
      await Promise.all([commit, pass]);
      return begun.missing;
    });
    expect(missing).toEqual([]);  // revived, its bytes not sent again
    expect(await chunk(r, sha)).toMatchObject({ state: "live", refs: 1 });
    expect(await read(r, `${W}/kept.bin`)).toEqual(piece);
    expect(await lostContent(r.stub)).toEqual([]);
  });

  it("fails the pass when a unit cannot be written, and never deletes bytes not marked first",
     async () => {
    // A pass's units: its housekeeping, the chunks' deleting mark, then their rows' delete.
    for (const [unit, state, asked] of [[1, "garbage", 0], [2, "garbage", 0],
                                        [3, "deleting", 1]] as const) {
      const r = await rig();
      const piece = filled(INLINE_BYTES + 1, 13);
      await dueGarbage(r, piece);
      const deletes: string[][] = [];
      const recording: ChunkStore = {
        putChunks: (space, chunks) => env.FILES.putChunks(space, chunks),
        getChunks: (space, reads) => env.FILES.getChunks(space, reads),
        deleteChunks: async (space, shas) => {
          deletes.push(shas);
          return env.FILES.deleteChunks(space, shas);
        },
      };
      const failed = await runInDurableObject(r.stub, async (_instance: OwnerDO, storage) => {
        const gate = new WriteGate(storage.storage);
        const files = filesOn(gate.storage, faulty(gate.write, (n) => n === unit), recording);
        return failure(() => files.collect(Date.now() / 1000 + 10));
      });
      expect([unit, failed.message, deletes.length]).toEqual([unit, STORAGE_FAULT, asked]);
      expect(await chunk(r, sha256Hex(piece))).toMatchObject({ state });
    }
  });

  it("fails an upload whose stored mark cannot be written", async () => {
    const r = await rig();
    const piece = filled(INLINE_BYTES + 1, 14);
    const sha = sha256Hex(piece);
    const begun = await begin(r, [piece]);
    const failed = await runInDurableObject(r.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      await filesOn(gate.storage, gate.write).namespace();  // made: the put goes out
      const files = filesOn(gate.storage, faulty(gate.write, () => true));
      return failure(() => files.upload(begun.intent_id, [{ sha256: sha, bytes: piece }]));
    });
    expect(failed.message).toBe(STORAGE_FAULT);
    expect(await chunk(r, sha)).toMatchObject({ stored: 0 });
  });

  it("fails a whole write with the fault that left its intent open, when its abort fails",
     async () => {
    const r = await rig();
    const down: ChunkStore = {
      putChunks: async () => { throw new Error("FILES is down"); },
      getChunks: (space, reads) => env.FILES.getChunks(space, reads),
      deleteChunks: (space, shas) => env.FILES.deleteChunks(space, shas),
    };
    const failed = await runInDurableObject(r.stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      await filesOn(gate.storage, gate.write).namespace();
      // The write's units: its intent, then (the put refused) its abort, which fails.
      const files = filesOn(gate.storage, faulty(gate.write, (n) => n === 2), down);
      return failure(() => files.write(`${W}/a.bin`, filled(INLINE_BYTES + 1, 16),
                                       { expected: null, writer: "owner" }));
    });
    expect(failed.message).toBe(STORAGE_FAULT);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_intents")).toEqual([{ n: 1 }]);
  });

  it("ends a whole write's intent when its commit is refused", async () => {
    const r = await rig();
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a.bin`, expected_etag: null,
                                           content: text("a") });
    const refused = await failure(() => inFiles(r.stub, {}, (files) =>
      files.write(`${W}/a.bin`, filled(INLINE_BYTES + 1, 15),
                  { expected: "", writer: "owner" })));
    expect(refused.name).toBe("FileConflict");
    expect(await sqlIn(r.stub, "SELECT intent_id FROM file_intents")).toEqual([]);
    expect(await chunk(r, sha256Hex(filled(INLINE_BYTES + 1, 15))))
      .toMatchObject({ state: "garbage", holds: 0 });
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
