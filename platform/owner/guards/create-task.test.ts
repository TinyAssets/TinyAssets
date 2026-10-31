/**
 * The owner's code creates exactly one kind of pi task, in exactly one place (tasks 5, A5
 * and F1): a scheduled launch's `ta.scheduled` task (`scheduled.ts`'s `createScheduled`),
 * conversation-owned, background and with no telemetry carrier (`trace: "root"`), so it
 * holds no turn: its delivery's input is an origin of its own, which a submission owns
 * while it is open (`origin.ts`, migration 10's turn rows). Every other task is pi's own,
 * so every model call's turn is owned by a submission or a compaction, each bounded. A task
 * made anywhere else by our own code or a package's (a conversation-owned task that is not
 * background, or a background one carrying its creator's origin) would hold a turn no
 * settle point ends. A5's subagents, and any other caller of `createTask`, change this
 * guard deliberately, with a cap on the conversations of a thread.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "./sources";

/** The one creation point: its file, its function, and its call as written. */
export const CREATION = Object.freeze({
  file: "owner/src/scheduled.ts",
  fn: "createScheduled",
  call: "tx.createTask(task, input, {\n    ownership: { kind: \"conversation\" }, " +
    "background: true, trace: \"root\",\n  })",
});

/** The function declaration `node` is in, by its name, if any. */
function functionOf(node: ts.Node): string | undefined {
  for (let up = node.parent; up !== undefined; up = up.parent) {
    if (ts.isFunctionDeclaration(up)) {
      return up.name?.text;
    }
  }
  return undefined;
}

/** Each place a source names `createTask` but the one creation point, as `file:line: what`. */
export function createsTasks(sources: Map<string, string>): string[] {
  const problems: string[] = [];
  for (const [path, text] of sources) {
    if (!path.startsWith("owner/")) {
      continue;
    }
    const file = parse(path, text);
    const report = reporter(file, problems);
    const visit = (node: ts.Node): void => {
      if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) && node.text === "createTask") {
        const call = node.parent?.parent;
        const allowed = path === CREATION.file && functionOf(node) === CREATION.fn
          && ts.isPropertyAccessExpression(node.parent) && node.parent.name === node
          && call !== undefined && ts.isCallExpression(call)
          && call.getText(file) === CREATION.call;
        if (!allowed) {
          report(node, "creates a pi task");
        }
      } else if (ts.isStringLiteralLike(node) && node.text === "createTask") {
        report(node, "names createTask");
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return problems;
}

/** How many times the owner's sources name `createTask` at all. */
function named(sources: Map<string, string>): number {
  return [...sources].filter(([path]) => path.startsWith("owner/"))
    .reduce((sum, [, text]) => sum + [...text.matchAll(/\bcreateTask\b/g)].length, 0);
}

describe("create-task guard", () => {
  it("finds the owner's one creation point, a scheduled launch's task, and no other", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/files.ts");
    expect(createsTasks(sources)).toEqual([]);
    expect(named(sources)).toBe(1);
    expect(sources.get(CREATION.file)).toContain(CREATION.call);
  });

  it.each([
    ["await tx.createTask(ToolTask, input, { ownership });"],
    ["const make = context.createTask;"],
    ['const make = context["createTask"];'],
    ["const { createTask } = context;"],
  ])("sees %s", (source) => {
    expect(createsTasks(new Map([["owner/src/x.ts", source]]))).toHaveLength(1);
  });

  it.each([
    // The creation point's call, but anywhere else, or changed in any way.
    [`export function other(root, task, input) { return root.commit((tx) => ${CREATION.call}); }`],
    [`export function createScheduled(root, task, input) { return root.commit((tx) => ${
      CREATION.call.replace("background: true", "background: false")}); }`],
    [`export function createScheduled(root, task, input) { return root.commit((tx) => ${
      CREATION.call.replace(", trace: \"root\"", "")}); }`],
    [`export function createScheduled(root, task, input) { return root.commit((tx) => ${
      CREATION.call.replace("conversation", "task")}); }`],
    [`export function createScheduled(root, task, input) { const f = tx.createTask; }`],
  ])("sees a changed creation point: %s", (source) => {
    expect(createsTasks(new Map([[CREATION.file, source]]))).toHaveLength(1);
  });

  it("allows the creation point as written", () => {
    const source = "export function createScheduled(root, task, input) {\n" +
      `  return root.commit((tx) => ${CREATION.call}, BACKGROUND_CONTEXT);\n}`;
    expect(createsTasks(new Map([[CREATION.file, source]]))).toEqual([]);
    expect(createsTasks(new Map([["owner/src/other.ts", source]]))).toHaveLength(1);
  });
});
