/**
 * The owner's object reaches the sandbox Worker only bound to its own owner (ADR-032.1,
 * `docs/decisions/untangle-2026-11/owner-isolation.md`).
 *
 * The sandbox Worker picks an owner's sandboxes by the owner its caller names, and a name is
 * a value any code could write. So the owner Worker's source reads its `SANDBOX` binding in one
 * place: the argument of the one `boundSandbox(env.SANDBOX, ctx.id)`, in the object's
 * constructor, which binds every call to the name the runtime gave the object. Every part
 * takes that handle (`ObjectReads.sandbox`), whose methods take no owner. This guard holds
 * that no Worker's source, the shared modules the owner Worker imports included, names the
 * binding anywhere else, however it is written as a name (a property, an index, a string, a
 * destructuring, a class's `extends`), and that the one binding is made from the object's own
 * id. A key computed at run time is not a name and is not seen.
 */
import { describe, expect, it } from "vitest";
import ts from "typescript";

import { parse, reporter, workerSources } from "../../ci/kit/sources";

const BINDING = "SANDBOX";
const BINDER = "boundSandbox";
const HOME = "owner/src/owner-do.ts";

/** `node` is `<object>.<name>`, both plain identifiers. */
function access(node: ts.Node, object: string, name: string): boolean {
  return ts.isPropertyAccessExpression(node) && ts.isIdentifier(node.expression)
    && node.expression.text === object && node.name.text === name;
}

/** `node` is the one binding: `boundSandbox(env.SANDBOX, ctx.id)`. */
function theBinding(node: ts.Node): boolean {
  return ts.isCallExpression(node) && ts.isIdentifier(node.expression)
    && node.expression.text === BINDER && node.arguments.length === 2
    && access(node.arguments[0]!, "env", BINDING) && access(node.arguments[1]!, "ctx", "id");
}

/**
 * Inside a type: a type names the binding but reaches nothing. A class's `extends` and an
 * instantiation expression (`f<T>`) are parsed as types but run.
 */
function inType(node: ts.Node): boolean {
  for (let at: ts.Node | undefined = node; at !== undefined; at = at.parent) {
    if (ts.isExpressionWithTypeArguments(at)) {
      return ts.isHeritageClause(at.parent) && (at.parent.token === ts.SyntaxKind.ImplementsKeyword
        || ts.isInterfaceDeclaration(at.parent.parent));
    }
    if (ts.isTypeNode(at) || ts.isPropertySignature(at)) {
      return true;
    }
  }
  return false;
}

/** Each way `source` names the sandbox binding but the one, and how many ones it holds. */
export function sandboxReach(fileName: string, source: string):
    { problems: string[]; bindings: number } {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  let bindings = 0;
  const visit = (node: ts.Node): void => {
    if (theBinding(node)) {
      if (fileName === HOME) {
        bindings += 1;
      } else {
        report(node, `binds the sandbox outside ${HOME}`);
      }
      ts.forEachChild((node as ts.CallExpression).arguments[1]!, visit);
      return;
    }
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)
        && node.expression.text === BINDER) {
      report(node, `binds the sandbox to something but the object's own id, from env.${BINDING}`);
    }
    if (((ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) && node.text === BINDING)
        || (ts.isStringLiteralLike(node) && node.text === BINDING)) {
      if (!inType(node)) {
        report(node, `names the sandbox binding, which only ${BINDER} reads`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return { problems, bindings };
}

describe("sandbox-bound guard", () => {
  it("finds the binding read once, bound to the object's own id, and nowhere else", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toEqual(expect.arrayContaining([HOME, "contract/src/sandbox.ts"]));
    const found = [...sources].map(([path, source]) => sandboxReach(path, source));
    expect(found.flatMap(({ problems }) => problems)).toEqual([]);
    expect(found.reduce((sum, { bindings }) => sum + bindings, 0)).toBe(1);
  });

  it.each([
    "o.env.SANDBOX.start('user_b', start);",
    "env.SANDBOX.erase(owner());",
    "const { SANDBOX } = env;",
    "const s = env['SANDBOX'];",
    "const s = Reflect.get(env, 'SANDBOX');",
    "boundSandbox(env.SANDBOX, other);",
    "boundSandbox(env.SANDBOX, { name: 'user_b' } as DurableObjectId);",
    "boundSandbox(service, ctx.id);",
    "class Reach extends bound(env.SANDBOX) {}",
    "const s = env.SANDBOX<never>;",
  ])("sees %s", (source) => {
    expect(sandboxReach(HOME, source).problems).not.toEqual([]);
  });

  it("sees the one binding made anywhere but the object", () => {
    expect(sandboxReach("owner/src/runs.ts", "boundSandbox(env.SANDBOX, ctx.id);").problems)
      .not.toEqual([]);
  });

  it.each([
    "const sandbox = boundSandbox(env.SANDBOX, ctx.id);",
    "type E = Omit<OwnerEnv, 'SANDBOX'>;",
    "interface E { readonly SANDBOX: SandboxService }",
    "interface F extends Pick<OwnerEnv, 'SANDBOX'> {}",
    "class G implements Pick<OwnerEnv, 'SANDBOX'> {}",
    "if (answer.code === SANDBOX_WAITING) {}",
  ])("allows %s", (source) => {
    expect(sandboxReach(HOME, source).problems).toEqual([]);
  });
});
