/**
 * Every read pages by its answer's encoded size (`encoded.ts`, `pages.ts`): the count is
 * exact without building the JSON, a page holds whole items while they fit `RESULT_CHARS`
 * and always its first, the first's body cut at a character and saying so, and a read by
 * range (`body_offset`) returns the rest, byte-exact.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import type { Window } from "../src/bodies";
import { bodied } from "../src/pages";

import {
  escapedBytes, fitted, jsonBytes, RESULT_CHARS, unitAt, utf8Length,
} from "../src/encoded";
import type { Row } from "../src/collections";
import type { WrittenRow } from "../src/owner-do";
import { MUSE, rig } from "./ledger-support";
import { at, readOn } from "./pages-support";
import { failure, ownerObject } from "./support";

const MIB = 1024 * 1024;
const enc = new TextEncoder();
const bytes = (value: unknown) => enc.encode(JSON.stringify(value)).byteLength;

/** Bodies of every escape class: six bytes a byte, two, one, three, four a character. */
const SHAPES: Record<string, string> = {
  control: String.fromCharCode(1),
  quote: "\"",
  ascii: "x",
  cjk: "中",
  pair: "𝄞",
};

describe("the exact count", () => {
  it("counts what JSON.stringify makes, byte for byte, without making it", () => {
    for (const ch of [...Object.values(SHAPES), "\\", "\n", "\t", "\u007f", "é", " ",
                      "\ud800", "\udc00x", "a\ud834", "\u0000\u001f"]) {
      const text = `a${ch.repeat(37)}b`;
      expect(escapedBytes(text)).toBe(bytes(text));
      expect(utf8Length(text)).toBe(enc.encode(text).byteLength);
    }
    const value = { a: [1, -2.5, null, undefined, () => 0, "q\"", { b: true }], c: undefined,
                    d: Number.NaN, e: { toJSON: () => "j" }, f: [], g: {} };
    expect(jsonBytes(value)).toBe(bytes(value));
  });

  it("cuts at a character to fit, never inside a pair", () => {
    const text = SHAPES["pair"]!.repeat(10);
    const part = fitted(text, 2 + 4 * 3 + 3);
    expect(part).toEqual({ text: SHAPES["pair"]!.repeat(3), bytes: 12 });
    expect(fitted(`${SHAPES["control"]}x`, 8)).toEqual({ text: SHAPES["control"], bytes: 1 });
    expect(fitted("abc", 1)).toEqual({ text: "", bytes: 0 });
    expect(unitAt(text, 8)).toBe(4);
    expect(unitAt(text, 6)).toBeNull();
  });
});

describe("a read's pages", () => {
  it("answers the reviewer's 8 MiB control-character body in pages under the cap, " +
     "reassembled byte-exact by range", { tags: ["large"] }, async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const body = SHAPES["control"]!.repeat(8 * MIB);
    await r.post(null, `dm:${muse}`, { body });
    await r.post(null, `dm:${muse}`, { body: "after", cmid: "c2" });
    const first = await r.read(MUSE);
    expect(first.messages).toHaveLength(1);
    expect(first.messages[0]).toMatchObject({ body_bytes: 8 * MIB, truncated: true,
                                              integrity: "ok" });
    expect(bytes(first)).toBeLessThanOrEqual(RESULT_CHARS);
    let pages = 0;
    const whole = await readOn(async (offset) => {
      pages += 1;
      const page = await r.stub.mailboxRead(r.bind(MUSE), {
        limit: 5, ...at(offset, { message_id: first.messages[0]!.message_id }) });
      expect(bytes(page)).toBeLessThanOrEqual(RESULT_CHARS);
      return page.messages[0]!;
    });
    expect(whole === body).toBe(true);
    expect(pages).toBeGreaterThan(8 * MIB / RESULT_CHARS);
    // Acknowledged, the next message is the page's first.
    await r.ack(MUSE, first.messages[0]!.seq);
    expect((await r.read(MUSE)).messages.map((m) => m.body)).toEqual(["after"]);
  });

  it("reads a row of each escape class whole by range, cut only at characters", { tags: ["large"] }, async () => {
    const { stub, as, name } = ownerObject();
    for (const [shape, ch] of Object.entries(SHAPES)) {
      const body = ch.repeat(Math.floor(MIB / 2 / utf8Length(ch)));
      const { row: { etag } } = await stub.collectionWrite(as(name), {
        collection: "board", row_id: shape, body, expected_etag: "", private: false }) as
        { row: WrittenRow };
      const got = await readOn((offset) => stub.collectionGet(as(name), {
        collection: "board", row_id: shape, ...at(offset, { etag }) }).then((answer) => {
        expect(bytes(answer)).toBeLessThanOrEqual(RESULT_CHARS);
        return answer.row!;
      }));
      expect(got === body).toBe(true);
    }
    // A pair is never split: an offset inside one is refused as such.
    const cut = await stub.collectionGet(as(name), { collection: "board", row_id: "pair" });
    expect((cut.row as Row & { body_next_offset: number }).body_next_offset % 4).toBe(0);
    expect((await failure(() => stub.collectionGet(as(name), {
      collection: "board", row_id: "pair", body_offset: 6, etag: cut.row!.etag }))).message)
      .toMatch(/^body_offset is inside a character/);
  });

  it("refuses a read on by range of a row written since, by its etag", async () => {
    const { stub, as, name } = ownerObject();
    const put = (body: string, etag: string) => stub.collectionWrite(as(name), {
      collection: "board", row_id: "r", body, expected_etag: etag, private: false }) as
      Promise<{ row: WrittenRow }>;
    const first = await put("a".repeat(200_000), "");
    const read = await stub.collectionGet(as(name), { collection: "board", row_id: "r" });
    const next = (read.row as Row & { body_next_offset: number }).body_next_offset;
    await put("b".repeat(200_000), first.row.etag);
    expect((await failure(() => stub.collectionGet(as(name), {
      collection: "board", row_id: "r", body_offset: next, etag: first.row.etag }))).message)
      .toMatch(/^row_changed: /);
  });

  it("fits a pull, a history and a digest to one answer, their cursors after the last kept",
     async () => {
    const { stub, as, name } = ownerObject();
    let etag = "";
    for (const n of [1, 2, 3]) {
      const done = await stub.collectionWrite(as(name), { collection: "notes", row_id: `r${n}`,
        body: `${n}`.repeat(30_000), expected_etag: "", private: false }) as { row: WrittenRow };
      etag = done.row.etag;
    }
    void etag;
    const pull = await stub.collectionPull(as(name), { collection: "notes", after: 0, limit: 10 });
    expect(pull.rows.map((row) => row.row_id)).toEqual(["r1"]);
    expect(bytes(pull)).toBeLessThanOrEqual(RESULT_CHARS);
    const rest = await stub.collectionPull(as(name), { collection: "notes", after: pull.cursor,
                                                       limit: 10 });
    expect(rest.rows.map((row) => row.row_id)).toEqual(["r2"]);
    // A first row past one answer is cut, and read on with collection_get.
    await stub.collectionWrite(as(name), { collection: "notes", row_id: "big",
      body: "q\"".repeat(40_000), expected_etag: "", private: false });
    const big = await stub.collectionPull(as(name), { collection: "notes", after: 3, limit: 10 });
    expect(big.rows[0]).toMatchObject({ row_id: "big", body_bytes: 80_000, truncated: true });
    // The board's digest: heads of every row, while they fit, and its cursor after them.
    for (const n of [1, 2, 3, 4]) {
      await stub.collectionWrite(as(name), { collection: "board", row_id: `t${n}`,
        body: SHAPES["control"]!.repeat(5000), expected_etag: "", private: false });
    }
    const digest = await stub.boardDigest(as(name), { limit: 10, after: 0, head_chars: 5000 });
    expect(bytes(digest)).toBeLessThanOrEqual(RESULT_CHARS);
    expect(digest.rows.length).toBe(1);
    const later = await stub.boardDigest(as(name), { limit: 10, after: digest.cursor,
                                                     head_chars: 5000 });
    expect(later.rows.map((row) => row.row_id)).toEqual(["t2"]);
  });

  it("holds file_read and fetch to one answer, read on by next_offset", async () => {
    const r = await rig();
    const text = `${"\"".repeat(100_000)}${SHAPES["pair"]!.repeat(10_000)}`;
    await r.stub.fileWrite(r.session(null), { path: "/workspace/big.md", expected_etag: null,
                                              content: enc.encode(text) });
    const parts: string[] = [];
    for (let offset: number | null = 0; offset !== null;) {
      const read: { text: string; next_offset: number | null } = await r.stub.fileText(
        r.bind(null), { path: "/workspace/big.md", offset, max_bytes: 4 * MIB });
      expect(bytes(read)).toBeLessThanOrEqual(RESULT_CHARS);
      parts.push(read.text);
      offset = read.next_offset;
    }
    expect(parts.join("") === text).toBe(true);
    const fetched = await r.stub.fileFetch(r.bind(null), { id: "/workspace/big.md" });
    expect(bytes(fetched)).toBeLessThanOrEqual(RESULT_CHARS);
    expect(fetched.metadata).toMatchObject({ truncated: true });
  });

  it("counts a cut body's fields at their widest, so its last part never passes (L1)", () => {
    // A body of four-digit size whose rest fills the room exactly.
    const size = 5000;
    const window: Window = { text: "y".repeat(size), offset: 0, end: size, size };
    const item = { id: 1, body: null as string | null };
    for (let room = 200; room < 5100; room += 37) {
      const fit = bodied(item, { ...window, text: window.text.slice(0, 4000), offset: 1000 },
                         room, true);
      expect(fit!.bytes).toBeLessThanOrEqual(room);
    }
  });

  it("reads a message on only by its message_id, and a row only by its etag (L2, L3)",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    await r.post(null, `dm:${muse}`, { body: "a".repeat(80_000) });
    await r.post(null, `dm:${muse}`, { body: "b".repeat(80_000), cmid: "c2" });
    const first = (await r.read(MUSE)).messages[0]!;
    const next = first.body_next_offset!;
    expect((await failure(() => r.stub.mailboxRead(r.bind(MUSE), {
      limit: 1, body_offset: next }))).message).toMatch(/name it by message_id/);
    await r.ack(MUSE, first.seq);
    // After the ack the page's first is the next message: the offset is not spliced into it.
    expect((await failure(() => r.stub.mailboxRead(r.bind(MUSE), {
      limit: 1, body_offset: next, message_id: first.message_id }))).message)
      .toMatch(/^message_changed: /);
    const { stub, as, name } = ownerObject();
    await stub.collectionWrite(as(name), { collection: "board", row_id: "r",
      body: "c".repeat(80_000), expected_etag: "", private: false });
    expect((await failure(() => stub.collectionGet(as(name), {
      collection: "board", row_id: "r", body_offset: 100 }))).message)
      .toMatch(/name its etag/);
  });

  it("pages agents_list: a thousand agents in answers under the cap, none lost (M3)",
     async () => {
    const { stub, as, name } = ownerObject();
    await stub.outsideAgents(as(name));
    await runInDurableObject(stub, (_instance, state) => {
      for (let n = 0; n < 1000; n++) {
        state.storage.sql.exec(
          "INSERT INTO outside_agents (agent_id, command_center_id, outside_client, " +
            "outside_family, source, source_ref, display_name, delivery_method, recipe_id, " +
            "revision, created_at) VALUES (?, ?, ?, ?, 'mcp', ?, ?, 'pull', NULL, 1, ?)",
          `agt_${n.toString().padStart(32, "0")}`, "cc_home", `client_${n}`, "family",
          `ref_${n}`, `Research helper number ${n}`, 1_800_000_000_000 + n);
      }
    });
    const seen: string[] = [];
    let removed = false;
    for (let after: string | undefined, pages = 0; ; pages++) {
      const page = await stub.outsideAgents(as(name), after === undefined ? {} : { after });
      expect(bytes(page)).toBeLessThanOrEqual(RESULT_CHARS);
      seen.push(...page.agents.map((agent) => agent.agent_id));
      if (page.next === undefined) {
        expect(pages).toBeGreaterThan(1);
        break;
      }
      after = page.next;
      if (!removed) {
        // An agent already answered goes between pages: none later is skipped for it.
        removed = true;
        await runInDurableObject(stub, (_instance, state) => {
          state.storage.sql.exec("DELETE FROM outside_agents WHERE agent_id = ?", seen[0]!);
        });
      }
    }
    expect(seen).toHaveLength(1000);
    expect(new Set(seen).size).toBe(1000);
  });
});
