/**
 * The isolate's one memory budget (`memory.ts`): grants that fit are given, one that does
 * not is refused `busy` before anything is changed, and under load every write lands once
 * it is tried again, byte-exact, the budget never passed.
 */
import { describe, expect, it } from "vitest";

import { PLACE_BYTES } from "../src/manifest";
import {
  BUSY, grant, GUARD_BYTES, isBusy, memoryHeld, RELAY_RESERVED, reserve, resetMemoryPeak,
} from "../src/memory";
import type { Row } from "../src/collections";
import { at, readOn } from "./pages-support";
import { failure, ownerObject, sqlIn } from "./support";

const MIB = 1024 * 1024;

/** A write tried again while the isolate is busy, as a client does on `503`. */
async function retried<T>(act: () => Promise<T>): Promise<{ value: T; busy: number }> {
  for (let busy = 0; ; busy++) {
    try {
      return { value: await act(), busy };
    } catch (error) {
      if (!isBusy(error) && !(error instanceof Error && error.message.startsWith("busy:"))) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
}

describe("the isolate's memory budget", () => {
  it("grants what fits, counts the largest twice, and refuses the rest busy", () => {
    const { reserved } = memoryHeld();
    const room = GUARD_BYTES - reserved;
    const half = Math.floor(room / 2);
    const one = grant(half, "a test's half");
    // A second grant must leave the largest's room beside the live set.
    expect(() => grant(2, "one more")).toThrow(BUSY);
    one.release();
    expect(() => one.release()).toThrow(/released twice/);
    const small = [grant(MIB, "a"), grant(MIB, "b")];
    expect(memoryHeld().held).toBeGreaterThanOrEqual(2 * MIB);
    small.forEach((held) => held.release());
    // A grant no load could make room for is a bug, never busy.
    expect(() => grant(room, "too much")).toThrow(/more than the isolate's memory holds/);
    reserve("relay", RELAY_RESERVED);
    expect(() => reserve("relay", 1)).toThrow(/reservation is/);
  });

  it("holds the relay's share as the owner objects' one reservation", async () => {
    const { stub, as, name } = ownerObject();
    await stub.collectionGet(as(name), { collection: "board", row_id: "none" });
    expect(memoryHeld().reserved).toBe(RELAY_RESERVED);
  });

  it("refuses a placed write busy while the budget is full, changing nothing, and lands it " +
     "when tried again", async () => {
    const { stub, as, name } = ownerObject();
    const body = "w".repeat(4 * MIB);
    const write = () => stub.collectionWrite(as(name), { collection: "board", row_id: "w",
      body, expected_etag: "", private: false });
    const room = GUARD_BYTES - memoryHeld().reserved - memoryHeld().held;
    const full = grant(Math.floor(room / 2) - 4 * MIB, "a full isolate");
    try {
      expect((await failure(write)).message).toBe(BUSY);
      expect(await sqlIn(stub, "SELECT COUNT(*) AS n FROM collection_rows")).toEqual([{ n: 0 }]);
      expect(await sqlIn(stub, "SELECT COUNT(*) AS n FROM file_intents")).toEqual([{ n: 0 }]);
      // A read whose page is small still fits beside it.
      expect(await stub.collectionGet(as(name), { collection: "board", row_id: "w" }))
        .toEqual({ row: null });
    } finally {
      full.release();
    }
    expect(await write()).toMatchObject({ row: { revision: 1 } });
    expect(await readOn((offset) => stub.collectionGet(as(name), {
      collection: "board", row_id: "w", ...at(offset) }).then((got) => got.row!)) === body)
      .toBe(true);
  }, 120_000);

  it("never passes the budget with several objects writing and reading at once, and loses " +
     "no write", async () => {
    resetMemoryPeak();
    const owners = Array.from({ length: 6 }, () => ownerObject());
    const bodies = owners.map((_, n) => String.fromCharCode(0x61 + n).repeat(8 * MIB));
    const writes = owners.flatMap(({ stub, as, name }, n) => [0, 1].map((k) => retried(() =>
      stub.collectionWrite(as(name), { collection: "board", row_id: `r${k}`, body: bodies[n]!,
                                       expected_etag: "", private: false }) as
        Promise<{ row: Row }>)));
    const reads = owners.map(({ stub, as, name }) => retried(() =>
      stub.collectionPull(as(name), { collection: "board", after: 0, limit: 10 })));
    const done = await Promise.all(writes);
    await Promise.all(reads);
    expect(done.every(({ value }) => value.row.revision === 1)).toBe(true);
    console.log(`concurrent writes: ${done.reduce((n, { busy }) => n + busy, 0)} refused busy ` +
                `and tried again; the most held at once ${memoryHeld().peak} of ${GUARD_BYTES}`);
    expect(memoryHeld().peak).toBeLessThanOrEqual(GUARD_BYTES - memoryHeld().reserved);
    expect(memoryHeld().held).toBe(0);
    for (const [n, { stub, as, name }] of owners.entries()) {
      for (const k of [0, 1]) {
        const got = await stub.collectionGet(as(name), { collection: "board", row_id: `r${k}`,
                                                         body_offset: 8 * MIB - 10 });
        expect(got.row!.body).toBe(bodies[n]!.slice(-10));
        expect(await sqlIn(stub, "SELECT COUNT(*) AS n FROM collection_rows WHERE " +
                                 "content_id IS NOT NULL")).toEqual([{ n: 2 }]);
      }
    }
    expect(PLACE_BYTES).toBeLessThan(8 * MIB);
  }, 300_000);
});
