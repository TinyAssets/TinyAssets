/**
 * S34: the one revision primitive, in the owner's object.
 *
 * Ported from `tests/test_collections.py` (branch `s5b-board`), test for test. Where
 * the Python test monkeypatched a bound, this one runs the primitive on the object's
 * own storage with that bound lowered (`inObject`). Where it went through the router's
 * owner channel, this one goes through the object's RPC door with a binding.
 */
import { describe, expect, it } from "vitest";

import { Collections, Conflict, LIMITS, type Row } from "../src/collections";
import { Invalid, Refused } from "../src/errors";
import { etags, failure, history, inObject, type Owner, ownerObject, rows } from "./support";

const ROOM = "room:chess:main";

async function write(o: Owner, caller: string, fields: {
  collection?: string; row_id: string; body: string; expected_etag?: string;
  private?: boolean; history_op?: string;
}): Promise<Row> {
  const answer = await o.stub.collectionWrite(o.as(caller), {
    collection: fields.collection ?? "board", row_id: fields.row_id, body: fields.body,
    expected_etag: fields.expected_etag ?? "", private: fields.private ?? false,
    ...(fields.history_op === undefined ? {} : { history_op: fields.history_op }),
  });
  if (!("row" in answer)) {
    throw new Error(`unexpected conflict: ${JSON.stringify(answer)}`);
  }
  return answer.row;
}

describe("the revision primitive", () => {
  it("a board race has one winner and one conflict carrying the current row", async () => {
    const o = ownerObject();
    const { stub, name: alice, as } = o;
    const first = await write(o, alice, { row_id: "task-1", body: '{"lane":"todo"}' });
    const outcomes = await Promise.all([[alice, "doing"], ["muse", "done"]].map(
      ([caller, lane]) => stub.collectionWrite(as(caller!), {
        collection: "board", row_id: "task-1", body: `{"lane":"${lane}"}`,
        expected_etag: first.etag, private: false,
      })));
    const [winner] = outcomes.flatMap((o) => ("row" in o ? [o.row] : []));
    const [lost] = outcomes.flatMap((o) => ("conflict" in o ? [o.conflict] : []));
    expect(winner).toBeDefined();
    expect(lost).toEqual(winner);
    expect([winner!.revision, winner!.seq]).toEqual([2, 2]);
    expect(await rows(stub)).toEqual([["board", "task-1", 2, 2, winner!.writer, 0,
                                       winner!.body]]);
  });

  it("payloads are verbatim and pull follows the sequence", async () => {
    const o = ownerObject();
    const { stub, name: alice, as } = o;
    const body = '{ "b": 1,  "a": "café" }';
    const one = await write(o, alice, { row_id: "t1", body });
    await write(o, alice, { collection: "room:chess:main", row_id: "x", body: "{}" });
    const two = await write(o, alice, { row_id: "t2", body: "[]" });
    const pull = (after: number, limit: number) =>
      stub.collectionPull(as("bob"), { collection: "board", after, limit });
    expect(await pull(0, 1)).toEqual({ cursor: 1, rows: [one] });
    expect(one.body).toBe(body);
    expect(await pull(1, 10)).toEqual({ cursor: 2, rows: [two] });
    expect(await pull(2, 10)).toEqual({ cursor: 2, rows: [] });
    expect(await stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t1", body: "{}", expected_etag: "", private: false,
    })).toEqual({ conflict: one });
  });

  it("a private row is its writer's own", async () => {
    const o = ownerObject();
    const { stub, as } = o;
    const mine = await write(o, "bob", { collection: ROOM, row_id: "p", body: "secret",
                                         private: true });
    expect(await stub.collectionPull(as("cara"), { collection: ROOM, after: 0, limit: 10 }))
      .toEqual({ cursor: 1, rows: [] });
    expect((await stub.collectionPull(as("bob"), { collection: ROOM, after: 0, limit: 10 }))
      .rows).toEqual([mine]);
    const taken = await failure(() => write(o, "cara", {
      collection: ROOM, row_id: "p", body: "x", expected_etag: mine.etag, private: true }));
    expect([taken.name, taken.message]).toEqual(["Refused", "a private row is its writer's own"]);
    const exposed = await failure(() => write(o, "bob", {
      collection: ROOM, row_id: "p", body: "x", expected_etag: mine.etag, private: false }));
    expect(exposed.name).toBe("Invalid");
    expect((await rows(stub))[0]?.[6]).toBe("secret");
  });

  it("get reads one row by key as pull would show it", async () => {
    const o = ownerObject();
    const { stub, as } = o;
    const mine = await write(o, "bob", { collection: ROOM, row_id: "p", body: "secret",
                                         private: true });
    const shared = await write(o, "bob", { collection: ROOM, row_id: "s", body: "turn" });
    const get = (caller: string, collection: string, row_id: string) =>
      stub.collectionGet(as(caller), { collection, row_id });
    expect(await get("bob", ROOM, "p")).toEqual({ row: mine });
    expect(await get("cara", ROOM, "p")).toEqual({ row: null });
    expect(await get("cara", ROOM, "s")).toEqual({ row: shared });
    expect(await get("bob", ROOM, "none")).toEqual({ row: null });
    expect(await get("bob", "board", "s")).toEqual({ row: null });
    // The caller is the binding's, never a request field.
    const named = await failure(() => stub.collectionGet(as("cara"), {
      collection: ROOM, row_id: "p", writer: "bob" } as never));
    expect(named.message).toBe("invalid collection request");
    expect((await failure(() => get("cara", ROOM, ""))).name).toBe("Invalid");
    expect((await rows(stub)).map((r) => r[1])).toEqual(["p", "s"]); // a get writes nothing
  });

  it("a full collection refuses a new row", async () => {
    const { stub, name: alice } = ownerObject();
    await inObject(stub, { maxRows: 1 }, (store) => {
      const row = store.write(alice, alice, "board", "a", "1", "", false);
      expect(() => store.write(alice, alice, "board", "b", "1", "", false))
        .toThrow(/full/);
      expect(store.write(alice, alice, "board", "a", "2", row.etag, false).revision)
        .toBe(2);
    });
    expect(LIMITS.maxRows).toBe(1000);
  });

  it("an owner's collections are bounded in rows and bytes", async () => {
    // Across every collection of one owner; another owner's object is untouched.
    const { stub, name: alice } = ownerObject();
    await inObject(stub, { maxOwnerRows: 2, maxOwnerBytes: 24 }, (store) => {
      // A body counts twice: the row, and its history.
      const a = store.write(alice, alice, "room:a:main", "x", "1234", "", false);
      store.write(alice, alice, "room:b:main", "x", "1234", "", false);
      expect(() => store.write(alice, alice, "room:c:main", "x", "", "", false))
        .toThrow("the owner's collections are full");
      expect(() => store.write(alice, alice, "room:a:main", "x", "caféé", a.etag, false))
        .toThrow("the owner's collections are full"); // 16 + 14 - 4
      expect(store.write(alice, alice, "room:a:main", "x", "123456", a.etag, false)
        .revision).toBe(2);
    });
    expect(await rows(stub)).toHaveLength(2);
    const { stub: bobs } = ownerObject();
    await inObject(bobs, { maxOwnerRows: 2, maxOwnerBytes: 24 }, (store) => {
      expect(store.write("bob", "bob", "room:c:main", "x", "1", "", false).revision).toBe(1);
    });
  });

  it("the RPC door carries the binder's caller and a conflict", async () => {
    // The verified caller is the writer; a lost swap is an answer, not a refusal.
    const o = ownerObject();
    const { stub, name: alice, as } = o;
    const row = await write(o, "bob", { row_id: "t", body: "1" });
    expect(row.writer).toBe("bob");
    expect((await rows(stub))[0]?.[4]).toBe("bob");
    expect(await stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t", body: "2", expected_etag: "", private: false,
    })).toEqual({ conflict: row });
    const forged = await failure(() => stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t", body: "2", expected_etag: "", private: false,
      writer: alice } as never));
    expect(forged.message).toBe("invalid collection request");
    const elsewhere = await failure(() => stub.collectionPull(as("bob", "cara"), {
      collection: "board", after: 0, limit: 1 }));
    expect(elsewhere.message).toBe("this object belongs to another owner");
  });

  it("a player at its row share still takes a turn on a shared row", async () => {
    // Only a creation counts against the share; a takeover charges the row's bytes.
    const { stub, name: alice } = ownerObject();
    const room = "room:go:main";
    await inObject(stub, { maxWriterRows: 2 }, (store) => {
      const put = (caller: string, rowId: string, etag = "") =>
        store.write(alice, caller, room, rowId, "stone", etag, false);
      const cell0 = put("bob", "cell0");
      put("cara", "cell1");
      put("cara", "cell2");
      expect(() => put("cara", "cell3")).toThrow("this writer's share");
      expect(put("cara", "cell0", cell0.etag).writer).toBe("cara");
    });
    const cell1 = (await etags(stub))["cell1"]!;
    await inObject(stub, { maxWriterRows: 2, maxWriterBytes: 4 }, (store) => {
      // Takes over all 5 bytes.
      expect(() => store.write(alice, "bob", room, "cell1", "stone", cell1, false))
        .toThrow("this writer's share");
    });
  });

  it("two accounts cannot launder a row share", async () => {
    // Rows count against their creator: a takeover by a second account frees nothing.
    const { stub, name: alice, as } = ownerObject();
    const room = "room:go:main";
    const limits = { maxWriterRows: 2 };
    await inObject(stub, limits, (store) => {
      const put = (caller: string, rowId: string, etag = "", body = "xx") =>
        store.write(alice, caller, room, rowId, body, etag, false);
      const made = [0, 1].map((n) => put("m1", `r${n}`));
      for (const row of made) {
        put("m2", row.row_id, row.etag, ""); // m2 takes both over
      }
      expect(() => put("m1", "r2")).toThrow("this writer's share"); // m1's rows live on
      put("honest", "h0");
      put("honest", "h1");
    });
    // The owner's clear-by-writer matches creator or writer: m1's rows go, though m2
    // last wrote them, and m1 may create again.
    expect(await stub.collectionClear(as(alice), { collection: room, writer: "m1" }))
      .toEqual({ deleted: 2 });
    await inObject(stub, limits, (store) => {
      store.write(alice, "m1", room, "r2", "xx", "", false);
    });
    expect(Object.keys(await etags(stub)).sort()).toEqual(["h0", "h1", "r2"]);
  });

  it("a seq collision fails loudly and deletes nothing", async () => {
    // A missing high-water mark still numbers past the rows; a clash never replaces one.
    const { stub, name: alice } = ownerObject();
    await inObject(stub, {}, (store, state) => {
      const put = (on: Collections, rowId: string) =>
        on.write(alice, alice, "board", rowId, rowId, "", false);
      expect(put(store, "a").seq).toBe(1);
      state.storage.sql.exec("DELETE FROM collection_seqs"); // as a store before the table
      expect(put(store, "b").seq).toBe(2);
      class Stale extends Collections {
        protected override top(): number {
          return 0;
        }
      }
      expect(() => put(new Stale(state.storage), "c")).toThrow(/UNIQUE/); // seq 1 is a's
    });
    expect(Object.keys(await etags(stub)).sort()).toEqual(["a", "b"]);
  });

  it("one visitor fills only its own share and the owner clears", async () => {
    // A visitor stops at its share; another still writes; one room cannot starve another.
    const { stub, name: alice, as } = ownerObject();
    const limits = { maxWriterRows: 2, maxCollectionBytes: 12 };
    const put = (store: Collections, caller: string, rowId: string, body = "1",
                 collection = ROOM) => store.write(alice, caller, collection, rowId, body, "",
                                                   true);
    await inObject(stub, limits, (store) => {
      put(store, "mallory", "a");
      put(store, "mallory", "b");
      expect(() => put(store, "mallory", "c")).toThrow("this writer's share");
      put(store, "cara", "c1");
      // 3 rows and their history, 6, + 8 > 12
      expect(() => put(store, alice, "big", "1234")).toThrow("the collection is full");
      put(store, alice, "big", "1234", "room:go:main");
    });
    const refused = await failure(() => stub.collectionClear(as("mallory"), {
      collection: ROOM, writer: null }));
    expect(refused.message).toBe("only the owner may clear collection rows");
    expect(await stub.collectionClear(as(alice), { collection: ROOM, writer: "mallory" }))
      .toEqual({ deleted: 2 });
    await inObject(stub, limits, (store) => put(store, "mallory", "c"));
    expect(await stub.collectionClear(as(alice), { collection: ROOM, writer: null }))
      .toEqual({ deleted: 2 });
    // A clear never moves a reader's cursor back: the next row is past every old seq.
    expect((await inObject(stub, limits, (store) => put(store, "cara", "z"))).seq).toBe(5);
    expect((await stub.collectionPull(as("cara"), { collection: ROOM, after: 4, limit: 10 }))
      .cursor).toBe(5);
  });
});

describe("the primitive's history", () => {
  it("records each write, delete and clear in its transaction", async () => {
    const o = ownerObject();
    const { stub, name: alice, as } = o;
    const room = "room:go:main";
    const put = (rowId: string, body: string, etag = "", caller = "bob", op = "write") =>
      write(o, caller, { collection: room, row_id: rowId, body, expected_etag: etag,
                         history_op: op });
    let a = await put("a", "1");
    a = await put("a", "2", a.etag, "cara", "move");
    const b = await put("b", "x");
    expect(await stub.collectionWrite(as("bob"), {
      collection: room, row_id: "a", body: "3", expected_etag: "stale", private: false,
    })).toHaveProperty("conflict"); // a lost swap writes no history
    expect(await stub.collectionDelete(as(alice), {
      collection: room, row_id: "a", expected_etag: a.etag })).toEqual({ deleted: 1 });
    expect(await stub.collectionClear(as(alice), { collection: room, writer: "bob" }))
      .toEqual({ deleted: 1 });
    // A delete or clear drops the row's bodies and keeps one body-less entry.
    expect(await history(stub)).toEqual([[room, "a", 3, "delete", "", alice],
                                         [room, "b", 2, "clear", "", alice]]);
    expect((await put("a", "again")).revision).toBe(4); // never a revision the row had
    expect(b.revision).toBe(1);
    const erase = await failure(() => put("c", "1", "", "bob", "delete"));
    expect(erase.message).toMatch(/history_op/);
  });

  it("keeps the last revisions and counts in the byte bounds", async () => {
    const { stub, name: alice, as } = ownerObject();
    const row = await inObject(stub, { maxRevisions: 3 }, (store) => {
      let last = { etag: "" };
      for (let n = 0; n < 5; n++) {
        last = store.write(alice, alice, "board", "t", `v${n}`, last.etag, false);
      }
      return last;
    });
    expect((await history(stub)).map((h) => [h[2], h[4]])).toEqual(
      [[3, "v2"], [4, "v3"], [5, "v4"]]);
    expect((await stub.collectionHistory(as("bob"), { collection: "board", row_id: "t" }))
      .entries.map((e) => e.body)).toEqual(["v2", "v3", "v4"]);
    await inObject(stub, { maxRevisions: 3, maxCollectionBytes: 9 }, (store) => {
      // Six bytes of history and two of the row: a new row of two needs four more.
      expect(() => store.write(alice, alice, "board", "u", "ab", "", false))
        .toThrow("the collection is full");
      // A write that prunes an old entry frees its bytes in the same transaction.
      expect(store.write(alice, alice, "board", "t", "v5", row.etag, false).revision)
        .toBe(6);
    });
  });

  it("is visible as its row is", async () => {
    const o = ownerObject();
    const { stub, name: alice, as } = o;
    const mine = await write(o, "bob", { collection: ROOM, row_id: "p", body: "secret",
                                         private: true });
    await write(o, "bob", { collection: ROOM, row_id: "s", body: "shared" });
    const entries = async (caller: string, rowId: string) =>
      (await stub.collectionHistory(as(caller), { collection: ROOM, row_id: rowId })).entries;
    expect((await entries("bob", "p")).map((e) => e.body)).toEqual(["secret"]);
    expect(await entries("cara", "p")).toEqual([]);
    expect(await entries(alice, "p")).toEqual([]);
    expect((await entries("cara", "s")).map((e) => [e.revision, e.op, e.writer]))
      .toEqual([[1, "write", "bob"]]);
    // As in the Python test, the store deletes for whichever caller it is handed; the
    // owner-only rule is the RPC door's, tested below.
    await inObject(stub, {}, (store) => store.delete("bob", ROOM, "p", mine.etag));
    expect(await entries("bob", "p")).toEqual([]); // a deleted row shows no history
    const extra = await failure(() => stub.collectionHistory(as("bob"), {
      collection: ROOM, row_id: "s", limit: 11 } as never));
    expect(extra.message).toBe("invalid collection request");
  });
});

/** A seeded generator (mulberry32), so a failing sequence replays from its seed. */
function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Usage = Record<string, [number, number, number]>;

/** The bounds' counters recounted from the rows and history, by the same scopes. */
function recount(sql: SqlStorage): Usage {
  const used: Usage = {};
  const add = (key: string, rows: number, rowBytes: number, historyBytes: number) => {
    const [r, b, h] = used[key] ?? [0, 0, 0];
    used[key] = [r + rows, b + rowBytes, h + historyBytes];
  };
  const bytes = (text: string) => new TextEncoder().encode(text).byteLength;
  for (const row of sql.exec<{ collection: string; writer: string; creator: string;
                               body: string }>(
    "SELECT collection, writer, creator, body FROM collection_rows").toArray()) {
    const size = bytes(row.body);
    add("owner||", 1, size, 0);
    add(`collection|${row.collection}|`, 1, size, 0);
    add(`writer|${row.collection}|${row.writer}`, 1, size, 0);
    add(`creator|${row.collection}|${row.creator}`, 1, size, 0);
  }
  for (const entry of sql.exec<{ collection: string; writer: string; body_json: string }>(
    "SELECT collection, writer, body_json FROM collection_history").toArray()) {
    const size = bytes(entry.body_json);
    add("owner||", 0, 0, size);
    add(`collection|${entry.collection}|`, 0, 0, size);
    add(`writer|${entry.collection}|${entry.writer}`, 0, 0, size);
  }
  return Object.fromEntries(Object.entries(used).filter(([, v]) => v.some((n) => n !== 0)));
}

function counters(sql: SqlStorage): Usage {
  return Object.fromEntries(sql.exec<{ scope: string; collection: string; who: string;
                                       rows: number; row_bytes: number;
                                       history_bytes: number }>(
    "SELECT * FROM collection_usage").toArray().map((u) => [
    `${u.scope}|${u.collection}|${u.who}`, [u.rows, u.row_bytes, u.history_bytes]]));
}

describe("the bounds' counters", () => {
  it.each([1, 7, 42, 1009, 65537])("equal a recount after every step (seed %i)", async (seed) => {
    const { stub, name: owner } = ownerObject();
    const outcomes = await inObject(stub, {
      maxRevisions: 3, maxWriterRows: 4, maxWriterBytes: 160, maxRows: 9,
      maxCollectionBytes: 600, maxOwnerRows: 20, maxOwnerBytes: 1400,
    }, (store, state) => {
      const random = seeded(seed);
      const pick = <T>(items: readonly T[]): T => items[Math.floor(random() * items.length)]!;
      const callers = [owner, "bob", "cara", "dan"];
      const collections = ["board", "room:go:main", "room:go:side"];
      const letters = ["a", "é", "€", "𝄞", "z"];
      const counted: Record<string, number> = {};
      for (let step = 0; step < 300; step++) {
        const collection = pick(collections);
        const rowId = `r${Math.floor(random() * 6)}`;
        const caller = pick(callers);
        const roll = random();
        const current = store.get(caller, collection, rowId).row;
        let outcome = "done";
        try {
          if (roll < 0.75) {
            const body = Array.from({ length: Math.floor(random() * 24) }, () => pick(letters))
              .join("");
            const etag = current !== null && random() < 0.9 ? current.etag
              : random() < 0.5 ? "" : "stale";
            store.write(owner, caller, collection, rowId, body, etag,
                        current?.private ?? random() < 0.2, pick(["write", "move", "claim"]));
          } else if (roll < 0.9) {
            store.delete(owner, collection, rowId, current?.etag ?? "stale");
          } else {
            store.clear(owner, collection, random() < 0.5 ? null : pick(callers));
          }
        } catch (error) {
          outcome = (error as Error).name;
        }
        counted[outcome] = (counted[outcome] ?? 0) + 1;
        expect(counters(state.storage.sql), `seed ${seed}, step ${step}`)
          .toEqual(recount(state.storage.sql));
      }
      return counted;
    });
    // The sequence reaches the bounds, the swaps and the erases, not only plain writes.
    expect(Object.keys(outcomes).sort()).toEqual(["Conflict", "Refused", "done"]);
  });

  it("are kept by the store alone: a history entry is never changed", async () => {
    const { stub, name: owner } = ownerObject();
    await inObject(stub, {}, (store, state) => {
      store.write(owner, owner, "board", "t", "v1", "", false);
      expect(() => state.storage.sql.exec("UPDATE collection_history SET body_json = 'x'"))
        .toThrow(/a history entry is never changed/);
    });
  });
});

describe("the RPC door's fields", () => {
  it("refuses a field sent as undefined, as the Python door refused None", async () => {
    const o = ownerObject();
    const { stub, as } = o;
    const refused = await failure(() => stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t", body: "1", expected_etag: "", private: false,
      history_op: undefined } as never));
    expect([refused.name, refused.message]).toEqual(["Invalid", "invalid collection request"]);
    expect(await rows(stub)).toEqual([]);
  });

  it("refuses a cursor or a limit that is not a whole number in range", async () => {
    const { stub, as } = ownerObject();
    for (const [after, limit] of [[1.5, 1], [Number.NaN, 1], [2 ** 53, 1], [-1, 1], ["1", 1],
                                  [true, 1], [0, 1.5], [0, Number.POSITIVE_INFINITY], [0, "2"],
                                  [0, 101], [0, 0], [0, null]]) {
      const refused = await failure(() => stub.collectionPull(as("bob"), {
        collection: "board", after, limit } as never));
      expect(refused.name, `after ${String(after)}, limit ${String(limit)}`).toBe("Invalid");
    }
  });
});

describe("the RPC door's owner-only erases", () => {
  it("refuses a delete by anyone but the owner, and a stale delete is a conflict", async () => {
    const o = ownerObject();
    const { stub, name: alice, as } = o;
    const row = await write(o, "bob", { row_id: "t", body: "1" });
    const refused = await failure(() => stub.collectionDelete(as("bob"), {
      collection: "board", row_id: "t", expected_etag: row.etag }));
    expect([refused.name, refused.message])
      .toEqual(["Refused", "only the owner may delete collection rows"]);
    expect(await stub.collectionDelete(as(alice), {
      collection: "board", row_id: "t", expected_etag: "stale" })).toEqual({ conflict: row });
    expect(await stub.collectionDelete(as(alice), {
      collection: "board", row_id: "gone", expected_etag: "" })).toEqual({ conflict: null });
    expect(await stub.collectionDelete(as(alice), {
      collection: "board", row_id: "t", expected_etag: row.etag })).toEqual({ deleted: 1 });
    expect(await rows(stub)).toEqual([]);
  });
});

describe("the primitive's errors", () => {
  it("names each kind", async () => {
    const { stub, name: alice } = ownerObject();
    await inObject(stub, { maxRows: 1 }, (store) => {
      store.write(alice, alice, "board", "a", "1", "", false);
      expect(() => store.write(alice, alice, "board", "b", "1", "", false))
        .toThrow(Refused);
      expect(() => store.write(alice, alice, "board", "a", "1", "", false))
        .toThrow(Conflict);
      expect(() => store.write(alice, alice, "board", "a\u0000", "1", "", false))
        .toThrow(Invalid);
      expect(() => store.write(alice, alice, "board", "a", "\ud800", "", false))
        .toThrow(Invalid);
    });
  });
});
