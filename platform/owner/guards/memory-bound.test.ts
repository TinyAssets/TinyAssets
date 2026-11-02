/**
 * The runs' live output stays well inside the isolate's memory (slice R, `relay.ts`; A6's
 * guard, salvaged), beside the owner-bound guard's database bound.
 *
 * An owner's object runs in an isolate of 128 MB that objects of one class may share, so
 * what its live output keeps in memory is bounded by the code and counted here, each term
 * at its worst, against `RELAY_MEMORY_BUDGET` (an eighth of the isolate):
 * - the runs' tails: every live run's kept frames together, at most `TAIL_BYTES` UTF-16 code
 *   units (two bytes each), and the one frame being relayed past it;
 * - each socket's attachment, the platform's at most 2 KiB, and the SDK's cached copy, at
 *   `OWNER_SOCKETS`, the relay's technical ceiling;
 * - the told sign-outs held (`SIGNED_OUT_KEPT`).
 * A frame longer than `TAIL_FRAME_MAX` is relayed but never kept. What the runtime buffers of
 * frames sent and not yet read is the runtime's, not the isolate's heap. Frames are never a
 * SQL row: the relay names no storage.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "./sources";

const SRC = join(PLATFORM, "owner", "src");

/** A module's `export const NAME = <number>;`, read from its source. */
function constant(module: string, name: string): number {
  const found = new RegExp(`export const ${name}(?:: number)? = ([0-9_]+);`)
    .exec(readFileSync(join(SRC, `${module}.ts`), "utf8"));
  if (found === null) {
    throw new Error(`${module}.ts holds no numeric ${name}`);
  }
  return Number(found[1]!.replace(/_/g, ""));
}

const KIB = 1024;
const MIB = 1024 * KIB;
/** The isolate's memory, and the share an owner's live output may take of it. */
export const ISOLATE_BYTES = 128 * 1000 * 1000;
export const RELAY_MEMORY_BUDGET = ISOLATE_BYTES / 8;
/** The platform's limit on a socket's serialized attachment. */
const ATTACHMENT_BYTES = 2 * KIB;
/** A tail's own objects beside its text: its array, its entry, its counters. */
const TAIL_OVERHEAD = 256;
/** One held sign-out: a grant id and its time. */
const SIGNED_OUT_BYTES = 256;

export const MEMORY = Object.freeze({
  tailUnits: constant("relay", "TAIL_BYTES"),
  frameMax: constant("relay", "TAIL_FRAME_MAX"),
  ownerSockets: constant("relay", "OWNER_SOCKETS"),
  signedOut: constant("relay", "SIGNED_OUT_KEPT"),
  liveRuns: constant("tier", "RUNS_CEILING"),
});

/** Each term's worst case, in bytes. */
export function memoryTerms(m: typeof MEMORY = MEMORY): Record<string, number> {
  return {
    tails: 2 * (m.tailUnits + m.frameMax) + m.liveRuns * TAIL_OVERHEAD,
    attachments: m.ownerSockets * 2 * ATTACHMENT_BYTES,
    signed_out: m.signedOut * SIGNED_OUT_BYTES,
  };
}

export function memoryWorstCase(m: typeof MEMORY = MEMORY): number {
  return Object.values(memoryTerms(m)).reduce((sum, bytes) => sum + bytes, 0);
}

describe("the runs' live output's memory bound", () => {
  it("keeps the relay's worst case under an eighth of the isolate", () => {
    const bytes = memoryWorstCase();
    expect(bytes).toBeLessThan(RELAY_MEMORY_BUDGET);
    console.log(`the relay's memory worst case: ${bytes} bytes (${(bytes / MIB).toFixed(2)} ` +
                `MiB, ${(bytes / ISOLATE_BYTES * 100).toFixed(1)}% of the isolate's ` +
                `${ISOLATE_BYTES}): ${JSON.stringify(memoryTerms())}`);
  });

  it("reads the bounds the sources hold, and the relay keeps no row", () => {
    expect(MEMORY).toEqual({ tailUnits: 4_194_304, frameMax: 262_144, ownerSockets: 1024,
                             signedOut: 64, liveRuns: 1000 });
    const relay = readFileSync(join(SRC, "relay.ts"), "utf8");
    expect(relay).not.toMatch(/\.sql\b|storage\./);
    // The tail is trimmed to its bound as each frame is kept, the longest tail's oldest first.
    expect(relay).toContain("while (this.#tailBytes > TAIL_BYTES) {");
    expect(relay).toContain("if (text.length <= TAIL_FRAME_MAX) {");
  });

  it("sees a bound that would pass the budget", () => {
    expect(memoryWorstCase({ ...MEMORY, tailUnits: 64 * MIB })).toBeGreaterThan(
      RELAY_MEMORY_BUDGET);
    expect(memoryWorstCase({ ...MEMORY, ownerSockets: 10_000 })).toBeGreaterThan(
      RELAY_MEMORY_BUDGET);
  });
});
