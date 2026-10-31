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

/** The thread store's source: its own object, which serves pi's records and reaches nothing. */
const STORE = "owner/src/thread-store.ts";

/**
 * Each way a thread store's source could reach past its own storage: any `env` (a binding,
 * `this.env`, a destructured one), any import of the owner's own modules but the store
 * types' (`import type`), or a runtime name but the class it extends and `RpcTarget` (its
 * sessions).
 */
/** The runtime names a thread store may import: its class, and its sessions'. */
const STORE_IMPORTS = new Set(["DurableObject", "DurableObject,RpcTarget"]);

export function storeReach(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) && node.text === "env") {
      report(node, "names `env`: a thread store reads no binding");
    }
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const from = node.moduleSpecifier.text;
      if (from.startsWith(".") && node.importClause?.isTypeOnly !== true) {
        report(node, `imports ${from}: a thread store runs none of the owner's code`);
      }
      if (from === RUNTIME) {
        const named = node.importClause?.namedBindings;
        const names = named !== undefined && ts.isNamedImports(named)
          ? named.elements.map((element) => (element.propertyName ?? element.name).text) : [];
        if (!STORE_IMPORTS.has([...names].sort().join())) {
          report(node, `imports ${names.join(", ") || "all"} from ${RUNTIME}`);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("thread-store guard", () => {
  it("reads no env binding and runs none of the owner's code", () => {
    const source = workerSources().get(STORE)!;
    expect(source).toContain("export class ThreadStore extends DurableObject");
    expect(storeReach(STORE, source)).toEqual([]);
  });

  it.each([
    "export class S extends DurableObject { m() { return this.env.FILES; } }",
    "export class S extends DurableObject { constructor(ctx, env) { super(ctx, env); } }",
    "import { env } from 'cloudflare:workers';",
    "import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';",
    "import { DurableObject, RpcTarget, WorkerEntrypoint } from 'cloudflare:workers';",
    "import { RpcTarget } from 'cloudflare:workers';",
    "import { Files } from './files';",
    "export class S { m(o: { env: unknown }) { const { env } = o; return env; } }",
  ])("sees %s", (source) => {
    expect(storeReach(STORE, source)).not.toEqual([]);
  });

  it("allows the store types' own import, and its sessions' class", () => {
    expect(storeReach(STORE, "import type { Read } from './remote-storage';")).toEqual([]);
    expect(storeReach(STORE, "import { RpcTarget, DurableObject } from 'cloudflare:workers';"))
      .toEqual([]);
  });
});

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
