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
  parseTrailer, recordText, TIER_STORAGE_MAX,
} from "../src/segments";
import { DOT, MUSE, rig } from "./ledger-support";
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

/** Drive the object's lanes until the archive has nothing due. */
async function settle(r: Rig): Promise<void> {
  for (let i = 0; i < 20; i++) {
    await drainDue(r.stub);
    const [{ n }] = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM archive_moves") as
      [{ n: number }];
    const due = await runInDurableObject(r.stub, (_i: OwnerDO, state) =>
      state.storage.sql.exec<{ messages: number; unacked: number }>(
        "SELECT messages, unacked FROM ledger_usage").one());
    const purges = await sqlIn(r.stub, "SELECT 1 FROM archive_purges");
    if (n === 0 && purges.length === 0
        && !(due.messages > LIMITS.maxMessages / 2 && due.messages - due.unacked >= 2)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("the archive lane did not settle");
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
    const kept = await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM ledger_messages");
    const archived = await sqlIn(r.stub, "SELECT coalesce(SUM(records), 0) AS n " +
                                         "FROM archive_segments");
    expect((kept[0]!["n"] as number) + (archived[0]!["n"] as number)).toBe(9);
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
        ledger.archiveApply(pick.seqs, batch.digest, "e".repeat(64), {
          first_key: 1, last_key: 2, first_at: 1, last_at: 1, records: 2, bytes: 1,
          trailer_offset: 0, trailer_bytes: 1, members: "0".repeat(128) }, 1);
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

describe("a tier's storage", () => {
  it("is held to TIER_STORAGE_MAX, the fence rows' bound: a larger tier needs larger segments",
     () => {
    const tier = (bytes: number) => ({ free: { runs: 5, storage_bytes: 25e9 },
                                       paid: { runs: 10, storage_bytes: bytes } });
    expect(parseTiers(tier(TIER_STORAGE_MAX)).paid.storage_bytes).toBe(TIER_STORAGE_MAX);
    expect(() => parseTiers(tier(TIER_STORAGE_MAX + 1))).toThrow(/storage whole bytes up to/);
  });
});
