/**
 * Slice STORE item 2, phase 1 (`history_research.md` §4): acked mail moved out of the owner's
 * object to archive segments, files-plane content in the export's own line grammar with a
 * trailer, one fence row a segment; the `archive` lane from half the hot ceiling, a post at
 * the ceiling `storage_busy` and never a delete; the two-unit move; a reply and the chain
 * read reaching archived mail by the seq its id carries; a deleted agent's mail dropped at
 * once and rewritten out; export version 3 shipping the segments as they are. Every object
 * here runs production's ceilings (`LEDGER_LIMITS`): a ballast agent's unread mail sets it a
 * few messages short of half the hot ceiling (`seeded`), and a segment seals at 8 MiB of
 * records, which one body at the cap reaches alone and two of 4 MiB together.
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it, onTestFinished } from "vitest";

import { BODY_BYTES_MAX } from "../src/bodies";
import { OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, ownerSlots } from "../src/holds";
import { refusalOf, STORAGE_BUSY_RETRY_S } from "../../contract/src/refusal";
import { PLACE_BYTES, SLOT_WAIT_MS } from "../../contract/src/limits";
import { LEDGER_LIMITS, type Ledger, mailParts, mailRecord } from "../src/ledger";
import { nativeId } from "../../contract/src/binding";
import type { Origin } from "../../contract/src/binding";
import type { OwnerDO } from "../src/owner-do";
import { parseTiers, TIER_STORAGE_MAX } from "../../contract/src/control";
import {
  type Content, idKey, idOrder, listed, makeChunk, MIN_RECORD_BYTES, MOVE_BYTES, MOVE_RECORDS,
  MoveTable, orderedId,
  parseRecord,
  parseTrailer, planSegment, RECORD_TRAILER_BYTES, recordBytes, recordParts, recordRows,
  type SegmentRecord, type Source,
} from "../src/segments";
import { exported } from "../src/export";
import type { ChunkStore } from "../../contract/src/files";
import { digest, generate, replay } from "./generation-support";
import { ballast, DOT, ledgerOn, MCP, MUSE, origin, rig } from "./ledger-support";
import { referenceSegment, storedContent, streamed } from "./segment-support";
import { drainDue, failure, gateFor, spaceOf, sqlIn } from "./support";

const EVE: Origin = Object.freeze({ client: "e".repeat(64), family: "f".repeat(64) });
const { maxAgentRows, maxMessages, maxUnacked } = LEDGER_LIMITS;
const MIB = 1024 * 1024;

type Rig = Awaited<ReturnType<typeof rig>>;
type Seeded = Rig & { readonly ballast: string };

/**
 * A fresh owner at production's ceilings, its ballast agent's
 * `maxUnacked - room` messages hot and unread (`ballast`): the test's own `room + 1`th hot
 * message takes the hot set past half, where the `archive` lane moves acked mail, and `room`
 * more may wait unread. Its storage is let go as the test ends (`letGo`).
 */
async function seeded(room = 4): Promise<Seeded> {
  const r = await rig();
  const agent = await r.inLedger((ledger: Ledger) =>
    ballast(ledger, room, maxMessages - maxUnacked + room));
  onTestFinished(() => r.letGo());
  return Object.assign(r, { ballast: agent });
}

/**
 * The ballast agent deleted, its hot mail with it, and the lane settled: an export then pages
 * the test's own mail alone, not the ballast's thousands of rows.
 */
async function unballast(r: Seeded): Promise<void> {
  await r.stub.deleteAgent(r.session(null), { agent_id: r.ballast });
  await settle(r);
}

/** The recipient a dm with an outside agent goes to from the owner: that agent. */
const MUSE_ID = (rows: Record<string, Record<string, unknown>>) =>
  (rows["ledger_messages"]!["room_id"] as string).slice(3);

/**
 * The owner's mail to `room`, read and acked by `reader` at once: acked, so it may move. Each
 * body is `prefix i`, padded to `bytes` when given (a body at the cap seals a segment alone).
 */
async function sentAndAcked(r: Rig, reader: typeof MUSE, room: string, n: number,
                            prefix = "m", bytes = 0): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < n; i++) {
    const posted = await r.post(null, room, { body: `${prefix} ${i}`.padEnd(bytes, "x"),
                                              cmid: `${prefix}${i}` });
    ids.push(posted.message_id);
    const page = await r.read(reader);
    if (page.messages.length > 0) {
      await r.ack(reader, page.messages.at(-1)!.seq);
    }
  }
  return ids;
}

/** A body at the cap, acked by `reader`: past half the hot ceiling, it seals what is before it. */
async function sealed(r: Rig, reader: typeof MUSE, room: string): Promise<string> {
  return (await sentAndAcked(r, reader, room, 1, "seal", BODY_BYTES_MAX))[0]!;
}

/** Whether the lane has work, as its schedule reads it (`Archive.next`), or a move in flight. */
async function busy(r: Rig): Promise<boolean> {
  const due = await r.inLedger((ledger: Ledger) => ledger.archiveDue()
    || ledger.purgedAgents().length > 0 || ledger.archiveCompactPending());
  return due || (await sqlIn(r.stub, "SELECT 1 FROM archive_moves")).length > 0;
}

/** Drive the object's lanes until the archive has nothing due. */
async function settle(r: Rig): Promise<void> {
  for (let i = 0; i < 60; i++) {
    await drainDue(r.stub);
    if (!await busy(r)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("the archive lane did not settle");
}

/** An archive move as it landed: its stream, the segments it replaced, the records it wrote. */
interface Move {
  readonly stream: string;
  readonly replaced: number;
  readonly records: number;
}

/**
 * Each archive move of the owner's from now on, in order, once the watch is stopped (the
 * function it answers). Every move sends its segments' chunks through FILES before the unit
 * that names them, so the FILES gate holds each upload (`putChunks`) of the owner's
 * namespace while the move's row (`archive_moves`, its stream and intent) and the fences are
 * read, then lets it go: the fences' change from one move's first upload to the next one's
 * (or to the end) is the first move's commit.
 */
async function watchMoves(r: Rig): Promise<() => Promise<Move[]>> {
  type Fence = { segment: number; records: number };
  const ns = await spaceOf(r.stub);
  const fenced = () => sqlIn(r.stub, "SELECT segment, records FROM archive_segments") as
    Promise<Fence[]>;
  await env.FILES_GATE.arm({ method: "putChunks", name: ns, hold: "before", times: 1_000_000 });
  const seen: { intent: string; stream: string; fences: Fence[] }[] = [];
  let stopped = false;
  const watching = (async () => {
    while (!stopped) {
      for (const call of (await env.FILES_GATE.held()).filter((held) => held.name === ns)) {
        const [move] = await sqlIn(r.stub, "SELECT stream, intent_id FROM archive_moves " +
                                           "ORDER BY started_at DESC");
        if (move !== undefined && move["intent_id"] !== seen.at(-1)?.intent) {
          seen.push({ intent: move["intent_id"] as string, stream: move["stream"] as string,
                      fences: await fenced() });
        }
        await env.FILES_GATE.release(call.id);
      }
      await new Promise((resolve) => setTimeout(resolve, 2));
    }
  })();
  return async () => {
    stopped = true;
    await watching;
    await env.FILES_GATE.disarm();
    const end = await fenced();
    return seen.map((move, i) => {
      const after = seen[i + 1]?.fences ?? end;
      const was = new Set(move.fences.map((fence) => fence.segment));
      const now = new Set(after.map((fence) => fence.segment));
      return { stream: move.stream,
               replaced: move.fences.filter((fence) => !now.has(fence.segment)).length,
               records: after.filter((fence) => !was.has(fence.segment))
                 .reduce((sum, fence) => sum + fence.records, 0) };
    });
  };
}

/** A message's seq, by its id. */
function seqOf(r: Rig, id: string): Promise<number> {
  return r.inLedger((ledger: Ledger) => ledger.seqOf(id)!);
}

/** Any two segments whose key ranges overlap: none, ever (review H1). */
async function overlaps(r: Rig): Promise<unknown[]> {
  return sqlIn(r.stub, "SELECT a.segment, b.segment FROM archive_segments a JOIN " +
               "archive_segments b ON a.segment < b.segment AND a.last_key >= b.first_key " +
               "AND a.first_key <= b.last_key");
}

/**
 * The owner's export, paged to its end, and its archived records read by v3's rule: each
 * `archive_segments` row's chunks from the export, each record's rows with its raw body
 * decoded back into its column. Its lines, and the archived records' rows by table.
 */
async function exportArchived(r: Rig): Promise<{
    lines: Record<string, unknown>[];
    archived: Record<string, Record<string, unknown>>[] }> {
  const lines: Record<string, unknown>[] = [];
  let exportId: string | undefined;
  for (let next: string | null = null, waits = 0; ;) {
    const page = await r.stub.ownerExport(r.session(null), next === null ? { cursor: null }
      : { cursor: next, export_id: exportId! });
    exportId = page.export_id;
    if (page.pending) {
      if (++waits > 400) {
        throw new Error("the export's generation did not go on");
      }
      await drainDue(r.stub);
      next = page.next;
      continue;
    }
    lines.push(...page.lines.map((line: string) => JSON.parse(line)));
    if (page.next === null) {
      break;
    }
    next = page.next;
  }
  const chunkNames = new Set(lines.filter((line) => line["t"] === "chunk")
    .map((line) => line["sha256"]));
  const archived: Record<string, Record<string, unknown>>[] = [];
  for (const fence of lines.filter((line) => line["t"] === "row"
                                   && line["table"] === "archive_segments")) {
    const row = fence["row"] as Record<string, unknown>;
    const shas = (await sqlIn(r.stub, "SELECT sha256 FROM file_content_chunks WHERE " +
                                      "content_id = ? ORDER BY idx", row["content_id"] as string))
      .map((chunk) => chunk["sha256"] as string);
    const parts: Uint8Array[] = [];
    for (const sha of shas) {
      expect(chunkNames.has(sha)).toBe(true);
      const { chunks } = await r.stub.exportChunks(r.session(null), {
        export_id: exportId!, sha256s: [sha] });
      parts.push(Uint8Array.from(Buffer.from(chunks[0]!.data, "base64")));
    }
    const whole = Buffer.concat(parts);
    const at = row["trailer_offset"] as number;
    const trailer = parseTrailer(new TextDecoder().decode(whole.subarray(
      at, at + (row["trailer_bytes"] as number))));
    for (const [, offset, length] of trailer.keys) {
      archived.push(Object.fromEntries(recordRows(whole.subarray(offset, offset + length))));
    }
  }
  return { lines, archived };
}

/**
 * A batch's sources as a move streams them, its records the table's in order (a tail's
 * batch), its bodies inline (no placed body read).
 */
const hotSources = (ledger: Ledger, table: MoveTable) => async (from: number, to: number) => {
  const keys = Array.from({ length: to - from }, (_, k) => table.key(from + k));
  const parts = ledger.archiveParts(keys);
  return keys.map((key) => {
    const own = parts.get(key)!;
    return [own.head, own.inline!, own.tail];
  });
};
/** A batch planned into a table of its own, no body placed. */
const batchOf = (ledger: Ledger, seqs: readonly number[]) => {
  const table = new MoveTable();
  const batch = ledger.archiveBatch(seqs, table, () => {
    throw new Error("no placed body in this batch");
  });
  return batch === null ? null : { ...batch, table };
};
const noRead = async (): Promise<Uint8Array> => {
  throw new Error("no stored content in this batch");
};

/** Every archived message's body by its id, as the export's reading rule reads it. */
async function archivedBodies(r: Rig): Promise<Map<string, string>> {
  const { archived } = await exportArchived(r);
  return new Map(archived.map((rows) => [rows["ledger_messages"]!["message_id"] as string,
                                         rows["message_bodies"]!["body"] as string]));
}

/** The most JSON one answer holds (`encoded.ts`). */
const RESULT_CHARS = 50_000;

/**
 * A chain read, paged by the last message's seq until `id` shows or it ends: its body read
 * whole (a page cuts its first body to fit one answer; the rest reads on by `body_offset`,
 * naming the message), or null. Every page held to one answer.
 */
async function chained(r: Rig, id: string): Promise<string | null> {
  const read = async (request: Record<string, unknown>) => {
    const page = await r.stub.mailChain(r.bind(null), { message_id: id, limit: 10,
                                                        ...request } as never);
    expect(JSON.stringify(page).length).toBeLessThanOrEqual(RESULT_CHARS);
    return page;
  };
  let after = 0;
  for (let n = 0; n < 40; n++) {
    const page = await read({ after });
    const found = page.messages.find((m) => m.message_id === id) as
      ({ seq: number; message_id: string; body: string | null;
         body_next_offset?: number | null }) | undefined;
    if (found !== undefined) {
      if (found.body === null) {
        return null;
      }
      let text = found.body;
      for (let next = found.body_next_offset ?? null; next !== null;) {
        const on = (await read({ after: found.seq - 1, limit: 1, body_offset: next,
                                 body_message_id: found.message_id })).messages[0] as
          { body: string; body_next_offset?: number | null };
        text += on.body;
        next = on.body_next_offset ?? null;
      }
      return text;
    }
    const last = page.messages.at(-1);
    if (!page.more || last === undefined) {
      return null;
    }
    after = last.seq;
  }
  return null;
}

describe("segments", () => {
  it("encode records as export lines with a trailer, each read back by its range", () => {
    const records = [1, 2, 5].map((key) => ({ key, chain: key === 5 ? "root" : null,
      rows: [["t", { k: key, é: "\u0000" }]] as [string, Record<string, unknown>][] }));
    const { bytes: whole, trailer_offset, trailer_bytes } = referenceSegment("mail", records);
    expect(Number(new TextDecoder().decode(whole.subarray(-16)))).toBe(trailer_offset);
    const trailer = parseTrailer(new TextDecoder().decode(whole.subarray(
      trailer_offset, trailer_offset + trailer_bytes)));
    expect(trailer).toMatchObject({ stream: "mail", first: 1, last: 5, records: 3 });
    expect(trailer.chains).toEqual({ root: [trailer.keys[2]] });
    for (const [key, offset, length] of trailer.keys) {
      expect(parseRecord(whole.subarray(offset, offset + length)))
        .toEqual([["t", { k: key, é: "\u0000" }]]);
    }
    expect(() => planSegment("mail", listed([{ key: 2, chain: null, length: 1 },
                                             { key: 1, chain: null, length: 1 }])))
      .toThrow(/key order/);
  });

  it("are streamed a chunk at a time to exactly the reference's bytes (review N5)", async () => {
    // A mix the moves make: small held records; a body at the cap of control characters
    // read from its stored content (its chunk boundaries inside the record); records kept
    // from an old segment as ranges of its content, one crossing a chunk; enough small
    // records that the trailer crosses a chunk too.
    const body = new TextEncoder().encode("\u0001".repeat(8 * 1024 * 1024));
    const placed = storedContent("body", body);
    const mail = (key: number, raw: Uint8Array): SegmentRecord => mailRecord(
      { seq: key, message_id: `msg_${key}`, room_id: "dm:x", chain_root: `msg_${key % 7}`,
        created_at: key, body_bytes: raw.byteLength },
      { message_id: `msg_${key}`, room_id: "dm:x", body: null, body_bytes: raw.byteLength,
        content_id: null, meta_bytes: 0 },
      [{ message_id: `msg_${key}`, agent_id: "x", seq: key, state: "read", changed_at: key,
         unacked: 0 }], [raw]);
    const old = referenceSegment("mail", [mail(10, new TextEncoder().encode("y".repeat(6e6))),
                                          mail(11, new TextEncoder().encode("z".repeat(300)))]);
    const oldContent = storedContent("old", old.bytes);
    const records: SegmentRecord[] = [];
    const planned: { key: number; chain: string | null; length: number; parts: Source[] }[] = [];
    const push = (record: SegmentRecord, parts: Source[]) => {
      records.push(record);
      planned.push({ key: record.key, chain: record.chain, length: recordBytes(record), parts });
    };
    for (let key = 1; key < 3; key++) {
      const record = mail(key, new TextEncoder().encode(`small ${key}`));
      push(record, recordParts(record));
    }
    // The placed body: its lines held, its body the stored content's whole range.
    const big = mail(5, body);
    const [line1, line2, , newline, ...tail] = recordParts(big);
    push(big, [line1!, line2!, { content: placed.content, offset: 0, length: body.byteLength },
               newline!, ...tail]);
    // The old segment's two records, kept as its content's ranges.
    for (const [key, offset, length] of old.trailer.keys) {
      const record: SegmentRecord = { key, chain: `msg_${key % 7}`, rows: [],
                                      held: [old.bytes.subarray(offset, offset + length)] };
      push(record, [{ content: oldContent.content, offset, length }]);
    }
    for (let key = 20; key < 30_020; key++) {
      const record = mail(key, new TextEncoder().encode("s"));
      push(record, recordParts(record));
    }
    const plan = planSegment("mail", listed(planned));
    const reads: string[] = [];
    const read = async (content: Content, index: number) => {
      reads.push(`${content.id}:${index}`);
      return (content.id === "body" ? placed : oldContent).chunks.get(content.chunks[index]!)!;
    };
    const bytes = await streamed(plan, async (from, to) =>
      planned.slice(from, to).map((record) => record.parts), read);
    const reference = referenceSegment("mail", records).bytes;
    expect(bytes.byteLength).toBe(reference.byteLength);
    expect(Buffer.compare(Buffer.from(bytes), Buffer.from(reference))).toBe(0);
    expect(plan.size).toBe(reference.byteLength);
    expect(plan.chunks).toBeGreaterThan(4);
    // Only the stored chunks each output chunk covers are read, and none twice in a row.
    expect(reads.length).toBeLessThan(2 * (placed.content.chunks.length
                                           + oldContent.content.chunks.length) + 2);
    // A record that is not the length its plan placed fails loudly.
    await expect(makeChunk(plan, 0, async (from, to) =>
      planned.slice(from, to).map(() => [new Uint8Array(1)]), read))
      .rejects.toThrow(/not the length its plan placed/);
  });

  it("are planned in a table that refuses a move past MOVE_RECORDS, loudly (review N7)", () => {
    const table = new MoveTable();
    for (let n = 0; n < MOVE_RECORDS; n++) {
      table.push(n + 1, null, "dm:x", n, MIN_RECORD_BYTES, MoveTable.INLINE, -1, 0);
    }
    expect(table.count).toBe(MOVE_RECORDS);
    expect(() => table.push(MOVE_RECORDS + 1, null, "dm:x", 0, MIN_RECORD_BYTES,
                            MoveTable.INLINE, -1, 0)).toThrow(/past MOVE_RECORDS/);
  });

  it("hold a body raw, never escaped: a record is its body's bytes and its lines' (N3)", () => {
    // The reviewer's body: 8 MiB of control characters, which JSON escapes six times over.
    const body = "\u0001".repeat(8 * 1024 * 1024);
    const raw = new TextEncoder().encode(body);
    const id = `msg_${"0".repeat(32)}`;
    const record = mailRecord(
      { seq: 7, message_id: id, room_id: "dm:x", chain_root: id, created_at: 1,
        body_bytes: raw.byteLength },
      { message_id: id, room_id: "dm:x", body, body_bytes: raw.byteLength, content_id: null,
        meta_bytes: 0 },
      [{ message_id: id, agent_id: "x", seq: 7, state: "read", changed_at: 1, unacked: 0 }],
      [raw.subarray(0, 3 << 20), raw.subarray(3 << 20)]);
    expect(recordBytes(record)).toBeLessThan(raw.byteLength + 2048);
    const encoded = referenceSegment("mail", [record]);
    expect(encoded.bytes.byteLength).toBeLessThan(raw.byteLength + 4096);
    const [, offset, length] = encoded.trailer.keys[0]!;
    const rows = recordRows(encoded.bytes.subarray(offset, offset + length));
    expect(rows.find(([table]) => table === "message_bodies")![1]["body"] === body).toBe(true);
  });

  it("hold no record smaller than MIN_RECORD_BYTES: the least mail's three rows", () => {
    const least = mailRecord(
      { seq: 1, message_id: `msg_${"0".repeat(32)}`, command_center_id: "", room_id: "dm:x",
        sender_kind: "owner", sender_id: "owner", client_message_id: "c",
        body_sha256: "0".repeat(64), parent_id: null, chain_root: `msg_${"0".repeat(32)}`,
        chain_depth: 0, created_at: 1, body_bytes: 1, unacked: 0, authority: null,
        attended: 0, line_bytes: 1 },
      { message_id: `msg_${"0".repeat(32)}`, room_id: "dm:x", body: "x", created_at: 1,
        body_bytes: 1, content_id: null, filer: "owner", meta_bytes: 0 },
      [{ message_id: `msg_${"0".repeat(32)}`, agent_id: "owner", seq: 1, state: "read",
         changed_at: 1, unacked: 0 }], [new TextEncoder().encode("x")]);
    expect(recordBytes(least)).toBeGreaterThanOrEqual(MIN_RECORD_BYTES);
  });

  it("give ids that carry their seq, opaque without the object's key", () => {
    const key = idKey();
    const ids = new Set<string>();
    for (const seq of [1, 2, 3, 2 ** 40, Number.MAX_SAFE_INTEGER]) {
      const id = orderedId(key, seq);
      expect(id).toMatch(/^[0-9a-f]{32}$/);
      expect(idOrder(key, id)).toBe(seq);
      expect(idOrder(idKey(), id)).not.toBe(seq);
      ids.add(id);
      ids.add(orderedId(key, seq));  // the same seq, a different id: eight random bytes
    }
    expect(ids.size).toBe(10);
  });
});

describe("a message's counted bytes", () => {
  it("are its record's, exactly, as its recipient row changes: read, replied, acked, failed",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const exact = async (id: string) => {
      const [message] = await sqlIn(r.stub, "SELECT * FROM ledger_messages WHERE message_id = ?",
                                    id);
      const [body] = await sqlIn(r.stub, "SELECT * FROM message_bodies WHERE message_id = ?", id);
      const recipients = await sqlIn(r.stub, "SELECT * FROM ledger_recipients WHERE " +
                                             "message_id = ?", id);
      const held = recordBytes(mailRecord(message!, body!, recipients,
                                          [new TextEncoder().encode(body!["body"] as string)]))
        + RECORD_TRAILER_BYTES;
      expect([message!["line_bytes"], body!["meta_bytes"]])
        .toEqual([held, held - (body!["body_bytes"] as number)]);
      // The one number and the acked bytes move with it.
      const [counted] = await sqlIn(r.stub, "SELECT (SELECT SUM(line_bytes) FROM " +
        "ledger_messages) AS lines, (SELECT coalesce(SUM(line_bytes), 0) FROM ledger_messages " +
        "WHERE unacked = 0) AS acked, (SELECT acked_bytes FROM ledger_usage) AS usage");
      expect(counted!["usage"]).toBe(counted!["acked"]);
      expect((await r.stub.storageUsage(r.bind(null))).breakdown.messages)
        .toBe(counted!["lines"]);
      return held;
    };
    const asked = (await r.post(null, `dm:${muse}`, { body: "hello" })).message_id;
    const posted = await exact(asked);
    const [mail] = (await r.read(MUSE)).messages;
    const read = await exact(asked);
    expect(read).toBeGreaterThan(posted);  // its read time, in its recipient row
    await r.ack(MUSE, mail!.seq);
    await r.post(MUSE, `dm:${muse}`, { body: "hi", cmid: "r", parent: asked });
    await exact(asked);  // replied, while acked
    const failed = await r.inLedger((ledger: Ledger) => {
      ledger.appear("main");
      const id = ledger.post(null, `dm:${nativeId("main")}`, "m", "x", null).message_id;
      ledger.undeliverable(id, nativeId("main"), "credential_refresh_failed");
      return id;
    });
    await exact(failed);
    // A placed body's record, as the move streams it (`mailParts`), holds no content id.
    const placed = (await r.post(null, `dm:${muse}`, { body: "p".repeat(PLACE_BYTES + 1),
                                                       cmid: "p" })).message_id;
    const of = (table: string) => sqlIn(r.stub, `SELECT * FROM ${table} WHERE message_id = ?`,
                                        placed);
    const [message] = await of("ledger_messages");
    const [body] = await of("message_bodies");
    const parts = mailParts(message!, body!, await of("ledger_recipients"));
    expect(body!["content_id"]).not.toBeNull();
    expect(message!["line_bytes"]).toBe(parts.head.byteLength + (body!["body_bytes"] as number)
                                        + parts.tail.byteLength + RECORD_TRAILER_BYTES);
  });
});

describe("the archive lane", () => {
  it("moves acked mail out from half the hot ceiling, its bytes counted the same, and the " +
     "chain read finds it", async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    // Acked mail to half the hot ceiling, short of a seal: nothing moves.
    const ids = await sentAndAcked(r, MUSE, room, 4);
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT * FROM archive_segments")).toEqual([]);
    // A body at the cap past half: the five are a seal's worth, one segment.
    const seal = await sealed(r, MUSE, room);
    const before = (await r.stub.storageUsage(r.bind(null))).breakdown.messages;
    await settle(r);
    const [usage] = await sqlIn(r.stub, "SELECT messages FROM ledger_usage");
    expect(usage!["messages"]).toBeLessThanOrEqual(maxMessages / 2);
    expect(await sqlIn(r.stub, "SELECT seq FROM ledger_messages WHERE room_id = ?", room))
      .toEqual([]);
    const fences = await sqlIn(r.stub, "SELECT * FROM archive_segments ORDER BY segment");
    expect(fences.map((fence) => fence["records"])).toEqual([5]);
    // The one number counts archived mail at its real bytes, as it counted it hot (D2).
    const after = (await r.stub.storageUsage(r.bind(null))).breakdown.messages;
    expect(Math.abs(after - before)).toBeLessThan(before * 0.2);
    // Every message is still the owner's: the archived ones read back from their segment.
    for (const [n, id] of ids.entries()) {
      const chain = await r.stub.mailChain(r.bind(null), { message_id: id, limit: 10 });
      expect(chain.messages.map((m) => [m.message_id, m.body, m.integrity]))
        .toEqual([[id, `m ${n}`, "ok"]]);
    }
    expect((await chained(r, seal)) === "seal 0".padEnd(BODY_BYTES_MAX, "x")).toBe(true);
    // No move is left in flight, and no upload behind one.
    expect(await sqlIn(r.stub, "SELECT * FROM archive_moves")).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT * FROM file_intents")).toEqual([]);
  });

  it("lets a reply name an archived parent, and the chain reads it with the reply",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const [first] = await sentAndAcked(r, MUSE, room, 4);
    await sealed(r, MUSE, room);
    await settle(r);
    const archived = await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                                 first!);
    expect(archived).toEqual([]);  // the oldest moved first
    const reply = await r.post(MUSE, room, { cmid: "r1", body: "late reply", parent: first! });
    const chain = await r.stub.mailChain(r.bind(null), { message_id: reply.message_id,
                                                         limit: 10 });
    expect(chain.root).toBe(first);
    expect(chain.messages.map((m) => [m.message_id, m.body])).toEqual([
      [first, "m 0"], [reply.message_id, "late reply"]]);
    // The reply holds its exact time to reply, its parent archived: no read needs the parent.
    const [parentAt, replyAt] = chain.messages.map((m) => m.created_at);
    const [mine] = (await r.read(null)).messages;
    expect([mine!.message_id, mine!.reply_s])
      .toEqual([reply.message_id, Math.round((replyAt! - parentAt!) * 1000) / 1000]);
    // A forged parent id is no parent.
    expect((await failure(() => r.post(MUSE, room, { cmid: "r2", body: "x",
                                                     parent: `msg_${"a".repeat(32)}` })))
      .message).toBe("parent is not a message to this sender in this room");
  });

  it("pages a chain of large bodies to one answer each, archived and hot, read on by offset",
     { tags: ["large"] }, async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const big = (c: string) => c.repeat(5 * 1024 * 1024);
    const root = await r.post(null, room, { cmid: "b0", body: big("a") });
    await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    const second = await r.post(MUSE, room, { cmid: "b1", body: big("b"),
                                              parent: root.message_id });
    await r.ack(null, (await r.read(null)).messages.at(-1)!.seq);
    const third = await r.post(null, room, { cmid: "b2", body: big("c"),
                                             parent: second.message_id });
    await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    // Past half: the first two seal a segment; the third stays hot.
    await sentAndAcked(r, MUSE, room, 4);
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                       root.message_id)).toEqual([]);  // archived
    // Each page one answer: its first 5 MiB body cut to fit, the rest read on by its id
    // and offset, archived or hot alike; the next page goes on after it.
    const pages: string[][] = [];
    for (let after = 0, more = true; more && pages.length < 10;) {
      const page = await r.stub.mailChain(r.bind(null), { message_id: third.message_id,
                                                          limit: 10, after });
      expect(JSON.stringify(page).length).toBeLessThanOrEqual(RESULT_CHARS);
      pages.push(page.messages.map((m) => m.message_id));
      expect(page.messages).toHaveLength(1);
      expect(page.messages[0]).toMatchObject({ truncated: true, body_bytes: 5 * 1024 * 1024 });
      more = page.more;
      after = page.messages[0]!.seq;
    }
    expect(pages.flat()).toEqual([root.message_id, second.message_id, third.message_id]);
    for (const [message, letter] of [[root, "a"], [second, "b"], [third, "c"]] as const) {
      expect((await chained(r, message.message_id)) === big(letter)).toBe(true);
    }
    // A read on names its message: another's offset is refused, never spliced.
    expect((await failure(() => r.stub.mailChain(r.bind(null), {
      message_id: third.message_id, limit: 1, after: 0, body_offset: 100,
      body_message_id: second.message_id } as never))).message).toMatch(/^message_changed/);
  });

  it("finds a chain's mail that the lane moves between its fences and its hot unit (N9)",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    // A chain of eight, each a reply to the last, the owner and the muse in turn: its first
    // four read, acked and moved with a body at the cap, so the read has an archived part;
    // its last four unread, so none can move yet (the unread ceiling's last room).
    const chain: string[] = [];
    const reply = async (i: number) => {
      chain.push((await r.post(i % 2 === 0 ? null : MUSE, room, {
        cmid: `c${i}`, body: `chain ${i}`, parent: chain.at(-1) ?? null })).message_id);
    };
    const readAll = async () => {
      await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
      await r.ack(null, (await r.read(null)).messages.at(-1)!.seq);
    };
    for (let i = 0; i < 4; i++) {
      await reply(i);
    }
    await readAll();
    await sealed(r, MUSE, room);
    await settle(r);
    for (let i = 4; i < 8; i++) {
      await reply(i);
    }
    const hot = async () => new Set((await sqlIn(r.stub, "SELECT message_id FROM " +
      "ledger_messages")).map((row) => row["message_id"] as string));
    const before = await hot();
    expect(chain.map((id) => before.has(id))).toEqual([false, false, false, false,
                                                     true, true, true, true]);
    // The read, named by a hot message of the chain (so its root is not read from a segment
    // first), held at the FILES gate once its first archived read has answered: its fences
    // taken, its hot unit not yet.
    const files = gateFor(env.FILES_GATE, await spaceOf(r.stub));
    await files.arm("getChunks", { hold: "after" });
    const reading = r.stub.mailChain(r.bind(null), { message_id: chain[4]!, limit: 50 });
    try {
      await files.held("getChunks");
      // There: the chain's last four read and acked, a body at the cap past half the hot
      // ceiling, and the lane moves them out of the hot rows into a segment the held read
      // never saw.
      await readAll();
      await sealed(r, MUSE, room);
      await settle(r);
      const still = await hot();
      expect(chain.slice(4).filter((id) => !still.has(id)).length).toBeGreaterThan(0);
    } finally {
      await files.release("getChunks");
    }
    const page = await reading;
    expect([page.messages.map((m) => [m.message_id, m.body]), page.more]).toEqual([
      chain.map((id, i) => [id, `chain ${i}`]), false]);
    // Each reply's time to reply, hot or from its segment, is its row's, exact.
    const again = (await r.stub.mailChain(r.bind(null), { message_id: chain[0]!, limit: 50 }))
      .messages;
    expect(again.map((m) => m.reply_s)).toEqual(again.map((m, i) => i === 0 ? null
      : Math.round((m.created_at - again[i - 1]!.created_at) * 1000) / 1000));
  });

  it("reads a still chain while stragglers merge into its segment, replacing it, never " +
     "failing inside (N10)", { tags: ["large"] }, async () => {
    const r = await seeded(30);
    const muse = await r.register(MUSE);
    const eve = await r.register(EVE, "Eve");
    // Eve's mail first, unread: each acked later is a straggler, below the muse's segment.
    const STRAGGLERS = 26;
    for (let i = 0; i < STRAGGLERS; i++) {
      await r.post(null, `dm:${eve}`, { cmid: `e${i}`, body: `eve ${i}` });
    }
    // The muse's chain: twelve, the owner and the muse in turn, each acked, all moved with a
    // body at the cap; then small acked mail keeps the hot set past half, short of a seal.
    const chain: string[] = [];
    for (let i = 0; i < 12; i++) {
      const from = i % 2 === 0 ? null : MUSE;
      chain.push((await r.post(from, `dm:${muse}`, {
        cmid: `m${i}`, body: `muse ${i}`, parent: chain.at(-1) ?? null })).message_id);
      const reader = from === null ? MUSE : null;
      await r.ack(reader, (await r.read(reader)).messages.at(-1)!.seq);
    }
    await sealed(r, MUSE, `dm:${muse}`);
    await settle(r);
    await sentAndAcked(r, MUSE, `dm:${muse}`, STRAGGLERS + 5, "past");
    const last = await seqOf(r, chain.at(-1)!);
    // The chain's last reply, from the owner, unread and hot: the read is named by it, so its
    // root is not read from a segment first, and its first read from the files plane is the
    // first of its archived part, just past the unit that took its fences.
    chain.push((await r.post(null, `dm:${muse}`, { cmid: "m12", body: "muse 12",
                                                   parent: chain.at(-1)! })).message_id);
    // Eve acks her oldest, and the lane runs at once: the straggler merges into the muse's
    // segment, which the merge replaces.
    let acked = 0;
    const churn = async () => {
      await r.ack(EVE, (await r.read(EVE, 1)).messages[0]!.seq);
      acked++;
      await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = " +
                          "'owner-schedule/archive'");
      await drainDue(r.stub);
    };
    const read = async () => {
      const page = await r.stub.mailChain(r.bind(null), { message_id: chain.at(-1)!,
                                                          limit: 50 });
      expect(page.messages.map((m) => [m.message_id, m.body]))
        .toEqual(chain.map((id, i) => [id, `muse ${i}`]));
    };
    const museSegments = async () => (await sqlIn(r.stub, "SELECT segment FROM " +
      "archive_segments WHERE first_key <= ? ORDER BY segment", last))
      .map((row) => row["segment"] as number).join();
    // One read while the churn runs beside it.
    let stop = false;
    const beside = (async () => {
      while (!stop && acked < 6) {
        await churn();
      }
    })();
    try {
      await read();
    } finally {
      stop = true;
      await beside;
    }
    // Then the rest, each read held just past the unit that took its fences while one
    // straggler merges.
    let replaced = 0;
    const files = gateFor(env.FILES_GATE, await spaceOf(r.stub));
    while (acked < STRAGGLERS) {
      const before = await museSegments();
      await files.arm("getChunks", { hold: "before" });
      const reading = read();
      try {
        await files.held("getChunks");
        await churn();
      } finally {
        await files.release("getChunks");
      }
      await reading;
      replaced += before === await museSegments() ? 0 : 1;
    }
    // The muse's segment was replaced under held reads, and each read held.
    expect(replaced).toBeGreaterThan(0);
  });

  it("holds its move's units on the isolate's pool; refused busy at once, the lane holds " +
     "with no failure counted, tells the owner once at the hot ceiling, and moves once there " +
     "is room (round 11, Lows A and B)", { tags: ["large"] }, async () => {
    const r = await rig();
    onTestFinished(() => r.letGo());
    const muse = await r.register(MUSE);
    // Three short of the hot ceiling, a segment's worth of it acked, three unread to spare.
    await r.inLedger((ledger: Ledger) => ballast(ledger, 3, 3));
    const lane = async () => {
      await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = " +
                          "'owner-schedule/archive'");
      await drainDue(r.stub);
    };
    const usage = async () => (await sqlIn(r.stub, "SELECT archive_fails AS fails, " +
                                           "archive_error AS error FROM ledger_usage"))[0];
    const told = async () => (await sqlIn(r.stub, "SELECT detail FROM owner_notices WHERE " +
                                          "type = 'storage_busy_long'")).length;
    // Every unit of the larger class held elsewhere: a move's table cannot take its own.
    const pool = ownerSlots();
    const held = await pool.acquire(OWNER_POOL_UNITS - OWNER_RESERVE_UNITS);
    let released = false;
    try {
      await sentAndAcked(r, MUSE, `dm:${muse}`, 1);  // rings the lane
      // Refused at once, never waiting on the pool inside the alarm: no failure counted, no
      // notice while the hot set is below its ceiling, nothing moved. The pool's units stay
      // held through the pass, so a pass that waited would be refused only by the pool's own
      // timer, `SLOT_WAIT_MS` on: finishing inside it is the never-waiting, by the code's
      // constant, not a hand-set figure.
      const before = Date.now();
      await lane();
      expect(Date.now() - before).toBeLessThan(SLOT_WAIT_MS);
      expect(await usage()).toEqual({ fails: 0, error: null });
      expect(await told()).toBe(0);
      expect(await sqlIn(r.stub, "SELECT * FROM archive_segments")).toEqual([]);
      // The hot set at its ceiling: the next post is refused `storage_busy`, and a busy pass
      // tells the owner, once for the blocked spell.
      await r.post(null, `dm:${muse}`, { cmid: "u0", body: "unread 0" });
      await r.post(null, `dm:${muse}`, { cmid: "u1", body: "unread 1" });
      expect((await failure(() => r.post(null, `dm:${muse}`, { cmid: "u2", body: "x" })))
        .message).toMatch(/^storage_busy:/);
      await lane();
      await lane();
      expect(await told()).toBe(1);
      expect(await usage()).toEqual({ fails: 0, error: null });
      held.release();
      released = true;
      // Room again: the lane moves, and no more is told.
      await lane();
      await settle(r);
      expect((await sqlIn(r.stub, "SELECT * FROM archive_segments")).length).toBeGreaterThan(0);
      expect(await told()).toBe(1);
      expect(pool.state()).toMatchObject({ held: 0 });
    } finally {
      if (!released) {
        held.release();
      }
    }
  });

  it("keeps a segment a purge's rewrite makes again byte for byte, its content still held",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // Two bodies at the cap: each sealed alone. Dot's deleted, its segment's rewrite takes
    // in the muse's whole, alone in its piece: the same bytes as it stands.
    const big = (c: string) => c.repeat(BODY_BYTES_MAX);
    const kept = await r.post(null, `dm:${muse}`, { cmid: "b0", body: big("a") });
    await r.post(null, `dm:${dot}`, { cmid: "b1", body: big("b") });
    await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    await r.ack(DOT, (await r.read(DOT)).messages.at(-1)!.seq);
    // Small acked mail past half the hot ceiling, then one post that rings the lane.
    await r.inLedger((ledger: Ledger) => {
      let last = 0;
      for (let n = 0; n < 3; n++) {
        last = ledger.seqOf(ledger.post(null, `dm:${muse}`, `s${n}`, "x", null).message_id)!;
      }
      ledger.read(MUSE, 1);
      ledger.ack(MUSE, last);
    });
    await r.post(null, `dm:${muse}`, { cmid: "ring", body: "ring" });
    await settle(r);
    const before = await sqlIn(r.stub, "SELECT segment, content_id FROM archive_segments " +
                               "WHERE records = 1 ORDER BY segment");
    expect(before).toHaveLength(2);
    await r.stub.deleteAgent(r.session(null), { agent_id: dot });
    await settle(r);
    const [usage] = await sqlIn(r.stub, "SELECT archive_fails AS fails, archive_error AS " +
                                "error FROM ledger_usage");
    expect(usage).toEqual({ fails: 0, error: null });
    // Every segment's content held (the compaction after may take the kept one in), and the
    // muse's body reads back whole.
    expect(await sqlIn(r.stub, "SELECT segment FROM archive_segments WHERE content_id NOT IN " +
                       "(SELECT content_id FROM file_contents)")).toEqual([]);
    expect((await chained(r, kept.message_id)) === big("a")).toBe(true);
  });

  it("aborts a move whose batch changed between its units, the mail kept hot", async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    // A body at the cap, acked: a seal's worth to pick (the hot set far from half, so no
    // lane moves it first).
    await sealed(r, MUSE, room);
    const outcome = await r.inLedger((ledger: Ledger) => {
      const pick = ledger.archivePick()!;
      const batch = ledger.archiveBatch(pick.seqs, new MoveTable(), () => 0)!;
      // Between the units: the agent deleted, its mail with it.
      ledger.remove(muse);
      try {
        ledger.archiveCommit(pick.seqs, batch.digest, [], [{ content_id: "e".repeat(64),
          fence: { first_key: 1, last_key: 1, first_at: 1, last_at: 1, records: 1, bytes: 1,
                   trailer_offset: 0, trailer_bytes: 1, members: "0".repeat(128) } }], 1);
        return "applied";
      } catch (error) {
        return (error as Error).message;
      }
    });
    expect(outcome).toMatch(/^archive_changed:/);
    expect(await sqlIn(r.stub, "SELECT * FROM archive_segments")).toEqual([]);
  });
});

describe("an agent's deletion", () => {
  it("drops its archived mail at once, and the lane rewrites it out, keeping the rest",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // Their mail interleaved, then a body at the cap to Dot: one segment holds both.
    const kept: string[] = [];
    const gone: string[] = [];
    for (let i = 0; i < 3; i++) {
      gone.push(...await sentAndAcked(r, MUSE, `dm:${muse}`, 1, `muse${i}`));
      kept.push(...await sentAndAcked(r, DOT, `dm:${dot}`, 1, `dot${i}`));
    }
    await sealed(r, DOT, `dm:${dot}`);
    await settle(r);
    const archivedGone = [];
    for (const id of gone) {
      if ((await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?", id))
        .length === 0) {
        archivedGone.push(id);
      }
    }
    expect(archivedGone.length).toBeGreaterThan(0);
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    // At once: no read finds the deleted agent's archived mail.
    for (const id of archivedGone) {
      expect((await failure(() => r.stub.mailChain(r.bind(null), { message_id: id,
                                                                     limit: 10 }))).message)
        .toBe("no such message held");
    }
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT * FROM archive_purges")).toEqual([]);
    // Rewritten: every segment's records are Dot's alone, and every Dot mail still reads.
    for (const fence of await sqlIn(r.stub, "SELECT * FROM archive_segments")) {
      const records = await runInDurableObject(r.stub, async (_i: OwnerDO, state) =>
        state.storage.sql.exec<{ sha256: string }>(
          "SELECT sha256 FROM file_content_chunks WHERE content_id = ? ORDER BY idx",
          fence["content_id"] as string).toArray());
      expect(records.length).toBeGreaterThan(0);
    }
    for (const [n, id] of kept.entries()) {
      const chain = await r.stub.mailChain(r.bind(null), { message_id: id, limit: 10 });
      expect(chain.messages.map((m) => m.body)).toEqual([`dot${n} 0`]);
    }
  });

  it("waits on at most maxAgentRows purges: past them a deletion is refused busy in plain " +
     "words, deleting nothing, and lands once the lane has caught up", async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const [first] = await sentAndAcked(r, MUSE, `dm:${muse}`, 4);
    await sealed(r, MUSE, `dm:${muse}`);
    await settle(r);
    expect((await sqlIn(r.stub, "SELECT * FROM archive_segments")).length).toBeGreaterThan(0);
    // A segment stands, so each deletion leaves a purge waiting: an agent registered and
    // deleted maxAgentRows times, its rows never more than one.
    const refused = await r.inLedger((ledger: Ledger) => {
      for (let n = 0; n < maxAgentRows; n++) {
        ledger.remove(ledger.register(origin(1), MCP, { command_center_id: "alpha",
          display_name: "Passing", delivery_method: "pull", recipe_id: null }).agent_id);
      }
      try {
        ledger.remove(muse);
        return null;
      } catch (error) {
        return (error as Error).message;
      }
    });
    expect(refused).toBe("storage_busy: earlier deleted agents' mail is still being removed " +
                         `from the archive; try again in ${STORAGE_BUSY_RETRY_S} seconds. ` +
                         JSON.stringify({ retry_after_s: STORAGE_BUSY_RETRY_S }));
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_purges"))
      .toEqual([{ n: maxAgentRows }]);
    expect((await chained(r, first!))).toBe("m 0");  // the muse and its mail kept
    // The lane, rung, catches up; then the deletion lands.
    await sentAndAcked(r, MUSE, `dm:${muse}`, 1, "ring");
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT * FROM archive_purges")).toEqual([]);
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    expect((await failure(() => r.stub.mailChain(r.bind(null), { message_id: first!,
                                                                 limit: 10 }))).message)
      .toBe("no such message held");
  });
});

describe("the export, version 3", () => {
  it("ships each segment as it is, and its rows read back as the mail before the move",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const ids = await sentAndAcked(r, MUSE, `dm:${muse}`, 4);
    const seal = await sealed(r, MUSE, `dm:${muse}`);
    await settle(r);
    await unballast(r);
    const fences = await sqlIn(r.stub, "SELECT * FROM archive_segments ORDER BY segment");
    expect(fences.length).toBeGreaterThan(0);
    // The export's pages: the fence rows among its tables, the segment's chunks named.
    const { lines, archived } = await exportArchived(r);
    expect(lines[0]).toMatchObject({ t: "header", version: 3 });
    expect(lines.filter((line) => line["t"] === "row" && line["table"] === "archive_segments")
      .length).toBe(fences.length);
    // Each archived message is the owner's mail whole: its row, its body inline, its acked
    // recipient; with the hot ones, every message sent, once.
    const hot = (await sqlIn(r.stub, "SELECT message_id FROM ledger_messages"))
      .map((row) => row["message_id"]);
    expect([...hot, ...archived.map((rows) => rows["ledger_messages"]!["message_id"])].sort())
      .toEqual([...ids, seal].sort());
    const bodies = new Map([...ids.map((id, n) => [id, `m ${n}`] as const),
                            [seal, "seal 0".padEnd(BODY_BYTES_MAX, "x")] as const]);
    for (const rows of archived) {
      const id = rows["ledger_messages"]!["message_id"] as string;
      expect(rows["message_bodies"]!["body"] === bodies.get(id)).toBe(true);
      expect(rows["message_bodies"]).toMatchObject({ content_id: null });
      expect(rows["ledger_recipients"]).toMatchObject({ agent_id: MUSE_ID(rows), unacked: 0 });
    }
  });
});

describe("segments never overlap in key range (review H1)", () => {
  it("are refused by the schema when two would", async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    await sentAndAcked(r, MUSE, `dm:${muse}`, 4);
    await sealed(r, MUSE, `dm:${muse}`);
    await settle(r);
    const [fence] = await sqlIn(r.stub, "SELECT * FROM archive_segments ORDER BY first_key");
    const [first0, last0] = [fence!["first_key"] as number, fence!["last_key"] as number];
    const insert = (first: number, last: number) => runInDurableObject(r.stub,
      (_i: OwnerDO, state) => state.storage.sql.exec(
        "INSERT INTO archive_segments (stream, first_key, last_key, first_at, last_at, " +
          "records, bytes, content_id, filer, trailer_offset, trailer_bytes, members, " +
          "made_at) VALUES ('mail', ?, ?, 1, 1, 1, 1, ?, 'owner', 0, 1, ?, 1)", first, last,
        fence!["content_id"] as string, "0".repeat(128)));
    for (const [first, last] of [[first0, first0], [last0, last0 + 5], [0, first0],
                                 [0, last0 + 100]] as const) {
      expect((await failure(() => insert(first, last))).message)
        .toMatch(/archive segments never overlap in key range/);
    }
    await insert(last0 + 10_000, last0 + 10_001);  // past every key: disjoint, taken
    expect(await overlaps(r)).toEqual([]);
  });

  it("stay disjoint, and every message readable by its chain, through two deletions " +
     "(the reviewer's case)", { tags: ["large"] }, async () => {
    const r = await seeded();
    const x = await r.register(MUSE, "Muse");
    const y = await r.register(DOT, "Dot");
    const a = await r.register(EVE, "Eve");
    // The reviewer's run: its first message to Muse, its 23rd to Dot, the rest to Eve; each
    // body 4 MiB, so two seal a segment, and its last four stay hot.
    const who = (n: number) => n === 1 ? [MUSE, x] as const
      : n === 23 ? [DOT, y] as const : [EVE, a] as const;
    const body = (n: number) => `m${n} `.padEnd(4 * MIB, "x");
    const ids: string[] = [];
    for (let n = 1; n <= 28; n++) {
      const [from, agent] = who(n);
      const posted = await r.post(null, `dm:${agent}`, { body: body(n), cmid: `c${n}` });
      ids.push(posted.message_id);
      const page = await r.read(from);
      await r.ack(from, page.messages.at(-1)!.seq);
      if (n % 2 === 0) {
        await settle(r);
      }
    }
    await settle(r);
    expect(await overlaps(r)).toEqual([]);
    await r.stub.deleteAgent(r.session(null), { agent_id: x });
    await settle(r);
    expect(await overlaps(r)).toEqual([]);
    await r.stub.deleteAgent(r.session(null), { agent_id: y });
    await settle(r);
    expect(await overlaps(r)).toEqual([]);
    // Each one's chain answers it whole and intact (its first answer cut to fit).
    for (let n = 2; n <= 28; n++) {
      if (n !== 23) {
        const page = await r.stub.mailChain(r.bind(null), { message_id: ids[n - 1]!,
                                                            limit: 1 });
        const [m] = page.messages;
        expect([m!.message_id, m!.integrity, m!.body_bytes, body(n).startsWith(m!.body!)],
               `message ${n}`).toEqual([ids[n - 1], "ok", 4 * MIB, true]);
      }
    }
    for (const n of [1, 23]) {
      expect((await failure(() => r.stub.mailChain(r.bind(null), {
        message_id: ids[n - 1]!, limit: 10 }))).message).toBe("no such message held");
    }
  });

  it("merges a lone straggler (acked after the mail around it moved) into the segment its " +
     "key falls in: due by itself, with no seal's worth behind it", async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // The first to Muse, left unread; Dot's mail after it is acked and moves.
    const first = await r.post(null, `dm:${muse}`, { body: "late", cmid: "late" });
    await sentAndAcked(r, DOT, `dm:${dot}`, 3, "d");
    await sealed(r, DOT, `dm:${dot}`);
    await settle(r);
    const late = await seqOf(r, first.message_id);
    const before = await sqlIn(r.stub, "SELECT first_key, last_key FROM archive_segments " +
                                       "ORDER BY first_key");
    expect(before.length).toBe(1);
    expect(before[0]!["first_key"]).toBeGreaterThan(late);
    // Dot's small mail past half the hot ceiling again, far short of a seal: nothing is due.
    await sentAndAcked(r, DOT, `dm:${dot}`, 4, "e");
    await settle(r);
    expect(await r.inLedger((ledger: Ledger) => ledger.archiveDue())).toBe(false);
    // Muse acks it now, in the ledger (so no lane runs first): it is below the archive's
    // last key, a straggler, and makes the lane due alone.
    const pick = await r.inLedger((ledger: Ledger) => {
      ledger.read(MUSE, 1);
      ledger.ack(MUSE, late);
      return [ledger.archiveDue(), ledger.archivePick()?.seqs];
    });
    expect(pick).toEqual([true, [late]]);
    await sentAndAcked(r, DOT, `dm:${dot}`, 1, "ring");
    await settle(r);
    expect(await overlaps(r)).toEqual([]);
    const held = await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                             first.message_id);
    expect(held).toEqual([]);
    expect(await chained(r, first.message_id)).toBe("late");
    // Merged, never a seal: Dot's small mail is still hot.
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM ledger_messages WHERE room_id = ?",
                       `dm:${dot}`)).toEqual([{ n: 5 }]);
  });
});

describe("a merge's byte budget (reviews N1 and N3)", () => {
  it("merges large late-acked mail a budget at a pass, never all at once, every message kept",
     { tags: ["large"] }, async () => {
    const r = await seeded(8);
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // Five messages near 7 MiB each to Muse, left unread; Dot's mail after them moves.
    const large: string[] = [];
    for (let i = 0; i < 5; i++) {
      large.push((await r.post(null, `dm:${muse}`, {
        cmid: `big${i}`, body: `${i}`.padEnd(7 * MIB, "x") })).message_id);
    }
    await sentAndAcked(r, DOT, `dm:${dot}`, 3, "d");
    await sealed(r, DOT, `dm:${dot}`);
    await settle(r);
    const [{ n: segments }] = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM " +
                                                  "archive_segments") as [{ n: number }];
    expect(segments).toBe(1);
    // Muse acks them now (a read pages at 8 MiB of bodies, so a page each): five stragglers,
    // about 35 MiB, inside that segment's keys; the hot set at half, so none merges yet.
    for (let i = 0; i < 5; i++) {
      await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    }
    const pick = await r.inLedger((ledger: Ledger) => ledger.archivePick());
    expect(pick!.into).not.toBeNull();
    // 21 MiB within MOVE_BYTES less the segment's 8 MiB and more; the other two wait.
    expect(pick!.into!.bytes + 4 * 7 * MIB).toBeGreaterThan(MOVE_BYTES);
    expect(pick!.seqs).toHaveLength(3);
    // Past half the ceiling until every merge has run: each pass merges one budget.
    await sentAndAcked(r, DOT, `dm:${dot}`, 12, "e");
    for (let i = 0; i < 40; i++) {
      await drainDue(r.stub);
      const left = await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id IN " +
                                       "(SELECT value FROM json_each(?))", JSON.stringify(large));
      if (left.length === 0) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    expect(await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id IN " +
                               "(SELECT value FROM json_each(?))", JSON.stringify(large)))
      .toEqual([]);
    expect(await overlaps(r)).toEqual([]);
    // Every one whole in its segment (read by the export's rule: the chain read pages a
    // 7 MiB body a hundred and fifty answers at a time, which the tests above cover).
    await unballast(r);
    const bodies = await archivedBodies(r);
    for (const [n, id] of large.entries()) {
      expect(bodies.get(id)?.length).toBe(7 * MIB);
      expect(bodies.get(id)?.[0]).toBe(`${n}`);
    }
  });
});

describe("an export across a move (review H2)", () => {
  it("replays exactly the owner at E by v3's reading: segments' rows after the log",
     async () => {
    const r = await rig();
    onTestFinished(() => r.letGo());
    const muse = await r.register(MUSE);
    // Acked mail of bodies as large as the object keeps inline, past a seal's worth.
    const body = (i: number) => `body ${i} `.padEnd(2_000, "x");
    const ids = await r.inLedger((ledger: Ledger) => {
      const sent: string[] = [];
      for (let i = 0; i < 4_000; i++) {
        sent.push(ledger.post(null, `dm:${muse}`, `c${i}`, body(i), null).message_id);
      }
      ledger.read(MUSE, 1);
      ledger.ack(MUSE, ledger.seqOf(sent.at(-1)!)!);
      return sent;
    });
    // A generation, with one archive move made inside its window (after its first unit, so
    // after S and before E): the move's deletes are logged `gone`, its fence logged too.
    let moved = false;
    const generated = await generate(r.stub, { afterUnit: async ({ files, gate }) => {
      if (moved) {
        return;
      }
      moved = true;
      const ledger = ledgerOn(gate.storage as unknown as DurableObjectStorage);
      const pick = await gate.write(() => ledger.archivePick())!;
      const batch = await gate.write(() => batchOf(ledger, pick!.seqs))!;
      // Placed as a move places it: planned, then streamed a chunk at a time.
      const plan = planSegment("mail", batch!.table);
      const chunks: { sha256: string; bytes: Uint8Array }[] = [];
      for (let n = 0; n < plan.chunks; n++) {
        const bytes = await makeChunk(plan, n, hotSources(ledger, batch!.table), noRead);
        chunks.push({ sha256: createHash("sha256").update(bytes).digest("hex"), bytes });
      }
      const begun = await gate.write(() => files.begin(plan.size,
        chunks.map((chunk) => chunk.sha256), "owner", [], true, null, true));
      for (const chunk of chunks) {  // an upload sends at most a chunk's bytes at once
        await files.upload(begun.intent_id, [chunk]);
      }
      await gate.write(() => files.adopt(begun.intent_id, "owner", (contentId) =>
        ledger.archiveCommit(pick!.seqs, batch!.digest, [], [{ content_id: contentId, fence: {
          first_key: plan.first, last_key: plan.last, first_at: 1, last_at: 1,
          records: plan.records.count, bytes: plan.size,
          trailer_offset: plan.trailer_offset, trailer_bytes: plan.trailer_bytes,
          members: "0".repeat(128) } }], 1)));
    }, sizes: { page: 256, part: 32 * 1024, gather: 64 * 1024 } });
    expect(moved).toBe(true);
    const lines = generated.lines as unknown as Record<string, unknown>[];
    expect(lines.filter((line) => line["t"] === "gone" && line["table"] === "ledger_messages")
      .length).toBeGreaterThan(0);  // the move ran inside the window
    // Base and log, last line per key: exactly the owner's rows at E (hot and fences).
    const atE = replay(generated.lines);
    expect(digest(atE)).toBe(generated.atE);
    // Then each segment standing at E, its rows applied over that, after the log.
    const tables = exported();
    const fences = Object.values(atE["archive_segments"]!).map((row) =>
      JSON.parse(row) as Record<string, unknown>);
    expect(fences.length).toBe(1);
    for (const fence of fences) {
      const shas = (await sqlIn(r.stub, "SELECT sha256 FROM file_content_chunks WHERE " +
                                        "content_id = ? ORDER BY idx",
                                fence["content_id"] as string)).map((row) => row["sha256"]);
      const whole = Buffer.from(await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
        const space = state.storage.sql.exec<{ ns: string; data_key: string }>(
          "SELECT ns, data_key FROM file_space").one();
        const parts: Buffer[] = [];
        for (const sha of shas) {  // a get moves at most a chunk's bytes at once
          const [part] = await (env.FILES as ChunkStore).getChunks(space.ns, space.data_key,
                                                                   [{ sha256: sha as string }]);
          parts.push(Buffer.from(part!));
        }
        return Buffer.concat(parts).toString("base64");
      }), "base64");
      const trailer = parseTrailer(whole.subarray(fence["trailer_offset"] as number,
        (fence["trailer_offset"] as number) + (fence["trailer_bytes"] as number)).toString());
      for (const [, offset, length] of trailer.keys) {
        for (const [table, row] of recordRows(whole.subarray(offset, offset + length))) {
          atE[table]![JSON.stringify(tables[table]!.key.map((column) => row[column]))] =
            JSON.stringify(row);
        }
      }
    }
    // Every message the owner held at E, once: the moved ones from their segment.
    const read = Object.values(atE["ledger_messages"]!).map((row) =>
      (JSON.parse(row) as Record<string, unknown>)["message_id"]);
    expect(read.sort()).toEqual([...ids].sort());
    const bodies = Object.values(atE["message_bodies"]!).map((row) =>
      (JSON.parse(row) as Record<string, unknown>)["body"]).sort();
    expect(bodies).toEqual(ids.map((_id, i) => body(i)).sort());
  });
});

describe("a body of control characters (review N3)", () => {
  it("moves as its own bytes, reads back byte-exact by its chain, and exports whole",
     { tags: ["large"] }, async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    // The reviewer's message: a body at the cap, every byte a control character (which JSON
    // escapes six times over), placed in R2 as any large body is.
    const body = "\u0001".repeat(BODY_BYTES_MAX);
    const posted = await r.post(null, `dm:${muse}`, { cmid: "ctl", body });
    await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    await sentAndAcked(r, MUSE, `dm:${muse}`, 4);
    await settle(r);
    const fences = await sqlIn(r.stub, "SELECT first_key, last_key, bytes FROM " +
                                       "archive_segments");
    const seq = await seqOf(r, posted.message_id);
    const holding = fences.find((fence) => (fence["first_key"] as number) <= seq
      && seq <= (fence["last_key"] as number));
    expect(holding).toBeDefined();
    // The segment is the body's own bytes and its lines', never its escapes.
    expect(holding!["bytes"] as number).toBeLessThan(BODY_BYTES_MAX + 64 * 1024);
    expect(await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                       posted.message_id)).toEqual([]);
    expect((await chained(r, posted.message_id)) === body).toBe(true);
    await unballast(r);
    const { archived } = await exportArchived(r);
    const exported = archived.find((rows) =>
      rows["ledger_messages"]!["message_id"] === posted.message_id);
    expect(exported!["message_bodies"]!["body"] === body).toBe(true);
    expect(exported!["message_bodies"]).toMatchObject({ body_bytes: BODY_BYTES_MAX,
                                                       content_id: null });
  });
});

describe("the worst merge (review N3)", () => {
  it("merges a straggler into pack's largest piece, every body control characters, intact",
     { tags: ["large"] }, async () => {
    const r = await seeded(8);
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    const near = "\u0001".repeat(8 * MIB - 100 * 1024);  // short of the least: no cut beside it
    const cap = "\u0001".repeat(BODY_BYTES_MAX);
    /** Read and ack `who`'s mail a page at a time (a page holds 8 MiB of bodies). */
    const drain = async (who: typeof MUSE, through = Number.MAX_SAFE_INTEGER) => {
      for (let page = await r.read(who); page.messages.length > 0; page = await r.read(who)) {
        const last = page.messages.filter((m) => m.seq <= through).at(-1);
        if (last === undefined) {
          return;
        }
        await r.ack(who, last.seq);
      }
    };
    const k1 = await r.post(null, `dm:${muse}`, { cmid: "k1", body: near });
    await drain(MUSE);
    const k2 = await r.post(null, `dm:${dot}`, { cmid: "k2", body: cap });
    const k2b = await r.post(null, `dm:${dot}`, { cmid: "k2b", body: cap });
    const k3 = await r.post(null, `dm:${muse}`, { cmid: "k3", body: near });
    await drain(MUSE);
    // Small mail after them, Dot's unread and Muse's acked: the hot set past half throughout.
    for (let i = 0; i < 3; i++) {
      await r.post(null, `dm:${dot}`, { cmid: `u${i}`, body: `u ${i}` });
      await r.post(null, `dm:${muse}`, { cmid: `a${i}`, body: `a ${i}` });
      await drain(MUSE);
    }
    await sentAndAcked(r, MUSE, `dm:${muse}`, 4, "b");
    const segments = () => sqlIn(r.stub, "SELECT first_key, last_key, bytes FROM " +
                                         "archive_segments ORDER BY first_key");
    const moved = async (n: number) => {
      for (let i = 0; i < 40 && (await segments()).length < n; i++) {
        await drainDue(r.stub);
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    };
    // The tail's seal: k1 and k3 (k2 and k2b still Dot's unread), one piece.
    await moved(1);
    expect((await segments()).map((fence) => fence["bytes"] as number))
      .toEqual([expect.any(Number)]);
    // k2 acked: merged into it, pack's largest piece (no cut leaves both sides the least).
    await drain(DOT, await seqOf(r, k2.message_id));
    for (let i = 0; i < 40; i++) {
      await drainDue(r.stub);
      if ((await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                       k2.message_id)).length === 0) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const [piece] = await segments();
    expect(piece!["bytes"] as number).toBeGreaterThan(2 * 8 * MIB + 7 * MIB);
    // k2b acked: the worst merge, that piece and one straggler at the cap (the measured one).
    await drain(DOT, await seqOf(r, k2b.message_id));
    await moved(2);
    const after = await segments();
    expect(after).toHaveLength(2);
    expect(after.every((fence) => (fence["bytes"] as number) >= 8 * MIB)).toBe(true);
    expect(await overlaps(r)).toEqual([]);
    // Each byte-exact in its segment, by the export's rule (a chain read of a body of
    // control characters, a thousand answers of it, is the test above's).
    await unballast(r);
    const bodies = await archivedBodies(r);
    for (const [id, body] of [[k1.message_id, near], [k2.message_id, cap],
                              [k2b.message_id, cap], [k3.message_id, near]] as const) {
      expect(bodies.get(id) === body).toBe(true);
    }
    // And the chain read answers each, its first answer cut to fit.
    const page = await r.stub.mailChain(r.bind(null), { message_id: k1.message_id, limit: 10 });
    expect(page.messages[0]).toMatchObject({ message_id: k1.message_id, truncated: true });
  });
});

describe("the archive lane's failures (review N3)", () => {
  /** The lane's job made due now (a failed pass holds it a minute), then run. */
  const passAgain = async (r: Rig) => {
    await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = " +
                        "'owner-schedule/archive'");
    await drainDue(r.stub);
  };
  const notices = (r: Rig) => sqlIn(r.stub, "SELECT detail FROM owner_notices WHERE type = " +
                                            "'storage_busy_long'");
  /**
   * Four small messages to Muse acked straight in the ledger (no lane rung), then a body at
   * the cap posted, unread, past half the hot ceiling: nothing is due until it is acked. The
   * small ones' seqs, and the cap's.
   */
  const unsealed = async (r: Seeded, muse: string, suffix: string) => {
    const seqs = await r.inLedger((ledger: Ledger) => {
      const sent: number[] = [];
      for (let i = 0; i < 4; i++) {
        sent.push(ledger.seqOf(ledger.post(null, `dm:${muse}`, `c${i}`, `body ${i}${suffix}`,
                                           null).message_id)!);
      }
      ledger.read(MUSE, 10);
      ledger.ack(MUSE, sent.at(-1)!);
      return sent;
    });
    const cap = await r.post(null, `dm:${muse}`, { cmid: "cap", body: "c".repeat(BODY_BYTES_MAX) });
    return { seqs, cap: await seqOf(r, cap.message_id) };
  };

  it("are counted in storage as a pass begins: passes killed with their isolate still tell",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    // Three passes begun that never ended or said why (an isolate killed for memory runs no
    // catch): the count is the row's, not the isolate's.
    await sqlIn(r.stub, "UPDATE ledger_usage SET archive_fails = 3, archive_error = NULL");
    await sentAndAcked(r, MUSE, `dm:${muse}`, 4);
    await sealed(r, MUSE, `dm:${muse}`);
    await settle(r);
    const told = await notices(r);
    expect(told).toHaveLength(1);
    expect(told[0]!["detail"]).toMatch(/stopped before they could say why/);
    // The pass that told them went on, moved the mail, and cleared the count.
    expect(await sqlIn(r.stub, "SELECT archive_fails AS n FROM ledger_usage"))
      .toEqual([{ n: 0 }]);
  });

  it("refuse a move whose second pass differs from its first, loudly, by the chunk's hash",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const { cap } = await unsealed(r, muse, "a");
    // Every segment chunk a move sends has a byte changed in flight, so the bytes R2 is sent
    // are not the ones the move's first pass hashed.
    await gateFor(env.FILES_GATE, await spaceOf(r.stub))
      .arm("putChunks", { corrupt: true }, 1_000);
    await r.stub.mailboxAck(r.bind(MUSE), { through: cap });  // rings the schedule
    for (let pass = 0; pass < 4; pass++) {
      await passAgain(r);
    }
    // Every pass sent other bytes than its first pass hashed: R2 refused each
    // chunk, nothing moved, and the owner was told once, with the error as it was said.
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_segments"))
      .toEqual([{ n: 0 }]);
    // Every message still hot and whole (its row, its body, its acked recipient), and no
    // upload left begun: each failed pass aborted its intents.
    expect(await sqlIn(r.stub, "SELECT m.body_bytes FROM ledger_messages m JOIN " +
                               "message_bodies b ON b.message_id = m.message_id JOIN " +
                               "ledger_recipients t ON t.message_id = m.message_id AND " +
                               "t.unacked = 0 ORDER BY m.seq"))
      .toEqual([...Array.from({ length: 4 }, () => ({ body_bytes: 7 })),
                { body_bytes: BODY_BYTES_MAX }]);
    expect(await sqlIn(r.stub, "SELECT b.body FROM ledger_messages m JOIN message_bodies b " +
                               "ON b.message_id = m.message_id WHERE m.room_id = ? AND " +
                               "b.content_id IS NULL ORDER BY m.seq", `dm:${muse}`))
      .toEqual(Array.from({ length: 4 }, (_, i) => ({ body: `body ${i}a` })));
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM file_intents")).toEqual([{ n: 0 }]);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_moves")).toEqual([{ n: 0 }]);
    const [{ n }] = await sqlIn(r.stub, "SELECT archive_fails AS n FROM ledger_usage") as
      [{ n: number }];
    expect(n).toBeGreaterThanOrEqual(3);
    const told = await notices(r);
    expect(told).toHaveLength(1);
    expect(told[0]!["detail"]).toMatch(/do not match its sha256/);
  });

  it("tell the owner once at three in a row, quoting the error, and clear on success",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const { seqs, cap } = await unsealed(r, muse, "");
    // The oldest acked message's hash no longer matches its body: every move fails.
    await sqlIn(r.stub, "UPDATE ledger_messages SET body_sha256 = ? WHERE seq = ?",
                "0".repeat(64), seqs[0]!);
    await r.stub.mailboxAck(r.bind(MUSE), { through: cap });  // rings the schedule
    for (let pass = 0; pass < 5; pass++) {
      await passAgain(r);
    }
    const [{ n }] = await sqlIn(r.stub, "SELECT archive_fails AS n FROM ledger_usage") as
      [{ n: number }];
    expect(n).toBeGreaterThanOrEqual(3);
    const told = await notices(r);
    expect(told).toHaveLength(1);
    expect(told[0]!["detail"]).toMatch(/an archived body is not the body its message holds/);
    // Mended, the next pass moves the mail and clears the count.
    await sqlIn(r.stub, "UPDATE ledger_messages SET body_sha256 = ? WHERE seq = ?",
                createHash("sha256").update("body 0").digest("hex"), seqs[0]!);
    await passAgain(r);
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT archive_fails AS n FROM ledger_usage"))
      .toEqual([{ n: 0 }]);
    expect(await notices(r)).toHaveLength(1);
  });
});

describe("the tail's seal at the production limits (review N4)", () => {
  it("counts records' own bytes, never their trailer shares: a sealed segment holds the least",
     { tags: ["large"] }, async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const seen = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const sql = state.storage.sql;
      const room = `dm:${muse}`;
      const post = (from: number, to: number) => {
        for (let n = from; n < to; n++) {
          ledger.post(null, room, `c${n}`, "x", null);
        }
        ledger.read(MUSE, 1);
        ledger.ack(MUSE, to);
      };
      // The reviewer's case: 11,999 small messages acked, then the real pick and batch.
      post(0, 11_999);
      const pick = ledger.archivePick()!;
      const { table } = batchOf(ledger, pick.seqs)!;
      const plan = planSegment("mail", table);
      // Streamed as the move streams it (the production `mailParts`), byte for byte the
      // reference encoder's of the same rows (`mailRecord`), every record its message.
      const bytes = await streamed(plan, hotSources(ledger, table), noRead);
      const reference = referenceSegment("mail", pick.seqs.map((seq) => {
        const message = sql.exec<Record<string, SqlStorageValue>>(
          "SELECT * FROM ledger_messages WHERE seq = ?", seq).one();
        const body = sql.exec<Record<string, SqlStorageValue>>(
          "SELECT * FROM message_bodies WHERE message_id = ?", message["message_id"]).one();
        const recipients = sql.exec<Record<string, SqlStorageValue>>(
          "SELECT * FROM ledger_recipients WHERE message_id = ? ORDER BY agent_id",
          message["message_id"]).toArray();
        return mailRecord(message, { ...body, content_id: null }, recipients,
                          [new TextEncoder().encode(body["body"] as string)]);
      })).bytes;
      const same = Buffer.compare(Buffer.from(bytes), Buffer.from(reference)) === 0;
      const trailer = parseTrailer(new TextDecoder().decode(bytes.subarray(
        plan.trailer_offset, plan.trailer_offset + plan.trailer_bytes)));
      const read = trailer.keys.filter(([key, offset, length]) =>
        recordRows(bytes.subarray(offset, offset + length))[0]![1]["seq"] === key).length;
      let largest = 0;
      let records = 0;
      for (let n = 0; n < table.count; n++) {
        largest = Math.max(largest, table.length(n));
        records += table.length(n);
      }
      return { picked: pick.seqs.length, size: bytes.byteLength, largest, read, records, same };
    });
    expect(seen.same).toBe(true);
    expect(seen.read).toBe(seen.picked);
    expect(seen.picked).toBeLessThan(11_999);
    expect(seen.size).toBeGreaterThanOrEqual(LEDGER_LIMITS.archiveBytes);
    expect(seen.records).toBeGreaterThanOrEqual(LEDGER_LIMITS.archiveBytes);
    expect(seen.records).toBeLessThan(LEDGER_LIMITS.archiveBytes + seen.largest);
  });
});

describe("a purge's rewrite, bounded a pass (review N7)", () => {
  /** The lane's job made due now, then run: one pass. */
  const pass = async (r: Rig) => {
    await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = " +
                        "'owner-schedule/archive'");
    await drainDue(r.stub);
  };
  const fences = (r: Rig) => sqlIn(r.stub, "SELECT segment, first_key, last_key, bytes, " +
                                           "records FROM archive_segments ORDER BY first_key");
  /**
   * Mail of `size` bytes a body, `n` messages, every `every`th to Dot and the rest to Muse,
   * each acked at once, then the lane settled: a run of real-sized segments (8 MiB each).
   */
  const build = async (n: number, every: number, size: number) => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    const kept = new Map<string, string>();
    for (let i = 0; i < n; i++) {
      const toDot = i % every === every - 1;
      const body = `${i} `.padEnd(size, toDot ? "d" : "m");
      const posted = await r.post(null, `dm:${toDot ? dot : muse}`, { cmid: `p${i}`, body });
      if (toDot) {
        kept.set(posted.message_id, body);
      }
      const reader = toDot ? DOT : MUSE;
      await r.ack(reader, (await r.read(reader)).messages.at(-1)!.seq);
      if (i % 10 === 9) {
        await drainDue(r.stub);
      }
    }
    for (let i = 0; i < 40; i++) {
      await drainDue(r.stub);
    }
    return { r, muse, kept };
  };
  /**
   * Delete Muse, then run the lane until it has nothing left to do, checking the archive
   * whole after each run; the rewrites, each as it landed (`watchMoves`).
   */
  const purge = async (r: Rig, muse: string) => {
    const before = await fences(r);
    const stop = await watchMoves(r);
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });

    for (let i = 0; i < 60; i++) {
      await pass(r);
      const now = await fences(r);
      // Whole and disjoint after every run, and at most one segment under the least.
      expect(await overlaps(r)).toEqual([]);
      expect(now.filter((fence) => (fence["bytes"] as number) < 8 * MIB).length)
        .toBeLessThanOrEqual(1);
      // Done when no purge waits and no segment under the least stands beside another.
      const [{ left }] = await sqlIn(r.stub, "SELECT (SELECT COUNT(*) FROM archive_purges) + " +
        "(SELECT COUNT(*) FROM archive_segments WHERE bytes < ?) * ((SELECT COUNT(*) FROM " +
        "archive_segments) > 1) AS left", 8 * MIB) as [{ left: number }];
      if (left === 0) {
        break;
      }
    }
    const passes = (await stop()).filter((move) => move.stream === "rewrite");
    return { before, passes, after: await fences(r) };
  };

  it("finishes nine in ten of a run's mail deleted over several passes, each bounded, " +
     "byte-exact", { tags: ["large"] }, async () => {
    const { r, muse, kept } = await build(120, 10, 860 * 1024);
    const { before, passes, after } = await purge(r, muse);
    expect(before.length).toBeGreaterThanOrEqual(8);
    // Each pass takes in neighbours only within MOVE_BYTES of them: the target and at most
    // three of these segments; so the purge took several passes, compacting across them.
    expect(Math.max(...passes.map((p) => p.replaced))).toBeLessThanOrEqual(4);
    expect(passes.every((p) => p.records <= MOVE_RECORDS)).toBe(true);
    expect(passes.filter((p) => p.replaced > 1).length).toBeGreaterThan(1);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_purges")).toEqual([{ n: 0 }]);
    expect(after.every((fence) => (fence["bytes"] as number) >= 8 * MIB)
           || after.length === 1).toBe(true);
    for (const [id, body] of kept) {
      expect((await chained(r, id)) === body).toBe(true);
    }
  });

  it("completes a purge of an agent with nearly all the mail (19 in 20): what is left " +
     "stands alone, byte-exact", { tags: ["large"] }, async () => {
    const { r, muse, kept } = await build(200, 20, 430 * 1024);
    const { before, passes, after } = await purge(r, muse);
    expect(before.length).toBeGreaterThanOrEqual(8);
    expect(Math.max(...passes.map((p) => p.replaced))).toBeLessThanOrEqual(4);
    expect(passes.every((p) => p.records <= MOVE_RECORDS)).toBe(true);
    expect(passes.length).toBeGreaterThan(2);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_purges")).toEqual([{ n: 0 }]);
    expect(after).toHaveLength(1);  // Dot's few archived, under the least, alone
    for (const [id, body] of kept) {
      expect((await chained(r, id)) === body).toBe(true);
    }
  });
});

describe("mail moves during a long purge (review N8, the reviewer's rvsh7_starve)", () => {
  it("keeps pace: a pass moves mail whenever it is due, a rewrite or not", { tags: ["large"] }, async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    const eve = await r.register(EVE, "Eve");
    // Eve's message first, left unread: acked with the deletion, it is a straggler.
    const late = await r.post(null, `dm:${eve}`, { cmid: "late", body: "late" });
    // A production-sized archive (8 MiB segments), Muse's mail 19 in 20 of it.
    for (let i = 0; i < 240; i++) {
      const toDot = i % 20 === 19;
      await r.post(null, `dm:${toDot ? dot : muse}`, { cmid: `p${i}`,
                                                      body: `${i} `.padEnd(860 * 1024, "m") });
      await r.ack(toDot ? DOT : MUSE, (await r.read(toDot ? DOT : MUSE)).messages.at(-1)!.seq);
      if (i % 10 === 9) {
        await drainDue(r.stub);
      }
    }
    for (let i = 0; i < 40; i++) {
      await drainDue(r.stub);
    }
    expect((await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_segments"))[0]!["n"])
      .toBeGreaterThan(15);
    const stop = await watchMoves(r);
    // Muse deleted, Eve's message acked (a straggler, so mail is due) and Dot's small mail,
    // acked, past half the hot ceiling, in one unit of the ledger, which rings no lane; then
    // one post rings it, so its first pass finds both the purge and mail due.
    // (Deleted through the door first, its ring could start a pass that rewrote alone
    // before the mail was due, as one in a loaded leg did: 2026-11-04 18:33, `rrM`.)
    const straggler = await seqOf(r, late.message_id);
    await r.inLedger((ledger: Ledger) => {
      ledger.remove(muse);
      ledger.read(EVE, 1);
      ledger.ack(EVE, straggler);
      for (let i = 0; i < 30; i++) {
        const seq = ledger.seqOf(ledger.post(null, `dm:${dot}`, `pre${i}`, `pre ${i}`, null)
          .message_id)!;
        ledger.read(DOT, 1);
        ledger.ack(DOT, seq);
      }
    });
    await r.post(null, `dm:${dot}`, { cmid: "ring", body: "ring" });
    await r.ack(DOT, (await r.read(DOT)).messages.at(-1)!.seq);
    // The purge runs pass by pass while Dot is sent mail and acks it.
    let done = false;
    const purged = (async () => {
      for (let i = 0; i < 60; i++) {
        await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = " +
                            "'owner-schedule/archive'");
        await drainDue(r.stub);
        const [{ left }] = await sqlIn(r.stub, "SELECT (SELECT COUNT(*) FROM archive_purges) " +
          "+ (SELECT COUNT(*) FROM archive_segments WHERE bytes < ?) * ((SELECT COUNT(*) FROM " +
          "archive_segments) > 1) AS left", 8 * MIB) as [{ left: number }];
        if (left === 0) {
          break;
        }
      }
      done = true;
    })();
    const answers: string[] = [];
    for (let n = 0; !done && n < 400; n++) {
      try {
        await r.post(null, `dm:${dot}`, { cmid: `new${n}`, body: `new ${n}` });
        await r.ack(DOT, (await r.read(DOT)).messages.at(-1)!.seq);
        answers.push("ok");
      } catch (error) {
        answers.push(String((error as Error).message).split(":")[0]!);
      }
    }
    await purged;
    const moves = (await stop()).map((move) => (move.stream === "mail" ? "M" : "r")).join("");
    // The first pass rewrote and moved: mail did not wait for the purge's rewrites.
    expect(moves.indexOf("M")).toBe(1);
    expect(moves.lastIndexOf("r")).toBeGreaterThan(1);
    expect(answers.filter((answer) => answer !== "ok")).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_purges")).toEqual([{ n: 0 }]);
  });
});

describe("a full owner's archive (review M1)", () => {
  it("still drains: a move may place past the figure, since it frees what it places",
     async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    // Four small bodies, acked in the ledger (no lane rung), then a body at the cap, placed
    // in R2, of bytes no chunk of the segment repeats: a seal's worth past half the hot
    // ceiling, inline and placed records together.
    for (let i = 0; i < 4; i++) {
      await r.post(null, `dm:${muse}`, { body: `body ${i}`, cmid: `c${i}` });
      await r.inLedger((ledger: Ledger) =>
        ledger.ack(MUSE, ledger.read(MUSE, 1).messages[0]!.seq));
    }
    const body = Array.from({ length: BODY_BYTES_MAX / 64 }, (_, i) =>
      createHash("sha256").update(String(i)).digest("hex")).join("");
    const last = await seqOf(r, (await r.post(null, `dm:${muse}`, { body, cmid: "cap" }))
      .message_id);
    // The owner's own figure: exactly what is stored, so no segment fits beside the mail.
    const { stored } = await r.stub.storageUsage(r.bind(null)).then((usage) =>
      ({ stored: usage.used_bytes }));
    const { generation, policy } = await r.stub.policyRead(r.bind(null));
    await r.stub.policyWrite(r.session(null), {
      expected_generation: generation,
      policy: { ...policy, files: { max_bytes: stored } } as never });
    await r.stub.mailboxAck(r.bind(MUSE), { through: last });  // rings the schedule
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT records FROM archive_segments")).toEqual([{ records: 5 }]);
    const after = (await r.stub.storageUsage(r.bind(null))).used_bytes;
    expect(after).toBeLessThanOrEqual(stored);
    expect(await sqlIn(r.stub, "SELECT archive_blocked AS b FROM ledger_usage"))
      .toEqual([{ b: 0 }]);
  });

  it("makes a post at the hot ceiling storage_full, the one wall, when a move cannot land",
     { tags: ["large"] }, async () => {
    const r = await rig();
    onTestFinished(() => r.letGo());
    // The hot set at its ceiling, a segment's worth of it acked, four unread to spare.
    const agent = await r.inLedger((ledger: Ledger) => ballast(ledger, 4, 0));
    // A move refused for the figure leaves the archive blocked (the lane's own mark).
    await sqlIn(r.stub, "UPDATE ledger_usage SET archive_blocked = 1");
    const refused = await failure(() => r.post(null, `dm:${agent}`, { cmid: "over",
                                                                      body: "over" }));
    expect(refused.message).toMatch(/^storage_full: your storage is full/);
    expect(refusalOf(refused)?.facts).toMatchObject({ limit_bytes: expect.any(Number) });
  });
});

describe("a tier's storage", () => {
  it("is held to TIER_STORAGE_MAX, the fence rows' bound: a larger tier needs larger segments",
     () => {
    const tier = (bytes: number) => ({ free: { runs: 5, storage_bytes: 25e9 },
                                       paid: { runs: 10, storage_bytes: bytes } });
    expect(parseTiers(tier(TIER_STORAGE_MAX)).paid.storage_bytes).toBe(TIER_STORAGE_MAX);
    expect(() => parseTiers(tier(TIER_STORAGE_MAX + 1))).toThrow(/storage whole bytes up to/);
  });
});

/**
 * `storage` with every statement's rows read and written counted, as its cursors count them
 * (`rowsRead`, `rowsWritten`, triggers' work included): exact, whatever the host.
 */
function counted(storage: DurableObjectStorage) {
  const cursors: SqlStorageCursor<Record<string, SqlStorageValue>>[] = [];
  const bound = <T extends object>(target: T, prop: string | symbol) => {
    const value = Reflect.get(target, prop, target) as unknown;
    return typeof value === "function" ? value.bind(target) : value;
  };
  const sql = new Proxy(storage.sql, { get: (target, prop) => prop !== "exec"
    ? bound(target, prop)
    : (query: string, ...bindings: unknown[]) => {
      const cursor = target.exec(query, ...bindings);
      cursors.push(cursor);
      return cursor;
    } });
  return {
    storage: new Proxy(storage, { get: (target, prop) => prop === "sql" ? sql
      : bound(target, prop) }),
    /** The rows read and written since the last take. */
    take: () => {
      const rows = cursors.reduce((sum, cursor) => ({ read: sum.read + cursor.rowsRead,
                                                      written: sum.written + cursor.rowsWritten }),
                                  { read: 0, written: 0 });
      cursors.length = 0;
      return rows;
    },
  };
}

describe("the ledger at its production limits (rs_cap §2.2)", () => {
  /** A message's body: over 335 bytes, as most mail is, so a segment seals by its bytes. */
  const BODY = "m".repeat(600);
  const { archiveBytes, archiveRecords } = LEDGER_LIMITS;

  it("reads and writes the same rows for a post at 1 and at 49,999 hot (never a scan), " +
     "and at the hot ceiling tells a post busy until one lane pass moves a segment sealed " +
     "by its bytes, then lands it, nothing lost", { tags: ["large"] }, async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const filled = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const count = counted(state.storage);
      const ledger = ledgerOn(count.storage as DurableObjectStorage);
      const acked = () => {
        ledger.read(MUSE, 1);
        ledger.ack(MUSE, state.storage.sql.exec<{ seq: number }>(
          "SELECT max(seq) AS seq FROM ledger_messages").one().seq);
      };
      // The first post makes the ledger's one-time rows (its id key, the reader's counter).
      ledger.post(null, room, "c0", BODY, null);
      acked();
      count.take();
      ledger.post(null, room, "c1", BODY, null);
      const first = count.take();
      acked();
      // To one short of the hot ceiling, every message acked, as the lane would find it.
      for (let n = 2; n < maxMessages - 1; n++) {
        ledger.post(null, room, `c${n}`, BODY, null);
        if (n % 1_000 === 0) {
          acked();
        }
      }
      acked();
      const hot = state.storage.sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM ledger_messages").one().n;
      count.take();
      ledger.post(null, room, `c${maxMessages - 1}`, BODY, null);
      const last = count.take();
      acked();
      let refused = "";
      try {
        ledger.post(null, room, "past", BODY, null);
      } catch (error) {
        refused = (error as Error).message;
      }
      return { first, hot, last, refused };
    });
    console.log(`a post's rows at 1 hot: ${JSON.stringify(filled.first)}; at ` +
                `${filled.hot} hot: ${JSON.stringify(filled.last)}`);
    expect(filled.hot).toBe(maxMessages - 1);
    expect(filled.last).toEqual(filled.first);
    expect(filled.refused).toMatch(/^storage_busy: your storage is catching up/);
    expect(JSON.parse(filled.refused.slice(filled.refused.indexOf("{"))))
      .toEqual({ retry_after_s: STORAGE_BUSY_RETRY_S });
    // The doors' post: busy alike, nothing deleted; it rings the lane, which may move mail
    // out at once, so hot and archived are counted together, in one read.
    expect((await failure(() => r.post(null, room, { cmid: "past", body: BODY }))).message)
      .toMatch(/^storage_busy:/);
    expect(await sqlIn(r.stub, "SELECT (SELECT COUNT(*) FROM ledger_messages) + (SELECT " +
                               "coalesce(SUM(records), 0) FROM archive_segments) AS n"))
      .toEqual([{ n: maxMessages }]);
    // One lane pass: at least one segment moved out, each sealed by its bytes.
    await sqlIn(r.stub, "UPDATE cf_agents_jobs SET time = 0 WHERE id = 'owner-schedule/archive'");
    await drainDue(r.stub);
    const fences = await sqlIn(r.stub, "SELECT records, bytes FROM archive_segments") as
      { records: number; bytes: number }[];
    console.log(`one pass moved ${JSON.stringify(fences)}`);
    expect(fences.length).toBeGreaterThan(0);
    for (const fence of fences) {
      expect(fence.records).toBeLessThan(archiveRecords);
      expect(fence.bytes).toBeGreaterThanOrEqual(archiveBytes);
    }
    // The post lands, and every message is still the owner's: hot or archived, in one read.
    await r.post(null, room, { cmid: "past", body: BODY });
    expect(await sqlIn(r.stub, "SELECT (SELECT COUNT(*) FROM ledger_messages) + (SELECT " +
                               "coalesce(SUM(records), 0) FROM archive_segments) AS n"))
      .toEqual([{ n: maxMessages + 1 }]);
    // Each message once: the segments' key ranges run on from the first seq with no gap,
    // each holding every key in its range, and the hot set is exactly the keys after them,
    // every one its own message. Both read in one turn of the object, so no move the post
    // rang lands between them.
    const { ranges, hot } = await runInDurableObject(r.stub, (_i: OwnerDO, state) => ({
      ranges: state.storage.sql.exec<{ first_key: number; last_key: number; records: number }>(
        "SELECT first_key, last_key, records FROM archive_segments ORDER BY first_key")
        .toArray(),
      hot: state.storage.sql.exec("SELECT min(seq) AS first, max(seq) AS last, COUNT(*) AS n, " +
                                  "COUNT(DISTINCT client_message_id) AS ids FROM ledger_messages")
        .toArray(),
    }));
    expect(ranges[0]!.first_key).toBe(1);
    for (const [n, range] of ranges.entries()) {
      expect(range.records).toBe(range.last_key - range.first_key + 1);
      if (n > 0) {
        expect(range.first_key).toBe(ranges[n - 1]!.last_key + 1);
      }
    }
    const moved = ranges.at(-1)!.last_key;
    expect(hot).toEqual([{ first: moved + 1, last: maxMessages + 1, n: maxMessages + 1 - moved,
                  ids: maxMessages + 1 - moved }]);
  });

  it("reads and writes the same rows for a post at 1 and at 24,999 unread; at its unread " +
     "ceiling refuses a post as full, naming who holds the most; acking one makes room for " +
     "exactly one", { tags: ["large"] }, async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    const seen = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const count = counted(state.storage);
      const ledger = ledgerOn(count.storage as DurableObjectStorage);
      const unread = () => state.storage.sql.exec<{ n: number }>(
        "SELECT unacked AS n FROM ledger_usage").one().n;
      const refusal = (cmid: string) => {
        try {
          ledger.post(null, `dm:${dot}`, cmid, BODY, null);
          return null;
        } catch (error) {
          return (error as Error).message;
        }
      };
      // The ledger's one-time rows and both readers' counters made, Muse's one message acked.
      ledger.post(null, `dm:${muse}`, "m0", BODY, null);
      ledger.post(null, `dm:${dot}`, "d0", BODY, null);
      ledger.ack(MUSE, ledger.read(MUSE, 1).messages[0]!.seq);
      const atOne = unread();
      count.take();
      ledger.post(null, `dm:${dot}`, "d1", BODY, null);
      const first = count.take();
      // Unread to one short of the ceiling, three in five to Muse.
      for (let n = 0; unread() < maxUnacked - 1; n++) {
        ledger.post(null, `dm:${n % 5 < 3 ? muse : dot}`, `c${n}`, BODY, null);
      }
      const atLast = unread();
      count.take();
      ledger.post(null, `dm:${dot}`, "d2", BODY, null);
      const last = count.take();
      const readers = state.storage.sql.exec<{ reader: string; unacked: number }>(
        "SELECT reader, unacked FROM ledger_readers").toArray();
      const full = refusal("over");
      // Mail to the owner waits on the same one ceiling: no mailbox has its own.
      let toOwner = null;
      try {
        ledger.post(MUSE, `dm:${muse}`, "to-owner", BODY, null);
      } catch (error) {
        toOwner = (error as Error).message;
      }
      // Muse reads and acks its first message: one room, for one post.
      ledger.ack(MUSE, ledger.read(MUSE, 1).messages[0]!.seq);
      return { atOne, first, atLast, last, readers, full, toOwner, landed: refusal("one"),
               again: refusal("two") };
    });
    console.log(`a post's rows at ${seen.atOne} unread: ${JSON.stringify(seen.first)}; at ` +
                `${seen.atLast} unread: ${JSON.stringify(seen.last)}`);
    expect([seen.atOne, seen.atLast]).toEqual([1, maxUnacked - 1]);
    expect(seen.last).toEqual(seen.first);
    const held = (id: string) => seen.readers.find((reader) => reader.reader === id)!.unacked;
    expect(held(muse) + held(dot)).toBe(maxUnacked);
    expect(held(muse)).toBeGreaterThan(held(dot));
    expect(seen.full).toMatch(/^ledger_full: too much mail is unread/);
    expect(seen.full).toContain(`the most for Muse (${held(muse)}), Dot (${held(dot)})`);
    expect(seen.toOwner).toBe(seen.full);
    expect(seen.landed).toBeNull();
    expect(seen.again).toMatch(/^ledger_full:/);
  });
});
