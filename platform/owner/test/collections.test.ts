/**
 * The one revision primitive, in the owner's object: on the object's own storage
 * (`inObject`), filled to production's bound or held to the shares the owner's policy sets,
 * and through the object's RPC door with a binding.
 */
import type { WrittenRow } from "../src/collections";
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { Collections, LIMITS, type Row } from "../src/collections";

import { MIGRATIONS } from "../src/schema";
import {
  etags, failure, fillHistory, fillRows, history, inFilled, inObject, type Owner, ownerObject,
  rows, setFiles, storesOn,
} from "./support";
import { PLACE_BYTES } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";
import { migrate } from "../../contract/src/migrate";
import type { OwnerDO } from "../src/owner-do";
import type { Origin } from "../../contract/src/binding";
import { DOT, MUSE, origin, rig } from "./ledger-support";

const ROOM = "room:chess:main";

async function write(o: Owner, caller: string, fields: {
  collection?: string; row_id: string; body: string; expected_etag?: string;
  private?: boolean; history_op?: string;
}): Promise<WrittenRow> {
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

/** A written row as a read shows it: its body in place of its size. */
function shownAs(row: WrittenRow, body: string): Row {
  const { body_bytes: bytes, ...rest } = row;
  expect(bytes).toBe(new TextEncoder().encode(body).byteLength);
  return { ...rest, body } as Row;
}

/** What `act` throws. */
function thrown(act: () => unknown): unknown {
  try {
    act();
  } catch (error) {
    return error;
  }
  throw new Error("expected a throw");
}

describe("the revision primitive", () => {
  it("a board race has one winner and one conflict carrying the current row", async () => {
    const o = await ownerObject();
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
    // The write answers its row without its body; the conflict, the row whole.
    const { body, ...rest } = lost as Row;
    expect({ ...rest, body_bytes: body.length }).toEqual(winner);
    expect(winner).not.toHaveProperty("body");
    expect(winner!.revision).toBe(2);
    expect(await rows(stub)).toEqual([["board", "task-1", 2, 2, winner!.writer, 0, body]]);
  });

  it("payloads are verbatim and pull follows the sequence", async () => {
    const o = await ownerObject();
    const { stub, name: alice, as } = o;
    const body = '{ "b": 1,  "a": "café" }';
    const one = await write(o, alice, { row_id: "t1", body });
    await write(o, alice, { collection: "room:chess:main", row_id: "x", body: "{}" });
    const two = await write(o, alice, { row_id: "t2", body: "[]" });
    const pull = (limit: number, after?: string) => stub.collectionPull(
      as("bob"), { collection: "board", limit, ...(after === undefined ? {} : { after }) });
    const first = await pull(1);
    expect(first).toEqual({ cursor: expect.stringMatching(/^[0-9a-f]{64}$/),
                            rows: [shownAs(one, body)] });
    const next = await pull(10, first.cursor);
    expect(next.rows).toEqual([shownAs(two, "[]")]);
    expect((await pull(10, next.cursor)).rows).toEqual([]);
    // A cursor is the object's own: one made up, or a digest's, is refused.
    for (const after of ["0".repeat(64), "7", (await stub.boardDigest(as("bob"), {
      limit: 1 })).cursor]) {
      expect((await failure(() => pull(10, after))).code).toBe("cursor_invalid");
    }
    expect(await stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t1", body: "{}", expected_etag: "", private: false,
    })).toEqual({ conflict: shownAs(one, body) });
  });

  it("a private row is its writer's own, under an id apart from every other row's",
     async () => {
    const o = await ownerObject();
    const { stub, name: alice, as } = o;
    const mine = await write(o, "bob", { collection: ROOM, row_id: "p", body: "secret",
                                         private: true });
    expect((await stub.collectionPull(as("cara"), { collection: ROOM, limit: 10 })).rows)
      .toEqual([]);
    expect((await stub.collectionPull(as("bob"), { collection: ROOM, limit: 10 }))
      .rows).toEqual([shownAs(mine, "secret")]);
    // Its id is bob's own: cara's private row and the public row of that id are rows of
    // their own, and bob's etag is no other row's.
    const hers = await write(o, "cara", { collection: ROOM, row_id: "p", body: "cara's",
                                          private: true });
    const open = await write(o, "cara", { collection: ROOM, row_id: "p", body: "everyone's" });
    expect(await stub.collectionWrite(as("cara"), { collection: ROOM, row_id: "p", body: "x",
                                                    expected_etag: mine.etag, private: true }))
      .toEqual({ conflict: shownAs(hers, "cara's") });
    // A public claim is one for everyone, bob and the owner too.
    for (const caller of ["bob", alice]) {
      expect(await stub.collectionWrite(as(caller), { collection: ROOM, row_id: "p",
                                                      body: "mine", expected_etag: "",
                                                      private: false }))
        .toEqual({ conflict: shownAs(open, "everyone's") });
    }
    expect((await stub.collectionPull(as("bob"), { collection: ROOM, limit: 10 })).rows)
      .toEqual([shownAs(mine, "secret"), shownAs(open, "everyone's")]);
    expect((await rows(stub)).map((row) => [row[4], row[5], row[6]]))
      .toEqual([["bob", 1, "secret"], ["cara", 1, "cara's"], ["cara", 0, "everyone's"]]);
    // The owner reads every row it could: the public one and its own, never another's.
    const own = await write(o, alice, { collection: ROOM, row_id: "p", body: "the owner's",
                                        private: true });
    expect((await stub.collectionPull(as(alice), { collection: ROOM, limit: 10 })).rows)
      .toEqual([shownAs(open, "everyone's"), shownAs(own, "the owner's")]);
  });

  it("get reads one row by key as pull would show it", async () => {
    const o = await ownerObject();
    const { stub, as } = o;
    const mine = await write(o, "bob", { collection: ROOM, row_id: "p", body: "secret",
                                         private: true });
    const shared = await write(o, "bob", { collection: ROOM, row_id: "s", body: "turn" });
    const get = (caller: string, collection: string, row_id: string, isPrivate?: boolean) =>
      stub.collectionGet(as(caller), {
        collection, row_id, ...(isPrivate === undefined ? {} : { private: isPrivate }) });
    expect(await get("bob", ROOM, "p", true)).toEqual({ row: shownAs(mine, "secret") });
    // Left out or false, the public row of that id; true, the caller's own.
    expect(await get("bob", ROOM, "p")).toEqual({ row: null });
    expect(await get("bob", ROOM, "p", false)).toEqual({ row: null });
    expect(await get("cara", ROOM, "p", true)).toEqual({ row: null });
    expect(await get("bob", ROOM, "s", true)).toEqual({ row: null });
    expect((await failure(() => stub.collectionGet(as("bob"), {
      collection: ROOM, row_id: "p", private: "yes" } as never))).code).toBe("invalid");
    expect(await get("cara", ROOM, "s")).toEqual({ row: shownAs(shared, "turn") });
    expect(await get("bob", ROOM, "none")).toEqual({ row: null });
    expect(await get("bob", "board", "s")).toEqual({ row: null });
    // The caller is the binding's, never a request field.
    const named = await failure(() => stub.collectionGet(as("cara"), {
      collection: ROOM, row_id: "p", writer: "bob" } as never));
    expect(named.message).toBe("invalid collection request");
    expect((await failure(() => get("cara", ROOM, ""))).code).toBe("invalid");
    expect((await rows(stub)).map((r) => r[1])).toEqual(["p", "s"]); // a get writes nothing
  });

  it("bounds the owner's rows by what they take, names and index entries counted",
     { tags: ["large"] }, async () => {
    // Across every collection of one owner; another owner's object is untouched.
    const { stub, name: alice } = await ownerObject();
    const charge = (state: DurableObjectState) => state.storage.sql.exec<{ rows_charge: number }>(
      "SELECT rows_charge FROM collection_space").one().rows_charge;
    const one = await inObject(stub, (store, state) => {
      store.write(alice, alice, "room:a:main", "x", "1234", "", false);
      return charge(state);
    });
    // A row with long names takes more: the charge counts every byte of them, four times.
    const long = await inObject(stub, (store, state) => {
      store.write(alice, alice, `room:${"n".repeat(250)}`, "y".repeat(256), "1234", "", false);
      return charge(state) - one;
    });
    expect(long).toBeGreaterThan(2 * one);
    await inFilled(stub, (store, state) => {
      fillRows(store, alice);
      expect(charge(state)).toBeLessThanOrEqual(LIMITS.maxRowsCharge);
      expect(() => store.write(alice, alice, "room:c:main", "x", "1", "", false))
        .toThrow("the owner's collections are full");
      // A write that takes no more passes, so an owner at the bound still writes.
      const a = store.get(alice, "room:a:main", "x", false).row!;
      expect(store.write(alice, alice, "room:a:main", "x", "4321", a.etag, false).revision)
        .toBe(2);
    });
    const { stub: bobs } = await ownerObject();
    await inObject(bobs, (store) => {
      expect(store.write("bob", "bob", "room:c:main", "x", "1", "", false).revision).toBe(1);
    });
  });

  it("the RPC door carries the binder's caller and a conflict", async () => {
    // The verified caller is the writer; a lost swap is an answer, not a refusal.
    const o = await ownerObject();
    const { stub, name: alice, as } = o;
    const row = await write(o, "bob", { row_id: "t", body: "1" });
    expect(row.writer).toBe("bob");
    expect((await rows(stub))[0]?.[4]).toBe("bob");
    expect(await stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t", body: "2", expected_etag: "", private: false,
    })).toEqual({ conflict: shownAs(row, "1") });
    const forged = await failure(() => stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t", body: "2", expected_etag: "", private: false,
      writer: alice } as never));
    expect(forged.message).toBe("invalid collection request");
    const elsewhere = await failure(() => stub.collectionPull(as("bob", "cara"), {
      collection: "board", limit: 1 }));
    expect(elsewhere.message).toBe("this object belongs to another owner");
  });

  it("holds an outside writer to the owner's own share, and all of them to its reserve",
     async () => {
    // Through the object's doors, so the shares are the ones the object reads: its owner's
    // policy, written in the owner's session.
    const r = await rig();
    const room = "room:go:main";
    const body = "s".repeat(PLACE_BYTES);
    const put = async (origin: Origin | null, rowId: string, etag = "") => {
      const answer = await r.stub.collectionWrite(r.bind(origin), {
        collection: room, row_id: rowId, body, expected_etag: etag, private: false });
      return "row" in answer ? answer.row.etag : "";
    };
    const used = async (who: string) => Number((await r.sql(
      "SELECT charge FROM collection_usage WHERE who = ?", who))[0]?.["charge"] ?? 0);
    // The row counted in its writer's own share and all outside writers'.
    await put(MUSE, "p000000");
    const each = await used(`client:${MUSE.client}`);
    expect(await used("*")).toBe(each);
    await r.stub.collectionClear(r.bind(null), { collection: room, writer: null });
    // The owner's shares: a writer 1% of the rows' bound, and all of them 1% too, so one
    // writer meets its own share and the next the reserve's.
    await setFiles(r, { client_share: 1, owner_reserve: 99 });
    const share = Math.floor(LIMITS.maxRowsCharge / 100);
    /** `origin`'s rows written until one is refused: the refusal's message. */
    const until = async (origin: Origin, prefix: string) => {
      for (let n = 0; n <= LIMITS.maxRowsCharge / each; n++) {
        try {
          await put(origin, `${prefix}${String(n).padStart(6, "0")}`);
        } catch (error) {
          return (error as Error).message;
        }
      }
      throw new Error("never refused");
    };
    const r0 = await put(MUSE, "r0");
    expect(await until(MUSE, "r")).toMatch(/^this writer's share/);
    const muse = await used(`client:${MUSE.client}`);
    expect([muse <= share, muse + each > share, await used("*")]).toEqual([true, true, muse]);
    expect(await until(DOT, "s")).toMatch(/^outside writers' share/);
    expect((await failure(() => put(origin(3), "t0"))).message)
      .toMatch(/^outside writers' share/);
    // A takeover moves the row's charge to its new writer, and the owner's own rows count
    // in neither share.
    await put(null, "r0", r0);
    await put(MUSE, "r1");
    await put(null, "o0");
  });

  it("numbers past the stored rows when the high-water mark is missing", async () => {
    const { stub, name: alice } = await ownerObject();
    await inObject(stub, (store, state) => {
      const put = (rowId: string) => {
        store.write(alice, alice, "board", rowId, rowId, "", false);
        return state.storage.sql.exec<{ seq: number }>(
          "SELECT seq FROM collection_rows WHERE row_id = ?", rowId).one().seq;
      };
      expect(put("a")).toBe(1);
      state.storage.sql.exec("DELETE FROM collection_seqs"); // as a store before the table
      expect(put("b")).toBe(2);
    });
    expect(Object.keys(await etags(stub)).sort()).toEqual(["a", "b"]);
  });

  it("a clear never moves a reader's cursor back, and only the owner clears", async () => {
    const { stub, name: alice, as } = await ownerObject();
    const put = (store: Collections, caller: string, rowId: string) =>
      store.write(alice, caller, ROOM, rowId, "1", "", true);
    await inObject(stub, (store) => {
      put(store, "mallory", "a");
      put(store, "mallory", "b");
      put(store, "cara", "c1");
    });
    const held = (await stub.collectionPull(as("cara"), { collection: ROOM, limit: 10 }))
      .cursor;
    const refused = await failure(() => stub.collectionClear(as("mallory"), {
      collection: ROOM, writer: null }));
    expect(refused.message).toBe("only the owner may clear collection rows");
    // The owner clears every writer's rows, but counts only those it could see.
    expect(await stub.collectionClear(as(alice), { collection: ROOM, writer: "mallory" }))
      .toEqual({ deleted: 0 });
    expect((await stub.collectionPull(as("mallory"), { collection: ROOM, limit: 10 })).rows)
      .toEqual([]);
    await inObject(stub, (store) => {
      put(store, "mallory", "c");
      store.write(alice, "mallory", ROOM, "d", "1", "", false);
    });
    expect(await stub.collectionClear(as(alice), { collection: ROOM, writer: null }))
      .toEqual({ deleted: 1 });
    expect(await rows(stub)).toEqual([]);
    // A clear never moves a reader's cursor back: the next row is past every old seq, so a
    // cursor from before the collection was emptied reads it.
    await inObject(stub, (store) => put(store, "cara", "z"));
    expect((await stub.collectionPull(as("cara"), { collection: ROOM, after: held, limit: 10 }))
      .rows.map((row) => row.row_id)).toEqual(["z"]);
  });
});

describe("the primitive's history", () => {
  it("records each write, delete and clear in its transaction", async () => {
    const o = await ownerObject();
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
    // Made again, a row is new: its writes counted from 1, under an etag none before had.
    const again = await put("a", "again");
    expect(again.revision).toBe(1);
    expect(Object.values(await etags(stub))).not.toContain(a.etag);
    expect(b.revision).toBe(1);
    // A deletion is told by its table, not its op: a write named `delete` is a write.
    await put("c", "1", "", "bob", "delete");
    expect((await inObject(stub, (store) => store.changed(alice, { collection: room },
                                                          undefined, 10)))
      .rows.map((row) => [row.row_id, row.deleted]))
      .toEqual([["a", true], ["b", true], ["a", false], ["c", false]]);
  });

  it("is bounded in bytes, oldest first, and a key with no entry left is made again new",
     { tags: ["large"] }, async () => {
    const { stub, name: alice } = await ownerObject();
    const kept = await inFilled(stub, (store, state) => {
      const sql = state.storage.sql;
      const a = store.write(alice, alice, "board", "a", "aaaa", "", false);
      store.delete(alice, "board", "a", false, a.etag);  // its deletion: revision 2
      store.write(alice, alice, "board", "b", "bbbb", "", false);
      // Entries past the charge: the oldest change, a's deletion, goes first.
      fillHistory(store, alice, () => one(
        sql, "SELECT COUNT(*) AS n FROM collection_erasures WHERE key = 'a'") === 0);
      expect(one(sql, "SELECT charge AS n FROM collection_space"))
        .toBeLessThanOrEqual(LIMITS.maxHistoryCharge);
      // Made again, `a` is a new row: revision 1, under an etag it never had.
      const again = store.write(alice, alice, "board", "a", "again", "", false);
      expect([again.revision, again.etag === a.etag]).toEqual([1, false]);
      return state.storage.sql.exec<{ revision: number; op: string }>(
        "SELECT revision, op FROM collection_history WHERE collection = 'board' AND key = 'a'")
        .toArray();
    });
    expect(kept).toEqual([{ revision: 1, op: "write" }]);
  });

  it("holds an erased key's tombstone in its bytes, and prunes it oldest first",
     { tags: ["large"] }, async () => {
    // A key erased and never made again leaves one entry; such keys are bounded by the
    // history's charge.
    const { stub, name: alice } = await ownerObject();
    await inFilled(stub, (store, state) => {
      const count = (table: string, where: string) => state.storage.sql.exec<{ n: number }>(
        `SELECT COUNT(*) AS n FROM ${table} WHERE ${where}`).one().n;
      const row = store.write(alice, alice, "board", "k0", "v", "", false);
      store.delete(alice, "board", "k0", false, row.etag);
      // Entries past the charge: k0's tombstone, the oldest, goes first.
      fillHistory(store, alice, () => count("collection_erasures", "key = 'k0'") === 0);
      const filled = count("collection_history", "collection != 'board'");
      // Keys erased until their tombstones' bytes take a fill entry's room.
      let n = 1;
      for (; count("collection_history", "collection != 'board'") === filled; n++) {
        expect(n, "the tombstones took no room").toBeLessThan(10_000);
        const made = store.write(alice, alice, "board", `k${n}`, "v", "", false);
        store.delete(alice, "board", `k${n}`, false, made.etag);
      }
      // The newer tombstones are kept, each in the history's bytes: older entries went.
      expect(count("collection_erasures", "collection = 'board'")).toBe(n - 1);
      expect(count("collection_history", "collection = 'board'")).toBe(0);
      expect(state.storage.sql.exec<{ charge: number }>("SELECT charge FROM collection_space")
        .one().charge).toBeLessThanOrEqual(LIMITS.maxHistoryCharge);
      // k0's tombstone was the oldest: made again, it is a new row, at revision 1.
      expect(store.write(alice, alice, "board", "k0", "again", "", false).revision).toBe(1);
    });
  });

  it("lets an emptied collection's seq row go, and never takes its cursor back", async () => {
    const { stub, name: alice, as } = await ownerObject();
    const held = await inObject(stub, (store, state) => {
      store.write(alice, alice, "room:a:main", "x", "1", "", false);
      store.write(alice, alice, "room:a:main", "y", "2", "", false);
      const cursor = store.pull(alice, "room:a:main", undefined, 10).cursor;
      store.clear(alice, "room:a:main", null);
      expect(state.storage.sql.exec("SELECT COUNT(*) AS n FROM collection_seqs").one().n)
        .toBe(0);
      store.write(alice, alice, "room:a:main", "x", "3", "", false);
      expect(state.storage.sql.exec("SELECT seq FROM collection_rows").one().seq).toBe(3);
      return cursor;
    });
    expect((await stub.collectionPull(as(alice), { collection: "room:a:main", after: held,
                                                   limit: 10 })).rows.map((r) => r.body))
      .toEqual(["3"]);
  });

  it("reads a cut entry on byte-exact before its cursor, and a deleted row's cursor reads " +
     "none of the row made again (Fact 9)", async () => {
    const o = await ownerObject();
    const { stub, as } = o;
    const history = (fields: { before?: string; body_offset?: number }) =>
      stub.collectionHistory(as("bob"), { collection: "board", row_id: "r", ...fields });
    let etag = "";
    for (const n of ["a", "b", "c"]) {
      etag = (await write(o, "bob", { row_id: "r", body: n.repeat(60_000),
                                      expected_etag: etag })).etag;
    }
    // Each body is past one answer: a page is its newest entry, cut.
    const first = await history({});
    expect([first.entries.map((e) => e.body[0]), first.more]).toEqual([["c"], true]);
    const page = await history({ before: first.before! });
    expect(page.entries.map((e) => e.revision)).toEqual([2]);
    // Written again between the reads, the row's entry before that cursor is still b: read
    // on, it is b's bytes alone.
    etag = (await write(o, "bob", { row_id: "r", body: "d", expected_etag: etag })).etag;
    let body = page.entries[0]!.body;
    let next = (page.entries[0] as { body_next_offset?: number | null }).body_next_offset;
    while (next !== null && next !== undefined) {
      const on = await history({ before: first.before!, body_offset: next });
      body += on.entries[0]!.body;
      next = (on.entries[0] as { body_next_offset?: number | null }).body_next_offset;
    }
    expect(body).toBe("b".repeat(60_000));
    // Deleted and made again, the key's new history is past every cursor of its old one.
    await stub.collectionDelete(as(o.name), { collection: "board", row_id: "r",
                                              expected_etag: etag });
    const made = await write(o, "bob", { row_id: "r", body: "x".repeat(60_000) });
    await write(o, "bob", { row_id: "r", body: "y".repeat(60_000), expected_etag: made.etag });
    for (const fields of [{ before: first.before! }, { before: page.before! },
                          { before: first.before!, body_offset: 1000 }]) {
      expect((await history(fields)).entries).toEqual([]);
    }
  });

  it("keeps every revision its bytes allow, read a page of entries at a time", async () => {
    const { stub, name: alice } = await ownerObject();
    const page = LIMITS.maxPull;
    const bodies = Array.from({ length: page + 2 }, (_, n) => `v${n}`);
    await inObject(stub, (store) => {
      // Another row's write first, so an entry's change is not its revision.
      store.write(alice, alice, "board", "u", "x", "", false);
      let last = { etag: "" };
      for (const body of bodies) {
        last = store.write(alice, alice, "board", "t", body, last.etag, false);
      }
      const first = store.history("bob", "board", "t", false, undefined);
      expect([first.entries.map((e) => e.body), first.more, first.before])
        .toEqual([bodies.slice(2), true, expect.stringMatching(/^[0-9a-f]{64}$/)]);
      const older = store.history("bob", "board", "t", false, first.before);
      expect([older.entries.map((e) => e.body), older.more, older.before])
        .toEqual([["v0", "v1"], false, null]);
      expect(store.write(alice, alice, "board", "t", "again", last.etag, false).revision)
        .toBe(page + 3);
    });
    expect((await history(stub)).length).toBe(page + 4);
  });

  it("reads a cursor in its own row's order alone, never a row of its id in another space",
     async () => {
    const { stub, name: alice } = await ownerObject();
    await inObject(stub, (store) => {
      let open = { etag: "" };
      let own = { etag: "" };
      for (const n of [1, 2]) {
        open = store.write(alice, alice, "board", "t", `open ${n}`, open.etag, false);
        own = store.write(alice, alice, "board", "t", `own ${n}`, own.etag, true);
      }
      const before = store.history(alice, "board", "t", false, undefined).entries[1]!.next;
      expect(store.history(alice, "board", "t", false, before).entries.map((e) => e.body))
        .toEqual(["open 1"]);
      expect((thrown(() => store.history(alice, "board", "t", true, before)) as Refusal).code)
        .toBe("cursor_invalid");
    });
  });

  it("is visible as its row is", async () => {
    const o = await ownerObject();
    const { stub, name: alice, as } = o;
    const mine = await write(o, "bob", { collection: ROOM, row_id: "p", body: "secret",
                                         private: true });
    await write(o, "bob", { collection: ROOM, row_id: "s", body: "shared" });
    const entries = async (caller: string, rowId: string, isPrivate = false) =>
      (await stub.collectionHistory(as(caller), { collection: ROOM, row_id: rowId,
                                                  private: isPrivate })).entries;
    expect((await entries("bob", "p", true)).map((e) => e.body)).toEqual(["secret"]);
    expect(await entries("bob", "p")).toEqual([]);
    expect(await entries("cara", "p", true)).toEqual([]);
    expect(await entries(alice, "p", true)).toEqual([]);
    expect((await entries("cara", "s")).map((e) => [e.revision, e.op, e.writer]))
      .toEqual([[1, "write", "bob"]]);
    // The store deletes for whichever caller it is handed; the owner-only rule is the RPC
    // door's, tested below.
    await inObject(stub, (store) => store.delete("bob", ROOM, "p", true, mine.etag));
    expect(await entries("bob", "p", true)).toEqual([]); // a deleted row shows no history
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

type Usage = Record<string, number>;

/**
 * What the frozen schema's trigger `trigger` adds to `collection_space`'s `column` for the
 * row it fires on, as an expression of the row `row`: the schema's own charge, read from its
 * text.
 */
function charge(trigger: string, column: string, row: string): string {
  const sql = MIGRATIONS.flat().findLast((each) => each.startsWith(`CREATE TRIGGER ${trigger} `));
  const at = `UPDATE collection_space SET ${column} = ${column} + `;
  const start = sql === undefined ? -1 : sql.indexOf(at);
  if (start < 0) {
    throw new Error(`the schema's ${trigger} charges no ${column}`);
  }
  return sql!.slice(start + at.length, sql!.indexOf(";", start)).replaceAll("NEW.", `${row}.`);
}
const ROW_CHARGE = (row: string) => charge("collection_rows_added", "rows_charge", row);
const SEQ_CHARGE = (row: string) => charge("collection_seqs_added", "rows_charge", row);
const USAGE_CHARGE = (row: string) => charge("collection_usage_added", "rows_charge", row);
const HISTORY_CHARGE = (row: string) => charge("collection_history_added", "charge", row);
const ERASURE_CHARGE = (row: string) => charge("collection_erasures_added", "charge", row);

/** One number a query reads. */
function one(sql: SqlStorage, query: string, ...bindings: SqlStorageValue[]): number {
  return sql.exec<{ n: number }>(query, ...bindings).one().n;
}

/**
 * What the rows and history entries `where` names take, by the schema's own charges: the
 * rows' (`rows`), each outside writer's share and all of theirs (`*`), and the history's.
 */
function charged(sql: SqlStorage, where: string):
    { shares: Usage; rows: number; history: number } {
  const shares = Object.fromEntries(sql.exec<{ who: string; charge: number }>(
    `SELECT who, SUM(charge) AS charge FROM (SELECT filer AS who, ${ROW_CHARGE("r")} AS charge ` +
      `FROM collection_rows r WHERE filer != 'owner' AND ${where} UNION ALL SELECT '*', ` +
      `${ROW_CHARGE("r")} FROM collection_rows r WHERE filer != 'owner' AND ${where}) ` +
      "GROUP BY who").toArray().map((row) => [row.who, row.charge]));
  return {
    shares,
    rows: one(sql, `SELECT COALESCE(SUM(${ROW_CHARGE("r")}), 0) AS n FROM collection_rows r ` +
      `WHERE ${where}`),
    history: one(sql, `SELECT COALESCE(SUM(${HISTORY_CHARGE("h")}), 0) AS n ` +
      `FROM collection_history h WHERE ${where}`)
      + one(sql, `SELECT COALESCE(SUM(${ERASURE_CHARGE("e")}), 0) AS n ` +
        `FROM collection_erasures e WHERE ${where}`),
  };
}

/** What the seq rows and the share rows take of the rows' charge. */
function bookkeeping(sql: SqlStorage): number {
  return one(sql, `SELECT COALESCE(SUM(${SEQ_CHARGE("s")}), 0) AS n FROM collection_seqs s`)
    + one(sql, `SELECT COALESCE(SUM(${USAGE_CHARGE("u")}), 0) AS n FROM collection_usage u`);
}

/**
 * The bounds' counters recounted from the rows, the seq rows and the share rows, by the
 * schema's own charges, and the history's (`history`).
 */
function recount(sql: SqlStorage): Usage {
  const all = charged(sql, "TRUE");
  return { ...all.shares, rows: all.rows + bookkeeping(sql), history: all.history };
}

/**
 * `recount` for steps that write only in the collections `among` names, beside a fill,
 * without reading the fill at every step: what is `among` recounted each time, the fill
 * once, here. A step leaves each fill row as it is, which this checks by the rows still at
 * or before the fill's last change (any write moves a row's change on). A step lets the
 * fill's history entries go but never changes one (`collection_history_kept`), so the
 * fill's history is the sum of the charges, recounted here, of its entries still kept. The
 * fill erases nothing, so every deletion is among the steps' collections.
 */
function recounter(sql: SqlStorage, among: string): () => Usage {
  expect(one(sql, `SELECT COUNT(*) AS n FROM collection_rows WHERE ${among}`)).toBe(0);
  const fill = charged(sql, `NOT ${among}`);
  const entries = new Map(sql.exec<{ id: number; charge: number }>(
    `SELECT change AS id, ${HISTORY_CHARGE("h")} AS charge FROM collection_history h ` +
      `WHERE NOT ${among}`).toArray().map((entry) => [entry.id, entry.charge]));
  const last = one(sql, "SELECT MAX(change) AS n FROM collection_rows");
  const rows = one(sql, "SELECT COUNT(*) AS n FROM collection_rows");
  return () => {
    expect(one(sql, "SELECT COUNT(*) AS n FROM collection_rows WHERE change <= ?", last),
           "the fill's rows are as they were").toBe(rows);
    let history = 0;
    let fills = 0;
    // By the keys' index, which holds each entry's key and change and no body.
    for (const { id } of sql.exec<{ id: number }>(
      "SELECT change AS id FROM collection_history")) {
      const each = entries.get(id);
      if (each !== undefined) {
        history += each;
        fills += 1;
      }
    }
    const now = charged(sql, among);
    expect(one(sql, "SELECT COUNT(*) AS n FROM collection_history") - fills,
           "every other history entry is in the collections the steps write")
      .toBe(one(sql, `SELECT COUNT(*) AS n FROM collection_history WHERE ${among}`));
    const shares: Usage = { ...fill.shares };
    for (const [who, each] of Object.entries(now.shares)) {
      shares[who] = (shares[who] ?? 0) + each;
    }
    return { ...shares, rows: fill.rows + now.rows + bookkeeping(sql),
             history: history + now.history };
  };
}

function counters(sql: SqlStorage): Usage {
  const space = sql.exec<{ charge: number; rows_charge: number }>(
    "SELECT charge, rows_charge FROM collection_space").one();
  return { ...Object.fromEntries(sql.exec<{ who: string; charge: number }>(
    "SELECT who, charge FROM collection_usage").toArray().map((u) => [u.who, u.charge])),
           rows: space.rows_charge, history: space.charge };
}

describe("the bounds' counters", () => {
  it.each([1, 7, 42, 1009, 65537])("equal a recount after every step (seed %i)",
                                    { tags: ["large"] }, async (seed) => {
    const { stub, name: owner } = await ownerObject();
    const collections = ["board", "room:go:main", "room:go:side"];
    const among = `collection IN (${collections.map((name) => `'${name}'`).join(", ")})`;
    const outcomes = await inFilled(stub, (store, state) => {
      const sql = state.storage.sql;
      // The history's charge passed, and the rows' reached but for the room a dozen of the
      // steps' own rows take, so the steps meet both: the history pruned by what they take,
      // and rows written until they are refused. The rows alone fill less than the history's
      // charge, so the history is filled first.
      for (let n = 0; n < 12; n++) {
        store.write(owner, owner, "board", `r${n}`, "𝄞".repeat(12), "", false);
      }
      fillHistory(store, owner, () => one(
        sql, "SELECT COUNT(*) AS n FROM collection_history WHERE collection = 'board'") < 12);
      fillRows(store, owner);
      store.clear(owner, "board", null);
      expect(counters(sql)).toEqual(recount(sql));
      const recounted = recounter(sql, among);
      const fills = () => one(sql,
                              `SELECT COUNT(*) AS n FROM collection_history WHERE NOT ${among}`);
      const filled = fills();
      const random = seeded(seed);
      const pick = <T>(items: readonly T[]): T => items[Math.floor(random() * items.length)]!;
      const callers = [owner, "bob", "cara", "dan"];
      const letters = ["a", "é", "€", "𝄞", "z"];
      const counted: Record<string, number> = {};
      for (let step = 0; step < 300; step++) {
        const collection = pick(collections);
        const rowId = `r${Math.floor(random() * 6)}`;
        const caller = pick(callers);
        const roll = random();
        const isPrivate = random() < 0.2;
        const current = store.get(caller, collection, rowId, isPrivate).row;
        let outcome = "done";
        try {
          if (roll < 0.75) {
            const body = Array.from({ length: Math.floor(random() * 24) }, () => pick(letters))
              .join("");
            const etag = current !== null && random() < 0.9 ? current.etag
              : random() < 0.5 ? "" : "stale";
            // An outside writer's rows count in its own share and all outside writers'.
            store.write(owner, caller, collection, rowId, body, etag,
                        isPrivate, pick(["write", "move", "claim"]),
                        null, caller === owner ? "owner" : `client:${caller[0]!.repeat(64)}`);
          } else if (roll < 0.9) {
            store.delete(owner, collection, rowId, isPrivate, current?.etag ?? "stale");
          } else {
            store.clear(owner, collection, random() < 0.5 ? null : pick(callers));
          }
        } catch (error) {
          outcome = error instanceof Refusal ? error.code : (error as Error).name;
        }
        counted[outcome] = (counted[outcome] ?? 0) + 1;
        expect(counters(sql), `seed ${seed}, step ${step}`).toEqual(recounted());
        expect(counters(sql)["history"]!, `seed ${seed}, step ${step}`)
          .toBeLessThanOrEqual(LIMITS.maxHistoryCharge);
      }
      return { counted, pruned: filled - fills(),
               rows: one(sql, `SELECT COUNT(*) AS n FROM collection_rows WHERE ${among}`) };
    });
    // The sequence reaches the bounds, the swaps and the erases, not only plain writes: rows
    // stand at the end, and the steps' entries pruned the fill's.
    expect(Object.keys(outcomes.counted).sort()).toEqual(["conflict", "done", "refused"]);
    expect([outcomes.rows > 0, outcomes.pruned > 0]).toEqual([true, true]);
  });

  it("are kept by the store alone: a history entry is never changed", async () => {
    const { stub, name: owner } = await ownerObject();
    await inObject(stub, (store, state) => {
      store.write(owner, owner, "board", "t", "v1", "", false);
      expect(() => state.storage.sql.exec("UPDATE collection_history SET body_json = 'x'"))
        .toThrow(/a history entry is never changed/);
    });
  });
});

describe("the history keys' migration", () => {
  it("keys each entry by its change, moves the deletions, ticks nothing, and leaves every " +
     "counter and content reference as a recount finds it", async () => {
    const { stub, name: owner } = await ownerObject();
    const placed = "c".repeat(64);
    const migrated = await runInDurableObject(stub, async (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      // Back to an object of the migrations before it, the owner's tables gone first.
      for (const { name } of sql.exec<{ name: string }>("SELECT name FROM sqlite_master " +
          "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' " +
          "AND name NOT GLOB 'cf_*'").toArray()) {
        sql.exec(`DROP TABLE "${name}"`);
      }
      migrate(state.storage, MIGRATIONS.slice(0, 6));  // through migration 6
      // As the code before it left them: `a` written three times; `b` deleted (its entries
      // with it) and made again private; `c` deleted while private; `d`'s body placed.
      sql.exec("INSERT INTO file_contents (content_id, size, inline, refs) VALUES (?, 5000, " +
                 "NULL, 0)", placed);
      const entry = (key: string, revision: number, at: number, change: number | null = null,
                     op = "write", privateWriter: string | null = null,
                     content: string | null = null) => sql.exec(
        "INSERT INTO collection_history (collection, key, revision, op, body_json, at, " +
          "writer, change, private_writer, body_bytes, content_id, filer) " +
          "VALUES ('board', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'owner')", key, revision, op,
        change === null && content === null ? `${key}${revision}` : "", at, owner, change,
        privateWriter, change !== null ? 0 : content === null ? 2 : 5000, content);
      const live = (key: string, revision: number, change: number, writer = owner,
                    content: string | null = null) => sql.exec(
        "INSERT INTO collection_rows (collection, row_id, revision, seq, etag, writer, " +
          "private, body, updated_at, creator, body_bytes, content_id, filer, change) " +
          "VALUES ('board', ?, ?, ?, 'e', ?, ?, ?, 1, ?, ?, ?, 'owner', ?)", key, revision,
        change, writer, writer === owner ? 0 : 1, content === null ? `${key}${revision}` : "",
        writer, content === null ? 2 : 5000, content, change);
      // The keys' entries interleave; `b`'s fourth was written as the wall clock went back, so
      // only the write order (`rowid`) keeps each key's entries in revision order; `e` cleared.
      entry("a", 1, 1);
      entry("a", 2, 2);
      entry("b", 2, 5, 5, "delete");
      entry("b", 3, 6);
      entry("b", 4, 4);
      entry("a", 3, 3);
      live("a", 3, 3);
      entry("b", 5, 7);
      live("b", 5, 7, "bob");
      entry("c", 2, 8, 8, "delete", "bob");
      entry("d", 1, 9, null, "write", null, placed);
      live("d", 1, 9, owner, placed);
      entry("e", 4, 10, 10, "clear");
      sql.exec("UPDATE collection_clock SET change = 10");
      const kept = () => sql.exec("SELECT (SELECT refs FROM file_contents WHERE content_id = ?) " +
        "AS refs, (SELECT body_bytes FROM file_space) AS inline, (SELECT change FROM " +
        "collection_clock) AS clock", placed).one();
      const before = kept();
      migrate(state.storage, MIGRATIONS);
      const space = sql.exec<{ charge: number }>("SELECT charge FROM collection_space").one();
      const stores = storesOn(state);
      return {
        before, after: kept(), space, recount: recount(sql)["history"],
        history: sql.exec("SELECT change, key, revision, prev FROM collection_history " +
                          "ORDER BY change").toArray().map((row) => Object.values(row)),
        erasures: sql.exec("SELECT change, key, revision, private_writer, prev FROM " +
                           "collection_erasures ORDER BY change").toArray()
          .map((row) => Object.values(row)),
        read: await stores.gate.write(() => {
          const store = stores.collections;
          const shown = (page: { entries: { revision: number; body: string }[] }) =>
            page.entries.map((each) => [each.revision, each.body]);
          const a = store.history(owner, "board", "a", false, undefined);
          // Before a2, whose change is a negative key.
          return [shown(a), shown(store.history("bob", "board", "b", true, undefined)),
                  shown(store.history(owner, "board", "a", false, a.entries[1]!.next))];
        }),
      };
    });
    // Each live row's newest entry takes its row's change; older ones negative keys in the
    // order they were written; each names the entry it follows.
    expect(migrated.history).toEqual([[-4, "a", 1, null], [-3, "a", 2, -4], [-2, "b", 3, null],
                                      [-1, "b", 4, -2], [3, "a", 3, -3], [7, "b", 5, -1],
                                      [9, "d", 1, null]]);
    expect(migrated.erasures).toEqual([[5, "b", 2, null, null], [8, "c", 2, "bob", null],
                                       [10, "e", 4, null, null]]);
    // A row made again reads from its making; a page before a negative key reads on.
    expect(migrated.read).toEqual([[[1, "a1"], [2, "a2"], [3, "a3"]],
                                   [[3, "b3"], [4, "b4"], [5, "b5"]], [[1, "a1"]]]);
    // Nothing else moved.
    expect(migrated.space.charge).toBe(migrated.recount);
    expect(migrated.after).toEqual(migrated.before);
    expect(migrated.after).toMatchObject({ refs: 2, clock: 10 });
  });
});

describe("the private key spaces' migration", () => {
  /** An object of the migrations through 30, its owner's tables gone first. */
  function at30(sql: SqlStorage, storage: DurableObjectStorage): void {
    for (const { name } of sql.exec<{ name: string }>("SELECT name FROM sqlite_master " +
        "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' " +
        "AND name NOT GLOB 'cf_*'").toArray()) {
      sql.exec(`DROP TABLE "${name}"`);
    }
    migrate(storage, MIGRATIONS.slice(0, 30));
  }
  /** A row and its one entry as the code before it wrote them, at `change`. */
  function made(sql: SqlStorage, collection: string, rowId: string, writer: string,
                change: number, { hidden = false, filer = "owner", content = null }:
                  { hidden?: boolean; filer?: string; content?: string | null } = {}): void {
    const body = content === null ? `${rowId} body` : "";
    const bytes = content === null ? body.length : 5000;
    sql.exec("INSERT INTO collection_rows (collection, row_id, revision, seq, etag, writer, " +
               "private, body, updated_at, creator, body_bytes, content_id, filer, change) " +
               "VALUES (?, ?, 1, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)", collection, rowId, change,
             `e${change}`.padEnd(64, "0"), writer, hidden ? 1 : 0, body, writer, bytes,
             content, filer, change);
    sql.exec("INSERT INTO collection_history (change, collection, key, revision, op, " +
               "body_json, at, writer, prev, body_bytes, content_id, filer) VALUES " +
               "(?, ?, ?, 1, 'write', ?, 1, ?, NULL, ?, ?, ?)", change, collection, rowId, body,
             writer, bytes, content, filer);
  }

  it("puts each private row and its entries in its writer's space, every counter and " +
     "content reference as a recount finds it", async () => {
    const { stub, name: owner } = await ownerObject();
    const placed = "c".repeat(64);
    const client = `client:${"b".repeat(64)}`;
    const out = await runInDurableObject(stub, async (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      at30(sql, state.storage);
      sql.exec("INSERT INTO file_contents (content_id, size, inline, refs) VALUES (?, 5000, " +
                 "NULL, 0)", placed);
      made(sql, "board", "t", owner, 1);
      made(sql, "board", "p", "bob", 2, { hidden: true, filer: client });
      made(sql, ROOM, "big", owner, 3, { content: placed });
      sql.exec("INSERT INTO collection_seqs VALUES ('board', 2), (?, 3)", ROOM);
      sql.exec("UPDATE collection_clock SET change = 3");
      const kept = () => sql.exec("SELECT (SELECT refs FROM file_contents WHERE content_id = ?) " +
        "AS refs, (SELECT body_bytes FROM file_space) AS inline", placed).one();
      const before = kept();
      migrate(state.storage, MIGRATIONS);
      const stores = storesOn(state);
      return {
        before, after: kept(), counters: counters(sql), recount: recount(sql),
        spaces: sql.exec("SELECT r.row_id, r.space, h.space AS entry FROM collection_rows r " +
                         "JOIN collection_history h ON h.change = r.change ORDER BY r.change")
          .toArray().map((row) => Object.values(row)),
        read: await stores.gate.write(() => {
          const store = stores.collections;
          // Bob's row is his alone, by its id in his space; cara's of that id is her own.
          const hers = store.write(owner, "cara", "board", "p", "cara's", "", true);
          return [store.get("bob", "board", "p", true).row?.body,
                  store.get("bob", "board", "p", false).row, hers.revision,
                  store.history("bob", "board", "p", true, undefined).entries.map((e) => e.body),
                  store.get(owner, "board", "t", false).row?.body];
        }),
      };
    });
    expect(out.spaces).toEqual([["t", "", ""], ["p", "bob", "bob"], ["big", "", ""]]);
    expect(out.read).toEqual(["p body", null, 1, ["p body"], "t body"]);
    expect(out.counters).toEqual(out.recount);
    expect(out.after).toEqual(out.before);
    expect(out.after).toMatchObject({ refs: 2 });
  });

  it("fails loudly on an entry with no row, never dropping it", async () => {
    const { stub, name: owner } = await ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const sql = state.storage.sql;
      at30(sql, state.storage);
      made(sql, "board", "t", owner, 1);
      sql.exec("DELETE FROM collection_rows");
      expect(() => migrate(state.storage, MIGRATIONS)).toThrow(/NOT NULL/);
      expect(sql.exec<{ version: number }>("SELECT version FROM schema_version").one().version)
        .toBe(30);
    });
  });
});

describe("the RPC door's fields", () => {
  it("refuses a field sent as undefined", async () => {
    const o = await ownerObject();
    const { stub, as } = o;
    const refused = await failure(() => stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t", body: "1", expected_etag: "", private: false,
      history_op: undefined } as never));
    expect([refused.code, refused.message]).toEqual(["invalid", "invalid collection request"]);
    expect(await rows(stub)).toEqual([]);
  });

  it("refuses a cursor no answer gave, or a limit that is not a whole number in range",
     async () => {
    const o = await ownerObject();
    const { stub, as } = o;
    await write(o, "bob", { row_id: "t", body: "x" });
    for (const cursor of ["1", "", true, null, 0, 7, "0".repeat(64)]) {
      for (const call of [
        () => stub.collectionPull(as("bob"), { collection: "board", after: cursor,
                                               limit: 1 } as never),
        () => stub.collectionHistory(as("bob"), { collection: "board", row_id: "t",
                                                  before: cursor } as never)]) {
        const refused = await failure(call);
        expect(refused.code, `cursor ${String(cursor)}`).toBe("cursor_invalid");
        expect(refused.message).toMatch(/^cursor_invalid: /);
      }
    }
    for (const limit of [1.5, Number.POSITIVE_INFINITY, "2", 101, 0, null]) {
      const refused = await failure(() => stub.collectionPull(as("bob"), {
        collection: "board", limit } as never));
      expect(refused.code, `limit ${String(limit)}`).toBe("invalid");
    }
  });

  it("refuses a pull's or a history's cursor of the object before a restore", async () => {
    const o = await ownerObject();
    const made = await write(o, "bob", { row_id: "t", body: "x" });
    await write(o, "bob", { row_id: "t", body: "y", expected_etag: made.etag });
    const pull = (await o.stub.collectionPull(o.as("bob"), { collection: "board", limit: 1 }))
      .cursor;
    const before = await inObject(o.stub, (store, state) => {
      const page = store.history("bob", "board", "t", false, undefined);
      const held = state.storage.sql.exec<{ incarnation: string }>(
        "SELECT incarnation FROM collection_space").one().incarnation;
      expect(held).toMatch(/^[0-9a-f]{16}$/);
      store.restored();
      return page.entries[1]!.next;
    });
    for (const call of [
      () => o.stub.collectionPull(o.as("bob"), { collection: "board", after: pull, limit: 1 }),
      () => o.stub.collectionHistory(o.as("bob"), { collection: "board", row_id: "t", before })]) {
      const refused = await failure(call);
      expect([refused.code, refused.message]).toEqual(["cursor_invalid", expect.stringMatching(
        /^cursor_invalid: (after|before) is a cursor from before this object was restored/)]);
    }
    // A cursor made after it is of the new incarnation, and reads.
    const fresh = await o.stub.collectionPull(o.as("bob"), { collection: "board", limit: 1 });
    expect((await o.stub.collectionPull(o.as("bob"), { collection: "board", after: fresh.cursor,
                                                       limit: 1 })).rows).toEqual([]);
  });
});

describe("the RPC door's owner-only erases", () => {
  it("refuses a delete by anyone but the owner, and a stale delete is a conflict", async () => {
    const o = await ownerObject();
    const { stub, name: alice, as } = o;
    const row = await write(o, "bob", { row_id: "t", body: "1" });
    const refused = await failure(() => stub.collectionDelete(as("bob"), {
      collection: "board", row_id: "t", expected_etag: row.etag }));
    expect([refused.code, refused.message])
      .toEqual(["refused", "only the owner may delete collection rows"]);
    expect(await stub.collectionDelete(as(alice), {
      collection: "board", row_id: "t", expected_etag: "stale" }))
      .toEqual({ conflict: shownAs(row, "1") });
    expect(await stub.collectionDelete(as(alice), {
      collection: "board", row_id: "gone", expected_etag: "" })).toEqual({ conflict: null });
    expect(await stub.collectionDelete(as(alice), {
      collection: "board", row_id: "t", expected_etag: row.etag })).toEqual({ deleted: 1 });
    expect(await rows(stub)).toEqual([]);
  });
});

describe("the primitive's errors", () => {
  it("names each kind", async () => {
    const r = await rig();
    const alice = r.owner;
    await inObject(r.stub, (store) => store.write(alice, alice, "board", "a", "1", "", false));
    // The owner holds outside writers to no share of its collections.
    await setFiles(r, { client_share: 0, owner_reserve: 100 });
    await inObject(r.stub, (store) => {
      expect(thrown(() => store.write(alice, alice, "board", "b", "1", "", false, "write", null,
                                      `client:${"1".repeat(64)}`)))
        .toMatchObject({ name: "Refusal", code: "refused" });
      expect(thrown(() => store.write(alice, alice, "board", "a", "1", "", false)))
        .toMatchObject({ name: "Refusal", code: "conflict", facts: { current: { row_id: "a" } } });
      expect(thrown(() => store.write(alice, alice, "board", "a\u0000", "1", "", false)))
        .toMatchObject({ name: "Refusal", code: "invalid" });
      expect(thrown(() => store.write(alice, alice, "board", "a", "\ud800", "", false)))
        .toMatchObject({ name: "Refusal", code: "invalid" });
    });
  });
});
