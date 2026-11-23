/**
 * The archive: acked mail moved out of the owner's object to archive segments, files-plane content
 * in the export's own line grammar with a trailer, one fence row a segment. This file holds the
 * segment's record format: its encoding, its ids, a message's counted bytes, the tail's seal at
 * production's limits and the tier bound the fence rows set. The archive at production's ceilings
 * (`LEDGER_LIMITS`, `archive-support.ts` `seeded`) is tested by subject beside it: the lane's moves
 * (`archive-move.test.ts`), archived mail read back by the chain read and the export
 * (`archive-read.test.ts`), stragglers merged into segments (`archive-merge.test.ts`), and a
 * deleted agent's archived mail dropped and rewritten out (`archive-erasure.test.ts`). A segment
 * seals at 8 MiB of records, which one body at the cap reaches alone and two of 4 MiB together.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { PLACE_BYTES } from "../../contract/src/limits";
import { LEDGER_LIMITS, type Ledger, mailParts, mailRecord } from "../src/ledger";
import { nativeId } from "../../contract/src/binding";
import type { OwnerDO } from "../src/owner-do";
import { parseTiers, TIER_STORAGE_MAX } from "../../contract/src/control";
import {
  type Content, listed, makeChunk, MIN_RECORD_BYTES, MOVE_RECORDS,
  MoveTable,
  parseRecord,
  parseTrailer, planSegment, RECORD_TRAILER_BYTES, recordBytes, recordParts, recordRows,
  type SegmentRecord, type Source,
} from "../src/segments";
import { idKey, idOrder, orderedId } from "../src/ordered";
import { batchOf, hotSources, noRead } from "./archive-support";
import { MUSE, rig, dm } from "./ledger-support";
import { referenceSegment, storedContent, streamed } from "./segment-support";
import { storesOn, TIERS, tiersAt } from "./support";

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

  it("are streamed a chunk at a time to exactly the reference's bytes", async () => {
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

  it("are planned in a table that refuses a move past MOVE_RECORDS, loudly", () => {
    const table = new MoveTable();
    for (let n = 0; n < MOVE_RECORDS; n++) {
      table.push(n + 1, null, "dm:x", n, MIN_RECORD_BYTES, MoveTable.INLINE, -1, 0);
    }
    expect(table.count).toBe(MOVE_RECORDS);
    expect(() => table.push(MOVE_RECORDS + 1, null, "dm:x", 0, MIN_RECORD_BYTES,
                            MoveTable.INLINE, -1, 0)).toThrow(/past MOVE_RECORDS/);
  });

  it("hold a body raw, never escaped: a record is its body's bytes and its lines'", () => {
    // A body of 8 MiB of control characters, which JSON escapes six times over.
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
    const asked = (await r.post(null, dm(muse), { body: "hello" })).message_id;
    const posted = await exact(asked);
    const [mail] = (await r.read(MUSE)).messages;
    const read = await exact(asked);
    expect(read).toBeGreaterThan(posted);  // its read time, in its recipient row
    await r.ack(MUSE, mail!.seq);
    await r.post(MUSE, dm(muse), { body: "hi", cmid: "r", parent: asked });
    await exact(asked);  // replied, while acked
    const failed = await r.inLedger((ledger: Ledger) => {
      ledger.appear("main");
      const id = ledger.post(null, dm(nativeId("main")), "m", "x", null).message_id;
      ledger.undeliverable(id, nativeId("main"), "credential_refresh_failed");
      return id;
    });
    await exact(failed);
    // A placed body's record, as the move streams it (`mailParts`), holds no content id.
    const placed = (await r.post(null, dm(muse), { body: "p".repeat(PLACE_BYTES + 1),
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

describe("the tail's seal at the production limits", () => {
  it("counts records' own bytes, never their trailer shares: a sealed segment holds the least",
     { tags: ["large"] }, async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    const seen = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const { ledger, gate } = storesOn(state);
      const sql = state.storage.sql;
      const room = dm(muse);
      const post = (from: number, to: number) => {
        for (let n = from; n < to; n++) {
          ledger.post(null, room, `c${n}`, "x", null);
        }
        ledger.read(MUSE, 1);
        ledger.ack(MUSE, to);
      };
      // 11,999 small messages acked, then the real pick and batch.
      const { pick, table } = await gate.write(() => {
        post(0, 11_999);
        const picked = ledger.archivePick()!;
        return { pick: picked, table: batchOf(ledger, picked.seqs)!.table };
      });
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

describe("a tier's storage", () => {
  it("is held to TIER_STORAGE_MAX, the fence rows' bound: a larger tier needs larger segments",
     () => {
    const tier = (bytes: number) => tiersAt(TIERS.free.storage_bytes, bytes);
    expect(parseTiers(tier(TIER_STORAGE_MAX)).paid.storage_bytes).toBe(TIER_STORAGE_MAX);
    expect(() => parseTiers(tier(TIER_STORAGE_MAX + 1))).toThrow(/storage whole bytes up to/);
  });
});
