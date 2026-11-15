/**
 * The widenings' proofs (`door.ts`; S30, N10, P0-3): each widening takes the proof its
 * caller made as its first argument, refused by `tsc` (each `@ts-expect-error` below fails
 * `tsc` the day its call compiles) and at run time (`proven`) without it; and a row's call
 * carries only its own caller's proof, so a door of another caller cannot make the widening.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

import { clock } from "../src/clock";
import type { CallerOf, DoorName, HandlerOf, LiveSession, OwnerSelf } from "../src/door";
import type { GrantsDoorNeeds } from "../src/grants";
import type { OwnerDO, OwnerState } from "../src/owner-do";
import { Sessions } from "../src/sessions";
import { rig, type Rig } from "./ledger-support";
import { storesOn, type TestStores } from "./support";

/** A proof of `kind` made by hand, as no source may make one (policy-boundary's `forged`). */
const forged = (kind: string): never => Object.freeze({ proved: kind }) as never;
const CHANGE = { client: "1".repeat(64), source: "mcp", mode: "full", scopes: [],
                 expected_revision: 0, name: "" };

/** `act` on the owner's own stores (`storesOn`), in one unit of their gate. */
function inStores<R>(stub: Rig["stub"], act: (stores: TestStores) => R): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) => {
    const stores = storesOn(state);
    return stores.gate.write(() => act(stores));
  });
}

/** What `act` comes to: "made", or the message it was refused with. */
function outcome(act: () => unknown): string {
  try {
    act();
    return "made";
  } catch (error) {
    return (error as Error).message;
  }
}

/**
 * Each widening, called with no proof, another caller's, or its own kind made by hand (not
 * the runner's mint, `proof-mint.ts`, which no test may import), refused at run time.
 */
async function widenings() {
  const r = await rig();
  return inStores(r.stub, (stores) => {
    const { grants, keys, policy, registrations, sessions, admission } = stores;
    const now = clock();
    const refusals = (call: (proof: never) => unknown, kind: string) =>
      [undefined as never, forged(kind === "session" ? "owner" : "session"), forged(kind)]
        .map((proof) => outcome(() => call(proof)));
    const proofs = {
      // In the owner's session (`LiveSession`).
      "grants.set": refusals((proof) => grants.set(proof, CHANGE, now), "session"),
      "grants.forgetRevoked": refusals((proof) => grants.forgetRevoked(proof, ["1".repeat(64)],
                                                                       now), "session"),
      "keys.add": refusals((proof) => keys.add(proof, "0".repeat(16), "2".repeat(64), "k", now),
                           "session"),
      "policy.write": refusals((proof) => policy.write(proof, policy.read().generation,
                                                       policy.read().document, null, now),
                               "session"),
      "policy.changed": refusals((proof) => policy.changed(proof, policy.read().generation,
                                                           "wake_limits", {}, "rule", now),
                                 "session"),
      "policy.applyKeyRequest": refusals((proof) => policy.applyKeyRequest(proof, {}, [], now),
                                         "session"),
      "registrations.take": refusals((proof) => registrations.take(proof, "card", "d", now),
                                     "session"),
      // The owner itself (`OwnerSelf`).
      "sessions.open": refusals((proof) => sessions.open(proof, Math.floor(now), now), "owner"),
      "sessions.extend": refusals((proof) => sessions.extend(proof, r.cookie, now), "owner"),
      // The platform's reserved line (`PlatformLine`) and its import line (`ImportLine`).
      "admission.set": refusals((proof) => admission.set(proof, { admit: true }), "reserved"),
      "admission.setTier": refusals((proof) => admission.setTier(proof, { tier: "paid" }),
                                    "reserved"),
      "policy.imported": refusals((proof) => policy.imported(proof, policy.read().document, now),
                                  "import"),
    };
    return proofs;
  });
}

/**
 * Each widening called with no proof, or another caller's: `tsc` refuses every line (the
 * check is `tsc`'s; nothing runs this).
 */
function untyped({ grants, keys, policy, registrations, sessions, admission }: TestStores,
                 session: LiveSession, self: OwnerSelf, now: number): void {
  // @ts-expect-error a grant is set only in the owner's live session
  grants.set(undefined, CHANGE, now);
  // @ts-expect-error the owner itself is no live session
  grants.set(self, CHANGE, now);
  // @ts-expect-error a revoke is forgotten only in it
  grants.forgetRevoked("session", [], now);
  // @ts-expect-error a key is minted only in it
  keys.add(null, "0".repeat(16), "2".repeat(64), "k", now);
  // @ts-expect-error the owner's policy is written only in it
  policy.write("session", 1, {}, null, now);
  // @ts-expect-error a policy change outside the document, the same
  policy.changed(self, 1, "wake_limits", {}, "rule", now);
  // @ts-expect-error a held key request, the same
  policy.applyKeyRequest(undefined, {}, [], now);
  // @ts-expect-error a held registration, the same
  registrations.take(undefined, "card", "d", now);
  // @ts-expect-error a live session is no import line
  policy.imported(session, {}, now);
  // @ts-expect-error a session opens only for the owner itself
  sessions.open(session, now, now);
  // @ts-expect-error and slides only for it
  sessions.extend(undefined, "cookie", now);
  // @ts-expect-error the admission is the platform's alone
  admission.set(self, { admit: true });
  // @ts-expect-error and so is the tier
  admission.setTier(undefined, { tier: "paid" });
}

describe("a widening's proof", () => {
  it("is refused at run time when the call holds none, another caller's, or an unminted one",
     async () => {
    const without = (kind: string) => Array(3).fill(`a widening without its ${kind} proof`);
    expect(await widenings()).toEqual({
      "grants.set": without("session"), "grants.forgetRevoked": without("session"),
      "keys.add": without("session"), "policy.write": without("session"),
      "policy.changed": without("session"), "policy.applyKeyRequest": without("session"),
      "registrations.take": without("session"), "sessions.open": without("owner"),
      "sessions.extend": without("owner"), "admission.set": without("reserved"),
      "admission.setTier": without("reserved"), "policy.imported": without("import"),
    });
  });

  it("is none of what a handler can reach or build: the review's forgeries", async () => {
    const r = await rig();
    const tried = await inStores(r.stub, ({ grants, policy, sessions, admission }) => {
      const now = clock();
      // The session check a handler can reach answers the row's hash, not a proof (A1-A4).
      const live = sessions.live(r.cookie, now) as never;
      const generation = policy.read().generation;
      const { live: unbound } = sessions;
      return {
        cast: outcome(() => policy.changed(live, generation, "wake_limits", {}, "rule", now)),
        reflected: outcome(() => grants.set(
          Reflect.get(sessions, "live").call(sessions, r.cookie, now) as never, CHANGE, now)),
        bound: outcome(() => grants.set(unbound.bind(sessions)(r.cookie, now) as never, CHANGE,
                                        now)),
        // A kind built at run time: a split key's JSON, a computed key (A5-A7).
        parsed: outcome(() => admission.setTier(JSON.parse(`{"pro${""}ved":"reserved"}`),
                                                { tier: "paid" })),
        computed: outcome(() => admission.set({ ["pro" + "ved"]: "reserved" } as never,
                                              { admit: true })),
        coded: outcome(() => sessions.extend(
          { [String.fromCharCode(112, 114, 111, 118, 101, 100)]: "owner" } as never, r.cookie,
          now)),
      };
    });
    const session = "a widening without its session proof";
    expect(tried).toEqual({ cast: session, reflected: session, bound: session,
                            parsed: "a widening without its reserved proof",
                            computed: "a widening without its reserved proof",
                            coded: "a widening without its owner proof" });
  });

  it("hands its holder no other: a real one re-kinded, remade, copied or wrapped is none",
     async () => {
    const r = await rig();
    // The owner's own proof, as the runner hands the session's extend door (`call.self`).
    const extend = vi.spyOn(Sessions.prototype, "extend");
    await r.stub.sessionExtend(r.bind(null, { session: r.cookie }));
    const real = extend.mock.calls[0]![0] as object;
    extend.mockRestore();
    const kind = JSON.parse(`{"pro${""}ved":"reserved"}`) as object;
    const tried = await inStores(r.stub, ({ admission }) => {
      const as = (proof: unknown) => outcome(() => admission.setTier(proof as never,
                                                                     { tier: "paid" }));
      const maker = (real as { constructor?: unknown }).constructor;
      return {
        maker: maker === undefined && Object.getPrototypeOf(real) === null,
        rekinded: outcome(() => Object.assign(real, kind)),
        spread: as({ ...real, ...kind }),
        derived: as(Object.create(real, Object.getOwnPropertyDescriptors(kind))),
        wrapped: as(new Proxy(real, { get: (to, key) => Reflect.get(kind, key)
                                                         ?? Reflect.get(to, key) })),
      };
    });
    const none = "a widening without its reserved proof";
    expect(tried).toEqual({ maker: true, spread: none, derived: none, wrapped: none,
                            rekinded: expect.stringContaining("read only") });
  });

  it("is carried only by its own caller's rows: an owner's door holds no live session", () => {
    // Every owner row's call, whichever door (N10: the owner's account alone sets no grant).
    type OwnerDoor = { [D in DoorName]: CallerOf<D> extends "owner" ? D : never }[DoorName];
    const ownerDoor: HandlerOf<OwnerDoor, GrantsDoorNeeds> = (o, call) => {
      // @ts-expect-error an owner row's call carries the owner itself, never a live session
      return o.grants.set(call.session, CHANGE, clock());
    };
    // An account's ledger doors, whatever their gate, reach neither ledger widening.
    const mailboxAck: HandlerOf<"mailboxAck", OwnerState> = (o, call) => {
      // @ts-expect-error an account row's call carries no proof
      o.policy.changed(call.session, 1, "wake_limits", {}, "rule", clock());
      // @ts-expect-error nor make one of the binding's session: the sessions' check is no proof
      o.grants.set(o.sessions.live(call.binding.session, clock()), CHANGE, clock());
      // @ts-expect-error nor may it take a held registration
      return o.registrations.take(call.session, "card", "d", clock());
    };
    const roomPost: HandlerOf<"roomPost", OwnerState> = async (o, door) => {
      // @ts-expect-error a units account row's checked call carries none either
      return o.registrations.take(door.check().session, "card", "d", clock());
    };
    // A session row's call carries its live session, which the widening takes.
    const wakeLimits: HandlerOf<"policyWakeLimits", OwnerState> = (o, call) =>
      o.policy.changed(call.session, 1, "wake_limits", {}, "rule", clock());
    const owner: HandlerOf<"sessionExtend", OwnerState> = (o, call) =>
      o.sessions.extend(call.self, call.binding.session, clock());
    expect([untyped, ownerDoor, mailboxAck, roomPost, wakeLimits, owner].every(
      (handler) => typeof handler === "function")).toBe(true);
  });
});
