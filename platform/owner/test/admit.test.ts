/**
 * The platform's explicit admit (slice L, `admission.ts`): a fresh object is pending and
 * refuses every door and lane until the platform admits it; the admit is the reserved
 * caller's alone, idempotent, and lifts no suspension; a pending owner is never suspended;
 * and the key store's and inbound bindings' rows follow their grants and routes.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { OWNER_UNADMITTED, PLATFORM_CALLER } from "../src/admission";
import { BREAKER_FAILURES, Inbound } from "../src/inbound";
import { profile, proves } from "../../contract/src/inbound-proof";
import { Keys, TOO_MANY_KEYS } from "../src/keys";
import { MAX_KEYS } from "../../contract/src/limits";
import { GRANT_LIMITS } from "../src/grants";
import type { Binding } from "../src/owner-do";
import { failure, inGate } from "./support";

function pending(): { stub: DurableObjectStub; owner: string } {
  const owner = `owner-${crypto.randomUUID()}`;
  // `get(idFromName())`, which `admitted.ts` leaves alone: the object as production makes it.
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
