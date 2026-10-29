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
import { agentPolicy, MODEL_DEFAULTS, modelSettings, validate } from "../src/policy";
import { type Ask, type ModelCaps, periods, Usage, USAGE_LIMITS, type UsageLimits } from "../src/usage";
import { gated, until } from "./gateway-support";
import { failure, ownerObject } from "./support";
import type { GatedOwnerDO } from "./worker";

const NOW = 1_793_000_000;

const ASK: Ask = Object.freeze({
  turn: "s1:1", connection: "c1", model: "m1", inputTokens: 1000, maxOutputTokens: 4000,
  reasoning: false, price: Object.freeze({ input: 3, output: 15 }),
});

/** 1000 input at 3 µ$ and 4000 output at 15 µ$. */
const FULL = 1000 * 3 + 4000 * 15;

interface Rig {
  readonly usage: Usage;
  readonly gate: WriteGate;
  readonly state: DurableObjectState;
  /** The policy the reservations read: its generation and caps, changeable. */
  readonly policy: { generation: number; caps: Partial<ModelCaps> };
  /** A second instance's Usage on the same storage, as after an eviction. */
  readonly fresh: () => Usage;
  readonly rows: () => Record<string, SqlStorageValue>[];
  readonly spend: () => Record<string, number>;
}

function withUsage<R>(act: (r: Rig) => Promise<R>, limits: Partial<UsageLimits> = {}): Promise<R> {
  return runInDurableObject(ownerObject().stub, async (_instance: OwnerDO, state) => {
    const gate = new WriteGate(state.storage);
    const policy: Rig["policy"] = { generation: 1, caps: {} };
    const make = () => new Usage(gate.storage, () => ({
      generation: policy.generation,
      settings: { ...MODEL_DEFAULTS, sources: {}, ...policy.caps },
    }), { ...USAGE_LIMITS, ...limits });
    return act({
      usage: make(), gate, state, policy, fresh: make,
      rows: () => state.storage.sql.exec<Record<string, SqlStorageValue>>(
        "SELECT id, turn, connection_id, state, rule, max_output_tokens, est_tokens, " +
          "est_cost, tokens, cost FROM model_reservations ORDER BY seq").toArray(),
      spend: () => Object.fromEntries(state.storage.sql.exec<{ period: string;
                                                                connection_id: string;
                                                                cost: number }>(
        "SELECT period, connection_id, cost FROM model_spend ORDER BY period, connection_id")
        .toArray().map((row) => [`${row.period}/${row.connection_id}`, row.cost])),
    });
  });
}

/** Reserve, dispatch and settle one call that cost `cost` for `tokens`. */
async function spent(r: Rig, ask: Ask, tokens: number, cost: number): Promise<string> {
  const reserved = await r.gate.write(() => r.usage.reserve(ask, NOW));
  if (!reserved.ok) {
    throw new Error(`refused: ${reserved.rule}`);
  }
  expect(await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null)))
    .toEqual({ sent: true });
  expect(await r.gate.write(() => r.usage.settle(reserved.id, { state: "succeeded", tokens,
                                                                cost }, NOW))).toBe(true);
  return reserved.id;
}

const { day, month } = periods(NOW);

describe("a reservation", () => {
  it("is reserved, dispatched and settled, the spend counters holding what it held",
     () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    expect(reserved).toMatchObject({ ok: true, max_output_tokens: 4000, policy_generation: 1 });
    expect(r.rows()).toMatchObject([{ state: "reserved", est_tokens: 5000, est_cost: FULL }]);
    expect(r.spend()).toEqual({ [`d:${day}/c1`]: FULL, [`m:${month}/c1`]: FULL });
    if (!reserved.ok) {
      return;
    }
    expect(await r.gate.write(() => r.usage.dispatched(reserved.id, NOW, null)))
      .toEqual({ sent: true });
    expect(await r.gate.write(() => r.usage.settle(reserved.id, {
      state: "succeeded", tokens: 1200, cost: 5000 }, NOW))).toBe(true);
    expect(r.rows()).toMatchObject([{ state: "succeeded", tokens: 1200, cost: 5000 }]);
    expect(r.spend()).toEqual({ [`d:${day}/c1`]: 5000, [`m:${month}/c1`]: 5000 });
    expect(() => r.usage.reserve(ASK, NOW)).toThrow();  // outside a unit: the gate refuses
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
    expect(r.spend()[`d:${day}/c1`]).toBe(FULL);
    // A failed call holds nothing.
    const third = await write(() => r.usage.reserve(ASK, NOW));
    if (!third.ok) {
      throw new Error("refused");
    }
    await write(() => r.usage.dispatched(third.id, NOW, null));
    expect(await write(() => r.usage.settle(third.id, { state: "failed" }, NOW))).toBe(true);
    expect(r.spend()[`d:${day}/c1`]).toBe(FULL);
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
});

describe("the per-turn caps", () => {
  it("hold a turn to its requests; what was never sent or refused never counts",
     () => withUsage(async (r) => {
    r.policy.caps = { turn_requests: 2 };
    await spent(r, ASK, 10, 10);
    const unsent = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    if (!unsent.ok) {
      throw new Error("refused");
    }
    await r.gate.write(() => r.usage.dispatched(unsent.id, NOW, "aborted"));
    await spent(r, ASK, 10, 10);
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toEqual({ ok: false, rule: "turn_requests" });
    expect(await r.gate.write(() => r.usage.reserve(ASK, NOW)))
      .toEqual({ ok: false, rule: "turn_requests" });
    // Another turn starts afresh.
    expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s1:2" }, NOW))).ok)
      .toBe(true);
    expect(r.rows().filter((row) => row["state"] === "refused")
      .map((row) => [row["rule"], row["est_cost"]]))
      .toEqual([["turn_requests", 0], ["turn_requests", 0]]);
  }));

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
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, price: { input: 3, output: 0 } },
                                                     NOW)))
      .toEqual({ ok: false, rule: "turn_cost" });
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, price: { input: 1, output: 0 } },
                                                     NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 4000 });
  }));
});

describe("the daily and monthly ceilings", () => {
  it("hold the owner's spend, settled and open, against its day's ceiling", () =>
    withUsage(async (r) => {
      r.policy.caps = { daily_cost: FULL * 2 - 10 };
      await r.gate.write(() => r.usage.reserve(ASK, NOW));  // open, holding FULL
      const second = await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s2:1" }, NOW));
      expect(second).toMatchObject({ ok: true,
                                     max_output_tokens: Math.floor((FULL - 10 - 3000) / 15) });
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s3:1" }, NOW)))
        .toEqual({ ok: false, rule: "daily_cost" });
      // The next day is a new day.
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s4:1" },
                                                         NOW + 86_400))).ok).toBe(true);
    }));

  it("hold a source's ceiling to that source's own spend", () => withUsage(async (r) => {
    r.policy.caps = { sources: { c2: { daily_cost: FULL + 10 } } };
    await spent(r, ASK, 10, FULL);  // c1's spend is not c2's
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s2:1", connection: "c2" },
                                                     NOW)))
      .toMatchObject({ ok: true, max_output_tokens: 4000 });
    expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s3:1", connection: "c2" },
                                                     NOW)))
      .toEqual({ ok: false, rule: "source_daily_cost" });
  }));

  it("hold each source to its own ceilings, and the month across its days", () =>
    withUsage(async (r) => {
      r.policy.caps = { monthly_cost: FULL * 3, sources: { c2: { daily_cost: 5000 } } };
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, connection: "c2" }, NOW)))
        .toEqual({ ok: false, rule: "source_daily_cost" });
      expect((await r.gate.write(() => r.usage.reserve(ASK, NOW))).ok).toBe(true);
      const later = NOW + 86_400;
      expect(periods(later).month).toBe(month);
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s2:1" }, later))).ok)
        .toBe(true);
      expect((await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s3:1" }, later))).ok)
        .toBe(true);
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s4:1" }, later)))
        .toEqual({ ok: false, rule: "monthly_cost" });
      r.policy.caps = { sources: { c1: { monthly_cost: FULL * 3 } } };
      expect(await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s5:1" }, later)))
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
      r.gate.write(() => r.usage.reserve({ ...ASK, turn: `s${i}:1` }, NOW)));
    release();
    await other;
    const results = await Promise.all(reserves);
    expect(results.filter((result) => result.ok)).toHaveLength(2);
    expect(r.spend()[`d:${day}/c1`]).toBe(FULL * 2);
  }));
});

describe("a dead instance's calls", () => {
  it("settle as not sent when reserved and unknown when dispatched, keeping the reservation",
     () => withUsage(async (r) => {
    const reserved = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    const dispatched = await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s2:1" }, NOW));
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
    expect(r.spend()[`d:${day}/c1`]).toBe(FULL);
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
        await spent(r, { ...ASK, turn: `s${i}:1` }, 10, 7);
      }
      expect(r.rows().length).toBeLessThanOrEqual(4);
      expect(r.spend()[`d:${day}/c1`]).toBe(42);
      expect(r.spend()[`m:${month}/c1`]).toBe(42);
    }, { receiptsKept: 3 }));

  it("never prune a reservation still open", () => withUsage(async (r) => {
    const open = await r.gate.write(() => r.usage.reserve(ASK, NOW));
    for (let i = 0; i < 4; i++) {
      await spent(r, { ...ASK, turn: `s${i}:1` }, 10, 7);
    }
    expect(r.rows().some((row) => open.ok && row["id"] === open.id)).toBe(true);
    expect(r.rows().length).toBeLessThanOrEqual(3);
  }, { receiptsKept: 1 }));

  it("drop spend counters past their window", () => withUsage(async (r) => {
    await spent(r, ASK, 10, 7);
    await r.gate.write(() => r.usage.reserve({ ...ASK, turn: "s9:1" }, NOW + 200 * 86_400));
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
    expect(MODEL_DEFAULTS).toMatchObject({ turn_cost: 25_000_000, turn_requests: 100 });
    const document = validate({ ...base, models: {
      turn_requests: 5, turn_tokens: 1000, turn_cost: 0, daily_cost: null, monthly_cost: 9,
      sources: { [`c${"a".repeat(24)}`]: { daily_cost: 1, monthly_cost: null } },
      retries: 0 } });
    expect(modelSettings(document)).toEqual({
      turn_requests: 5, turn_tokens: 1000, turn_cost: 0, daily_cost: null, monthly_cost: 9,
      sources: { [`c${"a".repeat(24)}`]: { daily_cost: 1, monthly_cost: null } } });
    expect(agentPolicy(document)).toEqual({ retry: { maxRetries: 0 } });
    expect(agentPolicy(validate(base))).toEqual({});
  });

  it.each([
    { turn_requests: 0 }, { turn_requests: 10_001 }, { turn_requests: 1.5 },
    { turn_tokens: null }, { turn_cost: -1 }, { daily_cost: "1" }, { retries: 11 },
    { unknown: 1 }, { sources: { c1: { daily_cost: 1 } } },
    { sources: { [`c${"a".repeat(24)}`]: { weekly_cost: 1 } } },
    { sources: { [`c${"a".repeat(24)}`]: { daily_cost: -1 } } }, { sources: [] }, [],
  ])("refuses %j", (models) => {
    expect(() => validate({ ...base, models })).toThrow("invalid models policy");
  });
});
