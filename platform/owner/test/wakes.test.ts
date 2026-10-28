/**
 * S3b wakes and the W3 limits: one open wake per ringing recipient, sent once.
 *
 * Ported from the ledger parts of `tests/test_outside_wakes.py` (origin/main 8b415f569).
 * The Python's broker tick becomes one alarm pass (`runWakes`) on the owner's own object
 * with a scripted doorbell (`FakeSender`); its op journal becomes the wake row's
 * may-have-sent mark. The cross-owner fairness test has no counterpart: each object is
 * its own owner. The kill switch, owner admission, event store and HTTP doorbell are the
 * doorbell slice's (openspec clean-sheet-platform tasks 7).
 */
import { evictDurableObject, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { clock, HOUR_S, type Ledger, outcome, retryAfter, WAKE_LEASE_S } from "../src/ledger";
import { runWakes, WAKE_RETRY_S } from "../src/wakes";
import type { OwnerDO } from "../src/owner-do";
import { DOT, ledgerOn, MCP, MUSE, origin, rig, type Rig, wakeReceipts } from "./ledger-support";
import { drainDue, failure, laneJobs } from "./support";

const RINGS = { command_center_id: "alpha", display_name: "Muse", delivery_method: "mcp_event",
                recipe_id: null };

async function wakes(r: Rig) {
  return r.table("ledger_wakes");
}

async function states(r: Rig, table = "ledger_wakes"): Promise<string[]> {
  return (await r.table(table)).map((row) => row["state"] as string);
}

/** Only the wake rows' states, leaving out revokes. */
async function wakeStates(r: Rig): Promise<string[]> {
  return (await wakes(r)).filter((w) => w["kind"] === "wake").map((w) => w["state"] as string);
}

/** A ringing agent with no batch window, registered and tuned in `ledger` itself. */
function ringing(ledger: Ledger, from = MUSE, perHour = 12): string {
  const id = ledger.register(from, MCP, RINGS).agent_id;
  ledger.setWakeLimits(id, perHour, 0);
  return id;
}

/**
 * Run the object's own alarm now (it has no doorbell yet): Lifecycle drives the jobs that
 * are due.
 */
async function fireAlarm(r: Rig): Promise<void> {
  await runInDurableObject(r.stub, (instance: OwnerDO) => instance.alarm());
  await drainDue(r.stub);  // and any run workerd's own firing of it started
}

describe("one open wake per ringing recipient", () => {
  it("opens one wake per burst, sends it once, and the next message opens the next",
     async () => {
    const r = await rig();
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
    const r = await rig();
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
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    // Each half of the identity alone is not the identity.
    const clientOnly = { client: MUSE.client, family: DOT.family };
    const familyOnly = { client: DOT.client, family: MUSE.family };
    for (const [from, agent] of [[DOT, muse], [MUSE, "agent_" + "0".repeat(16)],
                                 [null, muse], [clientOnly, muse],
                                 [familyOnly, muse]] as const) {
      const refused = await failure(() => r.stub.mailboxSubscriber(
        r.bind(from), { command_center_id: "alpha", agent_id: agent }));
      expect([refused.name, refused.message])
        .toEqual(["Refused", "not this caller's mcp_event agent"]);
    }
    const wrongCenter = await failure(() => r.stub.mailboxSubscriber(
      r.bind(MUSE), { command_center_id: "beta", agent_id: muse }));
    expect(wrongCenter.name).toBe("Refused");
    expect(await r.stub.mailboxSubscriber(r.bind(MUSE),
                                          { command_center_id: "alpha", agent_id: muse }))
      .toEqual({ agent_id: muse, generation: 1 });  // each subscribe opens the next
    expect(await r.subscribe(MUSE, muse)).toBe(2);
  });

  it("suppresses a disabled agent's open wake, or one moved off mcp_event", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "revoked" });
    expect(await wakeStates(r)).toEqual(["suppressed"]);
    expect(JSON.parse((await wakes(r))[0]!["receipt_json"] as string))
      .toEqual({ reason: "disabled" });
    expect(await r.tick(clock() + HOUR_S)).toBe(0);
    expect(r.sender.sent).toEqual([]);
    await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`, { cmid: "c2" });
    await r.register(MUSE, "Muse", "pull");
    expect(await wakeStates(r)).toEqual(["suppressed", "suppressed"]);
    const second = (await wakes(r)).filter((w) => w["kind"] === "wake")[1]!;
    expect(JSON.parse(second["receipt_json"] as string)).toEqual({ reason: "delivery_method" });
  });

  it("suppresses a wake left open for a disabled agent at claim, and revokes what it lost",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.subscribe(MUSE, muse);
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "revoked" });
    expect(await r.tick()).toBe(0);  // the disable's own revoke
    expect(r.sender.revoked).toEqual([[muse, 1]]);
    // A wake left open by a direct store change, as a crash would.
    await r.sql("UPDATE ledger_wakes SET state = 'pending', settled_at = NULL " +
                "WHERE kind = 'wake'");
    expect(await r.tick()).toBe(0);
    expect(r.sender.sent).toEqual([]);
    expect(await wakeStates(r)).toEqual(["suppressed"]);
    expect(await r.tick()).toBe(0);
    expect(r.sender.revoked).toEqual([[muse, 1]]);  // that subscription is revoked already
    // A crash that lost the suppression and the revoke's record too: the claim catches it.
    await r.sql("UPDATE ledger_wakes SET state = 'pending', settled_at = NULL " +
                "WHERE kind = 'wake'");
    await r.sql("UPDATE outside_agents SET revoked_through = 0");
    expect(await r.tick()).toBe(0);
    expect(await r.tick()).toBe(0);  // its revoke, on the next pass
    expect(r.sender.revoked).toEqual([[muse, 1], [muse, 1]]);
  });
});

describe("a wake is sent once and never replayed", () => {
  it("keeps the mail queued on a 429 and waits out Retry-After", async () => {
    const r = await rig();
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

  it("refuses a doorbell answer outside the outcome's shape", () => {
    for (const bad of [{ state: "pending" }, { state: "sent", body: "x" },
                       { state: "sent", status: 99 }, { state: "failed", reason: "Not Sent" },
                       { state: "rate_limited", retry_after: "120" }, null, ["sent"]]) {
      expect(() => outcome(bad)).toThrow("invalid wake outcome");
    }
    expect(outcome({ state: "sent", status: 202 })).toEqual({ state: "sent", status: 202 });
  });

  it("settles an ambiguous send unknown and never replays it", async () => {
    const r = await rig();
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
      const claim = await r.inLedger({}, (ledger) => ledger.claim(open["wake_id"], at));
      expect(claim.outcome).toBe("claimed");
      if (mark && claim.outcome === "claimed") {
        await r.inLedger({}, (ledger) => ledger.mark(claim.target.wake_id, claim.target.claim_id));
      }
      const state = async () => (await wakes(r)).find((w) => w["wake_id"] === open["wake_id"])!
        ["state"];
      await r.tick(at + WAKE_LEASE_S / 2);
      expect(await state()).toBe("sending");  // inside the lease nothing moves
      // Past it, the mark decides: unmarked is provably unsent, so the same pass that
      // recovers it sends it again; marked is unknown.
      expect(await r.tick(at + WAKE_LEASE_S + 1)).toBe(mark ? 0 : 1);
      expect(await state()).toBe(mark ? "unknown" : "sent");
      return [open["wake_id"] as string, at] as const;
    };
    const [unsent] = await crashMidSend(false);
    expect(r.sender.sent).toHaveLength(2);
    const [maybe, at2] = await crashMidSend(true);  // a byte may have left: never replayed
    expect(await r.tick(at2 + HOUR_S)).toBe(0);
    expect(r.sender.sent).toHaveLength(2);
    const byId = Object.fromEntries((await wakes(r)).map((w) => [w["wake_id"], w["state"]]));
    expect([byId[unsent], byId[maybe]]).toEqual(["sent", "unknown"]);
  });

  it("leaves a sender that dies mid-send to its lease, then unknown", async () => {
    const r = await rig();
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
    const r = await rig();
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
    const r = await rig();
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

describe("a claim settles only by its holder", () => {
  it("claims one due wake at a time and settles it only by the claim that holds it",
     async () => {
    const r = await rig();
    const now = clock();
    await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      const dot = ledger.register(DOT, MCP, { ...RINGS, display_name: "Dot" }).agent_id;
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, now);
      ledger.post(null, `dm:${dot}`, "c2", "hello", null, now);  // its batch window: not due
      const due = ledger.dueOpen(now, 16);
      expect(due.map((w) => [w.agent_id, w.kind, w.state])).toEqual([[muse, "wake", "pending"]]);
      const later = ledger.agents(null).find((a) => a.agent_id === dot)!;
      expect(later.batch_window_s).toBe(60);
      const claim = ledger.claim(due[0]!.wake_id, now);
      if (claim.outcome !== "claimed") {
        throw new Error("expected a claim");
      }
      expect(claim.target.cursor).toBe(0);
      expect(ledger.claim(due[0]!.wake_id, now)).toEqual({ outcome: "not_due" });  // one at a time
      expect(ledger.settle(due[0]!.wake_id, "claim_stale", { state: "sent" }, now)).toBe(false);
      expect(ledger.mark(due[0]!.wake_id, "claim_stale")).toBe(false);
      expect(ledger.settle(due[0]!.wake_id, claim.target.claim_id, { state: "sent" }, now))
        .toBe(true);
      expect(ledger.settle(due[0]!.wake_id, claim.target.claim_id, { state: "sent" }, now))
        .toBe(false);
    });
    expect(await wakeStates(r)).toEqual(["sent", "pending"]);
  });

  it("lets a stale claim neither mark nor settle after a lapse and a new claim", async () => {
    const r = await rig();
    const t = clock();
    const marked = await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t);
      const [wake] = ledger.dueOpen(t, 1);
      const stale = ledger.claim(wake!.wake_id, t);
      expect(ledger.recover(wake!.wake_id, t + WAKE_LEASE_S)).toBe("pending");  // unmarked
      const fresh = ledger.claim(wake!.wake_id, t + WAKE_LEASE_S);
      if (stale.outcome !== "claimed" || fresh.outcome !== "claimed") {
        throw new Error("expected two claims");
      }
      expect(ledger.mark(wake!.wake_id, stale.target.claim_id)).toBe(false);
      expect(ledger.settle(wake!.wake_id, stale.target.claim_id, { state: "sent" }, t))
        .toBe(false);
      return ledger.mark(wake!.wake_id, fresh.target.claim_id);
    });
    expect(marked).toBe(true);
    expect((await wakes(r)).map((w) => [w["state"], w["marked"], w["attempts"]]))
      .toEqual([["sending", 1, 2]]);
  });

  it("wakes only the mail its claim covered", async () => {
    const r = await rig();
    const t = clock();
    await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      ledger.post(null, `dm:${muse}`, "c1", "first", null, t);
      const claim = ledger.claim(ledger.dueOpen(t, 1)[0]!.wake_id, t);
      if (claim.outcome !== "claimed") {
        throw new Error("expected a claim");
      }
      ledger.post(null, `dm:${muse}`, "c2", "after the claim", null, t);  // its own next wake
      ledger.settle(claim.target.wake_id, claim.target.claim_id, { state: "sent" }, t);
    });
    expect(await states(r, "ledger_recipients")).toEqual(["woken", "queued"]);
    expect(await wakeStates(r)).toEqual(["sent", "pending"]);
  });

  it("folds a wake due again into the agent's next one, which waits out Retry-After",
     async () => {
    const r = await rig();
    const t = clock();
    await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      for (const [n, answer] of [[1, { state: "pending" }],
                                 [2, { state: "rate_limited", retry_after: 300 }]] as const) {
        ledger.post(null, `dm:${muse}`, `c${n}`, `body ${n}`, null, t);
        const open = ledger.dueOpen(t, 1)[0]!;
        const claim = ledger.claim(open.wake_id, t);
        if (claim.outcome !== "claimed") {
          throw new Error("expected a claim");
        }
        ledger.post(null, `dm:${muse}`, `n${n}`, "meanwhile", null, t);  // opens the next
        expect(ledger.settle(open.wake_id, claim.target.claim_id, answer, t)).toBe(true);
        // The claimed wake folds into the open one; nothing is left to send twice.
        const rows = ledger.dueOpen(t + 1000, 16);
        expect(rows).toHaveLength(1);
        if (n === 2) {
          expect([rows[0]!.state, rows[0]!.due]).toEqual(["rate_limited", t + 300]);
        } else {
          expect([rows[0]!.state, rows[0]!.due]).toEqual(["pending", t]);
          const next = ledger.claim(rows[0]!.wake_id, t);
          if (next.outcome !== "claimed") {
            throw new Error("expected a claim");
          }
          ledger.settle(rows[0]!.wake_id, next.target.claim_id, { state: "sent" }, t);
        }
      }
    });
    const folded = (await wakes(r)).filter((w) => w["receipt_json"] !== null
      && JSON.parse(w["receipt_json"] as string).folded === true);
    expect(folded.map((w) => w["state"])).toEqual(["suppressed", "suppressed"]);
  });
});

describe("W3 limits", () => {
  it("caps an agent's rings per hour and pushes the wake past the cap", async () => {
    const r = await rig();
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

  it("reads held mail as read, with its receipt", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event",
                                  { max_wakes_per_hour: 1, batch_window_s: 0 });
    const t = clock() + 1;
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    expect(await r.tick(t)).toBe(1);
    await r.post(null, `dm:${muse}`, { cmid: "c2" });
    expect(await r.tick(t + 1)).toBe(0);  // capped
    expect(await states(r, "ledger_recipients")).toEqual(["woken", "held_cap"]);
    await r.read(MUSE);
    expect(await states(r, "ledger_recipients")).toEqual(["read", "read"]);
    expect((await r.table("ledger_receipts")).filter((x) => x["kind"] === "read"))
      .toHaveLength(2);
  });

  it("holds a lowered cap to the newest rings still in the hour", async () => {
    const r = await rig();
    const t = clock();
    const capped = await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger, MUSE, 3);
      for (const n of [0, 1, 2]) {
        ledger.post(null, `dm:${muse}`, `c${n}`, "x", null, t + 10 * n);
        const claim = ledger.claim(ledger.dueOpen(t + 10 * n, 1)[0]!.wake_id, t + 10 * n);
        if (claim.outcome !== "claimed") {
          throw new Error("expected a claim");
        }
        ledger.settle(claim.target.wake_id, claim.target.claim_id, { state: "sent" }, t + 10 * n);
      }
      ledger.setWakeLimits(muse, 2, 0);  // three rings in the hour, two allowed
      ledger.post(null, `dm:${muse}`, "c3", "x", null, t + 30);
      return ledger.claim(ledger.dueOpen(t + 30, 1)[0]!.wake_id, t + 30);
    });
    // Two rings may stand in any hour: the second newest leaves it first.
    expect(capped).toEqual({ outcome: "capped", due: t + 10 + HOUR_S });
  });

  it("pushes a new wake's due time by the agent's batch window", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 300 });
    await r.post(null, `dm:${muse}`);
    const [wake] = await wakes(r);
    expect(wake!["due"] as number - (wake!["created_at"] as number)).toBe(300);
    expect(await r.tick((wake!["due"] as number) - 1)).toBe(0);
    expect(await r.tick(wake!["due"] as number)).toBe(1);
  });
});

describe("a revoke of a suppressed agent's callbacks", () => {
  it("is scheduled once per subscription, on disable, off mcp_event and on delete",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    const dot = await r.register(DOT, "Dot", "pull");
    const revokes = async () => (await wakes(r)).filter((w) => w["kind"] === "revoke")
      .map((w) => [w["agent_id"], w["state"], w["generation"]]);
    await r.stub.disableAgent(r.session(null), { agent_id: dot, reason: "gone" });
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "gone" });
    expect(await revokes()).toEqual([]);  // neither ever subscribed: no callback to erase
    await r.register(MUSE, "Muse", "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "gone" });
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "again" });
    expect(await revokes()).toEqual([[muse, "pending", 1]]);  // once for that subscription
    expect(await r.wakesAt()).toBeNull();  // no doorbell yet: held, no job
    expect(await r.alarmAt()).toBeNull();
    expect(await r.tick()).toBe(0);
    expect([r.sender.revoked, r.sender.callbacks.get(muse)]).toEqual([[[muse, 1]], new Set()]);
    expect(await revokes()).toEqual([[muse, "sent", 1]]);
    expect(await wakeReceipts(r)).toEqual(["revoked"]);
    expect(JSON.parse((await r.table("ledger_receipts")).at(-1)!["detail_json"] as string))
      .toEqual({ generation: 1 });
    await r.register(MUSE, "Muse", "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.register(MUSE, "Muse", "pull");  // off mcp_event
    await r.register(MUSE, "Muse", "mcp_event");
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    expect(await revokes()).toEqual([[muse, "sent", 1], [muse, "pending", 2]]);
    await r.tick();
    expect(r.sender.revoked).toEqual([[muse, 1], [muse, 2]]);
  });

  it("never erases the callback of an agent re-enabled after its revoke was scheduled",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.subscribe(MUSE, muse);
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "paused" });
    await r.register(MUSE, "Muse", "mcp_event");  // re-enabled before the revoke ran
    expect(await r.subscribe(MUSE, muse)).toBe(2);
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    expect(await r.tick(clock() + 1)).toBe(1);
    // The stale revoke still erased the old callback, and only it.
    expect(r.sender.revoked).toEqual([[muse, 1]]);
    expect(r.sender.callbacks.get(muse)).toEqual(new Set([2]));
    expect(r.sender.sent.map((t) => t.agent_id)).toEqual([muse]);
    const [agent] = (await r.stub.outsideAgents(r.bind(null))).agents;
    expect([agent!.disabled_reason, agent!.subscription, agent!.revoked_through])
      .toEqual([null, 2, 1]);
  });

  it("raises an open revoke's generation instead of opening a second", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "paused" });
    await r.register(MUSE, "Muse", "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "paused" });
    expect((await wakes(r)).map((w) => [w["kind"], w["state"], w["generation"]]))
      .toEqual([["revoke", "pending", 2]]);
    await r.tick();
    expect([r.sender.revoked, r.sender.callbacks.get(muse)]).toEqual([[[muse, 2]], new Set()]);
  });

  it("is due again after a lapsed lease, since erasing callbacks twice is harmless",
     async () => {
    const r = await rig();
    const t = clock();
    const state = await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      ledger.subscriber(MUSE, "alpha", muse);
      ledger.register(MUSE, MCP, { ...RINGS, delivery_method: "pull" }, t);  // off mcp_event
      ledger.register(MUSE, MCP, RINGS, t);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t + 10);  // an open wake too
      const [revoke] = ledger.dueOpen(t, 16);
      const claim = ledger.claim(revoke!.wake_id, t);
      expect(claim.outcome).toBe("revoke");
      expect(() => ledger.settle(revoke!.wake_id, (claim as { target: { claim_id: string } })
        .target.claim_id, { state: "unknown" }, t)).toThrow("a revoke settles sent");
      return ledger.recover(revoke!.wake_id, t + WAKE_LEASE_S);
    });
    expect(state).toBe("pending");
    // It stays its own row: a revoke never folds into the agent's open wake.
    expect((await wakes(r)).map((w) => [w["kind"], w["state"]]))
      .toEqual([["revoke", "pending"], ["wake", "pending"]]);
    expect(await r.tick(t + WAKE_LEASE_S)).toBe(1);
    expect(r.sender.revoked).toHaveLength(1);
  });
});

describe("the wakes lane of the object's one schedule", () => {
  it("backs a row whose step fails off in its own row, never in a loop (review N8)",
     async () => {
    const r = await rig();
    const t = clock();
    const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const muse = ringing(ledger);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      state.storage.sql.exec("CREATE TRIGGER probe_claim BEFORE UPDATE OF claim_id ON " +
                             "ledger_wakes BEGIN SELECT RAISE(ABORT, 'probe'); END");
      const sent = await runWakes(ledger, r.sender, () => t);
      const after = [sent, ledger.nextDue(t, true)];
      // A lapsed claim whose recovery fails waits the same.
      state.storage.sql.exec("DROP TRIGGER probe_claim");
      const at = t + WAKE_RETRY_S;
      const [wake] = ledger.dueOpen(at, 1);
      expect(ledger.claim(wake!.wake_id, at).outcome).toBe("claimed");
      state.storage.sql.exec("CREATE TRIGGER probe_settle BEFORE UPDATE OF settled_at ON " +
                             "ledger_wakes BEGIN SELECT RAISE(ABORT, 'probe'); END");
      await runWakes(ledger, null, () => at + WAKE_LEASE_S);
      state.storage.sql.exec("DROP TRIGGER probe_settle");
      return [...after, ledger.nextDue(at + WAKE_LEASE_S, false)];
    });
    expect(out).toEqual([0, t + WAKE_RETRY_S,
                         t + WAKE_RETRY_S + WAKE_LEASE_S + WAKE_RETRY_S]);
  });

  it("without a doorbell gives open wakes no job and holds them pending", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`);
    expect(await r.wakesAt()).toBeNull();  // nothing can send it, so nothing fires for it
    expect(await r.alarmAt()).toBeNull();
    await r.sql("UPDATE ledger_wakes SET due = due - 120");
    await evictDurableObject(r.stub);
    await r.stub.outsideAgents(r.bind(null));  // a wake re-derives the schedule as stored
    await fireAlarm(r);
    expect(await states(r)).toEqual(["pending"]);  // held, not lost
    expect([await r.wakesAt(), await r.alarmAt()]).toEqual([null, null]);  // and no busy loop
  });

  it("recovers a lapsed claim behind a full pass of due open wakes, its job due now, never " +
     "in the past", async () => {
    const r = await rig();
    const t = clock() - 600;
    const claimed = await r.inLedger({}, (ledger) => {
      const ids = Array.from({ length: 17 }, (_, n) => ringing(ledger, origin(n)));
      for (const id of ids) {
        ledger.post(null, `dm:${id}`, `c-${id}`, "hello", null, t);  // 17 wakes due at t
      }
      // The last agent's wake is claimed and marked, and its sender died: its lease ends
      // at t + 160, after the 16 open wakes still due ahead of it.
      const last = ledger.dueOpen(t, 17).find((w) => w.agent_id === ids[16])!;
      const claim = ledger.claim(last.wake_id, t + 100);
      if (claim.outcome !== "claimed") {
        throw new Error("expected a claim");
      }
      ledger.mark(claim.target.wake_id, claim.target.claim_id);
      return claim.target.wake_id;
    });
    const before = Date.now() / 1000;
    await evictDurableObject(r.stub);
    // The wake re-derives the schedule as stored: the lapsed lease is due, so the lane's
    // job is due now, never in the past. (workerd then fires its alarm on its own.)
    const due = await r.wakesAt();
    expect(due).not.toBeNull();
    expect(due!).toBeGreaterThanOrEqual(Math.floor(before * 1000) / 1000);
    expect(due!).toBeLessThanOrEqual(Date.now() / 1000 + 1);
    await fireAlarm(r);
    const byId = Object.fromEntries((await wakes(r)).map((w) => [w["wake_id"], w["state"]]));
    expect(byId[claimed]).toBe("unknown");  // marked: never replayed
    expect(Object.values(byId).filter((s) => s === "pending")).toHaveLength(16);
    // The 16 cannot be sent without a doorbell: the job is gone and the alarm unset.
    expect([await r.wakesAt(), await r.alarmAt()]).toEqual([null, null]);
  });

  it("arms for a live lease, and at once for due work, never earlier than now", async () => {
    const r = await rig();
    const t = clock();
    const times = await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      const dot = ringing(ledger, DOT);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      ledger.post(null, `dm:${dot}`, "c2", "hello", null, t + 500);
      const empty = ledger.nextDue(t, true);
      const claim = ledger.claim(ledger.dueOpen(t, 1)[0]!.wake_id, t);
      expect(claim.outcome).toBe("claimed");
      return [empty, ledger.nextDue(t, false), ledger.nextDue(t, true),
              ledger.nextDue(t + WAKE_LEASE_S + 5, false)];
    });
    expect(times).toEqual([
      t,  // a wake due 100 s ago, with a doorbell: at once, not in the past
      t + WAKE_LEASE_S,  // without one: only the claim's lease
      t + WAKE_LEASE_S,  // with one: the lease, before Dot's wake at t + 500
      t + WAKE_LEASE_S + 5,  // a lapsed lease: at once
    ]);
  });

  it("drains more due rows than one pass across passes that rearm at once", async () => {
    const r = await rig();
    const t = clock();
    await r.inLedger({}, (ledger) => {
      for (let n = 0; n < 17; n++) {
        ledger.post(null, `dm:${ringing(ledger, origin(n))}`, `c${n}`, "hello", null, t);
      }
    });
    expect(await r.tick(t)).toBe(16);
    expect(await r.inLedger({}, (ledger) => ledger.nextDue(t, true))).toBe(t);
    expect(await r.tick(t)).toBe(1);
    expect(await r.inLedger({}, (ledger) => ledger.nextDue(t, true))).toBeNull();
  });

  it("rearms on wake from the schedule as stored", async () => {
    const r = await rig();
    const t = clock();
    const lease = await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t);
      const claim = ledger.claim(ledger.dueOpen(t, 1)[0]!.wake_id, t);
      expect(claim.outcome).toBe("claimed");
      return t + WAKE_LEASE_S;
    });
    await r.sql("DELETE FROM cf_agents_jobs");
    await runInDurableObject(r.stub, (_i: OwnerDO, state) => state.storage.deleteAlarm());
    await evictDurableObject(r.stub);
    await r.stub.outsideAgents(r.bind(null));  // any call wakes it
    expect(await laneJobs(r.stub))
      .toEqual({ wakes: { time: Math.ceil(lease * 1000),
                          payload: { due: Math.ceil(lease * 1000) } } });
    expect(await r.alarmAt()).toBe(Math.ceil(lease * 1000) / 1000);
  });
});

/**
 * Slice 2's review item L1: five rules the code kept and no test held. Each test fails
 * if its rule is dropped.
 */
describe("the registry's quieter rules", () => {
  it("hides a deleted agent's tombstone from its own outside caller's listing", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    const [tombstone] = await r.table("outside_agents");
    expect([tombstone!["agent_id"], tombstone!["deleted_at"] !== null]).toEqual([muse, true]);
    expect((await r.stub.outsideAgents(r.bind(MUSE))).agents).toEqual([]);
    expect((await r.stub.outsideAgents(r.bind(null))).agents).toEqual([]);
  });

  it("refuses a subscribe for its own agent once that agent is disabled", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "paused" });
    const refused = await failure(() => r.subscribe(MUSE, muse));
    expect([refused.name, refused.message])
      .toEqual(["Refused", "not this caller's mcp_event agent"]);
    const [agent] = (await r.stub.outsideAgents(r.bind(null))).agents;
    expect(agent!.subscription).toBe(0);
  });

  it("lets an outside caller disable its own agent only by both halves of its identity",
     async () => {
    const r = await rig();
    const muse = await r.register(MUSE);
    for (const from of [{ client: MUSE.client, family: DOT.family },
                        { client: DOT.client, family: MUSE.family }]) {
      const refused = await r.inLedger({}, (ledger) => {
        try {
          ledger.disable(muse, "x", from);
          return "disabled";
        } catch (error) {
          return (error as Error).message;
        }
      });
      expect(refused).toBe("an outside caller disables only its own agent");
    }
    const [agent] = (await r.stub.outsideAgents(r.bind(null))).agents;
    expect(agent!.disabled_reason).toBeNull();
  });

  it("keeps a deleted agent's tombstone while its revoke is being sent", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "paused" });
    const at = clock() + 1;
    const claim = await r.inLedger({}, (ledger) => ledger.claim(ledger.dueOpen(at, 1)[0]!.wake_id,
                                                                at));
    if (claim.outcome !== "revoke") {
      throw new Error("expected the revoke claimed");
    }
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });  // its revoke is in flight
    expect((await r.table("outside_agents")).map((a) => a["deleted_at"] !== null))
      .toEqual([true]);
    await r.inLedger({}, (ledger) => ledger.settle(claim.target.wake_id, claim.target.claim_id,
                                                   { state: "sent" }, at + 1));
    expect(await r.table("outside_agents")).toEqual([]);
  });

  it("purges a tombstone only once no revoke of it is left to run", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.subscribe(MUSE, muse);
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    const at = clock() + 1;
    const first = await r.inLedger({}, (ledger) => ledger.claim(ledger.dueOpen(at, 1)[0]!.wake_id,
                                                                at));
    if (first.outcome !== "revoke") {
      throw new Error("expected the revoke claimed");
    }
    // While the first revoke is in flight the agent comes back, subscribes and is deleted
    // again: a second revoke, for its newer callback, opens beside the first.
    expect(await r.register(MUSE, "Muse", "mcp_event")).toBe(muse);
    expect(await r.subscribe(MUSE, muse)).toBe(2);
    await r.stub.deleteAgent(r.session(null), { agent_id: muse });
    expect((await r.table("ledger_wakes")).map((w) => [w["state"], w["generation"]]))
      .toEqual([["sending", 1], ["pending", 2]]);
    await r.inLedger({}, (ledger) => ledger.settle(first.target.wake_id, first.target.claim_id,
                                                   { state: "sent" }, at + 1));
    expect((await r.table("outside_agents")).map((a) => a["agent_id"])).toEqual([muse]);
    await r.tick(at + 2);
    expect(r.sender.revoked).toEqual([[muse, 2]]);
    expect(await r.table("outside_agents")).toEqual([]);
  });
});
