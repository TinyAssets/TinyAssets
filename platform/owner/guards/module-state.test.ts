/**
 * No module-level mutable state in a Worker's source (ADR-032).
 *
 * Objects of one class can share an isolate, so anything a module keeps at top level
 * is shared across owners. Per-owner data lives only in the object's storage. At top
 * level a module may import, export, declare types, functions and classes, and bind
 * `const` names to values that cannot change: primitives, functions, regexes without
 * `g` or `y` (whose `lastIndex` is state), and `Object.freeze` of literals made of the
 * same. Anything else (`let`, `var`, a bare object, array, `new`, a call, a static
 * class field, a statement that runs at load) is refused.
 *
 * The global object is shared the same way, so nothing anywhere in a source, at top
 * level or inside a body, writes through it (`globalWrites`), nor through a built-in
 * global it holds (`Math`, `console`, `Object.prototype`, `crypto`, `JSON` and the rest):
 * a member written, or a built-in handed to a call that changes it
 * (`Object.defineProperty(Math, ...)`). Every script extension a bundler takes is
 * scanned, not only `.ts`. One file is exempt, for three pinned findings and at one
 * pinned hash: the gateway's global-fetch interposer (`EXEMPT`).
 */
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "./sources";

function unwrap(node: ts.Expression): ts.Expression {
  let current = node;
  while (ts.isParenthesizedExpression(current) || ts.isAsExpression(current)
         || ts.isSatisfiesExpression(current) || ts.isNonNullExpression(current)
         || ts.isTypeAssertionExpression(current)) {
    current = current.expression;
  }
  return current;
}

function frozenLiteral(node: ts.Expression): boolean {
  const value = unwrap(node);
  if (ts.isArrayLiteralExpression(value)) {
    return value.elements.every((element) => !ts.isSpreadElement(element) && immutable(element));
  }
  if (ts.isObjectLiteralExpression(value)) {
    return value.properties.every((property) =>
      ts.isShorthandPropertyAssignment(property) || ts.isMethodDeclaration(property)
      || (ts.isPropertyAssignment(property) && immutable(property.initializer)));
  }
  return false;
}

function immutable(node: ts.Expression): boolean {
  const value = unwrap(node);
  switch (value.kind) {
    case ts.SyntaxKind.StringLiteral:
    case ts.SyntaxKind.NumericLiteral:
    case ts.SyntaxKind.BigIntLiteral:
    case ts.SyntaxKind.NoSubstitutionTemplateLiteral:
    case ts.SyntaxKind.TemplateExpression:
    case ts.SyntaxKind.TrueKeyword:
    case ts.SyntaxKind.FalseKeyword:
    case ts.SyntaxKind.NullKeyword:
    case ts.SyntaxKind.Identifier:
    case ts.SyntaxKind.ArrowFunction:
    case ts.SyntaxKind.FunctionExpression:
      return true;
    case ts.SyntaxKind.RegularExpressionLiteral: {
      const flags = value.getText().slice(value.getText().lastIndexOf("/") + 1);
      return !/[gy]/.test(flags);
    }
    default:
      break;
  }
  if (ts.isPrefixUnaryExpression(value)) {
    return immutable(value.operand);
  }
  if (ts.isBinaryExpression(value)) {
    return value.operatorToken.kind !== ts.SyntaxKind.EqualsToken
      && immutable(value.left) && immutable(value.right);
  }
  if (ts.isCallExpression(value) && value.expression.getText() === "Object.freeze"
      && value.arguments.length === 1) {
    return frozenLiteral(value.arguments[0]!);
  }
  return false;
}

const DECLARATIONS = new Set([
  ts.SyntaxKind.ImportDeclaration, ts.SyntaxKind.ExportDeclaration,
  ts.SyntaxKind.FunctionDeclaration, ts.SyntaxKind.InterfaceDeclaration,
  ts.SyntaxKind.TypeAliasDeclaration, ts.SyntaxKind.EmptyStatement,
]);

/** The isolate's global object, by each name a Worker can reach it. */
const GLOBALS = new Set(["globalThis", "self", "global"]);

/**
 * The built-in globals every owner's code shares in an isolate. Reading them and calling
 * their methods is allowed; writing a member of one, or naming one as a value an alias
 * could write through, is a write to the global object.
 */
const BUILTINS = new Set([
  "Array", "ArrayBuffer", "Atomics", "BigInt", "Boolean", "DataView", "Date", "Error",
  "Function", "Intl", "JSON", "Map", "Math", "Number", "Object", "Promise", "Proxy", "Reflect",
  "RegExp", "Set", "String", "Symbol", "TextDecoder", "TextEncoder", "URL", "URLSearchParams",
  "Uint8Array", "WeakMap", "WeakRef", "WeakSet", "WebAssembly", "caches", "console", "crypto",
  "fetch", "navigator", "performance", "scheduler", "structuredClone", "Headers", "Request",
  "Response",
]);

/** The built-in calls that change their first argument. */
const MUTATORS = new Set([
  "Object.assign", "Object.defineProperty", "Object.defineProperties", "Object.setPrototypeOf",
  "Object.freeze", "Object.seal", "Object.preventExtensions", "Reflect.set",
  "Reflect.defineProperty", "Reflect.deleteProperty", "Reflect.setPrototypeOf",
  "Reflect.preventExtensions",
]);

function wrapper(node: ts.Node): node is ts.Expression & { expression: ts.Expression } {
  return ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
    || ts.isSatisfiesExpression(node) || ts.isNonNullExpression(node)
    || ts.isTypeAssertionExpression(node);
}

function inType(node: ts.Node): boolean {
  for (let up = node.parent; up !== undefined && !ts.isStatement(up); up = up.parent) {
    if (ts.isTypeNode(up)) {
      return true;
    }
  }
  return false;
}

/** Whether `node` is written by an assignment, directly or as a destructuring target. */
function assigned(node: ts.Node): boolean {
  let up = node.parent;
  while (ts.isPropertyAssignment(up) || ts.isShorthandPropertyAssignment(up)
         || ts.isObjectLiteralExpression(up) || ts.isArrayLiteralExpression(up)
         || ts.isSpreadElement(up) || ts.isSpreadAssignment(up) || wrapper(up)) {
    up = up.parent;
  }
  if (ts.isBinaryExpression(up) && up.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
      && up.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
    return node.pos >= up.left.pos && node.end <= up.left.end;
  }
  return (ts.isForOfStatement(up) || ts.isForInStatement(up)) && up.initializer === node;
}

/** The member chain `node` heads (`Math.x.y`), through any parentheses or casts. */
function chainTop(node: ts.Node): ts.Node {
  let top = node;
  while (((ts.isPropertyAccessExpression(top.parent) || ts.isElementAccessExpression(top.parent))
          && top.parent.expression === top) || wrapper(top.parent)) {
    top = top.parent;
  }
  return top;
}

/** The identifier a member chain is rooted at (`Object` for `Object.prototype.x`). */
function chainRoot(node: ts.Expression): ts.Identifier | undefined {
  let current: ts.Expression = node;
  for (;;) {
    if (wrapper(current) || ts.isPropertyAccessExpression(current)
        || ts.isElementAccessExpression(current)) {
      current = current.expression;
    } else {
      return ts.isIdentifier(current) ? current : undefined;
    }
  }
}

/** How the chain `top` is written: an assignment, `++` or `--`, or `delete`; else null. */
function writeOf(top: ts.Node): string | null {
  const up = top.parent;
  if (assigned(top)) {
    return "an assignment";
  }
  if ((ts.isPrefixUnaryExpression(up) || ts.isPostfixUnaryExpression(up))
      && (up.operator === ts.SyntaxKind.PlusPlusToken
          || up.operator === ts.SyntaxKind.MinusMinusToken)) {
    return "an update";
  }
  return ts.isDeleteExpression(up) ? "a delete" : null;
}

/** Whether a bare built-in is used only as itself: called, extended, tested or typed. */
function usedInPlace(node: ts.Node): boolean {
  const up = node.parent;
  return ((ts.isCallExpression(up) || ts.isNewExpression(up)) && up.expression === node)
    || ((ts.isCallExpression(up) || ts.isNewExpression(up))
        && (up.arguments ?? []).some((argument) => argument === node))
    || (ts.isExpressionWithTypeArguments(up) && ts.isHeritageClause(up.parent))
    || (ts.isBinaryExpression(up) && up.right === node
        && up.operatorToken.kind === ts.SyntaxKind.InstanceOfKeyword)
    || ts.isTypeOfExpression(up);
}

/**
 * Each write through the global object anywhere in `file`: an assignment, `++`, `--`
 * or `delete` on a member chain rooted at `globalThis`, `self` or `global`; a call
 * through such a chain or handed one; and the global object named as a value, which
 * would let an alias write through it. Reading a member is allowed. A built-in global
 * counts the same for a write to a member, a call that changes it, and a bare built-in
 * named as a value; calling its methods is allowed.
 */
function globalWrites(file: ts.SourceFile, report: (node: ts.Node, what: string) => void) {
  const visit = (node: ts.Node): void => {
    const global = ts.isIdentifier(node) && GLOBALS.has(node.text);
    const builtin = ts.isIdentifier(node) && BUILTINS.has(node.text);
    if ((global || builtin) && !inType(node)) {
      const parent = node.parent;
      const named = "name" in parent && (parent as { name?: ts.Node }).name === node
        && !ts.isShorthandPropertyAssignment(parent);
      if (!named && !(ts.isPropertyAccessExpression(parent) && parent.name === node)) {
        const top = chainTop(node);
        const up = top.parent;
        const what = `writes through ${(node as ts.Identifier).text}`;
        const write = writeOf(top);
        if (write !== null) {
          report(top, `${what}: ${write}`);
        } else if (builtin) {
          if (top === node && !usedInPlace(node)) {
            report(top, `${what}: named as a value`);
          }
        } else if ((ts.isCallExpression(up) || ts.isNewExpression(up)) && up.expression === top) {
          report(top, `${what}: a call`);
        } else if ((ts.isCallExpression(up) || ts.isNewExpression(up))
                   && (up.arguments ?? []).some((argument) => argument === top)) {
          report(top, `${what}: handed to a call`);
        } else if (top === node && !ts.isTypeOfExpression(up)) {
          report(top, `${what}: named as a value`);
        }
      }
    }
    if (ts.isCallExpression(node) && MUTATORS.has(node.expression.getText(file).replace(/\s/g, ""))
        && node.arguments[0] !== undefined) {
      const root = chainRoot(node.arguments[0]);
      if (root !== undefined && BUILTINS.has(root.text)) {
        report(node, `writes through ${root.text}: a call that changes it`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
}

/** Each place `source` keeps module-level mutable state, as `file:line: what`. */
export function moduleState(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  globalWrites(file, report);
  const declared = (node: ts.Node) =>
    ts.canHaveModifiers(node)
    && (ts.getModifiers(node) ?? []).some((m) => m.kind === ts.SyntaxKind.DeclareKeyword);
  for (const statement of file.statements) {
    if (DECLARATIONS.has(statement.kind) || declared(statement)) {
      continue;
    }
    if (ts.isVariableStatement(statement)) {
      const scope = statement.declarationList.flags & ts.NodeFlags.BlockScoped;
      if (scope !== ts.NodeFlags.Const) {
        report(statement, "a module-level binding that is not const");
        continue;
      }
      for (const declaration of statement.declarationList.declarations) {
        if (declaration.initializer && !immutable(declaration.initializer)) {
          report(declaration, `${declaration.name.getText(file)} holds a mutable value`);
        }
      }
    } else if (ts.isClassDeclaration(statement)) {
      for (const member of statement.members) {
        if (ts.isClassStaticBlockDeclaration(member)) {
          report(member, "a static block runs at load");
        }
        const modifiers = ts.canHaveModifiers(member) ? ts.getModifiers(member) ?? [] : [];
        if (!ts.isPropertyDeclaration(member)
            || !modifiers.some((m) => m.kind === ts.SyntaxKind.StaticKeyword)) {
          continue;
        }
        if (!modifiers.some((m) => m.kind === ts.SyntaxKind.ReadonlyKeyword)
            || (member.initializer && !immutable(member.initializer))) {
          report(member, `static ${member.name.getText(file)} is shared mutable state`);
        }
      }
    } else if (ts.isEnumDeclaration(statement)) {
      if (!(ts.getModifiers(statement) ?? []).some((m) => m.kind === ts.SyntaxKind.ConstKeyword)) {
        report(statement, "an enum is a mutable object; use a const enum or a union");
      }
    } else if (ts.isExportAssignment(statement)) {
      if (!immutable(statement.expression)) {
        report(statement, "the default export is a mutable value");
      }
    } else {
      report(statement, "a top-level statement runs at load");
    }
  }
  return problems;
}

/**
 * The one exemption: the gateway's global-fetch interposer (`gateway/src/egress.ts`).
 * pi-ai's Google adapters take no custom fetch, so the gateway replaces the global
 * `fetch`, once, by a function that is the current call's endpoint guard inside that
 * call's `AsyncLocalStorage` scope and the runtime's own fetch outside any. That needs a
 * module-level scope, the captured runtime fetch and one write of the global, and
 * nothing else: no owner's data lives in any of them (gateway spec, "Every adapter
 * request goes through the call's guard"). The exemption is pinned to exactly those three
 * findings in that one file and to the file's SHA-256, so it can neither widen nor
 * outlive a change to the file without a review here.
 */
const EXEMPT = Object.freeze({
  path: "gateway/src/egress.ts",
  sha256: "42b225aacd2a3d7ff5812557f078c36cc2b7074d3e1f43f8528025dccc257712",
  problems: Object.freeze([
    "gateway/src/egress.ts:21: SCOPE holds a mutable value",
    "gateway/src/egress.ts:22: NATIVE holds a mutable value",
    "gateway/src/egress.ts:37: writes through globalThis: an assignment",
  ]),
});

/** Every finding in `sources` the exemption does not cover, and every way it is off. */
export function unexempted(sources: ReadonlyMap<string, string>): string[] {
  const problems = [...sources].flatMap(([path, source]) => moduleState(path, source));
  const exempt = sources.get(EXEMPT.path);
  if (exempt === undefined) {
    return problems;
  }
  const digest = createHash("sha256").update(exempt).digest("hex");
  const pinned = problems.filter((problem) => problem.startsWith(`${EXEMPT.path}:`)).sort();
  return [
    ...problems.filter((problem) => !EXEMPT.problems.includes(problem)),
    ...(digest === EXEMPT.sha256 ? [] : [`${EXEMPT.path} changed (sha256 ${digest}): review its exemption`]),
    ...(JSON.stringify(pinned) === JSON.stringify(EXEMPT.problems)
      ? [] : [`${EXEMPT.path} findings ${JSON.stringify(pinned)} are not the pinned three`]),
  ];
}

describe("module-state guard", () => {
  it("finds no module-level mutable state in any Worker's source", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/owner-do.ts");
    expect([...sources.keys()]).toContain(EXEMPT.path);
    expect(unexempted(sources)).toEqual([]);
  });

  it("exempts the interposer's three findings, in that one file, at that one hash", () => {
    const sources = workerSources();
    const exempt = sources.get(EXEMPT.path)!;
    expect([...sources].flatMap(([path, source]) => moduleState(path, source)).sort())
      .toEqual([...EXEMPT.problems]);
    // A fourth finding in the file, or any edit to it, is refused.
    expect(unexempted(new Map([[EXEMPT.path, `${exempt}\nexport let calls = 0;\n`]])))
      .toHaveLength(3);
    expect(unexempted(new Map([[EXEMPT.path, `// edited\n${exempt}`]])))
      .not.toEqual([]);
    // The same text anywhere else is not exempt.
    expect(unexempted(new Map([["gateway/src/other.ts", exempt]]))).toHaveLength(3);
    // And the file without its global write is a change, refused too.
    expect(unexempted(new Map([[EXEMPT.path,
      exempt.replace("globalThis.fetch = interposed;", "void interposed;")]]))).not.toEqual([]);
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
