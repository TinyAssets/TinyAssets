/**
 * pi's `Storage`, over a thread's store (`thread-store.ts`) by RPC: what a thread's Session
 * is opened over in the owner's object (ADR-032 §4, amended 2026-10-30; tasks 5, S2).
 *
 * - **Holder and session.** Opening draws a fresh random holder, makes it the store's one
 *   holder, and keeps the store's session for it: every call goes over that one session
 *   (live probe P7: one billed request per session, not one per call), fenced to the
 *   holder, so once another Session opened the store, this one can no longer reach it.
 * - **Ids are minted here**, from the store's next id at open, then counted up: pi's
 *   contract is ids "greater than every ID minted since the storage was opened and every ID
 *   stored". A commit advances the store's next id past every id it writes, and this
 *   storage's own count too, so a reopen seeds past everything committed. No RPC per id.
 * - **Reads are retried once** when the call itself fails (a store reset, or an RPC lost in
 *   transit), over a fresh stub and a session attached anew for the same holder (never a
 *   claim), since a reset breaks the old ones; a second failure fails
 *   the read, and with it the Session (pi: "retry transient failures inside the
 *   method"). An invalid request is pi's `StorageRequestError`, which fails only that
 *   read, never retried. A read whose caller's
 *   context is aborted rejects with the abort, before the call or while it runs, which also
 *   fails only that read.
 * - **Commits are never retried.** A commit the store did not answer may or may not have
 *   applied; it fails loudly, and pi fails the Session, which the thread host reopens over
 *   what was committed (pi-durable README: "Reopen to recover: commits are atomic"). An
 *   input resubmitted with the same request id is then pi's duplicate. A commit takes no
 *   abort: any error from a commit is fatal.
 * - **Shaped for pi's next minor**, whose `commit(writes, adopt, context)` takes the
 *   Session's adoption: `commitAdopting` calls `adopt(seq)` once the RPC resolved, once and
 *   before the next commit starts (pi serializes its commits), and never for one that
 *   failed.
 * - **Close** releases the session, so the store may hibernate, and makes every later call
 *   reject; the store keeps nothing of this Session but the fence.
 */
import type { JsonValue } from "@earendil-works/chord";
import { awaitWithContext, type Context } from "@earendil-works/chord/context";
import {
  type ConversationId, type ConversationQuery, type ConversationRecord, type Cursor,
  type DocumentAddress, type DocumentId, type DocumentPoint, type DocumentQuery,
  type DocumentRecord, type EntryId, type EntryQuery, type EntryRecord, type Id,
  type Page, type Seq, type Storage, StorageRequestError, type StorageWrite, type StoredDocument,
  type SubmissionId, type SubmissionQuery, type SubmissionRecord, type TaskId, type TaskQuery,
  type TaskRecord,
} from "@earendil-works/pi-durable";

import type { Read, StoreRpc, StoreSessionRpc } from "./thread-store";

/** A closed storage's refusal. */
export const STORAGE_CLOSED = "this thread's storage is closed";

type Stored<T> = { readonly entry: EntryRecord; readonly commitSeq: Seq } | T;

/** A holder: 128 random bits, as hex. */
export function holderId(): string {
  const raw = new Uint8Array(16);
  crypto.getRandomValues(raw);
  return Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("");
}

function aborted(context: Context): void {
  const signal = context.abortSignal;
  if (signal?.aborted === true) {
    throw signal.reason ?? new Error("aborted");
  }
}

/** Release an RPC session's stub, if it is one (a local object has nothing to release). */
function release(session: StoreSessionRpc): void {
  (session as Partial<Disposable>)[Symbol.dispose]?.();
}

/** The id a write claims, as pi's storages read it, if it claims one. */
function claimed(write: StorageWrite): number | undefined {
  switch (write.type) {
    case "conversation":
    case "entry":
    case "task":
    case "submission":
      return write.value.id as unknown as number;
    case "document.create":
    case "document.copy":
      return write.record.id as unknown as number;
    default:
      return undefined;
  }
}

export class RemoteStorage implements Storage {
  /** A stub to the store, made again for a retry. */
  readonly #connect: () => StoreRpc;
  /** The store's session for this holder, attached again for a retry. */
  #session: StoreSessionRpc;
  readonly #holder: string;
  #next: number;
  #closed = false;

  private constructor(connect: () => StoreRpc, session: StoreSessionRpc, holder: string,
                      next: number) {
    this.#connect = connect;
    this.#session = session;
    this.#holder = holder;
    this.#next = next;
  }

  /**
   * Open the store `connect` reaches as its one holder: a fresh holder, unless one is given
   * (a test's).
   */
  static async open(connect: () => StoreRpc, holder: string = holderId()):
      Promise<RemoteStorage> {
    const { next, session } = await connect().open(holder);
    if (!Number.isSafeInteger(next) || next < 2) {
      release(session);
      throw new Error("a store answered no next id");
    }
    return new RemoteStorage(connect, session, holder, next);
  }

  /** The holder this storage opened the store as. */
  get holder(): string {
    return this.#holder;
  }

  commit(writes: readonly StorageWrite[], _context: Context): Promise<Seq> {
    return this.commitAdopting(writes, undefined);
  }

  /**
   * One commit, sent once, in the shape of pi's next minor (`commit(writes, adopt,
   * context)`): `adopt` is told its seq once the store made it durable, before this
   * resolves, and never for a commit that failed.
   */
  async commitAdopting(writes: readonly StorageWrite[],
                       adopt: ((seq: Seq) => void) | undefined): Promise<Seq> {
    this.#open();
    const seq = await this.#session.commit(writes);
    for (const write of writes) {
      const id = claimed(write);
      if (id !== undefined && id >= this.#next) {
        this.#next = id + 1;
      }
    }
    adopt?.(seq);
    return seq;
  }

  async mintId<I extends Id<string>>(): Promise<I> {
    this.#open();
    if (!Number.isSafeInteger(this.#next)) {
      throw new Error("ID space is exhausted");
    }
    return this.#next++ as unknown as I;
  }

  conversation(id: ConversationId, context: Context): Promise<ConversationRecord | undefined> {
    return this.#read("conversation", [id], context);
  }

  scanConversations(query: ConversationQuery, limit: number, cursor: Cursor | undefined,
                    context: Context): Promise<Page<ConversationRecord, Cursor>> {
    return this.#read("scanConversations", [query, limit, cursor], context);
  }

  entry(id: EntryId, context: Context): Promise<Stored<undefined>>;
  entry(conversationId: ConversationId, id: EntryId, context: Context):
    Promise<Stored<undefined>>;
  entry(first: EntryId | ConversationId, second: EntryId | Context, third?: Context):
      Promise<Stored<undefined>> {
    return third === undefined ? this.#read("entry", [first], second as Context)
      : this.#read("entry", [first, second], third);
  }

  findLatestHeadMarker(conversationId: ConversationId, atOrBeforeEntryId: EntryId | undefined,
                       context: Context):
      Promise<(EntryRecord & { readonly head: EntryId }) | undefined> {
    return this.#read("findLatestHeadMarker", [conversationId, atOrBeforeEntryId], context);
  }

  scanEntries(query: EntryQuery, limit: number, cursor: Cursor | undefined, context: Context):
      Promise<Page<EntryRecord, Cursor>> {
    return this.#read("scanEntries", [query, limit, cursor], context);
  }

  task(id: TaskId, context: Context):
      Promise<TaskRecord<JsonValue, JsonValue, JsonValue> | undefined> {
    return this.#read("task", [id], context);
  }

  scanTasks(query: TaskQuery, limit: number, cursor: Cursor | undefined, context: Context):
      Promise<Page<TaskRecord<JsonValue, JsonValue, JsonValue>, Cursor>> {
    return this.#read("scanTasks", [query, limit, cursor], context);
  }

  submission(id: SubmissionId, context: Context): Promise<SubmissionRecord | undefined> {
    return this.#read("submission", [id], context);
  }

  scanSubmissions(query: SubmissionQuery, limit: number, cursor: Cursor | undefined,
                  context: Context): Promise<Page<SubmissionRecord, Cursor>> {
    return this.#read("scanSubmissions", [query, limit, cursor], context);
  }

  submissionByRequest(conversationId: ConversationId, requestId: string, context: Context):
      Promise<SubmissionRecord | undefined> {
    return this.#read("submissionByRequest", [conversationId, requestId], context);
  }

  findDocument(address: DocumentAddress, at: DocumentPoint, context: Context):
      Promise<DocumentRecord | undefined> {
    return this.#read("findDocument", [address, at], context);
  }

  document(id: DocumentId, at: DocumentPoint, context: Context):
      Promise<StoredDocument | undefined> {
    return this.#read("document", [id, at], context);
  }

  scanDocuments(query: DocumentQuery, limit: number, cursor: Cursor | undefined,
                context: Context): Promise<Page<DocumentRecord, Cursor>> {
    return this.#read("scanDocuments", [query, limit, cursor], context);
  }

  async close(_context: Context): Promise<void> {
    if (!this.#closed) {
      this.#closed = true;
      release(this.#session);
    }
  }

  #open(): void {
    if (this.#closed) {
      throw new Error(STORAGE_CLOSED);
    }
  }

  /**
   * One read: the context's abort checked before it and while it runs; a failed call tried
   * once more; a request error raised as pi's own.
   */
  async #read<T>(method: Read, args: readonly unknown[], context: Context): Promise<T> {
    this.#open();
    aborted(context);
    const call = () => this.#session.read(method, args);
    let reply;
    try {
      reply = await awaitWithContext(call(), context);
    } catch (error) {
      aborted(context);
      this.#open();
      reply = await awaitWithContext(this.#reattach().then(call), context)
        .catch((again: unknown) => {
          throw again instanceof Error ? again : error;
        });
    }
    if ("requestError" in reply) {
      throw new StorageRequestError(reply.requestError);
    }
    return reply.value as T;
  }

  /** A fresh stub, and a session on it for this holder in place of the broken one. */
  async #reattach(): Promise<void> {
    const session = await this.#connect().attach(this.#holder);
    release(this.#session);
    if (this.#closed) {
      release(session);
      throw new Error(STORAGE_CLOSED);
    }
    this.#session = session;
  }
}
