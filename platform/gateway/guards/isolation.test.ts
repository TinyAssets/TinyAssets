/**
 * The gateway's source keeps its key in one place and reaches no owner (research §1.4).
 *
 * - `GATEWAY_KEYRING` is named only in `src/keyring.ts`: as an identifier, a property or
 *   a string anywhere else, it is refused. The config also keeps it out of `process.env`
 *   (`config.test.ts`), and no gateway source names `process` at all (an identifier, a
 *   string such as `globalThis["process"]`, or the `node:process` module), so the env
 *   handed to a call is the only way to it.
 * - No gateway source names `OWNER` or `getByName`: the gateway is handed the owner and
 *   never looks one up, and its config binds no namespace to look one up in.
 * - Imports, read from the AST and resolved as paths: a relative one must land in
 *   `gateway/src/` or be the owner grammar (`owner/src/text`), however it is spelled
 *   (`./../../owner/...` included); a bare one must be pi-ai, `cloudflare:workers`, or
 *   `node:async_hooks` in `egress.ts` alone; absolute specifiers, dynamic `import()` and
 *   `require` are refused.
 * - The platform's module-state and self-reach guards (`owner/guards/`) scan every
 *   Worker's `src/`; this checks every gateway source is in what they scan.
 *
 * These are tripwires for our own code (a split spelling such as
 * `env["GATEWAY_" + "KEYRING"]` is not seen), not proof against deliberate evasion.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, posix } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, PLATFORM, reporter, workerSources } from "../../owner/guards/sources";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
const KEYRING_HOME = "gateway/src/keyring.ts";
/** The one module outside the gateway its source may import, as a resolved path. */
const OWNER_GRAMMAR = "owner/src/text";
const PI_AI = "@earendil-works/pi-ai";
/** The runtime modules a source may import, and where. */
const RUNTIME: Readonly<Record<string, string | null>> = Object.freeze({
  "cloudflare:workers": null,
  "node:async_hooks": "gateway/src/egress.ts",
});

/** Why `specifier`, imported by the source at `path`, is refused; `null` when allowed. */
function importProblem(path: string, specifier: string): string | null {
  if (specifier.startsWith("/") || /^[a-z][a-z0-9+.-]*:\/\//i.test(specifier)
      || specifier.startsWith("file:")) {
    return `imports ${specifier}, an absolute specifier`;
  }
  if (specifier.startsWith("./") || specifier.startsWith("../")) {
    const resolved = posix.normalize(posix.join(posix.dirname(path), specifier))
      .replace(/\.(?:[cm]?[jt]s|tsx|jsx)$/, "");
    return resolved.startsWith("gateway/src/") || resolved === OWNER_GRAMMAR
      ? null : `imports ${specifier} (${resolved}), outside the gateway`;
  }
  if (specifier === PI_AI || specifier.startsWith(`${PI_AI}/`)) {
    return null;
  }
  if (Object.hasOwn(RUNTIME, specifier)) {
    const only = RUNTIME[specifier];
    return only === null || only === path ? null : `imports ${specifier} outside ${only}`;
  }
  return `imports ${specifier}, not a module the gateway may use`;
}

/** Each named token of `file`: identifiers, private names and string-like literals. */
function tokens(file: ts.SourceFile, visit: (node: ts.Node, text: string) => void) {
  const walk = (node: ts.Node): void => {
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) {
      visit(node, node.text);
    } else if (ts.isStringLiteralLike(node) || ts.isTemplateHead(node)
               || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      visit(node, node.text);
    }
    ts.forEachChild(node, walk);
  };
  walk(file);
}

/** Each way `source` (at `path` from `platform/`) breaks the gateway's isolation. */
export function isolationProblems(path: string, source: string): string[] {
  const file = parse(path, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  tokens(file, (node, text) => {
    if (text.includes("GATEWAY_KEYRING") && path !== KEYRING_HOME) {
      report(node, "names GATEWAY_KEYRING outside keyring.ts");
    }
    if (text === "process" || text === "node:process") {
      report(node, "names process, whose env the gateway never reads");
    }
    if (/\bOWNER\b/.test(text) || text.includes("getByName")) {
      report(node, `names ${text.includes("getByName") ? "getByName" : "OWNER"}`);
    }
  });
  const visit = (node: ts.Node): void => {
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

function gatewaySources(): Map<string, string> {
  return new Map([...workerSources()].filter(([path]) => path.startsWith("gateway/src/")));
}

describe("gateway isolation guards", () => {
  it("finds the keyring named only in keyring.ts, and no OWNER or getByName", () => {
    const sources = gatewaySources();
    expect([...sources.keys()]).toContain(KEYRING_HOME);
    expect(sources.get(KEYRING_HOME)).toContain("GATEWAY_KEYRING");
    expect([...sources].flatMap(([path, source]) => isolationProblems(path, source))).toEqual([]);
  });

  it.each([
    ["env.GATEWAY_KEYRING", "export function f(env: { GATEWAY_KEYRING: string }) { return env.GATEWAY_KEYRING; }"],
    ["a string key", "export function f(env: Record<string, string>) { return env['GATEWAY_KEYRING']; }"],
    ["a template", "export const NAME = `${'x'}GATEWAY_KEYRING`;"],
    ["a destructure", "export function f({ GATEWAY_KEYRING: k }: Record<string, string>) { return k; }"],
    ["process.env", "export function f() { return process.env['X']; }"],
    ["globalThis.process", "export function f() { return globalThis.process; }"],
    ["an OWNER binding", "export function f(env: { OWNER: unknown }) { return env.OWNER; }"],
    ["getByName", "export function f(ns: { getByName(n: string): unknown }) { return ns.getByName('a'); }"],
    ["a string getByName", "export function f(ns: Record<string, unknown>) { return ns['getByName']; }"],
    ["the owner object's module", "import { OwnerDO } from '../../owner/src/owner-do';"],
    ["another Worker's module", "export { x } from '../../files/src/keys';"],
    ["a dot-slash path out", "import { OwnerDO } from './../../owner/src/owner-do';"],
    ["a path out and back", "import { x } from '../src/../../owner/src/owner-do';"],
    ["a dynamic import", "export async function f() { return import('../../owner/src/owner-do'); }"],
    ["a dynamic import of the grammar", "export async function f() { return import('../../owner/src/text'); }"],
    ["require", "export const x = require('node:fs');"],
    ["import-equals", "import fs = require('node:fs');"],
    ["node:process", "import { env } from 'node:process';\nexport const k = () => env;"],
    ["globalThis['process']", "export const k = () => (globalThis as never)['process'];"],
    ["an absolute specifier", "import { x } from '/platform/owner/src/owner-do';"],
    ["a URL specifier", "import { x } from 'https://example.com/x.js';"],
    ["another package", "import { x } from 'openai';"],
    ["node:async_hooks outside egress.ts", "import { AsyncLocalStorage } from 'node:async_hooks';"],
    ["a type import by path", "export type T = import('../../owner/src/owner-do').OwnerDO;"],
  ])("sees %s", (_what, source) => {
    expect(isolationProblems("gateway/src/probe.ts", source)).not.toEqual([]);
  });

  it("allows pi-ai, the runtime module, sibling sources, and node:async_hooks in egress.ts", () => {
    expect(isolationProblems("gateway/src/probe.ts", [
      "import { createModels } from '@earendil-works/pi-ai/models';",
      "import type { Api } from '@earendil-works/pi-ai';",
      "import { WorkerEntrypoint } from 'cloudflare:workers';",
      "import { open } from './keyring';",
      "import { principal } from './../../owner/src/text';",
    ].join("\n"))).toEqual([]);
    expect(isolationProblems("gateway/src/egress.ts",
      "import { AsyncLocalStorage } from 'node:async_hooks';")).toEqual([]);
  });

  it("allows the keyring in keyring.ts, and the owner grammar import", () => {
    expect(isolationProblems(KEYRING_HOME,
      "export function f(env: { GATEWAY_KEYRING?: unknown }) { return env.GATEWAY_KEYRING; }"))
      .toEqual([]);
    expect(isolationProblems("gateway/src/probe.ts",
      "import { principal } from '../../owner/src/text';\nexport const owner = 'owner';"))
      .toEqual([]);
  });

  it("are the platform's own module-state and self-reach guards, scanning every source", () => {
    const scanned = [...gatewaySources().keys()].sort();
    const sources = readdirSync(SRC, { recursive: true, encoding: "utf8" })
      .filter((path) => extname(path) !== "").map((path) => `gateway/src/${path}`).sort();
    expect(scanned).toEqual(sources);
    for (const guard of ["module-state.test.ts", "self-reach.test.ts"]) {
      expect(readFileSync(join(PLATFORM, "owner", "guards", guard), "utf8"))
        .toContain("workerSources()");
    }
  });
});
