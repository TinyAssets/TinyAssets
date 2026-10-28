/**
 * The sync lease (plan §6): a random lease id as the claim token and a rising epoch as
 * the fencing number. A stale sync commit is refused; API writes never take the lease;
 * a sync op against a version that moved on keeps the API's version and saves the
 * sandbox's as a reported conflict copy.
 */
import { describe, expect, it } from "vitest";

import { type Entry, INLINE_BYTES, type Lease, sha256Hex, type SyncOp } from "../src/files";
import { MUSE, rig, type Rig } from "./ledger-support";
import { failure, fileCounters, sqlIn, text } from "./support";

const W = "/workspace";

function entryOf(answer: { entry: Entry } | { conflict: Entry | null }): Entry {
  if (!("entry" in answer)) {
    throw new Error("expected a commit");
  }
  return answer.entry;
}

const acquire = (r: Rig, holder = "sandbox-1") =>
  r.stub.fileLeaseAcquire(r.bind(null), { holder });
const sync = (r: Rig, lease: Pick<Lease, "lease_id" | "epoch">, ops: SyncOp[],
              intent: string | null = null) =>
  r.stub.fileSyncCommit(r.bind(null), { lease_id: lease.lease_id, epoch: lease.epoch,
                                        intent_id: intent, ops });
const write = (r: Rig, path: string, body: string) => r.stub.fileWrite(
  r.bind(null), { path, expected_etag: null, content: text(body) });
const readText = async (r: Rig, path: string) => new TextDecoder().decode(
  (await r.stub.fileRead(r.bind(null), { path })).inline);
const held = ({ lease_id, epoch }: Lease) => ({ lease_id, epoch });
const FENCED = "the sync lease was lost; this sync is fenced";

describe("the sync lease", () => {
  it("is held by one holder at a time, renewed, and taken again only once it lapses",
     async () => {
    const r = await rig();
    const first = await acquire(r);
    expect(first.lease_id).toMatch(/^[0-9a-f]{32}$/);
    expect(first.epoch).toBe(1);
    expect((await failure(() => acquire(r, "sandbox-2"))).message).toBe("the sync lease is held");
    const renewed = await r.stub.fileLeaseRenew(r.bind(null), held(first));
    expect(renewed.expires_at).toBeGreaterThanOrEqual(first.expires_at);
    await sqlIn(r.stub, "UPDATE file_space SET lease_expires = 1");
    expect((await failure(() => r.stub.fileLeaseRenew(r.bind(null), held(first)))).message)
      .toBe(FENCED);
    const second = await acquire(r, "sandbox-2");
    expect(second.epoch).toBe(2);
    expect(second.lease_id).not.toBe(first.lease_id);
  });

  it("has an epoch that only rises", async () => {
    const r = await rig();
    await acquire(r);
    await expect(sqlIn(r.stub, "UPDATE file_space SET epoch = 0")).rejects
      .toThrow(/the lease epoch only rises/);
  });

  it("fences a commit with a stale epoch, a wrong lease id, or a lapsed or released lease",
     async () => {
    const r = await rig();
    const lease = await acquire(r);
    const op: SyncOp = { op: "mkdir", path: `${W}/d`, base: null };
    for (const stale of [{ ...lease, epoch: lease.epoch - 1 }, { ...lease, epoch: lease.epoch + 1 },
                         { ...lease, lease_id: "0".repeat(32) }]) {
      expect((await failure(() => sync(r, stale, [op]))).message).toBe(FENCED);
    }
    expect(await r.stub.fileLeaseRelease(r.bind(null), held(lease))).toEqual({ released: true });
    expect(await r.stub.fileLeaseRelease(r.bind(null), held(lease))).toEqual({ released: false });
    expect((await failure(() => sync(r, lease, [op]))).message).toBe(FENCED);
    const next = await acquire(r);
    await sqlIn(r.stub, "UPDATE file_space SET lease_expires = 1");
    expect((await failure(() => sync(r, next, [op]))).message).toBe(FENCED);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_entries")).toEqual([{ n: 0 }]);
  });

  it("never stops an API write while it is held", async () => {
    const r = await rig();
    await acquire(r);
    expect(entryOf(await write(r, `${W}/a`, "api")).writer).toBe("owner");
  });

  it("is the owner's own: an outside client never reaches it", async () => {
    const r = await rig();
    await r.register(MUSE);
    expect((await failure(() => r.stub.fileLeaseAcquire(r.bind(MUSE), { holder: "x" }))).message)
      .toBe("only the owner may hold the file sync lease");
    for (const holder of ["", "x".repeat(129), "a\nb", 7]) {
      expect((await failure(() => acquire(r, holder as string))).message)
        .toBe("a lease holder is 1 to 128 printable characters");
    }
  });
});

describe("a sync commit", () => {
  it("writes, makes and removes against the versions the sandbox saw", async () => {
    const r = await rig();
    const seen = entryOf(await write(r, `${W}/old`, "old"));
    await write(r, `${W}/gone`, "gone");
    const gone = (await r.stub.fileStat(r.bind(null), { path: `${W}/gone` })).entry!;
    const lease = await acquire(r);
    const { outcomes } = await sync(r, lease, [
      { op: "write", path: `${W}/old`, base: seen.version, inline: text("new") },
      { op: "write", path: `${W}/fresh/a.txt`, base: null, inline: text("a"), mode: 1 },
      { op: "mkdir", path: `${W}/dir`, base: null },
      { op: "remove", path: `${W}/gone`, base: gone.version },
    ]);
    expect(outcomes.map((o) => [o.path, o.outcome])).toEqual([
      [`${W}/old`, "committed"], [`${W}/fresh/a.txt`, "committed"],
      [`${W}/dir`, "committed"], [`${W}/gone`, "committed"]]);
    expect(await readText(r, `${W}/old`)).toBe("new");
    expect((await r.stub.fileStat(r.bind(null), { path: `${W}/fresh/a.txt` })).entry)
      .toMatchObject({ mode: 1, writer: `lease:${lease.epoch}` });
    expect((await r.stub.fileStat(r.bind(null), { path: `${W}/gone` })).entry).toBeNull();
  });

  it("keeps the API's version on a conflict and saves the sandbox's beside it, reported",
     async () => {
    const r = await rig();
    const seen = entryOf(await write(r, `${W}/notes.md`, "base"));
    const lease = await acquire(r);
    const api = entryOf(await write(r, `${W}/notes.md`, "the API's edit"));
    const { outcomes } = await sync(r, lease, [
      { op: "write", path: `${W}/notes.md`, base: seen.version, inline: text("the sandbox's edit") },
      { op: "write", path: `${W}/new.md`, base: null, inline: text("sandbox") },
    ]);
    const copy = `${W}/notes.md.ta-conflict-${lease.epoch}`;
    expect(outcomes[0]).toEqual({ path: `${W}/notes.md`, outcome: "conflict", current: api,
                                  copy: expect.objectContaining({ path: copy }) });
    expect(outcomes[1]).toMatchObject({ outcome: "committed" });
    expect(await readText(r, `${W}/notes.md`)).toBe("the API's edit");
    expect(await readText(r, copy)).toBe("the sandbox's edit");
    expect((await r.stub.fileHistory(r.bind(null), { path: copy, limit: 5 })).changes
      .map((c) => [c.op, c.writer])).toEqual([["conflict", `lease:${lease.epoch}`]]);
    // The sandbox thought new.md absent; a file made there by the API is a conflict too.
    await write(r, `${W}/race.md`, "api");
    expect((await sync(r, lease, [{ op: "write", path: `${W}/race.md`, base: null,
                                    inline: text("vm") }])).outcomes[0]!.outcome).toBe("conflict");
  });

  it("never removes what the API changed, nor a directory still holding entries", async () => {
    const r = await rig();
    const seen = entryOf(await write(r, `${W}/a`, "one"));
    const dir = entryOf(await r.stub.fileMkdir(r.bind(null), { path: `${W}/d`, expected_etag: null }));
    await write(r, `${W}/d/api.txt`, "written by the API");
    const lease = await acquire(r);
    await write(r, `${W}/a`, "two");
    const { outcomes } = await sync(r, lease, [
      { op: "remove", path: `${W}/a`, base: seen.version },
      { op: "remove", path: `${W}/d`, base: dir.version },
      { op: "mkdir", path: `${W}/d/api.txt`, base: null },
    ]);
    expect(outcomes.map((o) => o.outcome)).toEqual(["conflict", "conflict", "conflict"]);
    expect(await readText(r, `${W}/a`)).toBe("two");
    expect(await readText(r, `${W}/d/api.txt`)).toBe("written by the API");
  });

  it("commits chunked content from its intent, and releases it", async () => {
    const r = await rig();
    const lease = await acquire(r);
    const piece = new Uint8Array(INLINE_BYTES + 3).fill(5);
    const sha = sha256Hex(piece);
    const begun = await r.stub.fileUploadBegin(r.bind(null), { chunks: [{ sha256: sha,
                                                                          size: piece.length }] });
    await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
                                                  chunks: [{ sha256: sha, bytes: piece }] });
    const { outcomes } = await sync(r, lease, [{ op: "write", path: `${W}/big.bin`, base: null,
                                                 size: piece.length, chunks: [sha] }],
                                    begun.intent_id);
    expect(outcomes[0]).toMatchObject({ outcome: "committed",
                                        entry: { size: piece.length } });
    expect(await sqlIn(r.stub, "SELECT * FROM file_intents")).toEqual([]);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("is refused whole for a bad op, leaving every other op undone", async () => {
    const r = await rig();
    const lease = await acquire(r);
    for (const ops of [
      [{ op: "mkdir", path: `${W}/ok`, base: null }, { op: "chmod", path: `${W}/x`, base: null }],
      [{ op: "mkdir", path: `${W}/ok`, base: null }, { op: "mkdir", path: `${W}/ok`, base: null }],
      [{ op: "mkdir", path: `${W}/ok`, base: null }, { op: "mkdir", path: "/.ta/tmp/x", base: null }],
      [{ op: "write", path: `${W}/ok`, base: null, inline: new Uint8Array(INLINE_BYTES + 1) }],
      [{ op: "mkdir", path: `${W}/ok`, base: 0 }],
      [],
    ]) {
      expect((await failure(() => sync(r, lease, ops as SyncOp[]))).name).toBe("Invalid");
    }
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_entries")).toEqual([{ n: 0 }]);
  });
});
