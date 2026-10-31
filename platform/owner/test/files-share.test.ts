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
  type Files, FILE_QUOTA, FILE_SHARE, INLINE_BYTES, MAX_OPEN_INTENTS, sha256Hex,
} from "../src/files";
import { WriteGate } from "../src/gate";
import {
  CHUNK_LIST_ROW_BYTES, PAGE_BYTES, RECORD_FIXED, record, recordBytes, SHARE_BYTES,
} from "../src/manifest";
import type { OwnerDO } from "../src/owner-do";
import { MAX_PATH_BYTES } from "../src/paths";
import { FILE_DEFAULTS, FILE_SETTINGS } from "../src/policy";
import { MIGRATIONS, TURN_BYTES, TURNS_KEPT } from "../src/schema";
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
    expect((await r.stub.fileUsage(r.bind(null))).share).toBeNull();
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
    // Its writer changes only so: never on a chunk in use.
    await inFiles(r.stub, {}, (files: Files) => files.begin(INLINE_BYTES + 1, [sha], "owner"));
    expect(await share(r, CLIENT)).toBeUndefined();
    expect((await failure(() => sqlIn(r.stub, "UPDATE file_chunks SET writer = ?", CLIENT)))
      .message).toMatch(/writer changes only when an upload revives it/);
    expect(await sqlIn(r.stub, "SELECT who, manifest_bytes FROM file_shares"))
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

describe("the files policy's byte quota and shares", () => {
  it("are owner policy, with defaults and a high bound", async () => {
    const r = await rig();
    expect(FILE_DEFAULTS).toMatchObject({ max_manifest_bytes: 1024 ** 3, client_share: 25,
                                          owner_reserve: 25 });
    expect(FILE_SETTINGS.max_manifest_bytes).toEqual([0, 2 * 1024 ** 3]);
    for (const files of [{ max_manifest_bytes: 2 * 1024 ** 3 + 1 }, { client_share: 101 },
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

describe("migration 10", () => {
  it("fails loudly on a file row from before it", async () => {
    const r = await rig();
    await write(r, null, `${W}/a`);
    const guard = MIGRATIONS[9]!.find((statement) => statement.includes("from before"))!;
    expect((await failure(() => sqlIn(r.stub, guard))).message).toMatch(/CHECK constraint/);
  });
});

describe("the model turns' counters", () => {
  const reserve = (turn: string, state = "succeeded", n = 0) =>
    "INSERT INTO model_reservations (id, turn, connection_id, source, model, state, " +
    "max_output_tokens, est_tokens, est_cost, policy_generation, day, month, created_at) " +
    `VALUES ('r${n}-${turn.length}-${turn.slice(0, 8)}', '${turn}', 'c', 's', 'm', '${state}', ` +
    "1, 1, 1, 1, '2026-10-31', '2026-10', 1)";

  it("keep at most TURNS_KEPT, dropping the turn idle longest with no call open",
     async () => {
    const r = await rig();
    await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      state.storage.transactionSync(() => {
        for (let i = 0; i < TURNS_KEPT; i++) {
          sql.exec("INSERT INTO model_turns (turn, requests, tokens, cost, last_at) " +
                     "VALUES (?, 1, 1, 1, ?)", `input:t${i}`, i);
        }
      });
      // The two oldest have a call open, reserved or dispatched; the next oldest goes.
      sql.exec(reserve("input:t0", "dispatched"));
      sql.exec(reserve("input:t1", "reserved"));
      sql.exec(reserve("input:new"));
      const turns = (turn: string) => sql.exec("SELECT 1 FROM model_turns WHERE turn = ?", turn)
        .toArray().length;
      expect([turns("input:t0"), turns("input:t1"), turns("input:t2"), turns("input:new")])
        .toEqual([1, 1, 0, 1]);
      const counted = sql.exec<{ n: number; kept: number }>(
        "SELECT COUNT(*) AS n, (SELECT turns FROM model_usage) AS kept FROM model_turns").one();
      expect(counted).toEqual({ n: TURNS_KEPT, kept: TURNS_KEPT });
    });
  }, 120_000);

  it("refuses a turn past TURN_BYTES", async () => {
    const r = await rig();
    expect((await failure(() => sqlIn(r.stub, reserve(`input:${"é".repeat(TURN_BYTES / 2)}`))))
      .message).toMatch(/a turn is at most 320 bytes/);
    await sqlIn(r.stub, reserve(`input:${"a".repeat(TURN_BYTES - 6)}`));
  });
});
