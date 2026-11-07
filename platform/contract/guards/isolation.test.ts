/**
 * The contract depends on nothing that depends on it: its rules (`rules.ts`), held over its
 * whole import closure by the platform's isolation checker (`ci/kit/imports.ts`). And every
 * Worker speaks it: each one's source reaches it. Its source records' module
 * (`sources/src/`) is held the same way, reaching the contract and its record files alone,
 * and the Workers that read records reach it there.
 */
import { describe, expect, it } from "vitest";

import { closure, isolationTests } from "../../ci/kit/imports";
import { workerSources } from "../../ci/kit/sources";

import { IMPORTS, SOURCES_IMPORTS } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["a Worker's module", "import { OwnerDO } from '../../owner/src/a-module';"],
    ["a Worker's module, re-exported", "export { x } from '../../doors/src/a-module';"],
    ["a path out and back", "import { x } from '../src/../../gateway/src/a-module';"],
    ["the source records", "import { catalogOf } from '../../sources/src/catalog';"],
    ["a record file", "import groq from '../../sources/groq.json';"],
    ["the runtime's module", "import { DurableObject } from 'cloudflare:workers';"],
    ["a package", "import { x } from '@earendil-works/pi-ai';"],
    ["a Node module the Workers runtime lacks", "import { readFileSync } from 'node:fs';"],
    ["a fetch", "export const f = () => fetch('https://x.test');"],
    ["storage", "export const s = (ctx: { storage: unknown }) => ctx.storage;"],
    ["process", "export const p = () => process.env;"],
    ["a dynamic import", "export const f = () => import('./text');"],
    ["require", "export const x = require('node:crypto');"],
    ["a type import by path", "export type T = import('../../owner/src/a-module').OwnerDO;"],
  ],
  allows: [
    ["its own sources and node:crypto", "contract/src/probe.ts", [
      "import { createHash } from 'node:crypto';",
      "import { Refusal } from './refusal';",
      "import type { EgressScope } from './egress-scope';",
    ].join("\n")],
    ["its wake kinds' file", "contract/src/probe.ts",
     "import raw from '../wake-kinds.json' with { type: 'json' };"],
  ],
});

isolationTests(SOURCES_IMPORTS, {
  sees: [
    ["a Worker's module", "import { serve } from '../../doors/src/a-module';"],
    ["a Worker's module, by a path out and back",
     "import { x } from '../../contract/src/../../owner/src/a-module';"],
    ["a file beside the records that is not one", "import x from '../locks/pi/package.json';"],
    ["a contract module that is not there", "import { x } from '../../contract/src/nothing';"],
    ["the runtime's module", "import { DurableObject } from 'cloudflare:workers';"],
    ["a package", "import { builtinProviders } from '@earendil-works/pi-ai/providers/all';"],
    ["a Node module the Workers runtime lacks", "import { readdirSync } from 'node:fs';"],
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
    ["its record files", "sources/src/probe.ts", "import groq from '../groq.json';"],
  ],
});

describe("the contract", () => {
  it("is reached by every Worker's source, and reaches no Worker's", () => {
    const sources = workerSources();
    const reached = closure("contract", sources);
    expect([...reached.keys()].every((path) => path.startsWith("contract/src/"))).toBe(true);
    for (const worker of ["owner", "doors", "gateway", "files", "sandbox"]) {
      expect([...closure(worker, sources).keys()]
        .some((path) => path.startsWith("contract/src/")), worker).toBe(true);
    }
  });

  it("has its source records read where they live, by the Workers that read them", () => {
    const sources = workerSources();
    const reached = [...closure("sources", sources).keys()];
    expect(reached.every((path) => /^(?:sources|contract)\/src\//.test(path))).toBe(true);
    expect(reached).toContain("sources/src/records.ts");
    for (const worker of ["owner", "doors", "sandbox"]) {
      expect(closure(worker, sources).has("sources/src/catalog.ts"), worker).toBe(true);
    }
    for (const worker of ["gateway", "files", "hooks"]) {
      expect([...closure(worker, sources).keys()]
        .some((path) => path.startsWith("sources/")), worker).toBe(false);
    }
  });
});
