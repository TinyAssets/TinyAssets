/**
 * The door table (`doors.json`, read by `doorTable()`) as types, one of `npm run generate`'s
 * generators, which `ci/guards/generated.test.ts` holds current. A JSON module's types cannot
 * name them: its strings widen to `string`, so no row can be picked by its door. Node runs
 * this file as it is (type stripping). It writes two files, from the contract's half of the
 * table alone (the owner's typed handler map, from both halves, is `owner/write-door-map.ts`'s):
 *
 * - `contract/src/requests.ts`: each door's request whose row lists its fields, so a door's
 *   fields are listed once, in its row (`args`), and `tsc` holds the doors' request literals
 *   to them (`OwnerDoors`, `contract/src/owner-doors.ts`). Each row with a field list is one entry:
 *   its required fields, then its optional ones, as the runner holds them (`fields`). A
 *   field's value is `unknown`: the schemas are its tool's JSON arguments, and the doors send
 *   some as other values (`fileWrite`'s content as bytes), so its handler checks each value.
 *   A row by who asks (`registerAgent`) is either list. A row that takes no request (`none`),
 *   or one its module reads whole (`loose`), has no entry.
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
import { docLines, WIDTH } from "./src/source-lines.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
/** The files this writes. */
const REQUESTS_FILE = join(HERE, "src", "requests.ts");
const DOORS_FILE = join(HERE, "src", "owner-doors.ts");

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

/** One door as the doors call it: its binding, its request, its answer, its tool's doc. */
function door(row: DoorRow): string[] {
  const request = row.args === "none" ? []
    : row.args === "loose" ? [`...request: DoorLoose<"${row.door}">`]
      : [`request${"omitted" in row.args ? "?" : ""}: DoorRequest<"${row.door}">`];
  const parts = [...UNBOUND_DOORS.includes(row.door) ? [] : ["binding: Binding"], ...request];
  const answer = `Promise<DoorAnswer<"${row.door}">>;`;
  const line = `  ${row.door}(${parts.join(", ")}): ${answer}`;
  return [...row.tool === undefined ? [] : docLines(row.door, row.tool.description, "  "),
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
  writeFileSync(DOORS_FILE, doorsSource(rows));
  console.log("platform/contract/src/requests.ts, platform/contract/src/owner-doors.ts");
}
