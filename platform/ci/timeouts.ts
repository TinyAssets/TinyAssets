/**
 * Hand-set timeouts in a test file: what `guards/timeouts.test.ts` refuses.
 *
 * A test's time budget is its size class's, set once per project (`classes.ts`), never a
 * number beside the test. Found by parsing the file as TypeScript, never by running it:
 * - a trailing argument after a test's or hook's function (`it(name, fn, 60_000)`,
 *   `beforeAll(fn, 30_000)`), whatever its expression, so a named constant is refused too;
 * - a `timeout` property in a test's or suite's options (`it(name, { timeout: 5 }, fn)`);
 * - `vi.setConfig`, which changes the budget for the rest of a file.
 */
import ts from "typescript";

/** The Vitest functions whose calls take a function and, optionally, a timeout after it. */
const TAKES_TIMEOUT = new Set(["it", "test", "describe", "suite", "bench", "beforeAll",
                               "beforeEach", "afterAll", "afterEach"]);

export interface TimeoutLiteral {
  readonly line: number;
  readonly text: string;
  /** The call's own text span, for a fixer. */
  readonly call: ts.CallExpression;
  /** The argument or property that sets the timeout. */
  readonly node: ts.Node;
}

/** `it`, `it.skip`, `it.each(rows)`, `describe.sequential` and the like: the root's name. */
function rootName(callee: ts.Expression): string | null {
  let node: ts.Expression = callee;
  for (;;) {
    if (ts.isIdentifier(node)) {
      return node.text;
    }
    if (ts.isPropertyAccessExpression(node)) {
      node = node.expression;
    } else if (ts.isCallExpression(node)) {
      node = node.expression;
    } else {
      return null;
    }
  }
}

function isFunction(node: ts.Node): boolean {
  return ts.isArrowFunction(node) || ts.isFunctionExpression(node);
}

export function timeoutLiterals(source: string, file = "test.ts"): TimeoutLiteral[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const found: TimeoutLiteral[] = [];
  const at = (call: ts.CallExpression, node: ts.Node) => {
    found.push({ line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
                 text: node.getText(sf), call, node });
  };
  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      if (ts.isPropertyAccessExpression(callee) && callee.name.text === "setConfig"
          && ts.isIdentifier(callee.expression) && callee.expression.text === "vi") {
        at(node, node);
      }
      const name = rootName(callee);
      if (name !== null && TAKES_TIMEOUT.has(name)) {
        const args = node.arguments;
        const fn = args.findIndex(isFunction);
        if (fn !== -1 && fn < args.length - 1) {
          at(node, args[args.length - 1]!);
        }
        for (const arg of args) {
          if (ts.isObjectLiteralExpression(arg)) {
            for (const prop of arg.properties) {
              if (prop.name && ts.isIdentifier(prop.name) && prop.name.text === "timeout") {
                at(node, prop);
              }
            }
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return found;
}

/** The lines of each test or suite a file tags `large` (`{ tags: ["large"] }`). */
export function largeTests(source: string, file = "test.ts"): number[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const lines: number[] = [];
  const large = (node: ts.Expression): boolean =>
    (ts.isStringLiteralLike(node) && node.text === "large")
    || (ts.isArrayLiteralExpression(node) && node.elements.some(
      (element) => ts.isStringLiteralLike(element) && element.text === "large"));
  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) {
      const name = rootName(node.expression);
      if (name !== null && TAKES_TIMEOUT.has(name)) {
        for (const arg of node.arguments) {
          if (ts.isObjectLiteralExpression(arg) && arg.properties.some(
            (prop) => ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name)
              && prop.name.text === "tags" && large(prop.initializer))) {
            lines.push(sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return lines;
}
