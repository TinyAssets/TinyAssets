/**
 * Every owner write is one synchronous unit of the object's write gate (`gate.ts`), which
 * reads what it decides on itself and spans no outside call. pi's commits are no longer in
 * the owner's database at all (each thread's Session lives in its own store,
 * `thread-store.ts`), so the gate keeps no queue and no pi transaction can hold one.
 *
 * At run time the gate's storage view refuses a write outside a unit; this guard keeps the
 * source shaped so the view is the only storage the object's code writes through, and so
 * every write in an awaiting context is inside a unit:
 * - every door of `OwnerDO` (a public method taking the binding) begins in the gate: its
 *   first statement returns, or awaits, `this.#door`, `this.#synced` or `this.#filing`,
 *   and those three are the gate (`#door` is `this.#gate.write`, the others call `#door`);
 * - the object names `ctx.storage` once, to make the gate, and its modules are handed the
 *   gate's view; the one other source that names a `ctx.storage` is a thread store, which
 *   is a Durable Object of its own and names only its own; no source builds a SQLite
 *   adapter (`DurableObjectSqliteDatabase`) over the owner's storage, and no source opens an
 *   async storage transaction, which holds the input gate across its awaits;
 * - in an async function of the modules that write around outside calls (the lanes, the
 *   files' methods that cross to `FILES`, the pi environment, the schedule's job sets, the
 *   agent's notices and threads, the connections' refresh and lane, a model call's
 *   reserve, dispatch, settle and reseal), each call of a write (`WRITES`) sits inside a unit: an arrow handed
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

import { parse, reporter, workerSources } from "./sources";

const OWNER_DO = "owner/src/owner-do.ts";
const GATE = "owner/src/gate.ts";
/** A thread store: its own Durable Object, which names only its own storage. */
const STORE = "owner/src/thread-store.ts";
/** The gate's own helpers in the object, and what each must call. */
const ENTRIES: ReadonlySet<string> = new Set(["this.#door", "this.#synced", "this.#filing"]);
/** The calls whose arrow argument is a unit. */
const UNITS = /(^|\.)(#?write|unit|#door|#synced|#filing)$/;
/** By module, the calls that write: each must be in a unit wherever a function awaits. */
const WRITES: Readonly<Record<string, ReadonlySet<string>>> = Object.freeze({
  "owner/src/wakes.ts": new Set(["recover", "defer", "claim", "mark", "settle"]),
  "owner/src/files.ts": new Set(["#change", "transactionSync", "begin", "commit", "abort",
                                 "#writeInline", "ns"]),
  "owner/src/files-env.ts": new Set(["rename", "mkdir", "remove", "tempDir", "dropTemps"]),
  "owner/src/schedule.ts": new Set(["push", "cancel"]),
  "owner/src/agent.ts": new Set(["add", "record"]),
  "owner/src/threads.ts": new Set(["retain"]),
  "owner/src/connections.ts": new Set(["#lease", "#unlease", "#refreshed", "#sweep", "#owed",
                                       "#revokeOutcome"]),
  "owner/src/gateway-provider.ts": new Set(["reserve", "dispatched", "settle", "abandon",
                                             "resealed"]),
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

/** Each door of `source`'s `OwnerDO` that does not begin in the gate. */
export function ungatedDoors(source: string): string[] {
  const file = parse("owner-do.ts", source);
  const found: string[] = [];
  const entered = (node: ts.Expression | undefined): boolean => {
    let expression = node;
    if (expression !== undefined && ts.isAwaitExpression(expression)) {
      expression = expression.expression;
    }
    return expression !== undefined && ts.isCallExpression(expression)
      && ENTRIES.has(expression.expression.getText(file));
  };
  const visit = (node: ts.Node): void => {
    if (ts.isClassDeclaration(node) && node.name?.text === "OwnerDO") {
      for (const member of node.members) {
        if (!ts.isMethodDeclaration(member) || ts.isPrivateIdentifier(member.name)
            || member.parameters[0]?.type?.getText(file) !== "Binding") {
          continue;
        }
        const first = member.body?.statements[0];
        const gated = first !== undefined
          && ((ts.isReturnStatement(first) && entered(first.expression))
              || (ts.isVariableStatement(first)
                  && first.declarationList.declarations.length === 1
                  && entered(first.declarationList.declarations[0]!.initializer)
                  && ts.isAwaitExpression(first.declarationList.declarations[0]!.initializer!)));
        if (!gated) {
          found.push(member.name.getText(file));
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

/** The bodies of `OwnerDO`'s gate helpers, by name. */
function helpers(source: string): Record<string, string> {
  const file = parse("owner-do.ts", source);
  const found: Record<string, string> = {};
  const visit = (node: ts.Node): void => {
    if (ts.isMethodDeclaration(node) && ts.isPrivateIdentifier(node.name)
        && ["#door", "#synced", "#filing"].includes(node.name.text)) {
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
        const own = path === STORE && node.expression.getText(file) === "this.ctx";
        if (!made && !own) {
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

describe("write-gate guard", () => {
  const sources = workerSources();

  it("begins every door of the object in the gate", () => {
    const source = sources.get(OWNER_DO)!;
    expect(ungatedDoors(source)).toEqual([]);
    expect(source.match(/return this\.#(door|synced|filing)\(/g)?.length).toBeGreaterThan(50);
    const bodies = helpers(source);
    expect(bodies["#door"]).toBe("{\n    return this.#gate.write(act);\n  }");
    expect(bodies["#synced"]).toContain("await this.#door(act)");
    expect(bodies["#filing"]).toContain("await this.#door(act)");
  });

  it("names the owner's storage only to make the gate, and no SQLite adapter", () => {
    expect(storageReach(sources)).toEqual([]);
    expect(sources.get(OWNER_DO)).toContain("this.#gate = new WriteGate(ctx.storage);");
    expect(sources.get(GATE)).not.toContain("chord");
    expect(storageReach(new Map([[STORE, "export class S { m() { return " +
                                          "this.ctx.storage.sql; } }"]]))).toEqual([]);
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
  ])("sees an ungated door: %s", (source) => {
    expect(ungatedDoors(source)).toEqual(["fileRead"]);
  });

  it.each([
    "export class OwnerDO { fileRead(binding: Binding) { return this.#door(() => 1); } }",
    "export class OwnerDO { fileRead(binding: Binding) { return this.#filing(() => 1); } }",
    "export class OwnerDO { async fileRead(binding: Binding) { " +
      "const r = await this.#door(() => 1); return this.#files.fetch(r); } }",
  ])("allows a gated door: %s", (source) => {
    expect(ungatedDoors(source)).toEqual([]);
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
    [STORE, "export class S { m(ctx: DurableObjectState) { return ctx.storage.sql; } }"],
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
