/**
 * The search's worst case, measured (review M3): one call's CPU at every bound it allows,
 * `SEARCH_SCAN` entries of inline files at `INLINE_BYTES` each and a query of
 * `SEARCH_QUERY_BYTES` of distinct words, in two shapes: pages of distinct words, and OKF
 * indexes of as many lines as fit, each naming a file of the page. The figures go in
 * `limits-audit.md`; the test holds each well inside Workers' CPU limit an invocation
 * (30 s by default, developers.cloudflare.com/workers/platform/limits/#cpu-time), at a tenth
 * of it, so a slower host still has room. It also measures what a query's words take.
 */
import { describe, expect, it } from "vitest";

import { EGRESS_BODY_MAX } from "../src/network";
import {
  SEARCH_ANSWER_BYTES, SEARCH_QUERY_BYTES, SEARCH_SCAN, type Scanned, searchPage, words,
} from "../src/file-search";

const INLINE = 4096;
const encoder = new TextEncoder();

/** Distinct words from `from`, joined by spaces, to `bytes` bytes. */
function distinct(from: number, bytes: number): { text: string; next: number } {
  const parts: string[] = [];
  let size = 0;
  let n = from;
  for (;;) {
    const word = `w${(n++).toString(36)}`;
    if (size + word.length + 1 > bytes) {
      break;
    }
    parts.push(word);
    size += word.length + 1;
  }
  return { text: parts.join(" "), next: n };
}

function cpuMs(act: () => unknown): number {
  const start = process.cpuUsage();
  act();
  const used = process.cpuUsage(start);
  return (used.user + used.system) / 1000;
}

describe("the search's worst case", () => {
  const query = distinct(0, SEARCH_QUERY_BYTES);
  const terms = new Set(words(query.text));

  it("keeps one call of pages of distinct words inside a tenth of the CPU limit", () => {
    // Each page its own run of the query's words, so every page matches many of them.
    const entries: Scanned[] = Array.from({ length: SEARCH_SCAN }, (_, i) => {
      const page = distinct((i * 997) % (terms.size - 1000), INLINE);
      return { path: `/workspace/k/p${i}.md`, kind: "file", inline: encoder.encode(page.text) };
    });
    let found = 0;
    const ms = cpuMs(() => {
      found = searchPage(entries, false, terms, () => "u").results.length;
    });
    console.log(`search worst case, distinct words: ${Math.round(ms)} ms CPU, ${found} results`);
    expect(found).toBe(SEARCH_SCAN);
    expect(ms).toBeLessThan(3_000);
  }, 60_000);

  it("keeps one call of full indexes inside a tenth of the CPU limit", () => {
    const entries: Scanned[] = [];
    for (let i = 0; i < SEARCH_SCAN; i++) {
      const dir = `/workspace/k/d${String(i).padStart(4, "0")}`;
      if (i % 2 === 0) {
        let text = "---\nokf_version: \"0.2\"\n---\n";
        for (let line = 0; encoder.encode(text).byteLength < INLINE - 40; line++) {
          text += `* [w${line}](/x.md) - w${line}\n`;
        }
        entries.push({ path: `${dir}/index.md`, kind: "file", inline: encoder.encode(text) });
      } else {
        entries.push({ path: `${dir}/x.md`, kind: "file", inline: encoder.encode("w1 w2") });
      }
    }
    const ms = cpuMs(() => searchPage(entries, false, terms, () => "u"));
    console.log(`search worst case, full indexes: ${Math.round(ms)} ms CPU`);
    expect(ms).toBeLessThan(3_000);
  }, 60_000);

  it("keeps the worst answer, envelope and escapes included, under the body ceiling", () => {
    // Every result at its widest and worst to escape: a 4,096-byte path of quotes and
    // backslashes in names of 255 bytes, a title of as many, a url of its encoded folder.
    const name = '"\\'.repeat(127) + "x";
    const parts: string[] = [];
    while (`/workspace/${[...parts, name].join("/")}`.length <= 4096 - 10) {
      parts.push(name);
    }
    const dir = `/workspace/${parts.join("/")}`;
    const title = '"\\'.repeat(1900);
    const entries: Scanned[] = Array.from({ length: SEARCH_SCAN }, (_, i) => ({
      path: `${dir}/${String(i).padStart(5, "0")}.md`, kind: "file",
      inline: encoder.encode(`---\ntitle: ${title}\n---\nneedle`) }));
    const url = (path: string) => `https://tinyassets.io/app#data/${
      encodeURIComponent(path.slice(0, path.lastIndexOf("/")))}`;
    const page = searchPage(entries, true, new Set(["needle"]), url);
    expect(page.resume).not.toBeNull();  // it stopped at its bytes, not its scan
    const result = { results: page.results, next_cursor: page.resume,
                     note: "more of the files are still to search: search again with next_cursor" };
    // As the MCP door answers it (`mcp.ts`): structured content and its JSON text copy, in
    // a JSON-RPC response, framed as a server-sent event.
    const answer = `event: message\ndata: ${JSON.stringify({ jsonrpc: "2.0", id: Number.MAX_SAFE_INTEGER,
      result: { content: [{ type: "text", text: JSON.stringify(result) }],
                structuredContent: result } })}\n\n`;
    const bytes = encoder.encode(answer).byteLength;
    console.log(`worst answer: ${page.results.length} results, ${bytes} bytes of ${EGRESS_BODY_MAX}`);
    expect(bytes).toBeLessThanOrEqual(EGRESS_BODY_MAX);
    expect(encoder.encode(JSON.stringify(page.results)).byteLength)
      .toBeLessThanOrEqual(SEARCH_ANSWER_BYTES);
  }, 60_000);

  it("holds a query's words to their measured heap", () => {
    const ms = cpuMs(() => words(query.text));
    console.log(`a ${SEARCH_QUERY_BYTES}-byte query: ${terms.size} words, ${Math.round(ms)} ms CPU`);
    expect(terms.size).toBeGreaterThan(100_000);
  });
});
