/**
 * Sandbox egress at the owner's object (gateway research §5.4 step 4; tasks 4 G4): each
 * model call the sandbox's clients send to the egress door is admitted and reserved here,
 * at open, and settled here once its bytes stopped flowing. The door (the binder) streams
 * the bytes through the gateway's metered `forward` in between. The object does no work
 * meanwhile, but it stays resident while it holds a stream's end signal (an open stream it
 * returned), so it is billed duration for the stream's length.
 *
 * **Open** (`open`, in the door's unit, after the binding check admitted the owner for
 * `work`), each refusal one of the fixed codes (`EgressCode`):
 * - the live lease, by the hash of the egress token's secret (`egress_lease_closed`);
 * - the egress generation the request's transform carries, and the one the sandbox's
 *   network was last applied under, are the current one (`egress_policy_stale`,
 *   `egress_network_stale`): a write that changes the egress table leaves egress refused,
 *   fail-closed, until the sandbox slice applies the new network;
 * - the connection is the owner's, active, its credential usable and its scope stored
 *   (`egress_connection_refused`), and the method and path are among its sealed endpoints,
 *   by the gateway's own rule (`egress_endpoint_refused`);
 * - a read (`GET`, `HEAD`: a model list) is free; any other call must be its source's model
 *   call as pi-ai makes it, which the gateway can meter (`meteredCall`), with a declared
 *   body length (`egress_unmetered`, `egress_length_required`): the run's caps are always
 *   set, so a call that cannot be metered is never sent;
 * - at most `open` at once and `perMinute` opened a minute (`egress_rate`);
 * - and a model call's charge (`#reserve`), by the loop's rules but on counters of its
 *   own, none of them pi's: its sandbox run's requests, tokens and cost (the run is the
 *   lease and its epoch, and 8b opens a lease per engine run; the model policy's per-turn
 *   limits are its caps), and the owner's and the source's day and month (shared with the
 *   loop's calls) (`egress_spend_refused`, the binding cap its `rule`). Its input is held
 *   at the largest context window, so the charge bounds the call's cost.
 *   Then one open row, the blob at its version, the output cap the charge leaves (the
 *   gateway clamps the request's own to it) and the stream's end signal. A refusal is kept
 *   as a receipt row, at most `refusalsPerMinute` a minute, so refused calls can neither
 *   flood the receipts nor write a row each.
 *
 * **Ending** (`end`): a policy write, a suspension, the owner's erasure, the connection's
 * revoke and the lease's close or replacement each end the in-flight streams they cover,
 * once their own unit committed: the object closes each one's end signal (the door stops the
 * stream on it) and marks its row with why. An end signal that errors (this instance gone)
 * is an end too.
 *
 * **Settle** (`settle`, `reduce`): the door reports each reservation's end once, with the
 * provider's status, the bytes it streamed and the gateway's metered outcome, which settles
 * the charge once: `succeeded` at the cost pi-ai read and the catalog priced, `failed`
 * (nothing sent, or the provider refused before any output), or `unknown`, keeping the
 * whole charge. A reservation never settled (the door's invocation lost) is settled `lost`
 * past `LOST_AFTER_S`, its charge `unknown`.
 *
 * Every row lives in `egress_reservations` (migration 14): at most `open` open, the newest
 * `receiptsKept` admitted and `refusalsKept` refused rows kept as receipts.
 *
 * Declared difference from research §6, which let a sandbox response already streaming
 * finish within the gateway's 5-minute deadline: here a policy write ends it, as it ends
 * the owner's in-flight turns.
 */
import { randomBytes } from "node:crypto";

import { apiRules, tierMultiplier } from "../../gateway/src/api-rules";
import { type EgressScope, egressTarget, meteredCall, meteredModel }
  from "../../gateway/src/credential";

import { Invalid } from "./errors";
import type { OwnerStorage } from "./gate";
import type { SandboxLease } from "./files";
import { costMicros, largestFitting, periods } from "./money";
import { EGRESS_DEADLINE_S, type EgressCode, type Outcome, OUTCOMES } from "./network";

export { EGRESS_DEADLINE_S, type EgressCode, type Outcome };

/**
 * How much egress one owner holds: 64 calls open at once (a sandbox's clients stream a
 * few), 600 opened a minute, 60 refusals receipted a minute, and the newest 2,000 admitted
 * and 500 refused rows kept as receipts.
 */
export interface EgressLimits {
  readonly open: number;
  readonly perMinute: number;
  readonly refusalsPerMinute: number;
  readonly receiptsKept: number;
  readonly refusalsKept: number;
  readonly receiptsListed: number;
}

export const EGRESS_LIMITS: EgressLimits = Object.freeze({
  open: 64,
  perMinute: 600,
  refusalsPerMinute: 60,
  receiptsKept: 2_000,
  refusalsKept: 500,
  receiptsListed: 50,
});

/** An open reservation older than this was its door's lost invocation's. */
export const LOST_AFTER_S = EGRESS_DEADLINE_S + 60;
/** The longest path an egress request names, as its row keeps it: visible ASCII. */
export const EGRESS_PATH_MAX = 2048;
/** A path the door sends: URL-serialized, so visible ASCII only. */
const PATH = /^\/[\x21-\x7e]*$/;

/** Why the object ended a stream. */
export type EndedBy = "policy" | "lease" | "connection" | "admission" | "erasure";

/**
 * What the binder knows of the sandbox from its token (`Binding.sandbox`): which token it
 * is, the SHA-256 of its secret, and for the egress token the egress generation E2B's
 * transform set beside it.
 */
export type SandboxBinding =
  | { readonly kind: "egress"; readonly token: string; readonly generation: number }
  | { readonly kind: "sync"; readonly token: string };

export interface EgressRequest {
  readonly connection: string;
  readonly method: string;
  /** The path (and query) after `/c/<connection>`, as the provider's API names it. */
  readonly path: string;
  /** The request body's declared length, or null. */
  readonly bytes_hint: number | null;
  /**
   * What the egress door read of the body for the reservation (`HINT_KEYS`): the model the
   * body names (its top-level `model`), and its tier option's value; null when absent.
   */
  readonly model: string | null;
  readonly tier: string | null;
}

/** An admitted call: what the door forwards with, and the object's end signal. */
export interface EgressOpened {
  readonly ok: true;
  readonly reservation: string;
  readonly connection: string;
  readonly blob: string;
  readonly version: number;
  readonly generation: number;
  /**
   * The call's metering, for a model call: the output cap its charge leaves, which the
   * gateway clamps the call to, and the body's length it was reserved for, which the
   * gateway refuses past. Null for a free call (a read), which the gateway sends unmetered.
   */
  readonly metering: { readonly max_output_tokens: number; readonly input_bytes: number;
                        readonly model: string; readonly tier: string | null } | null;
  /** Closed (or errored) when the object ends the stream; the door watches it. */
  readonly ended: ReadableStream<Uint8Array>;
}

export interface EgressRefusal {
  readonly ok: false;
  readonly code: EgressCode;
  /** The refusal's receipt row; null past the minute's refusal receipts. */
  readonly receipt: string | null;
}

/** How the gateway's meter saw a call end, as the model reservation settles. */
export type SpendOutcome =
  | { readonly state: "succeeded"; readonly tokens: number; readonly cost: number;
      /** The model the answer says served it, when other than the one the call named. */
      readonly served?: string }
  | { readonly state: "failed" }
  | { readonly state: "unknown" };

export interface SettleRequest {
  readonly reservation: string;
  readonly outcome: Outcome;
  readonly status: number | null;
  readonly bytes: number;
  /** The gateway's metered outcome; the cost in dollars, as pi-ai reports it. */
  readonly spend: SpendOutcome;
}

/** What one reservation row shows its owner. */
export interface EgressReceipt {
  readonly reservation: string;
  readonly connection: string;
  readonly method: string;
  readonly path: string;
  readonly state: "open" | "settled" | "refused";
  readonly code: string | null;
  readonly ended: EndedBy | null;
  readonly outcome: string | null;
  readonly status: number | null;
  readonly bytes: number | null;
  /** A model call's charge (null for a free call or a refusal), and what it held and used. */
  readonly charge: Charge | null;
  /** The cap a spend refusal names. */
  readonly rule: string | null;
  readonly max_output_tokens: number | null;
  readonly est_cost: number | null;
  readonly cost: number | null;
  readonly tokens: number | null;
  /** The model the answer says served it, when other than the one the call named. */
  readonly served: string | null;
  readonly opened_at: number;
  readonly settled_at: number | null;
}

/**
 * The owner's model policy caps a sandbox model call is held to, in µ$ for money, as the
 * policy leaves them at the open's unit: its run's requests, tokens and cost (the `models`
 * table's per-turn limits), and the owner's and the call's source's day and month. No
 * more and no less: the spend table is the agent's purchases', not model calls'.
 */
export interface EgressCaps {
  readonly run_requests: number;
  readonly run_tokens: number;
  readonly run_cost: number;
  readonly daily_cost: number | null;
  readonly monthly_cost: number | null;
  readonly source_daily_cost: number | null;
  readonly source_monthly_cost: number | null;
}

/** A connection's model as a sandbox call is priced by it: opaque here but for its bounds. */
export interface EgressModel {
  readonly id: string;
  readonly maxTokens: number;
  readonly contextWindow: number;
}

/** What `open` reads beside its own rows, each at the open's unit. */
export interface EgressDeps {
  readonly lease: (now: number) => SandboxLease | null;
  readonly generation: () => number;
  /**
   * The connection's blob at its version, or null for one that is not the owner's, not
   * active, or whose credential has expired.
   */
  readonly credential: (connection: string, nowMs: number) =>
    { readonly blob: string; readonly version: number } | null;
  /** The connection's egress scope, source and models; null as `credential`'s. */
  readonly connection: (connection: string) =>
    { readonly scope: EgressScope; readonly source: string;
      readonly models: readonly EgressModel[] } | null;
  /** The caps for a call of `source`. */
  readonly caps: (source: string) => EgressCaps;
  /** The µ$ held in a period (`d:<day>`, `m:<month>`) by `source`, or by every source. */
  readonly spent: (period: string, source: string | null) => number;
  /**
   * The most µ$ a call of `input` and `output` tokens on any of `models` can cost (the
   * catalog's rates, by pi-ai's `calculateCost`, each model's tokens at most its own
   * context window and output cap): non-decreasing in the output.
   */
  readonly price: (models: readonly EgressModel[], input: number, output: number) => number;
  /** In the open's unit: the spend counters past their window pruned, as the loop's reserve. */
  readonly pruned: (now: number) => void;
}

/** The fewest output tokens a sandbox model call is worth reserving, as a loop call's. */
export const MIN_OUTPUT = 1_024;
/** The calls that carry no model use: a sealed endpoint's reads (a model list). */
const FREE_METHODS: readonly string[] = Object.freeze(["GET", "HEAD"]);

const METHODS: readonly string[] = Object.freeze(["GET", "HEAD", "POST", "PUT", "PATCH",
                                                  "DELETE"]);
const CONNECTION = /^c[0-9a-f]{24}$/;
const HEX32 = /^[0-9a-f]{32}$/;
const HEX64 = /^[0-9a-f]{64}$/;
/** A model id or a tier value the door read: visible ASCII. */
const HINT = /^[\x21-\x7e]{1,256}$/;
/** A served model's id, as a receipt keeps it: visible ASCII. */
const SERVED = /^[\x21-\x7e]{1,256}$/;

type Charge = "held" | "succeeded" | "failed" | "unknown";

/** What a model call's charge holds, as its row records it. */
interface Charged {
  readonly source: string;
  readonly max_output_tokens: number;
  readonly est_tokens: number;
  readonly est_cost: number;
  readonly day: string;
  readonly month: string;
}

type Row = {
  id: string; lease_id: string | null; epoch: number | null; token: string;
  source: string | null; charge: Charge | null; rule: string | null;
  max_output_tokens: number | null; est_tokens: number | null; est_cost: number | null;
  tokens: number | null; cost: number | null; served: string | null;
  connection_id: string; version: number | null; method: string;
  path: string; generation: number; bytes_hint: number | null; opened_at: number;
  state: EgressReceipt["state"]; code: string | null; ended: EndedBy | null;
  outcome: string | null; status: number | null; bytes: number | null;
  settled_at: number | null;
};

function plain(value: unknown, keys: string): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    && Object.keys(value).sort().join() === keys;
}

/** The request's fields, exactly, or `Invalid`. */
export function egressRequest(value: unknown): EgressRequest {
  if (!plain(value, "bytes_hint,connection,method,model,path,tier")) {
    throw new Invalid("invalid egress request");
  }
  const { connection, method, path, bytes_hint: hint, model, tier } = value;
  const named = (text: unknown) => text === null || (typeof text === "string" && HINT.test(text));
  if (typeof connection !== "string" || !CONNECTION.test(connection)
      || typeof method !== "string" || !METHODS.includes(method)
      || typeof path !== "string" || !PATH.test(path) || path.length > EGRESS_PATH_MAX
      || (hint !== null && (typeof hint !== "number" || !Number.isSafeInteger(hint) || hint < 0))
      || !named(model) || !named(tier)) {
    throw new Invalid("invalid egress request");
  }
  return { connection, method, path, bytes_hint: hint, model: model as string | null,
           tier: tier as string | null };
}

/** The binding's sandbox fields, exactly, or `Invalid`. */
export function sandboxBinding(value: unknown): SandboxBinding {
  if (plain(value, "generation,kind,token") && value["kind"] === "egress"
      && typeof value["token"] === "string" && HEX64.test(value["token"])
      && typeof value["generation"] === "number" && Number.isSafeInteger(value["generation"])
      && value["generation"] >= 1) {
    return { kind: "egress", token: value["token"], generation: value["generation"] };
  }
  if (plain(value, "kind,token") && value["kind"] === "sync"
      && typeof value["token"] === "string" && HEX64.test(value["token"])) {
    return { kind: "sync", token: value["token"] };
  }
  throw new Invalid("invalid sandbox binding");
}

function spendOutcome(value: unknown): SpendOutcome {
  if (plain(value, "state") && (value["state"] === "failed" || value["state"] === "unknown")) {
    return { state: value["state"] };
  }
  const served = typeof value === "object" && value !== null && "served" in value
    ? (value as { served: unknown }).served : undefined;
  if ((plain(value, "cost,state,tokens")
       || (plain(value, "cost,served,state,tokens") && typeof served === "string"
           && SERVED.test(served)))
      && value["state"] === "succeeded"
      && typeof value["tokens"] === "number" && Number.isSafeInteger(value["tokens"])
      && value["tokens"] >= 0 && typeof value["cost"] === "number"
      && Number.isFinite(value["cost"]) && value["cost"] >= 0) {
    return { state: "succeeded", tokens: value["tokens"], cost: value["cost"],
             ...(served === undefined ? {} : { served: served as string }) };
  }
  throw new Invalid("invalid egress settle");
}

/** The settle's fields, exactly, or `Invalid`. */
export function settleRequest(value: unknown): SettleRequest {
  if (!plain(value, "bytes,outcome,reservation,spend,status")) {
    throw new Invalid("invalid egress settle");
  }
  const { reservation, outcome, status, bytes, spend } = value;
  if (typeof reservation !== "string" || !HEX32.test(reservation)
      || typeof outcome !== "string" || !OUTCOMES.includes(outcome)
      || (status !== null && (typeof status !== "number" || !Number.isSafeInteger(status)
                              || status < 100 || status > 599))
      || typeof bytes !== "number" || !Number.isSafeInteger(bytes) || bytes < 0) {
    throw new Invalid("invalid egress settle");
  }
  return { reservation, outcome: outcome as Outcome, status: status as number | null, bytes,
           spend: spendOutcome(spend) };
}

export class Egress {
  readonly #storage: OwnerStorage;
  readonly #deps: EgressDeps;
  readonly #limits: EgressLimits;
  /** Each open reservation's end signal, by reservation, with what it covers. */
  readonly #live = new Map<string, { readonly connection: string; readonly lease: string;
                                     readonly end: WritableStreamDefaultWriter<Uint8Array> }>();

  constructor(storage: OwnerStorage, deps: EgressDeps, limits: EgressLimits = EGRESS_LIMITS) {
    this.#storage = storage;
    this.#deps = deps;
    this.#limits = limits;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /**
   * The live lease the egress token names, or null: a lease whose egress token's hash is
   * the one presented. Read before anything else an open does (an OAuth refresh among it).
   */
  lease(sandbox: SandboxBinding, now: number): SandboxLease | null {
    const lease = this.#deps.lease(now);
    return lease !== null && sandbox.kind === "egress" && lease.egress_token === sandbox.token
      ? lease : null;
  }

  /**
   * In the door's unit: the call admitted and reserved, an open row and its end signal; or
   * refused, with its fixed code, kept as a receipt row within the minute's refusals.
   */
  open(sandbox: SandboxBinding, request: EgressRequest, now: number):
      EgressOpened | EgressRefusal {
    this.#recover(now);
    const lease = this.lease(sandbox, now);
    const generation = this.#deps.generation();
    const refuse = (code: EgressCode, rule?: string): EgressRefusal => {
      if (this.#count("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'refused' " +
                        "AND opened_at > ?", now - 60) >= this.#limits.refusalsPerMinute) {
        return { ok: false, code, receipt: null };
      }
      return { ok: false, code,
               receipt: this.#record(sandbox, request, lease, generation, now,
                                     { code, ...(rule === undefined ? {} : { rule }) }) };
    };
    if (lease === null || sandbox.kind !== "egress") {
      return refuse("egress_lease_closed");
    }
    if (sandbox.generation !== generation) {
      return refuse("egress_policy_stale");
    }
    if (lease.network_generation !== generation) {
      return refuse("egress_network_stale");
    }
    const credential = this.#deps.credential(request.connection, now * 1000);
    const connection = this.#deps.connection(request.connection);
    if (credential === null || connection === null) {
      return refuse("egress_connection_refused");
    }
    if (egressTarget(connection.scope, request.method, request.path) === null) {
      return refuse("egress_endpoint_refused");
    }
    // A read of a sealed endpoint carries no model use; every other call must be the
    // source's model call as pi-ai makes it, which the gateway meters.
    const free = FREE_METHODS.includes(request.method);
    if (!free && (!meteredCall(connection.scope, request.method, request.path)
                  || connection.models.length === 0)) {
      return refuse("egress_unmetered");
    }
    if (!free && (request.bytes_hint === null || request.bytes_hint === 0)) {
      return refuse("egress_length_required");
    }
    if (this.#count("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'open'")
          >= this.#limits.open
        || this.#count("SELECT COUNT(*) AS n FROM egress_reservations WHERE opened_at > ? " +
                         "AND state != 'refused'", now - 60) >= this.#limits.perMinute) {
      return refuse("egress_rate");
    }
    this.#runsPruned(lease);
    let charge: Charged | null = null;
    let named: { readonly model: string; readonly tier: string | null } | null = null;
    if (!free) {
      // The model the call names, where its API's rules read it (the body's top-level key
      // the door read, or the path), and its tier: the call is reserved for them alone.
      const rules = apiRules(connection.scope.api);
      const meter = connection.scope.meter!;
      if (rules === null || (rules.model !== null && rules.model !== "model")) {
        return refuse("egress_unmetered");
      }
      const id = rules.model === null
        ? meteredModel(meter, request.method, request.path.split("?")[0]!, {})
        : request.model;
      const model = connection.models.find((entry) => entry.id === id);
      if (model === undefined) {
        return refuse("egress_unknown_model");
      }
      const multiplier = tierMultiplier(rules, request.tier);
      if (multiplier === null) {
        return refuse("egress_unpriced_option");
      }
      this.#deps.pruned(now);
      const reserved = this.#reserve(lease, connection.source, model, multiplier, now);
      if (!reserved.ok) {
        return refuse("egress_spend_refused", reserved.rule);
      }
      charge = reserved;
      named = { model: model.id, tier: rules.tier === null ? null : request.tier };
    }
    const reservation = this.#record(sandbox, request, lease, generation, now,
                                     { version: credential.version,
                                       ...(charge === null ? {} : { charge }) });
    const { readable, writable } = new IdentityTransformStream();
    this.#live.set(reservation, { connection: request.connection, lease: lease.lease_id,
                                  end: writable.getWriter() });
    return { ok: true, reservation, connection: request.connection, blob: credential.blob,
             version: credential.version, generation,
             metering: charge === null || named === null ? null
               : { max_output_tokens: charge.max_output_tokens, input_bytes: request.bytes_hint!,
                   model: named.model, tier: named.tier },
             ended: readable };
  }

  /**
   * The call's charge, against every cap, in the open's unit, so concurrent calls reserve
   * one after another and never pass a cap between them: its run's requests, tokens and
   * cost (`egress_runs`), and the owner's and the source's day and month (the spend
   * counters the loop's calls hold too, read through `spent`). It is the named model's:
   * its input held at that model's context window (a body's bytes do not bound its tokens:
   * a file or a cached context it names can be far larger), its output at that model's cap
   * and what the caps leave (the gateway clamps every cap alias the call names to it, and
   * refuses several choices and another model), at that model's dearest rates times its
   * tier's `multiplier`, so what it holds bounds what it can cost; the settle gives the
   * difference back. Refused, with the cap that binds, below `MIN_OUTPUT`.
   */
  #reserve(lease: SandboxLease, source: string, model: EgressModel, multiplier: number,
           now: number): ({ ok: true } & Charged) | { ok: false; rule: string } {
    const models = [model];
    const input = model.contextWindow;
    const caps = this.#deps.caps(source);
    const run = this.#sql.exec<{ requests: number; tokens: number; cost: number }>(
      "SELECT requests, tokens, cost FROM egress_runs WHERE lease_id = ? AND epoch = ?",
      lease.lease_id, lease.epoch).toArray()[0] ?? { requests: 0, tokens: 0, cost: 0 };
    if (run.requests >= caps.run_requests) {
      return { ok: false, rule: "run_requests" };
    }
    const { day, month } = periods(now);
    const rooms: [string, number][] = [["run_cost", caps.run_cost - run.cost]];
    const ceilings: [string, number | null, string, string | null][] = [
      ["daily_cost", caps.daily_cost, `d:${day}`, null],
      ["monthly_cost", caps.monthly_cost, `m:${month}`, null],
      ["source_daily_cost", caps.source_daily_cost, `d:${day}`, source],
      ["source_monthly_cost", caps.source_monthly_cost, `m:${month}`, source],
    ];
    for (const [rule, cap, period, by] of ceilings) {
      if (cap !== null) {
        rooms.push([rule, cap - this.#deps.spent(period, by)]);
      }
    }
    const [costRule, costRoom] = rooms.reduce((a, b) => (b[1] < a[1] ? b : a));
    const most = Math.max(...models.map((model) => model.maxTokens));
    const floor = Math.min(most, MIN_OUTPUT);
    const byTokens = caps.run_tokens - run.tokens - input;
    const price = (output: number) =>
      Math.ceil(this.#deps.price(models, input, output) * multiplier);
    const fits = (output: number) => price(output) <= costRoom;
    const output = largestFitting(Math.min(most, byTokens), fits);
    if (output < floor || output < 1) {
      const byTokensBinds = byTokens < floor && (byTokens < 0 || fits(byTokens));
      return { ok: false, rule: byTokensBinds ? "run_tokens" : costRule };
    }
    return { ok: true, source, max_output_tokens: output, est_tokens: input + output,
             est_cost: price(output), day, month };
  }

  /** The run counters of every run but `lease`'s that holds no open call: theirs are over. */
  #runsPruned(lease: SandboxLease): void {
    this.#sql.exec("DELETE FROM egress_runs WHERE NOT (lease_id = ? AND epoch = ?) " +
                     "AND NOT EXISTS (SELECT 1 FROM egress_reservations AS r WHERE " +
                     "r.lease_id = egress_runs.lease_id AND r.epoch = egress_runs.epoch " +
                     "AND r.state = 'open')", lease.lease_id, lease.epoch);
  }

  /**
   * In the door's unit: a reservation's one end, as the door and the gateway's meter saw
   * it; its model reservation settled with it. False for one already settled, lost or
   * refused, or presented by another token.
   */
  settle(sandbox: SandboxBinding, request: SettleRequest, now: number):
      { settled: false } | { settled: true; connection: string; version: number } {
    this.#recover(now);
    const row = this.#sql.exec<Row>("SELECT * FROM egress_reservations WHERE id = ?",
                                    request.reservation).toArray()[0];
    if (row === undefined || row.state !== "open" || sandbox.kind !== "egress"
        || row.token !== sandbox.token || row.version === null) {
      return { settled: false };
    }
    this.#sql.exec("UPDATE egress_reservations SET state = 'settled', outcome = ?, status = ?, " +
                     "bytes = ?, settled_at = ? WHERE id = ?",
                   request.outcome, request.status, request.bytes, now, request.reservation);
    if (row.charge === "held") {
      // pi-ai's cost, read and priced by the gateway; what it cannot read keeps it all.
      const spend = request.spend;
      this.#sql.exec("UPDATE egress_reservations SET charge = ?, tokens = ?, cost = ?, " +
                       "served = ? WHERE id = ? AND charge = 'held'", spend.state,
                     spend.state === "succeeded" ? spend.tokens : spend.state === "failed" ? 0
                       : null,
                     spend.state === "succeeded" ? costMicros(spend.cost)
                       : spend.state === "failed" ? 0 : null,
                     spend.state === "succeeded" ? spend.served ?? null : null,
                     request.reservation);
    }
    this.#release(request.reservation);
    this.#prune();
    return { settled: true, connection: row.connection_id, version: row.version };
  }

  /**
   * In a unit, once the change that ends them committed: every open stream `covers` takes
   * (all of them by default) ended, its signal closed and its row marked `why`.
   */
  end(why: EndedBy, covers: (live: { connection: string; lease: string }) => boolean =
        () => true): number {
    let ended = 0;
    for (const [reservation, live] of [...this.#live]) {
      if (!covers(live)) {
        continue;
      }
      this.#sql.exec("UPDATE egress_reservations SET ended = ? WHERE id = ? AND state = 'open' " +
                       "AND ended IS NULL", why, reservation);
      this.#release(reservation);
      ended++;
    }
    return ended;
  }

  /**
   * Every end signal closed, writing nothing: the owner's erasure, whose wipe takes the
   * rows. Each door then settles into a refusal, and its stream is over.
   */
  drop(): void {
    for (const reservation of [...this.#live.keys()]) {
      this.#release(reservation);
    }
  }

  /** The newest receipts, newest first: what was admitted, refused and how each ended. */
  receipts(): EgressReceipt[] {
    return this.#sql.exec<Row>("SELECT * FROM egress_reservations ORDER BY opened_at DESC, " +
                                 "rowid DESC LIMIT ?", this.#limits.receiptsListed).toArray()
      .map((row) => ({ reservation: row.id, connection: row.connection_id, method: row.method,
                       path: row.path, state: row.state, code: row.code, ended: row.ended,
                       outcome: row.outcome, status: row.status, bytes: row.bytes,
                       charge: row.charge, rule: row.rule,
                       max_output_tokens: row.max_output_tokens, est_cost: row.est_cost,
                       cost: row.cost, tokens: row.tokens, served: row.served,
                       opened_at: row.opened_at,
                       settled_at: row.settled_at }));
  }

  #count(statement: string, ...params: SqlStorageValue[]): number {
    return this.#sql.exec<{ n: number }>(statement, ...params).one().n;
  }

  #record(sandbox: SandboxBinding, request: EgressRequest, lease: SandboxLease | null,
          generation: number, now: number,
          fields: { code?: EgressCode; rule?: string; version?: number; charge?: Charged }):
      string {
    const id = randomBytes(16).toString("hex");
    const charge = fields.charge;
    this.#sql.exec(
      "INSERT INTO egress_reservations (id, lease_id, epoch, token, source, charge, rule, " +
        "max_output_tokens, est_tokens, est_cost, day, month, connection_id, version, method, " +
        "path, generation, bytes_hint, opened_at, state, code, settled_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      id, lease?.lease_id ?? null, lease?.epoch ?? null, sandbox.token,
      charge?.source ?? null, charge === undefined ? null : "held", fields.rule ?? null,
      charge?.max_output_tokens ?? null, charge?.est_tokens ?? null, charge?.est_cost ?? null,
      charge?.day ?? null, charge?.month ?? null,
      request.connection, fields.version ?? null, request.method, request.path, generation,
      request.bytes_hint, now, fields.code === undefined ? "open" : "refused",
      fields.code ?? null, fields.code === undefined ? null : now);
    this.#prune();
    return id;
  }

  /** Its end signal closed and forgotten; the door sees the close as the end. */
  #release(reservation: string): void {
    const live = this.#live.get(reservation);
    this.#live.delete(reservation);
    live?.end.close().catch(() => undefined);  // a door that stopped reading is gone already
  }

  /** Open rows past `LOST_AFTER_S`: their doors' invocations were lost; their spend unknown. */
  #recover(now: number): void {
    const lost = this.#sql.exec<{ id: string }>(
      "UPDATE egress_reservations SET state = 'settled', outcome = 'lost', settled_at = ? " +
        "WHERE state = 'open' AND opened_at <= ? RETURNING id", now, now - LOST_AFTER_S)
      .toArray();
    for (const { id } of lost) {
      // What it cost is unknown: it keeps its whole charge.
      this.#sql.exec("UPDATE egress_reservations SET charge = 'unknown' WHERE id = ? " +
                       "AND charge = 'held'", id);
      this.#release(id);
    }
  }

  /** Each bucket's rows past its newest kept, oldest first: admitted and refused apart. */
  #prune(): void {
    for (const [states, kept] of [["state = 'settled'", this.#limits.receiptsKept],
                                  ["state = 'refused'", this.#limits.refusalsKept]] as const) {
      const over = this.#count(`SELECT COUNT(*) AS n FROM egress_reservations WHERE ${states}`)
        - kept;
      if (over > 0) {
        this.#sql.exec("DELETE FROM egress_reservations WHERE id IN (SELECT id FROM " +
                         `egress_reservations WHERE ${states} ORDER BY opened_at, rowid ` +
                         "LIMIT ?)", over);
      }
    }
  }
}
