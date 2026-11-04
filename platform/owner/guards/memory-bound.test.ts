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

import {
  ISOLATE_BYTES, MEMORY, memoryTerms, memoryWorstCase, RELAY_MEMORY_BUDGET,
} from "./relay-memory";
import { PLATFORM } from "./sources";

const SRC = join(PLATFORM, "owner", "src");
const MIB = 1024 * 1024;

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
