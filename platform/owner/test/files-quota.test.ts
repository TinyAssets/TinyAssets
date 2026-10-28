/**
 * File quotas (plan §6): owner-editable policy defaults, each refused loudly at its
 * bound with nothing left behind, never making room by dropping history.
 */
import { describe, expect, it } from "vitest";

import { type FileLimits, INLINE_BYTES } from "../src/files";
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
      files.begin([{ sha256: "a".repeat(64), size: INLINE_BYTES + 2 }]), reason);
  });

  it("lets every change that frees space through a lowered quota", async () => {
    const r = await rig();
    await inFiles(r.stub, {}, async (files) => {
      for (const name of ["a", "b", "c"]) {
        await files.write(`${W}/${name}`, text(name), { expected: null, writer: "o" });
      }
    });
    // A delete adds a history row, so a full history refuses it too: the owner raises
    // the quota or lets retention prune, and nothing is dropped to make room.
    await expect(inFiles(r.stub, { max_history: 3 }, (files) =>
      files.remove(`${W}/a`, { expected: null, writer: "o" }))).rejects
      .toThrow("the owner's file history is at its quota");
    await inFiles(r.stub, { max_entries: 1, max_inline_bytes: 1, max_bytes: 0 },
                  async (files) => {
      files.remove(`${W}/a`, { expected: null, writer: "o" });
      files.rename(`${W}/b`, `${W}/c`, { expected: null, replace: true, writer: "o" });
    });
    expect((await r.stub.fileUsage(r.bind(null))).entries).toBe(1);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });
});

describe("the files policy", () => {
  async function setPolicy(r: Rig, files: unknown) {
    const { generation, policy } = await r.stub.policyRead(r.bind(null));
    return r.stub.policyWrite(r.session(null), { expected_generation: generation,
                                                 policy: { ...policy, files } as never });
  }

  it("is the defaults until the owner's policy sets its own, in session", async () => {
    const r = await rig();
    expect((await r.stub.fileUsage(r.bind(null))).limits).toEqual(FILE_DEFAULTS);
    expect(JSON.parse(DEFAULT_POLICY_JSON)).not.toHaveProperty("files");
    await setPolicy(r, { max_entries: 1 });
    expect((await r.stub.fileUsage(r.bind(null))).limits)
      .toEqual({ ...FILE_DEFAULTS, max_entries: 1 });
    await r.stub.fileWrite(r.bind(null), { path: `${W}/a`, expected_etag: null, content: text("a") });
    expect((await failure(() => r.stub.fileWrite(r.bind(null), {
      path: `${W}/b`, expected_etag: null, content: text("b") }))).message)
      .toBe("the owner's file entries are at their quota");
    expect(canonical((await r.stub.policyRead(r.bind(null))).policy.files)).toBe('{"max_entries":1}');
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
