/** The owner object: its schema migrates on wake, and it serves one owner only. */
import { evictDurableObject, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import type { OwnerDO } from "../src/owner-do";
import { MIGRATIONS, migrate } from "../src/schema";
import { as, failure, ownerObject } from "./support";

function version(state: DurableObjectState): number {
  return state.storage.sql.exec<{ version: number }>("SELECT version FROM schema_version").one()
    .version;
}

const PULL = { collection: "board", after: 0, limit: 1 };

describe("schema migration on wake", () => {
  it("applies every migration before the first call", async () => {
    const stub = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(version(state)).toBe(MIGRATIONS.length);
      const tables = state.storage.sql.exec<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
      ).toArray().map((t) => t.name);
      for (const table of ["collection_history", "collection_rows", "collection_seqs",
                           "object_owner", "schema_version"]) {
        expect(tables).toContain(table);
      }
    });
  });

  it("keeps data and applies nothing twice across an eviction", async () => {
    const stub = ownerObject();
    const written = await stub.collectionWrite(as("alice"), {
      collection: "board", row_id: "t", body: "1", expected_etag: "", private: false,
    });
    await evictDurableObject(stub);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(version(state)).toBe(MIGRATIONS.length);
    });
    expect((await stub.collectionGet(as("alice"), { collection: "board", row_id: "t" })).row)
      .toEqual("row" in written ? written.row : null);
  });

  it("applies only the migrations past the stored version, in order", async () => {
    const stub = ownerObject();
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
    const stub = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const broken = [...MIGRATIONS, ["CREATE TABLE probe_b (n INTEGER)", "NOT SQL"]];
      expect(() => migrate(state.storage, broken)).toThrow();
      expect(version(state)).toBe(MIGRATIONS.length);
      expect(state.storage.sql.exec(
        "SELECT name FROM sqlite_master WHERE name = 'probe_b'").toArray()).toEqual([]);
    });
  });

  it("refuses to run against a schema newer than its code", async () => {
    const stub = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      state.storage.sql.exec("UPDATE schema_version SET version = ?", MIGRATIONS.length + 1);
      expect(() => migrate(state.storage)).toThrow(/newer than this code/);
    });
    await evictDurableObject(stub);
    expect((await failure(() => stub.collectionPull(as("alice"), PULL))).message)
      .toMatch(/newer than this code/);
  });
});

describe("the object's owner", () => {
  it("is stored on first use and every other owner is refused", async () => {
    const stub = ownerObject();
    await stub.collectionPull(as("bob"), PULL);
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray())
        .toEqual([{ owner: "alice" }]);
    });
    expect((await failure(() => stub.collectionPull(as("cara", "cara"), PULL))).message)
      .toBe("this object belongs to another owner");
    const write = failure(() => stub.collectionWrite(as("alice", "bob"), {
      collection: "board", row_id: "t", body: "1", expected_etag: "", private: false,
    }));
    expect((await write).message).toBe("this object belongs to another owner");
  });

  it("survives an eviction", async () => {
    const stub = ownerObject();
    await stub.collectionPull(as("alice"), PULL);
    await evictDurableObject(stub);
    expect((await failure(() => stub.collectionPull(as("bob", "bob"), PULL))).message)
      .toBe("this object belongs to another owner");
  });

  it("refuses an invalid principal before binding anything", async () => {
    const stub = ownerObject();
    for (const binding of [{ owner: "", caller: "bob" }, { owner: " alice", caller: "bob" },
                           { owner: "alice", caller: "bo\nb" },
                           { owner: "a".repeat(513), caller: "bob" }]) {
      expect((await failure(() => stub.collectionPull(binding, PULL))).message)
        .toBe("invalid owner principal");
    }
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      expect(state.storage.sql.exec("SELECT owner FROM object_owner").toArray()).toEqual([]);
    });
  });

  it("keeps the error's kind across RPC", async () => {
    const stub = ownerObject();
    await stub.collectionPull(as("alice"), PULL);
    expect((await failure(() => stub.collectionPull(as("bob", "bob"), PULL))).name)
      .toBe("Refused");
    expect((await failure(() => stub.collectionPull(as("alice"), {
      ...PULL, limit: 0,
    }))).name).toBe("Invalid");
  });
});
