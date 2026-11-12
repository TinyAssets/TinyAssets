/**
 * Every owner write is one synchronous unit of the object's write gate (`gate.ts`), which
 * reads what it decides on itself and spans no outside call. No agent loop commits in the
 * owner's database (agents run on unmodified engines, ADR-034), so the gate keeps no queue
 * and no outside transaction can hold one.
 *
 * At run time the gate's storage view refuses a write outside a unit; this guard keeps the
 * source shaped so the view is the only storage the object's code writes through, and so
 * every write in an awaiting context is inside a unit:
 * - every door of `OwnerDO` (a public method taking the binding) begins in the gate: its
 *   first statement returns its row's runner (`this.#run`, whose every gate is a unit, or
 *   `this.#units`, whose handler's own first step is one), or returns or awaits
 *   `this.#door`, `this.#synced`, `this.#filing`, `this.#seededFor` or `this.#quiet`, and
 *   those are the gate (`#door` is `this.#gate.write`, `#quiet` its `quiet`, the others call
 *   `#door`; `#seededFor`, a file door's caller checked in a unit before it waits for a new
 *   owner's seed, slice BRAIN). A door's handler is in the module its row names
 *   (`contract/doors.json`'s `handler`: `<module>Doors.<door>`, untangle A2), handed the
 *   object's state, whose gate members (`o.door`, `o.synced`, ...) are those helpers;
 * - the object names `ctx.storage` once, to make the gate, and its modules are handed the
 *   gate's view, and no other source names a `ctx.storage`; no source builds a SQLite
 *   adapter (`DurableObjectSqliteDatabase`) over the owner's storage, and no source opens an
 *   async storage transaction, which holds the input gate across its awaits;
 * - in an async function of the modules that write around outside calls (the lanes, the
 *   files' methods that cross to `FILES`, the schedule's job sets, the connections'
 *   refresh and lane), each call of a write (`WRITES`) sits inside a unit: an arrow handed
 *   to `write`, `#write`, `unit` or a door's gate. A synchronous method is itself a unit's
 *   body, so its calls are the view's to check;
 * - in any function of the object's modules that opens a unit, async or not, no value read
 *   from SQL outside the unit is used inside it: what a unit decides on, it reads itself,
 *   since another unit may land between (the chunk GC's due list once was such a read).
 *   A synchronous function that opens a unit is not a unit's body, so its reads count.
 *   Traced: a name or a destructuring a read declares, a plain assignment of one, and what
 *   is derived from one in the same function. Not traced, the limits: a read through a
 *   module's own methods (`stat`, `lapsedClaims`), so a unit that acts on one re-checks
 *   its row in its own statement (a claim id, a state), as the lanes' units do; a read
 *   kept in a property or passed through a parameter; and a unit's own name shadowing a
 *   read's, which can only report too much.
 * The guard is a tripwire for our own code's mistakes, as the alarm guard is.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { doorRow } from "../../contract/src/doors";
import { parse, reporter, workerSources } from "../../ci/kit/sources";

const OWNER_DO = "owner/src/owner-do.ts";
const GATE = "owner/src/gate.ts";
/** The gate's own helpers in the object, and what each must call. */
const UNIT_HELPERS = Object.freeze(["door", "synced", "filing", "placing", "reading", "rereading",
                            "rereadingPage", "quiet", "seededFor"]);
/**
 * The object's gate helpers, as it calls them and as its doors' handlers do through the state
 * they are handed (`OwnerState`, whose members are those helpers: held below).
 */
const ENTRIES: ReadonlySet<string> = new Set(UNIT_HELPERS.flatMap((name) =>
  [`this.#${name}`, `o.${name}`]));
/** The calls whose arrow argument is a unit. */
const UNITS =
  /(^|\.)(#?write|unit|#?door|#?synced|#?filing|#?placing|#?reading|#?rereading|#?quiet)$/;
/** By module, the calls that write: each must be in a unit wherever a function awaits. */
const WRITES: Readonly<Record<string, ReadonlySet<string>>> = Object.freeze({
  "owner/src/wakes.ts": new Set(["recover", "defer", "claim", "mark", "settle"]),
  "owner/src/files.ts": new Set(["#change", "transactionSync", "begin", "commit", "abort",
                                 "#writeInline", "ns"]),
  "owner/src/schedule.ts": new Set(["push", "cancel"]),
  "owner/src/connections.ts": new Set(["#lease", "#unlease", "#refreshed", "#sweep", "#owed",
                                       "#revokeOutcome", "#keepResealed"]),
});

function isAsync(node: ts.Node): boolean {
  return ts.canHaveModifiers(node)
    && (ts.getModifiers(node) ?? []).some((m) => m.kind === ts.SyntaxKind.AsyncKeyword);
}

/** Whether `fn` is the unit an enclosing gate call is handed. */
function unitArgument(fn: ts.Node, file: ts.SourceFile): boolean {
  const call = fn.parent;
  return call !== undefined && ts.isCallExpression(call) && call.arguments.includes(fn as never)
    && UNITS.test(call.expression.getText(file));
}

/** Each write in an awaiting context of `source` that no unit holds, as `file:line: what`. */
export function ungatedWrites(fileName: string, source: string,
                              writes: ReadonlySet<string>): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
        && writes.has(node.expression.name.getText(file))) {
      for (let up: ts.Node | undefined = node.parent; up !== undefined; up = up.parent) {
        if (ts.isFunctionLike(up)) {
          if (unitArgument(up, file)) {
            break;  // inside a unit
          }
          if (isAsync(up)) {
            report(node, `${node.expression.getText(file)}() is outside a unit of the gate`);
            break;
          }
          if (!ts.isArrowFunction(up) && !ts.isFunctionExpression(up)) {
            break;  // a synchronous method or function: a unit's body, the view's to check
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

/** The function `node` is in, if any. */
function enclosing(node: ts.Node): ts.Node | undefined {
  for (let up = node.parent; up !== undefined; up = up.parent) {
    if (ts.isFunctionLike(up)) {
      return up;
    }
  }
  return undefined;
}

/**
 * Each value a function of `source` reads from SQL outside any unit and then uses inside a
 * unit it opens, as `file:line: what`. A value is read when its declaration (a name or
 * each name a destructuring binds) or a plain assignment to it takes a `.exec(` of the
 * function's own, or a value already read: `rows.map(...)` of a read `rows` is read too.
 */
export function staleReads(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const within = (node: ts.Node, test: (n: ts.Node) => boolean): boolean =>
    test(node) || (ts.forEachChild(node, (n) => within(n, test) || undefined) ?? false);
  const bound = (name: ts.BindingName): string[] => ts.isIdentifier(name) ? [name.text]
    : name.elements.flatMap((element) => ts.isOmittedExpression(element) ? []
      : bound(element.name));
  const inUnit = (node: ts.Node, fn: ts.Node): boolean => {
    for (let up = enclosing(node); up !== undefined && up !== fn; up = enclosing(up)) {
      if (unitArgument(up, file)) {
        return true;
      }
    }
    return false;
  };
  const check = (fn: ts.SignatureDeclaration): void => {
    const body = (fn as { body?: ts.Node }).body;
    if (body === undefined || unitArgument(fn, file)) {
      return;  // a unit's own reads are the unit's
    }
    const read = new Map<string, ts.Node>();
    const reads = (expression: ts.Node): boolean => within(expression, (n) =>
      enclosing(n) === fn
      && ((ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)
           && n.expression.name.text === "exec")
          || (ts.isIdentifier(n) && read.has(n.text))));
    const walk = (node: ts.Node): void => {  // in source order, so a read flows forward
      if (enclosing(node) === fn) {
        if (ts.isVariableDeclaration(node) && node.initializer !== undefined
            && reads(node.initializer)) {
          bound(node.name).forEach((name) => read.set(name, node));
        } else if (ts.isBinaryExpression(node)
                   && node.operatorToken.kind === ts.SyntaxKind.EqualsToken
                   && ts.isIdentifier(node.left) && reads(node.right)) {
          read.set(node.left.text, node);
        }
      }
      ts.forEachChild(node, walk);
    };
    walk(body);
    for (const [name, at] of read) {
      if (within(body, (n) => ts.isIdentifier(n) && n.text === name && inUnit(n, fn))) {
        report(at, `${name} is read outside a unit and used inside one`);
      }
    }
  };
  const visit = (node: ts.Node): void => {
    if (ts.isFunctionLike(node)) {
      check(node);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

/** The camel-case name of a handler module's doors (`sandbox-time` -> `sandboxTimeDoors`). */
export function handlerName(module: string): string {
  return `${module.replace(/-(\w)/g, (_, letter: string) => letter.toUpperCase())}Doors`;
}

/**
 * The door handlers of the owner's modules: each module's `export const <module>Doors =
 * Object.freeze({ ... })`, by `<module>Doors.<door>`, with the module that holds it.
 */
export function doorHandlers(sources: ReadonlyMap<string, string>):
    Map<string, { module: string; body: ts.Block | undefined }> {
  const found = new Map<string, { module: string; body: ts.Block | undefined }>();
  for (const [path, source] of sources) {
    if (!path.startsWith("owner/src/") || !source.includes("Doors = Object.freeze({")) {
      continue;
    }
    const file = parse(path, source);
    const visit = (node: ts.Node): void => {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)
          && node.name.text.endsWith("Doors") && node.initializer !== undefined
          && ts.isCallExpression(node.initializer)
          && ts.isObjectLiteralExpression(node.initializer.arguments[0]!)) {
        for (const member of node.initializer.arguments[0].properties) {
          if (ts.isMethodDeclaration(member) && ts.isIdentifier(member.name)) {
            found.set(`${node.name.text}.${member.name.text}`,
                      { module: path.slice("owner/src/".length, -".ts".length),
                        body: member.body });
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return found;
}

/**
 * Each door of `source`'s `OwnerDO` that does not begin in the gate, its handlers in their
 * modules read from `handlers` (`doorHandlers`).
 */
export function ungatedDoors(source: string,
                             handlers: ReturnType<typeof doorHandlers> = new Map()): string[] {
  const file = parse("owner-do.ts", source);
  const found: string[] = [];
  const own = new Map<string, ts.MethodDeclaration>();
  const entered = (node: ts.Expression | undefined): boolean => {
    let expression = node;
    if (expression !== undefined && ts.isAwaitExpression(expression)) {
      expression = expression.expression;
    }
    return expression !== undefined && ts.isCallExpression(expression)
      && ENTRIES.has(expression.expression.getText());
  };
  // A statement that begins in the gate: it returns, or awaits, a unit.
  const begins = (first: ts.Statement | undefined): boolean => first !== undefined
    && ((ts.isReturnStatement(first) && entered(first.expression))
        || (ts.isExpressionStatement(first) && ts.isAwaitExpression(first.expression)
            && entered(first.expression))
        || (ts.isVariableStatement(first)
            && first.declarationList.declarations.length === 1
            && entered(first.declarationList.declarations[0]!.initializer)
            && ts.isAwaitExpression(first.declarationList.declarations[0]!.initializer!)));
  // A `#units` handler begins in the gate: its body, or the one private method it hands the
  // call to, begins so; a handler in its module (`<module>Doors.<door>`), its own body.
  const handled = (handler: ts.Expression | undefined): boolean => {
    if (handler !== undefined && ts.isPropertyAccessExpression(handler)) {
      return begins(handlers.get(handler.getText(file))?.body?.statements[0]);
    }
    if (handler === undefined || !ts.isArrowFunction(handler)) {
      return false;
    }
    const body = handler.body;
    if (ts.isBlock(body)) {
      return begins(body.statements[0]);
    }
    if (entered(body)) {
      return true;
    }
    const call = ts.isCallExpression(body) ? body.expression : undefined;
    const method = call !== undefined && ts.isPropertyAccessExpression(call)
      && call.expression.kind === ts.SyntaxKind.ThisKeyword
      && ts.isPrivateIdentifier(call.name) ? own.get(call.name.text) : undefined;
    return method !== undefined && begins(method.body?.statements[0]);
  };
  const run = (first: ts.Statement | undefined): boolean => {
    if (first === undefined || !ts.isReturnStatement(first) || first.expression === undefined
        || !ts.isCallExpression(first.expression)) {
      return false;
    }
    const called = first.expression.expression.getText(file);
    return called === "this.#run"
      || (called === "this.#units" && handled(first.expression.arguments[3]));
  };
  const visit = (node: ts.Node): void => {
    if (ts.isClassDeclaration(node) && node.name?.text === "OwnerDO") {
      for (const member of node.members) {
        if (ts.isMethodDeclaration(member) && ts.isPrivateIdentifier(member.name)) {
          own.set(member.name.text, member);
        }
      }
      for (const member of node.members) {
        if (!ts.isMethodDeclaration(member) || ts.isPrivateIdentifier(member.name)
            || member.parameters[0]?.type?.getText(file) !== "Binding") {
          continue;
        }
        const first = member.body?.statements[0];
        if (!begins(first) && !run(first)) {
          found.push(member.name.getText(file));
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

/**
 * The state's members that hold no storage of the gate's: the app's sockets, the events
 * posts' count, and the env's bindings (R2, the sandbox's service).
 */
const NOT_STORED: ReadonlySet<string> = new Set(["relay", "posts", "env"]);
/** Each gate helper of the state, and which of its arguments run in its unit. */
const UNIT_ARGUMENTS: Readonly<Record<string, readonly number[]>> = Object.freeze({
  door: [0], quiet: [0], synced: [0], filing: [0], placing: [0, 1], reading: [0],
  rereading: [0, 1], rereadingPage: [0, 1],
});
/**
 * The stores' members a handler may call outside a unit: each makes its own units, or
 * reads what the gate does not hold (R2, a sandbox), or reads only what its unit read.
 */
const OWN_UNITS: ReadonlySet<string> = new Set([
  // Each writes in units of its own around its R2, sandbox or gateway calls.
  "files.write", "files.writeChunks", "files.upload", "files.rewrap", "files.space",
  "connections.fresh", "connections.reseal", "erasure.begin", "launcher.seeded", "launcher.launch",
  "launcher.cancel", "launcher.reapply",
  // Each reads in units of its own, or R2 alone.
  "files.fetch", "archive.archived", "archive.chain", "launcher.readAgent",
  "launcher.settledWithin",
  // A placed body sent to R2 before its unit, and released after it.
  "bodies.send", "bodies.release",
  // An export's page: its generation's rows read once its own unit read the export.
  "backups.finished", "backups.object",
]);

/**
 * Each call of the state's stores (`o.<store>.<member>(…)`) in a units door handler
 * (`<module>Doors`, an `async` member) made outside a unit of the gate, as `file:line:
 * what`, in any position: a statement, a value assigned, an awaited call, a condition. A
 * call in a unit's own argument (`UNIT_ARGUMENTS`) is the unit's. A store that holds no
 * storage of the gate's (`NOT_STORED`) and a member that makes its own units
 * (`OWN_UNITS`) are let be. A function of the module's own handed the state outside a unit
 * is followed into, under its parameter's name: its calls are held the same. A function the
 * handler names runs where it is called, so it is held where it is called or handed on
 * outside a unit (one handed to a member that makes its own units runs in them). A helper
 * of another module handed the state is not followed: the state's own guard
 * (policy-boundary's) holds what it may name.
 */
export function strayCalls(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const helpers =
    new Map<string, ts.FunctionDeclaration | ts.ArrowFunction | ts.FunctionExpression>();
  for (const statement of file.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name !== undefined) {
      helpers.set(statement.name.text, statement);
    } else if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        const value = declaration.initializer;
        if (value !== undefined && (ts.isArrowFunction(value) || ts.isFunctionExpression(value))) {
          helpers.set(declaration.name.getText(file), value);
        }
      }
    }
  }
  /** Whether `fn` is an argument its call runs in a unit: `root.door(() => …)`. */
  const unitArgument = (fn: ts.Node, root: string): boolean => {
    const call = fn.parent;
    if (!ts.isCallExpression(call) || !ts.isPropertyAccessExpression(call.expression)
        || call.expression.expression.getText(file) !== root) {
      return false;
    }
    const units = UNIT_ARGUMENTS[call.expression.name.text];
    return units !== undefined && units.includes(call.arguments.indexOf(fn as ts.Expression));
  };
  const followed = new Set<ts.Node>();
  /** Whether `node` lies in the arguments of a call of a member that makes its own units. */
  const handedToOwnUnits = (node: ts.Node, store: RegExp, body: ts.Node): boolean => {
    for (let up = node.parent; up !== undefined && up !== body; up = up.parent) {
      if (ts.isCallExpression(up) && up.arguments.some((arg) => arg.pos <= node.pos
                                                         && node.end <= arg.end)) {
        const named = store.exec(up.expression.getText(file));
        if (named !== null && OWN_UNITS.has(`${named[1]}.${named[2]}`)) {
          return true;
        }
      }
    }
    return false;
  };
  const walk = (body: ts.Node, root: string, via: string): void => {
    const store = new RegExp(`^${root}\\.(\\w+)\\.(\\w+)`);
    // A function a handler names (`const kept = () => …`) runs where it is called: walked
    // at a call or a hand-off outside a unit, not where it is made; one handed to a member
    // that makes its own units runs in them.
    const closures = new Map<string, ts.ArrowFunction | ts.FunctionExpression>();
    const visit = (n: ts.Node): void => {
      if ((ts.isArrowFunction(n) || ts.isFunctionExpression(n)) && unitArgument(n, root)) {
        return;
      }
      if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer !== undefined
          && (ts.isArrowFunction(n.initializer) || ts.isFunctionExpression(n.initializer))) {
        closures.set(n.name.text, n.initializer);
        return;
      }
      if (ts.isIdentifier(n) && closures.has(n.text)
          && !(ts.isPropertyAccessExpression(n.parent) && n.parent.name === n)
          && !handedToOwnUnits(n, store, body)) {
        const closure = closures.get(n.text)!;
        if (!followed.has(closure)) {
          followed.add(closure);
          visit(closure.body);
        }
      }
      if (ts.isCallExpression(n)) {
        const callee = n.expression.getText(file);
        const named = store.exec(callee);
        if (named !== null && !NOT_STORED.has(named[1]!)
            && !OWN_UNITS.has(`${named[1]}.${named[2]}`)) {
          report(n, `${callee}() is outside a unit of the gate${via}`);
        }
        const helper = ts.isIdentifier(n.expression) ? helpers.get(n.expression.text) : undefined;
        const index = n.arguments.findIndex((arg) => arg.getText(file) === root);
        if (helper !== undefined && index >= 0 && index < helper.parameters.length
            && helper.body !== undefined && !followed.has(helper)) {
          followed.add(helper);
          walk(helper.body, helper.parameters[index]!.name.getText(file),
               ` (in ${n.expression.getText(file)}, handed the state)`);
          followed.delete(helper);
        }
      }
      ts.forEachChild(n, visit);
    };
    visit(body);
  };
  const handler = (node: ts.Node): node is ts.MethodDeclaration => ts.isMethodDeclaration(node)
    && ts.isObjectLiteralExpression(node.parent) && ts.isCallExpression(node.parent.parent)
    && ts.isVariableDeclaration(node.parent.parent.parent)
    && node.parent.parent.parent.name.getText(file).endsWith("Doors") && isAsync(node);
  const visit = (node: ts.Node): void => {
    if (handler(node) && node.body !== undefined && node.parameters.length > 0) {
      walk(node.body, node.parameters[0]!.name.getText(file), "");
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

/** The bodies of `OwnerDO`'s gate helpers, by name. */
function helpers(source: string): Record<string, string> {
  const file = parse("owner-do.ts", source);
  const found: Record<string, string> = {};
  const visit = (node: ts.Node): void => {
    if (ts.isMethodDeclaration(node) && ts.isPrivateIdentifier(node.name)
        && ["#door", "#synced", "#filing", "#placing", "#reading", "#rereading",
            "#rereadingPage", "#quiet", "#run", "#stateOf",
            "#seededFor"].includes(node.name.text)) {
      found[node.name.text] = node.body?.getText(file) ?? "";
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

/** Each place a source reaches the storage or the queue past the gate. */
export function storageReach(sources: ReadonlyMap<string, string>): string[] {
  const problems: string[] = [];
  for (const [path, source] of sources) {
    if (!path.startsWith("owner/src/")) {
      continue;
    }
    const file = parse(path, source);
    const report = reporter(file, problems);
    const visit = (node: ts.Node): void => {
      if (ts.isPropertyAccessExpression(node) && node.name.text === "storage"
          && /(^|\.)ctx$/.test(node.expression.getText(file))) {
        const made = path === OWNER_DO && ts.isNewExpression(node.parent)
          && node.parent.expression.getText(file) === "WriteGate"
          && node.expression.getText(file) === "ctx";
        if (!made) {
          report(node, `names ${node.getText(file)} past the write gate`);
        }
      }
      if (ts.isIdentifier(node) && node.text === "DurableObjectSqliteDatabase") {
        report(node, "builds a SQLite adapter over a storage: writes past the gate");
      }
      if (ts.isPropertyAccessExpression(node) && node.name.text === "transaction"
          && ts.isCallExpression(node.parent) && node.parent.expression === node) {
        report(node, "opens an async storage transaction, which holds the input gate");
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return problems;
}

/** A module's door handlers, for the probes: one ungated, one gated. */
const HANDLERS = "export const filesDoors = Object.freeze({\n" +
  "  async fileRead(o: S, door: D) { return o.files.read(); },\n" +
  "  async fileGated(o: S, door: D) {\n" +
  "    const r = await o.door(() => 1);\n    return o.files.fetch(r);\n  },\n" +
  "});";

describe("write-gate guard", () => {
  const sources = workerSources();

  it("begins every door of the object in the gate", () => {
    const source = sources.get(OWNER_DO)!;
    expect(ungatedDoors(source, doorHandlers(sources))).toEqual([]);
    expect(source.match(/return this\.#(run|units)\(/g)?.length).toBeGreaterThan(100);
    const bodies = helpers(source);
    // The runner's every gate is a unit of the gate.
    for (const gate of ["#door", "#quiet", "#synced", "#filing"]) {
      expect(bodies["#run"]).toContain(`return this.${gate}(unit);`);
    }
    expect(bodies["#door"]).toBe("{\n    return this.#gate.write(act);\n  }");
    // A quiet unit is a unit of the same gate, which marks nothing changed (`gate.ts`).
    expect(bodies["#quiet"]).toBe("{\n    return this.#gate.quiet(act);\n  }");
    expect(bodies["#synced"]).toContain("await this.#door(act)");
    expect(bodies["#filing"]).toContain("await this.#door(act)");
    expect(bodies["#seededFor"]).toMatch(/^\{\n    await this\.#door\(/);
    // The doors with a body (ADR-035): each begins in a unit, and every step that writes is
    // one, the R2 calls between them.
    expect(bodies["#placing"]).toContain("first = await this.#door(");
    expect(bodies["#placing"]).toContain(
      "done = await this.#door(() => located(this.#files.held(writer, () => act(placed))));");
    expect(bodies["#reading"]).toContain("const { value, located, wants } = await this.#door(");
    expect(bodies["#rereading"]).toContain("const located = await this.#door(");
    expect(bodies["#rereading"]).toContain("const value = await this.#door(() => read(bodies));");
    // The handlers' gate members are the object's helpers.
    const state = bodies["#stateOf"]!;
    for (const name of UNIT_HELPERS) {
      expect(state).toMatch(new RegExp(`\\n      ${name}: [^]*?=>\\s*object\\.#${name}\\(`));
    }
  });

  it("hands each door its handler from the module its row names", () => {
    const handlers = doorHandlers(sources);
    const file = parse(OWNER_DO, sources.get(OWNER_DO)!);
    const handed: string[] = [];
    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node) && /^this\.#(run|units)$/.test(node.expression.getText(file))
          && node.arguments.length === 4 && ts.isStringLiteral(node.arguments[0]!)) {
        const door = node.arguments[0].text;
        const handler = node.arguments[3]!.getText(file);
        const held = handlers.get(handler);
        handed.push(door);
        expect([door, handler, held?.module])
          .toEqual([door, `${handlerName(doorRow(door).handler)}.${door}`, doorRow(door).handler]);
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    expect(handed.length).toBeGreaterThan(100);
    expect(new Set(handed).size).toBe(handed.length);
    expect(handlers.size).toBe(handed.length);
  });

  it("holds every statement of a door's handler in the gate, not only its first", () => {
    const handlers = [...sources].filter(([path, source]) => path.startsWith("owner/src/")
      && source.includes("Doors = Object.freeze({"));
    expect(handlers.length).toBeGreaterThan(20);
    expect(handlers.flatMap(([path, source]) => strayCalls(path, source))).toEqual([]);
  });

  it.each([
    ["a ledger write after the first unit",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { await o.door(() => 1); " +
       "o.ledger.settled(1, 2); } });", 1],
    ["a ledger write assigned to a value",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { await o.door(() => 1); " +
       "const stray = o.ledger.settled(1, 2); } });", 1],
    ["a synchronous ledger write awaited",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { await o.door(() => 1); " +
       "await o.ledger.settled(1, 2); } });", 1],
    ["a ledger write in a condition",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { await o.door(() => 1); " +
       "if (o.ledger.settled(1, 2) === undefined) {} } });", 1],
    ["a ledger write a helper of the module makes, handed the state",
     "function stray(s: S) { s.ledger.settled(1, 2); } const xDoors = Object.freeze({ " +
       "async d(o: S, door: D) { await o.door(() => 1); stray(o); } });", 1],
    ["a ledger write in a function the handler calls outside a unit",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { const f = () => " +
       "o.ledger.settled(1, 2); await o.door(() => 1); f(); } });", 1],
    ["a read door's fill, which runs after its unit",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { return o.reading(() => 1, " +
       "(value) => o.ledger.settled(value, 2)); } });", 1],
    ["a write in a unit, an awaited outside call and the sockets' own",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { await o.door(() => { " +
       "o.ledger.settled(1, 2); }); await o.files.write(1); o.relay.settled(1); } });", 0],
    ["a function the handler calls in a unit, or hands a member making its own",
     "const xDoors = Object.freeze({ async d(o: S, door: D) { const f = () => " +
       "o.ledger.settled(1, 2); await o.door(() => f()); " +
       "await o.files.write(1, { authorize: f }); } });", 0],
    ["a helper handed the state in a unit",
     "function stray(s: S) { s.ledger.settled(1, 2); } const xDoors = Object.freeze({ " +
       "async d(o: S, door: D) { await o.door(() => stray(o)); } });", 0],
  ])("sees %s", (_what, source, found) => {
    expect(strayCalls("probe.ts", source)).toHaveLength(found);
  });

  it("names the owner's storage only to make the gate, and no SQLite adapter", () => {
    expect(storageReach(sources)).toEqual([]);
    expect(sources.get(OWNER_DO)).toContain("this.#gate = new WriteGate(ctx.storage);");
    expect(sources.get(GATE)).not.toContain("chord");
  });

  it("holds every write of an awaiting function in a unit", () => {
    for (const [path, writes] of Object.entries(WRITES)) {
      expect(sources.has(path)).toBe(true);
      expect(ungatedWrites(path, sources.get(path)!, writes)).toEqual([]);
      // Each write named is one the module calls, so the list cannot go stale or empty.
      expect(writes.size).toBeGreaterThan(0);
      for (const write of writes) {
        expect([path, sources.get(path)!.includes(`.${write}(`)]).toEqual([path, true]);
      }
    }
  });

  it.each([
    "export class OwnerDO { fileRead(binding: Binding) { return this.#files.read(); } }",
    "export class OwnerDO { fileRead(binding: Binding) { this.#check(); " +
      "return this.#door(() => 1); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#other(() => 1); } }",
    "export class OwnerDO { async fileRead(binding: Binding) { const r = this.#door(() => 1); " +
      "return r; } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#units('fileRead', " +
      "binding, 1, (door) => this.#files.read()); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#units('fileRead', " +
      "binding, 1, (door) => this.#read(door)); } #read(door: D) { return this.#files.read(); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#units('fileRead', " +
      "binding, 1, filesDoors.fileRead); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#units('fileRead', " +
      "binding, 1, filesDoors.fileOther); } }",
  ])("sees an ungated door: %s", (source) => {
    expect(ungatedDoors(source, doorHandlers(new Map([["owner/src/files.ts", HANDLERS]]))))
      .toEqual(["fileRead"]);
  });

  it.each([
    "export class OwnerDO { fileRead(binding: Binding) { return this.#door(() => 1); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#filing(() => 1); } }",
    "export class OwnerDO { async fileRead(binding: Binding) { " +
      "const r = await this.#door(() => 1); return this.#files.fetch(r); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#run('fileRead', " +
      "binding, 1, (call) => 1); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#units('fileRead', " +
      "binding, 1, (door) => this.#read(door)); } #read(door: D) { return this.#door(() => 1); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#units('fileRead', " +
      "binding, 1, filesDoors.fileGated); } }",
  ])("allows a gated door: %s", (source) => {
    expect(ungatedDoors(source, doorHandlers(new Map([["owner/src/files.ts", HANDLERS]]))))
      .toEqual([]);
  });

  it.each([
    ["owner/src/x.ts", "export class O { m(ctx: DurableObjectState) { ctx.storage.sql; } }"],
    ["owner/src/x.ts", "export class O { m() { return this.ctx.storage; } }"],
    [OWNER_DO, "export class O { constructor(ctx: X) { new Files(ctx.storage); } }"],
    ["owner/src/x.ts", "import { DurableObjectSqliteDatabase } from 'c';"],
    ["owner/src/x.ts", "export async function f(s: DurableObjectStorage) { " +
      "await s.transaction(async () => 1); }"],
    [GATE, "export class G { write = (u: U) => this.#db.transaction(async () => u()); }"],
    [GATE, "import { DurableObjectSqliteDatabase } from 'c';"],
    ["owner/src/thread-store.ts", "export class S { m() { return this.ctx.storage.sql; } }"],
    ["owner/src/x.ts", "export class S { m() { return this.ctx.storage.sql; } }"],
  ])("sees a reach past the gate in %s: %s", (path, source) => {
    expect(storageReach(new Map([[path, source]]))).toHaveLength(1);
  });

  it("lets no function act in a unit on a read made outside it", () => {
    const owner = [...sources].filter(([path]) => path.startsWith("owner/src/"));
    expect(owner.length).toBeGreaterThan(10);
    expect(owner.flatMap(([path, source]) => staleReads(path, source))).toEqual([]);
  });

  it.each([
    "export class F { async c() { const due = sql.exec('SELECT 1').toArray(); " +
      "await this.#write(() => { for (const d of due) { sql.exec('UPDATE x'); } }); } }",
    "export async function f(write: W) { const n = s.sql.exec('SELECT 1').one(); " +
      "await write(() => t.transactionSync(() => n)); }",
    // A synchronous function that opens the unit itself: the same race.
    "export class F { c() { const n = sql.exec('SELECT 1').one(); return write(() => n); } }",
    // A destructuring, a value derived from a read, and a read assigned later.
    "export async function f() { const [x] = sql.exec('SELECT 1').toArray(); " +
      "await write(() => use(x)); }",
    "export async function f() { const { a: { b } } = sql.exec('SELECT 1').one(); " +
      "await write(() => use(b)); }",
    "export async function f() { const rows = sql.exec('SELECT 1').toArray(); " +
      "const ids = rows.map((r) => r.id); await f(rows); await write(() => use(ids)); }",
    "export async function f() { let x = 0; await g(); x = sql.exec('SELECT 1').one().n; " +
      "await write(() => use(x)); }",
  ])("sees a unit acting on a read made outside it: %s", (source) => {
    expect(staleReads("probe.ts", source)).toHaveLength(1);
  });

  it.each([
    "export class F { async c() { const due = await this.#write(() => " +
      "sql.exec('SELECT 1').toArray()); for (const d of due) { await f(d); } } }",
    "export class F { async c() { const n = sql.exec('SELECT 1').one(); await f(n); } }",
    // A unit's body reads for itself, synchronous methods it calls included.
    "export class F { m() { const n = sql.exec('SELECT 1').one(); return n + 1; } }",
    "export async function f() { await write(() => { const [x] = sql.exec('S').toArray(); " +
      "return t.transactionSync(() => x); }); }",
  ])("allows a read made in its unit, or used outside any: %s", (source) => {
    expect(staleReads("probe.ts", source)).toEqual([]);
  });

  it.each([
    "export async function run(l: L, write: W) { l.claim(1); }",
    "export async function run(l: L, write: W) { await write(() => 1); l.settle(1); }",
    "export async function run(l: L, write: W) { const f = () => l.mark(1); await write(f); }",
    "export class F { async write() { this.#change(() => 1); } }",
    "export async function run(l: L, write: W) { await sender.send(t, async () => { " +
      "l.mark(1); }); }",
  ])("sees a write outside a unit: %s", (source) => {
    expect(ungatedWrites("probe.ts", source,
                         new Set(["claim", "settle", "mark", "#change"]))).toHaveLength(1);
  });

  it.each([
    "export async function run(l: L, write: W) { await write(() => l.claim(1)); }",
    "export async function run(l: L, write: W) { l.lapsed(1); await write(() => { " +
      "l.settle(1); l.mark(1); }); }",
    "export class F { mkdir() { return this.#change(() => 1); } }",
    "export class F { async write() { return this.#write(() => this.#change(() => 1)); } }",
    "export class E { async m() { await this.#files.unit(() => this.#files.claim(1)); } }",
  ])("allows a write inside a unit: %s", (source) => {
    expect(ungatedWrites("probe.ts", source,
                         new Set(["claim", "settle", "mark", "#change"]))).toEqual([]);
  });
});
