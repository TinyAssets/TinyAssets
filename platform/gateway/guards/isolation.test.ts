/**
 * The gateway's source keeps its key in one place and reaches no owner (research §1.4): its
 * banned names (`rules.ts`), held over its whole import closure by the platform's isolation
 * checker (`ci/kit/imports.ts`). What it may import is the layer table's
 * (`ci/kit/layers.ts`, `ci/guards/isolation.test.ts`).
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
    ["a dynamic import", "export async function f() { return import('../../owner/src/owner-do'); }"],
    ["a dynamic import of the grammar", "export async function f() { return import('../../contract/src/text'); }"],
    ["require", "export const x = require('node:fs');"],
    ["import-equals", "import fs = require('node:fs');"],
    ["node:process", "import { env } from 'node:process';\nexport const k = () => env;"],
    ["globalThis['process']", "export const k = () => (globalThis as never)['process'];"],
    ["an absolute specifier", "import { x } from '/platform/owner/src/owner-do';"],
    ["a URL specifier", "import { x } from 'https://example.com/x.js';"],
    ["a type import by path", "export type T = import('../../owner/src/owner-do').OwnerDO;"],
  ],
  allows: [
    ["pi-ai, the runtime module and sibling sources", "gateway/src/probe.ts", [
      "import { createModels } from '@earendil-works/pi-ai/models';",
      "import type { Api } from '@earendil-works/pi-ai';",
      "import { WorkerEntrypoint } from 'cloudflare:workers';",
      "import { open } from './keyring';",
      "import { principal } from './../../contract/src/text';",
    ].join("\n")],
    ["the keyring in keyring.ts", KEYRING_HOME,
     "export function f(env: { GATEWAY_KEYRING?: unknown }) { return env.GATEWAY_KEYRING; }"],
    ["the owner grammar import beside the word owner", "gateway/src/probe.ts",
     "import { principal } from '../../contract/src/text';\nexport const owner = 'owner';"],
  ],
});

describe("gateway isolation guards", () => {
  it("reach the keyring's home and the contract's modules they may, through the closure", () => {
    const sources = closure("gateway");
    expect([...sources.keys()]).toContain(KEYRING_HOME);
    expect([...sources.keys()]).toEqual(expect.arrayContaining(
      ["contract/src/wake-records.ts", "contract/src/text.ts", "contract/src/refusal.ts"]));
    expect(sources.get(KEYRING_HOME)).toContain("GATEWAY_KEYRING");
  });

  it("walks the closure: a module an allowed one imports is checked as a gateway source",
     () => {
    const all = new Map([
      ["gateway/src/probe.ts", "import { x } from '../../contract/src/wake-records';"],
      ["contract/src/wake-records.ts",
       "export const x = () => process.env;\nimport { y } from './owner-do';"],
      ["contract/src/owner-do.ts", "export const y = 'getByName';"],
    ]);
    const reached = closure("gateway", all);
    expect([...reached.keys()].sort()).toEqual(
      ["contract/src/owner-do.ts", "contract/src/wake-records.ts", "gateway/src/probe.ts"]);
    expect([...reached].flatMap(([path, source]) => isolationProblems(IMPORTS, path, source)))
      .toEqual([
        "contract/src/wake-records.ts:1: names process, whose env the gateway never reads",
        "contract/src/owner-do.ts:1: names getByName",
      ]);
  });
});
