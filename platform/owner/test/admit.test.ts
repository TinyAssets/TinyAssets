/**
 * The platform's explicit admit (slice L, `admission.ts`): a fresh object is pending and
 * refuses every door and lane until the platform admits it; the admit is the reserved
 * caller's alone, idempotent, and lifts no suspension; a pending owner is never suspended;
 * and the key store's and inbound bindings' rows follow their grants and routes.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { OWNER_UNADMITTED } from "../src/admission";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { BREAKER_FAILURES, Inbound, INSTALL_PROOF } from "../src/inbound";
import { profile, proves } from "../../contract/src/inbound-proof";
import { Keys, TOO_MANY_KEYS } from "../src/keys";
import { MAX_KEYS } from "../../contract/src/limits";
import { GRANT_LIMITS } from "../src/grants";
import { LEDGER_LIMITS } from "../src/ledger";
import { sqlIn } from "../../ci/workerd";
import { rig } from "./ledger-support";
import { failure, inGate, TIERS } from "./support";

function pending(): { stub: DurableObjectStub; owner: string } {
  const owner = `owner-${crypto.randomUUID()}`;
  // `get(idFromName())`, never admitted by the rig: the object as production makes it.
  return { stub: env.OWNER.get(env.OWNER.idFromName(owner)), owner };
}

const platform = (owner: string): Binding => ({ owner, caller: PLATFORM_CALLER, origin: null });
const own = (owner: string): Binding => ({ owner, caller: owner, origin: null });

describe("the platform's admit", () => {
  it("is the only way past a fresh object's pending admission", async () => {
    const { stub, owner } = pending();
    const o = stub as unknown as DurableObjectStub<import("../src/owner-do").OwnerDO>;
    expect((await failure(() => o.grantList(own(owner)))).message).toBe(OWNER_UNADMITTED);
    expect((await failure(() => o.collectionPull(own(owner), { collection: "board", after: 0,
                                                               limit: 1 }))).message)
      .toBe(OWNER_UNADMITTED);
    expect(await o.admissionSet(platform(owner), { admit: true }))
      .toEqual({ state: "admitted", reasons: [] });
    expect(await o.grantList(own(owner))).toEqual({ clients: [] });
  });

  it("is idempotent and lifts no suspension; a pending owner is never suspended", async () => {
    const { stub, owner } = pending();
    const o = stub as unknown as DurableObjectStub<import("../src/owner-do").OwnerDO>;
    expect((await failure(() => o.admissionSet(platform(owner),
                                               { reason: "billing", suspended: true }))).message)
      .toMatch(/never admitted/);
    await o.admissionSet(platform(owner), { admit: true });
    await o.admissionSet(platform(owner), { reason: "abuse", suspended: true });
    expect(await o.admissionSet(platform(owner), { admit: true }))
      .toEqual({ state: "suspended", reasons: ["abuse"] });
    for (const request of [{ admit: false }, { admit: true, reason: "abuse" }, {}]) {
      expect((await failure(() => o.admissionSet(platform(owner), request as never))).code)
        .toBe("invalid");
    }
  });

  it("is refused from anyone but the platform", async () => {
    const { stub, owner } = pending();
    const o = stub as unknown as DurableObjectStub<import("../src/owner-do").OwnerDO>;
    expect((await failure(() => o.admissionSet(own(owner), { admit: true }))).message)
      .toMatch(/only the platform/);
  });
});

describe("the platform's read of the owner's state", () => {
  /** Every row of every table the object holds, by table. */
  async function everyRow(stub: DurableObjectStub) {
    const tables = await sqlIn(stub, "SELECT name FROM sqlite_master WHERE type = 'table' AND " +
                                     "name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%' " +
                                     "ORDER BY name");
    return Object.fromEntries(await Promise.all(tables.map(async ({ name }) =>
      [name, await sqlIn(stub, `SELECT * FROM "${String(name)}"`)])));
  }

  it("answers its state, its names, codes and counts alone, and changes nothing", async () => {
    const { stub, owner } = pending();
    const o = stub as unknown as DurableObjectStub<import("../src/owner-do").OwnerDO>;
    await o.admissionSet(platform(owner), { admit: true });
    await o.admissionSet(platform(owner), { reason: "abuse", suspended: true });
    const rows = await everyRow(stub);
    const state = { state: "suspended", reasons: ["abuse"], tier: "free", restore: null,
                    runs: 0, stored_bytes: 0 };
    for (let i = 0; i < 2; i++) {
      expect(await o.ownerState(platform(owner))).toEqual(state);
    }
    expect(await everyRow(stub)).toEqual(rows);
  });
});

describe("the key store", () => {
  it("counts live keys strictly, at the owner-set clients' own bound", async () => {
    expect(MAX_KEYS).toBe(GRANT_LIMITS.maxOwnerClients);
    const stub = env.OWNER.getByName(`owner-${crypto.randomUUID()}`);
    await inGate(stub, (_instance, state) => {
      const keys = new Keys({ sql: state.storage.sql, transactionSync: (f: () => unknown) => f() } as never);
      for (let i = 0; i < MAX_KEYS; i++) {
        keys.add(i.toString(16).padStart(16, "0"), i.toString(16).padStart(64, "0"), "k", 1);
      }
      expect(() => keys.add("f".repeat(16), "f".repeat(64), "k", 1)).toThrow(TOO_MANY_KEYS);
      keys.revoked("0".repeat(64), 2);
      keys.add("f".repeat(16), "f".repeat(64), "k", 1);
      keys.used("f".repeat(16), 100);
      keys.used("f".repeat(16), 120);
      expect(keys.list().find((key) => key.key_id === "f".repeat(16))?.last_used_at).toBe(100);
      keys.used("f".repeat(16), 161);
      expect(keys.list().find((key) => key.key_id === "f".repeat(16))?.last_used_at).toBe(161);
      keys.forgotten("f".repeat(64));
      expect(keys.idOf("f".repeat(64))).toBeNull();
    });
  });
});

describe("an inbound binding", () => {
  it("is never claimed by an owner as a shared app's: no door makes one before its install " +
     "flow", async () => {
    const r = await rig();
    const route = `i:slack:workspace:${"b".repeat(64)}`;
    expect((await failure(() => r.stub.inboundCreate(r.session(null),
                                                     { kind: "shared", agent: "main", route })))
      .message).toBe(INSTALL_PROOF);
    expect(await r.stub.inboundList(r.bind(null))).toEqual({ bindings: [] });
  });

  it("trips its breaker at BREAKER_FAILURES in its window, and a change re-arms it", async () => {
    const stub = env.OWNER.getByName(`owner-${crypto.randomUUID()}`);
    await inGate(stub, (_instance, state) => {
      const inbound = new Inbound({ sql: state.storage.sql, transactionSync: (f: () => unknown) => f() } as never);
      const made = inbound.create({ kind: "shared", agent: "main",
                                    route: `i:slack:workspace:${"a".repeat(64)}`, profile: null,
                                    secret: null }, 1000);
      let row = inbound.current(made.binding_id, 1);
      const trips: boolean[] = [];
      for (let i = 0; i < BREAKER_FAILURES; i++) {
        trips.push(inbound.failed(row, 1000 + i));
        row = inbound.current(made.binding_id, 1);
      }
      expect(trips).toEqual([...Array(BREAKER_FAILURES - 1).fill(false), true]);
      expect(row.tripped).toBe(1);
      const set = inbound.set({ binding_id: made.binding_id, expected_revision: 1 }, 1100);
      expect(set).toMatchObject({ revision: 2, tripped: false });
      expect(() => inbound.current(made.binding_id, 1)).toThrow(/withdrawn/);
      // Failures outside the window never add up.
      row = inbound.current(made.binding_id, 2);
      for (let i = 0; i < BREAKER_FAILURES; i++) {
        inbound.failed(row, 2000 + i * 61);
        row = inbound.current(made.binding_id, 2);
      }
      expect(row.tripped).toBe(0);
    });
  });

  it("holds a proof profile whole, and refuses a replay past its skew", () => {
    const slack = profile({ method: "hmac-sha256", signature_header: "x-slack-signature",
                            encoding: "hex", prefix: "v0=", version: "v0",
                            timestamp_header: "x-slack-request-timestamp",
                            max_skew_seconds: 300 });
    expect(proves(slack, {}, new Uint8Array(0), "s", 0)).toBe(false);
    for (const bad of [{ method: "none" }, { method: "hmac-sha256", signature_header: "X-Bad",
                                            encoding: "hex", prefix: "" },
                       { method: "hmac-sha256", signature_header: "x-s", encoding: "hex",
                         prefix: "", timestamp_header: "x-t" },
                       { method: "hmac-sha256", signature_header: "x-s", encoding: "hex",
                         prefix: "", timestamp_header: "x-t", max_skew_seconds: 301 }]) {
      expect(() => profile(bad)).toThrow();
    }
  });
});

describe("an inbound event at the object's door, as the doors hand it", () => {
  /** A capability URL's binding, as the owner's app makes it (`hooks/create`). */
  async function capability(r: Awaited<ReturnType<typeof rig>>): Promise<string> {
    const made = await r.stub.inboundCreate(r.session(null), {
      kind: "capability", agent: "main", route: `h:${crypto.randomUUID().replace(/-/g, "")
        .repeat(2)}` });
    return made.binding_id;
  }

  /**
   * A shared app's binding, its install written as its install flow will write it: no door
   * makes one before that flow's proof (`INSTALL_PROOF`).
   */
  async function shared(r: Awaited<ReturnType<typeof rig>>): Promise<string> {
    let id = "";
    await inGate(r.stub, (_instance, state) => {
      const inbound = new Inbound({ sql: state.storage.sql,
                                    transactionSync: (f: () => unknown) => f() } as never);
      id = inbound.create({ kind: "shared", agent: "main",
                            route: `i:slack:workspace:${"d".repeat(64)}`, profile: null,
                            secret: null }, Math.floor(Date.now() / 1000)).binding_id;
    });
    return id;
  }

  /** Event `id` with `body` accepted at the object's door for `binding`, at its revision 1. */
  function accept(r: Awaited<ReturnType<typeof rig>>, binding: string, id: string,
                  body = "{\"x\":1}", at = Math.floor(Date.now() / 1000)) {
    return r.stub.inboundAccept({ owner: r.owner, caller: r.owner, origin: null, tiers: TIERS,
                                  inbound: { binding, revision: 1 } },
                                { event_id: id, event_at: at, body: new TextEncoder().encode(body),
                                  headers: {} });
  }

  const inboundMail = (r: Awaited<ReturnType<typeof rig>>) =>
    r.sql("SELECT message_id FROM ledger_messages WHERE client_message_id LIKE 'inb_%'");

  it("mails the binding's agent the event's bytes verbatim, once: a replay deduped, the same " +
     "id with other bytes a conflict", async () => {
    const r = await rig();
    const binding = await capability(r);
    const bytes = "{\"raw\": \"é exact bytes\"}";
    const at = Math.floor(Date.now() / 1000);
    const first = await accept(r, binding, "evt-1", bytes, at);
    expect(first).toMatchObject({ state: "admitted", deduped: false });
    const { receipt } = first as { receipt: string };
    const [stored] = await r.sql("SELECT body FROM message_bodies WHERE message_id = ?", receipt);
    const envelope = JSON.parse(String(stored!["body"])) as { inbound: { raw_base64: string } };
    expect(Buffer.from(envelope.inbound.raw_base64, "base64").toString("utf8")).toBe(bytes);
    expect(await accept(r, binding, "evt-1", bytes, at))
      .toEqual({ state: "admitted", receipt, deduped: true });
    expect(await inboundMail(r)).toHaveLength(1);
    expect(await accept(r, binding, "evt-1", "{\"other\":true}", at))
      .toEqual({ state: "refused", error: "event_conflict" });
  });

  it("refuses an event at the hot ceiling mailbox_full, whether it clears by itself or is held " +
     "by a full storage, never counting it to the breaker (review E-2)", async () => {
    const r = await rig();
    const binding = await shared(r);
    for (const blocked of [0, ...Array<number>(BREAKER_FAILURES + 1).fill(1)]) {
      await r.sql("UPDATE ledger_usage SET messages = ?, archive_blocked = ?",
                  LEDGER_LIMITS.maxMessages, blocked);
      expect(await accept(r, binding, `evt-c${crypto.randomUUID()}`))
        .toEqual({ state: "refused", error: "mailbox_full" });
    }
    const { notices } = await r.stub.noticeList(r.bind(null));
    expect(notices.map((notice) => notice.type)).not.toContain("inbound");
  });

  it("trips a shared binding's breaker once the agent's mailbox refused it, telling the owner, " +
     "then no-ops until the owner's change re-arms it", async () => {
    const r = await rig();
    const binding = await shared(r);
    expect(await accept(r, binding, "evt-b0")).toMatchObject({ state: "admitted" });
    // Too much mail unread, owner-wide (slice STORE item 2: one ceiling, no mailbox's own).
    await r.sql("UPDATE ledger_usage SET unacked = 1000000");
    for (let i = 1; i <= BREAKER_FAILURES; i++) {
      expect(await accept(r, binding, `evt-b${i}`))
        .toEqual({ state: "refused", error: "mailbox_full" });
    }
    expect(await accept(r, binding, "evt-t"))
      .toEqual({ state: "no_op", error: "breaker_tripped" });
    const { notices } = await r.stub.noticeList(r.bind(null));
    expect(notices.map((notice) => notice.type)).toContain("inbound");
    await r.sql("UPDATE ledger_usage SET unacked = (SELECT COUNT(*) FROM ledger_messages " +
                "WHERE unacked > 0)");
    const set = await r.stub.inboundSet(r.session(null), { binding_id: binding,
                                                           expected_revision: 1, enabled: true });
    expect(set).toMatchObject({ revision: 2 });
    expect(await r.stub.inboundAccept({ owner: r.owner, caller: r.owner, origin: null,
                                        tiers: TIERS, inbound: { binding, revision: 2 } },
                                      { event_id: "evt-a", event_at: Math.floor(Date.now() / 1000),
                                        body: new TextEncoder().encode("{}"), headers: {} }))
      .toMatchObject({ state: "admitted" });
  });
});
