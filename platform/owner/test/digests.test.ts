/**
 * A main agent's digests (slice M): `inboxDigest` gives its unacknowledged mail and the
 * owner's rooms' rows written past its cursor, `boardDigest` the board's, each row as its
 * head, in one call; only since the cursor, which never goes back; each delete and clear
 * reported too, to whoever could see the row; and only what its grant holds: `read_graph`, a
 * private row only to its writer, no revoked client.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { changedSql } from "../src/collections";
import type { OwnerDO } from "../src/owner-do";
import type { Binding } from "../../contract/src/binding";
import type { RowHead } from "../src/pages";
import { MUSE, rig, type Rig } from "./ledger-support";
import { failure, fillHistory, inFilled, inObject, ownerObject } from "./support";

type Live = Extract<RowHead, { deleted: false }>;

/** A digest's rows, each a live row's head. */
function live(rows: readonly RowHead[]): Live[] {
  for (const row of rows) {
    expect(row.deleted).toBe(false);
  }
  return rows as Live[];
}

function write(r: Rig, collection: string, rowId: string, body: string,
               caller: Binding = r.bind(null), isPrivate = false) {
  return r.stub.collectionWrite(caller, { collection, row_id: rowId, body, expected_etag: "",
                                          private: isPrivate });
}

describe("the inbox digest", () => {
  it("gives unread mail and the rooms' rows past the cursor, as heads, in one read",
     async () => {
    const r = await rig();
    const agent = await r.register(MUSE);
    await r.post(null, `dm:${agent}`, { body: "are you there?" });
    await write(r, "room:app:lobby", "m1", "x".repeat(1000));
    await write(r, "board", "t1", "a task");
    await write(r, "room:app:den", "m1", "hello den");
    const first = await r.stub.inboxDigest(r.bind(MUSE), { limit: 10, after: 0 });
    expect(first.mail.messages.map((m) => m.body)).toEqual(["are you there?"]);
    expect(live(first.rooms.rows).map((row) => [row.collection, row.row_id, row.body_bytes]))
      .toEqual([["room:app:lobby", "m1", 1000], ["room:app:den", "m1", 9]]);
    expect(live(first.rooms.rows)[0]!.head).toBe("x".repeat(512));
    expect(live(first.rooms.rows)[1]!).toMatchObject({ head: "hello den", writer: r.owner });
    expect(live(first.rooms.rows)[0]!).not.toHaveProperty("body");
    // Only since the cursor: nothing new, then only the one row written since.
    const again = await r.stub.inboxDigest(r.bind(MUSE), { limit: 10,
                                                          after: first.rooms.cursor });
    expect(again.rooms).toEqual({ cursor: first.rooms.cursor, rows: [] });
    await write(r, "room:app:lobby", "m2", "later");
    const later = await r.stub.inboxDigest(r.bind(MUSE), { limit: 10, after: again.rooms.cursor,
                                                          head_chars: 3 });
    expect(live(later.rooms.rows).map((row) => [row.row_id, row.head])).toEqual([["m2", "lat"]]);
    expect(later.rooms.cursor).toBeGreaterThan(first.rooms.cursor);
    // Mail is since its own acknowledged cursor.
    await r.ack(MUSE, first.mail.messages.at(-1)!.seq);
    expect((await r.stub.inboxDigest(r.bind(MUSE), { limit: 10, after: 0 })).mail.messages)
      .toEqual([]);
  });

  it("shows a private row only to its writer", async () => {
    const r = await rig();
    await r.register(MUSE);
    const visitor: Binding = { owner: r.owner, caller: "bob", origin: null };
    await write(r, "room:app:lobby", "secret", "bob's own", visitor, true);
    await write(r, "room:app:lobby", "open", "everyone's", visitor);
    const seen = await r.stub.inboxDigest(r.bind(MUSE), { limit: 10, after: 0 });
    expect(seen.rooms.rows.map((row) => row.row_id)).toEqual(["open"]);
    const own = await r.stub.boardDigest(visitor, { limit: 10, after: 0 });
    expect(own.rows).toEqual([]);
  });

  it("is held to the grant: read_graph where the agent acts, and no revoked client",
     async () => {
    const r = await rig();
    await r.register(MUSE);
    const set = async (scopes: object[]) => {
      const row = (await r.stub.grantList(r.bind(null))).clients
        .find((c) => c.client === MUSE.client)!;
      await r.stub.grantSet(r.session(null), { client: MUSE.client, source: "mcp",
        mode: "scoped", scopes: scopes as never, expected_revision: row.revision, name: "" });
    };
    await set([{ universe: "alpha", agent: "main", capability: "write_graph" }]);
    for (const door of [() => r.stub.inboxDigest(r.bind(MUSE), { limit: 1, after: 0 }),
                        () => r.stub.boardDigest(r.bind(MUSE), { limit: 1, after: 0 })]) {
      expect((await failure(door)).message).toBe("outside client capability not granted");
    }
    await set([{ universe: "alpha", agent: "main", capability: "read_graph" }]);
    expect((await r.stub.boardDigest(r.bind(MUSE), { limit: 1, after: 0 })).rows).toEqual([]);
    await r.stub.grantRevoke(r.bind(null), { client: MUSE.client });
    expect((await failure(() => r.stub.inboxDigest(r.bind(MUSE), { limit: 1, after: 0 })))
      .message).toBe("outside client revoked");
  });
});

describe("a digest past a pruned deletion (review rvlim N1)", () => {
  it("R1: tells a reader behind a pruned tombstone to read again, never hides the delete",
     { tags: ["large"] }, async () => {
    const { stub, name } = ownerObject();
    await inFilled(stub, (store, state) => {
      const made = store.write(name, name, "board", "gone", "x", "", false);
      const cursor = store.changed(name, { collection: "board" }, 0, 100).cursor;
      store.delete(name, "board", "gone", made.etag);
      const tombstones = () => state.storage.sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM collection_erasures WHERE collection = 'board' AND " +
          "key = 'gone'").one().n;
      expect(tombstones()).toBe(1);
      // A reader past the delete is told it while it is kept.
      expect(store.changed(name, { collection: "board" }, cursor, 100).rows
        .map((row) => [row.row_id, row.deleted])).toEqual([["gone", true]]);
      // Entries past the history's charge: the tombstone, the oldest, goes first.
      fillHistory(store, name, () => tombstones() === 0);
      expect(tombstones()).toBe(0);
      const seen = store.changed(name, { collection: "board" }, cursor, 100);
      expect(seen).toMatchObject({ rows: [], reset: true });
      const top = state.storage.sql.exec<{ change: number }>(
        "SELECT change FROM collection_clock").one().change;
      expect(seen.cursor).toBe(top);
      // From the cursor it was given, the feed goes on as before; a fresh reader is never
      // reset: it holds no row to forget.
      expect(store.changed(name, { collection: "board" }, seen.cursor, 100))
        .toEqual({ cursor: top, rows: [] });
      expect(store.changed(name, { collection: "board" }, 0, 100)).not.toHaveProperty("reset");
    });
  });
});

describe("a digest's placed bodies (ADR-035)", () => {
  it("reads placed mail and rows from R2: whole heads and sizes, a delete's entry kept " +
     "body-less", async () => {
    const r = await rig();
    const agent = await r.register(MUSE);
    const mail = "m".repeat(3000) + "?";
    const row = "é".repeat(2000);
    await r.post(null, `dm:${agent}`, { body: mail });
    await write(r, "room:app:lobby", "big", row);
    await write(r, "board", "task", row);
    expect(await r.sql("SELECT COUNT(*) AS n FROM collection_rows WHERE content_id IS NOT NULL"))
      .toEqual([{ n: 2 }]);
    const inbox = await r.stub.inboxDigest(r.bind(MUSE), { limit: 10, after: 0 });
    expect(inbox.mail.messages.map((m) => [m.body, m.integrity])).toEqual([[mail, "ok"]]);
    expect(live(inbox.rooms.rows).map((m) => [m.row_id, m.head, m.body_bytes]))
      .toEqual([["big", "é".repeat(512), 4000]]);
    expect(live(inbox.rooms.rows)[0]!).not.toHaveProperty("content_id");
    const board = await r.stub.boardDigest(r.bind(MUSE), { limit: 10, after: 0 });
    expect(live(board.rows).map((m) => [m.row_id, m.body_bytes])).toEqual([["task", 4000]]);
    // A delete's entry carries no body: the row's placed entries go, and the digest reports
    // the deletion.
    const [stored] = await r.sql("SELECT etag FROM collection_rows WHERE row_id = 'task'");
    await r.stub.collectionDelete(r.bind(null), { collection: "board", row_id: "task",
                                                  expected_etag: stored!["etag"] as string });
    expect(await r.sql("SELECT (SELECT COUNT(*) FROM collection_history WHERE key = 'task') " +
                       "AS entries, (SELECT COUNT(*) FROM collection_erasures WHERE key = " +
                       "'task' AND op = 'delete') AS deletions"))
      .toEqual([{ entries: 0, deletions: 1 }]);
    const after = await r.stub.boardDigest(r.bind(MUSE), { limit: 10, after: board.cursor });
    expect(after.rows.map((m) => [m.row_id, m.deleted])).toEqual([["task", true]]);
  });
});

describe("the board digest", () => {
  it("gives the board's rows past the cursor, page by page, and no room's", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await write(r, "board", "t1", "first");
    await write(r, "room:app:lobby", "m1", "not the board");
    await write(r, "board", "t2", "second");
    const page = await r.stub.boardDigest(r.bind(MUSE), { limit: 1, after: 0 });
    expect(live(page.rows).map((row) => [row.collection, row.row_id, row.head]))
      .toEqual([["board", "t1", "first"]]);
    expect(page.cursor).toBe(live(page.rows)[0]!.change);
    const next = await r.stub.boardDigest(r.bind(MUSE), { limit: 1, after: page.cursor });
    expect(next.rows.map((row) => row.row_id)).toEqual(["t2"]);
    // A claim is a write on the row's etag: the row is changed again, past the cursor.
    const done = await r.stub.boardDigest(r.bind(MUSE), { limit: 5, after: next.cursor });
    expect(done.rows).toEqual([]);
    await r.stub.collectionWrite(r.bind(MUSE), { collection: "board", row_id: "t1",
      body: "claimed", expected_etag: live(page.rows)[0]!.etag, private: false, history_op: "claim" });
    expect(live((await r.stub.boardDigest(r.bind(MUSE), { limit: 5, after: done.cursor })).rows)
      .map((row) => [row.row_id, row.head])).toEqual([["t1", "claimed"]]);
    for (const bad of [{ limit: 0, after: 0 }, { limit: 1, after: -1 },
                       { limit: 1, after: 0, head_chars: -1 }, { limit: 1 }]) {
      expect((await failure(() => r.stub.boardDigest(r.bind(MUSE), bad as never))).code)
        .toBe("invalid");
    }
  });
});

describe("a digest's deletions", () => {
  it("reports a delete and a clear past the cursor, to whoever could see the row", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    const bob: Binding = { owner: r.owner, caller: "bob", origin: null };
    const t1 = await write(r, "board", "t1", "a task");
    await write(r, "board", "t2", "another");
    await write(r, "board", "mine", "bob's own", bob, true);
    const seen = await r.stub.boardDigest(r.bind(MUSE), { limit: 10, after: 0 });
    expect(live(seen.rows).map((row) => row.row_id)).toEqual(["t1", "t2"]);
    await r.stub.collectionDelete(r.bind(null), { collection: "board", row_id: "t1",
      expected_etag: "row" in t1 ? t1.row.etag : "" });
    const gone = await r.stub.boardDigest(r.bind(MUSE), { limit: 10, after: seen.cursor });
    expect(gone.rows).toEqual([{ collection: "board", row_id: "t1", deleted: true,
                                 op: "delete", writer: r.owner,
                                 change: expect.any(Number) }]);
    expect(gone.cursor).toBeGreaterThan(seen.cursor);
    // A clear erases bob's private row too: only bob is told of it.
    const bobSeen = await r.stub.boardDigest(bob, { limit: 10, after: gone.cursor });
    await r.stub.collectionClear(r.bind(null), { collection: "board", writer: null });
    const cleared = await r.stub.boardDigest(r.bind(MUSE), { limit: 10, after: gone.cursor });
    expect(cleared.rows.map((row) => [row.row_id, row.deleted, "op" in row ? row.op : ""]))
      .toEqual([["t2", true, "clear"]]);
    const bobs = await r.stub.boardDigest(bob, { limit: 10, after: bobSeen.cursor });
    expect(bobs.rows.map((row) => row.row_id).sort()).toEqual(["mine", "t2"]);
    // A row made again under a deleted key is a live row past its delete.
    await write(r, "board", "t1", "back");
    const back = await r.stub.boardDigest(r.bind(MUSE), { limit: 10, after: cleared.cursor });
    expect(live(back.rows).map((row) => [row.row_id, row.head])).toEqual([["t1", "back"]]);
    // A room's deletion reaches the inbox digest, not the board's.
    await write(r, "room:app:lobby", "m1", "hi");
    await r.stub.collectionClear(r.bind(null), { collection: "room:app:lobby", writer: null });
    const inbox = await r.stub.inboxDigest(r.bind(null), { limit: 10, after: back.cursor });
    expect(inbox.rooms.rows.map((row) => [row.collection, row.row_id, row.deleted]))
      .toEqual([["room:app:lobby", "m1", true]]);
  });
});

describe("a digest's reads", () => {
  it("read by the change orders from the cursor on, never a collection's rows", async () => {
    const r = await rig();
    await write(r, "board", "t1", "a task");
    const plans = await runInDurableObject(r.stub, (_instance: OwnerDO, state) =>
      (["collection", "prefix"] as const).map((where) => {
        const keys = where === "collection" ? ["board"] : ["room:", "room;"];
        return state.storage.sql.exec<{ detail: string }>(
          `EXPLAIN QUERY PLAN ${changedSql(where)}`,
          0, ...keys, "owner", 0, ...keys, "owner", 10).toArray()
          .map((row) => row.detail).join("; ");
      }));
    for (const plan of plans) {
      expect(plan).toMatch(/SEARCH collection_rows USING INDEX collection_changes \(change>\?\)/);
      expect(plan).toMatch(
        /SEARCH collection_erasures USING INTEGER PRIMARY KEY \(rowid>\?\)/);
      expect(plan).not.toMatch(/SCAN collection_|collection_writers|collection_creators/);
    }
  });

  it("keeps a key's delete once the key is made again private, and deleted again",
     async () => {
    const r = await rig();
    const bob = "bob";
    await inObject(r.stub, (store) => {
      const made = store.write(r.owner, r.owner, "board", "t", "public", "", false);
      const before = store.changed("muse", { collection: "board" }, 0, 10).cursor;
      store.delete(r.owner, "board", "t", made.etag);
      let etag = store.write(r.owner, bob, "board", "t", "mine", "", true).etag;
      for (let n = 0; n < 5; n++) {
        etag = store.write(r.owner, bob, "board", "t", `mine ${n}`, etag, true).etag;
      }
      // Anyone but bob still learns the public row is gone; bob sees his row.
      expect(store.changed("muse", { collection: "board" }, before, 10).rows
        .map((row) => [row.row_id, row.deleted])).toEqual([["t", true]]);
      expect(store.changed(bob, { collection: "board" }, before, 10).rows
        .map((row) => [row.row_id, row.deleted])).toEqual([["t", true], ["t", false]]);
      // The row made again has the history of its own writes, from its making.
      expect(store.history(bob, "board", "t").entries.map((entry) => entry.op))
        .toEqual(Array<string>(6).fill("write"));
      // Deleted again, its private deletion is bob's alone, and the public one stays.
      store.delete(r.owner, "board", "t", etag);
      expect(store.changed("muse", { collection: "board" }, before, 10).rows
        .map((row) => [row.row_id, row.deleted])).toEqual([["t", true]]);
      expect(store.changed(bob, { collection: "board" }, before, 10).rows
        .map((row) => [row.row_id, row.deleted])).toEqual([["t", true], ["t", true]]);
    });
  });
});

