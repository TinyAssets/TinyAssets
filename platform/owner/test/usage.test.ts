/**
 * The owner's model spend (`usage.ts`; tasks 4 G2): reservations held against the owner's
 * model policy, their states, the caps under concurrency, a dead instance's calls, and
 * the policy's `models` table.
 *
 * Acceptance cases ported from `tinyassets/request_budget.py` (`_RequestLedger`: a turn's
 * attempt limit; `dispatched` only from `reserved`; `not_sent` only from `reserved`,
 * `succeeded`, `failed` or `unknown` only from `dispatched`; a `not_sent` attempt never
 * counts) and from `PolicyStore.decide`'s spend reservations (`tinyassets/broker/policy.py`:
 * the day's and month's reservations summed against their caps, a source's ceiling).
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { describe, expect, it } from "vitest";

import { WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { agentPolicy, MODEL_DEFAULTS, MODEL_SETTINGS, modelSettings, validate } from "../src/policy";
import {
  type Ask, type ModelCaps, periods, priced, Usage, USAGE_LIMITS, type UsageLimits,
} from "../src/usage";
import { gated, until } from "./gateway-support";
import { failure, ownerObject } from "./support";
import type { GatedOwnerDO } from "./worker";

const NOW = 1_793_000_000;

/** A model's cost record at `input` and `output` dollars per million tokens, as pi-ai's. */
function rates(input: number, output: number,
               tiers?: NonNullable<Ask["rates"]["cost"]["tiers"]>): Ask["rates"] {
  return { cost: { input, output, cacheRead: 0, cacheWrite: 0,
                   ...(tiers === undefined ? {} : { tiers }) } };
}

const ASK: Ask = Object.freeze({
  turn: "t1", connection: "c1", source: "anthropic", model: "m1", inputTokens: 1000,
  maxOutputTokens: 4000, reasoning: false, rates: rates(3, 15),
});

/** 1000 input at 3 µ$ and 4000 output at 15 µ$. */
const FULL = 1000 * 3 + 4000 * 15;

interface Rig {
  readonly usage: Standing;
  readonly gate: WriteGate;
  readonly state: DurableObjectState;
  /** The policy the reservations read: its generation and caps, changeable. */
  readonly policy: { generation: number; caps: Partial<ModelCaps> };
  /** A second instance's Usage on the same storage, as after an eviction. */
  readonly fresh: () => Standing;
  readonly rows: () => Record<string, SqlStorageValue>[];
  /** The spend counters, by `<period>/<source>`. */
  readonly spend: () => Record<string, number>;
  /** A turn's counters, or undefined when it has none. */
  readonly turn: (turn: string) => Record<string, SqlStorageValue> | undefined;
}

/** The reservations of calls whose conversation's authority stands: the caps alone decide. */
class Standing extends Usage {
  override reserve(ask: Ask, now: number, authority: () => string | null = () => null) {
    return super.reserve(ask, now, authority);
  }
}

function withUsage<R>(act: (r: Rig) => Promise<R>, limits: Partial<UsageLimits> = {}): Promise<R> {
  return runInDurableObject(ownerObject().stub, async (_instance: OwnerDO, state) => {
    const gate = new WriteGate(state.storage);
    const policy: Rig["policy"] = { generation: 1, caps: {} };
    const make = () => new Standing(gate.storage, () => ({
      generation: policy.generation,
      settings: { ...MODEL_DEFAULTS, sources: {}, ...policy.caps },
    }), { ...USAGE_LIMITS, ...limits });
    const sql = state.storage.sql;
    return act({
      usage: make(), gate, state, policy, fresh: make,
      rows: () => sql.exec<Record<string, SqlStorageValue>>(
        "SELECT id, turn, connection_id, source, state, rule, max_output_tokens, est_tokens, " +
          "est_cost, tokens, cost FROM model_reservations ORDER BY seq").toArray(),
      spend: () => Object.fromEntries(sql.exec<{ period: string; source: string; cost: number }>(
        "SELECT period, source, cost FROM model_spend ORDER BY period, source")
        .toArray().map((row) => [`${row.period}/${row.source}`, row.cost])),
      turn: (turn) => sql.exec<Record<string, SqlStorageValue>>(
        "SELECT requests, tokens, cost, last_at FROM model_turns WHERE turn = ?", turn)
        .toArray()[0],
    });
  });
}

/** Reserve, dispatch and settle one call that cost `cost` for `tokens`. */
async function spent(r: Rig, ask: Ask, tokens: number, cost: number, now = NOW): Promise<string> {
  const reserved = await r.gate.write(() => r.usage.reserve(ask, now));
  if (!reserved.ok) {
    throw new Error(`refused: ${reserved.rule}`);
  }
  expect(await r.gate.write(() => r.usage.dispatched(reserved.id, now, null)))
    .toEqual({ sent: true });
  expect(await r.gate.write(() => r.usage.settle(reserved.id, { state: "succeeded", tokens,
                                                                cost }, now))).toBe(true);
  return reserved.id;
}

/** Reserve and dispatch one call, then settle it `failed`. */
async function failedCall(r: Rig, ask: Ask): Promise<void> {
  const reserved = await r.gate.write(() => r.usage.reserve(ask, NOW));
  if (!reserved.ok) {
    throw new Error(`refused: ${reserved.rule}`);
  }
  await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null));
  expect(await r.gate.write(() => r.usage.settle(reserved.id, { state: "failed" }, NOW)))
    .toBe(true);
}

const { day, month } = periods(NOW);
const DAY = `d:${day}/anthropic`;
const MONTH = `m:${month}/anthropic`;

describe("a reservation", () => {
  it("is reserved, dispatched and settled, the spend counters holding what it held",
     () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    expect(reserved).toMatchObject({ ok: true, max_output_tokens: 4000, policy_generation: 1 });
    expect(r.rows()).toMatchObject([{ state: "reserved", est_tokens: 5000, est_cost: FULL,
                                      source: "anthropic" }]);
    expect(r.spend()).toEqual({ [DAY]: FULL, [MONTH]: FULL });
    expect(r.turn("t1")).toMatchObject({ requests: 1, tokens: 5000, cost: FULL });
    if (!reserved.ok) {
      return;
    }
    expect(await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null)))
      .toEqual({ sent: true });
    expect(await r.gate.write(() => r.usage.settle(reserved.id, {
      state: "succeeded", tokens: 1200, cost: 5000 }, NOW + 5))).toBe(true);
    expect(r.rows()).toMatchObject([{ state: "succeeded", tokens: 1200, cost: 5000 }]);
    expect(r.spend()).toEqual({ [DAY]: 5000, [MONTH]: 5000 });
    expect(r.turn("t1")).toEqual({ requests: 1, tokens: 1200, cost: 5000, last_at: NOW + 5 });
    expect(() => r.usage.reserve(ASK, NOW)).toThrow();  // outside a unit: the gate refuses
  }));

  it("is refused, kept as a receipt, when its conversation's authority no longer stands",
     () => withUsage(async (r) => {
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW, () => "outside_authority")))
      .toEqual({ ok: false, rule: "outside_authority" });
    expect(r.rows()).toMatchObject([{ state: "refused", rule: "outside_authority", est_cost: 0 }]);
    expect(r.spend()).toEqual({});
    expect(r.turn("t1")).toBeUndefined();
  }));

  it("moves only along its states", () => withUsage(async (r) => {
    const write = r.gate.write;
    const first = await write(() => r.usage.reserve(ASK, NOW));
    const second = await write(() => r.usage.reserve(ASK, NOW));
    if (!first.ok || !second.ok) {
      throw new Error("refused");
    }
    // Settled before it was dispatched: nothing.
    expect(await write(() => r.usage.settle(second.id, { state: "failed" }, NOW))).toBe(false);
    expect(await write(() => r.usage.dispatched(first.id, NOW, null))).toEqual({ sent: true });
    expect(await write(() => r.usage.dispatched(first.id, NOW, null)))
      .toEqual({ sent: false, rule: "reservation_not_open" });
    expect(await write(() => r.usage.settle(first.id, { state: "unknown" }, NOW))).toBe(true);
    expect(await write(() => r.usage.settle(first.id, { state: "succeeded", tokens: 1, cost: 1 },
                                            NOW))).toBe(false);
    // The caller's own reason not to send: `not_sent`, then never dispatched.
    expect(await write(() => r.usage.dispatched(second.id, NOW, "aborted")))
      .toEqual({ sent: false, rule: "aborted" });
    expect(await write(() => r.usage.dispatched(second.id, NOW, null)))
      .toEqual({ sent: false, rule: "reservation_not_open" });
    expect(r.rows().map((row) => [row["state"], row["rule"]]))
      .toEqual([["unknown", null], ["not_sent", "aborted"]]);
    // Unknown keeps its whole reservation; not sent holds nothing.
    expect(r.spend()[DAY]).toBe(FULL);
    expect(r.turn("t1")).toMatchObject({ requests: 1, tokens: 5000, cost: FULL });
    // A failed call holds nothing.
    await failedCall(r, ASK);
    expect(r.spend()[DAY]).toBe(FULL);
    expect(r.turn("t1")).toMatchObject({ requests: 2, tokens: 5000, cost: FULL });
  }));

  it("is refused once the policy moved since it was reserved", () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    if (!reserved.ok) {
      throw new Error("refused");
    }
    r.policy.generation = 2;
    expect(await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null)))
      .toEqual({ sent: false, rule: "policy_changed" });
    expect(r.rows().map((row) => row["state"])).toEqual(["not_sent"]);
  }));

  it("is abandoned as it stands when a dispatch or settle cannot run: never left open",
     () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    const dispatched = await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" }, NOW));
    if (!reserved.ok || !dispatched.ok) {
      throw new Error("refused");
    }
    await r.gate.write(() => r.usage.dispatched(dispatched.id, NOW, null));
    await r.gate.write(() => r.usage.abandon(reserved.id, NOW));
    await r.gate.write(() => r.usage.abandon(dispatched.id, NOW));
    expect(r.rows().map((row) => row["state"])).toEqual(["not_sent", "unknown"]);
    // Neither is this instance's open call any more.
    expect(await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null)))
      .toEqual({ sent: false, rule: "reservation_not_open" });
    expect(r.turn("t1")).toMatchObject({ requests: 0, cost: 0 });
    expect(r.turn("t2")).toMatchObject({ requests: 1, cost: FULL });
    expect(r.spend()[DAY]).toBe(FULL);
  }));
});

describe("the per-turn caps", () => {
  it("hold a turn to its requests; a failed call counts, one never sent or refused does not",
     () => withUsage(async (r) => {
    r.policy.caps = { turn_requests: 3 };
    await spent(r, ASK, 10, 10);
    const unsent = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    if (!unsent.ok) {
      throw new Error("refused");
    }
    await r.gate.write(() => r.usage.dispatched(unsent.id, NOW, "aborted"));
    await failedCall(r, ASK);
    await spent(r, ASK, 10, 10);
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toEqual({ ok: false, rule: "turn_requests" });
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toEqual({ ok: false, rule: "turn_requests" });
    expect(r.turn("t1")).toMatchObject({ requests: 3 });
    // Another turn starts afresh.
    expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" }, NOW))).ok)
      .toBe(true);
    expect(r.rows().filter((row) => row["state"] === "refused")
      .map((row) => [row["rule"], row["est_cost"]]))
      .toEqual([["turn_requests", 0], ["turn_requests", 0]]);
  }));

  it("never free a turn's requests when its receipts are pruned", () => withUsage(async (r) => {
    r.policy.caps = { turn_requests: 3 };
    const results: string[] = [];
    for (let i = 0; i < 8; i++) {
      const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
      if (reserved.ok) {
        await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null));
        await r.gate.write(() => r.usage.settle(reserved.id, { state: "succeeded", tokens: 10,
                                                               cost: 7 }, NOW));
        results.push("ok");
      } else {
        results.push(reserved.rule);
      }
    }
    expect(results).toEqual(["ok", "ok", "ok", ...Array(5).fill("turn_requests")]);
    expect(r.rows().length).toBeLessThanOrEqual(4);
  }, { receiptsKept: 3 }));

  it("never free a live turn's caps when other turns' receipts push its own out", () =>
    withUsage(async (r) => {
      r.policy.caps = { turn_requests: 2 };
      await spent(r, ASK, 10, 7);
      await spent(r, ASK, 10, 7);
      expect((await r.gate.write(() => r.usage.reserve(ASK, NOW))).ok).toBe(false);
      for (let i = 0; i < 6; i++) {
        await spent(r, { ...ASK, turn: `other${i}` }, 10, 7);
      }
      expect(r.rows().some((row) => row["turn"] === "t1" && row["state"] === "succeeded"))
        .toBe(false);
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toEqual({ ok: false, rule: "turn_requests" });
    }, { receiptsKept: 4 }));

  it("hold 5,000 requests of one turn, past its receipts, and refuse the next", () =>
    withUsage(async (r) => {
      expect(MODEL_SETTINGS.turn_requests[1]).toBe(10_000);
      r.policy.caps = { turn_requests: 5_000, turn_tokens: 1_000_000_000,
                        turn_cost: 1_000_000_000_000 };
      const ask = { ...ASK, inputTokens: 1, maxOutputTokens: 1, rates: rates(0, 0) };
      for (let i = 0; i < 5_000; i++) {
        await r.gate.write(() => {
          const reserved = r.usage.reserve(ask, NOW);
          if (!reserved.ok) {
            throw new Error(`refused at ${i}: ${reserved.rule}`);
          }
          r.usage.dispatched(reserved.id, NOW, null);
          r.usage.settle(reserved.id, { state: "succeeded", tokens: 3, cost: 0 }, NOW);
        });
      }
      // Each held 2 tokens reserved and settled at 3, at no cost: the tokens alone moved.
      expect(r.turn("t1")).toMatchObject({ requests: 5_000, tokens: 15_000, cost: 0 });
      expect(r.rows().length).toBeLessThanOrEqual(101);
      expect(await r.gate.write(() => r.usage.reserve(ask, NOW)))
        .toEqual({ ok: false, rule: "turn_requests" });
    }, { receiptsKept: 100 }), 120_000);

  it("shrink a call's output to what the turn's tokens leave, then refuse", () =>
    withUsage(async (r) => {
      r.policy.caps = { turn_tokens: 8000 };
      await spent(r, ASK, 5000, 10);
      // 8000 - 5000 held - 1000 input leaves 2000 of the 4000 asked.
      const shrunk = await r.gate.write(() => r.usage.reserve(ASK, NOW));
      expect(shrunk).toMatchObject({ ok: true, max_output_tokens: 2000 });
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toEqual({ ok: false, rule: "turn_tokens" });
    }));

  it("count what a failed call used toward the turn's tokens, and an unknown one's estimate",
     () => withUsage(async (r) => {
    r.policy.caps = { turn_tokens: 10_000 };
    await failedCall(r, ASK);
    const lost = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    if (!lost.ok) {
      throw new Error("refused");
    }
    await r.gate.write(() => r.usage.dispatched(lost.id, NOW, null));
    await r.gate.write(() => r.usage.settle(lost.id, { state: "unknown" }, NOW));
    // 10,000 - 5,000 the unknown call holds - 1000 input leaves 4000.
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 4000 });
    expect(r.turn("t1")).toMatchObject({ requests: 3, tokens: 10_000 });
  }));

  it("refuse an output one token under the floor, and take one at it", () =>
    withUsage(async (r) => {
      r.policy.caps = { turn_tokens: 1000 + USAGE_LIMITS.minOutput - 1 };
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toEqual({ ok: false, rule: "turn_tokens" });
      r.policy.caps = { turn_tokens: 1000 + USAGE_LIMITS.minOutput };
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toMatchObject({ ok: true, max_output_tokens: USAGE_LIMITS.minOutput });
    }));

  it("count a settled call's cost toward the turn's cost", () => withUsage(async (r) => {
    r.policy.caps = { turn_cost: 50_000 };
    await spent(r, ASK, 10, 20_000);
    // 50,000 - 20,000 spent - 3000 input leaves 27,000 µ$: 1800 output tokens at 15.
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 1800 });
  }));

  it("shrink a call's output to what the turn's cost leaves, never below the floor", () =>
    withUsage(async (r) => {
      r.policy.caps = { turn_cost: 30_000 };
      // 3000 for the input leaves 27,000 µ$: 1800 output tokens at 15.
      const first = await r.gate.write(() => r.usage.reserve(ASK, NOW));
      expect(first).toMatchObject({ ok: true, max_output_tokens: 1800 });
      expect(r.rows()[0]).toMatchObject({ est_cost: 30_000, est_tokens: 2800 });
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toEqual({ ok: false, rule: "turn_cost" });
      // A reasoning call needs the gateway's floor for a thinking budget.
      r.policy.caps = { turn_cost: 30_000 + 3000 + 1500 * 15 };
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, reasoning: true }, NOW)))
        .toEqual({ ok: false, rule: "turn_cost" });
      expect((await r.gate.write(() => r.usage.reserve(ASK, NOW)))).toMatchObject({
        ok: true, max_output_tokens: 1500 });
      // Asking for less than the floor is the call's own choice.
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2",
                                                         maxOutputTokens: 100 }, NOW)))
        .toMatchObject({ ok: true, max_output_tokens: 100 });
    }));

  it("refuse a call whose input alone passes the cost left", () => withUsage(async (r) => {
    r.policy.caps = { turn_cost: 2000 };
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toEqual({ ok: false, rule: "turn_cost" });
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, rates: rates(3, 0) }, NOW)))
      .toEqual({ ok: false, rule: "turn_cost" });
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, rates: rates(1, 0) }, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 4000 });
  }));

  it("round every estimate up to the next µ$", () => withUsage(async (r) => {
    expect(priced(rates(0.5, 0.25), 3, 0)).toBe(2);
    await r.gate.write(() => r.usage.reserve({ ...ASK, inputTokens: 3, maxOutputTokens: 1031,
                                               rates: rates(0.5, 0.25) }, NOW));
    // 1.5 µ$ of input and 257.75 of output.
    expect(r.rows()[0]).toMatchObject({ est_cost: 260 });
  }));

  it("price a call by pi-ai's calculateCost, a long-context tier for the whole request once " +
     "its input passes the threshold", () => withUsage(async (r) => {
    const tiered = rates(3, 15, [{ inputTokensAbove: 2000, input: 6, output: 30, cacheRead: 0,
                                   cacheWrite: 0 }]);
    // At the threshold, the base rates: 2000 at 3 and 4000 at 15.
    await r.gate.write(() => r.usage.reserve({ ...ASK, inputTokens: 2000, rates: tiered }, NOW));
    // Past it, every token at the tier's: 2001 at 6 and 4000 at 30.
    await r.gate.write(() => r.usage.reserve({ ...ASK, inputTokens: 2001, rates: tiered }, NOW));
    expect(r.rows().map((row) => row["est_cost"])).toEqual([2000 * 3 + 4000 * 15,
                                                            2001 * 6 + 4000 * 30]);
    // The output the caps leave is priced at the tier too: 50,000 - 18,000 of input at 6
    // leaves 32,000 µ$, 1066 output tokens at 30.
    r.policy.caps = { turn_cost: 50_000 };
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2", inputTokens: 3000,
                                                      rates: tiered }, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 1066 });
    expect(r.rows()[2]).toMatchObject({ est_cost: 18_000 + 1066 * 30 });
  }));
});

describe("a turn's counters", () => {
  it("go once the turn has been idle past the window, and not before", () =>
    withUsage(async (r) => {
      await spent(r, ASK, 10, 7);
      const idle = USAGE_LIMITS.turnIdleS;
      await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" }, NOW + idle - 1));
      expect(r.turn("t1")).toMatchObject({ requests: 1 });
      await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t3" }, NOW + idle + 1));
      expect(r.turn("t1")).toBeUndefined();
      expect(r.turn("t2")).toMatchObject({ requests: 1 });
    }));

  it("keep the newest call's time when an older call of the turn settles after it", () =>
    withUsage(async (r) => {
      const first = await r.gate.write(() => r.usage.reserve(ASK, NOW));
      if (!first.ok) {
        throw new Error("refused");
      }
      await r.gate.write(() => r.usage.dispatched(first.id, NOW, null));
      await r.gate.write(() => r.usage.reserve(ASK, NOW + 10));
      await r.gate.write(() => r.usage.settle(first.id, { state: "succeeded", tokens: 1,
                                                          cost: 1 }, NOW + 5));
      expect(r.turn("t1")).toMatchObject({ requests: 2, last_at: NOW + 10 });
    }));

  it("are kept while a call of the turn is open, however old", () => withUsage(async (r) => {
    const open = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    if (!open.ok) {
      throw new Error("refused");
    }
    await r.gate.write(() => r.usage.dispatched(open.id, NOW, null));
    await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" },
                                             NOW + USAGE_LIMITS.turnIdleS + 1));
    expect(r.turn("t1")).toMatchObject({ requests: 1, cost: FULL });
  }));

  it("are never made by a refusal", () => withUsage(async (r) => {
    r.policy.caps = { turn_cost: 10 };
    expect((await r.gate.write(() => r.usage.reserve(ASK, NOW))).ok).toBe(false);
    expect(r.turn("t1")).toBeUndefined();
  }));
});

describe("the daily and monthly ceilings", () => {
  it("hold the owner's spend, settled and open, against its day's ceiling", () =>
    withUsage(async (r) => {
      r.policy.caps = { daily_cost: FULL * 2 - 10 };
      await r.gate.write(() => r.usage.reserve(ASK, NOW));  // open, holding FULL
      const second = await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" }, NOW));
      expect(second).toMatchObject({ ok: true,
                                     max_output_tokens: Math.floor((FULL - 10 - 3000) / 15) });
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t3" }, NOW)))
        .toEqual({ ok: false, rule: "daily_cost" });
      // The next day is a new day.
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t4" },
                                                         NOW + 86_400))).ok).toBe(true);
    }));

  it("hold a source's ceiling to that source's own spend", () => withUsage(async (r) => {
    r.policy.caps = { sources: { openai: { daily_cost: FULL + 10 } } };
    await spent(r, ASK, 10, FULL);  // anthropic's spend is not openai's
    const openai = { ...ASK, connection: "c2", source: "openai" };
    expect(await r.gate.write(() => r.usage.reserve({ ...openai, turn: "t2" }, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 4000 });
    expect(await r.gate.write(() => r.usage.reserve({ ...openai, turn: "t3" }, NOW)))
      .toEqual({ ok: false, rule: "source_daily_cost" });
  }));

  it("hold a source's ceiling across its connections, so a reconnect keeps it", () =>
    withUsage(async (r) => {
      r.policy.caps = { sources: { anthropic: { daily_cost: FULL + 10 } } };
      await spent(r, ASK, 10, FULL);
      // The owner revoked c1 and connected the same source again as c9.
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2", connection: "c9" },
                                                       NOW)))
        .toEqual({ ok: false, rule: "source_daily_cost" });
    }));

  it("hold each source to its own ceilings, and the month across its days", () =>
    withUsage(async (r) => {
      r.policy.caps = { monthly_cost: FULL * 3, sources: { openai: { daily_cost: 5000 } } };
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, connection: "c2",
                                                         source: "openai" }, NOW)))
        .toEqual({ ok: false, rule: "source_daily_cost" });
      expect((await r.gate.write(() => r.usage.reserve(ASK, NOW))).ok).toBe(true);
      const later = NOW + 86_400;
      expect(periods(later).month).toBe(month);
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" }, later))).ok)
        .toBe(true);
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t3" }, later))).ok)
        .toBe(true);
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t4" }, later)))
        .toEqual({ ok: false, rule: "monthly_cost" });
      r.policy.caps = { sources: { anthropic: { monthly_cost: FULL * 3 } } };
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t5" }, later)))
        .toEqual({ ok: false, rule: "source_monthly_cost" });
    }));
});

describe("concurrent reserves", () => {
  it("never pass a cap: each reads and inserts in its own unit, one after another", () =>
    withUsage(async (r) => {
      r.policy.caps = { turn_requests: 3 };
      let release!: () => void;
      const held = new Promise<void>((resolve) => { release = resolve; });
      // A transaction of the queue every unit waits on, held while the reserves queue.
      const other = new DurableObjectSqliteDatabase(r.state.storage).transaction(async () => {
        await held;
      });
      const reserves = Array.from({ length: 8 }, () =>
        r.gate.write(() => r.usage.reserve(ASK, NOW)));
      for (let i = 0; i < 20; i++) {
        await Promise.resolve();
      }
      expect(r.rows()).toEqual([]);
      release();
      await other;
      const results = await Promise.all(reserves);
      expect(results.filter((result) => result.ok)).toHaveLength(3);
      expect(results.slice(3)).toEqual(Array(5).fill({ ok: false, rule: "turn_requests" }));
    }));

  it("never pass a spend ceiling either", () => withUsage(async (r) => {
    r.policy.caps = { daily_cost: FULL * 2 };
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const other = new DurableObjectSqliteDatabase(r.state.storage).transaction(async () => {
      await held;
    });
    const reserves = Array.from({ length: 6 }, (_, i) =>
      r.gate.write(() => r.usage.reserve({ ...ASK, turn: `t${i}` }, NOW)));
    release();
    await other;
    const results = await Promise.all(reserves);
    expect(results.filter((result) => result.ok)).toHaveLength(2);
    expect(r.spend()[DAY]).toBe(FULL * 2);
  }));
});

describe("a dead instance's calls", () => {
  it("settle as not sent when reserved and unknown when dispatched, keeping the reservation",
     () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    const dispatched = await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" }, NOW));
    if (!reserved.ok || !dispatched.ok) {
      throw new Error("refused");
    }
    await r.gate.write(() => r.usage.dispatched(dispatched.id, NOW, null));
    // This instance's own open calls are live: a receipts read leaves them.
    await r.gate.write(() => r.usage.receipts(NOW));
    expect(r.rows().map((row) => row["state"])).toEqual(["reserved", "dispatched"]);
    // The next instance finds them open and not its own.
    const next = r.fresh();
    const receipts = await r.gate.write(() => next.receipts(NOW));
    expect(receipts.map((receipt) => [receipt.reservation, receipt.state])).toEqual([
      [dispatched.id, "unknown"], [reserved.id, "not_sent"]]);
    expect(r.spend()[DAY]).toBe(FULL);
    // A dead instance's settle never lands on a recovered row.
    expect(await r.gate.write(() => r.usage.settle(dispatched.id, { state: "succeeded",
                                                                   tokens: 1, cost: 1 }, NOW)))
      .toBe(false);
  }));

  it("are settled by the next reserve, so a call never sent frees its turn", () =>
    withUsage(async (r) => {
      r.policy.caps = { turn_requests: 1 };
      expect((await r.gate.write(() => r.usage.reserve(ASK, NOW))).ok).toBe(true);
      const next = r.fresh();
      expect((await r.gate.write(() => next.reserve(ASK, NOW))).ok).toBe(true);
      expect(r.rows().map((row) => row["state"])).toEqual(["not_sent", "reserved"]);
    }));

  it("are never dispatched by another instance", () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    if (!reserved.ok) {
      throw new Error("refused");
    }
    expect(await r.gate.write(() => r.fresh().dispatched(reserved.id, NOW, null)))
      .toEqual({ sent: false, rule: "reservation_not_open" });
    expect(r.rows().map((row) => row["state"])).toEqual(["reserved"]);
  }));

  it("settle unknown after the object dies mid-call, the reservation kept", async () => {
    const r = await gated();
    await r.connect("sk-crash");
    const id = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
    const cancelled = async () => ((await (await fetch(`https://status.test/cancelled?id=${id}`))
      .json()) as { cancelled: boolean | null }).cancelled;
    await r.in(async (instance: GatedOwnerDO) => {
      await instance.agent.harness.submit(`cmd:slow-${id}`);
    });
    await until(async () => (await cancelled()) === false, "the upstream stream to open");
    const [open] = await r.sql("SELECT id, state, est_cost FROM model_reservations");
    expect(open!["state"]).toBe("dispatched");
    // The object dies mid-call, as an eviction or a reset leaves it: no settle runs.
    await r.in((_instance, state) => state.abort("evicted mid-call")).catch(() => undefined);
    // The broken instance answers its last calls with the reset; a new one then serves.
    let receipts: Awaited<ReturnType<typeof r.stub.usageReceipts>>["receipts"] = [];
    await until(async () => {
      try {
        ({ receipts } = await env.GATED.getByName(r.name).usageReceipts(r.bind()));
        return true;
      } catch {
        return false;
      }
    }, "a new instance");
    const crashed = receipts.find((receipt) => receipt.reservation === open!["id"]);
    expect(crashed).toMatchObject({ state: "unknown", est_cost: open!["est_cost"] });
    const held = await runInDurableObject(env.GATED.getByName(r.name),
                                          (_instance: GatedOwnerDO, state) =>
      state.storage.sql.exec<{ cost: number }>(
        "SELECT SUM(cost) AS cost FROM model_spend WHERE period LIKE 'd:%'").one().cost);
    expect(held).toBeGreaterThanOrEqual(Number(open!["est_cost"]));
  });
});

describe("the receipts", () => {
  it("keep the newest, and pruning one never frees what it held", () =>
    withUsage(async (r) => {
      for (let i = 0; i < 6; i++) {
        await spent(r, { ...ASK, turn: `t${i}` }, 10, 7);
      }
      expect(r.rows().length).toBeLessThanOrEqual(4);
      expect(r.spend()[DAY]).toBe(42);
      expect(r.spend()[MONTH]).toBe(42);
      expect(r.turn("t0")).toMatchObject({ requests: 1, tokens: 10, cost: 7 });
    }, { receiptsKept: 3 }));

  it("never prune a reservation still open, reserved or dispatched", () =>
    withUsage(async (r) => {
      const open = await r.gate.write(() => r.usage.reserve(ASK, NOW));
      const out = await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t2" }, NOW));
      if (!open.ok || !out.ok) {
        throw new Error("refused");
      }
      await r.gate.write(() => r.usage.dispatched(out.id, NOW, null));
      for (let i = 0; i < 4; i++) {
        await spent(r, { ...ASK, turn: `s${i}` }, 10, 7);
      }
      const ids = r.rows().map((row) => row["id"]);
      expect(ids).toContain(open.id);
      expect(ids).toContain(out.id);
      expect(r.rows().length).toBeLessThanOrEqual(4);
      expect(await r.gate.write(() => r.usage.settle(out.id, { state: "succeeded", tokens: 1,
                                                               cost: 1 }, NOW))).toBe(true);
    }, { receiptsKept: 1 }));

  it("drop spend counters past their window", () => withUsage(async (r) => {
    await spent(r, ASK, 10, 7);
    await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "t9" }, NOW + 200 * 86_400));
    expect(Object.keys(r.spend()).some((key) => key.startsWith(`d:${day}`))).toBe(false);
    expect(Object.keys(r.spend()).some((key) => key.startsWith(`m:${month}`))).toBe(false);
  }));

  it("are the owner's alone", async () => {
    const r = await gated();
    expect((await failure(() => r.stub.usageReceipts(
      r.bind({ origin: { client: "a".repeat(64), family: "b".repeat(64) },
               source: { kind: "mcp", ref: "conn-1" } })))).message)
      .toBe("only the owner may read its model spend");
  });
});

describe("the models table of owner policy", () => {
  const base = { entries: [], egress: { mode: "open", destinations: [] },
                 spend: { daily: null, monthly: null, transaction: null, ask_above: null,
                          source_ceilings: {} } };

  it("is optional, its defaults the Python's per-turn limits, and feeds pi its retries",
     () => {
    expect(modelSettings(validate(base))).toEqual({ ...MODEL_DEFAULTS, sources: {} });
    expect(MODEL_DEFAULTS).toMatchObject({ turn_cost: 25_000_000, turn_requests: 100,
                                           daily_cost: null, monthly_cost: null });
    const document = validate({ ...base, models: {
      turn_requests: 5, turn_tokens: 1000, turn_cost: 0, daily_cost: null, monthly_cost: 9,
      sources: { anthropic: { daily_cost: 1, monthly_cost: null } },
      retries: 0 } });
    expect(modelSettings(document)).toEqual({
      turn_requests: 5, turn_tokens: 1000, turn_cost: 0, daily_cost: null, monthly_cost: 9,
      sources: { anthropic: { daily_cost: 1, monthly_cost: null } } });
    expect(agentPolicy(document)).toEqual({ retry: { maxRetries: 0 } });
    expect(agentPolicy(validate(base))).toEqual({});
  });

  it.each([
    { turn_requests: 0 }, { turn_requests: 10_001 }, { turn_requests: 1.5 },
    { turn_tokens: null }, { turn_cost: -1 }, { daily_cost: "1" }, { retries: 11 },
    { unknown: 1 }, { sources: { Anthropic: { daily_cost: 1 } } },
    { sources: { "-x": { daily_cost: 1 } } }, { sources: { [`a${"b".repeat(64)}`]: {} } },
    { sources: { anthropic: { weekly_cost: 1 } } },
    { sources: { anthropic: { daily_cost: -1 } } }, { sources: [] }, [],
  ])("refuses %j", (models) => {
    expect(() => validate({ ...base, models })).toThrow("invalid models policy");
  });
});
