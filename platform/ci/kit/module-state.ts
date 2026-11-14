/**
 * No module-level mutable state in a Worker's source (ADR-032): the rule, which
 * `ci/guards/module-state.test.ts` holds over every Worker's source.
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
 * scanned, not only `.ts`.
 *
 * Each finding names the symbol it is about: the binding, `Class.member`, or the global
 * member written. A Worker that keeps state by design says so in its own `guards/rules.ts`,
 * as `MODULE_STATE`: each exemption a file, a symbol and its reason. Nothing else is exempt,
 * and an exemption whose finding is gone fails, so the list only shrinks.
 */
import ts from "typescript";

import { parse } from "./sources.ts";

/** A symbol a Worker keeps in module state by design, in one of its files, and why. */
export interface Exemption {
  /** Its file, by path from `platform/`. */
  readonly file: string;
  /** The symbol, as a finding names it. */
  readonly symbol: string;
  readonly reason: string;
}

/** One finding: where, the symbol it is about, and what is wrong. */
export interface Finding {
  readonly line: number;
  readonly symbol: string;
  readonly what: string;
}

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
function globalWrites(file: ts.SourceFile,
                      report: (node: ts.Node, symbol: string, what: string) => void) {
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
        const symbol = top.getText(file).replace(/\s/g, "");
        if (write !== null) {
          report(top, symbol, `${what}: ${write}`);
        } else if (builtin) {
          if (top === node && !usedInPlace(node)) {
            report(top, symbol, `${what}: named as a value`);
          }
        } else if ((ts.isCallExpression(up) || ts.isNewExpression(up)) && up.expression === top) {
          report(top, symbol, `${what}: a call`);
        } else if ((ts.isCallExpression(up) || ts.isNewExpression(up))
                   && (up.arguments ?? []).some((argument) => argument === top)) {
          report(top, symbol, `${what}: handed to a call`);
        } else if (top === node && !ts.isTypeOfExpression(up)) {
          report(top, symbol, `${what}: named as a value`);
        }
      }
    }
    if (ts.isCallExpression(node) && MUTATORS.has(node.expression.getText(file).replace(/\s/g, ""))
        && node.arguments[0] !== undefined) {
      const root = chainRoot(node.arguments[0]);
      if (root !== undefined && BUILTINS.has(root.text)) {
        report(node, node.arguments[0].getText(file).replace(/\s/g, ""),
               `writes through ${root.text}: a call that changes it`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
}

/** Each place `source` keeps module-level mutable state. */
export function findings(fileName: string, source: string): Finding[] {
  const file = parse(fileName, source);
  const found: Finding[] = [];
  const report = (node: ts.Node, symbol: string, what: string) => {
    found.push({ line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1,
                 symbol, what });
  };
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
        report(statement, statement.declarationList.declarations
          .map((declaration) => declaration.name.getText(file)).join(","),
               "a module-level binding that is not const");
        continue;
      }
      for (const declaration of statement.declarationList.declarations) {
        if (declaration.initializer && !immutable(declaration.initializer)) {
          const name = declaration.name.getText(file);
          report(declaration, name, `${name} holds a mutable value`);
        }
      }
    } else if (ts.isClassDeclaration(statement)) {
      const owner = statement.name?.text ?? "default";
      for (const member of statement.members) {
        if (ts.isClassStaticBlockDeclaration(member)) {
          report(member, `${owner}.static`, "a static block runs at load");
        }
        const modifiers = ts.canHaveModifiers(member) ? ts.getModifiers(member) ?? [] : [];
        if (!ts.isPropertyDeclaration(member)
            || !modifiers.some((m) => m.kind === ts.SyntaxKind.StaticKeyword)) {
          continue;
        }
        if (!modifiers.some((m) => m.kind === ts.SyntaxKind.ReadonlyKeyword)
            || (member.initializer && !immutable(member.initializer))) {
          report(member, `${owner}.${member.name.getText(file)}`,
                 `static ${member.name.getText(file)} is shared mutable state`);
        }
      }
    } else if (ts.isEnumDeclaration(statement)) {
      if (!(ts.getModifiers(statement) ?? []).some((m) => m.kind === ts.SyntaxKind.ConstKeyword)) {
        report(statement, statement.name.text,
               "an enum is a mutable object; use a const enum or a union");
      }
    } else if (ts.isExportAssignment(statement)) {
      if (!immutable(statement.expression)) {
        report(statement, "default", "the default export is a mutable value");
      }
    } else {
      report(statement, "", "a top-level statement runs at load");
    }
  }
  return found;
}

/** Each place `source` keeps module-level mutable state, as `file:line: what`. */
export function moduleState(fileName: string, source: string): string[] {
  return findings(fileName, source).map(({ line, what }) => `${fileName}:${line}: ${what}`);
}

/**
 * Every finding in `sources` (by path from `platform/`) that no exemption of its own file
 * covers, as `file:line: what`, and every exemption that covers no finding.
 */
export function unexempted(sources: ReadonlyMap<string, string>,
                           exemptions: readonly Exemption[]): string[] {
  const used = new Set<Exemption>();
  const problems = [...sources].flatMap(([path, source]) => findings(path, source)
    .filter((found) => {
      const exempt = exemptions.find((each) => each.file === path
                                                && each.symbol === found.symbol);
      if (exempt !== undefined) {
        used.add(exempt);
      }
      return exempt === undefined;
    }).map(({ line, what }) => `${path}:${line}: ${what}`));
  return [...problems, ...exemptions.filter((each) => !used.has(each))
    .map((each) => `${each.file} exempts ${each.symbol}, which keeps no module state: ` +
                   "delete the exemption")];
}
