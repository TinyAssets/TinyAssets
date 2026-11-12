/**
 * The refusal guard (untangle B2): every answer a Worker refuses with is the contract's one
 * `Refusal` (`contract/src/refusal.ts`), told apart by its `code`, never by its message. So
 * in any Worker's own source:
 *
 * - no class extends an error: an error class of a Worker's own is a refusal the contract's
 *   code list does not hold, or a signal some caller must know to catch;
 * - no code is read from a message: no `startsWith`, `includes`, `indexOf`, `match` or
 *   `search` on one, no `split` at a colon, no regular expression run on one, and no
 *   comparison of one.
 *
 * Read from the syntax tree, by name (an error base is any name ending in `Error`, and the
 * contract's `Refusal` and `FileFault`): a tripwire for our own code, not proof against
 * deliberate evasion. `ci/guards/refusals.test.ts` holds it over the tree, with what each
 * workspace owes (`owed.ts`) the only exceptions.
 */
import ts from "typescript";

import { workerOf } from "./imports.ts";
import type { Found } from "./owed.ts";
import { parse } from "./sources.ts";

/** A class name a Worker's class may not extend. */
const ERROR_BASE = /^(?:[A-Za-z]*Error|Refusal|FileFault|DOMException)$/;
/** The string methods that test text for a part of it. */
const TESTS: ReadonlySet<string> = new Set(["startsWith", "endsWith", "includes", "indexOf",
                                            "lastIndexOf", "match", "matchAll", "search"]);
const EQUALITY: ReadonlySet<ts.SyntaxKind> = new Set([
  ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken,
  ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken]);

/** `node` without the wrappers that leave its value a message: parentheses, casts, `String()`. */
function unwrapped(node: ts.Expression): ts.Expression {
  let at = node;
  for (;;) {
    if (ts.isParenthesizedExpression(at) || ts.isAsExpression(at) || ts.isNonNullExpression(at)
        || ts.isTypeAssertionExpression(at) || ts.isSatisfiesExpression(at)) {
      at = at.expression;
    } else if (ts.isCallExpression(at) && ts.isIdentifier(at.expression)
               && at.expression.text === "String" && at.arguments.length === 1) {
      at = at.arguments[0]!;
    } else {
      return at;
    }
  }
}

/** Whether `node` is a message: an object's `message`, read by name. */
function isMessage(node: ts.Expression | undefined): boolean {
  const at = node === undefined ? undefined : unwrapped(node);
  return at !== undefined && (
    (ts.isPropertyAccessExpression(at) && at.name.text === "message")
    || (ts.isElementAccessExpression(at) && ts.isStringLiteralLike(at.argumentExpression)
        && at.argumentExpression.text === "message"));
}

/** Whether the call reads a code from a message, by a string method or a regular expression. */
function readsMessage(call: ts.CallExpression): boolean {
  if (!ts.isPropertyAccessExpression(call.expression)) {
    return false;
  }
  const method = call.expression.name.text;
  const on = call.expression.expression;
  if (TESTS.has(method) || method === "split") {
    const at = call.arguments[0];
    return isMessage(on) && (method !== "split"
      || (at !== undefined && ts.isStringLiteralLike(at) && at.text.includes(":")));
  }
  return (method === "test" || method === "exec") && isMessage(call.arguments[0]);
}

/** One source's breaks, each as its path from the repository root and what it does. */
function breaks(path: string, source: string): string[] {
  const file = parse(path, source);
  const found: string[] = [];
  const text = (node: ts.Node) => node.getText(file).replace(/\s+/g, " ");
  const visit = (node: ts.Node): void => {
    if ((ts.isClassDeclaration(node) || ts.isClassExpression(node))) {
      const base = node.heritageClauses
        ?.find((clause) => clause.token === ts.SyntaxKind.ExtendsKeyword)?.types[0]?.expression;
      const name = base === undefined ? undefined
        : ts.isIdentifier(base) ? base.text
        : ts.isPropertyAccessExpression(base) ? base.name.text : undefined;
      if (name !== undefined && ERROR_BASE.test(name)) {
        found.push(`class ${node.name?.text ?? "(anonymous)"} extends ${name}`);
      }
    } else if (ts.isCallExpression(node) && readsMessage(node)) {
      found.push(`reads a code from a message: ${text(node)}`);
    } else if (ts.isBinaryExpression(node) && EQUALITY.has(node.operatorToken.kind)
               && (isMessage(node.left) || isMessage(node.right))) {
      found.push(`reads a code from a message: ${text(node)}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found.map((what) => `platform/${path}: ${what}`);
}

/**
 * Every break of the refusal rule in `sources` (scripts by path from `platform/`), each in
 * the Worker whose own source it is; a source of no Worker is not held.
 */
export function refusalProblems(sources: ReadonlyMap<string, string>): Found[] {
  return [...sources].flatMap(([path, source]) => {
    const worker = workerOf(path);
    return worker === null ? []
      : breaks(path, source).map((what) => ({ workspace: worker, what }));
  });
}
