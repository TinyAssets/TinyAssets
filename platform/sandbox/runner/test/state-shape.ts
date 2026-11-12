/**
 * The recorded formats and protocols (`state-format.json`, `protocol.json`) and how a value
 * is held to a recorded shape, for the tests of the rule (`state-format.test.ts`;
 * `runner.test.ts`, which reads what the real runner wrote and answered; and the sandbox
 * Worker's, which reads the documents it wrote). Tests only.
 */
import formats from "../state-format.json" with { type: "json" };
import protocols from "../protocol.json" with { type: "json" };

/**
 * A JSON value's fields and their types (`a|b`), as `state-format.json` records each persisted
 * file and `protocol.json` each document and answer: a field holding an object of its own
 * fields is that object's shape.
 */
export interface Shape {
  readonly [field: string]: string | Shape;
}

export interface Format {
  /** The sync mirror's schema, as SQLite keeps it (`sqlite_master`, by name). */
  readonly mirror: readonly { type: string; name: string; sql: string | null }[];
  /** Each JSON file the runner keeps, by its path (`<...>` a name), its fields' types. */
  readonly files: Readonly<Record<string, Shape>>;
  /** Each file the runner keeps that is not JSON, in words. */
  readonly text: Readonly<Record<string, string>>;
}

/** A protocol's record: everything the Worker and a runner of it say to each other. */
export interface Protocol {
  readonly commands: readonly string[];
  readonly exit: Readonly<Record<string, number>>;
  /** Each path both sides name (`<digest>`, `<id>` a name), and the loader's install command. */
  readonly paths: Readonly<Record<string, string>>;
  /** The documents the Worker writes: a job's start or sign-in, and the lease file. */
  readonly documents: Readonly<Record<"start" | "signin" | "lease", Shape>>;
  /** The answers the Worker reads, by command. */
  readonly answers: Readonly<Record<string, Shape>>;
}

export const FORMATS: Readonly<Record<string, Format>> = formats;
export const PROTOCOLS: Readonly<Record<string, Protocol>> = protocols;

function typeOf(value: unknown): string {
  return value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
}

/** How `value` differs from `shape`: a field missing, one more, or one of another type. */
export function shapeProblems(value: unknown, shape: Shape, at = ""): string[] {
  if (typeOf(value) !== "object") {
    return [`${at === "" ? "" : `${at}: `}not an object: ${typeOf(value)}`];
  }
  const fields = value as Record<string, unknown>;
  return [...new Set([...Object.keys(shape), ...Object.keys(fields)])].sort().flatMap((key) => {
    const name = `${at}${key}`;
    const want = shape[key];
    return want === undefined ? [`${name}: not in the record`]
      : !(key in fields) ? [`${name}: missing`]
        : typeof want !== "string" ? shapeProblems(fields[key], want, `${name}.`)
          : want.split("|").includes(typeOf(fields[key])) ? []
            : [`${name}: ${typeOf(fields[key])}, not ${want}`];
  });
}

/** A value of `shape`, each field its first type's (`over` set as given). */
export function sample(shape: Shape, over: Record<string, unknown> = {}): Record<string, unknown> {
  const first: Record<string, unknown> = { number: 0, string: "s", boolean: false, null: null,
                                           object: {}, array: [] };
  return Object.fromEntries(Object.entries(shape).map(([key, types]) =>
    [key, key in over ? over[key] : typeof types === "string" ? first[types.split("|")[0]!]
      : sample(types)]));
}
