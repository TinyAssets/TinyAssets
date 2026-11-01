/**
 * The owner app's sockets stay well inside the isolate's memory (A6, `sockets.ts`,
 * `live-tee.ts`), beside the owner-bound guard's database bound.
 *
 * An owner's object runs in an isolate of 128 MB that objects of one class may share, so
 * what its sockets keep in memory is bounded by the code and counted here, each term at its
 * worst, against `SOCKET_MEMORY_BUDGET` (an eighth of the isolate). The terms are what the
 * sockets add over the Sessions they pin, whose own memory (their context, their documents,
 * the immutable revisions pi's watches deliver) is `OPEN_MAX`'s, from live probe P4:
 * - the live tee's text: at most `TEE_LIMITS.sessions` streams (one per conversation a
 *   socket listens to, none for any other), each at most `TEE_TEXT_MAX` UTF-16 code units in
 *   all, its buffers doubling (so at most twice that allocated), and at most `TEE_BLOCKS_MAX`
 *   blocks, each with its first buffer;
 * - each socket's attachment, the platform's at most 2 KiB, and the SDK's cached copy;
 * - each socket's watch state on the instance (`Live`), its tee listener and `shown` map;
 * - pi's undelivered frames, at most 100 a watch (two a socket: its view and its task
 *   graph), each a frame object and its ops array, whose values are the Session's own;
 * - the told sign-outs held (`SIGNED_OUT_KEPT`).
 * What the runtime buffers of frames sent and not yet read by the app is the runtime's, not
 * the isolate's heap; P12 measures memory with sockets attached live.
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

/** A frozen limits object's field, as its module declares it. */
function field(module: string, name: string, key: string): number {
  const text = readFileSync(join(SRC, `${module}.ts`), "utf8");
  const start = text.indexOf(`export const ${name}`);
  const found = new RegExp(`${key}: ([0-9_]+)`).exec(text.slice(start, text.indexOf(";", start)));
  if (start < 0 || found === null) {
    throw new Error(`${module}.ts declares no ${name}.${key}`);
  }
  return Number(found[1]!.replace(/_/g, ""));
}

const KIB = 1024;
const MIB = 1024 * KIB;
/** The isolate's memory, and the share an owner's sockets may take of it. */
export const ISOLATE_BYTES = 128 * 1000 * 1000;
export const SOCKET_MEMORY_BUDGET = ISOLATE_BYTES / 8;
/** The platform's limit on a socket's serialized attachment. */
const ATTACHMENT_BYTES = 2 * KIB;
/** pi's undelivered frames a watch keeps before its one whole-view frame. */
const PI_PENDING_FRAMES = 100;
/** A pending frame's own allocation: its object and its ops array, the values shared. */
const FRAME_BYTES = 512;
/** A socket's `Live`, its waiters, its tee listener: a generous object graph. */
const LIVE_BYTES = 2 * KIB;
/** One `shown` entry, and one held sign-out (a grant id and its time). */
const SHOWN_BYTES = 64;
const SIGNED_OUT_BYTES = 256;
/** A block's first buffer (`LiveText`, 64 code units) with its objects. */
const BLOCK_BYTES = 64 * 2 + 128;

export const MEMORY = Object.freeze({
  ownerSockets: constant("sockets", "OWNER_SOCKETS"),
  teeSessions: field("live-tee", "TEE_LIMITS", "sessions"),
  teeText: constant("live-tee", "TEE_TEXT_MAX"),
  teeBlocks: constant("live-tee", "TEE_BLOCKS_MAX"),
  signedOut: constant("sockets", "SIGNED_OUT_KEPT"),
});

/** Each term's worst case, in bytes. */
export function memoryTerms(m: typeof MEMORY = MEMORY): Record<string, number> {
  return {
    tee_text: m.teeSessions * (2 * m.teeText * 2 + m.teeBlocks * BLOCK_BYTES),
    attachments: m.ownerSockets * 2 * ATTACHMENT_BYTES,
    socket_state: m.ownerSockets * (LIVE_BYTES + m.teeBlocks * SHOWN_BYTES),
    pending_frames: m.ownerSockets * 2 * PI_PENDING_FRAMES * FRAME_BYTES,
    signed_out: m.signedOut * SIGNED_OUT_BYTES,
  };
}

export function memoryWorstCase(m: typeof MEMORY = MEMORY): number {
  return Object.values(memoryTerms(m)).reduce((sum, bytes) => sum + bytes, 0);
}

describe("the owner app's sockets' memory bound", () => {
  it("keeps the sockets' worst case under an eighth of the isolate", () => {
    const bytes = memoryWorstCase();
    expect(bytes).toBeLessThan(SOCKET_MEMORY_BUDGET);
    console.log(`the sockets' memory worst case: ${bytes} bytes (${(bytes / MIB).toFixed(2)} ` +
                `MiB, ${(bytes / ISOLATE_BYTES * 100).toFixed(1)}% of the isolate's ` +
                `${ISOLATE_BYTES}): ${JSON.stringify(memoryTerms())}`);
  });

  it("reads the bounds the sources hold", () => {
    expect(MEMORY).toEqual({ ownerSockets: 16, teeSessions: 16, teeText: 65_536,
                             teeBlocks: 64, signedOut: 64 });
    // The tee listens to no more conversations than an owner's sockets can.
    expect(MEMORY.teeSessions).toBeLessThanOrEqual(MEMORY.ownerSockets);
    // Text is kept only for a conversation listened to, and goes with its last listener.
    const tee = readFileSync(join(SRC, "live-tee.ts"), "utf8");
    expect(tee).toContain("    if (!this.#listeners.has(sessionId)) {\n      return;\n    }");
    expect(tee).toContain("      this.#streams.delete(sessionId);  // no one listens: its text goes");
    // And the sockets keep no SQL row of their own.
    expect(readFileSync(join(SRC, "sockets.ts"), "utf8")).not.toMatch(/\.sql\b|storage\./);
  });

  it("sees a bound that would pass the budget", () => {
    expect(memoryWorstCase({ ...MEMORY, teeText: 64 * MIB })).toBeGreaterThan(
      SOCKET_MEMORY_BUDGET);
    expect(memoryWorstCase({ ...MEMORY, ownerSockets: 10_000 })).toBeGreaterThan(
      SOCKET_MEMORY_BUDGET);
  });
});
