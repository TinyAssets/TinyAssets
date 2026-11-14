/**
 * Sandbox egress at the owner's object (tasks 4 G4): the sandbox's one lease and its two
 * MAC'd tokens, owner-internal; each call admitted at open (`egressOpen`) by every check in
 * order, a model call charged against its run's, the owner's and the source's caps and
 * nothing else, on counters none of them pi's; a refusal kept as a receipt; the streams a
 * change ends; and each charge settled once (`egressSettle`) at the cost the gateway's
 * meter read, or kept whole when it could not. The egress door itself is the doors' tests'.
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { verifyToken } from "../../contract/src/routing-mac";
import type { Metering } from "../../contract/src/gateway";
import type { ModelRecord } from "../../contract/src/model-record";
import type { EgressOpened, EgressRefusal } from "../../contract/src/owner";
import { EGRESS_BEAT_S, egressTokenOf, generationOf } from "../../contract/src/egress";
import {
  EGRESS_LIMITS, egressPrice,
  LOST_AFTER_S,
} from "../src/egress";
import { EGRESS_DEADLINE_S, SPEND_KEPT } from "../../contract/src/limits";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { apiRules } from "../../contract/src/api-rules";
import { NO_SANDBOX, SANDBOX_DOORS, SANDBOX_ONLY } from "../src/owner-do";
import { SYNC_FENCED } from "../src/files";
import { priced } from "../src/usage";
import {
  ANTHROPIC, catalog, type Gated, gated, OAUTH, OPENAI, scopeOf, sealedAs,
} from "./gateway-support";
import { signedIn } from "./ledger-support";
import { HELD_WAIT_MS } from "../../ci/fixtures";
import { drainDue, failure, TIERS } from "./support";
import { NETWORK_RETRY_S, type SandboxRun } from "../src/launch";
import { LANE_CALL_MS } from "../src/schedule";
import {
  defineAgent, hashed, later, ownerRun, recorder, runnerBinding, runnerSandbox, scripted,
  secretOf, whileOut,
} from "./runs-support";
import type { NetworkPlan, SandboxSignin, SandboxStart } from "../../contract/src/sandbox";
import { SANDBOX_WAITING } from "../src/sandbox";
import { bundledSources } from "../../sources/src/catalog";
import { RUN_BASICS, runEnv } from "../../sources/src/run-env";
import type { FauxOwnerDO } from "./worker";

afterEach(() => {
  vi.useRealTimers();
});

/** What the sandbox's lease handed its first run's start: its tokens, network and itself. */
interface Opened {
  readonly egress_token: string;
  readonly sync_token: string;
  readonly lease: { readonly lease_id: string; readonly epoch: number };
  readonly network: NetworkPlan;
}

interface Leased {
  readonly opened: Opened;
  /** The run the lease was opened or joined for: the one the calls name. */
  readonly run: string;
  /** That run's start, as the sandbox took it. */
  readonly start: SandboxStart;
  readonly lease_id: string;
  readonly epoch: number;
  readonly egress: string;
  readonly sync: string;
  /** The egress door's binding for this lease's egress token, at `generation`. */
  readonly as: (generation?: number, token?: string) => Binding;
  /** A sync door's binding for this lease's sync token, beside an egress token's hash. */
  readonly synced: (token?: string, egress?: string) => Binding;
}

/**
 * The run a lease is for unless a test says otherwise: Claude Code on the owner's own
 * sign-in, whose environment carries no key at all.
 */
const SIGNED_IN = Object.freeze({ engine: "claude-code",
                                  credential: "vendor_login_in_sandbox" as const });

/** Caps roomy enough that a test's every call fits, each charging up to the most it costs. */
const ROOMY = Object.freeze({ turn_requests: 10_000, turn_tokens: 1_000_000_000,
                              turn_cost: 1_000_000_000_000 });

/** A token's secret's hash, once its MAC is the routing key's own for `kind`. */
function hashOf(kind: "egress" | "sync", token: string): string {
  return verifyToken(env.ROUTING_MAC_KEY, kind, token)!.hash;
}

/** The run a lease is for when the owner holds a connection: pi on that key. */
const onKey = (connection: string) =>
  Object.freeze({ engine: "pi", credential: "api_key" as const, connection });

/** The run the calls name unless a test says otherwise: the newest one leased. */
let RUN = "";
/** Each run's own secret's hash, as its calls present it (`/r/<run>/<secret>/...`). */
const SECRETS = new Map<string, string>();
const secretFor = (run: string) =>
  (SECRETS.has(run) ? { secret: SECRETS.get(run)! } : {});
/** Each lease's tokens and network, as the start that opened it got them. */
const OPENED = new Map<string, Omit<Opened, "lease">>();

/**
 * A run admitted for `run` (an agent's file naming its engine, credential and connection),
 * through the run door, which opens the sandbox's lease (or joins the live one), its network
 * applied by the start that opened it; the owner's caps made roomy first, unless `roomy`
 * is false. Unless a test names the run, it is pi on the owner's oldest active connection,
 * the one the test calls, or a run on the owner's own sign-in when the owner holds none.
 */
async function leased(r: Gated, roomy = true,
                      run?: Omit<SandboxRun, "run" | "secret">): Promise<Leased> {
  if (roomy) {
    await capped(r, ROOMY);
  }
  const [oldest] = await r.sql("SELECT id FROM connections WHERE status = 'active' " +
                               "ORDER BY created_at, id LIMIT 1") as { id: string }[];
  const chosen = run ?? (oldest === undefined ? SIGNED_IN : onKey(oldest.id));
  const { start } = await ownerRun(r.stub, r.bind(), { ...chosen, scopes: [] });
  if (start.tokens !== null) {
    OPENED.set(start.lease.lease_id, { ...start.tokens,
                                       egress_token: egressTokenOf(start.network!.network)!,
                                       network: start.network! });
  }
  const opened: Opened = { ...OPENED.get(start.lease.lease_id)!, lease: start.lease };
  RUN = start.run;
  SECRETS.set(start.run, hashed(secretOf(start)));
  const egress = hashOf("egress", opened.egress_token);
  const sync = hashOf("sync", opened.sync_token);
  return {
    opened, run: start.run, start, lease_id: opened.lease.lease_id, epoch: opened.lease.epoch,
    egress, sync,
    as: (at = generationOf(opened.network.network)!, token = egress) =>
      r.bind({ sandbox: { kind: "egress", token, generation: at, ...secretFor(RUN) } }),
    synced: (token = sync, by = egress) => r.bind({ sandbox: { kind: "sync", token,
                                                                egress: by } }),
  };
}

/** The model every call names unless it says otherwise, and OpenAI's: each catalog's first. */
let MODEL = "";
let OPENAI_MODEL = "";

beforeAll(async () => {
  MODEL = (await catalog(ANTHROPIC))[0]!.id;
  OPENAI_MODEL = (await catalog(OPENAI))[0]!.id;
});

/** The door's open of a call: `extra` its fields, but for the model and tier the reserve names. */
function request(connection: string, extra: Record<string, unknown> = {}) {
  const { model: _model, tier: _tier, ...open } = extra;
  return { run: RUN, connection, method: "POST", path: "/v1/messages", bytes_hint: 120,
           ...open };
}

/** The gateway's reserve of an admitted call: the model and tier its parse read. */
function reserving(reservation: string, extra: Record<string, unknown> = {}) {
  return { reservation, model: extra["model"] === undefined ? MODEL : extra["model"],
           tier: extra["tier"] ?? null };
}

/** An admitted call, and a model call's metering once reserved (null for a free call). */
type Admitted = EgressOpened & { readonly metering: Metering | null };

/** A call opened and, for a model call, reserved, as the door and the gateway do. */
async function admitted(r: Gated, lease: Leased, connection: string,
                        extra: Record<string, unknown> = {}): Promise<Admitted> {
  const opened = await r.stub.egressOpen(lease.as(), request(connection, extra));
  if (!opened.ok) {
    throw new Error(`refused ${opened.code}`);
  }
  if (!opened.metered) {
    return { ...opened, metering: null };
  }
  const reserved = await r.stub.egressReserve(lease.as(),
                                              reserving(opened.reservation, extra));
  if (!reserved.ok) {
    throw new Error(`refused ${reserved.code}`);
  }
  return { ...opened, metering: reserved.metering };
}

/** The code a call is refused with, at its open or its reserve, each with its receipt. */
async function refusedWith(r: Gated, binding: Binding, connection: string,
                           extra: Record<string, unknown> = {}): Promise<string> {
  const opened = await r.stub.egressOpen(binding, request(connection, extra));
  let refused: EgressRefusal;
  if (opened.ok) {
    const reserved = opened.metered
      ? await r.stub.egressReserve(binding, reserving(opened.reservation, extra)) : null;
    if (reserved === null || reserved.ok) {
      throw new Error("expected a refusal");
    }
    refused = reserved;
  } else {
    refused = opened;
  }
  const [receipt] = await r.sql("SELECT state, code FROM egress_reservations WHERE id = ?",
                                refused.receipt);
  expect(receipt).toEqual({ state: "refused", code: refused.code });
  return refused.code;
}

/** Each end signal's one read: it resolves, done or failed, when the object ends it. */
const signals = new WeakMap<ReadableStream<Uint8Array>, Promise<boolean>>();

/** Whether the end signal closed within `waitMs`. */
async function ended(opened: EgressOpened, waitMs = 2000): Promise<boolean> {
  let read = signals.get(opened.ended);
  if (read === undefined) {
    read = opened.ended.getReader().read().then(() => true, () => true);
    signals.set(opened.ended, read);
  }
  const late = new Promise<boolean>((resolve) => setTimeout(() => resolve(false), waitMs));
  return Promise.race([read, late]);
}

const held = ({ lease_id, epoch }: Leased) => ({ lease_id, epoch });

/**
 * The lease's run ended, as its runner reports its CLI's exit: the run settles, and its
 * lease, with no run left under it, is given back (its tokens refused, its streams ended).
 */
async function over(r: Gated, lease: Leased): Promise<void> {
  await r.stub.runEvents(runnerBinding(r, lease.start), { frames: [], end: { exit_code: 0 } });
}

/** One reservation's state and outcome. */
async function state(r: Gated, reservation: string) {
  return (await r.sql("SELECT state, outcome FROM egress_reservations WHERE id = ?",
                      reservation))[0];
}

async function rows(r: Gated) {
  return r.sql("SELECT state, code, ended, outcome, status, bytes, bytes_hint " +
                 "FROM egress_reservations ORDER BY opened_at, rowid");
}

/** Each row's charge: what it held, what it used, and a refusal's binding cap. */
async function charges(r: Gated) {
  return r.sql("SELECT state, source, charge, rule, max_output_tokens, est_tokens, est_cost, " +
                 "tokens, cost FROM egress_reservations ORDER BY opened_at, rowid");
}

/** The run counters, of each run that made a model call, oldest first. */
async function runs(r: Gated) {
  return r.sql("SELECT id AS run, requests, tokens, cost FROM runs WHERE requests > 0 " +
                 "ORDER BY seq");
}

/** What the source's day and month hold. */
async function sourceSpend(r: Gated, source = "anthropic") {
  return r.sql("SELECT substr(period, 1, 1) AS p, cost FROM model_spend WHERE source = ? " +
                 "ORDER BY period", source);
}

const DONE = { state: "succeeded", tokens: 1500, cost: 0.0042 } as const;
const settle = (reservation: string, extra: Record<string, unknown> = {}) =>
  ({ reservation, outcome: "complete", status: 200, bytes: 42, spend: DONE, ...extra });

/** The owner's model policy set, in its session. */
async function capped(r: Gated, models: Record<string, unknown>): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind());
  await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                          policy: { ...policy, models } });
}

/**
 * The caps set once the lease is held: a model policy write leaves the egress generation, so
 * the network its start applied stays current.
 */
async function cappedLeased(r: Gated, models: Record<string, unknown>): Promise<Leased> {
  const lease = await leased(r);
  await capped(r, models);
  return lease;
}

describe("an egress call", () => {
  it("is admitted under a live lease, charged to its run, and settled at its real cost",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    expect(opened).toMatchObject({ ok: true, connection, version: 1, generation: 1,
                                   metering: { input_bytes: 120 } });
    expect(opened.metering!.max_output_tokens).toBeGreaterThan(0);
    expect(opened.blob.startsWith("v1.")).toBe(true);
    expect(await rows(r)).toEqual([{ state: "open", code: null, ended: null, outcome: null,
                                     status: null, bytes: null, bytes_hint: 120 }]);
    expect(opened.metering).toMatchObject({ model: MODEL, tier: null });
    const [charge] = await charges(r);
    // Held for the model the call names: its input at that model's context window, not the
    // body's bytes, and its output at that model's cap.
    const [{ models }] = (await r.sql("SELECT models FROM connections WHERE id = ?",
                                      connection)) as [{ models: string }];
    const named = (JSON.parse(models) as ModelRecord[]).find((model) => model.id === MODEL)!;
    const output = opened.metering!.max_output_tokens;
    expect(output).toBe(named.maxTokens);
    expect(charge).toMatchObject({ source: "anthropic", charge: "held", cost: null,
                                   max_output_tokens: output,
                                   est_tokens: named.contextWindow + output });
    const estimate = charge!["est_cost"] as number;
    // Priced at that model's dearest rates: input at the dearest of uncached, cache-read
    // and cache-write.
    const top = (rates: { input: number; cacheRead: number; cacheWrite: number }) =>
      Math.max(rates.input, rates.cacheRead, rates.cacheWrite);
    expect(estimate).toBe(priced(
      { cost: { ...named.cost, input: top(named.cost),
                ...(named.cost.tiers === undefined ? {}
                  : { tiers: named.cost.tiers.map((tier) => ({ ...tier, input: top(tier) })) }) } },
      named.contextWindow, output));
    expect(estimate).toBeGreaterThan(0);
    // Held, it counts at its estimate, on the run's and the source's counters.
    expect(await runs(r)).toEqual([{ run: lease.run, requests: 1,
                                     tokens: charge!["est_tokens"], cost: estimate }]);
    expect(await sourceSpend(r)).toEqual([{ p: "d", cost: estimate }, { p: "m", cost: estimate }]);
    // Another token cannot settle it; its own settles it once, at the metered cost.
    expect(await r.stub.egressSettle(lease.as(1, "f".repeat(64)), settle(opened.reservation)))
      .toEqual({ settled: false });
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation)))
      .toEqual({ settled: true });
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation)))
      .toEqual({ settled: false });
    expect(await rows(r)).toMatchObject([{ state: "settled", outcome: "complete", status: 200,
                                           bytes: 42 }]);
    expect(await charges(r)).toMatchObject([{ charge: "succeeded", tokens: 1500, cost: 4200 }]);
    expect(await runs(r)).toEqual([{ run: lease.run, requests: 1, tokens: 1500, cost: 4200 }]);
    // And the run's receipt shows it.
    expect(await r.stub.agentStatus(r.bind(), { run_id: lease.run }))
      .toMatchObject({ requests: 1, tokens: 1500, cost: 4200 });
    expect(await sourceSpend(r)).toEqual([{ p: "d", cost: 4200 }, { p: "m", cost: 4200 }]);
    // The model reservations are untouched: the sandbox's charge is its own row.
    expect(await r.sql("SELECT COUNT(*) AS n FROM model_reservations")).toEqual([{ n: 0 }]);
    const { receipts } = await r.stub.egressReceipts(r.bind());
    expect(receipts).toMatchObject([{ reservation: opened.reservation, connection,
                                      method: "POST", path: "/v1/messages", state: "settled",
                                      charge: "succeeded", cost: 4200 }]);
  });

  it("that counts tokens on a key that seals it is free, as its API's rules cite it",
     async () => {
    const r = await gated();
    const counted = { ...ANTHROPIC, endpoints: [...ANTHROPIC.endpoints, {
      origin: "https://api.anthropic.com", paths: ["/v1/messages/count_tokens"],
      methods: ["POST"] }] };
    const connection = await r.connect("sk-good", counted);
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection, { path: "/v1/messages/count_tokens" });
    expect(opened.metering).toBeNull();
    expect(await charges(r)).toMatchObject([{ state: "open", charge: null, est_cost: null }]);
    // Any other sealed POST that is not the source's model call is still never sent.
    const other = await r.connect("sk-good-2", { ...ANTHROPIC, endpoints: [
      ...ANTHROPIC.endpoints, { origin: "https://api.anthropic.com",
                                paths: ["/v1/messages/batches"], methods: ["POST"] }] },
                                  "Other");
    await over(r, lease);
    const onOther = await leased(r, true, onKey(other));
    expect(await refusedWith(r, onOther.as(), other, { path: "/v1/messages/batches" }))
      .toBe("egress_unmetered");
    // Another provider on the same API, sealing the same path: Anthropic's page is not its.
    await over(r, onOther);
    const onCounted = await leased(r, true, onKey(connection));
    await r.sql("UPDATE connections SET provider = 'vercel-ai-gateway' WHERE id = ?", connection);
    expect(await refusedWith(r, onCounted.as(), connection,
                             { path: "/v1/messages/count_tokens" })).toBe("egress_unmetered");
  });

  it("spends only on the connection its run was given", async () => {
    const r = await gated();
    const a = await r.connect("sk-good");
    const b = await r.connect("sk-good-2", ANTHROPIC, "B");
    const onA = await leased(r, true, onKey(a));
    expect(await refusedWith(r, onA.as(), b)).toBe("egress_run_connection");
    expect((await admitted(r, onA, a)).ok).toBe(true);
    // The connection is the run grant's, never the lease's.
    expect(await r.sql("SELECT run, connection FROM run_grants"))
      .toEqual([{ run: onA.run, connection: a }]);
    await over(r, onA);
    expect(await r.sql("SELECT run FROM run_grants")).toEqual([]);
  });

  it("lets a run on the owner's own sign-in spend on no connection at all", async () => {
    const r = await gated();
    const a = await r.connect("sk-good");
    const b = await r.connect("sk-good-2", ANTHROPIC, "B");
    const signedIn = await leased(r, true, SIGNED_IN);
    for (const connection of [a, b, `c${"0".repeat(24)}`]) {
      expect(await refusedWith(r, signedIn.as(), connection), connection)
        .toBe("egress_run_connection");
    }
    // Nor does its call refresh a connection's credential: nothing was opened.
    expect(await charges(r)).toMatchObject([{ state: "refused" }, { state: "refused" },
                                            { state: "refused" }]);
  });

  it("that reads a sealed endpoint is free: admitted unmetered, charged nothing", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection, { method: "GET", path: "/v1/models",
                                                          bytes_hint: null });
    expect(opened.metering).toBeNull();
    expect(await charges(r)).toMatchObject([{ state: "open", charge: null, est_cost: null }]);
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation, {
      spend: { state: "unknown" } }))).toEqual({ settled: true });
    expect(await charges(r)).toMatchObject([{ state: "settled", charge: null, cost: null }]);
    expect(await runs(r)).toEqual([]);
    expect(await sourceSpend(r)).toEqual([]);
  });

  it("is refused at each admit check, in order, each with its code and a receipt", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const other = await gated();
    const theirs = await other.connect("sk-good");
    const before = await leased(r);
    // An egress write since its start, whose re-apply finds no sandbox: its network stays at
    // the generation that start applied (1), and the policy's is 2.
    await scripted(r.stub, { networkAnswer: { state: "lost" } });
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
      ...policy, egress: { mode: "allowlist", destinations: [] } } });
    // The lease: another token, then the transform's generation, then the network's.
    expect(await refusedWith(r, before.as(2, "f".repeat(64)), connection))
      .toBe("egress_lease_closed");
    expect(await refusedWith(r, before.as(1), connection)).toBe("egress_policy_stale");
    expect(await refusedWith(r, before.as(2), connection)).toBe("egress_network_stale");
    later();
    expect(await refusedWith(r, before.as(2), connection)).toBe("egress_lease_closed");
    const lease = await leased(r);
    expect(await refusedWith(r, before.as(2), connection)).toBe("egress_lease_closed");
    // The run's connection, before the generations: another owner's, or none, is not it.
    expect(await refusedWith(r, lease.as(1), theirs)).toBe("egress_run_connection");
    expect(await refusedWith(r, lease.as(), `c${"0".repeat(24)}`))
      .toBe("egress_run_connection");
    // The run's own connection: one with no scope stored, an unusable blob.
    const [scope] = await r.sql("SELECT egress FROM connections WHERE id = ?", connection);
    await r.sql("UPDATE connections SET egress = NULL WHERE id = ?", connection);
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_connection_refused");
    await r.sql("UPDATE connections SET egress = ? WHERE id = ?", scope!["egress"]!, connection);
    await r.sql("UPDATE connections SET expires_at = 1 WHERE id = ?", connection);
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_connection_refused");
    await r.sql("UPDATE connections SET expires_at = NULL WHERE id = ?", connection);
    // The endpoint: a path, method or prefix not sealed, or a path naming another host.
    for (const extra of [{ path: "/v1/files" }, { method: "DELETE" }, { path: "/v1/messages-x" },
                         { path: "//api.openai.com/v1/messages" },
                         { path: "/v1/%2e%2e/admin" }]) {
      expect(await refusedWith(r, lease.as(), connection, extra)).toBe("egress_endpoint_refused");
    }
    // A sealed write that is not the source's metered model call, or no body length.
    expect(await refusedWith(r, lease.as(), connection, { path: "/v1/models" }))
      .toBe("egress_unmetered");
    expect(await refusedWith(r, lease.as(), connection, { bytes_hint: null }))
      .toBe("egress_length_required");
    expect(await refusedWith(r, lease.as(), connection, { bytes_hint: 0 }))
      .toBe("egress_length_required");
    await admitted(r, lease, connection);
    expect((await r.sql("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'open'"))
      [0]).toEqual({ n: 1 });
  });

  it("refreshes an expiring OAuth credential for a live lease's own run connection only",
     async () => {
    const r = await gated();
    const connection = await r.connectOAuth(OAUTH, Date.now() + 60_000);
    const version = async () =>
      (await r.sql("SELECT version FROM connections WHERE id = ?", connection))[0]!["version"];
    // A live lease whose run was not given the connection refreshes nothing.
    const signedIn = await leased(r, true, SIGNED_IN);
    expect(await refusedWith(r, signedIn.as(), connection,
                             { path: "/v1/responses", model: OPENAI_MODEL }))
      .toBe("egress_run_connection");
    expect(await version()).toBe(1);
    await over(r, signedIn);
    const lease = await leased(r);
    expect(await refusedWith(r, lease.as(1, "f".repeat(64)), connection,
                             { path: "/v1/responses", model: OPENAI_MODEL }))
      .toBe("egress_lease_closed");
    expect(await version()).toBe(1);
    await admitted(r, lease, connection, { path: "/v1/responses", model: OPENAI_MODEL });
    expect(await version()).toBe(2);
  });

  it("is held to the open calls the tier's runs give, and to no rate a minute",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const open: EgressOpened[] = [];
    // The tier's runs scale it: the tests' control record's free tier.
    for (let i = 0; i < EGRESS_LIMITS.openPerRun * TIERS.free.runs; i++) {
      open.push(await admitted(r, lease, connection));
    }
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_rate");
    expect(await r.stub.egressSettle(lease.as(), settle(open[0]!.reservation)))
      .toEqual({ settled: true });
    await admitted(r, lease, connection);
    await r.sql("UPDATE egress_reservations SET state = 'settled', outcome = 'complete', " +
                  "settled_at = opened_at WHERE state = 'open'");
    // Any number opened within the minute: only the open ones count.
    await r.sql("DELETE FROM egress_reservations");
    const now = Date.now() / 1000;
    const fill = (count: number, at: number, state: string) => r.sql(
      "INSERT INTO egress_reservations (id, lease_id, run, token, connection_id, method, " +
        "path, generation, opened_at, state, code, outcome, settled_at) SELECT " +
        "lower(hex(randomblob(16))), ?, ?, ?, ?, 'GET', '/x', 1, ?, ?, ?, ?, ? FROM " +
        "(WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) " +
        "SELECT i FROM n)", lease.lease_id, lease.run, lease.egress, connection, at, state,
      state === "refused" ? "egress_rate" : null, state === "settled" ? "complete" : null, at,
      count);
    await fill(5_000, now - 10, "settled");
    await fill(30, now - 30, "refused");
    await admitted(r, lease, connection);
  });

  it("receipts every refusal, keeping the newest refusalsKept, apart from the admitted",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    await r.stub.egressSettle(lease.as(), settle(opened.reservation));
    expect(EGRESS_LIMITS.refusalsKept).toBe(500);
    for (let i = 0; i < 8; i++) {
      await r.stub.egressOpen(lease.as(), request(connection, { path: `/v1/files/${i}` }));
    }
    const late = await r.stub.egressOpen(lease.as(), request(connection, { path: "/v1/x" }));
    expect(late).toMatchObject({ ok: false, code: "egress_endpoint_refused" });
    expect((late as { receipt: unknown }).receipt).not.toBeNull();
    expect(await r.sql("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'refused'"))
      .toEqual([{ n: 9 }]);
    expect(await r.sql("SELECT state FROM egress_reservations WHERE id = ?", opened.reservation))
      .toEqual([{ state: "settled" }]);
  });

  it("of a suspended owner is refused, and settled still, but not under abuse", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    const platform = { owner: r.name, caller: PLATFORM_CALLER, origin: null };
    await r.stub.admissionSet(platform, { reason: "billing", suspended: true });
    expect(await ended(opened)).toBe(true);
    expect((await failure(() => r.stub.egressOpen(lease.as(), request(connection)))).message)
      .toMatch(/^owner_suspended/);
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation, { outcome: "ended",
                                                                         spend: { state: "unknown" } })))
      .toEqual({ settled: true });
    expect(await rows(r)).toMatchObject([{ ended: "admission", outcome: "ended" }]);
    expect((await failure(() => ownerRun(r.stub, r.bind()))).message)
      .toMatch(/^owner_suspended/);
    await r.stub.admissionSet(platform, { reason: "abuse", suspended: true });
    expect((await failure(() => r.stub.egressSettle(lease.as(), settle(opened.reservation))))
      .message).toMatch(/^owner_suspended/);
  });

  it("takes the egress token's binding, which no other door takes, and only it", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    for (const call of [() => r.stub.policyRead(lease.as()),
                        () => r.stub.fileStat(lease.as(), { path: "/workspace/a" }),
                        () => r.stub.mailboxRead(lease.as(), { limit: 1 })]) {
      expect((await failure(call)).message).toBe(SANDBOX_DOORS);
    }
    for (const call of [() => r.stub.egressOpen(r.bind(), request(connection)),
                        () => r.stub.egressSettle(r.bind(), settle("0".repeat(32))),
                        () => r.stub.egressOpen(lease.synced(), request(connection)),
                        () => r.stub.egressSettle(lease.synced(), settle("0".repeat(32))),
                        // The sync doors take the sync token, never the egress token.
                        () => r.stub.fileChanges(lease.as(), { after: 0, limit: 10 }),
                        () => r.stub.fileSyncCommit(lease.as(), { ...held(lease), ops: [] })]) {
      expect((await failure(call)).message).toBe(SANDBOX_ONLY);
    }
    expect((await failure(() => r.stub.egressOpen(lease.as(1, "short"), request(connection))))
      .message).toBe("invalid sandbox binding");
    // The object's own check: the sync token's hash presented as an egress token.
    expect(await refusedWith(r, lease.as(1, lease.sync), connection)).toBe("egress_lease_closed");
    for (const bad of [{ method: "OPTIONS" }, { path: "v1/messages" }, { path: `/${"p".repeat(2048)}` },
                       { path: "/v1/€" }, { path: "/v1/a b" }, { bytes_hint: -1 },
                       { connection: "c1" }, { extra: 1 }]) {
      expect((await failure(() => r.stub.egressOpen(lease.as(), request(connection, bad))))
        .message).toBe("invalid egress request");
    }
  });

  it("writes nothing for a binding not the binder's exact shape, or for a never-seen owner",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const before = await r.sql("SELECT COUNT(*) AS n FROM egress_reservations");
    for (const binding of [{ ...lease.as(), origin: { kind: "mcp" } },
                           { ...lease.as(), caller: "someone-else" },
                           { ...lease.as(), session: "s" },
                           { ...lease.as(), sandbox: { kind: "egress", token: lease.egress,
                                                       generation: 1, extra: 1 } }]) {
      expect((await failure(() => r.stub.egressOpen(binding as Binding, request(connection))))
        .message).toBe("invalid sandbox binding");
    }
    expect(await r.sql("SELECT COUNT(*) AS n FROM egress_reservations")).toEqual(before);
    // An owner whose object never opened a lease: refused, and no row of its own written.
    const stranger = `owner-${crypto.randomUUID()}`;
    const stub = env.OWNER.getByName(stranger);
    expect((await failure(() => stub.egressOpen(
      { owner: stranger, caller: stranger, origin: null,
        sandbox: { kind: "egress", token: lease.egress, generation: 1 } },
      request(connection)))).message).toBe(NO_SANDBOX);
    expect(await runInDurableObject(stub, (_instance, state) => [
      ...state.storage.sql.exec("SELECT COUNT(*) AS n FROM object_owner").toArray(),
      ...state.storage.sql.exec("SELECT COUNT(*) AS n FROM egress_reservations").toArray()]))
      .toEqual([{ n: 0 }, { n: 0 }]);
  });
});

describe("the sync token", () => {
  it("works on the sync doors for its lease and epoch alone, and never on egress", async () => {
    const r = await gated();
    const lease = await leased(r);
    expect(await r.stub.fileChanges(lease.synced(), { after: 0, limit: 10 }))
      .toMatchObject({ cursor: expect.any(Number) });
    expect(await r.stub.fileSyncCommit(lease.synced(), { ...held(lease), ops: [
      { op: "mkdir", path: "/workspace/d", base: null }] }))
      .toMatchObject({ outcomes: [{ path: "/workspace/d", outcome: "committed" }] });
    for (const call of [
      () => r.stub.fileSyncCommit(lease.synced(), { lease_id: lease.lease_id,
                                                    epoch: lease.epoch + 1, ops: [] }),
      () => r.stub.fileSyncCommit(lease.synced("f".repeat(64)), { ...held(lease), ops: [] }),
      () => r.stub.fileChanges(lease.synced(lease.egress), { after: 0, limit: 10 })]) {
      expect((await failure(call)).message).toBe(SYNC_FENCED);
    }
    for (const call of [() => r.stub.fileStat(lease.synced(), { path: "/workspace/d" }),
                        () => r.stub.policyRead(lease.synced())]) {
      expect((await failure(call)).message).toBe(SANDBOX_DOORS);
    }
    await over(r, lease);
    expect((await failure(() => r.stub.fileChanges(lease.synced(), { after: 0, limit: 10 })))
      .message).toBe(SYNC_FENCED);
  });

  it("works only beside its own lease's egress token: from inside that sandbox (M-5)",
     async () => {
    const r = await gated();
    const lease = await leased(r);
    for (const egress of ["f".repeat(64), lease.sync]) {
      expect((await failure(() => r.stub.fileChanges(lease.synced(lease.sync, egress),
                                                      { after: 0, limit: 10 }))).message)
        .toBe(SYNC_FENCED);
    }
  });

  it("uploads a file past an inline write as its lease's writer, which its commit takes",
     async () => {
    const r = await gated();
    const lease = await leased(r);
    const bytes = new TextEncoder().encode("y".repeat(6000));
    const hash = createHash("sha256").update(bytes).digest("hex");
    const begun = await r.stub.fileSyncUploadBegin(lease.synced(), {
      ...held(lease), size: bytes.byteLength, chunks: [hash] });
    expect(begun.missing).toEqual([hash]);
    expect(await r.stub.fileSyncUploadChunks(lease.synced(), {
      ...held(lease), intent_id: begun.intent_id, chunks: [{ sha256: hash, bytes }] }))
      .toEqual({ created: 1 });
    expect(await r.stub.fileSyncCommit(lease.synced(), { ...held(lease), ops: [
      { op: "write", path: "/workspace/big.txt", base: null, intent_id: begun.intent_id }] }))
      .toMatchObject({ outcomes: [{ outcome: "committed", entry: { size: 6000 } }] });
    // An upload the owner began is not the lease's to send bytes for.
    const owners = await r.stub.fileUploadBegin(r.bind(), { size: bytes.byteLength,
                                                            chunks: [hash] });
    expect((await failure(() => r.stub.fileSyncUploadChunks(lease.synced(), {
      ...held(lease), intent_id: owners.intent_id, chunks: [{ sha256: hash, bytes }] })))
      .message).toBe(SYNC_FENCED);
    // A lease lost is fenced at the begin.
    expect((await failure(() => r.stub.fileSyncUploadBegin(lease.synced(), {
      lease_id: lease.lease_id, epoch: lease.epoch + 1, size: bytes.byteLength,
      chunks: [hash] }))).message).toBe(SYNC_FENCED);
  });
});

/** Google's record, as the gateway's tests seal it: its model named in the path. */
const GOOGLE = Object.freeze({
  upstream: { provider: "google", api: "google-generative-ai",
              base_url: "https://generativelanguage.googleapis.com/v1beta",
              auth: { header: "x-goog-api-key", scheme: "" } },
  endpoints: [{ origin: "https://generativelanguage.googleapis.com", paths: ["/v1beta/models/*"],
                methods: ["POST"] }],
});

/** OpenRouter's record, as the bundled source seals it: chat completions by key. */
const OPENROUTER = Object.freeze({
  upstream: { provider: "openrouter", api: "openai-completions",
              base_url: "https://openrouter.ai/api/v1",
              auth: { header: "authorization", scheme: "Bearer" } },
  endpoints: [{ origin: "https://openrouter.ai", paths: ["/api/v1/chat/completions",
                                                          "/api/v1/models/user"],
                methods: ["GET", "POST"] }],
});

/** A connection's priced models, each with what one call of it holds at most (µ$). */
async function pricedModels(r: Gated, connection: string) {
  const [{ models }] = (await r.sql("SELECT models FROM connections WHERE id = ?",
                                    connection)) as [{ models: string }];
  return (JSON.parse(models) as ModelRecord[]).filter((model) => model.cost.output > 0)
    .map((model) => ({ model, holds: egressPrice([model], model.contextWindow,
                                                 model.maxTokens, priced) }))
    .sort((a, b) => a.holds - b.holds);
}

/** A lease open and applied on the owner's own caps, and its egress binding. */
async function defaultLease(r: Gated): Promise<Leased> {
  return leased(r, false);
}

describe("a call's reservation (review round 3)", () => {
  it.each([
    ["OpenRouter", OPENROUTER, "/api/v1/chat/completions"],
    ["ChatGPT's Responses API", OPENAI, "/v1/responses"],
  ])("lets a fresh owner on the default caps call a normal %s model", async (_what, record, path) => {
    const r = await gated();
    const connection = await r.connect("sk-good", record as never);
    const priced = await pricedModels(r, connection);
    const normal = priced[Math.floor(priced.length / 2)]!;
    const lease = await defaultLease(r);
    const opened = await admitted(r, lease, connection, { path, model: normal.model.id });
    expect(opened.metering).toMatchObject({ model: normal.model.id });
    // A Responses call that names no tier is held at the dearest its API allows.
    const tiered = apiRules(record.upstream.api)!.tier;
    expect((await charges(r))[0])
      .toMatchObject({ est_cost: Math.ceil(normal.holds * (tiered?.absent ?? 1)) });
  });

  it("reads a model a path names, as Google's API names it", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good", GOOGLE as never);
    const named = (await pricedModels(r, connection))[0]!;
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection, {
      path: `/v1beta/models/${named.model.id}:streamGenerateContent?alt=sse`,
      model: named.model.id });
    expect(opened.metering).toMatchObject({ model: named.model.id });
    expect(await refusedWith(r, lease.as(), connection, {
      path: "/v1beta/models/no-such-model:streamGenerateContent?alt=sse",
      model: "no-such-model" }))
      .toBe("egress_unknown_model");
    // The path names the model: a reserve naming another, listed or not, is not this call.
    expect(await refusedWith(r, lease.as(), connection, {
      path: "/v1beta/models/no-such-model:streamGenerateContent?alt=sse",
      model: named.model.id }))
      .toBe("egress_unknown_model");
  });

  it("holds a call of the dearest model at that model's own price", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good", OPENAI);
    const dearest = (await pricedModels(r, connection)).at(-1)!;
    const lease = await leased(r);
    await admitted(r, lease, connection, { path: "/v1/responses", model: dearest.model.id,
                                           tier: "default" });
    expect((await charges(r))[0]).toMatchObject({ charge: "held", est_cost: dearest.holds,
                                                  max_output_tokens: dearest.model.maxTokens });
  });

  it("refuses a model the connection does not list, or a tier pi-ai cannot price", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good", OPENAI);
    const lease = await leased(r);
    expect(await refusedWith(r, lease.as(), connection, { path: "/v1/responses",
                                                         model: "no-such-model" }))
      .toBe("egress_unknown_model");
    expect(await refusedWith(r, lease.as(), connection, { path: "/v1/responses",
                                                         model: OPENAI_MODEL, tier: "scale" }))
      .toBe("egress_unpriced_option");
    // Each refused at its reserve: its row a receipt, charged nothing.
    expect(await charges(r)).toMatchObject([{ state: "refused", charge: null },
                                            { state: "refused", charge: null }]);
  });

  it("holds a priority-tier call, or one naming no tier, at the most pi-ai prices a tier at",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good", OPENAI);
    const named = (await pricedModels(r, connection))[0]!;
    const lease = await leased(r);
    const call = (tier: string | null) => admitted(r, lease, connection, {
      path: "/v1/responses", model: named.model.id, tier });
    const standard = await call("default");
    const priority = await call("priority");
    // No tier: the account's own default applies, which may be priority.
    const unnamed = await call(null);
    expect(standard.metering).toMatchObject({ tier: "default" });
    expect(priority.metering).toMatchObject({ tier: "priority" });
    expect(unnamed.metering).toMatchObject({ tier: null });
    expect((await charges(r)).map((row) => row["est_cost"]))
      .toEqual([named.holds, Math.ceil(named.holds * 2.5), Math.ceil(named.holds * 2.5)]);
  });
});

describe("admit, then reserve (egress shape research, A′)", () => {
  it("opens a model call uncharged, then charges it once the gateway's parse reserves it",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await r.stub.egressOpen(lease.as(), request(connection));
    if (!opened.ok) {
      throw new Error(`refused ${opened.code}`);
    }
    expect(opened.metered).toBe(true);
    expect(opened).not.toHaveProperty("metering");
    expect(await charges(r)).toEqual([{ state: "open", source: null, charge: null, rule: null,
                                        max_output_tokens: null, est_tokens: null,
                                        est_cost: null, tokens: null, cost: null }]);
    expect(await runs(r)).toEqual([]);
    const reserved = await r.stub.egressReserve(lease.as(), reserving(opened.reservation));
    expect(reserved).toMatchObject({ ok: true, metering: { input_bytes: 120, model: MODEL,
                                                           tier: null } });
    expect((await charges(r))[0]).toMatchObject({ state: "open", source: "anthropic",
                                                  charge: "held" });
    // The charge counts to its run, and to the source's day, as an admitted call's did.
    expect(await runs(r)).toMatchObject([{ requests: 1 }]);
    expect(await ended(opened, 50)).toBe(false);
  });

  it("refuses a second reserve, a free call's, a settled call's, or another token's",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const once = await admitted(r, lease, connection);
    expect((await failure(() => r.stub.egressReserve(lease.as(), reserving(once.reservation))))
      .message).toMatch(/^invalid egress reserve/);
    const free = await admitted(r, lease, connection, { method: "GET", path: "/v1/models",
                                                        bytes_hint: null });
    expect((await failure(() => r.stub.egressReserve(lease.as(), reserving(free.reservation))))
      .message).toMatch(/^invalid egress reserve/);
    const opened = await r.stub.egressOpen(lease.as(), request(connection));
    if (!opened.ok) {
      throw new Error(`refused ${opened.code}`);
    }
    const other = lease.as(generationOf(lease.opened.network.network)!, "f".repeat(64));
    expect((await failure(() => r.stub.egressReserve(other, reserving(opened.reservation))))
      .message).toMatch(/^invalid egress reserve/);
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation)))
      .toEqual({ settled: true });
    expect((await failure(() => r.stub.egressReserve(lease.as(), reserving(opened.reservation))))
      .message).toMatch(/^invalid egress reserve/);
    for (const bad of [{ reservation: opened.reservation, model: "a b", tier: null },
                       { reservation: opened.reservation, model: MODEL },
                       { reservation: "x", model: MODEL, tier: null }]) {
      expect((await failure(() => r.stub.egressReserve(lease.as(), bad as never))).message)
        .toMatch(/^invalid egress reserve/);
    }
  });

  it("ends the stream of a call it refuses, its row the refusal's receipt", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await r.stub.egressOpen(lease.as(), request(connection));
    if (!opened.ok) {
      throw new Error(`refused ${opened.code}`);
    }
    const refused = await r.stub.egressReserve(lease.as(), reserving(opened.reservation, {
      model: "no-such-model" }));
    expect(refused).toEqual({ ok: false, code: "egress_unknown_model",
                              receipt: opened.reservation });
    expect(await ended(opened)).toBe(true);
    expect(await rows(r)).toMatchObject([{ state: "refused", code: "egress_unknown_model" }]);
    // The door's settle after it changes nothing.
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation)))
      .toEqual({ settled: false });
  });

  it("settles an admitted call lost when its beats stop before any reserve, charged nothing",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await r.stub.egressOpen(lease.as(), request(connection));
    if (!opened.ok) {
      throw new Error(`refused ${opened.code}`);
    }
    await r.sql("UPDATE egress_reservations SET opened_at = opened_at - ?", LOST_AFTER_S + 1);
    await admitted(r, lease, connection);  // the next open recovers the lost one first
    expect((await charges(r))[0]).toMatchObject({ state: "settled", charge: null });
    expect(await state(r, opened.reservation)).toEqual({ state: "settled", outcome: "lost" });
    expect(await ended(opened)).toBe(true);
    expect((await failure(() => r.stub.egressReserve(lease.as(), reserving(opened.reservation))))
      .message).toMatch(/^invalid egress reserve/);
  });
});

describe("the spend caps", () => {
  it.each([
    ["the run's requests", { turn_requests: 1 }, null, "run_requests"],
    ["the run's cost", { turn_cost: 1 }, null, "run_cost"],
    ["the run's tokens", { turn_tokens: 100 }, null, "run_tokens"],
    ["the run's tokens, the input counted in them", { turn_tokens: 1_100 }, null, "run_tokens"],
    ["the owner's day", { daily_cost: 1 }, null, "daily_cost"],
    ["the owner's month", { monthly_cost: 1 }, null, "monthly_cost"],
    ["the source's day", { sources: { anthropic: { daily_cost: 1 } } }, null,
     "source_daily_cost"],
    ["the source's month", { sources: { anthropic: { monthly_cost: 1 } } }, null,
     "source_monthly_cost"],
  ])("refuse a call past %s before any forward, its receipt kept",
     async (_what, models, _none, rule) => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await cappedLeased(r, { ...ROOMY, ...models });
    if (rule === "run_requests") {
      await admitted(r, lease, connection);
    }
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    expect((await charges(r)).at(-1)).toMatchObject({ state: "refused", charge: null, rule });
  });

  it("price each model to its own window and cap, at its dearest rates", () => {
    const rates = { input: 1, output: 10, cacheRead: 0, cacheWrite: 2 };
    const wide = { cost: rates, contextWindow: 1_000_000, maxTokens: 1_000 } as ModelRecord;
    const dear = { cost: { ...rates, output: 20, cacheWrite: 3 }, contextWindow: 100_000,
                   maxTokens: 50_000 } as ModelRecord;
    // The wide model's million input tokens at 2, its thousand output at 10; the dear one's
    // hundred thousand at 3 and its 50,000 at 20: the dearer of the two.
    expect(egressPrice([wide, dear], 1_000_000, 50_000, priced))
      .toBe(Math.max(1_000_000 * 2 + 1_000 * 10, 100_000 * 3 + 50_000 * 20));
  });

  it("refuse a call the caps leave fewer than the floor's output tokens", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const [{ models }] = (await r.sql("SELECT models FROM connections WHERE id = ?",
                                      connection)) as [{ models: string }];
    const window = (JSON.parse(models) as ModelRecord[]).find((model) => model.id === MODEL)!
      .contextWindow;
    // Room for the input and 500 output tokens: under the 1,024 a call is worth.
    const lease = await cappedLeased(r, { ...ROOMY, turn_tokens: window + 500 });
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    expect((await charges(r)).at(-1)).toMatchObject({ rule: "run_tokens" });
  });

  it("prune the spend counters at each open, so an owner who uses only the sandbox never " +
     "fills them", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    // Old counters, long out of the window: as many as the schema keeps.
    await r.sql("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) " +
                  "INSERT INTO model_spend (period, source, cost) SELECT 'd:2019-01-01', " +
                  "'old-' || i, 1 FROM n", SPEND_KEPT);
    await admitted(r, lease, connection);
    expect(await r.sql("SELECT COUNT(*) AS n FROM model_spend WHERE period = 'd:2019-01-01'"))
      .toEqual([{ n: 0 }]);
    expect(await sourceSpend(r)).toHaveLength(2);
  });

  it("keep the whole charge when the usage cannot be read, and a refusal's at nothing",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    const [charge] = await charges(r);
    await r.stub.egressSettle(lease.as(), settle(opened.reservation, { outcome: "aborted",
                                                                        spend: { state: "unknown" } }));
    expect(await charges(r)).toMatchObject([{ charge: "unknown", cost: null }]);
    expect(await runs(r)).toMatchObject([{ requests: 1, cost: charge!["est_cost"],
                                           tokens: charge!["est_tokens"] }]);
    expect(await sourceSpend(r)).toEqual([{ p: "d", cost: charge!["est_cost"] },
                                          { p: "m", cost: charge!["est_cost"] }]);
    // A provider's refusal before any output costs nothing, as a loop call's does, and
    // counts as the run's request still.
    const refused = await admitted(r, lease, connection);
    await r.stub.egressSettle(lease.as(), settle(refused.reservation, { status: 429,
                                                                         spend: { state: "failed" } }));
    expect((await charges(r)).at(-1)).toMatchObject({ charge: "failed", cost: 0, tokens: 0 });
    expect(await runs(r)).toMatchObject([{ requests: 2, cost: charge!["est_cost"] }]);
    expect((await failure(() => r.stub.egressSettle(lease.as(), settle(refused.reservation, {
      spend: { state: "succeeded", tokens: -1, cost: 1 } })))).message)
      .toBe("invalid egress settle");
    // The model the answer says served it is kept on the receipt.
    const snapshot = await admitted(r, lease, connection);
    await r.stub.egressSettle(lease.as(), settle(snapshot.reservation, {
      spend: { ...DONE, served: "model-x-2026-01-01" } }));
    expect((await r.stub.egressReceipts(r.bind())).receipts[0])
      .toMatchObject({ reservation: snapshot.reservation, charge: "succeeded",
                       served: "model-x-2026-01-01" });
    expect((await failure(() => r.stub.egressSettle(lease.as(), settle(snapshot.reservation, {
      spend: { ...DONE, served: "a b" } })))).message).toBe("invalid egress settle");
    // A settled charge never moves again.
    await expect(r.sql("UPDATE egress_reservations SET charge = 'held' WHERE charge = 'failed'"))
      .rejects.toThrow(/settled once/);
  });

  it("let two calls at once never pass a cap between them", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const probe = await gated();
    const probed = await probe.connect("sk-good");
    const box = await leased(probe);
    await admitted(probe, box, probed);
    const [{ est_cost: one }] = (await charges(probe)) as [{ est_cost: number }];
    // Room for one whole call's charge, not two.
    const lease = await cappedLeased(r, { ...ROOMY, turn_cost: one + Math.floor(one / 2) });
    const [first, second] = await Promise.all([
      r.stub.egressOpen(lease.as(), request(connection)),
      r.stub.egressOpen(lease.as(), request(connection))]);
    if (!first.ok || !second.ok) {
      throw new Error("both are admitted: admission charges nothing");
    }
    // Their reserves at once, each in its own unit.
    const reserved = await Promise.all([first, second].map((opened) =>
      r.stub.egressReserve(lease.as(), reserving(opened.reservation))));
    expect(reserved.map((each) => each.ok)).toContain(true);
    const holding = (await charges(r)).filter((row) => row["charge"] === "held");
    const total = holding.reduce((sum, row) => sum + (row["est_cost"] as number), 0);
    expect(total).toBeLessThanOrEqual(one + Math.floor(one / 2));
    expect((await runs(r))[0]!["cost"]).toBe(total);
    // The second is shrunk to what the first left, or refused when that is under the floor.
    const refusals = (await charges(r)).filter((row) => row["state"] === "refused");
    for (const refusal of refusals) {
      expect(refusal).toMatchObject({ rule: "run_cost" });
    }
  });

  it("share the source's day and month with the model reservations, in both directions",
     async () => {
    // One whole call's charge, as a fresh owner's first call holds it.
    const probe = await gated();
    const probed = await probe.connect("sk-good");
    await admitted(probe, await leased(probe), probed);
    const [{ est_cost: one }] = (await charges(probe)) as [{ est_cost: number }];
    const r = await gated();
    const connection = await r.connect("sk-good");
    // Room for that call, but not once a model reservation has spent as much.
    const lease = await cappedLeased(r, { ...ROOMY,
                                          sources: { anthropic: { daily_cost: one + 500_000 } } });
    // A model call on the same source, settled, as `usage.ts` records one.
    const day = new Date().toISOString().slice(0, 10);
    await r.sql("INSERT INTO model_reservations (id, run, connection_id, source, model, state, " +
                  "max_output_tokens, est_tokens, est_cost, tokens, cost, policy_generation, " +
                  "day, month, created_at, settled_at) VALUES ('r' || lower(hex(randomblob(16))), " +
                  "'s:t', ?, 'anthropic', 'model-x', 'reserved', 1000, 1000, ?, NULL, NULL, " +
                  "2, ?, ?, ?, NULL)", connection, one, day, day.slice(0, 7), Date.now() / 1000);
    await r.sql("UPDATE model_reservations SET state = 'dispatched' WHERE state = 'reserved'");
    await r.sql("UPDATE model_reservations SET state = 'succeeded', tokens = 900, " +
                  "cost = ?, settled_at = ? WHERE state = 'dispatched'", one, Date.now() / 1000);
    // That spend leaves the source's day no room for the sandbox's call.
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    expect((await charges(r)).at(-1)).toMatchObject({ rule: "source_daily_cost" });
    // And the sandbox's spend is on the counters the model reservations read.
    await capped(r, ROOMY);
    const opened = await admitted(r, lease, connection);
    await r.stub.egressSettle(lease.as(), settle(opened.reservation));
    expect(await sourceSpend(r)).toEqual([{ p: "d", cost: one + 4200 },
                                          { p: "m", cost: one + 4200 }]);
  });

  it("count a run as its own: a new run starts afresh, its own counters on its receipt",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await cappedLeased(r, { ...ROOMY, turn_requests: 2 });
    const open = await admitted(r, lease, connection);
    await admitted(r, lease, connection);
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    await r.stub.egressSettle(lease.as(), settle(open.reservation));
    // Settled, its request still counts: the run's cap is its calls, not those open.
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    // A second run beside it, on the same lease, starts afresh on counters of its own.
    const next = await leased(r, false);
    expect(next.lease_id).toBe(lease.lease_id);
    expect(next.run).not.toBe(lease.run);
    await admitted(r, next, connection);
    expect(await runs(r)).toMatchObject([{ run: lease.run, requests: 2 },
                                         { run: next.run, requests: 1 }]);
    // The first run's own calls still refuse it, never the second's.
    RUN = lease.run;
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    // A settled run keeps its counters on its receipt.
    await over(r, lease);
    expect(await r.stub.agentStatus(r.bind(), { run_id: lease.run }))
      .toMatchObject({ state: "succeeded", requests: 2 });
  });
});

describe("a change that ends egress", () => {
  it("is a policy write: its streams end; the network is stale only when egress changed",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                            policy: { ...policy, models: { ...ROOMY,
                                                                           turn_requests: 50 } } });
    expect(await ended(opened)).toBe(true);
    expect(await rows(r)).toMatchObject([{ state: "open", ended: "policy" }]);
    // A write that leaves the egress table as it was leaves the network current: the lane
    // it wakes re-applies nothing.
    await drainDue(r.stub);
    expect((await recorder(r.stub)).networks).toEqual([]);
    await admitted(r, lease, connection);
    // A policy change outside the document (an agent's wake limits: \`Policy.changed\`), neither.
    await r.sql("UPDATE owner_policy SET generation = generation + 1");
    await admitted(r, lease, connection);
    // An egress write whose re-apply finds no sandbox (\`lost\`): the network stays stale.
    await scripted(r.stub, { networkAnswer: { state: "lost" } });
    const latest = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: latest.generation, policy: {
      ...latest.policy, egress: { mode: "allowlist", destinations: [] } } });
    await drainDue(r.stub);
    const { networks } = await recorder(r.stub);
    expect(networks).toMatchObject([{
      lease: { lease_id: lease.lease_id, epoch: lease.epoch },
      network: { mode: "allowlist", boundary: false } }]);
    expect(networks.map((each) => generationOf(each.network.network))).toEqual([2]);
    expect(await refusedWith(r, lease.as(1), connection)).toBe("egress_policy_stale");
    expect(await refusedWith(r, lease.as(2), connection)).toBe("egress_network_stale");
    // The next start that opens a lease carries the new network, and applies it.
    later();
    const next = await leased(r, false);
    expect(generationOf(next.opened.network.network)).toBe(2);
    expect(await r.stub.egressOpen(next.as(2), request(connection)))
      .toMatchObject({ ok: true, generation: 2 });
  });

  it("is its run's end, its lease's successor, or its connection's revoke, each its own " +
     "streams only", async () => {
    const r = await gated();
    const first = await r.connect("sk-good");
    const second = await r.connect("sk-good-2", ANTHROPIC, "Second");
    const third = await r.connect("sk-good-3", ANTHROPIC, "Third");
    const lease = await leased(r, true, onKey(first));
    const a = await admitted(r, lease, first);
    // Another connection's revoke leaves the stream be; its own connection's ends it.
    await r.stub.connectionRevoke(r.session(), { connection: second });
    expect(await ended(a, 300)).toBe(false);
    await r.stub.connectionRevoke(r.session(), { connection: first });
    expect(await ended(a)).toBe(true);
    await over(r, lease);
    const next = await leased(r, true, onKey(third));
    expect(next.lease_id).not.toBe(lease.lease_id);
    const b = await admitted(r, next, third);
    await over(r, next);
    expect(await ended(b)).toBe(true);
    expect(await rows(r)).toMatchObject([{ ended: "connection" }, { ended: "run" }]);
    const last = await leased(r, true, onKey(third));
    const c = await admitted(r, last, third);
    later();
    await leased(r, false, onKey(third));
    // The lapsed lease's run was lost: settled unknown, its streams ended with it.
    expect(await ended(c)).toBe(true);
    expect((await rows(r)).at(-1)).toMatchObject({ ended: "run" });
    expect(await r.stub.agentStatus(r.bind(), { run_id: last.run }))
      .toMatchObject({ state: "unknown", code: "lost" });
  });
});

describe("a lease given back (untangle A2, review F5)", () => {
  it("ends its streams when a sign-in that held it ends (leaseFreed)", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    await capped(r, ROOMY);
    await defineAgent(r.stub, r.bind(), "main", { ...onKey(connection), scopes: [] });
    // A sign-in holds the owner's lease first, so the run joins it: the sign-in's start
    // opened the lease, carrying its network.
    const { id } = await r.stub.signinStart(r.bind(), { engine: "claude-code" }) as { id: string };
    const signin = (await recorder(r.stub)).signins.at(-1)! as SandboxSignin;
    const sl = signin as unknown as SandboxStart;
    expect(await r.sql("SELECT network_generation FROM sandbox_leases"))
      .toEqual([{ network_generation: generationOf(sl.network!.network) }]);
    await scripted(r.stub, { answer: { started: false, code: SANDBOX_WAITING } });
    let call: EgressOpened | EgressRefusal | null = null;
    const run = await whileOut("start", () => r.stub.agentSubmit(
      r.bind({ tiers: TIERS }), { message: "go", client_send_id: "q-2" }), async () => {
      const start = (await recorder(r.stub)).starts.at(-1)!;
      call = await r.stub.egressOpen({
        owner: r.name, caller: r.name, origin: null, sandbox: {
          kind: "egress", token: hashOf("egress", egressTokenOf(sl.network!.network)!),
          generation: generationOf(sl.network!.network)!, secret: hashed(secretOf(start)) } },
        { run: start.run, connection, method: "GET", path: "/v1/models", bytes_hint: null });
    });
    expect(run).toMatchObject({ state: "waiting", code: SANDBOX_WAITING });
    expect(call).toMatchObject({ ok: true });
    // The sign-in still holds the lease: the run's wait gave nothing back, the stream is live.
    expect((await rows(r)).at(-1)).toMatchObject({ state: "open", ended: null });
    // The sign-in ends: its lease is given back, and the stream under it with it.
    await r.stub.signinEvents(runnerSandbox(r, sl), { id, frames: [
      { seq: 1, at: Date.now() / 1000, event: { type: "ta.signin", state: "cancelled" } }],
      end: { exit_code: 1 } });
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
    expect((await rows(r)).at(-1)).toMatchObject({ ended: "lease" });
  });

  it("ends its streams: a start the sandbox has no room for gives its lease back", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    await capped(r, ROOMY);
    await defineAgent(r.stub, r.bind(), "main", { ...onKey(connection), scopes: [] });
    // While the start is out (the run queued, its lease and grant live), the run's process
    // opens a model call on its lease; then the sandbox answers it has no room.
    await scripted(r.stub, { answer: { started: false, code: SANDBOX_WAITING } });
    let call: EgressOpened | EgressRefusal | null = null;
    const run = await whileOut("start", () => r.stub.agentSubmit(
      r.bind({ tiers: TIERS }), { message: "go", client_send_id: "q-1" }), async () => {
      const start = (await recorder(r.stub)).starts.at(-1)!;
      call = await r.stub.egressOpen({
        owner: r.name, caller: r.name, origin: null, sandbox: {
          kind: "egress", token: hashOf("egress", egressTokenOf(start.network!.network)!),
          generation: generationOf(start.network!.network)!, secret: hashed(secretOf(start)) } },
        { run: start.run, connection, method: "GET", path: "/v1/models", bytes_hint: null });
    });
    expect(run).toMatchObject({ state: "waiting", code: SANDBOX_WAITING });
    expect(call).toMatchObject({ ok: true });
    // Its run never started, so no run's end ended the stream: the lease's did, its signal
    // closed as the start's answer gave the lease back.
    expect(await ended(call! as EgressOpened)).toBe(true);
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
    expect((await rows(r)).at(-1)).toMatchObject({ ended: "lease" });
  });
});

describe("the owner's erasure", () => {
  it("ends every stream, and then every door refuses", async () => {
    // The faux-model class, whose test hook reaches the erasure's internal path.
    const owner = `owner-${crypto.randomUUID()}`;
    const stub: DurableObjectStub<FauxOwnerDO> = env.AGENT.getByName(owner);
    const bind = (extra: Partial<Binding> = {}): Binding =>
      ({ owner, caller: owner, origin: null,
         ...("sandbox" in extra ? {} : { tiers: TIERS }), ...extra });
    const { session: cookie } = await stub.sessionOpen(bind({ authenticated_at: signedIn() }));
    const session = bind({ session: cookie });
    const prepared = await stub.connectionPrepare(session, {
      source: "anthropic", kind: "api_key", display_name: "Key", provider: "anthropic",
      revocable: false });
    const sealed = await env.CONNECT.seal(owner, prepared.connection, 1, "sk-good", ANTHROPIC);
    await stub.connectionCommit(session, {
      connection: prepared.connection, version: 1, blob: sealed.blob, kid: sealed.kid,
      models: await catalog(ANTHROPIC), egress: await scopeOf(ANTHROPIC) });
    const { start } = await ownerRun(stub, bind(), { engine: "pi", credential: "api_key",
                                                     connection: prepared.connection,
                                                     scopes: [] });
    RUN = start.run;
    const sandbox = bind({ sandbox: { kind: "egress",
                                      token: hashOf("egress",
                                                    egressTokenOf(start.network!.network)!),
                                      generation: 1, secret: hashed(secretOf(start)) } });
    const open = await stub.egressOpen(sandbox, request(prepared.connection));
    expect(open.ok).toBe(true);
    await runInDurableObject(stub, async (instance: FauxOwnerDO) => {
      await instance.erasure.erase();
    });
    expect(await ended(open as EgressOpened)).toBe(true);
    expect((await failure(() => stub.egressOpen(sandbox, request(prepared.connection))))
      .message).toMatch(/^owner_erased/);
  });
});

describe("a reservation", () => {
  it("beaten by its door stays open; with its beats stopped it is settled lost within " +
     "minutes, and past the day's deadline whatever its beats", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    // Opened long ago, but beaten just now: its door still streams it.
    await r.sql("UPDATE egress_reservations SET opened_at = opened_at - ?", 10 * LOST_AFTER_S);
    expect(await r.stub.egressBeat(lease.as(), { reservation: opened.reservation }))
      .toEqual({ beat: true });
    await admitted(r, lease, connection);
    expect((await state(r, opened.reservation)))
      .toMatchObject({ state: "open" });
    // Its beats stop (a doors restart): lost three beats on, its slot and charge let go.
    await r.sql("UPDATE egress_reservations SET beat_at = beat_at - ? WHERE id = ?",
                LOST_AFTER_S + 1, opened.reservation);
    await admitted(r, lease, connection);
    expect((await state(r, opened.reservation)))
      .toMatchObject({ state: "settled", outcome: "lost" });
    expect(await r.stub.egressBeat(lease.as(), { reservation: opened.reservation }))
      .toEqual({ beat: false });
    // A row beaten forever is still lost past the door's one-day deadline.
    const forever = await admitted(r, lease, connection);
    await r.sql("UPDATE egress_reservations SET opened_at = opened_at - ? WHERE id = ?",
                EGRESS_DEADLINE_S + 2 * EGRESS_BEAT_S, forever.reservation);
    await r.stub.egressBeat(lease.as(), { reservation: forever.reservation });
    await admitted(r, lease, connection);
    expect((await state(r, forever.reservation)))
      .toMatchObject({ state: "settled", outcome: "lost" });
  });

  it("never settled is settled lost past its door's deadline, its charge unknown", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    await r.sql("UPDATE egress_reservations SET opened_at = opened_at - ?", LOST_AFTER_S - 5);
    await admitted(r, lease, connection);
    expect((await rows(r))[0]).toMatchObject({ state: "open" });
    await r.sql("UPDATE egress_reservations SET opened_at = opened_at - 10 WHERE id = ?",
                opened.reservation);
    await admitted(r, lease, connection);
    expect((await rows(r))[0]).toMatchObject({ state: "settled", outcome: "lost" });
    expect((await charges(r))[0]).toMatchObject({ charge: "unknown", cost: null });
    expect(await ended(opened)).toBe(true);
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation)))
      .toEqual({ settled: false });
  });

  it("keeps a blob the gateway resealed meanwhile, by the connection's compare-and-swap",
     async () => {
    const r = await gated();
    const prepared = await r.stub.connectionPrepare(r.session(), {
      source: "openai", kind: "api_key", display_name: "Old", provider: "openai",
      revocable: false });
    const blob = await sealedAs("k1", r.name, prepared.connection, 1, {
      kind: "api_key", secret: "sk-old", upstream: OPENAI.upstream,
      endpoints: OPENAI.endpoints, sealed_at: 1 });
    await r.stub.connectionCommit(r.session(), {
      connection: prepared.connection, version: 1, blob, kid: "k1",
      models: await catalog(OPENAI), egress: await scopeOf(OPENAI) });
    const lease = await leased(r);
    const opened = await admitted(r, lease, prepared.connection,
                                  { path: "/v1/responses", model: OPENAI_MODEL });
    const resealed = await env.GATEWAY.reseal(r.name, prepared.connection, 1, opened.blob);
    expect(await r.stub.egressSettle(lease.as(), { ...settle(opened.reservation), resealed }))
      .toEqual({ settled: true });
    expect(await r.sql("SELECT version, kid FROM connection_secrets WHERE connection_id = ?",
                       prepared.connection)).toEqual([{ version: 2, kid: resealed.kid }]);
  });
});

describe("the sandbox's lease", () => {
  it("is its authority's sandbox lease, with its two MAC'd tokens' hashes and the runner's, " +
     "which its last run's end clears",
     async () => {
    const r = await gated();
    const lease = await leased(r);
    const tokens = { egress: lease.opened.egress_token, sync: lease.opened.sync_token };
    for (const [kind, token] of Object.entries(tokens) as ["egress" | "sync", string][]) {
      expect(token).toMatch(/^[A-Za-z0-9_-]+\.0\.[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}$/);
      // It has no deadline of its own: it lives while its lease is renewed.
      expect(verifyToken(env.ROUTING_MAC_KEY, kind, token)!.expires).toBe(0);
      expect(verifyToken(env.ROUTING_MAC_KEY, kind, token)!.owner).toBe(r.name);
      // Each is its own kind's alone.
      expect(verifyToken(env.ROUTING_MAC_KEY, kind === "egress" ? "sync" : "egress", token))
        .toBeNull();
    }
    expect(lease.egress).not.toBe(lease.sync);
    expect(await r.sql("SELECT authority, lease_id, epoch, egress_token, sync_token, " +
                         "network_generation FROM sandbox_leases"))
      .toEqual([{ authority: "owner", lease_id: lease.lease_id, epoch: lease.epoch,
                  egress_token: lease.egress, sync_token: lease.sync,
                  network_generation: 1 }]);
    expect(await r.sql("SELECT epoch FROM file_space")).toEqual([{ epoch: lease.epoch }]);
    expect(JSON.stringify(await r.sql("SELECT * FROM sandbox_leases")))
      .not.toContain(lease.opened.egress_token.split(".")[2]);
    // Its runner's every event batch is its heartbeat: the lease lives on from that batch.
    const [opened] = await r.sql("SELECT lease_expires FROM sandbox_leases") as
      { lease_expires: number }[];
    later(30);
    await r.stub.runEvents(runnerBinding(r, lease.start), { frames: [] });
    const [renewed] = await r.sql("SELECT lease_expires FROM sandbox_leases") as
      { lease_expires: number }[];
    expect(renewed!.lease_expires).toBeGreaterThanOrEqual(opened!.lease_expires + 29);
    // No run meets a deadline: the tokens die with the lease, given back once its last run
    // ends.
    await over(r, lease);
    expect(await r.sql("SELECT lease_id FROM sandbox_leases")).toEqual([]);
    expect(await refusedWith(r, lease.as(), await r.connect("sk-good")))
      .toBe("egress_lease_closed");
    const first = await leased(r);
    later();
    const next = await leased(r);
    // A lapsed lease's successor has tokens of its own, its network applied by its own start.
    expect(await r.sql("SELECT egress_token, network_generation FROM sandbox_leases"))
      .toEqual([{ egress_token: next.egress, network_generation: 1 }]);
    expect(first.egress).not.toBe(next.egress);
  });

  it("hands the sandbox slice its policy and its clients' environment", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const { start } = await ownerRun(r.stub, r.bind(), { engine: "claude-code",
                                                         credential: "api_key", connection,
                                                         scopes: [] });
    const { config } = start;
    expect(config.generation).toBe(1);
    expect(config.egress).toEqual({ mode: "open", destinations: [] });
    // The run's whole environment: HOME, the record's own variables and the key's.
    expect(config.env).toMatchObject({ HOME: "{home}", CLAUDE_CONFIG_DIR: "{home}/.claude",
                                       DISABLE_AUTOUPDATER: "1" });
    expect(config.inherit).toEqual(RUN_BASICS);
    // Claude Code carries the run's secret as a header, its base URL the run alone.
    expect(config.env["ANTHROPIC_BASE_URL"])
      .toBe(`https://egress.tinyassets.io/r/${start.run}/c/${connection}/`);
    expect(config.env["ANTHROPIC_CUSTOM_HEADERS"]).toBe(`x-ta-run: ${secretOf(start)}`);
    expect(config.env["ANTHROPIC_API_KEY"]).toMatch(new RegExp(`^ta_ph_${connection}_[0-9a-f]{32}$`));
    expect(JSON.stringify(config)).not.toContain("sk-good");
  });

  it("gives a run on the owner's own sign-in no key, though the owner has one", async () => {
    const r = await gated();
    await r.connect("sk-good");
    const { start: { config } } = await ownerRun(r.stub, r.bind(), { ...SIGNED_IN, scopes: [] });
    // The allow-list alone: HOME and Claude Code's own variables, no key and no base URL.
    expect(config.env).toEqual(runEnv(bundledSources(), "claude-code",
                                      "vendor_login_in_sandbox").env);
    expect(config.env["HOME"]).toBe("{home}");
    expect(Object.keys(config.env).filter((name) => /API_KEY|BASE_URL|TOKEN/.test(name)))
      .toEqual([]);
    expect(config.inherit).toEqual(RUN_BASICS);
  });
});

describe("a sandbox's network (E1)", () => {
  /** The egress door's binding for `start`'s lease and run, at the transform's `generation`. */
  const egressOf = (r: Gated, start: SandboxStart, generation: number) => r.bind({ sandbox: {
    kind: "egress", token: hashOf("egress", egressTokenOf(start.network!.network)!), generation,
    secret: hashed(secretOf(start)) } });

  it("is marked by the unit that dispatches the start carrying it: the run's first model " +
     "call is admitted, with nothing else marking it", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    await capped(r, ROOMY);
    const { start } = await ownerRun(r.stub, r.bind(), { ...onKey(connection), scopes: [] });
    RUN = start.run;
    expect(generationOf(start.network!.network)).toBe(1);
    const opened = await r.stub.egressOpen(egressOf(r, start, 1), request(connection));
    expect(opened.ok || opened.code).toBe(true);  // its refusal's code, were it refused
    expect(opened).toMatchObject({ ok: true, connection, generation: 1 });
    expect(await r.sql("SELECT network_generation FROM sandbox_leases"))
      .toEqual([{ network_generation: 1 }]);
  });

  it("admits the run's first model call made before its start's answer is recorded",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    await capped(r, ROOMY);
    // The runner launched, its CLI calls its model at once: the start has not answered yet.
    let call: EgressOpened | EgressRefusal | null = null;
    const { run, start } = await whileOut("start", () => ownerRun(
      r.stub, r.bind(), { ...onKey(connection), scopes: [] }), async () => {
      const out = (await recorder(r.stub)).starts.at(-1)!;
      RUN = out.run;
      call = await r.stub.egressOpen(egressOf(r, out, generationOf(out.network!.network)!),
                                     request(connection));
    });
    expect(run).toMatchObject({ state: "running" });
    expect(call).toMatchObject({ ok: true, connection, generation: generationOf(start.network!.network),
                                 metered: true });
  });

  it("is applied at that start's own generation: a policy write during the start leaves " +
     "the newer one stale", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    await capped(r, ROOMY);
    // The write's re-apply reaches the Worker before this start does: no sandbox has the
    // lease yet (\`lost\`), so the lane tries it again later.
    await scripted(r.stub, { networkAnswer: { state: "lost" } });
    const { start } = await whileOut("start", () => ownerRun(
      r.stub, r.bind(), { ...onKey(connection), scopes: [] }), async () => {
      const { generation, policy } = await r.stub.policyRead(r.bind());
      await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
        ...policy, egress: { mode: "allowlist", destinations: [] } } });
    });
    RUN = start.run;
    expect(generationOf(start.network!.network)).toBe(1);
    expect(await r.sql("SELECT network_generation FROM sandbox_leases"))
      .toEqual([{ network_generation: 1 }]);
    expect(await refusedWith(r, egressOf(r, start, 1), connection)).toBe("egress_policy_stale");
    expect(await refusedWith(r, egressOf(r, start, 2), connection)).toBe("egress_network_stale");
  });
});

describe("a live lease's network re-applied after an egress write (E2)", () => {
  /**
   * The owner's egress table changed (the egress generation moves, to 2 from a fresh 1),
   * then the pass of the `runs` lane the write's unit woke.
   */
  async function egressWrite(r: Gated, mode: "allowlist" | "open" = "allowlist"):
      Promise<void> {
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
      ...policy, egress: { mode, destinations: [] } } });
    await drainDue(r.stub);
  }

  /** The lease's network as the object holds it: confirmed, out, and its retry clock. */
  function networkRow(r: Gated) {
    return r.sql("SELECT network_generation, network_pending, network_tried_at IS NOT NULL " +
                 "AS tried FROM sandbox_leases");
  }

  /** The `runs` lane's pass now, as its alarm would run it. */
  async function laneNow(r: Gated): Promise<void> {
    await r.sql("UPDATE cf_agents_jobs SET time = 0");
    await drainDue(r.stub);
  }

  it("is re-applied by the lane the write wakes: a call under the new generation is " +
     "admitted from the moment the re-apply is out", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    await admitted(r, lease, connection);
    // While the re-apply is out, the run's call through the new network: only that network's
    // transform sets the new generation, so it is admitted before the answer comes.
    let call: EgressOpened | EgressRefusal | null = null;
    await whileOut("network", () => egressWrite(r), async () => {
      call = await r.stub.egressOpen(lease.as(2), request(connection));
    });
    const [sent, ...more] = (await recorder(r.stub)).networks;
    expect(more).toEqual([]);
    expect(sent).toMatchObject({ authority: "owner",
                                 lease: { lease_id: lease.lease_id, epoch: lease.epoch },
                                 network: { mode: "allowlist" } });
    // Its plan sets the new generation and no egress token: the Worker keeps the live one.
    expect([generationOf(sent!.network.network), egressTokenOf(sent!.network.network)])
      .toEqual([2, null]);
    expect(call).toMatchObject({ ok: true, generation: 2 });
    expect(await networkRow(r)).toEqual([{ network_generation: 2, network_pending: null,
                                           tried: 0 }]);
    // The run's next call is admitted under the new generation; the old one is refused.
    expect(await r.stub.egressOpen(lease.as(2), request(connection)))
      .toMatchObject({ ok: true, generation: 2 });
    expect(await refusedWith(r, lease.as(1), connection)).toBe("egress_policy_stale");
  });

  it("is tried again by the lane once it failed, its calls refused until it lands; at " +
     "once under a newer generation", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    await scripted(r.stub, { networkAnswer: "throw" });
    await egressWrite(r);
    expect((await recorder(r.stub)).networks).toHaveLength(1);
    // Failed: nothing is out under the new generation, so its calls stay refused.
    expect(await networkRow(r)).toEqual([{ network_generation: 1, network_pending: null,
                                           tried: 1 }]);
    expect(await refusedWith(r, lease.as(2), connection)).toBe("egress_network_stale");
    // The lane waits out its retry clock: a pass before then sends nothing.
    await laneNow(r);
    expect((await recorder(r.stub)).networks).toHaveLength(1);
    expect(await refusedWith(r, lease.as(2), connection)).toBe("egress_network_stale");
    // Past it, the lane sends it again, and this time it lands.
    // A newer generation is sent at once, whatever the clock.
    await egressWrite(r, "open");
    expect((await recorder(r.stub)).networks.map((each) => generationOf(each.network.network)))
      .toEqual([2, 3]);
    expect(await refusedWith(r, lease.as(3), connection)).toBe("egress_network_stale");
    await scripted(r.stub, { networkAnswer: { state: "applied" } });
    await r.sql("UPDATE sandbox_leases SET network_tried_at = network_tried_at - ?",
                NETWORK_RETRY_S);
    await laneNow(r);
    expect((await recorder(r.stub)).networks).toHaveLength(3);
    expect(await networkRow(r)).toEqual([{ network_generation: 3, network_pending: null,
                                           tried: 0 }]);
    expect(await r.stub.egressOpen(lease.as(3), request(connection)))
      .toMatchObject({ ok: true, generation: 3 });
  });

  it("never sends a re-apply again while one is out: its retry outlasts a call's bound", () => {
    expect(NETWORK_RETRY_S * 1000).toBeGreaterThan(LANE_CALL_MS);
  });

  it("is kept out for a paused sandbox, applied as it resumes, and sent again until it " +
     "is confirmed", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    await scripted(r.stub, { networkAnswer: { state: "at_resume" } });
    await egressWrite(r);
    // Out still: a call through the new network (once its sandbox resumes) is admitted.
    expect(await networkRow(r)).toEqual([{ network_generation: 1, network_pending: 2,
                                           tried: 1 }]);
    expect(await r.stub.egressOpen(lease.as(2), request(connection)))
      .toMatchObject({ ok: true, generation: 2 });
    // The lane asks again past its retry clock; the resumed sandbox has it now.
    await scripted(r.stub, { networkAnswer: { state: "applied" } });
    await r.sql("UPDATE sandbox_leases SET network_tried_at = network_tried_at - ?",
                NETWORK_RETRY_S);
    await laneNow(r);
    expect((await recorder(r.stub)).networks).toHaveLength(2);
    expect(await networkRow(r)).toEqual([{ network_generation: 2, network_pending: null,
                                           tried: 0 }]);
  });

  it("leaves the policy write to answer at once, whatever the sandbox Worker does",
     async () => {
    const r = await gated();
    await leased(r);
    // Every re-apply held at the gate, its answer never given while the write waits.
    await env.SANDBOX_GATE.arm({ method: "network", hold: "before" });
    const { generation, policy } = await r.stub.policyRead(r.bind());
    const written = r.stub.policyWrite(r.session(), { expected_generation: generation,
      policy: { ...policy, egress: { mode: "allowlist", destinations: [] } } });
    // The write answers, on no clock: a write that waited on the sandbox would never answer
    // here, since the re-apply then reaches the gate and stays held, never let go.
    expect(await written).toMatchObject({ policy: { egress: { mode: "allowlist" } } });
    expect(await env.SANDBOX_GATE.held(HELD_WAIT_MS)).toHaveLength(1);
  });
});
