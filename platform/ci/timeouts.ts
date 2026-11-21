/**
 * Hand-set timeouts in a test file: the static half of what `guards/timeouts.test.ts` refuses.
 *
 * A test's time budget is its size class's, set once per project (`classes.ts`), never a
 * number beside the test. Tests are held at run time, whatever the syntax: every test's
 * effective timeout must be its class's (`class-check.ts`, every project's setup file).
 * What a run cannot see is held here, by parsing the file as TypeScript:
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
  const at = (node: ts.Node) => {
    found.push({ line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
                 text: node.getText(sf) });
  };
  const visit = (node: ts.Node): void => {
    if ((ts.isVariableDeclaration(node) || ts.isBinaryExpression(node))) {
      const value = ts.isVariableDeclaration(node) ? node.initializer : node.right;
      if (value && (ts.isIdentifier(value) || ts.isPropertyAccessExpression(value))
          && HOOKS.has(rootName(value) ?? "")) {
        at(node);
      }
    }
    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      if (ts.isPropertyAccessExpression(callee) && callee.name.text === "setConfig"
          && ts.isIdentifier(callee.expression)
          && ["vi", "vitest"].includes(callee.expression.text)) {
        at(node);
      }
      const name = rootName(callee);
      const args = node.arguments;
      if (name !== null && HOOKS.has(name) && args.length > 1) {
        at(args[args.length - 1]!);
      }
      if (name !== null && TESTS.has(name)) {
        const fn = args.findIndex(isFunction);
        const last = args[args.length - 1];
        if (fn !== -1 && fn < args.length - 1) {
          at(last!);
        } else if (args.length > 2 && last !== undefined && ts.isNumericLiteral(last)) {
          at(last);
        }
        for (const arg of args) {
          if (ts.isObjectLiteralExpression(arg)) {
            for (const prop of arg.properties) {
              if (isTimeoutKey(prop.name)) {
                at(prop);
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

/**
 * A test's wait on the host's clock (the untangle skill's rule: a test waits on an event it
 * caused or a record it reads, and time that must pass is the fake clock's). A timer wait is
 * an `await` of a timer's promise (`new Promise((r) => setTimeout(r, ms))`, `scheduler.wait`)
 * or of a call of one of the file's own helpers that is one, or a synchronous `Atomics.wait`,
 * for any length but zero. It waits on the clock unless it is inside a loop that polls: one
 * that leaves by a `break` or a `return`, or whose condition reads more than its own counter
 * and the clock. A timer's own callback (`setTimeout(() => unlock(), 150)`) waits for
 * nothing, and a zero-length hop orders nothing by time.
 */
export interface TimerWait {
  readonly line: number;
  readonly text: string;
}

const CLOCK = new Set(["Date.now", "performance.now"]);

const isZero = (node: ts.Expression | null): boolean =>
  node !== null && ts.isNumericLiteral(node) && Number(node.text) === 0;

const isFunctionNode = (node: ts.Node): boolean =>
  isFunction(node) || ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node);

/** The one expression a function's body is, or holds as its one statement. */
function soleExpression(body: ts.ConciseBody): ts.Expression | undefined {
  if (!ts.isBlock(body)) {
    return body;
  }
  const [only, ...rest] = body.statements;
  if (only === undefined || rest.length > 0) {
    return undefined;
  }
  return ts.isReturnStatement(only) || ts.isExpressionStatement(only) ? only.expression
    : undefined;
}

/**
 * The length of the timer `node` is a promise of (`null` when it names none), or `undefined`
 * when it is not one: `new Promise((resolve) => setTimeout(resolve, ms))`, its callback
 * `() => resolve(…)` alike, or `scheduler.wait(ms)`.
 */
function timerOf(node: ts.Expression): ts.Expression | null | undefined {
  if (ts.isParenthesizedExpression(node)) {
    return timerOf(node.expression);
  }
  if (ts.isCallExpression(node) && node.expression.getText() === "scheduler.wait") {
    return node.arguments[0] ?? null;
  }
  if (!ts.isNewExpression(node) || node.expression.getText() !== "Promise") {
    return undefined;
  }
  const executor = node.arguments?.[0];
  if (executor === undefined || !(ts.isArrowFunction(executor)
                                  || ts.isFunctionExpression(executor))) {
    return undefined;
  }
  const resolve = executor.parameters[0]?.name;
  const timer = soleExpression(executor.body);
  if (resolve === undefined || !ts.isIdentifier(resolve) || timer === undefined
      || !ts.isCallExpression(timer) || timer.expression.getText() !== "setTimeout") {
    return undefined;
  }
  const [callback, ms] = timer.arguments;
  const resolves = (each: ts.Node | undefined): boolean => each !== undefined
    && ((ts.isIdentifier(each) && each.text === resolve.text)
        || (ts.isCallExpression(each) && resolves(each.expression)));
  const later = callback !== undefined && (resolves(callback)
    || ((ts.isArrowFunction(callback) || ts.isFunctionExpression(callback))
        && resolves(soleExpression(callback.body))));
  return later ? ms ?? null : undefined;
}

const isAtomicsWait = (node: ts.Expression): node is ts.CallExpression =>
  ts.isCallExpression(node) && node.expression.getText() === "Atomics.wait";

/**
 * The file's sleep helpers, by name: each a function that is one wait, and the index of the
 * parameter its length is, or its own length.
 */
type Helpers = ReadonlyMap<string, number | ts.Expression | null>;

/** The length `node` waits, as a timer or a synchronous wait, or `undefined` if none. */
function waitOf(node: ts.Expression, awaited: boolean, helpers: Helpers):
    ts.Expression | null | undefined {
  if (awaited) {
    const timer = timerOf(node);
    if (timer !== undefined) {
      return timer;
    }
  }
  if (isAtomicsWait(node)) {
    return node.arguments[3] ?? null;
  }
  const helper = ts.isCallExpression(node) && ts.isIdentifier(node.expression)
    ? helpers.get(node.expression.text) : undefined;
  if (helper === undefined) {
    return undefined;
  }
  return typeof helper === "number" ? (node as ts.CallExpression).arguments[helper] ?? null
    : helper;
}

/** The function `node` binds to a name, as `function name` or `const name = (…) => …`. */
function named(node: ts.Node): { name: string; fn: ts.FunctionLikeDeclaration & {
  body: ts.ConciseBody } } | null {
  if (ts.isFunctionDeclaration(node) && node.name !== undefined && node.body !== undefined) {
    return { name: node.name.text, fn: node as ts.FunctionDeclaration & { body: ts.Block } };
  }
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)
      && node.initializer !== undefined
      && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) {
    return { name: node.name.text, fn: node.initializer };
  }
  return null;
}

/** `named`'s function as a sleep helper: the index of the parameter it waits, or its length. */
function helperOf(fn: ts.FunctionLikeDeclaration & { body: ts.ConciseBody }):
    number | ts.Expression | null | undefined {
  const sole = soleExpression(fn.body);
  const ms = sole === undefined ? undefined
    : isAtomicsWait(sole) ? sole.arguments[3] ?? null : timerOf(sole);
  if (ms === undefined || ms === null) {
    return ms;
  }
  const at = fn.parameters.findIndex((each) => ts.isIdentifier(each.name)
    && ts.isIdentifier(ms) && each.name.text === ms.text);
  return at >= 0 ? at : ms;
}

/** Whether `condition` reads only `counters`, the bounds they are compared with, and the clock. */
function countsOnly(condition: ts.Expression, counters: ReadonlySet<string>): boolean {
  const clock = (node: ts.Node) => ts.isCallExpression(node) && node.arguments.length === 0
    && CLOCK.has(node.expression.getText());
  const counter = (node: ts.Node) => clock(node)
    || (ts.isIdentifier(node) && counters.has(node.text));
  const only = (node: ts.Node): boolean => {
    if (ts.isCallExpression(node)) {
      return clock(node);
    }
    if (ts.isIdentifier(node)) {
      // A bound a counter or the clock is compared with is theirs.
      const parent = node.parent;
      return counter(node) || (ts.isBinaryExpression(parent)
        && (counter(parent.left) || counter(parent.right)));
    }
    if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
      return false;
    }
    let all = true;
    ts.forEachChild(node, (child) => {
      all &&= only(child);
    });
    return all;
  };
  return only(condition);
}

/** Whether `body` leaves its loop by a `break` or a `return`, not a nested function's. */
function leaves(body: ts.Node, nested = false): boolean {
  let found = false;
  const visit = (node: ts.Node, inner: boolean): void => {
    if (found || isFunctionNode(node)) {
      return;
    }
    if (ts.isReturnStatement(node)
        || (!inner && ts.isBreakStatement(node) && node.label === undefined)) {
      found = true;
      return;
    }
    const deeper = inner || ts.isIterationStatement(node, false) || ts.isSwitchStatement(node);
    ts.forEachChild(node, (child) => visit(child, deeper));
  };
  visit(body, nested);
  return found;
}

/** Whether `loop` polls: it leaves on what it reads, not on its count or the clock alone. */
function polls(loop: ts.IterationStatement): boolean {
  if (leaves(loop.statement)) {
    return true;
  }
  if (!ts.isForStatement(loop) && !ts.isWhileStatement(loop) && !ts.isDoStatement(loop)) {
    return false;
  }
  const condition = ts.isForStatement(loop) ? loop.condition : loop.expression;
  const counters = new Set<string>();
  if (ts.isForStatement(loop) && loop.initializer !== undefined
      && ts.isVariableDeclarationList(loop.initializer)) {
    for (const each of loop.initializer.declarations) {
      if (ts.isIdentifier(each.name)) {
        counters.add(each.name.text);
      }
    }
  }
  return condition !== undefined && !countsOnly(condition, counters);
}

/** The fake clock's switches in `node`, in order: `true` where it is installed, `false` off. */
function clockSwitches(node: ts.Node): { end: number; fake: boolean }[] {
  const found: { end: number; fake: boolean }[] = [];
  const visit = (each: ts.Node): void => {
    if (ts.isCallExpression(each) && ts.isPropertyAccessExpression(each.expression)
        && ["useFakeTimers", "useRealTimers"].includes(each.expression.name.text)
        && ["vi", "vitest"].includes(each.expression.expression.getText())) {
      found.push({ end: each.end, fake: each.expression.name.text === "useFakeTimers" });
    }
    ts.forEachChild(each, visit);
  };
  visit(node);
  return found;
}

/**
 * Whether `node` runs on the fake clock: after a test's body installs it (and before it turns
 * it off), or in a suite whose `beforeEach` or `beforeAll` installs it.
 */
function onFakeClock(node: ts.Node): boolean {
  for (let at = node.parent; at !== undefined; at = at.parent) {
    if (!isFunction(at) || !ts.isCallExpression(at.parent)
        || !at.parent.arguments.includes(at as ts.Expression)) {
      continue;
    }
    const name = rootName(at.parent.expression);
    const body = (at as ts.ArrowFunction).body;
    if ((name === "it" || name === "test")
        && clockSwitches(body).filter((each) => each.end <= node.pos).at(-1)?.fake === true) {
      return true;
    }
    if ((name === "describe" || name === "suite") && ts.isBlock(body)
        && body.statements.some((statement) => ts.isExpressionStatement(statement)
          && ts.isCallExpression(statement.expression)
          && ["beforeEach", "beforeAll"].includes(rootName(statement.expression.expression) ?? "")
          && clockSwitches(statement.expression).some((each) => each.fake))) {
      return true;
    }
  }
  return false;
}

/** Whether `node` is in the body of a loop that polls, within its own function. */
function polled(node: ts.Node): boolean {
  for (let child = node, at = node.parent; at !== undefined; child = at, at = at.parent) {
    if (isFunctionNode(at)) {
      return false;
    }
    if (ts.isIterationStatement(at, false) && at.statement === child && polls(at)) {
      return true;
    }
  }
  return false;
}

/** Each wait on the clock in a test's `source`, outside a loop that polls. */
export function timerWaits(source: string, file = "test.ts"): TimerWait[] {
  const kind = /\.m?js$/.test(file) ? ts.ScriptKind.JS : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind);
  const helpers = new Map<string, number | ts.Expression | null>();
  const findHelpers = (node: ts.Node): void => {
    const helper = named(node);
    const wait = helper === null ? undefined : helperOf(helper.fn);
    if (helper !== null && wait !== undefined) {
      helpers.set(helper.name, wait);
    }
    ts.forEachChild(node, findHelpers);
  };
  findHelpers(sf);
  const found: TimerWait[] = [];
  const visit = (node: ts.Node): void => {
    const helper = named(node);
    if (helper !== null && helpers.has(helper.name)) {
      return;  // a helper's own wait is its callers'
    }
    const awaited = ts.isAwaitExpression(node);
    const waited = awaited ? node.expression
      : ts.isExpressionStatement(node) ? node.expression : undefined;
    const ms = waited === undefined ? undefined : waitOf(waited, awaited, helpers);
    if (ms !== undefined && !isZero(ms) && !polled(node) && !onFakeClock(node)) {
      found.push({ line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
                   text: node.getText(sf) });
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return found;
}
