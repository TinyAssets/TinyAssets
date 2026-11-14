/**
 * The one wall (founder, 2026-11-03): at the tier's storage figure every refusal is
 * `storage_full`, naming what is stored (never the refused write), what the change needs,
 * what is free, how to free space and the upgrade path; reads and deletes always work when
 * full. Bytes held by open uploads are reported apart, and when they alone leave no room the
 * refusal is `uploads_open`, never a full storage nor an upgrade; an upload given up, or one a
 * lease left open when it ended, releases them at once. Tier numbers scaled down: free 10 KB
 * past what the owner's seed stores, paid 100 KB past it. Each change to the sync's ignore list
 * is a receipt and an owner notice.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { TIERS } from "../../ci/fixtures";
import { PLATFORM_CALLER } from "../../contract/src/binding";
import {
  heldOnly, shownBytes, STORAGE_FULL, storageFull, type StorageFull, storageFullRefusal,
  UPLOADS_OPEN,
} from "../src/storage-full";
import { refusalOf } from "../../contract/src/refusal";
import { LIST_BYTES } from "../../contract/src/sync-ignore";
import { CHUNK_BYTES } from "../../contract/src/limits";
import { sha256Hex } from "../../contract/src/digest";
import { rig, type Rig } from "./ledger-support";
import { later } from "./runs-support";
import { failure, inFiles, sqlIn, starterSeed, text } from "./support";

afterEach(() => {
  vi.useRealTimers();
});

/** What every owner's seed stores, counted in the figure like any file (`starterSeed`). */
const SEEDED = starterSeed().bytes;
const FREE = SEEDED + 10_000;
const PAID = SEEDED + 100_000;
const SMALL = Object.freeze({ free: { runs: 5, storage_bytes: FREE },
                              paid: { runs: 10, storage_bytes: PAID } });
const W = "/workspace";
/** Past the 4 KiB kept inline, so each file's bytes are chunk bytes the figure counts. */
const FILE = 6_000;
const TOKENS = { egress: "a".repeat(64), sync: "b".repeat(64), runner: "c".repeat(64) };
const OTHER = { egress: "d".repeat(64), sync: "e".repeat(64), runner: "f".repeat(64) };
const CLIENT = "k".repeat(64);
const open = async (r: Rig) =>
  (await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_intents"))[0]!["n"];

const bytes = (n: number, seed: number) => text(String(seed).padEnd(n, "x"));

function write(r: Rig, path: string, n: number, seed = 1) {
  return r.stub.fileWrite(r.bind(null, { tiers: SMALL }), { path, expected_etag: null,
                                                           content: bytes(n, seed) });
}

/** The refusal `call` makes, as it crossed RPC: its code, its message and its facts. */
async function refusedAs(code: string, call: () => Promise<unknown>) {
  const error = refusalOf(await failure(call))!;
  expect(error.code).toBe(code);
  expect(error.message.startsWith(`${code}: `)).toBe(true);
  return { message: error.message, facts: error.facts as unknown as StorageFull };
}

const full = (call: () => Promise<unknown>) => refusedAs(STORAGE_FULL, call);

describe("storage_full, the one wall", () => {
  it("names what is stored, what the write needs and the upgrade: never the refused write " +
     "as used", async () => {
    const r = await rig();
    await write(r, `${W}/a.bin`, FILE, 1);
    const { message, facts } = await full(() => write(r, `${W}/b.bin`, FILE, 2));
    expect(facts).toMatchObject({ used_bytes: SEEDED + FILE, needed_bytes: FILE, held_bytes: 0,
                                  limit_bytes: FREE, free_bytes: 4_000, tier: "free",
                                  tier_bytes: FREE, own_limit: false,
                                  upgrade: { tier: "paid", limit_bytes: PAID } });
    expect(message).toContain(`${shownBytes(SEEDED + FILE)} of ${shownBytes(FREE)} used, and ` +
                              "this needs 6.0 KB more (4.0 KB free)");
    expect(message).toContain(`upgrade to paid for ${shownBytes(PAID)}`);
  });

  it("is what an upload's begin meets at full, the sync's large-file path", async () => {
    const r = await rig();
    await write(r, `${W}/a.bin`, FILE, 1);
    const { facts } = await full(() => r.stub.fileUploadBegin(r.bind(null, { tiers: SMALL }),
      { size: FILE, chunks: ["ab".repeat(32)] }));
    expect(facts).toMatchObject({ used_bytes: SEEDED + FILE, needed_bytes: FILE,
                                  held_bytes: 0 });
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

  it("says nothing of plans on the largest tier", async () => {
    const r = await rig();
    await r.stub.tierSet({ owner: r.owner, caller: PLATFORM_CALLER, origin: null },
                         { tier: "paid" });
    for (let i = 0; i < 16; i++) {
      await write(r, `${W}/f${i}.bin`, FILE, 10 + i);
    }
    const { message, facts } = await full(() => write(r, `${W}/g.bin`, FILE, 99));
    expect(facts).toMatchObject({ tier: "paid", limit_bytes: PAID, upgrade: null,
                                  own_limit: false });
    expect(message.slice(0, message.indexOf("{"))).not.toMatch(/upgrade|largest|plan/);
  });

  it("says the owner's own lower limit binds, never an upgrade, when it does", async () => {
    const r = await rig();
    const { generation, policy } = await r.stub.policyRead(r.bind(null));
    await r.stub.policyWrite(r.session(null), {
      expected_generation: generation, policy: { ...policy, files: { max_bytes: SEEDED + 7_000 } } as never });
    await write(r, `${W}/a.bin`, FILE, 1);
    const { facts } = await full(() => write(r, `${W}/b.bin`, FILE, 2));
    expect(facts).toMatchObject({ limit_bytes: SEEDED + 7_000, tier_bytes: FREE, own_limit: true,
                                  upgrade: null });
  });

  it("refuses a collection row's placed body at the same wall, in the same words", async () => {
    const r = await rig();
    await write(r, `${W}/a.bin`, FILE, 1);
    const { facts } = await full(() => r.stub.collectionWrite(r.bind(null, { tiers: SMALL }), {
      collection: "board", row_id: "big", body: JSON.stringify({ text: "y".repeat(6_000) }),
      expected_etag: "", private: false }));
    expect(facts.limit_bytes).toBe(FREE);
  });
});

describe("open uploads, apart from what is stored", () => {
  it("are never a full storage nor an upgrade when they alone leave no room", async () => {
    const r = await rig();
    const at = r.bind(null, { tiers: SMALL });
    const begun = await r.stub.fileUploadBegin(at, { size: 8_000, chunks: ["cd".repeat(32)] });
    const { message, facts } = await refusedAs(UPLOADS_OPEN, () => write(r, `${W}/a.bin`,
                                                                          FILE, 1));
    expect(facts).toMatchObject({ used_bytes: SEEDED, needed_bytes: FILE, held_bytes: 8_000 });
    expect(message.slice(0, message.indexOf("{"))).not.toMatch(/upgrade|storage is full/);
    // Cancelled, it holds nothing: the write fits.
    expect(await r.stub.fileUploadAbort(at, { intent_id: begun.intent_id }))
      .toEqual({ aborted: true });
    await write(r, `${W}/a.bin`, FILE, 1);
  });

  it("leave a begin that letting history go would admit at storage_full, never uploads_open, " +
     "and let no history go (round 9, untangle F1)", async () => {
    const r = await rig();
    const at = r.bind(null, { tiers: SMALL });
    // An upload open: 4,200 bytes held apart.
    await r.stub.fileUploadBegin(at, { size: 4_200, chunks: ["cd".repeat(32)] });
    // A row whose placed body (past the inline 4 KiB, so chunk bytes) only its history keeps.
    const big = await r.stub.collectionWrite(at, { collection: "board", row_id: "r",
      body: JSON.stringify({ text: "y".repeat(5_000) }), expected_etag: "", private: false });
    if (!("row" in big)) {
      throw new Error("the write lost its swap");
    }
    await r.stub.collectionWrite(at, { collection: "board", row_id: "r", body: "{}",
                                       expected_etag: big.row.etag, private: false });
    const stored = (await r.stub.storageUsage(r.bind(null, { tiers: SMALL }))).used_bytes;
    // A begin past the wall that fits only were that history let go: nothing is let go to
    // admit a write (ADR-036), so it is refused at the storage figure, never uploads_open.
    const { facts } = await full(() => r.stub.fileUploadBegin(at, {
      size: 5_400, chunks: ["ef".repeat(32)] }));
    expect(facts.held_bytes).toBe(4_200);
    expect(facts.used_bytes).toBe(stored - 4_200);
    expect(facts.used_bytes + facts.needed_bytes).toBeGreaterThan(FREE);
    expect(facts.used_bytes + facts.held_bytes - 5_000 + facts.needed_bytes)
      .toBeLessThanOrEqual(FREE);
    // Refused whole: the history kept, the upload still the only one open.
    expect(await open(r)).toBe(1);
    expect((await r.stub.storageUsage(r.bind(null, { tiers: SMALL }))).used_bytes).toBe(stored);
  });

  it("are let go when the lease whose sync began them ends", async () => {
    const r = await rig();
    const lease = await inFiles(r.stub, {}, (files) =>
      files.leaseAcquire("owner", "sandbox-1", TOKENS, 1));
    await inFiles(r.stub, {}, (files) => files.syncBegin(lease.lease_id, lease.epoch, 8_000,
                                                         ["ef".repeat(32)]));
    expect(await open(r)).toBe(1);
    await inFiles(r.stub, {}, (files) => files.leaseRelease(lease.lease_id, lease.epoch));
    expect(await open(r)).toBe(0);
    await write(r, `${W}/a.bin`, FILE, 1);
  });

  it("are let go when a lapsed lease is noticed, and when a new lease replaces it first",
     async () => {
    const r = await rig();
    for (const [n, take] of [[1, "lapsed"], [2, "acquired"]] as const) {
      const lease = await inFiles(r.stub, {}, (files) =>
        files.leaseAcquire("owner", `sandbox-${n}`, n === 1 ? TOKENS : OTHER, 1));
      await inFiles(r.stub, {}, (files) => files.syncBegin(lease.lease_id, lease.epoch, 8_000,
                                                           [`a${n}`.repeat(32)]));
      later();  // the sandbox died
      if (take === "lapsed") {
        await inFiles(r.stub, {}, (files) => files.leasesLapsed(Date.now() / 1000));
      } else {
        // A sign-in takes the next lease before any runs pass noticed the lapse.
        await inFiles(r.stub, {}, (files) => files.leaseAcquire("owner", "sandbox-3", TOKENS, 1));
      }
      expect(await open(r)).toBe(0);
      await sqlIn(r.stub, "DELETE FROM sandbox_leases");
    }
  });

  it("never cancels a client's own upload when that client's sandbox lease ends", async () => {
    const r = await rig();
    // The client's own upload, as its MCP door begins it (its writer the lease's writer too).
    await inFiles(r.stub, {}, (files) => files.begin(8_000, ["a3".repeat(32)],
                                                     `client:${CLIENT}`));
    const lease = await inFiles(r.stub, {}, (files) =>
      files.leaseAcquire(CLIENT, "sandbox-1", TOKENS, 1));
    expect(await inFiles(r.stub, {}, (files) => files.syncWriter(lease.lease_id, lease.epoch)))
      .toBe(`client:${CLIENT}`);
    await inFiles(r.stub, {}, (files) => files.syncBegin(lease.lease_id, lease.epoch, 8_000,
                                                         ["a4".repeat(32)]));
    expect(await open(r)).toBe(2);
    await inFiles(r.stub, {}, (files) => files.leaseRelease(lease.lease_id, lease.epoch));
    // The sync's own went with its lease; the client's own write stands.
    expect(await sqlIn(r.stub, "SELECT writer, lease FROM file_intents"))
      .toEqual([{ writer: `client:${CLIENT}`, lease: null }]);
  });
});

describe("the sync's ignore list", () => {
  it("records a receipt and one notice for the owner at each change", async () => {
    const r = await rig();
    const at = r.bind(null, { tiers: SMALL });
    const made = await r.stub.fileWrite(at, { path: `${W}/.ta/sync-ignore`, expected_etag: null,
                                              content: text("node_modules\n") });
    await r.stub.fileWrite(at, { path: `${W}/.ta/sync-ignore`,
                                 expected_etag: (made as { entry: { etag: string } }).entry.etag,
                                 content: text("*\n") });
    const notices = (await r.stub.noticeList(r.bind(null))).notices
      .filter((notice) => notice.type === "sync_ignore_changed");
    expect(notices).toHaveLength(2);
    // Each names who changed it: the owner, here.
    expect(notices.every((notice) => notice.detail.includes(" by you:"))).toBe(true);
    const receipts = (await r.stub.policyReceipts(r.bind(null))).receipts
      .filter((receipt) => receipt.rule === "sync_ignore");
    expect(receipts.map((receipt) => receipt.decision)).toEqual(["you", "you"]);
  });

  it("gives the sync agent's count of the kept files a sync's list holds back, and never " +
     "scans the owner's files in the write", async () => {
    const r = await rig();
    // A sandbox's sync writes the list (its writer the owner's lease) with the count its
    // sync agent made in the sandbox. The object holds none of those files: it scans nothing.
    const lease = await inFiles(r.stub, {}, (files) =>
      files.leaseAcquire("owner", "sandbox-1", TOKENS, 1));
    const sync = (inline: string, held: unknown, base: number | null = null) =>
      r.stub.fileSyncCommit(r.bind(null), { lease_id: lease.lease_id, epoch: lease.epoch,
        ops: [{ op: "write", path: `${W}/.ta/sync-ignore`, base, inline: text(inline),
                held_back: held } as never] });
    expect((await sync("node_modules\nsrc\n", { kept: 6, changed: 2 })).outcomes
      .map((each) => each.outcome)).toEqual(["committed"]);
    const changed = async () => (await r.stub.noticeList(r.bind(null))).notices
      .filter((each) => each.type === "sync_ignore_changed")[0]!.detail;
    const detail = await changed();
    expect(detail).toContain("by a run in your sandbox:");
    expect(detail).toContain("Its new lines may hold back files you keep");
    expect(detail).toContain("its lines hold back 6 files you keep (2 with changes in the " +
                             "sandbox)");
    const [receipt] = (await r.stub.policyReceipts(r.bind(null))).receipts
      .filter((each) => each.rule === "sync_ignore");
    expect(receipt!.decision).toBe("a run in your sandbox");
    const version = async () => ((await r.stub.fileRead(r.bind(null),
      { path: `${W}/.ta/sync-ignore` })) as { entry: { version: number } }).entry.version;
    // A count of 0 never takes the sentence away: a new line is said whatever the count.
    await sync("node_modules\nsrc\nbuild-out\n", { kept: 0, changed: 0 }, await version());
    expect(await changed()).toContain("Its new lines may hold back files you keep");
    expect(await changed()).not.toContain("In the sync that wrote it");
    // Only a default line added, and a count that is no count: nothing held back is said.
    await sync("node_modules\nsrc\nbuild-out\ndist\n", { kept: 1, changed: 5 },
               await version());
    expect(await changed()).not.toContain("hold back");
  });

  it("says a list past `LIST_BYTES` is not applied past it, in the notice", async () => {
    const r = await rig();
    // Past a written file's most, so uploaded a 4 MiB chunk at a time, as a binder uploads.
    const line = "*.ext-0123456789\n";
    const bytes = text(line.repeat(Math.ceil((LIST_BYTES + 1) / line.length)));
    const pieces: Uint8Array[] = [];
    for (let at = 0; at < bytes.length; at += CHUNK_BYTES) {
      pieces.push(bytes.subarray(at, at + CHUNK_BYTES));
    }
    const begun = await r.stub.fileUploadBegin(r.bind(null), {
      size: bytes.length, chunks: pieces.map((piece) => sha256Hex(piece)) });
    for (const piece of pieces) {
      await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
        chunks: [{ sha256: sha256Hex(piece), bytes: piece }] });
    }
    await r.stub.fileUploadCommit(r.bind(null), { intent_id: begun.intent_id,
                                                  path: `${W}/.ta/sync-ignore`,
                                                  expected_etag: null });
    const [notice] = (await r.stub.noticeList(r.bind(null))).notices
      .filter((each) => each.type === "sync_ignore_changed");
    expect(notice!.detail).toContain(`The list past its ${LIST_BYTES.toLocaleString("en")} ` +
                                     "bytes is not applied");
  });

  it("never gives an outside client's sandbox count, which sees only its part of the files " +
     "(verification round 3, R3-2)", async () => {
    const r = await rig();
    const client = "c".repeat(64);
    const lease = await inFiles(r.stub, {}, (files) =>
      files.leaseAcquire(client, "sandbox-1", TOKENS, 1));
    await sqlIn(r.stub, "INSERT INTO outside_clients (client, source, mode, scopes, generation, " +
      "name, first_seen, last_seen) VALUES (?, 'mcp', 'full', '[]', 1, '', 1, 1)", client);
    await r.stub.fileSyncCommit(r.bind(null), { lease_id: lease.lease_id, epoch: lease.epoch,
      ops: [{ op: "write", path: `${W}/.ta/sync-ignore`, base: null, inline: text("AGENTS.md\n"),
              held_back: { kept: 0, changed: 0 } } as never] });
    const [notice] = (await r.stub.noticeList(r.bind(null))).notices
      .filter((each) => each.type === "sync_ignore_changed");
    expect(notice!.detail).toContain("by the outside client");
    expect(notice!.detail).toContain("Its new lines may hold back files you keep");
    expect(notice!.detail).not.toContain("In the sync that wrote it");
  });

  it("says a list written outside a sync may hold back kept files when it adds a line, at " +
     "the cost of reading its lines alone", async () => {
    const r = await rig();
    const at = r.bind(null, { tiers: SMALL });
    // The worst list an inline file holds: 2,000 one-letter lines, read as a set.
    const lines = Array.from({ length: 2_000 }, (_, i) => String.fromCharCode(97 + (i % 26)))
      .join("\n");
    const made = await r.stub.fileWrite(at, { path: `${W}/.ta/sync-ignore`, expected_etag: null,
                                              content: text(lines) });
    const notices = async () => (await r.stub.noticeList(r.bind(null))).notices
      .filter((each) => each.type === "sync_ignore_changed");
    expect((await notices())[0]!.detail).toContain("Its new lines may hold back files you keep");
    // A default line added alone says nothing of it.
    await r.stub.fileWrite(at, { path: `${W}/.ta/sync-ignore`,
                                 expected_etag: (made as { entry: { etag: string } }).entry.etag,
                                 content: text(`${lines}\ndist`) });
    expect((await notices())[0]!.detail).not.toContain("hold back");
  });
});

describe("the storage_full refusal", () => {
  it("carries its facts and reads plainly", () => {
    const facts = storageFull(24_500_000_000, 2_000_000_000, 0, 25_000_000_000, "free", TIERS);
    const refusal = storageFullRefusal(facts);
    expect(refusal.code).toBe(STORAGE_FULL);
    expect(refusal.facts).toEqual(facts);
    expect(refusal.message).toContain("your storage is full: 24.5 GB of 25.0 GB used, and this " +
                                      "needs 2.0 GB more (500 MB free)");
    expect(refusal.message).toContain("upgrade to paid for 250 GB");
    // Its message ends with the facts as clients have always been shown them.
    expect(refusal.message.endsWith(` ${JSON.stringify(facts)}`)).toBe(true);
  });

  it("tells held uploads apart, and only they make uploads_open", () => {
    const held = storageFull(1_000, 2_000, 9_000, 10_000, "free", TIERS);
    expect(heldOnly(held)).toBe(true);
    expect(storageFullRefusal(held).code).toBe(UPLOADS_OPEN);
    expect(storageFullRefusal(held).message.startsWith(`${UPLOADS_OPEN}: `)).toBe(true);
    const both = storageFull(9_000, 2_000, 500, 10_000, "free", TIERS);
    expect(heldOnly(both)).toBe(false);
    expect(storageFullRefusal(both).code).toBe(STORAGE_FULL);
    expect(storageFullRefusal(both).message)
      .toContain("500 bytes more is held by uploads still open");
  });

  it("shows bytes in decimal units", () => {
    expect([shownBytes(999), shownBytes(1_500), shownBytes(250_000_000_000)])
      .toEqual(["999 bytes", "1.5 KB", "250 GB"]);
  });

  it("without the tier numbers, says nothing of plans", () => {
    const facts = storageFull(10, 1, 0, 10, null, null);
    expect(facts).toMatchObject({ tier: null, upgrade: null, own_limit: false });
    const { message } = storageFullRefusal(facts);
    expect(message).toContain("10 bytes of 10 bytes used, and this needs 1 bytes more");
    expect(message.slice(0, message.indexOf("{"))).not.toMatch(/upgrade|largest|plan/);
  });
});
