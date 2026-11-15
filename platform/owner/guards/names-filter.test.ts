/**
 * A restore's names filter (`src/names-filter.ts`) at its worst: sized from the file index's
 * ceiling and its false-positive rate, one row under SQLite's 2 MB value, its indices within a
 * name's bits; and, filled with as many names as the index holds chunk rows, it holds every
 * one and reads about `FILTER_FALSE` of the names it never took as held.
 */
import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { CHUNK_ROWS_MAX } from "../src/manifest";
import {
  addNames, emptyFilter, FILTER_BITS, FILTER_BYTES, FILTER_FALSE, FILTER_HASHES, mayHold, SLICE,
} from "../src/names-filter";

/** SQLite's and Durable Objects' most bytes in one value. */
const VALUE_MAX = 2_000_000;

/** The `n`th of a run of names, an HMAC's 64 hex as the files Worker makes them. */
const name = (run: string, n: number) => createHash("sha256").update(`${run} ${n}`).digest("hex");

describe("a restore's names filter", () => {
  it("is sized from the index's ceiling at its false-positive rate, in one value", () => {
    const rows = CHUNK_ROWS_MAX();
    const least = rows * Math.log(1 / FILTER_FALSE) / Math.LN2 ** 2;
    expect(FILTER_BITS()).toBeGreaterThanOrEqual(least);
    expect(FILTER_BITS() - least).toBeLessThan(8);
    expect(FILTER_BYTES()).toBeLessThan(VALUE_MAX);
    expect(FILTER_HASHES()).toBe(7);
    expect(FILTER_HASHES() * SLICE).toBeLessThanOrEqual(64);
    console.log(`the names filter: ${FILTER_BYTES()} bytes, ${FILTER_HASHES()} bits a name, ` +
                `for ${rows} chunk rows`);
  });

  it("holds every name it took, and about FILTER_FALSE of those it never took", () => {
    const filter = emptyFilter();
    const taken = Array.from({ length: CHUNK_ROWS_MAX() }, (_, n) => name("taken", n));
    addNames(filter, taken);
    expect(taken.every((each) => mayHold(filter, each))).toBe(true);
    const probes = 100_000;
    let held = 0;
    for (let n = 0; n < probes; n++) {
      held += mayHold(filter, name("never", n)) ? 1 : 0;
    }
    // A fixed run of names, so a fixed count; the bound with three standard errors' room.
    expect(held / probes).toBeLessThan(FILTER_FALSE + 3 * Math.sqrt(FILTER_FALSE / probes));
    console.log(`the names filter at its worst: ${held} of ${probes} absent names read held`);
  });

  it("changes nothing for a name added twice, and refuses a name not 64 hex", () => {
    const once = emptyFilter();
    addNames(once, [name("x", 1)]);
    const twice = emptyFilter();
    addNames(twice, [name("x", 1), name("x", 1)]);
    expect(Buffer.from(twice).equals(Buffer.from(once))).toBe(true);
    expect(() => addNames(once, ["nope"])).toThrow("a chunk name is 64 lowercase hex characters");
  });
});
