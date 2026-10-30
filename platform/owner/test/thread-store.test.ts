/**
 * A thread's store (`thread-store.ts`) and pi's `Storage` over it by RPC
 * (`remote-storage.ts`), in workerd (tasks 5, S1 and S2): pi's own storage conformance
 * suite through the RPC, the holder fence, an unopened and a destroyed store, the store's
 * size, local minting, a read retried once, a commit never retried, and an aborted read.
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
  DESTROYED, NOT_OPENED, type Read, type Reply, STALE_HOLDER, type StoreRpc, ThreadStore,
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

/** A store whose calls a test can fail, as a reset or a lost RPC fails them. */
function faulty(store: StoreRpc, fails: { reads: number; commits: number }): StoreRpc {
  return {
    open: (holder) => store.open(holder),
    commit: async (holder, writes) => {
      if (fails.commits > 0) {
        fails.commits--;
        throw new Error("network connection lost");
      }
      return store.commit(holder, writes);
    },
    read: async (holder, method: Read, args) => {
      if (fails.reads > 0) {
        fails.reads--;
        throw new Error("network connection lost");
      }
      return store.read(holder, method, args) as Promise<Reply>;
    },
    size: () => store.size(),
    destroy: () => store.destroy(),
  };
}

describe("a thread store", () => {
  it("refuses every call but open until it is opened, and makes no table", async () => {
    const store = freshStore();
    const holder = holderId();
    expect(await failed(store.read(holder, "conversation", [ROOT])))
      .toContain(NOT_OPENED);
    expect(await failed(store.commit(holder, root)))
      .toContain(NOT_OPENED);
    const tables = await runInDurableObject(store, (_instance, state) =>
      state.storage.sql.exec("SELECT name FROM sqlite_master WHERE name LIKE 'pi_%'")
        .toArray().length);
    expect(tables).toBe(0);
    expect(await failed(store.open("not a holder")))
      .toContain("128 random bits");
    // Only pi's reads are served by name: no other method of the store or of pi's storage.
    await store.open(holder);
    for (const method of ["commit", "close", "mintId", "constructor"]) {
      expect(await failed(store.read(holder, method as Read, [])))
        .toContain("not a read of pi's storage");
    }
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

  it("is transparent to its own eviction, and a read survives its reset once", async () => {
    const name = holderId();
    const storage = await RemoteStorage.open(named(name));
    await storage.commit(root, BG);
    // Evicted idle: the holder's next call opens pi's storage again over what it holds.
    await evictDurableObject(env.PISTORE.getByName(name));
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

  it("retries a failed read once, and fails it the second time", async () => {
    const store = freshStore();
    const fails = { reads: 1, commits: 0 };
    const storage = await RemoteStorage.open(() => faulty(store as unknown as StoreRpc, fails));
    await storage.commit(root, BG);
    expect(await storage.conversation(ROOT, BG)).toEqual({ id: ROOT });
    expect(fails.reads).toBe(0);
    fails.reads = 2;
    expect(await failed(storage.conversation(ROOT, BG)))
      .toBe("network connection lost");
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
    const counted = { reads: 0 };
    const name = holderId();
    const counting = await RemoteStorage.open(() => {
      const store = env.PISTORE.getByName(name) as unknown as StoreRpc;
      return { ...faulty(store, { reads: 0, commits: 0 }),
               read: (holder: string, method: Read, args: readonly unknown[]) => {
                 counted.reads++;
                 return store.read(holder, method, args);
               } };
    });
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
