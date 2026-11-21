/**
 * The cutover's manifest (`platform/cutover.json`, read by `ci/kit/cutover.ts`) held to the
 * tree, so the cutover's change is the manifest applied and nothing else:
 * - it is in its shape: three phases, each entry a delete, an edit, a move or a live object;
 * - every entry exists: each path, each name an edit or a move takes out of its file, each
 *   import an edit names, and each live object's name in the file that names it today; no
 *   delete or edit lies inside a path the manifest deletes, and no file is edited twice in a
 *   phase; a move may (a carry-out, made before the deletes), but never into one;
 * - every file under `platform/` and `.github/` that says what is "deleted at the cutover",
 *   speaks the cutover's vocabulary (`/workspace/imported`, "old TinyAssets", the `import` and
 *   `discard` callers, `IMPORT_*`) or names a platform, workflow or script path it deletes, is
 *   on it;
 * - the layer table's cutover layer (`ci/kit/imports.ts`): what it deletes may import itself,
 *   and nothing that stays imports it but an import an edit names (held over the tree by
 *   `isolation.test.ts`, its cases here); a Python file that stays the same, by its imports;
 * - its dry run (`ci/dry-run.ts`) makes the tree its cases say.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join, posix } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { type CutoverLayer, cutoverLayer, dryRun, dryRunLegs, type Entry, isGone, LIVE,
         type Manifest, MANIFEST, PHASES, pythonLoads } from "../kit/cutover";
import { keyed, object } from "../../contract/src/checks";
import { layerProblems, repoScripts } from "../kit/imports";
import { treeFiles } from "../kit/sources";
import { REPO } from "../paths";
import { actionFiles, type Data, workflowNames } from "../workflows";
import { workerFolders } from "../wrangler";

/** A text that says what is deleted at the cutover, however its comment wraps the words. */
const MARKER = /deleted(?:[\s*#/]|<!--)+at(?:[\s*#/])+the(?:[\s*#/])+cutover/i;
/** The cutover's vocabulary: no file that stays may speak it. */
const VOCABULARY: readonly RegExp[] = Object.freeze([
  /\/workspace\/imported/, /old TinyAssets/i, /["'](?:import|discard)["']/, /\bIMPORT_[A-Z0-9_]+/]);
/**
 * The paths the manifest deletes under `platform/`, `.github/` and `scripts/`, as a file names
 * them: from the repository root, under `platform/` from there (`import/bundle.ts`), or as a
 * step's local action (`./.github/actions/x`). A one-word folder (`import`) is named only with
 * a path below it. The old stack's own paths are left out: a platform comment cites the code it
 * was ported from as history.
 */
function goneNames(manifest: Manifest): RegExp[] {
  return cutoverLayer(manifest).gone.filter((path) => /^(?:platform|\.github|scripts)\//.test(path))
    .flatMap((path) => [...new Set([path, path.replace(/^platform\//, ""),
                                    path.replace(/^\.github\//, "./.github/")])].map((name) =>
      new RegExp(`(?<![\\w./@-])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}${
        name.includes("/") ? "(?![\\w.-])" : "/"}`)));
}

/** The shells of a workflow's or a composite action's `run` steps, one after another. */
function runs(text: string): string {
  const data = (parse(text) ?? {}) as Data;
  return [...Object.values(data.jobs ?? {}) as Data[], data.runs ?? {}]
    .flatMap((job: Data) => (job.steps ?? []) as Data[])
    .flatMap((step: Data) => typeof step.run === "string" ? [step.run as string] : []).join("\n");
}

/**
 * Each script of `gone` a workflow's steps run: named by its path, or as a module
 * (`python -m scripts.x`). A step's script is the workflow's load, as an import is a module's.
 */
function stepLoads(text: string, gone: readonly string[]): string[] {
  const shell = runs(text);
  const escape = (name: string) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return gone.filter((path) => /^scripts\/.+\.py$/.test(path)).filter((path) =>
    new RegExp(`(?<![\\w./-])(?:${escape(path)}|${escape(path.slice(0, -3).replaceAll("/", "."))
    })(?![\\w-])`).test(shell));
}

/** A tree the manifest is held to: whether a path is there, and a file's text. */
interface Tree {
  readonly exists: (path: string) => boolean;
  readonly text: (path: string) => string | null;
}

const REPO_TREE: Tree = {
  exists: (path) => existsSync(join(REPO, path)),
  text: (path) => {
    const at = join(REPO, path);
    return existsSync(at) && statSync(at).isFile() ? readFileSync(at, "utf8") : null;
  },
};

const words = (value: unknown) => Array.isArray(value) && value.length > 0
  && value.every((each) => typeof each === "string" && each !== "");
const text = (value: unknown) => typeof value === "string" && value !== "";

/** A key of an entry: whether it must be there, and what its value is. */
type Key = readonly [boolean, (value: unknown) => boolean];
/** Each entry's keys, by its action. */
const SHAPES: Readonly<Record<string, Readonly<Record<string, Key>>>> = {
  delete: { delete: [true, text], what: [true, text] },
  edit: { edit: [true, text], names: [true, words], imports: [false, words], what: [true, text] },
  move: { move: [true, text], to: [true, text], names: [false, words], what: [true, text] },
  live: { live: [true, (v) => LIVE.includes(v as (typeof LIVE)[number])], name: [true, text],
          in: [true, text], what: [true, text] },
};

/** How the manifest is out of its shape. */
function shapeProblems(value: unknown): string[] {
  if (!keyed(value, ["about", "phases"]) || !text(value["about"])
      || !object(value["phases"]) || Object.keys(value["phases"]).join() !== PHASES.join()) {
    return [`the manifest is its about and its phases, ${PHASES.join(", ")}, in order`];
  }
  const problems: string[] = [];
  for (const phase of PHASES) {
    const body = value["phases"][phase];
    if (!keyed(body, ["entries", "why"]) || !text(body["why"])
        || !Array.isArray(body["entries"])) {
      problems.push(`phase ${phase} is its why and its entries`);
      continue;
    }
    body["entries"].forEach((entry: unknown, n: number) => {
      const action = object(entry) ? Object.keys(SHAPES).filter((key) => key in entry) : [];
      const shape = action.length === 1 ? SHAPES[action[0]!]! : null;
      if (shape === null || !Object.keys(entry as object).every((key) => key in shape)
          || !Object.entries(shape).every(([key, [needed, valid]]) =>
            (entry as Record<string, unknown>)[key] === undefined ? !needed
              : valid((entry as Record<string, unknown>)[key]))) {
        problems.push(`phase ${phase}'s entry ${n} is no delete, edit, move or live object`);
      }
    });
  }
  return problems;
}

const entries = (manifest: Manifest) => PHASES.flatMap((phase) =>
  manifest.phases[phase].entries.map((entry) => ({ phase, entry })));

/** The paths an entry lists: what it deletes, edits or moves. */
const pathOf = (entry: Entry): string | null =>
  "delete" in entry ? entry.delete : "edit" in entry ? entry.edit
    : "move" in entry ? entry.move : null;

/** How an entry is not what the tree holds today. */
function entryProblems(manifest: Manifest, tree: Tree): string[] {
  const problems: string[] = [];
  const { gone } = cutoverLayer(manifest);
  const edited = new Set<string>();
  const holds = (path: string, names: readonly string[], what: string) => {
    const found = tree.text(path);
    for (const name of names) {
      if (found === null || !found.includes(name)) {
        problems.push(`${what} names ${name}, which ${path} does not hold`);
      }
    }
  };
  for (const { phase, entry } of entries(manifest)) {
    const path = pathOf(entry);
    if (path !== null && !tree.exists(path)) {
      problems.push(`${phase}: ${path} is not there`);
      continue;
    }
    if (path !== null && !("move" in entry)
        && gone.some((each) => each !== path && isGone([each], path))) {
      problems.push(`${phase}: ${path} lies inside a path the manifest deletes`);
    }
    if ("edit" in entry) {
      if (edited.has(`${phase} ${entry.edit}`)) {
        problems.push(`${phase}: ${entry.edit} is edited twice`);
      }
      edited.add(`${phase} ${entry.edit}`);
      holds(entry.edit, entry.names, `the edit of ${entry.edit}`);
      for (const to of entry.imports ?? []) {
        if (!isGone(gone, to)) {
          problems.push(`the edit of ${entry.edit} names an import of ${to}, which stays`);
        }
      }
    } else if ("move" in entry) {
      holds(entry.move, entry.names ?? [], `the move of ${entry.move}`);
      if (isGone(gone, entry.to)) {
        problems.push(`${phase}: ${entry.move} moves into ${entry.to}, which the manifest deletes`);
      }
      if (tree.exists(entry.to) !== (entry.names !== undefined)) {
        problems.push(`${phase}: ${entry.move} moves ${entry.names === undefined
          ? `to ${entry.to}, which is there already` : `its names into ${entry.to}, not there`}`);
      }
    } else if ("live" in entry) {
      holds(entry.in, [entry.name], `the ${entry.live} ${entry.name}`);
    }
  }
  return problems;
}

/** Whether `path` is on the manifest: deleted, or inside what is, or edited or moved. */
function listed(manifest: Manifest, path: string): boolean {
  return entries(manifest).some(({ entry }) => {
    const at = pathOf(entry);
    return at !== null && ("delete" in entry ? isGone([at], path) : at === path);
  });
}

/** Each file of `files` that marks what the cutover deletes, or speaks its vocabulary, unlisted. */
function scopeProblems(manifest: Manifest, files: ReadonlyMap<string, string>): string[] {
  return [...files].flatMap(([path, content]) => {
    if (listed(manifest, path)) {
      return [];
    }
    const said = [MARKER.test(content) ? "deleted at the cutover" : null,
                  ...[...VOCABULARY, ...goneNames(manifest)].map((word) =>
                    word.exec(content)?.[0] ?? null)]
      .filter((each) => each !== null);
    return said.length === 0 ? [] : [`${path} says ${said.join(", ")}, and is not on the manifest`];
  });
}

/** The imports into what the cutover deletes that files which stay make: `<from> imports <to>`. */
function cutoverImports(files: ReadonlyMap<string, string>, layer: CutoverLayer): string[] {
  const { violations } = layerProblems({ files, workers: workerFolders(),
                                         cutover: { gone: layer.gone, entries: new Map() } });
  return violations.flatMap((each) =>
    /^(\S+ imports \S+), which the cutover deletes/.exec(each.what)?.[1] ?? []);
}

/** A manifest of `entries` in its cutover phase, the other two empty. */
const manifestOf = (...list: Entry[]): Manifest => ({
  about: "a probe", phases: { routes: { why: "a probe", entries: [] },
                               cutover: { why: "a probe", entries: list },
                               hold: { why: "a probe", entries: [] } } });

/** A tree of `files`, by path, with their folders. */
function treeOf(files: Record<string, string>): Tree {
  return {
    exists: (path) => Object.keys(files).some((each) => each === path
      || each.startsWith(`${path}/`)),
    text: (path) => files[path] ?? null,
  };
}

describe("the cutover's manifest", () => {
  it("is in its shape", () => {
    expect(shapeProblems(MANIFEST)).toEqual([]);
    const what = "a probe";
    for (const [why, entry] of [
      ["two actions", { delete: "a", edit: "a", what }],
      ["a key no action has", { delete: "a", why: "b", what }],
      ["an edit that takes out nothing", { edit: "a", names: [], what }],
      ["a kind of live object there is none of", { live: "server", name: "a", in: "b", what }],
      ["no what", { delete: "a" }],
    ] as const) {
      expect([why, shapeProblems(manifestOf(entry as unknown as Entry))])
        .toEqual([why, ["phase cutover's entry 0 is no delete, edit, move or live object"]]);
    }
    expect(shapeProblems({ about: "a", phases: { cutover: { why: "a", entries: [] } } }))
      .toEqual(["the manifest is its about and its phases, routes, cutover, hold, in order"]);
  });

  it("names only what the tree holds today, and nothing inside what it deletes", () => {
    expect(entryProblems(MANIFEST, REPO_TREE)).toEqual([]);
    const tree = treeOf({ "platform/a/x.ts": "export const ALPHA = 1;",
                          "platform/b.ts": "export const BETA = 2;", "platform/c.ts": "" });
    const what = "a probe";
    expect(entryProblems(manifestOf(
      { delete: "platform/a", what }, { delete: "platform/gone.ts", what },
      { edit: "platform/b.ts", names: ["BETA", "GAMMA"], imports: ["platform/c.ts"], what },
      { edit: "platform/b.ts", names: ["BETA"], what },
      { edit: "platform/a/x.ts", names: ["ALPHA"], what },
      { move: "platform/b.ts", to: "platform/c.ts", what },
      { move: "platform/b.ts", to: "platform/d.ts", names: ["BETA"], what },
      { move: "platform/a/x.ts", to: "platform/a/y.ts", what },
      { move: "platform/a/x.ts", to: "platform/e.ts", what },
      { live: "queue", name: "tinyassets-probe", in: "platform/b.ts", what }), tree)).toEqual([
      "cutover: platform/gone.ts is not there",
      "the edit of platform/b.ts names GAMMA, which platform/b.ts does not hold",
      "the edit of platform/b.ts names an import of platform/c.ts, which stays",
      "cutover: platform/b.ts is edited twice",
      "cutover: platform/a/x.ts lies inside a path the manifest deletes",
      "cutover: platform/b.ts moves to platform/c.ts, which is there already",
      "cutover: platform/b.ts moves its names into platform/d.ts, not there",
      "cutover: platform/a/x.ts moves into platform/a/y.ts, which the manifest deletes",
      "the queue tinyassets-probe names tinyassets-probe, which platform/b.ts does not hold",
    ]);
  });

  it("lists every file under platform/ and .github/ that marks a deletion or speaks the cutover",
     () => {
    expect(scopeProblems(MANIFEST, treeFiles())).toEqual([]);
    const what = "a probe";
    expect(scopeProblems(manifestOf({ delete: "platform/import", what },
                                    { delete: ".github/actions/gone", what },
                                    { delete: "scripts/old.py", what },
                                    { edit: "platform/kept.ts", names: ["IMPORT_X"], what }),
                         new Map([
      ["platform/import/x.ts", "export const IMPORT_X = 1;"],
      ["platform/kept.ts", "export const IMPORT_X = 1;"],
      ["platform/a.ts", "/**\n * Deleted at the\n * cutover with the job.\n */"],
      ["platform/b.md", "Your files from the old TinyAssets are in /workspace/imported."],
      ["platform/c.ts", 'const caller = "discard";'],
      ["platform/d.ts", "export const IMPORTANT_IMPORTS = 1; // imports a module"],
      ["platform/e.ts", "// built by `import/bundle.ts`"],
      [".github/workflows/f.yml", "      - uses: ./.github/actions/gone\n      - run: python scripts/old.py"],
      ["platform/g.ts", "// platform/importer/x.ts, deploy/import, scripts/old.pyc, tests/import/x"],
    ]))).toEqual([
      "platform/a.ts says deleted at the cutover, and is not on the manifest",
      "platform/b.md says /workspace/imported, old TinyAssets, and is not on the manifest",
      'platform/c.ts says "discard", and is not on the manifest',
      "platform/e.ts says import/, and is not on the manifest",
      ".github/workflows/f.yml says ./.github/actions/gone, scripts/old.py, and is not on the manifest",
    ]);
  });

  it("names an import from a file that stays only where that file makes it today", () => {
    const named = [...cutoverLayer().entries].flatMap(([from, to]) =>
      to.map((each) => `${from} imports ${each}`));
    const made = new Set(cutoverImports(repoScripts(), cutoverLayer()));
    // Or a Python module it imports, or a path it hands a tool, as a test config its setup file.
    const handed = (each: string) => {
      const [from, to] = each.split(" imports ") as [string, string];
      const source = REPO_TREE.text(from) ?? "";
      return pythonLoads(from, source, REPO_TREE.exists).includes(to)
        || (/\.ya?ml$/.test(from) && stepLoads(source, [to]).includes(to))
        || [...source.matchAll(/["'](\.{1,2}\/[^"']+)["']/g)]
          .some(([, path]) => posix.normalize(posix.join(posix.dirname(from), path!)) === to);
    };
    expect(named.filter((each) => !made.has(each) && !handed(each))).toEqual([]);
  });

  it("names every module of what goes that a Python file which stays imports", () => {
    const { gone, entries } = cutoverLayer();
    const python = execFileSync("git", ["ls-files", "-z", "--", "*.py"], { cwd: REPO,
      encoding: "utf8" }).split("\0").filter((path) => path !== "" && !isGone(gone, path));
    expect(python.length).toBeGreaterThan(20);
    expect(python.flatMap((path) => pythonLoads(path, REPO_TREE.text(path)!, REPO_TREE.exists)
      .filter((to) => isGone(gone, to) && !(entries.get(path) ?? []).includes(to))
      .map((to) => `${path} imports ${to}: name it on the file's edit`))).toEqual([]);
    // A module beside it, a folder above it, or from the root; a package's module; none else.
    const tree = new Set(["scripts/a.py", "scripts/b/__init__.py", "scripts/b/c.py",
                          "scripts/tests/t.py", "tools/d.py"]);
    expect(pythonLoads("scripts/tests/t.py", [
      "import a", "from b import c as see, e", "    from tools.d import x", "import os, a",
      "from . import nothing", "# import gone"].join("\n"), (path) => tree.has(path)))
      .toEqual(["scripts/a.py", "scripts/b/__init__.py", "scripts/b/c.py", "tools/d.py"]);
  });

  it("names every script that goes a workflow which stays runs, on its edit", () => {
    const { gone, entries } = cutoverLayer();
    const flows = [...workflowNames().map((name) => `.github/workflows/${name}`),
                   ...actionFiles()].filter((path) => !isGone(gone, path));
    expect(flows.length).toBeGreaterThan(20);
    expect(flows.flatMap((path) => stepLoads(REPO_TREE.text(path)!, gone)
      .filter((to) => !(entries.get(path) ?? []).includes(to))
      .map((to) => `${path} runs ${to}: name it on the file's edit`))).toEqual([]);
    // By path or as a module, in a job's step or a composite action's; never in a comment.
    const gone2 = ["scripts/a.py", "scripts/b.py", "scripts/c.py", "scripts/d/e.py"];
    expect(stepLoads(["# python scripts/c.py", "jobs:", "  x:", "    steps:",
                      "      - run: python scripts/a.py --x",
                      "      - run: python -m scripts.d.e"].join("\n"), gone2))
      .toEqual(["scripts/a.py", "scripts/d/e.py"]);
    expect(stepLoads("runs:\n  steps:\n    - run: python scripts/b.py\n", gone2))
      .toEqual(["scripts/b.py"]);
  });

  it("makes the tree a dry run applies: carry-outs, the deletes, what an edit loads kept, and " +
     "the tests it edits set aside", () => {
    const what = "a probe";
    const manifest = manifestOf(
      { delete: "platform/old", what }, { delete: "tests", what },
      { move: "tests/test_kept.py", to: "scripts/tests/test_kept.py", what },
      { move: "platform/kept.ts", to: "platform/moved.ts", what },
      { edit: "platform/stays.ts", names: ["x"], imports: ["platform/old/a.ts"], what },
      { edit: "platform/stays.test.ts", names: ["x"], what });
    const loads: Record<string, string[]> = { "platform/old/a.ts": ["platform/old/b.ts"],
                                              "platform/old/b.ts": ["platform/kept.ts"] };
    const tracked = ["platform/old/a.ts", "platform/old/b.ts", "platform/old/c.ts",
                     "tests/test_kept.py", "tests/test_gone.py", "platform/kept.ts",
                     "platform/stays.ts", "platform/stays.test.ts"];
    expect(dryRun(tracked, (path) => loads[path] ?? [], manifest)).toEqual({
      carried: [["tests/test_kept.py", "scripts/tests/test_kept.py"]],
      kept: ["platform/old/a.ts", "platform/old/b.ts"],
      removed: ["platform/old/c.ts", "tests/test_gone.py"],
      setAside: ["platform/stays.test.ts"],
    });
    // It runs platform-tests' legs, less those of a workspace that goes.
    expect(dryRunLegs([{ leg: "old", command: "npm run test -w old -- --shard=1/1" },
                       { leg: "kept", command: "npm run test:workerd -w kept" }], manifest))
      .toEqual([{ leg: "kept", command: "npm run test:workerd -w kept" }]);
    expect(() => dryRunLegs([{ leg: "x", command: "npm test" }], manifest))
      .toThrow("platform-tests' leg npm test names no workspace");
  });

  it("holds the cutover layer: what goes imports itself, and what stays only named imports", () => {
    const layer: CutoverLayer = {
      gone: ["platform/import", "platform/doors/src/import.ts",
             "platform/doors/test/import.test.ts"],
      entries: new Map([["platform/doors/src/index.ts", ["platform/doors/src/import.ts"]]]),
    };
    // What goes reaches across the layers, where nothing else may; what stays reaches what goes
    // only by a named import its own layer allows.
    const files = new Map([
      ["platform/doors/test/import.test.ts", 'import { x } from "../../import/bundle";'],
      ["platform/doors/src/import.ts", 'import { y } from "../../import/bundle";'],
      ["platform/import/bundle.ts", 'import { z } from "../doors/src/import";'],
      ["platform/doors/src/index.ts", 'import { importQueue } from "./import";'],
      ["platform/doors/src/ring.ts", 'import { importQueue } from "./import";'],
      ["platform/owner/src/probe.ts", 'import { x } from "../../import/bundle";'],
    ]);
    const { violations } = layerProblems({ files, workers: workerFolders(), cutover: layer });
    expect(violations.map((each) => each.what)).toEqual([
      "platform/doors/src/ring.ts imports platform/doors/src/import.ts, which the cutover " +
        "deletes: name the import on the file's edit in platform/cutover.json",
      "platform/owner/src/probe.ts imports platform/import/bundle.ts",
    ]);
    expect(cutoverImports(files, layer)).toEqual([
      "platform/doors/src/index.ts imports platform/doors/src/import.ts",
      "platform/doors/src/ring.ts imports platform/doors/src/import.ts",
    ]);
  });
});
