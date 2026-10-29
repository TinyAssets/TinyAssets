/**
 * The agent's authority holds where pi selects nothing (`authority.ts`): the environment
 * and the model provider enforce it on every operation, and `ta.authority` is the early
 * gate. Two of pi's own mechanisms decide which code runs for a conversation, so the
 * object's sources keep away from both:
 * - extension selection: pi takes hooks only from a conversation's selected extensions, so
 *   no source names `extensions` in an object it builds (a conversation's `agent`, a
 *   `configure`, the settings), and none calls `configure`;
 * - conversation making: a top-level conversation's authority is written where it is made,
 *   so only `OwnerAgent.thread` makes one (`createConversation`, `fork`,
 *   `forkConversation`), and a task's conversation takes its owner's in pi's
 *   `conversationCreated`.
 * The guard is a tripwire for our own code's mistakes, as the alarm guard is.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "./sources";

const MAKERS: ReadonlySet<string> = new Set(["createConversation", "fork", "forkConversation"]);
const THREAD = "owner/src/agent.ts";

/** Whether `node` sits in `OwnerAgent`'s `thread` method. */
function inThread(node: ts.Node): boolean {
  for (let up = node.parent; up !== undefined; up = up.parent) {
    if (ts.isMethodDeclaration(up) && up.name.getText() === "thread"
        && ts.isClassDeclaration(up.parent) && up.parent.name?.text === "OwnerAgent") {
      return true;
    }
  }
  return false;
}

/** Each selection or conversation made past the rules, as `file:line: what`. */
export function selections(path: string, source: string): string[] {
  const file = parse(path, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if ((ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node)
         || ts.isMethodDeclaration(node))
        && ts.isObjectLiteralExpression(node.parent) && node.name.getText(file) === "extensions") {
      report(node, "selects extensions: pi's hooks come only from what is selected");
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const name = node.expression.name.text;
      if (name === "configure") {
        report(node, "configures a conversation's agent");
      }
      if (MAKERS.has(name) && !(path === THREAD && inThread(node))) {
        report(node, `${name}() outside OwnerAgent.thread`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("agent-authority guard", () => {
  const owner = [...workerSources()].filter(([path]) => path.startsWith("owner/src/"));

  it("selects no extensions and makes conversations only as threads", () => {
    expect(owner.length).toBeGreaterThan(10);
    expect(owner.flatMap(([path, source]) => selections(path, source))).toEqual([]);
    // The one maker is where the guard looks for it.
    expect(owner.find(([path]) => path === THREAD)![1]).toContain("pi.createConversation(");
  });

  it.each([
    ["owner/src/x.ts", "c.configure({ extensions: { remove: [g] } }, BG);"],
    ["owner/src/x.ts", "pi.createConversation({ ownership, agent: { extensions } }, BG);"],
    ["owner/src/x.ts", "const s = () => ({ ...p(), extensions: [] });"],
    ["owner/src/x.ts", "await pi.createConversation({ ownership }, BG);"],
    [THREAD, "class OwnerAgent { other() { return tx.forkConversation(a, b, o); } }"],
    [THREAD, "class OwnerAgent { thread() { return c.fork(at, o, BG).then(() => " +
      "pi.createConversation(o, BG)); } other() { return c.fork(at, o, BG); } }"],
  ])("sees %s: %s", (path, source) => {
    expect(selections(path, source).length).toBeGreaterThan(0);
  });

  it.each([
    [THREAD, "class OwnerAgent { async thread() { await pi.createConversation(o, BG); } }"],
    ["owner/src/x.ts", "const packages = this.source.packages; read(source.extensions);"],
  ])("allows %s: %s", (path, source) => {
    expect(selections(path, source)).toEqual([]);
  });
});
