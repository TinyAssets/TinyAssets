/**
 * Each binding kind's parser (`contract/src/binding.ts`) takes the binding the binder builds
 * for it, field for field, and refuses any malformed one as `invalid`: a field missing, a
 * field another kind carries, a field out of its grammar, or another kind's caller.
 */
import { describe, expect, it } from "vitest";

import { BINDING_PARSERS, type BindingKind, PLATFORM_CALLER } from "../src/binding";
import { refusalOf } from "../src/refusal";

const OWNER = "user_owner";
const HEX = (char: string, length: number) => char.repeat(length);
const ORIGIN = { client: HEX("a", 64), family: HEX("b", 64) };
const EGRESS = { kind: "egress", token: HEX("c", 64), generation: 3 };

/** Each kind's bindings as the binder builds them (`doors/src/binder.ts`). */
const BUILT: Readonly<Record<BindingKind, readonly Record<string, unknown>[]>> = {
  account: [
    { owner: OWNER, caller: OWNER, origin: null },
    { owner: OWNER, caller: "user_visitor", origin: null, tiers: { free: 1 }, signins: [] },
    { owner: OWNER, caller: OWNER, origin: ORIGIN, source: { kind: "mcp", ref: "client-1" },
      authenticated_at: 1_700_000_000, vendor: { id: "claude", verified: true },
      tiers: null, signins: null },
    { owner: OWNER, caller: OWNER, origin: ORIGIN,
      source: { kind: "api_key", ref: HEX("d", 16) }, authenticated_at: 0 },
    { owner: OWNER, caller: OWNER, origin: null, grant: "grant-1", expires_at: 1_700_000_100 },
  ],
  session: [
    { owner: OWNER, caller: OWNER, origin: null, session: "cookie" },
    { owner: OWNER, caller: OWNER, origin: ORIGIN, source: { kind: "api_key", ref: HEX("d", 16) },
      authenticated_at: 5, session: "cookie" },
  ],
  sandbox: [
    { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS },
    { owner: OWNER, caller: OWNER, origin: null, sandbox: { ...EGRESS, runner: HEX("e", 64) } },
    { owner: OWNER, caller: OWNER, origin: null,
      sandbox: { kind: "sync", token: HEX("c", 64), egress: HEX("f", 64) } },
  ],
  run: [
    { owner: OWNER, caller: OWNER, origin: null, sandbox: { ...EGRESS, secret: HEX("1", 64) },
      run: `run_${HEX("2", 32)}` },
  ],
  inbound: [
    { owner: OWNER, caller: OWNER, origin: null, inbound: { binding: HEX("3", 32), revision: 1 },
      tiers: { free: 1 }, signins: [] },
  ],
  reserved: [{ owner: OWNER, caller: PLATFORM_CALLER, origin: null }],
  erasure: [{ owner: OWNER, caller: PLATFORM_CALLER, origin: null }],
  import: [
    { owner: OWNER, caller: PLATFORM_CALLER, origin: null, import: { bundle: HEX("4", 64) },
      tiers: null },
  ],
};

/** Each kind's binding made malformed one way, by what is wrong with it. */
const MALFORMED: Readonly<Record<BindingKind, readonly [string, unknown][]>> = {
  account: [
    ["not an object", "user_owner"],
    ["no origin", { owner: OWNER, caller: OWNER }],
    ["an origin with a third key", { owner: OWNER, caller: OWNER,
                                     origin: { ...ORIGIN, extra: 1 } }],
    ["a short client", { owner: OWNER, caller: OWNER, origin: { ...ORIGIN, client: "a" } }],
    ["another kind's field", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS }],
    ["an unknown source", { owner: OWNER, caller: OWNER, origin: ORIGIN,
                            source: { kind: "cookie", ref: "x" } }],
    ["an API key's ref not its id", { owner: OWNER, caller: OWNER, origin: ORIGIN,
                                      source: { kind: "api_key", ref: "key" } }],
    ["a sign-in time not whole seconds", { owner: OWNER, caller: OWNER, origin: null,
                                           authenticated_at: 1.5 }],
    ["an empty session", { owner: OWNER, caller: OWNER, origin: null, session: "" }],
    ["a vendor out of shape", { owner: OWNER, caller: OWNER, origin: ORIGIN,
                                vendor: { id: "claude" } }],
    ["an untrimmed owner", { owner: " user_owner", caller: OWNER, origin: null }],
  ],
  session: [["no session", { owner: OWNER, caller: OWNER, origin: null }]],
  sandbox: [
    ["an outside origin", { owner: OWNER, caller: OWNER, origin: ORIGIN, sandbox: EGRESS }],
    ["another caller", { owner: OWNER, caller: "user_other", origin: null, sandbox: EGRESS }],
    ["both secrets", { owner: OWNER, caller: OWNER, origin: null,
                       sandbox: { ...EGRESS, secret: HEX("1", 64), runner: HEX("e", 64) } }],
    ["no generation", { owner: OWNER, caller: OWNER, origin: null,
                        sandbox: { kind: "egress", token: HEX("c", 64) } }],
    ["generation 0", { owner: OWNER, caller: OWNER, origin: null,
                       sandbox: { ...EGRESS, generation: 0 } }],
    ["a sync token with a generation", { owner: OWNER, caller: OWNER, origin: null,
      sandbox: { kind: "sync", token: HEX("c", 64), egress: HEX("f", 64), generation: 1 } }],
    ["a run beside it", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS,
                          run: `run_${HEX("2", 32)}` }],
  ],
  run: [
    ["no run", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS }],
    ["a run out of its grammar", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS,
                                   run: "run_x" }],
    ["the sync token", { owner: OWNER, caller: OWNER, origin: null, run: `run_${HEX("2", 32)}`,
      sandbox: { kind: "sync", token: HEX("c", 64), egress: HEX("f", 64) } }],
  ],
  inbound: [
    ["revision 0", { owner: OWNER, caller: OWNER, origin: null,
                     inbound: { binding: HEX("3", 32), revision: 0 } }],
    ["a session beside it", { owner: OWNER, caller: OWNER, origin: null, session: "cookie",
                              inbound: { binding: HEX("3", 32), revision: 1 } }],
    ["a binding id of 64 hex", { owner: OWNER, caller: OWNER, origin: null,
                                 inbound: { binding: HEX("3", 64), revision: 1 } }],
  ],
  reserved: [
    ["the owner's own caller", { owner: OWNER, caller: OWNER, origin: null }],
    ["an outside origin", { owner: OWNER, caller: PLATFORM_CALLER, origin: ORIGIN }],
    ["tiers beside it", { owner: OWNER, caller: PLATFORM_CALLER, origin: null, tiers: null }],
  ],
  erasure: [["a session beside it", { owner: OWNER, caller: PLATFORM_CALLER, origin: null,
                                      session: "cookie" }]],
  import: [
    ["no tiers", { owner: OWNER, caller: PLATFORM_CALLER, origin: null,
                   import: { bundle: HEX("4", 64) } }],
    ["a bundle out of its grammar", { owner: OWNER, caller: PLATFORM_CALLER, origin: null,
                                      import: { bundle: "x" }, tiers: null }],
    ["the owner's own caller", { owner: OWNER, caller: OWNER, origin: null,
                                 import: { bundle: HEX("4", 64) }, tiers: null }],
  ],
};

const KINDS = Object.keys(BINDING_PARSERS) as BindingKind[];

describe("each binding kind's parser", () => {
  it.each(KINDS.flatMap((kind) => BUILT[kind].map((built) => [kind, built] as const)))(
    "%s takes the binding the binder builds, field for field", (kind, built) => {
      expect(BINDING_PARSERS[kind](built)).toEqual(built);
    });

  it.each(KINDS.flatMap((kind) => MALFORMED[kind].map(([what, bad]) => [kind, what, bad] as const)))(
    "%s refuses %s as invalid", (kind, _what, bad) => {
      let thrown: unknown;
      try {
        BINDING_PARSERS[kind](bad);
      } catch (error) {
        thrown = error;
      }
      expect(refusalOf(thrown)?.code).toBe("invalid");
    });
});
