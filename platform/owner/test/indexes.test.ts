/**
 * The owner-bound guard's honesty, against the migrated database itself (review rvlim H1):
 * every index SQLite makes on its own is in the guard's list (`guards/autoindexes.ts`), and
 * a collection row at the validators' widest names is charged at least what it takes laid
 * out as SQLite lays it, every index entry with it, so the rows' charge bounds them
 * whatever names a caller picks.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { AUTOINDEXES } from "../guards/autoindexes";
import { Collections, LIMITS } from "../src/collections";
import {
  INDEX_KEPT, MANIFEST_TABLES, PAGE_BYTES, PLACE_BYTES, RECORD_FIXED, recordLayout, TABLE_KEPT,
  WORST_FILL,
} from "../src/manifest";
import type { OwnerDO } from "../src/owner-do";
import { ownerObject } from "./support";

/** A record laid out as the guard lays a term (its page share at worst fill, its overflow). */
const laid = (payload: number, kept: number) => {
  const { inPlace, overflow } = recordLayout(payload + RECORD_FIXED, kept);
  return Math.min(inPlace * WORST_FILL, PAGE_BYTES) + overflow;
};

describe("the owner-bound guard against the database", () => {
  it("knows every index SQLite makes on its own", async () => {
    const { stub } = ownerObject();
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

  it("charges a row at the widest names at least what it takes, its indexes with it",
     async () => {
    const { stub } = ownerObject();
    await runInDurableObject(stub, (_instance: OwnerDO, state) => {
      const charge = () => state.storage.sql.exec<{ rows_charge: number }>(
        "SELECT rows_charge FROM collection_space").one().rows_charge;
      // A name is 256 code points of four bytes; a principal 512 of them.
      const name = (letter: string) => "𝄞".repeat(255) + letter;
      const who = "𝄞".repeat(512);
      const before = charge();
      new Collections(state.storage, LIMITS).write(who, who, name("c"), name("r"),
                                                   "b".repeat(PLACE_BYTES), "", false);
      const charged = charge() - before;
      const N = 9;
      const [collection, row, principal] = [1024, 1024, 2048];
      // The reviewer's recount (rvlim_guard): the row's record and its four index entries,
      // its collection's seq row, laid out as the guard lays them.
      const honest = laid(collection + row + 4 * N + 64 + 2 * principal + 1 + PLACE_BYTES + 71,
                          TABLE_KEPT)
        + laid(collection + row + N, INDEX_KEPT) + laid(collection + 2 * N, INDEX_KEPT)
        + 2 * laid(collection + principal + N, INDEX_KEPT)
        + laid(collection + N, TABLE_KEPT) + laid(collection + N, INDEX_KEPT);
      expect(charged * WORST_FILL).toBeGreaterThanOrEqual(honest);
    });
  });
});
