/** The owner object: its schema migrates on wake, and it serves its own owner only. */
import { evictDurableObject, runInDurableObject } from "cloudflare:test";
import { DurableObject, env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { LIMITS } from "../src/collections";
import { CHUNK_BYTES, PLACE_BYTES } from "../src/manifest";
import { OwnerDO } from "../src/owner-do";
import { MIGRATIONS, migrate } from "../src/schema";
import { failure, ownerObject } from "./support";

function version(state: DurableObjectState): number {
  return state.storage.sql.exec<{ version: number }>("SELECT version FROM schema_version").one()
    .version;
}

const PULL = { collection: "board", after: 0, limit: 1 };

/**
 * Every public method of the class is an RPC any binding can call, so this list is the
 * object's whole surface: its collection, ledger, policy, file, notice, model-connection and
 * model-spend doors. `alarm` is the
 * runtime's, handed to Lifecycle, and refused over RPC. Claiming, marking and settling a
 * wake have no door: only the `wakes` lane runs them, in process, with the doorbell's
 * sender. The list only grows by a door (ADR-034 took the agent's doors out until the runs
 * slice brings them back as run doors).
 */
const RPC_METHODS = Object.freeze([
  "admissionSet", "agentCancel", "agentDoorbell", "agentDoorbellEnd", "agentRun", "agentStatus", "agentSubmit",
  "boardDigest",
  "codeRun", "collectionClear", "collectionDelete", "collectionGet", "collectionHistory",
  "collectionPull",
  "collectionWrite", "connectionCommit", "connectionList", "connectionPrepare",
  "connectionRevoke", "deleteAgent", "disableAgent", "egressBeat", "egressOpen",
  "egressReceipts", "egressSettle", "erasureOutside", "erasureOutsideDone", "fileChanges", "fileHistory",
  "fileHistoryPurge", "fileList",
  "fileMkdir", "fileRead", "fileReadChunks", "fileRedact", "fileRemove", "fileRename",
  "fileRestore", "fileStat", "fileSyncCommit", "fileSyncUploadBegin", "fileSyncUploadChunks",
  "fileUploadAbort", "fileUploadBegin",
  "fileUploadChunks", "fileUploadCommit", "fileUploadStored", "fileUploadTarget",
  "fileUsage", "fileWrite",
  "flowFinish", "grantForget",
  "grantForgetRevoked", "grantList", "grantRevoke", "grantSet", "inboundAccept",
  "inboundCreate", "inboundList", "inboundRevoke", "inboundSet", "inboxDigest", "keyList",
  "keyMint", "mailChain", "mailboxAck", "mailboxRead",
  "noticeList", "outsideAgents", "ownerErase", "policyProposal", "policyPropose",
  "policyRead", "policyReceipts", "policyWakeLimits", "policyWrite", "registerAgent",
  "registerProposal", "registerPropose", "roomPost", "runEvents", "sandboxRung",
  "sessionClose",
  "sessionCloseAll", "sessionExtend", "sessionOpen", "signinReport", "socketTicket",
  "socketsEnd", "tierSet", "toolProfile", "usageReceipts", "wakeEvents", "wakeSubscribe",
  "wakeUnsubscribe",
]);

/** The runtime's reserved methods the object hands to Lifecycle: no RPC reaches them. */
const RESERVED = Object.freeze(["alarm", "fetch", "webSocketClose", "webSocketError",
                                "webSocketMessage"]);

describe("schema migration on wake", () => {
  it("applies every migration before the first call", async () => {
    const { stub } = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(version(state)).toBe(MIGRATIONS.length);
      const tables = state.storage.sql.exec<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
      ).toArray().map((t) => t.name);
      for (const table of ["collection_history", "collection_rows", "collection_seqs",
                           "collection_usage", "object_owner", "schema_version"]) {
        expect(tables).toContain(table);
      }
    });
  });

  it("keeps data and applies nothing twice across an eviction", async () => {
    const { stub, as, name } = ownerObject();
    const written = await stub.collectionWrite(as(name), {
      collection: "board", row_id: "t", body: "1", expected_etag: "", private: false,
    });
    await evictDurableObject(stub);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(version(state)).toBe(MIGRATIONS.length);
    });
    expect((await stub.collectionGet(as(name), { collection: "board", row_id: "t" })).row)
      .toEqual("row" in written ? written.row : null);
  });

  it("applies only the migrations past the stored version, in order", async () => {
    const { stub } = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const extra = [...MIGRATIONS, ["CREATE TABLE probe_a (n INTEGER)"],
                     ["INSERT INTO probe_a VALUES (2)"]];
      expect(migrate(state.storage, extra)).toBe(MIGRATIONS.length + 2);
      expect(version(state)).toBe(MIGRATIONS.length + 2);
      // Re-running applies nothing: the insert would add a second row.
      migrate(state.storage, extra);
      expect(state.storage.sql.exec("SELECT n FROM probe_a").toArray()).toEqual([{ n: 2 }]);
    });
  });

  it("rolls a failing migration back whole and leaves the version where it was", async () => {
    const { stub } = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const broken = [...MIGRATIONS, ["CREATE TABLE probe_b (n INTEGER)", "NOT SQL"]];
      expect(() => migrate(state.storage, broken)).toThrow();
      expect(version(state)).toBe(MIGRATIONS.length);
      expect(state.storage.sql.exec(
        "SELECT name FROM sqlite_master WHERE name = 'probe_b'").toArray()).toEqual([]);
    });
  });

  it("refuses to run against a schema newer than its code", async () => {
    const { stub, as, name } = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      state.storage.sql.exec("UPDATE schema_version SET version = ?", MIGRATIONS.length + 1);
      expect(() => migrate(state.storage)).toThrow(/newer than this code/);
    });
    await evictDurableObject(stub);
    expect((await failure(() => stub.collectionPull(as(name), PULL))).message)
      .toMatch(/newer than this code/);
  });

  it("keeps no body past PLACE_BYTES in a row: a larger one is placed (ADR-035)", async () => {
    const ceiling = Number(/length\(CAST\(body AS BLOB\)\) <= (\d+)/
      .exec(MIGRATIONS.flat().join(" "))?.[1]);
    expect(ceiling).toBe(PLACE_BYTES);
    // A placed body is any size up to what one call moves whole, cut into chunks.
    expect(LIMITS.maxRowBytes).toBe(2 * CHUNK_BYTES);
    const { stub } = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const insert = (body: string, bytes: number, content: string | null) =>
        state.storage.sql.exec(
          "INSERT INTO collection_rows (collection, row_id, revision, seq, etag, writer, " +
            "private, body, updated_at, creator, body_bytes, content_id, filer, change) " +
            "VALUES ('c', 'r', 1, 1, 'e', 'w', 0, ?, 0, 'w', ?, ?, 'owner', 1)", body, bytes, content);
      expect(() => insert("x".repeat(ceiling + 1), ceiling + 1, null)).toThrow(/CHECK/);
      // A placed body's row names its content and keeps no text; a small one is never placed.
      expect(() => insert("x", ceiling + 1, "c".repeat(64))).toThrow(/CHECK/);
      expect(() => insert("", ceiling, "c".repeat(64))).toThrow(/CHECK/);
      expect(() => insert("xy", 3, null)).toThrow(/CHECK/);
    });
  });
});

describe("the object's name", () => {
  it("is the owner's, inside the object, for getByName and idFromName", async () => {
    const named = env.OWNER.getByName("owner-name-probe");
    const fromName = env.OWNER.get(env.OWNER.idFromName("owner-id-probe"));
    for (const [stub, expected] of [[named, "owner-name-probe"],
                                    [fromName, "owner-id-probe"]] as const) {
      expect(await runInDurableObject(stub, (_instance: OwnerDO, state) => state.id.name))
        .toBe(expected);
    }
  });

  it("binds the object: only the owner it is named by is served", async () => {
    const { stub, as, name } = ownerObject();
    for (const binding of [as("bob", "cara"), as("cara", "cara"), as(name, `${name}x`)]) {
      const refused = await failure(() => stub.collectionPull(binding, PULL));
      expect([refused.name, refused.message])
        .toEqual(["Refused", "this object belongs to another owner"]);
    }
    const write = failure(() => stub.collectionWrite(as(name, "bob"), {
      collection: "board", row_id: "t", body: "1", expected_etag: "", private: false,
    }));
    expect((await write).message).toBe("this object belongs to another owner");
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray()).toEqual([]);
    });
    expect(await stub.collectionPull(as("bob"), PULL)).toEqual({ cursor: 0, rows: [] });
  });

  it("refuses every call on an object with no name", async () => {
    const unnamed = env.OWNER.get(env.OWNER.newUniqueId());
    const refused = await failure(() => unnamed.collectionPull({ owner: "alice", caller: "alice", origin: null },
                                                                PULL));
    expect([refused.name, refused.message])
      .toEqual(["Refused", "this object is not named by an owner"]);
    await runInDurableObject(unnamed, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray()).toEqual([]);
    });
  });
});

describe("the object's stored owner", () => {
  it("is stored on first use", async () => {
    const { stub, as, name } = ownerObject();
    await stub.collectionPull(as("bob"), PULL);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray())
        .toEqual([{ owner: name }]);
    });
  });

  it("is a second check: a stored owner other than the name refuses, across evictions",
     async () => {
       const { stub, as, name } = ownerObject();
       await stub.collectionPull(as(name), PULL);
       await runInDurableObject(stub, (_instance: OwnerDO, state) => {
         state.storage.sql.exec("UPDATE object_owner SET owner = 'someone-else'");
       });
       await evictDurableObject(stub);
       const refused = await failure(() => stub.collectionPull(as(name), PULL));
       expect([refused.name, refused.message])
         .toEqual(["Refused", "this object belongs to another owner"]);
     });

  it("refuses an invalid principal before binding anything", async () => {
    const { stub } = ownerObject();
    for (const binding of [{ owner: "", caller: "bob" }, { owner: " alice", caller: "bob" },
                           { owner: "alice", caller: "bo\nb" },
                           { owner: "a".repeat(513), caller: "bob" }]) {
      expect((await failure(() => stub.collectionPull({ ...binding, origin: null }, PULL))).message)
        .toBe("invalid owner principal");
    }
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray()).toEqual([]);
    });
  });

  it("keeps the error's kind across RPC", async () => {
    const { stub, as, name } = ownerObject();
    await stub.collectionPull(as(name), PULL);
    expect((await failure(() => stub.collectionPull(as("bob", "bob"), PULL))).name)
      .toBe("Refused");
    expect((await failure(() => stub.collectionPull(as(name), {
      ...PULL, limit: 0,
    }))).name).toBe("Invalid");
  });
});

describe("the object's RPC surface", () => {
  it("is exactly its doors: nothing claims, marks or settles a wake", async () => {
    const methods = Object.getOwnPropertyNames(OwnerDO.prototype)
      .filter((method) => method !== "constructor" && !RESERVED.includes(method)).sort();
    expect(methods).toEqual(RPC_METHODS);
    expect(Object.getOwnPropertyNames(DurableObject.prototype)).toEqual(["constructor"]);
    const { stub, name } = ownerObject();
    const call = (method: string) => (stub as unknown as
      Record<string, (...args: unknown[]) => Promise<unknown>>)[method]!(
      { owner: name, caller: name, origin: null }, {});
    for (const method of ["claimWake", "markWake", "settleWake", "dueWakes", "wakeSender"]) {
      expect((await failure(() => call(method))).message)
        .toBe(`The RPC receiver does not implement "${method}".`);
    }
    expect((await failure(() => call("alarm"))).message).toMatch(/reserved method/);
  });
});

describe("no reach past the object", () => {
  it("has no ctx.exports, so no stub to another owner's object", async () => {
    const { stub } = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect((state as DurableObjectState & { exports?: unknown }).exports).toBeUndefined();
    });
  });
});
