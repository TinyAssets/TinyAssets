/**
 * The module-state rule (`ci/kit/module-state.ts`, ADR-032) held over every Worker's source:
 * the only exemptions are each Worker's own, for its own files (`MODULE_STATE`, in its
 * `guards/rules.ts`), each a symbol and its reason.
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { type Exemption, moduleState, unexempted } from "../kit/module-state";
import { ruleLists } from "../kit/owed";
import { workerSources } from "../kit/sources";

/** Each workspace's exemptions, by workspace. */
const EXEMPTIONS = await ruleLists<readonly Exemption[]>("MODULE_STATE");

describe("module-state guard", () => {
  it("finds no module-level mutable state in any Worker's source but what it exempts", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/owner-do.ts");
    expect(unexempted(sources, [...EXEMPTIONS.values()].flat())).toEqual([]);
  });

  it("takes each Worker's exemptions for its own files alone, each a symbol with its reason",
     () => {
    expect([...EXEMPTIONS.keys()]).toEqual(["gateway", "owner"]);
    for (const [workspace, exemptions] of EXEMPTIONS) {
      for (const each of exemptions) {
        expect(each.file.startsWith(`${workspace}/src/`), each.file).toBe(true);
        expect([each.symbol, each.reason].every((text) => text.trim() !== ""), each.file)
          .toBe(true);
      }
    }
  });

  it("exempts a symbol in its one file alone, and refuses an exemption that covers nothing",
     () => {
    const pool: Exemption = { file: "w/src/a.ts", symbol: "POOL", reason: "the isolate's" };
    const source = "const POOL = new Map();\n";
    expect(unexempted(new Map([[pool.file, source]]), [pool])).toEqual([]);
    // Another finding in the file, or the same text in another file, is not exempt.
    expect(unexempted(new Map([[pool.file, `${source}export let spare = 0;\n`]]), [pool]))
      .toEqual(["w/src/a.ts:2: a module-level binding that is not const"]);
    expect(unexempted(new Map([["w/src/b.ts", source], [pool.file, "export const A = 1;\n"]]),
                      [pool]))
      .toEqual(["w/src/b.ts:1: POOL holds a mutable value",
                "w/src/a.ts exempts POOL, which keeps no module state: delete the exemption"]);
    // So is an exemption whose file is gone.
    expect(unexempted(new Map([["w/src/b.ts", "export const A = 1;\n"]]), [pool]))
      .toEqual(["w/src/a.ts exempts POOL, which keeps no module state: delete the exemption"]);
    // A write through the global object is named by the member it writes.
    const interposer = "export function f(g: typeof fetch) { globalThis.fetch = g; }\n";
    expect(unexempted(new Map([["w/src/e.ts", interposer]]),
                      [{ file: "w/src/e.ts", symbol: "globalThis.fetch", reason: "pi-ai's" }]))
      .toEqual([]);
    expect(unexempted(new Map([["w/src/e.ts", interposer.replace(".fetch", ".other")]]),
                      [{ file: "w/src/e.ts", symbol: "globalThis.fetch", reason: "pi-ai's" }]))
      .toHaveLength(2);
  });

  it.each([
    "let count = 0;",
    "var seen;",
    "export let current: string | undefined;",
    "const cache = new Map<string, string>();",
    "const rows: string[] = [];",
    "const byOwner = {} as Record<string, number>;",
    "const nested = Object.freeze({ list: [] });",
    "const spread = Object.freeze([...other]);",
    "const made = build();",
    "const sticky = /a/g;",
    "class Holder { static last = 0; }",
    "class Holder { static readonly all: string[] = []; }",
    "class Holder { static { setup(); } }",
    "enum Lane { Todo, Done }",
    "export default { owners: [] };",
    "if (ready) { start(); }",
  ])("sees %s", (source) => {
    expect(moduleState("probe.ts", source)).toHaveLength(1);
  });

  it.each([
    "globalThis.owner = 'alice';",
    // The reviewer's first miss: a write through the global object from inside a body.
    "export function remember(owner: string) { globalThis.lastOwner = owner; }",
    "class C { m(v: string) { self.cache.rows[v] = v; } }",
    "const f = () => { global['seen'] += 1; };",
    "function g() { (globalThis as any).n++; }",
    "function g() { delete self.owner; }",
    "function g(v: string) { globalThis.cache.set(v, v); }",
    "function g(v: object) { Object.assign(globalThis, v); }",
    "function g(v: object) { Reflect.set(self.store, 'k', v); }",
    "function g() { const g = globalThis; g.x = 1; }",
    "function g(v: string) { ({ a: globalThis.owner } = { a: v }); }",
    "function g(v: string[]) { for (self.last of v) {} }",
  ])("sees a write through the global object: %s", (source) => {
    expect(moduleState("probe.ts", source).some((p) => /writes through/.test(p))).toBe(true);
  });

  it.each([
    "export function f() { Math.owner = 'alice'; }",
    "export function f() { console.log = () => undefined; }",
    "export function f() { Object.prototype.owner = 'alice'; }",
    "export function f() { crypto.cache = new Map(); }",
    "export function f() { JSON.last = 1; }",
    "export function f() { Array.prototype['last'] = 1; }",
    "export function f() { delete (Math as any).PI; }",
    "export function f() { (Date as any).calls++; }",
    "export function f() { ({ a: Math.owner } = { a: 1 }); }",
    "export function f() { Object.defineProperty(globalThis, 'owner', { value: 1 }); }",
    "export function f() { Object.defineProperty(Math, 'owner', { value: 1 }); }",
    "export function f() { Object.defineProperties(Object.prototype, {}); }",
    "export function f() { Object.assign(console, { owner: 1 }); }",
    "export function f() { Reflect.set(Date, 'now', () => 0); }",
    "export function f() { Object.freeze(JSON); }",
    "export function f() { Object . setPrototypeOf(crypto, null); }",
    "export function f() { const m = Math; return m; }",
    "export function f() { return { Math }; }",
  ])("sees a write through a built-in global: %s", (source) => {
    expect(moduleState("probe.ts", source)).toEqual(
      [expect.stringMatching(/^probe\.ts:1: writes through \w+: /)]);
  });

  it.each([
    // The reviewer's second miss: a source the bundler takes that is not `.ts`.
    ["probe.js", "export let current = null;"],
    ["probe.mjs", "export function f() { globalThis.owner = 1; }"],
    ["probe.mts", "let count = 0;"],
    ["probe.cts", "const cache = new Map();"],
    ["probe.tsx", "export function f() { self.owner = 1; }"],
  ])("scans %s too", (fileName, source) => {
    expect(moduleState(fileName, source)).not.toEqual([]);
  });

  it("collects every script extension from a Worker's src", () => {
    const root = mkdtempSync(join(tmpdir(), "module-state-"));
    try {
      mkdirSync(join(root, "w", "src", "deep"), { recursive: true });
      for (const name of ["a.ts", "b.js", "c.mjs", "d.mts", "e.tsx", "f.cts", "deep/g.cjs",
                          "notes.md"]) {
        writeFileSync(join(root, "w", "src", name), "");
      }
      expect([...workerSources(root).keys()].sort()).toEqual(
        ["w/src/a.ts", "w/src/b.js", "w/src/c.mjs", "w/src/d.mts", "w/src/deep/g.cjs",
         "w/src/e.tsx", "w/src/f.cts"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it.each([
    "const LIMIT = 10 * 1024;",
    "const NAME = `a` + 'b';",
    "export const LIST: readonly string[] = Object.freeze(['a', Object.freeze(['b'])]);",
    "const TABLE = Object.freeze({ a: 1, b: -2, c: Object.freeze({ d: true }) });",
    "const RE = /^[a-z]+$/u;",
    "const f = (x: number) => x + 1;",
    "class Store { static readonly NAME = 'store'; #rows = new Map(); }",
    "const enum Lane { Todo, Done }",
    "declare const injected: string;",
    "export { Store } from './store';",
    "type T = { a: string };",
    "export function f() { return globalThis.crypto.randomUUID === undefined; }",
    "export function f(x: { self: number }) { return x.self + 1; }",
    "type G = typeof globalThis;",
    "export function f(n: number) { return Math.max(n, 0) + Number.parseInt('1', 10); }",
    "export class Refused extends Error {}",
    "export function f(x: unknown) { return x instanceof Map ? new Map() : JSON.stringify(x); }",
    "export function f() { crypto.getRandomValues(new Uint8Array(8)); return crypto.randomUUID(); }",
    "export function f(x: string[]) { return x.map(Number); }",
    "export function f() { console.error('failed'); }",
    "export const LIST = Object.freeze(['a', 'b']);",
    "export const KEYS = Object.freeze({ Math: 1, console: 2 });",
    "export function f(o: { Math: number }) { return o.Math + typeof Math; }",
    "export function f(x: object) { return Object.freeze({ ...x }); }",
  ])("allows %s", (source) => {
    expect(moduleState("probe.ts", source)).toEqual([]);
  });
});
