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
 * - **The archive's cases** (the same object, at the production ledger limits) run the
 *   `archive` lane's real move and rewrite, the segments production's own seal makes: the
 *   mail set up in the object's own ledger (bodies placed as a post places them), the lane
 *   rung by an owner's post, its pass run by the object's own alarm.
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

import { TIERS } from "../../ci/fixtures";
import { bundledSources } from "../../sources/src/catalog";
import { signinsOn } from "../../sources/src/signin";
import { type Binding, type Origin, PLATFORM_CALLER } from "../../contract/src/binding";
import type { Located } from "../src/bodies";
import { clock } from "../src/clock";
import { markErasing } from "../src/erasure";
import { OWNER } from "../src/authority";
import { LEDGER_LIMITS, pairRoom } from "../src/ledger";
import { EDIT_BYTES_MAX } from "../src/files";
import { WriteGate } from "../src/gate";
import * as holds from "../src/holds";
import { CHUNK_BYTES, SLOT_WAIT_MS } from "../../contract/src/limits";
import { chunkedId, cut } from "../../contract/src/content";
import type { OwnerEnv } from "../src/env";
import { OwnerDO, ownerStores, type Stores } from "../src/owner-do";
import { PAGE_BYTES } from "../src/bodies";
import {
  MIN_RECORD_BYTES, MOVE_BYTES, MOVE_RECORDS, MoveTable, pack, planSegment, RECORD_TRAILER_BYTES,
  SEGMENT_LEAST_BYTES, SEGMENT_MIN_BYTES,
} from "../src/segments";
import { UNIT_BYTES } from "../../contract/src/slots";
import type { ChunkCheck, ChunkStore } from "../../contract/src/files";

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
        // Its own bytes, as R2 holds them: a chunk sent as a view of a larger buffer (a
        // placed body's piece) arrives with that whole buffer, which a read must not answer.
        STORED.set(`${ns}/${sha256}`, bytes.slice());
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

  async checkChunks(ns: string, _key: string, sha256s: string[]): Promise<ChunkCheck[]> {
    await checkpoint(this.env);
    return sha256s.map((sha256) => {
      const bytes = STORED.get(`${ns}/${sha256}`);
      return bytes === undefined ? "missing"
        : !TIMED.on && sha(bytes) !== sha256 ? "broken" : "stored";
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
  async markNamespace(): Promise<never> { throw new Error("not in the leg"); }
  async deleteNamespace(): Promise<never> { throw new Error("not in the leg"); }
  async holdNamespace(): Promise<never> { throw new Error("not in the leg"); }
  async rootNamespace(): Promise<never> { throw new Error("not in the leg"); }
  async putManifestPart(): Promise<never> { throw new Error("not in the leg"); }
  /** A log segment as the files Worker takes it: a checkpoint while its call holds it. */
  async putManifestSegment(_ns: string, _key: string, _gen: string, _a: number, _b: number,
                           bytes: Uint8Array): Promise<{ sha256: string; size: number }> {
    // What crossed the RPC is the segment's own bytes, never a larger buffer (rvburst X1).
    if (bytes.buffer.byteLength !== bytes.byteLength) {
      throw new Error(`a segment arrived as a view of ${bytes.byteLength} bytes of a ` +
                      `${bytes.buffer.byteLength}-byte buffer`);
    }
    await checkpoint(this.env, undefined, "store-segment");
    return { sha256: sha(bytes), size: bytes.byteLength };
  }
  async putManifestRoot(): Promise<never> { throw new Error("not in the leg"); }
  async getManifestRoot(): Promise<never> { throw new Error("not in the leg"); }
  async getManifestPart(): Promise<never> { throw new Error("not in the leg"); }
  async getManifestSegment(): Promise<never> { throw new Error("not in the leg"); }
  async deleteSegments(): Promise<never> { throw new Error("not in the leg"); }
  async sweepManifests(): Promise<never> { throw new Error("not in the leg"); }
  async chunkNames(): Promise<never> { throw new Error("not in the leg"); }
  async listChunks(): Promise<never> { throw new Error("not in the leg"); }
  async deleteListed(): Promise<never> { throw new Error("not in the leg"); }
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
    checkChunks: async (ns, key, sha256s) => {
      await owned(env, "before-get");
      const got = await store.checkChunks(ns, key, sha256s);
      await owned(env, "after-get");
      return got;
    },
    rewrapKey: (ns, key) => store.rewrapKey(ns, key),
    rewrapManifestRoot: (ns, gen) => store.rewrapManifestRoot(ns, gen),
    markNamespace: (ns) => store.markNamespace(ns),
    deleteNamespace: (ns) => store.deleteNamespace(ns),
    holdNamespace: (ns, held) => store.holdNamespace(ns, held),
    rootNamespace: (ns) => store.rootNamespace(ns),
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
    deleteSegments: (ns, gen, segments) => store.deleteSegments(ns, gen, segments),
    sweepManifests: (ns, keep, cursor) => store.sweepManifests(ns, keep, cursor),
    chunkNames: (ns, key, sha256s) => store.chunkNames(ns, key, sha256s),
    listChunks: (ns, after) => store.listChunks(ns, after),
    deleteListed: (ns, names) => store.deleteListed(ns, names),
  };
}

/**
 * A generation's root and delete calls, answered in the owner isolate with nothing kept: no
 * case reads a root back, so the burst's and the ship's measure their own puts alone.
 */
const ROOTLESS: Pick<ChunkStore, "putManifestRoot" | "deleteSegments" | "sweepManifests"> = {
  putManifestRoot: async () => {},
  deleteSegments: async () => {},
  sweepManifests: async () => ({ deleted: 0, cursor: null }),
};

/**
 * What one move's records hold at `records` (the most, `MOVE_RECORDS`: no real mail reaches
 * it, its records being past `MIN_RECORD_BYTES`), built by the move's own code as a merge
 * builds it: the pick's seqs, a table of its segment's kept records (a third, ranges of one
 * content) and its stragglers, each its own chain (a message id, the longest name), its
 * order, its pieces and their plans; held, in the units it declares, at a checkpoint.
 */
async function moveRecords(env: LegEnv, records: number): Promise<number> {
  const slot = await holds.ownerSlots().acquire(holds.moveTableUnits(records), "",
                                                  undefined, true);
  try {
    const kept = Math.floor(records / 3);
    const seqs = Array.from({ length: records - kept }, (_, n) => kept + n + 1);
    const table = new MoveTable();
    const content = table.addContent(kept * MIN_RECORD_BYTES,
      Array.from({ length: Math.ceil(kept * MIN_RECORD_BYTES / CHUNK_BYTES) },
                 (_, n) => n.toString(16).padStart(64, "0")), null);
    const id = (n: number) => `msg_${n.toString(16).padStart(32, "0")}`;
    for (let n = 0; n < kept; n++) {
      table.push(n + 1, id(n), "dm:leg", n, MIN_RECORD_BYTES, MoveTable.KEPT, content,
                 n * MIN_RECORD_BYTES);
    }
    for (const seq of seqs) {
      table.push(seq, id(seq), "dm:leg", seq, MIN_RECORD_BYTES, MoveTable.INLINE, -1, 0);
    }
    const all = Array.from({ length: table.count }, (_, n) => n);
    const ordered = [...all].sort((a, b) => table.key(a) - table.key(b));
    const plans = pack(ordered, SEGMENT_LEAST_BYTES, (n) => table.length(n)).map((part) => {
      const piece = table.piece(Uint32Array.from(part));
      return { plan: planSegment("mail", piece), indices: piece.indices };
    });
    await owned(env, "move-records");
    return seqs.length + table.count + plans.length;
  } finally {
    slot.release();
  }
}

/**
 * The widest row the change log takes (`IMAGE_MAX`'s own: a connection's models at their
 * 640 KiB, every one a character JSON escapes, and its egress scope at its own), its name
 * one character past U+00FF, so its line is a two-byte string in V8: inserted, and its
 * image logged by `widen`, a change while capture is on (the change log test's L5-1 row).
 */
const WIDEN = "UPDATE connections SET expires_at = coalesce(expires_at, 0) + 1";
function wideRow(sql: Pick<SqlStorage, "exec">, n = 0): void {
  sql.exec("INSERT INTO connections (id, source, kind, display_name, provider, models, " +
           "status, version, created_at, revocable, egress) VALUES (?, 'src', 'oauth2', " +
           "?, 'p', ?, 'active', 1, 0, 1, ?)", `c${n}`.padEnd(25, "c"), "wide \u20ac",
           '"'.repeat(655_360), "\\".repeat(16_384));
}

const MUSE: Origin = Object.freeze({ client: "a".repeat(64), family: "b".repeat(64) });
const DOT: Origin = Object.freeze({ client: "c".repeat(64), family: "d".repeat(64) });
const PAT: Origin = Object.freeze({ client: "e".repeat(64), family: "f".repeat(64) });
const ORIGINS = [MUSE, DOT, PAT];

/**
 * The real owner object, its chunk calls checkpointed in its own isolate (`received`). What a
 * leg reaches directly (the placed bodies, the ledger its archive cases' mail is set up in,
 * the manifest's lane) it reaches through the object's own wiring (`ownerStores`), over a
 * gate of the leg's own on the object's storage, as a test does (`ledger-support.ts`
 * `storesOn`): the leg builds none of it.
 */
export class LegOwner extends OwnerDO {
  readonly #leg: LegEnv;
  /** The env the object was handed: its `FILES` the checkpointed store. */
  readonly #env: LegEnv;
  readonly #id: DurableObjectId;
  readonly #gate: WriteGate;
  readonly #stores: Stores;

  constructor(state: DurableObjectState, env: LegEnv) {
    const own = { ...env, FILES: received(env) };
    super(state, own);
    this.#leg = env;
    this.#env = own;
    this.#id = state.id;
    this.#gate = new WriteGate(state.storage);
    this.#stores = this.#storesOver(own.FILES);
  }

  /** The object's stores over the leg's gate, their outside calls through `files`. */
  #storesOver(files: ChunkStore): Stores {
    return ownerStores(this.#gate, { ...this.#env, FILES: files }, this.#id);
  }

  /**
   * A generation's burst at production sizes (`GENERATION_SIZES`) of the smallest lines a
   * stream holds (notices of no detail, about 80 bytes a line, as small as a chunk's), so a
   * burst holds the most lines its bytes allow: a whole generation run by the real lane, each
   * part's put a checkpoint, inside its units of the isolate's pool (`BURST_UNITS`), and the
   * owner rewriting rows wide during its third part's put, so the ship after the burst's
   * record builds a full log segment, under its own hold. The widest segment it put.
   */
  async legBurst(): Promise<number> {
    const sql = this.#gate.storage.sql;
    await this.#gate.write(() => sql.exec(
      "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 130000) " +
      "INSERT INTO owner_notices (at, type, detail) SELECT i, 'x', '' FROM n"));
    // Seven rows at `IMAGE_MAX`, each a page of its own and the first burst's (connections
    // come before notices): the seventh crosses its gather, a near-`IMAGE_MAX` two-byte row on
    // a burst's last page, its build sampled (rvburst W1).
    await this.#gate.write(() => {
      for (let n = 0; n < 7; n++) {
        wideRow(sql, n);
      }
    });
    let parts = 0;
    let widest = 0;
    const put = async (bytes: Uint8Array) => {
      await owned(this.#leg, "burst-part");
      return { sha256: sha(bytes), size: bytes.byteLength };
    };
    const { backups } = this.#storesOver({
      ...this.#env.FILES,
      putManifestPart: async (_ns, _key, _gen, _part, bytes) => {
        parts += 1;
        if (parts === 3) {
          // The owner writes during the put: 4,200 rows already read rewritten wide, so the
          // ship after the burst's record builds and puts a full segment, its own hold;
          // each line ASCII but one character past U+00FF, the worst mix for its heap (a
          // two-byte string in V8, twice its UTF-8: review rvburst V1).
          await this.#gate.write(() => {
            sql.exec(WIDEN);  // the widest row first: the segment holds it and the rest
            sql.exec("UPDATE owner_notices SET detail = printf('%.999c', 'b') || char(8364) " +
                     "WHERE seq <= 4200");
          });
        }
        return put(bytes);
      },
      putManifestSegment: async (_ns, _key, _gen, _a, _b, bytes) => {
        widest = Math.max(widest, bytes.byteLength);
        return put(bytes);
      },
      ...ROOTLESS,
    });
    await this.#gate.write(() => sql.exec("UPDATE owner_backups SET changed_at = ? WHERE changed_at IS NULL", Date.now() / 1000));
    await this.#gate.quiet(() => backups.forExport(Date.now() / 1000));
    await backups.run(Date.now() / 1000, Date.now() / 1000 + 120);
    if (parts < 3) {
      throw new Error(`the burst case put ${parts} parts: its write never ran`);
    }
    return widest;
  }

  /**
   * One full log segment shipped outside a burst: 4,200 notices of 1,000 characters (ASCII
   * but one past U+00FF each, the worst mix for a string's heap)
   * rewritten once the generation's capture is on, so the pass's first ship (before any
   * burst) builds and puts a segment of a part's bytes and then the rest, each a checkpoint
   * inside its units (`SHIP_UNITS`), the put made over RPC to the store's isolate as the
   * files Worker's is; then the pass reads as halted, so no burst begins. The widest segment
   * it put.
   */
  async legShip(): Promise<number> {
    const sql = this.#gate.storage.sql;
    await this.#gate.write(() => sql.exec(
      "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 4200) " +
      "INSERT INTO owner_notices (at, type, detail) SELECT i, 'x', 'a' FROM n"));
    await this.#gate.write(() => wideRow(sql));
    let segments = 0;
    let widest = 0;
    let parts = 0;
    const { backups } = this.#storesOver({
      ...this.#env.FILES,
      putManifestPart: async () => {
        parts += 1;
        throw new Error("the ship case begins no burst");
      },
      putManifestSegment: async (ns, key, gen, a, b, bytes) => {
        segments += 1;
        widest = Math.max(widest, bytes.byteLength);
        if (segments === 2) {
          // The owner's erasure begins during the second segment's put, so the pass reads
          // as halted once it lands (`export.test.ts` holds the same of a ship).
          await this.#gate.write(() => markErasing(this.#gate.storage, clock()));
        }
        await owned(this.#leg, "ship-segment");
        return this.#leg.FILES.putManifestSegment(ns, key, gen, a, b, bytes);
      },
      ...ROOTLESS,
    });
    await this.#gate.write(() => sql.exec("UPDATE owner_backups SET changed_at = ? WHERE changed_at IS NULL", Date.now() / 1000));
    await this.#gate.quiet(() => backups.forExport(Date.now() / 1000));
    await this.#gate.write(() => {
      sql.exec(WIDEN);  // the widest row first: the first segment holds it and the rest
      sql.exec("UPDATE owner_notices SET detail = printf('%.999c', 'b') || char(8364) " +
               "WHERE seq <= 4200");
    });
    await backups.run(Date.now() / 1000, Date.now() / 1000 + 120);
    if (segments < 2) {
      throw new Error(`the ship case shipped ${segments} segments, not a full one and more`);
    }
    // The lane takes a failed put as its pass's failure, so the case says it here.
    if (parts > 0) {
      throw new Error("the ship case began a burst: its pass did not halt at the erasure");
    }
    return widest;
  }

  /** A placed body of `bytes` letters, placed as a message's is: its content id. */
  async legPlace(seed: number, bytes: number): Promise<string> {
    const { bodies } = this.#stores;
    const text = new TextDecoder().decode(letters(seed, bytes));
    const placing = await this.#gate.write(() => bodies.begin(text, "owner"));
    const placed = await bodies.send(placing!);
    return this.#gate.write(() => bodies.adopt(placed, "owner", (id) => id));
  }

  #located(contentId: string): Located {
    return this.#stores.bodies.locate([contentId]);
  }

  /** The body read whole (`Bodies.fetch`, a run's mail input), held at a checkpoint. */
  async legFetch(contentId: string): Promise<number> {
    const text = (await this.#stores.bodies.fetch(this.#located(contentId))).get(contentId)!;
    await checkpoint(this.#leg);
    return text.length;
  }

  /** One page's window from the body's middle (`Bodies.windows`, a read's): its length. */
  async legWindow(contentId: string, offset: number): Promise<number> {
    const windows = await this.#stores.bodies.windows(this.#located(contentId), () => PAGE_BYTES,
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

  // The archive cases' (`archive` lane): the real object at the production ledger limits, its
  // mail set up in its own ledger (`ownerStores`), each act one unit of the leg's gate as a
  // door's is, outside any measurement; the lane rung by an owner's post, its passes run by
  // the object's own alarm.

  /** Agents registered as their own clients file them, each admitted first as its door does: each's id. */
  legAgents(): Promise<string[]> {
    const { grants, ledger } = this.#stores;
    return this.#gate.write(() => ORIGINS.map((origin, n) => {
      grants.admit(origin.client, "mcp", null, Math.floor(Date.now() / 1000));
      return ledger.register(origin, { kind: "mcp", ref: `conn-${n}` }, {
        command_center_id: "alpha", display_name: ["Muse", "Dot", "Pat"][n]!,
        delivery_method: "pull", recipe_id: null }).agent_id;
    }));
  }

  /** `count` small messages from the owner to `room`'s agent (`reader`), each acked. */
  legSmall(room: string, reader: number, count: number): Promise<number> {
    const { ledger } = this.#stores;
    const origin = ORIGINS[reader]!;
    return this.#gate.write(() => {
      let last = 0;
      for (let n = 0; n < count; n++) {
        last = ledger.seqOf(ledger.post(null, room, `s${n}`, "x", null).message_id)!;
        if (n % 1000 === 999 || n === count - 1) {
          ledger.read(origin, 1);
          ledger.ack(origin, last);
        }
      }
      return last;
    });
  }

  /** `count` small messages from the owner to `room`'s agent, left unread. */
  async legUnread(room: string, count: number): Promise<void> {
    const { ledger } = this.#stores;
    await this.#gate.write(() => {
      for (let n = 0; n < count; n++) {
        ledger.post(null, room, `u${n}`, "x", null);
      }
    });
  }

  /** What a message's record takes past its body's bytes, as its segment will hold it. */
  legOverhead(id: string): number {
    return this.#gate.storage.sql.exec<{ n: number }>(
      "SELECT m.line_bytes - b.body_bytes AS n FROM ledger_messages m JOIN message_bodies b " +
        "ON b.message_id = m.message_id WHERE m.message_id = ?", id).one().n;
  }

  /** Every message to `reader` acked, through the message `id`. */
  async legAck(reader: number, id: string): Promise<void> {
    const { ledger } = this.#stores;
    await this.#gate.write(() => {
      ledger.read(ORIGINS[reader]!, 1);
      ledger.ack(ORIGINS[reader]!, ledger.seqOf(id)!);
    });
  }

  /**
   * The mail for the most records one merge takes with real messages (the memory research's
   * merge, round 5), in the counts `mergeMail` derives: the muse's acked mail interleaved
   * with Dot's and Pat's (two of Pat's to each), unread; then more of the muse's, acked, so
   * the hot set stays past half. Sealed, the muse's first 8 MiB span Dot's and Pat's keys;
   * Dot's, acked, merge into it (a piece of about 16 MiB); Pat's, acked, merge into that up to
   * `MOVE_BYTES`: the measured move. The last of Dot's and of Pat's messages' seqs.
   */
  legInterleaved(rooms: string[], mail: MergeMail): Promise<{ dot: number; pat: number }> {
    const { ledger } = this.#stores;
    const post = (room: string, cmid: string) =>
      ledger.seqOf(ledger.post(null, room, cmid, "x", null).message_id)!;
    return this.#gate.write(() => {
      let museLast = 0;
      let dotLast = 0;
      let patLast = 0;
      let c = 0;
      const ackMuse = () => {
        ledger.read(MUSE, 1);
        ledger.ack(MUSE, museLast);
      };
      for (let i = 0; i < mail.interleaved; i++) {
        museLast = post(rooms[0]!, `a${i}`);
        if (i < mail.dot) {
          dotLast = post(rooms[1]!, `b${i}`);
        }
        for (let k = 0; k < 2 && c < mail.pat; k++, c++) {
          patLast = post(rooms[2]!, `c${c}`);
        }
        if (i % MUSE_ACKS === MUSE_ACKS - 1) {
          ackMuse();
        }
      }
      ackMuse();
      for (let i = 0; i < mail.more; i++) {
        museLast = post(rooms[0]!, `d${i}`);
        if (i % MUSE_ACKS === MUSE_ACKS - 1) {
          ackMuse();
        }
      }
      ackMuse();
      return { dot: dotLast, pat: patLast };
    });
  }

  /** Every message to `reader` acked, through the seq `through`. */
  async legAckSeq(reader: number, through: number): Promise<void> {
    const { ledger } = this.#stores;
    await this.#gate.write(() => {
      ledger.read(ORIGINS[reader]!, 1);
      ledger.ack(ORIGINS[reader]!, through);
    });
  }

  /** An agent deleted, its mail purged from the archive by the lane's rewrites. */
  async legRemove(agent: string): Promise<void> {
    const { ledger } = this.#stores;
    await this.#gate.write(() => ledger.remove(agent));
  }

  /**
   * Whether the `archive` lane is idle: no pass under way (each pass marks itself in
   * `archive_fails` as it begins and clears it as it ends) and the lane's `next` none, so no
   * pass is due either: what a case waits for before the next case's setup drops the store.
   */
  legIdle(): boolean {
    const { ledger } = this.#stores;
    const { fails } = this.#gate.storage.sql.exec<{ fails: number }>(
      "SELECT archive_fails AS fails FROM ledger_usage").one();
    return fails === 0 && !(ledger.archiveDue() || ledger.purgedRooms().length > 0
                            || ledger.archiveCompactPending());
  }

  /** The archive as it stands: its segments and records, purges waiting, the lane's failures. */
  legArchived(): { segments: number; records: number; bytes: number; purges: number;
                    fails: number; error: string | null } {
    const sql = this.#gate.storage.sql;
    const one = (query: string) => sql.exec<{ n: number }>(query).one().n;
    const usage = sql.exec<{ fails: number; error: string | null }>(
      "SELECT archive_fails AS fails, archive_error AS error FROM ledger_usage").one();
    return { segments: one("SELECT COUNT(*) AS n FROM archive_segments"),
             records: one("SELECT coalesce(SUM(records), 0) AS n FROM archive_segments"),
             bytes: one("SELECT coalesce(SUM(bytes), 0) AS n FROM archive_segments"),
             purges: one("SELECT COUNT(*) AS n FROM archive_purges"), ...usage };
  }
}

const SIGNINS = Object.freeze(signinsOn(
  bundledSources(), Object.fromEntries(bundledSources().ids().map((id) => [id, true]))));

/** The owner's own binding, as the doors hand it. */
function owner(name: string): Binding {
  return { owner: name, caller: name, origin: null, tiers: TIERS, signins: SIGNINS } as
    unknown as Binding;
}

/** The doors' import binding for `name` (`bindImport`): the platform, a bundle, the tiers. */
function importing(name: string): Binding {
  return { owner: name, caller: PLATFORM_CALLER, origin: null,
           import: { bundle: "b".repeat(64) }, tiers: TIERS } as unknown as Binding;
}

/**
 * A pending owner's object holding one chunked file of `bytes` distinct letters, put through
 * the import's own doors (`owner/src/import.ts`), its chunks to the store as the doors put them.
 */
async function imported(env: LegEnv, name: string, seed: number, bytes: number): Promise<void> {
  const stub = env.OWNER.getByName(name);
  const data = letters(seed, bytes);
  const pieces = cut(data);
  const request = { path: "/workspace/imported/f.bin", size: data.byteLength, mode: 0 as const,
                    content_id: chunkedId(data.byteLength, pieces.map((piece) => piece.sha256)),
                    chunks: pieces.map((piece) => piece.sha256) };
  const begun = await stub.importFileBegin(importing(name), request);
  if (begun.skipped) {
    throw new Error("the leg's import found its file there already");
  }
  for (const piece of pieces) {
    // Its own bytes: a view of the file's buffer would carry the whole buffer over the RPC.
    await env.STORE.putChunks(begun.namespace, begun.key, [{ sha256: piece.sha256,
                                                             bytes: piece.bytes.slice() }]);
    await stub.importFileStored(importing(name), { intent_id: begun.intent_id,
                                                   chunks: [piece.sha256] });
  }
  await stub.importFileCommit(importing(name), { intent_id: begun.intent_id,
                                                 path: request.path, mode: 0,
                                                 content_id: request.content_id, mtime_ms: 0 });
}

/**
 * A fresh owner's object, admitted as sign-up admits it, once its seed of the starter package
 * has landed: its first door (`starterReceipts`) binds it and hands it the tiers, and its own
 * lanes seed it until its starter says the carried version is reconciled, so no case's setup
 * or run shares the isolate with the seed's uploads.
 */
async function object(env: LegEnv, name: string): Promise<DurableObjectStub<LegOwner>> {
  const stub = env.OWNER.getByName(name);
  await stub.admissionSet({ owner: name, caller: PLATFORM_CALLER, origin: null } as Binding,
                          { admit: true });
  for (let n = 0; n < LANE_LOOKS; n++) {
    const { version, carried } = await stub.starterReceipts(owner(name));
    if (version === carried) {
      return stub;
    }
    await scheduler.wait(LANE_POLL_MS);
  }
  throw new Error(`${name}'s seed of the starter package did not land`);
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
  /** An archive case's: its first agent's id, and the records its archive holds once done. */
  readonly agents?: string[];
  readonly want?: number;
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
    if (path === "check") {
      // The cutover's check of a pending owner (`importCheck`): its object is never admitted.
      await imported(env, name, LEG.runs * 100 + index, mib * MIB);
      LEG.ready.push({ name, who: importing(name) });
      continue;
    }
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
    } else if (path === "move") {
      // At the production limits: small acked messages past half the hot ceiling (and `mib`
      // thousand more), so the lane seals a tail of them: 8 MiB of records (8,489 of these),
      // its arrays near the doubling's worst (at 16,384).
      const agents = await stub.legAgents();
      await stub.legSmall(pairRoom(OWNER, agents[0]!), 0, 25_000 + mib * 1000);
      LEG.ready.push({ name, who, agents, want: 1 });
    } else if (path === "merge") {
      // At the production limits: the muse's sealed mail, Dot's merged into it, and Pat's
      // left for the run to ack: its merge is the measured move.
      // The counts come from one record of the mail's shape, measured on an object of its own.
      const sizing = await object(env, `${name}-sizing`);
      const [to] = await sizing.legAgents();
      const own = await sizing.legOverhead((await sizing.roomPost(owner(`${name}-sizing`), {
        room_id: pairRoom(OWNER, to!), client_message_id: "a0000", body: "x", parent_id: null }))
        .message_id) + 1 - RECORD_TRAILER_BYTES;
      const mail = mergeMail(own);
      const agents = await stub.legAgents();
      const lasts = await stub.legInterleaved(agents.map((agent) => pairRoom(OWNER, agent)),
                                              mail);
      await ring(stub, who, agents[0]!, "r0");
      await settled(stub, (seen) => seen.segments >= 1);
      const sealed = await stub.legArchived();
      if (sealed.segments !== 1) {
        throw new Error(`the muse's acked mail is not one seal: ${JSON.stringify(sealed)}`);
      }
      await stub.legAckSeq(1, lasts.dot);
      await ring(stub, who, agents[0]!, "r1");
      await settled(stub, (seen) => seen.records >= sealed.records + mail.dot);
      // Its passes all over before the run: none left to run inside the measured move.
      await idle(stub);
      const merged = await stub.legArchived();
      if (merged.segments !== 1) {
        throw new Error(`Dot's mail did not merge into the muse's seal: ${
          JSON.stringify(merged)}`);
      }
      LEG.ready.push({ name, who, agents, want: merged.records + mail.take(merged.bytes),
                       offset: lasts.pat });
    } else if (path === "bodies" || path === "rewrite") {
      // At the production limits: two acked bodies, the owner's to the muse and to Dot, and
      // the hot mail past half its ceiling with unread small mail, so the lane seals the
      // bodies and nothing else, as production seals them (`archivePick`): the muse's body the
      // smaller of `mib` MiB and the largest that does not seal alone, then Dot's at `mib`, so
      // the two are one segment. At 4 MiB they seal together; at 8 MiB, the body cap, they are
      // the largest segment production makes, about 16 MiB of two records. The move moves it;
      // the rewrite's setup moves it, then deletes Dot.
      const agents = await stub.legAgents();
      const post = async (on: DurableObjectStub<LegOwner>, k: number, bytes: number,
                          as = who, to = agents) =>
        (await on.roomPost(as, { room_id: pairRoom(OWNER, to[k]!), client_message_id: `b${k}`,
                                  body: new TextDecoder().decode(letters(seed * 10 + k, bytes)),
                                  parent_id: null })).message_id;
      // A record's own bytes past its body's, read from a message of the same shape on an
      // object of its own. Its three rows each write its post's `clock()`, whole seconds to
      // three decimals, so another post's record is up to `TIMES_SLACK` longer: the largest
      // body that does not seal alone is then at most that and a byte short of the seal.
      const sizing = await object(env, `${name}-sizing`);
      const its = await sizing.legAgents();
      const overhead = await sizing.legOverhead(
        await post(sizing, 0, SEGMENT_MIN_BYTES / 2, owner(`${name}-sizing`), its));
      const kept = Math.min(mib * MIB, SEGMENT_MIN_BYTES - 1 - TIMES_SLACK
                                         - (overhead - RECORD_TRAILER_BYTES));
      await stub.legAck(0, await post(stub, 0, kept));
      await stub.legAck(1, await post(stub, 1, mib * MIB));
      // Each ring is one more unread message (the run rings once, the rewrite's setup once
      // before it): with them, the hot mail stays past half its ceiling through the last
      // seal, and the unread within `maxUnacked`.
      const rings = path === "rewrite" ? 2 : 1;
      await stub.legUnread(pairRoom(OWNER, agents[0]!), LEDGER_LIMITS.maxMessages / 2 - rings);
      if (path === "rewrite") {
        await ring(stub, who, agents[0]!, "r0");
        await settled(stub, (seen) => seen.segments >= 1);
        await idle(stub);
        const seen = await stub.legArchived();
        if (seen.segments !== 1 || seen.records !== 2) {
          throw new Error(`the rewrite's two bodies are not one segment of two records: ${
            JSON.stringify(seen)} (the seal rule changed?)`);
        }
        await stub.legRemove(agents[1]!);
      }
      LEG.ready.push({ name, who, agents, want: path === "rewrite" ? 0 : 2 });
    } else if (path === "fetch" || path === "window") {
      LEG.ready.push({ name, who, content: await stub.legPlace(seed, mib * MIB),
                       offset: Math.floor(mib * MIB / 2) });
    } else {
      LEG.ready.push({ name, who });
    }
  }
}

/** An owner's post to the agent `agent`: its door syncs the lanes, so the archive's runs. */
async function ring(stub: DurableObjectStub<LegOwner>, who: Binding, agent: string,
                    cmid: string): Promise<void> {
  await stub.roomPost(who, { room_id: pairRoom(OWNER, agent), client_message_id: cmid,
                             body: "ring", parent_id: null });
}

/**
 * The most two posts' records of one shape differ by: each of a record's three rows (the
 * message, its body, its recipient) writes the post's `clock()`, `Date.now() / 1000`, as
 * whole seconds up to three decimals, so 4 characters a row (`".123"`).
 */
const TIMES_SLACK = 3 * ".123".length;

/**
 * The most the merge case's seqs widen a record past the measured one's (its object's first,
 * one digit): the case posts under `10 ** MAIL_SEQ_DIGITS` messages, and two of a record's
 * rows (the message, its recipient) carry its seq.
 */
const MAIL_SEQ_DIGITS = 5;
const SEQ_SLACK = 2 * (MAIL_SEQ_DIGITS - 1);

/** The merge case's muse acks its mail each this many of its messages, so as many wait unread. */
const MUSE_ACKS = 50;

/** The merge case's mail (`mergeMail`). */
interface MergeMail {
  /** The muse's messages interleaved with Dot's and Pat's, then the muse's after them. */
  readonly interleaved: number;
  readonly more: number;
  readonly dot: number;
  readonly pat: number;
  /** The fewest of Pat's records the run's merge takes into the one segment, of `bytes`. */
  readonly take: (bytes: number) => number;
}

/**
 * The merge case's counts, from one record of its mail's shape (`own`, the bytes a seal
 * counts it at) and the ledger's ceilings, so a change to a record's size or a ceiling moves
 * them with it. A seal holds `fewest` to `most` such records (each post's times, a client
 * message id up to three characters shorter or longer than the measured one's, and a seq up
 * to `SEQ_SLACK` wider). The rings are the muse's three unread messages, the run's among them.
 */
function mergeMail(own: number): MergeMail {
  const widest = own + TIMES_SLACK + 3 + SEQ_SLACK;
  const fewest = Math.floor(SEGMENT_MIN_BYTES / widest);
  const most = Math.ceil(SEGMENT_MIN_BYTES / (own - TIMES_SLACK - 3));
  const rings = 3;
  // The muse's acked mail is one seal, never two: under two of the fewest, less a fiftieth.
  const muse = 2 * fewest - Math.ceil(fewest / 50);
  // What stays hot of it once sealed, and Pat's beside it, keep the hot mail past half into
  // the run; the unread (Dot's, Pat's, the muse's not yet acked and the rings) stay within
  // `maxUnacked`.
  const pat = LEDGER_LIMITS.maxMessages / 2 - (muse - most) + 1;
  const dot = LEDGER_LIMITS.maxUnacked - pat - MUSE_ACKS - rings;
  // Every one of Dot's and Pat's interleaved, and the seal taken from the interleaved mail.
  const interleaved = Math.max(most, Math.ceil(pat / 2), dot);
  if (muse - interleaved < 0 || muse - most + dot < most
      || muse + dot + pat + rings >= 10 ** MAIL_SEQ_DIGITS) {
    throw new Error(`the merge case's counts do not hold at ${own} bytes a record: ${
      JSON.stringify({ fewest, most, muse, pat, dot, interleaved })}`);
  }
  // The run's merge takes Pat's records within the segment's keys (the muse's first `fewest`
  // at least, two of Pat's after each but the last) while they fit `MOVE_BYTES` beside the
  // segment's bytes.
  return { interleaved, more: muse - interleaved, dot, pat,
           take: (bytes) => Math.min(pat, 2 * (fewest - 1),
                                     Math.floor((MOVE_BYTES - bytes) / widest)) };
}

/** How often a case looks at its archive object's lane, and how many looks it waits at most. */
const LANE_POLL_MS = 10;
const LANE_LOOKS = 12_000;

/** Until the lane's passes stand as `done` says (its own alarm runs them), or loudly not. */
async function settled(stub: DurableObjectStub<LegOwner>,
                       done: (seen: { segments: number; records: number;
                                  purges: number }) => boolean):
    Promise<void> {
  for (let n = 0; n < LANE_LOOKS; n++) {
    const seen = await stub.legArchived();
    if (seen.error !== null) {
      throw new Error(`the archive lane's pass failed: ${seen.error}`);
    }
    if (seen.fails === 0 && done(seen)) {
      return;
    }
    await scheduler.wait(LANE_POLL_MS);
  }
  throw new Error(`the archive lane did not settle: ${JSON.stringify(
    await stub.legArchived())}`);
}

/**
 * Until the lane is idle (`legIdle`), or loudly not: a case's passes all end inside its own
 * run. A look between two passes (one ended, the next not yet begun: `bodies 8` takes two)
 * sees `settled`'s condition already held; returning then, the next case's setup would drop
 * the store under the second pass.
 */
async function idle(stub: DurableObjectStub<LegOwner>): Promise<void> {
  for (let n = 0; n < LANE_LOOKS; n++) {
    if (await stub.legIdle()) {
      return;
    }
    await scheduler.wait(LANE_POLL_MS);
  }
  throw new Error(`the archive lane did not go idle: ${JSON.stringify(
    await stub.legArchived())}`);
}

/**
 * Each hold's end, before its release, as a checkpoint (`SlotPool.observe`). Made outside
 * `run`, so the pool, which keeps it until the next run, holds `env` alone: a closure made in
 * `run` shares its one context, and kept this case's appended text live into the next
 * case's floor.
 */
function observer(env: LegEnv): (units: number) => Promise<void> {
  return (units) => owned(env, `end-of-${units}`);
}

async function run(env: LegEnv, path: string, mib: number, timed: boolean):
    Promise<number[]> {
  TIMED.on = timed;
  holds.ownerSlots().observe = observer(env);
  await env.STORE.timed(timed);
  const appended = LEG.appended;
  return Promise.all(LEG.ready.map(async ({ name, who, etag, content, offset, agents,
                                          want }) => {
    const stub = env.OWNER.getByName(name);
    try {
      // Every case ready and nothing of its path begun: the driver reads its floor here too.
      await owned(env, "start");
      if (path === "records") {
        return await moveRecords(env, MOVE_RECORDS);
      }
      if (path === "burst") {
        return await stub.legBurst();
      }
      if (path === "ship") {
        return await stub.legShip();
      }
      if (path === "merge") {
        await stub.legAckSeq(2, offset!);
        await ring(stub, who, agents![0]!, "r2");
        await settled(stub, (seen) => seen.records >= want!);
        await idle(stub);
        return (await stub.legArchived()).records;
      }
      if (path === "move" || path === "bodies" || path === "rewrite") {
        await ring(stub, who, agents![0]!, "ring");
        await settled(stub, path === "rewrite" ? (seen) => seen.purges === 0
          : (seen) => seen.records >= want!);
        await idle(stub);
        const seen = await stub.legArchived();
        if (path === "bodies" && (seen.segments !== 1 || seen.records !== 2)) {
          throw new Error(`the move's two bodies are not one segment of two records: ${
            JSON.stringify(seen)} (the seal rule changed?)`);
        }
        return seen.records;
      }
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
      if (path === "check") {
        // Every chunk checked through the store, a page at a time: how many were stored.
        let proved = 0;
        for (let page = 0; ; page += 1) {
          const read = await stub.importCheck(who, { part: "chunks", page });
          if (read.part !== "chunks") {
            throw new Error("the check answered another part");
          }
          proved += read.chunks.filter((chunk) => chunk.read === chunk.sha256).length;
          if (!read.more) {
            return proved;
          }
        }
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
                             body_units: [1, 2].map((chunks) => holds.bodyUnits(chunks)),
                             move_seal_units: holds.moveTableUnits(25_000),
                             move_most_units: holds.moveTableUnits(MOVE_RECORDS),
                             move_records: MOVE_RECORDS,
                             move_body_units: holds.moveTableUnits(2) });
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
