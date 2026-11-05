/**
 * Hand-set timeouts in a test file: the static half of what `guards/timeouts.test.ts` refuses.
 *
 * A test's time budget is its size class's, set once per project (`classes.ts`), never a
 * number beside the test. Tests are held at run time, whatever the syntax: every test's
 * effective timeout must be its class's (`class-check.ts`, every project's setup file). What a run cannot see is held here, by parsing the file as TypeScript:
 * - a hook's own timeout: any argument after a hook's first (`beforeAll(setup, 30_000)`,
 *   `vitest.beforeEach(fn, LONG)`), since the hook's budget is its project's `hookTimeout`;
 * - a hook under another name (`const before = beforeAll`), which would dodge the above;
 * - `vi.setConfig` or `vitest.setConfig`, which changes the budget for the rest of a file;
 * - and, for a test, the plain forms (`it(name, fn, 60_000)`, `{ timeout: 5 }`,
 *   `{ "timeout": 5 }`), so the common case is named before anything runs.
 */
import ts from "typescript";

const TESTS = new Set(["it", "test", "describe", "suite", "bench"]);
const HOOKS = new Set(["beforeAll", "beforeEach", "afterAll", "afterEach"]);

export interface TimeoutLiteral {
  readonly line: number;
  readonly text: string;
  /** The call's own text span, for a fixer. */
  readonly call: ts.CallExpression | null;
  /** The argument or property that sets the timeout. */
  readonly node: ts.Node;
}

/**
 * `it`, `it.skip`, `it.each(rows)`, `describe.sequential`, `vitest.it` and the like: the
 * Vitest function's name.
 */
function rootName(callee: ts.Expression): string | null {
  let node: ts.Expression = callee;
  for (;;) {
    if (ts.isIdentifier(node)) {
      return node.text;
    }
    if (ts.isPropertyAccessExpression(node)) {
      if (ts.isIdentifier(node.expression) && node.expression.text === "vitest") {
        return node.name.text;
      }
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

function isTimeoutKey(name: ts.PropertyName | undefined): boolean {
  return name !== undefined
    && ((ts.isIdentifier(name) || ts.isStringLiteralLike(name)) && name.text === "timeout");
}

export function timeoutLiterals(source: string, file = "test.ts"): TimeoutLiteral[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const found: TimeoutLiteral[] = [];
  const at = (call: ts.CallExpression | null, node: ts.Node) => {
    found.push({ line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
                 text: node.getText(sf), call, node });
  };
  const visit = (node: ts.Node): void => {
    if ((ts.isVariableDeclaration(node) || ts.isBinaryExpression(node))) {
      const value = ts.isVariableDeclaration(node) ? node.initializer : node.right;
      if (value && (ts.isIdentifier(value) || ts.isPropertyAccessExpression(value))
          && HOOKS.has(rootName(value) ?? "")) {
        at(null, node);
      }
    }
    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      if (ts.isPropertyAccessExpression(callee) && callee.name.text === "setConfig"
          && ts.isIdentifier(callee.expression)
          && ["vi", "vitest"].includes(callee.expression.text)) {
        at(node, node);
      }
      const name = rootName(callee);
      const args = node.arguments;
      if (name !== null && HOOKS.has(name) && args.length > 1) {
        at(node, args[args.length - 1]!);
      }
      if (name !== null && TESTS.has(name)) {
        const fn = args.findIndex(isFunction);
        const last = args[args.length - 1];
        if (fn !== -1 && fn < args.length - 1) {
          at(node, last!);
        } else if (args.length > 2 && last !== undefined && ts.isNumericLiteral(last)) {
          at(node, last);
        }
        for (const arg of args) {
          if (ts.isObjectLiteralExpression(arg)) {
            for (const prop of arg.properties) {
              if (isTimeoutKey(prop.name)) {
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
      if (name !== null && TESTS.has(name)) {
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
