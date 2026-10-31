/**
 * No owner source creates a pi task (tasks 5, A5): pi alone makes its tasks today, so
 * every model call's turn is owned by a submission or a compaction (`origin.ts`,
 * migration 10's turn rows), each bounded. A task made by our own code or a package's (a
 * conversation-owned task that is not background, or a background one carrying its
 * creator's origin) would hold a turn no settle point ends. A5's subagents, and any
 * other caller of `createTask`, change this guard deliberately, with a cap on the
 * conversations of a thread.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "./sources";

/** Each place a source names `createTask`, as `file:line: what`. */
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
        report(node, "creates a pi task");
      } else if (ts.isStringLiteralLike(node) && node.text === "createTask") {
        report(node, "names createTask");
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return problems;
}

describe("create-task guard", () => {
  it("finds no owner source that creates a pi task", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/files.ts");
    expect(createsTasks(sources)).toEqual([]);
  });

  it.each([
    ["await tx.createTask(ToolTask, input, { ownership });"],
    ["const make = context.createTask;"],
    ['const make = context["createTask"];'],
    ["const { createTask } = context;"],
  ])("sees %s", (source) => {
    expect(createsTasks(new Map([["owner/src/x.ts", source]]))).toHaveLength(1);
  });
});
