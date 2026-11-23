/**
 * Each binding kind's parser (`contract/src/binding.ts`) takes the binding the binder builds
 * for it, field for field, which presents that kind (`bindingKind`), and refuses any malformed
 * one as `invalid`: a field missing, a field another kind carries, a field out of its grammar,
 * or another kind's caller. Each shape a binding is made of (`handed.json`) lists exactly the
 * fields its parser takes from what the binder builds.
 */
import { describe, expect, it } from "vitest";

import { BINDING_PARSERS, type BindingKind, bindingKind, PLATFORM_CALLER } from "../src/binding";
import { handedShapes, type ShapeName } from "../src/handed";
import { refusalOf } from "../src/refusal";

const OWNER = "user_owner";
const HEX = (char: string, length: number) => char.repeat(length);
const ORIGIN = { client: HEX("a", 64), family: HEX("b", 64) };
const EGRESS = { kind: "egress", token: HEX("c", 64), generation: 3 };
const SYNC = { kind: "sync", token: HEX("c", 64), egress: HEX("f", 64) };
/** A control record's text, as the binder hands it read fresh (`freshRecord`). */
const RECORD = JSON.stringify({ outside: "on", ordinary: "all", canary: [], inbound: false });

/** Each kind's bindings as the binder builds them (`doors/src/binder.ts`), and `none`'s. */
const BUILT: Readonly<Record<BindingKind, readonly unknown[]>> = {
  none: [undefined],
  account: [
    { owner: OWNER, caller: OWNER, origin: null },
    { owner: OWNER, caller: "user_visitor", origin: null, control: RECORD },
    { owner: OWNER, caller: OWNER, origin: ORIGIN, source: { kind: "mcp", ref: "client-1" },
      authenticated_at: 1_700_000_000, vendor: { id: "claude", verified: true },
      control: null },
    { owner: OWNER, caller: OWNER, origin: ORIGIN,
      source: { kind: "api_key", ref: HEX("d", 16) }, authenticated_at: 0 },
    { owner: OWNER, caller: OWNER, origin: null, grant: "grant-1", expires_at: 1_700_000_100 },
  ],
  session: [
    { owner: OWNER, caller: OWNER, origin: null, session: "cookie" },
    { owner: OWNER, caller: OWNER, origin: ORIGIN, source: { kind: "api_key", ref: HEX("d", 16) },
      authenticated_at: 5, session: "cookie" },
  ],
  egress: [
    { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS },
    { owner: OWNER, caller: OWNER, origin: null, sandbox: { ...EGRESS, runner: HEX("e", 64) } },
  ],
  sync: [{ owner: OWNER, caller: OWNER, origin: null, sandbox: SYNC }],
  run: [
    { owner: OWNER, caller: OWNER, origin: null, sandbox: { ...EGRESS, secret: HEX("1", 64) },
      run: `run_${HEX("2", 32)}` },
  ],
  inbound: [
    { owner: OWNER, caller: OWNER, origin: null, inbound: { binding: HEX("3", 32), revision: 1 },
      control: RECORD },
  ],
  reserved: [{ owner: OWNER, caller: PLATFORM_CALLER, origin: null }],
  import: [
    { owner: OWNER, caller: PLATFORM_CALLER, origin: null, import: { bundle: HEX("4", 64) },
      control: null },
    { owner: OWNER, caller: PLATFORM_CALLER, origin: null, import: { bundle: HEX("4", 64) },
      control: RECORD },
    { owner: OWNER, caller: PLATFORM_CALLER, origin: null, import: { bundle: HEX("4", 64) } },
  ],
};

/** Each kind's binding made malformed one way, by what is wrong with it. */
const MALFORMED: Readonly<Record<BindingKind, readonly [string, unknown][]>> = {
  none: [["a binding", { owner: OWNER, caller: OWNER, origin: null }], ["null", null]],
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
    ["a control record that does not parse", { owner: OWNER, caller: OWNER, origin: null,
                                               control: "{outside" }],
    ["a control record out of shape", { owner: OWNER, caller: OWNER, origin: null,
                                        control: JSON.stringify({ outside: "on" }) }],
    ["a control record not its text", { owner: OWNER, caller: OWNER, origin: null,
                                        control: JSON.parse(RECORD) }],
    // What the doors handed before #5805 and #5807, which no doors the stage serves hand now.
    ["the tiers beside the record", { owner: OWNER, caller: OWNER, origin: null,
                                      control: RECORD, tiers: { free: { runs: 5 } } }],
    ["the switches beside the record", { owner: OWNER, caller: OWNER, origin: null,
                                         control: RECORD, signins: ["claude-code"] }],
  ],
  session: [["no session", { owner: OWNER, caller: OWNER, origin: null }]],
  egress: [
    ["an outside origin", { owner: OWNER, caller: OWNER, origin: ORIGIN, sandbox: EGRESS }],
    ["another caller", { owner: OWNER, caller: "user_other", origin: null, sandbox: EGRESS }],
    ["both secrets", { owner: OWNER, caller: OWNER, origin: null,
                       sandbox: { ...EGRESS, secret: HEX("1", 64), runner: HEX("e", 64) } }],
    ["no generation", { owner: OWNER, caller: OWNER, origin: null,
                        sandbox: { kind: "egress", token: HEX("c", 64) } }],
    ["generation 0", { owner: OWNER, caller: OWNER, origin: null,
                       sandbox: { ...EGRESS, generation: 0 } }],
    ["the sync token", { owner: OWNER, caller: OWNER, origin: null, sandbox: SYNC }],
    ["a run beside it", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS,
                          run: `run_${HEX("2", 32)}` }],
  ],
  sync: [
    ["a sync token with a generation", { owner: OWNER, caller: OWNER, origin: null,
                                          sandbox: { ...SYNC, generation: 1 } }],
    ["the egress token", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS }],
    ["another caller", { owner: OWNER, caller: "user_other", origin: null, sandbox: SYNC }],
  ],
  run: [
    ["no run", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS }],
    ["a run out of its grammar", { owner: OWNER, caller: OWNER, origin: null, sandbox: EGRESS,
                                   run: "run_x" }],
    ["the sync token", { owner: OWNER, caller: OWNER, origin: null, run: `run_${HEX("2", 32)}`,
                         sandbox: SYNC }],
  ],
  inbound: [
    ["revision 0", { owner: OWNER, caller: OWNER, origin: null,
                     inbound: { binding: HEX("3", 32), revision: 0 } }],
    ["a session beside it", { owner: OWNER, caller: OWNER, origin: null, session: "cookie",
                              inbound: { binding: HEX("3", 32), revision: 1 } }],
    ["a binding id of 64 hex", { owner: OWNER, caller: OWNER, origin: null,
                                 inbound: { binding: HEX("3", 64), revision: 1 } }],
    ["the switches beside the record", { owner: OWNER, caller: OWNER, origin: null,
                                         inbound: { binding: HEX("3", 32), revision: 1 },
                                         control: null, signins: [] }],
  ],
  reserved: [
    ["the owner's own caller", { owner: OWNER, caller: OWNER, origin: null }],
    ["an outside origin", { owner: OWNER, caller: PLATFORM_CALLER, origin: ORIGIN }],
    ["a control record beside it", { owner: OWNER, caller: PLATFORM_CALLER, origin: null,
                                     control: RECORD }],
    ["a session beside it", { owner: OWNER, caller: PLATFORM_CALLER, origin: null,
                              session: "cookie" }],
  ],
  import: [
    ["a control record that does not parse", { owner: OWNER, caller: PLATFORM_CALLER,
                                               origin: null, import: { bundle: HEX("4", 64) },
                                               control: "not json" }],
    ["a bundle out of its grammar", { owner: OWNER, caller: PLATFORM_CALLER, origin: null,
                                      import: { bundle: "x" }, control: null }],
    ["the owner's own caller", { owner: OWNER, caller: OWNER, origin: null,
                                 import: { bundle: HEX("4", 64) }, control: null }],
  ],
};

const KINDS = Object.keys(BINDING_PARSERS) as BindingKind[];

describe("each binding kind's parser", () => {
  it.each(KINDS.flatMap((kind) => BUILT[kind].map((built) => [kind, built] as const)))(
    "%s takes the binding the binder builds, field for field, which presents it", (kind, built) => {
      expect(BINDING_PARSERS[kind](built)).toEqual(built);
      expect(bindingKind(built)).toBe(kind);
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

  // A field a shape drops (`handed.json`), which the doors' version before still hands, is
  // taken and left out.
  it.each(KINDS.flatMap((kind) => BUILT[kind].map((built) => [kind, built] as const))
    .filter(([kind]) => kind !== "none"))(
    "%s takes its shape's dropped fields and leaves them out", (kind, built) => {
      const dropped = SHAPES[AT[kind]!].dropped ?? [];
      const handed = { ...built as object, ...Object.fromEntries(dropped.map((field) =>
        [field, "handed before"])) };
      expect(BINDING_PARSERS[kind](handed)).toEqual(built);
    });
});

/** Where each shape sits in a kind's binding: the kind, and the path to it. */
const PLACES: Readonly<Partial<Record<ShapeName, readonly (readonly [BindingKind, string?])[]>>> = {
  account: [["account"], ["session"]],
  origin: [["account", "origin"], ["session", "origin"]],
  source: [["account", "source"], ["session", "source"]],
  vendor: [["account", "vendor"]],
  sandbox: [["egress"], ["sync"]],
  egress_token: [["egress", "sandbox"], ["run", "sandbox"]],
  sync_token: [["sync", "sandbox"]],
  run: [["run"]],
  inbound: [["inbound"]],
  inbound_route: [["inbound", "inbound"]],
  reserved: [["reserved"]],
  import: [["import"]],
  import_bundle: [["import", "import"]],
};
const SHAPES = handedShapes();
/** Each kind's own shape, the one its binding is. */
const AT: Partial<Record<BindingKind, ShapeName>> = Object.fromEntries(
  Object.entries(PLACES).flatMap(([name, places]) => places
    .filter(([, path]) => path === undefined).map(([kind]) => [kind, name])));

describe("the shapes a binding is made of (handed.json)", () => {
  it.each(Object.keys(PLACES) as ShapeName[])(
    "%s, which the doors hand the owner, lists exactly the fields its parser takes from what " +
    "the binder builds", (name) => {
      const shape = SHAPES[name];
      const always = [...shape.required, ...shape.always ?? []];
      const built = PLACES[name]!.flatMap(([kind, path]) => BUILT[kind].map((binding) =>
        path === undefined ? binding : (binding as Record<string, unknown>)[path]))
        .filter((value) => typeof value === "object" && value !== null) as object[];
      const fields = new Set(built.flatMap((value) => Object.keys(value)));
      expect([shape.from, shape.to]).toEqual(["doors", "owner"]);
      expect([...fields].sort()).toEqual([...always, ...shape.optional].sort());
      expect(built.every((value) => always.every((field) => field in value))).toBe(true);
    });
});
