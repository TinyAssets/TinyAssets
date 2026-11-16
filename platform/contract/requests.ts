/**
 * The door table (`doors.json`, read by `doorTable()`) as types, one of `npm run generate`'s
 * generators, which `ci/guards/generated.test.ts` holds current. A JSON module's types cannot
 * name them: its strings widen to `string`, so no row can be picked by its door. Node runs
 * this file as it is (type stripping). It writes three files:
 *
 * - `contract/src/requests.ts`: each door's request whose row lists its fields, so a door's
 *   fields are listed once, in its row (`args`), and `tsc` holds the doors' request literals
 *   to them (`OwnerDoors`, `contract/src/owner-doors.ts`). Each row with a field list is one entry:
 *   its required fields, then its optional ones, as the runner holds them (`fields`). A
 *   field's value is `unknown`: the schemas are its tool's JSON arguments, and the doors send
 *   some as other values (`fileWrite`'s content as bytes), so its handler checks each value.
 *   A row by who asks (`registerAgent`) is either list. A row that takes no request (`none`),
 *   or one its module reads whole (`loose`), has no entry.
 * - `owner/src/door-map.ts`: the owner object's typed handler map (untangle B4). Each row's
 *   module, caller and gate (`DoorRows`), which type its handler (`owner-do.ts`, `DoorsOf`),
 *   and each door as the object's method (`DoorMethods`), which the object's loop defines on
 *   its prototype from the row at module load: its binding unless its caller is `none`, its
 *   request unless it takes none (optional where the row says an omitted one is empty), its
 *   request and answer its handler's, and its doc the row's tool description.
 * - `contract/src/owner-doors.ts`: the owner object's doors as the doors call them
 *   (`OwnerDoors`, `contract/src/owner.ts` says what each part means): each door its row's,
 *   its binding but on an unbound door (`UNBOUND_DOORS`), no request, an optional one, its
 *   row's fields (`DoorRequest`) or a loose door's own parameters (`DoorLoose`), and the
 *   answer the doors read (`DoorAnswer`), its doc the row's tool description.
 */
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { type Args, type DoorRow, doorTable, UNBOUND_DOORS } from "./src/doors.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
/** The files this writes. */
const REQUESTS_FILE = join(HERE, "src", "requests.ts");
const MAP_FILE = join(HERE, "..", "owner", "src", "door-map.ts");
const DOORS_FILE = join(HERE, "src", "owner-doors.ts");

/** The widest line the platform's sources keep. */
const WIDTH = 100;

/** A list of field names as a union of string literals, `never` when empty. */
function names(door: string, fields: Readonly<Record<string, unknown>>): string {
  const listed = Object.keys(fields);
  for (const name of listed) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
      throw new Error(`${door}: a field ${name} that is no identifier`);
    }
  }
  return listed.length === 0 ? "never" : listed.map((name) => `"${name}"`).join(" | ");
}

/** One field list as its `DoorFields` type. */
function fieldsType(door: string, args: Args): string {
  const optional = names(door, args.optional);
  return `DoorFields<${names(door, args.required)}${optional === "never" ? "" : `, ${optional}`}>`;
}

/**
 * `lead` and `type` on one line when they fit, else `type` on the next, indented, its
 * unions broken where a line would pass the width.
 */
function entry(lead: string, type: string): string {
  if (`${lead} ${type};`.length <= WIDTH) {
    return `${lead} ${type};`;
  }
  const lines = ["   "];
  for (const [index, part] of `${type};`.split(" | ").entries()) {
    const piece = `${index === 0 ? "" : " |"} ${part}`;
    if (lines.at(-1)!.length + piece.length > WIDTH) {
      lines.push(`      | ${part}`);
    } else {
      lines[lines.length - 1] += piece;
    }
  }
  return [lead, ...lines].join("\n");
}

/** The source of `contract/src/requests.ts`, from `rows`. */
function requestsSource(rows: readonly DoorRow[]): string {
  const entries = rows.flatMap((row) => {
    const args = row.args;
    if (args === "none" || args === "loose") {
      return [];
    }
    const lead = `  readonly ${row.door}:`;
    if ("owner" in args) {
      return [`${lead}\n${[args.owner, args.outside]
        .map((part) => entry("    |", fieldsType(row.door, part)).replace(/;$/, ""))
        .join("\n")};`];
    }
    return [entry(lead, fieldsType(row.door, args))];
  });
  return [
    "/**",
    " * Each owner door's request whose row lists its fields, by door: written by",
    " * `npm run generate` from the door table (`doors.json`), which says what each means.",
    " * Edit the table, never this file.",
    " */",
    "",
    "/** A request of exactly the `Required` fields and any of the `Optional` ones. */",
    "export type DoorFields<Required extends string, Optional extends string = never> =",
    "  { readonly [Field in Required]: unknown } & { readonly [Field in Optional]?: unknown };",
    "",
    "export interface DoorRequests {",
    ...entries,
    "}",
    "",
  ].join("\n");
}

/** `text` as a doc comment at `indent`, its words wrapped at the width. */
function doc(door: string, text: string, indent: string): string[] {
  if (text.includes("*/")) {
    throw new Error(`${door}: a tool description that ends a comment`);
  }
  const lines = [`${indent} *`];
  for (const word of text.split(/\s+/).filter((each) => each !== "")) {
    if (lines.at(-1)!.length + 1 + word.length > WIDTH && lines.at(-1) !== `${indent} *`) {
      lines.push(`${indent} *`);
    }
    lines[lines.length - 1] += ` ${word}`;
  }
  return [`${indent}/**`, ...lines, `${indent} */`];
}

/** One door as the object's method: its binding, its request, its answer, its tool's doc. */
function method(row: DoorRow): string[] {
  const parts = [
    ...row.caller === "none" ? [] : ["binding: Binding"],
    ...row.args === "none" ? []
      : [`request${row.args !== "loose" && "omitted" in row.args ? "?" : ""}: ` +
         `RequestIn<H, "${row.door}">`],
  ];
  const line = `  ${row.door}(${parts.join(", ")}): Promise<AnswerIn<H, "${row.door}">>;`;
  return [...row.tool === undefined ? [] : doc(row.door, row.tool.description, "  "),
          ...line.length <= WIDTH ? [line]
            : [`  ${row.door}(${parts.join(", ")}):`, `    Promise<AnswerIn<H, "${row.door}">>;`]];
}

/** The source of `owner/src/door-map.ts`, from `rows`. */
function mapSource(rows: readonly DoorRow[]): string {
  return [
    "/**",
    " * The owner object's typed handler map, by door: written by `npm run generate` from the",
    " * door table (`contract/doors.json`), which says what each means. Edit the table, never",
    " * this file.",
    " */",
    "import type { Binding } from \"../../contract/src/binding\";",
    "",
    "/** Each door's module (its handler's), caller and gate, as its row names them. */",
    "export interface DoorRows {",
    ...rows.flatMap((row) => [
      `  readonly ${row.door}:`,
      `    { readonly module: "${row.handler}"; readonly caller: "${row.caller}"; ` +
        `readonly gate: "${row.gate}" };`]),
    "}",
    "",
    "/** The handler of `door` in a map of modules' handlers `H`, by its row's module. */",
    "type HandlerIn<H, D extends keyof DoorRows> = DoorRows[D][\"module\"] extends infer M",
    "  ? M extends keyof H ? D extends keyof H[M] ? H[M][D] : never : never : never;",
    "/** The request a door's handler in `H` takes (its call's `request`). */",
    "export type RequestIn<H, D extends keyof DoorRows> =",
    "  HandlerIn<H, D> extends (o: never, call: infer C) => unknown",
    "    ? C extends { readonly request: infer R } ? R : never : never;",
    "/** What a door's handler in `H` answers. */",
    "export type AnswerIn<H, D extends keyof DoorRows> =",
    "  HandlerIn<H, D> extends (...args: never) => infer T ? Awaited<T> : never;",
    "",
    "/** Each door as the owner object's method, typed by its handler in `H`. */",
    "export interface DoorMethods<H> {",
    ...rows.flatMap(method),
    "}",
    "",
  ].join("\n");
}

/** One door as the doors call it: its binding, its request, its answer, its tool's doc. */
function door(row: DoorRow): string[] {
  const request = row.args === "none" ? []
    : row.args === "loose" ? [`...request: DoorLoose<"${row.door}">`]
      : [`request${"omitted" in row.args ? "?" : ""}: DoorRequest<"${row.door}">`];
  const parts = [...UNBOUND_DOORS.includes(row.door) ? [] : ["binding: Binding"], ...request];
  const answer = `Promise<DoorAnswer<"${row.door}">>;`;
  const line = `  ${row.door}(${parts.join(", ")}): ${answer}`;
  return [...row.tool === undefined ? [] : doc(row.door, row.tool.description, "  "),
          ...line.length <= WIDTH ? [line]
            : [`  ${row.door}(${parts.join(", ")}):`, `    ${answer}`]];
}

/** The source of `contract/src/owner-doors.ts`, from `rows`. */
function doorsSource(rows: readonly DoorRow[]): string {
  return [
    "/**",
    " * The owner object's doors as the doors call them: written by `npm run generate` from the",
    " * door table (`doors.json`), which says what each means, and `DoorTypes` (`owner.ts`),",
    " * which says what the table cannot. Edit those, never this file.",
    " */",
    "import type { Binding } from \"./binding.ts\";",
    "import type { DoorAnswer, DoorLoose, DoorRequest, DoorTypes } from \"./owner.ts\";",
    "import type { DoorRequests } from \"./requests.ts\";",
    "",
    "/** The owner's object, as the doors reach it (`binder.ts` alone looks one up by name). */",
    "export interface OwnerDoors extends Rpc.DurableObjectBranded {",
    ...rows.flatMap(door),
    "}",
    "",
    "/** The doors whose rows leave their parameters to their modules. */",
    entry("type Loose =",
          rows.filter((row) => row.args === "loose").map((row) => `"${row.door}"`).join(" | ")),
    "/** What `DoorTypes` may say of a door, by its row. */",
    "type Parts<D> = \"answer\" | (D extends keyof DoorRequests ? \"values\" | \"handed\"",
    "  : D extends Loose ? \"loose\" : never);",
    "/** The fields a door's row lists (any of a choice's). */",
    "type Fields<D> = D extends keyof DoorRequests",
    "  ? DoorRequests[D] extends infer R ? R extends unknown ? keyof R : never : never : never;",
    "/** What an entry of `DoorTypes` says that its row leaves no room for. */",
    "type Stray<D extends keyof DoorTypes> = Exclude<keyof DoorTypes[D], Parts<D>>",
    "  | (DoorTypes[D] extends { readonly values: infer V }",
    "    ? Exclude<keyof V, Fields<D>> : never);",
    "",
    "/**",
    " * The doors `DoorTypes` names: `tsc` refuses one that is no door of the table, or one whose",
    " * entry says what its row leaves no room for (a part, or a field its row does not list).",
    " */",
    "type Doors<D extends keyof OwnerDoors> = D;",
    "type None<D extends never> = D;",
    "type Strays = { [D in keyof DoorTypes]: [Stray<D>] extends [never] ? never : D };",
    "export type TypedDoors = Doors<keyof DoorTypes> | None<Strays[keyof DoorTypes]>;",
    "",
  ].join("\n");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const rows = doorTable();
  writeFileSync(REQUESTS_FILE, requestsSource(rows));
  writeFileSync(MAP_FILE, mapSource(rows));
  writeFileSync(DOORS_FILE, doorsSource(rows));
  console.log("platform/contract/src/requests.ts, platform/owner/src/door-map.ts, " +
              "platform/contract/src/owner-doors.ts");
}
