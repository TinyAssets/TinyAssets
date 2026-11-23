/**
 * The shapes one Worker hands another over RPC that a strict parser reads (`handed.json`), by
 * name: each parser takes its fields here and nowhere else, so what a release hands and takes
 * is data at every commit, which `deploy/handed.ts` holds to the deploy order, against the base
 * branch and against the versions the stage serves.
 */
import table from "../handed.json" with { type: "json" };

import { frozen } from "./checks.ts";

/** A shape's name in `handed.json`. */
export type ShapeName = keyof typeof table.shapes;

/**
 * A shape: the Worker that hands it and the one that parses it; the fields it always hands,
 * the ones it always hands that the parser takes as if optional (the step between the two,
 * either way), the ones it hands when it has them, and the ones the parser takes that are not
 * handed (by a release before, or not yet).
 */
export interface Shape {
  readonly from: string;
  readonly to: string;
  readonly required: readonly string[];
  readonly optional: readonly string[];
  readonly always?: readonly string[];
  readonly dropped?: readonly string[];
}

/** Every shape, by its name, as the data file has it, frozen whole. */
export function handedShapes(): Readonly<Record<ShapeName, Shape>> {
  return frozen(table.shapes as Record<ShapeName, Shape>);
}

/** A shape's fields, by part. */
export type Listed = Pick<Shape, "required" | "always" | "optional" | "dropped">;

/**
 * What a parser of `shape` takes, as `keyed` holds it: each required field, then any always,
 * optional or dropped one.
 */
export function takes({ required, always = [], optional, dropped = [] }: Listed):
    readonly [readonly string[], readonly string[]] {
  return [required, [...always, ...optional, ...dropped]];
}

/** What a parser of the shape `name` takes (`takes`). */
export function taken(name: ShapeName): readonly [readonly string[], readonly string[]] {
  return takes(handedShapes()[name]);
}
