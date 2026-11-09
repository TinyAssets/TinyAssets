/**
 * No Worker imports another Worker's `src` (`decisions.md` A1): every Worker's whole import
 * closure reaches no other Worker's source, whatever its rules say, by every way one module
 * names another (an import or re-export, type ones included; a type imported by path;
 * `import()`; `require()`; `/// <reference path>`) and through any module outside a Worker's
 * `src` on the way (a test's helper, a guard); and
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
  it("over every Worker's whole import closure, by every way one module names another",
     () => {
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

  const OWNER_DO = ["owner/src/owner-do.ts", "export class OwnerDO {}"] as const;
  it.each([
    ["a type import of the owner's object, from a doors source",
     new Map([["doors/src/probe.ts", 'import type { OwnerDO } from "../../owner/src/owner-do";'],
              OWNER_DO])],
    ["a type imported by path",
     new Map([["doors/src/probe.ts",
               'export type Probe = import("../../owner/src/owner-do").OwnerDO;'], OWNER_DO])],
    ["an import()",
     new Map([["doors/src/probe.ts",
               'export const probe = () => import("../../owner/src/owner-do");'], OWNER_DO])],
    ["a require()",
     new Map([["doors/src/probe.ts", 'export const probe = require("../../owner/src/owner-do");'],
              OWNER_DO])],
    ["a reference path",
     new Map([["doors/src/probe.ts",
               '/// <reference path="../../owner/src/owner-do.ts" />\nexport {};'], OWNER_DO])],
    ["a re-export through a test's helper, read from disk (`ci/guards/fixtures/helper.ts`)",
     new Map([["doors/src/probe.ts",
               'export type { OwnerDO } from "../../ci/guards/fixtures/helper";'], OWNER_DO])],
    ["a script by its .mjs name",
     new Map([["doors/src/probe.ts", 'export { probe } from "../../owner/src/probe.mjs";'],
              ["owner/src/probe.mjs", "export const probe = 1;"]])],
    ["a data file in another Worker's source",
     new Map([["doors/src/probe.ts",
               'import ranking from "../../owner/src/model-ranking.json" with { type: "json" };'],
              ["owner/src/model-ranking.json", "{}"]])],
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
    ["another Worker's source as a bare folder, for a job",
     rules("import", ["files/src"], "import")],
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
