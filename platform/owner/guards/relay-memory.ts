/**
 * The relay's memory terms, each at its worst, read from the sources (`memory-bound.test.ts`
 * holds them within its share of the isolate; `memory-counts.test.ts` counts the per-object
 * ones in the isolate's counts).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { PLATFORM } from "../../ci/paths";
import { RUNS_CEILING } from "../../contract/src/control";
import {
  EVENT_FRAMES_MAX, EVENTS_BODY_MAX, EVENTS_IN_FLIGHT, FRAME_UNITS_MAX,
} from "../../contract/src/run-events";

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

/** The relay's memory terms' inputs, each a count. */
export const MEMORY: Readonly<Record<"tailUnits" | "frameMax" | "eventsBody" | "eventFrames"
  | "frameUnits" | "postsHeld" | "ownerSockets" | "signedOut" | "liveRuns", number>> = Object.freeze({
  tailUnits: constant("relay", "TAIL_UNITS"),
  frameMax: constant("relay", "TAIL_FRAME_UNITS"),
  eventsBody: EVENTS_BODY_MAX,
  eventFrames: EVENT_FRAMES_MAX,
  frameUnits: FRAME_UNITS_MAX,
  postsHeld: EVENTS_IN_FLIGHT,
  ownerSockets: constant("relay", "OWNER_SOCKETS"),
  signedOut: constant("relay", "SIGNED_OUT_KEPT"),
  liveRuns: RUNS_CEILING,
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

