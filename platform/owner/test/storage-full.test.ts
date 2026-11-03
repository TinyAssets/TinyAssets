/**
 * The one wall (founder, 2026-11-03): at the tier's storage figure every refusal is
 * `storage_full`, naming what is used, the whole, how to free space and the upgrade path;
 * reads and deletes always work when full. Tier numbers scaled down: free 10 KB, paid 100 KB.
 */
import { describe, expect, it } from "vitest";

import { PLATFORM_CALLER } from "../src/admission";
import {
  shownBytes, STORAGE_FULL, storageFull, storageFullMessage, storageFullOf,
} from "../src/storage-full";
import { rig, type Rig } from "./ledger-support";
import { failure, text } from "./support";

const SMALL = Object.freeze({ free: { runs: 5, storage_bytes: 10_000 },
                              paid: { runs: 10, storage_bytes: 100_000 } });
const W = "/workspace";
/** Past the 4 KiB kept inline, so each file's bytes are chunk bytes the figure counts. */
const FILE = 6_000;

const bytes = (n: number, seed: number) => text(String(seed).padEnd(n, "x"));

function write(r: Rig, path: string, n: number, seed = 1) {
  return r.stub.fileWrite(r.bind(null, { tiers: SMALL }), { path, expected_etag: null,
                                                           content: bytes(n, seed) });
}

async function full(call: () => Promise<unknown>) {
  const error = await failure(call);
  expect(error.name).toBe("Refused");
  expect(error.message.startsWith(`${STORAGE_FULL}: your storage is full: `)).toBe(true);
  return storageFullOf(error.message)!;
}

describe("storage_full, the one wall", () => {
  it("refuses at the free tier's figure with used, total, what to free and the upgrade",
     async () => {
    const r = await rig();
    await write(r, `${W}/a.bin`, FILE, 1);
    const facts = await full(() => write(r, `${W}/b.bin`, FILE, 2));
    expect(facts).toMatchObject({ used_bytes: 2 * FILE, limit_bytes: 10_000, tier: "free",
                                  tier_bytes: 10_000, own_limit: false,
                                  upgrade: { tier: "paid", limit_bytes: 100_000 } });
    expect(facts.free.length).toBeGreaterThan(0);
  });

  it("keeps reads and deletes working when full", async () => {
    const r = await rig();
    const at = r.bind(null, { tiers: SMALL });
    const kept = await write(r, `${W}/a.bin`, FILE, 1) as { entry: { etag: string } };
    await full(() => write(r, `${W}/b.bin`, FILE, 2));
    const read = await r.stub.fileRead(at, { path: `${W}/a.bin` });
    expect(read.entry.size).toBe(FILE);
    expect(await r.stub.fileRemove(at, { path: `${W}/a.bin`, expected_etag: kept.entry.etag,
                                         recursive: false })).toEqual({ removed: 1 });
    expect(await r.stub.fileStat(at, { path: `${W}/a.bin` })).toEqual({ entry: null });
    // Its bytes stay in its history until the owner purges it, which is never refused.
    expect(await r.stub.fileHistoryPurge(at, { path: `${W}/a.bin` }))
      .toMatchObject({ purged: 2 });  // its version and its deletion
  });

  it("names no upgrade on the largest tier", async () => {
    const r = await rig();
    await r.stub.tierSet({ owner: r.owner, caller: PLATFORM_CALLER, origin: null },
                         { tier: "paid" });
    for (let i = 0; i < 16; i++) {
      await write(r, `${W}/f${i}.bin`, FILE, 10 + i);
    }
    const facts = await full(() => write(r, `${W}/g.bin`, FILE, 99));
    expect(facts).toMatchObject({ tier: "paid", limit_bytes: 100_000, upgrade: null,
                                  own_limit: false });
  });

  it("says the owner's own lower limit binds, never an upgrade, when it does", async () => {
    const r = await rig();
    const { generation, policy } = await r.stub.policyRead(r.bind(null));
    await r.stub.policyWrite(r.session(null), {
      expected_generation: generation, policy: { ...policy, files: { max_bytes: 7_000 } } as never });
    await write(r, `${W}/a.bin`, FILE, 1);
    const facts = await full(() => write(r, `${W}/b.bin`, FILE, 2));
    expect(facts).toMatchObject({ limit_bytes: 7_000, tier_bytes: 10_000, own_limit: true,
                                  upgrade: null });
  });

  it("refuses a collection row's placed body at the same wall, in the same words", async () => {
    const r = await rig();
    await write(r, `${W}/a.bin`, FILE, 1);
    const facts = await full(() => r.stub.collectionWrite(r.bind(null, { tiers: SMALL }), {
      collection: "board", row_id: "big", body: JSON.stringify({ text: "y".repeat(6_000) }),
      expected_etag: "", private: false }));
    expect(facts.limit_bytes).toBe(10_000);
  });

});

describe("the storage_full message", () => {
  it("round-trips its facts and reads plainly", () => {
    const facts = storageFull(25_000_000_000, 25_000_000_000, "free",
                              { free: { runs: 5, storage_bytes: 25_000_000_000 },
                                paid: { runs: 10, storage_bytes: 250_000_000_000 } });
    const message = storageFullMessage(facts);
    expect(message).toContain("your storage is full: 25.0 GB of 25.0 GB used");
    expect(message).toContain("upgrade to paid for 250 GB");
    expect(storageFullOf(message)).toEqual(facts);
    expect(storageFullOf("file_quota: something else")).toBeNull();
  });

  it("shows bytes in decimal units", () => {
    expect([shownBytes(999), shownBytes(1_500), shownBytes(250_000_000_000)])
      .toEqual(["999 bytes", "1.5 KB", "250 GB"]);
  });

  it("without the tier numbers, states the facts it has and offers no upgrade", () => {
    const facts = storageFull(11, 10, null, null);
    expect(facts).toMatchObject({ tier: null, upgrade: null, own_limit: false });
    expect(storageFullMessage(facts)).toContain("11 bytes of 10 bytes used");
  });
});
