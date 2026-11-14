/**
 * The one storage number (founder, 2026-11-03): every byte of user content counts toward the
 * tier's storage wherever it lives (files and placed bodies in R2, small files, collection
 * rows, their history and mail kept inline in the object); retention makes room first, then
 * a write past it is refused with the files quota's fixed code; the owner reads used and left.
 */
import { describe, expect, it } from "vitest";

import { MANIFEST_PROTECT_S, manifestDay } from "../src/backup";
import { refusalOf } from "../../contract/src/refusal";
import { GC_GRACE_S, INLINE_BYTES } from "../../contract/src/limits";
import type { Binding } from "../../contract/src/binding";
import { MUSE, rig } from "./ledger-support";
import { drainDue, failure, laneAt, sqlIn, starterSeed, TIERS } from "./support";

/** What every owner's seed holds, its bytes counted in the figure like any file's. */
const SEED = starterSeed();
const tiers = (bytes: number) => ({ free: { runs: 5, storage_bytes: bytes },
                                    paid: { runs: 10, storage_bytes: bytes } });
/**
 * A figure of whole files of `unit` bytes: as many as the seed's bytes take, and `more`.
 * The garbage it keeps is then that many files too, and one more file's passes it.
 */
const wholeFiles = (unit: number, more: number) => unit * (Math.ceil(SEED.bytes / unit) + more);
/** The test's own chunks, by hash, with their state, in the order the lane takes them. */
const ownChunks = async (r: Awaited<ReturnType<typeof rig>>) => SEED.mine(
  await sqlIn(r.stub, "SELECT sha256, state FROM file_chunks ORDER BY gc_after, sha256"),
  (row) => row["sha256"] as string);

/** Each row's etag as the last write left it: a write swaps on it. */
const ETAGS = new Map<string, string>();

async function write(r: Awaited<ReturnType<typeof rig>>, binding: Binding, row: string,
                     body: string): Promise<void> {
  const key = `${r.owner}/${row}`;
  const written = await r.stub.collectionWrite(binding, {
    collection: "board", row_id: row, body, expected_etag: ETAGS.get(key) ?? "",
    private: false });
  if (!("row" in written)) {
    throw new Error("the write lost its swap");
  }
  ETAGS.set(key, written.row.etag);
}

describe("the one storage number", () => {
  it("counts inline bodies, mail and small files, and shows used and left", async () => {
    const r = await rig();
    const before = await r.stub.storageUsage(r.bind(null));
    expect(before).toEqual({ used_bytes: SEED.bytes, limit_bytes: TIERS.free.storage_bytes,
                             breakdown: { files: SEED.bytes, messages: 0, collections: 0 },
                             deleted_content: { kept_bytes: 0, kept_up_to_days: 30 } });
    await write(r, r.bind(null), "a", "x".repeat(1000));
    const rows = await sqlIn(r.stub, "SELECT (SELECT SUM(body_bytes) FROM collection_rows) + " +
                                     "(SELECT SUM(body_bytes) FROM collection_history) AS n");
    const collections = rows[0]!["n"] as number;
    expect(collections).toBeGreaterThanOrEqual(2000);  // the row, and its history entry
    const muse = await r.register(MUSE, "Muse", "pull");
    await r.post(null, `dm:${muse}`, { body: "hello, Muse" });
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/n.txt", expected_etag: null,
                                           content: new Uint8Array(100) });
    const after = await r.stub.storageUsage(r.bind(null));
    // Mail counts at its real bytes (slice STORE item 2, D2): its rows' lines, its body in
    // them, as its archive segment will hold it.
    const [mail] = await sqlIn(r.stub, "SELECT line_bytes, body_bytes FROM ledger_messages");
    expect(mail!["body_bytes"]).toBe(11);
    const messages = mail!["line_bytes"] as number;
    expect(messages).toBeGreaterThan(11);
    expect(after.breakdown).toEqual({ files: SEED.bytes + 100, messages, collections });
    expect(after.used_bytes).toBe(SEED.bytes + 100 + messages + collections);
    expect(after.limit_bytes).toBe(TIERS.free.storage_bytes);
    // An outside client reads none of it.
    expect((await failure(() => r.stub.storageUsage(r.bind(MUSE)))).message)
      .toBe("only the owner may read its storage");
  });

  it("refuses at the tier's figure, writing nothing and letting no history go (ADR-036, " +
     "untangle F1)", async () => {
    const r = await rig();
    const figure = SEED.bytes + 8000;
    const small = r.bind(null, { tiers: tiers(figure) });
    // Each rewrite keeps its row and adds a history entry. Nothing is let go to admit one:
    // past the bound the rewrite is refused at the one wall (`storage_full`).
    let refused: Error | null = null;
    let written = 0;
    for (let i = 0; i < 8 && refused === null; i++) {
      try {
        await write(r, small, "a", `${i}`.repeat(1500));
        written++;
      } catch (error) {
        refused = error as Error;
      }
    }
    expect(written).toBeGreaterThan(1);
    expect(refusalOf(refused!)?.facts).toMatchObject({ limit_bytes: figure, tier: "free" });
    const kept = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM collection_history " +
                                     "WHERE key = 'a'");
    expect(kept[0]!["n"]).toBe(written);
    const usage = await r.stub.storageUsage(small);
    expect(usage.used_bytes).toBeLessThanOrEqual(figure);
    expect(usage.limit_bytes).toBe(figure);
  });

  it("lets deleted bytes go at once, and keeps no more garbage than the tier's figure, " +
     "oldest collected first (review H1)", async () => {
    const r = await rig();
    const unit = INLINE_BYTES + 1000;
    const figure = wholeFiles(unit, 3);
    const small = r.bind(null, { tiers: tiers(figure) });
    const letGo: { sha256: string; at: number }[] = [];
    for (let i = 0; i <= figure / unit; i++) {
      const path = `/workspace/g${i}.bin`;
      await r.stub.fileWrite(small, { path, expected_etag: null,
                                      content: new Uint8Array(unit).fill(i + 1) });
      const [row] = await sqlIn(r.stub, "SELECT c.sha256 FROM file_entries e JOIN " +
        "file_content_chunks c ON c.content_id = e.content_id WHERE e.name = ?", `g${i}.bin`);
      const sha256 = row!["sha256"] as string;
      await r.stub.fileRemove(small, { path, expected_etag: null, recursive: false });
      await r.stub.fileHistoryPurge(small, { path });
      // When it was let go, read before any early collection can touch it (none can until
      // the last: only then is the garbage past the figure).
      const [held] = await sqlIn(r.stub, "SELECT gc_after FROM file_chunks WHERE sha256 = ?",
                                 sha256);
      letGo.push({ sha256, at: held!["gc_after"] as number });
      // Deleted: out of the owner's number at once, kept 30 days for recovery uncounted.
      expect((await r.stub.storageUsage(small)).used_bytes).toBe(SEED.bytes);
    }
    // In grace, none is due for 30 days; but the files' garbage passes the figure by one,
    // so the files lane may already be collecting the oldest early (the fourth delete rang
    // it): that chunk is then `deleting`, its R2 delete in flight, and the rest still wait.
    const due = await sqlIn(r.stub, "SELECT MIN(gc_after) AS due FROM file_chunks WHERE " +
                                    "state = 'garbage'");
    expect(due[0]!["due"] as number).toBeGreaterThan(Date.now() / 1000 + GC_GRACE_S - 3600);
    await r.stub.fileMkdir(small, { path: "/workspace/x", expected_etag: null });
    await drainDue(r.stub);
    // Until the early delete, wherever it began, has landed.
    for (let i = 0; i < 100 && (await sqlIn(r.stub, "SELECT 1 FROM file_chunks WHERE " +
                                                    "state = 'deleting'")).length > 0; i++) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    // The oldest goes early (by when it was let go, a tie by hash, as the lane takes them);
    // the rest, within the figure, wait out their grace.
    const oldest = [...letGo].sort((a, b) => a.at - b.at
      || (a.sha256 < b.sha256 ? -1 : a.sha256 > b.sha256 ? 1 : 0));
    expect(await ownChunks(r)).toEqual(oldest.slice(1).map(({ sha256 }) => ({ sha256, state: "garbage" })));
    expect(await sqlIn(r.stub, "SELECT garbage_bytes FROM file_space"))
      .toEqual([{ garbage_bytes: figure }]);
    // The owner reads what is kept for recovery, and for how long at most.
    expect((await r.stub.storageUsage(small)).deleted_content)
      .toEqual({ kept_bytes: figure, kept_up_to_days: 30 });
  });

  it("keeps the files lane off its own alarm while an early delete waits for its retry " +
     "(review N2)", async () => {
    const r = await rig();
    const unit = INLINE_BYTES + 1000;
    const roomy = r.bind(null, { tiers: tiers(SEED.bytes + 4 * unit) });
    for (let i = 0; i < 2; i++) {
      const path = `/workspace/g${i}.bin`;
      await r.stub.fileWrite(roomy, { path, expected_etag: null,
                                      content: new Uint8Array(unit).fill(i + 1) });
      await r.stub.fileRemove(roomy, { path, expected_etag: null, recursive: false });
      await r.stub.fileHistoryPurge(roomy, { path });
    }
    // An early collection's R2 delete failed: both rows wait out GC_RETRY_S as `deleting`.
    const retry = Date.now() / 1000 + 300;
    await sqlIn(r.stub, "UPDATE file_chunks SET state = 'deleting', gc_after = ? " +
                        "WHERE state = 'garbage'", retry);
    expect(await sqlIn(r.stub, "SELECT garbage_bytes FROM file_space"))
      .toEqual([{ garbage_bytes: 0 }]);
    // The figure is now the seed's and one file's (a lowered setting, or a downgrade):
    // nothing to take early, so the lane waits for the retry, never sets itself due at once.
    const small = r.bind(null, { tiers: tiers(SEED.bytes + unit) });
    await r.stub.fileMkdir(small, { path: "/workspace/x", expected_etag: null });
    for (let i = 0; i < 2; i++) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      expect(await laneAt(r.stub, "files")).toBeGreaterThanOrEqual(retry);
    }
  });

  it("never collects early a chunk the newest manifest names (review N3)", async () => {
    const r = await rig();
    const unit = INLINE_BYTES + 1000;
    const figure = wholeFiles(unit, 1);
    const small = r.bind(null, { tiers: tiers(figure) });
    // Last night's manifest began an hour ago: the files below were live then.
    await sqlIn(r.stub, "INSERT INTO owner_backups (gen, day, state, cursor, parts, " +
                        "part_list, bytes, s, schema, started_at, finished_at) VALUES " +
                        "('0123456789abcdef', ?, 'done', NULL, 0, '', 0, 1, 1, ?, ?)",
                manifestDay(Date.now() / 1000), Date.now() / 1000 - 3600,
                Date.now() / 1000 - 3000);
    for (let i = 0; i <= figure / unit; i++) {
      const path = `/workspace/n${i}.bin`;
      await r.stub.fileWrite(small, { path, expected_etag: null,
                                      content: new Uint8Array(unit).fill(i + 7) });
      await r.stub.fileRemove(small, { path, expected_etag: null, recursive: false });
      await r.stub.fileHistoryPurge(small, { path });
    }
    // Past the figure, but all named by the manifest: kept, and no alarm loop.
    await r.stub.fileMkdir(small, { path: "/workspace/y", expected_etag: null });
    await drainDue(r.stub);
    const garbage = (files: number) => Array.from({ length: files }, () => "garbage");
    expect((await ownChunks(r)).map((row) => row["state"])).toEqual(garbage(figure / unit + 1));
    expect(await laneAt(r.stub, "files")).toBeGreaterThan(Date.now() / 1000 + 86_400);
    // Once a newer manifest began after they went, the oldest goes early.
    await sqlIn(r.stub, "UPDATE owner_backups SET started_at = ?", Date.now() / 1000 + 1);
    await r.stub.fileMkdir(small, { path: "/workspace/z", expected_etag: null });
    await drainDue(r.stub);
    expect((await ownChunks(r)).map((row) => row["state"])).toEqual(garbage(figure / unit));
  });

  it("stops protecting garbage by a manifest past MANIFEST_PROTECT_S, no recovery point " +
     "(review N3)", async () => {
    const r = await rig();
    const unit = INLINE_BYTES + 1000;
    const figure = wholeFiles(unit, 1);
    const small = r.bind(null, { tiers: tiers(figure) });
    // The newest manifest began and finished more than two nights ago: no recovery point.
    const long = Date.now() / 1000 - MANIFEST_PROTECT_S - 60;
    await sqlIn(r.stub, "INSERT INTO owner_backups (gen, day, state, cursor, parts, " +
                        "part_list, bytes, s, schema, started_at, finished_at) VALUES " +
                        "('0123456789abcdef', ?, 'done', NULL, 0, '', 0, 1, 1, ?, ?)",
                manifestDay(Date.now() / 1000), long - 60, long);
    for (let i = 0; i <= figure / unit; i++) {
      const path = `/workspace/o${i}.bin`;
      await r.stub.fileWrite(small, { path, expected_etag: null,
                                      content: new Uint8Array(unit).fill(i + 9) });
      await r.stub.fileRemove(small, { path, expected_etag: null, recursive: false });
      await r.stub.fileHistoryPurge(small, { path });
    }
    await r.stub.fileMkdir(small, { path: "/workspace/y", expected_etag: null });
    await drainDue(r.stub);
    // The figure holds again: the oldest went early.
    expect((await ownChunks(r)).map((row) => row["state"]))
      .toEqual(Array.from({ length: figure / unit }, () => "garbage"));
  });

  it("holds mail to the same figure", async () => {
    const r = await rig();
    const figure = SEED.bytes + 3000;
    const small = r.bind(null, { tiers: tiers(figure) });
    const muse = await r.register(MUSE, "Muse", "pull");
    let refused: Error | null = null;
    for (let i = 0; i < 5 && refused === null; i++) {
      try {
        await r.stub.roomPost(small, { room_id: `dm:${muse}`, client_message_id: `c${i}`,
                                       body: "z".repeat(1500), parent_id: null });
      } catch (error) {
        refused = error as Error;
      }
    }
    // Mail is never let go at the figure (ADR-036): the post is refused, the mail held kept.
    expect(refusalOf(refused!)?.facts).toMatchObject({ limit_bytes: figure, tier: "free" });
    const held = (await r.stub.storageUsage(small)).breakdown.messages;
    expect(held).toBeGreaterThan(1500);
    expect(held).toBeLessThanOrEqual(3000);
  });
});
