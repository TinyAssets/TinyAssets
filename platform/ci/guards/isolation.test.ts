/**
 * No Worker imports another Worker's `src` (`decisions.md` A1), type imports included: every
 * Worker's whole import closure reaches no other Worker's source, whatever its rules say; and
 * every workspace's isolation rules (`<workspace>/guards/rules.ts`, the kit's `ImportRules`)
 * name no Worker's source but its own, a Worker's naming only the contract's modules and the
 * source records' (`ci/kit/imports.ts`, `outsideProblems`). The kit's checker refuses such an
 * import even where a rule lists it (`workerOf`).
 */
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../paths";
import {
  closure, foreignReach, importProblem, type ImportRules, outsideProblems,
} from "../kit/imports";
import { workerSources } from "../kit/sources";
import { workerFolders } from "../workers";

function isRules(value: unknown): value is ImportRules {
  const r = value as Partial<ImportRules> | null;
  return typeof value === "object" && value !== null && typeof r!.worker === "string"
    && typeof r!.called === "string" && typeof r!.bare === "object" && Array.isArray(r!.banned);
}

/** Every workspace's isolation rules, read from its `guards/rules.ts`, by workspace. */
const RULES = new Map<string, readonly ImportRules[]>();
for (const workspace of readdirSync(PLATFORM).sort()) {
  const path = join(PLATFORM, workspace, "guards", "rules.ts");
  if (existsSync(path)) {
    const module = await import(path) as Record<string, unknown>;
    RULES.set(workspace, Object.values(module).filter(isRules));
  }
}

const rules = (worker: string, outside: readonly string[], job?: string): ImportRules =>
  ({ worker, called: worker, outside, bare: {}, banned: [], ...(job === undefined ? {} : { job }) });

describe("no Worker's source reaches another Worker's", () => {
  it("over every Worker's whole import closure, type imports included", () => {
    const sources = workerSources();
    const workers = workerFolders();
    expect(workers).toEqual(["doors", "files", "gateway", "hooks", "owner", "sandbox"]);
    for (const worker of workers) {
      expect([...closure(worker, sources).keys()], worker).toContain(
        [...sources.keys()].find((path) => path.startsWith(`${worker}/src/`)));
    }
    expect(workers.flatMap((worker) => foreignReach(worker, closure(worker, sources).keys())))
      .toEqual([]);
  });

  it("by any workspace's rules, a Worker's naming only the contract and the source records",
     () => {
    const all = [...RULES.values()].flat();
    expect(all.map((each) => each.worker).sort())
      .toEqual(["contract", "gateway", "hooks", "import", "sources"]);
    expect(all.flatMap(outsideProblems)).toEqual([]);
  });

  it.each([
    ["a type import of the owner's object, from a doors source",
     new Map([["doors/src/probe.ts", 'import type { OwnerDO } from "../../owner/src/owner-do";'],
              ["owner/src/owner-do.ts", "export class OwnerDO {}"]])],
    ["another Worker's source, reached through the contract",
     new Map([["doors/src/probe.ts", 'import { x } from "../../contract/src/probe";'],
              ["contract/src/probe.ts", 'export { x } from "../../gateway/src/forward";'],
              ["gateway/src/forward.ts", "export const x = 1;"]])],
  ])("sees %s", (_what, sources) => {
    expect(foreignReach("doors", closure("doors", sources).keys())).not.toEqual([]);
  });

  it("allows a Worker its own source, the contract and the source records", () => {
    const sources = new Map([
      ["doors/src/probe.ts", 'import { a } from "./a";\nimport { b } from "../../contract/src/b";\n' +
                             'import { c } from "../../sources/src/c";'],
      ["doors/src/a.ts", "export const a = 1;"], ["contract/src/b.ts", "export const b = 1;"],
      ["sources/src/c.ts", "export const c = 1;"]]);
    expect(closure("doors", sources).size).toBe(4);
    expect(foreignReach("doors", closure("doors", sources).keys())).toEqual([]);
  });

  it.each([
    ["another Worker's source", rules("gateway", ["owner/src/text"])],
    ["another Worker's source, for a job", rules("import", ["owner/src/files"], "import")],
    ["a module neither the contract's nor the records'", rules("hooks", ["doors/test/support"])],
  ])("sees rules naming %s", (_what, named) => {
    expect(outsideProblems(named)).not.toEqual([]);
  });

  it("refuses an import of another Worker's source even where a rule lists it", () => {
    const listed = rules("gateway", ["owner/src/text"]);
    expect(importProblem(listed, "gateway/src/probe.ts", "../../owner/src/text"))
      .toBe("imports ../../owner/src/text (owner/src/text), the owner Worker's source");
    expect(importProblem(rules("gateway", ["contract/src/text"]), "gateway/src/probe.ts",
                         "../../contract/src/text")).toBeNull();
    expect(outsideProblems(rules("gateway", ["contract/src/text", "sources/groq.json"])))
      .toEqual([]);
  });
});
