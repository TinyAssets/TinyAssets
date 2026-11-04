/**
 * The runs' live output stays well inside the isolate's memory (slice R, `relay.ts`; A6's
 * guard, salvaged), beside the owner-bound guard's database bound.
 *
 * An owner's object runs in an isolate of 128 MB that objects of one class may share, so
 * what its live output keeps in memory is bounded by the code and counted here, each term
 * at its worst, against `RELAY_MEMORY_BUDGET` (an eighth of the isolate):
 * - the runs' tails: every live run's kept frames together, at most `TAIL_BYTES` UTF-16 code
 *   units (two bytes each);
 * - the runners' events posts the object holds while it reads and relays them (slice R's
 *   review M-4 and round 2): each its body, at most `EVENTS_BODY_MAX` bytes, once as it
 *   arrived and once parsed (its strings two bytes a unit), the one frame being relayed as
 *   its JSON text (at most `FRAME_UNITS_MAX` units, two bytes each), and each of its at most
 *   `EVENT_FRAMES_MAX` frames' own objects; the posts the object holds at once, at most
 *   `EVENTS_IN_FLIGHT` whatever the tier (`runEvents`' gate: one more is answered
 *   `events_busy` and posted again), and the one being refused beside them;
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
/** One posted frame's own objects beside its text: the frame, its relayed copy, numbers. */
const FRAME_OVERHEAD = 512;

export const MEMORY = Object.freeze({
  tailUnits: constant("relay", "TAIL_BYTES"),
  frameMax: constant("relay", "TAIL_FRAME_MAX"),
  eventsBody: constant("run-events", "EVENTS_BODY_MAX"),
  eventFrames: constant("run-events", "EVENT_FRAMES_MAX"),
  frameUnits: constant("run-events", "FRAME_UNITS_MAX"),
  postsHeld: constant("run-events", "EVENTS_IN_FLIGHT"),
  ownerSockets: constant("relay", "OWNER_SOCKETS"),
  signedOut: constant("relay", "SIGNED_OUT_KEPT"),
  liveRuns: constant("tier", "RUNS_CEILING"),
});

/** Each term's worst case, in bytes. */
export function memoryTerms(m: typeof MEMORY = MEMORY): Record<string, number> {
  return {
    tails: 2 * m.tailUnits + m.liveRuns * TAIL_OVERHEAD,
    events: (m.postsHeld + 1) * (3 * m.eventsBody + 2 * m.frameUnits
                                 + m.eventFrames * FRAME_OVERHEAD),
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
    expect(MEMORY).toEqual({ tailUnits: 4_194_304, frameMax: 262_144, eventsBody: 65_536,
                             eventFrames: 64, frameUnits: 32_768, postsHeld: 8,
                             ownerSockets: 1024,
                             signedOut: 64, liveRuns: 1000 });
    // A frame a post may carry is one the tail may keep.
    expect(MEMORY.frameUnits).toBeLessThanOrEqual(MEMORY.frameMax);
    const relay = readFileSync(join(SRC, "relay.ts"), "utf8");
    expect(relay).not.toMatch(/\.sql\b|storage\./);
    // The tail is trimmed to its bound as each frame is kept, the longest tail's oldest first.
    // Every object's tails in the isolate share one budget (`slots.ts`, review M5).
    expect(relay).toContain("const tails = isolateTails(TAIL_BYTES);");
    expect(relay).toContain("tails.trim();");
    expect(relay).toContain("if (text.length <= TAIL_FRAME_MAX) {");
  });

  it("sees a bound that would pass the budget", () => {
    expect(memoryWorstCase({ ...MEMORY, tailUnits: 64 * MIB })).toBeGreaterThan(
      RELAY_MEMORY_BUDGET);
    expect(memoryWorstCase({ ...MEMORY, ownerSockets: 10_000 })).toBeGreaterThan(
      RELAY_MEMORY_BUDGET);
    // The egress body's 16 MiB as an events post would pass it alone.
    expect(memoryWorstCase({ ...MEMORY, eventsBody: 16 * MIB })).toBeGreaterThan(
      RELAY_MEMORY_BUDGET);
    // The posts held are the gate's, whatever the tier: a bigger gate would pass it.
    expect(memoryWorstCase({ ...MEMORY, postsHeld: 50 })).toBeGreaterThan(RELAY_MEMORY_BUDGET);
  });
});
