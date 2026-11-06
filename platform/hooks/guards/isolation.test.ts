/**
 * The hooks Worker's source reaches no owner and no storage (agent research §6.4): its rules
 * (`rules.ts`), held over its whole import closure by the platform's isolation checker
 * (`ci/kit/imports.ts`).
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
    ["the owner object's module", "import { OwnerDO } from '../../owner/src/owner-do';"],
    ["a path out and back", "import { x } from '../src/../../owner/src/text';"],
    ["another package", "import { x } from '@earendil-works/pi-ai';"],
    ["QuickJS outside codemode.ts", "import { x } from '@earendil-works/pi-codemode/cloudflare';"],
    ["a dynamic import", "export const f = () => import('./codemode');"],
    ["require", "export const x = require('node:fs');"],
    ["a type import by path", "export type T = import('../../owner/src/owner-do').OwnerDO;"],
  ],
  allows: [
    ["the runtime, code mode's portable types and sibling sources", "hooks/src/probe.ts", [
      "import { WorkerEntrypoint } from 'cloudflare:workers';",
      "import type { CodemodeRemote } from '@earendil-works/pi-codemode/portable';",
      "import { CodemodeSandbox } from './codemode';",
    ].join("\n")],
    ["QuickJS in codemode.ts", "hooks/src/codemode.ts",
     "import { CodemodeSandboxDurableObject } from '@earendil-works/pi-codemode/cloudflare';"],
  ],
});

describe("hooks isolation guards", () => {
  it("reach only the hooks Worker's own two sources", () => {
    expect([...closure("hooks").keys()].sort())
      .toEqual(["hooks/src/codemode.ts", "hooks/src/index.ts"]);
  });
});
