/**
 * The placement rule (ADR-035): a body past `PLACE_BYTES` leaves the object's SQLite for R2,
 * through the files plane; a smaller one stays in its row. The bytes count in the tier's
 * storage; a crash between the R2 put and the row's commit leaves no reference to a missing
 * body, and housekeeping's sweep deletes the orphan; the erasure deletes every R2 copy.
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { Bodies, PLACE_BYTES, READ_PAGE_BYTES } from "../src/bodies";
import { LIMITS } from "../src/collections";
import { GC_GRACE_S, INTENT_TTL_S } from "../src/files";
import { WriteGate } from "../src/gate";
import { GRANT_LIMITS, scopes } from "../src/grants";
import { MAX_BODY } from "../src/ledger";
import type { Binding, OwnerDO } from "../src/owner-do";
import { TIER_UNREAD } from "../src/tier";
import { MUSE, rig } from "./ledger-support";
import { failure, filesOn, ownerObject, sqlIn, text, TIERS } from "./support";
import type { FauxOwnerDO } from "./worker";

const sha = (body: string) => createHash("sha256").update(body, "utf8").digest("hex");
/** A body just past the threshold in bytes, and well under it in characters. */
const LARGE = "é".repeat(PLACE_BYTES);
const SMALL = "s".repeat(PLACE_BYTES);

async function namespace(stub: DurableObjectStub<OwnerDO>): Promise<string> {
  return (await sqlIn(stub, "SELECT ns FROM file_space"))[0]!["ns"] as string;
}

/** What R2 holds under a chunk's hash, as UTF-8, or the failure reading it. */
async function inR2(ns: string, body: string): Promise<string> {
  try {
    const [bytes] = await env.FILES.getChunks(ns, [{ sha256: sha(body) }]);
    return new TextDecoder().decode(bytes);
  } catch (error) {
    return `missing: ${(error as Error).message}`;
  }
}

describe("the placement rule", () => {
  it("keeps a body of PLACE_BYTES in its row, and places a larger one in R2, read back whole",
     async () => {
    const { stub, as, name } = ownerObject();
    const put = (rowId: string, body: string, etag = "") => stub.collectionWrite(as(name), {
      collection: "board", row_id: rowId, body, expected_etag: etag, private: false });
    const small = await put("small", SMALL);
    const large = await put("large", LARGE);
    expect(large).toMatchObject({ row: { body: LARGE, revision: 1 } });
    expect(Object.keys((large as { row: object }).row)).not.toContain("content_id");
    const stored = await sqlIn(stub, "SELECT row_id, length(body) AS kept, body_bytes, " +
                                       "content_id FROM collection_rows ORDER BY row_id");
    expect(stored).toEqual([
      { row_id: "large", kept: 0, body_bytes: 2 * PLACE_BYTES,
        content_id: expect.stringMatching(/^[0-9a-f]{64}$/) },
      { row_id: "small", kept: PLACE_BYTES, body_bytes: PLACE_BYTES, content_id: null }]);
    // The row's history entry names the same content, counted twice.
    expect(await sqlIn(stub, "SELECT c.refs FROM file_contents c JOIN collection_rows r " +
                               "ON r.content_id = c.content_id")).toEqual([{ refs: 2 }]);
    expect(await inR2(await namespace(stub), LARGE)).toBe(LARGE);
    // Every read reads it back whole: get, pull and history.
    expect((await stub.collectionGet(as("bob"), { collection: "board", row_id: "large" })).row)
      .toEqual((large as { row: object }).row);
    expect((await stub.collectionPull(as("bob"), { collection: "board", after: 0, limit: 10 }))
      .rows.map((row) => [row.row_id, row.body])).toEqual([["small", SMALL], ["large", LARGE]]);
    expect((await stub.collectionHistory(as("bob"), { collection: "board", row_id: "large" }))
      .entries.map((entry) => entry.body)).toEqual([LARGE]);
    // Its bytes count in the files byte quota, the tier's storage, once however named.
    expect((await stub.fileUsage(as(name))).chunk_bytes).toBe(2 * PLACE_BYTES);
    // A small body written over it keeps the content for the history entry; a delete, which
    // drops the row's history, lets it go to garbage, collected after its grace.
    await put("large", "now small", (large as { row: { etag: string } }).row.etag);
    expect(await sqlIn(stub, "SELECT refs FROM file_contents")).toEqual([{ refs: 1 }]);
    const current = (await stub.collectionGet(as(name), { collection: "board",
                                                          row_id: "large" })).row!;
    await stub.collectionDelete(as(name), { collection: "board", row_id: "large",
                                            expected_etag: current.etag });
    expect(await sqlIn(stub, "SELECT state FROM file_chunks")).toEqual([{ state: "garbage" }]);
    expect(small).toMatchObject({ row: { body: SMALL } });
  });

  it("places a large message body, and the reader reads it back whole", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.post(null, `dm:${muse}`, { body: LARGE });
    await r.post(null, `dm:${muse}`, { body: "short", cmid: "c2" });
    expect(await r.sql("SELECT body_bytes, content_id IS NOT NULL AS placed FROM " +
                         "message_bodies ORDER BY created_at"))
      .toEqual([{ body_bytes: 2 * PLACE_BYTES, placed: 1 }, { body_bytes: 5, placed: 0 }]);
    const page = await r.read(MUSE);
    expect(page.messages.map((m) => [m.body, m.integrity])).toEqual([[LARGE, "ok"],
                                                                     ["short", "ok"]]);
    // A resend of the same message is its original; its second upload is let go.
    expect((await r.post(null, `dm:${muse}`, { body: LARGE })).message_id)
      .toBe(page.messages[0]!.message_id);
    expect(await r.sql("SELECT COUNT(*) AS n FROM file_intents")).toEqual([{ n: 0 }]);
  });

  it("ends a mailbox page before more placed bodies than a page holds", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    // Three bodies of 3 MiB each (four bytes a character): two fit a page.
    const big = "𝄞".repeat(768 * 1024);
    for (const cmid of ["a", "b", "c"]) {
      await r.post(null, `dm:${muse}`, { body: `${cmid}${big}`, cmid });
    }
    const first = await r.read(MUSE);
    expect(2 * 3 * 1024 * 1024).toBeLessThanOrEqual(READ_PAGE_BYTES);
    expect(3 * 3 * 1024 * 1024).toBeGreaterThan(READ_PAGE_BYTES);
    expect([first.messages.length, first.more]).toEqual([2, true]);
    await r.ack(MUSE, first.messages.at(-1)!.seq);
    expect((await r.read(MUSE)).messages.map((m) => m.body![0])).toEqual(["c"]);
  });

  it("leaves no reference to a body whose commit never came, and the sweep removes it",
     async () => {
    const { stub } = ownerObject();
    await runInDurableObject(stub, async (_instance: OwnerDO, state) => {
      const gate = new WriteGate(state.storage);
      const files = filesOn(gate.storage, gate.write);
      const bodies = new Bodies(files, gate.storage, gate.write);
      // The bytes reach R2 first; the object crashes before the row's unit.
      const placing = (await gate.write(() => bodies.begin(LARGE, "owner")))!;
      await bodies.send(placing);
      const ns = files.ns();
      expect(await inR2(ns, LARGE)).toBe(LARGE);
      const sql = state.storage.sql;
      expect(sql.exec("SELECT COUNT(*) AS n FROM collection_rows WHERE content_id IS NOT NULL " +
                        "UNION ALL SELECT COUNT(*) FROM file_contents").toArray())
        .toEqual([{ n: 0 }, { n: 0 }]);
      expect(sql.exec("SELECT state, stored, holds, refs FROM file_chunks").toArray())
        .toEqual([{ state: "uploading", stored: 1, holds: 1, refs: 0 }]);
      // Housekeeping lets the upload go at its expiry, and deletes the chunk past its grace.
      const now = Date.now() / 1000;
      await files.collect(now + 1);
      expect(sql.exec("SELECT state FROM file_chunks").toArray())
        .toEqual([{ state: "uploading" }]);  // not before the upload's expiry
      await files.collect(now + INTENT_TTL_S + GC_GRACE_S + 60);
      expect(sql.exec("SELECT COUNT(*) AS n FROM file_intents").toArray()).toEqual([{ n: 0 }]);
      expect(sql.exec("SELECT COUNT(*) AS n FROM file_chunks").toArray()).toEqual([{ n: 0 }]);
      expect(await inR2(ns, LARGE)).toMatch(/^missing: /);
    });
  });

  it("counts placed bytes in the tier's storage, and adds none while it is not known",
     async () => {
    const { stub, as, name } = ownerObject();
    const put = (rowId: string, body: string, binding: Binding) =>
      stub.collectionWrite(binding, { collection: "board", row_id: rowId, body,
                                      expected_etag: "", private: false });
    const tight = { free: { runs: 5, storage_bytes: 3 * PLACE_BYTES },
                    paid: TIERS.paid };
    await put("a", LARGE, { ...as(name), tiers: tight });
    expect((await failure(() => put("b", LARGE.replace("é", "e"), { ...as(name),
                                                                    tiers: tight }))).message)
      .toBe("file_quota: the owner's file bytes are at their quota");
    // The same body again is the same chunk: it adds no byte.
    await put("c", LARGE, { ...as(name), tiers: tight });
    // A small body is the row's own, whatever the tier's figure.
    await put("d", SMALL, { ...as(name), tiers: tight });
    expect((await failure(() => put("e", `${LARGE}x`, { ...as(name), tiers: null }))).message)
      .toBe(TIER_UNREAD);
    expect(await sqlIn(stub, "SELECT COUNT(*) AS n FROM file_intents")).toEqual([{ n: 0 }]);
  });

  it("is erased with the owner: every R2 copy, a file's and a placed body's", async () => {
    const owner = `owner-${crypto.randomUUID()}`;
    const stub = env.AGENT.getByName(owner) as DurableObjectStub<FauxOwnerDO>;
    const bind: Binding = { owner, caller: owner, origin: null, tiers: TIERS };
    await stub.collectionWrite(bind, { collection: "board", row_id: "t", body: LARGE,
                                       expected_etag: "", private: false });
    const file = "f".repeat(PLACE_BYTES + 1);
    await stub.fileWrite(bind, { path: "/workspace/big", expected_etag: null,
                                 content: text(file) });
    const ns = await namespace(stub as unknown as DurableObjectStub<OwnerDO>);
    expect([await inR2(ns, LARGE), await inR2(ns, file)]).toEqual([LARGE, file]);
    await runInDurableObject(stub, async (instance: FauxOwnerDO) => {
      await instance.erasure.erase();
    });
    expect(await inR2(ns, LARGE)).toMatch(/^missing: /);
    expect(await inR2(ns, file)).toMatch(/^missing: /);
  }, 30_000);

  it("holds the raised bodies at their bounds and refuses one past", async () => {
    const { stub, as, name } = ownerObject();
    const put = (rowId: string, body: string) => stub.collectionWrite(as(name), {
      collection: "board", row_id: rowId, body, expected_etag: "", private: false });
    // A row of `maxRow` characters, placed: the largest a chunk holds at four bytes each.
    expect(LIMITS.maxRow).toBe(1024 * 1024);
    expect((await put("most", "x".repeat(LIMITS.maxRow))) as { row: { body: string } })
      .toMatchObject({ row: { revision: 1 } });
    expect((await failure(() => put("past", "x".repeat(LIMITS.maxRow + 1)))).message)
      .toBe(`a row body is text of at most ${LIMITS.maxRow} characters`);
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.post(null, `dm:${muse}`, { body: "m".repeat(MAX_BODY) });
    expect((await r.read(MUSE)).messages[0]!.body).toHaveLength(MAX_BODY);
    // A grant's scopes: 256 of them, and their JSON counted in bytes, to the byte.
    const scope = (n: number) => ({ universe: `u${n}`, agent: "main", capability: "read_graph" });
    const most = Array.from({ length: GRANT_LIMITS.maxScopes }, (_, n) => scope(n));
    expect(scopes(most)).toHaveLength(256);
    expect(() => scopes([...most, scope(256)])).toThrow(/outside scopes/);
    const bytesOf = (list: object[]) => new TextEncoder().encode(JSON.stringify(list)).byteLength;
    const exact: { universe: string; agent: string; capability: string }[] = [];
    for (let n = 0; bytesOf([...exact, scope(n)]) <= GRANT_LIMITS.maxScopesBytes; n++) {
      exact.push(scope(n));
      const last = exact.at(-1)!;
      last.universe += "p".repeat(Math.min(512 - last.universe.length,
                                           GRANT_LIMITS.maxScopesBytes - bytesOf(exact)));
    }
    const last = exact.at(-1)!;
    last.agent += "a".repeat(GRANT_LIMITS.maxScopesBytes - bytesOf(exact));
    expect(bytesOf(exact)).toBe(GRANT_LIMITS.maxScopesBytes);
    expect(scopes(exact)).toHaveLength(exact.length);
    last.agent += "a";
    expect(() => scopes(exact)).toThrow(`a grant's scopes are at most ${
      GRANT_LIMITS.maxScopesBytes} bytes of JSON`);
  });
});
