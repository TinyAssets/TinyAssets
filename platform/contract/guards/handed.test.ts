/**
 * The shapes one Worker hands another (`handed.json`): each names two platform Workers, its
 * sender and its parser, and lists its fields once, in known parts; and each is read by name
 * in its parser's Worker or the contract (`taken`, a binding's `fields`, a connection's
 * `requested`), so no shape is listed that nothing reads. Which way each is held across a
 * deploy is `deploy/handed.ts`'s.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { workerFolders } from "../../ci/wrangler";
import { handedShapes } from "../src/handed";

const SHAPES = Object.entries(handedShapes());
const PARTS = ["from", "to", "required", "always", "optional", "dropped"];

/** Every TypeScript source under `folder`, read. */
function sources(folder: string): string[] {
  return readdirSync(folder, { recursive: true, encoding: "utf8" })
    .filter((path) => path.endsWith(".ts") && !path.endsWith(".d.ts"))
    .map((path) => readFileSync(join(folder, path), "utf8"));
}

describe("the shapes one Worker hands another (handed.json)", () => {
  it.each(SHAPES)("%s names its two Workers and lists each field once", (_name, shape) => {
    const listed = [...shape.required, ...shape.always ?? [], ...shape.optional,
                    ...shape.dropped ?? []];
    expect(Object.keys(shape).filter((key) => !PARTS.includes(key))).toEqual([]);
    expect(workerFolders()).toEqual(expect.arrayContaining([shape.from, shape.to]));
    expect(shape.from).not.toBe(shape.to);
    expect(new Set(listed).size).toBe(listed.length);
  });

  it.each(SHAPES)("%s is read by name where its parser runs", (name, shape) => {
    const reads = new RegExp(`\\b(?:taken|fields|requested)\\([^()]*"${name}"\\)`);
    const read = [shape.to, "contract"].flatMap((folder) => sources(join(PLATFORM, folder, "src")))
      .some((text) => reads.test(text));
    expect(read).toBe(true);
  });
});
