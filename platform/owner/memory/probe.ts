/**
 * The workerd memory leg's probe (`leg.mjs`, ADR-037): the owner's real paths, at their
 * worst, in an owner isolate against a chunk store in another isolate, as the files Worker
 * is, so each chunk crosses an RPC as in production.
 *
 * - **The owner isolate** (`main`) runs the platform's own owner object (`LegOwner`: the
 *   real `OwnerDO`, its SQLite, its migrations, its doors) and this module's driver entry.
 *   Its paths are the real ones: `fileEdit` (through `Files.writeChunks`), `fileAppend`, and
 *   a placed body read whole (`Bodies.fetch`) or by a window (`Bodies.windows`), the real
 *   `Bodies` over the object's own storage.
 * - **The store** (`files`, `Store`) answers the files Worker's chunk calls from memory,
 *   each hash checked and a verified range read from its whole chunk, as `FILES` answers.
 *
 * **Checkpoints.** Every chunk call first awaits the leg's driver: the owner's path is then
 * suspended on the store, holding what it holds at that call (a chunk it sends, the chunks
 * in hand), and the driver collects the owner isolate (`/gc`, a swept full collection) and
 * reads its heap and backing stores before it lets the call go on (rpcretain_research.md
 * §4: a read must be atomic with its collection). With several paths at once the driver
 * waits until each is parked on a call or done, so nothing runs in the isolate between the
 * collection and the reading.
 *
 * Content is distinct in every chunk (letters from a seeded generator), so a longer edit's
 * shifted chunks are each made and put, never found already stored (review H2).
 *
 * Routes: `/setup?path&mib&n` makes `n` objects ready for `path` at `mib` MiB (outside any
 * measurement), `/run?path&mib` runs it in each at once, `/rest` serves one object's first
 * request (the baseline), `/costs` answers the declared costs, `/gc` collects.
 */
import { createHash } from "node:crypto";

import { WorkerEntrypoint } from "cloudflare:workers";

import { bundledSources, signinsOn } from "../../doors/src/sources";
import { PLATFORM_CALLER } from "../src/admission";
import { Bodies, type Located } from "../src/bodies";
import { type ChunkStore, Files } from "../src/files";
import { WriteGate } from "../src/gate";
import * as holds from "../src/holds";
import { CHUNK_BYTES } from "../src/manifest";
import { type Binding, EDIT_BYTES_MAX, OwnerDO, type OwnerEnv } from "../src/owner-do";
import { PAGE_BYTES } from "../src/pages";
import { FILE_DEFAULTS } from "../src/policy";
import { SLOT_WAIT_MS, UNIT_BYTES } from "../src/slots";

declare function gc(): void;

interface LegEnv extends OwnerEnv {
  readonly OWNER: DurableObjectNamespace<LegOwner>;
  readonly STORE: Service<Store>;
  readonly DRIVER: Fetcher;
}

const MIB = 1024 * 1024;
const MARK = "<<MARK>>";
const EDITED = "<<MARK, edited and longer>>";
const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");

/** `size` bytes of lowercase letters, distinct for each `seed` (a xorshift generator). */
function letters(seed: number, size: number): Uint8Array {
  const out = new Uint8Array(size);
  let x = (Math.imul(seed + 1, 2654435761) >>> 0) || 1;
  for (let at = 0; at < size; at++) {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    out[at] = 97 + ((x >>> 0) % 26);
  }
  return out;
}

/** Each isolate's: whether a run is timed, its checkpoints skipped (`/run?timed=1`). */
const TIMED = { on: false };

/**
 * A checkpoint: the path waits while the driver samples. From the owner isolate it says the
 * units the isolate's pool holds then (`held`), so the driver holds what is live to them.
 */
const checkpoint = async (env: { DRIVER: Fetcher }, held?: number, at = "store") => {
  if (!TIMED.on) {
    await (await env.DRIVER.fetch(`http://driver/checkpoint?at=${at}${held === undefined ? ""
      : `&held=${held}`}`)).text();
  }
};

/** A checkpoint in the owner isolate, with the units its pool holds. */
const owned = (env: { DRIVER: Fetcher }, at: string) =>
  checkpoint(env, holds.ownerSlots().state().held, at);

const STORED = new Map<string, Uint8Array>();

/** The files Worker's chunk calls, answered from memory, each first a checkpoint. */
export class Store extends WorkerEntrypoint<{ DRIVER: Fetcher }> implements ChunkStore {
  async mintKey(): Promise<{ key: string }> {
    return { key: "v1.leg" };
  }

  async putChunks(ns: string, _key: string, chunks: { sha256: string; bytes: Uint8Array }[]):
      Promise<{ created: number }> {
    await checkpoint(this.env);
    let created = 0;
    for (const { sha256, bytes } of chunks) {
      // Timed, the files Worker's own hashing is left out: it is that Worker's time.
      if (!TIMED.on && sha(bytes) !== sha256) {
        throw new Error("a chunk's bytes do not match its hash");
      }
      if (!STORED.has(`${ns}/${sha256}`)) {
        STORED.set(`${ns}/${sha256}`, bytes);
        created += 1;
      }
    }
    return { created };
  }

  async getChunks(ns: string, _key: string,
                  reads: { sha256: string; offset?: number; length?: number }[]):
      Promise<Uint8Array[]> {
    await checkpoint(this.env);
    return reads.map((read) => {
      const bytes = STORED.get(`${ns}/${read.sha256}`);
      if (bytes === undefined || (!TIMED.on && sha(bytes) !== read.sha256)) {
        throw new Error("a chunk's bytes no longer match its hash");
      }
      return read.offset === undefined ? bytes
        : bytes.slice(read.offset, read.offset + read.length!);
    });
  }

  async deleteChunks(ns: string, _key: string, sha256s: string[]): Promise<{ deleted: number }> {
    let deleted = 0;
    for (const sha256 of sha256s) {
      deleted += STORED.delete(`${ns}/${sha256}`) ? 1 : 0;
    }
    return { deleted };
  }

  async drop(): Promise<void> {
    STORED.clear();
  }

  async timed(on: boolean): Promise<void> {
    TIMED.on = on;
  }

  // The rest of the files Worker's surface: no path the leg runs calls it.
  async rewrapKey(): Promise<never> { throw new Error("not in the leg"); }
  async rewrapManifestRoot(): Promise<never> { throw new Error("not in the leg"); }
  async deleteNamespace(): Promise<never> { throw new Error("not in the leg"); }
  async putManifestPart(): Promise<never> { throw new Error("not in the leg"); }
  async putManifestSegment(): Promise<never> { throw new Error("not in the leg"); }
  async putManifestRoot(): Promise<never> { throw new Error("not in the leg"); }
  async getManifestRoot(): Promise<never> { throw new Error("not in the leg"); }
  async getManifestPart(): Promise<never> { throw new Error("not in the leg"); }
  async getManifestSegment(): Promise<never> { throw new Error("not in the leg"); }
  async deleteManifest(): Promise<never> { throw new Error("not in the leg"); }
}

/**
 * The store as the owner isolate calls it: each chunk read, once it is in hand, is a
 * checkpoint too, so a sample sees the bytes a call brought before the path goes on.
 */
function received(env: LegEnv): ChunkStore {
  const store = env.FILES;
  return {
    mintKey: (ns) => store.mintKey(ns),
    putChunks: async (ns, key, chunks) => {
      await owned(env, "before-put");
      return store.putChunks(ns, key, chunks);
    },
    getChunks: async (ns, key, reads) => {
      await owned(env, "before-get");
      const got = await store.getChunks(ns, key, reads);
      await owned(env, "after-get");
      return got;
    },
    deleteChunks: (ns, key, sha256s) => store.deleteChunks(ns, key, sha256s),
    rewrapKey: (ns, key) => store.rewrapKey(ns, key),
    rewrapManifestRoot: (ns, gen) => store.rewrapManifestRoot(ns, gen),
    deleteNamespace: (ns) => store.deleteNamespace(ns),
    putManifestPart: (ns, key, gen, part, bytes) =>
      store.putManifestPart(ns, key, gen, part, bytes),
    putManifestSegment: (ns, key, gen, a, b, bytes) =>
      store.putManifestSegment(ns, key, gen, a, b, bytes),
    putManifestRoot: (ns, key, gen, parts, segments, span) =>
      store.putManifestRoot(ns, key, gen, parts, segments, span),
    getManifestRoot: (ns, gen) => store.getManifestRoot(ns, gen),
    getManifestPart: (ns, key, gen, part, sha256) =>
      store.getManifestPart(ns, key, gen, part, sha256),
    getManifestSegment: (ns, key, gen, a, b, sha256) =>
      store.getManifestSegment(ns, key, gen, a, b, sha256),
    deleteManifest: (ns, gen, parts, segments) => store.deleteManifest(ns, gen, parts, segments),
  };
}

/** The real owner object, with the body paths and the control in reach. */
export class LegOwner extends OwnerDO {
  readonly #leg: LegEnv;
  readonly #gate: WriteGate;
  readonly #bodies: Bodies;

  constructor(state: DurableObjectState, env: LegEnv) {
    const files = received(env);
    super(state, { ...env, FILES: files });
    this.#leg = env;
    this.#gate = new WriteGate(state.storage);
    const bodyFiles = new Files(this.#gate.storage, files,
                            () => ({ ...FILE_DEFAULTS, max_bytes: 25_000_000_000 }),
                            this.#gate.write, () => "storage_full");
    this.#bodies = new Bodies(bodyFiles, this.#gate.storage, this.#gate.write);
  }

  /** A placed body of `bytes` letters, placed as a message's is: its content id. */
  async legPlace(seed: number, bytes: number): Promise<string> {
    const text = new TextDecoder().decode(letters(seed, bytes));
    const placing = await this.#gate.write(() => this.#bodies.begin(text, "owner"));
    const placed = await this.#bodies.send(placing!);
    return this.#gate.write(() => this.#bodies.adopt(placed, "owner", (id) => id));
  }

  #located(contentId: string): Located {
    return this.#bodies.locate([contentId]);
  }

  /** The body read whole (`Bodies.fetch`, a run's mail input), held at a checkpoint. */
  async legFetch(contentId: string): Promise<number> {
    const text = (await this.#bodies.fetch(this.#located(contentId))).get(contentId)!;
    await checkpoint(this.#leg);
    return text.length;
  }

  /** One page's window from the body's middle (`Bodies.windows`, a read's): its length. */
  async legWindow(contentId: string, offset: number): Promise<number> {
    const windows = await this.#bodies.windows(this.#located(contentId), () => PAGE_BYTES,
                                               () => offset);
    return windows.get(contentId)!.text.length;
  }

  /** The control: a chunk made and kept at each checkpoint, its live set growing. */
  async legHoard(mib: number): Promise<number> {
    const kept: Uint8Array[] = [];
    for (let at = 0; at < mib * MIB; at += CHUNK_BYTES) {
      kept.push(letters(at, CHUNK_BYTES));
      await checkpoint(this.#leg);
    }
    return kept.length;
  }
}

const SIGNINS = Object.freeze(signinsOn(
  bundledSources(), Object.fromEntries(bundledSources().ids().map((id) => [id, true]))));
const TIERS = Object.freeze({ free: { runs: 5, storage_bytes: 25_000_000_000 },
                              paid: { runs: 10, storage_bytes: 250_000_000_000 } });

/** The owner's own binding, as the doors hand it. */
function owner(name: string): Binding {
  return { owner: name, caller: name, origin: null, tiers: TIERS, signins: SIGNINS } as
    unknown as Binding;
}

/** A fresh owner's object, admitted as sign-up admits it. */
async function object(env: LegEnv, name: string): Promise<DurableObjectStub<LegOwner>> {
  const stub = env.OWNER.getByName(name);
  await stub.admissionSet({ owner: name, caller: PLATFORM_CALLER, origin: null } as Binding,
                          { admit: true });
  return stub;
}

/**
 * A file of `bytes` distinct letters (the mark in its first chunk) uploaded as a client
 * uploads one, two chunks a call: its etag.
 */
async function upload(stub: DurableObjectStub<LegOwner>, who: Binding, path: string,
                      seed: number, bytes: number): Promise<string> {
  const piece = (index: number) => {
    const out = letters(seed * 1000 + index,
                        Math.min(CHUNK_BYTES, bytes - index * CHUNK_BYTES));
    if (index === 0) {
      out.set(new TextEncoder().encode(MARK), 100);
    }
    return out;
  };
  const count = Math.ceil(bytes / CHUNK_BYTES);
  const hashes = Array.from({ length: count }, (_, index) => sha(piece(index)));
  const { intent_id } = await stub.fileUploadBegin(who, { size: bytes, chunks: hashes });
  for (let index = 0; index < count; index += 2) {
    await stub.fileUploadChunks(who, { intent_id, chunks: [index, index + 1]
      .filter((each) => each < count)
      .map((each) => ({ sha256: hashes[each]!, bytes: piece(each) })) });
  }
  const done = await stub.fileUploadCommit(who, { intent_id, path, expected_etag: null });
  if (!("entry" in done)) {
    throw new Error("the leg's upload conflicted");
  }
  return done.entry.etag;
}

interface Ready {
  /** Its object's name: a stub is one request's, so each route gets its own. */
  readonly name: string;
  readonly who: Binding;
  readonly etag?: string;
  readonly content?: string;
  readonly offset?: number;
}

// The driver entry's own state between its routes (the leg's, never an owner's).
// The appended text is made in the setup, as the caller's (the doors') own, so the run's
// samples see only what the owner's path holds of it.
const LEG: { ready: Ready[]; runs: number; appended: string } = { ready: [], runs: 0,
                                                                   appended: "" };

async function setup(env: LegEnv, path: string, mib: number, n: number): Promise<void> {
  LEG.runs += 1;
  LEG.ready = [];
  LEG.appended = path === "append" ? new TextDecoder().decode(letters(LEG.runs, CHUNK_BYTES))
    : "";
  for (let index = 0; index < n; index++) {
    const name = `leg-${path}-${mib}-${n}-${index}-${LEG.runs}`;
    const stub = await object(env, name);
    const who = owner(name);
    const seed = LEG.runs * 100 + index;
    if (path === "edit") {
      LEG.ready.push({ name, who, etag: await upload(stub, who, "/workspace/f.txt", seed,
                                                     mib * MIB) });
    } else if (path === "append") {
      // Its last chunk a chunk less a KiB, so the append reads it and joins a whole chunk
      // of text to it, two chunks written: the append at its worst.
      await upload(stub, who, "/workspace/f.txt", seed, mib * MIB + CHUNK_BYTES - 1024);
      LEG.ready.push({ name, who });
    } else if (path === "fetch" || path === "window") {
      LEG.ready.push({ name, who, content: await stub.legPlace(seed, mib * MIB),
                       offset: Math.floor(mib * MIB / 2) });
    } else {
      LEG.ready.push({ name, who });
    }
  }
}

async function run(env: LegEnv, path: string, mib: number, timed: boolean):
    Promise<number[]> {
  TIMED.on = timed;
  // Each hold's end, before its release, is a checkpoint too (`SlotPool.observe`).
  holds.ownerSlots().observe = (units) => owned(env, `end-of-${units}`);
  await env.STORE.timed(timed);
  const appended = LEG.appended;
  return Promise.all(LEG.ready.map(async ({ name, who, etag, content, offset }) => {
    const stub = env.OWNER.getByName(name);
    try {
      if (path === "edit") {
        const done = await stub.fileEdit(who, { path: "/workspace/f.txt", old_text: MARK,
                                                new_text: EDITED, expected_etag: etag! });
        return "entry" in done ? done.entry.size : -1;
      }
      if (path === "append") {
        return (await stub.fileAppend(who, { path: "/workspace/f.txt", content: appended }))
          .entry.size;
      }
      if (path === "fetch") {
        return await stub.legFetch(content!);
      }
      if (path === "window") {
        return await stub.legWindow(content!, offset!);
      }
      return await stub.legHoard(mib);
    } finally {
      if (!timed) {
        await (await env.DRIVER.fetch("http://driver/done")).text();
      }
    }
  }));
}

export default {
  async fetch(request: Request, env: LegEnv): Promise<Response> {
    const url = new URL(request.url);
    const at = (name: string) => url.searchParams.get(name)!;
    if (url.pathname === "/gc") {
      // A full collection, then a moment for the backing stores it let go to be swept off
      // the main thread, then another, so what is left is the live set.
      gc();
      await scheduler.wait(5);
      gc();
      return new Response("gc");
    }
    if (url.pathname === "/costs") {
      return Response.json({ ...holds, unit: UNIT_BYTES, wait_ms: SLOT_WAIT_MS,
                             edit_bytes_max: EDIT_BYTES_MAX, chunk: CHUNK_BYTES,
                             body_units: [1, 2].map((chunks) => holds.bodyUnits(chunks)) });
    }
    if (url.pathname === "/rest") {
      LEG.runs += 1;
      const name = `leg-rest-${LEG.runs}`;
      const stub = await object(env, name);
      return Response.json(await stub.storageUsage(owner(name)));
    }
    if (url.pathname === "/setup") {
      await env.STORE.drop();
      await setup(env, at("path"), Number(at("mib")), Number(at("n")));
      return new Response("ready");
    }
    if (url.pathname === "/run") {
      try {
        return Response.json(await run(env, at("path"), Number(at("mib")),
                                       url.searchParams.has("timed")));
      } finally {
        TIMED.on = false;
        await env.STORE.timed(false);
      }
    }
    return new Response("no such route", { status: 404 });
  },
};
