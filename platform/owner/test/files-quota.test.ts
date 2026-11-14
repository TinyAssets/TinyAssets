/**
 * The files plane's bounds (plan §6, untangle F1): the owner's storage and the file index's
 * one ceiling, each refused loudly with nothing left behind, never making room by dropping
 * anything, and never a dead end: what only frees is never refused, and housekeeping is
 * never wedged. Retention is the owner's, off by default, and prunes only as the owner set it.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { storageFull, storageFullRefusal } from "../src/storage-full";

import { refusalOf } from "../../contract/src/refusal";
import { CHUNK_BYTES, INLINE_BYTES, MAX_INTENT_CHUNKS } from "../../contract/src/limits";
import { FILE_DEFAULTS, KEEP_CEILING } from "../../contract/src/file-defaults";
import { FILE_INDEX_FULL, type FileLimits, INDEX_BYTES } from "../src/files";
import { canonical, DEFAULT_POLICY_JSON, FILE_SETTINGS } from "../../contract/src/policy-document";
import type { OwnerDO } from "../src/owner-do";
import { MIGRATIONS } from "../src/schema";
import { migrate } from "../../contract/src/migrate";
import { rig, type Rig } from "./ledger-support";
import { TIER_UNREAD } from "../src/tier";
import {
  crowd, failure, fileCounters, inFiles, laneAt, namedCode, sqlIn, text, TIERS,
} from "./support";

const W = "/workspace";
/** The refusal at the index's ceiling: what is full and what frees it, never an upgrade. */
const INDEX_FULL = `${FILE_INDEX_FULL}: your file index is full: it holds as many files and ` +
  "versions as one owner's index can. Delete files or old versions of files you no longer " +
  "need to make room.";

/** Everything the manifest holds, to show a refusal left nothing behind. */
async function snapshot(r: Rig) {
  return Promise.all(["file_space", "file_entries", "file_history", "file_contents",
                      "file_chunks", "file_intents"].map((table) =>
    sqlIn(r.stub, `SELECT * FROM ${table}`)));
}

async function refusedWithNothingLeft(r: Rig, limits: Partial<FileLimits>,
                                      act: Parameters<typeof inFiles>[2], reason: string) {
  const before = await snapshot(r);
  const error = await failure(() => inFiles(r.stub, limits, act));
  expect([error.code, error.message]).toEqual([namedCode(reason), reason]);
  expect(await snapshot(r)).toEqual(before);
}

async function setPolicy(r: Rig, files: unknown) {
  const { generation, policy } = await r.stub.policyRead(r.bind(null));
  return r.stub.policyWrite(r.session(null), { expected_generation: generation,
                                               policy: { ...policy, files } as never });
}

const write = (r: Rig, path: string, body: string) => r.stub.fileWrite(
  r.bind(null), { path, expected_etag: null, content: text(body) });

async function counted(r: Rig) {
  const [kept, recounted] = await fileCounters(r.stub);
  expect(kept).toEqual(recounted);
}

describe("a file bound", () => {
  it("refuses a change past the file index's ceiling, in plain words with no upgrade, and " +
     "leaves nothing behind", async () => {
    const r = await rig();
    await write(r, `${W}/a`, "a");
    const back = await crowd(r.stub, INDEX_BYTES, 100);
    await refusedWithNothingLeft(r, {}, (files) =>
      files.write(`${W}/b`, text("b"), { expected: null, writer: "owner" }), INDEX_FULL);
    // Through the door: its own code, 507, and nothing about plans.
    const refused = refusalOf(await failure(() => write(r, `${W}/b`, "b")))!;
    expect([refused.code, refused.status, refused.facts]).toEqual([FILE_INDEX_FULL, 507,
                                                                  undefined]);
    expect(refused.message).not.toMatch(/upgrade|plan|tier/);
    await back();
    await write(r, `${W}/b`, "b");
    await counted(r);
  });

  it("refuses chunk bytes past max_bytes at the upload's begin, before any byte is sent",
     async () => {
    const r = await rig();
    await inFiles(r.stub, { max_bytes: INLINE_BYTES + 1 }, (files) =>
      files.write(`${W}/a`, new Uint8Array(INLINE_BYTES + 1).fill(1),
                  { expected: null, writer: "owner" }));
    await refusedWithNothingLeft(r, { max_bytes: INLINE_BYTES + 1 }, (files) =>
      files.write(`${W}/b`, new Uint8Array(INLINE_BYTES + 1).fill(2),
                  { expected: null, writer: "owner" }),
    storageFullRefusal(storageFull(INLINE_BYTES + 1, INLINE_BYTES + 1, 0, INLINE_BYTES + 1,
                                   null, null)).message);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_chunks")).toEqual([{ n: 1 }]);
  });

  it("names old versions among what frees storage, as retention keeps them by default", () => {
    expect(storageFull(10, 1, 0, 10, null, null).free).toContain("delete old versions of files");
    expect(storageFullRefusal(storageFull(10, 1, 0, 10, null, null)).message)
      .toContain("delete old versions of files");
  });

  it("refuses a file past max_file_bytes, through any door", async () => {
    const r = await rig();
    const reason = "file_quota: the file is larger than the owner's file size quota";
    await refusedWithNothingLeft(r, { max_file_bytes: INLINE_BYTES + 1 }, (files) =>
      files.write(`${W}/a`, new Uint8Array(INLINE_BYTES + 2), { expected: null, writer: "o" }),
    reason);
    await refusedWithNothingLeft(r, { max_file_bytes: 3 }, (files) =>
      files.write(`${W}/a`, text("four"), { expected: null, writer: "o" }), reason);
    await refusedWithNothingLeft(r, { max_file_bytes: INLINE_BYTES + 1 }, (files) =>
      files.begin(INLINE_BYTES + 2, ["a".repeat(64)], "owner"), reason);
  });

  it("lets every change that only frees through a full index, and lets nothing go to admit " +
     "one that adds", async () => {
    const r = await rig();
    await inFiles(r.stub, {}, async (files) => {
      for (const name of ["a", "b", "c", "d"]) {
        await files.write(`${W}/${name}`, text(name), { expected: null, writer: "o" });
      }
    });
    const back = await crowd(r.stub, INDEX_BYTES, 0);
    // A delete adds a history row, but only frees: a full index never refuses it. A rename
    // adds history, so it is refused, and no kept version is let go to make it room.
    await inFiles(r.stub, {}, (files) => files.remove(`${W}/a`, { expected: null, writer: "o" }));
    await refusedWithNothingLeft(r, {}, (files) =>
      files.rename(`${W}/b`, `${W}/c`, { expected: null, replace: true, writer: "o" }),
    INDEX_FULL);
    await inFiles(r.stub, {}, (files) => files.remove(`${W}/c`, { expected: null, writer: "o" }));
    expect(await r.stub.fileUsage(r.bind(null))).toMatchObject({ entries: 2, history: 6 });
    await back();
    await counted(r);
  });
});

describe("file history", () => {
  it("keeps every version by default, and a full index lets none go to admit a write",
     async () => {
    const r = await rig();
    for (let i = 0; i < 30; i++) {
      await write(r, `${W}/a`, `${i}`);
    }
    for (let i = 0; i < 5; i++) {
      await write(r, `${W}/n${i}`, `${i}`);
      await r.stub.fileRemove(r.bind(null), { path: `${W}/n${i}`, expected_etag: null,
                                              recursive: false });
    }
    const rows = async () => sqlIn(r.stub, "SELECT version, prune_after FROM file_history " +
                                             "ORDER BY version");
    const kept = await rows();
    expect(kept).toHaveLength(40);
    expect(kept.every((row) => row["prune_after"] === null)).toBe(true);
    // At the ceiling, the deleted paths' versions stay and the write is refused.
    const back = await crowd(r.stub, INDEX_BYTES, 0);
    expect((await failure(() => write(r, `${W}/b`, "b"))).message).toBe(INDEX_FULL);
    expect(await rows()).toEqual(kept);
    // Housekeeping lets none go either: nothing is due.
    await inFiles(r.stub, {}, (files) => files.run(Date.now() / 1000 + 400 * 86_400));
    expect(await rows()).toEqual(kept);
    await back();
    await counted(r);
  });

  it("binds each retention setting alone, as the owner set it, never the current version",
     async () => {
    const r = await rig();
    const o = { expected: null, writer: "o" };
    await inFiles(r.stub, { keep_recent: 3 }, async (files) => {
      for (let i = 0; i < 5; i++) {
        await files.write(`${W}/a`, text(`${i}`), o);
      }
    });
    await inFiles(r.stub, { retention_days: 0 }, async (files) => {
      for (let i = 0; i < 3; i++) {
        await files.write(`${W}/b`, text(`${i}`), o);
      }
    });
    // `keep_versions` alone is a count: the third version goes, the newest two stay.
    await inFiles(r.stub, { keep_versions: 2 }, async (files) => {
      for (const body of ["x", "xx", "xxx"]) {
        await files.write(`${W}/c`, text(body), o);
      }
    });
    expect(await sqlIn(r.stub, "SELECT path, size, prune_after IS NOT NULL AS due FROM " +
                                 "file_history ORDER BY version")).toEqual([
      ...[1, 1, 1].map((size) => ({ path: `${W}/a`, size, due: 0 })),
      ...[1, 1, 0].map((due) => ({ path: `${W}/b`, size: 1, due })),
      { path: `${W}/c`, size: 2, due: 0 }, { path: `${W}/c`, size: 3, due: 0 }]);
  });

  it("is never filled by live files' churn inside the window", async () => {
    const r = await rig();
    await setPolicy(r, { keep_versions: 1, keep_recent: 1 });
    for (let round = 0; round < 10; round++) {
      for (let i = 0; i < 10; i++) {
        await write(r, `${W}/f${i}`, `${round}`);
      }
    }
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(10);
    // Kept inside the window, up to keep_recent each, and never past it.
    await setPolicy(r, { keep_versions: 1, keep_recent: 3 });
    await runDurableObjectAlarm(r.stub);
    for (let round = 0; round < 5; round++) {
      await write(r, `${W}/f0`, `again ${round}`);
    }
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_history WHERE path = ?",
                       `${W}/f0`)).toEqual([{ n: 3 }]);
    await counted(r);
  });

  it("marks every kept row of a path that goes due, those kept for their age too",
     async () => {
    const r = await rig();
    await setPolicy(r, { keep_versions: 1, keep_recent: 3, retention_days: 30 });
    await write(r, `${W}/old`, "1");
    await write(r, `${W}/old`, "2");  // version 1 is now kept for its age, due to go
    const due = () => sqlIn(r.stub, "SELECT released, prune_after IS NOT NULL AS due " +
                                      "FROM file_history ORDER BY version");
    expect(await due()).toEqual([{ released: 0, due: 1 }, { released: 0, due: 0 }]);
    await r.stub.fileRemove(r.bind(null), { path: `${W}/old`, expected_etag: null,
                                            recursive: false });
    expect(await due())
      .toEqual([{ released: 0, due: 1 }, { released: 0, due: 1 }, { released: 0, due: 1 }]);
    // A path made again takes its rows back from the ones its delete marked.
    await write(r, `${W}/old`, "3");
    expect(await due()).toEqual([{ released: 0, due: 1 }, { released: 0, due: 1 },
                                 { released: 0, due: 0 }]);
  });

  it("never lowers the feed's floor when it prunes a path older than one purged", async () => {
    const r = await rig();
    const limits = { keep_recent: 2, retention_days: 0 };
    await setPolicy(r, limits);
    const pair = async (name: string) => {
      await write(r, `${W}/${name}`, name);
      await r.stub.fileRemove(r.bind(null), { path: `${W}/${name}`, expected_etag: null,
                                              recursive: false });
    };
    for (let i = 0; i < 5; i++) {
      await pair(`n${i}`);  // versions 2i + 1 and 2i + 2, due at once
    }
    expect(await r.stub.fileHistoryPurge(r.bind(null), { path: `${W}/n4` }))
      .toEqual({ purged: 2 });  // the floor rises to n4's delete, version 10
    await inFiles(r.stub, limits, (files) => files.run(Date.now() / 1000 + 1));
    expect(await sqlIn(r.stub, "SELECT feed_floor FROM file_space"))
      .toEqual([{ feed_floor: 10 }]);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_history WHERE path = ?",
                       `${W}/n0`)).toEqual([{ n: 0 }]);
  });

  it("raises the feed's floor to the newest pruned path-end, in whatever order rows come due",
     async () => {
    const r = await rig();
    for (const name of ["a", "b"]) {
      await write(r, `${W}/${name}`, name);
      await r.stub.fileRemove(r.bind(null), { path: `${W}/${name}`, expected_etag: null,
                                              recursive: false });
    }
    // Rows marked under two retentions, as a re-mark still under way leaves them: b's
    // delete (version 4) due before a's (version 2), both writes not yet.
    await sqlIn(r.stub, "UPDATE file_history SET prune_after = CASE version WHEN 4 THEN 1 " +
                          "WHEN 2 THEN 2 ELSE 9000000000 END");
    await inFiles(r.stub, {}, (files) => files.run(Date.now() / 1000));
    expect(await sqlIn(r.stub, "SELECT feed_floor FROM file_space"))
      .toEqual([{ feed_floor: 4 }]);
    expect(await sqlIn(r.stub, "SELECT version FROM file_history ORDER BY version"))
      .toEqual([{ version: 1 }, { version: 3 }]);
  });

  it("is re-marked by a retention change, on the one schedule", async () => {
    const r = await rig();
    await setPolicy(r, { keep_versions: 1, keep_recent: 1 });
    await write(r, `${W}/b`, "1");
    await write(r, `${W}/c`, "1");
    await r.stub.fileRemove(r.bind(null), { path: `${W}/c`, expected_etag: null,
                                            recursive: false });
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(2);
    // A retention change re-marks the rows already kept, on the one schedule.
    await setPolicy(r, { keep_versions: 1, keep_recent: 1, retention_days: 0 });
    // Due at once: the alarm may already have run by itself; run it if it has not.
    await runDurableObjectAlarm(r.stub);
    for (let i = 0; i < 50 && (await r.stub.fileUsage(r.bind(null))).history !== 1; i++) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(1);
    await write(r, `${W}/d`, "1");
    expect(await sqlIn(r.stub, "SELECT retained_keep, retained_recent, retained_days, " +
                                 "retain_cursor FROM file_space"))
      .toEqual([{ retained_keep: 1, retained_recent: 1, retained_days: 0, retain_cursor: null }]);
    await counted(r);
  });

  it("purges a gone path's history whole, and the feed asks a reader behind it to rescan",
     async () => {
    const r = await rig();
    await write(r, `${W}/gone`, "1");
    const { cursor } = await r.stub.fileChanges(r.bind(null), { after: 0, limit: 10 });
    await r.stub.fileRemove(r.bind(null), { path: `${W}/gone`, expected_etag: null,
                                            recursive: false });
    expect(await r.stub.fileHistoryPurge(r.bind(null), { path: `${W}/gone` }))
      .toEqual({ purged: 2 });
    expect((await r.stub.fileChanges(r.bind(null), { after: cursor, limit: 10 })).reset)
      .toBe(true);
    await write(r, `${W}/kept`, "1");
    expect(await r.stub.fileHistoryPurge(r.bind(null), { path: `${W}/kept` }))
      .toEqual({ purged: 0 });
  });

  it("re-marks rows for a longer retention too, a batch of paths a pass", async () => {
    const r = await rig();
    await inFiles(r.stub, { keep_versions: 1, retention_days: 30 }, async (files) => {
      for (let i = 0; i < 600; i++) {
        await files.write(`${W}/p${i}`, text("1"), { expected: null, writer: "o" });
        await files.write(`${W}/p${i}`, text("2"), { expected: null, writer: "o" });
      }
    });
    // The rows were marked under keep 1; the policy in force keeps 3.
    await sqlIn(r.stub, "UPDATE file_space SET retained_keep = 1, retained_days = 30");
    await inFiles(r.stub, { keep_versions: 3, retention_days: 30 }, async (files) => {
      expect(files.next(1000)).toBe(1000);
      await files.run(Date.now() / 1000);
      expect(files.next(1000)).toBe(1000);  // 500 paths a pass: one more to go
      await files.run(Date.now() / 1000);
      expect(files.next(Date.now() / 1000)).toBe(null);
    });
    expect(await sqlIn(r.stub,
                       "SELECT COUNT(*) AS n FROM file_history WHERE prune_after IS NOT NULL"))
      .toEqual([{ n: 0 }]);
  });

  it("re-marks rows for a change of days alone, each due its days after it was written",
     async () => {
    const r = await rig();
    await inFiles(r.stub, { keep_versions: 1, retention_days: 30 }, async (files) => {
      for (const body of ["1", "2", "3"]) {
        await files.write(`${W}/a`, text(body), { expected: null, writer: "o" });
      }
    });
    await sqlIn(r.stub, "UPDATE file_space SET retained_keep = 1, retained_days = 30");
    await inFiles(r.stub, { keep_versions: 1, retention_days: 2 }, async (files) => {
      expect(files.next(1000)).toBe(1000);
      await files.run(1000);
    });
    expect(await sqlIn(r.stub, "SELECT prune_after - at AS due FROM file_history " +
                                 "ORDER BY version"))
      .toEqual([{ due: 2 * 86_400 }, { due: 2 * 86_400 }, { due: null }]);
  });

  it("is armed at once by a policy write that changes retention", async () => {
    const r = await rig();
    await write(r, `${W}/a`, "1");
    // Only the nightly manifest's job: nothing of the files' is due.
    expect(await runInDurableObject(r.stub, (_instance: OwnerDO, state) =>
      state.storage.getAlarm())).toBe((await laneAt(r.stub, "manifest"))! * 1000);
    await setPolicy(r, { keep_recent: 5 });
    // Due at once, the alarm runs by itself and re-marks the rows under the new policy.
    const retained = async () => (await sqlIn(r.stub, "SELECT retained_recent FROM file_space"))[0];
    for (let i = 0; i < 50 && (await retained())!["retained_recent"] !== 5; i++) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    expect(await retained()).toEqual({ retained_recent: 5 });
  });

  it("drops rows past a lowered keep_recent, in housekeeping", async () => {
    const r = await rig();
    await inFiles(r.stub, { keep_versions: 1, keep_recent: 5 }, async (files) => {
      for (const body of ["1", "2", "3", "4", "5"]) {
        await files.write(`${W}/a`, text(body), { expected: null, writer: "o" });
      }
    });
    await sqlIn(r.stub, "UPDATE file_space SET retained_keep = 1, retained_recent = 5");
    await inFiles(r.stub, { keep_versions: 1, keep_recent: 2 }, async (files) => {
      expect(files.next(1000)).toBe(1000);
      await files.run(1000);
    });
    expect(await sqlIn(r.stub, "SELECT op FROM file_history WHERE path = ?", `${W}/a`))
      .toEqual([{ op: "write" }, { op: "write" }]);
    expect(await sqlIn(r.stub, "SELECT retained_recent FROM file_space"))
      .toEqual([{ retained_recent: 2 }]);
  });

  it("starts a pass over when the policy changes during it", async () => {
    const r = await rig();
    await inFiles(r.stub, { keep_versions: 1, retention_days: 30 }, async (files) => {
      for (let i = 0; i < 600; i++) {
        await files.write(`${W}/p${String(i).padStart(3, "0")}`, text("1"),
                          { expected: null, writer: "o" });
        await files.write(`${W}/p${String(i).padStart(3, "0")}`, text("2"),
                          { expected: null, writer: "o" });
      }
    });
    await sqlIn(r.stub, "UPDATE file_space SET retained_keep = 1, retained_days = 30");
    // A first pass under keep 2 stops after 500 paths; keep 1 then comes back in force.
    await inFiles(r.stub, { keep_versions: 2, retention_days: 30 }, (files) => files.run(1000));
    expect(await sqlIn(r.stub, "SELECT retain_cursor FROM file_space"))
      .toEqual([{ retain_cursor: `${W}/p499` }]);
    await inFiles(r.stub, { keep_versions: 1, retention_days: 30 }, async (files) => {
      await files.run(1000);
      await files.run(1000);
      expect(files.next(1000)).not.toBe(1000);
    });
    // Every path's older version is due again, the first 500's included.
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_history " +
                                 "WHERE prune_after IS NULL"))
      .toEqual([{ n: 600 }]);
  });  // 600 versions written: past the default timeout under load (slice D)

  it("never wedges housekeeping: a pass at every bound still expires, prunes and collects",
     async () => {
    const r = await rig();
    await inFiles(r.stub, {}, async (files) => {
      for (let i = 0; i < 20; i++) {
        const dir = files.tempDir("t-", "agent");
        for (let j = 0; j < 30; j++) {
          await files.write(`${dir}/f${j}`, text(`${i}-${j}`), { expected: null,
                                                                  writer: "agent" });
        }
      }
      await files.write(`${W}/a`, text("1"), { expected: null, writer: "o" });
      files.remove(`${W}/a`, { expected: null, writer: "o" });
      files.begin(INLINE_BYTES + 1, ["a".repeat(64)], "owner");
    });
    // The temporary area keeps no history at all.
    expect(await sqlIn(r.stub,
                       "SELECT COUNT(*) AS n FROM file_history WHERE path GLOB '/.ta/*'"))
      .toEqual([{ n: 0 }]);
    await sqlIn(r.stub, "UPDATE file_temps SET expires_at = 1");
    await sqlIn(r.stub, "UPDATE file_intents SET expires_at = 1");
    await sqlIn(r.stub, "UPDATE file_history SET prune_after = 1");
    const before = await r.stub.fileUsage(r.bind(null));
    expect(before.entries).toBeGreaterThan(600);
    expect(before.history).toBe(2);
    const back = await crowd(r.stub, INDEX_BYTES, 0);
    await inFiles(r.stub, { max_bytes: 0 }, (files) => files.run(Date.now() / 1000));
    const after = await r.stub.fileUsage(r.bind(null));
    expect([after.entries, after.history, after.intents]).toEqual([0, 0, 0]);
    await back();
    await counted(r);
  });
});

describe("the retention marks' migration (untangle F1, owner migration 8)", () => {
  /** An object of the migrations through H1's 7, `act` run on it, then migrated whole. */
  const migrated = async (act: (sql: SqlStorage) => void, r: Rig | null = null) => {
    return runInDurableObject((r ?? await rig()).stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      for (const { name } of sql.exec<{ name: string }>("SELECT name FROM sqlite_master " +
          "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' " +
          "AND name NOT GLOB 'cf_*'").toArray()) {
        sql.exec(`DROP TABLE "${name}"`);
      }
      migrate(state.storage, MIGRATIONS.slice(0, 7));
      act(sql);
      migrate(state.storage, MIGRATIONS);
      return sql.exec("SELECT retained_keep, retained_recent, retained_days, retain_cursor " +
                      "FROM file_space").toArray();
    });
  };

  it("leaves the marks off, and re-marks every kept row of an owner that keeps history",
     async () => {
    expect(await migrated(() => {})).toEqual([{ retained_keep: null, retained_recent: null,
                                                retained_days: null, retain_cursor: null }]);
    expect(await migrated((sql) => {
      expect(sql.exec("SELECT retained_keep, retained_recent, retained_days FROM file_space")
        .toArray()).toEqual([{ retained_keep: 10, retained_recent: 20, retained_days: 30 }]);
      sql.exec("INSERT INTO file_history (version, path, op, kind, content_id, size, mode, " +
                 "writer, at, prune_after) VALUES (1, '/workspace/a', 'mkdir', 'dir', NULL, 0, " +
                 "0, 'owner', 1, 86401)");
    })).toEqual([{ retained_keep: null, retained_recent: null, retained_days: null,
                   retain_cursor: "" }]);
  });

  it("lets no row the old defaults marked due go while the re-mark is under way", async () => {
    const r = await rig();
    // 501 paths, one more than a pass re-marks, each row due under the old 30 days.
    await migrated((sql) => sql.exec(
      "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 501) " +
        "INSERT INTO file_history (version, path, op, kind, content_id, size, mode, writer, " +
        "at, prune_after) SELECT i, '/workspace/p' || i, 'mkdir', 'dir', NULL, 0, 0, " +
        "'owner', 1, 86401 FROM n"), r);
    for (let pass = 0; pass < 2; pass++) {
      await inFiles(r.stub, {}, (files) => files.run(Date.now() / 1000));
    }
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n, COUNT(prune_after) AS due " +
                                 "FROM file_history")).toEqual([{ n: 501, due: 0 }]);
  });
});

describe("the files policy", () => {
  it("is the defaults until the owner's policy sets its own, in session", async () => {
    const r = await rig();
    const tier = TIERS.free.storage_bytes;
    expect((await r.stub.fileUsage(r.bind(null))).limits)
      .toEqual({ ...FILE_DEFAULTS, max_bytes: tier });
    expect(JSON.parse(DEFAULT_POLICY_JSON)).not.toHaveProperty("files");
    await setPolicy(r, { max_file_bytes: 1 });
    expect((await r.stub.fileUsage(r.bind(null))).limits)
      .toEqual({ ...FILE_DEFAULTS, max_bytes: tier, max_file_bytes: 1 });
    await write(r, `${W}/a`, "a");
    expect((await failure(() => write(r, `${W}/b`, "bb"))).message)
      .toBe("file_quota: the file is larger than the owner's file size quota");
    expect(canonical((await r.stub.policyRead(r.bind(null))).policy.files))
      .toBe('{"max_file_bytes":1}');
  });

  it("holds file bytes to the tier's storage, or the owner's own lower figure", async () => {
    const r = await rig();
    const big = (n: number) => new Uint8Array(INLINE_BYTES + 1).fill(n);
    const put = (path: string, n: number, tiers: unknown = TIERS) =>
      r.stub.fileWrite(r.bind(null, { tiers }), { path, expected_etag: null, content: big(n) });
    // The tier's figure, as the binder hands it from the control record.
    const small = { free: { runs: 5, storage_bytes: INLINE_BYTES + 1 },
                    paid: { runs: 10, storage_bytes: 250_000_000_000 } };
    await put(`${W}/a`, 1, small);
    expect((await r.stub.fileUsage(r.bind(null, { tiers: small }))).limits.max_bytes)
      .toBe(INLINE_BYTES + 1);
    expect(refusalOf((await failure(() => put(`${W}/b`, 2, small))))?.facts)
      .toMatchObject({ limit_bytes: INLINE_BYTES + 1, tier: "free",
                       upgrade: { tier: "paid", limit_bytes: 250_000_000_000 } });
    // The owner's own figure binds only below the tier's.
    await setPolicy(r, { max_bytes: 10 * INLINE_BYTES });
    expect((await r.stub.fileUsage(r.bind(null))).limits.max_bytes).toBe(10 * INLINE_BYTES);
    await setPolicy(r, { max_bytes: FILE_SETTINGS.max_bytes[1] });
    expect((await r.stub.fileUsage(r.bind(null))).limits.max_bytes)
      .toBe(TIERS.free.storage_bytes);
    await put(`${W}/b`, 2);
  });

  it("adds no file byte while the tier's storage is not known, and still frees", async () => {
    const r = await rig();
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null,
                                           content: new Uint8Array(INLINE_BYTES + 1) });
    // A control record with no tiers: the kept numbers go, and no figure takes their place.
    const none = r.bind(null, { tiers: null });
    expect((await r.stub.fileUsage(none)).limits.max_bytes).toBeNull();
    expect((await failure(() => r.stub.fileWrite(none, {
      path: `${W}/b`, expected_etag: null, content: new Uint8Array(INLINE_BYTES + 1).fill(7) })))
      .message).toBe(TIER_UNREAD);
    await r.stub.fileRemove(none, { path: `${W}/a`, expected_etag: null, recursive: false });
  });

  it("has retention off by default, and holds no setting for the index's room", async () => {
    const r = await rig();
    expect([FILE_DEFAULTS.retention_days, FILE_DEFAULTS.keep_versions,
            FILE_DEFAULTS.keep_recent]).toEqual([null, null, null]);
    for (const retired of ["max_entries", "max_history", "max_manifest_bytes",
                           "max_inline_bytes"]) {
      expect((await failure(() => setPolicy(r, { [retired]: 1 }))).message)
        .toBe("invalid files policy");
    }
    // Retention the owner sets has no rule against any other setting.
    await setPolicy(r, { keep_versions: KEEP_CEILING, keep_recent: KEEP_CEILING,
                         retention_days: 3650 });
    expect(FILE_SETTINGS.keep_versions[1]).toBe(KEEP_CEILING);
    expect(FILE_SETTINGS.max_file_bytes[1]).toBe(MAX_INTENT_CHUNKS * CHUNK_BYTES);
    expect(INDEX_BYTES).toBe(512 * 1024 ** 2);
  });

  it.each([
    ["an unknown setting", { sync_exclude: 1 }],
    ["a setting below its range", { keep_versions: 0 }],
    ["a setting above its range", { keep_recent: KEEP_CEILING + 1 }],
    ["a fraction", { retention_days: 1.5 }],
    ["text", { max_bytes: "1" }],
    ["off spelled as null", { keep_versions: null }],
    ["not a table", [1]],
  ])("refuses %s", async (_what, files) => {
    const r = await rig();
    expect((await failure(() => setPolicy(r, files))).message).toBe("invalid files policy");
  });
});
