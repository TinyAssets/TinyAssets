/**
 * Sandbox egress at the owner's object (gateway research §5.4 step 4): each
 * model call the sandbox's clients send to the egress door is admitted here at open,
 * reserved here once the gateway parsed its body (egress shape research, A′), and settled
 * here once its bytes stopped flowing. The door (the binder) streams the bytes through the
 * gateway's metered `forward` in between, and the gateway asks for the reserve through it. The
 * object does no work meanwhile, but it stays resident while it holds a stream's end signal (an
 * open stream it returned), so it is billed duration for the stream's length.
 *
 * **Open** (`open`, in the door's unit, after the binding check admitted the owner for
 * `work`), each refusal one of the fixed codes (`EgressCode`):
 * - the live lease, by the hash of the egress token's secret (`egress_lease_closed`);
 * - the run the call's path names is live under that lease, its grant standing (a cancel
 *   revokes it at once) and the call presenting that run's own secret, which only its CLI's
 *   environment holds (`egress_run_closed`): a run id alone authorizes nothing, so no
 *   process in the sandbox spends as another run; and the connection is one its run grant
 *   holds (`egress_run_connection`, `runs.ts`): its model connection (none for a run on the
 *   vendor's own sign-in), or one of its connectors, a service's key the run's admission
 *   bound to one of its agent's MCP servers by that server's URL (`launch.ts`), each only in
 *   its own role, so a run never calls on a connection it was not given;
 * - the egress generation the request's transform carries is the current one
 *   (`egress_policy_stale`), and so is the one the sandbox's network was last applied
 *   under or the one a re-apply is out with (`egress_network_stale`): a write that
 *   changes the egress table leaves egress refused, fail-closed, until the `runs` lane
 *   re-applies the lease's network under the new generation (`Launcher.reapply`), its
 *   calls admitted once that re-apply is sent, since only the new network's transform
 *   sets the new generation;
 * - the connection is the owner's, active, its credential usable and its scope stored
 *   (`egress_connection_refused`), and the method and path are among its sealed endpoints,
 *   by the gateway's own rule, and a connector's server one the owner's egress policy still
 *   lets out (`egress_endpoint_refused`);
 * - a service's every call on its sealed URL is free (it reaches no model), a read (`GET`,
 *   `HEAD`: a model list) is free, as is a call its API's rules cite as free
 *   (`freeCall`: a token count); any other call must be its source's model
 *   call as pi-ai makes it, which the gateway can meter (`meteredCall`), with a declared
 *   body length (`egress_unmetered`, `egress_length_required`): the run's caps are always
 *   set, so a call that cannot be metered is never sent;
 * - at most `EGRESS_LIMITS.open` open at once, whichever runs opened them (`egress_rate`):
 *   each open call is a row the owner-bound guard costs;
 * - and a model call's API has the rules the gateway meters it by (`egress_unmetered`).
 *   Then one open row, uncharged, the blob at its version and the stream's end signal. A
 *   refusal is kept as a receipt row, the newest `refusalsKept` of them.
 *
 * **Reserve** (`reserve`, in the door's unit, once the gateway parsed the admitted model
 * call's body; its model and tier are what that one parse read, since the model may be a
 * body's last key): the model one the connection lists (and, for an API whose path names
 * it, the path's) (`egress_unknown_model`), the tier one its rules price
 * (`egress_unpriced_option`), and the call's charge (`#reserve`): its run's requests, tokens
 * and cost (on its `runs` row; the model policy's per-run limits are its caps), and the
 * owner's and the source's day and month (`model_spend`, the spend counters its charge's
 * triggers move, which the runs' own limits read too, `spent`) (`egress_spend_refused`, the
 * binding cap its `rule`). Its input is held at the largest context window, so the charge
 * bounds the call's cost. Then the open row charged and the output cap the charge leaves (the gateway clamps
 * the request's own to it); or the row a refusal receipt and its stream ended. Until it is
 * reserved an admitted call is open as any other (it counts to the rate, is beaten, and
 * settled lost when its beats stop), charged nothing.
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
 * whole charge. While it streams, its door beats it every `EGRESS_BEAT_S`; a reservation
 * whose beats stop (its door's invocation lost: a deploy, an isolate's restart) is settled
 * `lost` past `LOST_AFTER_S` without one, its charge `unknown`, and any open one past its
 * door's own deadline (`EGRESS_DEADLINE_S`) is too.
 *
 * Every row lives in `egress_reservations` (migration 11): at most `EGRESS_LIMITS.open` open,
 * the newest `receiptsKept` admitted and `refusalsKept` refused rows kept as receipts.
 *
 * Declared difference from research §6, which let a sandbox response already streaming
 * finish within its deadline (`EGRESS_DEADLINE_S`): here a policy write ends it, as it ends
 * the owner's in-flight turns.
 */
import type { SandboxToken } from "../../contract/src/binding";
import { keyed } from "../../contract/src/checks";
import { CONNECTION_ID, HEX32, randomHex, RUN_ID } from "../../contract/src/ids";

import { apiRules, freeCall, tierMultiplier } from "../../contract/src/api-rules";
import {
  type EgressScope, egressTarget, meteredCall, meteredModel,
} from "../../contract/src/egress-scope";

import type { OwnerStorage } from "./gate";
import type { SandboxLease } from "./files";
import { costMicros, keptFrom, largestFitting, periods, priced, SETTLED_WITHIN_S } from "./money";
import { EGRESS_BEAT_S, type Outcome, OUTCOMES } from "../../contract/src/egress";
import { type MeterOutcome, MODEL_NAME } from "../../contract/src/gateway";
import type {
  EgressOpened, EgressRefusal, EgressRequest, EgressReserved, ReserveRequest,
} from "../../contract/src/owner";
import { type EgressCode, Refusal } from "../../contract/src/refusal";
import { EGRESS_PATH_MAX } from "../../contract/src/limits";
import type { ModelRecord } from "../../contract/src/model-record";
import type { Call, Door, DoorUnits } from "./door";
import type { Runs } from "./runs";
import { clock, clockMs } from "./clock";


/**
 * How much egress one owner holds: 16,000 calls open at once, whichever of its runs opened
 * them, each a row the owner-bound guard costs (`egress_reservations`), the object's 10 GB
 * the ceiling, and each its end signal in the object's isolate (`OPEN_CALL_BYTES`), which
 * the counts test counts 16,000 of for each owner object. One owner holds far fewer: the
 * paid tier's 10 runs of Claude Code, each its main loop and the 20 subagents it runs at once
 * (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`'s default in 2.1.309), stream 210. It is the room the guard held before, 16 a run at the
 * most runs a tier may give, as one count, so a busy run is never refused while the
 * others idle. And the newest 2,000 admitted and 500 refused rows kept as receipts.
 */
interface EgressLimits {
  readonly open: number;
  readonly receiptsKept: number;
  readonly refusalsKept: number;
  readonly receiptsListed: number;
}

export const EGRESS_LIMITS: EgressLimits = Object.freeze({
  open: 16_000,
  receiptsKept: 2_000,
  refusalsKept: 500,
  receiptsListed: 50,
});

/**
 * What one open call holds in the owner isolate: its end signal (`#live`'s entry, the writer
 * of the stream its door reads and that stream's pipe over RPC). What it covers is its
 * row's, never copied beside it. The workerd memory leg opens `EGRESS_LIMITS.open` of them
 * through `open`, each read from another isolate as the door reads it, and holds what they
 * hold within this (`memory/leg.mjs`); the counts test counts `EGRESS_LIMITS.open` of them
 * for each owner object (`guards/memory-counts.test.ts`).
 */
export const OPEN_CALL_BYTES = 256;

/**
 * An open reservation with no beat for this long was its door's lost invocation's: three
 * beats missed, so a slow beat is never taken for a lost door. The one-day total stays the
 * door's own deadline (`EGRESS_DEADLINE_S`), a beat past which an open row is lost too
 * (`money.ts` `SETTLED_WITHIN_S`).
 */
export const LOST_AFTER_S = 3 * EGRESS_BEAT_S;
/** A path the door sends: URL-serialized, so visible ASCII only. */
const PATH = /^\/[\x21-\x7e]*$/;

/** Why the object ended a stream. */
type EndedBy = "policy" | "lease" | "connection" | "admission" | "erasure" | "run";

/** What an open stream covers, as `end` selects them. */
interface LiveStream {
  readonly connection: string;
  readonly lease: string;
  readonly run: string;
}


interface SettleRequest {
  readonly reservation: string;
  readonly outcome: Outcome;
  readonly status: number | null;
  readonly bytes: number;
  /** The gateway's metered outcome; the cost in dollars, as pi-ai reports it. */
  readonly spend: MeterOutcome;
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
 * more and no less: an agent's purchases are an action rule's (`spend`, `acts.json`).
 */
interface EgressCaps {
  /** The owner's own per-run caps, each null (none) unless the owner's policy sets it. */
  readonly run_requests: number | null;
  readonly run_tokens: number | null;
  readonly run_cost: number | null;
  readonly daily_cost: number | null;
  readonly monthly_cost: number | null;
  readonly source_daily_cost: number | null;
  readonly source_monthly_cost: number | null;
}

/**
 * Egress pricing: a sandbox call's most cost before its model is known, input at its
 * dearest of uncached, cache-read and cache-write rates, each model's tokens at its rates by
 * pi-ai's `calculateCost` (`money.ts` `priced`).
 */

/** A model's cost record with its input at its dearest rate: what a token can cost at most. */
function dearest(model: ModelRecord): ModelRecord {
  const top = (rates: { input: number; cacheRead: number; cacheWrite: number }) =>
    Math.max(rates.input, rates.cacheRead, rates.cacheWrite);
  return { ...model, cost: { ...model.cost, input: top(model.cost),
                             ...(model.cost.tiers === undefined ? {}
                               : { tiers: model.cost.tiers.map((tier) =>
                                 ({ ...tier, input: top(tier) })) }) } };
}

/**
 * The most µ$ a sandbox call of `input` and `output` tokens can cost on any of `models`,
 * each model's tokens at most its own context window and output cap, at its dearest rates.
 */
export function egressPrice(models: readonly ModelRecord[], input: number,
                            output: number): number {
  return Math.max(...models.map((model) => priced(dearest(model),
                                                  Math.min(input, model.contextWindow),
                                                  Math.min(output, model.maxTokens))));
}


/** What `open` reads beside its own rows, each at the open's unit. */
interface EgressDeps {
  /** The live lease whose egress token's hash is `token`, or null. */
  readonly lease: (token: string, now: number) => SandboxLease | null;
  /**
   * The connections of `run`'s grant, when `run` is live under the lease, its grant standing
   * and `secret` the hash of its own secret (`undefined` otherwise): its model connection
   * (null for a run on the vendor's own sign-in) and its connectors.
   */
  readonly runConnections: (run: string, secret: string | undefined, lease: SandboxLease) =>
    { readonly connection: string | null; readonly connectors: readonly string[] } | undefined;
  /**
   * Whether the owner's egress policy, as it stands, lets the sandbox reach the server at
   * `url` (`network.ts` `serverUnreached`).
   */
  readonly reaches: (url: string) => boolean;
  /** `run`'s requests, tokens and cost so far (its `runs` row). */
  readonly runUsage: (run: string) => { requests: number; tokens: number; cost: number };
  readonly generation: () => number;
  /**
   * The connection's blob at its version, or null for one that is not the owner's, not
   * active, or whose credential has expired.
   */
  readonly credential: (connection: string, nowMs: number) =>
    { readonly blob: string; readonly version: number } | null;
  /** The connection's egress scope, source and models; null as `credential`'s. */
  readonly connection: (connection: string) =>
    { readonly scope: EgressScope; readonly source: string; readonly provider: string;
      readonly models: readonly ModelRecord[] } | null;
  /** The caps for a call of `source`. */
  readonly caps: (source: string) => EgressCaps;
}

/**
 * The fewest output tokens a sandbox model call is worth reserving: provider thinking budgets
 * start at 1,024.
 */
const MIN_OUTPUT = 1_024;

const METHODS: readonly string[] = Object.freeze(["GET", "HEAD", "POST", "PUT", "PATCH",
                                                  "DELETE"]);

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
  id: string; lease_id: string | null; epoch: number | null; run: string; token: string;
  source: string | null; charge: Charge | null; rule: string | null;
  max_output_tokens: number | null; est_tokens: number | null; est_cost: number | null;
  tokens: number | null; cost: number | null; served: string | null;
  connection_id: string; version: number | null; method: string;
  path: string; generation: number; bytes_hint: number | null; opened_at: number;
  state: EgressReceipt["state"]; code: string | null; ended: EndedBy | null;
  outcome: string | null; status: number | null; bytes: number | null;
  settled_at: number | null;
};

/** The request's fields, exactly, or `invalid`. */
export function egressRequest(value: unknown): EgressRequest {
  if (!keyed(value, ["bytes_hint", "connection", "method", "path", "run"])) {
    throw new Refusal("invalid", "invalid egress request");
  }
  const { run, connection, method, path, bytes_hint: hint } = value;
  if (typeof run !== "string" || !RUN_ID.test(run)
      || typeof connection !== "string" || !CONNECTION_ID.test(connection)
      || typeof method !== "string" || !METHODS.includes(method)
      || typeof path !== "string" || !PATH.test(path) || path.length > EGRESS_PATH_MAX
      || (hint !== null && (typeof hint !== "number" || !Number.isSafeInteger(hint) || hint < 0))) {
    throw new Refusal("invalid", "invalid egress request");
  }
  return { run, connection, method, path, bytes_hint: hint };
}

/** A reserve's fields, exactly, or `invalid`. */
function reserveRequest(value: unknown): ReserveRequest {
  if (!keyed(value, ["model", "reservation", "tier"])) {
    throw new Refusal("invalid", "invalid egress reserve");
  }
  const { reservation, model, tier } = value;
  if (typeof reservation !== "string" || !HEX32.test(reservation)
      || typeof model !== "string" || !MODEL_NAME.test(model)
      || (tier !== null && (typeof tier !== "string" || !MODEL_NAME.test(tier)))) {
    throw new Refusal("invalid", "invalid egress reserve");
  }
  return { reservation, model, tier: tier as string | null };
}

function spendOutcome(value: unknown): MeterOutcome {
  if (keyed(value, ["state"]) && (value["state"] === "failed" || value["state"] === "unknown")) {
    return { state: value["state"] };
  }
  const served = typeof value === "object" && value !== null && "served" in value
    ? (value as { served: unknown }).served : undefined;
  if ((keyed(value, ["cost", "state", "tokens"])
       || (keyed(value, ["cost", "served", "state", "tokens"]) && typeof served === "string"
           && MODEL_NAME.test(served)))
      && value["state"] === "succeeded"
      && typeof value["tokens"] === "number" && Number.isSafeInteger(value["tokens"])
      && value["tokens"] >= 0 && typeof value["cost"] === "number"
      && Number.isFinite(value["cost"]) && value["cost"] >= 0) {
    return { state: "succeeded", tokens: value["tokens"], cost: value["cost"],
             ...(served === undefined ? {} : { served: served as string }) };
  }
  throw new Refusal("invalid", "invalid egress settle");
}

/** The settle's fields, exactly, or `invalid`. */
function settleRequest(value: unknown): SettleRequest {
  if (!keyed(value, ["bytes", "outcome", "reservation", "spend", "status"])) {
    throw new Refusal("invalid", "invalid egress settle");
  }
  const { reservation, outcome, status, bytes, spend } = value;
  if (typeof reservation !== "string" || !HEX32.test(reservation)
      || typeof outcome !== "string" || !OUTCOMES.includes(outcome)
      || (status !== null && (typeof status !== "number" || !Number.isSafeInteger(status)
                              || status < 100 || status > 599))
      || typeof bytes !== "number" || !Number.isSafeInteger(bytes) || bytes < 0) {
    throw new Refusal("invalid", "invalid egress settle");
  }
  return { reservation, outcome: outcome as Outcome, status: status as number | null, bytes,
           spend: spendOutcome(spend) };
}

export class Egress {
  readonly #storage: OwnerStorage;
  readonly #deps: EgressDeps;
  /**
   * Each open reservation's end signal, by reservation: the one thing its row cannot hold
   * (`OPEN_CALL_BYTES`), beside the one fact it does not keep.
   */
  readonly #live = new Map<string, {
    readonly end: WritableStreamDefaultWriter<Uint8Array>;
    /** An admitted model call whose charge waits for the gateway's parse (`reserve`). */
    reserving: boolean }>();

  constructor(storage: OwnerStorage, deps: EgressDeps) {
    this.#storage = storage;
    this.#deps = deps;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /**
   * The live lease the egress token names, or null: a lease whose egress token's hash is
   * the one presented. Read before anything else an open does (an OAuth refresh among it).
   */
  lease(sandbox: SandboxToken, now: number): SandboxLease | null {
    return sandbox.kind === "egress" ? this.#deps.lease(sandbox.token, now) : null;
  }

  /**
   * In the door's unit: the call admitted and reserved, an open row and its end signal; or
   * refused, with its fixed code, kept as a receipt row (the newest `refusalsKept`).
   */
  open(sandbox: SandboxToken, request: EgressRequest, now: number):
      EgressOpened | EgressRefusal {
    this.#recover(now);
    const lease = this.lease(sandbox, now);
    const generation = this.#deps.generation();
    const refuse = (code: EgressCode): EgressRefusal =>
      ({ ok: false, code, receipt: this.#record(sandbox, request, lease, generation, now,
                                                { code }) });
    if (lease === null || sandbox.kind !== "egress") {
      return refuse("egress_lease_closed");
    }
    // A run calls only on the connections its own grant holds, never another simultaneous
    // run's: its model connection (a signed-in run's none) and its connectors.
    const granted = this.#deps.runConnections(request.run, sandbox.secret, lease);
    if (granted === undefined) {
      return refuse("egress_run_closed");
    }
    const connector = granted.connectors.includes(request.connection);
    if (granted.connection !== request.connection && !connector) {
      return refuse("egress_run_connection");
    }
    if (sandbox.generation !== generation) {
      return refuse("egress_policy_stale");
    }
    if (lease.network_generation !== generation && lease.network_pending !== generation) {
      return refuse("egress_network_stale");
    }
    const credential = this.#deps.credential(request.connection, now * 1000);
    const connection = this.#deps.connection(request.connection);
    if (credential === null || connection === null) {
      return refuse("egress_connection_refused");
    }
    // Each in its own role: a connector is a service's key, the model connection a model
    // source's, so no grant makes either the other.
    const scope = connection.scope;
    if (connector !== ("kind" in scope)) {
      return refuse("egress_run_connection");
    }
    // A connector's server only while the owner's egress lets it out, as at the run's
    // admission (`agentServers`): a narrowed policy holds a live run's server too.
    const target = egressTarget(scope, request.method, request.path);
    if (target === null || (connector && !this.#deps.reaches(scope.base_url))) {
      return refuse("egress_endpoint_refused");
    }
    // A service's call reaches no model; nor does a read of a sealed endpoint, or a call its
    // API's rules cite as free (a token count); every other call must be the source's model
    // call as pi-ai makes it, which the gateway meters.
    const free = "kind" in scope
      || freeCall(apiRules(scope.api), connection.provider, request.method, target.pathname);
    if (!free && (!meteredCall(scope, request.method, target.pathname)
                  || connection.models.length === 0)) {
      return refuse("egress_unmetered");
    }
    if (!free && (request.bytes_hint === null || request.bytes_hint === 0)) {
      return refuse("egress_length_required");
    }
    if (this.#count("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'open'")
        >= EGRESS_LIMITS.open) {
      return refuse("egress_rate");
    }
    // A model call's API must have rules the gateway meters it by; its charge waits for the
    // gateway's parse of its body (`reserve`).
    if (!free && !("kind" in scope) && apiRules(scope.api) === null) {
      return refuse("egress_unmetered");
    }
    const reservation = this.#record(sandbox, request, lease, generation, now,
                                     { version: credential.version });
    const { readable, writable } = new IdentityTransformStream();
    this.#live.set(reservation, { end: writable.getWriter(), reserving: !free });
    return { ok: true, reservation, connection: request.connection, blob: credential.blob,
             version: credential.version, generation, metered: !free, ended: readable };
  }

  /**
   * In the door's unit, once the gateway parsed the admitted call's body: its charge, for
   * the model and tier that parse read (`#reserve`), set on its open row, and the metering
   * the gateway holds the call to; or refused, the row a refusal receipt with its code and
   * its stream ended. The model must be one the connection lists, and, where its API's
   * path names it, the path's; the tier one its rules price. Only an admitted model call
   * still streaming, never reserved, by the egress token that opened it, is reserved, once;
   * anything else is `invalid`.
   */
  reserve(sandbox: SandboxToken, request: ReserveRequest, now: number):
      EgressReserved | EgressRefusal {
    this.#recover(now);
    const row = this.#sql.exec<Row>("SELECT * FROM egress_reservations WHERE id = ?",
                                    request.reservation).toArray()[0];
    const live = this.#live.get(request.reservation);
    if (row === undefined || row.state !== "open" || row.charge !== null
        || sandbox.kind !== "egress" || row.token !== sandbox.token
        || live === undefined || !live.reserving) {
      throw new Refusal("invalid", "invalid egress reserve: no admitted call awaits its charge");
    }
    live.reserving = false;
    const refuse = (code: EgressCode, rule?: string): EgressRefusal => {
      this.#sql.exec("UPDATE egress_reservations SET state = 'refused', code = ?, rule = ?, " +
                       "settled_at = ? WHERE id = ?", code, rule ?? null, now, row.id);
      this.#release(row.id);
      this.#prune();
      return { ok: false, code, receipt: row.id };
    };
    const connection = this.#deps.connection(row.connection_id);
    const scope = connection === null || "kind" in connection.scope ? null : connection.scope;
    const rules = scope === null ? null : apiRules(scope.api);
    if (connection === null || scope === null || rules === null || scope.meter === null) {
      return refuse("egress_connection_refused");
    }
    // The model the call names, where its API's rules read it (the body, as the gateway's
    // parse read it, or the path), and its tier: the call is reserved for them alone.
    const path = rules.model === null
      ? meteredModel(scope.meter, row.method, row.path, {}) : null;
    const model = connection.models.find((entry) => entry.id === request.model);
    if (model === undefined || (rules.model === null && path !== request.model)) {
      return refuse("egress_unknown_model");
    }
    const tier = rules.tier === null ? null : request.tier;
    const multiplier = tierMultiplier(rules, tier);
    if (multiplier === null) {
      return refuse("egress_unpriced_option");
    }
    this.#spendPruned(now);
    const charge = this.#reserve(row.run, connection.source, model, multiplier, now);
    if (!charge.ok) {
      return refuse("egress_spend_refused", charge.rule);
    }
    this.#sql.exec("UPDATE egress_reservations SET charge = 'held', source = ?, " +
                     "max_output_tokens = ?, est_tokens = ?, est_cost = ?, day = ?, month = ? " +
                     "WHERE id = ? AND charge IS NULL", charge.source, charge.max_output_tokens,
                   charge.est_tokens, charge.est_cost, charge.day, charge.month, row.id);
    return { ok: true, metering: { max_output_tokens: charge.max_output_tokens,
                                   input_bytes: row.bytes_hint!, model: model.id, tier } };
  }

  /**
   * The µ$ held in `period` (`d:<day>` or `m:<month>`), by `source` or by every source: the
   * counters the charges' triggers move (`model_spend`), which the caps here and the runs'
   * own limits (`runs.ts`) read. A read.
   */
  spent(period: string, source: string | null): number {
    return this.#sql.exec<{ cost: number }>(
      "SELECT COALESCE(SUM(cost), 0) AS cost FROM model_spend WHERE period = ? " +
        "AND (? IS NULL OR source = ?)", period, source, source).one().cost;
  }

  /**
   * In the reserve's unit, before its charge adds a counter: the spend counters before the
   * oldest kept (`keptFrom`), so the counters never fill.
   */
  #spendPruned(now: number): void {
    const { day, month } = keptFrom(now);
    this.#sql.exec("DELETE FROM model_spend WHERE period < ?", `d:${day}`);
    this.#sql.exec("DELETE FROM model_spend WHERE period >= 'm:' AND period < ?", `m:${month}`);
  }

  /**
   * The call's charge, against every cap, in the open's unit, so concurrent calls reserve
   * one after another and never pass a cap between them: its run's requests, tokens and
   * cost (its `runs` row), and the owner's and the source's day and month (the spend
   * counters, read through `spent`). It is the named model's:
   * its input held at that model's context window (a body's bytes do not bound its tokens:
   * a file or a cached context it names can be far larger), its output at that model's cap
   * and what the caps leave (the gateway clamps every cap alias the call names to it, and
   * refuses several choices and another model), at that model's dearest rates times its
   * tier's `multiplier`, so what it holds bounds what it can cost; the settle gives the
   * difference back. Refused, with the cap that binds, below `MIN_OUTPUT`.
   */
  #reserve(runId: string, source: string, model: ModelRecord, multiplier: number,
           now: number): ({ ok: true } & Charged) | { ok: false; rule: string } {
    const models = [model];
    const input = model.contextWindow;
    const caps = this.#deps.caps(source);
    const run = this.#deps.runUsage(runId);
    if (caps.run_requests !== null && run.requests >= caps.run_requests) {
      return { ok: false, rule: "run_requests" };
    }
    const { day, month } = periods(now);
    // With no cap set, nothing binds the cost: the call holds its whole output.
    const rooms: [string, number][] = [["none", Number.POSITIVE_INFINITY]];
    if (caps.run_cost !== null) {
      rooms.push(["run_cost", caps.run_cost - run.cost]);
    }
    const ceilings: [string, number | null, string, string | null][] = [
      ["daily_cost", caps.daily_cost, `d:${day}`, null],
      ["monthly_cost", caps.monthly_cost, `m:${month}`, null],
      ["source_daily_cost", caps.source_daily_cost, `d:${day}`, source],
      ["source_monthly_cost", caps.source_monthly_cost, `m:${month}`, source],
    ];
    for (const [rule, cap, period, by] of ceilings) {
      if (cap !== null) {
        rooms.push([rule, cap - this.spent(period, by)]);
      }
    }
    const [costRule, costRoom] = rooms.reduce((a, b) => (b[1] < a[1] ? b : a));
    const most = Math.max(...models.map((model) => model.maxTokens));
    const floor = Math.min(most, MIN_OUTPUT);
    const byTokens = caps.run_tokens === null ? Number.POSITIVE_INFINITY
      : caps.run_tokens - run.tokens - input;
    // The call's model is unknown until the gateway reads the body: priced at the most any
    // of the connection's models charges (`egressPrice`), non-decreasing in the output.
    const price = (output: number) =>
      Math.ceil(egressPrice(models, input, output) * multiplier);
    const fits = (output: number) => price(output) <= costRoom;
    const output = largestFitting(Math.min(most, byTokens), fits);
    if (output < floor || output < 1) {
      const byTokensBinds = byTokens < floor && (byTokens < 0 || fits(byTokens));
      return { ok: false, rule: byTokensBinds ? "run_tokens" : costRule };
    }
    return { ok: true, source, max_output_tokens: output, est_tokens: input + output,
             est_cost: price(output), day, month };
  }

  /**
   * In the door's unit: a reservation's one end, as the door and the gateway's meter saw
   * it; its charge settled with it. False for one already settled, lost or
   * refused, or presented by another token.
   */
  settle(sandbox: SandboxToken, request: SettleRequest, now: number):
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
  end(why: EndedBy, covers: (live: LiveStream) => boolean = () => true): void {
    // What each covers is its open row's: a reservation's ids never change (the schema's
    // `egress_reservations_kept`).
    const ending: string[] = [];
    for (const row of this.#sql.exec<{ id: string; connection_id: string; lease_id: string;
                                       run: string }>(
      "SELECT id, connection_id, lease_id, run FROM egress_reservations WHERE state = 'open' " +
        "AND ended IS NULL")) {
      if (this.#live.has(row.id)
          && covers({ connection: row.connection_id, lease: row.lease_id, run: row.run })) {
        ending.push(row.id);
      }
    }
    for (const reservation of ending) {
      this.#sql.exec("UPDATE egress_reservations SET ended = ? WHERE id = ?", why, reservation);
      this.#release(reservation);
    }
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
                                 "rowid DESC LIMIT ?", EGRESS_LIMITS.receiptsListed).toArray()
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

  #record(sandbox: SandboxToken, request: EgressRequest, lease: SandboxLease | null,
          generation: number, now: number,
          fields: { code?: EgressCode; version?: number }): string {
    const id = randomHex(16);
    // Uncharged: a model call's charge is set once the gateway's parse reserved it. Its path
    // is kept without its query: a receipt says what was called, never what the sandbox
    // wrote in the parameters.
    this.#sql.exec(
      "INSERT INTO egress_reservations (id, lease_id, epoch, run, token, connection_id, " +
        "version, method, path, generation, bytes_hint, opened_at, state, code, settled_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      id, lease?.lease_id ?? null, lease?.epoch ?? null, request.run, sandbox.token,
      request.connection, fields.version ?? null, request.method,
      request.path.split("?")[0]!, generation,
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

  /**
   * In a unit: a streaming call's open reservation beaten by its door (`EGRESS_BEAT_S`), by
   * the egress token that opened it; false for one settled, lost or another token's.
   */
  beat(sandbox: SandboxToken, reservation: unknown, now: number): boolean {
    if (typeof reservation !== "string" || sandbox.kind !== "egress") {
      return false;
    }
    return this.#sql.exec("UPDATE egress_reservations SET beat_at = ? WHERE id = ? AND " +
                            "state = 'open' AND token = ? RETURNING id", now, reservation,
                          sandbox.token).toArray().length === 1;
  }

  /**
   * Open rows with no beat for `LOST_AFTER_S`, or past their door's deadline and a beat
   * (`SETTLED_WITHIN_S`): their doors' invocations were lost; their spend unknown.
   */
  #recover(now: number): void {
    const lost = this.#sql.exec<{ id: string }>(
      "UPDATE egress_reservations SET state = 'settled', outcome = 'lost', settled_at = ? " +
        "WHERE state = 'open' AND (coalesce(beat_at, opened_at) <= ? OR opened_at <= ?) " +
        "RETURNING id", now, now - LOST_AFTER_S, now - SETTLED_WITHIN_S)
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
    for (const [states, kept] of [["state = 'settled'", EGRESS_LIMITS.receiptsKept],
                                  ["state = 'refused'", EGRESS_LIMITS.refusalsKept]] as const) {
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

// -- THE DOORS: the `egress` rows' handlers (`owner/doors.json`) ----------------------------------

/**
 * What the `egress` doors reach: the sandbox's egress, the run grant a lease's call is held
 * to, and its connection's credential, refreshed and resealed (`connections.ts`, which only
 * the object imports).
 */
export interface EgressDoorNeeds extends Pick<DoorUnits, "door"> {
  readonly egress: Egress;
  readonly runs: Pick<Runs, "grantFor">;
  readonly connections: {
    fresh(id: string): Promise<void>;
    resealed(id: string, version: number, resealed: unknown, nowMs: number): boolean;
  };
}
//
// The sandbox holds one lease, the file sync lease (`files.ts`): its id is the claim
// token and its epoch the fencing number, for the sync commits and the egress alike, and
// it holds the hashes of its two tokens' secrets, the egress token (E2B's transform alone
// carries it) and the sync token (the in-VM sync agent's). The lease itself is owner-
// internal (`launch.ts`): no door reaches it. The egress doors take the egress
// token's binding, the sync doors the sync token's, and no other door takes either.

export const egressDoors = Object.freeze({
  /**
   * One sandbox request, admitted at open (`Egress.open`): its blob, version, generation,
   * reservation and end signal, or its refusal's fixed code and receipt. An OAuth
   * credential about to expire is refreshed first, as a model call's is.
   */
  async egressOpen(o: EgressDoorNeeds, door: Door<unknown, "egress">):
      Promise<EgressOpened | EgressRefusal> {
    const { r, live } = await o.door(() => {
      const { sandbox } = door.check().binding;
      const checked = egressRequest(door.request);
      const lease = o.egress.lease(sandbox, clock());
      // Only the live lease's own run connection is ever refreshed for it.
      return { r: checked, live: lease !== null
        && o.runs.grantFor(checked.run, sandbox.secret, lease)?.connection
          === checked.connection };
    });
    // Only a live lease's call may refresh its connection's OAuth credential.
    if (live) {
      try {
        await o.connections.fresh(r.connection);
      } catch (error) {
        // A refresh refused leaves the credential as it was: the open below refuses it if it
        // has expired, and uses it while it has not. Anything else is thrown.
        const code = error instanceof Refusal ? error.code : null;
        if (code !== "credential_refresh_failed" && code !== "credential_expired") {
          throw error;
        }
      }
    }
    return o.door(() => o.egress.open(door.check().binding.sandbox, r, clock()));
  },
  /**
   * An admitted model call's reserve (`Egress.reserve`), which the gateway asks for through
   * the egress door once its one parse of the body read the model and tier: its metering, or
   * its refusal's fixed code and receipt. Held to the egress binding that opened the call.
   */
  egressReserve(o: EgressDoorNeeds, { binding, request }: Call<unknown, "egress">):
      EgressReserved | EgressRefusal {
    return o.egress.reserve(binding.sandbox, reserveRequest(request), clock());
  },
  /**
   * A streaming call's beat from its door (`EGRESS_BEAT_S`): the open reservation its egress
   * token opened is still its door's, so it is not settled lost.
   */
  egressBeat(o: EgressDoorNeeds, { binding, request }: Call<{ reservation: string }, "egress">):
      { beat: boolean } {
    return { beat: o.egress.beat(binding.sandbox, request?.reservation, clock()) };
  },
  /**
   * One reservation's end, as the egress door saw it: its outcome, the provider's status
   * and the bytes it streamed; a blob the gateway resealed meanwhile is kept by the
   * connection's compare-and-swap.
   */
  egressSettle(o: EgressDoorNeeds, { binding, request }: Call<Record<string, unknown>, "egress">):
      { settled: boolean } {
    const { resealed, ...rest } = request ?? {};
    const settled = o.egress.settle(binding.sandbox, settleRequest(rest), clock());
    if (settled.settled && resealed !== undefined) {
      try {
        o.connections.resealed(settled.connection, settled.version, resealed, clockMs());
      } catch (error) {
        if (!(error instanceof Refusal && error.invalid)) {
          throw error;
        }
        // A malformed reseal is the gateway's bug: the call still settles, and the
        // gateway reseals again at the next one.
        console.error("a resealed blob the egress door handed back was malformed");
      }
    }
    return { settled: settled.settled };
  },
  /** The owner's newest egress receipts: what the sandbox asked, and how each ended. */
  egressReceipts(o: EgressDoorNeeds, _call: Call<undefined>): { receipts: EgressReceipt[] } {
    return { receipts: o.egress.receipts() };
  },
});
