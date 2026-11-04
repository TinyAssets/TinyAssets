/**
 * The chunk-wise `file_edit` (`edit.ts`, ADR-037): the same chunks or the same refusal as
 * the whole-file editor it replaces (the oracle, below), on fuzzed small-chunk files; a
 * source that changes between its passes fails loudly; and through the door, edits at a
 * file's start, a chunk's seam and its end, of the same length and longer, on a 16 MiB file.
 */
import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import { planEdit, type Source } from "../src/edit";
import { owner } from "./generation-support";
import { failure } from "./support";

const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const MIB = 1024 * 1024;

function stored(bytes: Uint8Array, chunk: number, reads: { n: number } = { n: 0 }): Source {
  const chunks: { sha256: string; size: number; bytes: Uint8Array }[] = [];
  for (let at = 0; at < bytes.byteLength; at += chunk) {
    const piece = bytes.slice(at, Math.min(at + chunk, bytes.byteLength));
    chunks.push({ sha256: sha(piece), size: piece.byteLength, bytes: piece });
  }
  return { size: bytes.byteLength, chunks,
           read: async (index) => {
             reads.n += 1;
             return chunks[index]!.bytes.slice();
           } };
}

/** Today's whole-file editor: decode, indexOf, encode, cut. */
function whole(bytes: Uint8Array, old: string, replacement: string, chunk: number): string {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    return "not_utf8";
  }
  const first = text.indexOf(old);
  if (first < 0) {
    return "absent";
  }
  let times = 0;
  for (let at = first; at >= 0; at = text.indexOf(old, at + 1)) {
    times += 1;
  }
  if (times > 1) {
    return `times:${times}`;
  }
  const out = new TextEncoder().encode(text.slice(0, first) + replacement +
                                       text.slice(first + old.length));
  const hashes: string[] = [];
  for (let at = 0; at < out.byteLength; at += chunk) {
    hashes.push(sha(out.subarray(at, Math.min(at + chunk, out.byteLength))));
  }
  return JSON.stringify(hashes);
}

async function chunked(bytes: Uint8Array, old: string, replacement: string, chunk: number):
    Promise<string> {
  const plan = await planEdit(stored(bytes, chunk), old, replacement, chunk);
  if ("refused" in plan) {
    return typeof plan.refused === "string" ? plan.refused : `times:${plan.refused.times}`;
  }
  for (const [index] of plan.sizes.entries()) {
    expect(sha(await plan.make(index))).toBe(plan.hashes[index]);
  }
  return JSON.stringify(plan.hashes);
}

function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => (state = (state * 1664525 + 1013904223) >>> 0) / 2 ** 32;
}
const ALPHA = ["a", "b", "é", "一", "😀", "\n", "ab"];
function text(random: () => number, n: number): string {
  let out = "";
  while (out.length < n) {
    out += ALPHA[Math.floor(random() * ALPHA.length)];
  }
  return out;
}

describe("a chunk-wise edit", () => {
  it("answers what the whole-file editor answers, on fuzzed files of small chunks", async () => {
    let edits = 0;
    for (let seed = 1; seed <= 4000; seed++) {
      const random = rng(seed);
      const chunk = 1 + Math.floor(random() * 24);
      const body = text(random, Math.floor(random() * 120));
      let old = random() < 0.7 && body.length > 0
        ? body.slice(Math.floor(random() * body.length)).slice(0, 1 + Math.floor(random() * 10))
        : text(random, 1 + Math.floor(random() * 4));
      if (!old.isWellFormed()) {
        old = "a";
      }
      const replacement = random() < 0.3 ? [...old].reverse().join("").slice(0, old.length)
        : text(random, Math.floor(random() * 12));
      if (!replacement.isWellFormed()) {
        continue;
      }
      let bytes = new TextEncoder().encode(body);
      if (random() < 0.03 && bytes.byteLength > 2) {
        bytes = bytes.slice();
        bytes[Math.floor(random() * bytes.byteLength)] = 0xff;
      }
      const expected = whole(bytes, old, replacement, chunk);
      expect(await chunked(bytes, old, replacement, chunk), `seed ${seed}`).toBe(expected);
      edits += expected.startsWith("[") ? 1 : 0;
    }
    expect(edits).toBeGreaterThan(1000);
  });

  it("refuses ill-formed text, and fails loudly when a source changes between its passes",
     async () => {
    const bytes = new TextEncoder().encode("a".repeat(40) + "MARK" + "b".repeat(40));
    expect(await planEdit(stored(bytes, 16), "\ud800", "x", 16))
      .toEqual({ refused: "ill_formed" });
    // The source's chunk changes after the edit hashed it: making that chunk fails loudly.
    const source = stored(bytes, 16);
    const plan = await planEdit(source, "MARK", "DONE, longer", 16);
    if ("refused" in plan) {
      throw new Error("refused");
    }
    (source.chunks.at(-1) as unknown as { bytes: Uint8Array }).bytes[0] = 0x7a;
    expect((await failure(() => plan.make(plan.sizes.length - 1))).message)
      .toBe("a file's source changed between an edit's passes");
  });

  it("edits a 16 MiB file at its start, a seam and its end, of the same length and longer",
     async () => {
    const o = await owner();
    const path = "/workspace/big.md";
    await o.stub.fileWrite(o.bind(), { path, expected_etag: null,
                                       content: "a".repeat(4 * MIB - 8) + "<<SEAM>>" });
    for (let n = 0; n < 3; n++) {
      await o.stub.fileAppend(o.bind(), { path, content: "a".repeat(4 * MIB) });
    }
    await o.stub.fileAppend(o.bind(), { path, content: "<<END>>" });
    const entry = async () => (await o.stub.fileStat(o.bind(), { path })).entry!;
    await o.stub.fileEdit(o.bind(), { path, old_text: "aaaa", new_text: "<<START>>",
                                      expected_etag: (await entry()).etag })
      .catch((error: Error) => expect(error.message).toMatch(/occurs \d+ times/));
    for (const [old, replacement] of [["<<SEAM>>", "<<DONE>>"], ["<<END>>", "<<END, longer>>"]]) {
      const done = await o.stub.fileEdit(o.bind(), { path, old_text: old!,
        new_text: replacement!, expected_etag: (await entry()).etag });
      expect(done).toHaveProperty("entry");
    }
    const read = await o.stub.fileText(o.bind(), { path, offset: 4 * MIB - 8, max_bytes: 16 });
    expect(read.text.startsWith("<<DONE>>")).toBe(true);
    expect((await entry()).size).toBe(16 * MIB + "<<END, longer>>".length);
  }, 300_000);
});
