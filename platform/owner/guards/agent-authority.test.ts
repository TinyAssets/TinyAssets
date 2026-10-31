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
 * - conversation making: each thread is one pi Session whose root is the thread, its
 *   authority written in the root's creating commit, so the makers sources name are pi's
 *   `root`, in `OwnerAgent.#open` (the thread host's opener), and a subagent's
 *   `createConversation`, exactly at the create-task guard's creation point
 *   (`delegate.ts`'s `createChild`, A5), and no source names another
 *   (`createRootConversation`, `fork`, `forkConversation`); a task's conversation takes its
 *   owner's authority and the five model tools in pi's `conversationCreated`; and the one
 *   write of a conversation's agent besides its model tools is the subagent's
 *   `instructions` (its named agent's `AGENTS.md`), in `createChild`, through one draft of
 *   `AgentDoc` whose every use is its `instructions`;
 * - the model's tools (A3): pi offers the model what a conversation's `modelTools` names,
 *   so it is written in one place, `OwnerAgent.#inherit` (pi's `conversationCreated`, in
 *   every creating commit), as `(await tx.doc(AgentDoc, ...)).modelTools = ...`, that
 *   statement the one write of `AgentDoc` and of `modelTools`, which only
 *   `OwnerAgent.modelAuthority` (the backstop, `TOOL_SURFACE`) otherwise reads; and no
 *   source names `addTools`, or builds a `control` key, the result field whose `addTools`
 *   appends to it.
 * A guarded name spelled as a literal key (`x["configure"]`) is refused anywhere but one
 * read: a package manifest's `pi["extensions"]`, which `resources.ts` reads only to refuse
 * the package's code (`entriesOf(pi["extensions"], ...)`), selecting nothing. The
 * guard is a tripwire for our own code's mistakes, as the alarm guard is; the backstop
 * and the environment are the enforcement.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { CHILD, isCreation } from "./creations";
import { parse, reporter, workerSources } from "./sources";

const SELECTS = "extensions";
const CONFIGURE = "configure";
const AGENT_DOC = "AgentDoc";
const MAKERS: ReadonlySet<string> = new Set(["createConversation", "createRootConversation",
                                             "fork", "forkConversation", "root"]);
/** The one maker a source may name, and only in `OwnerAgent.#open`. */
const ROOT = "root";
const MODEL_TOOLS = "modelTools";
const ADD_TOOLS = "addTools";
const CONTROL = "control";
const GUARDED: ReadonlySet<string> = new Set([SELECTS, CONFIGURE, AGENT_DOC, MODEL_TOOLS,
                                              ADD_TOOLS, ...MAKERS]);
/** `OwnerAgent.#open` and the backstop's file. */
const THREAD = "owner/src/agent.ts";

/** Whether `node` sits in `OwnerAgent`'s method `name`. */
function inMethod(node: ts.Node, name: string): boolean {
  for (let up = node.parent; up !== undefined; up = up.parent) {
    if (ts.isMethodDeclaration(up) && up.name.getText() === name
        && ts.isClassDeclaration(up.parent) && up.parent.name?.text === "OwnerAgent") {
      return true;
    }
  }
  return false;
}

/** Whether `node` is the backstop's read of a snapshot's `modelTools`: never a write. */
function backstopReads(node: ts.Identifier, path: string): boolean {
  const access = node.parent;
  if (path !== THREAD || !inMethod(node, "modelAuthority")
      || !ts.isPropertyAccessExpression(access) || access.name !== node) {
    return false;
  }
  const used = access.parent;
  return !(ts.isBinaryExpression(used) && used.left === access)
    && !(ts.isPropertyAccessExpression(used) && used.expression === access)
    && !(ts.isElementAccessExpression(used) && used.expression === access)
    && !ts.isDeleteExpression(used) && !ts.isPrefixUnaryExpression(used)
    && !ts.isPostfixUnaryExpression(used);
}

/**
 * The one write of the model's tools: `(await x.doc(AgentDoc, ...)).modelTools = ...`, in
 * `OwnerAgent.#inherit`, as the statement it is. `node` is its `AgentDoc` or `modelTools`.
 */
function fixesModelTools(node: ts.Identifier, path: string): boolean {
  if (path !== THREAD || !inMethod(node, "#inherit")) {
    return false;
  }
  const access = node.text === MODEL_TOOLS ? node.parent : (() => {
    const call = node.parent;
    if (!ts.isCallExpression(call) || call.arguments[0] !== node
        || !ts.isPropertyAccessExpression(call.expression)
        || call.expression.name.text !== "doc" || !ts.isAwaitExpression(call.parent)
        || !ts.isParenthesizedExpression(call.parent.parent)) {
      return undefined;
    }
    return call.parent.parent.parent;
  })();
  if (access === undefined || !ts.isPropertyAccessExpression(access)
      || access.name.text !== MODEL_TOOLS || !ts.isParenthesizedExpression(access.expression)) {
    return false;
  }
  const doc = access.expression.expression;
  const assigned = access.parent;
  return ts.isAwaitExpression(doc) && ts.isCallExpression(doc.expression)
    && doc.expression.arguments[0] !== undefined
    && ts.isIdentifier(doc.expression.arguments[0])
    && doc.expression.arguments[0].text === AGENT_DOC
    && ts.isBinaryExpression(assigned) && assigned.left === access
    && assigned.operatorToken.kind === ts.SyntaxKind.EqualsToken
    && ts.isExpressionStatement(assigned.parent);
}

/**
 * Whether `node` names a member, as a maker is reached (`pi.root`, `{ root } = pi`): a
 * plain name `root` (a path's root, a parameter) is no maker.
 */
function namesMember(node: ts.Identifier): boolean {
  const parent = node.parent;
  return (ts.isPropertyAccessExpression(parent) && parent.name === node)
    || (ts.isBindingElement(parent) && (parent.propertyName === node
                                        || (parent.propertyName === undefined
                                            && parent.name === node)))
    || ts.isImportSpecifier(parent) || ts.isExportSpecifier(parent);
}

/** Whether `node` is pi's `root`, called in `OwnerAgent`'s `#open`. */
function opensRoot(node: ts.Identifier): boolean {
  const access = node.parent;
  return node.text === ROOT && ts.isPropertyAccessExpression(access) && access.name === node
    && ts.isCallExpression(access.parent) && access.parent.expression === access
    && inMethod(node, "#open");
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

/** The function declaration `node` is in, by its name, if any. */
function declaredIn(node: ts.Node): ts.FunctionDeclaration | undefined {
  for (let up = node.parent; up !== undefined; up = up.parent) {
    if (ts.isFunctionDeclaration(up)) {
      return up;
    }
  }
  return undefined;
}

/**
 * Whether `node` (an `AgentDoc`) is the subagent's one draft: in `createChild`, its import
 * in that file, or `const <name> = await tx.doc(AgentDoc, ...)` whose every later use is
 * `<name>.instructions`, so it writes nothing of the agent but its instructions.
 */
function childInstructions(node: ts.Identifier, path: string, file: ts.SourceFile): boolean {
  if (path !== CHILD.file) {
    return false;
  }
  if (ts.isImportSpecifier(node.parent)) {
    return true;
  }
  const fn = declaredIn(node);
  const call = node.parent;
  if (fn?.name?.text !== CHILD.fn || !ts.isCallExpression(call) || call.arguments[0] !== node
      || !ts.isPropertyAccessExpression(call.expression) || call.expression.name.text !== "doc"
      || !ts.isAwaitExpression(call.parent) || !ts.isVariableDeclaration(call.parent.parent)
      || !ts.isIdentifier(call.parent.parent.name)) {
    return false;
  }
  const draft = call.parent.parent.name;
  let only = true;
  const uses = (inner: ts.Node): void => {
    if (ts.isIdentifier(inner) && inner !== draft && inner.text === draft.text) {
      only &&= ts.isPropertyAccessExpression(inner.parent) && inner.parent.expression === inner
        && inner.parent.name.text === "instructions";
    }
    ts.forEachChild(inner, uses);
  };
  uses(fn.body ?? file);
  return only;
}

/** The resources' one read of a manifest's code list, to refuse it. */
const RESOURCES = "owner/src/resources.ts";

/** Whether `node` is `entriesOf(pi["extensions"], ...)` in `resources.ts`: a read, never a write. */
function readsManifestCode(node: ts.ElementAccessExpression, path: string): boolean {
  const call = node.parent;
  return path === RESOURCES && ts.isIdentifier(node.expression) && node.expression.text === "pi"
    && keyOf(node.argumentExpression) === SELECTS && ts.isCallExpression(call)
    && call.arguments[0] === node && ts.isIdentifier(call.expression)
    && call.expression.text === "entriesOf";
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
        && ts.isObjectLiteralExpression(node.parent)) {
      const key = keyOf(node.name);
      if (key === SELECTS) {
        report(node, "selects extensions: pi's hooks come only from what is selected");
      }
      if (key === CONTROL) {
        report(node, "builds a tool result's control, which may add a model tool");
      }
    }
    if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
        && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment
        && accessed(node.left) === SELECTS) {
      report(node, "writes a stored extension selection");
    }
    if (ts.isElementAccessExpression(node)) {
      const key = keyOf(node.argumentExpression);
      if (key !== undefined && GUARDED.has(key) && !readsManifestCode(node, path)) {
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
      if (MAKERS.has(node.text) && !declaredKey(node)
          && !(node.text === ROOT && !namesMember(node))
          && !(path === THREAD && opensRoot(node)) && !isCreation(path, node, file)) {
        report(node, `${node.text} outside OwnerAgent.#open's root and the subagent's ` +
                     "creation point");
      }
      if (node.text === AGENT_DOC && !(path === THREAD
          && (ts.isImportSpecifier(node.parent) || snapshotted(node)
              || fixesModelTools(node, path))) && !childInstructions(node, path, file)) {
        report(node, "names pi's AgentDoc past the backstop's reads of it");
      }
      if (node.text === MODEL_TOOLS && !fixesModelTools(node, path)
          && !backstopReads(node, path)) {
        report(node, "names the model's tools past their one write");
      }
      if (node.text === ADD_TOOLS) {
        report(node, "adds a model tool");
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("agent-authority guard", () => {
  const owner = [...workerSources()].filter(([path]) => path.startsWith("owner/src/"));

  it("selects no extensions and makes conversations only as threads' roots", () => {
    expect(owner.length).toBeGreaterThan(10);
    expect(owner.flatMap(([path, source]) => selections(path, source))).toEqual([]);
    // The one maker and the backstop are where the guard looks for them.
    const agent = owner.find(([path]) => path === THREAD)![1];
    expect(agent).toContain("await pi.root(BG, {");
    expect(agent).not.toContain("createConversation(");
    const child = owner.find(([path]) => path === CHILD.file)![1];
    expect(child).toContain(CHILD.call);
    expect(child).toContain("const agent = await tx.doc(AgentDoc, made.id);");
    expect(agent).toContain("pi.snapshot(AgentDoc, ");
    expect(agent).toContain("(await tx.doc(AgentDoc, record.id)).modelTools = [...MODEL_TOOLS];");
  });

  it.each([
    ["configure alone", "owner/src/x.ts", "c.configure({ model }, BG);"],
    ["a selection in a configure", "owner/src/x.ts",
     "c.configure({ extensions: { remove: [g] } }, BG);"],
    ["a selection in a conversation's agent", "owner/src/x.ts",
     "pi.createConversation({ ownership, agent: { extensions } }, BG);"],
    ["a selection in settings", "owner/src/x.ts", "const s = () => ({ ...p(), extensions: [] });"],
    ["a maker", "owner/src/x.ts", "await pi.createConversation({ ownership }, BG);"],
    ["a maker in OwnerAgent but #open", THREAD,
     "class OwnerAgent { other() { return tx.forkConversation(a, b, o); } }"],
    ["a fork beside #open's root", THREAD, "class OwnerAgent { async #open() { " +
      "await pi.root(BG, o); return c.fork(at, o, BG); } }"],
    ["another maker in #open", THREAD,
     "class OwnerAgent { async #open() { await pi.createConversation(o, BG); } }"],
    ["the root outside #open", THREAD,
     "class OwnerAgent { async thread() { await pi.root(BG, o); } }"],
    ["the root in another file", "owner/src/thread-host.ts",
     "class OwnerAgent { async #open() { await pi.root(BG, o); } }"],
    ["the root by reference", THREAD,
     "class OwnerAgent { async #open() { const r = pi.root; await r(BG); } }"],
    ["the root destructured", "owner/src/x.ts", "const { root } = pi; await root(BG);"],
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
    ["a manifest's code list read anywhere else", "owner/src/x.ts",
     "entriesOf(pi[\"extensions\"], \"pi.extensions\");"],
    ["a manifest's code list read past refusing it", RESOURCES,
     "const e = pi[\"extensions\"];"],
    ["a manifest's code list written", RESOURCES, "pi[\"extensions\"] = [];"],
    ["a selection written to a draft", "owner/src/x.ts", "draft.extensions = { remove: [g] };"],
    ["AgentDoc written in the backstop's file", THREAD,
     "import { AgentDoc } from \"p\"; await tx.doc(AgentDoc, id);"],
    ["a raw root", "owner/src/x.ts", "await tx.createRootConversation();"],
    ["a maker passed by reference", "owner/src/x.ts",
     "Reflect.apply(pi.createConversation, pi, [o, BG]);"],
    ["a maker by a literal key", "owner/src/x.ts", "await pi[\"createConversation\"](o, BG);"],
    // The model's tools (A3): written anywhere else, any other way, or widened.
    ["the model's tools in a conversation's agent", THREAD,
     "class OwnerAgent { async thread() { await pi.createConversation({ agent: " +
       "{ modelTools: [a] } }, BG); } }"],
    ["the model's tools written outside #inherit", THREAD,
     "class OwnerAgent { async other(tx) { (await tx.doc(AgentDoc, id)).modelTools = []; } }"],
    ["the model's tools written in another file", "owner/src/x.ts",
     "class OwnerAgent { async #inherit(tx) { (await tx.doc(AgentDoc, id)).modelTools = []; } }"],
    ["the model's tools pushed to", THREAD,
     "class OwnerAgent { async #inherit(tx) { " +
       "(await tx.doc(AgentDoc, id)).modelTools.push(x); } }"],
    ["the model's tools read off a kept draft", THREAD,
     "class OwnerAgent { async #inherit(tx) { const d = await tx.doc(AgentDoc, id); " +
       "d.modelTools = []; } }"],
    ["the model's tools by a literal key", THREAD,
     "class OwnerAgent { async #inherit(tx) { " +
       "(await tx.doc(AgentDoc, id))[\"modelTools\"] = []; } }"],
    ["a tool result's control", "owner/src/ta.ts",
     "const r = () => ({ output: [], control: { addTools: [\"x\"] } });"],
    ["a quoted control key", "owner/src/ta.ts", "const r = () => ({ \"control\": c });"],
    ["pi's addTools", "owner/src/x.ts", "await addTools(tx, id, [\"x\"]);"],
    ["the backstop writing the model's tools", THREAD,
     "class OwnerAgent { async modelAuthority() { agent.modelTools = []; } }"],
    ["the backstop pushing to the model's tools", THREAD,
     "class OwnerAgent { async modelAuthority() { agent.modelTools.push(x); } }"],
    // The subagent's maker and its one draft (A5): anywhere else, or past its instructions.
    ["the subagent's maker outside its function", CHILD.file,
     `export async function other(tx, taskId) { return ${CHILD.call}; }`],
    ["the subagent's maker, ownerless", CHILD.file,
     "export async function createChild(tx, taskId) { return " +
       "tx.createConversation({ ownership: { kind: \"ownerless\" } }); }"],
    ["the subagent's draft writing its model", CHILD.file,
     "export async function createChild(tx, made) { const agent = await tx.doc(AgentDoc, " +
       "made.id); agent.model = m; }"],
    ["the subagent's draft writing its extensions", CHILD.file,
     "export async function createChild(tx, made) { const agent = await tx.doc(AgentDoc, " +
       "made.id); agent.instructions = i; const a = agent; a.tools = []; }"],
    ["the subagent's draft outside its function", CHILD.file,
     "export async function other(tx, made) { const agent = await tx.doc(AgentDoc, " +
       "made.id); agent.instructions = i; }"],
    ["the subagent's draft in another file", THREAD,
     "export async function createChild(tx, made) { const agent = await tx.doc(AgentDoc, " +
       "made.id); agent.instructions = i; }"],
    ["the model's tools read elsewhere", THREAD,
     "class OwnerAgent { async other() { return keepsFive(agent?.modelTools); } }"],
  ])("sees %s", (_what, path, source) => {
    expect(selections(path, source).length).toBeGreaterThan(0);
  });

  it.each([
    [THREAD, "class OwnerAgent { async #open() { await pi.root(BG, o); } }"],
    ["owner/src/x.ts", "export function f(root: string) { return ROOTS.some((root) => root); }"],
    ["owner/src/x.ts", "const packages = this.source.packages; read(source.extensions);"],
    ["owner/src/x.ts", "const doc = defineDoc({ name, fork: \"current\" });"],
    [THREAD, "import { AgentDoc } from \"p\"; const a = await pi.snapshot(AgentDoc, id, BG);"],
    [THREAD, "class OwnerAgent { async #inherit(tx, record) { " +
      "(await tx.doc(AgentDoc, record.id)).modelTools = [...MODEL_TOOLS]; } }"],
    [THREAD, "class OwnerAgent { async modelAuthority() { " +
      "if (!keepsFive(agent?.modelTools)) { return () => TOOL_SURFACE; } } }"],
    [RESOURCES, "const e = entriesOf(pi[\"extensions\"], \"pi.extensions\");"],
    [CHILD.file, "import { AgentDoc } from \"p\";\nexport async function createChild(tx, " +
      `taskId) { const made = await ${CHILD.call}; const agent = await tx.doc(AgentDoc, ` +
      "made.id); delete agent.instructions; agent.instructions = i; }"],
  ])("allows %s: %s", (path, source) => {
    expect(selections(path, source)).toEqual([]);
  });
});
