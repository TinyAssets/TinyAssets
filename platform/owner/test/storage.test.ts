/**
 * The one storage number (founder, 2026-11-03): every byte of user content counts toward the
 * tier's storage wherever it lives (files and placed bodies in R2, small files, collection
 * rows, their history and mail kept inline in the object); retention makes room first, then
 * a write past it is refused with the files quota's fixed code; the owner reads used and left.
 */
import { describe, expect, it } from "vitest";

import { FILE_QUOTA, GC_GRACE_S, INLINE_BYTES } from "../src/files";
import type { Binding } from "../src/owner-do";
import { MUSE, rig } from "./ledger-support";
import { drainDue, failure, sqlIn, TIERS } from "./support";

const tiers = (bytes: number) => ({ free: { runs: 5, storage_bytes: bytes },
                                    paid: { runs: 10, storage_bytes: bytes } });

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
    expect(before).toEqual({ used_bytes: 0, limit_bytes: TIERS.free.storage_bytes,
                             breakdown: { files: 0, messages: 0, collections: 0 } });
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
    expect(after.breakdown).toEqual({ files: 100, messages: 11, collections });
    expect(after.used_bytes).toBe(100 + 11 + collections);
    expect(after.limit_bytes).toBe(TIERS.free.storage_bytes);
    // An outside client reads none of it.
    expect((await failure(() => r.stub.storageUsage(r.bind(MUSE)))).message)
      .toBe("only the owner may read its storage");
  });

  it("makes room by retention first, then refuses at the tier's figure, writing nothing",
     async () => {
    const r = await rig();
    const small = r.bind(null, { tiers: tiers(8000) });
    // Each rewrite keeps its row and adds a history entry: past the bound, the oldest
    // entries the row no longer is go first, so the rewrites all land.
    for (let i = 0; i < 8; i++) {
      await write(r, small, "a", `${i}`.repeat(1500));
    }
    const kept = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM collection_history " +
                                     "WHERE key = 'a'");
    expect(kept[0]!["n"] as number).toBeLessThan(8);
    expect((await r.stub.storageUsage(small)).used_bytes).toBeLessThanOrEqual(8000);
    // New rows take the rest; once only live rows and their own entries are left, the next
    // write is refused with the fixed code, and nothing of it is kept.
    let refused: Error | null = null;
    for (let i = 0; i < 10 && refused === null; i++) {
      try {
        await write(r, small, `row-${i}`, "y".repeat(1500));
      } catch (error) {
        refused = error as Error;
      }
    }
    expect(refused?.message).toMatch(new RegExp(`^${FILE_QUOTA}: the owner's stored bytes`));
    const usage = await r.stub.storageUsage(small);
    expect(usage.used_bytes).toBeLessThanOrEqual(8000);
    expect(usage.limit_bytes).toBe(8000);
  });

  it("lets deleted bytes go at once, and keeps no more garbage than the tier's figure, " +
     "oldest collected first (review H1)", async () => {
    const r = await rig();
    const figure = 3 * (INLINE_BYTES + 1000);
    const small = r.bind(null, { tiers: tiers(figure) });
    const shas: string[] = [];
    for (let i = 0; i < 4; i++) {
      const path = `/workspace/g${i}.bin`;
      await r.stub.fileWrite(small, { path, expected_etag: null,
                                      content: new Uint8Array(INLINE_BYTES + 1000).fill(i + 1) });
      const [row] = await sqlIn(r.stub, "SELECT c.sha256 FROM file_entries e JOIN " +
        "file_content_chunks c ON c.content_id = e.content_id WHERE e.name = ?", `g${i}.bin`);
      shas.push(row!["sha256"] as string);
      await r.stub.fileRemove(small, { path, expected_etag: null, recursive: false });
      await r.stub.fileHistoryPurge(small, { path });
      // Deleted: out of the owner's number at once, kept 30 days for recovery uncounted.
      expect((await r.stub.storageUsage(small)).used_bytes).toBe(0);
    }
    // In grace, none is due for 30 days; but four files' garbage passes the figure of three.
    const due = await sqlIn(r.stub, "SELECT MIN(gc_after) AS due FROM file_chunks");
    expect(due[0]!["due"] as number).toBeGreaterThan(Date.now() / 1000 + GC_GRACE_S - 3600);
    await r.stub.fileMkdir(small, { path: "/workspace/x", expected_etag: null });
    await drainDue(r.stub);
    // The oldest goes early; the rest, within the figure, wait out their grace.
    const left = await sqlIn(r.stub, "SELECT sha256, state FROM file_chunks ORDER BY gc_after");
    expect(left).toEqual(shas.slice(1).map((sha256) => ({ sha256, state: "garbage" })));
    expect(await sqlIn(r.stub, "SELECT garbage_bytes FROM file_space"))
      .toEqual([{ garbage_bytes: figure }]);
  });

  it("holds mail to the same figure", async () => {
    const r = await rig();
    const small = r.bind(null, { tiers: tiers(3000) });
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
    // Unacked mail is never let go: it is the recipient's still.
    expect(refused?.message).toMatch(new RegExp(`^${FILE_QUOTA}: `));
    expect((await r.stub.storageUsage(small)).breakdown.messages).toBe(3000);
  });
});
