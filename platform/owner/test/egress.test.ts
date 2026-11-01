/**
 * Sandbox egress at the owner's object (tasks 4 G4): the sandbox's one lease and its
 * token's hash, each request admitted at open (`egressOpen`) by every check in order, a
 * refusal kept as its receipt, the streams a change ends, and each reservation settled
 * once (`egressSettle`). The egress door itself is the doors' tests'.
 */
import { createHash } from "node:crypto";

import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { PLATFORM_CALLER } from "../src/admission";
import { EGRESS_LIMITS, type EgressOpened, LOST_AFTER_S } from "../src/egress";
import type { Binding } from "../src/owner-do";
import { SANDBOX_DOORS, SANDBOX_ONLY } from "../src/owner-do";
import { ANTHROPIC, type Gated, gated, OPENAI, sealedAs } from "./gateway-support";
import { failure } from "./support";

const FENCED = "the sync lease was lost; this sync is fenced";

function hashOf(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

interface Leased {
  readonly lease_id: string;
  readonly epoch: number;
  readonly token: string;
  /** The egress door's binding for this lease, at `generation`. */
  readonly as: (generation?: number, extra?: Partial<Binding["sandbox"] & object>) => Binding;
}

/** The sandbox's lease taken and, unless `apply` is false, its network applied. */
async function leased(r: Gated, apply = true): Promise<Leased> {
  const token = hashOf(crypto.randomUUID());
  const lease = await r.stub.sandboxLeaseOpen(r.bind(), { holder: "sandbox-1", token });
  const { generation } = await r.stub.policyRead(r.bind());
  if (apply) {
    await r.stub.sandboxNetworkApplied(r.bind(), { lease_id: lease.lease_id, epoch: lease.epoch,
                                                   generation });
  }
  return {
    lease_id: lease.lease_id, epoch: lease.epoch, token,
    as: (at = generation, extra = {}) => r.bind({ sandbox: {
      lease: lease.lease_id, token, generation: at, ...extra } }),
  };
}

function request(connection: string, extra: Record<string, unknown> = {}) {
  return { connection, method: "POST", path: "/v1/messages", bytes_hint: 12, ...extra };
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
  return r.sql("SELECT state, code, ended, outcome, status, bytes FROM egress_reservations " +
                 "ORDER BY opened_at, rowid");
}

const settle = (reservation: string, extra: Record<string, unknown> = {}) =>
  ({ reservation, outcome: "complete", status: 200, bytes: 42, ...extra });

describe("an egress request", () => {
  it("is admitted under a live lease, with its connection's blob and an end signal, and " +
     "settled once", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    expect(opened).toMatchObject({ ok: true, connection, version: 1, generation: 1 });
    expect(opened.blob.startsWith("v1.")).toBe(true);
    expect(opened.reservation).toMatch(/^[0-9a-f]{32}$/);
    expect(await rows(r)).toEqual([{ state: "open", code: null, ended: null, outcome: null,
                                     status: null, bytes: null }]);
    // Another lease's binding cannot settle it; its own settles it once.
    expect(await r.stub.egressSettle(lease.as(1, { lease: "0".repeat(32) }),
                                     settle(opened.reservation))).toEqual({ settled: false });
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation)))
      .toEqual({ settled: true });
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation, { outcome: "failed" })))
      .toEqual({ settled: false });
    expect(await rows(r)).toEqual([{ state: "settled", code: null, ended: null,
                                     outcome: "complete", status: 200, bytes: 42 }]);
    const { receipts } = await r.stub.egressReceipts(r.bind());
    expect(receipts).toMatchObject([{ reservation: opened.reservation, connection,
                                      method: "POST", path: "/v1/messages", state: "settled" }]);
  });

  it("is refused at each admit check, in order, each with its code and a receipt", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const other = await gated();
    const theirs = await other.connect("sk-good");
    const before = await leased(r, false);
    // The lease: none, another token, another lease id, or one that lapsed.
    expect(await refusedWith(r, before.as(1, { token: "f".repeat(64) }), connection))
      .toBe("egress_lease_closed");
    expect(await refusedWith(r, before.as(1, { lease: "0".repeat(32) }), connection))
      .toBe("egress_lease_closed");
    // The policy generation the network's transform carries, then the network's own.
    expect(await refusedWith(r, before.as(2), connection)).toBe("egress_policy_stale");
    expect(await refusedWith(r, before.as(), connection)).toBe("egress_network_stale");
    await r.sql("UPDATE file_space SET lease_expires = 1");
    const lease = await leased(r);
    expect(await refusedWith(r, before.as(), connection)).toBe("egress_lease_closed");
    // The connection: another owner's, none, or one revoked.
    expect(await refusedWith(r, lease.as(), theirs)).toBe("egress_connection_refused");
    expect(await refusedWith(r, lease.as(), `c${"0".repeat(24)}`))
      .toBe("egress_connection_refused");
    // The endpoint: a path, method or prefix not sealed, or a path naming another host.
    for (const extra of [{ path: "/v1/files" }, { method: "DELETE" }, { path: "/v1/messages-x" },
                         { path: "//api.openai.com/v1/messages" },
                         { path: "/v1/%2e%2e/admin" }]) {
      expect(await refusedWith(r, lease.as(), connection, extra)).toBe("egress_endpoint_refused");
    }
    await r.sql("UPDATE file_space SET lease_expires = 1");
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_lease_closed");
    const revoked = await gated();
    const gone = await revoked.connect("sk-good");
    const live = await leased(revoked);
    await revoked.stub.connectionRevoke(revoked.session(), { connection: gone });
    expect(await refusedWith(revoked, live.as(), gone)).toBe("egress_connection_refused");
    expect((await r.sql("SELECT COUNT(*) AS n FROM egress_reservations WHERE state = 'open'"))
      [0]).toEqual({ n: 0 });
  });

  it("is held to the rate: open at once, and opened a minute", async () => {
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
    // Settled ones count in the minute's opens, refused ones never.
    await r.sql("UPDATE egress_reservations SET opened_at = opened_at - 61");
    await r.sql("INSERT INTO egress_reservations (id, lease_id, connection_id, version, method, " +
                  "path, generation, opened_at, state, outcome, settled_at) SELECT " +
                  "lower(hex(randomblob(16))), ?, ?, 1, 'POST', '/x', 1, ?, 'settled', " +
                  "'complete', ? FROM (WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 " +
                  `FROM n WHERE i < ${EGRESS_LIMITS.perMinute - 1}) SELECT i FROM n)`,
                lease.lease_id, connection, Date.now() / 1000, Date.now() / 1000);
    await r.sql("UPDATE egress_reservations SET state = 'settled', outcome = 'complete', " +
                  "settled_at = opened_at WHERE state = 'open'");
    await admitted(r, lease, connection);
    expect(await refusedWith(r, lease.as(), connection)).toBe("egress_rate");
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
    expect(await r.stub.egressSettle(lease.as(), settle(opened.reservation, { outcome: "ended" })))
      .toEqual({ settled: true });
    expect(await rows(r)).toMatchObject([{ ended: "admission", outcome: "ended" }]);
    await r.stub.admissionSet(platform, { reason: "abuse", suspended: true });
    expect((await failure(() => r.stub.egressSettle(lease.as(), settle(opened.reservation))))
      .message).toMatch(/^owner_suspended/);
  });

  it("takes the sandbox binding, which no other door takes", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    for (const call of [() => r.stub.policyRead(lease.as()),
                        () => r.stub.fileStat(lease.as(), { path: "/workspace/a" }),
                        () => r.stub.sandboxConfig(lease.as()),
                        () => r.stub.sandboxLeaseRenew(lease.as(), held(lease)),
                        () => r.stub.agentStatus(lease.as(), { operation: "x" } as never)]) {
      expect((await failure(call)).message).toBe(SANDBOX_DOORS);
    }
    expect((await failure(() => r.stub.egressOpen(r.bind(), request(connection)))).message)
      .toBe(SANDBOX_ONLY);
    expect((await failure(() => r.stub.egressSettle(r.bind(), settle("0".repeat(32)))))
      .message).toBe(SANDBOX_ONLY);
    expect((await failure(() => r.stub.egressOpen(lease.as(1, { token: "short" }),
                                                  request(connection)))).message)
      .toBe("invalid sandbox binding");
    for (const bad of [{ method: "OPTIONS" }, { path: "v1/messages" }, { path: `/${"p".repeat(2048)}` },
                       { bytes_hint: -1 }, { connection: "c1" }, { extra: 1 }]) {
      expect((await failure(() => r.stub.egressOpen(lease.as(), request(connection, bad))))
        .message).toBe("invalid egress request");
    }
  });
});

describe("a change that ends egress", () => {
  it("is a policy write: its streams end, and the network is stale until applied again",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    const opened = await admitted(r, lease, connection);
    const { policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: 1, policy });
    expect(await ended(opened)).toBe(true);
    expect(await rows(r)).toMatchObject([{ state: "open", ended: "policy" }]);
    expect(await refusedWith(r, lease.as(1), connection)).toBe("egress_policy_stale");
    expect(await refusedWith(r, lease.as(2), connection)).toBe("egress_network_stale");
    // A network made under an older generation is not applied.
    expect((await failure(() => r.stub.sandboxNetworkApplied(r.bind(), {
      lease_id: lease.lease_id, epoch: lease.epoch, generation: 1 }))).message)
      .toMatch(/^egress_policy_stale/);
    expect((await failure(() => r.stub.sandboxNetworkApplied(r.bind(), {
      lease_id: lease.lease_id, epoch: lease.epoch + 1, generation: 2 }))).message).toBe(FENCED);
    await r.stub.sandboxNetworkApplied(r.bind(), { lease_id: lease.lease_id, epoch: lease.epoch,
                                                   generation: 2 });
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
    expect(await r.stub.sandboxLeaseClose(r.bind(), held(lease)))
      .toEqual({ released: true, token: lease.token });
    expect(await ended(b)).toBe(true);
    expect(await rows(r)).toMatchObject([{ ended: "connection" }, { ended: "lease" }]);
    // A lease that lapsed is replaced, and its streams end with it.
    const next = await leased(r);
    const c = await admitted(r, next, second);
    await r.sql("UPDATE file_space SET lease_expires = 1");
    await leased(r);
    expect(await ended(c)).toBe(true);
    expect((await rows(r)).at(-1)).toMatchObject({ ended: "lease" });
  });
});

describe("a reservation", () => {
  it("never settled is settled lost, past its door's deadline", async () => {
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
      connection: prepared.connection, version: 1, blob, kid: "k1", models: [],
      egress: { base_url: OPENAI.upstream.base_url, endpoints: OPENAI.endpoints } });
    const lease = await leased(r);
    const opened = await admitted(r, lease, prepared.connection, { path: "/v1/responses" });
    const resealed = await env.GATEWAY.reseal(r.name, prepared.connection, 1, opened.blob);
    expect(await r.stub.egressSettle(lease.as(), { ...settle(opened.reservation), resealed }))
      .toEqual({ settled: true });
    expect(await r.sql("SELECT version, kid FROM connection_secrets WHERE connection_id = ?",
                       prepared.connection)).toEqual([{ version: 2, kid: resealed.kid }]);
  });

  it("is kept as a receipt among the newest only", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const lease = await leased(r);
    await r.sql("INSERT INTO egress_reservations (id, lease_id, connection_id, version, method, " +
                  "path, generation, opened_at, state, code, settled_at) SELECT " +
                  "lower(hex(randomblob(16))), ?, ?, 1, 'POST', '/x', 1, 1, 'refused', " +
                  "'egress_rate', 1 FROM (WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT " +
                  `i + 1 FROM n WHERE i < ${EGRESS_LIMITS.receiptsKept}) SELECT i FROM n)`,
                lease.lease_id, connection);
    const opened = await admitted(r, lease, connection);
    expect(await r.sql("SELECT COUNT(*) AS n FROM egress_reservations WHERE state != 'open'"))
      .toEqual([{ n: EGRESS_LIMITS.receiptsKept }]);
    await r.stub.egressSettle(lease.as(), settle(opened.reservation));
    expect(await r.sql("SELECT COUNT(*) AS n FROM egress_reservations"))
      .toEqual([{ n: EGRESS_LIMITS.receiptsKept }]);
    expect((await r.stub.egressReceipts(r.bind())).receipts)
      .toHaveLength(EGRESS_LIMITS.receiptsListed);
  });
});

describe("the sandbox's lease and setup", () => {
  it("is the file sync lease, bound to its token's hash, which a close clears", async () => {
    const r = await gated();
    const lease = await leased(r, false);
    expect(await r.sql("SELECT lease_id, epoch, lease_token, network_generation FROM file_space"))
      .toEqual([{ lease_id: lease.lease_id, epoch: lease.epoch, lease_token: lease.token,
                  network_generation: null }]);
    const renewed = await r.stub.sandboxLeaseRenew(r.bind(), held(lease));
    expect(renewed).toMatchObject({ lease_id: lease.lease_id, epoch: lease.epoch,
                                    token: lease.token });
    await r.stub.sandboxLeaseClose(r.bind(), held(lease));
    expect(await r.sql("SELECT lease_id, lease_token, network_generation FROM file_space"))
      .toEqual([{ lease_id: null, lease_token: null, network_generation: null }]);
    expect((await failure(() => r.stub.sandboxLeaseOpen(r.bind(), {
      holder: "x", token: "not-a-hash" }))).message).toBe("a lease token's hash is 64 hex");
    // A lease that lapsed with its network applied leaves its successor's to be applied.
    const applied = await leased(r);
    await r.sql("UPDATE file_space SET lease_expires = 1");
    const next = await leased(r, false);
    expect(await r.sql("SELECT lease_token, network_generation FROM file_space"))
      .toEqual([{ lease_token: next.token, network_generation: null }]);
    expect(applied.token).not.toBe(next.token);
  });

  it("receipts an ask-mode network's refusal of what it does not list", async () => {
    const r = await gated();
    const lease = await leased(r, false);
    const { policy } = await r.stub.policyRead(r.bind());
    await r.stub.policyWrite(r.session(), { expected_generation: 1, policy: {
      ...policy, egress: { mode: "ask", destinations: [{ host: "pypi.org", port: 443 }] } } });
    await r.stub.sandboxNetworkApplied(r.bind(), { lease_id: lease.lease_id,
                                                   epoch: lease.epoch, generation: 2 });
    const { receipts } = await r.stub.policyReceipts(r.bind());
    expect(receipts.filter((receipt) => receipt.rule === "egress.ask"))
      .toMatchObject([{ kind: "refusal", decision: "deny", outcome: "refused", generation: 2 }]);
  });

  it("hands the sandbox slice its policy and its clients' environment", async () => {
    const r = await gated();
    const connection = await r.connect("sk-good");
    const config = await r.stub.sandboxConfig(r.bind());
    expect(config.generation).toBe(1);
    expect(config.egress).toEqual({ mode: "open", destinations: [] });
    expect(config.env["ANTHROPIC_BASE_URL"]).toBe(`https://egress.tinyassets.io/c/${connection}/`);
    expect(config.env["ANTHROPIC_API_KEY"]).toMatch(new RegExp(`^ta_ph_${connection}_[0-9a-f]{32}$`));
    expect(JSON.stringify(config)).not.toContain("sk-good");
  });
});
