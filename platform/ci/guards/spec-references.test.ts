/** Repository paths and source-linked constants in the active platform specifications. */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { KINDS, parse, treeFiles } from "../kit/sources";
import { PLATFORM, REPO } from "../paths";

const SPECS = join(REPO, "openspec/changes/clean-sheet-platform/specs");
const workspaces = new Set<string>(JSON.parse(readFileSync(join(PLATFORM, "package.json"),
                                                           "utf8")).workspaces);

/** The file a standalone code span names. Runtime paths and tool scopes such as files/read
 * name no repository file. A workspace path is relative to platform/, as in the specs. */
function repositoryPath(span: string): string | undefined {
  if (span.startsWith("platform/")) return join(REPO, span);
  if (workspaces.has(span.split("/")[0]!) && span.includes("/") &&
      (span.endsWith("/") || extname(span))) return join(PLATFORM, span);
  return undefined;
}

/** A constant reference names the declaration itself, with an optional GitHub line range. */
function definesConstant(source: ts.SourceFile, name: string, fragment?: string): boolean {
  const range = fragment?.match(/^L([1-9]\d*)(?:-L([1-9]\d*))?$/);
  if (fragment !== undefined && !range) return false;
  return source.statements.some((statement) => ts.isVariableStatement(statement) &&
    statement.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword) &&
    (statement.declarationList.flags & ts.NodeFlags.Const) !== 0 &&
    statement.declarationList.declarations.some((d) => {
      if (!ts.isIdentifier(d.name) || d.name.text !== name) return false;
      if (!range) return true;
      const first = Number(range[1]);
      const last = Number(range[2] ?? range[1]);
      const line = source.getLineAndCharacterOfPosition(d.name.getStart(source)).line + 1;
      return first <= line && line <= last && last <= source.getLineStarts().length;
    }));
}

const CONSTANT_LINK = /\[`([A-Z][A-Z0-9_]+)`\]\(([^)\s]+)\)/g;

function problems(spec: string, text: string): string[] {
  const found: string[] = [];
  for (const [, span] of text.matchAll(/`([\w./-]+)`/g)) {
    if (!span) continue;
    const path = repositoryPath(span);
    if (path && (span.split("/").includes("..") || !existsSync(path))) {
      found.push(`${span}: path is missing`);
    }
  }
  for (const [, name, target] of text.matchAll(CONSTANT_LINK)) {
    if (!target || /^https?:\/\//.test(target)) continue;
    const [file, ...fragments] = target.split("#");
    const path = resolve(dirname(spec), file!);
    const local = relative(PLATFORM, path);
    if (extname(path) !== ".ts" || isAbsolute(local) || local.split(sep)[0] === ".." || !existsSync(path)) {
      found.push(`${target}: linked file is missing`);
      continue;
    }
    const source = parse(path, readFileSync(path, "utf8"));
    if (!definesConstant(source, name!, fragments.length ? fragments.join("#") : undefined)) {
      found.push(`${target}: no exported constant ${name} at this reference`);
    }
  }
  return found;
}

/** Unlinked names describe runtime code, configuration and workspace guard rules. */
function platformSource(path: string): boolean {
  if (/\/(?:tests?|fixtures|e2e|memory)\/|\.(?:test|spec)\./.test(path)) return false;
  return /^platform\/[^/]+\/(?:src\/|guards\/rules\.ts$)/.test(path) || path.startsWith("platform/sandbox/runner/") ||
    /^platform\/[^/]+\/[^/]+\.jsonc?$/.test(path) || /^platform\/sources\/records\/[^/]+\.json$/.test(path);
}

/** A cited name may be a constant, a binding property or an exact configuration value
 * such as an engine environment key. Comments and test fixtures do not establish one. */
function findNames(source: ts.SourceFile, missing: Set<string>): void {
  function visit(node: ts.Node): void {
    if (ts.isIdentifier(node) || ts.isStringLiteralLike(node)) missing.delete(node.text);
    ts.forEachChild(node, visit);
  }
  visit(source);
}

function unlinkedNames(text: string): string[] {
  return [...text.replace(CONSTANT_LINK, "").matchAll(/`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`/g)]
    .map((match) => match[1]!);
}

describe("active platform specifications", () => {
  it("name existing repository paths and link each cited constant to its definition", () => {
    const files = readdirSync(SPECS, { recursive: true, encoding: "utf8" })
      .filter((file) => file.endsWith(".md"));
    expect(files.length).toBeGreaterThan(0);
    const specs = files.map((file) => {
      const path = join(SPECS, file);
      return { file, path, text: readFileSync(path, "utf8") };
    });
    const names = new Set(specs.flatMap(({ text }) => unlinkedNames(text)));
    const missing = specs.flatMap(({ file, path, text }) =>
      problems(path, text).map((p) => `${file}: ${p}`));
    for (const [path, contents] of treeFiles()) {
      if (!platformSource(path)) continue;
      const ext = extname(path);
      if (!(ext in KINDS || ext === ".json" || ext === ".jsonc") ||
          ![...names].some((name) => contents.includes(name))) continue;
      const source = ext === ".json" || ext === ".jsonc"
        ? ts.createSourceFile(path, contents, ts.ScriptTarget.Latest, false, ts.ScriptKind.JSON)
        : parse(path, contents);
      findNames(source, names);
    }
    missing.push(...[...names].sort().map((name) => `${name}: missing from platform code and config`));
    expect(missing).toEqual([]);
  });

  it("counts identifiers and exact data values, without accepting old names in prose", () => {
    const names = new Set(["OLD_LIMIT", "ACTIVE_LIMIT", "ENGINE_KEY"]);
    findNames(parse("limits.ts", '/* OLD_LIMIT */ const ACTIVE_LIMIT = "ENGINE_KEY";'), names);
    expect([...names]).toEqual(["OLD_LIMIT"]);
    expect(platformSource("platform/import/guards/fixture.ts")).toBe(false);
    expect(platformSource("platform/ci/fixtures.ts")).toBe(false);
    expect(platformSource("platform/owner/memory/probe.ts")).toBe(false);
    expect(platformSource("platform/sandbox/runner/runner.test.ts")).toBe(false);
    const link = "[`VENDOR_LIMIT`](https://example.test/upstream.ts)";
    expect(unlinkedNames(link)).toEqual([]);
    expect(unlinkedNames(link + " and `VENDOR_LIMIT`")).toEqual(["VENDOR_LIMIT"]);
  });

  it("checks repository paths and requires the linked module to define its constant", () => {
    const spec = join(SPECS, "owner-object/spec.md");
    expect(problems(spec, "\`owner/src/absent-in-this-test.ts\` \`files/read\`"))
      .toEqual(["owner/src/absent-in-this-test.ts: path is missing"]);
    const source = parse("bounds.ts", 'import { PAGE_BYTES } from "./page"; export const CAP = PAGE_BYTES;');
    expect(definesConstant(source, "PAGE_BYTES")).toBe(false);
    expect(definesConstant(source, "CAP", "L1")).toBe(true);
    expect(definesConstant(source, "CAP", "L1-L999")).toBe(false);
    expect(definesConstant(source, "CAP", "example")).toBe(false);
  });
});
