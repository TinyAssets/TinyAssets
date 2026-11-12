/**
 * S34: the one revision primitive, in the owner's object.
 *
 * Ported from `tests/test_collections.py` (branch `s5b-board`), test for test. Where
 * the Python test monkeypatched a bound, this one runs the primitive on the object's
 * own storage (`inObject`) and fills production's bound, or sets the owner's shares by its
 * policy. Where it went through the router's owner channel, this one goes through the
 * object's RPC door with a binding.
 */
import type { WrittenRow } from "../src/collections";
import { describe, expect, it } from "vitest";

import { Collections, Conflict, LIMITS, type Row } from "../src/collections";

import { MIGRATIONS } from "../src/schema";
import {
  collectionParts, etags, failure, fillHistory, fillRows, history, inFilled, inObject,
  type Owner, ownerObject, rows,
} from "./support";
import { PLACE_BYTES } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";
import type { Origin } from "../../contract/src/binding";
import { DOT, MUSE, origin, rig, type Rig } from "./ledger-support";

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

/** The owner sets its files policy's shares in its session, as the object then reads them. */
async function setShares(r: Rig, client_share: number, owner_reserve: number): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind(null));
  await r.stub.policyWrite(r.session(null), { expected_generation: generation, policy: {
    ...policy, files: { client_share, owner_reserve } } as never });
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
    // The write answers its row without its body (review H3); the conflict, the row whole.
    const { body, ...rest } = lost as Row;
    expect({ ...rest, body_bytes: body.length }).toEqual(winner);
    expect(winner).not.toHaveProperty("body");
    expect([winner!.revision, winner!.seq]).toEqual([2, 2]);
    expect(await rows(stub)).toEqual([["board", "task-1", 2, 2, winner!.writer, 0, body]]);
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
    expect(await pull(0, 1)).toEqual({ cursor: 1, rows: [shownAs(one, body)] });
    expect(await pull(1, 10)).toEqual({ cursor: 2, rows: [shownAs(two, "[]")] });
    expect(await pull(2, 10)).toEqual({ cursor: 2, rows: [] });
    expect(await stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t1", body: "{}", expected_etag: "", private: false,
    })).toEqual({ conflict: shownAs(one, body) });
  });

  it("a private row is its writer's own", async () => {
    const o = ownerObject();
    const { stub, as } = o;
    const mine = await write(o, "bob", { collection: ROOM, row_id: "p", body: "secret",
                                         private: true });
    expect(await stub.collectionPull(as("cara"), { collection: ROOM, after: 0, limit: 10 }))
      .toEqual({ cursor: 1, rows: [] });
    expect((await stub.collectionPull(as("bob"), { collection: ROOM, after: 0, limit: 10 }))
      .rows).toEqual([shownAs(mine, "secret")]);
    const taken = await failure(() => write(o, "cara", {
      collection: ROOM, row_id: "p", body: "x", expected_etag: mine.etag, private: true }));
    expect([taken.code, taken.message]).toEqual(["refused", "a private row is its writer's own"]);
    const exposed = await failure(() => write(o, "bob", {
      collection: ROOM, row_id: "p", body: "x", expected_etag: mine.etag, private: false }));
    expect(exposed.code).toBe("invalid");
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
    expect(await get("bob", ROOM, "p")).toEqual({ row: shownAs(mine, "secret") });
    expect(await get("cara", ROOM, "p")).toEqual({ row: null });
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
    const { stub, name: alice } = ownerObject();
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
      const a = store.get(alice, "room:a:main", "x").row!;
      expect(store.write(alice, alice, "room:a:main", "x", "4321", a.etag, false).revision)
        .toBe(2);
    });
    const { stub: bobs } = ownerObject();
    await inObject(bobs, (store) => {
      expect(store.write("bob", "bob", "room:c:main", "x", "1", "", false).revision).toBe(1);
    });
    expect(LIMITS.maxRowsCharge).toBe(256 * 1024 * 1024);
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
    })).toEqual({ conflict: shownAs(row, "1") });
    const forged = await failure(() => stub.collectionWrite(as("bob"), {
      collection: "board", row_id: "t", body: "2", expected_etag: "", private: false,
      writer: alice } as never));
    expect(forged.message).toBe("invalid collection request");
    const elsewhere = await failure(() => stub.collectionPull(as("bob", "cara"), {
      collection: "board", after: 0, limit: 1 }));
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
    await setShares(r, 1, 99);
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

  it("a seq collision fails loudly and deletes nothing", async () => {
    // A missing high-water mark still numbers past the rows; a clash never replaces one.
    const { stub, name: alice } = ownerObject();
    await inObject(stub, (store, state) => {
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
      expect(() => put(new Stale(...collectionParts(state.storage)), "c"))
        .toThrow(/UNIQUE/); // seq 1 is a's
    });
    expect(Object.keys(await etags(stub)).sort()).toEqual(["a", "b"]);
  });

  it("a clear never moves a reader's cursor back, and only the owner clears", async () => {
    const { stub, name: alice, as } = ownerObject();
    const put = (store: Collections, caller: string, rowId: string) =>
      store.write(alice, caller, ROOM, rowId, "1", "", true);
    await inObject(stub, (store) => {
      put(store, "mallory", "a");
      put(store, "mallory", "b");
      put(store, "cara", "c1");
    });
    const refused = await failure(() => stub.collectionClear(as("mallory"), {
      collection: ROOM, writer: null }));
    expect(refused.message).toBe("only the owner may clear collection rows");
    expect(await stub.collectionClear(as(alice), { collection: ROOM, writer: "mallory" }))
      .toEqual({ deleted: 2 });
    await inObject(stub, (store) => put(store, "mallory", "c"));
    expect(await stub.collectionClear(as(alice), { collection: ROOM, writer: null }))
      .toEqual({ deleted: 2 });
    // A clear never moves a reader's cursor back: the next row is past every old seq.
    expect((await inObject(stub, (store) => put(store, "cara", "z"))).seq).toBe(5);
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
    // A deletion is told by its table, not its op: a write named `delete` is a write.
    await put("c", "1", "", "bob", "delete");
    expect((await inObject(stub, (store) => store.changed(alice, { collection: room }, 0, 10)))
      .rows.map((row) => [row.row_id, row.deleted]))
      .toEqual([["a", true], ["b", true], ["a", false], ["c", false]]);
  });

  it("is bounded in bytes, oldest first, and a key with no entry left repeats no revision",
     { tags: ["large"] }, async () => {
    const { stub, name: alice } = ownerObject();
    const space = (state: DurableObjectState) => state.storage.sql.exec<{
      charge: number; revision_floor: number }>(
      "SELECT charge, revision_floor FROM collection_space").one();
    const kept = await inFilled(stub, (store, state) => {
      const a = store.write(alice, alice, "board", "a", "aaaa", "", false);
      store.delete(alice, "board", "a", a.etag);  // its tombstone: revision 2
      store.write(alice, alice, "board", "b", "bbbb", "", false);
      expect(space(state)).toMatchObject({ revision_floor: 0 });
      // Entries past the charge: the oldest, a's tombstone, goes first, and its revision
      // becomes the floor.
      fillHistory(store, alice, () => space(state).revision_floor > 0);
      expect(space(state).revision_floor).toBe(2);
      expect(space(state).charge).toBeLessThanOrEqual(LIMITS.maxHistoryCharge);
      // Made again, `a` goes on past every revision a prune let go.
      expect(store.write(alice, alice, "board", "a", "again", "", false).revision).toBe(3);
      return state.storage.sql.exec<{ revision: number; op: string }>(
        "SELECT revision, op FROM collection_history WHERE collection = 'board' AND key = 'a'")
        .toArray();
    });
    expect(kept).toEqual([{ revision: 3, op: "write" }]);
  });

  it("holds an erased key's tombstone in its bytes, and prunes it oldest first",
     { tags: ["large"] }, async () => {
    // A key erased and never made again leaves one entry; such keys are bounded by the
    // history's charge, and the floor keeps their revisions from repeating.
    const { stub, name: alice } = ownerObject();
    await inFilled(stub, (store, state) => {
      const count = (where: string) => state.storage.sql.exec<{ n: number }>(
        `SELECT COUNT(*) AS n FROM collection_history WHERE ${where}`).one().n;
      const row = store.write(alice, alice, "board", "k0", "v", "", false);
      store.delete(alice, "board", "k0", row.etag);
      // Entries past the charge: k0's tombstone, the oldest, goes first.
      fillHistory(store, alice, () => count("collection = 'board' AND key = 'k0'") === 0);
      const filled = count("collection != 'board'");
      for (let n = 1; n < 20; n++) {
        const made = store.write(alice, alice, "board", `k${n}`, "v", "", false);
        store.delete(alice, "board", `k${n}`, made.etag);
      }
      // The newer tombstones are kept, each in the history's bytes: older entries went.
      expect(count("collection = 'board'")).toBe(19);
      expect(count("collection != 'board'")).toBeLessThan(filled);
      expect(state.storage.sql.exec<{ charge: number }>("SELECT charge FROM collection_space")
        .one().charge).toBeLessThanOrEqual(LIMITS.maxHistoryCharge);
      // k0's tombstone was the oldest: made again, it goes past the pruned revisions.
      expect(store.write(alice, alice, "board", "k0", "again", "", false).revision)
        .toBeGreaterThan(2);
    });
  });

  it("lets an emptied collection's seq row go, and never takes its cursor back", async () => {
    const { stub, name: alice, as } = ownerObject();
    await inObject(stub, (store, state) => {
      store.write(alice, alice, "room:a:main", "x", "1", "", false);
      store.write(alice, alice, "room:a:main", "y", "2", "", false);
      store.clear(alice, "room:a:main", null);
      expect(state.storage.sql.exec("SELECT COUNT(*) AS n FROM collection_seqs").one().n)
        .toBe(0);
      expect(store.write(alice, alice, "room:a:main", "x", "3", "", false).seq).toBe(3);
    });
    expect((await stub.collectionPull(as(alice), { collection: "room:a:main", after: 2,
                                                   limit: 10 })).rows.map((r) => r.body))
      .toEqual(["3"]);
  });

  it("keeps every revision its bytes allow, read a page of entries at a time", async () => {
    const { stub, name: alice } = ownerObject();
    const page = LIMITS.maxPull;
    const bodies = Array.from({ length: page + 2 }, (_, n) => `v${n}`);
    await inObject(stub, (store) => {
      let last = { etag: "" };
      for (const body of bodies) {
        last = store.write(alice, alice, "board", "t", body, last.etag, false);
      }
      const first = store.history("bob", "board", "t");
      expect([first.entries.map((e) => e.body), first.more, first.before])
        .toEqual([bodies.slice(2), true, first.entries[0]!.change]);
      const older = store.history("bob", "board", "t", first.before);
      expect([older.entries.map((e) => e.body), older.more, older.before])
        .toEqual([["v0", "v1"], false, null]);
      expect(store.write(alice, alice, "board", "t", "again", last.etag, false).revision)
        .toBe(page + 3);
    });
    expect((await history(stub)).length).toBe(page + 3);
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
    await inObject(stub, (store) => store.delete("bob", ROOM, "p", mine.etag));
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

type Usage = Record<string, number>;

/**
 * What the frozen schema's trigger `trigger` adds to `collection_space`'s `column` for the
 * row it fires on, as an expression of the row `row`: the schema's own charge, read from its
 * text.
 */
function charge(trigger: string, column: string, row: string): string {
  const sql = MIGRATIONS.flat().find((each) => each.startsWith(`CREATE TRIGGER ${trigger} `));
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
      `FROM collection_history h WHERE ${where}`),
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
 * fill's history is the sum of the charges, recounted here, of its entries still kept.
 */
function recounter(sql: SqlStorage, among: string): () => Usage {
  expect(one(sql, `SELECT COUNT(*) AS n FROM collection_rows WHERE ${among}`)).toBe(0);
  const fill = charged(sql, `NOT ${among}`);
  const entries = new Map(sql.exec<{ id: number; charge: number }>(
    `SELECT rowid AS id, ${HISTORY_CHARGE("h")} AS charge FROM collection_history h ` +
      `WHERE NOT ${among}`).toArray().map((entry) => [entry.id, entry.charge]));
  const last = one(sql, "SELECT MAX(change) AS n FROM collection_rows");
  const rows = one(sql, "SELECT COUNT(*) AS n FROM collection_rows");
  return () => {
    expect(one(sql, "SELECT COUNT(*) AS n FROM collection_rows WHERE change <= ?", last),
           "the fill's rows are as they were").toBe(rows);
    let history = 0;
    let fills = 0;
    // By the history's age index, which holds each entry's rowid and no more.
    for (const { id } of sql.exec<{ id: number }>(
      "SELECT rowid AS id FROM collection_history ORDER BY at")) {
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
    const { stub, name: owner } = ownerObject();
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
        sql, "SELECT revision_floor AS n FROM collection_space") > 0);
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
        const current = store.get(caller, collection, rowId).row;
        let outcome = "done";
        try {
          if (roll < 0.75) {
            const body = Array.from({ length: Math.floor(random() * 24) }, () => pick(letters))
              .join("");
            const etag = current !== null && random() < 0.9 ? current.etag
              : random() < 0.5 ? "" : "stale";
            // An outside writer's rows count in its own share and all outside writers'.
            store.write(owner, caller, collection, rowId, body, etag,
                        current?.private ?? random() < 0.2, pick(["write", "move", "claim"]),
                        null, caller === owner ? "owner" : `client:${caller[0]!.repeat(64)}`);
          } else if (roll < 0.9) {
            store.delete(owner, collection, rowId, current?.etag ?? "stale");
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
    expect(Object.keys(outcomes.counted).sort()).toEqual(["Conflict", "done", "refused"]);
    expect([outcomes.rows > 0, outcomes.pruned > 0]).toEqual([true, true]);
  });

  it("are kept by the store alone: a history entry is never changed", async () => {
    const { stub, name: owner } = ownerObject();
    await inObject(stub, (store, state) => {
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
    expect([refused.code, refused.message]).toEqual(["invalid", "invalid collection request"]);
    expect(await rows(stub)).toEqual([]);
  });

  it("refuses a cursor or a limit that is not a whole number in range", async () => {
    const { stub, as } = ownerObject();
    for (const [after, limit] of [[1.5, 1], [Number.NaN, 1], [2 ** 53, 1], [-1, 1], ["1", 1],
                                  [true, 1], [0, 1.5], [0, Number.POSITIVE_INFINITY], [0, "2"],
                                  [0, 101], [0, 0], [0, null]]) {
      const refused = await failure(() => stub.collectionPull(as("bob"), {
        collection: "board", after, limit } as never));
      expect(refused.code, `after ${String(after)}, limit ${String(limit)}`).toBe("invalid");
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
    await setShares(r, 0, 100);
    await inObject(r.stub, (store) => {
      expect(thrown(() => store.write(alice, alice, "board", "b", "1", "", false, "write", null,
                                      `client:${"1".repeat(64)}`)))
        .toMatchObject({ name: "Refusal", code: "refused" });
      expect(() => store.write(alice, alice, "board", "a", "1", "", false))
        .toThrow(Conflict);
      expect(thrown(() => store.write(alice, alice, "board", "a\u0000", "1", "", false)))
        .toMatchObject({ name: "Refusal", code: "invalid" });
      expect(thrown(() => store.write(alice, alice, "board", "a", "\ud800", "", false)))
        .toMatchObject({ name: "Refusal", code: "invalid" });
    });
  });
});
