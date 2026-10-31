/**
 * The owner's pi creation points, as the create-task guard holds them and the
 * agent-authority guard allows the subagent's maker by (`create-task.test.ts`).
 */
import ts from "typescript";

/** A creation point: its file, its function, the maker it names, and its call as written. */
export interface Creation {
  readonly file: string;
  readonly fn: string;
  readonly maker: string;
  readonly call: string;
}

/** The scheduled launch's task. */
export const CREATION: Creation = Object.freeze({
  file: "owner/src/scheduled.ts",
  fn: "createScheduled",
  maker: "createTask",
  call: "tx.createTask(task, input, {\n    ownership: { kind: \"conversation\" }, " +
    "background: true, trace: \"root\",\n  })",
});

/** A subagent's conversation, owned by the `ta` call's task. */
export const CHILD: Creation = Object.freeze({
  file: "owner/src/delegate.ts",
  fn: "createChild",
  maker: "createConversation",
  call: "tx.createConversation({ ownership: { kind: \"task\", taskId } })",
});

export const CREATIONS: readonly Creation[] = Object.freeze([CREATION, CHILD]);
export const MAKERS: ReadonlySet<string> = new Set(CREATIONS.map((creation) => creation.maker));

/** The function declaration `node` is in, by its name, if any. */
function functionOf(node: ts.Node): string | undefined {
  for (let up = node.parent; up !== undefined; up = up.parent) {
    if (ts.isFunctionDeclaration(up)) {
      return up.name?.text;
    }
  }
  return undefined;
}

/** Whether `node`, a maker's name, is one creation point's call, exactly as written. */
export function isCreation(path: string, node: ts.Identifier | ts.PrivateIdentifier,
                           file: ts.SourceFile): boolean {
  const call = node.parent?.parent;
  return CREATIONS.some((creation) => path === creation.file && node.text === creation.maker
    && functionOf(node) === creation.fn
    && ts.isPropertyAccessExpression(node.parent) && node.parent.name === node
    && call !== undefined && ts.isCallExpression(call)
    && call.getText(file) === creation.call);
}
