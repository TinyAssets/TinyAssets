/**
 * The source records name none of the runtime's I/O: their banned names (`rules.ts`, the
 * contract's), held over their whole import closure by the platform's isolation checker
 * (`ci/kit/imports.ts`); and the Workers that read records reach them where they live.
 */
import { describe, expect, it } from "vitest";

import { closure, isolationTests } from "../../ci/kit/imports";
import { workerSources } from "../../ci/kit/sources";

import { IMPORTS } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["a fetch", "export const f = () => fetch('https://x.test');"],
    ["process", "export const p = () => process.env;"],
    ["a dynamic import", "export const f = (id: string) => import(`../${id}.json`);"],
  ],
  allows: [
    ["its own modules and the contract's", "sources/src/probe.ts", [
      "import { SourceRecordError } from './schema';",
      "import { SOURCE_ID } from '../../contract/src/text';",
      "import type { EngineEvents } from '../../contract/src/run-events';",
    ].join("\n")],
    ["its record files", "sources/src/probe.ts", "import groq from '../records/groq.json';"],
  ],
});

describe("the source records", () => {
  it("are read where they live, by the Workers that read them", () => {
    const sources = workerSources();
    const reached = [...closure("sources", sources).keys()];
    expect(reached.every((path) => /^(?:sources|contract)\/src\//.test(path))).toBe(true);
    expect(reached).toContain("sources/src/records.ts");
    for (const worker of ["owner", "doors"]) {
      expect(closure(worker, sources).has("sources/src/catalog.ts"), worker).toBe(true);
    }
    for (const worker of ["gateway", "files", "codemode", "sandbox"]) {
      expect([...closure(worker, sources).keys()]
        .some((path) => path.startsWith("sources/")), worker).toBe(false);
    }
  });
});
