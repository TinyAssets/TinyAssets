/**
 * The door table (`doors.json`, read by `doorTable()`) as types, one of `npm run generate`'s
 * generators, which `ci/guards/generated.test.ts` holds current. A JSON module's types cannot
 * name them: its strings widen to `string`, so no row can be picked by its door. Node runs
 * this file as it is (type stripping). It writes two files, from the contract's half of the
 * table alone (the owner's typed handler map, from both halves, is `owner/write-door-map.ts`'s):
 *
 * - `contract/src/requests.ts`: each door's request whose row lists its fields, so a door's
 *   fields and their types are listed once, in its row (`args`), and `tsc` holds the doors'
 *   request literals to them (`OwnerDoors`, `contract/src/owner-doors.ts`), as the owner's
 *   fields step holds each call (`requestFields`). Each row with a field list is one entry:
 *   its required fields, then its optional ones, each its schema's type (`fieldTypes`; an
 *   object's or a list's contents `unknown`, which its handler checks). A row by who asks
 *   (`registerAgent`, the owner's own file doors) is either list. A row that takes no request
 *   (`none`), or one its module reads whole (`loose`), has no entry. Beside them, each door's
 *   answer whose row declares one (`answer`): what the doors read (`DoorAnswers`: its required
 *   fields, then its optional ones, each its schema's type, an object's or a list's contents
 *   left to `DoorTypes`; null too where it is `nullable`), and what the owner still answers
 *   that they read no more (`UnreadAnswers`, its `unread` fields), so `tsc` holds the doors'
 *   reads and the owner's handlers to the row.
 * - `contract/src/owner-doors.ts`: the owner object's doors as the doors call them
 *   (`OwnerDoors`, `contract/src/owner.ts` says what each part means): each door its row's,
 *   its binding but on an unbound door (`UNBOUND_DOORS`), no request, an optional one, its
 *   row's fields (`DoorRequest`) or a loose door's own parameters (`DoorLoose`), its caller's
 *   stop where its row takes one (`stop`), and the answer the doors read (`DoorAnswer`), its
 *   doc the row's tool description. A door the doors call no more (`dropped`) is none of it.
 */
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  type Answer, type Args, type DoorRow, doorTable, fieldTypes, type FieldType, takesStop,
  UNBOUND_DOORS,
} from "./src/doors.ts";
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

/**
 * A field type's TypeScript type: a request's (`REQUEST_TS`), or an answer's, whose object is
 * `object`, which a handler's answer of an interface type is, and whose list holds values RPC
 * carries (a list of `unknown` makes a stub's whole answer `never`, which reads as anything);
 * an object's fields and a list's items are `DoorTypes`'s to say.
 */
const REQUEST_TS: Readonly<Record<FieldType, string>> = Object.freeze({
  string: "string", integer: "number", number: "number", boolean: "boolean",
  object: "Readonly<Record<string, unknown>>", array: "readonly unknown[]", null: "null",
  bytes: "Uint8Array", stream: "ReadableStream<Uint8Array>",
});
const ANSWER_TS: Readonly<Record<FieldType, string>> = Object.freeze({
  ...REQUEST_TS, object: "object", array: "readonly (string | number | boolean | object | null)[]",
});

/** One field as its type's member: its value's type, from its schema (`fieldTypes`), in `ts`. */
function member(door: string, name: string, schema: unknown, optional: boolean,
                ts: Readonly<Record<FieldType, string>>): string {
  const types = fieldTypes(`${door} ${name}`, schema);
  if (types === null) {
    throw new Error(`${door}: a field ${name} that names no type`);
  }
  return `readonly ${name}${optional ? "?" : ""}: ${
    [...new Set(types.map((type) => ts[type]))].join(" | ")}`;
}

/**
 * One field list as its type, its required fields, then its optional ones, each in `ts` (a
 * request's or an answer's), and `tail` after it (an answer's `| null`): on `lead`'s line
 * when it fits, else one member a line, indented past `indent`.
 */
function fieldsType(door: string, args: Pick<Args, "required" | "optional">, lead: string,
                    indent: string, ts = REQUEST_TS, tail = ""): string {
  names(door, { ...args.required, ...args.optional });
  const listed = [
    ...Object.entries(args.required).map(([name, schema]) =>
      member(door, name, schema, false, ts)),
    ...Object.entries(args.optional).map(([name, schema]) =>
      member(door, name, schema, true, ts))];
  const one = `${listed.length === 0 ? "Record<never, never>" : `{ ${listed.join("; ")} }`}${
    tail}`;
  if (`${lead} ${one};`.length <= WIDTH) {
    return `${lead} ${one}`;
  }
  const line = (each: string) => `${indent}  ${each};`.length <= WIDTH ? `${indent}  ${each};`
    : `${indent}  ${each.replace(/: .*$/, ":")}\n${indent}    ${each.replace(/^[^:]*: /, "")};`;
  return [`${lead} {`, ...listed.map(line), `${indent}}${tail}`].join("\n");
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
        .map((part) => fieldsType(row.door, part, "    |", "    ")).join("\n")};`];
    }
    return [`${fieldsType(row.door, args, lead, "  ")};`];
  });
  const answers = rows.flatMap((row) => row.answer === undefined ? [] : [row.door]);
  /**
   * The fields of `door`'s answer that `unread` lists (`listed`), or the others; with none
   * required, an `object` too, so an arm of a union answer that has none of them is still one
   * (`tsc` refuses a value sharing no field with a type of optional fields alone).
   */
  const answerType = (door: string, listed: boolean): string => {
    const answer = rows.find((row) => row.door === door)!.answer as Answer;
    const kept = ([name]: [string, unknown]) =>
      (answer.unread ?? []).includes(name) === listed;
    const required = Object.fromEntries(Object.entries(answer.required).filter(kept));
    return `${fieldsType(door, {
      required, optional: Object.fromEntries(Object.entries(answer.optional).filter(kept)) },
    `  readonly ${door}:`, "  ", ANSWER_TS,
    `${Object.keys(required).length === 0 ? " & object" : ""}${
      !listed && answer.nullable === true ? " | null" : ""}`)};`;
  };
  const unread = answers.filter((door) =>
    rows.find((row) => row.door === door)!.answer!.unread !== undefined);
  return [
    "/**",
    " * Each owner door's request whose row lists its fields, and each answer whose row",
    " * declares one, by door: written by `npm run generate` from the door table",
    " * (`doors.json`), which says what each means. Edit the table, never this file.",
    " */",
    "",
    "export interface DoorRequests {",
    ...entries,
    "}",
    "",
    "/** What the doors read of each answer its row declares: its fields, but `unread` ones. */",
    "export interface DoorAnswers {",
    ...answers.map((door) => answerType(door, false)),
    "}",
    "",
    "/** The answer fields the owner still answers, the doors reading them no more (`unread`). */",
    ...unread.length === 0 ? ["export type UnreadAnswers = Record<never, never>;"]
      : ["export interface UnreadAnswers {", ...unread.map((door) => answerType(door, true)),
         "}"],
    "",
  ].join("\n");
}

/** One door as the doors call it: its binding, its request, its answer, its tool's doc. */
function door(row: DoorRow): string[] {
  const request = row.args === "none" ? []
    : row.args === "loose" ? [`...request: DoorLoose<"${row.door}">`]
      : [`request${"omitted" in row.args ? "?" : ""}: DoorRequest<"${row.door}">`];
  const parts = [...UNBOUND_DOORS.includes(row.door) ? [] : ["binding: Binding"], ...request,
                 ...takesStop(row) ? ["stop: ReadableStream<Uint8Array>"] : []];
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
    "import type { DoorAnswers, DoorRequests } from \"./requests.ts\";",
    "",
    "/** The owner's object, as the doors reach it (`binder.ts` alone looks one up by name). */",
    "export interface OwnerDoors extends Rpc.DurableObjectBranded {",
    ...rows.filter((row) => row.dropped !== true).flatMap(door),
    "}",
    "",
    "/** The doors whose rows leave their parameters to their modules. */",
    entry("type Loose =",
          rows.filter((row) => row.args === "loose").map((row) => `"${row.door}"`).join(" | ")),
    "/** The doors the doors call no more, which the object still takes, its types with it. */",
    entry("type Dropped =", rows.filter((row) => row.dropped === true)
      .map((row) => `"${row.door}"`).join(" | ") || "never"),
    "/** What `DoorTypes` may say of a door, by its row. */",
    "type Parts<D> = (D extends keyof DoorAnswers ? \"answer\" : never)",
    "  | (D extends keyof DoorRequests ? \"values\" | \"handed\"",
    "    : D extends Loose ? \"loose\" : never);",
    "/** The fields a door's row lists (any of a choice's). */",
    "type Fields<D> = D extends keyof DoorRequests",
    "  ? DoorRequests[D] extends infer R ? R extends unknown ? keyof R : never : never : never;",
    "/** The fields of an answer, any of its arms'. */",
    "type Keys<A> = A extends object ? keyof A : never;",
    "/** What an entry of `DoorTypes` says that its row leaves no room for. */",
    "type Stray<D extends keyof DoorTypes> = Exclude<keyof DoorTypes[D], Parts<D>>",
    "  | (DoorTypes[D] extends { readonly values: infer V }",
    "    ? Exclude<keyof V, Fields<D>> : never)",
    "  | (DoorTypes[D] extends { readonly answer: infer A } ? D extends keyof DoorAnswers",
    "    ? Exclude<Keys<A>, Keys<DoorAnswers[D]>>",
    "      | ([DoorAnswer<D>] extends [DoorAnswers[D]] ? never : \"a type wider than its row's\")",
    "    : never : never);",
    "",
    "/**",
    " * The doors `DoorTypes` names: `tsc` refuses one that is no door of the table, or one whose",
    " * entry says what its row leaves no room for (a part, or a field its row does not list).",
    " */",
    "type Doors<D extends keyof OwnerDoors | Dropped> = D;",
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
