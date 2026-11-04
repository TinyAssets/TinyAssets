/**
 * S3b wakes and the W3 limits: one open wake per ringing recipient, sent once.
 *
 * Ported from the ledger parts of `tests/test_outside_wakes.py` (origin/main 8b415f569).
 * The Python's broker tick becomes one alarm pass (`runWakes`) on the owner's own object
 * with a scripted doorbell (`FakeSender`); its op journal becomes the wake row's
 * may-have-sent mark. The cross-owner fairness test has no counterpart: each object is
 * its own owner. The doorbell itself, the kill switch and admission are
 * `doorbell.test.ts`'s (slice W).
 */
import { evictDurableObject, runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { WriteGate } from "../src/gate";
import {
  clock, HOUR_S, type Ledger, type Outcome, outcome, retryAfter, WAKE_LEASE_S, type WakeTarget,
} from "../src/ledger";
import { LANE_HUNG_S, LANE_PASS_S } from "../src/schedule";
import {
  runWakes, WAKE_RETRY_S, WAKE_SEND_MS, type WakeSender, WAKES_PER_ALARM,
} from "../src/wakes";
import type { OwnerDO } from "../src/owner-do";
import {
  DOT, FakeSender, ledgerOn, MCP, MUSE, origin, rig, type Rig, subscription, wakeReceipts,
} from "./ledger-support";
import { drainDue, failure, faulty, laneJobs } from "./support";

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
  return (await wakes(r)).map((w) => w["state"] as string);
}

/** A write gate over the object's storage, as the object's lanes write through. */
const gateOf = (state: DurableObjectState) => new WriteGate(state.storage).write;

/** A ringing agent with no batch window, registered and tuned in `ledger` itself. */
function ringing(ledger: Ledger, from = MUSE, perHour: number | null = null): string {
  const id = ledger.register(from, MCP, RINGS).agent_id;
  ledger.setWakeLimits(id, perHour, 0);
  return id;
}

/**
 * A doorbell that ignores its own bound: each send hangs until `release`, and never calls
 * `mark()` first, so only the lane can mark it.
 */
class HangingSender extends FakeSender {
  readonly #waiting: (() => void)[] = [];

  release(): void {
    this.#waiting.splice(0).forEach((resolve) => resolve());
  }

  override async send(target: WakeTarget, _mark: () => Promise<void>): Promise<Outcome> {
    this.sent.push(target);
    await new Promise<void>((resolve) => this.#waiting.push(resolve));
    return { state: "sent", status: 202 };
  }
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
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 60 });
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
      through: 2, changes: ["mail"], after: 0, at: wake!["created_at"],
      fold: 1,  // the burst's second message folded in
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
    void dot;
    const refused = await failure(() => r.stub.wakeSubscribe(r.bind(DOT), subscription(
      "alpha", { client: DOT.client })));
    expect(refused.message).toBe("not this caller's subscribing agent");  // no doorbell
  });

  it("lets only the agent's own client subscribe to its mailbox", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    // Each half of the identity alone is not the identity.
    const clientOnly = { client: MUSE.client, family: DOT.family };
    const familyOnly = { client: DOT.client, family: MUSE.family };
    for (const from of [DOT, null, clientOnly, familyOnly]) {
      const refused = await failure(() => r.stub.wakeSubscribe(r.bind(from), subscription(
        "alpha", { client: from?.client ?? MUSE.client })));
      expect(refused.name).toBe("Refused");
    }
    const wrongCenter = await failure(() => r.stub.wakeSubscribe(r.bind(MUSE),
                                                                 subscription("beta")));
    expect(wrongCenter.name).toBe("Refused");
    const door = subscription();
    expect(await r.stub.wakeSubscribe(r.bind(MUSE), door))
      .toEqual({ subscription_id: door.subscription_id, agent_id: muse, expires_at: null });
    // The same identity again refreshes it; another URL is another subscription.
    expect(await r.subscribe(MUSE)).toBe(door.subscription_id);
    await r.subscribe(MUSE, "alpha", { url: "https://doorbell.test/work-chat" });
    expect(await r.sql("SELECT agent_id FROM wake_callbacks")).toHaveLength(2);
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
    const second = (await wakes(r))[1]!;
    expect(JSON.parse(second["receipt_json"] as string)).toEqual({ reason: "delivery_method" });
  });

  it("erases a disabled agent's subscriptions with it, and suppresses a wake left open at " +
     "claim", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.subscribe(MUSE);
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "revoked" });
    expect(await r.sql("SELECT * FROM wake_callbacks")).toEqual([]);  // in the disable's unit
    // A wake left open by a direct store change, as a crash would.
    await r.sql("UPDATE ledger_wakes SET state = 'pending', settled_at = NULL");
    expect(await r.tick()).toBe(0);
    expect(r.sender.sent).toEqual([]);
    expect(await wakeStates(r)).toEqual(["suppressed"]);
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
    expect(await wakes(r)).toEqual([{ ...wake, fold: (wake!["fold"] as number) + 1 }]);
    expect(new Set(await states(r, "ledger_recipients"))).toEqual(new Set(["queued"]));
    expect(await r.tick(now + 60)).toBe(0);
    expect(r.sender.sent).toHaveLength(1);
    expect(await r.tick(now + 121)).toBe(1);
    expect(r.sender.sent).toHaveLength(2);
    expect(await states(r)).toEqual(["sent"]);
    expect(await wakeReceipts(r)).toEqual(["rate_limited", "wake_accepted"]);
  });

  it("waits a Retry-After exactly as sent; one it could not read backs off by attempt", () => {
    expect([0.2, 120, 3 * 86_400, 10 ** 9].map((wait) => retryAfter(wait, 1)))
      .toEqual([0.2, 120, 3 * 86_400, 10 ** 9]);
    // Zero names no time to wait: a callback never keeps the object busy at its word.
    expect([0, 0].map((wait, n) => retryAfter(wait, n + 1))).toEqual([60, 120]);
    expect([1, 2, 3, 4].map((attempts) => retryAfter(null, attempts)))
      .toEqual([WAKE_LEASE_S, 2 * WAKE_LEASE_S, 4 * WAKE_LEASE_S, 8 * WAKE_LEASE_S]);
    expect([undefined, Number.NaN, -5, Number.POSITIVE_INFINITY]
      .map((wait) => retryAfter(wait, 1))).toEqual([60, 60, 60, 60]);
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
                                   await r.table("ledger_receipts")]);
    for (const body of bodies) {
      expect(stored).not.toContain(body);
    }
    for (const target of r.sender.sent) {
      expect(Object.keys(target).sort()).toEqual(["after", "agent_id", "at", "changes",
        "claim_id", "client", "command_center_id", "cursor", "family", "fold", "through",
        "wake_id"]);
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
      ledger.setWakeLimits(dot, null, 60);  // the owner's batch window for Dot
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, now);
      ledger.post(null, `dm:${dot}`, "c2", "hello", null, now);  // its batch window: not due
      const due = ledger.dueOpen(now, 16);
      expect(due.map((w) => [w.agent_id, w.state])).toEqual([[muse, "pending"]]);
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

  it("stops a sender whose mark fails, before its first byte", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    const now = clock() + 1;
    const accepted = await runInDurableObject(r.stub, (_i: OwnerDO, state) => {
      // The claim lapses and is taken again while the sender is handed its target.
      const lapsing: WakeSender = {
        open: () => r.sender.open(),
        send: (target, mark) => {
          state.storage.sql.exec("UPDATE ledger_wakes SET claim_id = 'claim_other' " +
                                 "WHERE wake_id = ?", target.wake_id);
          return r.sender.send(target, mark);
        },
      };
      return runWakes(ledgerOn(state.storage), lapsing, () => now, gateOf(state));
    });
    expect([accepted, r.sender.sent]).toEqual([0, []]);  // it never got past its mark
    expect((await wakes(r)).map((w) => [w["state"], w["marked"]])).toEqual([["sending", 0]]);
  });

  it("counts no send whose settle cannot be written, and backs its row off", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event", { batch_window_s: 0 });
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    const now = clock() + 1;
    // The pass's units: the claim, the mark, then the settle, which fails.
    const accepted = await runInDurableObject(r.stub, (_i: OwnerDO, state) =>
      runWakes(ledgerOn(state.storage), r.sender, () => now,
               faulty(gateOf(state), (n) => n === 3)));
    expect([accepted, r.sender.sent.length]).toEqual([0, 1]);
    const [wake] = await wakes(r);
    expect([wake!["state"], wake!["marked"], wake!["due"]])
      .toEqual(["sending", 1, now + WAKE_RETRY_S]);
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
  it("holds the owner's cap with one counter: one ring each hour over the cap", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event",
                                  { max_wakes_per_hour: 2, batch_window_s: 0 });
    const t = clock() + 1;
    await r.post(null, `dm:${muse}`, { cmid: "c1" });
    expect(await r.tick(t)).toBe(1);
    await r.post(null, `dm:${muse}`, { cmid: "c2" });
    expect(await r.tick(t + 10)).toBe(0);
    const open = (await wakes(r)).find((w) => w["state"] === "pending")!;
    expect(open["due"]).toBeCloseTo(t + HOUR_S / 2, 6);
    expect((await r.table("ledger_recipients")).map((x) => x["state"]))
      .toEqual(["woken", "held_cap"]);
    expect(await r.tick(t + HOUR_S / 2 - 1)).toBe(0);
    expect(await r.tick(t + HOUR_S / 2)).toBe(1);
    expect((await r.table("ledger_recipients")).map((x) => x["state"]))
      .toEqual(["woken", "woken"]);
    // The cap's whole state is one column on the agent's row.
    const [agent] = await r.sql("SELECT wake_tat FROM outside_agents");
    expect(agent!["wake_tat"]).toBeCloseTo(t + HOUR_S, 6);
  });

  it("takes any whole cap from 1, or none", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event",
                                  { max_wakes_per_hour: 100_000, batch_window_s: 0 });
    expect((await r.stub.outsideAgents(r.bind(null))).agents[0]!.max_wakes_per_hour)
      .toBe(100_000);
    const t = clock() + 1;
    for (const n of [1, 2, 3]) {
      await r.post(null, `dm:${muse}`, { cmid: `c${n}` });
      expect(await r.tick(t + n)).toBe(1);  // 0.036 s apart: never held at this rate
    }
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

  it("counts a changed cap afresh", async () => {
    const r = await rig();
    const t = clock();
    const claims = await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger, MUSE, 1);
      ledger.post(null, `dm:${muse}`, "c0", "x", null, t);
      const first = ledger.claim(ledger.dueOpen(t, 1)[0]!.wake_id, t);
      if (first.outcome !== "claimed") {
        throw new Error("expected a claim");
      }
      ledger.settle(first.target.wake_id, first.target.claim_id, { state: "sent" }, t);
      ledger.setWakeLimits(muse, 1, 0);  // the same cap, set again: counted afresh
      ledger.post(null, `dm:${muse}`, "c1", "x", null, t + 10);
      return ledger.claim(ledger.dueOpen(t + 10, 1)[0]!.wake_id, t + 10).outcome;
    });
    expect(claims).toBe("claimed");
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

describe("the wakes lane of the object's one schedule", () => {
  it("defers a row only later, never before its claim's lease", async () => {
    const r = await rig();
    const t = clock();
    const due = await r.inLedger({}, (ledger) => {
      const muse = ringing(ledger);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      const [wake] = ledger.dueOpen(t, 1);
      expect(ledger.claim(wake!.wake_id, t).outcome).toBe("claimed");
      ledger.defer(wake!.wake_id, t + 1);
      return ledger.nextDue(t, false);
    });
    expect(due).toBe(t + WAKE_LEASE_S);
  });

  it("takes no wake past its pass's budget (review N2)", async () => {
    const r = await rig();
    const t = clock();
    const open = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const muse = ringing(ledger);
      ledger.post(null, `dm:${muse}`, "c1", "hello", null, t - 100);
      expect(await runWakes(ledger, r.sender, () => t, gateOf(state), 16, WAKE_SEND_MS,
                            Date.now() - 1))
        .toBe(0);
      return ledger.dueOpen(t, 16).length;
    });
    expect(open).toBe(1);
  });

  it("bounds each send itself: a late one is marked, left to its lease, never sent again " +
     "(slice 5 N2)", async () => {
    expect(WAKE_SEND_MS).toBe((LANE_HUNG_S - LANE_PASS_S) * 1000);
    const r = await rig();
    const t = clock();
    const out = await runInDurableObject(r.stub, async (_i: OwnerDO, state) => {
      const ledger = ledgerOn(state.storage);
      const row = () => state.storage.sql.exec(
        "SELECT state, marked FROM ledger_wakes").one();
      ledger.post(null, `dm:${ringing(ledger)}`, "c1", "hello", null, t - 100);
      const hanging = new HangingSender();
      const started = Date.now();
      const accepted = await runWakes(ledger, hanging, () => t, gateOf(state), WAKES_PER_ALARM, 30);
      const took = Date.now() - started;
      const late = row();
      hanging.release();  // its answer comes after the pass let it go, and settles nothing
      await new Promise((resolve) => setTimeout(resolve, 20));
      const released = row();
      // Its lease lapses: a byte may have left, so it is unknown, never sent again.
      await runWakes(ledger, hanging, () => t + WAKE_LEASE_S, gateOf(state), WAKES_PER_ALARM, 30);
      return { accepted, took, late, released, after: row(), sent: hanging.sent.length };
    });
    expect(out.accepted).toBe(0);
    expect(out.took).toBeLessThan(1000);
    expect([out.late, out.released]).toEqual([{ state: "sending", marked: 1 },
                                              { state: "sending", marked: 1 }]);
    expect([out.after, out.sent]).toEqual([{ state: "unknown", marked: 1 }, 1]);
  });

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
      const sent = await runWakes(ledger, r.sender, () => t, gateOf(state));
      const after = [sent, ledger.nextDue(t, true)];
      // A lapsed claim whose recovery fails waits the same.
      state.storage.sql.exec("DROP TRIGGER probe_claim");
      const at = t + WAKE_RETRY_S;
      const [wake] = ledger.dueOpen(at, 1);
      expect(ledger.claim(wake!.wake_id, at).outcome).toBe("claimed");
      state.storage.sql.exec("CREATE TRIGGER probe_settle BEFORE UPDATE OF settled_at ON " +
                             "ledger_wakes BEGIN SELECT RAISE(ABORT, 'probe'); END");
      await runWakes(ledger, null, () => at + WAKE_LEASE_S, gateOf(state));
      state.storage.sql.exec("DROP TRIGGER probe_settle");
      return [...after, ledger.nextDue(at + WAKE_LEASE_S, false)];
    });
    expect(out).toEqual([0, t + WAKE_RETRY_S,
                         t + WAKE_RETRY_S + WAKE_LEASE_S + WAKE_RETRY_S]);
  });

  it("with the kill switch closed claims nothing, holds open wakes pending, and its job a " +
     "retry out, never a busy loop", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`);
    await r.sql("UPDATE ledger_wakes SET due = due - 120");
    await evictDurableObject(r.stub);
    await r.stub.outsideAgents(r.bind(null));  // a wake re-derives the schedule as stored
    const before = clock();
    await fireAlarm(r);  // the directory holds no control record: closed
    expect((await wakes(r)).map((w) => [w["state"], w["claim_id"]])).toEqual([["pending", null]]);
    const held = await r.wakesAt();
    expect(held!).toBeGreaterThanOrEqual(before + WAKE_RETRY_S - 1);
    expect(held!).toBeLessThanOrEqual(clock() + WAKE_RETRY_S + 1);
    await r.stub.outsideAgents(r.bind(null));  // a door's sync keeps the hold
    expect(await r.wakesAt()).toBe(held);
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
    // The 16 wait for the kill switch, closed here: the job is held a retry out.
    expect(await r.wakesAt()).toBeGreaterThan(clock() + WAKE_RETRY_S - 5);
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
    const jobs = await laneJobs(r.stub);
    expect(Object.keys(jobs)).toEqual(["manifest", "wakes"]);
    expect(jobs["wakes"]).toEqual({ time: Math.ceil(lease * 1000),
                                    payload: { due: Math.ceil(lease * 1000) } });
    expect(await r.alarmAt()).toBe(Math.ceil(lease * 1000) / 1000);
  });
});

/**
 * Slice 2's review item L1: five rules the code kept and no test held. Each test fails
 * if its rule is dropped.
 */
describe("the registry's quieter rules", () => {
  it("refuses a subscribe for its own agent once that agent is disabled", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.stub.disableAgent(r.session(null), { agent_id: muse, reason: "paused" });
    const refused = await failure(() => r.subscribe(MUSE));
    expect([refused.name, refused.message])
      .toEqual(["Refused", "not this caller's subscribing agent"]);
    expect(await r.sql("SELECT * FROM wake_callbacks")).toEqual([]);
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

});
