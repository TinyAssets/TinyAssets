/**
 * The recorded formats and protocols (`state-format.json`, `protocol.json`) and how a value
 * is held to a recorded shape, for the tests of the rule (`state-format.test.ts`, and
 * `runner.test.ts`, which reads what the real runner wrote). Tests only.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Each persisted file's fields and their types (`a|b`), as `state-format.json` records. */
export type Shape = Readonly<Record<string, string>>;

export interface Format {
  /** The sync mirror's schema, as SQLite keeps it (`sqlite_master`, by name). */
  readonly mirror: readonly { type: string; name: string; sql: string | null }[];
  /** Each JSON file the runner keeps, by its path (`<...>` a name), its fields' types. */
  readonly files: Readonly<Record<string, Shape>>;
  /** Each file the runner keeps that is not JSON, in words. */
  readonly text: Readonly<Record<string, string>>;
}

export interface Protocol {
  readonly commands: readonly string[];
  readonly state: readonly string[];
}

export const FORMATS = JSON.parse(readFileSync(join(import.meta.dirname, "state-format.json"),
                                               "utf8")) as Record<string, Format>;
export const PROTOCOLS = JSON.parse(readFileSync(join(import.meta.dirname, "protocol.json"),
                                                 "utf8")) as Record<string, Protocol>;

function typeOf(value: unknown): string {
  return value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
}

/** How `value` differs from `shape`: a field missing, one more, or one of another type. */
export function shapeProblems(value: unknown, shape: Shape): string[] {
  if (typeOf(value) !== "object") {
    return [`not an object: ${typeOf(value)}`];
  }
  const fields = value as Record<string, unknown>;
  return [...new Set([...Object.keys(shape), ...Object.keys(fields)])].sort().flatMap((key) =>
    !(key in shape) ? [`${key}: not in the record`]
      : !(key in fields) ? [`${key}: missing`]
        : shape[key]!.split("|").includes(typeOf(fields[key])) ? []
          : [`${key}: ${typeOf(fields[key])}, not ${shape[key]}`]);
}

/** A value of `shape`, each field its first type's (`over` set as given). */
export function sample(shape: Shape, over: Record<string, unknown> = {}): Record<string, unknown> {
  const first: Record<string, unknown> = { number: 0, string: "s", boolean: false, null: null,
                                           object: {}, array: [] };
  return Object.fromEntries(Object.entries(shape).map(([key, types]) =>
    [key, key in over ? over[key] : first[types.split("|")[0]!]]));
}
