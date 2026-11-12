/**
 * The codemode Worker's source reaches no owner and no storage (agent research §6.4): its banned
 * names (`rules.ts`), held over its whole import closure by the platform's isolation checker
 * (`ci/kit/imports.ts`). What it may import is the layer table's (`ci/kit/layers.ts`).
 */
import { describe, expect, it } from "vitest";

import { closure, isolationTests } from "../../ci/kit/imports";

import { IMPORTS } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["an OWNER binding", "export function f(env: { OWNER: unknown }) { return env.OWNER; }"],
    ["the files Worker", "export const f = (env: { FILES: unknown }) => env.FILES;"],
    ["the gateway", "export const g = (env: { GATEWAY: unknown }) => env.GATEWAY;"],
    ["storage", "export const s = (ctx: { storage: unknown }) => ctx.storage;"],
    ["process", "export const p = () => process;"],
    ["a dynamic import", "export const f = () => import('./codemode');"],
    ["require", "export const x = require('node:fs');"],
    ["a type import by path", "export type T = import('../../owner/src/owner-do').OwnerDO;"],
  ],
  allows: [
    ["the runtime, code mode's portable types and sibling sources", "codemode/src/probe.ts", [
      "import { WorkerEntrypoint } from 'cloudflare:workers';",
      "import type { CodemodeRemote } from '@earendil-works/pi-codemode/portable';",
      "import { CodemodeSandbox } from './codemode';",
    ].join("\n")],
  ],
});

describe("codemode isolation guards", () => {
  it("reach only the codemode Worker's own two sources, its env and its contract", () => {
    expect([...closure("codemode").keys()].sort())
      .toEqual(["codemode/src/codemode.ts", "codemode/src/env.ts", "codemode/src/index.ts",
                "contract/src/codemode.ts"]);
  });
});
