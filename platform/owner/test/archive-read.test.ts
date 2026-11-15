/**
 * Archived mail read back (slice STORE item 2, phase 1): a reply naming an archived parent;
 * the chain read reaching archived mail by the seq its id carries, paged to one answer each,
 * whole while the lane moves or merges under it; and export version 3 shipping the segments
 * as they are, replayed exactly across a move. Each owner runs production's ceilings
 * (`archive-support.ts`).
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it, onTestFinished } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { BODY_BYTES_MAX, READ_TRIES } from "../src/bodies";
import { RESULT_CHARS } from "../../contract/src/encoded";
import { PLACE_BYTES } from "../../contract/src/limits";
import { refusalOf } from "../../contract/src/refusal";
import type { Ledger } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { makeChunk, parseTrailer, planSegment, recordRows } from "../src/segments";
import { exported } from "../src/export";
import type { ChunkStore } from "../../contract/src/files";
import {
  batchOf, chained, EVE, exportArchived, hotSources, noRead, sealed, seeded, sentAndAcked,
  seqOf, settle, unballast,
} from "./archive-support";
import { digest, generate, replay } from "./generation-support";
import { MUSE, rig } from "./ledger-support";
import { drainDue, failure, gateFor, spaceOf, storesOn } from "./support";

/** The recipient a dm with an outside agent goes to from the owner: that agent. */
const MUSE_ID = (rows: Record<string, Record<string, unknown>>) =>
  (rows["ledger_messages"]!["room_id"] as string).slice(3);

describe("the archive lane", () => {
  it("lets a reply name an archived parent, and the chain reads it with the reply",
     { tags: ["large"] }, async () => {
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

  it("takes only a parent it does not hold as none: a parent read that fails throws",
     async () => {
    const r = await rig();
    const failed = await failure(() => runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const { archive } = storesOn(state);
      expect(archive.archivedParent({ parent_id: "not-a-message" })).toBeNull();
      // The read itself fails, as a storage fault would.
      state.storage.sql.exec("ALTER TABLE ledger_messages RENAME TO ledger_messages_gone");
      return archive.archivedParent({ parent_id: "not-a-message" });
    }));
    expect(failed.message).toMatch(/no such table: ledger_messages/);
  });

  it("posts a reply whose parent moved to the archive after its gate, its parent read again",
     { tags: ["large"] }, async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const [first] = await sentAndAcked(r, MUSE, room, 4);
    // The reply's body is placed, so its put to the files plane comes between the unit that
    // took its parent as hot and the unit that posts it: held there, the parent moves.
    const files = gateFor(env.FILES_GATE, await spaceOf(r.stub));
    await files.arm("putChunks", { hold: "before" });
    const body = "late reply".padEnd(PLACE_BYTES + 1, "x");
    const posting = r.post(MUSE, room, { cmid: "r1", body, parent: first! });
    try {
      await files.held("putChunks");
      await sealed(r, MUSE, room);
      await settle(r);
      expect(await sqlIn(r.stub, "SELECT 1 FROM ledger_messages WHERE message_id = ?",
                         first!)).toEqual([]);
    } finally {
      await files.release("putChunks");
    }
    const reply = await posting;
    const chain = await r.stub.mailChain(r.bind(null), { message_id: reply.message_id,
                                                         limit: 10 });
    expect([chain.root, chain.messages.map((m) => [m.message_id, m.body])]).toEqual([
      first, [[first, "m 0"], [reply.message_id, body]]]);
  });

  it("answers a read the archive moved under each of its reads storage_busy, again in a second",
     { tags: ["large"] }, async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const room = `dm:${muse}`;
    const [first] = await sentAndAcked(r, MUSE, room, 4);
    await sealed(r, MUSE, room);
    await settle(r);
    const mark = async () => JSON.stringify(await sqlIn(r.stub, "SELECT COUNT(*) AS n, " +
                                                        "MAX(segment) AS newest FROM archive_segments"));
    // Each read of the archived message held at the files plane while the lane moves more mail
    // into a new segment, and then its answer lost: read again, the archive having moved.
    const files = gateFor(env.FILES_GATE, await spaceOf(r.stub));
    await files.arm("getChunks", { hold: "before" });
    const reading = failure(() => r.stub.mailChain(r.bind(null), { message_id: first!,
                                                                    limit: 10 }));
    for (let read = 1; read <= READ_TRIES; read++) {
      await files.held("getChunks");
      const before = await mark();
      await sentAndAcked(r, MUSE, room, 4, `c${read}`);
      await sealed(r, MUSE, room);
      await settle(r);
      expect(await mark()).not.toBe(before);
      if (read < READ_TRIES) {
        await files.arm("getChunks", { hold: "before" });
      }
      await files.lose("getChunks");
    }
    const refused = refusalOf(await reading)!;
    expect([refused.code, refused.message, refused.retryAfter]).toEqual([
      "storage_busy", `storage_busy: the archive moved this mail under each of ${READ_TRIES} ` +
      "reads; try again in 1 second.", 1]);
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
     { tags: ["large"] }, async () => {
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
});

describe("the export, version 3", () => {
  it("ships each segment as it is, and its rows read back as the mail before the move",
     { tags: ["large"] }, async () => {
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
    const generated = await generate(r.stub, { afterUnit: async ({ gate, stores }) => {
      if (moved) {
        return;
      }
      moved = true;
      const { files, ledger } = stores;
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
    } });
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
