/**
 * The agent's authority holds where pi selects nothing (`authority.ts`): the environment
 * and the model provider enforce it on every operation, `ta.authority` is the early gate,
 * and a client's conversation whose stored selection drops the gate makes no model call
 * (`OwnerAgent.modelAuthority`, the runtime backstop). Two of pi's own mechanisms decide
 * which code runs for a conversation, so the object's sources keep away from both:
 * - extension selection: pi takes hooks only from a conversation's selected extensions, so
 *   no source names `extensions` as a key it builds or a property it writes, names
 *   `configure` at all (a call, a destructuring, an import), or names pi's `AgentDoc`,
 *   but the backstop's reads of it;
 * - conversation making: a top-level conversation's authority is written where it is made,
 *   so only `OwnerAgent.thread` names a maker (`createConversation`,
 *   `createRootConversation`, `fork`, `forkConversation`), and a task's conversation
 *   takes its owner's in pi's `conversationCreated`.
 * A guarded name spelled as a literal key (`x["configure"]`) is refused anywhere. The
 * guard is a tripwire for our own code's mistakes, as the alarm guard is; the backstop
 * and the environment are the enforcement.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "./sources";

const SELECTS = "extensions";
const CONFIGURE = "configure";
const AGENT_DOC = "AgentDoc";
const MAKERS: ReadonlySet<string> = new Set(["createConversation", "createRootConversation",
                                             "fork", "forkConversation"]);
const GUARDED: ReadonlySet<string> = new Set([SELECTS, CONFIGURE, AGENT_DOC, ...MAKERS]);
/** `OwnerAgent.thread` and the backstop's file. */
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

/** The name a key or a key expression spells, when it is a literal one. */
function keyOf(node: ts.Node | undefined): string | undefined {
  if (node === undefined) {
    return undefined;
  }
  if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteral(node)
      || ts.isNoSubstitutionTemplateLiteral(node) || ts.isNumericLiteral(node)) {
    return node.text;
  }
  if (ts.isComputedPropertyName(node) || ts.isParenthesizedExpression(node)) {
    return keyOf(node.expression);
  }
  return undefined;
}

/** The property a property or element access names, when it is a literal one. */
function accessed(node: ts.Expression): string | undefined {
  if (ts.isPropertyAccessExpression(node)) {
    return node.name.text;
  }
  return ts.isElementAccessExpression(node) ? keyOf(node.argumentExpression) : undefined;
}

/** Whether `node` is the key of a member it declares (`{ fork: "current" }`). */
function declaredKey(node: ts.Identifier): boolean {
  const parent = node.parent;
  return (ts.isPropertyAssignment(parent) || ts.isPropertyDeclaration(parent)
          || ts.isMethodDeclaration(parent) || ts.isPropertySignature(parent))
    && parent.name === node;
}

/** Whether `node` is an argument of a `.snapshot(...)` call: a read of the document. */
function snapshotted(node: ts.Node): boolean {
  const call = node.parent;
  return ts.isCallExpression(call) && call.arguments.includes(node as ts.Expression)
    && ts.isPropertyAccessExpression(call.expression) && call.expression.name.text === "snapshot";
}

/** Each selection or conversation made past the rules, as `file:line: what`. */
export function selections(path: string, source: string): string[] {
  const file = parse(path, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if ((ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node)
         || ts.isMethodDeclaration(node) || ts.isGetAccessorDeclaration(node)
         || ts.isSetAccessorDeclaration(node))
        && ts.isObjectLiteralExpression(node.parent) && keyOf(node.name) === SELECTS) {
      report(node, "selects extensions: pi's hooks come only from what is selected");
    }
    if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
        && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment
        && accessed(node.left) === SELECTS) {
      report(node, "writes a stored extension selection");
    }
    if (ts.isElementAccessExpression(node)) {
      const key = keyOf(node.argumentExpression);
      if (key !== undefined && GUARDED.has(key)) {
        report(node, `names ${key} by a literal key`);
      }
    }
    if (ts.isBindingElement(node) && node.propertyName !== undefined) {
      const key = keyOf(node.propertyName);
      if (key !== undefined && GUARDED.has(key)) {
        report(node, `destructures ${key}`);
      }
    }
    if (ts.isIdentifier(node)) {
      if (node.text === CONFIGURE) {
        report(node, "configures a conversation's agent");
      }
      if (MAKERS.has(node.text) && !declaredKey(node) && !(path === THREAD && inThread(node))) {
        report(node, `${node.text} outside OwnerAgent.thread`);
      }
      if (node.text === AGENT_DOC && !(path === THREAD
          && (ts.isImportSpecifier(node.parent) || snapshotted(node)))) {
        report(node, "names pi's AgentDoc past the backstop's reads of it");
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
    // The one maker and the backstop are where the guard looks for them.
    const agent = owner.find(([path]) => path === THREAD)![1];
    expect(agent).toContain("pi.createConversation(");
    expect(agent).toContain("pi.snapshot(AgentDoc, ");
  });

  it.each([
    ["configure alone", "owner/src/x.ts", "c.configure({ model }, BG);"],
    ["a selection in a configure", "owner/src/x.ts",
     "c.configure({ extensions: { remove: [g] } }, BG);"],
    ["a selection in a conversation's agent", "owner/src/x.ts",
     "pi.createConversation({ ownership, agent: { extensions } }, BG);"],
    ["a selection in settings", "owner/src/x.ts", "const s = () => ({ ...p(), extensions: [] });"],
    ["a maker", "owner/src/x.ts", "await pi.createConversation({ ownership }, BG);"],
    ["a maker in OwnerAgent but thread", THREAD,
     "class OwnerAgent { other() { return tx.forkConversation(a, b, o); } }"],
    ["a fork beside thread", THREAD, "class OwnerAgent { thread() { return c.fork(at, o, BG)" +
      ".then(() => pi.createConversation(o, BG)); } other() { return c.fork(at, o, BG); } }"],
    // The review's evasions (R7).
    ["configure by a literal key", "owner/src/x.ts", "c[\"configure\"]({ model }, BG);"],
    ["a quoted selection key", "owner/src/x.ts",
     "pi.root(BG, { agent: { \"extensions\": [] } });"],
    ["a computed selection key", "owner/src/x.ts", "const s = () => ({ [\"extensions\"]: [] });"],
    ["a destructured configure", "owner/src/x.ts",
     "const { configure } = c; await configure({ model }, BG);"],
    ["configure renamed", "owner/src/x.ts", "const { \"configure\": f } = c; await f(m, BG);"],
    ["pi's configure imported", "owner/src/x.ts",
     "import { configure } from \"@earendil-works/pi-durable\"; await configure(tx, id, c);"],
    ["a selection written to pi's AgentDoc", "owner/src/x.ts",
     "(await tx.doc(AgentDoc, id)).extensions = [];"],
    ["a selection written by a literal key", "owner/src/x.ts", "agent[\"extensions\"] = [];"],
    ["a selection written to a draft", "owner/src/x.ts", "draft.extensions = { remove: [g] };"],
    ["AgentDoc written in the backstop's file", THREAD,
     "import { AgentDoc } from \"p\"; await tx.doc(AgentDoc, id);"],
    ["a raw root", "owner/src/x.ts", "await tx.createRootConversation();"],
    ["a maker passed by reference", "owner/src/x.ts",
     "Reflect.apply(pi.createConversation, pi, [o, BG]);"],
    ["a maker by a literal key", "owner/src/x.ts", "await pi[\"createConversation\"](o, BG);"],
  ])("sees %s", (_what, path, source) => {
    expect(selections(path, source).length).toBeGreaterThan(0);
  });

  it.each([
    [THREAD, "class OwnerAgent { async thread() { await pi.createConversation(o, BG); } }"],
    ["owner/src/x.ts", "const packages = this.source.packages; read(source.extensions);"],
    ["owner/src/x.ts", "const doc = defineDoc({ name, fork: \"current\" });"],
    [THREAD, "import { AgentDoc } from \"p\"; const a = await pi.snapshot(AgentDoc, id, BG);"],
  ])("allows %s: %s", (path, source) => {
    expect(selections(path, source)).toEqual([]);
  });
});
