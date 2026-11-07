/**
 * The contract depends on nothing that depends on it: its rules (`rules.ts`), held over its
 * whole import closure by the platform's isolation checker (`ci/kit/imports.ts`). And every
 * Worker speaks it: each one's source reaches it.
 */
import { describe, expect, it } from "vitest";

import { closure, isolationTests } from "../../ci/kit/imports";
import { workerSources } from "../../ci/kit/sources";

import { IMPORTS } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["a Worker's module", "import { OwnerDO } from '../../owner/src/a-module';"],
    ["a Worker's module, re-exported", "export { x } from '../../doors/src/a-module';"],
    ["a path out and back", "import { x } from '../src/../../gateway/src/a-module';"],
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
      "import { Invalid } from './errors';",
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
