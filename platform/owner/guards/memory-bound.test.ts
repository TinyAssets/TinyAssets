/**
 * The isolate's memory ledger (`memory.ts`): the one budget every Worker's requests share,
 * each path's declared grant, the relay's reservation, and the recorded baseline, read from
 * the sources and held together here; beside the owner-bound guard's database bound
 * (`owner-bound.test.ts`) and the storage audit (`limits-audit.test.ts`), which keep their
 * own domains. Each grant is measured in workerd by `test/memory-peaks.mjs`.
 *
 * **The budget.** `GUARD_BYTES` is the isolate's 128 MB less `BASELINE_BYTES` (recorded,
 * to be replaced by production's figure). A grant is admitted while the grants in flight,
 * the reservations and the largest grant once more (the collector's room for its garbage)
 * fit it, so each path's grant must fit alone: twice it, with its Worker's reservations,
 * within `GUARD_BYTES`. In the owner's isolate that is every read's page (`READ_GRANT`), a
 * placed body's write at its most (`writeGrant(BODY_BYTES_MAX)`), and a placed body read
 * whole for a run's input (`wholeGrant`), beside the relay's reservation; in the doors',
 * every tool call's request at its door's ceiling with its answer (`requestGrant`).
 *
 * **The relay's reservation.** An owner's object runs in an isolate of 128 MB that objects
 * of one class may share, so what its live output keeps in memory is bounded by the code and
 * counted here, each term at its worst, against `RELAY_RESERVED` (an eighth of the
 * isolate), which the first object an isolate runs reserves out of the budget:
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
 * SQL row: the relay names no storage. Its terms are one object's: objects sharing an
 * isolate each keep their own, and the reservation is one share, so a second object with
 * live runs in the same isolate is beyond what the guard reserves (owed: the relay's tails
 * held under request-scoped grants, with slice R's next change).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { ANSWER_GRANT, REQUEST_FACTOR, requestGrant } from "../../doors/src/answers";
import { API_BODY_MAX, APP_BODY_MAX } from "../../doors/src/config";
import {
  BODY_BYTES_MAX, READ_GRANT, WINDOWS_BYTES, wholeGrant, writeGrant,
} from "../src/bodies";
import { RESULT_CHARS } from "../src/encoded";
import { MAX_BODY_BYTES } from "../src/ledger";
import {
  BASELINE_BYTES, GUARD_BYTES, ISOLATE_BYTES, RELAY_RESERVED, SMALL_BYTES,
} from "../src/memory";
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
/** The share an owner's live output may take of the isolate: the relay's reservation. */
export const RELAY_MEMORY_BUDGET = RELAY_RESERVED;
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

/** A doors' request at `max` bytes, as its grant counts it. */
function doorsGrant(max: number): number {
  return requestGrant(new Request("https://doors/", { method: "POST",
                                                      headers: { "Content-Length": `${max}` } }),
                      max);
}

/** The MCP door's body ceiling, read from its source (it imports the MCP SDK). */
const MCP_BODY_MAX = Number(/export const MCP_BODY_MAX = ([0-9* ]+);/
  .exec(readFileSync(join(PLATFORM, "doors", "src", "mcp.ts"), "utf8"))![1]!
  .split("*").reduce((product, term) => product * Number(term.trim()), 1));

/** Each Worker's declared grants, and what its isolate reserves. */
export function ledger(): Record<string, { reserved: number; grants: Record<string, number> }> {
  return {
    owner: { reserved: RELAY_RESERVED, grants: {
      read: READ_GRANT, placed_write: writeGrant(BODY_BYTES_MAX),
      whole_read: wholeGrant(MAX_BODY_BYTES) } },
    doors: { reserved: 0, grants: {
      api: doorsGrant(API_BODY_MAX), app: doorsGrant(APP_BODY_MAX),
      mcp: doorsGrant(MCP_BODY_MAX) } },
  };
}

describe("the isolate's memory ledger", () => {
  it("holds the budget to the isolate less its recorded baseline", () => {
    expect(ISOLATE_BYTES).toBe(128_000_000);
    // Recorded from workerd (`test/memory-peaks.mjs` fails past it), to be replaced by each
    // Worker's production figure once deployed.
    expect(BASELINE_BYTES).toBe(32 * MIB);
    expect(GUARD_BYTES).toBe(ISOLATE_BYTES - BASELINE_BYTES);
    expect(SMALL_BYTES).toBe(64 * KIB);
    expect([REQUEST_FACTOR, ANSWER_GRANT, WINDOWS_BYTES])
      .toEqual([2, 16 * RESULT_CHARS, 4 * RESULT_CHARS]);
    expect(MCP_BODY_MAX).toBe(4 * MIB);
  });

  it("fits every path's grant alone, twice over, beside its Worker's reservations", () => {
    for (const [worker, { reserved, grants }] of Object.entries(ledger())) {
      for (const [path, bytes] of Object.entries(grants)) {
        expect(`${worker} ${path}: ${2 * bytes + reserved <= GUARD_BYTES}`)
          .toBe(`${worker} ${path}: true`);
      }
    }
    console.log(`the memory ledger, of ${GUARD_BYTES} bytes: ${JSON.stringify(ledger())}`);
  });

  it("sees a grant that would not fit", () => {
    expect(2 * writeGrant(16 * MIB) + RELAY_RESERVED).toBeGreaterThan(GUARD_BYTES);
    expect(2 * doorsGrant(32 * MIB)).toBeGreaterThan(GUARD_BYTES);
  });
});

describe("the runs' live output's memory bound", () => {
  it("keeps the relay's worst case within its reservation, an eighth of the isolate", () => {
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
    expect(relay).toContain("while (this.#tailBytes > TAIL_BYTES) {");
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
