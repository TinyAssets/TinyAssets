/**
 * The owner's door requests as types, written from the door table (`doors.json`), so a
 * door's fields are listed once, in its row (`args`), and `tsc` holds both sides of a call
 * to them: the doors' request literals and the object's handlers (`OwnerDO implements
 * OwnerDoors`, `contract/src/owner.ts`).
 *
 * A JSON module's types cannot name them: its strings widen to `string`, so no row can be
 * picked by its door. So this writes `contract/src/requests.ts`, one of `npm run generate`'s
 * generators, which `ci/guards/generated.test.ts` holds current.
 * Node runs this file as it is (type stripping).
 *
 * Each row with a field list is one entry: its required fields, then its optional ones, as
 * the runner holds them (`fields`). A field's value is `unknown`: the schemas are its tool's
 * JSON arguments, and the doors send some as other values (`fileWrite`'s content as bytes),
 * so its handler checks each value. A row by who asks (`registerAgent`) is either list. A row
 * that takes no request (`none`), or one its module reads whole (`loose`), has no entry.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { Args, DoorRow } from "./src/doors.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
/** The file this writes. */
const REQUESTS_FILE = join(HERE, "src", "requests.ts");

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

/** The table's rows, read as the file holds them. */
function tableRows(): readonly DoorRow[] {
  return (JSON.parse(readFileSync(join(HERE, "doors.json"), "utf8")) as
    { doors: readonly DoorRow[] }).doors;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  writeFileSync(REQUESTS_FILE, requestsSource(tableRows()));
  console.log("platform/contract/src/requests.ts");
}
