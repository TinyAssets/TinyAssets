/**
 * The gateway's source keeps its key in one place and reaches no owner (research §1.4): its
 * rules (`rules.ts`), held over its whole import closure by the platform's isolation checker
 * (`ci/kit/imports.ts`).
 */
import { describe, expect, it } from "vitest";

import { closure, isolationProblems, isolationTests } from "../../ci/kit/imports";

import { IMPORTS, KEYRING_HOME } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["env.GATEWAY_KEYRING", "export function f(env: { GATEWAY_KEYRING: string }) { return env.GATEWAY_KEYRING; }"],
    ["a string key", "export function f(env: Record<string, string>) { return env['GATEWAY_KEYRING']; }"],
    ["a template", "export const NAME = `${'x'}GATEWAY_KEYRING`;"],
    ["a destructure", "export function f({ GATEWAY_KEYRING: k }: Record<string, string>) { return k; }"],
    ["process.env", "export function f() { return process.env['X']; }"],
    ["globalThis.process", "export function f() { return globalThis.process; }"],
    ["an OWNER binding", "export function f(env: { OWNER: unknown }) { return env.OWNER; }"],
    ["getByName", "export function f(ns: { getByName(n: string): unknown }) { return ns.getByName('a'); }"],
    ["a string getByName", "export function f(ns: Record<string, unknown>) { return ns['getByName']; }"],
    ["the owner object's module", "import { OwnerDO } from '../../owner/src/owner-do';"],
    ["another Worker's module", "export { x } from '../../files/src/keys';"],
    ["a dot-slash path out", "import { OwnerDO } from './../../owner/src/owner-do';"],
    ["a path out and back", "import { x } from '../src/../../owner/src/owner-do';"],
    ["a dynamic import", "export async function f() { return import('../../owner/src/owner-do'); }"],
    ["a dynamic import of the grammar", "export async function f() { return import('../../owner/src/text'); }"],
    ["require", "export const x = require('node:fs');"],
    ["import-equals", "import fs = require('node:fs');"],
    ["node:process", "import { env } from 'node:process';\nexport const k = () => env;"],
    ["globalThis['process']", "export const k = () => (globalThis as never)['process'];"],
    ["an absolute specifier", "import { x } from '/platform/owner/src/owner-do';"],
    ["a URL specifier", "import { x } from 'https://example.com/x.js';"],
    ["another package", "import { x } from 'openai';"],
    ["node:async_hooks outside egress.ts", "import { AsyncLocalStorage } from 'node:async_hooks';"],
    ["a type import by path", "export type T = import('../../owner/src/owner-do').OwnerDO;"],
  ],
  allows: [
    ["pi-ai, the runtime module and sibling sources", "gateway/src/probe.ts", [
      "import { createModels } from '@earendil-works/pi-ai/models';",
      "import type { Api } from '@earendil-works/pi-ai';",
      "import { WorkerEntrypoint } from 'cloudflare:workers';",
      "import { open } from './keyring';",
      "import { principal } from './../../owner/src/text';",
    ].join("\n")],
    ["node:async_hooks in egress.ts", "gateway/src/egress.ts",
     "import { AsyncLocalStorage } from 'node:async_hooks';"],
    ["the keyring in keyring.ts", KEYRING_HOME,
     "export function f(env: { GATEWAY_KEYRING?: unknown }) { return env.GATEWAY_KEYRING; }"],
    ["the owner grammar import beside the word owner", "gateway/src/probe.ts",
     "import { principal } from '../../owner/src/text';\nexport const owner = 'owner';"],
  ],
});

describe("gateway isolation guards", () => {
  it("reach the keyring's home and the owner modules they may, through the closure", () => {
    const sources = closure("gateway");
    expect([...sources.keys()]).toContain(KEYRING_HOME);
    expect([...sources.keys()]).toEqual(expect.arrayContaining(
      ["owner/src/wake-records.ts", "owner/src/text.ts", "owner/src/errors.ts"]));
    expect(sources.get(KEYRING_HOME)).toContain("GATEWAY_KEYRING");
  });

  it("walks the closure: a module an allowed one imports is checked as a gateway source",
     () => {
    const all = new Map([
      ["gateway/src/probe.ts", "import { x } from '../../owner/src/wake-records';"],
      ["owner/src/wake-records.ts",
       "import { createHash } from 'node:crypto';\nimport { y } from './owner-do';"],
      ["owner/src/owner-do.ts", "export const y = 1;"],
    ]);
    const reached = closure("gateway", all);
    expect([...reached.keys()].sort()).toEqual(
      ["gateway/src/probe.ts", "owner/src/owner-do.ts", "owner/src/wake-records.ts"]);
    expect([...reached].flatMap(([path, source]) => isolationProblems(IMPORTS, path, source)))
      .toEqual([
        "owner/src/wake-records.ts:1: imports node:crypto, not a module the gateway may use",
        "owner/src/wake-records.ts:2: imports ./owner-do (owner/src/owner-do), outside the gateway",
      ]);
  });
});
