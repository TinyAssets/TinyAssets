/**
 * Owner-wide bounds on the ledger's growth (the Python's owed L5), new in this port.
 *
 * Each runs the ledger on the object's own storage with one bound lowered (`inLedger`),
 * and checks that the counters the bounds read agree with the rows they count.
 */
import { describe, expect, it } from "vitest";

import { clock, LEDGER_LIMITS, type Ledger, OWNER } from "../src/ledger";
import { counters, DOT, MCP, MUSE, rig } from "./ledger-support";

const PULL = { command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
               recipe_id: null };
const RINGS = { ...PULL, delivery_method: "mcp_event", batch_window_s: 0 };

function refusal(act: () => unknown): string {
  try {
    act();
  } catch (error) {
    return (error as Error).message;
  }
  throw new Error("expected a refusal");
}

describe("the ledger's bounds", () => {
  it("evicts the oldest acked message past the ceiling, and refuses when none is acked",
     async () => {
    const r = rig();
    const seqs = await r.inLedger({ maxMessages: 3 }, (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      for (const n of [1, 2, 3]) {
        ledger.post(null, room, `c${n}`, `body ${n}`, null);
      }
      expect(refusal(() => ledger.post(null, room, "c4", "body 4", null)))
        .toBe("the owner's ledger is full");
      ledger.read(MUSE, 10);
      ledger.ack(MUSE, 2);
      ledger.post(null, room, "c4", "body 4", null);  // evicts seq 1 only
      ledger.post(null, room, "c5", "body 5", null);  // and then seq 2
      expect(refusal(() => ledger.post(null, room, "c6", "body 6", null)))
        .toBe("the owner's ledger is full");
      return ledger.read(MUSE, 10).messages.map((m) => m.seq);
    });
    expect(seqs).toEqual([3, 4, 5]);  // a seq is never reused
    expect((await r.table("message_bodies")).map((b) => b["body"]))
      .toEqual(["body 3", "body 4", "body 5"]);
    expect(await r.table("ledger_recipients")).toHaveLength(3);
    const [usage, actual] = await counters(r);
    expect(usage).toEqual(actual);
  });

  it("evicts by body bytes too", async () => {
    const r = rig();
    await r.inLedger({ maxBodyBytes: 10 }, (ledger: Ledger) => {
      const room = `dm:${ledger.register(MUSE, MCP, PULL).agent_id}`;
      ledger.post(null, room, "c1", "12345", null);
      ledger.post(null, room, "c2", "é234", null);  // five bytes, four characters
      expect(refusal(() => ledger.post(null, room, "c3", "1", null)))
        .toBe("the owner's ledger is full");
      ledger.ack(MUSE, 1);
      ledger.post(null, room, "c3", "1", null);
    });
    const [usage, actual] = await counters(r);
    expect([usage, usage["body_bytes"]]).toEqual([actual, 6]);
  });

  it("holds one agent to its share of mail the owner has not acked", async () => {
    const r = rig();
    const left = await r.inLedger({ maxSenderUnacked: 2 }, (ledger: Ledger) => {
      const muse = ledger.register(MUSE, MCP, PULL).agent_id;
      const dot = ledger.register(DOT, MCP, { ...PULL, display_name: "Dot" }).agent_id;
      ledger.post(MUSE, `dm:${muse}`, "m1", "one", null);
      ledger.post(MUSE, `dm:${muse}`, "m2", "two", null);
      expect(refusal(() => ledger.post(MUSE, `dm:${muse}`, "m3", "three", null)))
        .toBe("this agent's unacked mail to the owner is full");
      ledger.post(DOT, `dm:${dot}`, "d1", "dot's own share", null);
      ledger.post(null, `dm:${muse}`, "o1", "the owner is never held to a share", null);
      const page = ledger.read(null, 10);
      ledger.ack(null, page.messages[0]!.seq);
      ledger.post(MUSE, `dm:${muse}`, "m3", "three", null);
      const share = new Map(ledger.agents().map((a) => [a.agent_id, a.unacked_to_owner]));
      return [share.get(muse), share.get(dot)];
    });
    expect(left).toEqual([2, 1]);
  });

  it("caps the registry, and a re-registration takes no new place", async () => {
    const r = rig();
    await r.inLedger({ maxAgents: 1 }, (ledger: Ledger) => {
      const muse = ledger.register(MUSE, MCP, PULL).agent_id;
      expect(refusal(() => ledger.register(DOT, MCP, PULL)))
        .toBe("the owner's agent registry is full");
      expect(ledger.register(MUSE, MCP, PULL).agent_id).toBe(muse);
    });
    const [usage, actual] = await counters(r);
    expect([usage, usage["agents"]]).toEqual([actual, 1]);
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
      for (const n of [1, 2, 3]) {
        ledger.post(null, `dm:${muse}`, `c${n}`, `body ${n}`, null);
        const [wake] = ledger.dueWakes(now + n, 1);
        const claim = ledger.claim(wake!.wake_id, now + n, false);
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
      ledger.post(null, `dm:${ledger.agents()[0]!.agent_id}`, "c4", "body 4", null);
      ledger.disable(ledger.agents()[0]!.agent_id, "revoked", later);
    });
    expect((await r.table("ledger_wakes")).map((w) => w["state"])).toEqual(["suppressed"]);
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
  });
});
