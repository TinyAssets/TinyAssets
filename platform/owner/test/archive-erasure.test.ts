/**
 * A deleted agent's archived mail (slice STORE item 2, phase 1): dropped at once, then
 * rewritten out of its segments by the lane, a bounded rewrite a pass, keeping the rest
 * byte-exact and disjoint; deletions held busy past `maxAgentRows` purges waiting; and mail
 * still moved during a long purge. Each owner runs production's ceilings
 * (`archive-support.ts`).
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { BODY_BYTES_MAX } from "../../contract/src/limits";
import { STORAGE_BUSY_RETRY_S } from "../../contract/src/refusal";
import { LEDGER_LIMITS, type Ledger } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { MOVE_RECORDS } from "../src/segments";
import {
  chained, EVE, MIB, overlaps, sealed, seeded, sentAndAcked, seqOf, settle,
} from "./archive-support";
import { DOT, MUSE, origin, registerOn, type Rig } from "./ledger-support";
import { drainDue, failure, spaceOf } from "./support";

const { maxAgentRows } = LEDGER_LIMITS;

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

describe("the archive lane", () => {
  it("keeps a segment a purge's rewrite makes again byte for byte, its content still held",
     { tags: ["large"] }, async () => {
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
});

describe("an agent's deletion", () => {
  it("drops its archived mail at once, and the lane rewrites it out, keeping the rest",
     { tags: ["large"] }, async () => {
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
     "words, deleting nothing, and lands once the lane has caught up",
     { tags: ["large"] }, async () => {
    const r = await seeded();
    const muse = await r.register(MUSE);
    const [first] = await sentAndAcked(r, MUSE, `dm:${muse}`, 4);
    await sealed(r, MUSE, `dm:${muse}`);
    await settle(r);
    expect((await sqlIn(r.stub, "SELECT * FROM archive_segments")).length).toBeGreaterThan(0);
    // A segment stands, so each deletion leaves a purge waiting: an agent registered and
    // deleted maxAgentRows times, its rows never more than one.
    const refused = await r.inLedger((ledger: Ledger, stores) => {
      for (let n = 0; n < maxAgentRows; n++) {
        ledger.remove(registerOn(stores, origin(1), { command_center_id: "alpha",
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
                         `from the archive; try again in ${STORAGE_BUSY_RETRY_S} seconds.`);
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

describe("segments never overlap in key range (review H1)", () => {
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
