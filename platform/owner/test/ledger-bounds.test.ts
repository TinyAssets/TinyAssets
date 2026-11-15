/**
 * Owner-wide bounds on the ledger's growth (the Python's owed L5), new in this port.
 *
 * The ledger's decisions are pure functions of its counters and its ceilings, tested here
 * with small numbers. The object's own tests run the ledger at production's ceilings
 * (`LEDGER_LIMITS`), on its own storage (`inLedger`), and check that the counters the bounds
 * read agree with the rows they count. Mail is bounded by two technical ceilings and no
 * mailbox's count (slice STORE item 2): too much unread mail owner-wide is `ledger_full`,
 * and the hot ceiling `storage_busy` while the archive lane moves acked mail out; nothing is
 * ever evicted (`archive-move.test.ts` covers the lane; "the ledger at its production
 * limits", below, both ceilings in the object).
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import {
  archiveDue, hotFull, LEDGER_LIMITS, type Ledger, moveTake, OWNER, postRoom,
  sealable,
} from "../src/ledger";
import { clock } from "../src/clock";
import type { Origin } from "../../contract/src/binding";
import { STORAGE_BUSY_RETRY_S } from "../../contract/src/refusal";
import type { OwnerDO } from "../src/owner-do";
import { MOVE_BYTES, RECORD_TRAILER_BYTES } from "../src/segments";
import { ballast, counters, DOT, MUSE, origin, registerOn, rig } from "./ledger-support";
import { drainDue, failure, sqlIn, starterSeed, storesOn } from "./support";

const PULL = { command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
               recipe_id: null };
const RINGS = { ...PULL, delivery_method: "mcp_event" };
const { maxAgents, maxMessages, maxSettledWakes, maxUnacked } = LEDGER_LIMITS;

function refusal(act: () => unknown): string {
  try {
    act();
  } catch (error) {
    return (error as Error).message;
  }
  throw new Error("expected a refusal");
}

const ANY_FULL = new RegExp("^ledger_full: too much mail is unread \\(\\d+ messages wait " +
  "for their readers; the most for .+\\); reading and acking mail makes room$");
const BUSY = "storage_busy: your storage is catching up, moving older mail out; try again in " +
  '30 seconds. {"retry_after_s":30}';

describe("the ledger's limits", () => {
  it("leave the hot ceiling a segment to move past the unread ceiling", () => {
    const L = LEDGER_LIMITS;
    expect([L.maxMessages, L.maxUnacked, L.archiveRecords, L.archiveBytes])
      .toEqual([50_000, 25_000, 25_000, 8 * 1024 * 1024]);
    expect(L.maxUnacked + L.archiveRecords).toBeLessThanOrEqual(L.maxMessages);
    // A segment's seal, not a bound: the one byte count the ledger keeps (ADR-035).
    expect(Object.keys(LEDGER_LIMITS).filter((key) => key.endsWith("Bytes")))
      .toEqual(["archiveBytes"]);
  });
});

describe("the ledger's decisions, at small ceilings", () => {
  const L = { maxMessages: 8, maxUnacked: 4, archiveRecords: 2, archiveBytes: 1_000 };
  /** `messages` hot, `unacked` of them unread, the acked ones' lines `acked` bytes. */
  const usage = (messages: number, unacked: number, acked = 0) =>
    ({ messages, unacked, acked_bytes: acked });
  /** A record of `own` bytes past its trailer share, as its line counts it. */
  const line = (own: number) => own + RECORD_TRAILER_BYTES;

  it("hold a post off at too much unread mail first, then at the hot ceiling: owner-wide " +
     "counts alone, never a mailbox's or a body's bytes", () => {
    expect(postRoom(usage(3, 3), L)).toBeNull();
    expect(postRoom(usage(4, 4), L)).toBe("unread");
    expect(postRoom(usage(7, 0), L)).toBeNull();
    expect(postRoom(usage(8, 0), L)).toBe("hot");
    expect(postRoom(usage(8, 4), L)).toBe("unread");
    expect([hotFull(usage(7, 7), L), hotFull(usage(8, 0), L)]).toEqual([false, true]);
  });

  it("seal a segment at its records, or at its records' own bytes, their trailers out", () => {
    expect(sealable(usage(3, 2, line(999)), L)).toBe(false);
    expect(sealable(usage(3, 2, line(1_000)), L)).toBe(true);
    expect(sealable(usage(4, 2, 2 * RECORD_TRAILER_BYTES), L)).toBe(true);
    expect(sealable(usage(4, 4, 0), L)).toBe(false);
  });

  it("give the lane a move past half the hot ceiling alone: a straggler, or a seal", () => {
    let asked = 0;
    const straggler = (found: boolean) => () => {
      asked += 1;
      return found;
    };
    expect(archiveDue(usage(4, 0, line(5_000)), L, straggler(true))).toBe(false);
    expect(asked).toBe(0);  // at half, never asked
    expect(archiveDue(usage(5, 4), L, straggler(true))).toBe(true);
    expect(archiveDue(usage(5, 4), L, straggler(false))).toBe(false);
    expect(archiveDue(usage(5, 3), L, straggler(false))).toBe(true);
  });

  it("take a tail's records to the seal, and a merge's while it fits MOVE_BYTES, at least " +
     "one", () => {
    expect(moveTake([line(400), line(400), line(400), line(400)], null, L)).toBe(3);
    expect(moveTake([line(600), line(400), line(5)], null, L)).toBe(2);
    expect(moveTake([line(10), line(10)], null, L)).toBe(2);
    expect(moveTake([], null, L)).toBe(0);
    const room = (bytes: number) => ({ bytes: MOVE_BYTES - bytes });
    expect(moveTake([line(600), line(400), line(1)], room(1_000), L)).toBe(2);
    expect(moveTake([line(5), line(5)], room(0), L)).toBe(1);
    expect(moveTake([line(5), line(5)], { bytes: MOVE_BYTES + 1 }, L)).toBe(1);
  });
});

describe("the ledger's bounds", () => {
  it("releases a disabled agent's unacked mail from the unread count, and keeps it",
     async () => {
    const r = await rig();
    const [muse, dot] = await r.inLedger((ledger: Ledger, stores) => {
      const a = registerOn(stores, MUSE, PULL).agent_id;
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${a}`, `c${n}`, `body ${n}`, null);  // never read nor acked
      }
      ledger.disable(a, "gone");
      const b = registerOn(stores, DOT, { ...PULL, display_name: "Dot" }).agent_id;
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${b}`, `d${n}`, `body ${n}`, null);
      }
      return [a, b];
    });
    // What the unread ceiling reads holds Dot's three alone.
    expect(await r.sql("SELECT unacked FROM ledger_usage")).toEqual([{ unacked: 3 }]);
    const messages = await r.table("ledger_messages");
    expect(messages.map((m) => m["room_id"])).toEqual([
      `dm:${muse}`, `dm:${muse}`, `dm:${muse}`, `dm:${dot}`, `dm:${dot}`, `dm:${dot}`]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("deletes an agent with its mail, wakes and subscriptions", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    const dot = await r.register(DOT, "Dot");
    await r.subscribe(MUSE);
    const first = await r.post(null, `dm:${muse}`, { cmid: "o1" });
    await r.tick(clock() + 1);
    await r.read(MUSE);
    await r.ack(MUSE, 1);
    await r.post(MUSE, `dm:${muse}`, { cmid: "m1", body: "reply", parent: first.message_id });
    await r.post(null, `dm:${muse}`, { cmid: "o2" });  // an open wake
    await r.read(null);
    const toDot = await r.post(null, `dm:${dot}`, { cmid: "d1" });
    await r.read(DOT);
    const deleted = await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    expect(deleted).toEqual({ agent_id: muse, messages: 3 });
    expect((await r.table("ledger_messages")).map((m) => m["message_id"]))
      .toEqual([toDot.message_id]);
    expect((await r.table("message_bodies")).map((m) => m["message_id"]))
      .toEqual([toDot.message_id]);
    expect((await r.table("ledger_recipients")).map((m) => [m["agent_id"], m["message_id"],
                                                             m["read_at"] !== null]))
      .toEqual([[dot, toDot.message_id, true]]);
    expect((await r.table("ledger_readers")).map((x) => x["reader"]).sort())
      .toEqual([dot, OWNER].sort());
    expect(await r.table("ledger_wakes")).toEqual([]);
    expect(await r.sql("SELECT * FROM wake_callbacks")).toEqual([]);
    expect((await r.table("outside_agents")).map((a) => a["agent_id"])).toEqual([dot]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
    expect((await failure(() => r.stub.deleteAgent(r.session(null), { agent_id: muse })))
      .message).toBe("agent not found");
  });

  it("leaves no row for a deleted agent", async () => {
    const r = await rig();
    await r.inLedger((ledger: Ledger, stores) => {
      const muse = registerOn(stores, MUSE, RINGS).agent_id;
      ledger.disable(muse, "paused");
      registerOn(stores, MUSE, RINGS);
      ledger.remove(muse);
    });
    expect(await r.table("ledger_wakes")).toEqual([]);
    expect(await r.table("outside_agents")).toEqual([]);
  });

  it("caps enabled agents, and registry rows until the owner deletes one", async () => {
    const r = await rig();
    await r.inLedger((ledger: Ledger, stores) => {
      // The owner's own agents, its seed's and these, take the reserved mailboxes but one.
      for (let n = 0; n < maxAgents - 1 - starterSeed().agents.length; n++) {
        ledger.appear(`agent-${n}`);
      }
      const muse = registerOn(stores, MUSE, PULL).agent_id;
      expect(refusal(() => registerOn(stores, DOT, PULL)))
        .toBe("the owner's enabled agents are at their limit");
      expect(registerOn(stores, MUSE, PULL).agent_id).toBe(muse);  // takes no new place
      ledger.disable(muse, "paused");
      // A disabled agent frees its mailbox, but its row stays the registry's.
      expect(refusal(() => registerOn(stores, DOT, PULL)))
        .toBe("the owner's agent registry is full; delete a disabled agent to make room");
      expect(registerOn(stores, MUSE, PULL).agent_id).toBe(muse);  // enabled again
      ledger.disable(muse, "paused");
      ledger.remove(muse);
      registerOn(stores, DOT, PULL);
    });
    const [usage, actual] = await counters(r);
    expect([usage, usage["agents"], usage["enabled_agents"]])
      .toEqual([actual, maxAgents, maxAgents]);
  });

  it("answers a retried ack, and dedupes a resend of what it holds hot", async () => {
    const r = await rig();
    await r.inLedger((ledger: Ledger, stores) => {
      const room = `dm:${registerOn(stores, MUSE, PULL).agent_id}`;
      const sent = ledger.post(null, room, "c1", "hello", null);
      expect(ledger.post(null, room, "c1", "hello", null)).toEqual(sent);  // held: the same
      ledger.ack(MUSE, 1);
      expect(ledger.ack(MUSE, 1)).toEqual({ reader: expect.any(String), through: 1 });
      expect(ledger.post(null, room, "c1", "hello", null)).toEqual(sent);  // acked, still hot
    });
    expect(await r.sql("SELECT COUNT(*) AS n FROM ledger_messages")).toEqual([{ n: 1 }]);
  });

  it("keeps settled wakes for their retention and at most the ceiling", async () => {
    const r = await rig();
    const now = clock();
    await r.inLedger((ledger: Ledger, stores) => {
      const muse = registerOn(stores, MUSE, RINGS).agent_id;
      ledger.setWakeLimits(muse, null, 0);
      for (let n = 1; n <= maxSettledWakes + 1; n++) {
        ledger.post(null, `dm:${muse}`, `c${n}`, `body ${n}`, null, now + n);
        const [wake] = ledger.dueOpen(now + n, 1);
        const claim = ledger.claim(wake!.wake_id, now + n);
        if (claim.outcome !== "claimed") {
          throw new Error("expected a claim");
        }
        ledger.settle(wake!.wake_id, claim.target.claim_id, { state: "sent" }, now + n);
      }
    });
    expect(await r.sql("SELECT COUNT(*) AS n, MIN(settled_at) AS first FROM ledger_wakes"))
      .toEqual([{ n: maxSettledWakes, first: now + 2 }]);
    await r.inLedger((ledger: Ledger) => {
      // A week on, the next settle prunes them all but its own.
      const later = now + maxSettledWakes + 1 + LEDGER_LIMITS.settledWakeRetentionS + 10;
      const [agent] = ledger.agents(null);
      ledger.post(null, `dm:${agent!.agent_id}`, "c-later", "body later", null, later);
      ledger.disable(agent!.agent_id, "revoked", null, later);
    });
    expect((await r.table("ledger_wakes")).map((w) => w["state"])).toEqual(["suppressed"]);
    const [usage, actual] = await counters(r);
    expect([usage, usage["settled_wakes"]]).toEqual([actual, 1]);
  });

  it("keeps every counter equal to its rows through a busy exchange", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    const dot = await r.register(DOT, "Dot");
    for (const n of [1, 2, 3, 4, 5]) {
      const sent = await r.post(null, `dm:${muse}`, { body: `é ${n}`, cmid: `o${n}` });
      await r.post(null, `dm:${dot}`, { body: "x".repeat(n * 100), cmid: `d${n}` });
      await r.tick(clock() + n);
      await r.read(MUSE);
      await r.post(MUSE, `dm:${muse}`, { body: `reply ${n}`, cmid: `m${n}`,
                                         parent: sent.message_id });
    }
    const page = await r.read(null);
    await r.ack(null, page.messages.at(-1)!.seq);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
    expect((await r.table("outside_agents")).map((a) => a["unacked_to_owner"])).toEqual([0, 0]);
    expect((await r.table("ledger_messages")).filter((m) => m["sender_id"] === OWNER)
      .map((m) => m["unacked"])).toEqual([1, 1, 1, 1, 1, 1, 1, 1, 1, 1]);
    // An acked recipient row never holds its message again: the counters could not follow.
    expect((await failure(() => r.sql("UPDATE ledger_recipients SET unacked = 1 " +
                                      "WHERE unacked = 0"))).message)
      .toMatch(/an acked recipient never holds its message again/);
  });

  it("never deletes mail through a long random exchange at both ceilings: a post is held " +
     "off in plain words", { tags: ["large"] }, async () => {
    const r = await rig();
    try {
      const seen = await r.inLedger((ledger: Ledger, stores) => {
        ballast(stores, 4, 6);
        let seed = 7;
        const pick = (n: number) => {  // mulberry32: seeded, so every run is the same run
          seed = (seed + 0x6d2b79f5) | 0;
          let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
          t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
          return ((t ^ (t >>> 14)) >>> 0) % n;
        };
        const people: Origin[] = [MUSE, DOT, origin(3), origin(4), origin(5)];
        const refusals = new Map<string, number>();
        const allowed = new Set([
          "FULL", BUSY, "room is not an enabled agent's dm",
          "this outside identity is not one enabled agent",
          "agent not found", "ack is past this reader's mail",
        ]);
        const idOf = (who: Origin) => ledger.agents(who).find((a) => a.disabled_reason === null)
          ?.agent_id ?? ledger.agents(who)[0]?.agent_id ?? "agent_none";
        for (let step = 0; step < 1500; step++) {
          const who = people[pick(people.length)]!;
          const body = "x".repeat(1 + pick(20));
          const act = pick(8);
          try {
            if (act <= 1) {
              ledger.post(null, `dm:${idOf(who)}`, `o${step}`, body, null);
            } else if (act === 2) {
              ledger.post(who, `dm:${idOf(who)}`, `a${step}`, body, null);
            } else if (act <= 4) {
              const reader = act === 3 ? null : who;
              const page = ledger.read(reader, 10);
              if (page.messages.length && pick(2)) {
                ledger.ack(reader, page.messages.at(-1)!.seq);
              }
            } else if (act === 5) {
              registerOn(stores, who, PULL);
            } else if (act === 6) {
              ledger.disable(idOf(who), "paused");
            } else if (pick(4) === 0) {
              ledger.remove(idOf(who));
            }
          } catch (error) {
            const said = (error as Error).message;
            const message = ANY_FULL.test(said) ? "FULL" : said;
            if (!allowed.has(message)) {
              throw error;  // the invariant's own loud error among them
            }
            refusals.set(message, (refusals.get(message) ?? 0) + 1);
          }
        }
        return refusals;
      });
      // The exchange really pressed both ceilings, and deleted nothing to get past them.
      expect(seen.get("FULL") ?? 0).toBeGreaterThan(0);
      expect(seen.get(BUSY) ?? 0).toBeGreaterThan(0);
      const [usage, actual] = await counters(r);
      expect(usage).toEqual(actual);
      expect(usage["messages"]).toBeLessThanOrEqual(maxMessages);
    } finally {
      await r.letGo();
    }
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
    const filled = await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const count = counted(state.storage);
      const { ledger, gate } = storesOn({ id: state.id, storage: count.storage });
      return gate.write(() => {
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
    const seen = await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      const count = counted(state.storage);
      const { ledger, gate } = storesOn({ id: state.id, storage: count.storage });
      return gate.write(() => {
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
