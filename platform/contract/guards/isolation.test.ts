/**
 * The contract names none of the runtime's I/O: its banned names (`rules.ts`), held over its
 * whole import closure by the platform's isolation checker (`ci/kit/imports.ts`); that it
 * depends on nothing that depends on it is the layer table's (`ci/kit/layers.ts`). And every
 * Worker speaks it: each one's source reaches it.
 */
import { describe, expect, it } from "vitest";

import { closure, isolationTests } from "../../ci/kit/imports";
import { workerSources } from "../../ci/kit/sources";

import { IMPORTS } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["a fetch", "export const f = () => fetch('https://x.test');"],
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

});
