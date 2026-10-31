/**
 * A thread's store (`thread-store.ts`) and pi's `Storage` over it by RPC
 * (`remote-storage.ts`), in workerd (tasks 5, S1 and S2): pi's own storage conformance
 * suite through the RPC, the holder fence, an unopened and a destroyed store, the store's
 * size, one session per open storage (released at close), local minting, a read retried
 * once over a session attached anew, a commit never retried, and an aborted read.
 */
import { evictDurableObject, runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT, withAbortSignal } from "@earendil-works/chord/context";
import {
  type ConversationId, ROOT_CONVERSATION_ID, type Storage, StorageRequestError,
} from "@earendil-works/pi-durable";
import { registerStorageConformance } from "@earendil-works/pi-durable/testing";
import { describe, expect, it } from "vitest";

import { holderId, RemoteStorage, STORAGE_CLOSED } from "../src/remote-storage";
import {
  DESTROYED, NOT_OPENED, type Read, type Reply, STALE_HOLDER, type StoreRpc,
  type StoreSessionRpc, ThreadStore,
} from "../src/thread-store";

const BG = BACKGROUND_CONTEXT;
const ROOT = ROOT_CONVERSATION_ID;

/** A fresh store, by a name no row holds (as the owner names one, 128 random bits). */
function freshStore(): DurableObjectStub<ThreadStore> {
  return env.PISTORE.getByName(holderId());
}

/** A store by its name, a fresh stub at each call, as the owner reaches one. */
function named(name: string = holderId()): () => StoreRpc {
  return () => env.PISTORE.getByName(name) as unknown as StoreRpc;
}

/** How a call failed: its error's message ("ok" when it did not). */
const failed = (call: Promise<unknown>): Promise<string> =>
  call.then(() => "ok", (error: Error) => error.message);

/** The root conversation, as pi's first commit makes it. */
const root = [{ type: "conversation" as const, value: { id: ROOT } }];

registerStorageConformance({ describe, expect, it }, "RemoteStorage over a ThreadStore, by RPC",
  async (use: (storage: Storage) => Promise<void>) => {
    const storage = await RemoteStorage.open(named());
    try {
      await use(storage);
    } finally {
      await storage.close(BG);
    }
  });

/** What a test counts of a store's sessions: those made, released, and the calls made. */
type Tally = { sessions: number; released: number; reads: number; commits: number };
const tally = (): Tally => ({ sessions: 0, released: 0, reads: 0, commits: 0 });

/**
 * A store whose sessions' calls a test can fail, as a reset or a lost RPC fails them, and
 * whose sessions and calls it counts.
 */
function faulty(store: StoreRpc, fails: { reads: number; commits: number },
                seen: Tally = tally()): StoreRpc {
  const wrap = (session: StoreSessionRpc): StoreSessionRpc => {
    seen.sessions++;
    return {
      commit: async (writes) => {
        seen.commits++;
        if (fails.commits > 0) {
          fails.commits--;
          throw new Error("network connection lost");
        }
        return session.commit(writes);
      },
      read: async (method: Read, args) => {
        seen.reads++;
        if (fails.reads > 0) {
          fails.reads--;
          throw new Error("network connection lost");
        }
        return session.read(method, args) as Promise<Reply>;
      },
      [Symbol.dispose]: () => {
        seen.released++;
        (session as Partial<Disposable>)[Symbol.dispose]?.();
      },
    } as StoreSessionRpc;
  };
  return {
    open: async (holder) => {
      const { next, session } = await store.open(holder);
      return { next, session: wrap(session) };
    },
    attach: async (holder) => wrap(await store.attach(holder)),
    size: () => store.size(),
    destroy: () => store.destroy(),
  };
}

describe("a thread store", () => {
  it("refuses every call but open until it is opened, and makes no table", async () => {
    const store = freshStore();
    const holder = holderId();
    expect(await failed(store.attach(holder)))
      .toContain(NOT_OPENED);
    const tables = await runInDurableObject(store, (_instance, state) =>
      state.storage.sql.exec("SELECT name FROM sqlite_master WHERE name LIKE 'pi_%'")
        .toArray().length);
    expect(tables).toBe(0);
    expect(await failed(store.open("not a holder")))
      .toContain("128 random bits");
    // Only pi's reads are served by name: no other method of the store or of pi's storage.
    const { session } = await store.open(holder);
    for (const method of ["commit", "close", "mintId", "constructor"]) {
      expect(await failed(session.read(method as Read, [])))
        .toContain("not a read of pi's storage");
    }
    // A session attaches only for the store's holder.
    expect(await failed(store.attach(holderId()))).toContain(STALE_HOLDER);
    expect(await (await store.attach(holder)).read("conversation", [ROOT]))
      .toEqual({ value: undefined });
  });

  it("serves its holder only: a newer Session's open fences the older one out", async () => {
    const name = holderId();
    const older = await RemoteStorage.open(named(name));
    await older.commit(root, BG);
    const newer = await RemoteStorage.open(named(name));
    // The stale Session can neither write nor read here again.
    expect(await failed(older.commit([{ type: "conversation", value: { id: 99 as ConversationId,
                                                               } }], BG)
      )).toContain(STALE_HOLDER);
    expect(await failed(older.conversation(ROOT, BG)))
      .toContain(STALE_HOLDER);
    // The newer one reads what was committed, and mints past it.
    expect(await newer.conversation(ROOT, BG)).toEqual({ id: ROOT });
    expect(await newer.mintId()).toBeGreaterThan(1);
    expect(await newer.conversation(99 as ConversationId, BG)).toBeUndefined();
  });

  it("stays in memory while a session holds it, is transparent to its own eviction once " +
     "released, and a read survives its reset once", async () => {
    const name = holderId();
    const held = await RemoteStorage.open(named(name));
    await held.commit(root, BG);
    // A held session keeps the store in memory (live probe P7): eviction waits for it.
    const evicting = evictDurableObject(env.PISTORE.getByName(name)).then(() => "evicted");
    expect(await Promise.race([evicting, new Promise((resolve) =>
      setTimeout(() => resolve("held"), 500))])).toBe("held");
    expect(await held.conversation(ROOT, BG)).toEqual({ id: ROOT });
    // Released at close, it is evicted; a reopen opens pi's storage again over what it holds.
    await held.close(BG);
    expect(await evicting).toBe("evicted");
    const storage = await RemoteStorage.open(named(name));
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });
    // Reset outright: the old stub is broken; the read is retried once over a fresh one.
    await runInDurableObject(env.PISTORE.getByName(name), (_instance, state) =>
      state.abort("reset")).catch(() => undefined);
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });
  });

  it("answers its size, and once destroyed is empty and never opens again", async () => {
    const store = freshStore();
    const storage = await RemoteStorage.open(() => store as unknown as StoreRpc);
    await storage.commit(root, BG);
    const before = await store.size();
    expect(before).toBeGreaterThan(0);
    await store.destroy();
    await store.destroy();  // idempotent: an interrupted destroy is run again
    const left = await runInDurableObject(store, (_instance, state) =>
      state.storage.sql.exec("SELECT name FROM sqlite_master WHERE name LIKE 'pi_%'")
        .toArray().length);
    expect(left).toBe(0);
    expect(await failed(store.open(holderId()))).toContain(DESTROYED);
    expect(await failed(store.attach(storage.holder))).toContain(DESTROYED);
    expect(await failed(storage.conversation(ROOT, BG)))
      .toContain(DESTROYED);
    expect(await store.size()).toBeLessThan(before);
  });
});

describe("pi's storage over a store", () => {
  it("mints ids locally, past every id the store holds, also after a reopen", async () => {
    const store = named();
    const first = await RemoteStorage.open(store);
    const ids = [await first.mintId(), await first.mintId(), await first.mintId()];
    expect(ids).toEqual([2, 3, 4]);
    // A commit advances the store's next id past every id it writes; minted and unused
    // ids are the Session's alone.
    await first.commit([...root, { type: "conversation", value: { id: 3 as ConversationId } }],
                       BG);
    const again = await RemoteStorage.open(store);
    expect(await again.mintId()).toBe(4);
    expect(await again.mintId()).toBe(5);
  });

  it("calls over one session while open, and releases it at close", async () => {
    const store = freshStore();
    const seen = tally();
    const storage = await RemoteStorage.open(
      () => faulty(store as unknown as StoreRpc, { reads: 0, commits: 0 }, seen));
    await storage.commit(root, BG);
    for (let i = 0; i < 5; i++) {
      expect(await storage.conversation(100 + i as ConversationId, BG)).toBeUndefined();
    }
    expect(seen).toEqual({ sessions: 1, released: 0, reads: 5, commits: 1 });
    await storage.close(BG);
    await storage.close(BG);
    expect(seen).toMatchObject({ sessions: 1, released: 1 });
  });

  it("retries a failed read once, over a session attached anew, and fails it the second time",
     async () => {
    const store = freshStore();
    const fails = { reads: 1, commits: 0 };
    const seen = tally();
    const storage = await RemoteStorage.open(
      () => faulty(store as unknown as StoreRpc, fails, seen));
    await storage.commit(root, BG);
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });
    expect(fails.reads).toBe(0);
    // The broken session was released, and a new one attached for the same holder.
    expect(seen).toMatchObject({ sessions: 2, released: 1 });
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });
    expect(seen).toMatchObject({ sessions: 2, released: 1 });
    fails.reads = 2;
    expect(await failed(storage.conversation(77 as ConversationId, BG)))
      .toBe("network connection lost");
  });

  it("keeps a conversation record once read, never one not found", async () => {
    const store = freshStore();
    const seen = tally();
    const storage = await RemoteStorage.open(
      () => faulty(store as unknown as StoreRpc, { reads: 0, commits: 0 }, seen));
    expect(await storage.conversation(ROOT, BG)).toBeUndefined();
    await storage.commit(root, BG);
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });  // read again
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });  // kept
    expect(seen.reads).toBe(2);
  });

  it("never retries a commit: one that failed fails loudly, and nothing is written twice",
     async () => {
    const store = freshStore();
    const fails = { reads: 0, commits: 1 };
    const storage = await RemoteStorage.open(() => faulty(store as unknown as StoreRpc, fails));
    expect(await failed(storage.commit(root, BG)))
      .toBe("network connection lost");
    expect(await storage.conversation(ROOT, BG)).toBeUndefined();
    expect(await storage.commit(root, BG)).toBeGreaterThanOrEqual(0);
  });

  it("raises an invalid request as pi's own, never retried, failing only that read",
     async () => {
    const storage = await RemoteStorage.open(named());
    await storage.commit(root, BG);
    const refused = await storage.scanEntries({ conversationId: 77 as ConversationId }, 10,
                                              undefined, BG).catch((e: Error) => e);
    expect(refused).toBeInstanceOf(StorageRequestError);
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });
  });

  it("rejects a read whose context is aborted, before and while it runs", async () => {
    const storage = await RemoteStorage.open(named());
    await storage.commit(root, BG);
    const stop = new AbortController();
    stop.abort(new Error("stopped"));
    expect(await failed(storage.conversation(ROOT, withAbortSignal(stop.signal, BG))
      )).toBe("stopped");
    // An aborted read never reaches the store.
    const counted = tally();
    const name = holderId();
    const counting = await RemoteStorage.open(() => faulty(
      env.PISTORE.getByName(name) as unknown as StoreRpc, { reads: 0, commits: 0 }, counted));
    expect(await failed(counting.conversation(ROOT, withAbortSignal(stop.signal, BG))))
      .toBe("stopped");
    expect(counted.reads).toBe(0);
    const late = new AbortController();
    const reading = storage.conversation(ROOT, withAbortSignal(late.signal, BG));
    late.abort(new Error("stopped late"));
    expect((await reading.catch((e: Error) => e) as Error).message).toBe("stopped late");
  });

  it("calls a commit's adoption once it is durable, and never for one that failed", async () => {
    const store = freshStore();
    const fails = { reads: 0, commits: 1 };
    const storage = await RemoteStorage.open(() => faulty(store as unknown as StoreRpc, fails));
    const adopted: number[] = [];
    const send = (storage as unknown as { commitAdopting(writes: unknown,
                                                         adopt: (seq: number) => void):
      Promise<number> }).commitAdopting.bind(storage);
    await send(root, (seq) => adopted.push(seq)).catch(() => undefined);
    expect(adopted).toEqual([]);
    const seq = await send(root, (at) => adopted.push(at));
    expect(adopted).toEqual([seq]);
  });

  it("refuses every call once closed", async () => {
    const storage = await RemoteStorage.open(named());
    await storage.close(BG);
    expect(await failed(storage.conversation(ROOT, BG)))
      .toBe(STORAGE_CLOSED);
    expect(await failed(storage.commit(root, BG)))
      .toBe(STORAGE_CLOSED);
    await expect(storage.mintId()).rejects.toThrow(STORAGE_CLOSED);
  });
});
