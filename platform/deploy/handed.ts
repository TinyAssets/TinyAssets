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
 * parsed; one the parser's version before does not list is a new answer (or one from before
 * the shapes were data), which no caller before it reads. For the door table, the owner takes
 * every door the doors' version before calls (a door the doors call no more is first marked
 * `dropped`), every field its request lists, no request where it took none, an omitted request
 * and an `undefined` field where it took them, and requires no field it did not always hand. A
 * door's request its own module reads (`loose`) is held by that module's shapes here.
 *
 * The one exception is the cutover's: the doors and shapes the version before's manifest
 * (`cutover.json`) names in its `cutover` phase's edits of the two files may go at once, since
 * that change lands after the import window closes (tasks.md §9 step 15), when nothing calls
 * them. The base branch's side is the guard's (`guards/handed.test.ts`); the versions the stage
 * serves are the kit's plan's (`deploy.ts` `rollout`).
 */
import tree from "../contract/handed.json" with { type: "json" };
import table from "../contract/doors.json" with { type: "json" };
import type { Args, DoorArgs, DoorRow } from "../contract/src/doors.ts";
import type { Shape } from "../contract/src/handed.ts";

import { WORKERS } from "./facts.ts";

const HANDED_JSON = "platform/contract/handed.json";
/**
 * The shapes' file before `handed.json` (#5808): the binding's alone, each the doors' to the
 * owner. Read only at a commit that holds no `handed.json`, as the stage served when this
 * file came.
 */
const BINDINGS_JSON = "platform/contract/bindings.json";
const DOORS_JSON = "platform/contract/doors.json";
const CUTOVER_JSON = "platform/cutover.json";

/** What one release hands and takes: its shapes and its door table. */
export interface Side {
  readonly shapes: Readonly<Record<string, Shape>>;
  readonly doors: readonly DoorRow[];
}

/** A release before, as held: its side, and the doors and shapes its cutover manifest takes out. */
export interface Before extends Side {
  readonly cutover: { readonly doors: ReadonlySet<string>; readonly shapes: ReadonlySet<string> };
}

/** This tree's side. */
export const TREE: Side = Object.freeze({ shapes: tree.shapes as Record<string, Shape>,
                                          doors: table.doors as unknown as DoorRow[] });

/**
 * The text of `path` (repo-relative) at `commit`, or null when its tree has no such file;
 * unknown commits throw (`ci/history.ts` `fileAt`).
 */
export type FileAt = (commit: string, path: string) => string | null;

/** The side of the release at `commit`, read from git by `read`; a commit with neither throws. */
export function sideAt(commit: string, read: FileAt): Before {
  const doors = read(commit, DOORS_JSON);
  const handed = read(commit, HANDED_JSON);
  const bindings = handed === null ? read(commit, BINDINGS_JSON) : null;
  if (doors === null || (handed ?? bindings) === null) {
    throw new Error(`${commit} holds no ${doors === null ? DOORS_JSON : HANDED_JSON}: what it ` +
                    "hands and takes is unknown");
  }
  const listed = (JSON.parse((handed ?? bindings)!) as { shapes: Record<string, Shape> }).shapes;
  const shapes = handed !== null ? listed : Object.fromEntries(Object.entries(listed)
    .map(([name, shape]) => [name, { ...shape, from: "doors", to: "owner" }]));
  const manifest = read(commit, CUTOVER_JSON);
  const edits = manifest === null ? [] : (JSON.parse(manifest) as {
    phases: { cutover: { entries: { edit?: string; names?: string[] }[] } } })
    .phases.cutover.entries;
  const cut = (path: string) => new Set(edits.flatMap((entry) => entry.edit === path
    ? (entry.names ?? []).map((name) => name.replace(/^"(.*)"$/, "$1")) : []));
  return { shapes, doors: (JSON.parse(doors) as { doors: DoorRow[] }).doors,
           cutover: { doors: cut(DOORS_JSON),
                      shapes: cut(handed !== null ? HANDED_JSON : BINDINGS_JSON) } };
}

/** The fields one side hands, always and sometimes, and the ones it takes and drops. */
interface Fields {
  readonly required: readonly string[];
  readonly always: readonly string[];
  readonly optional: readonly string[];
  readonly dropped: readonly string[];
}

/** What a problem says of a field, by which of the two is the version before. */
interface Words {
  /** A field handed that the taker does not take. */
  readonly untaken: (field: string) => string;
  /** A field the taker requires that the hander does not always hand. */
  readonly unhanded: (field: string) => string;
}

/** Each way `taken` refuses what `handed` hands, in `words`. */
function fieldProblems(handed: Fields, taken: Fields, words: Words): string[] {
  const takes = new Set([...taken.required, ...taken.always, ...taken.optional, ...taken.dropped]);
  const always = [...handed.required, ...handed.always];
  return [
    ...[...always, ...handed.optional].filter((field) => !takes.has(field)).map(words.untaken),
    ...taken.required.filter((field) => !always.includes(field)).map(words.unhanded),
  ];
}

function shapeFields(shape: Shape): Fields {
  return { required: shape.required, always: shape.always ?? [], optional: shape.optional,
           dropped: shape.dropped ?? [] };
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
    ? fieldProblems(shapeFields(then), shapeFields(now), {
      untaken: (field) => `${what} no longer takes ${field}, which ${before(now.from)} hands: ` +
        "drop it first (taken and left out), and take it out once the stage serves no " +
        `${now.from} version that hands it`,
      unhanded: (field) => `${what} requires ${field}, which ${before(now.from)} does not ` +
        `always hand: take it as always first, and require it once the stage serves no ${
          now.from} version that does not` })
    : fieldProblems(shapeFields(now), shapeFields(then), {
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

function argsFields(args: Args): Fields {
  return { required: Object.keys(args.required), always: [], optional: Object.keys(args.optional),
           dropped: args.dropped ?? [] };
}

/** Each way the tree's arguments of `door` refuse what the doors' version before hands it. */
function argsProblems(door: string, then: DoorArgs, now: DoorArgs): string[] {
  if (then === "loose" || now === "loose" || now === "none") {
    return [];
  }
  const sides = requestSides(now);
  const paired = (side: string): [string, Args][] => Object.entries(sides)
    .filter(([key]) => side === "" || key === "" || key === side)
    .map(([key, args]) => [side || key, args]);
  const named = (side: string) => side === "" ? door : `${door} (${side})`;
  if (then === "none") {
    return paired("").filter(([, args]) => args.omitted !== true
                             || Object.keys(args.required).length > 0)
      .map(([side]) => `${named(side)} takes a request now, which the doors' version before ` +
                       "calls it without: take none, or an omitted one, until the stage serves " +
                       "no doors version that calls it without");
  }
  return Object.entries(requestSides(then)).flatMap(([side, was]) =>
    paired(side).flatMap(([key, args]) => [
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
    ]));
}

/** Each way the tree's owner refuses what the doors' version before `then` calls it with. */
function doorProblems(then: Before, now: Side): string[] {
  const rows = new Map(now.doors.map((row) => [row.door, row]));
  return then.doors.flatMap((was) => {
    const row = rows.get(was.door);
    return row !== undefined ? argsProblems(was.door, was.args, row.args)
      : was.dropped === true || then.cutover.doors.has(was.door) ? []
        : [`the door ${was.door} is gone, which the doors' version before calls: drop it ` +
           "first (the owner takes it, the doors call it no more), and take it out once the " +
           "stage serves no doors version that calls it"];
  });
}

// --- The whole hold --------------------------------------------------------------------------

/**
 * Each way `now` refuses, across a deploy, what the versions before hand it or it hands them:
 * `then(worker)` is `worker`'s version before, null where none serves (nothing of it talks).
 */
export function handedProblems(then: (worker: string) => Before | null, now: Side): string[] {
  if (WORKERS.indexOf("owner") > WORKERS.indexOf("doors")) {
    throw new Error("the door table is held as the doors' requests to an owner deployed " +
                    "before them, and the deploy order deploys the doors first");
  }
  return WORKERS.flatMap((worker) => {
    const side = then(worker);
    if (side === null) {
      return [];
    }
    const listed = Object.entries(now.shapes).filter(([, shape]) => later(shape) === worker)
      .flatMap(([name, shape]) => side.shapes[name] === undefined ? []
        : shapeProblems(name, shape, side.shapes[name]));
    const gone = Object.entries(side.shapes).filter(([name, shape]) =>
      now.shapes[name] === undefined && later(shape) === worker && request(shape)
      && !side.cutover.shapes.has(name))
      .map(([name, shape]) => `the shape ${name} is gone, which ${before(shape.from)} hands`);
    return [...listed, ...gone, ...worker === "doors" ? doorProblems(side, now) : []];
  });
}
