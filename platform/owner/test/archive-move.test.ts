/**
 * The `archive` lane's moves: acked mail moved out from half the hot ceiling, its bytes counted the
 * same; the two-unit move, aborted when its batch changed between them; its units held on the
 * isolate's pool; a body of control characters moved as its own bytes; the lane's failures counted
 * and told; and a full owner's archive, whose move may place past the figure, and whose post at the
 * hot ceiling is `storage_full` when no move can land. Each owner runs production's ceilings
 * (`archive-support.ts`).
 */
import { createHash } from "node:crypto";

import { env } from "cloudflare:workers";
import { describe, expect, it, onTestFinished } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { BODY_BYTES_MAX } from "../../contract/src/limits";
import { OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, ownerSlots } from "../src/holds";
import { refusalOf } from "../../contract/src/refusal";
import { SLOT_WAIT_MS } from "../../contract/src/limits";
import { LEDGER_LIMITS, type Ledger } from "../src/ledger";
import { MoveTable } from "../src/segments";
import {
  chained, exportArchived, sealed, seeded, type Seeded, sentAndAcked, seqOf, settle, unballast,
} from "./archive-support";
import { ballast, MUSE, type Rig, rig } from "./ledger-support";
import { drainDue, failure, gateFor, setFiles, spaceOf } from "./support";

const { maxMessages } = LEDGER_LIMITS;

describe("the archive lane", () => {
  it("moves acked mail out from half the hot ceiling, its bytes counted the same, and the " +
     "chain read finds it", { tags: ["large"] }, async () => {
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
    // The one number counts archived mail at its real bytes, as it counted it hot.
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

  it("holds its move's units on the isolate's pool; refused busy at once, the lane holds " +
     "with no failure counted, tells the owner once at the hot ceiling, and moves once there " +
     "is room", { tags: ["large"] }, async () => {
    const r = await rig();
    onTestFinished(() => r.letGo());
    const muse = await r.register(MUSE);
    // Three short of the hot ceiling, a segment's worth of it acked, three unread to spare.
    await r.inLedger((_ledger: Ledger, stores) => ballast(stores, 3, 3));
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

describe("a body of control characters", () => {
  it("moves as its own bytes, reads back byte-exact by its chain, and exports whole",
     { tags: ["large"] }, async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    // A body at the cap, every byte a control character (which JSON escapes six times over), placed
    // in R2 as any large body is.
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

describe("the archive lane's failures", () => {
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
     { tags: ["large"] }, async () => {
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
     { tags: ["large"] }, async () => {
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
     { tags: ["large"] }, async () => {
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

describe("a full owner's archive", () => {
  it("still drains: a move may place past the figure, since it frees what it places",
     { tags: ["large"] }, async () => {
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
    await setFiles(r, { max_bytes: stored });
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
    const agent = await r.inLedger((_ledger: Ledger, stores) => ballast(stores, 4, 0));
    // A move refused for the figure leaves the archive blocked (the lane's own mark).
    await sqlIn(r.stub, "UPDATE ledger_usage SET archive_blocked = 1");
    const refused = await failure(() => r.post(null, `dm:${agent}`, { cmid: "over",
                                                                      body: "over" }));
    expect(refused.message).toMatch(/^storage_full: your storage is full/);
    expect(refusalOf(refused)?.facts).toMatchObject({ limit_bytes: expect.any(Number) });
  });
});
