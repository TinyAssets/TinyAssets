/**
 * The one rule for a new name (`contract/src/paths.ts` `unportable`), held where the object
 * makes an entry: every writer refused alike, saying why (a file write, an append, a folder
 * and the parents a write makes, a rename's target, an upload's commit, a sandbox's sync op,
 * which refuses that op alone), nothing made; and a name the owner held before the rule
 * read, written, renamed and removed as it is.
 */
import { describe, expect, it } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { sha256Hex } from "../../contract/src/digest";
import { unportable } from "../../contract/src/paths";
import { type Lease } from "../src/files";
import { rig, type Rig } from "./ledger-support";
import { failure, inFiles, text } from "./support";

const W = "/workspace";

/** Each kind of name the rule refuses, and one beside each that it lets be. */
const REFUSED = ["back\\slash.md", "a:b.md", "what?.md", "pipe|.md", "quote\".md",
                 "star*.md", "less<.md", "more>.md", "tab\there.md", "line\nbreak.md",
                 "del\u007f.md", "trailing.", "trailing ", "CON", "nul.txt", "Com1.tar.gz",
                 "LPT9", "aux .md", "COM¹"];
const KEPT = ["CONSOLE.md", "con-notes.md", "a.b.md", ".hidden", "café.md", "x. y.md",
              "COM10", "LPT", "naïve", "emoji-🙂.md"];

const write = (r: Rig, path: string) => r.stub.fileWrite(
  r.bind(null), { path, expected_etag: null, content: text("x") });
const stat = async (r: Rig, path: string) => (await r.stub.fileStat(r.bind(null), { path })).entry;

describe("a new name", () => {
  it("is one every place can hold, or refused saying why", () => {
    for (const name of REFUSED) {
      expect([name, unportable(name)])
        .toEqual([name, expect.stringContaining(JSON.stringify(name))]);
    }
    for (const name of KEPT) {
      expect([name, unportable(name)]).toEqual([name, null]);
    }
  });

  it("is refused alike to every writer, nothing made", async () => {
    const r = await rig();
    await write(r, `${W}/fine.md`);
    for (const name of ["back\\slash.md", "a:b.md", "CON.md", "tab\there.md"]) {
      const path = `${W}/${name}`;
      const why = unportable(name);
      const refusals = [
        await failure(() => write(r, path)),
        await failure(() => r.stub.fileAppend(r.bind(null), { path, content: "x" })),
        await failure(() => r.stub.fileMkdir(r.bind(null), { path, expected_etag: null })),
        // A parent a write would make is a name made too.
        await failure(() => write(r, `${path}/inner.md`)),
        await failure(() => r.stub.fileRename(r.bind(null), {
          from: `${W}/fine.md`, to: path, expected_etag: null, replace: false })),
      ];
      expect(refusals.map((refused) => [refused.code, refused.message]))
        .toEqual(refusals.map(() => ["invalid", why]));
      expect(await stat(r, path)).toBeNull();
    }
    expect(await stat(r, `${W}/fine.md`)).not.toBeNull();
  });

  it("is refused at an upload's commit, and as one sync op of a commit whose others land",
     async () => {
    const r = await rig();
    const bytes = new Uint8Array(8192).fill(7);
    const begun = await r.stub.fileUploadBegin(r.bind(null), {
      size: bytes.byteLength, chunks: [sha256Hex(bytes)] });
    await r.stub.fileUploadChunks(r.bind(null), { intent_id: begun.intent_id,
                                                  chunks: [{ sha256: sha256Hex(bytes), bytes }] });
    const committed = await failure(() => r.stub.fileUploadCommit(r.bind(null), {
      intent_id: begun.intent_id, path: `${W}/up:load.bin`, expected_etag: null }));
    expect([committed.code, committed.message]).toEqual(["invalid", unportable("up:load.bin")]);
    const lease: Lease = await inFiles(r.stub, (files, unit) => unit(() => files.leaseAcquire(
      "owner", "sandbox-1", { egress: "a".repeat(64), sync: "b".repeat(64),
                              runner: "c".repeat(64) }, 1)));
    const { outcomes } = await r.stub.fileSyncCommit(r.bind(null), {
      lease_id: lease.lease_id, epoch: lease.epoch, ops: [
        { op: "write", path: `${W}/2026-11-28T10:00.log`, base: null, inline: text("a") },
        { op: "write", path: `${W}/ok.log`, base: null, inline: text("b") }] });
    expect(outcomes.map((each) => [each.path, each.outcome,
                                   "reason" in each ? each.reason : null]))
      .toEqual([[`${W}/2026-11-28T10:00.log`, "refused", unportable("2026-11-28T10:00.log")],
                [`${W}/ok.log`, "committed", null]]);
    expect(await stat(r, `${W}/2026-11-28T10:00.log`)).toBeNull();
  });
});

describe("a name the owner held before the rule", () => {
  it("is read, written, renamed and removed as it is", async () => {
    const r = await rig();
    await write(r, `${W}/legacy.md`);
    const held = `${W}/leg:acy.md`;
    await sqlIn(r.stub, "UPDATE file_entries SET name = ? WHERE parent = ? AND name = ?",
                "leg:acy.md", W, "legacy.md");
    // Its entry alone is renamed: history rows never change, so they stay the old path's.
    const rewritten = await r.stub.fileWrite(r.bind(null), {
      path: held, expected_etag: (await stat(r, held))!.etag, content: text("again") });
    expect(new TextDecoder().decode((await r.stub.fileRead(r.bind(null), { path: held })).inline))
      .toBe("again");
    const moved = await r.stub.fileRename(r.bind(null), {
      from: held, to: `${W}/legacy.md`, expected_etag: "entry" in rewritten
        ? rewritten.entry.etag : null, replace: false });
    expect("entry" in moved && moved.entry.path).toBe(`${W}/legacy.md`);
    // Once it is gone, the name is new again: never made back.
    expect((await failure(() => write(r, held))).message).toBe(unportable("leg:acy.md"));
    await sqlIn(r.stub, "UPDATE file_entries SET name = ? WHERE parent = ? AND name = ?",
                "leg:acy.md", W, "legacy.md");
    expect(await r.stub.fileRemove(r.bind(null), { path: held, expected_etag: null,
                                                   recursive: false }))
      .toEqual({ removed: 1 });
  });
});
