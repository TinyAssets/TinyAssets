/**
 * File quotas (plan §6): owner-editable policy defaults, each refused loudly at its
 * bound with nothing left behind, never making room by dropping history, and never a
 * dead end: what only frees is never refused, and housekeeping is never wedged.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { storageFull, storageFullMessage, storageFullOf } from "../src/storage-full";

import {
  CHUNK_BYTES, type FileLimits, INLINE_BYTES, KEEP_CEILING, MAX_INTENT_CHUNKS,
} from "../src/files";
import { canonical, DEFAULT_POLICY_JSON, FILE_DEFAULTS, FILE_SETTINGS } from "../src/policy";
import type { OwnerDO } from "../src/owner-do";
import { rig, type Rig } from "./ledger-support";
import { TIER_UNREAD } from "../src/tier";
import { failure, fileCounters, inFiles, laneAt, sqlIn, text, TIERS } from "./support";

const W = "/workspace";

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
  expect([error.name, error.message]).toEqual(["Refused", reason]);
  expect(await snapshot(r)).toEqual(before);
}

async function setPolicy(r: Rig, files: unknown) {
  const { generation, policy } = await r.stub.policyRead(r.bind(null));
  return r.stub.policyWrite(r.session(null), { expected_generation: generation,
                                               policy: { ...policy, files } as never });
}

const write = (r: Rig, path: string, body: string) => r.stub.fileWrite(
  r.bind(null), { path, expected_etag: null, content: text(body) });

describe("a file quota", () => {
  it("refuses an entry past max_entries, parents and all", async () => {
    const r = await rig();
    await inFiles(r.stub, { max_entries: 2 }, async (files) => {
      await files.write(`${W}/a`, text("a"), { expected: null, writer: "owner" });
    });
    await refusedWithNothingLeft(r, { max_entries: 2 }, (files) =>
      files.write(`${W}/d/b`, text("b"), { expected: null, writer: "owner" }),
    "file_quota: the owner's file entries are at their quota");
    await inFiles(r.stub, { max_entries: 2 }, (files) =>
      files.write(`${W}/b`, text("b"), { expected: null, writer: "owner" }));
  });

  it("refuses history past max_history", async () => {
    const r = await rig();
    await inFiles(r.stub, { max_history: 2 }, async (files) => {
      await files.write(`${W}/a`, text("1"), { expected: null, writer: "owner" });
      await files.write(`${W}/a`, text("2"), { expected: null, writer: "owner" });
    });
    await refusedWithNothingLeft(r, { max_history: 2 }, (files) =>
      files.write(`${W}/a`, text("3"), { expected: null, writer: "owner" }),
    "file_quota: the owner's file history rows are at their quota");
  });

  it("refuses inline bytes past max_inline_bytes, the history's included", async () => {
    const r = await rig();
    await inFiles(r.stub, { max_inline_bytes: 12 }, (files) =>
      files.write(`${W}/a`, text("12345678"), { expected: null, writer: "owner" }));
    await refusedWithNothingLeft(r, { max_inline_bytes: 12 }, (files) =>
      files.write(`${W}/a`, text("abcdefgh"), { expected: null, writer: "owner" }),
    "file_quota: the owner's small-file bytes are at their quota");
    // The same content again costs nothing: it is stored once.
    await inFiles(r.stub, { max_inline_bytes: 12 }, (files) =>
      files.write(`${W}/b`, text("12345678"), { expected: null, writer: "owner" }));
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
    storageFullMessage(storageFull(INLINE_BYTES + 1, INLINE_BYTES + 1, 0, INLINE_BYTES + 1,
                                   null, null)));
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_chunks")).toEqual([{ n: 1 }]);
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

  it("lets every change that only frees space through a lowered or full quota", async () => {
    const r = await rig();
    await inFiles(r.stub, {}, async (files) => {
      for (const name of ["a", "b", "c", "d"]) {
        await files.write(`${W}/${name}`, text(name), { expected: null, writer: "o" });
      }
    });
    // A delete adds a history row, but only frees: a full history never refuses it, and
    // gone paths' rows make room for what adds history, so the rename lands too.
    await inFiles(r.stub, { max_history: 4, max_entries: 1, max_inline_bytes: 1 },
                  async (files) => {
      files.remove(`${W}/a`, { expected: null, writer: "o" });
      files.rename(`${W}/b`, `${W}/c`, { expected: null, replace: true, writer: "o" });
      files.remove(`${W}/c`, { expected: null, writer: "o" });
    });
    expect(await r.stub.fileUsage(r.bind(null))).toMatchObject({ entries: 1, history: 4 });
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });
});

describe("a full history", () => {
  const full = "file_quota: the owner's file history rows are at their quota";

  it("is never filled by live files' churn inside the window", async () => {
    const r = await rig();
    // The reviewer's probe: ten entries of one version each, ten writes each inside the
    // window, in a history that holds exactly what the live paths may keep.
    await setPolicy(r, { max_entries: 10, keep_versions: 1, keep_recent: 1, max_history: 10 });
    for (let round = 0; round < 10; round++) {
      for (let i = 0; i < 10; i++) {
        await write(r, `${W}/f${i}`, `${round}`);
      }
    }
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(10);
    // Kept inside the window, up to keep_recent each, and never past it.
    await setPolicy(r, { max_entries: 10, keep_versions: 1, keep_recent: 3, max_history: 30 });
    await runDurableObjectAlarm(r.stub);
    for (let round = 0; round < 5; round++) {
      await write(r, `${W}/f0`, `again ${round}`);
    }
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_history WHERE path = ?",
                       `${W}/f0`)).toEqual([{ n: 3 }]);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("is never filled by gone paths' churn: a change at its quota prunes their rows first " +
     "(slice 4 L1)", async () => {
    const r = await rig();
    // The reviewer's probe: a policy that validates (5 entries x max(1, 2) <= 10), then
    // create-and-delete pairs of unique names, past what history holds.
    await setPolicy(r, { max_entries: 5, keep_versions: 1, keep_recent: 2, max_history: 10 });
    for (let i = 0; i < 8; i++) {
      await write(r, `${W}/n${i}`, `${i}`);  // version 2i + 1
      await r.stub.fileRemove(r.bind(null), { path: `${W}/n${i}`, expected_etag: null,
                                              recursive: false });  // version 2i + 2
    }
    // Every write landed; the six earliest-due rows went, n0 to n2's, and the feed's floor
    // rose past n2's delete, the newest pruned row that was its path's newest.
    expect(await sqlIn(r.stub, "SELECT version, released FROM file_history ORDER BY version"))
      .toEqual(Array.from({ length: 10 }, (_v, i) => ({ version: i + 7, released: 1 })));
    expect(await sqlIn(r.stub, "SELECT feed_floor, history FROM file_space"))
      .toEqual([{ feed_floor: 6, history: 10 }]);
    expect((await r.stub.fileChanges(r.bind(null), { after: 5, limit: 10 })).reset).toBe(true);
    const behind = await r.stub.fileChanges(r.bind(null), { after: 6, limit: 10 });
    expect([behind.reset, behind.changes.map((change) => change.version)])
      .toEqual([false, Array.from({ length: 10 }, (_v, i) => i + 7)]);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("takes only gone paths' rows early, never a live path's kept for its age", async () => {
    const r = await rig();
    await setPolicy(r, { max_entries: 5, keep_versions: 1, keep_recent: 2, max_history: 10 });
    for (const round of ["1", "2", "3"]) {
      await write(r, `${W}/live`, round);  // versions 1 to 3; 2 is kept for its age
    }
    for (let i = 0; i < 6; i++) {
      await write(r, `${W}/n${i}`, `${i}`);
      await r.stub.fileRemove(r.bind(null), { path: `${W}/n${i}`, expected_etag: null,
                                              recursive: false });
    }
    // The live path's version 2 is the earliest due, but it is not let go: gone rows went.
    expect(await sqlIn(r.stub, "SELECT version, released, prune_after IS NOT NULL AS due " +
                                 "FROM file_history WHERE path = ? ORDER BY version",
                       `${W}/live`))
      .toEqual([{ version: 2, released: 0, due: 1 }, { version: 3, released: 0, due: 0 }]);
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(10);
    // A path made again takes its rows back from the gone ones.
    await write(r, `${W}/n5`, "again");
    expect(await sqlIn(r.stub, "SELECT released FROM file_history WHERE path = ? " +
                                 "ORDER BY version", `${W}/n5`))
      .toEqual([{ released: 0 }, { released: 0 }]);
  });

  it("releases every kept row of a path that goes, those kept for their age too", async () => {
    const r = await rig();
    await setPolicy(r, { max_entries: 3, keep_versions: 1, keep_recent: 3, max_history: 9 });
    await write(r, `${W}/old`, "1");
    await write(r, `${W}/old`, "2");  // version 1 is now kept for its age, due to go
    expect(await sqlIn(r.stub, "SELECT released, prune_after IS NOT NULL AS due " +
                                 "FROM file_history ORDER BY version"))
      .toEqual([{ released: 0, due: 1 }, { released: 0, due: 0 }]);
    await r.stub.fileRemove(r.bind(null), { path: `${W}/old`, expected_etag: null,
                                            recursive: false });
    expect(await sqlIn(r.stub, "SELECT released, prune_after IS NOT NULL AS due " +
                                 "FROM file_history ORDER BY version"))
      .toEqual([{ released: 1, due: 1 }, { released: 1, due: 1 }, { released: 1, due: 1 }]);
  });

  it("never lowers the feed's floor when it prunes a path older than one purged", async () => {
    const r = await rig();
    await setPolicy(r, { max_entries: 5, keep_versions: 1, keep_recent: 2, max_history: 10 });
    const pair = async (name: string) => {
      await write(r, `${W}/${name}`, name);
      await r.stub.fileRemove(r.bind(null), { path: `${W}/${name}`, expected_etag: null,
                                              recursive: false });
    };
    for (let i = 0; i < 5; i++) {
      await pair(`n${i}`);  // versions 2i + 1 and 2i + 2
    }
    expect(await r.stub.fileHistoryPurge(r.bind(null), { path: `${W}/n4` }))
      .toEqual({ purged: 2 });  // the floor rises to n4's delete, version 10
    await pair("n5");
    await pair("n6");  // takes n0's write, then n0's delete, version 2: its path's newest
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
    await inFiles(r.stub, {}, (files) => files.collect(Date.now() / 1000));
    expect(await sqlIn(r.stub, "SELECT feed_floor FROM file_space"))
      .toEqual([{ feed_floor: 4 }]);
    expect(await sqlIn(r.stub, "SELECT version FROM file_history ORDER BY version"))
      .toEqual([{ version: 1 }, { version: 3 }]);
  });

  it("prunes early no more than the change took past where it found history", async () => {
    const r = await rig();
    await inFiles(r.stub, {}, async (files) => {
      for (let i = 0; i < 6; i++) {
        await files.write(`${W}/n${i}`, text(`${i}`), { expected: null, writer: "o" });
        files.remove(`${W}/n${i}`, { expected: null, writer: "o" });
      }
    });
    // A quota lowered under what is kept: a change brings history back to where it was,
    // not down to the quota, so its work stays its own size.
    await inFiles(r.stub, { max_history: 4 }, (files) =>
      files.write(`${W}/live`, text("1"), { expected: null, writer: "o" }));
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(12);
    // A sync op at the quota makes its room the same way.
    const synced = await inFiles(r.stub, { max_history: 4 }, (files) => {
      const lease = files.leaseAcquire("owner", "sandbox", { egress: "a".repeat(64), sync: "b".repeat(64), runner: "c".repeat(64) });
      return files.syncCommit({ lease_id: lease.lease_id, epoch: lease.epoch, ops: [
        { op: "write", path: `${W}/synced`, base: null, inline: text("s") },
      ] });
    });
    expect(synced.outcomes.map((outcome) => outcome.outcome)).toEqual(["committed"]);
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(12);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("is re-marked by a retention change, on the one schedule", async () => {
    const r = await rig();
    await setPolicy(r, { max_entries: 2, keep_versions: 1, keep_recent: 1, max_history: 2 });
    await write(r, `${W}/b`, "1");
    await write(r, `${W}/c`, "1");
    await r.stub.fileRemove(r.bind(null), { path: `${W}/c`, expected_etag: null,
                                            recursive: false });
    // A retention change re-marks the rows already kept, on the one schedule.
    await setPolicy(r, { max_entries: 2, keep_versions: 1, keep_recent: 1, max_history: 2,
                         retention_days: 0 });
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
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
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
    await inFiles(r.stub, { keep_versions: 1 }, async (files) => {
      for (let i = 0; i < 600; i++) {
        await files.write(`${W}/p${i}`, text("1"), { expected: null, writer: "o" });
        await files.write(`${W}/p${i}`, text("2"), { expected: null, writer: "o" });
      }
    });
    // The rows were marked under keep 1; the policy in force keeps 3.
    await sqlIn(r.stub, "UPDATE file_space SET retained_keep = 1");
    await inFiles(r.stub, { keep_versions: 3 }, async (files) => {
      expect(files.nextAlarm(1000)).toBe(1000);
      await files.collect(Date.now() / 1000);
      expect(files.nextAlarm(1000)).toBe(1000);  // 500 paths a pass: one more to go
      await files.collect(Date.now() / 1000);
      expect(files.nextAlarm(Date.now() / 1000)).toBe(null);
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
      expect(files.nextAlarm(1000)).toBe(1000);
      await files.collect(1000);
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
      expect(files.nextAlarm(1000)).toBe(1000);
      await files.collect(1000);
    });
    expect(await sqlIn(r.stub, "SELECT op FROM file_history WHERE path = ?", `${W}/a`))
      .toEqual([{ op: "write" }, { op: "write" }]);
    expect(await sqlIn(r.stub, "SELECT retained_recent FROM file_space"))
      .toEqual([{ retained_recent: 2 }]);
  });

  it("starts a pass over when the policy changes during it", async () => {
    const r = await rig();
    await inFiles(r.stub, { keep_versions: 1 }, async (files) => {
      for (let i = 0; i < 600; i++) {
        await files.write(`${W}/p${String(i).padStart(3, "0")}`, text("1"),
                          { expected: null, writer: "o" });
        await files.write(`${W}/p${String(i).padStart(3, "0")}`, text("2"),
                          { expected: null, writer: "o" });
      }
    });
    await sqlIn(r.stub, "UPDATE file_space SET retained_keep = 1");
    // A first pass under keep 2 stops after 500 paths; keep 1 then comes back in force.
    await inFiles(r.stub, { keep_versions: 2 }, (files) => files.collect(1000));
    expect(await sqlIn(r.stub, "SELECT retain_cursor FROM file_space"))
      .toEqual([{ retain_cursor: `${W}/p499` }]);
    await inFiles(r.stub, { keep_versions: 1 }, async (files) => {
      await files.collect(1000);
      await files.collect(1000);
      expect(files.nextAlarm(1000)).not.toBe(1000);
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
    await inFiles(r.stub, { max_entries: 0, max_history: 0, max_inline_bytes: 0, max_bytes: 0 },
                  (files) => files.collect(Date.now() / 1000));
    const after = await r.stub.fileUsage(r.bind(null));
    expect([after.entries, after.history, after.intents]).toEqual([0, 0, 0]);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });
});

describe("the files policy", () => {
  it("is the defaults until the owner's policy sets its own, in session", async () => {
    const r = await rig();
    const tier = TIERS.free.storage_bytes;
    expect((await r.stub.fileUsage(r.bind(null))).limits)
      .toEqual({ ...FILE_DEFAULTS, max_bytes: tier });
    expect(JSON.parse(DEFAULT_POLICY_JSON)).not.toHaveProperty("files");
    await setPolicy(r, { max_entries: 1 });
    expect((await r.stub.fileUsage(r.bind(null))).limits)
      .toEqual({ ...FILE_DEFAULTS, max_bytes: tier, max_entries: 1 });
    await write(r, `${W}/a`, "a");
    expect((await failure(() => write(r, `${W}/b`, "b"))).message)
      .toBe("file_quota: the owner's file entries are at their quota");
    expect(canonical((await r.stub.policyRead(r.bind(null))).policy.files))
      .toBe('{"max_entries":1}');
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
    expect(storageFullOf((await failure(() => put(`${W}/b`, 2, small))).message))
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

  it("refuses a history that cannot hold every live path's kept versions", async () => {
    const r = await rig();
    const reason = "files policy: max_history holds max_entries times the larger of " +
      "keep_versions and keep_recent";
    expect((await failure(() => setPolicy(r, { max_entries: 500, keep_versions: 1000,
                                               max_history: 499_999 }))).message).toBe(reason);
    expect((await failure(() => setPolicy(r, { max_entries: 500, keep_versions: 1,
                                               keep_recent: 1000, max_history: 499_999 })))
      .message).toBe(reason);
    expect((await failure(() => setPolicy(r, { keep_recent: 21 }))).message).toBe(reason);
    await setPolicy(r, { max_entries: 500, keep_versions: 1000, max_history: 500_000 });
    await setPolicy(r, { max_entries: 500, keep_versions: 1, keep_recent: 1000,
                         max_history: 500_000 });
    expect(FILE_DEFAULTS.max_entries * Math.max(FILE_DEFAULTS.keep_versions,
                                                FILE_DEFAULTS.keep_recent))
      .toBe(FILE_DEFAULTS.max_history);
    expect(FILE_SETTINGS.keep_versions[1]).toBe(KEEP_CEILING);
    expect(FILE_SETTINGS.max_file_bytes[1]).toBe(MAX_INTENT_CHUNKS * CHUNK_BYTES);
  });

  it.each([
    ["an unknown setting", { sync_exclude: 1 }],
    ["a setting below its range", { keep_versions: 0 }],
    ["a setting above its range", { max_inline_bytes: FILE_SETTINGS.max_inline_bytes[1] + 1 }],
    ["a fraction", { retention_days: 1.5 }],
    ["text", { max_bytes: "1" }],
    ["not a table", [1]],
  ])("refuses %s", async (_what, files) => {
    const r = await rig();
    expect((await failure(() => setPolicy(r, files))).message).toBe("invalid files policy");
  });
});
