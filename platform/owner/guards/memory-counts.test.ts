/**
 * The counts test (ADR-037): an owner isolate's memory is bounded by structure, so what it
 * holds live is a sum of counts the code enforces times costs the code constructs, read from
 * the sources, within the isolate's 128 MB:
 * - its pool's units (`OWNER_POOL_UNITS` x `UNIT_BYTES`): every path that holds chunks takes
 *   them while its bytes are in hand, each path's cost its declared units, which the workerd
 *   memory leg holds each path's measured peak within (`memory/leg.mjs`);
 * - the relay's tails (`TAIL_UNITS` UTF-16 units, every object's in the isolate together);
 * - the relay's per-object terms (each object's events posts, its sockets' attachments, its
 *   told sign-outs, its tails' own objects: `relay-memory.ts`), once for each owner object
 *   the isolate holds;
 * - the Worker's resting baseline (`OWNER_BASELINE_BYTES`), which the leg measures with an
 *   owner object built and served and holds within its figure.
 *
 * It asserts only what is derived. What it does not count is named here, never fitted:
 * garbage V8 has not collected yet, unmeasured in production (`UNCOUNTED.garbage`, workerd's
 * figures recorded), and the holds not yet under a unit, each owed by a named PR.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import * as holds from "../src/holds";
import { MOVE_RECORDS } from "../src/segments";
import { UNIT_BYTES } from "../src/slots";
import { ISOLATE_BYTES, memoryTerms } from "./relay-memory";
import { PLATFORM } from "../../ci/kit/sources";

const MIB = 1024 * 1024;

/**
 * What the counts leave out, and where each is owed: never summed, never fitted. The garbage
 * figures are workerd's (`rpcretain_research.md` §2, review H1): the high-water RSS over the
 * resting RSS, with no forced collection, in MiB.
 */
export const UNCOUNTED = Object.freeze({
  garbage: Object.freeze({
    owed: "the production probe (rr_prod_probe): whether the 128 MB counts it, then how much",
    workerd_mib: Object.freeze({ arrays_one_isolate: 78.9, plain_allocation: [65.2, 76.0],
                                 returned_arrays: 101.9, byte_streams: [112, 177],
                                 arrays_two_isolates: 141.7, r2_put: 235.5 }),
  }),
  holds: Object.freeze({
    "a call's RPC arguments (up to MCP_BODY_MAX on /mcp, API_BODY_MAX on /api)": "PR 3b",
    "the waiting calls' own arguments (OWNER_WAITERS and OWNER_SMALL_WAITERS of them)": "PR 3b",
    "file_write's whole bytes (WHOLE_BYTES)": "PR 3b",
    "file_upload_chunks' two chunks, as arguments": "PR 3b",
    "file_read_chunks' two chunks, as its answer": "PR 3b",
    "a placed body's bytes as room_post or collection_write sends them (BODY_BYTES_MAX)": "PR 3b",
    "an export's chunks, as its answer": "PR 4",
    "sync's raw chunk bytes": "PR 4",
    "an inbound event's body (INBOUND_BODY_MAX)": "PR 4",
  }),
});

/** The live terms, in bytes, for `objects` owner objects in the isolate. */
export function counts(objects = 1): Record<string, number> {
  const relay = memoryTerms();
  const tailUnits = Number(/export const TAIL_UNITS = ([0-9_]+);/
    .exec(readFileSync(join(PLATFORM, "owner", "src", "relay.ts"), "utf8"))![1]!
    .replace(/_/g, ""));
  return {
    pool: holds.OWNER_POOL_UNITS * UNIT_BYTES,
    tails: 2 * tailUnits,
    relay_per_object: objects * (relay.events! + relay.attachments! + relay.signed_out!
                                 + relay.tails! - 2 * tailUnits),
    baseline: holds.OWNER_BASELINE_BYTES,
  };
}

/** A door's `export const NAME = <a product of numbers>;`, read from its source. */
function doorBytes(file: string, name: string): number {
  const found = new RegExp(`export const ${name} = ([0-9_* ]+);`)
    .exec(readFileSync(join(PLATFORM, "doors", "src", file), "utf8"));
  if (found === null) {
    throw new Error(`doors/src/${file} holds no ${name}`);
  }
  return found[1]!.split("*").reduce((product, factor) =>
    product * Number(factor.trim().replace(/_/g, "")), 1);
}

const sum = (terms: Record<string, number>) =>
  Object.values(terms).reduce((total, bytes) => total + bytes, 0);

describe("an owner isolate's counts", () => {
  it("holds its live terms within 128 MB, and names the room left as uncounted", () => {
    const terms = counts();
    const total = sum(terms);
    const room = ISOLATE_BYTES - total;
    const perObject = counts(2).relay_per_object! - terms.relay_per_object!;
    console.log(`an owner isolate's live counts: ${total} bytes (${(total / MIB).toFixed(1)} ` +
                `MiB) of ${ISOLATE_BYTES}: ${JSON.stringify(terms)}; ${(room / MIB).toFixed(1)} ` +
                `MiB left, ${Math.floor(room / perObject) + 1} owner objects' relay terms in ` +
                `all; uncounted: ${JSON.stringify(UNCOUNTED)}`);
    expect(total).toBeLessThanOrEqual(ISOLATE_BYTES);
  });

  it("derives a burst's and a ship's units from what they hold at their most", () => {
    // A burst's peak is the more of its two, the arithmetic exact.
    expect(holds.BURST_PEAK_BYTES).toBe(Math.max(holds.BURST_CUT_BYTES,
                                                 holds.BURST_PAGE_BYTES));
    expect(Number.isSafeInteger(holds.BURST_PEAK_BYTES)).toBe(true);
    expect(holds.SHIP_UNITS * UNIT_BYTES).toBeGreaterThanOrEqual(holds.SHIP_BYTES);
    expect((holds.SHIP_UNITS - 1) * UNIT_BYTES).toBeLessThan(holds.SHIP_BYTES);
    expect(holds.BURST_UNITS * UNIT_BYTES).toBeGreaterThanOrEqual(holds.BURST_PEAK_BYTES);
    expect((holds.BURST_UNITS - 1) * UNIT_BYTES).toBeLessThan(holds.BURST_PEAK_BYTES);
    console.log(`a ship: ${holds.SHIP_UNITS} units for ${holds.SHIP_BYTES} bytes; a burst: ` +
                `${holds.BURST_UNITS} units for ${holds.BURST_PEAK_BYTES}`);
  });

  it("lets every declared hold run: each fits the pool beside its reserve", () => {
    const larger = holds.OWNER_POOL_UNITS - holds.OWNER_RESERVE_UNITS;
    for (const units of [holds.EDIT_READ_UNITS, holds.EDIT_MAKE_UNITS, holds.APPEND_UNITS,
                         holds.WINDOW_UNITS, holds.bodyUnits(1), holds.bodyUnits(2),
                         holds.BURST_UNITS, holds.SHIP_UNITS,
                         holds.moveTableUnits(MOVE_RECORDS) + holds.MOVE_PASS_UNITS]) {
      expect(units === 1 || units <= larger).toBe(true);
    }
    expect(holds.WINDOW_UNITS).toBe(1);  // a window takes the reserve, never waits on edits
    expect(holds.OWNER_RESERVE_UNITS).toBeGreaterThanOrEqual(1);
  });

  it("caps the larger waiters by the largest arguments a waiter holds on any door", () => {
    // A waiting call holds its own request: at most the largest body a door takes.
    const largest = Math.max(doorBytes("config.ts", "API_BODY_MAX"),
                             doorBytes("mcp.ts", "MCP_BODY_MAX"));
    const pool = holds.OWNER_POOL_UNITS * UNIT_BYTES;
    expect(holds.OWNER_WAITERS * largest).toBeLessThanOrEqual(pool);
    expect((holds.OWNER_WAITERS + 1) * largest).toBeGreaterThan(pool);
    // The one-unit waiters, capped apart, so larger waiters never refuse a small read.
    expect(holds.OWNER_SMALL_WAITERS).toBeGreaterThan(0);
  });

  it("sees a pool that would pass the isolate", () => {
    const terms = counts();
    const room = ISOLATE_BYTES - sum(terms);
    const passing = { ...terms, pool: terms.pool! + (Math.floor(room / UNIT_BYTES) + 1) *
                      UNIT_BYTES };
    expect(sum(passing)).toBeGreaterThan(ISOLATE_BYTES);
  });

  it("names every uncounted hold's owner", () => {
    for (const owed of Object.values(UNCOUNTED.holds)) {
      expect(owed).toMatch(/^(PR 3b|PR 4|STORE item 2)$/);
    }
  });
});
