/**
 * The owner's model spend (`usage.ts`): reservations held against the owner's model policy,
 * their states, the ceilings under concurrency, a dead instance's calls, and the policy's
 * `models` table. The reservations are keyed to runs.
 *
 * A reservation is `dispatched` only from `reserved`; `not_sent` only from `reserved`;
 * `succeeded`, `failed` or `unknown` only from `dispatched`; and a `not_sent` attempt never
 * counts. The day's and month's reservations are summed against their caps, and a source's
 * against its ceiling.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import {
  DEFAULT_POLICY_JSON, MODEL_DEFAULTS, MODEL_SETTINGS, validate,
} from "../../contract/src/policy-document";
import { modelSettings } from "../src/policy";
import { keptFrom, largestFitting, periods, SETTLED_WITHIN_S } from "../src/money";
import { MIGRATIONS } from "../src/schema";
import { migrate } from "../../contract/src/migrate";
import { SPEND_KEPT } from "../../contract/src/limits";
import {
  type Ask, type ModelCaps, priced, Usage, USAGE_LIMITS,
} from "../src/usage";
import { gated } from "./gateway-support";
import { failure, ownerObject } from "./support";

const NOW = 1_793_000_000;
/** A model's cost record at `input` and `output` dollars per million tokens, as pi-ai's. */
function rates(input: number, output: number,
               tiers?: NonNullable<Ask["rates"]["cost"]["tiers"]>): Ask["rates"] {
  return { cost: { input, output, cacheRead: 0, cacheWrite: 0,
                   ...(tiers === undefined ? {} : { tiers }) } };
}

const ASK: Ask = Object.freeze({
  run: "t1", connection: "c1", source: "anthropic", model: "m1", inputTokens: 1000,
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
}

/** The reservations of calls whose conversation's authority stands: the caps alone decide. */
class Standing extends Usage {
  override reserve(ask: Ask, now: number, authority: () => string | null = () => null) {
    return super.reserve(ask, now, authority);
  }
}

async function withUsage<R>(act: (r: Rig) => Promise<R>): Promise<R> {
  return runInDurableObject((await ownerObject()).stub, async (_instance: OwnerDO, state) => {
    const gate = new WriteGate(state.storage);
    const policy: Rig["policy"] = { generation: 1, caps: {} };
    const make = () => new Standing(gate.storage, () => ({
      generation: policy.generation,
      settings: { ...MODEL_DEFAULTS, sources: {}, ...policy.caps },
    }));
    const sql = state.storage.sql;
    const usage = make();
    return act({
      usage, gate, state, policy, fresh: make,
      rows: () => sql.exec<Record<string, SqlStorageValue>>(
        "SELECT id, run, connection_id, source, state, rule, max_output_tokens, est_tokens, " +
          "est_cost, tokens, cost FROM model_reservations ORDER BY seq").toArray(),
      spend: () => Object.fromEntries(sql.exec<{ period: string; source: string; cost: number }>(
        "SELECT period, source, cost FROM model_spend ORDER BY period, source")
        .toArray().map((row) => [`${row.period}/${row.source}`, row.cost])),
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
    if (!reserved.ok) {
      return;
    }
    expect(await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null)))
      .toEqual({ sent: true });
    expect(await r.gate.write(() => r.usage.settle(reserved.id, {
      state: "succeeded", tokens: 1200, cost: 5000 }, NOW + 5))).toBe(true);
    expect(r.rows()).toMatchObject([{ state: "succeeded", tokens: 1200, cost: 5000 }]);
    expect(r.spend()).toEqual({ [DAY]: 5000, [MONTH]: 5000 });
    expect(() => r.usage.reserve(ASK, NOW)).toThrow();  // outside a unit: the gate refuses
  }));

  it("is refused, kept as a receipt, when the authority it runs under no longer stands",
     () => withUsage(async (r) => {
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW, () => "outside_authority")))
      .toEqual({ ok: false, rule: "outside_authority" });
    expect(r.rows()).toMatchObject([{ state: "refused", rule: "outside_authority", est_cost: 0 }]);
    expect(r.spend()).toEqual({});
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
    // A failed call holds nothing.
    await failedCall(r, ASK);
    expect(r.spend()[DAY]).toBe(FULL);
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
    const dispatched = await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t2" }, NOW));
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
    expect(r.spend()[DAY]).toBe(FULL);
  }));
});

describe("a reservation's output", () => {
  it("is refused one token under the floor, and taken at it", () =>
    withUsage(async (r) => {
      r.policy.caps = { daily_cost: 3000 + (USAGE_LIMITS.minOutput - 1) * 15 };
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toEqual({ ok: false, rule: "daily_cost" });
      r.policy.caps = { daily_cost: 3000 + USAGE_LIMITS.minOutput * 15 };
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toMatchObject({ ok: true, max_output_tokens: USAGE_LIMITS.minOutput });
    }));

  it("counts a settled call's cost toward the ceiling", () => withUsage(async (r) => {
    r.policy.caps = { daily_cost: 50_000 };
    await spent(r, ASK, 10, 20_000);
    // 50,000 - 20,000 spent - 3000 input leaves 27,000 µ$: 1800 output tokens at 15.
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 1800 });
  }));

  it("shrinks to what the ceiling leaves, never below the floor", () =>
    withUsage(async (r) => {
      r.policy.caps = { daily_cost: 30_000 };
      // 3000 for the input leaves 27,000 µ$: 1800 output tokens at 15.
      const first = await r.gate.write(() => r.usage.reserve(ASK, NOW));
      expect(first).toMatchObject({ ok: true, max_output_tokens: 1800 });
      expect(r.rows()[0]).toMatchObject({ est_cost: 30_000, est_tokens: 2800 });
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toEqual({ ok: false, rule: "daily_cost" });
      // A reasoning call needs the gateway's floor for a thinking budget.
      r.policy.caps = { daily_cost: 30_000 + 3000 + 1500 * 15 };
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, reasoning: true }, NOW)))
        .toEqual({ ok: false, rule: "daily_cost" });
      expect((await r.gate.write(() => r.usage.reserve(ASK, NOW)))).toMatchObject({
        ok: true, max_output_tokens: 1500 });
    }));

  it("is what the call asked for, its floor its own choice, with no ceiling set", () =>
    withUsage(async (r) => {
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, maxOutputTokens: 100 }, NOW)))
        .toMatchObject({ ok: true, max_output_tokens: 100 });
      expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
        .toMatchObject({ ok: true, max_output_tokens: 4000 });
    }));

  it("is refused when the input alone passes the cost left", () => withUsage(async (r) => {
    r.policy.caps = { daily_cost: 2000 };
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toEqual({ ok: false, rule: "daily_cost" });
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, rates: rates(3, 0) }, NOW)))
      .toEqual({ ok: false, rule: "daily_cost" });
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, rates: rates(1, 0) }, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 4000 });
  }));

  it("rounds every estimate up to the next µ$", () => withUsage(async (r) => {
    expect(priced(rates(0.5, 0.25), 3, 0)).toBe(2);
    await r.gate.write(() => r.usage.reserve({ ...ASK, inputTokens: 3, maxOutputTokens: 1031,
                                               rates: rates(0.5, 0.25) }, NOW));
    // 1.5 µ$ of input and 257.75 of output.
    expect(r.rows()[0]).toMatchObject({ est_cost: 260 });
  }));

  it("is found by bisection, about log2 of the bound in prices", () => {
    // The steepest search: the largest output any model takes, at the policy's largest
    // ceiling, against a cheap record of the real catalog's.
    const record = rates(0.029, 0.03);
    let calls = 0;
    const fits = (output: number) => {
      calls += 1;
      return priced(record, 50_000, output) <= MODEL_SETTINGS.daily_cost[1];
    };
    expect(largestFitting(100_000_000, fits)).toBe(100_000_000);
    expect(calls).toBeLessThanOrEqual(28);
    calls = 0;
    const room = 1_000_000;
    const found = largestFitting(100_000_000, (output) => {
      calls += 1;
      return priced(record, 50_000, output) <= room;
    });
    expect(calls).toBeLessThanOrEqual(28);
    expect(priced(record, 50_000, found)).toBeLessThanOrEqual(room);
    expect(priced(record, 50_000, found + 1)).toBeGreaterThan(room);
    expect(largestFitting(-1, () => true)).toBe(-1);
    expect(largestFitting(10, () => false)).toBe(-1);
    expect(largestFitting(0, () => true)).toBe(0);
  });

  it("is reserved promptly for a catalog-priced call at the policy's largest ceiling", () =>
    withUsage(async (r) => {
      r.policy.caps = { daily_cost: MODEL_SETTINGS.daily_cost[1] };
      const began = Date.now();
      expect(await r.gate.write(() => r.usage.reserve({
        ...ASK, inputTokens: 50_000, maxOutputTokens: 4_000, rates: rates(0.029, 0.03) }, NOW)))
        .toMatchObject({ ok: true, max_output_tokens: 4_000 });
      expect(Date.now() - began).toBeLessThan(500);
    }));

  it("keeps an estimate rounded up to the µ$ inside the cost it fits", () =>
    withUsage(async (r) => {
      // Here pi-ai's price of the output that fits by its rate, rounded up, is a µ$ over.
      r.policy.caps = { daily_cost: 100_700 };
      const reserved = await r.gate.write(() => r.usage.reserve({
        ...ASK, inputTokens: 260, maxOutputTokens: 2_000_000, rates: rates(0.1, 0.1) }, NOW));
      expect(reserved.ok).toBe(true);
      const [row] = r.rows();
      expect(row!["est_cost"]).toBeLessThanOrEqual(100_700);
      expect(priced(rates(0.1, 0.1), 260, Number(row!["max_output_tokens"]) + 1))
        .toBeGreaterThan(100_700);
    }));

  it("is priced by pi-ai's calculateCost, a long-context tier for the whole request once " +
     "its input passes the threshold", () => withUsage(async (r) => {
    const tiered = rates(3, 15, [{ inputTokensAbove: 2000, input: 6, output: 30, cacheRead: 0,
                                   cacheWrite: 0 }]);
    // At the threshold, the base rates: 2000 at 3 and 4000 at 15.
    await r.gate.write(() => r.usage.reserve({ ...ASK, inputTokens: 2000, rates: tiered }, NOW));
    // Past it, every token at the tier's: 2001 at 6 and 4000 at 30.
    await r.gate.write(() => r.usage.reserve({ ...ASK, inputTokens: 2001, rates: tiered }, NOW));
    expect(r.rows().map((row) => row["est_cost"])).toEqual([2000 * 3 + 4000 * 15,
                                                            2001 * 6 + 4000 * 30]);
  }));
});

describe("the daily and monthly ceilings", () => {
  it("hold the owner's spend, settled and open, against its day's ceiling", () =>
    withUsage(async (r) => {
      r.policy.caps = { daily_cost: FULL * 2 - 10 };
      await r.gate.write(() => r.usage.reserve(ASK, NOW));  // open, holding FULL
      const second = await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t2" }, NOW));
      expect(second).toMatchObject({ ok: true,
                                     max_output_tokens: Math.floor((FULL - 10 - 3000) / 15) });
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t3" }, NOW)))
        .toEqual({ ok: false, rule: "daily_cost" });
      // The next day is a new day.
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t4" },
                                                         NOW + 86_400))).ok).toBe(true);
    }));

  it("hold a source's ceiling to that source's own spend", () => withUsage(async (r) => {
    r.policy.caps = { sources: { openai: { daily_cost: FULL + 10 } } };
    await spent(r, ASK, 10, FULL);  // anthropic's spend is not openai's
    const openai = { ...ASK, connection: "c2", source: "openai" };
    expect(await r.gate.write(() => r.usage.reserve({ ...openai, run: "t2" }, NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 4000 });
    expect(await r.gate.write(() => r.usage.reserve({ ...openai, run: "t3" }, NOW)))
      .toEqual({ ok: false, rule: "source_daily_cost" });
  }));

  it("hold a source's ceiling across its connections, so a reconnect keeps it", () =>
    withUsage(async (r) => {
      r.policy.caps = { sources: { anthropic: { daily_cost: FULL + 10 } } };
      await spent(r, ASK, 10, FULL);
      // The owner revoked c1 and connected the same source again as c9.
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t2", connection: "c9" },
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
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t2" }, later))).ok)
        .toBe(true);
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t3" }, later))).ok)
        .toBe(true);
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t4" }, later)))
        .toEqual({ ok: false, rule: "monthly_cost" });
      r.policy.caps = { sources: { anthropic: { monthly_cost: FULL * 3 } } };
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t5" }, later)))
        .toEqual({ ok: false, rule: "source_monthly_cost" });
    }));
});

describe("concurrent reserves", () => {
  it("never pass a spend ceiling: each reads and inserts in its own unit, one after " +
     "another", () => withUsage(async (r) => {
    r.policy.caps = { daily_cost: FULL * 2 };
    const reserves = Array.from({ length: 6 }, (_, i) =>
      r.gate.write(() => r.usage.reserve({ ...ASK, run: `t${i}` }, NOW)));
    const results = await Promise.all(reserves);
    expect(results.filter((result) => result.ok)).toHaveLength(2);
    expect(r.spend()[DAY]).toBe(FULL * 2);
  }));
});

describe("a dead instance's calls", () => {
  it("settle as not sent when reserved and unknown when dispatched, keeping the reservation",
     () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    const dispatched = await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t2" }, NOW));
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

  it("are settled by the next reserve, so a call never sent frees what it held", () =>
    withUsage(async (r) => {
      r.policy.caps = { daily_cost: FULL };
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
});

describe("the receipts", () => {
  it("keep the newest, and pruning one never frees what it held", () =>
    withUsage(async (r) => {
      const calls = USAGE_LIMITS.receiptsKept + 3;
      for (let i = 0; i < calls; i++) {
        await spent(r, { ...ASK, run: `t${i}` }, 10, 7);
      }
      expect(r.rows().length).toBeLessThanOrEqual(USAGE_LIMITS.receiptsKept + 1);
      expect(r.spend()[DAY]).toBe(calls * 7);
      expect(r.spend()[MONTH]).toBe(calls * 7);
    }));

  it("never prune a reservation still open, reserved or dispatched", () =>
    withUsage(async (r) => {
      const open = await r.gate.write(() => r.usage.reserve(ASK, NOW));
      const out = await r.gate.write(() => r.usage.reserve({ ...ASK, run: "t2" }, NOW));
      if (!open.ok || !out.ok) {
        throw new Error("refused");
      }
      await r.gate.write(() => r.usage.dispatched(out.id, NOW, null));
      for (let i = 0; i < USAGE_LIMITS.receiptsKept + 2; i++) {
        await spent(r, { ...ASK, run: `s${i}` }, 10, 7);
      }
      const ids = r.rows().map((row) => row["id"]);
      expect(ids).toContain(open.id);
      expect(ids).toContain(out.id);
      expect(r.rows().length).toBeLessThanOrEqual(USAGE_LIMITS.receiptsKept + 3);
      expect(await r.gate.write(() => r.usage.settle(out.id, { state: "succeeded", tokens: 1,
                                                               cost: 1 }, NOW))).toBe(true);
    }));

  it("are the owner's alone", async () => {
    const r = await gated();
    expect((await failure(() => r.stub.usageReceipts(
      r.bind({ origin: { client: "a".repeat(64), family: "b".repeat(64) },
               source: { kind: "mcp", ref: "conn-1" } })))).message)
      .toBe("only the owner may read its model spend");
  });
});

describe("the spend counters", () => {
  /** The first instant of 2026-12, seconds. */
  const DECEMBER = Date.UTC(2026, 11, 1) / 1000;

  it("are kept until every charge reserved in their period has settled, and no longer",
     () => withUsage(async (r) => {
    const counters = ["d:2026-11-28", "d:2026-11-29", "d:2026-11-30", "d:2026-12-01",
                     "m:2026-10", "m:2026-11", "m:2026-12"];
    // The last charge reserved on 29 November may settle until a moment before this.
    const edge = Date.UTC(2026, 10, 30) / 1000 + SETTLED_WITHIN_S;
    const now = edge - 1;
    await r.gate.write(() => {
      for (const period of counters) {
        r.state.storage.sql.exec("INSERT INTO model_spend (period, source, cost) " +
                                   "VALUES (?, 'anthropic', 1)", period);
      }
      r.usage.spendPruned(now);
    });
    expect(Object.keys(r.spend())).toEqual(["d:2026-11-29/anthropic", "d:2026-11-30/anthropic",
                                            "d:2026-12-01/anthropic", "m:2026-11/anthropic",
                                            "m:2026-12/anthropic"]);
    await r.gate.write(() => r.usage.spendPruned(edge));
    expect(Object.keys(r.spend())).toEqual(["d:2026-11-30/anthropic", "d:2026-12-01/anthropic",
                                            "m:2026-11/anthropic", "m:2026-12/anthropic"]);
  }));

  it("hold a charge reserved before midnight until it settles, its settle moving its own " +
     "counters", () => withUsage(async (r) => {
    const reservedAt = DECEMBER - 10;
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, reservedAt));
    if (!reserved.ok) {
      throw new Error("refused");
    }
    await r.gate.write(() => r.usage.dispatched(reserved.id, reservedAt, null));
    // Another call's reserve prunes just before the first settles, at the last it may.
    const settledAt = reservedAt + SETTLED_WITHIN_S - 1;
    await spent(r, { ...ASK, run: "t2" }, 10, 7, settledAt);
    expect(await r.gate.write(() => r.usage.settle(reserved.id, {
      state: "succeeded", tokens: 1200, cost: 5000 }, settledAt))).toBe(true);
    expect(r.spend()).toEqual({ "d:2026-11-30/anthropic": 5000, "d:2026-12-02/anthropic": 7,
                                "m:2026-11/anthropic": 5000, "m:2026-12/anthropic": 7 });
  }));

  it("past the bound migration 25 holds them to go before the next reservation adds one",
     () => withUsage(async (r) => {
    const sql = r.state.storage.sql;
    for (const { name } of sql.exec<{ name: string }>("SELECT name FROM sqlite_master " +
        "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' " +
        "AND name NOT GLOB 'cf_*'").toArray()) {
      sql.exec(`DROP TABLE "${name}"`);
    }
    migrate(r.state.storage, MIGRATIONS.slice(0, 24));
    // Forty days of thirteen sources' counters, as the window before kept: more than the bound.
    sql.exec("WITH RECURSIVE n(i) AS (SELECT 0 UNION ALL SELECT i + 1 FROM n WHERE i < 519) " +
               "INSERT INTO model_spend (period, source, cost) SELECT 'd:' || " +
               "date(?, 'unixepoch', '-' || (i / 13) || ' days'), 's' || (i % 13), 1 FROM n",
             NOW);
    migrate(r.state.storage, MIGRATIONS);
    expect(sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM model_spend").one().n)
      .toBeGreaterThan(SPEND_KEPT);
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW))).toMatchObject({ ok: true });
    const kept = keptFrom(NOW);
    expect(sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM model_spend WHERE period < ? " +
                                     "OR (period >= 'm:' AND period < ?)",
                                   `d:${kept.day}`, `m:${kept.month}`).one().n).toBe(0);
  }));

});

describe("the models table of owner policy", () => {
  const base = JSON.parse(DEFAULT_POLICY_JSON) as object;

  it("is optional, every cap off until the owner sets it (ADR-034 decision 7)", () => {
    expect(modelSettings(validate(base))).toEqual({
      turn_requests: null, turn_tokens: null, turn_cost: null, daily_cost: null,
      monthly_cost: null, sources: {} });
    // A cap the owner turns off again, and the highest each takes.
    expect(modelSettings(validate({ ...base, models: { turn_tokens: null } })).turn_tokens)
      .toBeNull();
    expect(modelSettings(validate({ ...base, models: { turn_requests: 1_000_000 } }))
      .turn_requests).toBe(1_000_000);
    const document = validate({ ...base, models: {
      turn_requests: 5, turn_tokens: 1000, turn_cost: 0, daily_cost: null, monthly_cost: 9,
      sources: { anthropic: { daily_cost: 1, monthly_cost: null } } } });
    expect(modelSettings(document)).toEqual({
      turn_requests: 5, turn_tokens: 1000, turn_cost: 0, daily_cost: null, monthly_cost: 9,
      sources: { anthropic: { daily_cost: 1, monthly_cost: null } } });
  });

  it.each([
    { turn_requests: 0 }, { turn_requests: 1_000_001 }, { turn_requests: 1.5 },
    { turn_tokens: "1" }, { turn_cost: -1 }, { daily_cost: "1" }, { retries: 0 },
    { unknown: 1 }, { sources: { Anthropic: { daily_cost: 1 } } },
    { sources: { "-x": { daily_cost: 1 } } }, { sources: { [`a${"b".repeat(64)}`]: {} } },
    { sources: { anthropic: { weekly_cost: 1 } } },
    { sources: { anthropic: { daily_cost: -1 } } }, { sources: [] }, [],
  ])("refuses %j", (models) => {
    expect(() => validate({ ...base, models })).toThrow("invalid models policy");
  });
});
