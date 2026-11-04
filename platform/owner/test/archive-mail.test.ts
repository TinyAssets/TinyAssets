/**
 * Slice STORE item 2, phase 1 (`history_research.md` §4): acked mail moved out of the owner's
 * object to archive segments, files-plane content in the export's own line grammar with a
 * trailer, one fence row a segment; the `archive` lane from half the hot ceiling, a post at
 * the ceiling `storage_busy` and never a delete; the two-unit move; a reply and the chain
 * read reaching archived mail by the seq its id carries; a deleted agent's mail dropped at
 * once and rewritten out; export version 3 shipping the segments as they are. The object
 * here (`ArchivingOwnerDO`) holds eight messages hot, four unread, and seals a segment at two.
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { BODY_BYTES_MAX } from "../src/bodies";
import { LEDGER_LIMITS, type Ledger, mailRecord } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { parseTiers } from "../src/tier";
import {
  type Content, idKey, idOrder, listed, makeChunk, MIN_RECORD_BYTES, MoveTable, orderedId,
  parseRecord,
  parseTrailer, planSegment, recordBytes, recordParts, recordRows, type SegmentRecord,
  type Source,
  TIER_STORAGE_MAX,
} from "../src/segments";
import { exported } from "../src/export";
import { storageFullOf } from "../src/storage-full";
import type { ChunkStore } from "../src/files";
import { digest, generate, replay } from "./generation-support";
import { DOT, ledgerOn, MUSE, rig } from "./ledger-support";
import { referenceSegment, storedContent, streamed } from "./segment-support";
import { drainDue, failure, sqlIn } from "./support";

const ARCHIVING = env.ARCHIVING as unknown as DurableObjectNamespace<OwnerDO>;
/** The test object's ceilings (`test/worker.ts`). */
const LIMITS = { ...LEDGER_LIMITS, maxMessages: 8, maxUnacked: 4, archiveRecords: 2 };

type Rig = Awaited<ReturnType<typeof rig>>;

/** The recipient a dm with an outside agent goes to from the owner: that agent. */
const MUSE_ID = (rows: Record<string, Record<string, unknown>>) =>
  (rows["ledger_messages"]!["room_id"] as string).slice(3);

/** The owner's mail to `room`, read and acked by `reader` at once: acked, so it may move. */
async function sentAndAcked(r: Rig, reader: typeof MUSE, room: string, n: number,
                            prefix = "m"): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < n; i++) {
    const posted = await r.post(null, room, { body: `${prefix} ${i}`, cmid: `${prefix}${i}` });
    ids.push(posted.message_id);
    const page = await r.read(reader);
    if (page.messages.length > 0) {
      await r.ack(reader, page.messages.at(-1)!.seq);
    }
  }
  return ids;
}

/** The lane's work as the ledger reads it (`archiveDue`), and any purge or move left. */
const BUSY_SQL = "SELECT (SELECT COUNT(*) FROM archive_moves) + (SELECT COUNT(*) FROM " +
  "archive_purges) + (SELECT CASE WHEN u.messages > ? AND (u.messages - u.unacked >= 2 OR " +
  "EXISTS (SELECT 1 FROM ledger_messages WHERE unacked = 0 AND seq < (SELECT " +
  "coalesce(max(last_key), 0) FROM archive_segments))) THEN 1 ELSE 0 END FROM ledger_usage u) " +
  "AS n";

/** Drive the object's lanes until the archive has nothing due. */
async function settle(r: Rig): Promise<void> {
  for (let i = 0; i < 60; i++) {
    await drainDue(r.stub);
    const [{ n }] = await sqlIn(r.stub, BUSY_SQL, LIMITS.maxMessages / 2) as [{ n: number }];
    if (n === 0) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("the archive lane did not settle");
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

/** A chain read, paged by its `after` cursor until `id` shows or it ends: its body, or null. */
async function chained(r: Rig, id: string): Promise<string | null> {
  let after = 0;
  for (let page = 0; page < 20; page++) {
    const chain = await r.stub.mailChain(r.bind(null), { message_id: id, limit: 10, after });
    const found = chain.messages.find((m) => m.message_id === id);
    if (found !== undefined) {
      return found.body;
    }
    if (!chain.more || chain.after <= after) {
      return null;
    }
    after = chain.after;
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

describe("the archive lane", () => {
  it("moves acked mail out from half the hot ceiling, its bytes counted the same, and the " +
     "chain read finds it", async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const ids = await sentAndAcked(r, MUSE, room, 6);
    const before = (await r.stub.storageUsage(r.bind(null))).breakdown.messages;
    await settle(r);
    const hot = await sqlIn(r.stub, "SELECT seq FROM ledger_messages ORDER BY seq");
    const fences = await sqlIn(r.stub, "SELECT * FROM archive_segments ORDER BY segment");
    expect(hot.length).toBeLessThanOrEqual(LIMITS.maxMessages / 2);
    expect(fences.reduce((sum, fence) => sum + (fence["records"] as number), 0) + hot.length)
      .toBe(6);
    expect(fences.length).toBeGreaterThan(0);
    // The one number counts archived mail at its real bytes, as it counted it hot (D2).
    const after = (await r.stub.storageUsage(r.bind(null))).breakdown.messages;
    expect(Math.abs(after - before)).toBeLessThan(before * 0.2);
    // Every message is still the owner's: the archived ones read back from their segment.
    for (const [n, id] of ids.entries()) {
      const chain = await r.stub.mailChain(r.bind(null), { message_id: id, limit: 10 });
      expect(chain.messages.map((m) => [m.message_id, m.body, m.integrity]))
        .toEqual([[id, `m ${n}`, "ok"]]);
    }
    // No move is left in flight, and no upload behind one.
    expect(await sqlIn(r.stub, "SELECT * FROM archive_moves")).toEqual([]);
    expect(await sqlIn(r.stub, "SELECT * FROM file_intents")).toEqual([]);
  });

  it("holds a post at the hot ceiling as storage_busy, deleting nothing, until a move lands",
     async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    // Straight into the ledger, so no lane runs between: eight hot, every one acked.
    const refused = await r.inLedger(LIMITS, (ledger: Ledger) => {
      for (let i = 0; i < 8; i++) {
        ledger.post(null, room, `c${i}`, `body ${i}`, null);
        ledger.read(MUSE, 10);
        ledger.ack(MUSE, ledger.read(MUSE, 10).messages.at(-1)?.seq ?? i + 1);
      }
      try {
        ledger.post(null, room, "c8", "body 8", null);
        return null;
      } catch (error) {
        return (error as Error).message;
      }
    });
    expect(refused).toMatch(/^storage_busy: your storage is catching up/);
    expect(JSON.parse(refused!.slice(refused!.indexOf("{")))).toEqual({ retry_after_s: 30 });
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM ledger_messages")).toEqual([{ n: 8 }]);
    // The doors' post: busy alike, until the lane (rung by the post) has moved mail out.
    expect((await failure(() => r.post(null, room, { cmid: "c8", body: "body 8" }))).message)
      .toMatch(/^storage_busy:/);
    await settle(r);
    await r.post(null, room, { cmid: "c8", body: "body 8" });
    await settle(r);  // the post rang the lane: let any move it started land first
    // Both counts in one read, so no move can land between them (review M2).
    const [held] = await sqlIn(r.stub, "SELECT (SELECT COUNT(*) FROM ledger_messages) + " +
                                       "(SELECT coalesce(SUM(records), 0) FROM " +
                                       "archive_segments) AS n");
    expect(held!["n"]).toBe(9);
  });

  it("lets a reply name an archived parent, and the chain reads it with the reply",
     async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const [first] = await sentAndAcked(r, MUSE, room, 6);
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
    // A forged parent id is no parent.
    expect((await failure(() => r.post(MUSE, room, { cmid: "r2", body: "x",
                                                     parent: `msg_${"a".repeat(32)}` })))
      .message).toBe("parent is not a message to this sender in this room");
  });

  it("pages a chain of large bodies by their bytes, archived and hot, never all at once",
     async () => {
    const r = await rig(ARCHIVING);
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
    await sentAndAcked(r, MUSE, room, 4);
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                       root.message_id)).toEqual([]);  // archived
    // Each page one 5 MiB body (two pass `READ_PAGE_BYTES`), its cursor going on.
    const pages: string[][] = [];
    for (let after = 0, more = true; more && pages.length < 10;) {
      const page = await r.stub.mailChain(r.bind(null), { message_id: third.message_id,
                                                          limit: 10, after });
      pages.push(page.messages.map((m) => m.message_id));
      expect(page.messages.length).toBeLessThanOrEqual(1);
      expect(page.messages.every((m) => m.body?.length === 5 * 1024 * 1024)).toBe(true);
      ({ after, more } = page);
    }
    expect(pages.flat()).toEqual([root.message_id, second.message_id, third.message_id]);
  }, 120_000);

  it("aborts a move whose batch changed between its units, the mail kept hot", async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const outcome = await r.inLedger(LIMITS, (ledger: Ledger) => {
      for (let i = 0; i < 4; i++) {
        ledger.post(null, room, `c${i}`, `body ${i}`, null);
      }
      ledger.read(MUSE, 10);
      ledger.ack(MUSE, 2);
      ledger.post(null, room, "c4", "body 4", null);
      const pick = ledger.archivePick()!;
      const batch = batchOf(ledger, pick.seqs)!;
      // Between the units: the agent deleted, its mail with it.
      ledger.remove(muse);
      try {
        ledger.archiveCommit(pick.seqs, batch.digest, [], [{ content_id: "e".repeat(64),
          fence: { first_key: 1, last_key: 2, first_at: 1, last_at: 1, records: 2, bytes: 1,
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
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // Their mail interleaved, so a segment holds both.
    const kept: string[] = [];
    const gone: string[] = [];
    for (let i = 0; i < 3; i++) {
      gone.push(...await sentAndAcked(r, MUSE, `dm:${muse}`, 1, `muse${i}`));
      kept.push(...await sentAndAcked(r, DOT, `dm:${dot}`, 1, `dot${i}`));
    }
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
});

describe("the export, version 3", () => {
  it("ships each segment as it is, and its rows read back as the mail before the move",
     async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const ids = await sentAndAcked(r, MUSE, `dm:${muse}`, 6);
    await settle(r);
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
      .toEqual([...ids].sort());
    for (const rows of archived) {
      const n = ids.indexOf(rows["ledger_messages"]!["message_id"] as string);
      expect(rows["message_bodies"]).toMatchObject({ body: `m ${n}`, content_id: null });
      expect(rows["ledger_recipients"]).toMatchObject({ agent_id: MUSE_ID(rows), unacked: 0 });
    }
  });
});

describe("segments never overlap in key range (review H1)", () => {
  it("are refused by the schema when two would", async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    await sentAndAcked(r, MUSE, `dm:${muse}`, 6);
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
    await insert(10_000, 10_001);  // past every key: disjoint, taken
    expect(await overlaps(r)).toEqual([]);
  }, 60_000);

  it("stay disjoint, and every message readable by its chain, through two deletions " +
     "(the reviewer's case)", async () => {
    const r = await rig(ARCHIVING);
    const eve = Object.freeze({ client: "e".repeat(64), family: "f".repeat(64) });
    const x = await r.register(MUSE, "Muse");
    const y = await r.register(DOT, "Dot");
    const a = await r.register(eve, "Eve");
    const who = (seq: number) => seq === 1 ? [MUSE, x] as const
      : seq === 23 ? [DOT, y] as const : [eve, a] as const;
    const ids: string[] = [];
    for (let seq = 1; seq <= 28; seq++) {
      const [origin, agent] = who(seq);
      const posted = await r.post(null, `dm:${agent}`, { body: `m${seq}`, cmid: `c${seq}` });
      ids.push(posted.message_id);
      const page = await r.read(origin);
      await r.ack(origin, page.messages.at(-1)!.seq);
      if (seq % 2 === 0) {
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
    for (let seq = 2; seq <= 28; seq++) {
      if (seq !== 23) {
        expect(await chained(r, ids[seq - 1]!), `seq ${seq}`).toBe(`m${seq}`);
      }
    }
    for (const seq of [1, 23]) {
      expect((await failure(() => r.stub.mailChain(r.bind(null), {
        message_id: ids[seq - 1]!, limit: 10 }))).message).toBe("no such message held");
    }
  }, 120_000);

  it("merges a straggler (acked after the mail around it moved) into the segment its key " +
     "falls in", async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // Seq 1 to Muse, left unread; Dot's mail after it is acked and moves.
    const first = await r.post(null, `dm:${muse}`, { body: "late", cmid: "late" });
    await sentAndAcked(r, DOT, `dm:${dot}`, 6, "d");
    await settle(r);
    const before = await sqlIn(r.stub, "SELECT first_key, last_key FROM archive_segments " +
                                       "ORDER BY first_key");
    expect(before.length).toBeGreaterThan(0);
    expect(before[0]!["first_key"]).toBeGreaterThan(1);
    // Muse acks it now: it is below the archive's last key, so it merges, never overlaps.
    await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    await sentAndAcked(r, DOT, `dm:${dot}`, 3, "e");  // over half the ceiling again
    await settle(r);
    expect(await overlaps(r)).toEqual([]);
    const held = await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                             first.message_id);
    expect(held).toEqual([]);
    expect(await chained(r, first.message_id)).toBe("late");
  }, 60_000);
});

describe("a merge's byte budget (reviews N1 and N3)", () => {
  it("merges large late-acked mail a budget at a pass, never all at once, every message kept",
     async () => {
    const big = env.BIGARCHIVING as unknown as DurableObjectNamespace<OwnerDO>;
    const bigLimits = { ...LEDGER_LIMITS, maxMessages: 12, maxUnacked: 6, archiveRecords: 6 };
    const r = await rig(big);
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // Five messages near 7 MiB each to Muse, left unread; Dot's small mail after them moves.
    const large: string[] = [];
    for (let i = 0; i < 5; i++) {
      large.push((await r.post(null, `dm:${muse}`, {
        cmid: `big${i}`, body: `${i}`.padEnd(7 * 1024 * 1024, "x") })).message_id);
    }
    await sentAndAcked(r, DOT, `dm:${dot}`, 7, "d");
    for (let i = 0; i < 20; i++) {
      await drainDue(r.stub);
    }
    const [{ n: segments }] = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM " +
                                                  "archive_segments") as [{ n: number }];
    expect(segments).toBe(1);
    // Muse acks them now (a read pages at 8 MiB of bodies, so a page each): five stragglers,
    // about 35 MiB, inside that segment's keys.
    for (let i = 0; i < 5; i++) {
      await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    }
    const pick = await r.inLedger(bigLimits, (ledger: Ledger) => ledger.archivePick());
    expect(pick!.into).not.toBeNull();
    expect(pick!.seqs).toHaveLength(4);  // 28 MiB within MOVE_BYTES (32 MiB); the fifth waits
    // Past half the ceiling until both merges have run: each pass merges one budget.
    await sentAndAcked(r, DOT, `dm:${dot}`, 8, "e");
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
    for (const [n, id] of large.entries()) {
      expect((await chained(r, id))?.length).toBe(7 * 1024 * 1024);
      expect((await chained(r, id))?.[0]).toBe(`${n}`);
    }
  }, 180_000);
});

describe("an export across a move (review H2)", () => {
  it("replays exactly the owner at E by v3's reading: segments' rows after the log",
     async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    const ids = await r.inLedger(LIMITS, (ledger: Ledger) => {
      const sent: string[] = [];
      for (let i = 0; i < 6; i++) {
        sent.push(ledger.post(null, `dm:${muse}`, `c${i}`, `body ${i}`, null).message_id);
        ledger.read(MUSE, 10);
        ledger.ack(MUSE, i + 1);
      }
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
      const ledger = ledgerOn(gate.storage as unknown as DurableObjectStorage, LIMITS);
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
      await files.upload(begun.intent_id, chunks);
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
        const parts = await (env.FILES as ChunkStore).getChunks(space.ns, space.data_key,
          shas.map((sha) => ({ sha256: sha as string })));
        return Buffer.concat(parts.map((part) => Buffer.from(part))).toString("base64");
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
    expect(bodies).toEqual(ids.map((_id, i) => `body ${i}`).sort());
  }, 60_000);
});

describe("a body of control characters (review N3)", () => {
  it("moves as its own bytes, reads back byte-exact by its chain, and exports whole",
     async () => {
    const r = await rig(ARCHIVING);
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
    const seq = (await r.inLedger(LIMITS, (ledger: Ledger) =>
      ledger.seqOf(posted.message_id)))!;
    const holding = fences.find((fence) => (fence["first_key"] as number) <= seq
      && seq <= (fence["last_key"] as number));
    expect(holding).toBeDefined();
    // The segment is the body's own bytes and its lines', never its escapes.
    expect(holding!["bytes"] as number).toBeLessThan(BODY_BYTES_MAX + 64 * 1024);
    expect(await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                       posted.message_id)).toEqual([]);
    expect((await chained(r, posted.message_id)) === body).toBe(true);
    const { archived } = await exportArchived(r);
    const exported = archived.find((rows) =>
      rows["ledger_messages"]!["message_id"] === posted.message_id);
    expect(exported!["message_bodies"]!["body"] === body).toBe(true);
    expect(exported!["message_bodies"]).toMatchObject({ body_bytes: BODY_BYTES_MAX,
                                                       content_id: null });
  }, 120_000);
});

describe("the worst merge (review N3)", () => {
  it("merges a straggler into pack's largest piece, every body control characters, intact",
     async () => {
    const big = env.BIGARCHIVING as unknown as DurableObjectNamespace<OwnerDO>;
    const r = await rig(big);
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    const MIB = 1024 * 1024;
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
    for (let i = 0; i < 3; i++) {
      await r.post(null, `dm:${dot}`, { cmid: `u${i}`, body: `u ${i}` });
      await r.post(null, `dm:${muse}`, { cmid: `a${i}`, body: `a ${i}` });
      await drain(MUSE);
    }
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
    const seqOf = (id: string) => r.inLedger(LEDGER_LIMITS, (ledger: Ledger) =>
      ledger.seqOf(id)!);
    await drain(DOT, await seqOf(k2.message_id));
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
    await drain(DOT, await seqOf(k2b.message_id));
    await moved(2);
    const after = await segments();
    expect(after).toHaveLength(2);
    expect(after.every((fence) => (fence["bytes"] as number) >= 8 * MIB)).toBe(true);
    expect(await overlaps(r)).toEqual([]);
    for (const [id, body] of [[k1.message_id, near], [k2.message_id, cap],
                              [k2b.message_id, cap], [k3.message_id, near]] as const) {
      expect((await chained(r, id)) === body).toBe(true);
    }
  }, 300_000);
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

  it("are counted in storage as a pass begins: passes killed with their isolate still tell",
     async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    // Three passes begun that never ended or said why (an isolate killed for memory runs no
    // catch): the count is the row's, not the isolate's.
    await sqlIn(r.stub, "UPDATE ledger_usage SET archive_fails = 3, archive_error = NULL");
    await sentAndAcked(r, MUSE, `dm:${muse}`, 6);
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
    const diverging = env.DIVERGING as unknown as DurableObjectNamespace<OwnerDO>;
    const r = await rig(diverging);
    const muse = await r.register(MUSE);
    await r.inLedger(LIMITS, (ledger: Ledger) => {
      for (let i = 0; i < 6; i++) {
        ledger.post(null, `dm:${muse}`, `c${i}`, `body ${i}a`, null);
        ledger.read(MUSE, 10);
        ledger.ack(MUSE, i + 1);
      }
    });
    await r.stub.mailboxAck(r.bind(MUSE), { through: 6 });  // rings the schedule
    for (let pass = 0; pass < 4; pass++) {
      await passAgain(r);
    }
    // Every pass's second pass made other bytes than its first hashed: R2 refused each
    // chunk, nothing moved, and the owner was told once, with the error as it was said.
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_segments"))
      .toEqual([{ n: 0 }]);
    // Every message still hot and whole (its row, its body, its acked recipient), and no
    // upload left begun: each failed pass aborted its intents.
    expect(await sqlIn(r.stub, "SELECT b.body FROM ledger_messages m JOIN message_bodies b " +
                               "ON b.message_id = m.message_id JOIN ledger_recipients t ON " +
                               "t.message_id = m.message_id AND t.unacked = 0 ORDER BY m.seq"))
      .toEqual(Array.from({ length: 6 }, (_, i) => ({ body: `body ${i}a` })));
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
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    await r.inLedger(LIMITS, (ledger: Ledger) => {
      for (let i = 0; i < 6; i++) {
        ledger.post(null, `dm:${muse}`, `c${i}`, `body ${i}`, null);
        ledger.read(MUSE, 10);
        ledger.ack(MUSE, i + 1);
      }
    });
    // The oldest acked message's hash no longer matches its body: every move fails.
    await sqlIn(r.stub, "UPDATE ledger_messages SET body_sha256 = ? WHERE seq = 1",
                "0".repeat(64));
    await r.stub.mailboxAck(r.bind(MUSE), { through: 6 });  // rings the schedule
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
    await sqlIn(r.stub, "UPDATE ledger_messages SET body_sha256 = ? WHERE seq = 1",
                createHash("sha256").update("body 0").digest("hex"));
    await passAgain(r);
    await settle(r);
    expect(await sqlIn(r.stub, "SELECT archive_fails AS n FROM ledger_usage"))
      .toEqual([{ n: 0 }]);
    expect(await notices(r)).toHaveLength(1);
  });
});

describe("the tail's seal at the production limits (review N4)", () => {
  it("counts records' own bytes, never their trailer shares: a sealed segment holds the least",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const seen = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage, LEDGER_LIMITS);
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
  }, 300_000);
});

describe("a full owner's archive (review M1)", () => {
  it("still drains: a move may place past the figure, since it frees what it places",
     async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    await r.inLedger(LIMITS, (ledger: Ledger) => {
      for (let i = 0; i < 8; i++) {
        ledger.post(null, `dm:${muse}`, `c${i}`, `body ${i}`, null);
        ledger.read(MUSE, 10);
        ledger.ack(MUSE, i + 1);
      }
    });
    // The owner's own figure: exactly what is stored, so no segment fits beside the mail.
    const { stored } = await r.stub.storageUsage(r.bind(null)).then((usage) =>
      ({ stored: usage.used_bytes }));
    const { generation, policy } = await r.stub.policyRead(r.bind(null));
    await r.stub.policyWrite(r.session(null), {
      expected_generation: generation,
      policy: { ...policy, files: { max_bytes: stored } } as never });
    await r.stub.mailboxAck(r.bind(MUSE), { through: 8 });  // rings the schedule
    await settle(r);
    const [moved] = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_segments");
    expect(moved!["n"]).toBeGreaterThan(0);
    const after = (await r.stub.storageUsage(r.bind(null))).used_bytes;
    expect(after).toBeLessThanOrEqual(stored);
    expect(await sqlIn(r.stub, "SELECT archive_blocked AS b FROM ledger_usage"))
      .toEqual([{ b: 0 }]);
  });

  it("makes a post at the hot ceiling storage_full, the one wall, when a move cannot land",
     async () => {
    const r = await rig(ARCHIVING);
    const muse = await r.register(MUSE);
    await r.inLedger(LIMITS, (ledger: Ledger) => {
      for (let i = 0; i < 8; i++) {
        ledger.post(null, `dm:${muse}`, `c${i}`, `body ${i}`, null);
        ledger.read(MUSE, 10);
        ledger.ack(MUSE, i + 1);
      }
    });
    // A move refused for the figure leaves the archive blocked (the lane's own mark).
    await sqlIn(r.stub, "UPDATE ledger_usage SET archive_blocked = 1");
    const refused = await failure(() => r.post(null, `dm:${muse}`, { cmid: "c8",
                                                                     body: "body 8" }));
    expect(refused.message).toMatch(/^storage_full: your storage is full/);
    expect(storageFullOf(refused.message)).toMatchObject({ limit_bytes: expect.any(Number) });
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
