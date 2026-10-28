/**
 * The object's one alarm is Lifecycle's (ADR-032, tasks 3.6).
 *
 * A Durable Object has one alarm, and the Agents SDK's Lifecycle owns it: its job queue
 * derives the alarm from every queued job, PiHarness's wake job among them, so a source
 * that set, read or cleared the alarm itself would overwrite theirs. No Worker source names
 * `setAlarm`, `deleteAlarm` or `getAlarm` at all (as an identifier, a property or a
 * string). The object holds a Lifecycle and never extends the SDK's `Agent`: a source
 * imports from the `agents` package only what `AGENTS_IMPORTS` lists, and never installs
 * Lifecycle's runtime handlers (`Lifecycle.install`, `installHandlers`), which would add a
 * `fetch` and WebSocket handlers to the object's surface. A method named `alarm` is only
 * the hand-off, `await this.#lifecycle.alarm();`.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "./sources";

const ALARM_CALLS: ReadonlySet<string> = new Set(["setAlarm", "deleteAlarm", "getAlarm"]);
/** What a source may import from the Agents SDK: Lifecycle and its capability base. */
const AGENTS_IMPORTS: ReadonlySet<string> = new Set(["agents/lifecycle"]);
const HAND_OFF = "await this.#lifecycle.alarm();";

function agentsModule(node: ts.Node): node is ts.StringLiteralLike {
  return ts.isStringLiteralLike(node)
    && (node.text === "agents" || node.text.startsWith("agents/"));
}

/** Each way `source` reaches the alarm past Lifecycle, as `file:line: what`. */
export function alarmReach(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node))
        && ALARM_CALLS.has(node.text)) {
      report(node, `names ${node.text}, which only Lifecycle calls`);
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
    if (ts.isPropertyAccessExpression(node) && (node.name.text === "installHandlers"
        || (node.name.text === "install" && node.expression.getText(file) === "Lifecycle"))) {
      report(node, `calls ${node.name.text}, which installs Lifecycle's runtime handlers`);
    }
    if ((ts.isMethodDeclaration(node) || ts.isPropertyDeclaration(node)
         || ts.isPropertyAssignment(node) || ts.isGetAccessorDeclaration(node))
        && node.name.getText(file) === "alarm") {
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
    "import { PiHarness } from 'agents/harness/pi';",
    "import { Schedule } from 'agents/schedule';",
    "export * from 'agents';",
    "export async function f() { return import('agents'); }",
    "type A = typeof import('agents');",
    "export class O { lifecycle = Lifecycle.install(this); }",
    "export class O { m() { this.lifecycle.installHandlers(); } }",
    "export class O { async alarm() { await runWakes(); } }",
    "export class O { async alarm() { await this.#lifecycle.alarm(); await this.more(); } }",
    "export class O { alarm = async () => { await this.#lifecycle.alarm(); }; }",
    "export class O { async alarm() {} }",
    "export const handlers = { alarm: () => undefined };",
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
  ])("allows %s", (source) => {
    expect(alarmReach("probe.ts", source)).toEqual([]);
  });
});
