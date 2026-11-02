/**
 * The owner's model spend: one reservation per model call, held against the owner's
 * model policy, settled from what the call cost (gateway research §4; tasks 4 G2).
 *
 * Ported from `TurnRequestBudget` and its ledger (`tinyassets/request_budget.py`) and the
 * spend reservations of `PolicyStore.decide` (`tinyassets/broker/policy.py`), with their
 * states: `reserved` → `dispatched` → `succeeded` | `failed` | `unknown`, or `reserved` →
 * `not_sent`; plus `refused`, a reservation the ceilings turned down, kept as its receipt.
 * The sandbox's egress (`egress.ts`) holds its calls to the same states and counters
 * (`model_spend`); the runs slice (R) keys these reservations to runs (ADR-034 decision 7).
 *
 * - **Reserve**, in one unit: it reads the owner's model policy and its generation and the
 *   day's and month's spend, checks every ceiling against what is settled plus what is
 *   still open, and inserts the row, so concurrent calls reserve one after another and
 *   never pass a ceiling between them. The reservation bounds the call's output tokens
 *   (`max_output_tokens`): what the ceilings leave, at most what was asked, and at least
 *   `minOutput`, or the call is refused.
 * - **Dispatched**, in a unit of its own just before the call: the row must still be
 *   `reserved` and the owner policy at the generation it was reserved under; else it is
 *   `not_sent`, and the call never goes. A row is `dispatched` before the first byte
 *   leaves, so an unsent one is provably unsent.
 * - **Settle**, in a unit, once: `succeeded` with the tokens and cost the gateway's pi-ai
 *   adapter read; `failed`, cost 0, when the call was refused before any output;
 *   `unknown` otherwise (a stream lost after it started, an abort), which keeps its whole
 *   reservation and is never replayed.
 * - **Abandon**, when a dispatch or settle unit threw: the row settled as it stands
 *   (`reserved` as `not_sent`, `dispatched` as `unknown`), never left open in this instance.
 * - **Recovery**: an object has one live instance, so an open row this instance did not
 *   reserve is a dead instance's call. Each reserve and each receipts read settles them
 *   first: `reserved` as `not_sent`, `dispatched` as `unknown`.
 *
 * Every ceiling reads a counter only the schema's triggers write (`schema.ts`, migration 6):
 * `model_spend`, a source's day and month. The reservation rows are receipts, history
 * only: the newest `receiptsKept` are kept, and pruning one frees nothing.
 *
 * Money is micro-dollars (µ$): pi-ai's catalog prices are dollars per million tokens, so a
 * token at price `p` costs `p` µ$. A call's estimate is pi-ai's own `calculateCost` over the
 * target model's whole cost record (pi audit F6), so a long-context tier applies to the
 * whole request once its input passes the tier's threshold, as the provider bills it. A
 * source is a connection's source record (`connections.ts`), so its ceilings survive a
 * reconnect. The day and month are UTC.
 *
 * Declared differences from the Python: the free-tier pools, their consecutive-failure
 * stop and the advisory daily evidence have no counterpart (no platform-paid source
 * exists); a reservation shrinks its output to fit what the ceilings leave, where the
 * Python counted attempts only.
 */
import { randomBytes } from "node:crypto";

import type { Api, Model, Usage as PiUsage } from "@earendil-works/pi-ai";
import { calculateCost } from "@earendil-works/pi-ai/models";

import type { OwnerStorage } from "./gate";
import { costMicros, largestFitting, periods } from "./money";

export { costMicros, largestFitting, periods };

export interface UsageLimits {
  /** Reservation rows kept, as receipts; no cap reads them. */
  readonly receiptsKept: number;
  readonly receiptsListed: number;
  /** The fewest output tokens a call is worth reserving. */
  readonly minOutput: number;
  /** The fewest a reasoning call may have: the gateway refuses less (`reservation_too_small`). */
  readonly minReasoningOutput: number;
  /** Days of daily spend counters kept, and months of monthly ones. */
  readonly daysKept: number;
  readonly monthsKept: number;
}

export const USAGE_LIMITS: UsageLimits = Object.freeze({
  receiptsKept: 5_000,
  receiptsListed: 50,
  minOutput: 1_024,
  minReasoningOutput: 2_048,
  daysKept: 40,
  monthsKept: 3,
});

/** The ceilings of the owner's model policy (`policy.ts`, `modelSettings`), in µ$. */
export interface ModelCaps {
  readonly daily_cost: number | null;
  readonly monthly_cost: number | null;
  /** Each source record's own ceilings, by its id. */
  readonly sources: { readonly [source: string]: {
    readonly daily_cost?: number | null; readonly monthly_cost?: number | null } };
}

/** The owner's model policy as a reservation reads it, in the reservation's unit. */
export interface UsagePolicy {
  readonly generation: number;
  readonly settings: ModelCaps;
}

/** What a call asks to reserve. */
export interface Ask {
  /**
   * The work the call is part of, as its receipt names it. The `model_reservations.turn`
   * column keeps its name until the runs slice (R) keys reservations to runs.
   */
  readonly turn: string;
  readonly connection: string;
  /** The connection's source record, which its spend is counted under. */
  readonly source: string;
  readonly model: string;
  readonly inputTokens: number;
  /** The output tokens the call asks for (its `maxTokens`, else the model's). */
  readonly maxOutputTokens: number;
  readonly reasoning: boolean;
  /** The model the call goes to, whose pi-ai cost record prices it (`calculateCost`). */
  readonly rates: Pick<Model<Api>, "cost">;
}

export type Reserved =
  | { readonly ok: true; readonly id: string; readonly max_output_tokens: number;
      readonly policy_generation: number }
  | { readonly ok: false; readonly rule: string };

/** How a dispatched call ended. */
export type Outcome =
  | { readonly state: "succeeded"; readonly tokens: number; readonly cost: number }
  | { readonly state: "failed" }
  | { readonly state: "unknown" };

export type State = "reserved" | "dispatched" | "succeeded" | "failed" | "unknown" | "not_sent"
  | "refused";

export interface UsageReceipt {
  readonly reservation: string;
  readonly at: number;
  readonly connection: string;
  readonly source: string;
  readonly model: string;
  readonly state: State;
  readonly rule: string | null;
  readonly max_output_tokens: number;
  readonly est_tokens: number;
  readonly est_cost: number;
  readonly tokens: number | null;
  readonly cost: number | null;
  readonly settled_at: number | null;
}

/**
 * µ$ of a call of `input` uncached input tokens and `output` output tokens on `model`, by
 * pi-ai's own `calculateCost` (which reads only the model's `cost`, tiers included).
 */
export function priced(model: Pick<Model<Api>, "cost">, input: number, output: number): number {
  const usage: PiUsage = {
    input, output, cacheRead: 0, cacheWrite: 0, totalTokens: input + output,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
  return costMicros(calculateCost(model as Model<Api>, usage).total);
}

export class Usage {
  readonly #storage: OwnerStorage;
  readonly #policy: () => UsagePolicy;
  readonly #limits: UsageLimits;
  /** The open reservations this instance made: any other open row is a dead instance's. */
  readonly #live = new Set<string>();

  constructor(storage: OwnerStorage, policy: () => UsagePolicy,
              limits: UsageLimits = USAGE_LIMITS) {
    this.#storage = storage;
    this.#policy = policy;
    this.#limits = limits;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /**
   * In a unit: a dead instance's open reservations settled, `reserved` as `not_sent`
   * (it never left) and `dispatched` as `unknown` (it may have), each keeping its
   * reservation when unknown. Returns how many.
   */
  recover(now: number): number {
    const open = this.#sql.exec<{ id: string }>(
      "SELECT id FROM model_reservations WHERE state IN ('reserved', 'dispatched')").toArray()
      .map((row) => row.id).filter((id) => !this.#live.has(id));
    for (const id of open) {
      this.#settleOpen(id, now);
    }
    return open.length;
  }

  /** An open row settled as it stands: `reserved` as `not_sent`, `dispatched` as `unknown`. */
  #settleOpen(id: string, now: number): void {
    this.#sql.exec("UPDATE model_reservations SET state = CASE state WHEN 'reserved' " +
                     "THEN 'not_sent' ELSE 'unknown' END, settled_at = ? " +
                     "WHERE id = ? AND state IN ('reserved', 'dispatched')", now, id);
  }

  /**
   * In a unit: reserve a call against every ceiling, or refuse it, the refusal kept as a
   * receipt. The policy and the spend are read here, and first the authority the call runs
   * under (`authority`: the refusal's rule, or null when it stands).
   */
  reserve(ask: Ask, now: number, authority: () => string | null): Reserved {
    return this.#storage.transactionSync(() => {
      this.recover(now);
      this.#prune(now);
      const { generation, settings } = this.#policy();
      const { day, month } = periods(now);
      const refuse = (rule: string): Reserved => {
        this.#insert(ask, "refused", rule, 0, 0, 0, generation, day, month, now);
        return { ok: false, rule };
      };
      const unauthorized = authority();
      if (unauthorized !== null) {
        return refuse(unauthorized);
      }
      const floor = Math.min(ask.maxOutputTokens, ask.reasoning
        ? Math.max(this.#limits.minOutput, this.#limits.minReasoningOutput)
        : this.#limits.minOutput);
      // The room each ceiling leaves, the tightest first to name.
      const rooms: [string, number][] = [];
      const own = settings.sources[ask.source];
      const ceilings: [string, number | null | undefined, string, string | null][] = [
        ["daily_cost", settings.daily_cost, `d:${day}`, null],
        ["monthly_cost", settings.monthly_cost, `m:${month}`, null],
        ["source_daily_cost", own?.daily_cost, `d:${day}`, ask.source],
        ["source_monthly_cost", own?.monthly_cost, `m:${month}`, ask.source],
      ];
      for (const [rule, cap, period, source] of ceilings) {
        if (cap !== null && cap !== undefined) {
          rooms.push([rule, cap - this.spent(period, source)]);
        }
      }
      const [costRule, costRoom]: [string | null, number] = rooms.length === 0
        ? [null, Infinity] : rooms.reduce((x, y) => (y[1] < x[1] ? y : x));
      // pi-ai's price, rounded up to a µ$, is non-decreasing in the output (the input alone
      // picks a tier), so the most output the cost room takes is found by bisection, within
      // the call's ask.
      const fits = (output: number) => priced(ask.rates, ask.inputTokens, output) <= costRoom;
      const output = largestFitting(ask.maxOutputTokens, fits);
      if (output < floor || output < 1) {
        return refuse(costRule ?? "max_output_tokens");
      }
      const id = `r${randomBytes(16).toString("hex")}`;
      const cost = priced(ask.rates, ask.inputTokens, output);
      this.#insert(ask, "reserved", null, output, ask.inputTokens + output, cost, generation,
                   day, month, now, id);
      this.#live.add(id);
      return { ok: true, id, max_output_tokens: output, policy_generation: generation };
    });
  }

  /**
   * The µ$ held in `period` (`d:<day>` or `m:<month>`), by `source` or by every source:
   * the counters these reservations and the sandbox's calls (`egress.ts`, through the
   * schema's triggers) both move. A read.
   */
  spent(period: string, source: string | null): number {
    return this.#sql.exec<{ cost: number }>(
      "SELECT COALESCE(SUM(cost), 0) AS cost FROM model_spend WHERE period = ? " +
        "AND (? IS NULL OR source = ?)", period, source, source).one().cost;
  }

  #insert(ask: Ask, state: State, rule: string | null, output: number, tokens: number,
          cost: number, generation: number, day: string, month: string, now: number,
          id = `r${randomBytes(16).toString("hex")}`): void {
    this.#sql.exec("INSERT INTO model_reservations (id, turn, connection_id, source, model, " +
                     "state, rule, max_output_tokens, est_tokens, est_cost, policy_generation, " +
                     "day, month, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   id, ask.turn, ask.connection, ask.source, ask.model, state, rule, output,
                   tokens, cost, generation, day, month, now);
  }

  /**
   * Receipts past the newest `receiptsKept`, never an open one, and spend counters past
   * their window. None of it moves a counter a cap reads.
   */
  #prune(now: number): void {
    this.#sql.exec("DELETE FROM model_reservations WHERE seq <= " +
                     "(SELECT MAX(seq) FROM model_reservations) - ? " +
                     "AND state NOT IN ('reserved', 'dispatched')", this.#limits.receiptsKept);
    this.spendPruned(now);
  }

  /**
   * The spend counters past their window (`daysKept` days, `monthsKept` months), as each
   * reserve prunes them: a reservation's here, a sandbox call's at its open (`egress.ts`),
   * so an owner who uses only the sandbox never fills them. None in a window is touched.
   */
  spendPruned(now: number): void {
    const day = periods(now - this.#limits.daysKept * 86_400).day;
    const month = new Date(now * 1000);
    month.setUTCDate(1);
    month.setUTCMonth(month.getUTCMonth() - this.#limits.monthsKept);
    this.#sql.exec("DELETE FROM model_spend WHERE period < ?", `d:${day}`);
    this.#sql.exec("DELETE FROM model_spend WHERE period >= 'm:' AND period < ?",
                   `m:${month.toISOString().slice(0, 7)}`);
  }

  /**
   * In a unit: the reservation goes out, re-checked in its own row: still `reserved`, and
   * the owner policy still at the generation it was reserved under, and no `refusal`, the
   * caller's own reason not to send (its connection changed, the call aborted). Otherwise
   * it is `not_sent` with that rule, and the call must not be made.
   */
  dispatched(id: string, now: number, refusal: string | null):
      { sent: true } | { sent: false; rule: string } {
    const row = this.#sql.exec<{ state: State; policy_generation: number }>(
      "SELECT state, policy_generation FROM model_reservations WHERE id = ?", id).toArray()[0];
    if (row === undefined || row.state !== "reserved" || !this.#live.has(id)) {
      return { sent: false, rule: "reservation_not_open" };
    }
    const rule = this.#policy().generation !== row.policy_generation ? "policy_changed"
      : refusal;
    this.#sql.exec("UPDATE model_reservations SET state = ?, rule = ?, settled_at = ? " +
                     "WHERE id = ? AND state = 'reserved'",
                   rule === null ? "dispatched" : "not_sent", rule, rule === null ? null : now, id);
    if (rule !== null) {
      this.#live.delete(id);
      return { sent: false, rule };
    }
    return { sent: true };
  }

  /** In a unit: a dispatched call's outcome, once; false when the row was not dispatched. */
  settle(id: string, outcome: Outcome, now: number): boolean {
    const tokens = outcome.state === "succeeded" ? outcome.tokens : outcome.state === "failed"
      ? 0 : null;
    const cost = outcome.state === "succeeded" ? outcome.cost : outcome.state === "failed"
      ? 0 : null;
    const settled = this.#sql.exec("UPDATE model_reservations SET state = ?, tokens = ?, " +
                                     "cost = ?, settled_at = ? WHERE id = ? " +
                                     "AND state = 'dispatched' RETURNING id",
                                   outcome.state, tokens, cost, now, id).toArray().length === 1;
    if (settled) {
      this.#live.delete(id);
    }
    return settled;
  }

  /**
   * In a unit: a reservation this instance can no longer carry through, because its
   * dispatch or settle unit threw, dropped from this instance's open calls first (so,
   * should this write fail too, the next reserve's recovery settles it) and then settled
   * as it stands.
   */
  abandon(id: string, now: number): void {
    this.#live.delete(id);
    this.#settleOpen(id, now);
  }

  /** In a unit: the newest receipts, a dead instance's open calls settled first. */
  receipts(now: number): UsageReceipt[] {
    this.recover(now);
    return this.#sql.exec<Record<string, SqlStorageValue>>(
      "SELECT id, created_at, connection_id, source, model, state, rule, max_output_tokens, " +
        "est_tokens, est_cost, tokens, cost, settled_at FROM model_reservations " +
        "ORDER BY seq DESC LIMIT ?", this.#limits.receiptsListed).toArray()
      .map((row) => ({
        reservation: row["id"] as string, at: row["created_at"] as number,
        connection: row["connection_id"] as string, source: row["source"] as string,
        model: row["model"] as string,
        state: row["state"] as State, rule: row["rule"] as string | null,
        max_output_tokens: row["max_output_tokens"] as number,
        est_tokens: row["est_tokens"] as number, est_cost: row["est_cost"] as number,
        tokens: row["tokens"] as number | null, cost: row["cost"] as number | null,
        settled_at: row["settled_at"] as number | null,
      }));
  }
}
