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
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { workerSources } from "./sources";

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

/** Each place `source` keeps module-level mutable state, as `file:line: what`. */
export function moduleState(fileName: string, source: string): string[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true,
                                   ts.ScriptKind.TS);
  const problems: string[] = [];
  const report = (node: ts.Node, what: string) => {
    const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
    problems.push(`${fileName}:${line}: ${what}`);
  };
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
    "globalThis.owner = 'alice';",
    "if (ready) { start(); }",
  ])("sees %s", (source) => {
    expect(moduleState("probe.ts", source)).toHaveLength(1);
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
  ])("allows %s", (source) => {
    expect(moduleState("probe.ts", source)).toEqual([]);
  });
});
