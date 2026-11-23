/**
 * What the doors hand the owner's object, held against the base branch: the owner takes
 * everything the base's doors hand it. The kit deploys the owner before the doors
 * (`facts.ts` `WORKERS`: each Worker after the ones it binds), so from the owner's 100% until
 * the doors' (their 0% soak and smoke, or a run stopped between) the doors' version before
 * calls this owner, and a rollback takes the doors back first. The 0% smoke reaches only the
 * doors' new version, so nothing else would see the owner refuse the version before.
 *
 * Two things the doors hand are data, which the base's files say for the base's doors: each
 * binding's shapes (`contract/bindings.json`) and each door's request (`contract/doors.json`
 * `args`). The owner must still take every door the base's table has; every field a base
 * shape or request lists, required or optional; no request where the base's door takes none
 * or may be called with none (`omitted`); a field sent `undefined` where the base's door
 * takes one (`undefinedAllowed`); and it may require no field the base's doors do not always
 * hand. What the doors stop handing is dropped first: a binding field to its shape's
 * `dropped`, a request's field to its arguments' `dropped`, which the owner takes and drops,
 * and a door to `dropped: true`, which the owner takes and the doors' `OwnerDoors` lacks. It
 * goes once the doors the stage serve hand it no more (expand, then contract): the base
 * branch is not what the stage serves, so that wait is the change's own, named in its
 * decision log. A door's request read by its own module (`loose`) is its module's, and not
 * held here. The base is `ci/history.ts`'s.
 */
import { describe, expect, it } from "vitest";

import { baseOf, fileAt } from "../../ci/history";
import bindings from "../../contract/bindings.json" with { type: "json" };
import doors from "../../contract/doors.json" with { type: "json" };
import type { Shape } from "../../contract/src/binding";
import type { Args, DoorArgs, DoorRow } from "../../contract/src/doors";
import { WORKERS } from "../facts";

const BINDINGS_JSON = "platform/contract/bindings.json";
const DOORS_JSON = "platform/contract/doors.json";

/** What the doors hand on one side of the change: the binding's shapes and the door table. */
interface Side {
  /** Null on a base before the shapes were data. */
  readonly shapes: Readonly<Record<string, Shape>> | null;
  readonly doors: readonly DoorRow[];
}

/** The fields one side hands, always and sometimes, and the ones it takes and drops. */
interface Fields {
  readonly required: readonly string[];
  readonly optional: readonly string[];
  readonly dropped: readonly string[];
}

/**
 * Each way `taken` refuses what `handed` hands, as `what`'s: a field it no longer takes, or
 * one it requires that `handed` does not always hand.
 */
function fieldProblems(what: string, handed: Fields, taken: Fields): string[] {
  const takes = new Set([...taken.required, ...taken.optional, ...taken.dropped]);
  return [
    ...[...handed.required, ...handed.optional].filter((field) => !takes.has(field))
      .map((field) => `${what} no longer takes ${field}, which the doors' version before ` +
                      "hands: drop it first (the owner takes and drops it), and take it out " +
                      "once the doors the stage serve hand it no more"),
    ...taken.required.filter((field) => !handed.required.includes(field))
      .map((field) => `${what} requires ${field}, which the doors' version before does not ` +
                      "always hand: take it as optional until the doors the stage serve do"),
  ];
}

function shapeFields(shape: Shape): Fields {
  return { required: shape.required, optional: shape.optional, dropped: shape.dropped ?? [] };
}

/** A door's request as one list of fields, by who asks where its row splits them. */
function requestSides(args: Args | { readonly owner: Args; readonly outside: Args }):
    Readonly<Record<string, Args>> {
  return "refused" in args ? { "": args } : { owner: args.owner, outside: args.outside };
}

function argsFields(args: Args): Fields {
  return { required: Object.keys(args.required), optional: Object.keys(args.optional),
           dropped: args.dropped ?? [] };
}

/** Each way the head's arguments of `door` refuse what the base's doors hand it. */
function argsProblems(door: string, base: DoorArgs, head: DoorArgs): string[] {
  if (base === "loose" || head === "loose" || head === "none") {
    return [];
  }
  const sides = requestSides(head);
  const paired = (side: string): [string, Args][] => Object.entries(sides)
    .filter(([key]) => side === "" || key === "" || key === side)
    .map(([key, args]) => [side || key, args]);
  const named = (side: string) => side === "" ? door : `${door} (${side})`;
  if (base === "none") {
    return paired("").filter(([, args]) => args.omitted !== true
                             || Object.keys(args.required).length > 0)
      .map(([side]) => `${named(side)} takes a request now, which the doors' version before ` +
                       "calls it without: take none, or an omitted one, until the doors the " +
                       "stage serve hand one");
  }
  return Object.entries(requestSides(base)).flatMap(([side, before]) =>
    paired(side).flatMap(([key, after]) => [
      ...fieldProblems(named(key), argsFields(before), argsFields(after)),
      ...before.omitted === true && after.omitted !== true
        ? [`${named(key)} refuses no request, which the doors' version before may hand`] : [],
      ...before.undefinedAllowed === true && after.undefinedAllowed !== true
        ? [`${named(key)} refuses a field sent undefined, which the doors' version before ` +
           "may hand"] : [],
    ]));
}

/** Each way `head`'s owner refuses what `base`'s doors hand it. */
function handedProblems(base: Side, head: Side): string[] {
  const shapes = base.shapes === null || head.shapes === null ? []
    : Object.entries(base.shapes).flatMap(([name, before]) => {
      const after = head.shapes![name];
      return after === undefined
        ? [`the binding shape ${name} is gone, which the doors' version before hands`]
        : fieldProblems(`the binding shape ${name}`, shapeFields(before), shapeFields(after));
    });
  const rows = new Map(head.doors.map((row) => [row.door, row]));
  return [...shapes, ...base.doors.flatMap((before) => {
    const after = rows.get(before.door);
    return after !== undefined ? argsProblems(before.door, before.args, after.args)
      : before.dropped === true ? []
        : [`the door ${before.door} is gone, which the doors' version before calls: drop it ` +
           "first (the owner takes it, the doors call it no more), and take it out once the " +
           "doors the stage serve call it no more"];
  })];
}

/** The base's side: its shapes, none before they were data, and its door table. */
function baseSide(base: string): Side {
  const shapes = fileAt(base, BINDINGS_JSON);
  const table = fileAt(base, DOORS_JSON);
  if (table === null) {
    throw new Error(`${base} holds no ${DOORS_JSON}`);
  }
  return { shapes: shapes === null ? null : (JSON.parse(shapes) as typeof bindings).shapes,
           doors: (JSON.parse(table) as { doors: DoorRow[] }).doors };
}

const HEAD: Side = { shapes: bindings.shapes, doors: doors.doors as unknown as DoorRow[] };

describe("what the doors hand the owner, against the base branch", () => {
  it("is all the owner takes, which the kit deploys before the doors", () => {
    // The rule's direction is the deploy order's: were the doors deployed first, the doors'
    // new version would call the owner's version before, and the rule would run the other way.
    expect(WORKERS.indexOf("owner")).toBeLessThan(WORKERS.indexOf("doors"));
    expect(handedProblems(baseSide(baseOf()), HEAD)).toEqual([]);
  });

  const ACCOUNT: Shape = { required: ["owner", "caller", "origin"],
                           optional: ["source", "control"] };
  const side = (account: Shape, rows: readonly DoorRow[] = []): Side =>
    ({ shapes: { account }, doors: rows });
  const door = (args: DoorArgs): DoorRow =>
    ({ door: "fileStat", class: "read", capability: "read_graph", args });
  const fields = (required: string[], optional: string[], more: Partial<Args> = {}): Args => ({
    refused: "invalid file request", ...more,
    required: Object.fromEntries(required.map((name) => [name, { type: "string" }])),
    optional: Object.fromEntries(optional.map((name) => [name, { type: "string" }])) });

  it("refuses an owner that no longer takes a binding field the base's doors hand", () => {
    // #5805's and #5807's: the doors stopped handing tiers, then signins, and the owner's
    // parser refused both in the same change.
    const before = { ...ACCOUNT, optional: [...ACCOUNT.optional, "tiers", "signins"] };
    expect(handedProblems(side(before), side(ACCOUNT))).toEqual(["tiers", "signins"].map((field) =>
      `the binding shape account no longer takes ${field}, which the doors' version before ` +
      "hands: drop it first (the owner takes and drops it), and take it out once the doors " +
      "the stage serve hand it no more"));
    // Dropped first, as HANDED_BEFORE did, it passes; and taken out once the base drops it.
    const dropped = { ...ACCOUNT, dropped: ["tiers", "signins"] };
    expect(handedProblems(side(before), side(dropped))).toEqual([]);
    expect(handedProblems(side(dropped), side(ACCOUNT))).toEqual([]);
  });

  it("refuses a field the owner newly requires, a shape gone, and a base before the shapes " +
     "holds none", () => {
    const required = { ...ACCOUNT, required: [...ACCOUNT.required, "control"],
                       optional: ["source"] };
    const newly = { ...ACCOUNT, required: [...ACCOUNT.required, "grant"] };
    expect(handedProblems(side(ACCOUNT), side(required))).toEqual([
      "the binding shape account requires control, which the doors' version before does not " +
      "always hand: take it as optional until the doors the stage serve do"]);
    expect(handedProblems(side(ACCOUNT), side(newly))).toHaveLength(1);
    // A new optional field, and one made optional, the doors' version before never hands
    // wrongly.
    expect(handedProblems(side(ACCOUNT), side({ ...ACCOUNT, optional: ["source", "control",
                                                                      "grant"] })))
      .toEqual([]);
    expect(handedProblems(side(required), side(ACCOUNT))).toEqual([]);
    expect(handedProblems(side(ACCOUNT), { shapes: {}, doors: [] }))
      .toEqual(["the binding shape account is gone, which the doors' version before hands"]);
    expect(handedProblems({ shapes: null, doors: [] }, side(ACCOUNT))).toEqual([]);
  });

  it("refuses a door gone, and a request field it no longer takes or newly requires", () => {
    const base = side(ACCOUNT, [door(fields(["path"], ["etag"]))]);
    expect(handedProblems(base, side(ACCOUNT, []))).toEqual([
      "the door fileStat is gone, which the doors' version before calls: drop it first (the " +
      "owner takes it, the doors call it no more), and take it out once the doors the stage " +
      "serve call it no more"]);
    const dropped = { ...door(fields(["path"], ["etag"])), dropped: true } as const;
    expect(handedProblems(base, side(ACCOUNT, [dropped]))).toEqual([]);
    expect(handedProblems(side(ACCOUNT, [dropped]), side(ACCOUNT, []))).toEqual([]);
    expect(handedProblems(base, side(ACCOUNT, [door(fields(["path"], []))]))).toEqual([
      "fileStat no longer takes etag, which the doors' version before hands: drop it first " +
      "(the owner takes and drops it), and take it out once the doors the stage serve hand it " +
      "no more"]);
    const etagDropped = door(fields(["path"], [], { dropped: ["etag"] }));
    expect(handedProblems(base, side(ACCOUNT, [etagDropped]))).toEqual([]);
    expect(handedProblems(side(ACCOUNT, [etagDropped]), side(ACCOUNT, [door(fields(["path"],
                                                                                  []))])))
      .toEqual([]);
    expect(handedProblems(base, side(ACCOUNT, [door(fields(["path", "etag"], []))])))
      .toHaveLength(1);
    expect(handedProblems(base, side(ACCOUNT, [door(fields(["path"], ["etag", "max"]))])))
      .toEqual([]);
    // A door that reads its request no more, or leaves it to its module, refuses nothing here.
    expect(handedProblems(base, side(ACCOUNT, [door("none")]))).toEqual([]);
    expect(handedProblems(base, side(ACCOUNT, [door("loose")]))).toEqual([]);
  });

  it("refuses a request where the base's door takes none, and a request's omission or an " +
     "undefined field refused", () => {
    const none = side(ACCOUNT, [door("none")]);
    expect(handedProblems(none, side(ACCOUNT, [door(fields([], ["after"], { omitted: true }))])))
      .toEqual([]);
    expect(handedProblems(none, side(ACCOUNT, [door(fields([], ["after"]))]))).toEqual([
      "fileStat takes a request now, which the doors' version before calls it without: take " +
      "none, or an omitted one, until the doors the stage serve hand one"]);
    const loose = side(ACCOUNT, [door(fields(["path"], [], { omitted: true,
                                                                undefinedAllowed: true }))]);
    expect(handedProblems(loose, side(ACCOUNT, [door(fields(["path"], []))]))).toEqual([
      "fileStat refuses no request, which the doors' version before may hand",
      "fileStat refuses a field sent undefined, which the doors' version before may hand"]);
  });

  it("holds a request split by who asks side by side, and either side to one list", () => {
    const owner = fields(["card_id", "digest"], []);
    const outside = fields(["command_center_id"], []);
    const split = side(ACCOUNT, [door({ owner, outside })]);
    expect(handedProblems(split, split)).toEqual([]);
    expect(handedProblems(split, side(ACCOUNT, [door({ owner: outside, outside })]))).toEqual([
      "fileStat (owner) no longer takes card_id, which the doors' version before hands: drop " +
      "it first (the owner takes and drops it), and take it out once the doors the stage " +
      "serve hand it no more",
      "fileStat (owner) no longer takes digest, which the doors' version before hands: drop " +
      "it first (the owner takes and drops it), and take it out once the doors the stage " +
      "serve hand it no more",
      "fileStat (owner) requires command_center_id, which the doors' version before does not " +
      "always hand: take it as optional until the doors the stage serve do"]);
    // One list for both sides takes what either side hands.
    const both = fields([], ["card_id", "digest", "command_center_id"]);
    expect(handedProblems(split, side(ACCOUNT, [door(both)]))).toEqual([]);
    // Split from one list, each side is held to all of it.
    expect(handedProblems(side(ACCOUNT, [door(both)]), split)).toHaveLength(6);
  });
});
