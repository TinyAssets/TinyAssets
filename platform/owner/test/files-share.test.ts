/**
 * The files byte quota (plan §6, migration 10): every manifest row charged what the object
 * stores it in, against `max_manifest_bytes`; each outside client held to its share of
 * every quota and all clients to what the owner's reserve leaves; gone paths' history
 * pruned by bytes to make room; and the model turns' counters bounded. An outside client
 * holding `write_files` cannot fill the owner's object, so the owner's grants, revokes and
 * policy writes always have room.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import {
  CHUNK_BYTES, type Files, FILE_QUOTA, FILE_SHARE, INLINE_BYTES, MAX_OPEN_INTENTS, sha256Hex,
} from "../src/files";
import { WriteGate } from "../src/gate";
import {
  CHUNK_LIST_ROW_BYTES, HISTORY_BYTES, MANIFEST_INDEXES, MANIFEST_TABLES, PAGE_BYTES, PAGE_ROOM,
  PAGE_USABLE, RECORD_FIXED, record, recordBytes, SHARE_BYTES, TABLE_KEPT, WORST_FILL,
} from "../src/manifest";
import type { OwnerDO } from "../src/owner-do";
import { MAX_PATH_BYTES } from "../src/paths";
import { FILE_DEFAULTS, FILE_SETTINGS } from "../src/policy";
import {
  CALLS_OPEN, MIGRATIONS, SPEND_KEPT, THREAD_COMPACTIONS, THREAD_INPUTS, TURN_BYTES,
} from "../src/schema";
import { DOT, MUSE, rig, type Rig } from "./ledger-support";
import { failure, fileCounters, filesOn, inFiles, sqlIn, text } from "./support";

const W = "/workspace";
const CLIENT = `client:${MUSE.client}`;

async function setPolicy(r: Rig, files: unknown) {
  const { generation, policy } = await r.stub.policyRead(r.bind(null));
  return r.stub.policyWrite(r.session(null), { expected_generation: generation,
                                               policy: { ...policy, files } as never });
}

const write = (r: Rig, origin: typeof MUSE | null, path: string, body = "x") =>
  r.stub.fileWrite(r.bind(origin), { path, expected_etag: null, content: text(body) });

/** Everything the manifest holds, to show a refusal left nothing behind. */
const snapshot = (r: Rig) => Promise.all(["file_space", "file_shares", "file_entries",
  "file_history", "file_contents", "file_chunks", "file_intents"].map((table) =>
  sqlIn(r.stub, `SELECT * FROM ${table}`)));

async function refused(r: Rig, act: () => Promise<unknown>, reason: string) {
  const before = await snapshot(r);
  const error = await failure(act);
  expect([error.name, error.message]).toEqual(["Refused", reason]);
  expect(await snapshot(r)).toEqual(before);
}

async function counted(r: Rig) {
  const [kept, recounted] = await fileCounters(r.stub);
  expect(kept).toEqual(recounted);
  return kept;
}

/** A directory path exactly `bytes` long, of components of at most 200 bytes. */
function deep(bytes: number): string {
  let path = W;
  while (path.length < bytes) {
    path += `/${"d".repeat(Math.min(200, Math.max(1, bytes - path.length - 1)))}`;
  }
  return path;
}

/** A file path of exactly `bytes` under `dir`, unique by `n`. */
const longPath = (dir: string, bytes: number, n: number) =>
  `${dir}/${String(n).padStart(6, "0")}`.padEnd(bytes, "q");

const share = async (r: Rig, who: string) =>
  (await sqlIn(r.stub, "SELECT * FROM file_shares WHERE who = ?", who))[0];
const manifest = async (r: Rig) =>
  (await sqlIn(r.stub, "SELECT manifest_bytes AS m FROM file_space"))[0]!["m"] as number;

describe("the manifest charge", () => {
  it("is SQLite's record layout, in SQL as in the module", async () => {
    const r = await rig();
    for (const [payload, kept] of [[1, 1002], [500, 1002], [1002, 1002], [1003, 1002],
                                   [4096, 1002], [9000, 1002], [4061, 4061], [4062, 4061],
                                   [12_000, 4061]] as const) {
      const [row] = await sqlIn(r.stub, `SELECT ${record(String(payload - RECORD_FIXED), kept)}
                                         AS b`);
      expect([payload, row!["b"]]).toEqual([payload, recordBytes(payload, kept)]);
    }
    expect(recordBytes(1003, 1002)).toBeGreaterThan(PAGE_BYTES);
    expect(SHARE_BYTES).toBe(recordBytes(7 + 64 + 5 * 9 + RECORD_FIXED, 1002));
    const [list] = await sqlIn(r.stub, `SELECT ${CHUNK_LIST_ROW_BYTES()} AS b`);
    expect(list!["b"]).toBe(recordBytes(64 + 64 + 9 + RECORD_FIXED, 1002));
  });

  // Paths at each edge of SQLite's page layout: short, a full name, just under and over
  // what an index page keeps in place, and the longest a path may be.
  it.each([24, 255, 1002, 1003, 2048, MAX_PATH_BYTES])(
    "covers what the object's database grows by, for paths of %i bytes", async (bytes) => {
      const r = await rig();
      await runInDurableObject(r.stub, async (_instance: OwnerDO, state) => {
        const files = filesOn(state.storage, new WriteGate(state.storage).write, undefined,
                              { max_entries: 1_000_000, max_history: 5_000_000,
                                keep_versions: 20, keep_recent: 20 });
        const sql = state.storage.sql;
        const charged = () => sql.exec<{ m: number }>(
          "SELECT manifest_bytes AS m FROM file_space").one().m;
        const dir = bytes > 300 ? deep(bytes - 100) : W;
        const at = (n: number) => `${dir}/${sha256Hex(String(n)).slice(0, 16)}`
          .padEnd(bytes, "q");
        await files.write(at(-1), text("seed"), { expected: null, writer: CLIENT });
        const [size, bytesBefore] = [sql.databaseSize, charged()];
        const n = 400;
        for (let i = 0; i < 2 * n; i++) {  // n new files, then a version of each
          await files.write(at(i % n), text(`v${i}`), { expected: null, writer: CLIENT });
        }
        expect(sql.databaseSize - size).toBeLessThanOrEqual(charged() - bytesBefore);
      });
      await counted(r);
    }, 120_000);
});

describe("path lengths", () => {
  it("takes a path of exactly 4096 bytes, charging its overflow pages, and no longer",
     async () => {
    const r = await rig();
    const dir = deep(MAX_PATH_BYTES - 50);
    await r.stub.fileMkdir(r.bind(null), { path: dir, expected_etag: null });
    const before = await manifest(r);
    const path = longPath(dir, MAX_PATH_BYTES, 1);
    expect(new TextEncoder().encode(path).byteLength).toBe(MAX_PATH_BYTES);
    await write(r, MUSE, path);
    // The entry, the history row and its path index each spill a page.
    expect(await manifest(r) - before).toBeGreaterThan(3 * PAGE_BYTES);
    expect((await failure(() => write(r, MUSE, `${path}q`))).name).toBe("Invalid");
    // A component of 255 bytes is a name; one of 256 is not. Bytes count, not characters.
    await write(r, MUSE, `${W}/${"é".repeat(127)}a`);
    expect((await failure(() => write(r, MUSE, `${W}/${"é".repeat(128)}`))).name)
      .toBe("Invalid");
    const short = await manifest(r);
    await write(r, MUSE, `${W}/${"é".repeat(100)}`);
    const wide = await manifest(r) - short;
    await write(r, MUSE, `${W}/${"e".repeat(100)}`);
    expect(wide).toBeGreaterThan(await manifest(r) - short - wide);
    await counted(r);
  });
});

describe("an outside client's share", () => {
  it("refuses a client writing long paths at its share, and the owner still writes",
     async () => {
    const r = await rig();
    await setPolicy(r, { max_manifest_bytes: 2_000_000, client_share: 10 });
    const dir = deep(MAX_PATH_BYTES - 100);
    await r.stub.fileMkdir(r.bind(null), { path: dir, expected_etag: null });
    const reason = `${FILE_SHARE}: this client's file manifest bytes are at its share of ` +
      "the owner's quota";
    let n = 0;
    for (; n < 100; n++) {
      const error = await write(r, MUSE, longPath(dir, MAX_PATH_BYTES, n))
        .then(() => null, (refusal: Error) => refusal);
      if (error !== null) {
        expect(error.message).toBe(reason);
        break;
      }
    }
    expect(n).toBeGreaterThan(5);
    expect(n).toBeLessThan(100);
    const mine = await share(r, CLIENT);
    expect(mine!["manifest_bytes"] as number).toBeLessThanOrEqual(200_000);
    await refused(r, () => write(r, MUSE, longPath(dir, MAX_PATH_BYTES, 1000)), reason);
    const usage = await r.stub.fileUsage(r.bind(MUSE));
    expect(usage.share?.manifest_bytes).toBe(mine!["manifest_bytes"]);
    expect((await r.stub.fileUsage(r.bind(null))).share)
      .toEqual((await share(r, "owner")) && { ...(await share(r, "owner")), who: undefined });
    // The owner's room is its own.
    for (let i = 0; i < 20; i++) {
      await write(r, null, longPath(dir, MAX_PATH_BYTES, 2000 + i));
    }
    // The client still writes what frees, and short files once it has room.
    await r.stub.fileRemove(r.bind(MUSE), { path: longPath(dir, MAX_PATH_BYTES, 0),
                                            expected_etag: null, recursive: false });
    await counted(r);
  });

  it("refuses a client writing many versions of a long path at its share, and the owner " +
     "still writes", async () => {
    const r = await rig();
    await setPolicy(r, { max_manifest_bytes: 2_000_000, client_share: 10, keep_versions: 100,
                         keep_recent: 100, max_entries: 1000, max_history: 100_000 });
    const dir = deep(MAX_PATH_BYTES - 100);
    await r.stub.fileMkdir(r.bind(null), { path: dir, expected_etag: null });
    const path = longPath(dir, MAX_PATH_BYTES, 1);
    const reason = `${FILE_SHARE}: this client's file manifest bytes are at its share of ` +
      "the owner's quota";
    let versions = 0;
    for (; versions < 100; versions++) {
      const error = await write(r, MUSE, path, `v${versions}`)
        .then(() => null, (refusal: Error) => refusal);
      if (error !== null) {
        expect(error.message).toBe(reason);
        break;
      }
    }
    expect(versions).toBeGreaterThan(5);
    expect(versions).toBeLessThan(100);
    expect((await share(r, CLIENT))!["history"]).toBe(versions);
    await refused(r, () => write(r, MUSE, path, "one more"), reason);
    // The owner writes on, the client's file among them: the entry is its own then.
    for (let i = 0; i < 30; i++) {
      await write(r, null, path, `owner ${i}`);
    }
    expect((await share(r, CLIENT))!["entries"]).toBe(0);
    await counted(r);
  });

  it("holds all clients together to what the owner's reserve leaves", async () => {
    const r = await rig();
    await setPolicy(r, { max_entries: 10, max_history: 200, keep_versions: 1, keep_recent: 1,
                         client_share: 40, owner_reserve: 50 });
    for (let i = 0; i < 4; i++) {
      await write(r, MUSE, `${W}/muse-${i}`);
    }
    await refused(r, () => write(r, MUSE, `${W}/muse-4`),
                  `${FILE_SHARE}: this client's file entries are at its share of the owner's ` +
                  "quota");
    await write(r, DOT, `${W}/dot-0`);
    await refused(r, () => write(r, DOT, `${W}/dot-1`),
                  `${FILE_SHARE}: outside clients' file entries are at their share; the rest ` +
                  "is kept for the owner");
    for (let i = 0; i < 5; i++) {
      await write(r, null, `${W}/owner-${i}`);
    }
    await refused(r, () => write(r, null, `${W}/owner-5`),
                  `${FILE_QUOTA}: the owner's file entries are at their quota`);
    expect((await share(r, "*"))!["entries"]).toBe(5);
    await counted(r);
  });

  it("leaves grants, revokes and policy writes working once a client fills its share",
     async () => {
    const r = await rig();
    await r.register(MUSE);
    await setPolicy(r, { max_manifest_bytes: 1_000_000, client_share: 5 });
    let full = false;
    for (let i = 0; i < 200 && !full; i++) {
      full = await write(r, MUSE, `${W}/${String(i).padStart(200, "f")}`, "x".repeat(4000))
        .then(() => false, (error: Error) => error.message.startsWith(FILE_SHARE));
    }
    expect(full).toBe(true);
    const [row] = (await r.stub.grantList(r.bind(null))).clients;
    const narrowed = await r.stub.grantSet(r.session(null), {
      client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: [{ universe: "alpha", agent: "main", capability: "read_files" }],
      expected_generation: row!.generation, name: "" });
    expect(narrowed.mode).toBe("scoped");
    expect((await r.stub.grantRevoke(r.bind(null), { client: MUSE.client })).revoked).toBe(true);
    await setPolicy(r, { max_manifest_bytes: 2_000_000, client_share: 50 });
    expect((await r.stub.fileUsage(r.bind(null))).limits.client_share).toBe(50);
    await write(r, null, `${W}/owner.md`, "the owner's own");
    expect((await failure(() => write(r, MUSE, `${W}/after.md`))).message)
      .not.toMatch(/^file_/);
    expect(await manifest(r)).toBeLessThanOrEqual(2_000_000);
    await counted(r);
  });

  it("holds a client's open uploads to its share, and all clients' to the owner's reserve",
     async () => {
    const r = await rig();
    await setPolicy(r, { client_share: 25, owner_reserve: 60 });
    const begin = (origin: typeof MUSE | null, n: number) => r.stub.fileUploadBegin(
      r.bind(origin), { size: INLINE_BYTES + 1, chunks: [n.toString(16).padStart(64, "0")] });
    const mine = Math.floor(MAX_OPEN_INTENTS * 25 / 100);
    for (let i = 0; i < mine; i++) {
      await begin(MUSE, i);
    }
    await refused(r, () => begin(MUSE, 100),
                  `${FILE_SHARE}: this client's open uploads are at its share; commit or ` +
                  "abort one first");
    const theirs = Math.floor(MAX_OPEN_INTENTS * 40 / 100);
    for (let i = mine; i < theirs; i++) {
      await begin(DOT, i);
    }
    await refused(r, () => begin(DOT, 101),
                  `${FILE_SHARE}: outside clients' open uploads are at their share; the rest ` +
                  "is kept for the owner");
    for (let i = theirs; i < MAX_OPEN_INTENTS; i++) {
      await begin(null, i);
    }
    await refused(r, () => begin(null, 102),
                  `${FILE_QUOTA}: too many uploads are open; commit or abort one first`);
    expect((await share(r, CLIENT))!["chunk_bytes"]).toBe(mine * (INLINE_BYTES + 1));
    await counted(r);
  });

  it("charges a client the chunks it uploads, until another's upload revives them",
     async () => {
    const r = await rig();
    const sha = "e".repeat(64);
    await inFiles(r.stub, {}, (files: Files) => {
      const begun = files.begin(INLINE_BYTES + 1, [sha], CLIENT);
      files.abort(begun.intent_id);
    });
    // Garbage holds no chunk bytes, but its row is still the client's.
    expect(await share(r, CLIENT)).toMatchObject({ chunk_bytes: 0, entries: 0 });
    await counted(r);
    // A garbage chunk's holder never changes.
    expect((await failure(() => sqlIn(r.stub, "UPDATE file_chunks SET holder = 'owner'")))
      .message).toMatch(/a garbage chunk's holder never changes/);
    await inFiles(r.stub, {}, (files: Files) => files.begin(INLINE_BYTES + 1, [sha], "owner"));
    expect(await share(r, CLIENT)).toBeUndefined();
    expect(await sqlIn(r.stub, "SELECT who, manifest_bytes FROM file_shares WHERE who != 'owner'"))
      .toEqual([{ who: "*", manifest_bytes: SHARE_BYTES }]);
    await counted(r);
  });

  it("keeps every share and the manifest's bytes equal to a recount of the rows",
     async () => {
    const r = await rig();
    const lease = await r.stub.fileLeaseAcquire(r.bind(null), { holder: "sync" });
    await inFiles(r.stub, { client_share: 100, owner_reserve: 0 }, async (files: Files) => {
      await files.write(`${W}/d/a.txt`, text("a"), { expected: null, writer: CLIENT });
      await files.write(`${W}/d/b.bin`, new Uint8Array(INLINE_BYTES + 10),
                        { expected: null, writer: CLIENT });
      await files.write(`${W}/d/a.txt`, text("owner's"), { expected: null, writer: "owner" });
      files.rename(`${W}/d`, `${W}/e`, { expected: null, replace: false, writer: CLIENT });
      const temp = files.tempDir("t-", CLIENT);
      await files.write(`${temp}/x`, text("tmp"), { expected: null, writer: CLIENT });
      files.mkdir(`${W}/f`, { expected: null, writer: `client:${DOT.client}` });
      files.syncCommit({ lease_id: lease.lease_id, epoch: lease.epoch, ops: [
        { op: "write", path: `${W}/e/a.txt`, base: null, inline: text("sandbox") },
      ] });
      files.remove(`${W}/e/b.bin`, { expected: null, writer: CLIENT });
      files.purge(`${W}/e/b.bin`);
      files.dropTemps([temp], CLIENT);
    });
    const kept = await counted(r);
    expect(JSON.parse(kept["shares"] as string)).toHaveProperty(CLIENT);
    await sqlIn(r.stub, "UPDATE file_history SET prune_after = 1");
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1 WHERE gc_after IS NOT NULL");
    await inFiles(r.stub, {}, (files: Files) => files.collect(Date.now() / 1000));
    await counted(r);
  });
});

describe("history retention by bytes", () => {
  it("prunes gone paths' history, earliest due first, so a change fits the manifest quota",
     async () => {
    const r = await rig();
    const limits = { keep_versions: 1, keep_recent: 2 };
    const dir = deep(2900);
    await inFiles(r.stub, limits, async (files: Files) => {
      await files.write(`${dir}/live`, text("kept"), { expected: null, writer: "owner" });
      for (let i = 0; i < 10; i++) {
        const path = longPath(dir, 3000, i);
        await files.write(path, text(`gone ${i}`), { expected: null, writer: "owner" });
        files.remove(path, { expected: null, writer: "owner" });
      }
    });
    const quota = await manifest(r) + 1000;
    const released = async () => (await sqlIn(r.stub,
      "SELECT COUNT(*) AS n FROM file_history WHERE released = 1"))[0]!["n"] as number;
    expect(await released()).toBe(20);
    const { cursor } = await r.stub.fileChanges(r.bind(null), { after: 0, limit: 1 });
    await inFiles(r.stub, { ...limits, max_manifest_bytes: quota }, async (files: Files) => {
      for (let i = 0; i < 5; i++) {
        await files.write(longPath(dir, 3000, 100 + i), text("new"),
                          { expected: null, writer: "owner" });
      }
    });
    expect(await manifest(r)).toBeLessThanOrEqual(quota);
    expect(await released()).toBeLessThan(20);
    expect(await released()).toBeGreaterThan(0);
    // The earliest gone path went first, and a reader behind it rescans.
    expect(await sqlIn(r.stub, "SELECT 1 FROM file_history WHERE path = ?",
                       longPath(dir, 3000, 0))).toEqual([]);
    expect((await r.stub.fileChanges(r.bind(null), { after: cursor - 1, limit: 1 })).reset)
      .toBe(true);
    // The live path keeps its version; and with nothing gone left, the quota refuses.
    expect((await r.stub.fileStat(r.bind(null), { path: `${dir}/live` })).entry).not.toBeNull();
    const reason = `${FILE_QUOTA}: the owner's file manifest bytes are at their quota`;
    await expect(inFiles(r.stub, { ...limits, max_manifest_bytes: quota }, async (files: Files) => {
      for (let i = 0; i < 50; i++) {
        await files.write(longPath(dir, 3000, 200 + i), text("more"),
                          { expected: null, writer: "owner" });
      }
    })).rejects.toThrow(reason);
    expect(await released()).toBe(0);
    await counted(r);
  });

  it("prunes a client's own gone history for its share, never the owner's", async () => {
    const r = await rig();
    const limits = { keep_versions: 1, keep_recent: 2, owner_reserve: 0 };
    const dir = deep(2900);
    await inFiles(r.stub, limits, async (files: Files) => {
      for (let i = 0; i < 6; i++) {
        for (const writer of ["owner", CLIENT]) {
          const path = longPath(dir, 3000, i * 10 + writer.length);
          await files.write(path, text("gone"), { expected: null, writer });
          files.remove(path, { expected: null, writer });
        }
      }
    });
    // A client's share of 1% that its rows nearly fill, of a quota the owner is far from.
    const quota = 100 * (((await share(r, CLIENT))!["manifest_bytes"] as number) + 1000);
    const ownerRows = async () => (await sqlIn(r.stub,
      "SELECT COUNT(*) AS n FROM file_history WHERE writer = 'owner'"))[0]!["n"];
    const clientRows = async () => (await sqlIn(r.stub,
      "SELECT COUNT(*) AS n FROM file_history WHERE writer = ? AND released = 1", CLIENT))[0]!["n"];
    const before = [await ownerRows(), await clientRows()];
    const tight = { ...limits, client_share: 1, max_manifest_bytes: quota };
    await inFiles(r.stub, tight, async (files: Files) => {
      for (let i = 0; i < 3; i++) {
        await files.write(longPath(dir, 3000, 500 + i), text("new"),
                          { expected: null, writer: CLIENT });
      }
    });
    expect(await ownerRows()).toBe(before[0]);
    expect(await clientRows()).toBeLessThan(before[1] as number);
    expect((await share(r, CLIENT))!["manifest_bytes"] as number)
      .toBeLessThanOrEqual(quota / 100);
    await counted(r);
  });
});

describe("a lowered share", () => {
  it("prunes no more of a client's gone history than its change added", async () => {
    const r = await rig();
    const limits = { keep_versions: 1, keep_recent: 2, owner_reserve: 0 };
    await inFiles(r.stub, limits, async (files: Files) => {
      for (let i = 0; i < 10; i++) {
        await files.write(`${W}/gone-${i}`, text("gone"), { expected: null, writer: CLIENT });
        files.remove(`${W}/gone-${i}`, { expected: null, writer: CLIENT });
      }
    });
    const rows = async () => (await share(r, CLIENT))!["history"] as number;
    const before = await rows();
    expect(before).toBe(20);
    // A share of 2% of 200 history rows: 4, far below the 20 the client holds.
    await inFiles(r.stub, { ...limits, max_history: 200, client_share: 2 },
                  async (files: Files) => {
      await files.write(`${W}/new`, text("new"), { expected: null, writer: CLIENT });
    });
    expect(await rows()).toBe(before);
    await counted(r);
  });
});

describe("the manifest charge at worst fill", () => {
  it("counts a page at three quarters of its room, and a third full at worst", () => {
    expect(PAGE_ROOM).toBe(PAGE_USABLE * 3 / 4);
    expect(WORST_FILL).toBe(PAGE_ROOM / (PAGE_USABLE / 3));
    expect(recordBytes(100, 1002)).toBe(Math.floor(PAGE_BYTES / Math.floor(3066 / 104)));
  });

  it("bounds what a database filled, thinned and filled again grows by, at WORST_FILL",
     async () => {
    const r = await rig();
    const ratios = await runInDurableObject(r.stub, async (_instance: OwnerDO, state) => {
      const files = filesOn(state.storage, new WriteGate(state.storage).write, undefined, {
        max_entries: 1_000_000, max_history: 5_000_000, keep_versions: 1, keep_recent: 1,
        client_share: 100, owner_reserve: 0 });
      const sql = state.storage.sql;
      const charged = () => sql.exec<{ m: number }>(
        "SELECT manifest_bytes AS m FROM file_space").one().m;
      const rounds = 4;
      const dirs = Array.from({ length: rounds + 1 }, (_, k) => `${deep(450)}${k}`);
      for (const dir of dirs) {
        await files.write(`${dir}/seed`, text("seed"), { expected: null, writer: CLIENT });
      }
      const name = (dir: string, n: number) =>
        `${dir}/${sha256Hex(`${dir}${n}`).slice(0, 16)}`.padEnd(700, "q");
      const [size, charge] = [sql.databaseSize, charged()];
      const target = charge + 3_000_000;
      const seen: number[] = [];
      for (let k = 0; k <= rounds; k++) {
        const made: string[] = [];
        for (let i = 0; charged() < target; i++) {
          made.push(name(dirs[k]!, i));
          await files.write(made.at(-1)!, text(`${k}.${i}`), { expected: null, writer: CLIENT });
        }
        seen.push((sql.databaseSize - size) / (charged() - charge));
        if (k < rounds) {
          made.forEach((at, i) => {
            if (i % 2 === 1) {
              files.remove(at, { expected: null, writer: CLIENT });
              files.purge(at);
            }
          });
        }
      }
      return seen;
    });
    // Thinning leaves half-used pages, so the ratio climbs past 1, within the worst fill.
    expect(Math.max(...ratios)).toBeGreaterThan(ratios[0]!);
    expect(Math.max(...ratios)).toBeLessThanOrEqual(WORST_FILL);
    await counted(r);
  }, 300_000);

  it("charges a history row's entries in the prune-time and released indexes", async () => {
    const r = await rig();
    const n = 2000;
    const grown = await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      const batch = (from: number, due: number | null) => {
        const start = sql.databaseSize;
        state.storage.transactionSync(() => {
          for (let i = from; i < from + n; i++) {
            sql.exec("INSERT INTO file_history (version, path, op, kind, content_id, size, mode, " +
                       "writer, at, prune_after, released) VALUES (?, ?, 'delete', NULL, NULL, " +
                       "0, 0, 'owner', 1, ?, ?)", i, `${W}/${sha256Hex(String(i)).slice(0, 24)}`,
                     due, due === null ? 0 : 1);
          }
        });
        return sql.databaseSize - start;
      };
      return batch(1_000_000, 5) - batch(2_000_000, null);
    });
    const [row] = await sqlIn(r.stub,
      `SELECT ${HISTORY_BYTES("h")} - ${record("length(CAST(h.path AS BLOB)) + 5 + 64 + 15",
                                                TABLE_KEPT)} - ${record(
        "length(CAST(h.path AS BLOB)) + 9", 1002)} AS indexes FROM file_history h LIMIT 1`);
    expect(grown).toBeGreaterThan(0);
    expect(n * (row!["indexes"] as number)).toBeGreaterThanOrEqual(grown);
  });

  it("names every index on a manifest table, each charged", async () => {
    const r = await rig();
    const tables = Object.keys(MANIFEST_TABLES);
    const indexes = await sqlIn(r.stub,
      `SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name IN (${
        tables.map(() => "?").join(", ")}) ORDER BY name`, ...tables);
    expect(indexes.map((row) => row["name"])).toEqual([...MANIFEST_INDEXES].sort());
  });

  it("holds a content's chunk list to the chunks its size is cut into", async () => {
    const r = await rig();
    await sqlIn(r.stub, "INSERT INTO file_contents (content_id, size) VALUES (?, ?)",
                "c".repeat(64), INLINE_BYTES + 1);
    const reason = /a chunk list holds the chunks its content's size is cut into/;
    expect((await failure(() => sqlIn(r.stub, "INSERT INTO file_content_chunks VALUES (?, 1, ?)",
                                      "c".repeat(64), "d".repeat(64)))).message).toMatch(reason);
    expect((await failure(() => sqlIn(r.stub, "INSERT INTO file_content_chunks VALUES (?, 0, ?)",
                                      "e".repeat(64), "d".repeat(64)))).message).toMatch(reason);
    await sqlIn(r.stub, "INSERT INTO file_content_chunks VALUES (?, 0, ?)", "c".repeat(64),
                "d".repeat(64));
  });
});

describe("deduplication in each scope", () => {
  it("charges a client for the owner's bytes it copies, and credits the owner once it lets go",
     async () => {
    const r = await rig();
    const big = new Uint8Array(3 * INLINE_BYTES + 7).fill(7);
    const small = text("s".repeat(4000));
    await inFiles(r.stub, {}, async (files: Files) => {
      await files.write(`${W}/owner-big`, big, { expected: null, writer: "owner" });
      await files.write(`${W}/owner-small`, small, { expected: null, writer: "owner" });
      await files.write(`${W}/client-big`, big, { expected: null, writer: CLIENT });
      await files.write(`${W}/client-small`, small, { expected: null, writer: CLIENT });
    });
    expect(await share(r, "owner")).toMatchObject({ chunk_bytes: big.length,
                                                    inline_bytes: small.length });
    await inFiles(r.stub, {}, (files: Files) => {
      for (const path of [`${W}/owner-big`, `${W}/owner-small`]) {
        files.remove(path, { expected: null, writer: "owner" });
        files.purge(path);
      }
    });
    expect(await share(r, CLIENT)).toMatchObject({ chunk_bytes: big.length,
                                                   inline_bytes: small.length });
    expect(await share(r, "owner")).toMatchObject({ chunk_bytes: 0, inline_bytes: 0 });
    // The real storage holds them once.
    expect((await sqlIn(r.stub, "SELECT chunk_bytes, inline_bytes FROM file_space"))[0])
      .toEqual({ chunk_bytes: big.length, inline_bytes: small.length });
    await counted(r);
  });

  it("lets a write keep the chunks its caller read, sending only its own tail", async () => {
    const r = await rig();
    const whole = new Uint8Array(CHUNK_BYTES + 5).fill(3);
    const head = sha256Hex(whole.subarray(0, CHUNK_BYTES));
    const entry = await inFiles(r.stub, {}, async (files: Files) => {
      await files.write(`${W}/log`, whole, { expected: null, writer: "owner" });
      // A client's append, as the environment makes it: the read head kept, a tail sent.
      return files.write(`${W}/log`, text("more"), {
        expected: null, writer: CLIENT, prefix: [{ sha256: head, size: CHUNK_BYTES }] });
    });
    expect(entry.size).toBe(CHUNK_BYTES + 4);
    expect(await share(r, CLIENT)).toMatchObject({ chunk_bytes: CHUNK_BYTES + 4 });
    await counted(r);
  });

  it("charges garbage to the scope that let its chunk go last", async () => {
    const r = await rig();
    const big = new Uint8Array(INLINE_BYTES + 11).fill(5);
    await inFiles(r.stub, {}, async (files: Files) => {
      await files.write(`${W}/owner.bin`, big, { expected: null, writer: "owner" });
      await files.write(`${W}/client.bin`, big, { expected: null, writer: CLIENT });
      for (const [path, writer] of [[`${W}/owner.bin`, "owner"], [`${W}/client.bin`, CLIENT]]) {
        files.remove(path!, { expected: null, writer: writer! });
        files.purge(path!);
      }
    });
    expect(await sqlIn(r.stub, "SELECT state, holder FROM file_chunks"))
      .toEqual([{ state: "garbage", holder: CLIENT }]);
    // Held as garbage, by the client alone: its share keeps the row's bytes.
    expect(await share(r, CLIENT)).toMatchObject({ chunk_bytes: 0, entries: 0, history: 0 });
    expect((await share(r, "owner"))!["manifest_bytes"]).toBe(SHARE_BYTES);
    await counted(r);
  });

  it("asks a client for bytes only another scope holds, and refuses a commit without them",
     async () => {
    const r = await rig();
    const piece = new Uint8Array(INLINE_BYTES + 9).fill(9);
    const sha = sha256Hex(piece);
    await r.stub.fileWrite(r.bind(null), { path: `${W}/owner.bin`, expected_etag: null,
                                           content: piece });
    const begin = () => r.stub.fileUploadBegin(r.bind(MUSE), { size: piece.length,
                                                               chunks: [sha] });
    const first = await begin();
    expect(first.missing).toEqual([sha]);
    const reason = "a chunk of this commit was not sent by its upload; send its bytes first";
    const commit = (intent: string, path: string) => r.stub.fileUploadCommit(r.bind(MUSE), {
      intent_id: intent, path, expected_etag: null });
    expect((await failure(() => commit(first.intent_id, `${W}/copy`))).message).toBe(reason);
    // A second upload of the same chunk is no proof of the first.
    const second = await begin();
    expect(second.missing).toEqual([sha]);
    expect((await failure(() => commit(second.intent_id, `${W}/copy`))).message).toBe(reason);
    await r.stub.fileUploadChunks(r.bind(MUSE), { intent_id: second.intent_id,
                                                  chunks: [{ sha256: sha, bytes: piece }] });
    await commit(second.intent_id, `${W}/copy`);
    // Held now, its next upload need not send it.
    expect((await begin()).missing).toEqual([]);
    // Its committed file and its open uploads hold the one chunk, charged once.
    expect(await share(r, CLIENT)).toMatchObject({ chunk_bytes: piece.length });
    await counted(r);
    // An open upload whose hashes, end to end, spell the chunk's across their boundary holds
    // none of it: the client's proof stands.
    const zeros = "0".repeat(32);
    await r.stub.fileUploadBegin(r.bind(MUSE), { size: CHUNK_BYTES + 1, chunks: [
      `${zeros}${sha.slice(0, 32)}`, `${sha.slice(32)}${zeros}`] });
    expect((await begin()).missing).toEqual([]);
  });

  it("holds an owner's write to the whole quota, beside the clients' share", async () => {
    const r = await rig();
    await setPolicy(r, { max_entries: 10, max_history: 200, keep_versions: 1, keep_recent: 1,
                         client_share: 30, owner_reserve: 0 });
    for (let i = 0; i < 8; i++) {
      await write(r, null, `${W}/owner-${i}`);
    }
    await write(r, MUSE, `${W}/muse-0`);
    await write(r, MUSE, `${W}/muse-1`);
    await refused(r, () => write(r, MUSE, `${W}/muse-2`),
                  `${FILE_QUOTA}: the owner's file entries are at their quota`);
  });
});

describe("the model calls and spend counters", () => {
  const open = (n: number, state = "reserved") =>
    "INSERT INTO model_reservations (id, turn, connection_id, source, model, state, " +
    "max_output_tokens, est_tokens, est_cost, policy_generation, day, month, created_at) " +
    `VALUES ('open-${n}', 'input:t${n}', 'c', 's', 'm', '${state}', 1, 1, 1, 1, ` +
    "'2026-10-31', '2026-10', 1)";

  it("keep at most CALLS_OPEN calls open and SPEND_KEPT counters", async () => {
    const r = await rig();
    await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      state.storage.transactionSync(() => {
        for (let i = 0; i < CALLS_OPEN; i++) {
          sql.exec(open(i, i % 2 === 0 ? "reserved" : "dispatched"));
        }
      });
      expect(() => sql.exec(open(CALLS_OPEN))).toThrow(/model_calls_full/);
      sql.exec(open(CALLS_OPEN + 1, "refused"));
      sql.exec("DELETE FROM model_spend");
      state.storage.transactionSync(() => {
        for (let i = 0; i < SPEND_KEPT; i++) {
          sql.exec("INSERT INTO model_spend (period, source, cost) VALUES ('d:x', ?, 0)", `s${i}`);
        }
      });
      expect(() => sql.exec("INSERT INTO model_spend (period, source, cost) VALUES " +
                            "('d:x', 'one more', 0)")).toThrow(/model_spend_full/);
    });
  }, 120_000);
});

/** A small deterministic PRNG. */
function prng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

describe("a random sequence of changes", () => {
  it.each([1, 2, 3, 4])("keeps every counter and scope reference equal to a recount, seed %i",
                        async (seed) => {
    const r = await rig();
    const rand = prng(seed);
    const pick = <T,>(items: readonly T[]): T => items[Math.floor(rand() * items.length)]!;
    const writers = ["owner", "agent", CLIENT, `client:${DOT.client}`];
    const paths = ["a", "b", "d/c", "d/e", "d/f/g", "h"].map((path) => `${W}/${path}`);
    const bodies = [text("one"), text("two"), text("x".repeat(4096)),
                    new Uint8Array(INLINE_BYTES + 3).fill(1),
                    new Uint8Array(INLINE_BYTES + 3).fill(2)];
    const broken: string[] = [];
    let done = 0;
    await inFiles(r.stub, { client_share: 60, owner_reserve: 10, keep_versions: 2,
                            keep_recent: 3, max_manifest_bytes: 400_000 },
                  async (files: Files) => {
      const temps: string[] = [];
      for (let step = 0; step < 120; step++) {
        const writer = pick(writers);
        const op = pick(["write", "write", "write", "mkdir", "remove", "rename", "restore",
                         "purge", "redact", "temp", "drop", "collect"]);
        try {
          if (op === "write") {
            await files.write(pick(paths), pick(bodies), { expected: null, writer });
          } else if (op === "mkdir") {
            files.mkdir(`${pick(paths)}x`, { expected: null, writer });
          } else if (op === "remove") {
            files.remove(pick(paths), { expected: null, recursive: true, writer });
          } else if (op === "rename") {
            files.rename(pick(paths), pick(paths), { expected: null, replace: rand() < 0.5,
                                                     writer });
          } else if (op === "restore" || op === "redact") {
            const path = pick(paths);
            const versions = files.history(path, null, 50).changes;
            if (op === "restore" && versions.length > 0) {
              files.restore(path, pick(versions).version, { expected: null, writer });
            } else if (op === "redact" && versions.length > 1) {
              files.redact(path, versions.at(-1)!.version);
            }
          } else if (op === "purge") {
            files.purge(pick(paths));
          } else if (op === "temp") {
            temps.push(files.tempDir("t-", writer));
            await files.write(`${temps.at(-1)}/f`, pick(bodies), { expected: null, writer });
          } else if (op === "drop" && temps.length > 0) {
            files.dropTemps([temps.pop()!], writer);
          } else if (op === "collect") {
            await files.collect(Date.now() / 1000 + 40 * 86_400);
          }
          done++;
        } catch (error) {
          if (/constraint|SQLITE|abort/i.test((error as Error).message)) {
            broken.push(`${op}: ${(error as Error).message}`);
          }
        }
      }
    });
    expect(broken).toEqual([]);
    expect(done).toBeGreaterThan(40);
    await counted(r);
  }, 300_000);
});

describe("the files policy's byte quota and shares", () => {
  it("are owner policy, with defaults and a high bound", async () => {
    const r = await rig();
    expect(FILE_DEFAULTS).toMatchObject({ max_manifest_bytes: 512 * 1024 ** 2,
                                          client_share: 25, owner_reserve: 25 });
    expect(FILE_SETTINGS.max_manifest_bytes).toEqual([0, 512 * 1024 ** 2]);
    for (const files of [{ max_manifest_bytes: 512 * 1024 ** 2 + 1 }, { client_share: 101 },
                         { owner_reserve: -1 }, { client_share: 2.5 }]) {
      expect((await failure(() => setPolicy(r, files))).message).toBe("invalid files policy");
    }
    await setPolicy(r, { max_manifest_bytes: 0 });
    await refused(r, () => write(r, null, `${W}/a`),
                  `${FILE_QUOTA}: the owner's file manifest bytes are at their quota`);
    await setPolicy(r, { client_share: 0 });
    await refused(r, () => write(r, MUSE, `${W}/a`),
                  `${FILE_SHARE}: this client's small-file bytes are at its share of the ` +
                  "owner's quota");
    await write(r, null, `${W}/a`);
  });
});

describe("the files policy's bounds inside the manifest's", () => {
  it("let each of them bind before the manifest bound does, at its high and its default",
     async () => {
    const r = await rig();
    const bytes = async () => Number((await sqlIn(r.stub, "SELECT manifest_bytes FROM " +
                                                        "file_space"))[0]!["manifest_bytes"]);
    const start = await bytes();
    const first = await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null,
                                                         content: text("1") });
    const file = await bytes() - start;
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, content: text("2"),
      expected_etag: (first as { entry: { etag: string } }).entry.etag });
    const version = await bytes() - start - file;
    expect([file, version]).toEqual([expect.any(Number), expect.any(Number)]);
    for (const [entries, history, inline, manifest] of [
      [FILE_SETTINGS.max_entries[1], FILE_SETTINGS.max_history[1],
       FILE_SETTINGS.max_inline_bytes[1], FILE_SETTINGS.max_manifest_bytes[1]],
      [FILE_DEFAULTS.max_entries, FILE_DEFAULTS.max_history, FILE_DEFAULTS.max_inline_bytes,
       FILE_DEFAULTS.max_manifest_bytes]] as const) {
      // A small file's least charge, at one version, and each further version's.
      expect(entries * file).toBeLessThanOrEqual(manifest);
      expect(history * version).toBeLessThanOrEqual(manifest);
      expect(inline).toBeLessThanOrEqual(manifest * 3 / 4);
    }
  });
});

describe("migration 10", () => {
  it("fails loudly on a file row from before it", async () => {
    const r = await rig();
    await write(r, null, `${W}/a`);
    const guard = MIGRATIONS[9]!.find((statement) => statement.includes("from before"))!;
    expect((await failure(() => sqlIn(r.stub, guard))).message).toMatch(/CHECK constraint/);
  });

  it("fails loudly on a model turn from before it", async () => {
    const r = await rig();
    const guard = MIGRATIONS[9]!.find((statement) => statement.includes("a turn from before"))!;
    await sqlIn(r.stub, guard);
    await sqlIn(r.stub, "INSERT INTO model_turns (turn, requests, tokens, cost, last_at) " +
                        "VALUES ('input:old', 0, 0, 0, 0)");
    expect((await failure(() => sqlIn(r.stub, guard))).message).toMatch(/CHECK constraint/);
  });
});

describe("the model turns' counters", () => {
  const reserve = (turn: string, state = "succeeded", n = 0) =>
    "INSERT INTO model_reservations (id, turn, connection_id, source, model, state, " +
    "max_output_tokens, est_tokens, est_cost, policy_generation, day, month, created_at) " +
    `VALUES ('r${n}-${turn.length}-${turn.slice(0, 8)}', '${turn}', 'c', 's', 'm', '${state}', ` +
    "1, 1, 1, 1, '2026-10-31', '2026-10', 1)";

  it("are made with a turn's first owner, go with its last or its thread, and never move",
     async () => {
    const r = await rig();
    const thread = `thread_${"a".repeat(32)}`;
    await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      const own = (owner: string, turn: string) => sql.exec(
        "INSERT INTO model_turn_owners (owner, thread, turn) VALUES (?, ?, ?)", owner, thread,
        turn);
      own("s:1", "input:a");
      own("s:2", "input:a");
      own("t:3", `task:${thread}:3`);
      expect(sql.exec("SELECT turn, thread, owners FROM model_turns ORDER BY turn").toArray())
        .toEqual([{ turn: "input:a", thread, owners: 2 },
                  { turn: `task:${thread}:3`, thread, owners: 1 }]);
      sql.exec("DELETE FROM model_turn_owners WHERE owner = 's:1'");
      expect(sql.exec("SELECT owners FROM model_turns WHERE turn = 'input:a'").one())
        .toEqual({ owners: 1 });
      expect(() => sql.exec("UPDATE model_turns SET thread = 'x'")).toThrow(/a turn never moves/);
      sql.exec("DELETE FROM model_turn_owners WHERE owner = 's:2'");
      expect(sql.exec("SELECT turn FROM model_turns").toArray())
        .toEqual([{ turn: `task:${thread}:3` }]);
      expect(() => own("x:4", "input:b")).toThrow(/CHECK constraint/);
    });
  });

  it("are refused a thread's owner past its open inputs or live compactions, no other",
     async () => {
    const r = await rig();
    const [a, b] = [`thread_${"a".repeat(32)}`, `thread_${"b".repeat(32)}`];
    await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      const own = (thread: string, owner: string) => sql.exec(
        "INSERT INTO model_turn_owners (thread, owner, turn) VALUES (?, ?, ?) " +
          "ON CONFLICT (thread, owner) DO NOTHING", thread, owner, `input:${thread}:${owner}`);
      for (let i = 1; i <= THREAD_INPUTS; i++) {
        own(a, `s:${i}`);
      }
      expect(() => own(a, `s:${THREAD_INPUTS + 1}`)).toThrow(/turn_owners_full/);
      // One it holds is no new one; another thread's are its own, of the same ids.
      own(a, "s:1");
      own(b, `s:${THREAD_INPUTS + 1}`);
      for (let i = 1; i <= THREAD_COMPACTIONS; i++) {
        own(a, `t:${i}`);
      }
      expect(() => own(a, `t:${THREAD_COMPACTIONS + 1}`)).toThrow(/turn_owners_full/);
      sql.exec("DELETE FROM model_turn_owners WHERE thread = ? AND owner = 's:2'", a);
      own(a, `s:${THREAD_INPUTS + 1}`);
      expect(sql.exec("SELECT thread, COUNT(*) AS n FROM model_turn_owners GROUP BY thread " +
                      "ORDER BY thread").toArray())
        .toEqual([{ thread: a, n: THREAD_INPUTS + THREAD_COMPACTIONS }, { thread: b, n: 1 }]);
    });
  });

  it("refuses a turn past TURN_BYTES", async () => {
    const r = await rig();
    expect((await failure(() => sqlIn(r.stub, reserve(`input:${"é".repeat(TURN_BYTES / 2)}`))))
      .message).toMatch(new RegExp(`a turn is at most ${TURN_BYTES} bytes`));
    await sqlIn(r.stub, reserve(`input:${"a".repeat(TURN_BYTES - 6)}`));
  });
});
