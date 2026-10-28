/**
 * The file tree (plan §6): directories, a rename that moves a subtree in one
 * transaction, removes, listings in name order, and the change feed.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { type Entry, INLINE_BYTES } from "../src/files";
import { rig, type Rig } from "./ledger-support";
import { failure, fileCounters, inFiles, sqlIn, text } from "./support";

const W = "/workspace";

function entryOf(answer: { entry: Entry } | { conflict: Entry | null }): Entry {
  if (!("entry" in answer)) {
    throw new Error("expected a commit");
  }
  return answer.entry;
}

const write = (r: Rig, path: string, body: string | Uint8Array = "x") => r.stub.fileWrite(
  r.bind(null), { path, expected_etag: null,
                  content: typeof body === "string" ? text(body) : body });
const mkdir = (r: Rig, path: string, expected: string | null = null) =>
  r.stub.fileMkdir(r.bind(null), { path, expected_etag: expected });
const rename = (r: Rig, from: string, to: string, replace = false,
                expected: string | null = null) =>
  r.stub.fileRename(r.bind(null), { from, to, expected_etag: expected, replace });
const remove = (r: Rig, path: string, recursive = false, expected: string | null = null) =>
  r.stub.fileRemove(r.bind(null), { path, expected_etag: expected, recursive });
const stat = async (r: Rig, path: string) => (await r.stub.fileStat(r.bind(null), { path })).entry;

async function listAll(r: Rig, dir: string, limit = 2): Promise<string[]> {
  const names: string[] = [];
  let after: string | undefined;
  for (;;) {
    const page = await r.stub.fileList(r.bind(null), { dir, limit,
                                                       ...(after === undefined ? {} : { after }) });
    names.push(...page.entries.map((e) => e.path.slice(dir.length + 1)));
    if (page.next === null) {
      return names;
    }
    after = page.next;
  }
}

describe("a directory", () => {
  it("is made with its parents, and an existing one is no error", async () => {
    const r = await rig();
    const made = entryOf(await mkdir(r, `${W}/a/b/c`));
    expect(made).toMatchObject({ path: `${W}/a/b/c`, kind: "dir", size: 0 });
    for (const path of [`${W}/a`, `${W}/a/b`]) {
      expect((await stat(r, path))?.kind).toBe("dir");
    }
    expect(entryOf(await mkdir(r, `${W}/a/b/c`))).toEqual(made);
    expect(await mkdir(r, `${W}/a/b/c`, "")).toEqual({ conflict: made });
    expect(entryOf(await mkdir(r, W))).toMatchObject({ path: W, kind: "dir", version: 0 });
    await write(r, `${W}/f`);
    expect((await failure(() => mkdir(r, `${W}/f`))).message).toBe("exists: a file is in the way");
  });

  it("lists its entries in name order, paged, and refuses a file or a missing one", async () => {
    const r = await rig();
    for (const name of ["b", "a", "B", "é", "aa", "_"]) {
      await write(r, `${W}/d/${name}`);
    }
    expect(await listAll(r, `${W}/d`)).toEqual(["B", "_", "a", "aa", "b", "é"]);
    expect(await listAll(r, `${W}/d`, 100)).toEqual(["B", "_", "a", "aa", "b", "é"]);
    expect((await failure(() => listAll(r, `${W}/d/a`))).message)
      .toBe("not_directory: not a directory");
    expect((await failure(() => listAll(r, `${W}/none`))).message)
      .toBe("not_found: no such directory");
    for (const limit of [0, 1001, 1.5]) {
      expect((await failure(() => r.stub.fileList(r.bind(null), { dir: W, limit }))).message)
        .toBe("limit is a whole number from 1 to 1000");
    }
  });
});

describe("a rename", () => {
  it("moves a directory's subtree in one transaction, with no byte moved", async () => {
    const r = await rig();
    const big = new Uint8Array(INLINE_BYTES + 100).fill(9);
    await write(r, `${W}/src/a.txt`, "a");
    await write(r, `${W}/src/deep/b.bin`, big);
    await mkdir(r, `${W}/src/empty`);
    const [space] = await sqlIn(r.stub, "SELECT ns FROM file_space");
    const before = await sqlIn(r.stub, "SELECT sha256, refs FROM file_chunks");
    const moved = entryOf(await rename(r, `${W}/src`, `${W}/dst`));
    expect(moved).toMatchObject({ path: `${W}/dst`, kind: "dir" });
    expect(await stat(r, `${W}/src`)).toBeNull();
    expect(await listAll(r, `${W}/dst`)).toEqual(["a.txt", "deep", "empty"]);
    expect((await r.stub.fileRead(r.bind(null), { path: `${W}/dst/a.txt` })).inline)
      .toEqual(text("a"));
    expect(await sqlIn(r.stub, "SELECT sha256, refs FROM file_chunks")).toEqual(before);
    expect(await env.FILES.getChunks(space!["ns"] as string, [{ sha256: before[0]!["sha256"] as
                                                                 string }])).toEqual([big]);
    // History continues on both sides: the old paths end, the new ones begin.
    const from = await r.stub.fileHistory(r.bind(null), { path: `${W}/src/deep/b.bin`, limit: 5 });
    const to = await r.stub.fileHistory(r.bind(null), { path: `${W}/dst/deep/b.bin`, limit: 5 });
    expect(from.changes.map((c) => c.op)).toEqual(["rename_from", "write"]);
    expect(to.changes.map((c) => [c.op, c.content_id])).toEqual(
      [["rename_to", from.changes[1]!.content_id]]);
    const [kept, counted] = await fileCounters(r.stub);
    expect(kept).toEqual(counted);
  });

  it("is all or nothing: a refused rename leaves the tree as it was", async () => {
    const r = await rig();
    await write(r, `${W}/a/x`);
    await write(r, `${W}/b/y`);
    await write(r, `${W}/f`);
    const tree = await sqlIn(r.stub, "SELECT * FROM file_entries ORDER BY parent, name");
    const history = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_history");
    expect(await rename(r, `${W}/a`, `${W}/b`)).toEqual({ conflict: await stat(r, `${W}/b`) });
    for (const [from, to, replace, message] of [
      [`${W}/a`, `${W}/a/inner`, true, "a directory is never moved into itself"],
      [`${W}/a`, `${W}/b`, true, "not_empty: the destination directory is not empty"],
      [`${W}/f`, `${W}/b`, true, "is_directory: a file never replaces a directory"],
      [`${W}/a`, `${W}/f`, true, "not_directory: a directory never replaces a file"],
      [`${W}/a`, `${W}/none/a`, true, "not_found: no such destination directory"],
      [`${W}/a`, `${W}/f/a`, true, "not_directory: the destination's parent is not a directory"],
      [`${W}/none`, `${W}/z`, true, "not_found: no such file or directory"],
      [W, `${W}/z`, true, "a root is never renamed"],
      [`${W}/a`, "/.ta/tmp/stray", true, "the temporary area holds only temporary directories"],
    ] as const) {
      expect([from, to, (await failure(() => rename(r, from, to, replace))).message])
        .toEqual([from, to, message]);
    }
    expect(await sqlIn(r.stub, "SELECT * FROM file_entries ORDER BY parent, name")).toEqual(tree);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_history")).toEqual(history);
  });

  it("with replace puts a file over a file, whose history goes on", async () => {
    const r = await rig();
    await write(r, `${W}/new`, "new");
    const old = entryOf(await write(r, `${W}/old`, "old"));
    const moved = entryOf(await rename(r, `${W}/new`, `${W}/old`, true));
    expect(moved.version).toBeGreaterThan(old.version);
    expect((await r.stub.fileRead(r.bind(null), { path: `${W}/old` })).inline).toEqual(text("new"));
    expect((await r.stub.fileHistory(r.bind(null), { path: `${W}/old`, limit: 5 })).changes
      .map((c) => c.op)).toEqual(["rename_to", "write"]);
  });

  it("onto itself changes nothing, as the environment's does, but still swaps", async () => {
    const r = await rig();
    const made = entryOf(await write(r, `${W}/same`));
    expect(entryOf(await rename(r, `${W}/same`, `${W}/same`))).toEqual(made);
    expect(entryOf(await rename(r, `${W}/same`, `${W}/same`, false, made.etag))).toEqual(made);
    expect(await rename(r, `${W}/same`, `${W}/same`, false, "x".repeat(64)))
      .toEqual({ conflict: made });
    expect((await failure(() => rename(r, `${W}/none`, `${W}/none`))).message)
      .toBe("not_found: no such file or directory");
    expect((await r.stub.fileHistory(r.bind(null), { path: `${W}/same`, limit: 5 })).changes)
      .toHaveLength(1);
  });

  it("swaps on the source's etag", async () => {
    const r = await rig();
    const made = entryOf(await write(r, `${W}/a`));
    await write(r, `${W}/a`, "changed");
    expect(await rename(r, `${W}/a`, `${W}/b`, false, made.etag))
      .toEqual({ conflict: await stat(r, `${W}/a`) });
    expect(entryOf(await rename(r, `${W}/a`, `${W}/b`, false, (await stat(r, `${W}/a`))!.etag))
      .path).toBe(`${W}/b`);
  });
});

describe("a remove", () => {
  it("takes a file, or a directory and everything below it with recursive", async () => {
    const r = await rig();
    await write(r, `${W}/d/a`);
    await write(r, `${W}/d/e/b`);
    await write(r, `${W}/keep`);
    expect((await failure(() => remove(r, `${W}/d`))).message)
      .toBe("not_empty: a directory is removed with recursive");
    await mkdir(r, `${W}/empty`);
    expect((await failure(() => remove(r, `${W}/empty`))).message)
      .toBe("is_directory: a directory is removed with recursive");
    expect(await remove(r, `${W}/d`, true)).toEqual({ removed: 4 });
    expect(await listAll(r, W)).toEqual(["empty", "keep"]);
    expect((await failure(() => remove(r, `${W}/d`))).message)
      .toBe("not_found: no such file or directory");
    expect((await failure(() => remove(r, W, true))).message).toBe("a root is never removed");
  });

  it("swaps on the etag, and an etag for a missing path is a conflict", async () => {
    const r = await rig();
    const made = entryOf(await write(r, `${W}/a`));
    await write(r, `${W}/a`, "again");
    expect(await remove(r, `${W}/a`, false, made.etag)).toEqual({ conflict: await stat(r, `${W}/a`) });
    expect(await remove(r, `${W}/b`, false, made.etag)).toEqual({ conflict: null });
    expect((await failure(() => remove(r, `${W}/b`, false, ""))).message)
      .toBe("not_found: no such file or directory");
  });
});

describe("the change feed", () => {
  it("pages every workspace change in order, and its cursor never goes back", async () => {
    const r = await rig();
    await write(r, `${W}/a`);
    await mkdir(r, `${W}/d`);
    await rename(r, `${W}/a`, `${W}/d/a`);
    await remove(r, `${W}/d`, true);
    const ops: string[] = [];
    let cursor = 0;
    const cursors: number[] = [];
    for (;;) {
      const page = await r.stub.fileChanges(r.bind(null), { after: cursor, limit: 2 });
      ops.push(...page.changes.map((c) => `${c.op} ${c.path.slice(W.length)}`));
      expect(page.reset).toBe(false);
      if (page.cursor === cursor) {
        break;
      }
      cursor = page.cursor;
      cursors.push(cursor);
    }
    expect(ops).toEqual(["write /a", "mkdir /d", "rename_from /a", "rename_to /d/a",
                         "delete /d/a", "delete /d"]);
    expect(cursors).toEqual([...cursors].sort((a, b) => a - b));
    // Deletes never lower the cursor: a later write lands past every earlier change.
    const later = entryOf(await write(r, `${W}/a`));
    expect(later.version).toBeGreaterThan(cursor);
    expect((await r.stub.fileChanges(r.bind(null), { after: cursor, limit: 10 })).changes
      .map((c) => c.version)).toEqual([later.version]);
  });

  it("asks a reader to rescan once a delete it had not seen is pruned", async () => {
    const r = await rig();
    await write(r, `${W}/gone`);
    const { cursor } = await r.stub.fileChanges(r.bind(null), { after: 0, limit: 10 });
    await write(r, `${W}/kept`);
    // With no retention, the delete's record is due as soon as it is written.
    await inFiles(r.stub, { retention_days: 0 }, async (files) => {
      files.remove(`${W}/gone`, { expected: null, writer: "owner" });
      await files.collect(Date.now() / 1000 + 1);
    });
    const page = await r.stub.fileChanges(r.bind(null), { after: cursor, limit: 10 });
    expect(page.reset).toBe(true);
    expect(page.changes.map((c) => c.path)).toEqual([`${W}/kept`]);
    expect((await r.stub.fileChanges(r.bind(null), { after: page.cursor, limit: 10 })).reset)
      .toBe(false);
  });

  it("leaves the temporary area out of the feed", async () => {
    const r = await rig();
    await inFiles(r.stub, {}, (files) => files.tempDir("t-", "agent"));
    expect((await r.stub.fileChanges(r.bind(null), { after: 0, limit: 10 })).changes).toEqual([]);
  });
});
