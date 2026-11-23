/**
 * What each Worker hands another, held to the deploy order (`deploy/handed.ts`) against the base
 * branch (`ci/history.ts`): this tree takes everything the base's versions hand it and hands
 * nothing the base's versions refuse. The kit's plan holds the same against the versions the
 * stage serves (`deploy.ts` `rollout`), which the base branch is not.
 */
import { describe, expect, it } from "vitest";

import { baseOf, fileAt } from "../../ci/history";
import type { Args, DoorArgs, DoorRow } from "../../contract/src/doors";
import type { Shape } from "../../contract/src/handed";
import { type Before, handedHold, type Side, sideAt, TREE } from "../handed";

describe("what each Worker hands another, against the base branch", () => {
  it("is all the other takes, by the deploy order", () => {
    const base = sideAt(baseOf(), fileAt);
    expect(handedHold(() => base, TREE).problems).toEqual([]);
  });
});

const NONE = { doors: new Set<string>(), shapes: new Set<string>() };
/** A side of `shapes` and door `rows`, the same for every Worker. */
const side = (shapes: Readonly<Record<string, Shape>>, rows: readonly DoorRow[] = []):
  Before & Side => ({ shapes, doors: rows, cutover: NONE });
/** Each way `now` refuses, held against `then` as every Worker's version before. */
const held = (then: Before, now: Side) => handedHold(() => then, now).problems;

const ACCOUNT: Shape = { from: "doors", to: "owner", required: ["owner", "caller", "origin"],
                         optional: ["source", "control"] };
/** An answer: the sandbox, which deploys first, hands it the owner. */
const STARTED: Shape = { from: "sandbox", to: "owner", required: ["started"], optional: [] };

describe("a request, whose parser deploys before its sender", () => {
  it("refuses a parser that no longer takes a field the sender's version before hands", () => {
    // #5805's and #5807's: the doors stopped handing tiers, then signins, and the owner's
    // parser refused both in the same change.
    const before = { ...ACCOUNT, optional: [...ACCOUNT.optional, "tiers", "signins"] };
    expect(held(side({ account: before }), side({ account: ACCOUNT }))).toEqual(
      ["tiers", "signins"].map((field) => `the shape account no longer takes ${field}, which ` +
        "the doors' version before hands: drop it first (taken and left out), and take it out " +
        "once the stage serves no doors version that hands it"));
    // Dropped first, it passes; and taken out once the version before drops it.
    const dropped = { ...ACCOUNT, dropped: ["tiers", "signins"] };
    expect(held(side({ account: before }), side({ account: dropped }))).toEqual([]);
    expect(held(side({ account: dropped }), side({ account: ACCOUNT }))).toEqual([]);
  });

  it("refuses a field newly required, until the version before hands it always", () => {
    const required = { ...ACCOUNT, required: [...ACCOUNT.required, "control"],
                       optional: ["source"] };
    expect(held(side({ account: ACCOUNT }), side({ account: required }))).toEqual([
      "the shape account requires control, which the doors' version before does not always " +
      "hand: take it as always first, and require it once the stage serves no doors version " +
      "that does not"]);
    const always = { ...ACCOUNT, always: ["control"], optional: ["source"] };
    expect(held(side({ account: ACCOUNT }), side({ account: always }))).toEqual([]);
    expect(held(side({ account: always }), side({ account: required }))).toEqual([]);
    // A new optional field, and one made optional, the version before never hands wrongly.
    expect(held(side({ account: ACCOUNT }),
                side({ account: { ...ACCOUNT, optional: [...ACCOUNT.optional, "grant"] } })))
      .toEqual([]);
    expect(held(side({ account: required }), side({ account: ACCOUNT }))).toEqual([]);
  });

  it("refuses a shape gone until the version before hands it no more, but for the cutover's, " +
     "and holds no shape the base lacks", () => {
    expect(held(side({ account: ACCOUNT }), side({}))).toEqual([
      "the shape account is gone, which the doors' version before hands: mark it handed: false " +
      "first (parsed, handed no more), and take it out once the stage serves no doors version " +
      "whose shapes hand it"]);
    // Marked first, it passes; and taken out once the version before marks it.
    const unhanded: Shape = { ...ACCOUNT, handed: false };
    expect(held(side({ account: ACCOUNT }), side({ account: unhanded }))).toEqual([]);
    expect(held(side({ account: unhanded }), side({}))).toEqual([]);
    const cutover = { ...side({ account: ACCOUNT }),
                      cutover: { doors: new Set<string>(), shapes: new Set(["account"]) } };
    expect(held(cutover, side({}))).toEqual([]);
    expect(held(side({}), side({ account: { ...ACCOUNT, required: ["grant"] } }))).toEqual([]);
  });
});

describe("an answer, whose sender deploys before its parser", () => {
  it("refuses a sender that hands a field the parser's version before does not take", () => {
    const code = { ...STARTED, optional: ["code"] };
    expect(held(side({ started: STARTED }), side({ started: code }))).toEqual([
      "the shape started hands code, which the owner's version before does not take: take it " +
      "first (dropped: taken, not yet handed), and hand it once the stage serves no owner " +
      "version that refuses it"]);
    // Taken first, not yet handed, then handed.
    const ready = { ...STARTED, dropped: ["code"] };
    expect(held(side({ started: STARTED }), side({ started: ready }))).toEqual([]);
    expect(held(side({ started: ready }), side({ started: code }))).toEqual([]);
  });

  it("refuses a sender that stops always handing what the version before requires", () => {
    const stopped = { ...STARTED, required: [], optional: ["started"] };
    expect(held(side({ started: STARTED }), side({ started: stopped }))).toEqual([
      "the shape started does not always hand started, which the owner's version before " +
      "requires: hand it always first, and stop once the stage serves no owner version that " +
      "requires it"]);
    const always = { ...STARTED, required: [], always: ["started"] };
    expect(held(side({ started: STARTED }), side({ started: always }))).toEqual([]);
    expect(held(side({ started: always }), side({ started: stopped }))).toEqual([]);
  });

  it("holds no answer the parser's version before lists not, and lets one go", () => {
    expect(held(side({}), side({ started: STARTED }))).toEqual([]);
    expect(held(side({ started: STARTED }), side({}))).toEqual([]);
  });

  it("reads each Worker's own version before: the answer's parser's, the request's sender's",
     () => {
    const sides: Readonly<Record<string, Before>> = {
      owner: side({ started: { ...STARTED, required: ["started", "code"] } }),
      doors: side({ account: ACCOUNT }) };
    expect(handedHold((worker) => sides[worker] ?? null, side({ started: STARTED,
                                                                account: ACCOUNT })).problems)
      .toEqual(["the shape started does not always hand code, which the owner's version " +
                "before requires: hand it always first, and stop once the stage serves no " +
                "owner version that requires it"]);
    // No version before serves: nothing of it talks.
    expect(handedHold(() => null, side({ started: STARTED })).problems).toEqual([]);
  });
});

describe("the door table, the doors' requests to the owner", () => {
  const door = (args: DoorArgs): DoorRow =>
    ({ door: "fileStat", class: "read", capability: "read_graph", args });
  const fields = (required: string[], optional: string[], more: Partial<Args> = {}): Args => ({
    refused: "invalid file request", ...more,
    required: Object.fromEntries(required.map((name) => [name, { type: "string" }])),
    optional: Object.fromEntries(optional.map((name) => [name, { type: "string" }])) });
  const table = (...rows: DoorRow[]) => side({}, rows);

  it("refuses a door gone, and a request field it no longer takes or newly requires", () => {
    const base = table(door(fields(["path"], ["etag"])));
    expect(held(base, table())).toEqual([
      "the door fileStat is gone, which the doors' version before calls: drop it first (the " +
      "owner takes it, the doors call it no more), and take it out once the stage serves no " +
      "doors version that calls it"]);
    const dropped = { ...door(fields(["path"], ["etag"])), dropped: true } as const;
    expect(held(base, table(dropped))).toEqual([]);
    expect(held(table(dropped), table())).toEqual([]);
    expect(held(base, table(door(fields(["path"], []))))).toEqual([
      "fileStat no longer takes etag, which the doors' version before hands: drop it first " +
      "(taken and left out), and take it out once the stage serves no doors version that " +
      "hands it"]);
    const etagDropped = door(fields(["path"], [], { dropped: ["etag"] }));
    expect(held(base, table(etagDropped))).toEqual([]);
    expect(held(table(etagDropped), table(door(fields(["path"], []))))).toEqual([]);
    expect(held(base, table(door(fields(["path", "etag"], []))))).toHaveLength(1);
    expect(held(base, table(door(fields(["path"], ["etag", "max"]))))).toEqual([]);
    // What the base's cutover manifest takes out goes at once; nothing else it names does.
    const cutover = { ...base, cutover: { doors: new Set(["fileStat"]),
                                          shapes: new Set<string>() } };
    expect(held(cutover, table())).toEqual([]);
    expect(held(cutover, table(door(fields(["path", "etag"], []))))).toHaveLength(1);
    // A door that reads its request no more, or leaves it to its module, refuses nothing here.
    expect(held(base, table(door("none")))).toEqual([]);
    expect(held(base, table(door("loose")))).toEqual([]);
  });

  it("refuses a request where the base's door takes none, and a request's omission or an " +
     "undefined field refused", () => {
    const none = table(door("none"));
    expect(held(none, table(door(fields([], ["after"], { omitted: true }))))).toEqual([]);
    expect(held(none, table(door(fields([], ["after"]))))).toEqual([
      "fileStat takes a request now, which the doors' version before calls it without: take " +
      "none, or an omitted one, until the stage serves no doors version that calls it without"]);
    const loose = table(door(fields(["path"], [], { omitted: true, undefinedAllowed: true })));
    expect(held(loose, table(door(fields(["path"], []))))).toEqual([
      "fileStat refuses no request, which the doors' version before may hand",
      "fileStat refuses a field sent undefined, which the doors' version before may hand"]);
  });

  it("refuses a field's type the owner takes no more, until it is a dropped type, and takes a " +
     "wider one", () => {
    // #5822's: the cursors went from integers to strings, b65adb647's doors handing integers.
    const typed = (type: unknown, more: Partial<Args> = {}) =>
      table(door({ refused: "invalid collection request", required: {},
                   optional: { after: type === undefined ? {} : { type } }, ...more }));
    expect(held(typed("integer"), typed("string"))).toEqual([
      "fileStat no longer takes after as integer, which the doors' version before hands: take " +
      "it as that too first (droppedTypes: taken, no longer handed), and take it out once the " +
      "stage serves no doors version that hands it"]);
    const step = typed("string", { droppedTypes: { after: ["integer"] } });
    expect(held(typed("integer"), step)).toEqual([]);
    expect(held(step, typed("string"))).toEqual([]);
    // Each type of a list is held; an integer is a number; a wider type passes.
    expect(held(typed(["string", "null"]), typed("string"))).toEqual([
      expect.stringContaining("fileStat no longer takes after as null,")]);
    expect(held(typed("number"), typed("integer"))).toHaveLength(1);
    expect(held(typed("integer"), typed("number"))).toEqual([]);
    expect(held(typed("string"), typed(["string", "null"]))).toEqual([]);
    expect(held(typed("integer"), typed(undefined))).toEqual([]);
    // A field the owner takes no more is the names' problem, said once; a dropped one takes any.
    expect(held(typed("integer"), table(door(fields([], []))))).toHaveLength(1);
    expect(held(typed("integer"), table(door(fields([], [], { dropped: ["after"] })))))
      .toEqual([]);
  });

  it("holds by name alone, and says so, a field the doors' version before types not", () => {
    const untyped = table(door({ refused: "x", required: { path: {} }, optional: {} }));
    expect(handedHold(() => untyped, table(door(fields(["path"], []))))).toEqual(
      { problems: [], untyped: ["fileStat path"] });
    expect(handedHold(() => untyped, untyped)).toEqual({ problems: [], untyped: [] });
    // Only the doors' version before hands the owner a request.
    expect(handedHold((worker) => worker === "doors" ? null : untyped,
                      table(door(fields(["path"], []))))).toEqual({ problems: [], untyped: [] });
    // A schema the hold cannot read is not guessed at.
    const ranged = table(door({ refused: "x", required: { path: { type: "string", maxLength: 9 } },
                                optional: {} }));
    expect(() => held(ranged, table(door(fields(["path"], [])))))
      .toThrow("path's schema is not its type and description alone");
  });

  it("holds a request split by who asks side by side, and either side to one list", () => {
    const split = table(door({ owner: fields(["card_id", "digest"], []),
                               outside: fields(["command_center_id"], []) }));
    expect(held(split, split)).toEqual([]);
    expect(held(split, table(door({ owner: fields(["command_center_id"], []),
                                    outside: fields(["command_center_id"], []) }))))
      .toHaveLength(3);
    // One list for both sides takes what either side hands.
    const both = table(door(fields([], ["card_id", "digest", "command_center_id"])));
    expect(held(split, both)).toEqual([]);
    // Split from one list, each side is held to all of it.
    expect(held(both, split)).toHaveLength(6);
  });
});

describe("a release's side, read from git", () => {
  const files = (at: Readonly<Record<string, string>>) => (_commit: string, path: string) =>
    at[path] ?? null;
  const DOORS = JSON.stringify({ doors: [] });

  it("names the cutover's edits of its two files, and throws for a release without handed.json",
     () => {
    const manifest = JSON.stringify({ phases: { cutover: { entries: [
      { edit: "platform/contract/handed.json", names: ["\"account\"", "origin"] },
      { edit: "platform/contract/doors.json", names: ["importRows"] }] } } });
    const read = sideAt("c", files({ "platform/contract/doors.json": DOORS,
                                     "platform/contract/handed.json": "{\"shapes\": {}}",
                                     "platform/cutover.json": manifest }));
    expect(read.cutover).toEqual({ doors: new Set(["importRows"]),
                                   shapes: new Set(["account", "origin"]) });
    expect(() => sideAt("c", files({ "platform/contract/doors.json": DOORS })))
      .toThrow("c holds no platform/contract/handed.json: what it hands and takes is unknown");
  });
});
