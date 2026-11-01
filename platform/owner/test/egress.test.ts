/**
 * Sandbox egress at the owner's object (tasks 4 G4): the sandbox's one lease and its two
 * MAC'd tokens, owner-internal; each call admitted at open (`egressOpen`) by every check in
 * order, a model call charged against its run's, the owner's and the source's caps and
 * `ask_above`, on counters none of them pi's; a refusal kept as a receipt; the streams a
 * change ends; and each charge settled once (`egressSettle`) at the cost the gateway's
 * meter read, or kept whole when it could not. The egress door itself is the doors' tests'.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { verifyToken } from "../../doors/src/routing-mac";
import { PLATFORM_CALLER } from "../src/admission";
import { EGRESS_LIMITS, type EgressOpened, LOST_AFTER_S } from "../src/egress";
import type { Binding, OpenedLease } from "../src/owner-do";
import { NO_SANDBOX, SANDBOX_DOORS, SANDBOX_ONLY, SYNC_FENCED } from "../src/owner-do";
import {
  ANTHROPIC, catalog, type Gated, gated, OPENAI, scopeOf, sealedAs,
} from "./gateway-support";
import { signedIn } from "./ledger-support";
import { failure } from "./support";
import type { FauxOwnerDO } from "./worker";

interface Leased {
  readonly opened: OpenedLease;
  readonly lease_id: string;
  readonly epoch: number;
  readonly egress: string;
  readonly sync: string;
  /** The egress door's binding for this lease's egress token, at `generation`. */
  readonly as: (generation?: number, token?: string) => Binding;
  /** A sync door's binding for this lease's sync token. */
  readonly synced: (token?: string) => Binding;
}

/** Caps roomy enough that a test's every call fits, each charging up to the most it costs. */
const ROOMY = Object.freeze({ turn_requests: 10_000, turn_cost: 1_000_000_000_000 });

/** A token's secret's hash, once its MAC is the routing key's own for `kind`. */
function hashOf(kind: "egress" | "sync", token: string): string {
  return verifyToken(env.ROUTING_MAC_KEY, kind, token)!.hash;
}

/**
 * The sandbox's lease taken, and unless `apply` is false its network applied; the owner's
 * caps made roomy first, unless `roomy` is false.
 */
async function leased(r: Gated, apply = true, roomy = true): Promise<Leased> {
  if (roomy) {
    await capped(r, ROOMY);
  }
  const opened = await r.in((instance) => instance.sandbox.open({ holder: "sandbox-1" }));
  const egress = hashOf("egress", opened.egress_token);
  const sync = hashOf("sync", opened.sync_token);
  if (apply) {
    await r.in((instance) => instance.sandbox.networkApplied({
      lease_id: opened.lease.lease_id, epoch: opened.lease.epoch,
      generation: opened.network.generation }));
  }
  return {
    opened, lease_id: opened.lease.lease_id, epoch: opened.lease.epoch, egress, sync,
    as: (at = opened.network.generation, token = egress) =>
      r.bind({ sandbox: { kind: "egress", token, generation: at } }),
    synced: (token = sync) => r.bind({ sandbox: { kind: "sync", token } }),
  };
}

function request(connection: string, extra: Record<string, unknown> = {}) {
  return { connection, method: "POST", path: "/v1/messages", bytes_hint: 120, ...extra };
}

async function admitted(r: Gated, lease: Leased, connection: string,
                        extra: Record<string, unknown> = {}): Promise<EgressOpened> {
  const opened = await r.stub.egressOpen(lease.as(), request(connection, extra));
  if (!opened.ok) {
    throw new Error(`refused ${opened.code}`);
  }
  return opened;
}

async function refusedWith(r: Gated, binding: Binding, connection: string,
                           extra: Record<string, unknown> = {}): Promise<string> {
  const opened = await r.stub.egressOpen(binding, request(connection, extra));
  if (opened.ok) {
    throw new Error("expected a refusal");
  }
  const [receipt] = await r.sql("SELECT state, code FROM egress_reservations WHERE id = ?",
                                opened.receipt);
  expect(receipt).toEqual({ state: "refused", code: opened.code });
  return opened.code;
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

async function rows(r: Gated) {
  return r.sql("SELECT state, code, ended, outcome, status, bytes, bytes_hint " +
                 "FROM egress_reservations ORDER BY opened_at, rowid");
}

/** Each row's charge: what it held, what it used, and a refusal's binding cap. */
async function charges(r: Gated) {
  return r.sql("SELECT state, source, charge, rule, max_output_tokens, est_tokens, est_cost, " +
                 "tokens, cost FROM egress_reservations ORDER BY opened_at, rowid");
}

/** The run counters, by lease and epoch. */
async function runs(r: Gated) {
  return r.sql("SELECT lease_id, epoch, requests, tokens, cost FROM egress_runs " +
                 "ORDER BY lease_id");
}

/** What the source's day and month hold. */
async function sourceSpend(r: Gated, source = "anthropic") {
  return r.sql("SELECT substr(period, 1, 1) AS p, cost FROM model_spend WHERE source = ? " +
                 "ORDER BY period", source);
}

const DONE = { state: "succeeded", tokens: 1500, cost: 0.0042 } as const;
const settle = (reservation: string, extra: Record<string, unknown> = {}) =>
  ({ reservation, outcome: "complete", status: 200, bytes: 42, spend: DONE, ...extra });

/** The owner's model policy set, in its session, and its spend table's `ask_above`. */
async function capped(r: Gated, models: Record<string, unknown>,
                      askAbove: number | null = null): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind());
  await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
    ...policy, models, spend: { ...policy.spend, ask_above: askAbove } } });
}

/** The caps set once the lease is held, its network applied under the new generation. */
async function cappedLeased(r: Gated, models: Record<string, unknown>,
                            askAbove: number | null = null): Promise<Leased> {
  const lease = await leased(r, false);
  await capped(r, models, askAbove);
  await r.in((instance) => instance.sandbox.networkApplied({ ...held(lease), generation: 1 }));
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
    const [charge] = await charges(r);
    expect(charge).toMatchObject({ source: "anthropic", charge: "held", cost: null,
                                   max_output_tokens: opened.metering!.max_output_tokens,
                                   est_tokens: 120 + opened.metering!.max_output_tokens });
    const estimate = charge!["est_cost"] as number;
    expect(estimate).toBeGreaterThan(0);
    // Held, it counts at its estimate, on the run's and the source's counters.
    expect(await runs(r)).toEqual([{ lease_id: lease.lease_id, epoch: lease.epoch, requests: 1,
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
    expect(await runs(r)).toEqual([{ lease_id: lease.lease_id, epoch: lease.epoch, requests: 1,
                                     tokens: 1500, cost: 4200 }]);
    expect(await sourceSpend(r)).toEqual([{ p: "d", cost: 4200 }, { p: "m", cost: 4200 }]);
    // No pi record holds it: the loop's turn counters and reservations are untouched.
    expect(await r.sql("SELECT COUNT(*) AS n FROM model_turns")).toEqual([{ n: 0 }]);
    expect(await r.sql("SELECT COUNT(*) AS n FROM model_reservations")).toEqual([{ n: 0 }]);
    const { receipts } = await r.stub.egressReceipts(r.bind());
    expect(receipts).toMatchObject([{ reservation: opened.reservation, connection,
                                      method: "POST", path: "/v1/messages", state: "settled",
                                      charge: "succeeded", cost: 4200 }]);
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
    const before = await leased(r, false);
    // The lease: another token, then the transform's generation, then the network's.
    expect(await refusedWith(r, before.as(1, "f".repeat(64)), connection))
      .toBe("egress_lease_closed");
    expect(await refusedWith(r, before.as(2), connection)).toBe("egress_policy_stale");
    expect(await refusedWith(r, before.as(), connection)).toBe("egress_network_stale");
    await r.sql("UPDATE file_space SET lease_expires = 1");
    expect(await refusedWith(r, before.as(), connection)).toBe("egress_lease_closed");
    const lease = await leased(r);
    expect(await refusedWith(r, before.as(), connection)).toBe("egress_lease_closed");
    // The connection: another owner's, none, one with no scope stored, an unusable blob.
    expect(await refusedWith(r, lease.as(), theirs)).toBe("egress_connection_refused");
    expect(await refusedWith(r, lease.as(), `c${"0".repeat(24)}`))
      .toBe("egress_connection_refused");
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

  it("is held to the rate: open at once, and opened a minute, to its window's edge",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const open: EgressOpened[] = [];
    for (let i = 0; i < EGRESS_LIMITS.open; i++) {
      open.push(await admitted(r, lease, connection));
    }
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_rate");
    expect(await r.stub.egressSettle(lease.as(), settle(open[0]!.reservation)))
      .toEqual({ settled: true });
    await admitted(r, lease, connection);
    await r.sql("UPDATE egress_reservations SET state = 'settled', outcome = 'complete', " +
                  "settled_at = opened_at WHERE state = 'open'");
    // The minute's window: a row 59 s old counts, one 61 s old does not; refused ones never.
    await r.sql("DELETE FROM egress_reservations");
    const now = Date.now() / 1000;
    const fill = (count: number, at: number, state: string) => r.sql(
      "INSERT INTO egress_reservations (id, lease_id, token, connection_id, method, path, " +
        "generation, opened_at, state, code, outcome, settled_at) SELECT " +
        "lower(hex(randomblob(16))), ?, ?, ?, 'GET', '/x', 1, ?, ?, ?, ?, ? FROM " +
        "(WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?) " +
        "SELECT i FROM n)", lease.lease_id, lease.egress, connection, at, state,
      state === "refused" ? "egress_rate" : null, state === "settled" ? "complete" : null, at,
      count);
    await fill(EGRESS_LIMITS.perMinute - 1, now - 59, "settled");
    await fill(50, now - 61, "settled");
    await fill(30, now - 30, "refused");
    await admitted(r, lease, connection);
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_rate");
  }, 120_000);

  it("receipts at most the minute's refusals, apart from the admitted calls' receipts",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    await r.stub.egressSettle(lease.as(), settle(opened.reservation));
    for (let i = 0; i < EGRESS_LIMITS.refusalsPerMinute + 20; i++) {
      await r.stub.egressOpen(lease.as(), request(connection, { path: `/v1/files/${i}` }));
    }
    const late = await r.stub.egressOpen(lease.as(), request(connection, { path: "/v1/x" }));
    expect(late).toEqual({ ok: false, code: "egress_endpoint_refused", receipt: null });
    expect(await r.sql("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'refused'"))
      .toEqual([{ n: EGRESS_LIMITS.refusalsPerMinute }]);
    expect(await r.sql("SELECT state FROM egress_reservations WHERE id = ?", opened.reservation))
      .toEqual([{ state: "settled" }]);
  }, 120_000);

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
    expect((await failure(() => r.in((instance) => instance.sandbox.open({ holder: "x" }))))
      .message).toMatch(/^owner_suspended/);
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
                        () => r.stub.agentStatus(lease.as(), { operation: "x" } as never)]) {
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
    const stub = env.GATED.getByName(stranger);
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
    await r.in((instance) => instance.sandbox.close(held(lease)));
    expect((await failure(() => r.stub.fileChanges(lease.synced(), { after: 0, limit: 10 })))
      .message).toBe(SYNC_FENCED);
  });
});

describe("the spend caps", () => {
  it.each([
    ["the run's requests", { turn_requests: 1 }, null, "run_requests"],
    ["the run's cost", { turn_cost: 1 }, null, "run_cost"],
    ["the run's tokens", { turn_tokens: 100 }, null, "run_tokens"],
    ["the owner's day", { daily_cost: 1 }, null, "daily_cost"],
    ["the owner's month", { monthly_cost: 1 }, null, "monthly_cost"],
    ["the source's day", { sources: { anthropic: { daily_cost: 1 } } }, null,
     "source_daily_cost"],
    ["the source's month", { sources: { anthropic: { monthly_cost: 1 } } }, null,
     "source_monthly_cost"],
    ["ask_above", {}, 1, "ask_above"],
  ])("refuse a call past %s before any forward, its receipt kept",
     async (_what, models, askAbove, rule) => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await cappedLeased(r, { ...ROOMY, ...models }, askAbove);
    if (rule === "run_requests") {
      await admitted(r, lease, connection);
    }
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    expect((await charges(r)).at(-1)).toMatchObject({ state: "refused", charge: null, rule });
  });

  it("shrink a call's output to what `ask_above` leaves it, so the charge stays under it",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const roomy = await cappedLeased(r, ROOMY);
    const free = await admitted(r, roomy, connection);
    const most = free.metering!.max_output_tokens;
    const [{ est_cost: whole }] = (await charges(r)) as [{ est_cost: number }];
    await r.stub.egressSettle(roomy.as(), settle(free.reservation));
    const ceiling = Math.floor(whole / 2);
    await capped(r, ROOMY, ceiling);
    await r.in((instance) => instance.sandbox.networkApplied({ ...held(roomy), generation: 1 }));
    const asked = await admitted(r, roomy, connection);
    expect(asked.metering!.max_output_tokens).toBeLessThan(most);
    expect((await charges(r)).at(-1)!["est_cost"]).toBeLessThanOrEqual(ceiling);
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
    expect([first.ok, second.ok]).toContain(true);
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

  it("share the source's day and month with the loop's calls, in both directions",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await cappedLeased(r, { ...ROOMY,
                                          sources: { anthropic: { daily_cost: 1_000_000 } } });
    // A loop call on the same source, settled, as `usage.ts` records one.
    const day = new Date().toISOString().slice(0, 10);
    await r.sql("INSERT INTO model_turns (turn, requests, tokens, cost, last_at, thread, owners) " +
                  "VALUES ('s:t', 0, 0, 0, ?, 't', 1)", Date.now() / 1000);
    await r.sql("INSERT INTO model_reservations (id, turn, connection_id, source, model, state, " +
                  "max_output_tokens, est_tokens, est_cost, tokens, cost, policy_generation, " +
                  "day, month, created_at, settled_at) VALUES ('r' || lower(hex(randomblob(16))), " +
                  "'s:t', ?, 'anthropic', 'claude', 'reserved', 1000, 1000, 999000, NULL, NULL, " +
                  "2, ?, ?, ?, NULL)", connection, day, day.slice(0, 7), Date.now() / 1000);
    await r.sql("UPDATE model_reservations SET state = 'dispatched' WHERE state = 'reserved'");
    await r.sql("UPDATE model_reservations SET state = 'succeeded', tokens = 900, " +
                  "cost = 999000, settled_at = ? WHERE state = 'dispatched'", Date.now() / 1000);
    // The loop's spend leaves the source's day no room for the sandbox's call.
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    expect((await charges(r)).at(-1)).toMatchObject({ rule: "source_daily_cost" });
    // And the sandbox's spend is on the counters the loop's reservation reads.
    await capped(r, ROOMY);
    await r.in((instance) => instance.sandbox.networkApplied({ ...held(lease), generation: 1 }));
    const opened = await admitted(r, lease, connection);
    await r.stub.egressSettle(lease.as(), settle(opened.reservation));
    expect(await sourceSpend(r)).toEqual([{ p: "d", cost: 999_000 + 4200 },
                                          { p: "m", cost: 999_000 + 4200 }]);
  });

  it("count a run as its lease and epoch: a new lease's run starts afresh, the old one's " +
     "counters gone once no call of it is open", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await cappedLeased(r, { ...ROOMY, turn_requests: 2 });
    const open = await admitted(r, lease, connection);
    await admitted(r, lease, connection);
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    await r.stub.egressSettle(lease.as(), settle(open.reservation));
    // Settled, its request still counts: the run's cap is its calls, not those open.
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_spend_refused");
    await r.in((instance) => instance.sandbox.close(held(lease)));
    const next = await leased(r, true, false);
    await admitted(r, next, connection);
    const counted = await runs(r);
    expect(counted).toContainEqual(expect.objectContaining({ lease_id: next.lease_id,
                                                              epoch: next.epoch, requests: 1 }));
    // The old run's second call is still open (its stream ended with the lease), so its
    // counters stay until that call settles; then the next open prunes them.
    const [old] = (await r.sql("SELECT id FROM egress_reservations WHERE state = 'open' " +
                                 "AND lease_id = ?", lease.lease_id)) as [{ id: string }];
    await r.stub.egressSettle(lease.as(), settle(old.id));
    await admitted(r, next, connection);
    expect((await runs(r)).map((row) => row["lease_id"])).toEqual([next.lease_id]);
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
    // A write that leaves the egress table as it was leaves the network current.
    expect(await r.in((instance) => instance.sandbox.stale())).toBe(false);
    await admitted(r, lease, connection);
    // A policy change outside the document (an agent's wake limits: \`Policy.changed\`), neither.
    await r.sql("UPDATE owner_policy SET generation = generation + 1");
    expect(await r.in((instance) => instance.sandbox.stale())).toBe(false);
    await admitted(r, lease, connection);
    const latest = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: latest.generation, policy: {
      ...latest.policy, egress: { mode: "allowlist", destinations: [] } } });
    expect(await r.in((instance) => instance.sandbox.stale())).toBe(true);
    expect(await refusedWith(r, lease.as(1), connection)).toBe("egress_policy_stale");
    expect(await refusedWith(r, lease.as(2), connection)).toBe("egress_network_stale");
    expect((await failure(() => r.in((instance) => instance.sandbox.networkApplied({
      ...held(lease), generation: 1 })))).message).toMatch(/^egress_policy_stale/);
    expect((await failure(() => r.in((instance) => instance.sandbox.networkApplied({
      lease_id: lease.lease_id, epoch: lease.epoch + 1, generation: 2 })))).message)
      .toBe(SYNC_FENCED);
    const network = await r.in((instance) => instance.sandbox.network(lease.opened.egress_token));
    expect(network).toMatchObject({ generation: 2, mode: "allowlist", boundary: false });
    await r.in((instance) => instance.sandbox.networkApplied({ ...held(lease), generation: 2 }));
    expect(await r.stub.egressOpen(lease.as(2), request(connection)))
      .toMatchObject({ ok: true, generation: 2 });
  });

  it("is the lease's close or its successor, or the connection's revoke, each its own " +
     "streams only", async () => {
    const r = await gated();
    const first = await r.connect("sk-good");
    const second = await r.connect("sk-good-2", ANTHROPIC, "Second");
    const lease = await leased(r);
    const a = await admitted(r, lease, first);
    const b = await admitted(r, lease, second);
    await r.stub.connectionRevoke(r.session(), { connection: first });
    expect(await ended(a)).toBe(true);
    expect(await ended(b, 300)).toBe(false);
    expect(await r.in((instance) => instance.sandbox.close(held(lease))))
      .toEqual({ released: true });
    expect(await ended(b)).toBe(true);
    expect(await rows(r)).toMatchObject([{ ended: "connection" }, { ended: "lease" }]);
    const next = await leased(r);
    const c = await admitted(r, next, second);
    await r.sql("UPDATE file_space SET lease_expires = 1");
    await leased(r, true, false);
    expect(await ended(c)).toBe(true);
    expect((await rows(r)).at(-1)).toMatchObject({ ended: "lease" });
  });
});

describe("the owner's erasure", () => {
  it("ends every stream, and then every door refuses", async () => {
    // The faux-model class, whose test hook reaches the erasure's internal path.
    const owner = `owner-${crypto.randomUUID()}`;
    const stub: DurableObjectStub<FauxOwnerDO> = env.AGENT.getByName(owner);
    const bind = (extra: Partial<Binding> = {}): Binding =>
      ({ owner, caller: owner, origin: null, ...extra });
    const { session: cookie } = await stub.sessionOpen(bind({ authenticated_at: signedIn() }));
    const session = bind({ session: cookie });
    const prepared = await stub.connectionPrepare(session, {
      source: "anthropic", kind: "api_key", display_name: "Key", provider: "anthropic",
      revocable: false });
    const sealed = await env.GATEWAY.seal(owner, prepared.connection, 1, "sk-good", ANTHROPIC);
    await stub.connectionCommit(session, {
      connection: prepared.connection, version: 1, blob: sealed.blob, kid: sealed.kid,
      models: await catalog(ANTHROPIC), egress: await scopeOf(ANTHROPIC) });
    const opened = await runInDurableObject(stub, async (instance: FauxOwnerDO) => {
      const lease = await instance.sandbox.open({ holder: "sandbox-1" });
      await instance.sandbox.networkApplied({ lease_id: lease.lease.lease_id,
                                              epoch: lease.lease.epoch, generation: 1 });
      return lease;
    });
    const sandbox = bind({ sandbox: { kind: "egress", token: hashOf("egress", opened.egress_token),
                                      generation: 1 } });
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
    const opened = await admitted(r, lease, prepared.connection, { path: "/v1/responses" });
    const resealed = await env.GATEWAY.reseal(r.name, prepared.connection, 1, opened.blob);
    expect(await r.stub.egressSettle(lease.as(), { ...settle(opened.reservation), resealed }))
      .toEqual({ settled: true });
    expect(await r.sql("SELECT version, kid FROM connection_secrets WHERE connection_id = ?",
                       prepared.connection)).toEqual([{ version: 2, kid: resealed.kid }]);
  });
});

describe("the sandbox's lease", () => {
  it("is the file sync lease, with its two MAC'd tokens' hashes, which a close clears",
     async () => {
    const r = await gated();
    const lease = await leased(r, false);
    const tokens = { egress: lease.opened.egress_token, sync: lease.opened.sync_token };
    for (const [kind, token] of Object.entries(tokens) as ["egress" | "sync", string][]) {
      expect(token).toMatch(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}$/);
      expect(verifyToken(env.ROUTING_MAC_KEY, kind, token)!.owner).toBe(r.name);
      // Each is its own kind's alone.
      expect(verifyToken(env.ROUTING_MAC_KEY, kind === "egress" ? "sync" : "egress", token))
        .toBeNull();
    }
    expect(lease.egress).not.toBe(lease.sync);
    expect(await r.sql("SELECT lease_id, epoch, egress_token, sync_token, " +
                         "network_generation FROM file_space"))
      .toEqual([{ lease_id: lease.lease_id, epoch: lease.epoch, egress_token: lease.egress,
                  sync_token: lease.sync, network_generation: null }]);
    expect(JSON.stringify(await r.sql("SELECT * FROM file_space")))
      .not.toContain(lease.opened.egress_token.split(".")[1]);
    const renewed = await r.in((instance) => instance.sandbox.renew(held(lease)));
    expect(renewed).toMatchObject({ lease_id: lease.lease_id, epoch: lease.epoch });
    await r.in((instance) => instance.sandbox.close(held(lease)));
    expect(await r.sql("SELECT lease_id, egress_token, sync_token, " +
                         "network_generation FROM file_space"))
      .toEqual([{ lease_id: null, egress_token: null, sync_token: null,
                  network_generation: null }]);
    const applied = await leased(r);
    await r.sql("UPDATE file_space SET lease_expires = 1");
    const next = await leased(r, false);
    expect(await r.sql("SELECT egress_token, network_generation FROM file_space"))
      .toEqual([{ egress_token: next.egress, network_generation: null }]);
    expect(applied.egress).not.toBe(next.egress);
  });

  it("receipts an ask-mode network's refusal of what it does not list", async () => {
    const r = await gated();
    const lease = await leased(r, false);
    const { generation, policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: generation, policy: {
      ...policy, egress: { mode: "ask", destinations: [{ host: "pypi.org", port: 443 }] } } });
    await r.in((instance) => instance.sandbox.networkApplied({ ...held(lease), generation: 2 }));
    const { receipts } = await r.stub.policyReceipts(r.bind());
    expect(receipts.filter((receipt) => receipt.rule === "egress.ask"))
      .toMatchObject([{ kind: "refusal", decision: "deny", outcome: "refused" }]);
  });

  it("hands the sandbox slice its policy and its clients' environment", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const config = await r.in((instance) => instance.sandbox.config());
    expect(config.generation).toBe(1);
    expect(config.egress).toEqual({ mode: "open", destinations: [] });
    expect(config.env["ANTHROPIC_BASE_URL"]).toBe(`https://egress.tinyassets.io/c/${connection}/`);
    expect(config.env["ANTHROPIC_API_KEY"]).toMatch(new RegExp(`^ta_ph_${connection}_[0-9a-f]{32}$`));
    expect(JSON.stringify(config)).not.toContain("sk-good");
  });
});
