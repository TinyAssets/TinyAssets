/**
 * The object's one alarm is Lifecycle's (ADR-032, tasks 3.6).
 *
 * A Durable Object has one alarm, and the Agents SDK's Lifecycle owns it: its job queue
 * derives the alarm from every queued job, each agent thread's wake job among them, so a
 * source that set, read or cleared the alarm itself would overwrite theirs. So a Worker
 * source:
 * - never names `setAlarm`, `deleteAlarm`, `getAlarm`, Lifecycle's `disableAlarms` or the
 *   storage's `deleteAll` (which takes the queue with it), as an identifier, a property or
 *   a string, and holds no string with `Alarm` in it, so the obvious split spellings fail;
 *   three exceptions (`WIPES`), each by identifier only: a thread store
 *   (`thread-store.ts`), a Durable Object of its own that holds no Lifecycle and no alarm,
 *   wipes itself whole with `deleteAll`; and the owner's erasure, whose last step disables
 *   Lifecycle's alarm for good (`erase.ts`, `disableAlarms`) and then has the write gate,
 *   the one holder of the owner's raw storage, delete the whole database (`gate.ts`,
 *   `deleteAll`), so Lifecycle writes nothing after it;
 * - reads `ctx.storage` and a Lifecycle only by literal names, never by a computed one,
 *   and gives no class or object member a computed name but a literal;
 * - imports from the `agents` package only what `AGENTS_IMPORTS` lists, and loads no
 *   module by a name computed at run time;
 * - never installs Lifecycle's runtime handlers (`Lifecycle.install`, `installHandlers`),
 *   which would add a `fetch` and WebSocket handlers to the object's surface;
 * - has one `alarm`, the hand-off `await this.#lifecycle.alarm();`.
 * Lifecycle's own tables (`cf_agents_*`) and pi's (`pi_*`, in thread stores only) are named
 * by no source (the policy-boundary guard). The guard is a tripwire for our own code's
 * mistakes, not proof against a source written to evade it.
 *
 * `agents/lifecycle` is the Agents SDK module the object loads, pinned here by version and
 * lockfile integrity (agents 0.31.0, source-read 2026-10-28); PiHarness is not loaded (the
 * thread host, `thread-host.ts`, replaces it). Its module-level state, which objects sharing
 * an isolate share, holds no owner's data: `WeakMap`s keyed by each Lifecycle or capability
 * instance (event sinks, route transports, host invokers, bound services), one
 * `AsyncLocalStorage` whose store lives only for the invocation that set it
 * (`current-agent.ts`), and `node:diagnostics_channel` channels, which publish only to
 * subscribers and this Worker has none. Lifecycle's `nanoid` pool is never drawn on, since
 * every job id here is given. It reads no `env` or `exports`. pi's storage is opened only in
 * a thread store, over that store's own `ctx.storage`; pi's own versions are the pi pin
 * guard's.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, PLATFORM, reporter, workerSources } from "./sources";

const BANNED: ReadonlySet<string> = new Set(["setAlarm", "deleteAlarm", "getAlarm",
                                             "disableAlarms", "deleteAll"]);
/** What a source may import from the Agents SDK: Lifecycle alone. */
const AGENTS_IMPORTS: ReadonlySet<string> = new Set(["agents/lifecycle"]);
/** The one source that may name `deleteAll`: a thread store, which holds no Lifecycle. */
const STORE = "owner/src/thread-store.ts";
/** The erasure's capability and the write gate, whose wipe is the erasure's last step. */
const ERASE = "owner/src/erase.ts";
const GATE = "owner/src/gate.ts";
/** The banned names each source may still use, as an identifier only. */
const WIPES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  [STORE]: Object.freeze(["deleteAll"]),
  [GATE]: Object.freeze(["deleteAll"]),
  [ERASE]: Object.freeze(["disableAlarms"]),
});
const HAND_OFF = "await this.#lifecycle.alarm();";
/** The pinned Agents SDK, as the lockfile resolves it. */
const AGENTS = Object.freeze({
  version: "0.31.0",
  integrity: "sha512-FQeIsuMXycGPtQUxpRcT3ArVe8tkhGkioGLQ1kKRIzDCbYBOyx3RzAxi+G2dq6pku+" +
    "uUqR04QYg+i3BzDouMnA==",
});

function agentsModule(node: ts.Node): node is ts.StringLiteralLike {
  return ts.isStringLiteralLike(node)
    && (node.text === "agents" || node.text.startsWith("agents/"));
}

function unwrapped(node: ts.Expression): ts.Expression {
  let current = node;
  while (ts.isParenthesizedExpression(current) || ts.isAsExpression(current)
         || ts.isNonNullExpression(current) || ts.isSatisfiesExpression(current)
         || ts.isTypeAssertionExpression(current)) {
    current = current.expression;
  }
  return current;
}

/** Whether `node` is a storage or a Lifecycle, by the names a source reaches them by. */
function guarded(node: ts.Expression, file: ts.SourceFile): boolean {
  const inner = unwrapped(node);
  const name = ts.isPropertyAccessExpression(inner) ? inner.name.text
    : ts.isIdentifier(inner) ? inner.text : null;
  return name !== null && /^#?(storage|lifecycle)$/i.test(name.replace(/^#/, ""))
    || /\.(#?lifecycle|storage)$/.test(inner.getText(file));
}

/** A member's name as written: a literal computed name is its text. */
function memberName(name: ts.PropertyName, file: ts.SourceFile): string {
  if (ts.isComputedPropertyName(name)) {
    const inner = unwrapped(name.expression);
    return ts.isStringLiteralLike(inner) ? inner.text : name.getText(file);
  }
  return name.getText(file);
}

/** Each way `source` reaches the alarm past Lifecycle, as `file:line: what`. */
export function alarmReach(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node))
        && BANNED.has(node.text) && !((WIPES[fileName] ?? []).includes(node.text)
                                      && ts.isIdentifier(node))) {
      report(node, `names ${node.text}, which only Lifecycle calls`);
    } else if ((ts.isStringLiteralLike(node) || ts.isTemplateHead(node)
                || ts.isTemplateMiddle(node) || ts.isTemplateTail(node))
               && /Alarm/.test(node.text)) {
      report(node, "spells an alarm call in a string");
    }
    if (ts.isElementAccessExpression(node) && guarded(node.expression, file)
        && !ts.isStringLiteralLike(unwrapped(node.argumentExpression))) {
      report(node, "reaches a storage or a Lifecycle by a computed name");
    }
    if (ts.isComputedPropertyName(node) && !ts.isStringLiteralLike(unwrapped(node.expression))
        && !ts.isNumericLiteral(unwrapped(node.expression))) {
      report(node, "names a member by a computed name");
    }
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier !== undefined && agentsModule(node.moduleSpecifier)
        && !AGENTS_IMPORTS.has(node.moduleSpecifier.text)) {
      report(node, `imports ${node.moduleSpecifier.text}, not Lifecycle`);
    }
    if ((ts.isCallExpression(node) && node.arguments.some(agentsModule))
        || (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)
            && agentsModule(node.argument.literal))) {
      report(node, "loads the agents package dynamically");
    }
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword
        && !(node.arguments[0] !== undefined && ts.isStringLiteralLike(node.arguments[0]))) {
      report(node, "loads a module by a name computed at run time");
    }
    if (ts.isPropertyAccessExpression(node) && (node.name.text === "installHandlers"
        || (node.name.text === "install" && node.expression.getText(file) === "Lifecycle"))) {
      report(node, `calls ${node.name.text}, which installs Lifecycle's runtime handlers`);
    }
    if ((ts.isMethodDeclaration(node) || ts.isPropertyDeclaration(node)
         || ts.isPropertyAssignment(node) || ts.isGetAccessorDeclaration(node)
         || ts.isSetAccessorDeclaration(node))
        && memberName(node.name, file) === "alarm") {
      const body = ts.isMethodDeclaration(node) ? node.body : undefined;
      if (body === undefined || body.statements.length !== 1
          || body.statements[0]!.getText(file) !== HAND_OFF) {
        report(node, `alarm does more than \`${HAND_OFF}\``);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("alarm guard", () => {
  it("finds no alarm reach past Lifecycle in any Worker's source", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/owner-do.ts");
    expect([...sources].flatMap(([path, source]) => alarmReach(path, source))).toEqual([]);
  });

  it("pins the Agents SDK by version and lockfile integrity", () => {
    const lock = JSON.parse(readFileSync(join(PLATFORM, "package-lock.json"), "utf8")) as
      { packages: Record<string, { version?: string; integrity?: string;
                                   devDependencies?: Record<string, string> }> };
    const resolved = lock.packages["node_modules/agents"]!;
    expect([resolved.version, resolved.integrity]).toEqual([AGENTS.version, AGENTS.integrity]);
    const owner = JSON.parse(readFileSync(join(PLATFORM, "owner", "package.json"), "utf8")) as
      { devDependencies: Record<string, string> };
    expect(owner.devDependencies["agents"]).toBe(AGENTS.version);
    expect(lock.packages["owner"]!.devDependencies!["agents"]).toBe(AGENTS.version);
  });

  it("lets only a thread store, which holds no Lifecycle, wipe itself", () => {
    const wipe = "export class S { async destroy() { await this.ctx.storage.deleteAll(); } }";
    expect(alarmReach(STORE, wipe)).toEqual([]);
    expect(alarmReach("owner/src/owner-do.ts", wipe)).not.toEqual([]);
    expect(alarmReach(STORE, "export const k = 'deleteAll';")).not.toEqual([]);
    expect(alarmReach(STORE, "export class S { m() { return this.ctx.storage.setAlarm(1); } }"))
      .not.toEqual([]);
    expect(workerSources().get(STORE)).toContain("await this.ctx.storage.deleteAll();");
  });

  it("lets the owner's erasure alone disable Lifecycle's alarm, and its gate alone wipe " +
     "the owner", () => {
    const disable = "export class E { async m(l: L) { await l.disableAlarms(); } }";
    const wipe = "export class G { wipe() { return this.#raw.deleteAll(); } }";
    expect(alarmReach(ERASE, disable)).toEqual([]);
    expect(alarmReach(GATE, wipe)).toEqual([]);
    expect(alarmReach(ERASE, wipe)).not.toEqual([]);
    expect(alarmReach(GATE, disable)).not.toEqual([]);
    expect(alarmReach("owner/src/owner-do.ts", disable)).not.toEqual([]);
    expect(alarmReach(ERASE, "export const k = 'disableAlarms';")).not.toEqual([]);
    expect(alarmReach(ERASE, "export class E { m(s: S) { return s.setAlarm(1); } }"))
      .not.toEqual([]);
    expect(workerSources().get(ERASE)).toContain("await lifecycle.disableAlarms();");
    expect(workerSources().get(GATE)).toContain("return this.#raw.deleteAll();");
  });

  it("lets only the object's erasure wiring call the gate's wipe", () => {
    const callers = [...workerSources()].flatMap(([path, source]) =>
      [...source.matchAll(/\.wipe\(/g)].map(() => path));
    expect(callers).toEqual(["owner/src/owner-do.ts"]);
    expect(workerSources().get("owner/src/owner-do.ts"))
      .toContain("wipe: () => this.#gate.wipe(),");
  });

  it("finds the object's one hand-off", () => {
    const source = workerSources().get("owner/src/owner-do.ts")!;
    expect(source).toContain(`override async alarm(): Promise<void> {\n    ${HAND_OFF}\n  }`);
  });

  it.each([
    "export async function f(s: DurableObjectStorage) { await s.setAlarm(Date.now()); }",
    "export async function f(s: DurableObjectStorage) { await s.deleteAlarm(); }",
    "export async function f(s: DurableObjectStorage) { return s.getAlarm(); }",
    "export class O { async m() { await this.ctx.storage.setAlarm(1); } }",
    "export function f(s: DurableObjectStorage) { const { setAlarm } = s; return setAlarm; }",
    "export function f(s: DurableObjectStorage) { return s['deleteAlarm'](); }",
    "export function f(s: DurableObjectStorage) { return s[`getAlarm`](); }",
    "export function f(s: DurableObjectStorage) { return Reflect.get(s, 'setAlarm'); }",
    "import { Agent } from 'agents';",
    "import { skills } from 'agents/harness/pi/skills';",
    "import { PiHarness } from 'agents/harness/pi';",
    "import { Schedule } from 'agents/schedule';",
    "import { createAI } from 'agents/models/pi-ai';",
    "export * from 'agents';",
    "export async function f() { return import('agents'); }",
    "type A = typeof import('agents');",
    "export class O { lifecycle = Lifecycle.install(this); }",
    "export class O { m() { this.lifecycle.installHandlers(); } }",
    "export class O { async alarm() { await runWakes(); } }",
    "export class O { async alarm() { await this.#lifecycle.alarm(); await this.more(); } }",
    "export class O { alarm = async () => { await this.#lifecycle.alarm(); }; }",
    "export class O { async alarm() {} }",
    "export class O { async alarm() { await this.#lifecycle.start(); " +
      "await this.#lifecycle.alarm(); } }",
    "export const handlers = { alarm: () => undefined };",
    // Slice 5's review N4: each bypass the first guard let through.
    "export function f(s: DurableObjectStorage) { return s['set' + 'Alarm'](1); }",
    "export function f(s: DurableObjectStorage) { const k = `set${'Alarm'}`; return s[k](1); }",
    "export function f(s: any) { const n = ['set', 'Alarm'].join(''); return s[n](1); }",
    "export class C extends LifecycleCapability { m() { " +
      "return (this.lifecycle.storage as any)['delete' + 'Alarm'](); } }",
    "export class O { m() { return this.#lifecycle.disableAlarms(); } }",
    "export async function f(s: DurableObjectStorage) { await s.deleteAll(); }",
    "export async function f() { const m = 'agents'; return import(m); }",
    "export class O { ['al' + 'arm']() { return 1; } }",
    "export class O { ['alarm']() { return 1; } }",
    "export function f(ctx: DurableObjectState, k: string) { return (ctx.storage as any)[k]; }",
    "export class O { m(k: string) { return (this.#lifecycle as any)[k](); } }",
    "export function f(lifecycle: any, k: string) { return lifecycle[k](); }",
    "export const o = { [Symbol.iterator]: 1 };",
  ])("sees %s", (source) => {
    expect(alarmReach("probe.ts", source)).not.toEqual([]);
  });

  it.each([
    "import { Lifecycle } from 'agents/lifecycle';",
    "import { LifecycleCapability, type LifecycleJob } from 'agents/lifecycle';",
    "import type { LifecycleJobContext } from 'agents/lifecycle';",
    "export class O { readonly #lifecycle = new Lifecycle(this); " +
      "override async alarm(): Promise<void> { await this.#lifecycle.alarm(); } }",
    "export function f(lane: { nextDue(n: number): number }) { return lane.nextDue(0); }",
    "import { x } from 'agentsmith';",
    "export function f(npm: { install(): void }) { npm.install(); }",
    "export function f(s: DurableObjectStorage) { return s.sql.exec('SELECT 1'); }",
    "export function f(rows: string[], i: number) { return rows[i]; }",
    "export function f(o: Record<string, number>, k: string) { return o[k]; }",
    "export const o = { ['literal']: 1 };",
    "export async function f() { return import('./wakes'); }",
  ])("allows %s", (source) => {
    expect(alarmReach("probe.ts", source)).toEqual([]);
  });
});

