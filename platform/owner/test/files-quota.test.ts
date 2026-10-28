/**
 * File quotas (plan §6): owner-editable policy defaults, each refused loudly at its
 * bound with nothing left behind, never making room by dropping history, and never a
 * dead end: what only frees is never refused, and housekeeping is never wedged.
 */
import { runDurableObjectAlarm } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import {
  CHUNK_BYTES, type FileLimits, INLINE_BYTES, KEEP_CEILING, MAX_INTENT_CHUNKS,
} from "../src/files";
import { canonical, DEFAULT_POLICY_JSON, FILE_DEFAULTS, FILE_SETTINGS } from "../src/policy";
import { rig, type Rig } from "./ledger-support";
import { failure, fileCounters, inFiles, sqlIn, text } from "./support";

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
    "the owner's file entries are at their quota");
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
    "the owner's file history is at its quota");
  });

  it("refuses inline bytes past max_inline_bytes, the history's included", async () => {
    const r = await rig();
    await inFiles(r.stub, { max_inline_bytes: 12 }, (files) =>
      files.write(`${W}/a`, text("12345678"), { expected: null, writer: "owner" }));
    await refusedWithNothingLeft(r, { max_inline_bytes: 12 }, (files) =>
      files.write(`${W}/a`, text("abcdefgh"), { expected: null, writer: "owner" }),
    "the owner's small-file bytes are at their quota");
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
    "the owner's file bytes are at their quota");
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_chunks")).toEqual([{ n: 1 }]);
  });

  it("refuses a file past max_file_bytes, through any door", async () => {
    const r = await rig();
    const reason = "the file is larger than the owner's file size quota";
    await refusedWithNothingLeft(r, { max_file_bytes: INLINE_BYTES + 1 }, (files) =>
      files.write(`${W}/a`, new Uint8Array(INLINE_BYTES + 2), { expected: null, writer: "o" }),
    reason);
    await refusedWithNothingLeft(r, { max_file_bytes: 3 }, (files) =>
      files.write(`${W}/a`, text("four"), { expected: null, writer: "o" }), reason);
    await refusedWithNothingLeft(r, { max_file_bytes: INLINE_BYTES + 1 }, (files) =>
      files.begin(INLINE_BYTES + 2, ["a".repeat(64)]), reason);
  });

  it("lets every change that only frees space through a lowered or full quota", async () => {
    const r = await rig();
    await inFiles(r.stub, {}, async (files) => {
      for (const name of ["a", "b", "c", "d"]) {
        await files.write(`${W}/${name}`, text(name), { expected: null, writer: "o" });
      }
    });
    // A delete adds a history row, but only frees: a full history never refuses it.
    await inFiles(r.stub, { max_history: 4, max_entries: 1, max_inline_bytes: 1 },
                  async (files) => {
      files.remove(`${W}/a`, { expected: null, writer: "o" });
      expect(() => files.rename(`${W}/b`, `${W}/c`, { expected: null, replace: true,
                                                     writer: "o" }))
        .toThrow("the owner's file history is at its quota");
      files.remove(`${W}/b`, { expected: null, writer: "o" });
    });
    expect((await r.stub.fileUsage(r.bind(null))).entries).toBe(2);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });
});

describe("a full history", () => {
  const full = "the owner's file history is at its quota";

  it("is never a dead end: a purge, a delete or a retention change makes room", async () => {
    const r = await rig();
    await setPolicy(r, { max_entries: 2, keep_versions: 2, max_history: 4 });
    for (const body of ["1", "2"]) {
      await write(r, `${W}/a`, body);
      await write(r, `${W}/b`, body);
    }
    expect((await failure(() => write(r, `${W}/a`, "3"))).message).toBe(full);
    // The owner's purge drops a path's old versions, never refused.
    expect(await r.stub.fileHistoryPurge(r.bind(null), { path: `${W}/a` }))
      .toEqual({ purged: 1 });
    await write(r, `${W}/a`, "3");
    expect((await failure(() => write(r, `${W}/b`, "3"))).message).toBe(full);
    // A retention change re-marks the rows already kept, on the one schedule.
    await setPolicy(r, { max_entries: 2, keep_versions: 1, max_history: 4, retention_days: 0 });
    // Due at once: the alarm may already have run by itself; run it if it has not.
    await runDurableObjectAlarm(r.stub);
    for (let i = 0; i < 50 && (await r.stub.fileUsage(r.bind(null))).history !== 2; i++) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    expect((await r.stub.fileUsage(r.bind(null))).history).toBe(2);
    await write(r, `${W}/b`, "3");
    expect(await sqlIn(r.stub, "SELECT retained_keep, retained_days, retain_cursor " +
                                 "FROM file_space"))
      .toEqual([{ retained_keep: 1, retained_days: 0, retain_cursor: null }]);
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
      files.begin(INLINE_BYTES + 1, ["a".repeat(64)]);
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
    expect((await r.stub.fileUsage(r.bind(null))).limits).toEqual(FILE_DEFAULTS);
    expect(JSON.parse(DEFAULT_POLICY_JSON)).not.toHaveProperty("files");
    await setPolicy(r, { max_entries: 1 });
    expect((await r.stub.fileUsage(r.bind(null))).limits)
      .toEqual({ ...FILE_DEFAULTS, max_entries: 1 });
    await write(r, `${W}/a`, "a");
    expect((await failure(() => write(r, `${W}/b`, "b"))).message)
      .toBe("the owner's file entries are at their quota");
    expect(canonical((await r.stub.policyRead(r.bind(null))).policy.files))
      .toBe('{"max_entries":1}');
  });

  it("refuses a history that cannot hold every live path's kept versions", async () => {
    const r = await rig();
    const reason = "files policy: max_history holds max_entries times keep_versions";
    expect((await failure(() => setPolicy(r, { max_entries: 1000, keep_versions: 1000,
                                               max_history: 999_999 }))).message).toBe(reason);
    expect((await failure(() => setPolicy(r, { keep_versions: 11 }))).message).toBe(reason);
    await setPolicy(r, { max_entries: 1000, keep_versions: 1000, max_history: 1_000_000 });
    expect(FILE_DEFAULTS.max_entries * FILE_DEFAULTS.keep_versions)
      .toBeLessThanOrEqual(FILE_DEFAULTS.max_history);
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
