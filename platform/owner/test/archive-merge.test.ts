/**
 * Stragglers merged into the archive (slice STORE item 2, phase 1): mail acked after the mail
 * around it moved is merged into the segment its key falls in, a byte budget at a pass, even
 * at the worst merge, and no two segments ever overlap in key range. Each owner runs
 * production's ceilings (`archive-support.ts`).
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { sqlIn } from "../../ci/workerd";
import { BODY_BYTES_MAX } from "../../contract/src/limits";
import type { Ledger } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { MOVE_BYTES } from "../src/segments";
import {
  archivedBodies, chained, MIB, overlaps, sealed, seeded, sentAndAcked, seqOf, settle,
  unballast,
} from "./archive-support";
import { DOT, MUSE } from "./ledger-support";
import { drainDue, failure } from "./support";

describe("segments never overlap in key range (review H1)", () => {
  it("are refused by the schema when two would", { tags: ["large"] }, async () => {
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

  it("merges a lone straggler (acked after the mail around it moved) into the segment its " +
     "key falls in: due by itself, with no seal's worth behind it",
     { tags: ["large"] }, async () => {
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
