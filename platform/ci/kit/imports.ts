/**
 * The platform's import checks: one layer table over every file, and each workspace's banned
 * names over its import closure.
 *
 * **The layers** (untangle B1, `layers.ts`). Every script under `platform/` and `scripts/` is
 * in exactly one layer by its path, and each of its imports must land where its layer may
 * import (`layerProblems`); a workflow's inline module is held the same way
 * (`inlineImports`). Imports are read from the syntax tree, every way one module names
 * another, and a relative one is held by the path it resolves to, however it is spelled
 * (`./../../owner/...` included). `ci/guards/isolation.test.ts` holds it over every file, with
 * what each workspace owes (`owed.ts`) the only exceptions.
 *
 * **Banned names** (`<workspace>/guards/rules.ts`, `ImportRules`). A workspace names what no
 * source of its own may use (an identifier, a private name or any string, template parts
 * included), anywhere or outside one file; absolute specifiers, dynamic `import()`,
 * `require` and type imports by path are refused. The rules hold over the import closure: each
 * module a source reaches by a relative specifier, however deep, is checked as the
 * workspace's own source is, a module outside every Worker's `src/` on the way read from disk.
 * A job (`job`, a workspace run in Node rather than deployed) is held the same way from its
 * own folder.
 *
 * Tripwires for our own code (a split spelling such as `env["GATEWAY_" + "KEYRING"]` is not
 * seen), not proof against deliberate evasion.
 */
import { execFileSync } from "node:child_process";
import { existsSync, lstatSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, posix } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";
import { parse as parseYaml } from "yaml";

import { PLATFORM, REPO } from "../paths.ts";
import { workerFolders } from "../workers.ts";
import { INLINE, type Layer, LAYERS, SCRIPT } from "./layers.ts";
import type { Found } from "./owed.ts";
import { KINDS, parse, reporter, scriptsIn, workerSources } from "./sources.ts";

// -- Banned names -----------------------------------------------------------------------------

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
  /**
   * A workspace that is no Worker but a job run in Node (the cutover's importer): its own
   * sources are the scripts directly in this folder, from `platform/`, not its tests or
   * guards, and no Worker's source guard walks them.
   */
  readonly job?: string;
  /** The Worker, as a problem names it ("the gateway"). */
  readonly called: string;
  /** The names no source may use. */
  readonly banned: readonly BannedName[];
}

/** `specifier` from the source at `path`, as a path from `platform/` without its extension. */
export function resolved(path: string, specifier: string): string {
  return posix.normalize(posix.join(posix.dirname(path), specifier))
    .replace(/\.(?:[cm]?[jt]s|tsx|jsx)$/, "");
}

/**
 * The Worker whose own source `path` (from `platform/`, with or without its extension) is:
 * `<folder>/src/...` of a folder that holds a Worker's config. Null for any other module: the
 * contract's, the source records', a job's, a test's.
 */
export function workerOf(path: string): string | null {
  const folder = /^([^/]+)\/src(?:\/|$)/.exec(path)?.[1];
  return folder !== undefined && existsSync(join(PLATFORM, folder, "wrangler.jsonc")) ? folder
    : null;
}

/** Whether the source at `path` (from `platform/`) is one of the workspace's own. */
export function owns(rules: ImportRules, path: string): boolean {
  return rules.job === undefined ? path.startsWith(`${rules.worker}/src/`)
    : posix.dirname(path) === rules.job && !/\.config(?:\.[cm]?[jt]s)?$/.test(path);
}

/** A specifier that names a place, not a package: an absolute path or a URL. */
const ABSOLUTE = /^(?:\/|file:|[a-z][a-z0-9+.-]*:\/\/)/i;

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
      if (!ts.isStringLiteralLike(node.moduleSpecifier)) {
        report(node, "imports a computed module");
      } else if (ABSOLUTE.test(node.moduleSpecifier.text)) {
        report(node, `imports ${node.moduleSpecifier.text}, an absolute specifier`);
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

/**
 * The relative specifiers `source` reaches: imports and re-exports, a type imported by path
 * (`import("…").T`), `import()` and `require()` of a literal, and `/// <reference path>`.
 */
function relativeImports(path: string, source: string): string[] {
  return specifiers(path, source).filter((specifier): specifier is string =>
    specifier !== null && specifier.startsWith("."));
}

/** A module outside every Worker's `src/` (a test's helper, a guard), read from disk. */
function onDisk(path: string): string | undefined {
  const full = join(PLATFORM, path);
  return !/^[^/]+\/src\//.test(path) && !path.split("/").includes("node_modules")
    && existsSync(full) ? readFileSync(full, "utf8") : undefined;
}

/**
 * Every source `worker`'s own reach by relative specifiers (`relativeImports`), however
 * deep, with its own: what its bundle holds of ours, and any module outside a Worker's `src/`
 * on the way. A data file (`.json`) is checked by its import's path alone, and one in a
 * Worker's `src/` is in the closure, so another Worker's is seen.
 */
export function closure(worker: string,
                        all: ReadonlyMap<string, string> = workerSources()): Map<string, string> {
  return walk(all, [...all.keys()].filter((path) => path.startsWith(`${worker}/src/`)));
}

/**
 * Every source the workspace's own sources reach, with its own: a Worker's `closure`, or a
 * job's from the scripts in its folder, through the Workers' sources (the contract's).
 */
export function reach(rules: ImportRules): Map<string, string> {
  if (rules.job === undefined) {
    return closure(rules.worker);
  }
  const all = new Map([...workerSources(), ...scriptsIn(rules.job)]);
  return walk(all, [...all.keys()].filter((path) => owns(rules, path)));
}

/** The extensions a script is imported without (`resolved` drops them), as a bundler tries. */
const SCRIPTS = Object.freeze([".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"]);

function walk(all: ReadonlyMap<string, string>, queue: string[]): Map<string, string> {
  const reached = new Map<string, string>();
  while (queue.length) {
    const path = queue.pop()!;
    const source = all.get(path) ?? onDisk(path);
    if (reached.has(path) || source === undefined) {
      continue;
    }
    reached.set(path, source);
    for (const specifier of relativeImports(path, source)) {
      const at = resolved(path, specifier);
      if (/\.[^/.]+$/.test(at) && workerOf(at) !== null
          && (all.has(at) || existsSync(join(PLATFORM, at)))) {
        reached.set(at, "");  // a Worker's data file (`.json`): reached, never read as a script
      }
      queue.push(...[...SCRIPTS.map((extension) => `${at}${extension}`), `${at}/index.ts`]
        .filter((next) => all.has(next) || onDisk(next) !== undefined));
    }
  }
  return reached;
}

export interface IsolationTests {
  /** Sources a new file of the workspace could hold, each of which must be refused. */
  readonly sees: readonly (readonly [string, string])[];
  /** Sources that must pass, each named, at its path. */
  readonly allows: readonly (readonly [string, string, string])[];
}

/**
 * The tests every workspace's banned names are held to, from its rules: nothing over its
 * whole import closure breaks them, each of `sees` (as a new source of the workspace) is
 * refused, and each of `allows` passes.
 */
export function isolationTests(rules: ImportRules, tests: IsolationTests): void {
  describe(`${rules.worker} isolation, by its rules`, () => {
    it("finds nothing over the whole import closure", () => {
      const sources = reach(rules);
      expect(sources.size).toBeGreaterThan(0);
      expect([...sources].flatMap(([path, source]) => isolationProblems(rules, path, source)))
        .toEqual([]);
    });

    it.each(tests.sees)("sees %s", (_what, source) => {
      const probe = `${rules.job ?? `${rules.worker}/src`}/probe.ts`;
      expect(isolationProblems(rules, probe, source)).not.toEqual([]);
    });

    it.each(tests.allows)("allows %s", (_what, path, source) => {
      expect(isolationProblems(rules, path, source)).toEqual([]);
    });
  });
}

// -- The layers -------------------------------------------------------------------------------

/** Each pattern's expression, by the pattern and what its placeholders stand for. */
const GLOBS = new Map<string, RegExp>();

/** `pattern` (`layers.ts`'s grammar, without its `!`) as a regular expression. */
function globOf(pattern: string, ws: string | null, worker: string | null,
                workers: readonly string[]): RegExp {
  const key = JSON.stringify([pattern, ws, worker, workers]);
  let glob = GLOBS.get(key);
  if (glob === undefined) {
    glob = compiled(pattern, ws, worker, workers);
    GLOBS.set(key, glob);
  }
  return glob;
}

function compiled(pattern: string, ws: string | null, worker: string | null,
                  workers: readonly string[]): RegExp {
  const escape = (text: string) => text.replace(/[.+^$()|[\]\\?]/g, "\\$&");
  let out = "";
  for (let i = 0; i < pattern.length;) {
    if (pattern.startsWith("**/", i)) {
      out += "(?:[^/]+/)*";
      i += 3;
    } else if (pattern.startsWith("/**", i) && i + 3 === pattern.length) {
      out += "(?:/.*)?";
      i += 3;
    } else if (pattern.startsWith("**", i)) {
      out += ".*";
      i += 2;
    } else if (pattern[i] === "*") {
      out += "[^/]*";
      i += 1;
    } else if (pattern[i] === "{") {
      const end = pattern.indexOf("}", i);
      out += `(?:${pattern.slice(i + 1, end).split(",").map(escape).join("|")})`;
      i = end + 1;
    } else if (pattern.startsWith("<ws>", i)) {
      out += ws === null ? "[^/]+" : escape(ws);
      i += 4;
    } else if (pattern.startsWith("<worker>", i)) {
      out += worker !== null ? escape(worker)
        : workers.length ? `(?:${workers.map(escape).join("|")})` : "(?!)";
      i += 8;
    } else {
      out += escape(pattern[i]!);
      i += 1;
    }
  }
  return new RegExp(`^${out}$`);
}

/** Whether `path` is in `patterns`: the last one that matches it decides. */
function within(patterns: readonly string[], path: string, ws: string | null,
                worker: string | null, workers: readonly string[]): boolean {
  let inside = false;
  for (const pattern of patterns) {
    const negated = pattern.startsWith("!");
    if (globOf(negated ? pattern.slice(1) : pattern, ws, worker, workers).test(path)) {
      inside = !negated;
    }
  }
  return inside;
}

/** The workspace a path from the repository root is in: its folder of `platform/`. */
function workspaceOf(path: string): string | null {
  return /^platform\/([^/]+)\//.exec(path)?.[1] ?? null;
}

/** Whether `layer`'s files are named by `<worker>`, so its `may` names that Worker's folder. */
const bindsWorker = (layer: Layer) => layer.files.some((each) => each.includes("<worker>"));

/** The layers `path` (from the repository root) is in: exactly one, unless the table is wrong. */
export function layersOf(path: string, layers: readonly Layer[] = LAYERS,
                         workers: readonly string[] = workerFolders()): Layer[] {
  return layers.filter((layer) => within(layer.files, path, null, null, workers));
}

/** Whether `layer`, holding a file of `ws`, may import what lands at `to`. */
function mayImport(layer: Layer, ws: string | null, to: string,
                   workers: readonly string[]): boolean {
  const worker = ws !== null && bindsWorker(layer) ? ws : null;
  return within([...layer.may, ...(worker === null ? [] : layer.also?.[worker] ?? [])], to, ws,
                worker, workers);
}

/**
 * The layers of `ws` other than `layer` (each whose files, named for `ws`, lie in its folder):
 * what a layer that `inherits` may import, too.
 */
function ownLayers(layer: Layer, ws: string, layers: readonly Layer[],
                   workers: readonly string[]): Layer[] {
  return layers.filter((other) => other !== layer && other.files.some((pattern) =>
    !pattern.startsWith("!") && (!pattern.includes("<worker>") || workers.includes(ws))
    && pattern.replaceAll("<ws>", ws).replaceAll("<worker>", ws).startsWith(`platform/${ws}/`)));
}

/**
 * What a script names as a module, every way: each specifier, or null where it is computed.
 * TypeScript's own pre-processor reads the named ones (imports and re-exports, type ones
 * too, a type imported by path, `import()`, `require()`, `import x = require()` and
 * `/// <reference path>`, as its compiler finds them, at a fifth of a full parse's cost); a
 * script that calls `import(` or `require(` at all is parsed in full for those it computes.
 */
function specifiers(path: string, source: string): (string | null)[] {
  const named = ts.preProcessFile(source, true, true);
  const found: (string | null)[] = [
    ...named.referencedFiles, ...named.importedFiles,
    ...named.typeReferenceDirectives.filter((each) => each.fileName.startsWith(".")),
  ].map((each) => each.fileName);
  if (!/\b(?:import|require|createRequire)\s*\(|=\s*require\b|\bimport\.meta\.|\bvi\./
    .test(source)) {
    return found;
  }
  // `require` under another name: `createRequire` itself imported as another
  // (`createRequire as make`), a binding of what it makes or of `require`
  // (`const load = createRequire(…)`, `module.createRequire(…)`, `= require`), and what it
  // makes called at once (`createRequire(…)(…)`).
  const makers = new Set(["createRequire", ...[...source.matchAll(
    /\bcreateRequire\s+as\s+([A-Za-z_$][\w$]*)/g)].map((each) => each[1]!)]);
  const made = (node: ts.Expression): boolean => ts.isCallExpression(node)
    && (ts.isIdentifier(node.expression) ? makers.has(node.expression.text)
      : ts.isPropertyAccessExpression(node.expression)
        && node.expression.name.text === "createRequire");
  const requires = new Set(["require"]);
  const bind = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)
        && node.initializer !== undefined
        && (made(node.initializer)
            || (ts.isIdentifier(node.initializer) && requires.has(node.initializer.text)))) {
      requires.add(node.name.text);
    }
    ts.forEachChild(node, bind);
  };
  const loads = (callee: ts.Expression): "named" | "computed" | null => {
    if (callee.kind === ts.SyntaxKind.ImportKeyword
        || (ts.isIdentifier(callee) && requires.has(callee.text)) || made(callee)) {
      return "named";
    }
    if (!ts.isPropertyAccessExpression(callee)) {
      return null;
    }
    // vitest's loaders name a module as an import does; `import.meta`'s find one at run time.
    if (ts.isIdentifier(callee.expression) && callee.expression.text === "vi"
        && /^(?:mock|doMock|unmock|doUnmock|importActual|importMock)$/.test(callee.name.text)) {
      return "named";
    }
    return ts.isMetaProperty(callee.expression) && /^(?:glob|resolve)$/.test(callee.name.text)
      ? "computed" : null;
  };
  const visit = (node: ts.Node): void => {
    const how = ts.isCallExpression(node) ? loads(node.expression) : null;
    if (how !== null && ts.isCallExpression(node)) {
      const first = node.arguments[0];
      if (how === "computed" || first === undefined || !ts.isStringLiteralLike(first)) {
        found.push(null);
      } else if (node.expression.kind !== ts.SyntaxKind.ImportKeyword
                 && !(ts.isIdentifier(node.expression) && node.expression.text === "require")) {
        found.push(first.text);  // the pre-processor has read `import("…")` and `require("…")`
      }
    }
    ts.forEachChild(node, visit);
  };
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, false,
                                   KINDS[posix.extname(path)] ?? ts.ScriptKind.TS);
  bind(file);
  visit(file);
  return found;
}

/** Each workspace's package name (`@tinyassets/owner`), with its folder from the repository root. */
let packages: Map<string, string> | undefined;
function workspacePackages(): Map<string, string> {
  return packages ??= new Map(readdirSync(PLATFORM).flatMap((folder) => {
    const at = join(PLATFORM, folder, "package.json");
    const name = isFile(at) ? (JSON.parse(readFileSync(at, "utf8")) as { name?: string }).name
      : undefined;
    return name === undefined ? [] : [[name, `platform/${folder}`] as const];
  }));
}

/**
 * Where a specifier that names a workspace by its package (`@tinyassets/owner/src/x`, which
 * npm links into `node_modules`) lands, or null for any other: held as the path is.
 */
function workspaceNamed(specifier: string): string | null {
  for (const [name, folder] of workspacePackages()) {
    if (specifier === name || specifier.startsWith(`${name}/`)) {
      return landing(`${folder}/[package]`, `.${specifier.slice(name.length)}`,
                     (path) => isFile(join(REPO, path)));
    }
  }
  return null;
}

/**
 * A specifier that names a package or a runtime's module (`vitest/config`, `@scope/name/x`,
 * `node:fs`, `cloudflare:workers`). Any other that is no relative path names a place under
 * another name: an absolute path, a URL, or a path rooted at an expression
 * (`${{ github.workspace }}/platform/…`, `$GITHUB_WORKSPACE/…`).
 */
const PACKAGE =
  /^(?:[a-z][a-z0-9-]*:)?(?:@[a-z0-9~-][a-z0-9._~-]*\/)?[a-z0-9~-][a-z0-9._~-]*(?:\/[\w.~@/-]+)?$/;

/**
 * A path rooted at an expression a workflow step holds the checkout's folder in
 * (`$GITHUB_WORKSPACE/…`, `${root}/…`, `${{ github.workspace }}/…`): the path from there.
 */
const ROOTED = /^(?:\$\{\{[^}]*\}\}|\$\{[^}]*\}|\$[A-Za-z_]\w*)\/(.+)$/;

/** Where `specifier`, relative, from `from`, lands: the file it names, by the bundler's tries. */
function landing(from: string, specifier: string, has: (path: string) => boolean): string {
  const at = posix.normalize(posix.join(posix.dirname(from), specifier));
  const tries = [at, ...SCRIPTS.map((extension) => `${at}${extension}`),
                 ...SCRIPTS.map((extension) => `${at}/index${extension}`),
                 at.replace(/\.([cm]?)js$/, ".$1ts")];
  return tries.find(has) ?? at;
}

/**
 * Each import of `from` (held as `layer`, in workspace `ws`) its layer does not allow, as a
 * violation: `<from> imports <where it lands>`, or the specifier itself where it is no
 * relative path, or a computed module.
 */
function edgeViolations(layer: Layer, layers: readonly Layer[], from: string, ws: string | null,
                        named: readonly (string | null)[], has: (path: string) => boolean,
                        workers: readonly string[]): string[] {
  const holders = [layer, ...(layer.inherits === true && ws !== null
    ? ownLayers(layer, ws, layers, workers) : [])];
  const out: string[] = [];
  for (const specifier of named) {
    if (specifier === null) {
      if (layer.computed !== true) {
        out.push(`${from} imports a computed module`);
      }
    } else if (specifier.startsWith("./") || specifier.startsWith("../")
               || workspaceNamed(specifier) !== null) {
      const to = workspaceNamed(specifier) ?? landing(from, specifier, has);
      if (!holders.some((each) => mayImport(each, ws, to, workers))) {
        out.push(`${from} imports ${to}`);
      }
    } else if (specifier.startsWith("#")) {
      out.push(`${from} imports ${specifier}, a name its package maps to a path`);
    } else if (layer.packages === undefined ? !PACKAGE.test(specifier)
               : !layer.packages.some((name) => specifier === name
                                      || specifier.startsWith(`${name}/`))) {
      out.push(`${from} imports ${specifier}`);
    }
  }
  return [...new Set(out)];
}

/** What the layer table finds over a set of files: files in no layer or two, and violations. */
export interface LayerReport {
  /** Each file in no layer, or in more than one: the table's own error, never owed. */
  readonly unplaced: readonly string[];
  /** Each import a layer does not allow, by the workspace that owes it. */
  readonly violations: readonly Found[];
}

export interface LayerInputs {
  /** The scripts, by path from the repository root, with their text. */
  readonly files: ReadonlyMap<string, string>;
  /** The workflows, by path from the repository root, with their text. */
  readonly workflows?: ReadonlyMap<string, string>;
  readonly layers?: readonly Layer[];
  readonly inline?: Layer;
  readonly workers?: readonly string[];
}

/**
 * Every file of `files` in exactly one layer, and every import of each, and of each workflow's
 * inline modules, where its layer may import. A file's violation is owed by its workspace
 * (`scripts` for the repository's scripts, which owe nothing), and a workflow's by the ci
 * workspace, whose guard reads the workflows.
 */
export function layerProblems(inputs: LayerInputs): LayerReport {
  const { files, workflows = new Map<string, string>(), layers = LAYERS, inline = INLINE,
          workers = workerFolders() } = inputs;
  const has = (path: string) => files.has(path) || isFile(join(REPO, path));
  const unplaced: string[] = [];
  const violations: Found[] = [];
  for (const [path, source] of files) {
    const placed = layersOf(path, layers, workers);
    if (placed.length !== 1) {
      unplaced.push(placed.length === 0 ? `${path} is in no layer`
        : `${path} is in ${placed.length} layers: ${placed.map((each) => each.name).join(", ")}`);
      continue;
    }
    const ws = workspaceOf(path);
    for (const what of edgeViolations(placed[0]!, layers, path, ws, specifiers(path, source),
                                      has, workers)) {
      violations.push({ workspace: ws ?? path.split("/")[0]!, what });
    }
  }
  for (const [path, text] of workflows) {
    if (!within(inline.files, path, null, null, workers)) {
      unplaced.push(`${path} is no workflow`);
      continue;
    }
    const found = new Set<string>();
    for (const { dir, code } of inlineImports(text)) {
      // A path rooted at an expression (`$GITHUB_WORKSPACE/…`) is held from the root.
      const named = specifiers("inline.ts", code);
      const rooted = named.map((each) => (each === null ? null : ROOTED.exec(each)?.[1]));
      for (const [from, held] of [
        [posix.join(dir, "[inline]"), named.filter((_each, at) => rooted[at] == null)],
        ["[inline]", rooted.filter((each) => each != null).map((each) => `./${each}`)],
      ] as const) {
        for (const what of edgeViolations(inline, layers, from, null, held, has, workers)) {
          found.add(`${path}${what.slice(from.length)}`);
        }
      }
    }
    violations.push(...[...found].map((what) => ({ workspace: "ci", what })));
  }
  return { unplaced, violations };
}

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/**
 * The modules a workflow's or a composite action's steps name inline, each with the folder
 * its step runs in, from the repository root (the step's `working-directory`, else its job's
 * or the workflow's default): what a step evaluates (`node -e`, `--eval`, `-p`, `--print`),
 * its shell quoting undone, and each module its text names by a path, however it is run.
 */
export function inlineImports(text: string): { dir: string; code: string }[] {
  // The workflow YAML is untyped data, read field by field.
  type Data = any;  // eslint-disable-line @typescript-eslint/no-explicit-any
  const data = parseYaml(text) as Data;
  const found: { dir: string; code: string }[] = [];
  const named = (value: unknown) => (typeof value === "string" ? value : undefined);
  const evals = /(?:^|\s)(?:-e|--eval|-p|--print)(?:\s|\\\n)+("(?:[^"\\]|\\[\s\S])*"|'[^']*')/g;
  // A module a step names by a relative path, in any text it runs: `--eval=`, `-pe`, a
  // here-document, `shell: node`, a script of `actions/github-script`.
  const paths = /\b(?:from|import|require)\b[\s(]*\\?["'`](\.{1,2}\/[^"'`\\\s]+)/g;
  // And by a path that is absolute or rooted at an expression (`$X/…`, `${{ … }}/…`), where
  // it names a script or a data file: a shell command of the same words names neither.
  const rooted =
    /\b(?:from|import|require)\b[\s(]*\\?["'`]([/$](?:\{\{[^}]*\}\}|[^"'`\\\s])+\.(?:[cm]?[jt]s|json))["'`\\]/g;
  // A composite action's steps run in the caller's folder, as a workflow's do.
  const jobs = [...Object.values<Data>(data?.jobs ?? {}),
                ...(data?.runs === undefined ? [] : [data.runs])];
  for (const job of jobs) {
    for (const step of (job?.steps ?? []) as Data[]) {
      const texts = [step?.run, step?.with?.script]
        .filter((each): each is string => typeof each === "string");
      const dir = posix.normalize(named(step?.["working-directory"])
        ?? named(job?.defaults?.run?.["working-directory"])
        ?? named(data?.defaults?.run?.["working-directory"]) ?? ".");
      for (const text of texts) {
        for (const match of text.matchAll(evals)) {
          const quoted = match[1]!;
          const code = quoted.startsWith("'") ? quoted.slice(1, -1)
            : quoted.slice(1, -1).replace(/\\\n/g, "").replace(/\\(["\\$`])/g, "$1");
          found.push({ dir: dir === "." ? "" : dir, code });
        }
        const pathed = [...text.matchAll(paths), ...text.matchAll(rooted)]
          .map((match) => match[1]!);
        if (pathed.length) {
          found.push({ dir: dir === "." ? "" : dir,
                       code: pathed.map((each) => `import ${JSON.stringify(each)};`).join("\n") });
        }
      }
    }
  }
  return found;
}

/** Every path under `platform/` and `scripts/` git tracks, or does not ignore. */
function listed(): string[] {
  return execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard",
                              "--", "platform", "scripts"],
                      { cwd: REPO, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 })
    .split("\0").filter((path) => path !== "").sort();
}

/**
 * Every script under `platform/` and `scripts/`, by path from the repository root, with its
 * text: what git tracks, and any new file it does not ignore (dependencies and builds are).
 */
export function repoScripts(): Map<string, string> {
  return new Map(listed().filter((path) => SCRIPT.test(path) && isFile(join(REPO, path)))
    .map((path) => [path, readFileSync(join(REPO, path), "utf8")] as const));
}

/**
 * Every symbolic link under `platform/` and `scripts/`: a link is a file, or a folder, of
 * another path, which the table would hold by the link's own.
 */
export function repoLinks(): string[] {
  return listed().filter((path) => lstatSync(join(REPO, path), { throwIfNoEntry: false })
    ?.isSymbolicLink() === true);
}
