/** The owner object: its schema migrates on wake, and it serves its own owner only. */
import { evictDurableObject, runInDurableObject } from "cloudflare:test";
import { DurableObject, env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { LIMITS } from "../src/collections";
import { CHUNK_BYTES, PLACE_BYTES } from "../../contract/src/limits";
import { doorTable } from "../../contract/src/doors";
import { OwnerDO } from "../src/owner-do";
import { MIGRATION_CHANGED, MIGRATION_UNPINNED, migrate, migrationDigest } from "../../contract/src/migrate";
import { MIGRATIONS } from "../src/schema";
import { failure, ownerObject, unseededObject } from "./support";

function version(state: DurableObjectState): number {
  return state.storage.sql.exec<{ version: number }>("SELECT version FROM schema_version").one()
    .version;
}

const PULL = { collection: "board", after: 0, limit: 1 };

/** The runtime's reserved methods the object hands to Lifecycle: no RPC reaches them. */
const RESERVED = Object.freeze(["alarm", "fetch", "webSocketClose", "webSocketError",
                                "webSocketMessage"]);

describe("schema migration on wake", () => {
  it("applies every migration before the first call", async () => {
    const { stub } = await ownerObject();
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
    const { stub, as, name } = await ownerObject();
    const written = await stub.collectionWrite(as(name), {
      collection: "board", row_id: "t", body: "1", expected_etag: "", private: false,
    });
    await evictDurableObject(stub);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(version(state)).toBe(MIGRATIONS.length);
    });
    // The write answered its row without its body (review H3); the read, with it.
    const { body_bytes: _bytes, ...row } = "row" in written ? written.row : { body_bytes: 0 };
    expect((await stub.collectionGet(as(name), { collection: "board", row_id: "t" })).row)
      .toEqual({ ...row, body: "1" });
  });

  it("applies only the migrations past the stored version, in order", async () => {
    const { stub } = await ownerObject();
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

  it("ends an object migrated from each earlier version with a fresh object's schema",
     async () => {
    // Every table, index, trigger and view of the owner's, as `sqlite_master` holds it; not
    // the runtime's own (`_cf_*`), which workerd makes as it needs them (one appears after
    // migration 8's column rebuild from version 6, holding nothing of the owner's).
    const schema = (state: DurableObjectState) => state.storage.sql.exec(
      "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT GLOB '_cf_*' " +
        "ORDER BY type, name").toArray();
    const fresh = await runInDurableObject((await ownerObject()).stub,
                                           (_instance: OwnerDO, state) => schema(state));
    expect(fresh.length).toBeGreaterThan(100);
    for (let applied = 1; applied < MIGRATIONS.length; applied++) {
      const { stub } = await ownerObject();
      await runInDurableObject(stub, (_instance: OwnerDO, state) => {
        // Back to an object a deploy of `applied` migrations made: the owner's tables gone
        // (the runtime's and Lifecycle's kept), then those migrations alone.
        const sql = state.storage.sql;
        for (const { name } of sql.exec<{ name: string }>("SELECT name FROM sqlite_master " +
            "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' " +
            "AND name NOT GLOB 'cf_*'").toArray()) {
          sql.exec(`DROP TABLE "${name}"`);
        }
        expect(migrate(state.storage, MIGRATIONS.slice(0, applied))).toBe(applied);
      });
      // Its next wake migrates the rest, as a later deploy's would.
      await evictDurableObject(stub);
      const migrated = await runInDurableObject(stub, (_instance: OwnerDO, state) => {
        expect(version(state)).toBe(MIGRATIONS.length);
        return schema(state);
      });
      expect(migrated, `from version ${applied}`).toEqual(fresh);
    }
  });

  it("rolls a failing migration back whole and leaves the version where it was", async () => {
    const { stub } = await ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const broken = [...MIGRATIONS, ["CREATE TABLE probe_b (n INTEGER)", "NOT SQL"]];
      expect(() => migrate(state.storage, broken)).toThrow();
      expect(version(state)).toBe(MIGRATIONS.length);
      expect(state.storage.sql.exec(
        "SELECT name FROM sqlite_master WHERE name = 'probe_b'").toArray()).toEqual([]);
    });
  });

  it("refuses to start once a migration it applied reads differently (Flyway's validate)",
     async () => {
    const { stub, as, name } = await ownerObject();
    await stub.collectionPull(as(name), PULL);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT version, sha256 FROM schema_digests").toArray())
        .toEqual(MIGRATIONS.map((each, i) => ({ version: i + 1, sha256: migrationDigest(each) })));
      const edited = [[...MIGRATIONS[0]!, "CREATE TABLE probe_edit (n INTEGER)"],
                      ...MIGRATIONS.slice(1)];
      expect(() => migrate(state.storage, edited)).toThrow(MIGRATION_CHANGED);
      // The code as applied still starts, and a new migration after it applies.
      expect(migrate(state.storage, [...MIGRATIONS, ["CREATE TABLE probe_next (n INTEGER)"]]))
        .toBe(MIGRATIONS.length + 1);
    });
  });

  it("refuses to start once an applied migration's digest is gone, never passing by default " +
     "(review M3)", async () => {
    const { stub, as, name } = await ownerObject();
    await stub.collectionPull(as(name), PULL);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      // The review's attack: the digest row deleted, then an edited migration.
      state.storage.sql.exec("DELETE FROM schema_digests");
      const edited = [[...MIGRATIONS[0]!, "CREATE TABLE probe_unpinned (n INTEGER)"],
                      ...MIGRATIONS.slice(1)];
      expect(() => migrate(state.storage, edited)).toThrow(MIGRATION_UNPINNED);
      expect(() => migrate(state.storage, MIGRATIONS)).toThrow(MIGRATION_UNPINNED);
      expect(state.storage.sql.exec(
        "SELECT name FROM sqlite_master WHERE name = 'probe_unpinned'").toArray()).toEqual([]);
    });
  });

  it("refuses to run against a schema newer than its code", async () => {
    const { stub, as, name } = await ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      state.storage.sql.exec("UPDATE schema_version SET version = ?", MIGRATIONS.length + 1);
      expect(() => migrate(state.storage, MIGRATIONS)).toThrow(/newer than this code/);
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
    const { stub } = await ownerObject();
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
    const { stub, as, name } = await unseededObject();
    for (const binding of [as("bob", "cara"), as("cara", "cara"), as(name, `${name}x`)]) {
      const refused = await failure(() => stub.collectionPull(binding, PULL));
      expect([refused.code, refused.message])
        .toEqual(["refused", "this object belongs to another owner"]);
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
    expect([refused.code, refused.message])
      .toEqual(["refused", "this object is not named by an owner"]);
    await runInDurableObject(unnamed, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray()).toEqual([]);
    });
  });
});

describe("the object's stored owner", () => {
  it("is stored on first use", async () => {
    const { stub, as, name } = await ownerObject();
    await stub.collectionPull(as("bob"), PULL);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray())
        .toEqual([{ owner: name }]);
    });
  });

  it("is a second check: a stored owner other than the name refuses, across evictions",
     async () => {
       const { stub, as, name } = await ownerObject();
       await stub.collectionPull(as(name), PULL);
       await runInDurableObject(stub, (_instance: OwnerDO, state) => {
         state.storage.sql.exec("UPDATE object_owner SET owner = 'someone-else'");
       });
       await evictDurableObject(stub);
       const refused = await failure(() => stub.collectionPull(as(name), PULL));
       expect([refused.code, refused.message])
         .toEqual(["refused", "this object belongs to another owner"]);
     });

  it("refuses an invalid principal before binding anything", async () => {
    const { stub } = await unseededObject();
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
    const { stub, as, name } = await ownerObject();
    await stub.collectionPull(as(name), PULL);
    expect((await failure(() => stub.collectionPull(as("bob", "bob"), PULL))).code)
      .toBe("refused");
    expect((await failure(() => stub.collectionPull(as(name), {
      ...PULL, limit: 0,
    }))).code).toBe("invalid");
  });
});

describe("the object's RPC surface", () => {
  // Every public method of the class is an RPC any binding can call, so the door table
  // (`contract/doors.json`) is the object's whole surface. `alarm` is the runtime's, handed to
  // Lifecycle, and refused over RPC. Claiming, marking and settling a wake have no door: only
  // the `wakes` lane runs them, in process, with the doorbell's sender.
  it("is exactly its doors: nothing claims, marks or settles a wake", async () => {
    const methods = Object.getOwnPropertyNames(OwnerDO.prototype)
      .filter((method) => method !== "constructor" && !RESERVED.includes(method)).sort();
    expect(methods).toEqual(doorTable().map((row) => row.door).sort());
    expect(Object.getOwnPropertyNames(DurableObject.prototype)).toEqual(["constructor"]);
    const { stub, name } = await ownerObject();
    const call = (method: string) => {
      // The test pool's stub makes a property's RPC lookup eagerly on access, a promise
      // that rejects at once for a method the object lacks and is awaited only once the
      // call's queued turn runs; handled here at access, so it is never reported unhandled.
      const prop = (stub as unknown as
        Record<string, (...args: unknown[]) => Promise<unknown>>)[method]!;
      void Promise.resolve(prop).catch(() => {});
      return prop({ owner: name, caller: name, origin: null }, {});
    };
    // Read off the class itself: an RPC call on a method the object lacks rejects in the
    // pool's stub outside the call's own promise (round 6's unhandled rejection).
    for (const method of ["claimWake", "markWake", "settleWake", "dueWakes", "wakeSender"]) {
      expect(Reflect.has(OwnerDO.prototype, method)).toBe(false);
    }
    expect((await failure(() => call("alarm"))).message).toMatch(/reserved method/);
  });
});

describe("no reach past the object", () => {
  it("has no ctx.exports, so no stub to another owner's object", async () => {
    const { stub } = await ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect((state as DurableObjectState & { exports?: unknown }).exports).toBeUndefined();
    });
  });
});
