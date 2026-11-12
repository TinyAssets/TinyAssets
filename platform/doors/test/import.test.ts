/**
 * The cutover import's consumer (`src/import.ts`, slice 4), in workerd through the real owner
 * object and the real files Worker: a bundle the slice-2 builder made from its old-stack
 * fixture (`import-setup.ts`) imported into a pending owner and read back through the
 * object's own read doors; each gate dropping its message loudly with nothing written; a
 * second run skipping everything; a killed delivery and a sliced one converging; a page's
 * inline files batched within the doors' bounds; and every chunk's bytes put to `FILES` by
 * the doors, never through the object. Deleted at the cutover with the importer.
 */
import { createHash, randomBytes } from "node:crypto";

import { env, runInDurableObject } from "cloudflare:test";
import { beforeAll, describe, expect, inject, it } from "vitest";

import { inlineId } from "../../contract/src/content";
import { canonical } from "../../contract/src/policy-document";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import {
  answerName, doneName, importMac, type ImportMessage, NO_ROWS, rehearsalName, rowsSum,
} from "../../contract/src/import-message";
import { type Answers, clean, differences, expectedOf } from "../../import/check";
import type { DoorsEnv } from "../src/env";
import { putOwner, readOwner } from "../src/directory";
import { IMPORT_HOLD_BYTES, importQueue, type SliceBounds } from "../src/import";
import { CHUNK_BYTES } from "../../contract/src/limits";
import type { OwnerDO } from "./entry";
import type { ImportBundle } from "./import-setup";
import { admitOwner, doorsEnv, OPEN, setControl, TIERS } from "./support";

const KEY = randomBytes(32).toString("base64url");
const BUNDLE: ImportBundle = inject("importBundle");

const sha256 = (bytes: Uint8Array | string) => createHash("sha256").update(bytes).digest("hex");
const text = (bytes: Uint8Array) => new TextDecoder().decode(bytes);
const json = (bytes: Uint8Array) => JSON.parse(text(bytes)) as Record<string, unknown>;
const fresh = () => `user_${randomBytes(12).toString("hex")}`;
const own = (owner: string): Binding => ({ owner, caller: owner, origin: null });
const stubOf = (owner: string) => env.OWNER.getByName(owner);

/** The fixture's bundle, its files' bytes by name. */
function bundleFiles(): Map<string, Uint8Array> {
  return new Map(Object.entries(BUNDLE.files).map(([name, b64]) =>
    [name, new Uint8Array(Buffer.from(b64, "base64"))]));
}

interface Entry {
  readonly path: string;
  readonly kind: "dir" | "file";
  readonly size?: number;
  readonly mode?: number;
  readonly mtime_ms?: number;
  readonly content_id?: string;
  readonly chunks?: string[];
}

/** A bundle's pages of `kind`, in order, flattened. */
function paged<T>(files: Map<string, Uint8Array>, kind: "manifest" | "rows", key: string): T[] {
  return [...files.keys()].filter((name) => name.startsWith(`${kind}/`))
    .sort((a, b) => parseInt(a.slice(kind.length + 1)) - parseInt(b.slice(kind.length + 1)))
    .flatMap((name) => json(files.get(name)!)[key] as T[]);
}

/** A bundle made `owner`'s: its index's and its owner's name set, so its digest is new. */
function made(owner: string, files = bundleFiles()): { files: Map<string, Uint8Array>; bundle: string } {
  const index = json(files.get("bundle.json")!);
  index["owner"] = owner;
  const record = json(files.get("owner.json")!);
  record["owner"] = owner;
  const recordBytes = new TextEncoder().encode(`${JSON.stringify(record)}\n`);
  files.set("owner.json", recordBytes);
  index["parts"] = { ...(index["parts"] as Record<string, string>),
                     "owner.json": sha256(recordBytes) };
  const bytes = new TextEncoder().encode(`${JSON.stringify(index)}\n`);
  files.set("bundle.json", bytes);
  return { files, bundle: sha256(bytes) };
}

/** A bundle staged in the bucket, and its message signed under `key`. */
async function staged(owner: string, files = made(owner), key = KEY): Promise<ImportMessage> {
  const prefix = `t${randomBytes(4).toString("hex")}/${sha256(owner).slice(0, 16)}`;
  for (const [name, bytes] of files.files) {
    await doorsEnv.IMPORT_STAGING.put(`${prefix}/${name}`, bytes);
  }
  return { op: "import", owner, bundle: files.bundle, prefix,
           mac: importMac(key, "import", owner, files.bundle) };
}

/** The doors' env with the window's key, a recording producer and a recording `FILES`. */
function world(over: Record<string, unknown> = {}) {
  const sent: ImportMessage[] = [];
  const puts: string[] = [];
  const files = {
    putChunks: async (ns: string, key: string, chunks: { sha256: string; bytes: Uint8Array }[]) => {
      puts.push(...chunks.map((chunk) => chunk.sha256));
      return doorsEnv.FILES.putChunks(ns, key, chunks);
    },
  };
  const environment = { ...doorsEnv, IMPORT_KEY: KEY, FILES: files,
                        IMPORTS: { send: async (body: ImportMessage) => { sent.push(body); },
                                   sendBatch: async () => undefined }, ...over } as unknown as DoorsEnv;
  return { env: environment, sent, puts };
}

function batch(body: unknown) {
  const acked: number[] = [];
  const retried: number[] = [];
  return { batch: { queue: "tinyassets-import", ackAll: () => undefined, retryAll: () => undefined,
                    messages: [{ id: "0", timestamp: new Date(), attempts: 1, body,
                                 ack: () => acked.push(0), retry: () => retried.push(0) }] } as
             unknown as MessageBatch<unknown>, acked, retried };
}

/** Each message and its continuations through the consumer: how each delivery ended. */
async function drain(w: ReturnType<typeof world>, first: unknown, bounds?: SliceBounds):
    Promise<{ deliveries: number; outcome: "acked" | "retried" }> {
  const queue = [first];
  let deliveries = 0;
  while (queue.length > 0) {
    const delivered = batch(queue.shift());
    deliveries += 1;
    await importQueue(delivered.batch, w.env, bounds);
    if (delivered.retried.length > 0) {
      return { deliveries, outcome: "retried" };
    }
    expect(delivered.acked).toEqual([0]);
    queue.push(...w.sent.splice(0));
  }
  return { deliveries, outcome: "acked" };
}

/**
 * What the pending object holds, by its own tables: how many entries, uploads, rows and
 * notices, the policy's generation, and a digest of every entry's and row's version, so a
 * run that changes nothing leaves it equal.
 */
function held(owner: string): Promise<Record<string, number | string>> {
  return runInDurableObject(stubOf(owner), (_instance: unknown, state: DurableObjectState) => {
    const sql = state.storage.sql;
    const count = (table: string) => sql.exec<{ n: number }>(
      `SELECT COUNT(*) AS n FROM ${table}`).one().n;
    const versions = JSON.stringify([
      ...sql.exec("SELECT parent, name, version FROM file_entries ORDER BY parent, name")
        .toArray(),
      ...sql.exec("SELECT collection, row_id, revision FROM collection_rows " +
                  "ORDER BY collection, row_id").toArray()]);
    return { entries: count("file_entries"), intents: count("file_intents"),
             rows: count("collection_rows"), notices: count("owner_notices"),
             generation: sql.exec<{ generation: number }>(
               "SELECT generation FROM owner_policy").one().generation,
             versions: sha256(versions) };
  });
}

/** Every imported entry, row, the policy and the notice read back through the object's doors. */
async function readBack(owner: string, files = bundleFiles()): Promise<void> {
  const stub = stubOf(owner);
  for (const entry of paged<Entry>(files, "manifest", "entries")) {
    const stat = (await stub.fileStat(own(owner), { path: entry.path })).entry;
    expect([entry.path, stat?.kind]).toEqual([entry.path, entry.kind]);
    if (entry.kind === "file") {
      const read = await stub.fileRead(own(owner), { path: entry.path });
      expect([entry.path, read.entry.size, read.entry.mode, read.entry.writer,
              read.entry.mtime_ms])
        .toEqual([entry.path, entry.size, entry.mode, "owner", entry.mtime_ms]);
      if (entry.chunks === undefined) {
        expect(inlineId(read.inline!)).toBe(entry.content_id);
      } else {
        expect(read.chunks!.map((chunk) => chunk.sha256)).toEqual(entry.chunks);
      }
    }
  }
  for (const row of paged<Record<string, unknown>>(files, "rows", "rows")) {
    if (row["kind"] === "policy") {
      const policy = await stub.policyRead(own(owner));
      expect(canonical(policy.policy)).toBe(canonical(row["document"]));
    } else {
      const got = await stub.collectionGet(own(owner), { collection: row["collection"] as string,
                                                         row_id: row["row_id"] as string });
      expect([got.row?.body, got.row?.private, got.row?.writer])
        .toEqual([row["body"], row["private"] === 1, owner]);
    }
  }
  const notices = (await stub.noticeList(own(owner))).notices;
  expect(notices.filter((notice) => notice.type === "imported").map((notice) => notice.detail))
    .toEqual([json(files.get("owner.json")!)["notice"]]);
}

beforeAll(async () => {
  await setControl(OPEN);
});

describe("the import consumer", () => {
  it("imports a slice-2 bundle into a pending owner, read back through the object's own doors, " +
     "every chunk put to FILES by the doors alone", async () => {
    const owner = fresh();
    const w = world();
    expect(await drain(w, await staged(owner))).toEqual({ deliveries: 1, outcome: "acked" });
    expect((await held(owner)).intents).toBe(0);
    const chunks = [...bundleFiles().keys()].filter((name) => name.startsWith("chunks/"))
      .map((name) => name.slice("chunks/".length));
    expect(chunks.length).toBeGreaterThan(0);
    // The doors put every chunk, once each, through their FILES: no byte went through the
    // object, whose own FILES never saw them.
    expect([...w.puts].sort()).toEqual([...chunks].sort());
    await admitOwner(owner);
    await readBack(owner);
  });

  it("skips everything on a run again, and changes nothing", async () => {
    const owner = fresh();
    const w = world();
    const message = await staged(owner);
    await drain(w, message);
    const before = await held(owner);
    w.puts.splice(0);
    expect(await drain(w, message)).toEqual({ deliveries: 1, outcome: "acked" });
    expect(w.puts).toEqual([]);
    // Every entry, row, the policy and the notice as they were: nothing written again.
    expect(await held(owner)).toEqual(before);
    // The old stack's policy is archived, not carried (d3 §N6), so the default's generation.
    expect(before).toMatchObject({ intents: 0, notices: 1, generation: 1 });
  });

  it("goes on from its own continuation, one slice at a time, to the same owner state", async () => {
    const owner = fresh();
    const w = world();
    // Every slice is over once it moved a byte or a step: each sends its continuation.
    const sliced = await drain(w, await staged(owner), { ms: 0, bytes: 0 });
    expect(sliced.outcome).toBe("acked");
    expect(sliced.deliveries).toBeGreaterThan(3);
    const whole = fresh();
    await drain(world(), await staged(whole));
    const [a, b] = [await held(owner), await held(whole)];
    expect(a).toEqual(b);
    await admitOwner(owner);
    await readBack(owner);
  });

  it("converges on a run again after a delivery killed mid-file", async () => {
    const owner = fresh();
    const first: string[] = [];
    const killed = world({ FILES: { putChunks: async (ns: string, key: string,
                                                      chunks: { sha256: string; bytes: Uint8Array }[]) => {
      if (first.length > 0) {
        throw new Error("the delivery is killed");
      }
      first.push(...chunks.map((chunk) => chunk.sha256));
      return doorsEnv.FILES.putChunks(ns, key, chunks);
    } } });
    const message = await staged(owner);
    expect((await drain(killed, message)).outcome).toBe("retried");
    // Its upload stays open, and the run again resumes it: no second intent is begun.
    expect((await held(owner)).intents).toBe(1);
    const w = world();
    expect(await drain(w, message)).toEqual({ deliveries: 1, outcome: "acked" });
    expect((await held(owner)).intents).toBe(0);
    // Every chunk but the one the killed delivery put is put now, that one never again.
    const chunks = [...bundleFiles().keys()].filter((name) => name.startsWith("chunks/"));
    expect(first.length).toBe(1);
    expect(w.puts.length).toBe(chunks.length - 1);
    expect(w.puts).not.toContain(first[0]);
    await admitOwner(owner);
    await readBack(owner);
  });

  it("batches a page's inline files within the doors' bounds, and holds one chunk at a time",
     async () => {
    expect(IMPORT_HOLD_BYTES).toBe(CHUNK_BYTES);
    const owner = fresh();
    const files = bundleFiles();
    // One manifest page of 256 inline files of 4 KiB each and two folders: past the call's
    // 256 entries, and its 1 MiB of bytes.
    const entries: Entry[] = [{ path: "/workspace/imported", kind: "dir" }];
    for (let n = 0; n < 256; n += 1) {
      const bytes = new Uint8Array(4096).fill(n % 251);
      bytes[0] = n >> 8;
      bytes[1] = n & 0xff;
      const id = inlineId(bytes);
      files.set(`inline/${id}`, bytes);
      entries.push({ path: `/workspace/imported/many/${n}.bin`, kind: "file", size: 4096, mode: 0,
                     mtime_ms: 0, content_id: id });
    }
    entries.splice(1, 0, { path: "/workspace/imported/many", kind: "dir" });
    for (const name of [...files.keys()].filter((each) => each.startsWith("manifest/"))) {
      files.delete(name);
    }
    const page = new TextEncoder().encode(`${JSON.stringify({ entries })}\n`);
    files.set("manifest/0.json", page);
    const index = json(files.get("bundle.json")!);
    const parts = Object.fromEntries(Object.entries(index["parts"] as Record<string, string>)
      .filter(([name]) => !name.startsWith("manifest/")));
    index["parts"] = { ...parts, "manifest/0.json": sha256(page) };
    files.set("bundle.json", new TextEncoder().encode(`${JSON.stringify(index)}\n`));
    expect(await drain(world(), await staged(owner, made(owner, files))))
      .toEqual({ deliveries: 1, outcome: "acked" });
    expect((await held(owner)).entries).toBe(entries.length);
  });
});

describe("the import consumer's gates, each before anything is written", () => {
  async function dropped(owner: string, w: ReturnType<typeof world>, body: unknown):
      Promise<void> {
    const before = await held(owner);
    const delivered = batch(body);
    await importQueue(delivered.batch, w.env);
    expect([delivered.acked, delivered.retried]).toEqual([[0], []]);
    expect(w.sent).toEqual([]);
    expect(w.puts).toEqual([]);
    expect(await held(owner)).toEqual(before);
  }

  it("drops every message while the doors hold no IMPORT_KEY", async () => {
    const owner = fresh();
    await dropped(owner, world({ IMPORT_KEY: undefined }), await staged(owner));
  });

  it("drops a message out of shape, or whose MAC is not under IMPORT_KEY", async () => {
    const owner = fresh();
    const message = await staged(owner);
    const w = world();
    await dropped(owner, w, { ...message, extra: 1 });
    await dropped(owner, w, { owner: message.owner, bundle: message.bundle, prefix: message.prefix });
    await dropped(owner, w, { ...message, mac: importMac(randomBytes(32).toString("base64url"),
                                                         "import", owner, message.bundle) });
    // A MAC for another owner, another bundle or another op is no MAC of this message.
    await dropped(owner, w, { ...message, mac: importMac(KEY, "import", fresh(), message.bundle) });
    await dropped(owner, w, { ...message, bundle: sha256("another"),
                              mac: importMac(KEY, "import", owner, sha256("another")) });
    await dropped(owner, w, { ...message, mac: importMac(KEY, "check", owner, message.bundle) });
  });

  it("drops a bundle whose index or a part is not what the MAC and the index name", async () => {
    const owner = fresh();
    const w = world();
    // The index changed after it was signed, still well formed (its rows pages left out):
    // its digest is not the message's.
    const message = await staged(owner);
    const index = json(made(owner).files.get("bundle.json")!);
    index["parts"] = Object.fromEntries(Object.entries(index["parts"] as Record<string, string>)
      .filter(([name]) => !name.startsWith("rows/")));
    await doorsEnv.IMPORT_STAGING.put(`${message.prefix}/bundle.json`,
                                      `${JSON.stringify(index)}\n`);
    await dropped(owner, w, message);
    // A rows page changed under its index.
    const rows = await staged(owner);
    await doorsEnv.IMPORT_STAGING.put(`${rows.prefix}/rows/0.json`, '{"rows":[]}\n');
    await dropped(owner, w, rows);
    // A manifest page changed under its index.
    const pages = await staged(owner);
    await doorsEnv.IMPORT_STAGING.put(`${pages.prefix}/manifest/0.json`, '{"entries":[]}\n');
    await dropped(owner, w, pages);
  });

  it("drops a bundle whose staged chunk or inline file is not its hash, writing no byte of it",
     async () => {
    // A chunk's bytes swapped in the bucket: dropped before FILES takes it.
    const owner = fresh();
    const chunked = await staged(owner);
    const chunk = [...bundleFiles().keys()].find((name) => name.startsWith("chunks/"))!;
    await doorsEnv.IMPORT_STAGING.put(`${chunked.prefix}/${chunk}`, new Uint8Array(64).fill(7));
    const w = world();
    const delivered = batch(chunked);
    await importQueue(delivered.batch, w.env);
    expect([delivered.acked, delivered.retried]).toEqual([[0], []]);
    expect(w.puts).not.toContain(chunk.slice("chunks/".length));
    // An inline file's bytes swapped: dropped, the file never made.
    const other = fresh();
    const inlined = await staged(other);
    const name = [...bundleFiles().keys()].find((each) => each.startsWith("inline/"))!;
    // Same length, one byte changed: only the content id tells them apart.
    const swapped = bundleFiles().get(name)!.slice();
    swapped[0] = swapped[0]! ^ 1;
    await doorsEnv.IMPORT_STAGING.put(`${inlined.prefix}/${name}`, swapped);
    const again = batch(inlined);
    await importQueue(again.batch, world().env);
    expect([again.acked, again.retried]).toEqual([[0], []]);
    const entries = paged<Entry>(bundleFiles(), "manifest", "entries")
      .filter((entry) => entry.content_id === name.slice("inline/".length));
    expect(entries.length).toBeGreaterThan(0);
    await admitOwner(other);
    for (const entry of entries) {
      expect((await stubOf(other).fileStat(own(other), { path: entry.path })).entry).toBeNull();
    }
  });

  it("drops an owner the directory holds a record of, admitted or erased", async () => {
    for (const s of ["admitted", "erased"] as const) {
      const owner = fresh();
      const message = await staged(owner);
      await putOwner(doorsEnv, owner, { s, t: 1 });
      await dropped(owner, world(), message);
    }
  });

  it("drops a message for an owner that is not pending: the object refuses its import doors",
     async () => {
    const owner = fresh();
    await admitOwner(owner);
    const w = world();
    const delivered = batch(await staged(owner));
    await importQueue(delivered.batch, w.env);
    expect([delivered.acked, delivered.retried]).toEqual([[0], []]);
    expect(w.puts).toEqual([]);
    const stub = stubOf(owner) as unknown as DurableObjectStub<OwnerDO>;
    expect((await stub.noticeList(own(owner))).notices.filter((n) => n.type === "imported"))
      .toEqual([]);
    expect((await stub.fileStat(own(owner), { path: "/workspace/imported" })).entry).toBeNull();
  });
});

/** `message`'s owner and bundle asked for `op`, MAC'd for it, with a fresh ask. */
function asking(message: ImportMessage, op: "check" | "discard" | "admit"): ImportMessage {
  return { op, owner: message.owner, bundle: message.bundle, prefix: message.prefix,
           mac: importMac(KEY, op, message.owner, message.bundle),
           ask: randomBytes(8).toString("hex") };
}

/** An answer the consumer wrote to the staging bucket, or null. */
async function staging(name: string): Promise<Record<string, unknown> | null> {
  const found = await doorsEnv.IMPORT_STAGING.get(name);
  return found === null ? null : JSON.parse(await found.text()) as Record<string, unknown>;
}

/** A check's answer and pages read back from the staging bucket, joined as the job joins them. */
async function answersOf(message: ImportMessage): Promise<Answers> {
  const pages = async (part: string) => {
    const found: Record<string, unknown>[] = [];
    for (let n = 0; ; n += 1) {
      const page = (await staging(answerName(message.prefix, message.ask!, `${part}-${n}`)))!;
      found.push(page);
      if (page["more"] !== true) {
        return found;
      }
    }
  };
  const state = (await staging(answerName(message.prefix, message.ask!)))!;
  expect(state["refused"]).toBeUndefined();
  return {
    entries: (await pages("files")).flatMap((page) => page["entries"] as Answers["entries"]),
    chunks: (await pages("chunks")).flatMap((page) => page["chunks"] as Answers["chunks"]),
    rows: (await pages("rows")).reduce((sum: { count: number; sum: string }, page) => ({
      count: sum.count + (page["count"] as number),
      sum: rowsSum(sum.sum, page["sum"] as string) }), { count: 0, sum: NO_ROWS }),
    state: state as unknown as Answers["state"],
  };
}

/** The job's diff of the owner's check against the fixture's bundle (`import/check.ts`). */
async function checkOf(message: ImportMessage, bounds?: SliceBounds) {
  const asked = asking(message, "check");
  expect(await drain(world(), asked, bounds)).toMatchObject({ outcome: "acked" });
  const files = bundleFiles();
  return differences(expectedOf(paged(files, "manifest", "entries"), paged(files, "rows", "rows")),
                     await answersOf(asked), "free");
}

/** The owner object's file space: its namespace and wrapped data key. */
function spaceOf(owner: string): Promise<{ ns: string; data_key: string }> {
  return runInDurableObject(stubOf(owner), (_i: unknown, state: DurableObjectState) =>
    state.storage.sql.exec<{ ns: string; data_key: string }>(
      "SELECT ns, data_key FROM file_space").one());
}

describe("the import's check, through the consumer, held to the bundle by the job's diff", () => {
  it("finds nothing missing, extra or different after an import, sliced or not, and marks " +
     "the import done", async () => {
    const owner = fresh();
    const message = await staged(owner);
    await drain(world(), message);
    expect(await staging(doneName(message.prefix)))
      .toEqual({ op: "import", bundle: message.bundle, done: true });
    const found = await checkOf(message);
    expect(found).toEqual({ missing: [], extra: [], different: [] });
    expect(clean(found)).toBe(true);
    // A check killed between its slices goes on from its continuation to the same answer.
    expect(await checkOf(message, { ms: 0, bytes: 0 })).toEqual(found);
  });

  it("finds a missing, an extra and a changed file, a changed row and a bad chunk", async () => {
    const owner = fresh();
    const message = await staged(owner);
    await drain(world(), message);
    const entries = paged<Entry>(bundleFiles(), "manifest", "entries");
    const files = entries.filter((entry) => entry.kind === "file" && entry.chunks === undefined);
    const [gone, changed] = [files[0]!, files[1]!];
    const chunk = entries.find((entry) => entry.chunks !== undefined)!.chunks![0]!;
    const row = paged<Record<string, unknown>>(bundleFiles(), "rows", "rows")
      .find((each) => each["kind"] === "collection")!;
    const split = (path: string) => [path.slice(0, path.lastIndexOf("/")),
                                     path.slice(path.lastIndexOf("/") + 1)];
    // The same length, one letter changed: only the body's id tells them apart.
    const body = (row["body"] as string).replace(/[a-z]/, (c) => c === "x" ? "y" : "x");
    await runInDurableObject(stubOf(owner), (_i: unknown, state: DurableObjectState) => {
      const sql = state.storage.sql;
      sql.exec("DELETE FROM file_entries WHERE parent = ? AND name = ?", ...split(gone.path));
      sql.exec("UPDATE file_entries SET mtime_ms = mtime_ms + 1 WHERE parent = ? AND name = ?",
               ...split(changed.path));
      sql.exec("UPDATE collection_rows SET body = ? WHERE row_id = ?", body,
               row["row_id"] as string);
    });
    const extra = new TextEncoder().encode("extra");
    const { binding, stub } = bindImportFor(owner, message.bundle);
    await stub.importFiles(binding, { entries: [{ kind: "file", path: "/workspace/imported/x.md",
                                                  size: extra.byteLength, mode: 0,
                                                  content_id: inlineId(extra), mtime_ms: 1,
                                                  bytes: extra }] });
    const space = await spaceOf(owner);
    await (env as unknown as { OWNER_FILES: { deleteChunks(ns: string, key: string,
                                                       shas: string[]): Promise<unknown> } })
      .OWNER_FILES.deleteChunks(space.ns, space.data_key, [chunk]);
    const found = await checkOf(message);
    expect(found.missing).toEqual([`file ${gone.path}`]);
    expect(found.extra).toEqual(["file /workspace/imported/x.md"]);
    expect(found.different).toEqual([`file ${changed.path}: mtime_ms`,
                                     `chunk ${chunk}: not read back as its hash`,
                                     "rows: their digest"]);
    expect(clean(found)).toBe(false);
  });

  it("answers a check of an owner that is not pending as refused, writing nothing", async () => {
    const owner = fresh();
    const message = await staged(owner);
    await drain(world(), message);
    await admitOwner(owner);
    const asked = asking(message, "check");
    const delivered = batch(asked);
    await importQueue(delivered.batch, world().env);
    expect([delivered.acked, delivered.retried]).toEqual([[0], []]);
    expect((await staging(answerName(asked.prefix, asked.ask!)))?.["refused"])
      .toMatch(/only a pending owner/);
  });
});

/** The import's binding for `owner`, as the consumer makes it. */
function bindImportFor(owner: string, bundle: string) {
  return { binding: { owner, caller: PLATFORM_CALLER, origin: null, import: { bundle },
                      tiers: TIERS } as Binding,
           stub: stubOf(owner) as unknown as DurableObjectStub<OwnerDO> };
}

describe("the import's discard, through the consumer", () => {
  it("wipes a pending owner, its namespace answered, writing no record, and again", async () => {
    const owner = fresh();
    const message = await staged(owner);
    await drain(world(), message);
    const { ns } = await spaceOf(owner);
    const asked = asking(message, "discard");
    expect(await drain(world(), asked)).toMatchObject({ outcome: "acked" });
    expect(await staging(answerName(asked.prefix, asked.ask!)))
      .toEqual({ op: "discard", bundle: message.bundle, discarded: true, namespace: ns });
    expect(await readOwner(doorsEnv, owner)).toBeNull();
    // Killed before its answer, the discard run again answers the same.
    const again = asking(message, "discard");
    expect(await drain(world(), again)).toMatchObject({ outcome: "acked" });
    expect((await staging(answerName(again.prefix, again.ask!)))?.["namespace"]).toBe(ns);
  });

  it("takes a rehearsal's admitted owner and its record, and never a real admitted one",
     async () => {
    const rehearsal = rehearsalName("r1", fresh());
    const message = await staged(rehearsal);
    await drain(world(), message);
    await admitOwner(rehearsal);
    await putOwner(doorsEnv, rehearsal, { s: "admitted", t: 1 });
    const asked = asking(message, "discard");
    await drain(world(), asked);
    expect((await staging(answerName(asked.prefix, asked.ask!)))?.["discarded"]).toBe(true);
    const real = fresh();
    const kept = await staged(real);
    await drain(world(), kept);
    await admitOwner(real);
    const refused = asking(kept, "discard");
    expect(await drain(world(), refused)).toMatchObject({ outcome: "acked" });
    expect((await staging(answerName(refused.prefix, refused.ask!)))?.["refused"])
      .toMatch(/only a pending owner/);
    await putOwner(doorsEnv, real, { s: "admitted", t: 1 });
    const recorded = asking(kept, "discard");
    await drain(world(), recorded);
    expect((await staging(answerName(recorded.prefix, recorded.ask!)))?.["refused"])
      .toMatch(/holds a record of this owner/);
    expect((await stubOf(real).fileStat(own(real), { path: "/workspace/imported" })).entry)
      .not.toBeNull();
  });
});

describe("the import's admit, through the consumer", () => {
  it("sets the tier, admits, suspends for each old reason, then writes the record, and again",
     async () => {
    const owner = fresh();
    const files = bundleFiles();
    const record = json(files.get("owner.json")!);
    record["admission"] = { state: "suspended", reasons: ["billing"] };
    files.set("owner.json", new TextEncoder().encode(`${JSON.stringify(record)}\n`));
    const message = await staged(owner, made(owner, files));
    await drain(world(), message);
    for (let n = 0; n < 2; n += 1) {
      const asked = asking(message, "admit");
      expect(await drain(world(), asked)).toMatchObject({ outcome: "acked" });
      expect(await staging(answerName(asked.prefix, asked.ask!))).toEqual({
        op: "admit", bundle: message.bundle, tier: "paid", state: "suspended",
        reasons: ["billing"] });
    }
    expect((await readOwner(doorsEnv, owner))?.s).toBe("admitted");
    const { binding, stub } = bindImportFor(owner, message.bundle);
    expect((await stub.importCheck(binding, { part: "state", page: 0 }).catch((e) => e)).message)
      .toMatch(/only a pending owner/);
  });

  it("never admits an owner the old stack never admitted", async () => {
    const owner = fresh();
    const files = bundleFiles();
    const record = json(files.get("owner.json")!);
    record["admission"] = { state: "absent", reasons: [] };
    files.set("owner.json", new TextEncoder().encode(`${JSON.stringify(record)}\n`));
    const message = await staged(owner, made(owner, files));
    await drain(world(), message);
    const asked = asking(message, "admit");
    await drain(world(), asked);
    expect((await staging(answerName(asked.prefix, asked.ask!)))?.["refused"])
      .toMatch(/never admitted/);
    expect(await readOwner(doorsEnv, owner)).toBeNull();
    const { binding, stub } = bindImportFor(owner, message.bundle);
    expect(await stub.importCheck(binding, { part: "state", page: 0 }))
      .toMatchObject({ state: "pending", tier: "free" });
  });

  it("converges when killed between its steps", async () => {
    const owner = fresh();
    const message = await staged(owner);
    await drain(world(), message);
    // The object's admit fails once, after the tier is set: the queue retries, and the run
    // again admits and writes the record.
    let failed = 0;
    const flaky = world({ OWNER: { getByName: (name: string) => {
      const stub = doorsEnv.OWNER.getByName(name);
      return new Proxy(stub, { get: (target, key) => key === "admissionSet" && failed++ === 0
        ? async () => { throw new Error("the delivery is killed"); }
        : Reflect.get(target, key) });
    } } });
    const asked = asking(message, "admit");
    expect((await drain(flaky, asked)).outcome).toBe("retried");
    expect(await readOwner(doorsEnv, owner)).toBeNull();
    expect(await drain(world(), asked)).toMatchObject({ outcome: "acked" });
    expect((await readOwner(doorsEnv, owner))?.s).toBe("admitted");
  });
});

describe("the import consumer's own bounds", () => {
  it("converges when two copies of one owner's import run at once", async () => {
    const owner = fresh();
    const message = await staged(owner);
    // The original and its redelivery, each going on from its own continuations, at once:
    // what a kill between a continuation's send and its ack leaves running.
    const [a, b] = [world(), world()];
    const outcomes = await Promise.all([drain(a, message, { ms: 0, bytes: 0 }),
                                        drain(b, message, { ms: 0, bytes: 0 })]);
    expect(outcomes.map((outcome) => outcome.outcome)).toEqual(["acked", "acked"]);
    const whole = fresh();
    await drain(world(), await staged(whole));
    const [two, one] = [await held(owner), await held(whole)];
    expect(two).toEqual(one);
    expect(await checkOf(message)).toEqual({ missing: [], extra: [], different: [] });
  });

  it("drops a staged object past the one chunk a delivery holds, before reading it", async () => {
    expect(IMPORT_HOLD_BYTES).toBe(CHUNK_BYTES);
    const owner = fresh();
    const files = bundleFiles();
    const index = json(made(owner, files).files.get("bundle.json")!);
    // The index, valid and signed, padded past the hold: never read, so nothing is written.
    const padded = new TextEncoder().encode(
      `${JSON.stringify(index)}${" ".repeat(IMPORT_HOLD_BYTES)}\n`);
    files.set("bundle.json", padded);
    const message = await staged(owner, { files, bundle: sha256(padded) });
    const w = world();
    const delivered = batch(message);
    await importQueue(delivered.batch, w.env);
    expect([delivered.acked, delivered.retried]).toEqual([[0], []]);
    expect(w.puts).toEqual([]);
    expect((await held(owner)).entries).toBe(0);
  });
});
