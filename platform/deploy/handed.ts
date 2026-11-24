/**
 * What each Worker hands another over RPC, held to the deploy order. The kit deploys each
 * Worker after the ones it binds (`facts.ts` `WORKERS`), so from the earlier one's 100% until
 * the later one's (its 0% soak and smoke, or a run stopped between), the later one's version
 * before talks to the earlier one's new version, and a rollback takes the later one back first.
 * Overrides reach no RPC, so nothing else would see either refuse the other.
 *
 * What is handed is data at every commit, so the version before is read from git, never run:
 * the shapes (`contract/handed.json`, each its sender and its parser) and the door table's
 * requests (`contract/doors.json` `args`, which the doors hand the owner). Where a shape's
 * parser deploys first (a request), the new parser takes every field the sender's version before
 * hands, required or optional, and requires none it does not always hand; where its sender
 * deploys first (an answer), the parser's version before takes every field the new sender hands,
 * and requires none it does not always hand. A field goes in steps (`dropped`: taken, not
 * handed; `always`: handed every time, taken as if optional), each once the versions the stage
 * serves no longer need the one before. A shape the sender's version before hands is still
 * parsed, so a shape its sender hands no more is first marked so (`handed: false`: parsed, not
 * handed), and goes once the stage serves no sender version whose shapes hand it; one the
 * parser's version before does not list is a new answer (or one from before the shapes were
 * data), which no caller before it reads. For the door table, the owner takes
 * every door the doors' version before calls (a door the doors call no more is first marked
 * `dropped`), every field its request lists, each as every JSON type its schema names
 * (`fieldTypes`; a type the schema names no more is first listed in `droppedTypes`), no request
 * where it took none, an omitted request and an `undefined` field where it took them, and
 * requires no field it did not always hand. A field the version before types not, and the owner
 * does, is held by its name alone, and the plan names it. A door's request its own module reads
 * (`loose`) is held by that module's shapes here. The shapes declare no types: their parsers
 * check them in code. The table's answers (`answer`: what the doors' code and the app they
 * serve read of the owner's) are held the other way: the owner answers every field the doors'
 * version before reads (a field they read no more is first marked `unread`: answered, not
 * read), always where it read it so, as JSON types it reads, and null only where it read
 * null; a door whose version before declares no answer is held by nothing, and the plan names
 * it. The runner reads the owner's answers too, as the doors relay them, whatever doors serve:
 * a live runner is any protocol a sandbox Worker the stage serves speaks, or this tree's does
 * (`sandbox/runner/speaks.json`), so the owner answers each one's record of what it reads
 * (`sandbox/runner/protocol.json` `relayed`) the same way; a protocol recorded nowhere, or a
 * sandbox version that records none it speaks, is held by nothing, and the plan names it. The
 * kit's and the tests' reads are not a pairing, and no row holds them.
 *
 * The one exception is the cutover's: the doors and shapes the version before's manifest
 * (`cutover.json`) names in its `cutover` phase's edits of the two files may go at once, since
 * that change lands after the import window closes (tasks.md §9 step 15), when nothing calls
 * them. The base branch's side is the guard's (`guards/handed.test.ts`); the versions the stage
 * serves are the kit's plan's (`deploy.ts` `rollout`).
 */
import tree from "../contract/handed.json" with { type: "json" };
import table from "../contract/doors.json" with { type: "json" };
import protocols from "../sandbox/runner/protocol.json" with { type: "json" };
import speaks from "../sandbox/runner/speaks.json" with { type: "json" };
import { type Answer, type Args, type DoorArgs, type DoorRow, fieldTypes, takesType }
  from "../contract/src/doors.ts";
import { type Listed, type Shape, takes } from "../contract/src/handed.ts";

import { WORKERS } from "./facts.ts";

const HANDED_JSON = "platform/contract/handed.json";
const DOORS_JSON = "platform/contract/doors.json";
const CUTOVER_JSON = "platform/cutover.json";
const PROTOCOL_JSON = "platform/sandbox/runner/protocol.json";
const SPEAKS_JSON = "platform/sandbox/runner/speaks.json";

/** Each runner protocol's record of the owner's answers its runner reads, by door. */
type Relayed = Readonly<Record<string, Answer>>;

/**
 * The runner protocols a release's sandbox Worker speaks, by number, each its record of what
 * its runner reads (`relayed`), null where the release records none; null where the release
 * records none it speaks.
 */
export type Spoken = Readonly<Record<string, Relayed | null>> | null;

/** What one release hands and takes: its shapes, its door table and the runners it speaks. */
export interface Side {
  readonly shapes: Readonly<Record<string, Shape>>;
  readonly doors: readonly DoorRow[];
  readonly runners: Spoken;
}

/** A release before, as held: its side, and the doors and shapes its cutover manifest takes out. */
export interface Before extends Side {
  readonly cutover: { readonly doors: ReadonlySet<string>; readonly shapes: ReadonlySet<string> };
}

/** The protocols `speaks` names, each `records`' record of what its runner reads, or null. */
function spoken(speaks: readonly number[],
                records: Readonly<Record<string, { readonly relayed?: Relayed }>>): Spoken {
  return Object.fromEntries(speaks.map((protocol) =>
    [String(protocol), records[String(protocol)]?.relayed ?? null]));
}

/** This tree's side. */
export const TREE: Side = Object.freeze({
  shapes: tree.shapes as Record<string, Shape>, doors: table.doors as unknown as DoorRow[],
  runners: spoken(speaks, protocols as Readonly<Record<string, { relayed?: Relayed }>>),
});

/**
 * The text of `path` (repo-relative) at `commit`, or null when its tree has no such file;
 * unknown commits throw (`ci/history.ts` `fileAt`).
 */
export type FileAt = (commit: string, path: string) => string | null;

/** The side of the release at `commit`, read from git by `read`; a commit without both throws. */
export function sideAt(commit: string, read: FileAt): Before {
  const doors = read(commit, DOORS_JSON);
  const handed = read(commit, HANDED_JSON);
  if (doors === null || handed === null) {
    throw new Error(`${commit} holds no ${doors === null ? DOORS_JSON : HANDED_JSON}: what it ` +
                    "hands and takes is unknown");
  }
  const manifest = read(commit, CUTOVER_JSON);
  const edits = manifest === null ? [] : (JSON.parse(manifest) as {
    phases: { cutover: { entries: { edit?: string; names?: string[] }[] } } })
    .phases.cutover.entries;
  const cut = (path: string) => new Set(edits.flatMap((entry) => entry.edit === path
    ? (entry.names ?? []).map((name) => name.replace(/^"(.*)"$/, "$1")) : []));
  const speaking = read(commit, SPEAKS_JSON);
  const records = read(commit, PROTOCOL_JSON);
  return { shapes: (JSON.parse(handed) as { shapes: Record<string, Shape> }).shapes,
           doors: (JSON.parse(doors) as { doors: DoorRow[] }).doors,
           runners: speaking === null || records === null ? null
             : spoken(JSON.parse(speaking) as number[],
                      JSON.parse(records) as Record<string, { relayed?: Relayed }>),
           cutover: { doors: cut(DOORS_JSON), shapes: cut(HANDED_JSON) } };
}

/** What a problem says of a field, by which of the two is the version before. */
interface Words {
  /** A field handed that the taker does not take. */
  readonly untaken: (field: string) => string;
  /** A field the taker requires that the hander does not always hand. */
  readonly unhanded: (field: string) => string;
}

/** Each way a parser of `taken` refuses what `handed` hands, in `words`. */
function fieldProblems(handed: Listed, taken: Listed, words: Words): string[] {
  const [requires, optional] = takes(taken);
  const took = new Set([...requires, ...optional]);
  const always = [...handed.required, ...handed.always ?? []];
  return [
    ...[...always, ...handed.optional].filter((field) => !took.has(field)).map(words.untaken),
    ...requires.filter((field) => !always.includes(field)).map(words.unhanded),
  ];
}

/** `worker`'s version before, as a problem names it. */
function before(worker: string): string {
  return `the ${worker}${worker.endsWith("s") ? "'" : "'s"} version before`;
}

/** The later of a shape's two Workers in the deploy order: the one whose version before talks. */
function later(shape: Shape): string {
  return WORKERS.indexOf(shape.from) > WORKERS.indexOf(shape.to) ? shape.from : shape.to;
}

/** A request: the shape's parser deploys before its sender. */
function request(shape: Shape): boolean {
  return later(shape) === shape.from;
}

/** Each way the shape `name` refuses across the deploy, `then` its version before's listing. */
function shapeProblems(name: string, now: Shape, then: Shape): string[] {
  const what = `the shape ${name}`;
  return request(now)
    ? fieldProblems(then, now, {
      untaken: (field) => `${what} no longer takes ${field}, which ${before(now.from)} hands: ` +
        "drop it first (taken and left out), and take it out once the stage serves no " +
        `${now.from} version that hands it`,
      unhanded: (field) => `${what} requires ${field}, which ${before(now.from)} does not ` +
        `always hand: take it as always first, and require it once the stage serves no ${
          now.from} version that does not` })
    : fieldProblems(now, then, {
      untaken: (field) => `${what} hands ${field}, which ${before(now.to)} does not take: ` +
        "take it first (dropped: taken, not yet handed), and hand it once the stage serves no " +
        `${now.to} version that refuses it`,
      unhanded: (field) => `${what} does not always hand ${field}, which ${before(now.to)} ` +
        `requires: hand it always first, and stop once the stage serves no ${now.to} version ` +
        "that requires it" });
}

// --- The door table: the doors' requests to the owner -------------------------------------

/** A door's request as one list of fields, by who asks where its row splits them. */
function requestSides(args: Args | { readonly owner: Args; readonly outside: Args }):
    Readonly<Record<string, Args>> {
  return "refused" in args ? { "": args } : { owner: args.owner, outside: args.outside };
}

function argsFields(args: Args): Listed {
  return { required: Object.keys(args.required), optional: Object.keys(args.optional),
           dropped: args.dropped ?? [] };
}

/** The field `name` of `args`'s schema, where it lists one. */
function schemaOf(args: Args, name: string): unknown {
  return Object.hasOwn(args.required, name) ? args.required[name]
    : Object.hasOwn(args.optional, name) ? args.optional[name] : undefined;
}

/**
 * A hold across a deploy: each way it refuses, each door field held by name alone, each door
 * whose answer nothing holds, and each runner whose reads nothing holds.
 */
export interface Hold {
  readonly problems: readonly string[];
  /**
   * Each door field, as `<door> <field>`, the tree's owner types and the doors' version before
   * does not: what was handed is unknown, so only its name is held.
   */
  readonly untyped: readonly string[];
  /**
   * Each door whose answer the tree's row declares and the doors' version before's does not:
   * what it reads is unknown, so nothing holds it.
   */
  readonly undeclared: readonly string[];
  /**
   * Each runner whose reads of the owner's answers are unknown, so nothing holds them: a
   * protocol a sandbox version speaks that no record of it names them for (`protocol <n>`),
   * or the sandbox's version before, which records none it speaks.
   */
  readonly unrecorded: readonly string[];
}

/** No problem, and nothing held short. */
const HELD: Hold = Object.freeze({ problems: [], untyped: [], undeclared: [], unrecorded: [] });

/** The holds `holds` as one. */
function joined(holds: readonly Hold[]): Hold {
  return { problems: holds.flatMap((each) => each.problems),
           untyped: holds.flatMap((each) => each.untyped),
           undeclared: holds.flatMap((each) => each.undeclared),
           unrecorded: holds.flatMap((each) => each.unrecorded) };
}

/** A reader of the owner's answers, as a refusal names it, and the step a field it reads takes. */
interface Reader {
  readonly name: string;
  readonly step: string;
}

/** The doors' version before, which reads a field it marks `unread` no more. */
const DOORS_BEFORE: Reader = Object.freeze({
  name: "the doors' version before",
  step: "mark it unread first (answered, read no more), and take it out once the stage serves " +
    "no doors version that reads it",
});

/** A runner of `protocol`, which reads its record's fields while a sandbox version speaks it. */
function runner(protocol: string): Reader {
  return { name: `protocol ${protocol}'s runner`,
           step: "keep answering it until no sandbox version the stage serves, nor this " +
             `tree's, speaks protocol ${protocol}` };
}

/**
 * Each way the tree's owner answers `door` short of what `reader` reads of it (`was`), the
 * tree's answer `now`: every field it reads (but those it marks `unread`) still answered,
 * always where it read it so, as types it reads, and null only where it read null. A doors
 * version before that declares no answer is held by nothing, and named.
 */
function answerHold(door: string, was: Answer | undefined, now: Answer | undefined,
                    reader: Reader): Hold {
  if (was === undefined) {
    return now === undefined ? HELD : { ...HELD, undeclared: [door] };
  }
  const problems: string[] = [];
  const { required, optional } = now ?? { required: {}, optional: {} };
  for (const [name, schema] of [...Object.entries(was.required),
                                ...Object.entries(was.optional)]) {
    if ((was.unread ?? []).includes(name)) {
      continue;
    }
    const held = Object.hasOwn(required, name) ? required[name]
      : Object.hasOwn(optional, name) ? optional[name] : undefined;
    if (held === undefined) {
      problems.push(`${door} no longer answers ${name}, which ${reader.name} reads: ${
        reader.step}`);
      continue;
    }
    if (Object.hasOwn(was.required, name) && !Object.hasOwn(required, name)) {
      problems.push(`${door} answers ${name} not always, which ${reader.name} reads as always ` +
                    `answered: answer that under a new name, and ${reader.step}`);
    }
    const read = fieldTypes(name, schema) ?? [];
    const refused = (fieldTypes(name, held) ?? []).filter((type) => !takesType(read, type));
    if (refused.length > 0) {
      problems.push(`${door} answers ${name} as ${refused.join(" or ")}, which ${reader.name} ` +
                    `does not read: answer that under a new name, and ${reader.step}`);
    }
  }
  if (now?.nullable === true && was.nullable !== true) {
    problems.push(`${door} may answer null, which ${reader.name} does not read: answer it by a ` +
                  "door of a new name, and drop this one first");
  }
  return { ...HELD, problems };
}

/**
 * Each type of a field the doors' version before hands (`was`) that the tree's owner takes
 * (`now`) no more, and each field it types that the version before does not, which only its
 * name holds: of the request `named`.
 */
function typeHold(named: string, was: Args, now: Args): Hold {
  const problems: string[] = [];
  const untyped: string[] = [];
  for (const name of [...Object.keys(was.required), ...Object.keys(was.optional)]) {
    const schema = schemaOf(now, name);
    const taken = schema === undefined ? null : fieldTypes(name, schema);
    if (taken === null) {
      continue;
    }
    const handed = fieldTypes(name, schemaOf(was, name));
    if (handed === null) {
      untyped.push(`${named} ${name}`);
      continue;
    }
    const all = [...taken, ...now.droppedTypes?.[name] ?? []];
    const refused = handed.filter((type) => !takesType(all, type));
    if (refused.length > 0) {
      problems.push(`${named} no longer takes ${name} as ${refused.join(" or ")}, which the ` +
        "doors' version before hands: take it as that too first (droppedTypes: taken, no " +
        "longer handed), and take it out once the stage serves no doors version that hands it");
    }
  }
  return { ...HELD, problems, untyped };
}

/** Each way the tree's arguments of `door` refuse what the doors' version before hands it. */
function argsProblems(door: string, then: DoorArgs, now: DoorArgs): Hold {
  if (then === "loose" || now === "loose" || now === "none") {
    return HELD;
  }
  const sides = requestSides(now);
  const paired = (side: string): [string, Args][] => Object.entries(sides)
    .filter(([key]) => side === "" || key === "" || key === side)
    .map(([key, args]) => [side || key, args]);
  const named = (side: string) => side === "" ? door : `${door} (${side})`;
  if (then === "none") {
    return { ...HELD, problems: paired("").filter(([, args]) => args.omitted !== true
                                         || Object.keys(args.required).length > 0)
      .map(([side]) => `${named(side)} takes a request now, which the doors' version before ` +
                       "calls it without: take none, or an omitted one, until the stage serves " +
                       "no doors version that calls it without") };
  }
  const pairs = Object.entries(requestSides(then)).flatMap(([side, was]) =>
    paired(side).map(([key, args]) => ({ key, was, args,
                                         types: typeHold(named(key), was, args) })));
  return { ...HELD, untyped: pairs.flatMap(({ types }) => types.untyped),
           problems: pairs.flatMap(({ key, was, args, types }) => [
      ...fieldProblems(argsFields(was), argsFields(args), {
        untaken: (field) => `${named(key)} no longer takes ${field}, which the doors' version ` +
          "before hands: drop it first (taken and left out), and take it out once the stage " +
          "serves no doors version that hands it",
        unhanded: (field) => `${named(key)} requires ${field}, which the doors' version before ` +
          "does not always hand: take it as optional until the stage serves no doors version " +
          "that does not" }),
      ...was.omitted === true && args.omitted !== true
        ? [`${named(key)} refuses no request, which the doors' version before may hand`] : [],
      ...was.undefinedAllowed === true && args.undefinedAllowed !== true
        ? [`${named(key)} refuses a field sent undefined, which the doors' version before ` +
           "may hand"] : [],
      ...types.problems,
    ]) };
}

/**
 * Each way the tree's owner refuses what the doors' version before `then` calls it with, or
 * answers it short of what it reads.
 */
function doorProblems(then: Before, now: Side): Hold {
  const rows = new Map(now.doors.map((row) => [row.door, row]));
  return joined(then.doors.map((was): Hold => {
    const row = rows.get(was.door);
    if (row === undefined) {
      return { ...HELD, problems: was.dropped === true || then.cutover.doors.has(was.door) ? []
        : [`the door ${was.door} is gone, which the doors' version before calls: drop it ` +
           "first (the owner takes it, the doors call it no more), and take it out once the " +
           "stage serves no doors version that calls it"] };
    }
    return joined([argsProblems(was.door, was.args, row.args), was.dropped === true ? HELD
      : answerHold(was.door, was.answer, row.answer, DOORS_BEFORE)]);
  }));
}

/**
 * Each way the tree's owner answers short of a runner that may be talking to it: of each
 * protocol the serving sandbox version (`then`) or this tree's speaks, its record (a record
 * is frozen, so any commit's that has one; this tree's first).
 */
function runnerProblems(then: Side | null, now: Side): Hold {
  const rows = new Map(now.doors.map((row) => [row.door, row]));
  const protocols = new Set([...Object.keys(then?.runners ?? {}),
                             ...Object.keys(now.runners ?? {})]);
  return joined([
    ...then !== null && then.runners === null
      ? [{ ...HELD, unrecorded: ["the sandbox's version before"] }] : [],
    ...[...protocols].sort().map((protocol): Hold => {
      const record = now.runners?.[protocol] ?? then?.runners?.[protocol] ?? null;
      if (record === null) {
        return { ...HELD, unrecorded: [`protocol ${protocol}`] };
      }
      return joined(Object.entries(record).map(([door, was]) => {
        const row = rows.get(door);
        return row === undefined
          ? { ...HELD, problems: [`the door ${door} is gone, which ${runner(protocol).name} ` +
                                  `reads: ${runner(protocol).step}`] }
          : answerHold(door, was, row.answer, runner(protocol));
      }));
    }),
  ]);
}

// --- The whole hold --------------------------------------------------------------------------

/**
 * Each way `now` refuses, across a deploy, what the versions before hand it or it hands them:
 * `then(worker)` is `worker`'s version before, null where none serves (nothing of it talks).
 */
export function handedHold(then: (worker: string) => Before | null, now: Side): Hold {
  if (WORKERS.indexOf("owner") > WORKERS.indexOf("doors")) {
    throw new Error("the door table is held as the doors' requests to an owner deployed " +
                    "before them, and the deploy order deploys the doors first");
  }
  const doors = then("doors");
  const table = doors === null ? HELD : doorProblems(doors, now);
  const runners = runnerProblems(then("sandbox"), now);
  const problems = WORKERS.flatMap((worker) => {
    const side = then(worker);
    if (side === null) {
      return [];
    }
    const listed = Object.entries(now.shapes).filter(([, shape]) => later(shape) === worker)
      .flatMap(([name, shape]) => side.shapes[name] === undefined ? []
        : shapeProblems(name, shape, side.shapes[name]));
    const gone = Object.entries(side.shapes).filter(([name, shape]) =>
      now.shapes[name] === undefined && later(shape) === worker && request(shape)
      && shape.handed !== false && !side.cutover.shapes.has(name))
      .map(([name, shape]) => `the shape ${name} is gone, which ${before(shape.from)} hands: ` +
        "mark it handed: false first (parsed, handed no more), and take it out once the stage " +
        `serves no ${shape.from} version whose shapes hand it`);
    return [...listed, ...gone, ...worker === "doors" ? table.problems : []];
  });
  return { ...joined([table, runners]), problems: [...problems, ...runners.problems] };
}
