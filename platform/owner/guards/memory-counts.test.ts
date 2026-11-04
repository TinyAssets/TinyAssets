/**
 * The counts test (ADR-037, memshape_research.md §2): an owner isolate's memory is bounded by
 * structure, so the bound is a sum of counts the code enforces times costs the code
 * constructs, read from the sources: its chunk slots (`OWNER_CHUNK_SLOTS` x
 * `CHUNK_SLOT_BYTES`), the relay's tails (`TAIL_BYTES` UTF-16 units, every object's in the
 * isolate together), the Worker's resting baseline and what no unit models, all within the
 * isolate's 128 MB. The slot's cost is the workerd memory leg's to prove (`memory/leg.mjs`:
 * a slot's path holds at most it, whatever its data's size); the baseline and the unmodeled
 * gap are workerd's figures until production's replace them. The relay's per-object terms
 * (its sockets' attachments, its events posts) are bounded per object (`memory-bound.test.ts`).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "./sources";

const MIB = 1024 * 1024;
const UNITS: Record<string, number> = { MIB, UNIT_BYTES: 4 * MIB };

/** A module's `export const NAME = <sum of products>;`, its factors numbers or `UNITS`. */
function constant(path: string, name: string): number {
  const found = new RegExp(`export const ${name} = ([^;]+);`)
    .exec(readFileSync(join(PLATFORM, path), "utf8"));
  if (found === null) {
    throw new Error(`${path} holds no ${name}`);
  }
  return found[1]!.split("+").reduce((sum, term) => sum + term.split("*").reduce(
    (product, factor) => product * (UNITS[factor.trim()] ?? Number(factor.trim().replace(/_/g, ""))),
    1), 0);
}

export const ISOLATE_BYTES = 128_000_000;

export function counts(): Record<string, number> {
  return {
    slots: constant("owner/src/holds.ts", "OWNER_CHUNK_SLOTS")
      * constant("owner/src/slots.ts", "CHUNK_SLOT_BYTES"),
    tails: 2 * constant("owner/src/relay.ts", "TAIL_BYTES"),
    baseline: constant("owner/src/holds.ts", "OWNER_BASELINE_BYTES"),
    unmodeled: constant("owner/src/holds.ts", "UNMODELED_BYTES"),
  };
}

describe("an owner isolate's counts", () => {
  it("holds its slots, its tails, its baseline and what no unit models within 128 MB", () => {
    const terms = counts();
    const total = Object.values(terms).reduce((sum, bytes) => sum + bytes, 0);
    console.log(`an owner isolate's counts: ${total} bytes of ${ISOLATE_BYTES}: ` +
                JSON.stringify(terms));
    expect(terms).toEqual({ slots: 4 * 12 * MIB, tails: 8 * MIB, baseline: 8 * MIB,
                            unmodeled: 24 * MIB });
    expect(total).toBeLessThanOrEqual(ISOLATE_BYTES);
  });

  it("sees a pool that would pass the isolate", () => {
    const terms = counts();
    const others = terms.tails! + terms.baseline! + terms.unmodeled!;
    const room = Math.floor((ISOLATE_BYTES - others) / (12 * MIB));
    expect(room).toBeGreaterThanOrEqual(4);
    expect(others + (room + 1) * 12 * MIB).toBeGreaterThan(ISOLATE_BYTES);
  });
});
