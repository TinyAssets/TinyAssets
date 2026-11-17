/**
 * The owner object's typed handler map (untangle B4), one of `npm run generate`'s generators,
 * which `ci/guards/generated.test.ts` holds current: `src/door-map.ts`, from the door table's
 * two halves (`ownerRows()`, `src/door-steps.ts`), whose JSON modules' strings widen to
 * `string`. Node runs this file as it is (type stripping). It writes:
 *
 * - `DoorRows`: each door's module, caller and gate (its steps'), which type its handler
 *   (`owner-do.ts`, `DoorsOf`);
 * - `DoorMethods`: each door as the object's method, which the object's loop defines on its
 *   prototype from the row at module load: its binding unless its caller is `none`, its
 *   request unless it takes none (optional where the row says an omitted one is empty), its
 *   request and answer its handler's, and its doc the row's tool description.
 *
 * A door of either half with no match in the other is refused here, as the object's loop
 * refuses it when the class loads.
 */
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { docLines, WIDTH } from "../contract/src/source-lines.ts";
import { type OwnerRow, ownerRows } from "./src/door-steps.ts";

/** The file this writes. */
const MAP_FILE = join(dirname(fileURLToPath(import.meta.url)), "src", "door-map.ts");

/** One door as the object's method: its binding, its request, its answer, its tool's doc. */
function method(row: OwnerRow): string[] {
  const parts = [
    ...row.caller === "none" ? [] : ["binding: Binding"],
    ...row.args === "none" ? []
      : [`request${row.args !== "loose" && "omitted" in row.args ? "?" : ""}: ` +
         `RequestIn<H, "${row.door}">`],
  ];
  const line = `  ${row.door}(${parts.join(", ")}): Promise<AnswerIn<H, "${row.door}">>;`;
  return [...row.tool === undefined ? [] : docLines(row.door, row.tool.description, "  "),
          ...line.length <= WIDTH ? [line]
            : [`  ${row.door}(${parts.join(", ")}):`, `    Promise<AnswerIn<H, "${row.door}">>;`]];
}

/** The source of `src/door-map.ts`, from `rows`. */
function mapSource(rows: readonly OwnerRow[]): string {
  return [
    "/**",
    " * The owner object's typed handler map, by door: written by `npm run generate` from the",
    " * door table's two halves (`contract/doors.json`, `owner/doors.json`), which say what each",
    " * means. Edit the tables, never this file.",
    " */",
    "import type { Binding } from \"../../contract/src/binding\";",
    "",
    "/** Each door's module (its handler's), caller and gate, as its steps name them. */",
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

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  writeFileSync(MAP_FILE, mapSource(ownerRows()));
  console.log("platform/owner/src/door-map.ts");
}
