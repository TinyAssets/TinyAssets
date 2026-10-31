/**
 * The owner's code creates pi work in exactly two places, each as written (tasks 5, F1 and
 * A5):
 * - a scheduled launch's `ta.scheduled` task (`scheduled.ts`'s `createScheduled`):
 *   conversation-owned, background and with no telemetry carrier (`trace: "root"`), so it
 *   holds no turn: its delivery's input is an origin of its own, which a submission owns
 *   while it is open (`origin.ts`, migration 10's turn rows);
 * - a subagent's conversation (`delegate.ts`'s `createChild`, `ta`'s `agent:delegate`):
 *   owned by the `ta` call's own task (`{ kind: "task", taskId }`), so aborting the call
 *   aborts it, the parent is idle only once it is, and its one input joins the call's turn,
 *   which that input owns while it is open; a thread holds at most `THREAD_CONVERSATIONS`
 *   live ones, each counted in the owner-bound guard's `model_turns` term.
 * Every other task is pi's own, so every model call's turn is owned by a submission or a
 * compaction, each bounded. A task made anywhere else by our own code (a conversation-owned
 * task that is not background, or a background one carrying its creator's origin) would
 * hold a turn no settle point ends; and no package code runs in the object at all
 * (`resources.ts` refuses a package that declares any), so none can. A later caller of
 * `createTask` or `createConversation` changes this guard deliberately, with its bound.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { CHILD, CREATION, CREATIONS, isCreation, MAKERS } from "./creations";
import { parse, reporter, workerSources } from "./sources";

/** Each place a source names a maker but its creation point, as `file:line: what`. */
export function createsTasks(sources: Map<string, string>): string[] {
  const problems: string[] = [];
  for (const [path, text] of sources) {
    if (!path.startsWith("owner/")) {
      continue;
    }
    const file = parse(path, text);
    const report = reporter(file, problems);
    const visit = (node: ts.Node): void => {
      if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) && MAKERS.has(node.text)) {
        if (!isCreation(path, node, file)) {
          report(node, `creates pi work (${node.text})`);
        }
      } else if (ts.isStringLiteralLike(node) && MAKERS.has(node.text)) {
        report(node, `names ${node.text}`);
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return problems;
}

/** How many times the owner's sources name `maker` at all. */
function named(sources: Map<string, string>, maker: string): number {
  return [...sources].filter(([path]) => path.startsWith("owner/"))
    .reduce((sum, [, text]) => sum + [...text.matchAll(new RegExp(`\\b${maker}\\b`, "g"))]
      .length, 0);
}

describe("create-task guard", () => {
  it("finds the owner's two creation points, a scheduled launch's task and a subagent's " +
     "conversation, and no other", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/files.ts");
    expect(createsTasks(sources)).toEqual([]);
    expect(named(sources, "createTask")).toBe(1);
    expect(named(sources, "createConversation")).toBe(1);
    for (const creation of CREATIONS) {
      expect(sources.get(creation.file)).toContain(creation.call);
    }
  });

  it.each([
    ["await tx.createTask(ToolTask, input, { ownership });"],
    ["const make = context.createTask;"],
    ['const make = context["createTask"];'],
    ["const { createTask } = context;"],
    ["await tx.createConversation({ ownership: { kind: \"ownerless\" } });"],
    ['const make = tx["createConversation"];'],
  ])("sees %s", (source) => {
    expect(createsTasks(new Map([["owner/src/x.ts", source]]))).toHaveLength(1);
  });

  it.each([
    // The creation point's call, but anywhere else, or changed in any way.
    [CREATION.file,
     `export function other(root, task, input) { return root.commit((tx) => ${CREATION.call}); }`],
    [CREATION.file, `export function createScheduled(root, task, input) { return root.commit(` +
      `(tx) => ${CREATION.call.replace("background: true", "background: false")}); }`],
    [CREATION.file, `export function createScheduled(root, task, input) { return root.commit(` +
      `(tx) => ${CREATION.call.replace(", trace: \"root\"", "")}); }`],
    [CREATION.file, `export function createScheduled(root, task, input) { return root.commit(` +
      `(tx) => ${CREATION.call.replace("conversation", "task")}); }`],
    [CREATION.file, "export function createScheduled(root, task, input) { const f = " +
      "tx.createTask; }"],
    // The subagent's: owned by the conversation (no abort with its call), or ownerless, or
    // made anywhere but its function.
    [CHILD.file, `export async function createChild(tx, taskId) { return ${
      CHILD.call.replace("{ kind: \"task\", taskId }", "{ kind: \"conversation\" }")}; }`],
    [CHILD.file, `export async function createChild(tx, taskId) { return ${
      CHILD.call.replace("{ kind: \"task\", taskId }", "{ kind: \"ownerless\" }")}; }`],
    [CHILD.file, `export async function other(tx, taskId) { return ${CHILD.call}; }`],
    [CREATION.file, `export async function createChild(tx, taskId) { return ${CHILD.call}; }`],
    // A task made where the child is made.
    [CHILD.file, "export async function createChild(tx, taskId) { return " +
      "tx.createTask(t, i, { ownership: { kind: \"task\", taskId } }); }"],
  ])("sees a changed creation point in %s: %s", (path, source) => {
    expect(createsTasks(new Map([[path, source]]))).toHaveLength(1);
  });

  it("allows each creation point as written, and only in its file", () => {
    const scheduled = "export function createScheduled(root, task, input) {\n" +
      `  return root.commit((tx) => ${CREATION.call}, BACKGROUND_CONTEXT);\n}`;
    expect(createsTasks(new Map([[CREATION.file, scheduled]]))).toEqual([]);
    expect(createsTasks(new Map([["owner/src/other.ts", scheduled]]))).toHaveLength(1);
    const child = `export async function createChild(tx, taskId) {\n  return ${CHILD.call};\n}`;
    expect(createsTasks(new Map([[CHILD.file, child]]))).toEqual([]);
    expect(createsTasks(new Map([["owner/src/agent.ts", child]]))).toHaveLength(1);
  });
});
