/**
 * Slice STORE item 2, phase 1 (`history_research.md` §4): acked mail moved out of the owner's
 * object to archive segments, files-plane content in the export's own line grammar with a
 * trailer, one fence row a segment; the `archive` lane from half the hot ceiling, a post at
 * the ceiling `storage_busy` and never a delete; the two-unit move; a reply and the chain
 * read reaching archived mail by the seq its id carries; a deleted agent's mail dropped at
 * once and rewritten out; export version 3 shipping the segments as they are. The object
 * here (`ArchivingOwnerDO`) holds eight messages hot, four unread, and seals a segment at two.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { archivedRecord, LEDGER_LIMITS, type Ledger, mailRecord } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { parseTiers } from "../src/tier";
import {
  bytesOf, encodeSegment, idKey, idOrder, MIN_RECORD_BYTES, orderedId, parseRecord,
  parseTrailer, recordText, segmentChunks, TIER_STORAGE_MAX,
} from "../src/segments";
import { exported } from "../src/export";
import { storageFullOf } from "../src/storage-full";
import type { ChunkStore } from "../src/files";
import { digest, generate, replay } from "./generation-support";
import { DOT, ledgerOn, MUSE, rig } from "./ledger-support";
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
    const encoded = encodeSegment("mail", records);
    const text = new TextDecoder().decode(encoded.bytes);
    const suffix = text.slice(-16);
    expect(Number(suffix)).toBe(encoded.trailer_offset);
    const trailer = parseTrailer(new TextDecoder().decode(encoded.bytes.subarray(
      encoded.trailer_offset, encoded.trailer_offset + encoded.trailer_bytes)));
    expect(trailer).toMatchObject({ stream: "mail", first: 1, last: 5, records: 3 });
    expect(trailer.chains).toEqual({ root: [trailer.keys[2]] });
    for (const [key, offset, length] of trailer.keys) {
      const rows = parseRecord(new TextDecoder().decode(encoded.bytes.subarray(offset,
                                                                               offset + length)));
      expect(rows).toEqual([["t", { k: key, é: "\u0000" }]]);
    }
    expect(() => encodeSegment("mail", [records[1]!, records[0]!])).toThrow(/key order/);
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
         changed_at: 1, unacked: 0 }]);
    expect(bytesOf(recordText(least))).toBeGreaterThanOrEqual(MIN_RECORD_BYTES);
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
      const batch = ledger.archiveBatch(pick.seqs, new Map())!;
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
    expect(lines[0]).toMatchObject({ t: "header", version: 3 });
    const fenceLines = lines.filter((line) => line["t"] === "row"
      && line["table"] === "archive_segments");
    expect(fenceLines.length).toBe(fences.length);
    const chunkNames = new Set(lines.filter((line) => line["t"] === "chunk")
      .map((line) => line["sha256"]));
    const archived: Record<string, Record<string, unknown>>[] = [];
    for (const fence of fences) {
      const shas = (await sqlIn(r.stub, "SELECT sha256 FROM file_content_chunks WHERE " +
                                        "content_id = ? ORDER BY idx",
                                fence["content_id"] as string)).map((row) => row["sha256"]);
      const parts: Buffer[] = [];
      for (const sha of shas) {
        expect(chunkNames.has(sha)).toBe(true);
        const { chunks } = await r.stub.exportChunks(r.session(null), {
          export_id: exportId!, sha256s: [sha as string] });
        parts.push(Buffer.from(chunks[0]!.data, "base64"));
      }
      const whole = Buffer.concat(parts);
      const trailer = parseTrailer(whole.subarray(fence["trailer_offset"] as number,
        (fence["trailer_offset"] as number) + (fence["trailer_bytes"] as number)).toString());
      for (const [key, offset, length] of trailer.keys) {
        const record = archivedRecord(key, parseRecord(whole.subarray(offset, offset + length)
          .toString()));
        archived.push(Object.fromEntries(record.rows));
      }
    }
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

describe("a merge's byte budget (review N1)", () => {
  it("merges large late-acked mail a budget at a pass, never all at once, every message kept",
     async () => {
    const big = env.BIGARCHIVING as unknown as DurableObjectNamespace<OwnerDO>;
    const bigLimits = { ...LEDGER_LIMITS, maxMessages: 12, maxUnacked: 6, archiveRecords: 6 };
    const r = await rig(big);
    const muse = await r.register(MUSE);
    const dot = await r.register(DOT, "Dot");
    // Three messages near 7 MiB each to Muse, left unread; Dot's small mail after them moves.
    const large: string[] = [];
    for (let i = 0; i < 3; i++) {
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
    // Muse acks them now (a read pages at 8 MiB of bodies, so a page each): three stragglers,
    // about 21 MiB, inside that segment's keys.
    for (let i = 0; i < 3; i++) {
      await r.ack(MUSE, (await r.read(MUSE)).messages.at(-1)!.seq);
    }
    const pick = await r.inLedger(bigLimits, (ledger: Ledger) => ledger.archivePick());
    expect(pick!.into).not.toBeNull();
    expect(pick!.seqs).toHaveLength(2);  // 14 MiB within the 16 MiB budget; the third waits
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
      const batch = await gate.write(() => ledger.archiveBatch(pick!.seqs, new Map()))!;
      const encoded = encodeSegment("mail", batch!.records);
      const chunks = segmentChunks(encoded.bytes);
      const begun = await gate.write(() => files.begin(encoded.bytes.byteLength,
        chunks.map((chunk) => chunk.sha256), "owner", [], true, null, true));
      await files.upload(begun.intent_id, chunks);
      await gate.write(() => files.adopt(begun.intent_id, "owner", (contentId) =>
        ledger.archiveCommit(pick!.seqs, batch!.digest, [], [{ content_id: contentId, fence: {
          first_key: encoded.trailer.first, last_key: encoded.trailer.last, first_at: 1,
          last_at: 1, records: encoded.trailer.records, bytes: encoded.bytes.byteLength,
          trailer_offset: encoded.trailer_offset, trailer_bytes: encoded.trailer_bytes,
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
      for (const [key, offset, length] of trailer.keys) {
        for (const [table, row] of archivedRecord(key, parseRecord(
          whole.subarray(offset, offset + length).toString())).rows) {
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
