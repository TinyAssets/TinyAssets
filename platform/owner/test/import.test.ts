/**
 * The cutover's import doors (`src/import.ts`, slice 3), on the real object: taken only from
 * the platform's reserved caller with an import binding and only while the owner is
 * `pending`, every other caller and state refused (terminal); each step through the
 * object's own write path, idempotent from the owner's state (a run again skips, another file
 * or row at a key is refused loudly, the policy set only over the default); the bounds of a
 * call and of the open uploads; the tier numbers the binding carries; and a chunked file's
 * bytes put to `FILES` by its caller, the object taking none. Deleted at the cutover.
 */
import { env, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { chunkedId, cut, inlineId } from "../../contract/src/content";
import {
  bodyId, CHECK_CHUNKS, CHECK_FILES, IMPORT_BATCH_BYTES, IMPORT_FILES, IMPORT_INTENTS,
  IMPORT_ROWS, NO_ROWS, rehearsalName, rowLeaf, rowsSum,
} from "../../contract/src/import-message";
import { CHUNK_BYTES, PLACE_BYTES } from "../../contract/src/limits";
import type { Binding } from "../../contract/src/binding";
import {
  DISCARDED, IMPORT_DOORS_ONLY, IMPORT_PENDING_ONLY, IMPORT_PLATFORM_ONLY, PLATFORM_CALLER,
} from "../src/admission";
import { MAX_OPEN_INTENTS } from "../src/files";
import { IMPORT_NOTICE, type ImportCheckAnswer } from "../src/import";
import type { OwnerDO } from "../src/owner-do";
import { DEFAULT_POLICY_DIGEST, policyDigest } from "../../contract/src/policy-document";
import { failure, TIERS } from "./support";

type Stub = DurableObjectStub<OwnerDO>;
const BUNDLE = "b".repeat(64);
const I = "/workspace/imported";
const bytesOf = (text: string) => new TextEncoder().encode(text);

/** A fresh object as production makes one: pending (`admitted.ts` leaves `get` alone). */
function pending(owner = `owner-${crypto.randomUUID()}`): { stub: Stub; owner: string } {
  return { stub: env.OWNER.get(env.OWNER.idFromName(owner)) as unknown as Stub, owner };
}

/** The mtime the import's files keep: the old stack's, never the write's own time. */
const MTIME = Date.UTC(2024, 0, 2, 3, 4, 5);

/** The doors' import binding (`bindImport`): the platform, the bundle, the control's tiers. */
const importing = (owner: string, tiers: unknown = TIERS): Binding =>
  ({ owner, caller: PLATFORM_CALLER, origin: null, import: { bundle: BUNDLE }, tiers });
const own = (owner: string): Binding => ({ owner, caller: owner, origin: null, tiers: TIERS });

const POLICY = { entries: [], egress: { mode: "ask", destinations: [] },
                 spend: { daily: null, monthly: null, transaction: null, ask_above: null,
                          source_ceilings: {} } };

function inline(path: string, text: string, mode: 0 | 1 = 0) {
  const bytes = bytesOf(text);
  return { kind: "file" as const, path, size: bytes.byteLength, mode,
           content_id: inlineId(bytes), mtime_ms: MTIME, bytes };
}

/** A chunked file's request, its bytes cut as the files plane cuts them. */
function chunked(path: string, bytes: Uint8Array, mode: 0 | 1 = 0) {
  const pieces = cut(bytes);
  return { request: { path, size: bytes.byteLength, mode,
                      content_id: chunkedId(bytes.byteLength, pieces.map((p) => p.sha256)),
                      chunks: pieces.map((p) => p.sha256) }, pieces };
}

/** A chunked file through the import's upload: begun, its chunks put to `FILES`, committed. */
async function upload(stub: Stub, owner: string, path: string, bytes: Uint8Array) {
  const { request, pieces } = chunked(path, bytes);
  const begun = await stub.importFileBegin(importing(owner), request);
  if (begun.skipped) {
    return { skipped: true };
  }
  for (const piece of pieces.filter((p) => begun.pending.includes(p.sha256))) {
    await env.FILES.putChunks(begun.namespace, begun.key, [{ sha256: piece.sha256,
                                                             bytes: piece.bytes }]);
    await stub.importFileStored(importing(owner), { intent_id: begun.intent_id,
                                                    chunks: [piece.sha256] });
  }
  return stub.importFileCommit(importing(owner), { intent_id: begun.intent_id, path,
                                                   mode: request.mode,
                                                   content_id: request.content_id,
                                                   mtime_ms: MTIME });
}

const big = () => new Uint8Array(CHUNK_BYTES + 99).map((_b, i) => (i * 13 + 1) % 251);

function intents(stub: Stub): Promise<number> {
  return runInDurableObject(stub, (_instance: unknown, state: DurableObjectState) =>
    state.storage.sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM file_intents").one().n);
}

describe("the import doors' caller and state", () => {
  it("takes the platform's import binding while the owner is pending", async () => {
    const { stub, owner } = pending();
    expect(await stub.importNotice(importing(owner), { detail: "imported" }))
      .toEqual({ added: true });
  });

  it("refuses every other caller, binding and state with a typed terminal refusal", async () => {
    const { stub, owner } = pending();
    const refused = async (call: () => Promise<unknown>, message: string) => {
      const error = await failure(call);
      expect([error.code, error.message]).toEqual(["refused", message]);
    };
    const request = { detail: "x" };
    // The owner itself, an outside client, the platform with no import binding, a binding
    // with a session or another field, and one naming no bundle.
    await refused(() => stub.importNotice({ owner, caller: owner, origin: null,
                                            import: { bundle: BUNDLE }, tiers: TIERS }, request),
                  IMPORT_PLATFORM_ONLY);
    await refused(() => stub.importNotice({ owner, caller: owner, origin: null }, request),
                  IMPORT_PLATFORM_ONLY);
    await refused(() => stub.importNotice({ owner, caller: PLATFORM_CALLER, origin: null },
                                          request), IMPORT_PLATFORM_ONLY);
    await refused(() => stub.importNotice({ ...importing(owner), session: "s" }, request),
                  IMPORT_PLATFORM_ONLY);
    await refused(() => stub.importNotice({ ...importing(owner),
                                            origin: { client: "c", family: "f" } }, request),
                  IMPORT_PLATFORM_ONLY);
    await refused(() => stub.importNotice({ ...importing(owner), import: { bundle: "x" } },
                                          request), IMPORT_PLATFORM_ONLY);
    // The import binding at any other door, the platform's own included.
    await refused(() => stub.fileStat(importing(owner), { path: I }), IMPORT_DOORS_ONLY);
    await refused(() => stub.admissionSet(importing(owner), { admit: true }), IMPORT_DOORS_ONLY);
    await refused(() => stub.tierSet(importing(owner), { tier: "paid" }), IMPORT_DOORS_ONLY);
    // Admitted, then suspended: no longer pending, never imported into.
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    await refused(() => stub.importNotice(importing(owner), request), IMPORT_PENDING_ONLY);
    await refused(() => stub.importFiles(importing(owner), { entries: [{ kind: "dir", path: I }] }),
                  IMPORT_PENDING_ONLY);
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null },
                            { reason: "billing", suspended: true });
    await refused(() => stub.importRows(importing(owner), { rows: [{ kind: "policy",
                                                                     document: POLICY }] }),
                  IMPORT_PENDING_ONLY);
    expect((await stub.noticeList(own(owner))).notices
      .filter((notice) => notice.type === IMPORT_NOTICE)).toEqual([]);
  });
});

describe("the import's rows", () => {
  it("sets the policy over the default, skips it once set, and refuses another", async () => {
    const { stub, owner } = pending();
    const rows = [{ kind: "policy" as const, document: POLICY }];
    expect(await stub.importRows(importing(owner), { rows })).toEqual({ written: 1, skipped: 0 });
    expect(await stub.importRows(importing(owner), { rows })).toEqual({ written: 0, skipped: 1 });
    const other = { ...POLICY, egress: { mode: "open", destinations: [] } };
    expect(policyDigest(other)).toBe(DEFAULT_POLICY_DIGEST);
    const changed = { ...POLICY, egress: { mode: "allowlist", destinations: [] } };
    expect((await failure(() => stub.importRows(importing(owner), {
      rows: [{ kind: "policy", document: changed }] }))).code).toBe("conflict");
    // A document the validator refuses is refused, never stored.
    expect((await failure(() => stub.importRows(importing(owner), {
      rows: [{ kind: "policy", document: { egress: 1 } }] }))).code).toBe("invalid");
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    const read = await stub.policyRead(own(owner));
    expect([read.digest, read.generation]).toEqual([policyDigest(POLICY), 2]);
  });

  it("writes collection rows through the object's own path, a large body placed, and skips " +
     "a row equal by key and body, refusing another", async () => {
    const { stub, owner } = pending();
    const large = `{"t":"${"x".repeat(PLACE_BYTES + 10)}"}`;
    const rows = [
      { kind: "collection" as const, collection: "board", row_id: "b1", private: 1 as const,
        body: '{"t":"first"}' },
      { kind: "collection" as const, collection: "board", row_id: "b2", private: 0 as const,
        body: large },
    ];
    expect(await stub.importRows(importing(owner), { rows })).toEqual({ written: 2, skipped: 0 });
    expect(await stub.importRows(importing(owner), { rows })).toEqual({ written: 0, skipped: 2 });
    for (const row of [{ ...rows[0]!, body: '{"t":"other"}' }, { ...rows[1]!, private: 1 as const },
                       { ...rows[1]!, body: `${large} ` }]) {
      expect((await failure(() => stub.importRows(importing(owner), { rows: [row] }))).code)
        .toBe("conflict");
    }
    // The placed body's content in R2, its row naming it, its history the import's.
    const placed = await runInDurableObject(stub, (_i: unknown, state: DurableObjectState) =>
      state.storage.sql.exec<{ row_id: string; content_id: string | null; writer: string;
                               op: string }>(
        "SELECT r.row_id, r.content_id, r.writer, h.op FROM collection_rows r JOIN " +
        "collection_history h ON h.collection = r.collection AND h.key = r.row_id " +
        "ORDER BY r.row_id").toArray());
    expect(placed.map((row) => [row.row_id, row.content_id !== null, row.writer, row.op]))
      .toEqual([["b1", false, owner, "import"], ["b2", true, owner, "import"]]);
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    expect((await stub.collectionGet(own(owner), { collection: "board", row_id: "b2" }))
      .row?.body).toBe(large);
  });
});

describe("the import's files", () => {
  it("makes folders and inline files where none is, skips them again, and refuses another " +
     "file at a path", async () => {
    const { stub, owner } = pending();
    const entries = [{ kind: "dir" as const, path: I }, { kind: "dir" as const, path: `${I}/novel` },
                     inline(`${I}/novel/a.md`, "alpha"), inline(`${I}/novel/run.sh`, "#!/bin/sh", 1)];
    expect(await stub.importFiles(importing(owner), { entries }))
      .toEqual({ written: 4, skipped: 0 });
    expect(await stub.importFiles(importing(owner), { entries }))
      .toEqual({ written: 0, skipped: 4 });
    for (const entry of [inline(`${I}/novel/a.md`, "beta"), inline(`${I}/novel/run.sh`, "#!/bin/sh"),
                         { kind: "dir" as const, path: `${I}/novel/a.md` },
                         inline(`${I}/novel`, "a file where a folder is")]) {
      expect((await failure(() => stub.importFiles(importing(owner), { entries: [entry] }))).code)
        .toBe("conflict");
    }
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    const read = await stub.fileRead(own(owner), { path: `${I}/novel/run.sh` });
    expect([new TextDecoder().decode(read.inline), read.entry.mode, read.entry.writer])
      .toEqual(["#!/bin/sh", 1, "owner"]);
  });

  it("writes the imported tree alone, each inline file its content id", async () => {
    const { stub, owner } = pending();
    for (const path of ["/workspace/agents/main/AGENTS.md", "/workspace/notes.md", "/.ta/tmp/x"]) {
      expect((await failure(() => stub.importFiles(importing(owner),
                                                   { entries: [inline(path, "x")] }))).code)
        .toBe("refused");
    }
    const lying = { ...inline(`${I}/a.md`, "alpha"), content_id: inlineId(bytesOf("beta")) };
    expect((await failure(() => stub.importFiles(importing(owner), { entries: [lying] }))).code)
      .toBe("invalid");
  });

  it("uploads a chunked file by the upload door's steps, its bytes put by the caller, and " +
     "skips it once committed", async () => {
    const { stub, owner } = pending();
    const bytes = big();
    expect(await upload(stub, owner, `${I}/big.bin`, bytes)).toEqual({ written: 1, skipped: 0 });
    expect(await upload(stub, owner, `${I}/big.bin`, bytes)).toEqual({ skipped: true });
    expect(await intents(stub)).toBe(0);
    // Another file at its path is refused, at the begin.
    const other = bytes.slice();
    other[0] = other[0]! ^ 1;
    expect((await failure(() => stub.importFileBegin(importing(owner),
                                                     chunked(`${I}/big.bin`, other).request))).code)
      .toBe("conflict");
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    const read = await stub.fileRead(own(owner), { path: `${I}/big.bin` });
    expect(read.chunks!.map((chunk) => chunk.sha256)).toEqual(cut(bytes).map((p) => p.sha256));
  });

  it("resumes an open upload of the same file, and holds at most its open uploads", async () => {
    expect(IMPORT_INTENTS).toBeLessThan(MAX_OPEN_INTENTS);
    const { stub, owner } = pending();
    const first = chunked(`${I}/f0.bin`, big()).request;
    const a = await stub.importFileBegin(importing(owner), first);
    const b = await stub.importFileBegin(importing(owner), first);
    expect([a.skipped, b.skipped]).toEqual([false, false]);
    expect(!a.skipped && !b.skipped && a.intent_id === b.intent_id).toBe(true);
    for (let n = 1; n < IMPORT_INTENTS; n += 1) {
      const bytes = big();
      bytes[0] = 100 + n;
      expect((await stub.importFileBegin(importing(owner),
                                         chunked(`${I}/f${n}.bin`, bytes).request)).skipped)
        .toBe(false);
    }
    expect(await intents(stub)).toBe(IMPORT_INTENTS);
    const past = big();
    past[0] = 200;
    expect((await failure(() => stub.importFileBegin(importing(owner),
                                                     chunked(`${I}/past.bin`, past).request))).code)
      .toBe("refused");
    // A commit of a chunk never stored is the files plane's refusal.
    expect((await failure(() => stub.importFileCommit(importing(owner), {
      intent_id: a.skipped ? "" : a.intent_id, path: first.path, mode: 0,
      content_id: first.content_id, mtime_ms: MTIME }))).message).toMatch(/never stored/);
  });

  it("holds the files to the tier numbers the binding carries", async () => {
    const { stub, owner } = pending();
    const entries = [inline(`${I}/a.md`, "alpha")];
    expect((await failure(() => stub.importFiles(importing(owner, null), { entries }))).code)
      .toBe("tier_unread");
    const tiny = { free: { runs: 1, storage_bytes: 2 }, paid: { runs: 1, storage_bytes: 2 } };
    expect((await failure(() => stub.importFiles(importing(owner, tiny), { entries }))).code)
      .toBe("storage_full");
    expect(await stub.importFiles(importing(owner), { entries })).toEqual({ written: 1, skipped: 0 });
  });
});

describe("the import's bounds", () => {
  it("takes at most its rows and bytes a call", async () => {
    const { stub, owner } = pending();
    const row = (n: number, body = "{}") => ({ kind: "collection" as const, collection: "board",
                                                row_id: `r${n}`, private: 0 as const, body });
    const rows = Array.from({ length: IMPORT_ROWS + 1 }, (_r, n) => row(n));
    expect((await failure(() => stub.importRows(importing(owner), { rows }))).code).toBe("invalid");
    const wide = Array.from({ length: 2 }, (_r, n) => row(n, `"${"x".repeat(IMPORT_BATCH_BYTES / 2)}"`));
    expect((await failure(() => stub.importRows(importing(owner), { rows: wide }))).code)
      .toBe("invalid");
    expect((await failure(() => stub.importRows(importing(owner), { rows: [] }))).code)
      .toBe("invalid");
    expect(await stub.importRows(importing(owner), { rows: rows.slice(0, IMPORT_ROWS) }))
      .toEqual({ written: IMPORT_ROWS, skipped: 0 });
  });

  it("takes at most its entries and their bytes a call, each inline file at most 4 KiB",
     async () => {
    const { stub, owner } = pending();
    const many = Array.from({ length: IMPORT_FILES + 1 }, (_e, n) =>
      ({ kind: "dir" as const, path: `${I}/d${n}` }));
    expect((await failure(() => stub.importFiles(importing(owner), { entries: many }))).code)
      .toBe("invalid");
    const full = Array.from({ length: IMPORT_FILES }, (_e, n) => {
      const bytes = new Uint8Array(4096).fill(n % 251);
      bytes[0] = n >> 8;
      bytes[1] = n & 0xff;
      return { kind: "file" as const, path: `${I}/${n}`, size: 4096, mode: 0 as const,
               content_id: inlineId(bytes), mtime_ms: MTIME, bytes };
    });
    expect((await failure(() => stub.importFiles(importing(owner), { entries: full }))).code)
      .toBe("invalid");
    const fat = new Uint8Array(4097);
    expect((await failure(() => stub.importFiles(importing(owner), { entries: [{
      kind: "file", path: `${I}/fat`, size: 4097, mode: 0, content_id: inlineId(fat),
      mtime_ms: MTIME, bytes: fat }] }))).code).toBe("invalid");
    expect(await stub.importFiles(importing(owner), { entries: many.slice(0, IMPORT_FILES) }))
      .toEqual({ written: IMPORT_FILES, skipped: 0 });
  });

  it("adds the owner's notice once", async () => {
    const { stub, owner } = pending();
    expect(await stub.importNotice(importing(owner), { detail: "in /workspace/imported" }))
      .toEqual({ added: true });
    expect(await stub.importNotice(importing(owner), { detail: "in /workspace/imported" }))
      .toEqual({ added: false });
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    expect((await stub.noticeList(own(owner))).notices
      .filter((notice) => notice.type === IMPORT_NOTICE).map((notice) => notice.detail))
      .toEqual(["in /workspace/imported"]);
  });
});

describe("the import's mtime", () => {
  it("keeps each file's mtime from the old stack, inline and chunked", async () => {
    const { stub, owner } = pending();
    await stub.importFiles(importing(owner), { entries: [{ kind: "dir", path: I },
                                                         inline(`${I}/a.md`, "alpha")] });
    await upload(stub, owner, `${I}/big.bin`, big());
    expect((await failure(() => stub.importFiles(importing(owner), {
      entries: [{ ...inline(`${I}/b.md`, "beta"), mtime_ms: -1 }] }))).code).toBe("invalid");
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    for (const path of [`${I}/a.md`, `${I}/big.bin`]) {
      const stat = await stub.fileStat(own(owner), { path });
      expect([path, stat.entry?.mtime_ms]).toEqual([path, MTIME]);
    }
  });
});

/** Every page of a check's part, from page 0 until one says no more follows. */
async function pages(stub: Stub, owner: string, part: "files" | "chunks" | "rows"):
    Promise<ImportCheckAnswer[]> {
  const found: ImportCheckAnswer[] = [];
  for (let page = 0; ; page += 1) {
    const read = await stub.importCheck(importing(owner), { part, page });
    found.push(read);
    if (!read.more) {
      return found;
    }
  }
}

describe("the import's check", () => {
  it("lists the files, reads back every chunk, sums the rows and reads the rest", async () => {
    const { stub, owner } = pending();
    const entries = [{ kind: "dir" as const, path: I }, inline(`${I}/a.md`, "alpha", 1)];
    await stub.importFiles(importing(owner), { entries });
    const bytes = big();
    await upload(stub, owner, `${I}/big.bin`, bytes);
    const large = `{"t":"${"x".repeat(PLACE_BYTES + 10)}"}`;
    const rows = [
      { kind: "collection" as const, collection: "board", row_id: "b1", private: 1 as const,
        body: '{"t":"first"}' },
      { kind: "collection" as const, collection: "board", row_id: "b2", private: 0 as const,
        body: large }];
    await stub.importRows(importing(owner), { rows: [{ kind: "policy", document: POLICY },
                                                     ...rows] });
    const files = (await pages(stub, owner, "files")).flatMap((page) =>
      page.part === "files" ? page.entries : []);
    expect(files.map((entry) => [entry.path, entry.kind, entry.size, entry.mode,
                                 entry.content_id, entry.mtime_ms])).toEqual([
      [I, "dir", 0, 0, null, expect.any(Number)],
      [`${I}/a.md`, "file", 5, 1, inlineId(bytesOf("alpha")), MTIME],
      [`${I}/big.bin`, "file", bytes.byteLength, 0, chunked(`${I}/big.bin`, bytes).request.content_id,
       MTIME]]);
    // Every chunk, the file's and the placed body's, read back and proved its hash.
    const chunks = (await pages(stub, owner, "chunks")).flatMap((page) =>
      page.part === "chunks" ? page.chunks : []);
    const wanted = [...cut(bytes).map((piece) => piece.sha256),
                    ...cut(bytesOf(large)).map((piece) => piece.sha256)].sort();
    expect(chunks.map((chunk) => chunk.sha256)).toEqual(wanted);
    expect(chunks.every((chunk) => chunk.read === chunk.sha256)).toBe(true);
    const [summed] = await pages(stub, owner, "rows");
    expect(summed).toEqual({ part: "rows", count: 2, more: false,
                             sum: rows.reduce((sum, row) => rowsSum(sum, rowLeaf(
                               row.collection, row.row_id, row.private, bodyId(row.body))),
                                             NO_ROWS) });
    const state = await stub.importCheck(importing(owner), { part: "state", page: 0 });
    expect(state).toMatchObject({ part: "state", policy_digest: policyDigest(POLICY),
                                  tier: "free", state: "pending", reasons: [] });
    expect(state.part === "state" && state.used_bytes).toBeGreaterThan(bytes.byteLength);
  });

  it("pages its listing and its chunks", async () => {
    const { stub, owner } = pending();
    const many = Array.from({ length: CHECK_FILES + 3 }, (_e, n) =>
      ({ kind: "dir" as const, path: `${I}/d${String(n).padStart(4, "0")}` }));
    await stub.importFiles(importing(owner), { entries: many.slice(0, IMPORT_FILES) });
    await stub.importFiles(importing(owner), { entries: many.slice(IMPORT_FILES) });
    const listed = await pages(stub, owner, "files");
    expect(listed.length).toBe(2);
    expect(listed.flatMap((page) => page.part === "files" ? page.entries : []).length)
      .toBe(CHECK_FILES + 4);
    for (let n = 0; n <= CHECK_CHUNKS; n += 1) {
      const bytes = big();
      bytes[0] = n;
      bytes[bytes.length - 1] = n;
      await upload(stub, owner, `${I}/c${n}.bin`, bytes);
    }
    const read = await pages(stub, owner, "chunks");
    expect(read.map((page) => page.part === "chunks" ? page.chunks.length : -1))
      .toEqual([CHECK_CHUNKS, CHECK_CHUNKS, 2]);
  });

  it("reads a chunk the bucket lost, or holds other bytes for, as not read back", async () => {
    const { stub, owner } = pending();
    const bytes = big();
    await upload(stub, owner, `${I}/big.bin`, bytes);
    const [first, second] = cut(bytes);
    const space = await runInDurableObject(stub, (_i: unknown, state: DurableObjectState) =>
      state.storage.sql.exec<{ ns: string; data_key: string }>(
        "SELECT ns, data_key FROM file_space").one());
    await env.FILES.deleteChunks(space.ns, space.data_key, [first!.sha256]);
    const chunks = (await pages(stub, owner, "chunks")).flatMap((page) =>
      page.part === "chunks" ? page.chunks : []);
    expect(Object.fromEntries(chunks.map((chunk) => [chunk.sha256, chunk.read])))
      .toEqual({ [first!.sha256]: null, [second!.sha256]: second!.sha256 });
  });

  it("is the platform's import binding's alone, while the owner is pending", async () => {
    const { stub, owner } = pending();
    const request = { part: "files" as const, page: 0 };
    expect((await failure(() => stub.importCheck({ owner, caller: owner, origin: null },
                                                 request))).message).toBe(IMPORT_PLATFORM_ONLY);
    expect((await failure(() => stub.importCheck(importing(owner),
                                                 { part: "state", page: 1 }))).code)
      .toBe("invalid");
    await stub.admissionSet({ owner, caller: PLATFORM_CALLER, origin: null }, { admit: true });
    expect((await failure(() => stub.importCheck(importing(owner), request))).message)
      .toBe(IMPORT_PENDING_ONLY);
  });
});

/** The owner object's own tables and its files' R2 objects, after a discard. */
async function left(stub: Stub, ns: string): Promise<{ tables: string[]; objects: number }> {
  const tables = await runInDurableObject(stub, (_i: unknown, state: DurableObjectState) =>
    state.storage.sql.exec<{ name: string }>(
      "SELECT name FROM sqlite_master WHERE type = 'table'").toArray()
      .map((row) => row.name).filter((name) => !/^(_cf_|sqlite_)/.test(name)));
  const objects = (await env.BLOBS.list({ prefix: `o/${ns}/` })).objects.length;
  return { tables, objects };
}

describe("the import's discard", () => {
  async function imported(owner?: string) {
    const made = pending(owner);
    await made.stub.importFiles(importing(made.owner), { entries: [{ kind: "dir", path: I },
                                                                   inline(`${I}/a.md`, "a")] });
    await upload(made.stub, made.owner, `${I}/big.bin`, big());
    const ns = await runInDurableObject(made.stub, (_i: unknown, state: DurableObjectState) =>
      state.storage.sql.exec<{ ns: string }>("SELECT ns FROM file_space").one().ns);
    return { ...made, ns };
  }

  it("deletes a pending owner's R2 objects and database, with no tombstone, and is repeatable",
     async () => {
    const { stub, owner, ns } = await imported();
    expect((await left(stub, ns)).objects).toBeGreaterThan(0);
    expect(await stub.importDiscard(importing(owner))).toEqual({ discarded: true, namespace: ns });
    // Nothing of the owner: no table (so no erased admission row), no object under its
    // namespace; the instance answers busy until the runtime starts the name afresh.
    expect(await left(stub, ns)).toEqual({ tables: [], objects: 0 });
    expect(await stub.importDiscard(importing(owner))).toEqual({ discarded: true, namespace: ns });
    const busy = await failure(() => stub.importFiles(importing(owner), { entries: [{
      kind: "dir", path: I }] }));
    expect([busy.code, busy.message]).toEqual(["busy", DISCARDED]);
    expect((await failure(() => stub.fileStat(own(owner), { path: I }))).code).toBe("busy");
  });

  it("takes a rehearsal's name in any state, and no admitted real owner", async () => {
    const real = await imported();
    await real.stub.admissionSet({ owner: real.owner, caller: PLATFORM_CALLER, origin: null },
                                 { admit: true });
    expect((await failure(() => real.stub.importDiscard(importing(real.owner)))).message)
      .toBe(IMPORT_PENDING_ONLY);
    expect((await left(real.stub, real.ns)).objects).toBeGreaterThan(0);
    const rehearsal = await imported(rehearsalName("r1", `user_${crypto.randomUUID()}`));
    const platform = { owner: rehearsal.owner, caller: PLATFORM_CALLER, origin: null };
    await rehearsal.stub.admissionSet(platform, { admit: true });
    await rehearsal.stub.admissionSet(platform, { reason: "billing", suspended: true });
    expect(await rehearsal.stub.importDiscard(importing(rehearsal.owner)))
      .toEqual({ discarded: true, namespace: rehearsal.ns });
    expect(await left(rehearsal.stub, rehearsal.ns)).toEqual({ tables: [], objects: 0 });
  });

  it("is the platform's import binding's alone", async () => {
    const { stub, owner, ns } = await imported(rehearsalName("r2", "user_x"));
    for (const binding of [{ owner, caller: owner, origin: null },
                           { owner, caller: PLATFORM_CALLER, origin: null },
                           { ...importing(owner), session: "s" }]) {
      expect((await failure(() => stub.importDiscard(binding as Binding))).code).toBe("refused");
    }
    expect((await left(stub, ns)).objects).toBeGreaterThan(0);
  });
});
