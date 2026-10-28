/**
 * Owner-wide bounds on the ledger's growth (the Python's owed L5), new in this port.
 *
 * Each runs the ledger on the object's own storage with some bounds changed
 * (`inLedger`), and checks that the counters the bounds read agree with the rows they
 * count. Unacked mail is reserved per mailbox inside the ceilings, so eviction always
 * finds a message to take: the last test drives a long random exchange to show it.
 */
import { describe, expect, it } from "vitest";

import {
  checkLimits, clock, LEDGER_LIMITS, type Ledger, type LedgerLimits, type Origin, OWNER,
} from "../src/ledger";
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

/** Small mailboxes that fit `maxMessages` exactly: `agents` agents of `each`, owner `own`. */
function tight(agents: number, each: number, own: number): Partial<LedgerLimits> {
  return { maxAgents: agents, maxAgentRows: agents + 2, maxAgentUnacked: each,
           maxOwnerUnacked: own, maxSenderUnacked: own, maxMessages: agents * each + own };
}

describe("the ledger's limits", () => {
  it("fit every enabled agent's mailbox and the owner's inside the ceilings", () => {
    expect(checkLimits(LEDGER_LIMITS)).toBe(LEDGER_LIMITS);
    const { maxAgents, maxAgentUnacked, maxAgentUnackedBytes, maxOwnerUnacked,
            maxOwnerUnackedBytes, maxMessages, maxBodyBytes } = LEDGER_LIMITS;
    expect(maxAgents * maxAgentUnacked + maxOwnerUnacked).toBeLessThanOrEqual(maxMessages);
    expect(maxAgents * maxAgentUnackedBytes + maxOwnerUnackedBytes)
      .toBeLessThanOrEqual(maxBodyBytes);
    for (const [bad, reason] of [
      [{ maxMessages: 9_999 }, "the mailboxes' messages exceed maxMessages"],
      [{ maxAgentUnackedBytes: 600 * 1024 }, "the mailboxes' bytes exceed maxBodyBytes"],
      [{ maxAgents: 1_001, maxAgentUnacked: 1 }, "maxAgents exceeds maxAgentRows"],
    ] as const) {
      expect(() => checkLimits({ ...LEDGER_LIMITS, ...bad })).toThrow(reason);
    }
  });

  it("refuses to build a ledger over limits whose mailboxes do not fit", async () => {
    const r = rig();
    const refused = await failure(() => r.inLedger({ maxMessages: 9_999 }, () => "built"));
    expect(refused.message).toMatch(/invalid ledger limits: the mailboxes' messages exceed/);
    expect(await r.inLedger({}, () => "built")).toBe("built");
  });
});

describe("the ledger's bounds", () => {
  it("refuses a full mailbox's posts only, and evicts the oldest acked message",
     async () => {
    const r = rig();
    const seqs = await r.inLedger(tight(1, 2, 1), (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      ledger.post(null, room, "c1", "body 1", null);
      ledger.post(null, room, "c2", "body 2", null);
      expect(refusal(() => ledger.post(null, room, "c3", "body 3", null)))
        .toBe("this agent's mailbox is full");
      ledger.post(MUSE, room, "m1", "to the owner", null);  // the owner's mailbox is its own
      expect(refusal(() => ledger.post(MUSE, room, "m2", "again", null)))
        .toBe("this agent's unacked mail to the owner is full");
      ledger.read(MUSE, 10);
      ledger.ack(MUSE, 1);
      ledger.post(null, room, "c3", "body 3", null);  // evicts seq 1 only
      ledger.ack(MUSE, 4);
      ledger.post(null, room, "c4", "body 4", null);  // and then seq 2
      return ledger.read(MUSE, 10).messages.map((m) => m.seq);
    });
    expect(seqs).toEqual([5]);  // a seq is never reused
    expect((await r.table("message_bodies")).map((b) => b["body"]))
      .toEqual(["to the owner", "body 3", "body 4"]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("evicts an acked message past an older one still unread, never the unread one",
     async () => {
    const r = rig();
    const bodies = await r.inLedger(tight(1, 2, 1), (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      ledger.post(null, room, "c1", "unread, and oldest", null);
      ledger.post(MUSE, room, "m1", "to the owner", null);
      ledger.post(null, room, "c2", "unread too", null);  // the ledger is at its ceiling
      ledger.ack(null, 2);
      ledger.post(MUSE, room, "m2", "evicts m1, the only acked message", null);
      return ledger.read(MUSE, 10).messages.map((m) => m.body);
    });
    expect(bodies).toEqual(["unread, and oldest", "unread too"]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("holds one agent to its share of the owner's mailbox in bytes too", async () => {
    const r = rig();
    await r.inLedger({ maxSenderUnackedBytes: 10 }, (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      ledger.post(MUSE, room, "m1", "x".repeat(6), null);
      expect(refusal(() => ledger.post(MUSE, room, "m2", "x".repeat(5), null)))
        .toBe("this agent's unacked mail to the owner is full");
      ledger.post(MUSE, room, "m2", "x".repeat(4), null);
      ledger.ack(null, 2);
      ledger.post(MUSE, room, "m3", "x".repeat(10), null);
    });
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("evicts by body bytes too", async () => {
    const r = rig();
    await r.inLedger({ maxBodyBytes: 10, maxAgents: 1, maxAgentUnackedBytes: 10,
                       maxOwnerUnackedBytes: 0 }, (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      ledger.post(null, room, "c1", "12345", null);
      ledger.post(null, room, "c2", "é234", null);  // five bytes, four characters
      expect(refusal(() => ledger.post(null, room, "c3", "1", null)))
        .toBe("this agent's mailbox is full");
      expect(refusal(() => ledger.post(MUSE, room, "m1", "1", null)))
        .toBe("the owner's mailbox is full");  // it holds no bytes here
      ledger.ack(MUSE, 1);
      ledger.post(null, room, "c3", "1", null);
    });
    const [usage, actual] = await counters(r);
    expect([usage, usage["body_bytes"]]).toEqual([actual, 6]);
  });

  it("holds one agent to its share of the owner's mailbox", async () => {
    const r = rig();
    const left = await r.inLedger({ maxSenderUnacked: 2, maxOwnerUnacked: 3 },
                                  (ledger: Ledger) => {
      const muse = ledger.register(MUSE, MCP, PULL).agent_id;
      const dot = ledger.register(DOT, MCP, { ...PULL, display_name: "Dot" }).agent_id;
      const third = ledger.register(origin(3), MCP, PULL).agent_id;
      ledger.post(MUSE, `dm:${muse}`, "m1", "one", null);
      ledger.post(MUSE, `dm:${muse}`, "m2", "two", null);
      expect(refusal(() => ledger.post(MUSE, `dm:${muse}`, "m3", "three", null)))
        .toBe("this agent's unacked mail to the owner is full");
      ledger.post(DOT, `dm:${dot}`, "d1", "dot's own share", null);
      expect(refusal(() => ledger.post(origin(3), `dm:${third}`, "t1", "x", null)))
        .toBe("the owner's mailbox is full");  // until the owner acks
      ledger.post(null, `dm:${muse}`, "o1", "the owner is never held to a share", null);
      const page = ledger.read(null, 10);
      ledger.ack(null, page.messages[0]!.seq);
      ledger.post(MUSE, `dm:${muse}`, "m3", "three", null);
      const share = new Map(ledger.agents(null).map((a) => [a.agent_id, a.unacked_to_owner]));
      return [share.get(muse), share.get(dot)];
    });
    expect(left).toEqual([2, 1]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("releases a disabled agent's unacked mail, so it no longer holds the ledger",
     async () => {
    const r = rig();
    const [muse, dot] = await r.inLedger({ ...tight(1, 3, 1), maxSenderUnacked: 1 },
                                         (ledger: Ledger) => {
      const a = ledger.register(MUSE, MCP, PULL).agent_id;
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${a}`, `c${n}`, `body ${n}`, null);  // never read nor acked
      }
      ledger.disable(a, "gone");
      const b = ledger.register(DOT, MCP, { ...PULL, display_name: "Dot" }).agent_id;
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${b}`, `d${n}`, `body ${n}`, null);  // evicts as it needs
      }
      ledger.post(DOT, `dm:${b}`, "r1", "the agent's own post", null);
      return [a, b];
    });
    const messages = await r.table("ledger_messages");
    expect(messages.map((m) => [m["sender_id"], m["room_id"]])).toEqual([
      [OWNER, `dm:${dot}`], [OWNER, `dm:${dot}`], [OWNER, `dm:${dot}`], [dot, `dm:${dot}`]]);
    // A re-enabled agent reads what eviction has left of its mail.
    await r.inLedger({ ...tight(2, 3, 1), maxSenderUnacked: 1 }, (ledger: Ledger) => {
      ledger.register(MUSE, MCP, PULL);
      expect(ledger.read(MUSE, 10).messages).toEqual([]);
      expect(ledger.agents(null).map((a) => a.agent_id).sort()).toEqual([muse, dot].sort());
    });
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("deletes an agent with its mail, wakes, rings and receipts", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    const dot = await r.register(DOT, "Dot");
    await r.subscribe(MUSE, muse);
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
    expect((await r.table("ledger_wakes")).map((w) => [w["agent_id"], w["kind"]]))
      .toEqual([[muse, "revoke"]]);  // its callbacks are still to be erased
    expect(await r.table("ledger_rings")).toEqual([]);
    // Its row stays as a tombstone until that revoke settles, listed to no one.
    expect((await r.table("outside_agents")).map((a) => [a["agent_id"], a["disabled_reason"],
                                                         a["deleted_at"] !== null]))
      .toEqual([[muse, "deleted", true], [dot, null, false]]);
    expect((await r.stub.outsideAgents(r.bind(null))).agents.map((a) => a.agent_id))
      .toEqual([dot]);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
    expect((await failure(() => r.stub.deleteAgent(r.session(null), { agent_id: muse })))
      .message).toBe("agent not found");
    await r.tick();
    expect(r.sender.revoked).toEqual([[muse, 1]]);
    expect((await r.table("outside_agents")).map((a) => a["agent_id"])).toEqual([dot]);
  });

  it("bounds pending revokes by the registry: a deleted agent's tombstone counts",
     async () => {
    const r = rig();
    const people = Array.from({ length: 30 }, (_, n) => origin(n));
    const [ids, refused] = await r.inLedger({ maxAgents: 3, maxAgentRows: 3 },
                                            (ledger: Ledger) => {
      const made: string[] = [];
      let full = 0;
      for (const who of people) {  // 30 register, subscribe and delete cycles
        try {
          const id = ledger.register(who, MCP, RINGS).agent_id;
          ledger.subscriber(who, "alpha", id);
          ledger.remove(id);
          made.push(id);
        } catch (error) {
          expect((error as Error).message).toBe("the owner's agent registry is full");
          full++;
        }
      }
      // A tombstone is no agent: no door reaches it.
      for (const act of [() => ledger.disable(made[0], "x"), () => ledger.remove(made[0]),
                         () => ledger.setWakeLimits(made[0], 12, 60)]) {
        expect(refusal(act)).toBe("agent not found");
      }
      expect(refusal(() => ledger.post(null, `dm:${made[0]}`, "c1", "x", null)))
        .toBe("room is not an enabled agent's dm");
      expect(ledger.agents(null)).toEqual([]);
      // Its identity registers again as itself, its generation kept.
      const again = ledger.register(people[0]!, MCP, RINGS);
      expect([again.agent_id, again.subscription, again.revoked_through, again.deleted_at])
        .toEqual([made[0], 1, 1, null]);
      return [made, full] as const;
    });
    expect([ids.length, refused]).toEqual([3, 27]);
    expect((await r.table("ledger_wakes")).filter((w) => w["kind"] === "revoke")).toHaveLength(3);
    await r.tick();  // the doorbell erases them, and the tombstones go
    expect(r.sender.revoked.map(([id]) => id).sort()).toEqual([...ids].sort());
    expect((await r.table("outside_agents")).map((a) => a["agent_id"])).toEqual([ids[0]]);
    await r.inLedger({ maxAgents: 3, maxAgentRows: 3 }, (ledger: Ledger) => {
      ledger.register(people[29]!, MCP, PULL);  // room again
    });
    const [usage, actual] = await counters(r);
    expect([usage, usage["agents"]]).toEqual([actual, 2]);
  });

  it("schedules no revoke for an agent that never subscribed, and leaves no row", async () => {
    const r = rig();
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
    const r = rig();
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
        .toBe("the owner's agent registry is full");
      ledger.remove(muse);
      ledger.register(origin(3), MCP, PULL);
    });
    const [usage, actual] = await counters(r);
    expect([usage, usage["agents"], usage["enabled_agents"]]).toEqual([actual, 2, 1]);
  });

  it("answers a retried ack after its mail was evicted, and dedupes only what it holds",
     async () => {
    const r = rig();
    await r.inLedger({ ...tight(1, 1, 1), maxSenderUnacked: 1 }, (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      const sent = ledger.post(null, room, "c1", "hello", null);
      expect(ledger.post(null, room, "c1", "hello", null)).toEqual(sent);  // held: the same
      ledger.ack(MUSE, 1);
      ledger.post(MUSE, room, "m1", "one", null);
      ledger.ack(null, 2);
      ledger.post(MUSE, room, "m2", "two", null);  // evicts seq 1, Muse's only mail
      expect(ledger.ack(MUSE, 1)).toEqual({ reader: expect.any(String), through: 1 });
      // The declared idempotency window: once evicted, the same id is a new message.
      expect(ledger.post(null, room, "c1", "hello", null).message_id).not.toBe(sent.message_id);
    });
  });

  it("keeps the newest receipts only", async () => {
    const r = rig();
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
    const r = rig();
    const now = clock();
    await r.inLedger({ maxSettledWakes: 2 }, (ledger: Ledger) => {
      const muse = ledger.register(MUSE, MCP, RINGS).agent_id;
      ledger.setWakeLimits(muse, 12, 0);
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
    expect((await r.table("ledger_wakes")).map((w) => [w["kind"], w["state"]]))
      .toEqual([["wake", "suppressed"]]);
    const [usage, actual] = await counters(r);
    expect([usage, usage["settled_wakes"]]).toEqual([actual, 1]);
  });

  it("keeps every counter equal to its rows through a busy exchange", async () => {
    const r = rig();
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

  it("always finds room by eviction through a long random exchange", async () => {
    const r = rig();
    const limits = { ...tight(3, 2, 4), maxSenderUnacked: 2, maxAgentRows: 4,
                     maxBodyBytes: 3 * 40 + 60, maxAgentUnackedBytes: 40,
                     maxOwnerUnackedBytes: 60, maxSenderUnackedBytes: 40 };
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
        "this agent's mailbox is full", "the owner's mailbox is full",
        "this agent's unacked mail to the owner is full", "room is not an enabled agent's dm",
        "this outside identity is not one enabled agent",
        "the owner's enabled agents are at their limit", "the owner's agent registry is full",
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
          const message = (error as Error).message;
          if (!allowed.has(message)) {
            throw error;  // the invariant's own loud error among them
          }
          refusals.set(message, (refusals.get(message) ?? 0) + 1);
        }
      }
      return refusals;
    });
    // The exchange really pressed every mailbox, and evicted many times over.
    for (const reason of ["this agent's mailbox is full", "the owner's mailbox is full",
                          "this agent's unacked mail to the owner is full"]) {
      expect(seen.get(reason) ?? 0).toBeGreaterThan(0);
    }
    const [posted] = await r.sql("SELECT seq FROM sqlite_sequence WHERE name = 'ledger_messages'");
    expect(posted!["seq"]).toBeGreaterThan(10 * limits.maxMessages!);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
    expect(usage["messages"]).toBeLessThanOrEqual(limits.maxMessages!);
    expect(usage["body_bytes"]).toBeLessThanOrEqual(limits.maxBodyBytes);
  });
});
