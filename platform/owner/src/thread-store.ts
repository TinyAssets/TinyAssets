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
 * - **One session per open Session.** `open(holder)` makes the caller the store's one
 *   holder and answers a `StoreSession`, an `RpcTarget` whose calls carry pi's commits and
 *   reads: calls on one session are billed as one store request, not one each (live probe
 *   P7: 200 calls billed 2 requests, against 202 per call), and holding it keeps the store
 *   in memory while its thread's Session is open; the owner releases it when the Session
 *   closes, and the store may hibernate. `attach(holder)` answers a new session for the
 *   holder that already holds the store (a reconnect after the old stub broke), never
 *   claiming it.
 * - **Holder fence.** Every session call checks that its holder is still the store's
 *   holder, as stored. Once a newer Session opened the store (an owner evicted mid-commit,
 *   or a Session failed and reopened), an older one's calls are refused (`STALE_HOLDER`):
 *   its late commit fails rather than interleaves (pi-durable README: "one process owns a
 *   storage at a time"). The holder is durable (key-value storage), so the store's own
 *   eviction is invisible past a reconnect: pi's storage opens again over what was
 *   committed. A store never opened refuses every call but `open`, and makes no table.
 * - **Requests.** A read pi rejects as an invalid request (`StorageRequestError`, which
 *   fails only that read) is answered as `{ requestError }`, since RPC keeps no error
 *   class; any other failure is thrown, and fails the Session (pi: "an error a method
 *   throws is final").
 * - **`answer(request)`** is a pure read for the owner's status of a settled run: how the
 *   root's input with that request id stands, and its answer's whole text once done. It
 *   claims no holder and opens no Session; a store never opened refuses it (`NOT_OPENED`),
 *   so it never makes one.
 * - **`size()`** is the store's database size in bytes (its 10 GB is its own: a full store
 *   fails only its own thread).
 * - **`destroy()`** wipes the store whole (`deleteAll()`, atomic), leaving nothing: an
 *   empty object, which should bill nothing it stores (live probe P8, still owed) and which
 *   refuses every call but `open` (`NOT_OPENED`), as one never made does. While the instance
 *   that destroyed it is in memory, every call, `open` included, is refused at once
 *   (`DESTROYED`), so a late call from a closing Session never makes it again. No mark is
 *   kept past that: names are never reused, the owner opens only a store its `ready` row
 *   names, and it forgets a row only once its store's `destroy()` returned (the drop and
 *   erasure paths, `threads.ts`, `erase.ts`); a namespace's objects cannot be listed, so
 *   the row is the only way back to a store, and no row names a destroyed one.
 */
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import {
  type EntryRecord, ROOT_CONVERSATION_ID, type Seq, type Storage, StorageRequestError,
  type StorageWrite,
} from "@earendil-works/pi-durable";
import {
  openDurableObjectSqliteStorage,
} from "@earendil-works/pi-durable/storage/sqlite/cloudflare";
import { DurableObject, RpcTarget } from "cloudflare:workers";

/** pi's table prefix in a store: its own default, alone in the database. */
export const STORE_PREFIX = "pi_";
/** The refusals, each fixed. */
export const STALE_HOLDER = "a stale holder: a newer Session holds this store";
export const NOT_OPENED = "this store was never opened";
export const DESTROYED = "this store was destroyed";
/** A holder: 128 random bits, as hex. */
const HOLDER = /^[0-9a-f]{32}$/;
/** The durable key of the holder. */
const HOLDER_KEY = "ta:holder";

/** The reads of pi's `Storage` the store serves, each by its name. */
export const READS = Object.freeze([
  "conversation", "scanConversations", "entry", "findLatestHeadMarker", "scanEntries", "task",
  "scanTasks", "submission", "scanSubmissions", "submissionByRequest", "findDocument",
  "document", "scanDocuments",
] as const satisfies readonly (keyof Storage)[]);
export type Read = (typeof READS)[number];

/** A read's answer: its value, or the request error pi raised for it. */
export type Reply<T = unknown> = { readonly value: T } | { readonly requestError: string };

/** One open Session's calls on its store: pi's commits and reads, for its holder only. */
export interface StoreSessionRpc {
  commit(writes: readonly StorageWrite[]): Promise<Seq>;
  read(method: Read, args: readonly unknown[]): Promise<Reply>;
}

/** What the owner's object calls on a store (`ThreadStore`'s RPC surface). */
export interface StoreRpc {
  /** Claim the store for `holder`: the next id it would mint, and the holder's session. */
  open(holder: string): Promise<{ readonly next: number; readonly session: StoreSessionRpc }>;
  /** A fresh session for the holder that holds the store now; never a claim. */
  attach(holder: string): Promise<StoreSessionRpc>;
  /** How the root's input `request` stands, its whole answer once done; a pure read. */
  answer(request: string): Promise<Answer | null>;
  size(): Promise<number> | number;
  destroy(): Promise<void>;
}

/** A root input as a store holds it: how it settled, and its answer's text once done. */
export interface Answer {
  readonly status: "queued" | "placed" | "done" | "unanswered";
  readonly reason?: string;
  readonly text?: string;
}

/** The text of an assistant entry, for an operation's result. */
export function assistantText(entry: EntryRecord | undefined): string {
  const message = (entry as { model?: readonly unknown[] } | undefined)?.model?.[0] as
    { role?: string; content?: readonly { type: string; text?: string }[] } | undefined;
  if (message?.role !== "assistant") {
    return "";
  }
  return (message.content ?? []).map((part) => (part.type === "text" ? part.text ?? "" : ""))
    .join("");
}

type Sqlite = Awaited<ReturnType<typeof openDurableObjectSqliteStorage>>;

/** pi's storage, for `holder` only: refused once another holds the store. */
type Held = (holder: string) => Promise<Sqlite>;

/** One open Session's calls, each fenced to its holder. */
export class StoreSession extends RpcTarget implements StoreSessionRpc {
  readonly #holder: string;
  readonly #held: Held;

  constructor(holder: string, held: Held) {
    super();
    this.#holder = holder;
    this.#held = held;
  }

  /** pi's `commit`. */
  async commit(writes: readonly StorageWrite[]): Promise<Seq> {
    return (await this.#held(this.#holder)).commit(writes, BACKGROUND_CONTEXT);
  }

  /** One of pi's reads (`READS`); an invalid request is answered. */
  async read(method: Read, args: readonly unknown[]): Promise<Reply> {
    if (!READS.includes(method) || !Array.isArray(args)) {
      throw new Error("not a read of pi's storage");
    }
    const pi = await this.#held(this.#holder);
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
}

export class ThreadStore extends DurableObject implements StoreRpc {
  /** Set once this instance wiped the store: it answers nothing more. */
  #destroyed = false;
  /** pi's storage over this object's database, opened at the holder's first call. */
  #storage: Promise<Sqlite> | undefined;

  /**
   * Make `holder` this store's one holder, opening pi's storage (and making its tables on
   * first use); answer the next id the store would mint, which seeds the holder's own
   * minting (`RemoteStorage.mintId`), and the holder's session. Refused once destroyed.
   */
  async open(holder: string): Promise<{ next: number; session: StoreSession }> {
    if (typeof holder !== "string" || !HOLDER.test(holder)) {
      throw new Error("a holder is 128 random bits, as hex");
    }
    this.#alive();
    this.ctx.storage.kv.put(HOLDER_KEY, holder);
    const next = Number(await (await this.#pi()).mintId());
    return { next, session: this.#session(holder) };
  }

  /** A fresh session for `holder`, the store's holder now; refused for any other. */
  async attach(holder: string): Promise<StoreSession> {
    await this.#held(holder);
    return this.#session(holder);
  }

  /**
   * How the root's input with pi's request id `request` stands, and its answer's whole text
   * once done; null for none. Claims no holder: a pure read, refused for a store never
   * opened (so it never makes one) and for one this instance destroyed.
   */
  async answer(request: string): Promise<Answer | null> {
    this.#alive();
    if (this.ctx.storage.kv.get<string>(HOLDER_KEY) === undefined) {
      throw new Error(NOT_OPENED);
    }
    if (typeof request !== "string" || request.length === 0 || request.length > 128) {
      throw new Error("a request id is 1 to 128 characters");
    }
    const pi = await this.#pi();
    const record = await pi.submissionByRequest(ROOT_CONVERSATION_ID, request,
                                                BACKGROUND_CONTEXT);
    if (record === undefined || record.type !== "input") {
      return null;
    }
    if (record.status === "done") {
      const entry = await pi.entry(record.answer, BACKGROUND_CONTEXT);
      return { status: "done", text: assistantText(entry?.entry) };
    }
    return record.status === "unanswered" ? { status: "unanswered", reason: record.reason }
      : { status: record.status };
  }

  /** The store's database size in bytes. */
  size(): number {
    return this.ctx.storage.sql.databaseSize;
  }

  /**
   * Wipe the store whole, leaving nothing; this instance refuses every later call. Idempotent,
   * so an interrupted destroy is simply run again.
   */
  async destroy(): Promise<void> {
    const open = this.#storage;
    this.#storage = undefined;
    await (await open?.catch(() => undefined))?.close(BACKGROUND_CONTEXT).catch(() => {});
    this.#destroyed = true;
    await this.ctx.storage.deleteAll();
  }

  #session(holder: string): StoreSession {
    return new StoreSession(holder, (asking) => this.#held(asking));
  }

  #alive(): void {
    if (this.#destroyed) {
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
