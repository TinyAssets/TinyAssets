/**
 * The gateway's source keeps its key in one place and reaches no owner (research §1.4).
 *
 * - `GATEWAY_KEYRING` is named only in `src/keyring.ts`: as an identifier, a property or
 *   a string anywhere else, it is refused. The config also keeps it out of `process.env`
 *   (`config.test.ts`), and no gateway source names `process` at all, so the env handed
 *   to a call is the only way to it.
 * - No gateway source names `OWNER` or `getByName`: the gateway is handed the owner and
 *   never looks one up, and its config binds no namespace to look one up in. Of the owner
 *   Worker's code it imports only the owner grammar (`owner/src/text.ts`).
 * - The platform's module-state and self-reach guards (`owner/guards/`) scan every
 *   Worker's `src/`; this checks every gateway source is in what they scan.
 *
 * These are tripwires for our own code (a split spelling such as
 * `env["GATEWAY_" + "KEYRING"]` is not seen), not proof against deliberate evasion.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, PLATFORM, reporter, workerSources } from "../../owner/guards/sources";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
const KEYRING_HOME = "gateway/src/keyring.ts";
const OWNER_IMPORTS: readonly string[] = Object.freeze(["../../owner/src/text"]);

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
    if (text === "process" && !ts.isStringLiteralLike(node)) {
      report(node, "names process, whose env the gateway never reads");
    }
    if (/\bOWNER\b/.test(text) || text.includes("getByName")) {
      report(node, `names ${text.includes("getByName") ? "getByName" : "OWNER"}`);
    }
  });
  for (const statement of file.statements) {
    if ((ts.isImportDeclaration(statement) || ts.isExportDeclaration(statement))
        && statement.moduleSpecifier !== undefined
        && ts.isStringLiteralLike(statement.moduleSpecifier)) {
      const specifier = statement.moduleSpecifier.text;
      if (specifier.startsWith("../") && !OWNER_IMPORTS.includes(specifier)) {
        report(statement, `imports ${specifier}, outside the gateway`);
      }
    }
  }
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
  ])("sees %s", (_what, source) => {
    expect(isolationProblems("gateway/src/probe.ts", source)).not.toEqual([]);
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
