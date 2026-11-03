/**
 * A main agent's digests (slice M): `inboxDigest` gives its unacknowledged mail and the
 * owner's rooms' rows written past its cursor, `boardDigest` the board's, each row as its
 * head, in one call; only since the cursor, which never goes back; and only what its grant
 * holds: `read_graph`, a private row only to its writer, no revoked client.
 */
import { describe, expect, it } from "vitest";

import type { Binding } from "../src/owner-do";
import { MUSE, rig, type Rig } from "./ledger-support";
import { failure } from "./support";

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
    expect(first.rooms.rows.map((row) => [row.collection, row.row_id, row.body_chars]))
      .toEqual([["room:app:lobby", "m1", 1000], ["room:app:den", "m1", 9]]);
    expect(first.rooms.rows[0]!.head).toBe("x".repeat(512));
    expect(first.rooms.rows[1]!).toMatchObject({ head: "hello den", writer: r.owner });
    expect(first.rooms.rows[0]!).not.toHaveProperty("body");
    // Only since the cursor: nothing new, then only the one row written since.
    const again = await r.stub.inboxDigest(r.bind(MUSE), { limit: 10,
                                                          after: first.rooms.cursor });
    expect(again.rooms).toEqual({ cursor: first.rooms.cursor, rows: [] });
    await write(r, "room:app:lobby", "m2", "later");
    const later = await r.stub.inboxDigest(r.bind(MUSE), { limit: 10, after: again.rooms.cursor,
                                                          head_chars: 3 });
    expect(later.rooms.rows.map((row) => [row.row_id, row.head])).toEqual([["m2", "lat"]]);
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

describe("the board digest", () => {
  it("gives the board's rows past the cursor, page by page, and no room's", async () => {
    const r = await rig();
    await r.stub.outsideAgents(r.bind(MUSE));
    await write(r, "board", "t1", "first");
    await write(r, "room:app:lobby", "m1", "not the board");
    await write(r, "board", "t2", "second");
    const page = await r.stub.boardDigest(r.bind(MUSE), { limit: 1, after: 0 });
    expect(page.rows.map((row) => [row.collection, row.row_id, row.head]))
      .toEqual([["board", "t1", "first"]]);
    expect(page.cursor).toBe(page.rows[0]!.change);
    const next = await r.stub.boardDigest(r.bind(MUSE), { limit: 1, after: page.cursor });
    expect(next.rows.map((row) => row.row_id)).toEqual(["t2"]);
    // A claim is a write on the row's etag: the row is changed again, past the cursor.
    const done = await r.stub.boardDigest(r.bind(MUSE), { limit: 5, after: next.cursor });
    expect(done.rows).toEqual([]);
    await r.stub.collectionWrite(r.bind(MUSE), { collection: "board", row_id: "t1",
      body: "claimed", expected_etag: page.rows[0]!.etag, private: false, history_op: "claim" });
    expect((await r.stub.boardDigest(r.bind(MUSE), { limit: 5, after: done.cursor })).rows
      .map((row) => [row.row_id, row.head])).toEqual([["t1", "claimed"]]);
    for (const bad of [{ limit: 0, after: 0 }, { limit: 1, after: -1 },
                       { limit: 1, after: 0, head_chars: -1 }, { limit: 1 }]) {
      expect((await failure(() => r.stub.boardDigest(r.bind(MUSE), bad as never))).name)
        .toBe("Invalid");
    }
  });
});
