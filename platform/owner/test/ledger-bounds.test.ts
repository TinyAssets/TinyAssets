/**
 * Owner-wide bounds on the ledger's growth (the Python's owed L5), new in this port.
 *
 * Each runs the ledger on the object's own storage with some bounds changed
 * (`inLedger`), and checks that the counters the bounds read agree with the rows they
 * count. Mail is bounded by two technical ceilings and no mailbox's count (slice STORE item
 * 2): too much unread mail owner-wide is `ledger_full`, and the hot ceiling `storage_busy`
 * while the archive lane moves acked mail out; nothing is ever evicted (`archive-mail.test.ts`
 * covers the lane).
 */
import { describe, expect, it } from "vitest";

import { checkLimits, LEDGER_LIMITS, type Ledger, type LedgerLimits, OWNER } from "../src/ledger";
import { clock } from "../src/clock";
import type { Origin } from "../../contract/src/binding";
import { PLACE_BYTES } from "../../contract/src/limits";
import { counters, DOT, MCP, MUSE, origin, rig } from "./ledger-support";
import { failure } from "./support";

const PULL = { command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
               recipe_id: null };
const RINGS = { ...PULL, delivery_method: "mcp_event" };

function refusal(act: () => unknown): string {
  try {
    act();
  } catch (error) {
    return (error as Error).message;
  }
  throw new Error("expected a refusal");
}

/** Small ceilings: `unread` messages unread owner-wide, `hot` held hot, a segment of one. */
function tight(unread: number, hot: number, agents = 3): Partial<LedgerLimits> {
  return { maxAgents: agents, maxAgentRows: agents + 2, maxUnacked: unread, archiveRecords: 1,
           maxMessages: hot };
}

/** `ledger_full`, naming the readers holding the most unread mail (`most`). */
const FULL = (n: number, most: string) => `ledger_full: too much mail is unread (${n} message${
  n === 1 ? " waits" : "s wait"} for their readers; the most for ${most}); reading and ` +
  "acking mail makes room";
const ANY_FULL = new RegExp("^ledger_full: too much mail is unread \\(\\d+ messages? waits? " +
  "for their readers; the most for .+\\); reading and acking mail makes room$");
const BUSY = "storage_busy: your storage is catching up, moving older mail out; try again in " +
  '30 seconds. {"retry_after_s":30}';

describe("the ledger's limits", () => {
  it("leave the hot ceiling a segment to move past the unread ceiling", () => {
    expect(checkLimits(LEDGER_LIMITS)).toBe(LEDGER_LIMITS);
    const L = LEDGER_LIMITS;
    expect([L.maxMessages, L.maxUnacked, L.archiveRecords, L.archiveBytes])
      .toEqual([50_000, 25_000, 25_000, 8 * 1024 * 1024]);
    expect(L.maxUnacked + L.archiveRecords).toBeLessThanOrEqual(L.maxMessages);
    for (const [bad, reason] of [
      [{ maxUnacked: L.maxMessages }, "the unread ceiling leaves the hot ceiling no segment"],
      [{ archiveRecords: 0 }, "the unread ceiling leaves the hot ceiling no segment"],
      [{ maxAgents: 1_001 }, "maxAgents exceeds maxAgentRows"],
    ] as const) {
      expect(() => checkLimits({ ...LEDGER_LIMITS, ...bad })).toThrow(reason);
    }
  });

  it("refuses to build a ledger over limits that leave no segment to move", async () => {
    const r = await rig();
    const refused = await failure(() => r.inLedger({ maxMessages: 9_999 }, () => "built"));
    expect(refused.message).toMatch(/invalid ledger limits: the unread ceiling leaves/);
    expect(await r.inLedger({}, () => "built")).toBe("built");
  });

  it("refuses a post, in plain words, when too much mail is unread, whoever it is to",
     async () => {
    const r = await rig();
    await r.inLedger(tight(2, 3), (ledger: Ledger) => {
      const muse = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      const dot = `dm:${ledger.register(DOT, MCP, { ...PULL, display_name: "Dot" }).agent_id}`;
      ledger.post(null, muse, "c1", "for muse", null);
      ledger.post(null, muse, "c2", "for muse too", null);
      // Dot's own mailbox is empty, but two messages wait for their reader.
      expect(refusal(() => ledger.post(null, dot, "c3", "for dot", null)))
        .toBe(FULL(2, "Muse (2)"));
      ledger.ack(MUSE, 1);
      ledger.post(null, dot, "c3", "for dot", null);
    });
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });
});

describe("the ledger's bounds", () => {
  it("holds a post at the hot ceiling as storage_busy, retryable, and never deletes",
     async () => {
    const r = await rig();
    const kept = await r.inLedger(tight(2, 3), (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      for (const n of [1, 2, 3]) {
        ledger.post(null, room, `c${n}`, `body ${n}`, null);
        ledger.read(MUSE, 10);
        ledger.ack(MUSE, n);
      }
      expect(ledger.archiveDue()).toBe(true);  // the archive lane's work, not a delete
      expect(refusal(() => ledger.post(null, room, "c4", "body 4", null))).toBe(BUSY);
      return ledger.read(MUSE, 10).messages.length;
    });
    expect(kept).toBe(0);  // all acked: past the cursor nothing is unread
    expect((await r.table("message_bodies")).map((b) => b["body"]))
      .toEqual(["body 1", "body 2", "body 3"]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("counts no mailbox: one reader may hold every unread message, and an ack frees room",
     async () => {
    const r = await rig();
    await r.inLedger(tight(3, 4), (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      for (const n of [1, 2, 3]) {
        ledger.post(null, room, `c${n}`, `body ${n}`, null);
      }
      expect(refusal(() => ledger.post(null, room, "c4", "body 4", null)))
        .toBe(FULL(3, "Muse (3)"));
      // Mail to the owner waits on the same one ceiling: no mailbox has its own.
      expect(refusal(() => ledger.post(MUSE, room, "m1", "to the owner", null)))
        .toBe(FULL(3, "Muse (3)"));
      ledger.ack(MUSE, 1);
      ledger.post(MUSE, room, "m1", "to the owner", null);
    });
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("counts no body bytes: a body's own size bounds no ceiling (ADR-035)", async () => {
    const r = await rig();
    await r.inLedger(tight(2, 3), (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      ledger.post(null, room, "c1", "x".repeat(PLACE_BYTES), null);
      ledger.post(null, room, "c2", "y".repeat(PLACE_BYTES), null);
      expect(refusal(() => ledger.post(null, room, "c3", "1", null))).toBe(FULL(2, "Muse (2)"));
    });
    // A segment's seal, not a bound: the one byte count the ledger keeps.
    expect(Object.keys(LEDGER_LIMITS).filter((key) => key.endsWith("Bytes")))
      .toEqual(["archiveBytes"]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("releases a disabled agent's unacked mail, so it no longer holds the unread ceiling, " +
     "and keeps it", async () => {
    const r = await rig();
    const [muse, dot] = await r.inLedger(tight(3, 7), (ledger: Ledger) => {
      const a = ledger.register(MUSE, MCP, PULL).agent_id;
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${a}`, `c${n}`, `body ${n}`, null);  // never read nor acked
      }
      ledger.disable(a, "gone");
      const b = ledger.register(DOT, MCP, { ...PULL, display_name: "Dot" }).agent_id;
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${b}`, `d${n}`, `body ${n}`, null);
      }
      return [a, b];
    });
    const messages = await r.table("ledger_messages");
    expect(messages.map((m) => m["room_id"])).toEqual([
      `dm:${muse}`, `dm:${muse}`, `dm:${muse}`, `dm:${dot}`, `dm:${dot}`, `dm:${dot}`]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("deletes an agent with its mail, wakes, subscriptions and receipts", async () => {
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
    expect((await r.table("ledger_recipients")).map((m) => m["agent_id"])).toEqual([dot]);
    expect((await r.table("ledger_receipts")).map((x) => [x["agent_id"], x["kind"], x["ref"]]))
      .toEqual([[dot, "read", toDot.message_id]]);
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
    await r.inLedger({}, (ledger: Ledger) => {
      const muse = ledger.register(MUSE, MCP, RINGS).agent_id;
      ledger.disable(muse, "paused");
      ledger.register(MUSE, MCP, RINGS);
      ledger.remove(muse);
    });
    expect(await r.table("ledger_wakes")).toEqual([]);
    expect(await r.table("outside_agents")).toEqual([]);
  });

  it("caps enabled agents, and registry rows until the owner deletes one", async () => {
    const r = await rig();
    await r.inLedger({ maxAgents: 1, maxAgentRows: 2 }, (ledger: Ledger) => {
      const muse = ledger.register(MUSE, MCP, PULL).agent_id;
      expect(refusal(() => ledger.register(DOT, MCP, PULL)))
        .toBe("the owner's enabled agents are at their limit");
      expect(ledger.register(MUSE, MCP, PULL).agent_id).toBe(muse);  // takes no new place
      ledger.disable(muse, "paused");
      const dot = ledger.register(DOT, MCP, PULL).agent_id;  // a disabled agent counts not
      expect(refusal(() => ledger.register(MUSE, MCP, PULL)))
        .toBe("the owner's enabled agents are at their limit");  // re-enabling counts
      ledger.disable(dot, "paused");
      expect(refusal(() => ledger.register(origin(3), MCP, PULL)))
        .toBe("the owner's agent registry is full; delete a disabled agent to make room");
      ledger.remove(muse);
      ledger.register(origin(3), MCP, PULL);
    });
    const [usage, actual] = await counters(r);
    expect([usage, usage["agents"], usage["enabled_agents"]]).toEqual([actual, 2, 1]);
  });

  it("answers a retried ack, and dedupes a resend of what it holds hot", async () => {
    const r = await rig();
    await r.inLedger(tight(1, 3), (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      const sent = ledger.post(null, room, "c1", "hello", null);
      expect(ledger.post(null, room, "c1", "hello", null)).toEqual(sent);  // held: the same
      ledger.ack(MUSE, 1);
      expect(ledger.ack(MUSE, 1)).toEqual({ reader: expect.any(String), through: 1 });
      expect(ledger.post(null, room, "c1", "hello", null)).toEqual(sent);  // acked, still hot
    });
  });

  it("keeps the newest receipts only", async () => {
    const r = await rig();
    await r.inLedger({ maxReceipts: 3 }, (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      for (const n of [1, 2, 3, 4]) {
        ledger.post(null, room, `c${n}`, `body ${n}`, null);
      }
      ledger.read(MUSE, 10);
      ledger.ack(MUSE, 4);
    });
    expect((await r.table("ledger_receipts")).map((x) => [x["seq"], x["kind"], x["ref"]]))
      .toEqual([[3, "read", expect.any(String)], [4, "read", expect.any(String)],
                [5, "ack", "4"]]);
  });

  it("keeps settled wakes for their retention and at most the ceiling", async () => {
    const r = await rig();
    const now = clock();
    await r.inLedger({ maxSettledWakes: 2 }, (ledger: Ledger) => {
      const muse = ledger.register(MUSE, MCP, RINGS).agent_id;
      ledger.setWakeLimits(muse, null, 0);
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${muse}`, `c${n}`, `body ${n}`, null, now + n);
        const [wake] = ledger.dueOpen(now + n, 1);
        const claim = ledger.claim(wake!.wake_id, now + n);
        if (claim.outcome !== "claimed") {
          throw new Error("expected a claim");
        }
        ledger.settle(wake!.wake_id, claim.target.claim_id, { state: "sent" }, now + n);
      }
    });
    expect((await r.table("ledger_wakes")).map((w) => w["settled_at"]))
      .toEqual([now + 2, now + 3]);
    await r.inLedger({}, (ledger: Ledger) => {
      // A week on, the next settle prunes them all but its own.
      const later = now + LEDGER_LIMITS.settledWakeRetentionS + 10;
      const [agent] = ledger.agents(null);
      ledger.post(null, `dm:${agent!.agent_id}`, "c4", "body 4", null, later);
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

  it("never deletes mail through a long random exchange: a post is held off in plain words",
     async () => {
    const r = await rig();
    const limits = { ...tight(4, 6), maxAgentRows: 4 };
    const seen = await r.inLedger(limits, (ledger: Ledger) => {
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
        "the owner's enabled agents are at their limit",
        "the owner's agent registry is full; delete a disabled agent to make room",
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
            ledger.register(who, MCP, PULL);
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
    expect(usage["messages"]).toBeLessThanOrEqual(limits.maxMessages!);
  });
});
