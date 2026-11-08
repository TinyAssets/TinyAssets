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
import { canonical } from "../../owner/src/policy";
import type { Binding } from "../../contract/src/binding";
import { importMac, type ImportMessage } from "../../contract/src/import-message";
import type { DoorsEnv } from "../src/config";
import { putOwner } from "../src/directory";
import { IMPORT_HOLD_BYTES, importQueue, type SliceBounds } from "../src/import";
import { CHUNK_BYTES } from "../../contract/src/limits";
import type { OwnerDO } from "./entry";
import type { ImportBundle } from "./import-setup";
import { admitOwner, doorsEnv, OPEN, setControl } from "./support";

const KEY = randomBytes(32).toString("base64url");
const BUNDLE: ImportBundle = inject("importBundle");
const NOTICE = "Your files from the old TinyAssets are in /workspace/imported";

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
  readonly content_id?: string;
  readonly chunks?: string[];
}

/** A bundle's pages of `kind`, in order, flattened. */
function paged<T>(files: Map<string, Uint8Array>, kind: "manifest" | "rows", key: string): T[] {
  return [...files.keys()].filter((name) => name.startsWith(`${kind}/`))
    .sort((a, b) => parseInt(a.slice(kind.length + 1)) - parseInt(b.slice(kind.length + 1)))
    .flatMap((name) => json(files.get(name)!)[key] as T[]);
}

/** A bundle made `owner`'s: its index's owner set, so its digest is new. */
function made(owner: string, files = bundleFiles()): { files: Map<string, Uint8Array>; bundle: string } {
  const index = json(files.get("bundle.json")!);
  index["owner"] = owner;
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
  return { owner, bundle: files.bundle, prefix, mac: importMac(key, owner, files.bundle) };
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
      expect([entry.path, read.entry.size, read.entry.mode, read.entry.writer])
        .toEqual([entry.path, entry.size, entry.mode, "owner"]);
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
    .toEqual([NOTICE]);
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
    expect(before).toMatchObject({ intents: 0, notices: 1, generation: 2 });
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
                     content_id: id });
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
                                                         owner, message.bundle) });
    // A MAC for another owner, or another bundle, is no MAC of this message.
    await dropped(owner, w, { ...message, mac: importMac(KEY, fresh(), message.bundle) });
    await dropped(owner, w, { ...message, bundle: sha256("another"),
                              mac: importMac(KEY, owner, sha256("another")) });
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
