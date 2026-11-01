/**
 * The hooks Worker's source reaches no owner and no storage (agent research §6.4).
 *
 * - No source names a binding of the owner's world (`OWNER`, `PISTORE`, `FILES`,
 *   `GATEWAY`, `BLOBS`) or `HOOKS` (it is never bound to itself), nor `storage`: its
 *   objects never use their storage, so an owner's code lives only in the owner's files.
 *   No source names `process`.
 * - Imports, read from the AST and resolved as paths: a relative one must land in
 *   `hooks/src/`; a bare one must be `cloudflare:workers` or pi's code mode
 *   (`@earendil-works/pi-codemode/cloudflare` in `codemode.ts` alone, which bundles
 *   QuickJS; `/portable` for its types); absolute specifiers, dynamic `import()` and
 *   `require` are refused.
 * - The platform's module-state and self-reach guards (`owner/guards/`) scan every
 *   Worker's `src/`; this checks every hooks source is in what they scan.
 *
 * Tripwires for our own code, as the gateway's are, not proof against deliberate evasion.
 */
import { readdirSync } from "node:fs";
import { dirname, extname, join, posix } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "../../owner/guards/sources";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
const BANNED = /^(?:OWNER|PISTORE|FILES|GATEWAY|BLOBS|HOOKS|storage|process|node:process)$/;
/** The bare modules a source may import, and where (null: anywhere). */
const BARE: Readonly<Record<string, string | null>> = Object.freeze({
  "cloudflare:workers": null,
  "@earendil-works/pi-codemode/cloudflare": "hooks/src/codemode.ts",
  "@earendil-works/pi-codemode/portable": null,
});

function importProblem(path: string, specifier: string): string | null {
  if (specifier.startsWith("./") || specifier.startsWith("../")) {
    const resolved = posix.normalize(posix.join(posix.dirname(path), specifier));
    return resolved.startsWith("hooks/src/") ? null
      : `imports ${specifier} (${resolved}), outside the hooks Worker`;
  }
  if (Object.hasOwn(BARE, specifier)) {
    const only = BARE[specifier];
    return only === null || only === path ? null : `imports ${specifier} outside ${only}`;
  }
  return `imports ${specifier}, not a module the hooks Worker may use`;
}

/** Each way `source` (at `path` from `platform/`) breaks the hooks Worker's isolation. */
export function hooksIsolation(path: string, source: string): string[] {
  const file = parse(path, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node))
        && BANNED.test(node.text)) {
      report(node, `names ${node.text}`);
    }
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier !== undefined) {
      const problem = ts.isStringLiteralLike(node.moduleSpecifier)
        ? importProblem(path, node.moduleSpecifier.text) : "imports a computed module";
      if (problem !== null) {
        report(node, problem);
      }
    } else if (ts.isImportEqualsDeclaration(node)) {
      report(node, "imports with require");
    } else if (ts.isCallExpression(node)
               && (node.expression.kind === ts.SyntaxKind.ImportKeyword
                   || (ts.isIdentifier(node.expression) && node.expression.text === "require"))) {
      report(node, "loads a module dynamically");
    } else if (ts.isImportTypeNode(node)) {
      report(node, "imports a type by path");
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

function hooksSources(): Map<string, string> {
  return new Map([...workerSources()].filter(([path]) => path.startsWith("hooks/src/")));
}

describe("hooks isolation guards", () => {
  it("find no owner binding, no storage and no import from outside", () => {
    const sources = hooksSources();
    expect([...sources.keys()]).toContain("hooks/src/runner.ts");
    expect([...sources].flatMap(([path, source]) => hooksIsolation(path, source))).toEqual([]);
  });

  it.each([
    ["an OWNER binding", "export function f(env: { OWNER: unknown }) { return env.OWNER; }"],
    ["the thread stores", "export const s = (env: Record<string, unknown>) => env['PISTORE'];"],
    ["the files Worker", "export const f = (env: { FILES: unknown }) => env.FILES;"],
    ["the gateway", "export const g = (env: { GATEWAY: unknown }) => env.GATEWAY;"],
    ["storage", "export const s = (ctx: { storage: unknown }) => ctx.storage;"],
    ["process", "export const p = () => process;"],
    ["the owner object's module", "import { OwnerDO } from '../../owner/src/owner-do';"],
    ["a path out and back", "import { x } from '../src/../../owner/src/text';"],
    ["another package", "import { x } from '@earendil-works/pi-durable';"],
    ["QuickJS outside codemode.ts", "import { x } from '@earendil-works/pi-codemode/cloudflare';"],
    ["a dynamic import", "export const f = () => import('./runner');"],
    ["require", "export const x = require('node:fs');"],
    ["a type import by path", "export type T = import('../../owner/src/owner-do').OwnerDO;"],
  ])("sees %s", (_what, source) => {
    expect(hooksIsolation("hooks/src/probe.ts", source)).not.toEqual([]);
  });

  it("allows the runtime, code mode's portable types and sibling sources", () => {
    expect(hooksIsolation("hooks/src/probe.ts", [
      "import { WorkerEntrypoint } from 'cloudflare:workers';",
      "import type { CodemodeRemote } from '@earendil-works/pi-codemode/portable';",
      "import { hookRequest } from './protocol';",
    ].join("\n"))).toEqual([]);
    expect(hooksIsolation("hooks/src/codemode.ts",
      "import { CodemodeSandboxDurableObject } from '@earendil-works/pi-codemode/cloudflare';"))
      .toEqual([]);
  });

  it("are scanned by the platform's module-state and self-reach guards", () => {
    const scanned = [...hooksSources().keys()].sort();
    const sources = readdirSync(SRC, { recursive: true, encoding: "utf8" })
      .filter((path) => extname(path) !== "").map((path) => `hooks/src/${path}`).sort();
    expect(scanned).toEqual(sources);
  });
});
