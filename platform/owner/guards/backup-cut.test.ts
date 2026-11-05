/**
 * A generation's burst cut into parts at production sizes (`backup.ts` `cutParts`,
 * `GENERATION_SIZES`; rs_cap §2.1). The cut it replaced re-encoded the whole part so far for
 * every line, quadratic in the part: about 50 to 132 s of CPU a burst at these sizes, past
 * the owner Worker's CPU limit, so a large owner's generation never finished. Held here:
 * one burst's cut within a third of that limit (`cpu.ts` `STEP_CPU_MS`), on its thread, its
 * cost linear in its size, every part at
 * most `CHUNK_BYTES` and cut at a whole line, and its bytes those of the cut it replaced
 * for any line, multi-byte characters included.
 *
 * Measured in Node (`cpu.ts`), as `search-cpu.test.ts` measures the search; both are V8
 * with a native `TextEncoder`, and a live burst's CPU in workerd is not measured here.
 */
import { describe, expect, it } from "vitest";

import { cutParts, GENERATION_SIZES } from "../src/backup";
import { chunkLine, encodeLines, EXPORT_PAGE_BYTES, type Lines } from "../src/export";
import { CHUNK_BYTES } from "../src/manifest";
import { CPU_LIMIT_MS, cpuMs, STEP_CPU_MS } from "./cpu";

/** The cut `cutParts` replaced, verbatim but for its inputs: the reference for its bytes. */
function replacedCut(lines: readonly string[], part: number): Uint8Array[] {
  const parts: Uint8Array[] = [];
  let text = "";
  for (const line of lines) {
    const add = `${line}\n`;
    if (text !== "" && new TextEncoder().encode(text + add).byteLength > part) {
      parts.push(new TextEncoder().encode(text));
      text = "";
    }
    text += add;
  }
  if (text !== "") {
    parts.push(new TextEncoder().encode(text));
  }
  return parts;
}

/** A small deterministic generator, so a failure repeats. */
function random(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

/** Text of 1-, 2-, 3- and 4-byte characters, and lone surrogates, `units` UTF-16 long. */
const ALPHABET = ["a", "Z", "0", " ", "\"", "\\", "é", "ß", "€", "語", "😀", "\ud800", "\udc00"];
function mixed(next: () => number, units: number): string {
  let text = "";
  while (text.length < units) {
    text += ALPHABET[Math.floor(next() * ALPHABET.length)]!;
  }
  return text;
}

/**
 * A burst as the lane gathers one: export lines (rows of a notice's width, and chunk lines,
 * the stream's smallest at about 90 bytes) until their bytes reach `gather`.
 */
function burst(gather: number, seed = 1): string[] {
  const next = random(seed);
  const lines: string[] = [];
  let bytes = 0;
  for (let n = 0; bytes < gather; n++) {
    const line = n % 3 === 0
      ? chunkLine({ sha256: n.toString(16).padStart(64, "0"), size: CHUNK_BYTES })
      : JSON.stringify({ t: "row", table: "owner_notices", row: {
        seq: n, at: n, type: "agent", detail: mixed(next, 20 + Math.floor(next() * 200)) } });
    lines.push(line);
    bytes += Buffer.byteLength(line) + 1;
  }
  return lines;
}

/**
 * Lines as a burst's pages hold them, each page's encoded once (`encodeLines`): pages of
 * `EXPORT_PAGE_BYTES`, as `readPage` cuts them, or of the line counts `sizes` gives in turn.
 */
function paged(lines: readonly string[], sizes?: () => number): Lines[] {
  const pages: Lines[] = [];
  for (let at = 0; at < lines.length;) {
    let to = at;
    if (sizes === undefined) {
      for (let bytes = 0; to < lines.length && bytes < EXPORT_PAGE_BYTES; to++) {
        bytes += Buffer.byteLength(lines[to]!) + 1;
      }
    } else {
      to = Math.min(lines.length, at + sizes());
    }
    pages.push(encodeLines(lines.slice(at, to)));
    at = to;
  }
  return pages;
}

/** A burst's cut as the lane makes it: each page encoded once, then cut. */
const cut = (lines: readonly string[], part: number, sizes?: () => number) =>
  cutParts(paged(lines, sizes), part);

/** The least CPU of a few runs of one cut, so a stray collection does not count. */
async function leastMs(lines: readonly string[], part: number): Promise<number> {
  let least = Number.POSITIVE_INFINITY;
  for (let run = 0; run < 5; run++) {
    least = Math.min(least, await cpuMs(() => cut(lines, part)));
  }
  return least;
}

describe("a burst's cut into parts at production sizes", () => {
  const { part, gather } = GENERATION_SIZES;
  const lines = burst(gather);

  it("keeps one burst's cut inside a third of the CPU limit", async () => {
    const ms = await cpuMs(() => cut(lines, part));
    console.log(`one burst's cut: ${lines.length} lines, ${Math.round(ms)} ms CPU of ` +
                `${CPU_LIMIT_MS}`);
    expect(ms).toBeLessThan(STEP_CPU_MS);
  }, 60_000);

  it("costs in proportion to its size: a burst four times as large, parts and all, is " +
     "under eight times the CPU (the replaced cut's was sixteen)", async () => {
    const quarter = burst(gather / 4, 2);
    const small = await leastMs(quarter, part / 4);
    const large = await leastMs(lines, part);
    console.log(`the cut at a quarter: ${small.toFixed(1)} ms; at production: ` +
                `${large.toFixed(1)} ms`);
    expect(large).toBeLessThan(8 * Math.max(small, 1));
  }, 60_000);

  it("cuts parts of at most CHUNK_BYTES, each of whole lines, that join to the burst", () => {
    expect(part).toBe(CHUNK_BYTES);
    const parts = cut(lines, part);
    const decoder = new TextDecoder();
    expect(parts.length).toBeGreaterThan(1);
    for (const [n, bytes] of parts.entries()) {
      expect(bytes.byteLength).toBeLessThanOrEqual(CHUNK_BYTES);
      expect(bytes.at(-1)).toBe(0x0a);
      // Full: the next part's first line would not have fit in it.
      if (n + 1 < parts.length) {
        const first = parts[n + 1]!.indexOf(0x0a) + 1;
        expect(bytes.byteLength + first).toBeGreaterThan(CHUNK_BYTES);
      }
    }
    const whole = new TextEncoder().encode(lines.map((line) => `${line}\n`).join(""));
    expect(Buffer.compare(Buffer.concat(parts), Buffer.from(whole))).toBe(0);
    expect(decoder.decode(parts[0]!).split("\n").slice(0, -1).every((line) =>
      JSON.parse(line) !== null)).toBe(true);
  }, 60_000);
});

describe("a burst's cut", () => {
  it("makes byte for byte the parts of the cut it replaced, for lines of any characters " +
     "in pages of any sizes", () => {
    const next = random(7);
    for (const part of [1, 64, 1000, 4096, 65_536]) {
      // Lines of every width up to past a part, and the empty line, in pages of 0 to 40.
      const lines = Array.from({ length: 400 }, (_, n) => n % 50 === 0 ? ""
        : mixed(next, Math.floor(next() * part * (n % 7 === 0 ? 1.5 : 0.3))));
      const made = cut(lines, part, () => Math.floor(next() * 41));
      const reference = replacedCut(lines, part);
      expect(made.length).toBe(reference.length);
      for (const [n, bytes] of made.entries()) {
        expect(Buffer.compare(Buffer.from(bytes), Buffer.from(reference[n]!))).toBe(0);
      }
    }
    // A line that fits exactly, one byte past, and none at all.
    for (const lines of [["abc", "de"], ["abc", "def"], ["€", "€"], []]) {
      expect(cut(lines, 7).map((bytes) => Buffer.from(bytes).toString("hex")))
        .toEqual(replacedCut(lines, 7).map((bytes) => Buffer.from(bytes).toString("hex")));
    }
    // An empty page between two, and none.
    expect(cutParts([encodeLines(["ab"]), encodeLines([]), encodeLines(["cd"])], 6)
      .map((bytes) => Buffer.from(bytes).toString())).toEqual(["ab\ncd\n"]);
    expect(cutParts([], 6)).toEqual([]);
  }, 60_000);

  it("counts a line by its UTF-8, not its UTF-16: a part of three-byte characters is cut " +
     "at its bytes", () => {
    const lines = Array.from({ length: 100 }, () => "€".repeat(99));  // 298 bytes, 100 units
    const parts = cut(lines, 3_000);
    expect(parts.every((bytes) => bytes.byteLength <= 3_000)).toBe(true);
    expect(parts[0]!.byteLength).toBe(10 * 298);
  });
});
