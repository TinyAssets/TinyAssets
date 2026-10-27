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
 * level or inside a body, writes through it (`globalWrites`). Every script extension a
 * bundler takes is scanned, not only `.ts`.
 */
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

/**
 * Each write through the global object anywhere in `file`: an assignment, `++`, `--`
 * or `delete` on a member chain rooted at `globalThis`, `self` or `global`; a call
 * through such a chain or handed one; and the global object named as a value, which
 * would let an alias write through it. Reading a member is allowed.
 */
function globalWrites(file: ts.SourceFile, report: (node: ts.Node, what: string) => void) {
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) && GLOBALS.has(node.text) && !inType(node)) {
      const parent = node.parent;
      const named = "name" in parent && (parent as { name?: ts.Node }).name === node
        && !ts.isShorthandPropertyAssignment(parent);
      if (!named && !(ts.isPropertyAccessExpression(parent) && parent.name === node)) {
        let top: ts.Node = node;
        while (((ts.isPropertyAccessExpression(top.parent)
                 || ts.isElementAccessExpression(top.parent)) && top.parent.expression === top)
               || wrapper(top.parent)) {
          top = top.parent;
        }
        const up = top.parent;
        const what = `writes through ${node.text}`;
        if (assigned(top)) {
          report(top, `${what}: an assignment`);
        } else if ((ts.isPrefixUnaryExpression(up) || ts.isPostfixUnaryExpression(up))
                   && (up.operator === ts.SyntaxKind.PlusPlusToken
                       || up.operator === ts.SyntaxKind.MinusMinusToken)) {
          report(top, `${what}: an update`);
        } else if (ts.isDeleteExpression(up)) {
          report(top, `${what}: a delete`);
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

describe("module-state guard", () => {
  it("finds no module-level mutable state in any Worker's source", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/owner-do.ts");
    const problems = [...sources].flatMap(([path, source]) => moduleState(path, source));
    expect(problems).toEqual([]);
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
  ])("allows %s", (source) => {
    expect(moduleState("probe.ts", source)).toEqual([]);
  });
});
