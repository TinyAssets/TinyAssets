/**
 * The owner's model spend: one reservation per model call, held against the owner's
 * model policy, settled from what the call cost (gateway research §4; tasks 4 G2).
 *
 * Ported from `TurnRequestBudget` and its ledger (`tinyassets/request_budget.py`) and the
 * spend reservations of `PolicyStore.decide` (`tinyassets/broker/policy.py`), with their
 * states: `reserved` → `dispatched` → `succeeded` | `failed` | `unknown`, or `reserved` →
 * `not_sent`; plus `refused`, a reservation the caps turned down, kept as its receipt.
 *
 * - **Reserve**, in one unit with the call's credential (`gateway-provider.ts`): it reads
 *   the owner's model policy and its generation, the turn's reservations and the day's and
 *   month's spend, checks every cap against what is settled plus what is still open, and
 *   inserts the row, so concurrent calls reserve one after another and never pass a cap
 *   between them. The reservation bounds the call's output tokens (`max_output_tokens`,
 *   which the gateway clamps to): what the caps leave, at most what was asked, and at
 *   least `minOutput`, or the call is refused.
 * - **Dispatched**, in a unit of its own just before the gateway call: the row must still
 *   be `reserved` and the owner policy at the generation it was reserved under; else it
 *   is `not_sent`, and the call never goes. A row is `dispatched` before the first byte
 *   leaves, so an unsent one is provably unsent.
 * - **Settle**, in a unit, once: `succeeded` with pi-ai's `Usage` (tokens, and its catalog
 *   cost) from the `done` event; `failed`, cost 0, when the gateway refused the call
 *   before it reached the provider; `unknown` otherwise (a stream that died, an abort, a
 *   provider error mid-stream), which keeps its whole reservation and is never replayed.
 * - **Recovery**: an object has one live instance, so an open row this instance did not
 *   reserve is a dead instance's call. Each reserve and each receipts read settles them
 *   first: `reserved` as `not_sent`, `dispatched` as `unknown`.
 *
 * Money is micro-dollars (µ$): pi-ai's catalog prices are dollars per million tokens, so a
 * token at price `p` costs `p` µ$. An estimate is the context's estimated input at the
 * input price plus the reserved output at the output price. A turn is a conversation's
 * calls since its newest user message (`turnOf`): steering starts a new one, and so does
 * a compaction that summarizes that message away. The day and month are UTC.
 *
 * Declared differences from the Python: the per-turn limits are owner policy (the
 * `models` table) rather than constants; the free-tier pools, their consecutive-failure
 * stop and the advisory daily evidence have no counterpart (no platform-paid source
 * exists); a reservation shrinks its output to fit what the caps leave, where the Python
 * counted attempts only; the receipts are the reservation rows, the newest
 * `receiptsKept`, while the day's and month's spend are counters the schema's triggers
 * keep, so pruning a receipt never frees a ceiling.
 */
import { randomBytes } from "node:crypto";

import type { OwnerStorage } from "./gate";

export interface UsageLimits {
  /** Reservation rows kept; the counters keep the spend of the ones pruned. */
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

/** The caps of the owner's model policy (`policy.ts`, `modelSettings`), in µ$ for money. */
export interface ModelCaps {
  readonly turn_requests: number;
  readonly turn_tokens: number;
  readonly turn_cost: number;
  readonly daily_cost: number | null;
  readonly monthly_cost: number | null;
  readonly sources: { readonly [connection: string]: {
    readonly daily_cost?: number | null; readonly monthly_cost?: number | null } };
}

/** The owner's model policy as a reservation reads it, in the reservation's unit. */
export interface UsagePolicy {
  readonly generation: number;
  readonly settings: ModelCaps;
}

/** What a call asks to reserve. Prices are pi-ai's: dollars per million tokens. */
export interface Ask {
  readonly turn: string;
  readonly connection: string;
  readonly model: string;
  readonly inputTokens: number;
  /** The output tokens the call asks for (its `maxTokens`, else the model's). */
  readonly maxOutputTokens: number;
  readonly reasoning: boolean;
  readonly price: { readonly input: number; readonly output: number };
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

/** What a row holds against the turn's token cap. */
const HELD_TOKENS = "CASE WHEN state IN ('reserved', 'dispatched', 'unknown') THEN est_tokens " +
  "WHEN state IN ('succeeded', 'failed') THEN coalesce(tokens, 0) ELSE 0 END";
/** What a row holds against the cost caps, as the schema's `model_spend` triggers count it. */
const HELD_COST = "CASE WHEN state IN ('reserved', 'dispatched', 'unknown') THEN est_cost " +
  "WHEN state IN ('succeeded', 'failed') THEN coalesce(cost, 0) ELSE 0 END";

/** The UTC day and month of `now` (seconds). */
export function periods(now: number): { day: string; month: string } {
  const day = new Date(now * 1000).toISOString().slice(0, 10);
  return { day, month: day.slice(0, 7) };
}

/** µ$ of `tokens` at `price` dollars per million tokens. */
export function micros(tokens: number, price: number): number {
  return Math.ceil(tokens * price);
}

/** µ$ of a cost pi-ai reports in dollars. */
export function costMicros(dollars: number): number {
  return Number.isFinite(dollars) && dollars > 0 ? Math.ceil(dollars * 1_000_000) : 0;
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
      this.#sql.exec("UPDATE model_reservations SET state = CASE state WHEN 'reserved' " +
                       "THEN 'not_sent' ELSE 'unknown' END, settled_at = ? " +
                       "WHERE id = ? AND state IN ('reserved', 'dispatched')", now, id);
    }
    return open.length;
  }

  /**
   * In a unit: reserve a call against every cap, or refuse it, the refusal kept as a
   * receipt. The policy, the turn's rows and the spend are all read here.
   */
  reserve(ask: Ask, now: number): Reserved {
    return this.#storage.transactionSync(() => {
      this.recover(now);
      this.#prune(now);
      const { generation, settings } = this.#policy();
      const { day, month } = periods(now);
      const turn = this.#sql.exec<{ n: number; tokens: number; cost: number }>(
        `SELECT COUNT(*) AS n, COALESCE(SUM(${HELD_TOKENS}), 0) AS tokens, ` +
          `COALESCE(SUM(${HELD_COST}), 0) AS cost FROM model_reservations ` +
          "WHERE turn = ? AND state NOT IN ('refused', 'not_sent')", ask.turn).one();
      const floor = Math.min(ask.maxOutputTokens, ask.reasoning
        ? Math.max(this.#limits.minOutput, this.#limits.minReasoningOutput)
        : this.#limits.minOutput);
      const refuse = (rule: string): Reserved => {
        this.#insert(ask, "refused", rule, 0, 0, 0, generation, day, month, now);
        return { ok: false, rule };
      };
      if (turn.n >= settings.turn_requests) {
        return refuse("turn_requests");
      }
      // The room each cost cap leaves, the tightest first to name.
      const rooms: [string, number][] = [["turn_cost", settings.turn_cost - turn.cost]];
      const spent = (period: string, connection: string | null) => this.#sql.exec<{ cost: number }>(
        "SELECT COALESCE(SUM(cost), 0) AS cost FROM model_spend WHERE period = ? " +
          "AND (? IS NULL OR connection_id = ?)", period, connection, connection).one().cost;
      const ceilings: [string, number | null | undefined, string, string | null][] = [
        ["daily_cost", settings.daily_cost, `d:${day}`, null],
        ["monthly_cost", settings.monthly_cost, `m:${month}`, null],
        ["source_daily_cost", settings.sources[ask.connection]?.daily_cost, `d:${day}`,
         ask.connection],
        ["source_monthly_cost", settings.sources[ask.connection]?.monthly_cost, `m:${month}`,
         ask.connection],
      ];
      for (const [rule, cap, period, connection] of ceilings) {
        if (cap !== null && cap !== undefined) {
          rooms.push([rule, cap - spent(period, connection)]);
        }
      }
      const [costRule, costRoom] = rooms.reduce((a, b) => (b[1] < a[1] ? b : a));
      const inputCost = micros(ask.inputTokens, ask.price.input);
      const byTokens = settings.turn_tokens - turn.tokens - ask.inputTokens;
      const byCost = ask.price.output > 0
        ? Math.floor((costRoom - inputCost) / ask.price.output)
        : costRoom - inputCost >= 0 ? Infinity : -1;
      const output = Math.min(ask.maxOutputTokens, byTokens, byCost);
      if (output < floor || output < 1) {
        return refuse(byTokens <= byCost && byTokens < floor ? "turn_tokens" : costRule);
      }
      const id = `r${randomBytes(16).toString("hex")}`;
      const cost = inputCost + micros(output, ask.price.output);
      this.#insert(ask, "reserved", null, output, ask.inputTokens + output, cost, generation,
                   day, month, now, id);
      this.#live.add(id);
      return { ok: true, id, max_output_tokens: output, policy_generation: generation };
    });
  }

  #insert(ask: Ask, state: State, rule: string | null, output: number, tokens: number,
          cost: number, generation: number, day: string, month: string, now: number,
          id = `r${randomBytes(16).toString("hex")}`): void {
    this.#sql.exec("INSERT INTO model_reservations (id, turn, connection_id, model, state, " +
                     "rule, max_output_tokens, est_tokens, est_cost, policy_generation, day, " +
                     "month, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   id, ask.turn, ask.connection, ask.model, state, rule, output, tokens, cost,
                   generation, day, month, now);
  }

  /** Receipts past the newest `receiptsKept`, and spend counters past their window. */
  #prune(now: number): void {
    this.#sql.exec("DELETE FROM model_reservations WHERE seq <= " +
                     "(SELECT MAX(seq) FROM model_reservations) - ? " +
                     "AND state NOT IN ('reserved', 'dispatched')", this.#limits.receiptsKept);
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

  /** In a unit: the newest receipts, a dead instance's open calls settled first. */
  receipts(now: number): UsageReceipt[] {
    this.recover(now);
    return this.#sql.exec<Record<string, SqlStorageValue>>(
      "SELECT id, created_at, connection_id, model, state, rule, max_output_tokens, " +
        "est_tokens, est_cost, tokens, cost, settled_at FROM model_reservations " +
        "ORDER BY seq DESC LIMIT ?", this.#limits.receiptsListed).toArray()
      .map((row) => ({
        reservation: row["id"] as string, at: row["created_at"] as number,
        connection: row["connection_id"] as string, model: row["model"] as string,
        state: row["state"] as State, rule: row["rule"] as string | null,
        max_output_tokens: row["max_output_tokens"] as number,
        est_tokens: row["est_tokens"] as number, est_cost: row["est_cost"] as number,
        tokens: row["tokens"] as number | null, cost: row["cost"] as number | null,
        settled_at: row["settled_at"] as number | null,
      }));
  }
}
