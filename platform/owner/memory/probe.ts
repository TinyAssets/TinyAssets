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
 * - **The archive's objects** (`LegArchive` at the production ledger limits, `LegSegments`
 *   at the archive tests' small ones) run the `archive` lane's real move and rewrite: the
 *   mail set up in their own ledger (bodies placed as a post places them), the lane rung by
 *   an owner's post, its pass run by the object's own alarm.
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
import { PLATFORM_CALLER } from "../src/admission";
import { Backups } from "../src/backup";
import { Bodies, type Located } from "../src/bodies";
import { ChangeLog } from "../src/changelog";
import { Grants } from "../src/grants";
import { Ledger, LEDGER_LIMITS, type LedgerLimits } from "../src/ledger";
import type { Binding, Origin } from "../../contract/src/binding";
import { type ChunkStore, Files } from "../src/files";
import { WriteGate } from "../src/gate";
import * as holds from "../src/holds";
import { Refusal } from "../../contract/src/refusal";
import { CHUNK_BYTES, SLOT_WAIT_MS } from "../../contract/src/limits";
import { FILE_DEFAULTS } from "../../contract/src/file-defaults";
import { EDIT_BYTES_MAX, OwnerDO, type OwnerEnv } from "../src/owner-do";
import { PAGE_BYTES } from "../src/pages";
import { Policy } from "../src/policy";
import { MIGRATIONS } from "../src/schema";
import {
  MIN_RECORD_BYTES, MOVE_RECORDS, MoveTable, pack, planSegment, SEGMENT_LEAST_BYTES,
} from "../src/segments";
import { UNIT_BYTES } from "../../contract/src/slots";

declare function gc(): void;

interface LegEnv extends OwnerEnv {
  readonly OWNER: DurableObjectNamespace<LegOwner>;
  readonly ARCHIVE: DurableObjectNamespace<LegArchive>;
  readonly SEGMENTS: DurableObjectNamespace<LegSegments>;
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

/** The real owner object, with the body paths and the control in reach. */
export class LegOwner extends OwnerDO {
  readonly #leg: LegEnv;
  readonly #gate: WriteGate;
  readonly #bodies: Bodies;
  readonly #files: Files;

  constructor(state: DurableObjectState, env: LegEnv) {
    const files = received(env);
    super(state, { ...env, FILES: files });
    this.#leg = env;
    this.#gate = new WriteGate(state.storage);
    const bodyFiles = new Files(this.#gate.storage, files,
                            () => ({ ...FILE_DEFAULTS, max_bytes: 25_000_000_000 }),
                            this.#gate.write, () => new Refusal("storage_full"));
    this.#bodies = new Bodies(bodyFiles, this.#gate.storage, this.#gate.write);
    this.#files = bodyFiles;
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
    const store = {
      putManifestPart: async (_ns: string, _key: string, _gen: string, _part: number,
                              bytes: Uint8Array) => {
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
      putManifestSegment: async (_ns: string, _key: string, _gen: string, _a: number,
                                 _b: number, bytes: Uint8Array) => {
        widest = Math.max(widest, bytes.byteLength);
        return put(bytes);
      },
      putManifestRoot: async () => {},
      getManifestRoot: async () => null,
      deleteManifest: async () => {},
    } as unknown as ChunkStore;
    const lane = new Backups({
      storage: this.#gate.storage, write: (unit) => this.#gate.quiet(unit), store,
      log: new ChangeLog(this.#gate.storage), space: () => this.#files.space(),
      owner: () => "owner", serves: () => true, schema: MIGRATIONS.length,
      halted: () => false, notice: () => {},
    });
    await this.#gate.write(() => sql.exec("UPDATE owner_backup_state SET changed = 1"));
    await this.#gate.quiet(() => lane.forExport(Date.now() / 1000));
    await lane.run(Date.now() / 1000, Date.now() + 120_000);
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
    const store = {
      putManifestPart: async () => {
        throw new Error("the ship case begins no burst");
      },
      putManifestSegment: async (ns: string, key: string, gen: string, a: number,
                                 b: number, bytes: Uint8Array) => {
        segments += 1;
        widest = Math.max(widest, bytes.byteLength);
        await owned(this.#leg, "ship-segment");
        return this.#leg.FILES.putManifestSegment(ns, key, gen, a, b, bytes);
      },
      putManifestRoot: async () => {},
      getManifestRoot: async () => null,
      deleteManifest: async () => {},
    } as unknown as ChunkStore;
    const lane = new Backups({
      storage: this.#gate.storage, write: (unit) => this.#gate.quiet(unit), store,
      log: new ChangeLog(this.#gate.storage), space: () => this.#files.space(),
      owner: () => "owner", serves: () => true, schema: MIGRATIONS.length,
      halted: () => segments >= 2, notice: () => {},
    });
    await this.#gate.write(() => sql.exec("UPDATE owner_backup_state SET changed = 1"));
    await this.#gate.quiet(() => lane.forExport(Date.now() / 1000));
    await this.#gate.write(() => {
      sql.exec(WIDEN);  // the widest row first: the first segment holds it and the rest
      sql.exec("UPDATE owner_notices SET detail = printf('%.999c', 'b') || char(8364) " +
               "WHERE seq <= 4200");
    });
    await lane.run(Date.now() / 1000, Date.now() + 120_000);
    if (segments < 2) {
      throw new Error(`the ship case shipped ${segments} segments, not a full one and more`);
    }
    return widest;
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

const MUSE: Origin = Object.freeze({ client: "a".repeat(64), family: "b".repeat(64) });
const DOT: Origin = Object.freeze({ client: "c".repeat(64), family: "d".repeat(64) });
const PAT: Origin = Object.freeze({ client: "e".repeat(64), family: "f".repeat(64) });
const ORIGINS = [MUSE, DOT, PAT];

/**
 * An owner object whose `archive` lane the leg runs: the real `OwnerDO` at `limits`, its
 * mail set up in its own ledger, outside any measurement.
 */
class ArchiveObject extends OwnerDO {
  readonly #state: DurableObjectState;
  readonly #limits: LedgerLimits;

  constructor(state: DurableObjectState, env: LegEnv, limits: LedgerLimits) {
    super(state, { ...env, FILES: received(env) }, { ledgerLimits: limits, starter: null });
    this.#state = state;
    this.#limits = limits;
  }

  #ledger(): Ledger {
    const storage = this.#state.storage;
    return new Ledger(storage, new Grants(storage, new Policy(storage)), this.#limits);
  }

  /** Agents registered as their own clients file them: each's id. */
  legAgents(): string[] {
    const storage = this.#state.storage;
    const grants = new Grants(storage, new Policy(storage));
    const ledger = new Ledger(storage, grants, this.#limits);
    return ORIGINS.map((origin, n) => {
      grants.admit(origin.client, "mcp", null, Math.floor(Date.now() / 1000));
      return ledger.register(origin, { kind: "mcp", ref: `conn-${n}` }, {
        command_center_id: "alpha", display_name: ["Muse", "Dot", "Pat"][n]!,
        delivery_method: "pull", recipe_id: null }).agent_id;
    });
  }

  /** `count` small messages from the owner to `room`'s agent (`reader`), each acked. */
  legSmall(room: string, reader: number, count: number): number {
    const ledger = this.#ledger();
    const origin = ORIGINS[reader]!;
    let last = 0;
    for (let n = 0; n < count; n++) {
      last = ledger.seqOf(ledger.post(null, room, `s${n}`, "x", null).message_id)!;
      if (n % 1000 === 999 || n === count - 1) {
        ledger.read(origin, 1);
        ledger.ack(origin, last);
      }
    }
    return last;
  }

  /** Every message to `reader` acked, through the message `id`. */
  legAck(reader: number, id: string): void {
    const ledger = this.#ledger();
    const origin = ORIGINS[reader]!;
    ledger.read(origin, 1);
    ledger.ack(origin, ledger.seqOf(id)!);
  }

  /**
   * The mail for the most records one merge takes with real messages (the memory research's
   * merge, round 5): the muse's acked mail interleaved with Dot's and Pat's (two of Pat's to
   * each), unread; then more of the muse's, acked, so the hot set stays past half. Sealed,
   * the muse's first 8 MiB span Dot's and Pat's keys; Dot's, acked, merge into it (a piece of
   * about 16 MiB); Pat's, acked, merge into that up to `MOVE_BYTES`: the measured move. The
   * last of Dot's and of Pat's messages' seqs.
   */
  legInterleaved(rooms: string[]): { dot: number; pat: number } {
    const ledger = this.#ledger();
    const post = (room: string, cmid: string) =>
      ledger.seqOf(ledger.post(null, room, cmid, "x", null).message_id)!;
    let museLast = 0;
    let dotLast = 0;
    let patLast = 0;
    let c = 0;
    const ackMuse = () => {
      ledger.read(MUSE, 1);
      ledger.ack(MUSE, museLast);
    };
    for (let i = 0; i < 8_600; i++) {
      museLast = post(rooms[0]!, `a${i}`);
      if (i < 7_700) {
        dotLast = post(rooms[1]!, `b${i}`);
      }
      for (let k = 0; k < 2 && c < 16_900; k++, c++) {
        patLast = post(rooms[2]!, `c${c}`);
      }
      if (i % 50 === 49) {
        ackMuse();
      }
    }
    ackMuse();
    for (let i = 0; i < 8_050; i++) {
      museLast = post(rooms[0]!, `d${i}`);
      if (i % 50 === 49) {
        ackMuse();
      }
    }
    ackMuse();
    return { dot: dotLast, pat: patLast };
  }

  /** Every message to `reader` acked, through the seq `through`. */
  legAckSeq(reader: number, through: number): void {
    const ledger = this.#ledger();
    ledger.read(ORIGINS[reader]!, 1);
    ledger.ack(ORIGINS[reader]!, through);
  }

  /** An agent deleted, its mail purged from the archive by the lane's rewrites. */
  legRemove(agent: string): void {
    this.#ledger().remove(agent);
  }

  /** The archive as it stands: its segments and records, purges waiting, the lane's failures. */
  legArchived(): { segments: number; records: number; purges: number; fails: number;
                    error: string | null } {
    const sql = this.#state.storage.sql;
    const one = (query: string) => sql.exec<{ n: number }>(query).one().n;
    const usage = sql.exec<{ fails: number; error: string | null }>(
      "SELECT archive_fails AS fails, archive_error AS error FROM ledger_usage").one();
    return { segments: one("SELECT COUNT(*) AS n FROM archive_segments"),
             records: one("SELECT coalesce(SUM(records), 0) AS n FROM archive_segments"),
             purges: one("SELECT COUNT(*) AS n FROM archive_purges"), ...usage };
  }
}

/** At the production ledger limits: a tail seal of small messages. */
export class LegArchive extends ArchiveObject {
  constructor(state: DurableObjectState, env: LegEnv) {
    super(state, env, LEDGER_LIMITS);
  }
}

/** At the archive tests' small limits: a segment sealed at two records, large bodies. */
export class LegSegments extends ArchiveObject {
  constructor(state: DurableObjectState, env: LegEnv) {
    super(state, env, { ...LEDGER_LIMITS, maxMessages: 8, maxUnacked: 4, archiveRecords: 2 });
  }
}

const SIGNINS = Object.freeze(signinsOn(
  bundledSources(), Object.fromEntries(bundledSources().ids().map((id) => [id, true]))));

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
  /** An archive case's: its first agent's id, and the segments its lane stands at once done. */
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
      const stub = await archiveObject(env.ARCHIVE, name);
      const agents = await stub.legAgents();
      await stub.legSmall(`dm:${agents[0]}`, 0, 25_000 + mib * 1000);
      LEG.ready.push({ name, who, agents, want: 1 });
    } else if (path === "merge") {
      // At the production limits: the muse's sealed mail, Dot's merged into it, and Pat's
      // left for the run to ack: its merge is the measured move.
      const stub = await archiveObject(env.ARCHIVE, name);
      const agents = await stub.legAgents();
      const lasts = await stub.legInterleaved(agents.map((agent) => `dm:${agent}`));
      await ring(stub, who, agents[0]!, "r0");
      await settled(stub, (seen) => seen.segments >= 1);
      const sealed = (await stub.legArchived()).records;
      await stub.legAckSeq(1, lasts.dot);
      await ring(stub, who, agents[0]!, "r1");
      await settled(stub, (seen) => seen.records >= sealed + 7_700);
      LEG.ready.push({ name, who, agents, want: (await stub.legArchived()).records + 10_000,
                       offset: lasts.pat });
    } else if (path === "bodies" || path === "rewrite") {
      // At the small limits: two acked bodies of `mib` MiB each, the owner's to the muse and
      // to Dot, sealed together; for the rewrite, the lane has sealed them and Dot is deleted.
      const stub = await archiveObject(env.SEGMENTS, name);
      const agents = await stub.legAgents();
      const body = (k: number) => new TextDecoder().decode(letters(seed * 10 + k, mib * MIB));
      const one = await stub.roomPost(who, { room_id: `dm:${agents[0]}`,
                                             client_message_id: "b0", body: body(0),
                                             parent_id: null });
      const two = await stub.roomPost(who, { room_id: `dm:${agents[1]}`,
                                             client_message_id: "b1", body: body(1),
                                             parent_id: null });
      await stub.legAck(0, one.message_id);
      await stub.legAck(1, two.message_id);
      // Past half the hot ceiling with small acked mail, so the two are due to move.
      await stub.legSmall(`dm:${agents[0]}`, 0, 3);
      if (path === "rewrite") {
        await ring(stub, who, agents[0]!, "r0");
        await settled(stub, (seen) => seen.segments >= 1);
        await stub.legRemove(agents[1]!);
      }
      LEG.ready.push({ name, who, agents, want: path === "rewrite" ? 0 : 1 });
    } else if (path === "fetch" || path === "window") {
      LEG.ready.push({ name, who, content: await stub.legPlace(seed, mib * MIB),
                       offset: Math.floor(mib * MIB / 2) });
    } else {
      LEG.ready.push({ name, who });
    }
  }
}

/** An archive case's object, admitted as sign-up admits it. */
async function archiveObject<T extends ArchiveObject>(namespace: DurableObjectNamespace<T>,
                                                     name: string):
    Promise<DurableObjectStub<T>> {
  const stub = namespace.getByName(name);
  await stub.admissionSet({ owner: name, caller: PLATFORM_CALLER, origin: null } as Binding,
                          { admit: true });
  return stub;
}

/** An owner's post to the agent `agent`: its door syncs the lanes, so the archive's runs. */
async function ring(stub: DurableObjectStub<ArchiveObject>, who: Binding, agent: string,
                    cmid: string): Promise<void> {
  await stub.roomPost(who, { room_id: `dm:${agent}`, client_message_id: cmid, body: "ring",
                             parent_id: null });
}

/** Until the lane's passes stand as `done` says (its own alarm runs them), or loudly not. */
async function settled(stub: DurableObjectStub<ArchiveObject>,
                       done: (seen: { segments: number; records: number;
                                  purges: number }) => boolean):
    Promise<void> {
  for (let n = 0; n < 1200; n++) {
    const seen = await stub.legArchived();
    if (seen.error !== null) {
      throw new Error(`the archive lane's pass failed: ${seen.error}`);
    }
    if (seen.fails === 0 && done(seen)) {
      return;
    }
    await scheduler.wait(100);
  }
  throw new Error(`the archive lane did not settle: ${JSON.stringify(
    await stub.legArchived())}`);
}

async function run(env: LegEnv, path: string, mib: number, timed: boolean):
    Promise<number[]> {
  TIMED.on = timed;
  // Each hold's end, before its release, is a checkpoint too (`SlotPool.observe`).
  holds.ownerSlots().observe = (units) => owned(env, `end-of-${units}`);
  await env.STORE.timed(timed);
  const appended = LEG.appended;
  return Promise.all(LEG.ready.map(async ({ name, who, etag, content, offset, agents,
                                          want }) => {
    const stub = env.OWNER.getByName(name);
    try {
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
        const archive = env.ARCHIVE.getByName(name);
        await archive.legAckSeq(2, offset!);
        await ring(archive, who, agents![0]!, "r2");
        await settled(archive, (seen) => seen.records >= want!);
        return (await archive.legArchived()).records;
      }
      if (path === "move" || path === "bodies" || path === "rewrite") {
        const archive = path === "move" ? env.ARCHIVE.getByName(name)
          : env.SEGMENTS.getByName(name);
        await ring(archive, who, agents![0]!, "ring");
        await settled(archive, path === "rewrite" ? (seen) => seen.purges === 0
          : (seen) => seen.segments >= want!);
        return (await archive.legArchived()).records;
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
