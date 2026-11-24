/**
 * What each Worker hands another, held to the deploy order (`deploy/handed.ts`) against the base
 * branch (`ci/history.ts`): this tree takes everything the base's versions hand it and hands
 * nothing the base's versions refuse. The kit's plan holds the same against the versions the
 * stage serves (`deploy.ts` `rollout`), which the base branch is not.
 */
import { describe, expect, it } from "vitest";

import { baseOf, fileAt } from "../../ci/history";
import type { Answer, Args, DoorArgs, DoorRow } from "../../contract/src/doors";
import type { Shape } from "../../contract/src/handed";
import { type Before, handedHold, type Side, sideAt, TREE } from "../handed";

describe("what each Worker hands another, against the base branch", () => {
  it("is all the other takes, by the deploy order", () => {
    const base = sideAt(baseOf(), fileAt);
    expect(handedHold(() => base, TREE).problems).toEqual([]);
  });
});

const NONE = { doors: new Set<string>(), shapes: new Set<string>() };
/** A side of `shapes` and door `rows`, the same for every Worker, its sandbox speaking none. */
const side = (shapes: Readonly<Record<string, Shape>>, rows: readonly DoorRow[] = []):
  Before & Side => ({ shapes, doors: rows, runners: {}, cutover: NONE });
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
    const none = { problems: [], untyped: [], undeclared: [], unrecorded: [] };
    expect(handedHold(() => untyped, table(door(fields(["path"], []))))).toEqual(
      { ...none, untyped: ["fileStat path"] });
    expect(handedHold(() => untyped, untyped)).toEqual(none);
    // Only the doors' version before hands the owner a request.
    expect(handedHold((worker) => worker === "doors" ? null : untyped,
                      table(door(fields(["path"], []))))).toEqual(none);
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

describe("the door table's answers, which the doors' version before reads of the owner's", () => {
  /** `tierRead`, its answer `answer` (none where undefined). */
  const tierRead = (answer?: Answer): DoorRow =>
    ({ door: "tierRead", class: "read", capability: null, args: "none",
       ...answer === undefined ? {} : { answer } });
  const reads = (required: Record<string, unknown>, optional: Record<string, unknown> = {},
                 more: Partial<Answer> = {}) =>
    side({}, [tierRead({ required, optional, ...more } as Answer)]);
  const TIER = reads({ tier: { type: "string" } });
  const MESSAGE = (what: string) => `tierRead ${what}, which the doors' version before reads`;
  const STEP = "mark it unread first (answered, read no more), and take it out once the stage " +
    "serves no doors version that reads it";

  it("refuses an owner that no longer answers a field the doors' version before reads, until " +
     "it is marked unread", () => {
    // policyProposal's digest and registerProposal's request went with their doors (#5832,
    // #5821); a field of a door kept goes this way.
    expect(held(TIER, reads({ plan: { type: "string" } }))).toEqual(
      [`${MESSAGE("no longer answers tier")}: ${STEP}`]);
    expect(held(TIER, side({}, [tierRead()]))).toEqual(
      [`${MESSAGE("no longer answers tier")}: ${STEP}`]);
    const unread = reads({ tier: { type: "string" } }, {}, { unread: ["tier"] });
    expect(held(TIER, unread)).toEqual([]);
    expect(held(unread, reads({ plan: { type: "string" } }))).toEqual([]);
    // An answer newly read, or newly answered, is never paired with a reader before it.
    expect(held(TIER, reads({ tier: { type: "string" }, plan: { type: "string" } }))).toEqual([]);
  });

  it("refuses a field read as always answered that the owner answers not always, a type the " +
     "reader takes not, and a null it reads not", () => {
    expect(held(TIER, reads({}, { tier: { type: "string" } }))).toEqual(
      [`tierRead answers tier not always, which the doors' version before reads as always ` +
       `answered: answer that under a new name, and ${STEP}`]);
    expect(held(reads({}, { tier: { type: "string" } }), TIER)).toEqual([]);
    // Widening what the owner answers is a narrowing for the reader.
    expect(held(TIER, reads({ tier: { type: ["string", "null"] } }))).toEqual(
      [`tierRead answers tier as null, which the doors' version before does not read: answer ` +
       `that under a new name, and ${STEP}`]);
    expect(held(reads({ tier: { type: ["string", "null"] } }), TIER)).toEqual([]);
    // An integer is a number the reader takes; a number is no integer.
    const count = (type: string) => reads({ tier: { type } });
    expect(held(count("number"), count("integer"))).toEqual([]);
    expect(held(count("integer"), count("number"))).toHaveLength(1);
    expect(held(TIER, reads({ tier: { type: "string" } }, {}, { nullable: true }))).toEqual(
      ["tierRead may answer null, which the doors' version before does not read: answer it by " +
       "a door of a new name, and drop this one first"]);
  });

  it("names a door whose version before declares no answer, and holds none of a dropped door",
     () => {
    expect(handedHold(() => side({}, [tierRead()]), TIER)).toEqual(
      { problems: [], untyped: [], undeclared: ["tierRead"], unrecorded: [] });
    expect(handedHold(() => side({}, [tierRead()]), side({}, [tierRead()])).undeclared)
      .toEqual([]);
    // The doors' version before calls a dropped door no more, so reads none of its answer.
    expect(held(side({}, [{ ...tierRead({ required: { tier: { type: "string" } },
                                           optional: {} }), dropped: true }]),
                side({}, [tierRead()]))).toEqual([]);
  });
});

describe("the owner's answers each runner protocol a sandbox version speaks reads", () => {
  /** `fileList`, its answer `answer`. */
  const fileList = (answer: Answer): DoorRow =>
    ({ door: "fileList", class: "read", capability: null, args: "none", answer });
  const NEXT: Answer = { required: { entries: { type: "array" }, next: { type: "string" } },
                         optional: {}, unread: ["next"] };
  /** A side whose rows answer `answer` and whose sandbox speaks `runners`. */
  const speaking = (runners: Side["runners"], answer: Answer = NEXT): Before & Side =>
    ({ ...side({}, [fileList(answer)]), runners });
  /** Protocol `n`'s record, its runner reading `fileList` `next` as a string, always. */
  const reads = (n: string) => ({ [n]: { fileList: { required: { next: { type: "string" } },
                                                      optional: {} } } });
  const GONE: Answer = { required: { entries: { type: "array" } }, optional: {} };
  const STEP = (n: string) => `keep answering it until no sandbox version the stage serves, ` +
    `nor this tree's, speaks protocol ${n}`;

  it("refuses an owner that no longer answers a field this tree's protocols read, as they " +
     "read it", () => {
    expect(handedHold(() => null, speaking(reads("5"), GONE)).problems).toEqual(
      [`fileList no longer answers next, which protocol 5's runner reads: ${STEP("5")}`]);
    // An unread field is the doors' alone: the runner's record still holds it.
    expect(handedHold(() => null, speaking(reads("5"))).problems).toEqual([]);
    expect(handedHold(() => null, speaking(reads("5"), {
      required: { entries: { type: "array" } }, optional: { next: { type: ["string", "null"] } },
    })).problems).toEqual([
      `fileList answers next not always, which protocol 5's runner reads as always answered: ` +
      `answer that under a new name, and ${STEP("5")}`,
      `fileList answers next as null, which protocol 5's runner does not read: answer that ` +
      `under a new name, and ${STEP("5")}`]);
    expect(handedHold(() => null, { ...speaking(reads("5")), doors: [] }).problems).toEqual(
      [`the door fileList is gone, which protocol 5's runner reads: ${STEP("5")}`]);
    // Spoken no more, by this tree or the stage's, its reads are no one's.
    expect(handedHold(() => speaking({}), speaking({}, GONE)).problems).toEqual([]);
  });

  it("holds the protocols the serving sandbox speaks, newer ones on a rollback too", () => {
    // The serving sandbox speaks 6, which this (older) tree records nowhere.
    expect(handedHold(() => speaking(reads("6")), speaking(reads("5"), GONE)).problems).toEqual([
      `fileList no longer answers next, which protocol 5's runner reads: ${STEP("5")}`,
      `fileList no longer answers next, which protocol 6's runner reads: ${STEP("6")}`]);
    // A protocol both speak is held once, by this tree's record where the serving one has none.
    const held = handedHold(() => speaking({ 5: null }), speaking(reads("5"), GONE));
    expect(held.problems).toHaveLength(1);
    expect(held.unrecorded).toEqual([]);
  });

  it("names each runner whose reads no record holds", () => {
    expect(handedHold(() => speaking({ 4: null }), speaking(reads("5"))).unrecorded)
      .toEqual(["protocol 4"]);
    expect(handedHold(() => speaking(null), speaking(reads("5"))).unrecorded)
      .toEqual(["the sandbox's version before"]);
    expect(handedHold(() => null, speaking(reads("5"))).unrecorded).toEqual([]);
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

  it("reads the runner protocols its sandbox speaks, each its record's reads, or none", () => {
    const at = { "platform/contract/doors.json": DOORS,
                 "platform/contract/handed.json": "{\"shapes\": {}}" };
    const relayed = { fileList: { required: { next: { type: "string" } }, optional: {} } };
    expect(sideAt("c", files({ ...at, "platform/sandbox/runner/speaks.json": "[4, 5]",
                               "platform/sandbox/runner/protocol.json": JSON.stringify(
                                 { 4: { commands: [] }, 5: { relayed } }) })).runners)
      .toEqual({ 4: null, 5: relayed });
    expect(sideAt("c", files(at)).runners).toBeNull();
  });
});
