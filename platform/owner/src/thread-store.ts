/**
 * One agent thread's store (ADR-032 §4, amended 2026-10-30; tasks 5, S1): pi's own SQLite
 * storage, in a Durable Object of its own, which the owner's object reaches over RPC.
 *
 * Each agent thread is one pi Session (`thread-host.ts`), and its bytes live here, never in
 * the owner's database: pi's `SqliteStorage` over this object's own `ctx.storage`, under
 * pi's `pi_` prefix, migrated by pi itself on first use. The harness, every hook, the
 * environment, the model provider and every authority check stay in the owner's object;
 * only `Storage`'s calls cross (`remote-storage.ts`). `ThreadStore` is a second class in the
 * owner Worker, so the Session and its storage always run one pi version, from one bundle,
 * and one deploy resets both together.
 *
 * Isolation: the owner Worker binds this class as `PISTORE`, its only Durable Object
 * namespace, and names a store only by the random 128-bit id its own thread row holds
 * (`threads.ts`), as it names its files by a namespace only it holds. The class reads no
 * `env` binding (a guard holds it) and reaches nothing: it serves pi's records to the
 * object that names it, and nothing else.
 *
 * - **Holder fence.** `open(holder)` makes the caller the store's one holder: a Session
 *   opens with a fresh random holder, and every later call names it. A call naming any
 *   other holder is refused (`STALE_HOLDER`), so a Session an owner's newer instance
 *   replaced (an owner evicted mid-commit, or a Session failed and reopened) can neither
 *   write nor read here again: its late commit fails rather than interleaves (pi-durable
 *   README: "one process owns a storage at a time"). The holder is durable (key-value
 *   storage), so the store's own eviction between calls is invisible: the next call from
 *   the holder opens pi's storage again over what was committed. A store never opened
 *   refuses every call but `open`, and makes no table.
 * - **Requests.** A read pi rejects as an invalid request (`StorageRequestError`, which
 *   fails only that read) is answered as `{ requestError }`, since RPC keeps no error
 *   class; any other failure is thrown, and fails the Session (pi: "an error a method
 *   throws is final").
 * - **`size()`** is the store's database size in bytes (its 10 GB is its own: a full store
 *   fails only its own thread).
 * - **`destroy()`** wipes the store whole (`deleteAll()`, atomic) and leaves only a mark
 *   that it was destroyed, so every later call, `open` included, is refused: a destroyed
 *   store's Session can never be opened again, and a late call from a closed one never
 *   resurrects it. Names are never reused, so nothing needs it back. Its caller (the drop
 *   and erasure paths, S4) names the store in a row before it is made and wipes it before
 *   that row is forgotten, since a namespace's objects cannot be listed.
 */
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type Seq, type Storage, StorageRequestError, type StorageWrite,
} from "@earendil-works/pi-durable";
import {
  openDurableObjectSqliteStorage,
} from "@earendil-works/pi-durable/storage/sqlite/cloudflare";
import { DurableObject } from "cloudflare:workers";

/** pi's table prefix in a store: its own default, alone in the database. */
export const STORE_PREFIX = "pi_";
/** The refusals, each fixed. */
export const STALE_HOLDER = "a stale holder: a newer Session holds this store";
export const NOT_OPENED = "this store was never opened";
export const DESTROYED = "this store was destroyed";
/** A holder: 128 random bits, as hex. */
const HOLDER = /^[0-9a-f]{32}$/;
/** The durable keys: the holder, and the mark a destroyed store keeps. */
const HOLDER_KEY = "ta:holder";
const DESTROYED_KEY = "ta:destroyed";

/** The reads of pi's `Storage` the store serves, each by its name. */
export const READS = Object.freeze([
  "conversation", "scanConversations", "entry", "findLatestHeadMarker", "scanEntries", "task",
  "scanTasks", "submission", "scanSubmissions", "submissionByRequest", "findDocument",
  "document", "scanDocuments",
] as const satisfies readonly (keyof Storage)[]);
export type Read = (typeof READS)[number];

/** A read's answer: its value, or the request error pi raised for it. */
export type Reply<T = unknown> = { readonly value: T } | { readonly requestError: string };

/** What the owner's object calls on a store (`ThreadStore`'s RPC surface). */
export interface StoreRpc {
  open(holder: string): Promise<number>;
  commit(holder: string, writes: readonly StorageWrite[]): Promise<Seq>;
  read(holder: string, method: Read, args: readonly unknown[]): Promise<Reply>;
  size(): Promise<number> | number;
  destroy(): Promise<void>;
}

type Sqlite = Awaited<ReturnType<typeof openDurableObjectSqliteStorage>>;

export class ThreadStore extends DurableObject implements StoreRpc {
  /** pi's storage over this object's database, opened at the holder's first call. */
  #storage: Promise<Sqlite> | undefined;

  /**
   * Make `holder` this store's one holder, opening pi's storage (and making its tables on
   * first use); answer the next id the store would mint, which seeds the holder's own
   * minting (`RemoteStorage.mintId`). Refused once destroyed.
   */
  async open(holder: string): Promise<number> {
    if (typeof holder !== "string" || !HOLDER.test(holder)) {
      throw new Error("a holder is 128 random bits, as hex");
    }
    this.#alive();
    this.ctx.storage.kv.put(HOLDER_KEY, holder);
    return Number(await (await this.#pi()).mintId());
  }

  /** pi's `commit`, for the holder only. */
  async commit(holder: string, writes: readonly StorageWrite[]): Promise<Seq> {
    const storage = await this.#held(holder);
    return storage.commit(writes, BACKGROUND_CONTEXT);
  }

  /** One of pi's reads (`READS`), for the holder only; an invalid request is answered. */
  async read(holder: string, method: Read, args: readonly unknown[]): Promise<Reply> {
    if (!READS.includes(method) || !Array.isArray(args)) {
      throw new Error("not a read of pi's storage");
    }
    const pi = await this.#held(holder);
    const call = pi[method] as (...given: unknown[]) => Promise<unknown>;
    try {
      return { value: await call.apply(pi, [...args, BACKGROUND_CONTEXT]) };
    } catch (error) {
      if (error instanceof StorageRequestError) {
        return { requestError: error.message };
      }
      throw error;
    }
  }

  /** The store's database size in bytes. */
  size(): number {
    return this.ctx.storage.sql.databaseSize;
  }

  /**
   * Wipe the store whole, then mark it destroyed: every later call is refused. Idempotent,
   * so an interrupted destroy is simply run again.
   */
  async destroy(): Promise<void> {
    const open = this.#storage;
    this.#storage = undefined;
    await (await open?.catch(() => undefined))?.close(BACKGROUND_CONTEXT).catch(() => {});
    await this.ctx.storage.deleteAll();
    this.ctx.storage.kv.put(DESTROYED_KEY, true);
  }

  #alive(): void {
    if (this.ctx.storage.kv.get(DESTROYED_KEY) !== undefined) {
      throw new Error(DESTROYED);
    }
  }

  /** pi's storage, for `holder` only: the store's holder, as stored. */
  async #held(holder: string): Promise<Sqlite> {
    this.#alive();
    const current = this.ctx.storage.kv.get<string>(HOLDER_KEY);
    if (current === undefined) {
      throw new Error(NOT_OPENED);
    }
    if (holder !== current) {
      throw new Error(STALE_HOLDER);
    }
    return this.#pi();
  }

  #pi(): Promise<Sqlite> {
    if (this.#storage === undefined) {
      const opening = openDurableObjectSqliteStorage(this.ctx.storage, { prefix: STORE_PREFIX });
      opening.catch(() => {
        if (this.#storage === opening) {
          this.#storage = undefined;
        }
      });
      this.#storage = opening;
    }
    return this.#storage;
  }
}
