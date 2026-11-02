/**
 * No reach from a Worker's source to another owner's object (ADR-032).
 *
 * The runtime can hand code a way to its own namespace without any binding: the
 * Worker's own exports, as `ctx.exports` or `import { exports } from
 * "cloudflare:workers"`, and the environment as `import { env } from
 * "cloudflare:workers"`. The config turns `ctx.exports` off (`disable_ctx_exports`)
 * and the owner Worker binds nothing; this guard keeps the source from naming either
 * way in. A source never names `exports` at all (as an identifier, a property or a
 * string), and imports from `cloudflare:workers` only the classes it extends.
 */
import { describe, expect, it } from "vitest";
import ts from "typescript";

import { parse, reporter, workerSources } from "./sources";

const RUNTIME = "cloudflare:workers";
/** What a source may import from the runtime module: the classes it extends. */
const RUNTIME_IMPORTS = new Set(["DurableObject", "RpcTarget", "WorkerEntrypoint"]);

function runtimeModule(node: ts.Node): boolean {
  return ts.isStringLiteralLike(node) && node.text === RUNTIME;
}

/** Each way `source` could reach past its own object, as `file:line: what`. */
export function selfReach(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) && node.text === "exports") {
      report(node, "names `exports`, the Worker's own namespaces");
    } else if (ts.isStringLiteralLike(node) && node.text === "exports") {
      report(node, "names `exports`, the Worker's own namespaces");
    }
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier !== undefined && runtimeModule(node.moduleSpecifier)) {
      const clause = ts.isImportDeclaration(node) ? node.importClause : undefined;
      const named = ts.isImportDeclaration(node) ? clause?.namedBindings : node.exportClause;
      if (ts.isImportDeclaration(node) && (clause === undefined || clause.name !== undefined)) {
        report(node, `imports ${RUNTIME} whole`);
      }
      if (named === undefined || ts.isNamespaceImport(named) || ts.isNamespaceExport(named)) {
        report(node, `takes every name from ${RUNTIME}`);
      } else {
        for (const element of named.elements) {
          const imported = (element.propertyName ?? element.name).text;
          if (!RUNTIME_IMPORTS.has(imported)) {
            report(element, `imports ${imported} from ${RUNTIME}`);
          }
        }
      }
    }
    if ((ts.isCallExpression(node) && node.arguments.some(runtimeModule))
        || (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)
            && runtimeModule(node.argument.literal))) {
      report(node, `loads ${RUNTIME} dynamically`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("self-reach guard", () => {
  it("finds no way past the object in any Worker's source", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain("owner/src/owner-do.ts");
    expect([...sources].flatMap(([path, source]) => selfReach(path, source))).toEqual([]);
  });

  it.each([
    "export class O { m() { return this.ctx.exports.OwnerDO; } }",
    "export class O { m() { return this.ctx['exports']; } }",
    "export class O { m() { const { exports } = this.ctx; return exports; } }",
    "export class O { m() { return Reflect.get(this.ctx, 'exports'); } }",
    "import { exports } from 'cloudflare:workers';",
    "import { env } from 'cloudflare:workers';",
    "import { env as bindings } from 'cloudflare:workers';",
    "import * as workers from 'cloudflare:workers';",
    "import workers from 'cloudflare:workers';",
    "export { env } from 'cloudflare:workers';",
    "export * from 'cloudflare:workers';",
    "export async function f() { return (await import('cloudflare:workers')).env; }",
  ])("sees %s", (source) => {
    expect(selfReach("probe.ts", source)).not.toEqual([]);
  });

  it.each([
    "import { DurableObject } from 'cloudflare:workers';",
    "import { DurableObject, RpcTarget } from 'cloudflare:workers';",
    "import { WorkerEntrypoint } from 'cloudflare:workers';",
    "import type { Binding } from './owner-do';",
    "export class O { m() { return this.ctx.storage.sql; } }",
  ])("allows %s", (source) => {
    expect(selfReach("probe.ts", source)).toEqual([]);
  });
});
