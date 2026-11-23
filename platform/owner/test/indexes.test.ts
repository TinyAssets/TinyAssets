/**
 * The owner-bound guard's honesty, against the migrated database itself: every index SQLite makes
 * on its own is in the guard's list (`guards/autoindexes.ts`), and a collection row at the
 * validators' widest names is charged at least what it takes laid out as SQLite lays it, every
 * index entry with it, so the rows' charge bounds them whatever names a caller picks.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { AUTOINDEXES } from "../guards/autoindexes";
import {
  INDEX_KEPT, MANIFEST_TABLES, PAGE_BYTES, RECORD_FIXED, recordLayout, TABLE_KEPT, WORST_FILL,
} from "../src/manifest";
import { PLACE_BYTES } from "../../contract/src/limits";
import type { OwnerDO } from "../src/owner-do";
import { ownerObject, storesOn } from "./support";

/** A record laid out as the guard lays a term (its page share at worst fill, its overflow). */
const laid = (payload: number, kept: number) => {
  const { inPlace, overflow } = recordLayout(payload + RECORD_FIXED, kept);
  return Math.min(inPlace * WORST_FILL, PAGE_BYTES) + overflow;
};

describe("the owner-bound guard against the database", () => {
  it("knows every index SQLite makes on its own", async () => {
    const { stub } = await ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const made = state.storage.sql.exec<{ name: string; tbl_name: string }>(
        "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND name GLOB " +
          "'sqlite_autoindex_*'").toArray()
        .filter((index) => !Object.hasOwn(MANIFEST_TABLES, index.tbl_name)
                           && !index.tbl_name.startsWith("_cf_")
                           && index.tbl_name !== "cf_agents_jobs")
        .map((index) => index.name).sort();
      expect(made).toEqual([...AUTOINDEXES].sort());
    });
  });

  it.each([
    // A name is 256 code points of four bytes; a principal 512 of them.
    ["the widest names", "𝄞".repeat(255), "𝄞".repeat(512), "b".repeat(PLACE_BYTES)],
    // Each record in place, so no overflow page's slack hides a term the charge leaves out.
    ["the shortest names", "", `client:${"a".repeat(64)}`, "b"],
  ])("charges a row at %s at least what it takes, its indexes with it, its writer's key " +
     "space among them", async (_shape, stem, who, body) => {
    const { stub } = await ownerObject();
    await runInDurableObject(stub, async (_instance: OwnerDO, state) => {
      const charge = () => state.storage.sql.exec<{ rows_charge: number }>(
        "SELECT rows_charge FROM collection_space").one().rows_charge;
      const { gate, collections } = storesOn(state);
      // By a client's filer, at its 71 bytes, whose usage rows and the collection's seq row a
      // row of its own takes first, so the charge measured is the private row's alone.
      const put = (rowId: string, isPrivate: boolean) => gate.write(() => collections.write(
        who, who, `${stem}c`, rowId, body, "", isPrivate, "write", null,
        `client:${"f".repeat(64)}`));
      await put(`${stem}q`, false);
      const before = charge();
      await put(`${stem}r`, true);
      const charged = charge() - before;
      const N = 9;
      const bytes = (text: string) => new TextEncoder().encode(text).byteLength;
      const [collection, row, principal] = [bytes(`${stem}c`), bytes(`${stem}r`), bytes(who)];
      // The row's record (its change in it, its space its writer) and its five index entries
      // (`collection_changes` among them, its key's with the space), laid out as the guard
      // lays them.
      const honest = laid(collection + row + 5 * N + 64 + 3 * principal + bytes(body) + 71,
                          TABLE_KEPT)
        + laid(collection + principal + row + N, INDEX_KEPT) + laid(collection + 2 * N, INDEX_KEPT)
        + 2 * laid(collection + principal + N, INDEX_KEPT) + laid(2 * N, INDEX_KEPT);
      expect(charged * WORST_FILL).toBeGreaterThanOrEqual(honest);
    });
  });

  it("charges a delete's entry at the widest names at least what it takes, its private " +
     "writer with it", async () => {
    const { stub } = await ownerObject();
    await runInDurableObject(stub, async (_instance: OwnerDO, state) => {
      const charge = () => state.storage.sql.exec<{ charge: number }>(
        "SELECT charge FROM collection_space").one().charge;
      const name = (letter: string) => "𝄞".repeat(255) + letter;
      const who = "𝄞".repeat(512);
      const { gate, collections: store } = storesOn(state);
      await gate.write(() => {
        const made = store.write(who, who, name("c"), name("r"), "b", "", true);
        store.delete(who, name("c"), name("r"), true, made.etag);
      });
      // The delete drops the row's entries: its own is the history's one entry.
      expect(state.storage.sql.exec<{ n: number }>(
        "SELECT (SELECT COUNT(*) FROM collection_erasures WHERE op = 'delete' AND " +
          "private_writer IS NOT NULL) + (SELECT COUNT(*) FROM collection_history) AS n")
        .one().n).toBe(1);
      const N = 9;
      const [collection, key, principal] = [1024, 1024, 2048];
      // Its record (names, op, writer, private writer, numbers, its change the rowid): its
      // table has no index.
      const honest = laid(collection + key + 6 + principal + principal + 4 * N, TABLE_KEPT);
      expect(charge() * WORST_FILL).toBeGreaterThanOrEqual(honest);
    });
  });
});
