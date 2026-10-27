/**
 * S3b wakes and the W3 limits: one open wake per ringing recipient, sent once.
 *
 * Ported from the ledger parts of `tests/test_outside_wakes.py` (origin/main 8b415f569).
 * The Python's broker tick becomes one alarm pass (`runWakes`) on the owner's own object
 * with a scripted doorbell (`FakeSender`); its op journal becomes the wake row's
 * may-have-sent mark. The cross-owner fairness test has no counterpart: each object is
 * its own owner. The kill switch, event store and HTTP doorbell are later slices.
 */
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { clock, HOUR_S, retryAfter, WAKE_LEASE_S } from "../src/ledger";
import type { OwnerDO } from "../src/owner-do";
import { DOT, MUSE, rig, type Rig, wakeReceipts } from "./ledger-support";
import { failure } from "./support";

async function wakes(r: Rig) {
  return r.table("ledger_wakes");
}

async function states(r: Rig, table = "ledger_wakes"): Promise<string[]> {
  return (await r.table(table)).map((row) => row["state"] as string);
}

async function alarmAt(r: Rig): Promise<number | null> {
  return runInDurableObject(r.stub, (_i: OwnerDO, state) => state.storage.getAlarm());
}

describe("one open wake per ringing recipient", () => {
  it("opens one wake per burst, sends it once, and the next message opens the next",
     async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    const first = await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await r.post(null, `dm:${muse}`, { body: "again", cmid: "c2" });
    await r.post(null, `dm:${muse}`, { cmid: "c1" });  // a resend is the same message
    const [wake] = await wakes(r);
    expect([wake!["state"], wake!["agent_id"], wake!["attempts"]]).toEqual(["pending", muse, 0]);
    // W3: a new wake waits out the agent's batch window, so the burst rings once.
    expect(wake!["due"] as number - (wake!["created_at"] as number)).toBe(60);
    expect(await r.tick()).toBe(0);
    expect(r.sender.sent).toEqual([]);
    const later = clock() + 61;
    expect(await r.tick(later)).toBe(1);
    expect(await r.tick(later)).toBe(0);  // sent once, never twice
    const [target] = r.sender.sent;
    expect(target).toEqual({
      wake_id: wake!["wake_id"], claim_id: target!.claim_id, agent_id: muse,
      command_center_id: "alpha", client: MUSE.client, family: MUSE.family, cursor: 0,
      through: 2,
    });
    expect(await states(r)).toEqual(["sent"]);
    expect(new Set(await states(r, "ledger_recipients"))).toEqual(new Set(["woken"]));
    expect(await wakeReceipts(r)).toEqual(["wake_accepted"]);
    const [mail] = (await r.read(MUSE)).messages;
    expect(mail!.message_id).toBe(first.message_id);
    await r.post(null, `dm:${muse}`, { body: "later", cmid: "c3" });
    expect(await states(r)).toEqual(["sent", "pending"]);
  });

  it("opens none for the sender and never one for a pull agent", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    const dot = await r.register(DOT, "Dot", "pull");
    const first = await r.post(null, `dm:${muse}`, { cmid: "c1" });
    expect(await r.tick()).toBe(1);
    await r.read(MUSE);
    await r.post(MUSE, `dm:${muse}`, { body: "reply", cmid: "r1", parent: first.message_id });
    await r.post(null, `dm:${dot}`, { cmid: "d1" });
    expect((await wakes(r)).map((w) => [w["agent_id"], w["state"]])).toEqual([[muse, "sent"]]);
    expect(await r.tick(clock() + HOUR_S)).toBe(0);
    expect(r.sender.sent).toHaveLength(1);
    const refused = await failure(() => r.stub.mailboxSubscriber(
      r.bind(DOT), { command_center_id: "alpha", agent_id: dot }));
    expect(refused.message).toBe("not this caller's mcp_event agent");  // no doorbell
  });

  it("lets only the agent's own client subscribe to its mailbox", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    for (const [origin, agent] of [[DOT, muse], [MUSE, "agent_" + "0".repeat(16)],
                                   [null, muse]] as const) {
      const refused = await failure(() => r.stub.mailboxSubscriber(
        r.bind(origin), { command_center_id: "alpha", agent_id: agent }));
      expect([refused.name, refused.message])
        .toEqual(["Refused", "not this caller's mcp_event agent"]);
    }
    const wrongCenter = await failure(() => r.stub.mailboxSubscriber(
      r.bind(MUSE), { command_center_id: "beta", agent_id: muse }));
    expect(wrongCenter.name).toBe("Refused");
    expect(await r.stub.mailboxSubscriber(r.bind(MUSE),
                                          { command_center_id: "alpha", agent_id: muse }))
      .toEqual({ agent_id: muse });
  });

  it("suppresses a disabled agent's open wake, or one moved off mcp_event", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "revoked" });
    expect(await states(r)).toEqual(["suppressed"]);
    expect(JSON.parse((await wakes(r))[0]!["receipt_json"] as string))
      .toEqual({ reason: "disabled" });
    expect(await r.tick(clock() + HOUR_S)).toBe(0);
    expect(r.sender.sent).toEqual([]);
    await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`, { cmid: "c2" });
    await r.register(MUSE, "Muse", "pull");
    expect(await states(r)).toEqual(["suppressed", "suppressed"]);
    expect(JSON.parse((await wakes(r))[1]!["receipt_json"] as string))
      .toEqual({ reason: "delivery_method" });
  });

  it("suppresses a wake left open for a disabled agent at claim, and revokes", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "revoked" });
    // A wake left open by a direct store change, as a crash would.
    await r.sql("UPDATE ledger_wakes SET state = 'pending', settled_at = NULL");
    await r.sql("UPDATE ledger_usage SET settled_wakes = settled_wakes - 1");
    expect(await r.tick()).toBe(0);
    expect(r.sender.sent).toEqual([]);
    expect(r.sender.revoked).toEqual([muse]);
    expect(await states(r)).toEqual(["suppressed"]);
  });
});

describe("a wake is sent once and never replayed", () => {
  it("keeps the mail queued on a 429 and waits out Retry-After", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    r.sender.answers.push({ state: "rate_limited", status: 429, retry_after: 120 });
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    const now = clock() + 1;
    expect(await r.tick(now)).toBe(0);  // only an accepted wake counts
    expect(r.sender.sent).toHaveLength(1);
    const [wake] = await wakes(r);
    expect(wake!["state"]).toBe("rate_limited");
    expect(wake!["due"]).toBeCloseTo(now + 120, 6);
    await r.post(null, `dm:${muse}`, { body: "more", cmid: "c2" });  // extends; still waits
    expect(await wakes(r)).toEqual([wake]);
    expect(new Set(await states(r, "ledger_recipients"))).toEqual(new Set(["queued"]));
    expect(await r.tick(now + 60)).toBe(0);
    expect(r.sender.sent).toHaveLength(1);
    expect(await r.tick(now + 121)).toBe(1);
    expect(r.sender.sent).toHaveLength(2);
    expect(await states(r)).toEqual(["sent"]);
    expect(await wakeReceipts(r)).toEqual(["rate_limited", "wake_accepted"]);
  });

  it("clamps Retry-After to one second through one day; unreadable waits a minute", () => {
    expect([0.2, 1, 120, 10 ** 9, -5].map(retryAfter)).toEqual([1, 1, 120, 86_400, 1]);
    expect([null, undefined, Number.NaN, Number.POSITIVE_INFINITY].map(retryAfter))
      .toEqual([60, 60, 60, 60]);
  });

  it("settles an ambiguous send unknown and never replays it", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    r.sender.answers.push({ state: "unknown" });  // the connection dropped after it left
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    const now = clock() + 1;
    expect(await r.tick(now)).toBe(0);
    expect(r.sender.sent).toHaveLength(1);
    const [wake] = await wakes(r);
    expect([wake!["state"], wake!["marked"]]).toEqual(["unknown", 1]);
    expect(await wakeReceipts(r)).toEqual(["wake_sent"]);
    expect(await r.tick(now + HOUR_S)).toBe(0);
    expect(r.sender.sent).toHaveLength(1);
    expect(new Set(await states(r, "ledger_recipients"))).toEqual(new Set(["queued"]));

    /** A claim, perhaps the durable may-have-sent mark, then the object dies. */
    const crashMidSend = async (mark: boolean) => {
      await r.post(null, `dm:${muse}`, { body: crypto.randomUUID(), cmid: crypto.randomUUID() });
      const open = (await wakes(r)).find((w) => w["state"] === "pending")!;
      const at = clock() + 1;
      const claim = await r.inLedger({}, (ledger) => ledger.claim(open["wake_id"], at, false));
      expect(claim.outcome).toBe("claimed");
      if (mark && claim.outcome === "claimed") {
        await r.inLedger({}, (ledger) => ledger.mark(claim.target.wake_id, claim.target.claim_id));
      }
      const state = async () => (await wakes(r)).find((w) => w["wake_id"] === open["wake_id"])!
        ["state"];
      await r.tick(at + WAKE_LEASE_S / 2);
      expect(await state()).toBe("sending");  // inside the lease nothing moves
      await r.tick(at + WAKE_LEASE_S + 1);  // past it, the mark decides
      expect(await state()).toBe(mark ? "unknown" : "pending");
      return [open["wake_id"] as string, at] as const;
    };
    const [unsent, at1] = await crashMidSend(false);  // provably never sent: due again
    expect(await r.tick(at1 + WAKE_LEASE_S + 2)).toBe(1);
    expect(r.sender.sent).toHaveLength(2);
    const [maybe, at2] = await crashMidSend(true);  // a byte may have left: never replayed
    expect(await r.tick(at2 + HOUR_S)).toBe(0);
    expect(r.sender.sent).toHaveLength(2);
    const byId = Object.fromEntries((await wakes(r)).map((w) => [w["wake_id"], w["state"]]));
    expect([byId[unsent], byId[maybe]]).toEqual(["sent", "unknown"]);
  });

  it("leaves a sender that dies mid-send to its lease, then unknown", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    r.sender.answers.push("crash");
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    const now = clock() + 1;
    expect(await r.tick(now)).toBe(0);
    expect(await states(r)).toEqual(["sending"]);
    await r.tick(now + WAKE_LEASE_S + 1);
    expect(await states(r)).toEqual(["unknown"]);
    expect(await wakeReceipts(r)).toEqual(["wake_sent"]);
  });

  it("settles a callback refused before any connection failed, not unknown", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    r.sender.answers.push({ state: "failed", reason: "not_sent" });
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    expect(await r.tick(clock() + 1)).toBe(0);
    const [wake] = await wakes(r);
    expect([wake!["state"], JSON.parse(wake!["receipt_json"] as string)["reason"]])
      .toEqual(["failed", "not_sent"]);
    expect(await wakeReceipts(r)).toEqual(["failed"]);
  });

  it("carries no message body in any doorbell, wake row or receipt", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    const bodies: string[] = [];
    const start = clock();
    const answers = [{ state: "rate_limited", retry_after: 1 }, { state: "unknown" },
                     { state: "failed", status: 500 }, { state: "sent", status: 202 }] as const;
    for (const [step, answer] of answers.entries()) {
      r.sender.answers.push(answer);
      bodies.push("BODY-" + crypto.randomUUID());
      await r.post(null, `dm:${muse}`, { body: bodies.at(-1)!, cmid: `c${step}` });
      await r.tick(start + 100 * (step + 1));
      expect(r.sender.sent).toHaveLength(step + 1);
    }
    const stored = JSON.stringify([r.sender.sent, await r.table("ledger_wakes"),
                                   await r.table("ledger_receipts"),
                                   await r.table("ledger_rings")]);
    for (const body of bodies) {
      expect(stored).not.toContain(body);
    }
    for (const target of r.sender.sent) {
      expect(Object.keys(target).sort()).toEqual(["agent_id", "claim_id", "client",
        "command_center_id", "cursor", "family", "through", "wake_id"]);
    }
  });
});

describe("W3 limits", () => {
  it("caps an agent's rings per hour and pushes the wake past the cap", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event",
                                  { max_wakes_per_hour: 2, batch_window_s: 0 });
    const t = clock() + 1;
    for (const [n, at] of [[1, t], [2, t + 10]] as const) {
      await r.post(null, `dm:${muse}`, { cmid: `c${n}` });
      expect(await r.tick(at)).toBe(1);
    }
    await r.post(null, `dm:${muse}`, { cmid: "c3" });
    expect(await r.tick(t + 20)).toBe(0);
    expect(r.sender.sent).toHaveLength(2);
    const open = (await wakes(r)).find((w) => w["state"] === "pending")!;
    expect(open["due"]).toBeCloseTo(t + HOUR_S, 6);  // when the first ring leaves the hour
    expect((await r.table("ledger_recipients")).map((x) => x["state"]))
      .toEqual(["woken", "woken", "held_cap"]);
    expect(await r.tick(t + HOUR_S - 1)).toBe(0);
    expect(await r.tick(t + HOUR_S)).toBe(1);
    expect((await r.table("ledger_recipients")).map((x) => x["state"]))
      .toEqual(["woken", "woken", "woken"]);
    // The rings older than an hour are pruned at the next claim.
    expect((await r.table("ledger_rings")).map((x) => x["at"])).toEqual([t + 10, t + HOUR_S]);
  });

  it("pushes a new wake's due time by the agent's batch window", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 300 });
    await r.post(null, `dm:${muse}`);
    const [wake] = await wakes(r);
    expect(wake!["due"] as number - (wake!["created_at"] as number)).toBe(300);
    expect(await r.tick((wake!["due"] as number) - 1)).toBe(0);
    expect(await r.tick(wake!["due"] as number)).toBe(1);
  });
});

describe("the object's one alarm", () => {
  it("is set to the earliest live wake and cleared when none is left", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    expect(await alarmAt(r)).toBeNull();
    await r.post(null, `dm:${muse}`);
    const [wake] = await wakes(r);
    expect(await alarmAt(r)).toBe(Math.ceil((wake!["due"] as number) * 1000));
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "revoked" });
    expect(await alarmAt(r)).toBeNull();
  });

  it("without a doorbell holds open wakes pending and does not rearm for them", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`);
    expect(await alarmAt(r)).not.toBeNull();
    // Due now, with the alarm still set for the batch window's end; run it early.
    await r.sql("UPDATE ledger_wakes SET due = due - 120");
    expect(await runDurableObjectAlarm(r.stub)).toBe(true);
    expect(await states(r)).toEqual(["pending"]);  // held, not lost
    expect(await alarmAt(r)).toBeNull();
  });

  it("recovers a lapsed claim on the alarm and rearms for a claim's lease", async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.post(null, `dm:${muse}`);
    const [wake] = await wakes(r);
    const claim = await r.stub.claimWake(r.bind(null), { wake_id: wake!["wake_id"] as string });
    expect(claim.outcome).toBe("claimed");
    const [sending] = await wakes(r);
    expect(await alarmAt(r)).toBe(Math.ceil((sending!["due"] as number) * 1000));
    await r.sql("UPDATE ledger_wakes SET due = due - ?", WAKE_LEASE_S + 1);
    await runDurableObjectAlarm(r.stub);
    // A claim made over RPC is marked at once: lapsed, it is unknown, never replayed.
    expect(await states(r)).toEqual(["unknown"]);
    expect(await alarmAt(r)).toBeNull();
  });
});

describe("the wake RPC door", () => {
  it("claims a due wake under a lease and settles it only by the claim that holds it",
     async () => {
    const r = rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    const later = await r.register(DOT, "Dot", "mcp_event");
    await r.post(null, `dm:${muse}`);
    await r.post(null, `dm:${later}`, { cmid: "c2" });
    const { wakes: due } = await r.stub.dueWakes(r.bind(null), { limit: 16 });
    expect(due.map((w) => [w.agent_id, w.state])).toEqual([[muse, "pending"]]);
    const notDue = (await wakes(r)).find((w) => w["agent_id"] === later)!["wake_id"] as string;
    expect(await r.stub.claimWake(r.bind(null), { wake_id: notDue }))
      .toEqual({ outcome: "not_due" });
    const claim = await r.stub.claimWake(r.bind(null), { wake_id: due[0]!.wake_id });
    if (claim.outcome !== "claimed") {
      throw new Error("expected a claim");
    }
    expect(claim.target.cursor).toBe(0);
    expect(await r.stub.claimWake(r.bind(null), { wake_id: due[0]!.wake_id }))
      .toEqual({ outcome: "not_due" });  // one claim at a time
    const settle = (claimId: string, outcome: unknown) => r.stub.settleWake(
      r.bind(null), { wake_id: due[0]!.wake_id, claim_id: claimId, outcome });
    expect(await settle("claim_stale", { state: "sent" })).toEqual({ settled: false });
    for (const bad of [{ state: "pending" }, { state: "sent", body: "x" },
                       { state: "sent", status: 99 }, { state: "failed", reason: "Not Sent" },
                       { state: "rate_limited", retry_after: "120" }]) {
      expect((await failure(() => settle(claim.target.claim_id, bad))).message)
        .toBe("invalid wake outcome");
    }
    expect(await settle(claim.target.claim_id, { state: "sent", status: 202 }))
      .toEqual({ settled: true });
    expect(await settle(claim.target.claim_id, { state: "sent" })).toEqual({ settled: false });
    expect((await wakes(r)).map((w) => w["state"])).toEqual(["sent", "pending"]);
  });

  it("is the owner's own doorbell's, never an outside agent's", async () => {
    const r = rig();
    for (const call of [() => r.stub.dueWakes(r.bind(MUSE), { limit: 1 }),
                        () => r.stub.claimWake(r.bind(MUSE), { wake_id: "wake_x" }),
                        () => r.stub.settleWake(r.bind(MUSE), {
                          wake_id: "wake_x", claim_id: "claim_x", outcome: { state: "sent" } })]) {
      const refused = await failure(call);
      expect([refused.name, refused.message])
        .toEqual(["Refused", "only the owner's doorbell claims and settles wakes"]);
    }
    expect((await failure(() => r.stub.dueWakes(r.bind(null), { limit: 17 }))).message)
      .toBe("limit is 1 to 16");
  });
});
