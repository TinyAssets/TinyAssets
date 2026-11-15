/**
 * The widenings' proofs (`door.ts`; S30, N10, P0-3): each widening takes the proof its
 * caller made as its first argument, refused by `tsc` (each `@ts-expect-error` below fails
 * `tsc` the day its call compiles) and at run time (`proven`) without it; and a row's call
 * carries only its own caller's proof, so a door of another caller cannot make the widening.
 */
import { describe, expect, it } from "vitest";

import { clock } from "../src/clock";
import type { CallerOf, DoorName, HandlerOf, LiveSession, OwnerSelf } from "../src/door";
import type { GrantsDoorNeeds } from "../src/grants";
import type { OwnerState } from "../src/owner-do";
import { rig } from "./ledger-support";
import { inSession, type TestStores } from "./support";

/** A proof of `kind` made by hand, as no source may make one (policy-boundary's `forged`). */
const forged = (kind: string): never => Object.freeze({ proved: kind }) as never;
const CHANGE = { client: "1".repeat(64), source: "mcp", mode: "full", scopes: [],
                 expected_revision: 0, name: "" };

/** Each widening, called with no proof and with another caller's, refused at run time. */
async function widenings() {
  const r = await rig();
  return inSession(r.stub, r.cookie, (stores, session) => {
    const { grants, keys, policy, registrations, sessions, admission } = stores;
    const now = clock();
    const refusals = (call: (proof: never) => unknown, kind: string) =>
      [undefined as never, forged(kind === "session" ? "owner" : "session")].map((proof) => {
        try {
          call(proof);
          return "made";
        } catch (error) {
          return (error as Error).message;
        }
      });
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
  it("is refused at run time when the call holds none, or another caller's", async () => {
    const without = (kind: string) => Array(2).fill(`a widening without its ${kind} proof`);
    expect(await widenings()).toEqual({
      "grants.set": without("session"), "grants.forgetRevoked": without("session"),
      "keys.add": without("session"), "policy.write": without("session"),
      "policy.changed": without("session"), "policy.applyKeyRequest": without("session"),
      "registrations.take": without("session"), "sessions.open": without("owner"),
      "sessions.extend": without("owner"), "admission.set": without("reserved"),
      "admission.setTier": without("reserved"), "policy.imported": without("import"),
    });
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
