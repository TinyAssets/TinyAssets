/**
 * One checker for every Worker's isolation allow-list, and one test template for it.
 *
 * A Worker states as data (`<workspace>/guards/rules.ts`) what its source may import and
 * name. Imports are read from the syntax tree and resolved as paths: a relative one must
 * land in the Worker's own `src/` or on one of the modules outside it the rules name,
 * however it is spelled (`./../../owner/...` included); a bare one must be a module the
 * rules allow, anywhere or in one file alone; absolute specifiers, dynamic `import()`,
 * `require` and type imports by path are refused. A name the rules ban (an identifier, a
 * private name or any string, template parts included) is refused outside where they allow
 * it. Every rule holds over the import closure: each module a source reaches through
 * relative imports, however deep, is checked as the Worker's own source is.
 *
 * Tripwires for our own code (a split spelling such as `env["GATEWAY_" + "KEYRING"]` is not
 * seen), not proof against deliberate evasion.
 */
import { posix } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "./sources.ts";

export interface BannedName {
  /** What a name must not match (its match is the `%s` of `says`). */
  readonly pattern: RegExp;
  /** The one source that may name it, if any. */
  readonly home?: string;
  readonly says: string;
}

export interface ImportRules {
  /** The Worker's workspace folder: its own sources are `<worker>/src/`. */
  readonly worker: string;
  /** The Worker, as a problem names it ("the gateway"). */
  readonly called: string;
  /** The modules outside its `src/` it may reach, as resolved paths without extension. */
  readonly outside?: readonly string[];
  /** The packages it may import, any subpath of each, anywhere. */
  readonly packages?: readonly string[];
  /** The other bare modules it may import, each anywhere (`null`) or in one source alone. */
  readonly bare: Readonly<Record<string, string | null>>;
  /** The names no source may use. */
  readonly banned: readonly BannedName[];
}

/** `specifier` from the source at `path`, as a path from `platform/` without its extension. */
export function resolved(path: string, specifier: string): string {
  return posix.normalize(posix.join(posix.dirname(path), specifier))
    .replace(/\.(?:[cm]?[jt]s|tsx|jsx)$/, "");
}

/** Why `specifier`, imported by the source at `path`, is refused; `null` when allowed. */
export function importProblem(rules: ImportRules, path: string, specifier: string): string | null {
  if (specifier.startsWith("/") || /^[a-z][a-z0-9+.-]*:\/\//i.test(specifier)
      || specifier.startsWith("file:")) {
    return `imports ${specifier}, an absolute specifier`;
  }
  if (specifier.startsWith("./") || specifier.startsWith("../")) {
    const at = resolved(path, specifier);
    return at.startsWith(`${rules.worker}/src/`) || (rules.outside ?? []).includes(at)
      ? null : `imports ${specifier} (${at}), outside ${rules.called}`;
  }
  if ((rules.packages ?? []).some((name) => specifier === name || specifier.startsWith(`${name}/`))) {
    return null;
  }
  if (Object.hasOwn(rules.bare, specifier)) {
    const only = rules.bare[specifier];
    return only === null || only === path ? null : `imports ${specifier} outside ${only}`;
  }
  return `imports ${specifier}, not a module ${rules.called} may use`;
}

/** Each way `source` (at `path` from `platform/`) breaks `rules`. */
export function isolationProblems(rules: ImportRules, path: string, source: string): string[] {
  const file = parse(path, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node)
        || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      for (const name of rules.banned) {
        const match = name.pattern.exec(node.text);
        if (match !== null && path !== name.home) {
          report(node, name.says.replace("%s", match[0]));
        }
      }
    }
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier !== undefined) {
      const problem = ts.isStringLiteralLike(node.moduleSpecifier)
        ? importProblem(rules, path, node.moduleSpecifier.text) : "imports a computed module";
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

/** The relative specifiers `source` imports or re-exports. */
function relativeImports(path: string, source: string): string[] {
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier !== undefined && ts.isStringLiteralLike(node.moduleSpecifier)
        && node.moduleSpecifier.text.startsWith(".")) {
      found.push(node.moduleSpecifier.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(parse(path, source));
  return found;
}

/**
 * Every source `worker`'s own reach through relative imports, however deep, with its own:
 * what its bundle holds of ours. A data file (`.json`) is checked by its import's path alone.
 */
export function closure(worker: string,
                        all: ReadonlyMap<string, string> = workerSources()): Map<string, string> {
  const reached = new Map<string, string>();
  const queue = [...all.keys()].filter((path) => path.startsWith(`${worker}/src/`));
  while (queue.length) {
    const path = queue.pop()!;
    const source = all.get(path);
    if (reached.has(path) || source === undefined) {
      continue;
    }
    reached.set(path, source);
    for (const specifier of relativeImports(path, source)) {
      const at = resolved(path, specifier);
      queue.push(...[`${at}.ts`, `${at}/index.ts`].filter((next) => all.has(next)));
    }
  }
  return reached;
}

export interface IsolationTests {
  /** Sources a new file of the Worker could hold, each of which must be refused. */
  readonly sees: readonly (readonly [string, string])[];
  /** Sources that must pass, each named, at its path. */
  readonly allows: readonly (readonly [string, string, string])[];
}

/**
 * The tests every Worker's isolation is held to, from its rules: nothing over its whole
 * import closure breaks them, each of `sees` (as a new source of the Worker) is refused, and
 * each of `allows` passes.
 */
export function isolationTests(rules: ImportRules, tests: IsolationTests): void {
  describe(`${rules.worker} isolation, by its rules`, () => {
    it("finds nothing over the whole import closure", () => {
      const sources = closure(rules.worker);
      expect(sources.size).toBeGreaterThan(0);
      expect([...sources].flatMap(([path, source]) => isolationProblems(rules, path, source)))
        .toEqual([]);
    });

    it.each(tests.sees)("sees %s", (_what, source) => {
      expect(isolationProblems(rules, `${rules.worker}/src/probe.ts`, source)).not.toEqual([]);
    });

    it.each(tests.allows)("allows %s", (_what, path, source) => {
      expect(isolationProblems(rules, path, source)).toEqual([]);
    });
  });
}
